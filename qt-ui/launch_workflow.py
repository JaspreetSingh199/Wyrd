"""Launch orchestration methods for MainWindow.

The window supplies UI state, widgets, and presentation callbacks; process
monitoring is owned by ProcessMonitorWorkflowMixin.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from proton_runtime import managed_paths, _runtime_environment
from process_monitor import _proc_start_time
from mo2_instances import inspect_mo2_instances
from mo2_launch import inspect_skyrim_launch_targets
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
