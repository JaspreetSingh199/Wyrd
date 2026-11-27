"""Audio inventory and audio-preference UI workflow methods."""
from __future__ import annotations

from pathlib import Path

from wyrd_settings import write_settings


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
