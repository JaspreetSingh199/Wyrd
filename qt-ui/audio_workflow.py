"""Audio inventory and audio-preference UI workflow methods."""
from __future__ import annotations

from pathlib import Path

from audio_compatibility import inspect_audio_compatibility
from wyrd_mo2 import write_settings


class AudioWorkflowMixin:
    def _log_audio_runtime_environment(self, processes: list[dict]) -> None:
        """Log the whitelisted audio environment read from actual game processes."""
        for item in processes:
            modules = item.get("audio_module_mappings", {})
            maps_readable = item.get("audio_module_mappings_readable")
            if maps_readable is False:
                reason = item.get("audio_module_mappings_error") or "access denied"
                self._log(
                    f"Audio DLL mappings from PID {item.get('pid')} ({item.get('binary')}): "
                    f"<unavailable: {reason}>"
                )
            else:
                observed = [name for name, path in modules.items() if path]
                absent = [name for name, path in modules.items() if not path]
                self._log(
                    f"Audio DLL mappings from PID {item.get('pid')} ({item.get('binary')}): "
                    f"observed={', '.join(observed) if observed else 'none'}; "
                    f"not observed={', '.join(absent) if absent else 'none'} "
                    "(not proof of audio functionality)"
                )
            audio_env = item.get("audio_runtime_environment", {})
            readable = item.get("audio_runtime_environment_readable")
            if readable is False:
                reason = item.get("audio_runtime_environment_error") or "access denied"
                self._log(
                    f"Audio runtime env from PID {item.get('pid')} ({item.get('binary')}): "
                    f"<unavailable: {reason}>; values were not inspected"
                )
                continue
            overrides = audio_env.get("WINEDLLOVERRIDES")
            pulse = audio_env.get("PULSE_LATENCY_MSEC")
            self._log(
                f"Audio runtime env from PID {item.get('pid')} ({item.get('binary')}): "
                f"WINEDLLOVERRIDES={overrides if overrides is not None else '<not present in initial environment>'}; "
                f"PULSE_LATENCY_MSEC={pulse if pulse is not None else '<not present in initial environment>'} "
                "(read from /proc; may not reflect later runtime changes)"
            )


    def _save_audio_option(self, enabled: bool) -> None:
        """Persist the optional XAudio2 override preference."""
        self._saved_settings["xaudio2_compat"] = bool(enabled)
        proton = self.proton_combo.currentData()
        game = self.game_state.get("game_dir")
        if proton and game and not self._restoring_selection:
            try:
                write_settings(None, Path(proton), Path(game), xaudio2_compat=bool(enabled))
                self._log("XAudio2 compatibility override preference saved: " + ("enabled" if enabled else "disabled"))
            except OSError as error:
                self._log(f"Could not save XAudio2 compatibility preference: {error}")


    def _refresh_audio_status(self, report: dict | None = None) -> None:
        """Render a read-only audio library inventory."""
        if report is None:
            report = inspect_audio_compatibility()
        self.audio_report = report
        if not report.get("ok") or not report.get("safe"):
            self.audio_detail.setText("Audio inspection is fail-closed: " + report.get("message", "Wyrd prefix ownership could not be verified."))
            return
        components = report.get("components", {})
        labels = [
            ("xact", "XACT (32-bit)"), ("xact_x64", "XACT (64-bit)"),
            ("xaudio2", "XAudio2 (32-bit)"), ("xaudio2_x64", "XAudio2 (64-bit)"),
            ("x3daudio", "X3DAudio 1.7 (32-bit)"), ("x3daudio_x64", "X3DAudio 1.7 (64-bit)"),
            ("xapofx", "XAPOFX 1.5 (32-bit)"), ("xapofx_x64", "XAPOFX 1.5 (64-bit)"),
        ]
        lines = [
            "LEGACY Wyrd-prefix inventory — NOT the Skyrim Steam-prefix runtime.",
            f"Inspected prefix: {report.get('prefix', 'unknown')}",
        ]
        lines += [f"{label}: {components.get(key, {}).get('state', 'unknown')}" for key, label in labels]
        lines += [
            "FAudio / FFmpeg support: UNKNOWN",
            "Skyrim Steam-prefix audio: NOT INSPECTED",
            "Dialogue audio: NOT VERIFIED (test an NPC conversation in Skyrim)",
        ]
        self.audio_detail.setText("\n".join(lines))


    def check_audio_libraries(self) -> None:
        """Manually refresh the read-only inventory of audio libraries."""
        self.audio_check_button.setEnabled(False)
        try:
            report = inspect_audio_compatibility()
            self._refresh_audio_status(report)
            if report.get("ok") and report.get("safe"):
                summary = ", ".join(f"{name}={item.get('state', 'unknown')}" for name, item in report.get("components", {}).items())
                self._log("Audio library check completed (read-only): " + summary)
            else:
                self._log("Audio library check failed closed: " + report.get("message", "ownership could not be verified"))
        except Exception as error:
            self._log(f"Audio library check failed: {error}")
            self.audio_detail.setText(f"Audio inspection failed: {error}")
        finally:
            self.audio_check_button.setEnabled(True)
