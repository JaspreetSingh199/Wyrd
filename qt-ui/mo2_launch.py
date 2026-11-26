"""MO2 executable discovery and launch orchestration for Wyrd-managed prefixes."""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mo2_instances import inspect_mo2_instances, _resolve_windows_path
from proton_runtime import (
    _runtime_environment, _steam_root_for_game, _prefix_ready,
    inspect_proton_build, inspect_wyrd_installation, managed_paths,
)
from steam_prefix import APP_ID


def launch_mo2(home: Path | None = None, pick_instance: bool = False) -> dict:
    """Launch MO2 visibly through its configured Proton build."""
    home = (home or Path.home()).expanduser()
    state = inspect_wyrd_installation(home)
    if not state.get("installed"):
        return {"ok": False, "message": "Wyrd-managed MO2 is not installed yet."}
    metadata = state.get("metadata", {})
    proton_path = Path(metadata.get("proton", ""))
    game_path = Path(metadata.get("game_path", ""))
    paths = managed_paths(home)
    if not proton_path.is_dir() or not game_path.is_dir() or not _prefix_ready(paths["prefix"]):
        return {"ok": False, "message": "The saved Proton runtime, Skyrim directory, or MO2 prefix is missing. Refresh detection and repair the setup before launching."}
    validation = inspect_proton_build(proton_path)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is no longer valid.")}
    executable = paths["install"] / "ModOrganizer.exe"
    arguments = [str(executable)]
    if pick_instance:
        arguments.append("--pick")
    env = _runtime_environment(proton_path, Path(metadata["steam_root"]), paths["prefix"])
    try:
        process = subprocess.Popen(
            [str(proton_path / "proton"), "run", *arguments],
            cwd=str(paths["install"]), env=env, stdin=None, stdout=None, stderr=None,
            start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not launch MO2 through Proton: {error}"}
    return {"ok": True, "pid": process.pid,
            "message": "MO2 launched through Wyrd's dedicated Proton prefix."}


def inspect_skyrim_launch_targets(prefix_root: Path, game_path: Path,
                                 instance_name: str | None = None,
                                 install_root: Path | None = None,
                                 require_managed: bool = True) -> dict:
    """Compatibility facade for MO2 Skyrim target discovery."""
    from mo2_targets import inspect_skyrim_launch_targets as inspect

    return inspect(
        prefix_root, game_path, instance_name, install_root, require_managed
    )


def launch_skyrim_via_mo2(executable_title: str, instance_name: str,
                          home: Path | None = None, log_path: Path | None = None,
                          expected_binary: Path | str | None = None,
                          audio_mode: str = "default",
                          proton_path: Path | None = None,
                          game_path: Path | None = None,
                          prefix_root: Path | None = None,
                          install_root: Path | None = None,
                          require_managed: bool = True,
                          steam_root_path: Path | None = None) -> dict:
    """Start an MO2 configured-executable handoff with explicit runtime paths.

    Supplying prefix_root/install_root selects the already-initialized Steam
    AppID prefix. The legacy Wyrd prefix remains supported for compatibility.
    """
    home = (home or Path.home()).expanduser()
    title = executable_title.strip()
    instance = instance_name.strip()
    if not title or not instance:
        return {"ok": False, "message": "An MO2 instance and configured executable title are required."}
    if any(char in title + instance for char in ("\x00", "\r", "\n")):
        return {"ok": False, "message": "The selected MO2 instance or executable title is invalid."}

    explicit_prefix = prefix_root is not None or install_root is not None
    if explicit_prefix and (prefix_root is None or install_root is None or proton_path is None or game_path is None):
        return {"ok": False, "message": "Steam-prefix launch requires explicit Proton, game, prefix and MO2 install paths."}
    if explicit_prefix:
        proton = Path(proton_path).expanduser()
        game = Path(game_path).expanduser()
        prefix = Path(prefix_root).expanduser()
        install = Path(install_root).expanduser()
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "message": "Skyrim's Steam compatdata prefix is missing or unsafe."}
        try:
            prefix.resolve(strict=True).relative_to(prefix.parent.resolve(strict=True))
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": "The selected compatdata prefix is outside its expected Steam compatdata directory."}
        if install.is_symlink() or not install.is_dir() or not (install / "ModOrganizer.exe").is_file():
            return {"ok": False, "message": "The selected MO2 install directory is missing or unsafe."}
        if steam_root_path is not None:
            steam_root = Path(steam_root_path).expanduser()
        elif prefix.parent.name == "compatdata" and prefix.parent.parent.name == "steamapps":
            steam_root = prefix.parents[2]
        else:
            try:
                steam_root = _steam_root_for_game(game, home)
            except RuntimeError:
                return {"ok": False, "message": "The Steam client root for this registered Skyrim game could not be resolved."}
        if not steam_root.is_dir():
            return {"ok": False, "message": "The associated Steam client root is missing or unsafe."}
    else:
        state = inspect_wyrd_installation(home)
        if not state.get("installed") or not state.get("prefix_ready"):
            return {"ok": False, "message": "Wyrd-managed MO2 and its dedicated prefix must be ready before launching Skyrim."}
        metadata = state.get("metadata", {})
        proton = Path(metadata.get("proton", ""))
        game = Path(metadata.get("game_path", ""))
        paths = managed_paths(home)
        prefix, install = paths["prefix"], paths["install"]
        steam_root = Path(metadata.get("steam_root", ""))
        if not proton.is_dir() or not game.is_dir():
            return {"ok": False, "message": "The saved Proton runtime or Skyrim directory is missing."}
        state_ok = True

    if not proton.is_dir() or not game.is_dir():
        return {"ok": False, "message": "Selected Proton runtime or Skyrim directory is unavailable."}
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}

    prefix_check = inspect_mo2_instances(
        prefix, install,
        require_managed=require_managed if explicit_prefix else True,
        detected_game_path=game,
    )
    instance_match = next(
        (item for item in prefix_check.get("skyrim_instances", []) if item["name"] == instance),
        None,
    ) if prefix_check.get("ok") else None
    if not instance_match:
        return {"ok": False, "message": "The selected Skyrim instance is no longer present in the selected MO2 prefix."}
    target_state = inspect_skyrim_launch_targets(
        prefix, game, instance_name=instance, install_root=install,
        require_managed=require_managed if explicit_prefix else True,
    )
    expected_path = None
    if expected_binary is not None:
        try:
            expected_path = Path(expected_binary).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return {"ok": False, "message": "The selected Skyrim executable is no longer available at its verified path."}
    matching_targets = [
        item for item in target_state.get("targets", [])
        if item["title"] == title
        and (expected_path is None or Path(item["binary"]).resolve() == expected_path)
    ]
    if len(matching_targets) != 1:
        return {
            "ok": False,
            "message": (
                "The selected Skyrim executable is no longer verified at the selected path."
                if expected_path is not None else
                "The selected executable title is ambiguous or is no longer verified in this MO2 instance."
            ),
        }
    target_match = matching_targets[0]
    if audio_mode not in {"default", "xaudio2_compat"}:
        return {"ok": False, "message": "Unknown audio compatibility test mode."}
    env = _runtime_environment(proton, steam_root, prefix)
    # Prefix identity alone is not Wyrd attribution: Steam-launched Skyrim
    # shares this AppID prefix. This marker is inherited by the MO2 handoff.
    launch_token = uuid.uuid4().hex
    env["WYRD_LAUNCHED"] = "1"
    env["WYRD_LAUNCH_ID"] = launch_token
    if audio_mode == "xaudio2_compat":
        current_overrides = env.get("WINEDLLOVERRIDES", "")
        overrides = [
            item for item in current_overrides.split(";")
            if item and not item.lower().startswith("xaudio2_7=")
        ]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)

    command = [
        str(proton / "proton"), "run", str(install / "ModOrganizer.exe"),
        "--instance", instance, "run", "--executable", title,
    ]
    diagnostics = Path(log_path) if log_path else managed_paths(home)["root"] / "logs" / "skyrim-launch.log"
    try:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("a", encoding="utf-8") as log:
            log.write("\n=== Wyrd Skyrim launch handoff ===\n")
            log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\n")
            log.write(f"instance: {instance}\nexecutable: {title}\nbinary: {target_match['binary']}\naudio_mode: {audio_mode}\n")
            log.write("command: " + json.dumps(command) + "\n")
            log.write("audio_override: " + ("WINEDLLOVERRIDES=" + env.get("WINEDLLOVERRIDES", "") if audio_mode == "xaudio2_compat" else "none; Proton defaults retained") + "\n")
            log.write("stdout/stderr are redirected to this diagnostic log.\n")
            log.flush()
            process = subprocess.Popen(
                command, cwd=str(install), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.write(f"wrapper_pid: {process.pid}\n")
            log.flush()
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 executable handoff: {error}"}
    return {
        "ok": True, "pid": process.pid, "command": command,
        "launch_token": launch_token,
        "log_path": str(diagnostics),
        "message": (
            f"Started the Proton wrapper for MO2 executable '{title}' "
            f"in instance '{instance}'. This does not yet confirm that MO2 or Skyrim started. "
            f"Diagnostics: {diagnostics}"
        ),
    }
