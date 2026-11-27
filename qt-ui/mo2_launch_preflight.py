"""Single source of truth for per-instance MO2 Skyrim launch readiness.

This module is read-only. It never repairs mappings, edits MO2 INI files,
starts Wine/Proton helpers, or changes the Steam compatdata prefix.
"""
from __future__ import annotations

from pathlib import Path

from proton_runtime import _steam_root_for_game, inspect_proton_build
from steam_prefix import steam_compat_prefix
from steam_drive_mapping import discover_wine_game_path, verify_wine_game_path
from steam_game_registry import configure_skyrim_se_registry
from mo2_targets import inspect_skyrim_launch_targets


def inspect_instance_launch(
    instance: dict,
    game_path: Path,
    proton_path: str | Path | None,
    installation: dict,
    profile_name: str | None = None,
) -> dict:
    """Return a verified launch plan or the first precise blocking reason."""
    checks: list[dict] = []

    def fail(name: str, message: str, details: dict | None = None) -> dict:
        checks.append({"name": name, "ok": False, "message": message})
        return {
            "ok": False,
            "reason": message,
            "failed_check": name,
            "checks": checks,
            "details": details or {},
        }

    def passed(name: str, message: str, details: dict | None = None) -> None:
        checks.append({"name": name, "ok": True, "message": message, "details": details or {}})

    if not isinstance(instance, dict) or not instance.get("name"):
        return fail("instance", "The selected MO2 instance has no verified name.")
    if not instance.get("is_skyrim_se"):
        return fail("instance_game", "This instance is not identified as Skyrim Special Edition.")

    game = Path(game_path).expanduser()
    if game.is_symlink() or not game.is_dir():
        return fail("game_directory", "The detected Skyrim directory is missing or unsafe.")
    try:
        game = game.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        return fail("game_directory", f"Could not resolve the Skyrim directory safely: {error}")
    if not (game / "SkyrimSE.exe").is_file():
        return fail("game_executable", "SkyrimSE.exe is missing from the detected game directory.")
    passed("game", "Skyrim Special Edition directory and executable verified.")

    if not proton_path:
        return fail("proton_selection", "No Proton runtime is selected.")
    proton = Path(proton_path).expanduser()
    runtime = inspect_proton_build(proton)
    if not runtime.get("ok"):
        return fail("proton_runtime", runtime.get("message", "The selected Proton runtime is invalid."))
    try:
        proton = proton.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        return fail("proton_runtime", f"Could not resolve the selected Proton runtime: {error}")
    passed("proton", "Selected Proton runtime verified.", {"path": str(proton)})

    if not installation.get("installed"):
        return fail(
            "mo2_installation",
            installation.get("message", "A verified MO2 installation was not found in Skyrim's prefix."),
        )
    required = ("prefix", "install", "executable")
    missing = [key for key in required if not installation.get(key)]
    if missing:
        return fail("mo2_installation", "MO2 installation metadata is incomplete: " + ", ".join(missing))
    prefix = Path(installation["prefix"]).expanduser()
    install = Path(installation["install"]).expanduser()
    executable = Path(installation["executable"]).expanduser()
    if any(path.is_symlink() for path in (prefix, install, executable)):
        return fail("mo2_paths", "The MO2 installation or prefix contains a symlink; launch was refused.")
    if not prefix.is_dir() or not install.is_dir() or not executable.is_file():
        return fail("mo2_paths", "The verified MO2 installation, prefix, or ModOrganizer.exe is missing.")
    try:
        prefix = prefix.resolve(strict=True)
        install = install.resolve(strict=True)
        executable = executable.resolve(strict=True)
        executable.relative_to(install)
        steam_root = _steam_root_for_game(game, Path.home()).resolve(strict=True)
        expected_prefix = steam_compat_prefix(steam_root).resolve(strict=True)
        if prefix != expected_prefix:
            return fail(
                "steam_prefix",
                "The MO2 prefix does not match Skyrim's existing Steam AppID 489830 compatdata prefix.",
                {"expected": str(expected_prefix), "actual": str(prefix)},
            )
        prefix.relative_to(steam_root)
    except (OSError, ValueError, RuntimeError) as error:
        return fail("steam_prefix", f"Steam/MO2 paths could not be safely verified: {error}")
    if installation.get("steam_root"):
        try:
            if Path(installation["steam_root"]).expanduser().resolve(strict=True) != steam_root:
                return fail("steam_root", "The discovered MO2 Steam root does not match the detected Skyrim library.")
        except (OSError, RuntimeError) as error:
            return fail("steam_root", f"The discovered Steam root is unsafe: {error}")
    passed(
        "mo2_installation",
        "MO2 executable and existing Skyrim AppID 489830 prefix verified.",
        {"executable": str(executable), "prefix": str(prefix), "install": str(install)},
    )

    mapping = discover_wine_game_path(prefix, game, steam_root)
    if not mapping.get("ok"):
        return fail("proton_drive_mapping", mapping.get("message", "No safe Proton drive mapping was found."))
    proton_mapping = verify_wine_game_path(
        proton, prefix, steam_root, mapping["windows_game_path"]
    )
    if not proton_mapping.get("ok"):
        return fail(
            "proton_drive_mapping",
            proton_mapping.get("message", "The derived Proton game path did not resolve to SkyrimSE.exe."),
            {"mapping": mapping},
        )
    passed(
        "proton_drive_mapping",
        "Existing Proton drive mapping resolves to the detected Skyrim installation.",
        {"windows_path": mapping["windows_game_path"], "linux_path": mapping["resolved_game_path"]},
    )

    registry = configure_skyrim_se_registry(
        prefix, game, steam_root, proton, mapping["windows_game_path"]
    )
    if not registry.get("ok"):
        return fail(
            "bethesda_registry",
            registry.get("message", "Read-only Skyrim registry verification failed."),
            {"mapping": mapping},
        )
    passed("bethesda_registry", "Bethesda registry path matches Skyrim (read-only verification).")

    targets = inspect_skyrim_launch_targets(
        prefix, game, instance_name=instance["name"],
        install_root=install, require_managed=False,
    )
    if not targets.get("ok") or not targets.get("targets"):
        return fail(
            "mo2_executable_target",
            targets.get("message", "No configured Skyrim/SKSE executable was verified for this instance."),
            {"warnings": targets.get("warnings", [])},
        )
    passed(
        "mo2_executable_target",
        f"Verified {len(targets['targets'])} Skyrim/SKSE launch target(s) for this instance.",
        {"targets": targets["targets"]},
    )

    if profile_name is not None:
        profiles = instance.get("profiles") or []
        if profile_name not in profiles:
            return fail("mo2_profile", "The selected MO2 profile is no longer in the verified profile list.")
        profile_path = Path(instance.get("selected_profile_path") or "")
        if profile_path and profile_path.exists() and profile_path.is_symlink():
            return fail("mo2_profile", "The selected MO2 profile path is a symlink; launch was refused.")
        passed("mo2_profile", f"MO2 profile '{profile_name}' is present in the verified profile list.")

    return {
        "ok": True,
        "reason": None,
        "failed_check": None,
        "checks": checks,
        "game_path": str(game),
        "proton_path": str(proton),
        "steam_root": str(steam_root),
        "prefix": str(prefix),
        "install_root": str(install),
        "installation": dict(installation),
        "mapping": mapping,
        "registry": registry,
        "targets": targets,
        "profile_name": profile_name,
    }
