import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from steamcompat import install_mo2_into_skyrim_prefix, inspect_skyrim_prefix_mo2_installation


class SkyrimPrefixMO2InstallerTests(unittest.TestCase):
    def test_installer_refuses_missing_or_uninitialized_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            proton = home / "Proton"
            (proton / "files/bin").mkdir(parents=True)
            (proton / "proton").write_text("#!/bin/sh\\n")
            (proton / "proton").chmod(0o755)
            (proton / "files/bin/wine").write_bytes(b"")
            with patch("steamcompat.inspect_proton_build", return_value={"ok": True}), patch(
                "steamcompat._steam_root_for_game", return_value=steam
            ):
                with self.assertRaisesRegex(RuntimeError, "prefix is missing or unsafe"):
                    install_mo2_into_skyrim_prefix(proton, game, home)

    def test_installer_refuses_symlinked_compatdata_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            outside = home / "outside"
            outside.mkdir()
            compatdata = steam / "steamapps/compatdata"
            compatdata.mkdir(parents=True)
            prefix = compatdata / "489830"
            prefix.symlink_to(outside, target_is_directory=True)
            proton = home / "Proton"
            with patch("steamcompat.inspect_proton_build", return_value={"ok": True}), patch(
                "steamcompat._steam_root_for_game", return_value=steam
            ):
                with self.assertRaisesRegex(RuntimeError, "prefix is missing or unsafe"):
                    install_mo2_into_skyrim_prefix(proton, game, home)
            self.assertEqual(list(outside.iterdir()), [])

    def test_installer_never_overwrites_existing_mo2_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            prefix = steam / "steamapps/compatdata/489830"
            install = prefix / "pfx/drive_c/Modding/MO2"
            install.mkdir(parents=True)
            (prefix / "pfx/drive_c/users").mkdir(parents=True)
            (install / "ModOrganizer.exe").write_bytes(b"existing")
            proton = home / "Proton"
            with patch("steamcompat.inspect_proton_build", return_value={"ok": True}), patch(
                "steamcompat._steam_root_for_game", return_value=steam
            ):
                result = install_mo2_into_skyrim_prefix(proton, game, home)
            self.assertTrue(result["ok"])
            self.assertTrue(result["already_installed"])
            self.assertEqual((install / "ModOrganizer.exe").read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
