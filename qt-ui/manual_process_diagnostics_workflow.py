"""Manual, on-demand inspection of the current game process state."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from launch_models import GameProcess
from ui_path_helpers import _steam_prefix_for_game_path
from process_monitor import (
    launch_process_status, detect_skyrim_processes, _proc_identity,
    _is_descendant_process,
)


class ManualProcessDiagnosticsWorkflowMixin:
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
