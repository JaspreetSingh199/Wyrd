"""Launch orchestration methods for MainWindow.

The window supplies UI state, widgets, and presentation callbacks; process
monitoring is owned by ProcessMonitorWorkflowMixin.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog, QProgressDialog

from process_monitor import _proc_start_time, detect_mo2_processes
from proton_runtime import managed_paths
# Keep Skyrim launches routed through the established SteamCompat facade.
from steamcompat import launch_skyrim_via_mo2_in_skyrim_prefix
from steam_client import activate_steam_client, steam_client_status, verify_skyrim_launch_context


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
            "Use it for an unmodded audio baseline.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        diagnostics = managed_paths()["root"] / "logs" / "skyrim-launch.log"
        try:
            diagnostics.parent.mkdir(parents=True, exist_ok=True)
            with diagnostics.open("a", encoding="utf-8") as log:
                log.write("\n=== Wyrd direct Steam launch (AppID 489830; unmodded) ===\n")
                log.flush()
                process = subprocess.Popen(
                    [steam, "-applaunch", "489830"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                log.write(f"steam_pid: {process.pid}\n")
                log.flush()
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

    def start_skyrim(self, instance_name: str | None = None,
                     profile_name: str | None = None) -> None:
        """Launch Skyrim from the live MO2 inventory; no Wyrd registration is required."""
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Skyrim unavailable", "Skyrim Special Edition was not detected.")
            return

        instances = self.instance_state.get("skyrim_instances", [])
        if not instances:
            QMessageBox.warning(
                self, "No Skyrim MO2 instance found",
                self.instance_state.get("message", "Create a Skyrim Special Edition instance in MO2, then refresh Wyrd."),
            )
            return
        if instance_name is None:
            if len(instances) == 1:
                instance_name = instances[0]["name"]
            else:
                instance_name, accepted = QInputDialog.getItem(
                    self, "Select MO2 instance", "Skyrim Special Edition instance:",
                    [item["name"] for item in instances], 0, False,
                )
                if not accepted or not instance_name:
                    return
        elif not any(item.get("name") == instance_name for item in instances):
            QMessageBox.warning(
                self, "MO2 instance unavailable",
                f"The selected MO2 instance '{instance_name}' is no longer in the latest scan. Refresh instances and try again.",
            )
            return

        selected_instance = next(
            (item for item in instances if item.get("name") == instance_name), None
        )
        if selected_instance is None:
            QMessageBox.warning(self, "MO2 instance unavailable", "The selected MO2 instance is no longer in the latest scan.")
            return
        if profile_name is not None and profile_name not in selected_instance.get("profiles", []):
            QMessageBox.warning(
                self, "MO2 profile unavailable",
                "The selected profile is no longer present in the verified profile list for this instance. Refresh instances and try again.",
            )
            return

        preflight_getter = getattr(self, "mo2_launch_preflight", None)
        if not callable(preflight_getter):
            message = "The canonical MO2 launch preflight is unavailable; launch was refused."
            self._log(message)
            QMessageBox.warning(self, "Launch preflight unavailable", message)
            return
        preflight = preflight_getter(selected_instance, profile_name=profile_name)
        if not preflight.get("ok"):
            message = preflight.get("reason", "MO2 launch preflight failed without a reason.")
            self._log(
                f"Modded launch blocked [{preflight.get('failed_check', 'unknown')}]: {message}"
            )
            QMessageBox.warning(self, "Skyrim launch blocked", message)
            return

        self._log(
            f"MO2 launch preflight passed for instance={instance_name!r}, "
            f"profile={profile_name or 'MO2 saved selection'!r}; "
            f"{len(preflight.get('targets', {}).get('targets', []))} target(s) verified."
        )
        self._launch_skyrim_target_from_inventory(
            instance_name,
            preflight["targets"],
            Path(preflight["game_path"]),
            Path(preflight["prefix"]),
            Path(preflight["install_root"]),
            Path(preflight["proton_path"]),
            bool(preflight["installation"].get("managed", False)),
            Path(preflight["steam_root"]),
            profile_name=profile_name,
        )

    def _launch_registered_skyrim_target(
        self, record: dict, game: dict, installation: dict, runtime: dict,
        target_state: dict, game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None = None,
    ) -> None:
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
        steam_root: Path | None = None, profile_name: str | None = None,
    ) -> None:
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

        if steam_root is None:
            message = "Steam's installation root was not verified; Skyrim launch was refused."
            self._log(message)
            QMessageBox.warning(self, "Steam root unavailable", message)
            return

        # Steam is intentionally not started during Wyrd startup or discovery.
        # After confirmation, open Steam and wait for its main process plus UI
        # helper before rechecking Skyrim and handing off to MO2. Do not use
        # -applaunch: Skyrim must still start through MO2/SteamCompat.
        if getattr(self, "_steam_launch_pending", None):
            QMessageBox.information(
                self, "Steam is starting",
                "Wyrd is already waiting for Steam to become ready for a Skyrim launch.",
            )
            return
        steam_result = activate_steam_client()
        if not steam_result.get("ok"):
            message = steam_result.get("message", "Could not open Steam.")
            self._log("Modded Skyrim launch blocked: " + message)
            QMessageBox.warning(self, "Steam unavailable", message)
            return

        self._show_launch_loader("Opening Steam…", "Waiting for the Steam client to finish starting.")
        self._steam_launch_pending = {
            "instance_name": instance_name,
            "selected_target": dict(selected_target),
            "selected_title": selected,
            "game_path": Path(game_path),
            "prefix": Path(prefix),
            "install_root": Path(install_root),
            "proton": Path(proton),
            "steam_root": Path(steam_root) if steam_root else None,
            "profile_name": profile_name,
            "xaudio2_compat": self.xaudio_override_checkbox.isChecked(),
            "attempts": 0,
            "ready_streak": 0,
            "last_status": None,
        }
        if not hasattr(self, "_steam_launch_wait_timer"):
            self._steam_launch_wait_timer = QTimer(self)
            self._steam_launch_wait_timer.setInterval(1000)
            self._steam_launch_wait_timer.timeout.connect(self._poll_steam_before_modded_launch)
        self._log(
            "Steam client activation requested for modded Skyrim launch "
            f"(bootstrap PID {steam_result.get('pid', 'unknown')}); waiting for Steam UI readiness."
        )
        self.progress_label.setText("Opening Steam… waiting for its client and UI helper before checking Skyrim.")
        self._update_launch_loader("Opening Steam…", "Waiting for Steam's client and UI helper.")
        self._steam_launch_wait_timer.start()

    def _poll_steam_before_modded_launch(self) -> None:
        """Wait asynchronously for Steam, then revalidate Skyrim before MO2 launch."""
        pending = getattr(self, "_steam_launch_pending", None)
        if not pending:
            self._steam_launch_wait_timer.stop()
            return

        pending["attempts"] += 1
        status = steam_client_status()
        if not status.get("ok"):
            self._steam_launch_wait_timer.stop()
            self._steam_launch_pending = None
            message = status.get("message", "Could not inspect Steam client readiness.")
            self._log("Modded Skyrim launch blocked: " + message)
            self._close_launch_loader("Steam status unavailable", message)
            QMessageBox.warning(self, "Steam status unavailable", message)
            self.progress_label.setText("Steam status could not be verified; Skyrim was not launched.")
            return

        status_message = status.get("message", "Checking Steam client status.")
        self._update_launch_loader(
            "Opening Steam…",
            f"{status_message} ({pending['attempts']}/120)",
        )
        if status_message != pending["last_status"]:
            self._log(status_message)
            pending["last_status"] = status_message
        if status.get("ready"):
            pending["ready_streak"] += 1
        else:
            pending["ready_streak"] = 0

        # Require two consecutive ready samples to avoid racing the first
        # transient webhelper process during Steam's startup/restart cycle.
        if pending["ready_streak"] < 2:
            if pending["attempts"] >= 120:
                self._steam_launch_wait_timer.stop()
                self._steam_launch_pending = None
                message = (
                    "Steam did not become ready within 120 seconds. "
                    "MO2 and Skyrim were not launched. Open Steam manually, wait for its Library "
                    "to finish loading, then retry."
                )
                self._log(message)
                self._close_launch_loader("Steam startup timed out", message)
                QMessageBox.warning(self, "Steam startup timed out", message)
                self.progress_label.setText("Steam startup timed out; Skyrim was not launched.")
            return

        self._steam_launch_wait_timer.stop()
        self._steam_launch_pending = None
        game_check = verify_skyrim_launch_context(
            pending["game_path"], pending["steam_root"], pending["prefix"],
        )
        if not game_check.get("ok"):
            message = game_check.get("message", "Skyrim could not be re-verified after Steam started.")
            self._log("Post-Steam Skyrim check failed: " + message)
            self._close_launch_loader("Skyrim verification failed", message)
            QMessageBox.warning(self, "Skyrim verification failed", message)
            self.progress_label.setText("Skyrim verification failed; MO2 was not launched.")
            return

        self._log("Steam is ready. " + game_check.get("message", "Skyrim launch context verified."))
        self.progress_label.setText("Steam is ready; Skyrim verified. Starting MO2…")
        self._update_launch_loader("Starting MO2…", "Steam is ready and Skyrim's installation and prefix are verified.")
        self._start_modded_skyrim_after_steam(pending)

    def _start_modded_skyrim_after_steam(self, pending: dict) -> None:
        """Start the existing MO2 -> Proton -> Skyrim SteamCompat chain."""
        instance_name = pending["instance_name"]
        selected_target = pending["selected_target"]
        selected = pending["selected_title"]
        game_path = pending["game_path"]
        prefix = pending["prefix"]
        install_root = pending["install_root"]
        proton = pending["proton"]
        steam_root = pending["steam_root"]
        profile_name = pending["profile_name"]
        result = launch_skyrim_via_mo2_in_skyrim_prefix(
            proton, game_path, instance_name, selected,
            expected_binary=selected_target["binary"],
            prefix_path=prefix, install_root=install_root,
            steam_root_path=steam_root,
            xaudio2_compat=pending["xaudio2_compat"],
            profile_name=profile_name,
        )
        self._log(result.get("message", "Skyrim launch handoff failed."))
        if result.get("ok"):
            self._update_launch_loader("Launching MO2…", "Proton has been asked to start MO2. Waiting for MO2 or Skyrim to appear.")
            self._start_mo2_launch_wait(pending)
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
            self.progress_label.setText("MO2 launch handoff started; game startup is not yet confirmed.")
            self._log("Launch diagnostics: " + result.get("log_path", "unavailable"))
            self._log("Waiting for MO2 or Skyrim process detection before dismissing launch loader.")
        else:
            self._close_launch_loader(
                "MO2 launch failed",
                result.get("message", "Could not launch Skyrim through MO2."),
            )
            QMessageBox.warning(self, "Skyrim launch failed", result.get("message", "Could not launch Skyrim through MO2."))

    def _launch_loader_label(self, message: str) -> str:
        """Append elapsed time so the busy dialog never looks frozen."""
        started = getattr(self, "_launch_progress_started_at", None)
        if started is None:
            self._launch_progress_started_at = time.monotonic()
            started = self._launch_progress_started_at
        elapsed = max(0, int(time.monotonic() - started))
        minutes, seconds = divmod(elapsed, 60)
        return f"{message}\n\nElapsed: {minutes:02d}:{seconds:02d}"

    def _restore_launch_loader_on_activation(self, state) -> None:
        """Restore the active launch indicator when the user returns to Wyrd."""
        if state != Qt.ApplicationState.ApplicationActive:
            return
        dialog = getattr(self, "_launch_progress_dialog", None)
        launch_active = bool(
            getattr(self, "_steam_launch_pending", None)
            or getattr(self, "_mo2_launch_wait_pending", None)
            or (getattr(self, "_launch_monitor", None) is not None
                and self._launch_monitor.isActive())
        )
        if dialog is not None and launch_active and not dialog.isVisible():
            dialog.show()
            dialog.raise_()

    def _show_launch_loader(self, title: str, message: str) -> None:
        """Show a non-cancellable busy dialog until this launch's game is detected."""
        app = QApplication.instance()
        if app is not None and not getattr(self, "_launch_activation_hooked", False):
            app.applicationStateChanged.connect(self._restore_launch_loader_on_activation)
            self._launch_activation_hooked = True
        dialog = getattr(self, "_launch_progress_dialog", None)
        if dialog is None:
            self._launch_progress_started_at = time.monotonic()
            dialog = QProgressDialog(message, "", 0, 0, self)
            dialog.setObjectName("launchLoader")
            dialog.setWindowTitle("Launching Skyrim")
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setCancelButton(None)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setMinimumWidth(500)
            dialog.setMinimumHeight(112)
            self._launch_progress_dialog = dialog
        dialog.setLabelText(self._launch_loader_label(message))
        dialog.setWindowTitle(title)
        dialog.setRange(0, 0)
        dialog.show()
        dialog.raise_()

    def _update_launch_loader(self, title: str, message: str) -> None:
        dialog = getattr(self, "_launch_progress_dialog", None)
        if dialog is None:
            self._show_launch_loader(title, message)
            return
        dialog.setWindowTitle(title)
        dialog.setLabelText(self._launch_loader_label(message))
        if not dialog.isVisible():
            dialog.show()
        dialog.raise_()

    def _close_launch_loader(self, title: str, message: str) -> None:
        dialog = getattr(self, "_launch_progress_dialog", None)
        if dialog is not None:
            dialog.setLabelText(self._launch_loader_label(message))
            dialog.setWindowTitle(title)
            dialog.close()
            self._launch_progress_dialog = None
        self._launch_progress_started_at = None
        self._mo2_launch_wait_pending = None
        if getattr(self, "_mo2_launch_wait_timer", None) is not None:
            self._mo2_launch_wait_timer.stop()

    def _start_mo2_launch_wait(self, pending: dict) -> None:
        """Watch MO2 startup while the game-process monitor waits for Skyrim/SKSE."""
        self._mo2_launch_wait_pending = pending
        self._mo2_launch_wait_attempts = 0
        self._mo2_launch_wait_seen = False
        self._mo2_launch_wait_last_message = ""
        self._mo2_launch_visibility_logged = False
        if not hasattr(self, "_mo2_launch_wait_timer"):
            self._mo2_launch_wait_timer = QTimer(self)
            self._mo2_launch_wait_timer.setInterval(1000)
            self._mo2_launch_wait_timer.timeout.connect(self._poll_mo2_launch_visibility)
        self._mo2_launch_wait_timer.start()

    def _poll_mo2_launch_visibility(self) -> None:
        """Keep the loader open after MO2 appears; only game detection closes it."""
        pending = getattr(self, "_mo2_launch_wait_pending", None)
        if not pending:
            if getattr(self, "_mo2_launch_wait_timer", None) is not None:
                self._mo2_launch_wait_timer.stop()
            return
        self._mo2_launch_wait_attempts += 1
        mo2 = detect_mo2_processes(pending["prefix"])
        processes = mo2.get("processes", []) if mo2.get("ok") else []
        if processes:
            self._mo2_launch_wait_seen = True
            pids = ", ".join(str(item["pid"]) for item in processes)
            message = (
                f"Mod Organizer is running (PID {pids}). Waiting for "
                f"{pending.get('selected_title', 'Skyrim/SKSE')} "
                f"({self._mo2_launch_wait_attempts}/300 seconds)."
            )
            self.progress_label.setText("MO2 is running; waiting for Skyrim/SKSE to launch.")
            if not self._mo2_launch_visibility_logged:
                self._log("MO2 process detected. Keeping the launch loader open until Skyrim/SKSE is detected.")
                self._mo2_launch_visibility_logged = True
        elif self._mo2_launch_wait_seen:
            message = (
                "MO2 was detected but is no longer visible. Waiting for the selected Skyrim/SKSE "
                f"process or a launch failure ({self._mo2_launch_wait_attempts}/300 seconds)."
            )
        else:
            message = (
                "Steam is ready. Waiting for Mod Organizer 2 to start through Proton, "
                f"then Skyrim/SKSE ({self._mo2_launch_wait_attempts}/300 seconds)."
            )

        # Refresh the visible phase every few seconds without spamming logs.
        if self._mo2_launch_wait_attempts == 1 or self._mo2_launch_wait_attempts % 5 == 0:
            self._update_launch_loader(
                "MO2 is running — waiting for Skyrim" if self._mo2_launch_wait_seen
                else "Starting MO2 through Proton…",
                message,
            )

        if self._mo2_launch_wait_attempts >= 300:
            # This timer only presents MO2 visibility. The process monitor is
            # the single authority for the five-minute launch deadline and
            # terminal session state; do not close the loader or clear pending
            # state here because that can race its final process check.
            self._mo2_launch_wait_timer.stop()
            self._log(
                "MO2 visibility timer reached five minutes; waiting for the "
                "process monitor to record the launch timeout."
            )
