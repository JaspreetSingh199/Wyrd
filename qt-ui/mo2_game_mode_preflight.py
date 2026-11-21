"""Read-only preflight for the experimental STL-style MO2 Game Mode."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mo2_game_mode_links import inspect_link, MANIFEST_NAME
from skyrim_detection import detect_skyrim, APP_ID
from wyrd_mo2 import managed_paths, inspect_wyrd_installation, discover_proton_builds
from mo2_game_mode_storage import inspect_game_mode_storage


def _item(name: str, status: str, detail: str, path: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "path": path}


def build_game_mode_preflight(
    home: Path | None = None,
    game_detection: dict | None = None,
    proton_builds: list[dict] | None = None,
) -> dict[str, Any]:
    """Inspect candidate resources without creating, changing, or deleting paths.

    The only proposed link is a candidate MO2 data directory under the existing
    Wyrd-managed install, linked into the Skyrim prefix's compatdata/pfx/drive_c
    tree. This is intentionally a conservative placeholder pending confirmation
    of STL's exact directory mapping for the installed MO2 build.
    """
    home = (home or Path.home()).expanduser()
    findings: list[dict[str, Any]] = []
    blockers: list[str] = []

    detection = game_detection if game_detection is not None else detect_skyrim(home)
    if detection.get("found"):
        game_dir = Path(detection["game_dir"]).expanduser()
        findings.append(_item("Skyrim installation", "ready", "Detected Skyrim SE.", str(game_dir)))
        library = Path(detection.get("library", "")).expanduser()
        if detection.get("library") and detection.get("library") != "saved Wyrd selection":
            # The detected library path is the steamapps directory. Its parent
            # is the Steam library root containing compatdata for this AppID.
            steamapps = library
            compatdata = steamapps / "compatdata" / APP_ID
            if (compatdata / "pfx/drive_c").is_dir():
                findings.append(_item("Skyrim Proton prefix", "ready",
                                      "Existing AppID prefix has pfx/drive_c.", str(compatdata)))
                game_prefix = compatdata
            else:
                findings.append(_item("Skyrim Proton prefix", "blocked",
                                      "Expected initialized prefix was not found. No prefix will be created.",
                                      str(compatdata)))
                blockers.append(f"Skyrim prefix is not initialized: {compatdata}")
                game_prefix = compatdata
        else:
            findings.append(_item("Skyrim Proton prefix", "blocked",
                                  "A manually saved game path does not establish the Steam library compatdata location."))
            blockers.append("Could not reliably derive Skyrim's Steam compatdata from the saved game path.")
            game_prefix = None
    else:
        game_dir = None
        game_prefix = None
        findings.append(_item("Skyrim installation", "blocked", "Skyrim SE AppID 489830 was not detected."))
        blockers.append("Skyrim SE installation was not detected.")

    builds = proton_builds if proton_builds is not None else discover_proton_builds(home)
    selected = builds[0] if builds else None
    if selected and selected.get("ok") and selected.get("path"):
        findings.append(_item("Proton build", "ready",
                              "A compatible Proton build was discovered (review selection before use).",
                              str(selected["path"])))
    else:
        findings.append(_item("Proton build", "blocked", "No compatible Proton build was discovered."))
        blockers.append("No compatible Proton build was discovered.")

    installation = inspect_wyrd_installation(home)
    if installation.get("installed"):
        mo2_exe = Path(installation["executable"])
        findings.append(_item("Wyrd-managed MO2", "ready",
                              "Wyrd ownership marker and executable were detected.", str(mo2_exe)))
    else:
        mo2_exe = None
        findings.append(_item("Wyrd-managed MO2", "blocked", installation.get("message", "MO2 not verified.")))
        blockers.append("A verified Wyrd-managed MO2 installation is required; external installs are not adopted.")

    paths = managed_paths(home)
    storage = inspect_game_mode_storage(paths["prefix"])
    if storage.get("ok"):
        findings.append(_item("MO2 instance storage inspection", "ready",
                              storage.get("message", "Read-only instance inventory completed."),
                              str(paths["prefix"])))
        for instance in storage.get("instances", []):
            findings.append(_item(
                f"MO2 instance: {instance['name']}",
                "review" if instance.get("is_skyrim_se") else "info",
                "Skyrim SE instance detected." if instance.get("is_skyrim_se") else "Instance detected; game identity not confirmed.",
                instance.get("path"),
            ))
            for key, value in instance.get("storage_paths", {}).items():
                findings.append(_item(f"MO2 configured {key}", "review",
                                      "Path read from MO2 configuration; not resolved or modified.", value))
    elif installation.get("installed"):
        findings.append(_item("MO2 instance storage inspection", "blocked",
                              storage.get("message", "Could not inspect MO2 instance storage.")))
    # Do not guess the MO2 storage layout. Report the standard candidate and
    # require an explicit existing directory until STL's mapping is confirmed.
    data_source = paths["install"] / "ModOrganizer"
    if data_source.is_dir():
        findings.append(_item("MO2 data source candidate", "review",
                              "Found a ModOrganizer directory under the Wyrd install; verify it is the active instance data.",
                              str(data_source)))
    else:
        findings.append(_item("MO2 data source candidate", "blocked",
                              "No verified MO2 data directory exists at the conservative candidate path.",
                              str(data_source)))
        blockers.append("MO2 data source mapping is not confirmed; no link will be planned.")

    proposed_links: list[dict] = []
    if game_prefix and data_source.is_dir():
        # Candidate target is deliberately explicit and is only inspected.
        target = game_prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer"
        link_state = inspect_link(data_source, target, paths["config"] / "game-mode")
        proposed_links.append({
            "source": str(data_source),
            "target": str(target),
            "status": link_state["state"],
            "ok": link_state["ok"],
            "owned": link_state.get("owned", False),
            "detail": link_state["message"],
        })
        if not link_state["ok"]:
            blockers.append("Proposed MO2 data link is blocked: " + link_state["message"])
        elif link_state["state"] == "linked" and not link_state.get("owned"):
            # Existing matching links are not adopted: ownership must be recorded.
            blockers.append("Matching symlink exists but is not recorded as Wyrd-owned.")
    else:
        findings.append(_item("MO2 data link", "not-planned",
                              "No link candidate was generated because source/prefix discovery is incomplete."))

    manifest = paths["config"] / "game-mode" / MANIFEST_NAME
    findings.append(_item("Wyrd link ownership manifest",
                          "present" if manifest.is_file() else "not-created",
                          "Read-only inspection; this preflight does not create the manifest.",
                          str(manifest)))

    # Even a clean initial scan is not authorization to mutate anything. Require
    # mapping confirmation and a later explicit prepare step.
    findings.append(_item("Filesystem changes", "none",
                          "Preflight is read-only. It did not create directories, links, or prefix files."))
    ready = not blockers and bool(proposed_links)
    return {
        "ok": ready,
        "mode": "read-only-preflight",
        "game_app_id": APP_ID,
        "findings": findings,
        "proposed_links": proposed_links,
        "blockers": blockers,
        "ready_for_prepare": False,
        "message": (
            "Preflight completed. A separate reviewed preparation step is still required."
            if ready else
            "Preflight completed with blockers. No files or directories were changed."
        ),
    }


def format_preflight_report(report: dict[str, Any]) -> str:
    """Render the preflight result as plain text for CLI and Qt."""
    lines = [
        "Wyrd Experimental Game Mode — READ-ONLY PREFLIGHT",
        f"AppID: {report.get('game_app_id')}",
        report.get("message", ""),
        "",
        "Findings:",
    ]
    for item in report.get("findings", []):
        lines.append(f"- [{item['status'].upper()}] {item['name']}: {item['detail']}")
        if item.get("path"):
            lines.append(f"    {item['path']}")
    if report.get("proposed_links"):
        lines.extend(["", "Proposed links (inspection only):"])
        for link in report["proposed_links"]:
            lines.append(f"- [{link['status']}] {link['target']} -> {link['source']}")
            lines.append(f"    {link['detail']}")
    lines.extend(["", "Blockers:"])
    lines.extend(f"- {value}" for value in report.get("blockers", []))
    if not report.get("blockers"):
        lines.append("- None detected by this inspection.")
    lines.extend(["", "No filesystem changes were made."])
    return "\n".join(lines)


def main() -> int:
    report = build_game_mode_preflight()
    print(format_preflight_report(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
