"""Read and classify diagnostic output from MO2/Proton launch logs."""
from __future__ import annotations

from pathlib import Path


def read_launch_diagnostics(log_path: Path, max_bytes: int = 65536) -> dict:
    """Read the tail of Wyrd's launch diagnostics without changing the log."""
    path = Path(log_path)
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            content = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "message": "No launch diagnostic log exists yet.", "content": ""}
    except OSError as error:
        return {"ok": False, "message": f"Could not read launch diagnostics: {error}", "content": ""}
    return {"ok": True, "path": str(path), "content": content, "truncated": size > max_bytes}



def classify_launch_diagnostics(content: str) -> list[dict]:
    """Classify common MO2/Proton launch-log symptoms into actionable hints."""
    text = content.lower()
    rules = [
        ("missing_executable", ("could not find executable", "executable not found", "file not found", "no such file or directory"),
         "MO2 could not resolve the selected executable. Re-check the Skyrim install path and the executable selected in MO2."),
        ("missing_proton", ("proton not found", "compatibility tool not found", "wine: command not found"),
         "The selected Proton/Wine runtime appears unavailable. Re-detect Proton builds and select an installed runtime."),
        ("prefix_permission", ("permission denied", "access denied", "read-only file system"),
         "A launch path or managed prefix is inaccessible. Check ownership and filesystem permissions; Wyrd will not take ownership or overwrite it."),
        ("graphics_initialization", ("vulkan", "dxvk", "d3d11", "failed to create device", "gpu"),
         "Graphics/runtime initialization may have failed. Review Vulkan drivers and Proton runtime logs before changing game files."),
        ("mo2_usvfs", ("usvfs", "virtual filesystem", "hook failed", "failed to inject"),
         "MO2/USVFS initialization may have failed. Confirm MO2 is launching the game through its configured instance; do not bypass MO2."),
        ("wine_crash", ("wine: fatal", "unhandled exception", "segmentation fault", "crash"),
         "The Wine/Proton process appears to have reported a crash. Inspect the surrounding log context and runtime version."),
    ]
    findings = []
    for code, needles, hint in rules:
        if any(needle in text for needle in needles):
            findings.append({"code": code, "hint": hint})
    return findings
