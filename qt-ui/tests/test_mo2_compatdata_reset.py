import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wyrd_mo2 import managed_paths, reset_mo2_compatdata


class MO2CompatdataResetTests(unittest.TestCase):
    def _setup(self, home):
        paths = managed_paths(home)
        paths["prefix"].mkdir(parents=True)
        (paths["prefix"] / ".wyrd-managed.json").write_text(
            json.dumps({"managed_by": "Wyrd", "game_app_id": "489830"}), encoding="utf-8"
        )
        (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
        return paths



    def test_reset_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._setup(Path(tmp))
            result = reset_mo2_compatdata(Path(tmp), confirmed=False)
            self.assertFalse(result["ok"])
            self.assertTrue(paths["prefix"].exists())


    def test_reset_removes_only_managed_mo2_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = self._setup(home)
            install_sentinel = paths["install"] / "ModOrganizer.exe"
            paths["install"].mkdir()
            install_sentinel.write_bytes(b"keep")
            steam_compat = home / ".steam/steam/steamapps/compatdata/489830"
            steam_compat.mkdir(parents=True)
            steam_sentinel = steam_compat / "keep.txt"
            steam_sentinel.write_text("keep", encoding="utf-8")
            result = reset_mo2_compatdata(home, confirmed=True)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["removed"])
            self.assertFalse(paths["prefix"].exists())
            self.assertEqual(install_sentinel.read_bytes(), b"keep")
            self.assertEqual(steam_sentinel.read_text(encoding="utf-8"), "keep")


    def test_unmarked_prefix_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = managed_paths(Path(tmp))
            paths["prefix"].mkdir(parents=True)
            sentinel = paths["prefix"] / "user-data"
            sentinel.write_text("keep", encoding="utf-8")
            result = reset_mo2_compatdata(Path(tmp), confirmed=True)
            self.assertFalse(result["ok"])
            self.assertTrue(sentinel.exists())


    def test_symlinked_prefix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            outside = home / "outside"
            outside.mkdir()
            (outside / ".wyrd-managed.json").write_text(json.dumps({"managed_by": "Wyrd"}), encoding="utf-8")
            paths["prefix"].parent.mkdir(parents=True, exist_ok=True)
            paths["prefix"].symlink_to(outside, target_is_directory=True)
            result = reset_mo2_compatdata(home, confirmed=True)
            self.assertFalse(result["ok"])
            self.assertTrue((outside / ".wyrd-managed.json").exists())


if __name__ == "__main__":
    unittest.main()
