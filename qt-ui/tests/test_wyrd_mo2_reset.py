import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from steamcompat import reset_wyrd_mo2
from wyrd_mo2_registry import empty_registry, save_registry
from wyrd_mo2 import managed_paths


class WyrdMO2ResetTests(unittest.TestCase):
    def _managed_layout(self, home):
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
        custom_storage = home / "custom-mods"
        custom_storage.mkdir()
        (custom_storage / "keep.txt").write_text("keep", encoding="utf-8")
        steam_sentinel = prefix / "pfx/user.reg"
        steam_sentinel.write_text("prefix stays", encoding="utf-8")
        return steam, prefix, install, instance, game, custom_storage, steam_sentinel

    def _registry(self, home, install, instance):
        registry = empty_registry()
        registry["installations"] = [
            {"id": "wyrd-install", "executable": str(install / "ModOrganizer.exe"), "owner": "wyrd"},
            {"id": "external-install", "executable": str(home / "external/MO2/ModOrganizer.exe"), "owner": "external"},
        ]
        registry["games"] = [{"id": "skyrim", "path": str(home / "Skyrim"), "app_id": "489830"}]
        registry["runtimes"] = [{"id": "proton", "path": str(home / "Proton")}]
        registry["instances"] = [
            {
                "id": "wyrd-instance", "installation_id": "wyrd-install",
                "instance_path": str(instance), "instance_type": "global",
                "game_id": "skyrim", "runtime_id": "proton",
                "config": str(instance / "ModOrganizer.ini"),
            },
            {
                "id": "external-instance", "installation_id": "external-install",
                "instance_path": str(home / "external/MO2/instances/Skyrim"),
                "instance_type": "global", "game_id": "skyrim", "runtime_id": "proton",
                "config": str(home / "external/MO2/instances/Skyrim/ModOrganizer.ini"),
            },
        ]
        save_registry(managed_paths(home)["config"] / "mo2-registry.json", registry)

    def test_removes_only_owned_install_and_registered_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam, prefix, install, instance, game, custom, sentinel = self._managed_layout(home)
            self._registry(home, install, instance)
            settings = managed_paths(home)["settings"]
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({
                "game_path": str(game), "selected_mo2_instance_id": "wyrd-instance"
            }), encoding="utf-8")
            with patch("steamcompat.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "install": str(install), "prefix": str(prefix),
            }):
                result = reset_wyrd_mo2(home, game)
            self.assertTrue(result["ok"], result)
            self.assertFalse(install.exists())
            self.assertFalse(instance.exists())
            self.assertTrue(prefix.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "prefix stays")
            self.assertEqual((custom / "keep.txt").read_text(encoding="utf-8"), "keep")
            cleaned = json.loads((settings.parent / "mo2-registry.json").read_text(encoding="utf-8"))
            self.assertEqual([x["id"] for x in cleaned["installations"]], ["external-install"])
            self.assertEqual([x["id"] for x in cleaned["instances"]], ["external-instance"])
            saved = json.loads(settings.read_text(encoding="utf-8"))
            self.assertNotIn("selected_mo2_instance_id", saved)

    def test_reset_without_registry_still_removes_verified_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _steam, prefix, install, _instance, game, _custom, sentinel = self._managed_layout(home)
            with patch("steamcompat.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "install": str(install), "prefix": str(prefix),
            }):
                result = reset_wyrd_mo2(home, game)
            self.assertTrue(result["ok"], result)
            self.assertFalse(install.exists())
            self.assertTrue(prefix.is_dir())
            self.assertTrue(sentinel.exists())

    def test_unverified_install_is_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _steam, _prefix, install, _instance, game, _custom, _sentinel = self._managed_layout(home)
            with patch("steamcompat.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": False, "message": "not owned",
            }):
                result = reset_wyrd_mo2(home, game)
            self.assertFalse(result["ok"])
            self.assertTrue(install.exists())


if __name__ == "__main__":
    unittest.main()
