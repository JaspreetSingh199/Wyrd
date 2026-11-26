"""Launch orchestration and process monitoring methods for MainWindow.

This mixin owns launch workflows while the window supplies UI state, widgets,
and presentation callbacks.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from launch_models import GameProcess
from ui_path_helpers import _steam_prefix_for_game_path
from proton_runtime import managed_paths, _runtime_environment
from process_monitor import (
    launch_process_status, detect_skyrim_processes, _proc_start_time,
    _proc_identity, _is_descendant_process, read_launch_diagnostics,
    classify_launch_diagnostics,
)
from mo2_instances import inspect_mo2_instances
from wyrd_mo2 import inspect_skyrim_launch_targets
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_external_skyrim_prefix_mo2_installation,
)
from steam_mo2_launch import launch_skyrim_via_mo2_in_skyrim_prefix
from steam_prefix import steam_compat_prefix


class LaunchWorkflowMixin:
    def start_skyrim_direct(self) -> None:
        """Launch Skyrim through Steam's own AppID path, independent of MO2 instances."""
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Skyrim unavailable", "Skyrim Special Edition was not detected.")
            return
        steam = shutil.which("steam")
        if not steam:
            QMessageBox.warning(self, "Steam unavailable", "The Steam command was not found on PATH.")
            return
        answer = QMessageBox.question(
            self, "Launch Skyrim through Steam",
            "This launches Skyrim Special Edition directly through Steam, without MO2 or mods. "
            "Use it for an unmodded audio baseline.\\n\\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            process = subprocess.Popen(
                [steam, "-applaunch", "489830"],
                stdin=None, stdout=None, stderr=None, start_new_session=True,
            )
        except OSError as error:
            QMessageBox.warning(self, "Steam launch failed", f"Could not start Steam: {error}")
            self._log(f"Direct Steam launch failed: {error}")
            return
        self._log(f"Direct Steam launch requested (AppID 489830), PID {process.pid}. This launch bypasses MO2.")
        self.progress_label.setText("Direct Steam launch requested; game startup is not yet confirmed.")
        QMessageBox.information(
            self, "Steam launch requested",
            "Steam was asked to launch Skyrim Special Edition directly. This is an unmodded launch and bypasses MO2.",
        )

    def start_active_game(self) -> None:
        """Launch the active Skyrim MO2 instance from the live filesystem scan."""
        self.start_skyrim()

    def _launch_registered_custom_executable(
        self, record: dict, installation: dict, game: dict, runtime: dict, target: dict
    ) -> dict:
        """Start an MO2 custom executable under its registered Proton/game context."""
        import json
        import subprocess
        from datetime import datetime, timezone
        from wyrd_mo2 import inspect_proton_build, _runtime_environment

        executable = Path(installation.get("executable", "")).expanduser()
        instance_path = Path(record.get("instance_path", "")).expanduser()
        config = Path(record.get("config", "")).expanduser()
        prefix = Path(record.get("prefix_path") or installation.get("prefix_path") or "").expanduser()
        game_path = Path(game.get("path", "")).expanduser()
        proton = Path(runtime.get("path", "")).expanduser()
        expected_binary = Path(target.get("resolved_binary", "")).expanduser()
        title = str(target.get("title", "")).strip()
        if not title or executable.is_symlink() or not executable.is_file():
            return {"ok": False, "message": "MO2 executable or configured title is invalid."}
        if any(path.is_symlink() for path in (instance_path, config, prefix, game_path, proton, expected_binary)):
            return {"ok": False, "message": "A registered launch path became a symlink; launch refused."}
        if not instance_path.is_dir() or not config.is_file() or not game_path.is_dir():
            return {"ok": False, "message": "MO2 instance, configuration, or game directory is missing."}
        if not (prefix / "pfx/drive_c/users").is_dir() or not (proton / "proton").is_file():
            return {"ok": False, "message": "Registered Proton prefix or runtime is unavailable."}
        validation = inspect_proton_build(proton)
        if not validation.get("ok"):
            return {"ok": False, "message": validation.get("message", "Proton runtime validation failed.")}
        if not expected_binary.is_file():
            return {"ok": False, "message": "Configured game executable disappeared after selection."}

        # Re-read the actual MO2 INI at launch time. The executable list shown
        # before the confirmation dialog is only a preview and may have changed.
        from wyrd_mo2 import inspect_mo2_instances, _resolve_windows_path
        inventory = inspect_mo2_instances(
            prefix, executable.parent, require_managed=False,
            detected_game_path=Path(self.game_state["game_dir"]),
        )
        if not inventory.get("ok"):
            return {"ok": False, "message": inventory.get("message", "Could not re-inspect the selected MO2 prefix.")}
        live_instance = next(
            (item for item in inventory.get("instances", [])
             if Path(item.get("path", "")).resolve() == instance_path.resolve()),
            None,
        )
        if live_instance is None or Path(live_instance.get("config", "")).resolve() != config.resolve():
            return {"ok": False, "message": "The registered MO2 instance or its configuration changed after selection."}
        live_targets = []
        for entry in live_instance.get("executables", []):
            if str(entry.get("title", "")).strip() != title:
                continue
            binary = _resolve_windows_path(str(entry.get("binary", "")), prefix, game_path)
            if binary and binary.is_file() and not binary.is_symlink():
                live_targets.append(binary.resolve())
        if len(live_targets) != 1 or live_targets[0] != expected_binary.resolve():
            return {"ok": False, "message": "The selected executable is no longer uniquely verified in the current MO2 configuration."}
        try:
            config.resolve(strict=True).relative_to(instance_path.resolve(strict=True))
            expected_binary.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": "Instance configuration or game executable no longer resolves safely."}

        steam_root_text = game.get("steam_root")
        if steam_root_text:
            steam_root = Path(steam_root_text).expanduser()
        else:
            candidates = (
                Path.home() / ".steam/steam",
                Path.home() / ".local/share/Steam",
                Path.home() / ".steam/root",
            )
            steam_root = next((path for path in candidates if path.is_dir()), Path(""))
        if not steam_root.is_dir():
            return {"ok": False, "message": "A Steam client install root is needed by this Proton runtime but was not found."}

        env = _runtime_environment(proton, steam_root, prefix)
        app_id = str(game.get("app_id") or "").strip()
        if app_id:
            env.update({"SteamAppId": app_id, "SteamGameId": app_id, "STEAM_COMPAT_APP_ID": app_id})
        else:
            for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                env.pop(key, None)
        env["STEAM_COMPAT_DATA_PATH"] = str(prefix.resolve())
        import uuid
        launch_token = uuid.uuid4().hex
        env["WYRD_LAUNCHED"] = "1"
        env["WYRD_LAUNCH_ID"] = launch_token
        command = [
            str(proton / "proton"), "run", str(executable),
            "--instance", instance_path.name, "run", "--executable", title,
        ]
        logs = managed_paths()["root"] / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{record.get('id', 'instance')}-game-launch.log"
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n=== Wyrd registered game launch ===\n")
                log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\n")
                log.write(f"game: {game.get('name')}\ninstance: {instance_path}\ntitle: {title}\n")
                log.write(f"binary: {expected_binary}\napp_id: {app_id or 'non-Steam'}\n")
                log.write("command: " + json.dumps(command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    command, cwd=str(executable.parent), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                log.write(f"wrapper_pid: {process.pid}\n")
                log.flush()
        except OSError as error:
            return {"ok": False, "message": f"Could not launch configured MO2 executable: {error}"}

        self.launch_session.wrapper_pid = process.pid
        self.launch_session.wrapper_start_time = _proc_start_time(process.pid)
        self.launch_session.log_path = str(log_path)
        self.launch_session.instance_name = instance_path.name
        self.launch_session.executable_title = title
        self.launch_session.started_at = time.time()
        self.launch_session.detected_processes = []
        self.launch_session.process_identities = {}
        self.launch_session.game_processes = []
        self.launch_session.prefix_root = str(prefix.resolve())
        self.launch_session.game_path = str(game_path.resolve())
        self.launch_session.app_id = app_id
        self.launch_session.target_binary = expected_binary.name
        self.launch_session.launch_token = launch_token
        self.launch_session.state = "handoff"
        self.launch_session.poll_count = 0
        if app_id == "489830" or not app_id:
            self._launch_monitor.start()
        else:
            self._launch_monitor.start()
        return {
            "ok": True, "pid": process.pid, "log_path": str(log_path),
            "message": f"Started '{title}' through MO2 instance '{instance_path.name}'.",
        }

    def start_skyrim(self) -> None:
        """Launch Skyrim from the live MO2 inventory; no Wyrd registration is required."""
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Skyrim unavailable", "Skyrim Special Edition was not detected.")
            return
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd could not verify MO2's dynamically derived Proton game path. "
                "The modded launch is blocked to avoid using a stale game location.\\n\\n"
                + discovery.get("message", "Unknown Steam discovery mapping error."),
            )
            return

        instances = self.instance_state.get("skyrim_instances", [])
        if not instances:
            QMessageBox.warning(
                self, "No Skyrim MO2 instance found",
                self.instance_state.get("message", "Create a Skyrim Special Edition instance in MO2, then refresh Wyrd."),
            )
            return
        if len(instances) == 1:
            instance_name = instances[0]["name"]
        else:
            instance_name, accepted = QInputDialog.getItem(
                self, "Select MO2 instance", "Skyrim Special Edition instance:",
                [item["name"] for item in instances], 0, False,
            )
            if not accepted or not instance_name:
                return

        game_path = Path(self.game_state["game_dir"]).expanduser()
        mo2_state = inspect_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            mo2_state = inspect_external_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            # Discovery may find an MO2 instance without finding its executable.
            # Never guess or launch another copy; keep the inventory visible and
            # fail closed until an executable can be verified.
            QMessageBox.warning(
                self, "MO2 executable unavailable",
                self.instance_state.get(
                    "installation_message",
                    mo2_state.get("message", "MO2 instance found, but no verified ModOrganizer.exe is available."),
                ),
            )
            return
        proton_value = self.proton_combo.currentData()
        if not proton_value:
            QMessageBox.warning(self, "Proton unavailable", "Select a valid Proton runtime before launching.")
            return
        prefix = Path(mo2_state["prefix"])
        install_root = Path(mo2_state["install"])
        target_state = inspect_skyrim_launch_targets(
            prefix, game_path, instance_name=instance_name,
            install_root=install_root, require_managed=bool(mo2_state.get("managed", True)),
        )
        self._launch_skyrim_target_from_inventory(
            instance_name, target_state, game_path, prefix, install_root,
            Path(proton_value), bool(mo2_state.get("managed", True)),
            Path(mo2_state["steam_root"]) if mo2_state.get("steam_root") else None,
        )

    def _launch_registered_skyrim_target(
        self, record: dict, game: dict, installation: dict, runtime: dict,
        target_state: dict, game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None = None,
    ) -> None:
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd has not verified the dynamically derived Skyrim Proton game path. "
                "The modded launch is blocked until a successful refresh.\\n\\n"
                + (discovery or {}).get("message", "No verified Steam discovery result is available."),
            )
            return
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No configured Skyrim/SKSE executable was found."),
            )
            return
        instance_name = Path(record["instance_path"]).name
        self._launch_skyrim_target_from_inventory(
            instance_name, target_state, game_path, prefix, install_root, proton, False, steam_root,
        )

    def _run_launch_preflight(
        self, instance_name: str, target_state: dict, selected_target: dict,
        game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None,
    ) -> bool:
        """Show a visible, read-only checklist and require all checks before launch."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preparing modded Skyrim")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Launch pre-check")
        heading.setObjectName("section")
        layout.addWidget(heading)
        detail = QLabel("Wyrd is checking the selected environment before starting MO2.")
        detail.setWordWrap(True)
        detail.setObjectName("muted")
        layout.addWidget(detail)
        rows = []
        checks = [
            ("Skyrim installation", lambda: (
                not game_path.is_symlink() and game_path.is_dir(),
                f"Detected game folder: {game_path}",
            )),
            ("Skyrim Steam prefix", lambda: (
                not prefix.is_symlink()
                and prefix.is_dir()
                and (prefix / "pfx/drive_c/users").is_dir()
                and (steam_root is None or prefix.resolve(strict=True) == steam_compat_prefix(steam_root).resolve(strict=True)),
                f"AppID 489830 prefix: {prefix}",
            )),
            ("MO2 application", lambda: (
                not (install_root / "ModOrganizer.exe").is_symlink()
                and (install_root / "ModOrganizer.exe").is_file(),
                f"MO2 executable: {install_root / 'ModOrganizer.exe'}",
            )),
            ("Selected Proton runtime", lambda: (
                not proton.is_symlink()
                and proton.is_dir()
                and not (proton / "proton").is_symlink()
                and (proton / "proton").is_file()
                and os.access(proton / "proton", os.X_OK),
                f"Runtime: {proton}",
            )),
            ("Selected MO2 instance", lambda: self._preflight_instance_exists(
                prefix, install_root, instance_name
            )),
            ("Configured Skyrim executable", lambda: (
                bool(target_state.get("ok"))
                and any(item.get("title") == selected_target.get("title")
                        and item.get("binary") == selected_target.get("binary")
                        for item in target_state.get("targets", [])),
                f"MO2 target: {selected_target.get('title', 'Unknown')}",
            )),
        ]
        for label, _ in checks:
            checkbox = QCheckBox(label)
            checkbox.setEnabled(False)
            layout.addWidget(checkbox)
            rows.append(checkbox)
        progress = QProgressBar()
        progress.setRange(0, len(checks))
        progress.setValue(0)
        layout.addWidget(progress)
        status = QLabel("Starting checks…")
        status.setWordWrap(True)
        status.setObjectName("muted")
        layout.addWidget(status)
        buttons = QHBoxLayout()
        continue_button = QPushButton("Continue to launch")
        continue_button.setObjectName("primaryAction")
        continue_button.setEnabled(False)
        cancel_button = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)

        failed = False
        for index, (label, check) in enumerate(checks):
            detail.setText(f"Checking {label.lower()}…")
            QCoreApplication.processEvents()
            try:
                passed, message = check()
            except Exception as error:
                passed, message = False, str(error)
            rows[index].setChecked(bool(passed))
            rows[index].setText(f"{label} — {'OK' if passed else 'FAILED'}")
            progress.setValue(index + 1)
            status.setText(message)
            QCoreApplication.processEvents()
            if not passed:
                failed = True
                detail.setText("Pre-check failed. Wyrd will not launch Skyrim.")
                status.setText(f"{label}: {message}")
                break

        if failed:
            cancel_button.setText("Close")
            continue_button.hide()
            dialog.exec()
            return False
        detail.setText("All checks passed. Review the checklist, then continue.")
        status.setText(f"Ready to launch '{selected_target.get('title')}' through MO2 instance '{instance_name}'.")
        continue_button.setEnabled(True)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _preflight_instance_exists(prefix: Path, install_root: Path, instance_name: str) -> tuple[bool, str]:
        inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
        if not inventory.get("ok"):
            return False, inventory.get("message", "MO2 instance inventory is unavailable.")
        item = next((entry for entry in inventory.get("instances", [])
                     if entry.get("name") == instance_name), None)
        if not item:
            return False, f"MO2 instance '{instance_name}' was not found in the selected prefix."
        instance_path = Path(item.get("path", ""))
        config_value = item.get("config")
        config_path = Path(config_value) if config_value else instance_path / "ModOrganizer.ini"
        valid = (
            not instance_path.is_symlink() and instance_path.is_dir()
            and not config_path.is_symlink() and config_path.is_file()
        )
        return valid, f"Instance folder: {instance_path}; configuration: {config_path}"

    def _launch_skyrim_target_from_inventory(
        self, instance_name: str, target_state: dict, game_path: Path,
        prefix: Path, install_root: Path, proton: Path, require_managed: bool,
        steam_root: Path | None = None,
    ) -> None:
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd has not verified the dynamically derived Skyrim Proton game path. "
                "The modded launch is blocked until a successful refresh.\\n\\n"
                + (discovery or {}).get("message", "No verified Steam discovery result is available."),
            )
            return
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No verified Skyrim/SKSE executable is available in this instance."),
            )
            self._log(target_state.get("message", "No valid targets found."))
            return
        targets = target_state["targets"]
        title_counts = {}
        for target in targets:
            title_counts[target["title"]] = title_counts.get(target["title"], 0) + 1
        labels = [
            (f"{target['title']} — {Path(target['binary']).name}"
             if title_counts[target["title"]] > 1 else target["title"])
            for target in targets
        ]
        selected_label, accepted = QInputDialog.getItem(
            self, "Launch Skyrim through MO2", "Configured Skyrim executable:",
            labels, 0, False,
        )
        if not accepted or not selected_label:
            return
        selected_target = targets[labels.index(selected_label)]
        selected = selected_target["title"]
        if not self._run_launch_preflight(
            instance_name, target_state, selected_target,
            game_path, prefix, install_root, proton, steam_root,
        ):
            return
        answer = QMessageBox.question(
            self, "Confirm Skyrim launch",
            f"MO2 will launch '{selected}' in instance '{instance_name}' using its virtual filesystem. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        audio_mode = "xaudio2_compat" if self.xaudio_override_checkbox.isChecked() else "default"
        result = launch_skyrim_via_mo2_in_skyrim_prefix(
            proton, game_path, instance_name, selected,
            expected_binary=selected_target["binary"],
            prefix_path=prefix, install_root=install_root,
            steam_root_path=steam_root,
            xaudio2_compat=(audio_mode == "xaudio2_compat"),
        )
        self._log(result.get("message", "Skyrim launch handoff failed."))
        if result.get("ok"):
            self.launch_session.wrapper_pid = result.get("pid")
            self.launch_session.wrapper_start_time = _proc_start_time(self.launch_session.wrapper_pid) if self.launch_session.wrapper_pid else None
            self.launch_session.log_path = result.get("log_path")
            self.launch_session.instance_name = instance_name
            self.launch_session.executable_title = selected
            self.launch_session.started_at = time.time()
            self.launch_session.detected_processes = []
            self.launch_session.process_identities = {}
            self.launch_session.game_processes = []
            self.launch_session.prefix_root = str(prefix.resolve())
            self.launch_session.game_path = str(game_path.resolve())
            self.launch_session.app_id = "489830"
            self.launch_session.target_binary = Path(selected_target["binary"]).name
            self.launch_session.launch_token = result.get("launch_token")
            self.launch_session.state = "handoff"
            self.launch_session.poll_count = 0
            if self.launch_session.wrapper_pid:
                self._launch_monitor.start()
            self.launch_status_button.setEnabled(True)
            self.progress_label.setText("MO2 launch handoff started; game startup is not yet confirmed.")
            self._log("Launch diagnostics: " + result.get("log_path", "unavailable"))
            QMessageBox.information(self, "MO2 launch handoff started", result.get("message", ""))
        else:
            QMessageBox.warning(self, "Skyrim launch failed", result.get("message", "Could not launch Skyrim through MO2."))

    def _stop_launch_monitor(self, reason: str | None = None) -> None:
        if self._launch_monitor.isActive():
            self._launch_monitor.stop()
            if reason:
                self._log("Launch monitoring stopped: " + reason)

    def _poll_launch_status(self) -> None:
        """Refresh launch status without modal dialogs until a terminal state is reached."""
        if not self.launch_session.wrapper_pid:
            self._stop_launch_monitor("no launch session is active")
            return
        self.launch_session.poll_count += 1
        if self.launch_session.poll_count > self._launch_poll_limit:
            self.launch_session.state = "timeout"
            self._stop_launch_monitor("monitoring time limit reached")
            self.progress_label.setText(
                "Launch monitoring timed out; use Check launch status or inspect diagnostics."
            )
            return
        wrapper_identity = _proc_identity(self.launch_session.wrapper_pid)
        result = launch_process_status(
            self.launch_session.wrapper_pid, self.launch_session.wrapper_start_time
        )
        wrapper_identity_matches = (
            self.launch_session.wrapper_start_time is not None
            and wrapper_identity.get("start_time") == self.launch_session.wrapper_start_time
        )
        scan_prefix = (
            Path(self.launch_session.prefix_root)
            if self.launch_session.prefix_root else
            _steam_prefix_for_game_path(self.game_state.get("game_dir"))
        )
        if self.launch_session.app_id == "489830":
            process_scan = detect_skyrim_processes(
                scan_prefix, launch_token=self.launch_session.launch_token
            )
            session_processes = []
            seen_pids = set()
            if process_scan.get("ok") and wrapper_identity_matches:
                for item in process_scan.get("processes", []):
                    if _is_descendant_process(item["pid"], self.launch_session.wrapper_pid):
                        session_processes.append({**item, "_launch_source": "descendant"})
                        seen_pids.add(item["pid"])
            if process_scan.get("ok") and not session_processes:
                # Prefer confirmed process-tree ancestry. Use the unique
                # launch-token/prefix attribution only when ancestry is gone.
                for item in process_scan.get("wyrd_processes", []):
                    if item["pid"] not in seen_pids:
                        session_processes.append({**item, "_launch_source": "token-prefix"})
                        seen_pids.add(item["pid"])
        elif self.launch_session.target_binary:
            from wyrd_mo2 import detect_game_processes
            process_scan = detect_game_processes(
                self.launch_session.wrapper_pid, self.launch_session.target_binary,
                self.launch_session.prefix_root,
                launch_token=self.launch_session.launch_token,
                ancestor_start_time=self.launch_session.wrapper_start_time,
            )
            # The scanner independently verifies either the original wrapper
            # identity (for descendants) or this launch's unique environment token
            # and prefix (for reparented game processes).
            session_processes = process_scan.get("processes", []) if process_scan.get("ok") else []
        else:
            process_scan = {"ok": False, "processes": [], "message": "No configured target binary is available for process attribution."}
            session_processes = []
        if session_processes:
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})" for item in session_processes
            )
            skyrim_prefix_match = (
                self.launch_session.app_id == "489830"
                and any(item.get("_launch_source") == "token-prefix" for item in session_processes)
                and not any(item.get("_launch_source") == "descendant" for item in session_processes)
            )
            self.progress_label.setText(
                "Skyrim/SKSE detected in this Wyrd launch process tree."
                if self.launch_session.app_id == "489830" and not skyrim_prefix_match
                else "Skyrim/SKSE detected with this launch's unique Wyrd prefix token."
                if skyrim_prefix_match
                else "Configured game executable detected in this Wyrd launch process tree."
            )
            self.launch_session.detected_processes = list(session_processes)
            self.launch_session.process_identities = {
                item["pid"]: item.get("start_time") for item in session_processes
            }
            self.launch_session.game_processes = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source=item.get("_launch_source", "launch_process_tree"),
                    attribution_confidence="confirmed",
                ) for item in session_processes
            ]
            self.launch_session.state = "detected"
            self._log("Launch session detected: " + detected)
            if self.launch_session.app_id == "489830":
                self._log_audio_runtime_environment(session_processes)
            self._stop_launch_monitor(
                "Skyrim/SKSE process detected"
                if self.launch_session.app_id == "489830"
                else "registered game executable detected"
            )
            self._start_game_process_watch()
            return
        if result["state"] in {"exited", "invalid"}:
            self.progress_label.setText(
                "Proton wrapper exited before Skyrim was detected; review launch diagnostics."
                if self.launch_session.app_id == "489830"
                else "MO2 game wrapper exited before the configured executable was detected; review diagnostics."
            )
            self._log(
                ("Launch handoff ended without detected Skyrim: " if self.launch_session.app_id == "489830"
                 else "MO2 custom-executable handoff ended without a matching game process: ")
                + result["message"]
            )
            if self.launch_session.log_path:
                diagnostics = read_launch_diagnostics(Path(self.launch_session.log_path))
                if diagnostics.get("ok") and diagnostics.get("content", "").strip():
                    excerpt = diagnostics["content"][-2400:].strip()
                    self._log("Recent MO2/Proton diagnostics:\\n" + excerpt)
                    for finding in classify_launch_diagnostics(excerpt):
                        self._log("Launch diagnosis [" + finding["code"] + "]: " + finding["hint"])
                    self.progress_label.setText(
                        "Launch handoff exited. Recent diagnostics were added to the setup log."
                    )
                elif not diagnostics.get("ok"):
                    self._log("Launch diagnostics: " + diagnostics.get("message", "Unavailable."))
            self.launch_session.state = "failed"
            self._stop_launch_monitor("wrapper exited without detected game process")
            return
        if result["state"] == "unknown":
            self.progress_label.setText(
                "Launch status is unknown; automatic monitoring will continue briefly."
            )
            self.launch_session.state = "unknown"
            self._log("Launch status unknown: " + result["message"])
        else:
            self.launch_session.state = "waiting"
            self.progress_label.setText(
                (
                    f"Waiting for Skyrim/SKSE… (check {self.launch_session.poll_count}/{self._launch_poll_limit})"
                    if self.launch_session.app_id == "489830"
                    else f"Waiting for {self.launch_session.target_binary or 'game process'}… "
                         f"(check {self.launch_session.poll_count}/{self._launch_poll_limit})"
                )
            )

    def _start_game_process_watch(self) -> None:
        """Keep watching detected game PIDs after the Proton wrapper has exited."""
        if not self.launch_session.game_processes:
            return
        self._game_watch_timer.start()
        self._log("Post-detection process watch started for " + ", ".join(
            f"{process.binary} (PID {process.pid})"
            for process in self.launch_session.game_processes
        ))

    def _revalidate_detected_game_processes(self) -> None:
        """Revalidate saved process identities and report when the game exits."""
        processes = self.launch_session.game_processes
        if not processes:
            self._game_watch_timer.stop()
            return
        active = [process for process in processes if process.matches_current_process()]
        if active:
            self.launch_session.game_processes = active
            return
        self._game_watch_timer.stop()
        self.launch_session.game_processes = []
        self.launch_session.state = "game-exited"
        is_skyrim = self.launch_session.app_id == "489830"
        self.progress_label.setText(
            "Skyrim/SKSE processes exited; launch session ended."
            if is_skyrim else "Registered game process exited; launch session ended."
        )
        self._log(
            "Detected Skyrim/SKSE process identities are no longer present. Game exit recorded."
            if is_skyrim else "Detected registered game process identities are no longer present. Game exit recorded."
        )

    def check_launch_status(self) -> None:
        """Report the current tracked game processes, even after Proton's wrapper exits."""
        session = self.launch_session
        game_dir = session.game_path or self.game_state.get("game_dir")
        steam_prefix = (
            Path(session.prefix_root) if session.prefix_root
            else _steam_prefix_for_game_path(game_dir)
        )
        if session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None):
            process_scan = detect_skyrim_processes(
                steam_prefix, launch_token=session.launch_token
            )
            self._log("Skyrim process scan: " + process_scan.get("message", "Detection unavailable."))
            for item in process_scan.get("processes", []):
                self._log(
                    f"Skyrim/SKSE candidate PID {item.get('pid')} ({item.get('binary')}): "
                    f"prefix attribution={item.get('attributed_to_wyrd')}; "
                    f"start_time={item.get('start_time')}"
                )
                mappings = item.get("audio_module_mappings", {})
                if item.get("audio_module_mappings_readable"):
                    observed = [name for name, path in mappings.items() if path]
                    self._log(
                        f"Audio DLL mappings PID {item.get('pid')}: "
                        + (", ".join(observed) if observed else "none observed")
                    )
                else:
                    self._log(
                        f"Audio DLL mappings PID {item.get('pid')}: unavailable "
                        f"({item.get('audio_module_mappings_error') or 'access denied'})"
                    )
                env = item.get("audio_runtime_environment", {})
                self._log(
                    f"Audio runtime environment PID {item.get('pid')}: "
                    f"WINEDLLOVERRIDES={env.get('WINEDLLOVERRIDES')!r}; "
                    f"PULSE_LATENCY_MSEC={env.get('PULSE_LATENCY_MSEC')!r}"
                )
        elif session.wrapper_pid and session.target_binary:
            from wyrd_mo2 import detect_game_processes
            process_scan = detect_game_processes(
                session.wrapper_pid, session.target_binary, session.prefix_root,
                launch_token=session.launch_token,
                ancestor_start_time=session.wrapper_start_time,
            )
            self._log("Registered game process scan: " + process_scan.get("message", "Detection unavailable."))
        else:
            process_scan = {"ok": False, "processes": [], "message": "No generic target is available for process attribution."}
            self._log(process_scan["message"])

        # First revalidate processes already attributed to this launch. This avoids
        # losing a still-running game when Proton's short-lived wrapper has exited.
        tracked = [process for process in session.game_processes
                   if process.matches_current_process()]
        if tracked:
            session.game_processes = tracked
            session.state = (
                "detected" if any(p.attribution_confidence == "confirmed" for p in tracked)
                else "detected-unconfirmed"
            )
            self._start_game_process_watch()
            detected = "\\n".join(
                f"{p.binary} (PID {p.pid}) — {p.attribution_confidence}"
                for p in tracked
            )
            is_skyrim = session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None)
            self.progress_label.setText(
                "Skyrim/SKSE is running; tracked process identity verified."
                if is_skyrim else "Registered game process is running; tracked identity verified."
            )
            detail = (
                ("Tracked Skyrim/SKSE processes are still present:\\n" if is_skyrim
                 else "Tracked registered game processes are still present:\\n") + detected
            )
            QMessageBox.information(self, "Launch process status", detail)
            return

        if session.game_processes:
            session.game_processes = []
            session.state = "game-exited"
            self._game_watch_timer.stop()
            is_skyrim = session.app_id == "489830" or not session.app_id
            self.progress_label.setText(
                "Previously detected Skyrim/SKSE processes have exited."
                if is_skyrim else "Previously detected registered game processes have exited."
            )
            self._log(
                "Tracked Skyrim/SKSE process identities are no longer present."
                if is_skyrim else "Tracked registered game process identities are no longer present."
            )

        if not session.wrapper_pid:
            detail = (
                "No Wyrd launch handoff has been recorded in this session. "
                "That is expected when Skyrim is launched directly from MO2.\\n\\n"
                + process_scan.get("message", "Process detection unavailable.")
                + "\\n\\nThis check detects currently running Skyrim/SKSE processes; "
                "it cannot confirm a game that has already exited."
            )
            QMessageBox.information(self, "Launch status", detail)
            return

        wrapper_identity = _proc_identity(session.wrapper_pid)
        wrapper_matches = (
            session.wrapper_start_time is not None
            and wrapper_identity.get("start_time") == session.wrapper_start_time
        )
        result = launch_process_status(session.wrapper_pid, session.wrapper_start_time)
        self._log(
            f"Launch PID {session.wrapper_pid}: {result['state']} — {result['message']}"
        )

        session_processes = []
        if process_scan.get("ok"):
            if session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None):
                if wrapper_matches and session.wrapper_pid:
                    for item in process_scan.get("processes", []):
                        if _is_descendant_process(item["pid"], session.wrapper_pid):
                            session_processes.append({**item, "_launch_source": "descendant"})
            elif session.wrapper_pid and session.target_binary:
                # Generic scanner has already validated the exact launch token,
                # registered prefix, and original wrapper identity.
                session_processes = list(process_scan.get("processes", []))

        if session_processes:
            tracked = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="launch_process_tree",
                    attribution_confidence="confirmed",
                )
                for item in session_processes
            ]
            session.game_processes = tracked
            session.detected_processes = list(session_processes)
            session.process_identities = {
                item["pid"]: item.get("start_time") for item in session_processes
            }
            session.state = "detected"
            self._start_game_process_watch()
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})" for item in session_processes
            )
            self.progress_label.setText("Skyrim/SKSE detected in this Wyrd launch process tree.")
            detail = result["message"] + "\\n\\nProcesses descended from this launch:\\n" + detected
        elif process_scan.get("ok") and process_scan.get("wyrd_processes"):
            items = process_scan["wyrd_processes"]
            tracked = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="managed_prefix",
                    attribution_confidence="prefix",
                )
                for item in items
            ]
            session.game_processes = tracked
            session.detected_processes = list(items)
            session.process_identities = {
                item["pid"]: item.get("start_time") for item in items
            }
            session.state = "detected-unconfirmed"
            self._start_game_process_watch()
            detected = ", ".join(f"{item['binary']} (PID {item['pid']})" for item in items)
            self.progress_label.setText(
                "Skyrim detected in Wyrd's prefix; launch ancestry is unconfirmed."
            )
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "") + "\\n" + detected
        elif process_scan.get("ok") and process_scan.get("processes"):
            self.progress_label.setText("Skyrim detected, but Wyrd ownership could not be confirmed.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        elif session.state == "game-exited":
            detail = "The previously detected game process has exited.\\n\\n" + result["message"]
        elif result["state"] == "exited":
            self.progress_label.setText("Proton wrapper exited. Skyrim process not detected; review diagnostics.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        elif result["state"] == "running":
            self.progress_label.setText("Proton wrapper is running; Skyrim process not detected yet.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        else:
            self.progress_label.setText("Launch status is unknown; inspect diagnostics.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        QMessageBox.information(self, "Launch process status", detail)


    def show_launch_diagnostics(self) -> None:
        path = managed_paths()["root"] / "logs" / "skyrim-launch.log"
        result = read_launch_diagnostics(path)
        if not result.get("ok"):
            QMessageBox.information(self, "Launch diagnostics", result.get("message", "No diagnostics available."))
            return
        self._log("Launch diagnostics opened: " + result["path"])
        QMessageBox.information(self, "Launch diagnostics", result["content"][-5000:])
