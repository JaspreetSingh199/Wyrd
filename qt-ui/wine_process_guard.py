"""Resource guard for short-lived Proton/Wine helper process groups.

Only use this for a Proton operation started with start_new_session=True.
Never attach it to the persistent MO2 or Skyrim game launch session.
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path
import subprocess


DEFAULT_MAX_RSS_BYTES = 6 * 1024 ** 3
DEFAULT_MAX_PROCESSES = 256
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_TERMINATE_GRACE_SECONDS = 5.0


class WineResourceLimitExceeded(RuntimeError):
    """A Wyrd-owned Wine helper exceeded its configured resource budget."""


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
            # comm is parenthesized and may contain spaces or parentheses.
            fields = raw_stat[raw_stat.rfind(")") + 2:].split()
            if len(fields) < 22:
                continue
            process_group = int(fields[2])
            if process_group != pgid:
                continue
            statm = (entry / "statm").read_text(encoding="ascii").split()
            rss_pages = int(statm[1]) if len(statm) > 1 else 0
            rss_bytes = rss_pages * page_size
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            total_rss += rss_bytes
            processes.append({
                "pid": int(entry.name),
                "name": comm,
                "rss_bytes": rss_bytes,
            })
        except (OSError, ValueError, IndexError):
            continue

    return {
        "rss_bytes": total_rss,
        "process_count": len(processes),
        "processes": processes,
    }


def terminate_process_group(
    pgid: int,
    grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
) -> None:
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
) -> int:
    """Wait while enforcing a memory/process-count budget on the isolated group.

    The caller must launch the helper with start_new_session=True so its PID is
    also the dedicated process-group ID. No system Wine process outside that
    group is inspected for termination or signalled.
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
                f"Wine resource guard stopped the MO2 installer: its isolated Proton "
                f"process group reached {rss / (1024 ** 3):.2f} GiB RSS across {count} "
                f"processes (limits: {max_rss_bytes / (1024 ** 3):.0f} GiB and "
                f"{max_processes} processes). Only this installer process group was stopped."
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
                f"Wine memory guard warning: installer process group is using "
                f"{rss / (1024 ** 3):.2f} GiB RSS ({count} processes); hard limit is "
                f"{max_rss_bytes / (1024 ** 3):.0f} GiB."
            )

        if time.monotonic() >= deadline:
            terminate_process_group(pgid)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            raise TimeoutError(
                "MO2 installer exceeded its 30-minute limit. Its isolated Proton process group was stopped."
            )

        time.sleep(poll_seconds)
