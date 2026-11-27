"""Resource guard for Wyrd-owned, short-lived Proton/Wine helper operations.

The preferred guard is a transient systemd user scope with cgroup-v2
MemoryMax and TasksMax limits. A process-group RSS monitor remains as a
fallback on desktops without a usable user systemd manager.

Never attach this guard to the persistent MO2 or Skyrim game launch session.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path


DEFAULT_MAX_RSS_BYTES = 6 * 1024 ** 3
DEFAULT_MAX_PROCESSES = 256
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_TERMINATE_GRACE_SECONDS = 5.0


class WineResourceLimitExceeded(RuntimeError):
    """A Wyrd-owned Wine helper exceeded its configured resource budget."""


def build_guarded_wine_command(command: list[str], *, max_memory_bytes: int = DEFAULT_MAX_RSS_BYTES,
                               max_processes: int = DEFAULT_MAX_PROCESSES) -> tuple[list[str], str]:
    """Wrap a helper command in a transient systemd cgroup scope when available.

    systemd-run --scope keeps the actual Proton command in the calling session
    while systemd accounts and limits every descendant in the scope cgroup.
    If user systemd/cgroup v2 is unavailable, return the original command and
    let the process-group RSS monitor enforce the fallback budget.
    """
    systemd_run = shutil.which("systemd-run")
    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    if not systemd_run or not controllers.is_file():
        return command, "process-group fallback (cgroup v2/systemd-run unavailable)"

    try:
        probe = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return command, "process-group fallback (user systemd manager unavailable)"
    if probe.returncode != 0:
        return command, "process-group fallback (user systemd manager unavailable)"

    unit = f"wyrd-wine-installer-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    memory = max(1, int(max_memory_bytes))
    tasks = max(1, int(max_processes))
    wrapped = [
        systemd_run, "--user", "--scope", "--quiet",
        f"--unit={unit}",
        f"--property=MemoryMax={memory}",
        f"--property=TasksMax={tasks}",
        "--",
        *command,
    ]
    return wrapped, f"systemd cgroup scope {unit} (MemoryMax={memory}, TasksMax={tasks})"


def process_group_usage(pgid: int, proc_root: Path = Path("/proc")) -> dict:
    """Return RSS and process count for one isolated Linux process group."""
    page_size = os.sysconf("SC_PAGE_SIZE")
    total_rss = 0
    processes = []
    try:
        entries = proc_root.iterdir()
    except OSError:
        return {"rss_bytes": 0, "process_count": 0, "processes": []}

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw_stat = (entry / "stat").read_text(encoding="utf-8")
            fields = raw_stat[raw_stat.rfind(")") + 2:].split()
            if len(fields) < 22:
                continue
            if int(fields[2]) != pgid:
                continue
            statm = (entry / "statm").read_text(encoding="ascii").split()
            rss_bytes = (int(statm[1]) if len(statm) > 1 else 0) * page_size
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            total_rss += rss_bytes
            processes.append({"pid": int(entry.name), "name": comm, "rss_bytes": rss_bytes})
        except (OSError, ValueError, IndexError):
            continue

    return {"rss_bytes": total_rss, "process_count": len(processes), "processes": processes}


def terminate_process_group(pgid: int, grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS) -> None:
    """Stop only the isolated process group created for the guarded helper."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def wait_for_wine_process(
    process: subprocess.Popen,
    *,
    timeout_seconds: float = 1800,
    max_rss_bytes: int = DEFAULT_MAX_RSS_BYTES,
    max_processes: int = DEFAULT_MAX_PROCESSES,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    on_warning=None,
    guard_description: str = "process-group RSS fallback",
) -> int:
    """Wait for a dedicated helper while enforcing fallback process-group limits.

    When launched inside a systemd scope, MemoryMax/TasksMax are the hard
    cgroup limits. This monitor remains active for warnings, timeout handling,
    and a second line of defence. Caller must use start_new_session=True.
    """
    pgid = process.pid
    deadline = time.monotonic() + timeout_seconds
    warned = False

    while True:
        return_code = process.poll()
        if return_code is not None:
            return return_code

        usage = process_group_usage(pgid)
        rss = usage["rss_bytes"]
        count = usage["process_count"]
        if rss > max_rss_bytes or count > max_processes:
            reason = (
                f"Wine resource guard stopped the MO2 installer: isolated helper "
                f"reached {rss / (1024 ** 3):.2f} GiB RSS across {count} processes "
                f"(fallback limits: {max_rss_bytes / (1024 ** 3):.0f} GiB and "
                f"{max_processes} processes; guard: {guard_description})."
            )
            terminate_process_group(pgid)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            raise WineResourceLimitExceeded(reason)

        if not warned and on_warning and rss >= 2 * 1024 ** 3:
            warned = True
            on_warning(
                f"Wine memory warning: installer helper uses {rss / (1024 ** 3):.2f} GiB "
                f"RSS across {count} processes. Guard: {guard_description}."
            )

        if time.monotonic() >= deadline:
            terminate_process_group(pgid)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            raise TimeoutError(
                "MO2 installer exceeded its 30-minute limit. Its isolated helper process group was stopped."
            )
        time.sleep(poll_seconds)
