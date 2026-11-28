"""MO2 installation, inspection, launch and reset UI workflows."""
from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QDesktopServices

from proton_runtime import _steam_root_for_game, _runtime_environment
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_external_skyrim_prefix_mo2_installation,
)
from steam_mo2_management import reset_wyrd_mo2
from wyrd_settings import reset_wyrd_settings
from steam_prefix import APP_ID, steam_compat_prefix
from ui_path_helpers import _steam_prefix_for_game_path
from steam_workers import SteamPrefixInstallWorker
from process_monitor import detect_mo2_processes, snapshot_wyrd_session
from proton_runtime import managed_paths
from steam_client import activate_steam_client, steam_client_status
from steam_mo2_launch import _persist_mo2_selected_profile


class Mo2ManagementWorkflowMixin:
    def open_mo2_instance(self, instance_name: str | None = None, profile_name: str | None = None) -> None:
        """Open MO2's native Instance Manager, or a named instance when requested."""
        # Header-level Open MO2 opens MO2's own Instance Manager, where users
        # create, remove and manage instances. Per-instance actions pass a name.
        if instance_name is not None:
            inventory = getattr(self, "instance_state", {}) or {}
            instances = list(inventory.get("skyrim_instances", []))
            selected_instance = next(
                (item for item in instances if str(item.get("name") or "") == instance_name),
                None,
            )
            if selected_instance is None:
                QMessageBox.warning(
                    self, "MO2 instance unavailable",
                    "That instance is no longer in the current discovery results. Refresh the environment and try again.",
                )
                return
            if profile_name is not None and profile_name not in selected_instance.get("profiles", []):
                QMessageBox.warning(
                    self, "MO2 profile unavailable",
                    "That profile is no longer present in the verified profile list. Refresh the environment and try again.",
                )
                return

        if getattr(self, "_steam_launch_pending", None) or getattr(self, "_mo2_steam_launch_pending", None):
            QMessageBox.information(
                self, "Steam is starting",
                "Wyrd is already waiting for Steam to become ready. Please wait for that action to finish.",
            )
            return
        steam_result = activate_steam_client()
        if not steam_result.get("ok"):
            message = steam_result.get("message", "Could not open Steam.")
            self._log("Open MO2 blocked: " + message)
            QMessageBox.warning(self, "Steam unavailable", message)
            return

        self._mo2_steam_launch_pending = {
            "instance_name": instance_name,
            "profile_name": profile_name,
            "attempts": 0,
            "ready_streak": 0,
            "last_status": None,
        }
        self._show_launch_loader(
            "Opening Steam…",
            "Waiting for Steam to finish starting before opening Mod Organizer.",
        )
        if not hasattr(self, "_mo2_steam_wait_timer"):
            self._mo2_steam_wait_timer = QTimer(self)
            self._mo2_steam_wait_timer.setInterval(1000)
            self._mo2_steam_wait_timer.timeout.connect(self._poll_steam_before_mo2_open)
        self._log(
            "Steam client activation requested for Open MO2 "
            f"(bootstrap PID {steam_result.get('pid', 'unknown')}); waiting for Steam UI readiness."
        )
        self.progress_label.setText("Opening Steam… waiting for its client and UI helper before opening MO2.")
        self._mo2_steam_wait_timer.start()

    def _poll_steam_before_mo2_open(self) -> None:
        """Wait for Steam readiness, then open MO2 without launching Skyrim."""
        pending = getattr(self, "_mo2_steam_launch_pending", None)
        if not pending:
            if getattr(self, "_mo2_steam_wait_timer", None) is not None:
                self._mo2_steam_wait_timer.stop()
            return

        pending["attempts"] += 1
        status = steam_client_status()
        if not status.get("ok"):
            self._mo2_steam_wait_timer.stop()
            self._mo2_steam_launch_pending = None
            message = status.get("message", "Could not inspect Steam client readiness.")
            self._log("Open MO2 blocked: " + message)
            self._close_launch_loader("Steam status unavailable", message)
            QMessageBox.warning(self, "Steam status unavailable", message)
            self.progress_label.setText("Steam status could not be verified; MO2 was not launched.")
            return

        status_message = status.get("message", "Checking Steam client status.")
        self._update_launch_loader(
            "Opening Steam…",
            f"{status_message} ({pending['attempts']}/120)",
        )
        if status_message != pending["last_status"]:
            self._log("Open MO2: " + status_message)
            pending["last_status"] = status_message

        pending["ready_streak"] = pending["ready_streak"] + 1 if status.get("ready") else 0
        if pending["ready_streak"] >= 2:
            self._mo2_steam_wait_timer.stop()
            self._mo2_steam_launch_pending = None
            instance_name = pending["instance_name"]
            profile_name = pending.get("profile_name")
            self._log("Steam is ready. Opening MO2 without launching Skyrim.")
            self.progress_label.setText("Steam is ready; opening MO2…")
            self._close_launch_loader("Steam ready", "Opening Mod Organizer.")
            QTimer.singleShot(
                0,
                lambda name=instance_name, profile=profile_name:
                    self.launch_mo2_gui(instance_name=name, profile_name=profile),
            )
            return

        if pending["attempts"] >= 120:
            self._mo2_steam_wait_timer.stop()
            self._mo2_steam_launch_pending = None
            message = (
                "Steam did not become ready within 120 seconds. MO2 was not launched. "
                "Open Steam manually, wait for its Library to finish loading, then retry."
            )
            self._log(message)
            self._close_launch_loader("Steam startup timed out", message)
            QMessageBox.warning(self, "Steam startup timed out", message)
            self.progress_label.setText("Steam startup timed out; MO2 was not launched.")

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
            # Removing MO2 is a full environment reset from Wyrd's perspective:
            # clear Wyrd preferences only after the verified MO2 removal succeeds.
            settings_result = reset_wyrd_settings()
            if settings_result.get("ok"):
                self._saved_settings = {"first_run_completed": False}
                self._saved_game_path = None
                self._restoring_selection = True
                self.proton_combo.blockSignals(True)
                if self.proton_combo.count():
                    self.proton_combo.setCurrentIndex(0)
                self.proton_combo.blockSignals(False)
                self.mo2_library_widget.preferred_instance_id = ""
                self._saved_settings["first_run_completed"] = False
                self._log("MO2 removed; Wyrd preferences reset. First-launch setup will run next time Wyrd starts.")
                self.progress_label.setText("MO2 removed. Wyrd settings reset.")
                QMessageBox.information(
                    self, "MO2 removed",
                    result["message"] + "\n\nWyrd preferences were also reset. First-launch setup will appear the next time you start Wyrd."
                )
            else:
                self._log("MO2 removed, but Wyrd settings could not be reset: " + settings_result.get("message", "Unknown error"))
                QMessageBox.warning(
                    self, "MO2 removed; settings reset incomplete",
                    result["message"] + "\n\nMO2 was removed, but Wyrd preferences could not be reset: "
                    + settings_result.get("message", "Unknown error")
                )
            self._restoring_selection = False
        else:
            QMessageBox.warning(self, "MO2 removal incomplete", result.get("message", "Reset failed safely."))
        self.refresh_all()
        self._update_active_instance_from_scan()


    def open_mo2_installation_folder(self) -> None:
        """Open the verified MO2 program directory in the desktop file manager."""
        game_value = self.game_state.get("game_dir") if self.game_state.get("found") else None
        if not game_value:
            QMessageBox.information(
                self, "MO2 installation unavailable",
                "Skyrim Special Edition must be detected before Wyrd can locate its MO2 installation.",
            )
            return
        game = Path(game_value).expanduser()
        try:
            state = inspect_skyrim_prefix_mo2_installation(game)
            if not state.get("installed"):
                external_state = inspect_external_skyrim_prefix_mo2_installation(game)
                if external_state.get("installed"):
                    state = external_state
            install_value = state.get("install")
            if not state.get("installed") or not install_value:
                raise OSError(
                    state.get("message", "No verified MO2 installation directory was found.")
                )
            install = Path(install_value).expanduser()
            if install.is_symlink() or not install.is_dir():
                raise OSError("The verified MO2 installation folder is missing or unsafe.")
            resolved = install.resolve(strict=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved))):
                raise OSError("The desktop environment could not open the MO2 installation folder.")
            self._log("Opened MO2 installation folder: " + str(resolved))
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(
                self, "Could not open MO2 installation folder", str(error)
            )
            self._log("Could not open MO2 installation folder: " + str(error))


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
                        lines.append(f"  • {item.get('name', 'Unnamed')} — {item.get('path') or item.get('instance_path') or 'path unknown'}")
                    if not inventory.get("ok"):
                        lines.append("Instance scan: " + inventory.get("message", "unavailable"))
                except Exception as error:
                    lines.append(f"MO2 instance scan: ERROR ({error})")

        QMessageBox.information(self, "MO2 installation check", "\n".join(lines))


    def launch_mo2_gui(
        self, instance_name: str | None = None, profile_name: str | None = None,
    ) -> None:
        """Open MO2's native Instance Manager or a requested instance/profile directly."""
        active_session = (
            getattr(self, "_mo2_session_timer", None) is not None
            and self._mo2_session_timer.isActive()
        )
        # A successful process scan is authoritative for whether MO2 is
        # currently open. Do not require _mo2_session_seen or a polling grace
        # count: both caused a closed/undetected MO2 window to leave a stale
        # active timer that blocked every subsequent Open MO2 request.
        if active_session:
            previous_prefix = getattr(self, "_mo2_session_prefix", None)
            if previous_prefix is None:
                self._mo2_session_timer.stop()
                active_session = False
                self._log("Cleared MO2 session with no registered prefix before reopening.")
            else:
                current = detect_mo2_processes(previous_prefix)
                if current.get("ok") and not current.get("processes"):
                    self._mo2_session_timer.stop()
                    self._mo2_session_prefix = None
                    self._mo2_session_seen = False
                    self._mo2_session_polls = 0
                    self._mo2_session_loader_active = False
                    self._mo2_session_launch_token = None
                    self._mo2_session_wrapper_pid = None
                    self._log("No live MO2 process found in Skyrim's prefix; cleared stale session before reopening.")
                    active_session = False
                elif not current.get("ok"):
                    detail = str(current.get("message") or "process scan failed")
                    self._log("Could not verify MO2 process state before reopening: " + detail)
                    QMessageBox.warning(
                        self, "Cannot verify MO2 process state",
                        "Wyrd could not inspect the MO2 process table, so it cannot safely "
                        "decide whether another MO2 launch is already running.\n\n" + detail,
                    )
                    return
        if active_session:
            QMessageBox.information(
                self, "MO2 already opening",
                "Wyrd is already opening or monitoring an MO2 window in Skyrim's prefix.",
            )
            return
        existing_loader = getattr(self, "_launch_progress_dialog", None)
        if existing_loader is not None and existing_loader.isVisible():
            QMessageBox.information(
                self, "Another launch is in progress",
                "Wait for the current launch to finish before opening another MO2 window.",
            )
            return
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
            launch_token = uuid.uuid4().hex
            env.update({
                "SteamAppId": APP_ID,
                "SteamGameId": APP_ID,
                "STEAM_COMPAT_APP_ID": APP_ID,
                "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
                "WYRD_LAUNCHED": "1",
                "WYRD_LAUNCH_ID": launch_token,
            })
            # Header-level Open MO2 uses --pick for MO2's native Instance
            # Manager. Per-instance Open MO2 Mod List uses --instance.
            if instance_name is not None:
                inventory = self.instance_state.get("skyrim_instances", [])
                available_names = {str(item.get("name") or "") for item in inventory}
                selected_instance = next(
                    (item for item in inventory if str(item.get("name") or "") == instance_name),
                    None,
                )
                if selected_instance is None:
                    raise RuntimeError(
                        "That MO2 instance is no longer in the current discovery results."
                    )
                if profile_name is not None:
                    if profile_name not in selected_instance.get("profiles", []):
                        raise RuntimeError(
                            "That MO2 profile is no longer present in the verified profile list."
                        )
                    _persist_mo2_selected_profile(selected_instance, profile_name, prefix)
            elif profile_name is not None:
                raise RuntimeError("A profile can only be selected when opening a specific MO2 instance.")
            # Preserve Skyrim's established AppID 489830 Proton context.
            diagnostics = managed_paths()["root"] / "logs" / "skyrim-launch.log"
            diagnostics.parent.mkdir(parents=True, exist_ok=True)
            with diagnostics.open("a", encoding="utf-8") as log:
                log.write("\n=== Wyrd Open MO2 session (Steam AppID 489830) ===\n")
                log.write(f"runtime: {proton}\nprefix: {prefix}\nlaunch_id: {launch_token}\n")
                log.write(f"instance: {instance_name or '<native instance manager>'}\n")
                log.write("monitoring: session RSS, process/thread count, CPU, winedbg count, host RAM and swap\n")
                log.flush()
                mo2_args = (
                    ["--instance", instance_name]
                    + (["--profile", profile_name] if profile_name is not None else [])
                    if instance_name is not None else ["--pick"]
                )
                process = subprocess.Popen(
                    [str(proton / "proton"), "run", str(executable), *mo2_args],
                    cwd=str(executable.parent), env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                log.write(f"wrapper_pid: {process.pid}\n")
                log.flush()
            self._mo2_session_prefix = prefix
            self._mo2_session_seen = False
            self._mo2_session_polls = 0
            self._mo2_session_launch_token = launch_token
            self._mo2_session_wrapper_pid = process.pid
            self._mo2_session_log_path = diagnostics
            self._mo2_session_last_log = 0.0
            self._mo2_session_peak_rss = 0
            self._mo2_session_peak_processes = 0
            self._mo2_session_warned = set()
            self._mo2_session_loader_active = True
            self._show_launch_loader(
                "Opening MO2…",
                (
                    "Opening Mod Organizer's Instance Manager through Proton."
                    if instance_name is None
                    else f"Starting MO2 instance '{instance_name}' through Proton. Waiting for Mod Organizer to appear."
                ),
            )
            self._mo2_session_timer.start()
            self._log(
                (
                    f"MO2 Instance Manager opened (PID {process.pid}); "
                    if instance_name is None
                    else f"MO2 instance '{instance_name}' opened directly (PID {process.pid}); "
                ) + "resource monitoring active."
            )
            self.progress_label.setText(
                "Opening MO2 Instance Manager…" if instance_name is None
                else f"Opening MO2 mod list for {instance_name}…"
            )
        except Exception as error:
            if getattr(self, "_mo2_session_loader_active", False):
                self._mo2_session_loader_active = False
                self._close_launch_loader("MO2 launch failed", str(error))
            self._log(f"MO2 mod-list launch failed: {error}")
            QMessageBox.warning(self, "Open MO2 Mod List", str(error))


    def _poll_mo2_session(self) -> None:
        """Refresh MO2 instances and profiles after the launched MO2 process exits."""
        prefix = getattr(self, "_mo2_session_prefix", None)
        if prefix is None:
            self._mo2_session_timer.stop()
            return

        self._mo2_session_polls += 1
        token = getattr(self, "_mo2_session_launch_token", None)
        if token:
            snapshot = snapshot_wyrd_session(token, getattr(self, "_mo2_session_wrapper_pid", None))
            if snapshot.get("ok"):
                rss = snapshot["rss_bytes"]
                count = snapshot["process_count"]
                self._mo2_session_peak_rss = max(getattr(self, "_mo2_session_peak_rss", 0), rss)
                self._mo2_session_peak_processes = max(getattr(self, "_mo2_session_peak_processes", 0), count)
                thresholds = ((2 * 1024**3, "2 GiB"), (4 * 1024**3, "4 GiB"))
                warned = getattr(self, "_mo2_session_warned", set())
                for threshold, label in thresholds:
                    if rss >= threshold and label not in warned:
                        warned.add(label)
                        self._log(f"MO2 resource warning: Wyrd session RSS reached {label}.")
                if snapshot["winedbg_count"] >= 8 and "winedbg" not in warned:
                    warned.add("winedbg")
                    self._log(f"MO2 resource warning: {snapshot['winedbg_count']} winedbg processes detected.")
                self._mo2_session_warned = warned
                now = time.monotonic()
                if now - getattr(self, "_mo2_session_last_log", 0.0) >= 10:
                    self._mo2_session_last_log = now
                    try:
                        mem = {}
                        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                            key, _, value = line.partition(":")
                            parts = value.split()
                            if parts and parts[0].isdigit():
                                mem[key] = int(parts[0]) * 1024
                        with self._mo2_session_log_path.open("a", encoding="utf-8") as log:
                            log.write(
                                f"RESOURCE sample: session_rss={rss} process_count={count} "
                                f"thread_count={snapshot['thread_count']} cpu_ticks={snapshot['cpu_ticks']} "
                                f"winedbg_count={snapshot['winedbg_count']} "
                                f"host_mem_available={mem.get('MemAvailable', 0)} "
                                f"swap_free={mem.get('SwapFree', 0)}\n"
                            )
                            for item in snapshot["processes"][:8]:
                                log.write(
                                    f"  PID={item['pid']} PPID={item['ppid']} RSS={item['rss_bytes']} "
                                    f"threads={item['threads']} state={item['state']} name={item['name']}\n"
                                )
                    except (OSError, AttributeError):
                        pass
        scan = detect_mo2_processes(prefix)
        if scan.get("ok") and scan.get("processes"):
            if not self._mo2_session_seen:
                self._mo2_session_seen = True
                processes = scan.get("processes", [])
                pids = ", ".join(str(item.get("pid")) for item in processes)
                self._log(
                    "MO2 process detected in Skyrim's Steam prefix"
                    + (f" (PID {pids})" if pids else "")
                    + "; monitoring until it closes."
                )
                self.progress_label.setText("MO2 is open. Wyrd will refresh instances and profiles when it closes.")
                if getattr(self, "_mo2_session_loader_active", False):
                    self._mo2_session_loader_active = False
                    self._close_launch_loader(
                        "MO2 opened",
                        "Mod Organizer is running in Skyrim's existing Proton prefix."
                        + (f" Detected PID(s): {pids}." if pids else ""),
                    )
            return

        if not self._mo2_session_seen:
            if (
                getattr(self, "_mo2_session_loader_active", False)
                and self._mo2_session_polls % 3 == 0
            ):
                self._update_launch_loader(
                    "Opening MO2…",
                    f"Waiting for Mod Organizer in Skyrim's Proton prefix "
                    f"({self._mo2_session_polls}/80 checks).",
                )
            # Proton may take a few seconds to start Wine/MO2. Stop after a
            # bounded startup window rather than silently polling forever.
            if self._mo2_session_polls >= 80:
                self._mo2_session_timer.stop()
                self._mo2_session_prefix = None
                message = (
                    "MO2 did not appear in Skyrim's existing Proton prefix within 120 seconds. "
                    "No automatic inventory refresh was triggered."
                )
                self._log(message)
                if getattr(self, "_mo2_session_loader_active", False):
                    self._mo2_session_loader_active = False
                    self._close_launch_loader("MO2 startup not detected", message + " Check the MO2 launch log.")
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
