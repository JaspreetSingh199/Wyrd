import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from steamcompat import (
    install_mo2_into_skyrim_prefix, steam_compat_prefix,
    inspect_skyrim_prefix_mo2_installation,
)


def fake_proton(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "proton").write_text("#!/bin/sh\n")
    (path / "proton").chmod(0o755)
    wine = path / "files/bin/wine"
    wine.parent.mkdir(parents=True)
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (62).to_bytes(2, "little")
    wine.write_bytes(header)
    return path


class SkyrimPrefixMO2InstallerTests(unittest.TestCase):
    def test_installs_into_initialized_steam_app_prefix_and_marks_only_mo2(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            prefix = steam / "steamapps/compatdata/489830"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (prefix / "pfx/drive_c").mkdir(parents=True)
            proton = fake_proton(home / "Proton")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }), patch("steamcompat._steam_root_for_game", return_value=steam), patch(
                "steamcompat._latest_mo2_archive", return_value=("2.5.2", "MO2.7z", "https://example.test/MO2.7z")
            ), patch("steamcompat._download_archive", side_effect=lambda url, dest, callback=None: dest.write_bytes(b"archive")), patch(
                "steamcompat._extract_mo2", side_effect=lambda archive, stage, callback=None: (
                    stage.mkdir(parents=True, exist_ok=True),
                    (stage / "ModOrganizer.exe").write_bytes(b"MO2"),
                    stage
                )[2]
            ):
                result = install_mo2_into_skyrim_prefix(proton, game, home)
            target = prefix / "pfx/drive_c/Modding/MO2"
            self.assertTrue(result["ok"], result)
            self.assertTrue((target / "ModOrganizer.exe").is_file())
            metadata = json.loads((target / ".wyrd-managed.json").read_text())
            self.assertEqual(metadata["installation_mode"], "skyrim-steam-prefix")
            self.assertEqual(metadata["game_app_id"], "489830")
            self.assertFalse((prefix / ".wyrd-managed.json").exists())
            self.assertTrue((prefix / "pfx/drive_c").is_dir())

    def test_refuses_missing_or_uninitialized_steam_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            proton = fake_proton(home / "Proton")
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                with self.assertRaisesRegex(RuntimeError, "compatdata prefix is missing"):
                    install_mo2_into_skyrim_prefix(proton, game, home)

    def test_installer_refuses_symlinked_proton_prefix_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            prefix = steam_compat_prefix(steam)
            prefix.mkdir(parents=True)
            outside = home / "outside-proton-prefix"
            (outside / "drive_c").mkdir(parents=True)
            (prefix / "pfx").symlink_to(outside, target_is_directory=True)
            proton = fake_proton(home / "Proton")
            with patch("steamcompat._steam_root_for_game", return_value=steam), patch(
                "steamcompat._latest_mo2_archive"
            ) as release:
                with self.assertRaisesRegex(RuntimeError, "symlinked Proton prefix path"):
                    install_mo2_into_skyrim_prefix(proton, game, home)
                release.assert_not_called()
            self.assertFalse((outside / "drive_c/Modding/MO2").exists())

    def test_inspector_rejects_symlinked_proton_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            prefix = steam_compat_prefix(steam)
            prefix.mkdir(parents=True)
            outside = home / "outside-proton-prefix"
            (outside / "drive_c/users").mkdir(parents=True)
            (prefix / "pfx").symlink_to(outside, target_is_directory=True)
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertFalse(state["installed"])
            self.assertIn("symlink", state["message"].lower())

    def test_inspector_rejects_symlinked_ownership_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            install = steam_compat_prefix(steam) / "pfx/drive_c/Modding/MO2"
            game.mkdir(parents=True)
            install.mkdir(parents=True)
            (install / "ModOrganizer.exe").write_bytes(b"MO2")
            outside_marker = home / "external-marker.json"
            outside_marker.write_text(json.dumps({
                "managed_by": "Wyrd", "game_app_id": "489830",
                "installation_mode": "skyrim-steam-prefix",
                "install": str(install),
            }))
            (install / ".wyrd-managed.json").symlink_to(outside_marker)
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertFalse(state["installed"])
            self.assertIn("marker is a symlink", state["message"])

    def test_refuses_unowned_existing_install_target_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            prefix = steam_compat_prefix(steam)
            (prefix / "pfx/drive_c").mkdir(parents=True)
            target = prefix / "pfx/drive_c/Modding/MO2"
            target.mkdir(parents=True)
            sentinel = target / "keep.txt"
            sentinel.write_text("untouched")
            proton = fake_proton(home / "Proton")
            with patch("steamcompat._steam_root_for_game", return_value=steam), patch(
                "steamcompat._latest_mo2_archive"
            ) as release:
                with self.assertRaisesRegex(RuntimeError, "not a verified Wyrd installation"):
                    install_mo2_into_skyrim_prefix(proton, game, home)
                release.assert_not_called()
            self.assertEqual(sentinel.read_text(), "untouched")

    def test_inspection_verifies_marker_only_in_mo2_install_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            prefix = steam / "steamapps/compatdata/489830"
            game = steam / "steamapps/common/Skyrim Special Edition"
            install = prefix / "pfx/drive_c/Modding/MO2"
            game.mkdir(parents=True)
            install.mkdir(parents=True)
            (prefix / "pfx/drive_c/users").mkdir()
            (install / "ModOrganizer.exe").write_bytes(b"MO2")
            (install / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "game_app_id": "489830",
                "installation_mode": "skyrim-steam-prefix",
                "install": str(install),
            }))
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertTrue(state["installed"], state)
            self.assertEqual(Path(state["executable"]), install / "ModOrganizer.exe")
            self.assertFalse((prefix / ".wyrd-managed.json").exists())

    def test_inspection_rejects_mismatched_install_path_and_unowned_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            prefix = steam / "steamapps/compatdata/489830"
            game = steam / "steamapps/common/Skyrim Special Edition"
            install = prefix / "pfx/drive_c/Modding/MO2"
            game.mkdir(parents=True)
            install.mkdir(parents=True)
            (prefix / "pfx/drive_c/users").mkdir()
            exe = install / "ModOrganizer.exe"
            exe.write_bytes(b"keep")
            marker = install / ".wyrd-managed.json"
            marker.write_text(json.dumps({
                "managed_by": "Wyrd", "game_app_id": "489830",
                "installation_mode": "skyrim-steam-prefix",
                "install": str(install / "elsewhere"),
            }))
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertFalse(state["installed"])
            self.assertEqual(exe.read_bytes(), b"keep")
            marker.unlink()
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertFalse(state["installed"])
            self.assertEqual(exe.read_bytes(), b"keep")

    def test_inspection_fails_closed_for_symlinked_install_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".local/share/Steam"
            prefix = steam / "steamapps/compatdata/489830"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (prefix / "pfx/drive_c/Modding").mkdir(parents=True)
            outside = home / "outside"
            outside.mkdir()
            (prefix / "pfx/drive_c/Modding/MO2").symlink_to(outside, target_is_directory=True)
            with patch("steamcompat._steam_root_for_game", return_value=steam):
                state = inspect_skyrim_prefix_mo2_installation(game, home)
            self.assertFalse(state["installed"])
            self.assertIn("symlink", state["message"].lower())


if __name__ == "__main__":
    unittest.main()
