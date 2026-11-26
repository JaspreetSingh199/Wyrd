"""Launch-process monitoring and diagnostic presentation workflows."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from launch_models import GameProcess
from ui_path_helpers import _steam_prefix_for_game_path
from proton_runtime import managed_paths
from process_monitor import (
    launch_process_status, detect_skyrim_processes, _proc_identity,
    _is_descendant_process, read_launch_diagnostics, classify_launch_diagnostics,
)


class ProcessMonitorWorkflowMixin:
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
