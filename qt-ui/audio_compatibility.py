"""Read-only audio inventory and explicitly confirmed Wyrd-prefix repair."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from proton_runtime import PREFIX_DIR_NAME, managed_paths, inspect_wyrd_installation
from steam_prefix import APP_ID


def _validated_wyrd_audio_prefix(home: Path | None = None) -> tuple[Path, dict]:
    """Resolve only Wyrd's canonical prefix after validating both ownership records."""
    requested_home = (home or Path.home()).expanduser()
    canonical_home = Path.home().expanduser().resolve()
    home = requested_home.resolve()
    # Explicit home overrides are used by isolated tests; normal GUI calls use Path.home().
    paths = managed_paths(home)
    prefix = paths["prefix"]
    expected = (paths["root"] / PREFIX_DIR_NAME).resolve()
    if prefix.resolve(strict=False) != expected:
        raise RuntimeError("The configured Wyrd prefix path is not canonical; refusing audio inspection.")
    install_marker = paths["install"] / ".wyrd-managed.json"
    prefix_marker = prefix / ".wyrd-managed.json"
    exe = paths["install"] / "ModOrganizer.exe"
    if not exe.is_file() or exe.is_symlink() or not install_marker.is_file() or install_marker.is_symlink():
        raise RuntimeError("Wyrd MO2 ownership cannot be established; refusing audio inspection.")
    try:
        install_data = json.loads(install_marker.read_text(encoding="utf-8"))
        prefix_data = json.loads(prefix_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("A Wyrd ownership marker is missing or unreadable; refusing audio inspection.") from error
    if install_data.get("managed_by") != "Wyrd" or prefix_data.get("managed_by") != "Wyrd":
        raise RuntimeError("Both MO2 and Proton prefix ownership markers must identify Wyrd.")
    if str(Path(install_data.get("prefix", prefix)).resolve()) != str(expected):
        raise RuntimeError("The MO2 ownership marker does not point to Wyrd's canonical prefix.")
    if install_data.get("game_app_id") not in (None, APP_ID) or prefix_data.get("game_app_id") not in (None, APP_ID):
        raise RuntimeError("The ownership marker does not identify Skyrim Special Edition.")
    if not (prefix / "pfx/drive_c").is_dir():
        raise RuntimeError("Wyrd's Proton prefix is not initialized.")
    # Refuse symlinks in both ownership roots and their target parents.
    for candidate in (paths["root"], paths["install"], prefix,
                      prefix / "pfx", prefix / "pfx/drive_c",
                      prefix / "pfx/drive_c/windows",
                      prefix / "pfx/drive_c/windows/system32",
                      prefix / "pfx/drive_c/windows/syswow64"):
        if candidate.is_symlink():
            raise RuntimeError(f"Symlink detected in managed prefix path: {candidate}")
    return prefix, {"install": install_data, "prefix": prefix_data}




def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_xact_dlls(home: Path | None = None) -> dict:
    """Return the XACT payload provenance for an explicitly installed, verified cache.

    Wyrd currently does not download or install redistributables automatically.
    A future importer may populate this cache only after validating Microsoft's
    signed package and every extracted file. Keeping this resolver fail-closed
    avoids ever guessing a Proton/Wine DLL as a Microsoft redistributable.
    """
    cache_root = managed_paths(home)["root"] / "cache" / "xact"
    paths = managed_paths(home)
    for candidate in (paths["root"], cache_root.parent, cache_root):
        if candidate.is_symlink():
            return {}
    required = {
        "xact": cache_root / "xactengine3_7.dll",
        "xact_x64": cache_root / "xactengine3_7_x64.dll",
    }
    found = {}
    for key, path in required.items():
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.read_bytes()[:2] != b"MZ":
                continue
            found[key] = {"source": str(path.resolve(strict=True)), "sha256": _sha256_file(path)}
        except OSError:
            continue
    return found

def inspect_audio_compatibility(home: Path | None = None) -> dict:
    """Inspect XACT/XAudio DLLs in the verified Wyrd prefix without mutation."""
    try:
        prefix, _ownership = _validated_wyrd_audio_prefix(home)
        windows = prefix / "pfx/drive_c/windows"
        dlls = {
            "xact": ("syswow64", "xactengine3_7.dll"),
            "xact_x64": ("system32", "xactengine3_7.dll"),
            "xaudio2": ("syswow64", "xaudio2_7.dll"),
            "xaudio2_x64": ("system32", "xaudio2_7.dll"),
            "x3daudio": ("syswow64", "x3daudio1_7.dll"),
            "x3daudio_x64": ("system32", "x3daudio1_7.dll"),
            "xapofx": ("syswow64", "xapofx1_5.dll"),
            "xapofx_x64": ("system32", "xapofx1_5.dll"),
        }
        components = {}
        for key, (arch_dir, filename) in dlls.items():
            target = windows / arch_dir / filename
            if target.is_symlink() or target.exists() and not target.is_file():
                raise RuntimeError(f"Unsafe XACT/XAudio target: {target}")
            components[key] = {
                "path": str(target),
                "state": "present" if target.is_file() else "missing",
                "sha256": _sha256_file(target) if target.is_file() else None,
            }
        state = inspect_wyrd_installation(home)
        proton_root = Path(state.get("metadata", {}).get("proton", ""))
        payloads = _resolve_xact_dlls(home)
        runtime_paths = []
        for path in (proton_root / "files/lib64/wine", proton_root / "files/lib/wine",
                     proton_root / "dist/lib64/wine"):
            if path.is_dir():
                runtime_paths.append(str(path))
        return {
            "ok": True, "safe": True, "prefix": str(prefix),
            "components": components,
            "runtime": {
                "proton_library_paths": runtime_paths,
                "faudio_ffmpeg_support": "unknown; needs live runtime verification",
            },
            "repair_recommended": any(
                components[name]["state"] == "missing" for name in ("xact", "xact_x64")
            ),
            "repair_available": set(payloads) == {"xact", "xact_x64"},
            "runtime_verified": False,
            "runtime_note": "File presence is not proof of dialogue audio; test an NPC conversation in Skyrim.",
            "message": "Wyrd prefix ownership verified; XACT and XAudio2 files inspected.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "ok": False, "safe": False, "prefix": None, "components": {},
            "repair_recommended": False, "runtime_verified": False,
            "message": str(error),
        }


def repair_audio_compatibility(home: Path | None = None, confirmed: bool = False) -> dict:
    """Install verified cached Microsoft XACT DLLs into Wyrd's prefix only.

    Existing Wine/user files are never replaced; changes are additive, journaled,
    hash-verified and rolled back if post-write verification fails.
    """
    if not confirmed:
        return {"ok": False, "modified": False,
                "message": "Repair requires explicit user confirmation."}
    try:
        prefix, _ownership = _validated_wyrd_audio_prefix(home)
        report = inspect_audio_compatibility(home)
        if not report.get("safe"):
            raise RuntimeError(report.get("message", "Wyrd prefix ownership check failed."))
        sources = _resolve_xact_dlls(home)
        if set(sources) != {"xact", "xact_x64"}:
            raise RuntimeError(
                "No verified Microsoft XACT payload cache is available. "
                "Wyrd did not download or install DLLs and no files were changed."
            )
        windows = prefix / "pfx/drive_c/windows"
        targets = {
            "xact": windows / "syswow64" / "xactengine3_7.dll",
            "xact_x64": windows / "system32" / "xactengine3_7.dll",
        }
        for target in targets.values():
            if target.is_symlink() or not target.parent.is_dir() or target.parent.is_symlink():
                raise RuntimeError(f"Unsafe XACT target path; no files were changed: {target}")
            if target.exists():
                raise RuntimeError(
                    f"XACT target already exists; Wyrd will not overwrite a Wine or user DLL: {target}"
                )
        # Stage and verify both DLLs before changing either target.
        staged_root = prefix / ".audio-repair-staging"
        staged_root.mkdir(mode=0o700, exist_ok=True)
        staged = {}
        for key, target in targets.items():
            source = Path(sources[key]["source"]).resolve(strict=True)
            if not source.is_file() or source.is_symlink():
                raise RuntimeError(f"Unsafe or missing Proton XACT source: {source}")
            stage_file = staged_root / f"{key}.dll"
            shutil.copy2(source, stage_file)
            if stage_file.read_bytes()[:2] != b"MZ":
                raise RuntimeError(f"Extracted XACT payload is not a PE DLL: {source}")
            if _sha256_file(stage_file) != sources[key]["sha256"]:
                raise RuntimeError(f"Staged XACT DLL hash mismatch for {key}.")
            staged[key] = stage_file
        log_path = prefix / "audio-repair.log"
        journal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "managed_prefix": str(prefix),
            "changes": [],
            "sources": {key: {"path": sources[key]["source"], "sha256": sources[key]["sha256"]}
                        for key in sources},
            "runtime_verified": False,
        }
        modified = []
        try:
            for key, target in targets.items():
                os.replace(staged[key], target)
                modified.append(key)
                journal["changes"].append({
                    "component": key, "target": str(target),
                    "sha256": _sha256_file(target),
                })
            verification = inspect_audio_compatibility(home)
            if not verification.get("safe") or any(
                verification["components"][key]["sha256"] != sources[key]["sha256"]
                for key in targets
            ):
                raise RuntimeError("Post-repair XACT verification failed.")
        except Exception:
            # Roll back only the exact files installed by this operation.
            for key in reversed(modified):
                target = targets[key]
                if target.is_file() and _sha256_file(target) == sources[key]["sha256"]:
                    target.unlink()
            raise
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(journal, indent=2) + "\n")
        return {
            "ok": True, "modified": bool(modified), "verified": True,
            "runtime_verified": False, "log_path": str(log_path),
            "message": "Microsoft XACT DLLs were installed and file hashes verified in Wyrd's prefix only. Skyrim dialogue audio is NOT runtime-verified.",
        }
    except (OSError, RuntimeError, ValueError) as error:
        return {"ok": False, "modified": False, "verified": False,
                "runtime_verified": False, "message": str(error)}
    finally:
        if "staged_root" in locals():
            shutil.rmtree(staged_root, ignore_errors=True)
