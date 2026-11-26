"""Read-only launch diagnostics and Linux process monitoring."""
from __future__ import annotations

import os
from pathlib import Path

from launch_log_diagnostics import read_launch_diagnostics, classify_launch_diagnostics


def detect_game_processes(
    ancestor_pid: int, expected_binary: Path | str,
    prefix_root: Path | str | None = None,
    proc_root: Path = Path("/proc"),
    launch_token: str | None = None,
    ancestor_start_time: int | None = None,
) -> dict:
    """Find the configured game executable under a Wyrd-launched Proton context.

    A process is attributed when it is either a descendant of the exact launch
    wrapper, or it has inherited WYRD_LAUNCHED=1 and the exact registered
    STEAM_COMPAT_DATA_PATH. In both cases its command/executable basename must
    match the executable that Wyrd verified from the MO2 instance.
    """
    expected_name = Path(str(expected_binary).replace("\\", "/")).name.casefold()
    if not expected_name:
        return {"ok": False, "processes": [], "message": "No expected executable was supplied."}
    expected_prefix = None
    if prefix_root is not None:
        try:
            expected_prefix = str(Path(prefix_root).expanduser().resolve(strict=True))
        except (OSError, RuntimeError):
            return {"ok": False, "processes": [], "message": "Registered Proton prefix is unavailable for process attribution."}
    found = []
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError as error:
        return {"ok": False, "processes": [], "message": f"Could not inspect process table: {error}"}
    ancestor_identity = _proc_identity(ancestor_pid, proc_root)
    ancestor_valid = (
        ancestor_start_time is not None
        and ancestor_identity.get("start_time") == ancestor_start_time
    )
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == ancestor_pid:
            continue
        descendant = ancestor_valid and _is_descendant_process(pid, ancestor_pid, proc_root)
        try:
            argv_bytes = (entry / "cmdline").read_bytes()
        except OSError:
            argv_bytes = b""
        argv = [
            part.decode("utf-8", errors="replace")
            for part in argv_bytes.split(bytes([0])) if part
        ]
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            comm = ""
        try:
            exe_path = os.readlink(entry / "exe")
        except OSError:
            exe_path = ""
        names = {
            Path(value.replace("\\", "/").strip('"')).name.casefold()
            for value in argv if value.strip()
        }
        names.add(Path(exe_path.replace("\\", "/")).name.casefold())
        names.add(comm.casefold())
        if expected_name not in names:
            continue

        inherited_launch_marker = False
        if expected_prefix is not None:
            try:
                env_data = (entry / "environ").read_bytes()
            except OSError:
                env_data = b""
            variables = {}
            for item in env_data.split(bytes([0])):
                if b"=" not in item:
                    continue
                key, value = item.split(b"=", 1)
                variables[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
            inherited_launch_marker = (
                variables.get("WYRD_LAUNCHED") == "1"
                and launch_token is not None
                and variables.get("WYRD_LAUNCH_ID") == launch_token
                and variables.get("STEAM_COMPAT_DATA_PATH")
                and os.path.normcase(os.path.realpath(variables["STEAM_COMPAT_DATA_PATH"]))
                    == os.path.normcase(expected_prefix)
            )
        if not descendant and not inherited_launch_marker:
            continue
        identity = _proc_identity(pid, proc_root)
        found.append({
            "pid": pid, "binary": expected_name,
            "start_time": identity.get("start_time"),
            "ppid": identity.get("ppid"),
            "attributed_to_launch": "descendant" if descendant else "inherited-prefix-marker",
        })
    found.sort(key=lambda item: item["pid"])
    return {
        "ok": True, "processes": found,
        "message": (
            f"Matched {len(found)} process(es) for {expected_name}."
            if found else f"No process matching {expected_name} was attributed to this Wyrd launch yet."
        ),
    }


def detect_skyrim_processes(
    prefix_root: Path | None = None,
    proc_root: Path = Path("/proc"),
    launch_token: str | None = None,
) -> dict:
    """Read /proc for Skyrim/SKSE and attribute processes to Wyrd when possible."""
    expected = {"skyrimse.exe", "skyrimse", "skse64_loader.exe", "skse64.exe"}
    prefix = str(Path(prefix_root).expanduser().resolve()) if prefix_root else None
    detected = []
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError as error:
        return {"ok": False, "processes": [], "message": f"Could not inspect process table: {error}"}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv_data = (entry / "cmdline").read_bytes()
        except OSError:
            argv_data = b""
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
        except OSError:
            comm = ""
        try:
            exe_path = os.readlink(entry / "exe")
        except OSError:
            exe_path = ""
        env_readable = True
        env_error = None
        try:
            env_data = (entry / "environ").read_bytes()
        except OSError as error:
            # /proc/<pid>/environ may be restricted by ptrace/Yama or process
            # ownership. Do not misreport an inspection failure as an unset
            # variable.
            env_data = b""
            env_readable = False
            env_error = str(error)
        argv = [part.decode("utf-8", errors="replace") for part in
                argv_data.split(bytes([0])) if part]
        if not argv and not comm and not exe_path:
            continue
        normalize = lambda value: value.replace(chr(92), "/")
        names = {Path(normalize(arg)).name.lower() for arg in argv}
        names.add(Path(normalize(exe_path)).name.lower())
        names.add(comm)
        matches = sorted(names.intersection(expected))
        if not matches:
            continue
        env_parts = [part.decode("utf-8", errors="replace") for part in env_data.split(bytes([0])) if part]
        # A failed /proc/<pid>/environ read is unknown, not evidence that a
        # process belongs to Wyrd or that a launch marker is absent. Keep
        # prefix attribution separate because Steam and Wyrd share AppID 489830.
        prefix_attributed = bool(
            env_readable and prefix and (
                any(part.startswith("STEAM_COMPAT_DATA_PATH=") and
                    str(Path(part.split("=", 1)[1]).expanduser().resolve()) == prefix
                    for part in env_parts)
                or any(arg == f"STEAM_COMPAT_DATA_PATH={prefix}" for arg in argv)
            )
        )
        wyrd_marker = env_readable and any(
            part == "WYRD_LAUNCHED=1" for part in env_parts
        )
        if launch_token is not None:
            wyrd_marker = wyrd_marker and any(
                part == "WYRD_LAUNCH_ID=" + launch_token for part in env_parts
            )
        attributed = bool(prefix_attributed and wyrd_marker)
        # Only expose a small, non-secret audio-related environment allowlist.
        # This reads the actual Skyrim/SKSE process environment, not the Proton wrapper.
        audio_runtime = {}
        for key in ("WINEDLLOVERRIDES", "PULSE_LATENCY_MSEC"):
            value = next((part.split("=", 1)[1] for part in env_parts
                         if part.startswith(key + "=")), None)
            audio_runtime[key] = value if env_readable else None
        # Read-only observation of DLL mappings in the actual game process.
        # A mapped library is not proof that its audio path works.
        mapped_audio = {}
        maps_readable = True
        maps_error = None
        try:
            maps_text = (entry / "maps").read_text(encoding="utf-8", errors="replace")
            map_paths = set()
            for line in maps_text.splitlines():
                fields = line.split(None, 5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    map_paths.add(fields[5].replace("\\040", " "))
            normalized_maps = {Path(value).name.casefold(): value for value in map_paths}
            for library in (
                "xaudio2_7.dll", "xaudio2_8.dll", "xaudio2_9.dll",
                "xactengine3_7.dll", "x3daudio1_7.dll", "xapofx1_5.dll",
                "faudio.dll",
            ):
                mapped_audio[library] = normalized_maps.get(library)
        except OSError as error:
            maps_readable = False
            maps_error = str(error)
            mapped_audio = {}
        detected.append({
            "pid": int(entry.name), "binary": matches[0], "argv": argv,
            "audio_module_mappings": mapped_audio,
            "audio_module_mappings_readable": maps_readable,
            "audio_module_mappings_error": maps_error,
            "start_time": _proc_start_time(int(entry.name), Path(proc_root)),
            "attributed_to_wyrd": attributed, "prefix_attributed": prefix_attributed,
            "prefix": prefix if prefix_attributed else None,
            "audio_runtime_environment": audio_runtime,
            "audio_runtime_environment_readable": env_readable,
            "audio_runtime_environment_error": env_error,
        })
    detected.sort(key=lambda item: item["pid"])
    associated = [item for item in detected if item["attributed_to_wyrd"]]
    message = (f"Detected {len(associated)} Skyrim/SKSE process(es) associated with Wyrd's managed prefix."
               if associated else
               "Skyrim/SKSE process(es) detected, but none could be attributed to Wyrd's managed prefix."
               if detected else "No Skyrim/SKSE process was detected.")
    return {"ok": True, "processes": detected, "wyrd_processes": associated, "message": message}

def _proc_identity(pid: int, proc_root: Path = Path("/proc")) -> dict:
    """Return stable Linux process identity fields from /proc/<pid>/stat."""
    try:
        stat = (Path(proc_root) / str(pid) / "stat").read_text(encoding="utf-8")
        # comm is parenthesized and may itself contain spaces or ')' characters.
        fields = stat[stat.rfind(")") + 2:].split()
        return {"ppid": int(fields[1]), "start_time": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return {"ppid": None, "start_time": None}


def _proc_start_time(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """Return Linux process start time (clock ticks since boot), if readable."""
    return _proc_identity(pid, proc_root)["start_time"]


def _is_descendant_process(pid: int, ancestor_pid: int,
                           proc_root: Path = Path("/proc"),
                           max_depth: int = 64) -> bool:
    """Return whether pid currently descends from ancestor_pid using procfs PPIDs."""
    current = pid
    seen: set[int] = set()
    for _ in range(max_depth):
        if current == ancestor_pid:
            return True
        if current <= 1 or current in seen:
            return False
        seen.add(current)
        identity = _proc_identity(current, proc_root)
        parent = identity["ppid"]
        if parent is None:
            return False
        current = parent
    return False

def launch_process_status(pid: int, start_time: int | None = None,
                          proc_root: Path = Path("/proc")) -> dict:
    """Report wrapper state and optionally verify its original process identity."""
    if not isinstance(pid, int) or pid <= 0:
        return {"state": "invalid", "message": "Invalid launch process ID."}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"state": "exited", "message": "The Proton wrapper process has exited."}
    except PermissionError:
        return {"state": "unknown", "message": "The Proton wrapper exists but its status cannot be read."}
    except OSError as error:
        return {"state": "unknown", "message": f"Could not inspect Proton wrapper process: {error}"}
    if start_time is not None:
        actual_start = _proc_start_time(pid, proc_root)
        if actual_start is None:
            return {"state": "unknown", "message": "The Proton wrapper exists, but its process identity cannot be verified from /proc."}
        if actual_start != start_time:
            return {"state": "exited", "message": "The original Proton wrapper exited; its PID has since been reused by another process."}
    return {
        "state": "running",
        "message": "The Proton wrapper process is still running. Skyrim startup is not independently confirmed.",
    }
