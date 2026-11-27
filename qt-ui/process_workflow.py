"""Launch-process monitoring and diagnostic presentation workflows."""
from __future__ import annotations

from pathlib import Path

from launch_models import GameProcess
from ui_path_helpers import _steam_prefix_for_game_path
from process_monitor import (
    launch_process_status, detect_skyrim_processes, detect_mo2_processes,
    _proc_identity, _is_descendant_process,
)
from launch_log_diagnostics import read_launch_diagnostics, classify_launch_diagnostics



class ProcessMonitorWorkflowMixin:
    def _refresh_session_ui_status(self) -> None:
        """Show a compact, live view of MO2 and Skyrim process state."""
        if not hasattr(self, "session_status_label"):
            return

        pending = getattr(self, "_steam_launch_pending", None)
        launch_waiting = getattr(self, "_mo2_launch_wait_pending", None)
        session = self.launch_session
        prefix = (
            Path(session.prefix_root) if session.prefix_root else
            _steam_prefix_for_game_path(self.game_state.get("game_dir"))
        )

        if pending is not None:
            status, detail, state_name = (
                "Opening Steam",
                "Waiting for Steam before handing the launch to MO2.",
                "active",
            )
        elif session.state in {"handoff", "waiting", "unknown"} or launch_waiting is not None:
            mo2_scan = detect_mo2_processes(prefix) if prefix else {"ok": False, "processes": []}
            mo2_running = bool(mo2_scan.get("ok") and mo2_scan.get("processes"))
            if mo2_running:
                status = "MO2 running"
                detail = "Mod Organizer is open; waiting for Skyrim to start."
            else:
                status = "Launching MO2"
                detail = "Waiting for Mod Organizer to appear in Skyrim's Proton prefix."
            state_name = "active"
        elif session.state == "detected" and any(
            process.matches_current_process() for process in session.game_processes
        ):
            status = "Skyrim running"
            detail = f"{session.executable_title or 'Skyrim'} is running through MO2."
            state_name = "running"
        elif session.state == "game-exited":
            status = "Exited"
            detail = "Skyrim has closed. You can launch it again when ready."
            state_name = "idle"
        elif session.state in {"failed", "timeout"}:
            status = "Launch failed"
            detail = "The last launch did not reach a confirmed game process. Check Launch Diagnostics."
            state_name = "failed"
        else:
            mo2_scan = detect_mo2_processes(prefix) if prefix else {"ok": False, "processes": []}
            mo2_running = bool(mo2_scan.get("ok") and mo2_scan.get("processes"))
            game_scan = detect_skyrim_processes(prefix) if prefix else {"ok": False, "processes": []}
            game_processes = [
                item for item in game_scan.get("processes", [])
                if item.get("prefix_attributed")
            ] if game_scan.get("ok") else []
            if game_processes:
                status = "Skyrim running"
                detail = "Skyrim/SKSE is running in the detected Skyrim Steam prefix."
                state_name = "running"
            elif mo2_running:
                status = "MO2 running"
                detail = "Mod Organizer is open in Skyrim's Steam prefix."
                state_name = "running"
            else:
                status = "Idle"
                detail = "No MO2 or Skyrim process detected in the selected Skyrim prefix."
                state_name = "idle"

        self.session_status_label.setText(status)
        self.session_status_label.setProperty("sessionState", state_name)
        self.session_detail_label.setText(detail)
        self.session_status_label.style().unpolish(self.session_status_label)
        self.session_status_label.style().polish(self.session_status_label)

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
            message = "Skyrim/SKSE was not detected within five minutes. Check the launch log before retrying."
            self.progress_label.setText(message)
            self._close_launch_loader("Game launch not confirmed", message)
            self._log(message)
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
            self._close_launch_loader(
                "Skyrim launched",
                "Detected " + detected + ". The game process is running.",
            )
            self._log("Launch loader dismissed after the Skyrim/SKSE process was verified.")
            self._start_game_process_watch()
            return
        if result["state"] in {"exited", "invalid"}:
            # Proton's short-lived wrapper can exit after it has successfully
            # spawned the MO2 GUI. Do not dismiss the loader or declare failure
            # while ModOrganizer.exe is still running in Skyrim's exact prefix.
            mo2_scan = detect_mo2_processes(scan_prefix)
            mo2_processes = mo2_scan.get("processes", []) if mo2_scan.get("ok") else []
            if mo2_processes:
                pids = ", ".join(str(item["pid"]) for item in mo2_processes)
                self.launch_session.state = "waiting"
                self.progress_label.setText("MO2 is running; waiting for Skyrim/SKSE to launch.")
                self._update_launch_loader(
                    "MO2 is running — waiting for Skyrim",
                    f"Mod Organizer is still active (PID {pids}). Waiting for the selected Skyrim/SKSE executable.",
                )
                return
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
            self._close_launch_loader(
                "MO2/Skyrim launch failed",
                "The Proton wrapper exited and no MO2 or Skyrim process remains in AppID 489830. Check the launch log.",
            )
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
            if self.launch_session.app_id == "489830":
                self._update_launch_loader(
                    "MO2 handoff active — waiting for Skyrim",
                    f"Checking for {self.launch_session.target_binary or 'Skyrim/SKSE'} "
                    f"({self.launch_session.poll_count}/{self._launch_poll_limit}).",
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

