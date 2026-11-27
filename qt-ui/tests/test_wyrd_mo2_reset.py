import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from steamcompat import reset_wyrd_mo2
from wyrd_mo2 import managed_paths


class WyrdMO2ResetTests(unittest.TestCase):
    def _layout(self, home):
        steam = home / ".local/share/Steam"
        prefix = steam / "steamapps/compatdata/489830"
        install = prefix / "pfx/drive_c/Modding/MO2"
        users = prefix / "pfx/drive_c/users/steamuser"
        instance = users / "AppData/Local/ModOrganizer/Skyrim"
        game = steam / "steamapps/common/Skyrim Special Edition"
        game.mkdir(parents=True)
        instance.mkdir(parents=True)
        install.mkdir(parents=True)
        (install / "ModOrganizer.exe").write_bytes(b"MO2")
        (install / ".wyrd-managed.json").write_text(json.dumps({
            "managed_by": "Wyrd",
            "game_app_id": "489830",
            "installation_mode": "skyrim-steam-prefix",
            "install": str(install),
        }), encoding="utf-8")
        (instance / "ModOrganizer.ini").write_text("[General]\n", encoding="utf-8")
        (instance / "mods").mkdir()
        (instance / "mods/example.txt").write_text("mod", encoding="utf-8")
        custom = home / "custom-mods"
        custom.mkdir()
        (custom / "keep.txt").write_text("keep", encoding="utf-8")
        sentinel = prefix / "pfx/user.reg"
        sentinel.write_text("prefix stays", encoding="utf-8")
        return prefix, install, instance, game, custom, sentinel

    def test_removes_only_verified_program_and_preserves_instances_and_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefix, install, instance, game, custom, sentinel = self._layout(home)
            settings = managed_paths(home)["settings"]
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({
                "game_path": str(game), "selected_mo2_instance_id": "legacy-id"
            }), encoding="utf-8")
            with patch("steam_mo2_management.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "managed": True,
                "install": str(install), "prefix": str(prefix),
            }):
                result = reset_wyrd_mo2(home, game)
            self.assertTrue(result["ok"], result)
            self.assertFalse(install.exists())
            self.assertTrue(instance.is_dir())
            self.assertEqual((instance / "mods/example.txt").read_text(encoding="utf-8"), "mod")
            self.assertTrue(prefix.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "prefix stays")
            self.assertEqual((custom / "keep.txt").read_text(encoding="utf-8"), "keep")
            saved = json.loads(settings.read_text(encoding="utf-8"))
            self.assertNotIn("selected_mo2_instance_id", saved)

    def test_missing_settings_do_not_block_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefix, install, _instance, game, _custom, sentinel = self._layout(home)
            with patch("steam_mo2_management.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "managed": True,
                "install": str(install), "prefix": str(prefix),
            }), patch.dict("os.environ", {"XDG_CONFIG_HOME": str(home / ".config")}):
                result = reset_wyrd_mo2(home, game)
            self.assertTrue(result["ok"], result)
            self.assertFalse(install.exists())
            self.assertTrue(prefix.is_dir())
            self.assertTrue(sentinel.exists())

    def test_unverified_install_is_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _prefix, install, _instance, game, _custom, _sentinel = self._layout(home)
            with patch("steam_mo2_management.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": False, "managed": False, "message": "not owned",
            }):
                result = reset_wyrd_mo2(home, game)
            self.assertFalse(result["ok"])
            self.assertTrue(install.exists())


if __name__ == "__main__":
    unittest.main()
