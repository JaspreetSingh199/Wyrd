"""Transactional preparation of the reviewed Game Mode links."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mo2_game_mode_links import create_owned_link, rollback_owned_link


def prepare_game_mode(report: dict[str, Any], state_dir: Path, *, confirmed: bool = False) -> dict[str, Any]:
    """Create only links listed in a successful preflight after explicit confirmation."""
    if not confirmed:
        return {"ok": False, "state": "confirmation-required",
                "message": "Explicit user confirmation is required; no filesystem changes were made.",
                "created": [], "rolled_back": []}
    if report.get("mode") != "read-only-preflight" or not report.get("ok"):
        return {"ok": False, "state": "invalid-preflight",
                "message": "A successful read-only preflight report is required.",
                "created": [], "rolled_back": []}
    links = report.get("proposed_links")
    if not isinstance(links, list) or not links:
        return {"ok": False, "state": "no-links",
                "message": "Preflight did not provide link candidates.",
                "created": [], "rolled_back": []}
    created: list[tuple[Path, Path]] = []
    for item in links:
        source, target = Path(item["source"]), Path(item["target"])
        result = create_owned_link(source, target, state_dir)
        if (not result.get("ok") or
                (result.get("state") == "linked" and not result.get("owned"))):
            if result.get("ok"):
                result = {"ok": False, "state": "unowned-link",
                          "message": "Matching symlink exists but is not recorded as Wyrd-owned; it was not adopted."}
            rolled_back = []
            rollback_errors = []
            for old_source, old_target in reversed(created):
                rollback = rollback_owned_link(old_source, old_target, state_dir)
                rolled_back.append({"source": str(old_source), "target": str(old_target), **rollback})
                if not rollback.get("ok"):
                    rollback_errors.append(rollback.get("message", "Rollback failed"))
            return {"ok": False, "state": "creation-failed",
                    "message": result.get("message", "Link creation failed."),
                    "created": [{"source": str(s), "target": str(t)} for s, t in created],
                    "rolled_back": rolled_back, "rollback_errors": rollback_errors}
        if result.get("state") == "created":
            created.append((source, target))
    return {"ok": True, "state": "prepared",
            "message": "Reviewed Game Mode links are prepared and ownership-recorded.",
            "created": [{"source": str(s), "target": str(t)} for s, t in created],
            "rolled_back": []}
