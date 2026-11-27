"""MO2 installation, inspection, launch and reset UI workflows."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from proton_runtime import _steam_root_for_game, _runtime_environment
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_external_skyrim_prefix_mo2_installation,
)
from steam_mo2_management import reset_wyrd_mo2
from steam_prefix import APP_ID, steam_compat_prefix
from ui_path_helpers import _steam_prefix_for_game_path
from steam_workers import SteamPrefixInstallWorker
from process_monitor import detect_mo2_processes


class Mo2ManagementWorkflowMixin:
    def reset_managed_mo2(self) -> None:
        """Remove only Wyrd-owned MO2 files; never reset Steam's Skyrim prefix."""
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "MO2 is busy", "Wait for the current setup operation to finish before resetting MO2.")
            return
        state = (
            inspect_skyrim_prefix_mo2_installation(Path(self.game_state["game_dir"]))
            if self.game_state.get("found")
            else {"installed": False}
        )
        if not state.get("installed") or not state.get("managed", True):
            QMessageBox.information(
                self, "MO2 cannot be removed",
                "Wyrd could not verify a Wyrd-managed MO2 installation. External or unverified installations will not be removed.",
            )
            return
        install = state.get("install", "Wyrd-managed MO2 directory")
        answer = QMessageBox.warning(
            self, "Remove Wyrd-managed MO2?",
            "This removes only the verified Wyrd-installed MO2 program directory. Existing MO2 instances and their profiles, mods, downloads and custom storage are preserved.\n\n"
            "NEVER DELETED: Skyrim's Steam compatdata/489830 prefix, Skyrim/game files, saves, external MO2 installations, "
            "and custom storage outside the managed instance folders.\n\n"
            f"Installation: {install}\n\nThis cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = reset_wyrd_mo2(game_path=Path(self.game_state["game_dir"]))
        self._log(result.get("message", "MO2 removal attempted."))
        if result.get("ok"):
            self.progress_label.setText("Wyrd-managed MO2 removed.")
            QMessageBox.information(self, "MO2 removed", result["message"])
        else:
            QMessageBox.warning(self, "MO2 removal incomplete", result.get("message", "Reset failed safely."))
        self.refresh_all()
        self._update_active_instance_from_scan()


    def check_mo2_installation(self) -> None:
        """Show a read-only inventory of Skyrim prefix, MO2 files and instances."""
        lines = []
        game_value = self.game_state.get("game_dir") if self.game_state.get("found") else None
        if not game_value:
            lines.append("Skyrim game: NOT DETECTED")
            prefix = None
        else:
            game = Path(game_value).expanduser()
            lines.append(f"Skyrim game: {'OK' if game.is_dir() else 'MISSING'}")
            lines.append(f"Game path: {game}")
            try:
                state = inspect_skyrim_prefix_mo2_installation(game)
            except Exception as error:
                state = {"installed": False, "message": str(error)}
            prefix_value = state.get("prefix")
            prefix = Path(prefix_value).expanduser() if prefix_value else None
            if prefix is None:
                try:
                    prefix = steam_compat_prefix(_steam_root_for_game(game, Path.home()))
                except Exception as error:
                    lines.append(f"Steam prefix: UNKNOWN ({error})")
            if prefix is not None:
                pfx = prefix / "pfx"
                drive_c = pfx / "drive_c"
                lines.append(f"Skyrim compatdata prefix: {'EXISTS' if prefix.is_dir() else 'MISSING'}")
                lines.append(f"Prefix initialized (pfx/drive_c): {'YES' if drive_c.is_dir() else 'NO'}")
                lines.append(f"Prefix path: {prefix}")
                install = prefix / "pfx/drive_c/Modding/MO2"
                executable = install / "ModOrganizer.exe"
                marker = install / ".wyrd-managed.json"
                lines.append(f"MO2 install directory: {'EXISTS' if install.is_dir() else 'MISSING'}")
                lines.append(f"ModOrganizer.exe: {'EXISTS' if executable.is_file() and not executable.is_symlink() else 'MISSING'}")
                lines.append(f"Wyrd ownership marker: {'EXISTS' if marker.is_file() and not marker.is_symlink() else 'MISSING'}")
                try:
                    from steam_mo2_discovery import inspect_any_skyrim_prefix_mo2_instances
                    inventory = inspect_any_skyrim_prefix_mo2_instances(game)
                    instances = inventory.get("instances", []) if inventory.get("ok") else []
                    skyrim_instances = inventory.get("skyrim_instances", []) if inventory.get("ok") else []
                    lines.append(f"MO2 instance folders found: {len(instances)}")
                    lines.append(f"Skyrim SE instances found: {len(skyrim_instances)}")
                    for item in instances:
                        lines.append(f"  • {item.get('name', 'Unnamed')} — {item.get('instance_path', 'path unknown')}")
                    if not inventory.get("ok"):
                        lines.append("Instance scan: " + inventory.get("message", "unavailable"))
                except Exception as error:
                    lines.append(f"MO2 instance scan: ERROR ({error})")

        QMessageBox.information(self, "MO2 installation check", "\n".join(lines))


    def launch_mo2_gui(self) -> None:
        """Open MO2's native instance picker in Skyrim's existing Proton prefix."""
        try:
            installation = self.instance_state.get("installation", {}) or {}
            executable_value = (
                self.mo2_state.get("executable")
                or installation.get("executable")
            )
            executable = Path(executable_value or "").expanduser()
            game_path = Path(self.game_state.get("game_dir", "")).expanduser()
            prefix = _steam_prefix_for_game_path(str(game_path)) if game_path else None
            proton_value = self.proton_combo.currentData()
            if not executable_value or executable.is_symlink() or not executable.is_file():
                raise RuntimeError("No verified ModOrganizer.exe was found. Instances can be inspected, but MO2 cannot be launched.")
            if installation.get("installed") and not self.mo2_state.get("installed"):
                try:
                    executable.resolve(strict=True).relative_to(
                        Path(installation.get("install", "")).resolve(strict=True)
                    )
                except (OSError, ValueError, RuntimeError):
                    raise RuntimeError("The discovered external MO2 executable is outside its verified installation directory.")
            if not game_path.is_dir() or game_path.is_symlink() or prefix is None:
                raise RuntimeError("Skyrim's existing Steam compatdata prefix could not be resolved.")
            if not proton_value:
                raise RuntimeError("Select a valid Proton runtime before launching MO2.")
            proton = Path(proton_value).expanduser()
            if proton.is_symlink() or not (proton / "proton").is_file():
                raise RuntimeError("The selected Proton runtime is no longer valid.")
            steam_root = _steam_root_for_game(game_path, Path.home())
            if not steam_root:
                raise RuntimeError("Could not identify the Steam library containing Skyrim.")
            env = _runtime_environment(proton, Path(steam_root), prefix)
            env.update({
                "SteamAppId": APP_ID,
                "SteamGameId": APP_ID,
                "STEAM_COMPAT_APP_ID": APP_ID,
                "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
            })
            # MO2's own --pick option opens its native instance selector.
            # This deliberately avoids Wyrd registration and does not depend
            # on Wyrd's read-only inventory being able to classify an instance.
            process = subprocess.Popen(
                [str(proton / "proton"), "run", str(executable), "--pick"],
                cwd=str(executable.parent), env=env, stdin=None, stdout=None,
                stderr=None, start_new_session=True,
            )
            self._mo2_session_prefix = prefix
            self._mo2_session_seen = False
            self._mo2_session_polls = 0
            self._mo2_session_timer.start()
            self._log(f"MO2 native instance-picker handoff started (PID {process.pid}); waiting for MO2 to open.")
            self.progress_label.setText("Opening MO2 instance picker…")
        except Exception as error:
            self._log(f"MO2 launch failed: {error}")
            QMessageBox.warning(self, "Launch MO2", str(error))


    def _poll_mo2_session(self) -> None:
        """Refresh MO2 instances and profiles after the launched MO2 process exits."""
        prefix = getattr(self, "_mo2_session_prefix", None)
        if prefix is None:
            self._mo2_session_timer.stop()
            return

        self._mo2_session_polls += 1
        scan = detect_mo2_processes(prefix)
        if scan.get("ok") and scan.get("processes"):
            if not self._mo2_session_seen:
                self._mo2_session_seen = True
                self._log("MO2 process detected in Skyrim's Steam prefix; monitoring until it closes.")
                self.progress_label.setText("MO2 is open. Wyrd will refresh instances and profiles when it closes.")
            return

        if not self._mo2_session_seen:
            # Proton may take a few seconds to start Wine/MO2. Stop after a
            # bounded startup window rather than silently polling forever.
            if self._mo2_session_polls >= 80:
                self._mo2_session_timer.stop()
                self._mo2_session_prefix = None
                self._log(
                    "MO2 did not appear in the Skyrim prefix within 120 seconds. "
                    "No automatic inventory refresh was triggered."
                )
                self.progress_label.setText("MO2 startup was not detected. Check the MO2 launch log.")
            return

        self._mo2_session_timer.stop()
        self._mo2_session_prefix = None
        self._mo2_session_seen = False
        self._log("MO2 has closed. Refreshing Skyrim instances and profiles…")
        self.progress_label.setText("MO2 closed. Refreshing instances and profiles…")
        self.refresh_all()

    def install_mo2(self, *, confirm: bool = True) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Wyrd setup", "Skyrim Special Edition was not detected.")
            return
        proton_path = self.proton_combo.currentData()
        if not proton_path:
            QMessageBox.warning(self, "Wyrd setup", "No compatible Proton build is selected.")
            return
        game_path = Path(self.game_state["game_dir"])
        state = inspect_skyrim_prefix_mo2_installation(game_path)
        if not state.get("installed"):
            state = inspect_external_skyrim_prefix_mo2_installation(game_path)
        if state.get("installed"):
            self._log("MO2 installation already exists in Skyrim's Steam prefix; refreshing detection.")
            self.refresh_all()
            return
        occupied_target = Path(state["install"]).expanduser() if state.get("install") else None
        if occupied_target is not None and (occupied_target.exists() or occupied_target.is_symlink()):
            self._log(f"MO2 installation target is occupied; refusing to overwrite: {occupied_target}")
            QMessageBox.warning(
                self, "MO2 installation target is occupied",
                state.get("message") or (
                    f"The MO2 target already exists but is not a verified Wyrd installation:\n{occupied_target}\n\n"
                    "Wyrd will leave it untouched. Inspect or connect the existing installation instead."
                ),
            )
            self.refresh_all()
            return
        if confirm:
            answer = QMessageBox.question(
                self, "Install Mod Organizer 2",
                "Install official MO2 inside Skyrim's existing Steam compatdata prefix? "
                "The prefix must already be initialized by launching Skyrim through Steam. "
                "Wyrd will not reset the prefix or overwrite an unowned MO2 directory. "
                "Normal Proton/MO2 runtime files may be written there. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.worker = SteamPrefixInstallWorker(str(proton_path), self.game_state["game_dir"])
        self.worker.progress.connect(self._on_install_progress)
        self.worker.completed.connect(self._on_install_completed)
        self.install_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.proton_combo.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("Installing MO2 into Skyrim's Steam prefix…")
        self._log("Starting MO2 installation in Skyrim's Steam prefix.")
        self.worker.start()


    def _on_install_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.progress_label.setText(message)
        self._log(message)


    def _on_install_completed(self, result: dict) -> None:
        if result.get("ok"):
            # The worker result is not the final trust boundary: re-open the
            # installed target and validate both ModOrganizer.exe and the
            # Wyrd ownership marker before reporting success to the user.
            game_dir = self.game_state.get("game_dir")
            verified = (
                inspect_skyrim_prefix_mo2_installation(Path(game_dir))
                if game_dir else {"installed": False, "message": "Skyrim game path is unavailable."}
            )
            if not verified.get("installed"):
                result = {
                    "ok": False,
                    "message": "MO2 installer returned success, but post-install ownership/executable verification failed: "
                               + verified.get("message", "installation could not be verified."),
                }
        if result.get("ok"):
            self.progress.setValue(100)
            self.progress_label.setText("MO2 installed and verified.")
            self._log(result.get("message", "MO2 setup completed."))
            QMessageBox.information(
                self, "MO2 installed",
                result.get("message", "MO2 is ready.")
                + "\n\nContinue by creating or connecting an MO2 instance.",
            )
        else:
            self.progress_label.setText("Setup stopped. Review the log for details.")
            self._log("MO2 setup failed: " + result.get("message", "Unknown error"))
            QMessageBox.warning(self, "Wyrd setup failed", result.get("message", "MO2 setup failed."))
        self.refresh_button.setEnabled(True)
        self.refresh_all()
