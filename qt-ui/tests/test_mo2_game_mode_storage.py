"""Tests for read-only Game Mode storage inspection."""
import json
import tempfile
import unittest
from pathlib import Path

from mo2_game_mode_storage import inspect_game_mode_storage


class GameModeStorageTests(unittest.TestCase):
    def make_instance(self, root):
        marker = root / ".wyrd-managed.json"
        marker.write_text(json.dumps({"managed_by": "Wyrd"}), encoding="utf-8")
        ini = root / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim/ModOrganizer.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(
            "[General]\ngameName=Skyrim Special Edition\ngamePath=Z:\\games\\Skyrim Special Edition\n"
            "selected_profile=Default\n[Settings]\nmod_directory=Z:\\mods\\Skyrim\n"
            "profile_directory=Z:\\profiles\\Skyrim\ndownload_directory=Z:\\downloads\n",
            encoding="utf-8",
        )
        return ini

    def test_reads_instance_and_storage_config_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ini = self.make_instance(root)
            report = inspect_game_mode_storage(root)
            self.assertTrue(report["ok"])
            self.assertEqual(len(report["instances"]), 1)
            instance = report["instances"][0]
            self.assertTrue(instance["is_skyrim_se"])
            self.assertEqual(instance["storage_paths"]["mod_directory"], "Z:\\mods\\Skyrim")
            self.assertEqual(instance["storage_paths"]["profile_directory"], "Z:\\profiles\\Skyrim")
            self.assertTrue(ini.is_file())
            self.assertFalse((root / "storage-inspection.json").exists())

    def test_detects_portable_config_at_wyrd_install_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / ".wyrd-managed.json"
            marker.write_text(json.dumps({"managed_by": "Wyrd"}), encoding="utf-8")
            install = root / "mo2"
            install.mkdir()
            ini = install / "ModOrganizer.ini"
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\ngamePath=Z:\\games\\Skyrim Special Edition\n"
                "selected_profile=Default\n[Settings]\nmod_directory=Z:\\mods\\Skyrim\n",
                encoding="utf-8",
            )
            report = inspect_game_mode_storage(root, install)
            self.assertTrue(report["ok"])
            self.assertEqual(len(report["instances"]), 1)
            self.assertEqual(report["instances"][0]["layout"], "portable")
            self.assertTrue(report["instances"][0]["is_skyrim_se"])
            self.assertEqual(report["instances"][0]["storage_paths"]["mod_directory"], "Z:\\mods\\Skyrim")
            self.assertTrue(ini.is_file())

    def test_unowned_prefix_is_not_inspected(self):
        with tempfile.TemporaryDirectory() as temp:
            report = inspect_game_mode_storage(Path(temp))
            self.assertFalse(report["ok"])
            self.assertEqual(report["instances"], [])


if __name__ == "__main__":
    unittest.main()
