"""Resolve configured Skyrim/SKSE targets from read-only MO2 inventory."""
from __future__ import annotations

from pathlib import Path

from mo2_instances import inspect_mo2_instances, _resolve_windows_path


def inspect_skyrim_launch_targets(prefix_root: Path, game_path: Path, instance_name: str | None = None,
                                 install_root: Path | None = None,
                                 require_managed: bool = True) -> dict:
    """Validate Skyrim/SKSE targets in a managed or explicitly supplied Steam prefix."""
    inventory = inspect_mo2_instances(
        prefix_root, install_root, require_managed=require_managed,
        detected_game_path=Path(game_path),
    )
    if not inventory.get("ok"):
        return {"ok": False, "targets": [], "message": inventory.get("message", "MO2 inspection failed.")}
    if not inventory.get("skyrim_instances"):
        return {"ok": False, "targets": [], "message": "No Skyrim Special Edition MO2 instance was identified."}

    targets = []
    warnings = []
    selected_instances = inventory["skyrim_instances"]
    if instance_name is not None:
        selected_instances = [item for item in selected_instances if item["name"] == instance_name]
        if not selected_instances:
            return {"ok": False, "targets": [], "message": "The selected Skyrim MO2 instance was not found."}
    for instance in selected_instances:
        # MO2 gamePath may be stored as either the game directory or
        # SkyrimSE.exe itself. The registered path is authoritative only
        # after it resolves beneath the detected Steam game directory.
        raw_game_path = instance.get("game_path", "").strip()
        game_root = Path(game_path).resolve()
        game_binary = (game_root / "SkyrimSE.exe").resolve()
        try:
            game_binary.relative_to(game_root)
        except ValueError:
            game_binary = None
        if raw_game_path:
            game_location = _resolve_windows_path(raw_game_path, prefix_root, game_root)
            if game_location:
                if game_location.is_dir():
                    candidate_binary = game_location / "SkyrimSE.exe"
                elif game_location.is_file() and game_location.name.casefold() == "skyrimse.exe":
                    candidate_binary = game_location
                else:
                    candidate_binary = None
                if candidate_binary and candidate_binary.is_file():
                    game_binary = candidate_binary.resolve()
                    try:
                        game_binary.relative_to(game_root)
                    except ValueError:
                        game_binary = None
            elif game_binary is None:
                warnings.append(f"{instance['name']}: MO2 gamePath could not be resolved safely.")
        # A detected game binary is not automatically a configured MO2 executable.
        # MO2 run --executable resolves the title from customExecutables.
        for entry in instance.get("executables", []):
            title = entry.get("title", "").strip()
            binary = _resolve_windows_path(entry.get("binary", ""), prefix_root, game_path)
            if not title or not binary or not binary.is_file():
                continue
            # Z: and other Proton drive mappings can resolve outside the
            # detected Steam library. Never launch a same-named executable
            # from a stale or unrelated game directory.
            try:
                binary.resolve(strict=True).relative_to(game_root)
            except (OSError, ValueError, RuntimeError):
                warnings.append(
                    f"{instance['name']}: executable '{title}' resolves outside the detected Skyrim directory."
                )
                continue
            name = binary.name.casefold()
            # The configured title is user-editable metadata. Never treat a
            # title containing "SKSE" as proof that the binary is SKSE.
            is_skse = name in {"skse64_loader.exe", "skse_loader.exe"}
            is_skyrim = name == "skyrimse.exe"
            if not (is_skse or is_skyrim):
                continue
            targets.append({
                "title": title,
                "binary": str(binary),
                "arguments": entry.get("arguments", ""),
                "working_directory": entry.get("working_directory") or str(binary.parent),
                "source": "MO2 custom executable",
                "verified": True,
            })

    unique = {}
    for target in targets:
        key = (target["title"].casefold(), target["binary"].casefold())
        unique[key] = target
    diagnostic = None if unique else ("unresolved_game_path" if warnings else "missing_executable_entry")
    message = (
        f"Verified {len(unique)} Skyrim/SKSE launch target(s) from MO2."
        if unique else
        ("No configured Skyrim or SKSE executable entry could be verified. "
         "Check the MO2 instance's game path and executable entries.")
    )
    return {
        "ok": bool(unique),
        "targets": list(unique.values()),
        "warnings": warnings,
        "diagnostic": diagnostic,
        "message": message,
    }
