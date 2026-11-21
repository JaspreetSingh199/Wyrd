"""Safe, ownership-tracked MO2 Game Mode data-link helpers.

This is an experimental primitive only: it does not run Proton or launch MO2.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MANIFEST_NAME = "game-mode-links.json"


def manifest_path(state_dir: Path) -> Path:
    return Path(state_dir).expanduser() / MANIFEST_NAME


def inspect_link(source: Path, target: Path, state_dir: Path) -> dict:
    source, target = Path(source).expanduser(), Path(target).expanduser()
    manifest = manifest_path(state_dir)
    owned = False
    if manifest.is_file():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("links", [])
            owned = any(
                item.get("source") == str(source) and item.get("target") == str(target)
                for item in entries
            )
        except (OSError, ValueError):
            return {"ok": False, "owned": False, "state": "invalid-manifest",
                    "message": f"Cannot read ownership manifest: {manifest}"}
    if not source.exists():
        return {"ok": False, "owned": owned, "state": "missing-source",
                "message": f"MO2 data source does not exist: {source}"}
    if target.is_symlink():
        if target.resolve() == source.resolve():
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
    state_dir.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Recheck immediately before creation to reduce race risk.
    if target.exists() or target.is_symlink():
        return inspect_link(source, target, state_dir)
    target.symlink_to(source, target_is_directory=source.is_dir())
    manifest = manifest_path(state_dir)
    payload = {"version": 1, "links": []}
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    links = payload.setdefault("links", [])
    record = {"source": str(source), "target": str(target)}
    if record not in links:
        links.append(record)
    temp = manifest.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, manifest)
    return {"ok": True, "owned": True, "state": "created",
            "message": f"Created and recorded symlink: {target} -> {source}"}


def rollback_owned_link(source: Path, target: Path, state_dir: Path) -> dict:
    source, target = Path(source).expanduser(), Path(target).expanduser()
    manifest = manifest_path(state_dir)
    if not manifest.is_file():
        return {"ok": False, "state": "not-owned",
                "message": "No Wyrd ownership manifest exists; nothing was removed."}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "state": "invalid-manifest",
                "message": f"Ownership manifest is unreadable: {manifest}"}
    record = {"source": str(source), "target": str(target)}
    links = payload.get("links", [])
    if record not in links:
        return {"ok": False, "state": "not-owned",
                "message": "This link is not recorded as Wyrd-owned; it was left untouched."}
    if not target.is_symlink():
        return {"ok": False, "state": "changed-target",
                "message": "Recorded target is no longer a symlink; it was left untouched."}
    if target.resolve() != source.resolve():
        return {"ok": False, "state": "changed-link",
                "message": "Target now points elsewhere; it was left untouched."}
    target.unlink()
    links.remove(record)
    temp = manifest.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, manifest)
    return {"ok": True, "state": "removed",
            "message": f"Removed Wyrd-owned symlink: {target}"}
