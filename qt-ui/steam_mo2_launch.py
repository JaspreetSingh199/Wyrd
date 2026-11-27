"""MO2 and Skyrim launch adapters using Skyrim's existing Steam Proton prefix."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from proton_runtime import _runtime_environment, _steam_root_for_game, inspect_proton_build, managed_paths
from mo2_instances import inspect_mo2_instances
from steam_prefix import APP_ID, steam_compat_prefix
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_external_skyrim_prefix_mo2_installation,
)
from mo2_targets import inspect_skyrim_launch_targets


def launch_external_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                          home: Path | None = None,
                                          instance_name: str | None = None,
                                          xaudio2_compat: bool = False) -> dict:
    """Launch an existing unowned MO2 installation without adopting or modifying it."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_external_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed") or state.get("managed"):
        return {"ok": False, "message": state.get("message", "External MO2 installation is unavailable.")}
    prefix, install = Path(state["prefix"]), Path(state["install"])
    executable = Path(state["executable"])
    if (prefix.is_symlink() or install.is_symlink() or executable.is_symlink()
            or not executable.is_file() or not install.is_dir()):
        return {"ok": False, "message": "External MO2 paths changed or became unsafe after discovery; launch was refused."}
    try:
        executable.resolve(strict=True).relative_to(install.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "message": "External MO2 executable resolves outside its installation directory; launch was refused."}
    inventory = inspect_mo2_instances(
        prefix, install, require_managed=False, detected_game_path=game,
    )
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect external MO2 instances.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not present in the external installation."}
    steam_root = Path(state["steam_root"])
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable),
             "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not launch existing MO2: {error}"}
    return {"ok": True, "pid": process.pid, "managed": False,
            "prefix": str(prefix.resolve()), "instance": instance_name,
            "message": f"Started externally managed MO2 instance '{instance_name}' through Skyrim's Steam prefix. Existing MO2 files were not changed by Wyrd."}


def launch_mo2_in_skyrim_prefix(proton_path: Path, game_path: Path,
                                home: Path | None = None,
                                instance_name: str | None = None,
                                xaudio2_compat: bool = False) -> dict:
    """Launch verified MO2 using Skyrim's Steam AppID prefix and selected Proton."""
    home = (home or Path.home()).expanduser()
    proton = Path(proton_path).expanduser()
    game = Path(game_path).expanduser()
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}
    state = inspect_skyrim_prefix_mo2_installation(game, home)
    if not state.get("installed"):
        return {"ok": False, "message": state.get("message", "Wyrd-managed MO2 is not installed.")}
    steam_root = Path(state["steam_root"])
    prefix = Path(state["prefix"])
    install = Path(state["install"])
    executable = Path(state["executable"])
    inventory = inspect_mo2_instances(prefix, install, detected_game_path=game)
    if not inventory.get("ok"):
        return {"ok": False, "message": inventory.get("message", "Could not inspect MO2 instances in Skyrim's prefix.")}
    candidates = inventory.get("skyrim_instances", [])
    if instance_name is None:
        if len(candidates) == 1:
            instance_name = candidates[0]["name"]
        elif candidates:
            return {"ok": False, "needs_instance_selection": True,
                    "instances": [item["name"] for item in candidates],
                    "message": "Choose an MO2 Skyrim instance before launching."}
        else:
            return {"ok": False, "message": "No Skyrim Special Edition MO2 instance was found. Create one in MO2 first."}
    if instance_name not in [item["name"] for item in candidates]:
        return {"ok": False, "message": "The selected Skyrim MO2 instance is not verified in Skyrim's prefix."}
    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    try:
        process = subprocess.Popen(
            [str(proton / "proton"), "run", str(executable), "--instance", instance_name],
            cwd=str(install), env=env, start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 in Skyrim's Proton prefix: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "instance": instance_name,
        "message": f"Started the Proton wrapper for MO2 instance '{instance_name}' in Skyrim's Steam prefix. MO2 process startup is not yet independently confirmed.",
    }



def launch_skyrim_via_mo2_in_skyrim_prefix(
    proton_path: Path, game_path: Path, instance_name: str,
    executable_title: str, expected_binary: Path | str,
    *, prefix_path: Path | None = None, install_root: Path | None = None,
    steam_root_path: Path | None = None, xaudio2_compat: bool = False,
    profile_name: str | None = None, home: Path | None = None,
) -> dict:
    """Launch a verified MO2 Skyrim target using the established Steam-prefix context.

    This is the single Skyrim launch adapter for registered environments. It
    deliberately builds the same Steam AppID environment used by
    launch_mo2_in_skyrim_prefix(), including SteamAppId, SteamGameId,
    STEAM_COMPAT_APP_ID and STEAM_COMPAT_DATA_PATH. It never creates or resets
    the Steam prefix.
    """
    home = (home or Path.home()).expanduser()
    game = Path(game_path).expanduser()
    proton = Path(proton_path).expanduser()
    if game.is_symlink() or not game.is_dir():
        return {"ok": False, "message": "Skyrim game directory is missing or unsafe."}
    if proton.is_symlink() or not proton.is_dir():
        return {"ok": False, "message": "Selected Proton runtime is missing or unsafe."}
    validation = inspect_proton_build(proton)
    if not validation.get("ok"):
        return {"ok": False, "message": validation.get("message", "Selected Proton build is invalid.")}

    try:
        steam_root = Path(steam_root_path).expanduser() if steam_root_path else _steam_root_for_game(game, home)
        expected_prefix = steam_compat_prefix(steam_root)
        prefix = Path(prefix_path).expanduser() if prefix_path else expected_prefix
        if prefix.is_symlink() or not prefix.is_dir():
            return {"ok": False, "message": "Skyrim's existing Steam compatdata prefix is missing or unsafe."}
        if prefix.resolve(strict=True) != expected_prefix.resolve(strict=True):
            return {"ok": False, "message": "Registered prefix does not match Skyrim's existing Steam AppID 489830 prefix."}
        if not (prefix / "pfx/drive_c/users").is_dir():
            return {"ok": False, "message": "Skyrim's Steam Proton prefix is not initialized."}
        install = Path(install_root).expanduser() if install_root else prefix / "pfx/drive_c/Modding/MO2"
        executable = install / "ModOrganizer.exe"
        if install.is_symlink() or not install.is_dir() or executable.is_symlink() or not executable.is_file():
            return {"ok": False, "message": "Registered MO2 installation is missing or unsafe."}
        executable.resolve(strict=True).relative_to(install.resolve(strict=True))
        prefix.resolve(strict=True).relative_to(steam_root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "message": f"Steam compatibility paths could not be verified: {error}"}

    # Re-read the instance inventory at the launch boundary. The caller's
    # UI scan can become stale while the user chooses an executable or confirms.
    inventory = inspect_mo2_instances(
        prefix, install, require_managed=False, detected_game_path=game,
    )
    if not inventory.get("ok"):
        return {
            "ok": False,
            "message": inventory.get("message", "Could not re-verify MO2 instances before launch."),
        }
    selected_instance = next(
        (item for item in inventory.get("skyrim_instances", [])
         if item.get("name") == instance_name), None
    )
    if selected_instance is None:
        return {"ok": False, "message": "The selected MO2 instance is no longer present in the verified inventory."}
    if profile_name is not None and profile_name not in selected_instance.get("profiles", []):
        return {"ok": False, "message": "The selected MO2 profile is no longer present in the verified profile list."}

    targets = inspect_skyrim_launch_targets(
        prefix, game, instance_name=instance_name, install_root=install,
        require_managed=False,
    )
    if not targets.get("ok"):
        return {"ok": False, "message": targets.get("message", "Could not inspect the selected MO2 instance.")}
    wanted = Path(expected_binary).expanduser()
    matches = [
        item for item in targets.get("targets", [])
        if item.get("title") == executable_title
        and Path(item.get("binary", "")).resolve() == wanted.resolve()
    ]
    if len(matches) != 1 or wanted.is_symlink() or not wanted.is_file():
        return {"ok": False, "message": "Selected Skyrim executable is no longer uniquely verified in this MO2 instance."}

    env = _runtime_environment(proton, steam_root, prefix)
    env.update({
        "SteamAppId": APP_ID, "SteamGameId": APP_ID,
        "STEAM_COMPAT_APP_ID": APP_ID,
        "STEAM_COMPAT_DATA_PATH": str(prefix.resolve()),
    })
    if xaudio2_compat:
        current = env.get("WINEDLLOVERRIDES", "")
        overrides = [item for item in current.split(";")
                     if item and not item.lower().startswith("xaudio2_7=")]
        overrides.append("xaudio2_7=n,b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)

    launch_token = uuid.uuid4().hex
    env["WYRD_LAUNCHED"] = "1"
    env["WYRD_LAUNCH_ID"] = launch_token
    command = [
        str(proton / "proton"), "run", str(executable),
        "--logs", "--instance", instance_name,
    ]
    if profile_name is not None:
        command.extend(["--profile", profile_name])
    command.extend(["run", "--executable", executable_title])
    diagnostics = managed_paths(home)["root"] / "logs" / "skyrim-launch.log"
    try:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("a", encoding="utf-8") as log:
            log.write("\n=== Wyrd SteamCompat Skyrim launch ===\n")
            log.write(
                f"instance: {instance_name}\n"
                f"profile: {profile_name or 'MO2 saved selection'}\n"
                f"executable: {executable_title}\n"
            )
            log.write(
                f"binary: {wanted}\n"
                f"working_directory: {matches[0].get('working_directory') or wanted.parent}\n"
                f"arguments: {matches[0].get('arguments', '')}\n"
                f"audio_mode: {'xaudio2_compat' if xaudio2_compat else 'default'}\n"
            )
            log.write("command: " + json.dumps(command) + "\n")
            log.write("SteamAppId: 489830\n")
            log.flush()
            process = subprocess.Popen(
                command, cwd=str(install), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.write(f"wrapper_pid: {process.pid}\n")
            log.flush()
    except OSError as error:
        return {"ok": False, "message": f"Could not start MO2 through SteamCompat: {error}"}
    return {
        "ok": True, "pid": process.pid, "prefix": str(prefix.resolve()),
        "instance": instance_name, "profile": profile_name, "launch_token": launch_token,
        "log_path": str(diagnostics),
        "message": f"Started '{executable_title}' through MO2 instance '{instance_name}' using Skyrim's SteamCompat environment.",
    }
