"""Provision and verify Skyrim's Bethesda registry installation path."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from mo2_registry import _reject_symlinked_path_components
from mo2_instances import _resolve_windows_path
from proton_runtime import _runtime_environment
from steam_prefix import APP_ID, steam_compat_prefix


_REGISTRY_SECTION = (
    "software\\wow6432node\\bethesda softworks\\skyrim special edition"
)


def _read_installed_path(system_registry: Path) -> str | None:
    """Read Skyrim's Installed Path directly from Wine's system.reg.

    This is a read-only check: unlike \`proton run reg\`, it does not start a
    Wine process or wineserver. Wine escapes backslashes in registry strings.
    """
    if system_registry.is_symlink() or not system_registry.is_file():
        return None
    try:
        lines = system_registry.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None

    in_target_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            section = stripped[1:stripped.index("]")]
            normalized = section.replace("\\\\", "\\").casefold()
            in_target_section = normalized == _REGISTRY_SECTION
            continue
        if not in_target_section:
            continue
        match = re.match(r'^"Installed Path"="(.*)"$', stripped)
        if match:
            return match.group(1).replace("\\\\", "\\")
    return None


def configure_skyrim_se_registry(prefix_root: Path, game_path: Path,
                                 steam_root: Path, proton_path: Path,
                                 windows_game_path: str | None = None) -> dict:
    """Ensure Skyrim's registry path matches the verified Proton drive mapping.

    The common refresh path is read-only and launches no Wine processes. Only
    when the value is absent or stale does Wyrd invoke Proton once to import it;
    read-back is then verified from system.reg rather than launching a second
    Proton process for \`reg query\`.
    """
    try:
        prefix = _reject_symlinked_path_components(Path(prefix_root), "Steam compatdata prefix")
        game = _reject_symlinked_path_components(Path(game_path), "Skyrim game")
        steam = _reject_symlinked_path_components(Path(steam_root), "Steam client root")
        proton = Path(proton_path).expanduser()
        resolved_prefix = prefix.resolve(strict=True)
        resolved_steam = steam.resolve(strict=True)
        if resolved_prefix != steam_compat_prefix(resolved_steam).resolve(strict=True):
            raise RuntimeError("Registry provisioning refused: prefix is not Skyrim AppID 489830 compatdata.")
        resolved_game = game.resolve(strict=True)
        if not (resolved_game / "SkyrimSE.exe").is_file():
            raise RuntimeError("SkyrimSE.exe is missing from the detected game directory.")
        if proton.is_symlink() or not (proton / "proton").is_file():
            raise RuntimeError("Selected Proton runtime is missing or unsafe.")

        if not windows_game_path:
            raise RuntimeError("A dynamically discovered Proton game path is required; no drive letter is assumed.")
        mapped = _resolve_windows_path(windows_game_path, resolved_prefix)
        if mapped != resolved_game:
            raise RuntimeError("Generated Wine game path does not resolve to the detected Skyrim directory.")

        windows_path = windows_game_path.rstrip("\\") + "\\"
        system_registry = resolved_prefix / "pfx/system.reg"
        existing_path = _read_installed_path(system_registry)
        if existing_path and existing_path.casefold() == windows_path.casefold():
            return {
                "ok": True, "already_configured": True,
                "registry_key": r"HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                "value": "Installed Path", "windows_path": windows_game_path,
                "message": "Skyrim registry path already matches; verified system.reg without starting Proton.",
            }

        registry_path = windows_path.replace("\\", "\\\\")
        reg_text = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Bethesda Softworks\\Skyrim Special Edition]\n"
            f'"Installed Path"="{registry_path}"\n'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".reg", delete=False) as handle:
            handle.write(reg_text)
            reg_path = Path(handle.name)
        try:
            env = _runtime_environment(proton, resolved_steam, resolved_prefix)
            env.update({
                "SteamAppId": APP_ID, "SteamGameId": APP_ID,
                "STEAM_COMPAT_APP_ID": APP_ID,
                "STEAM_COMPAT_DATA_PATH": str(resolved_prefix),
            })
            # Keep Proton output off the Python heap. A runaway or verbose
            # wrapper must not accumulate unbounded captured stdout/stderr.
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log:
                result = subprocess.run(
                    [str(proton / "proton"), "run", "regedit", "/S", str(reg_path)],
                    cwd=str(resolved_steam), env=env,
                    stdout=log, stderr=subprocess.STDOUT,
                    timeout=120, check=False,
                )
                if result.returncode != 0:
                    log.seek(max(0, log.tell() - 4000))
                    details = log.read().strip()
                    raise RuntimeError(
                        "Proton regedit failed to import Skyrim's registry path."
                        + (" " + details[-2000:] if details else "")
                    )
        finally:
            reg_path.unlink(missing_ok=True)

        written_path = _read_installed_path(system_registry)
        if not written_path or written_path.casefold() != windows_path.casefold():
            raise RuntimeError(
                "Skyrim registry import could not be verified from Wine's system.reg. "
                f"Expected {windows_path}; found {written_path or 'no Installed Path value'}."
            )
        return {
            "ok": True, "already_configured": False,
            "registry_key": r"HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
            "value": "Installed Path", "windows_path": windows_game_path,
            "message": "Registered and verified Skyrim SE's path using one Proton invocation.",
        }
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "message": str(error)}
