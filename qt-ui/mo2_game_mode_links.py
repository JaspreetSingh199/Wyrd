"""Safe, ownership-tracked MO2 Game Mode data-link helpers.

The inspection functions are read-only. Mutating operations are intentionally
small and require an explicit Wyrd ownership manifest.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "game-mode-links.json"
MANIFEST_VERSION = 1


class ManifestError(ValueError):
    """Raised when the ownership manifest is malformed or unsafe."""


def manifest_path(state_dir: Path) -> Path:
    return Path(state_dir).expanduser() / MANIFEST_NAME


def _normal_path(path: Path) -> str:
    # Do not resolve a missing destination: resolve(strict=False) can normalize
    # parents without requiring the target to exist.
    return str(Path(path).expanduser().absolute())


def _read_manifest(state_dir: Path) -> dict[str, Any]:
    path = manifest_path(state_dir)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "links": []}
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"Ownership manifest is not a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"Cannot read ownership manifest: {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise ManifestError(f"Unsupported or invalid ownership manifest: {path}")
    links = payload.get("links")
    if not isinstance(links, list):
        raise ManifestError(f"Invalid links list in ownership manifest: {path}")
    seen: set[tuple[str, str]] = set()
    for item in links:
        if not isinstance(item, dict) or set(item) != {"source", "target"}:
            raise ManifestError(f"Invalid link entry in ownership manifest: {path}")
        if not isinstance(item["source"], str) or not isinstance(item["target"], str):
            raise ManifestError(f"Invalid link paths in ownership manifest: {path}")
        pair = (item["source"], item["target"])
        if pair in seen:
            raise ManifestError(f"Duplicate link entry in ownership manifest: {path}")
        seen.add(pair)
    return payload


def _write_manifest(state_dir: Path, payload: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_path(state_dir)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ManifestError(f"Refusing to replace non-regular manifest path: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".game-mode-links-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def inspect_link(source: Path, target: Path, state_dir: Path) -> dict:
    source, target = Path(source).expanduser(), Path(target).expanduser()
    try:
        payload = _read_manifest(Path(state_dir).expanduser())
    except ManifestError as error:
        return {"ok": False, "owned": False, "state": "invalid-manifest",
                "message": str(error)}
    source_name, target_name = _normal_path(source), _normal_path(target)
    owned = any(item["source"] == source_name and item["target"] == target_name
                for item in payload["links"])
    if not source.exists():
        return {"ok": False, "owned": owned, "state": "missing-source",
                "message": f"MO2 data source does not exist: {source}"}
    if not source.is_dir():
        return {"ok": False, "owned": owned, "state": "invalid-source",
                "message": f"MO2 data source is not a directory: {source}"}
    if target.is_symlink():
        try:
            matches = target.resolve(strict=True) == source.resolve(strict=True)
        except OSError:
            matches = False
        if matches:
            return {"ok": True, "owned": owned, "state": "linked",
                    "message": "Target already points to the intended MO2 data."}
        return {"ok": False, "owned": owned, "state": "foreign-symlink",
                "message": f"Target is an existing symlink not pointing to source: {target}"}
    if target.exists():
        return {"ok": False, "owned": owned, "state": "occupied",
                "message": f"Target already exists and is not a symlink: {target}"}
    return {"ok": True, "owned": owned, "state": "missing-link",
            "message": "Target is absent; a link can be created safely."}


def create_owned_link(source: Path, target: Path, state_dir: Path) -> dict:
    source, target = Path(source).expanduser(), Path(target).expanduser()
    state_dir = Path(state_dir).expanduser()
    check = inspect_link(source, target, state_dir)
    if not check["ok"]:
        return check
    if check["state"] == "linked":
        return check
    try:
        payload = _read_manifest(state_dir)
        record = {"source": _normal_path(source), "target": _normal_path(target)}
        links = payload["links"]
        if record not in links:
            # Persist ownership intent before creating the filesystem link. If the
            # process stops afterwards, rollback can safely detect the absent link.
            links.append(record)
            _write_manifest(state_dir, payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive symlink creation fails rather than replacing an existing path.
        target.symlink_to(source, target_is_directory=True)
    except FileExistsError:
        return inspect_link(source, target, state_dir)
    except (OSError, ManifestError) as error:
        return {"ok": False, "owned": False, "state": "creation-failed",
                "message": f"Could not prepare owned symlink: {error}"}
    return {"ok": True, "owned": True, "state": "created",
            "message": f"Created and recorded symlink: {target} -> {source}"}


def rollback_owned_link(source: Path, target: Path, state_dir: Path) -> dict:
    source, target = Path(source).expanduser(), Path(target).expanduser()
    try:
        payload = _read_manifest(Path(state_dir).expanduser())
    except ManifestError as error:
        return {"ok": False, "state": "invalid-manifest", "message": str(error)}
    record = {"source": _normal_path(source), "target": _normal_path(target)}
    links = payload["links"]
    if record not in links:
        return {"ok": False, "state": "not-owned",
                "message": "This link is not recorded as Wyrd-owned; it was left untouched."}
    if not target.is_symlink():
        if not target.exists():
            # A recorded intent with no link is safe to forget.
            links.remove(record)
            try:
                _write_manifest(Path(state_dir).expanduser(), payload)
            except (OSError, ManifestError) as error:
                return {"ok": False, "state": "manifest-write-failed", "message": str(error)}
            return {"ok": True, "state": "already-absent",
                    "message": "Recorded link was absent; removed its ownership record."}
        return {"ok": False, "state": "changed-target",
                "message": "Recorded target is now a real path; it was left untouched."}
    try:
        matches = target.resolve(strict=True) == source.resolve(strict=True)
    except OSError:
        matches = False
    if not matches:
        return {"ok": False, "state": "changed-link",
                "message": "Target now points elsewhere; it was left untouched."}
    target.unlink()
    links.remove(record)
    try:
        _write_manifest(Path(state_dir).expanduser(), payload)
    except (OSError, ManifestError) as error:
        # The link is gone, but keep the stale record for safe, explicit recovery.
        return {"ok": False, "state": "manifest-write-failed",
                "message": f"Removed the symlink but could not update ownership manifest: {error}"}
    return {"ok": True, "state": "removed",
            "message": f"Removed Wyrd-owned symlink: {target}"}
