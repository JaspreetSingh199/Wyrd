"""Dedicated log views for application activity, launch output and host processes."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

from PySide6.QtGui import QTextCursor

from proton_runtime import managed_paths


class LogsWorkflowMixin:
    def refresh_application_log_view(self) -> None:
        """Load the persistent Wyrd application activity log."""
        path = managed_paths()["root"] / "logs" / "application.log"
        try:
            if path.is_symlink() or not path.is_file():
                self.log_view.setPlainText("No application log has been created yet.")
                return
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                lines = stream.readlines()[-1000:]
            self.log_view.setPlainText("".join(lines) or "The application log is empty.")
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        except OSError as error:
            self.log_view.setPlainText(f"Could not read application log: {error}")

    def refresh_launch_log_view(self) -> None:
        """Load the persistent SteamCompat launch log without modifying it."""
        path = managed_paths()["root"] / "logs" / "skyrim-launch.log"
        try:
            if path.is_symlink() or not path.is_file():
                self.launch_log_view.setPlainText("No Skyrim launch log has been created yet.")
                return
            # Read a bounded tail: Proton/Wine can be extremely verbose and
            # the launch log may grow for the lifetime of the game.
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 512 * 1024))
                content = stream.read().decode("utf-8", errors="replace")
            scrollbar = self.launch_log_view.verticalScrollBar()
            was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
            if self.launch_log_view.toPlainText() != content:
                self.launch_log_view.setPlainText(content or "The launch log is empty.")
                if was_at_bottom:
                    self.launch_log_view.moveCursor(QTextCursor.MoveOperation.End)
        except OSError as error:
            self.launch_log_view.setPlainText(f"Could not read launch log: {error}")

    def refresh_system_log_view(self) -> None:
        """Show a read-only snapshot of host memory and Wine/Proton processes."""
        output = [f"System snapshot: {datetime.now().astimezone().isoformat(timespec='seconds')}", ""]
        meminfo = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                fields = value.strip().split()
                if fields and fields[0].isdigit():
                    meminfo[key] = int(fields[0]) * 1024
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            output.append(
                f"Host memory: {available / (1024 ** 3):.2f} GiB available / "
                f"{total / (1024 ** 3):.2f} GiB total"
            )
            output.append(
                f"Swap: {meminfo.get('SwapFree', 0) / (1024 ** 3):.2f} GiB free / "
                f"{meminfo.get('SwapTotal', 0) / (1024 ** 3):.2f} GiB total"
            )
        except OSError as error:
            output.append(f"Host memory unavailable: {error}")

        output.extend(["", "Wine guard policy: MO2 installer only; 6 GiB RSS / 256-process hard limits.", "Persistent Skyrim game sessions are never automatically terminated by this guard.", "", "Wine / Proton related processes (largest RSS first):"])
        processes = []
        try:
            entries = Path("/proc").iterdir()
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
                    argv = (entry / "cmdline").read_bytes().replace(bytes([0]), b" ").decode(
                        "utf-8", errors="replace"
                    ).strip()
                    name = comm.casefold()
                    command = argv.casefold()
                    if not any(token in name or token in command for token in (
                        "wine", "wineserver", "winedbg", "proton", "modorganizer.exe",
                        "skyrimse.exe", "skse64_loader.exe",
                    )):
                        continue
                    status = (entry / "status").read_text(encoding="utf-8", errors="replace")
                    rss_kib = 0
                    ppid = 0
                    for line in status.splitlines():
                        if line.startswith("VmRSS:"):
                            rss_kib = int(line.split()[1])
                        elif line.startswith("PPid:"):
                            ppid = int(line.split()[1])
                    processes.append((rss_kib, int(entry.name), ppid, comm, argv[:180]))
                except (OSError, ValueError, IndexError):
                    continue
        except OSError as error:
            output.append(f"Process table unavailable: {error}")

        if not processes:
            output.append("No matching Wine / Proton processes found.")
        else:
            for rss_kib, pid, ppid, comm, command in sorted(processes, reverse=True)[:100]:
                output.append(
                    f"PID {pid:<7} PPID {ppid:<7} {rss_kib / 1024:>8.1f} MiB  "
                    f"{comm}  {command}"
                )
            if len(processes) > 100:
                output.append(f"... {len(processes) - 100} additional matching processes omitted.")
        self.system_log_view.setPlainText("\n".join(output))
        self.system_log_view.moveCursor(QTextCursor.MoveOperation.Start)
