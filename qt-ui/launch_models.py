"""Data models for tracking MO2-mediated Skyrim launch sessions."""
from __future__ import annotations

from dataclasses import dataclass, field

from process_monitor import _proc_identity


@dataclass
class LaunchSession:
    """Metadata for one MO2-mediated launch handoff and monitoring session."""

    wrapper_pid: int | None = None
    wrapper_start_time: int | None = None
    log_path: str | None = None
    instance_name: str | None = None
    executable_title: str | None = None
    started_at: float | None = None
    poll_count: int = 0
    detected_processes: list[dict] = field(default_factory=list)
    process_identities: dict[int, int | None] = field(default_factory=dict)
    game_processes: list[GameProcess] = field(default_factory=list)
    prefix_root: str | None = None
    game_path: str | None = None
    app_id: str | None = None
    target_binary: str | None = None
    launch_token: str | None = None
    state: str = "idle"


@dataclass
class GameProcess:
    """Stable identity for a detected Skyrim/SKSE process."""

    pid: int
    binary: str
    start_time: int | None
    detection_source: str
    attribution_confidence: str

    def matches_current_process(self) -> bool:
        """Return false if the PID vanished or now belongs to a different process."""
        identity = _proc_identity(self.pid)
        return bool(identity and (
            self.start_time is None or identity.get("start_time") == self.start_time
        ))
