"""Read-only preflight tests for experimental Game Mode."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_game_mode_preflight import build_game_mode_preflight, format_preflight_report


class GameModePreflightTests(unittest.TestCase):
    def test_missing_game_is_reported_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = build_game_mode_preflight(
                root,
                game_detection={"found": False, "steam_roots": []},
                proton_builds=[],
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["ready_for_prepare"])
            self.assertFalse((root / ".local/share/wyrd").exists())
            self.assertIn("No filesystem changes", format_preflight_report(result))

    def test_preflight_does_not_create_state_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("mo2_game_mode_preflight.detect_skyrim",
                       return_value={"found": False, "steam_roots": []}), \
                 patch("mo2_game_mode_preflight.discover_proton_builds", return_value=[]), \
                 patch("mo2_game_mode_preflight.inspect_wyrd_installation",
                       return_value={"installed": False, "message": "not installed",
                                     "external_installations": []}):
                result = build_game_mode_preflight(root)
            manifest = root / ".config/wyrd/game-mode/game-mode-links.json"
            self.assertFalse(manifest.exists())
            self.assertEqual(result["mode"], "read-only-preflight")

    def test_game_mode_mapping_uses_mo2_prefix_data_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steamapps = root / "SteamLibrary/steamapps"
            game = steamapps / "common/Skyrim Special Edition"
            game.mkdir(parents=True)
            game_prefix = steamapps / "compatdata/489830/pfx/drive_c"
            game_prefix.mkdir(parents=True)
            mo2_prefix = root / ".local/share/wyrd/compatdata/mo2-skyrim/pfx/drive_c"
            source = mo2_prefix / "users/steamuser/AppData/Local/ModOrganizer"
            source.mkdir(parents=True)
            (source / "ModOrganizer.ini").write_text("[General]\\ngameName=Skyrim Special Edition\\n")
            install = root / ".local/share/wyrd/mo2"
            install.mkdir(parents=True)
            exe = install / "ModOrganizer.exe"
            exe.write_text("placeholder")
            config = root / ".config/wyrd"
            config.mkdir(parents=True)
            detection = {"found": True, "game_dir": str(game), "library": str(steamapps)}
            proton = [{"ok": True, "path": str(root / "Proton")}]
            with patch.dict("os.environ", {"XDG_DATA_HOME": str(root / ".local/share"),
                                           "XDG_CONFIG_HOME": str(root / ".config")}):
                with patch("mo2_game_mode_preflight.inspect_wyrd_installation",
                           return_value={"installed": True, "executable": str(exe)}):
                    with patch("mo2_game_mode_preflight.inspect_game_mode_storage",
                               return_value={"ok": True, "instances": [], "message": "test inventory"}):
                        result = build_game_mode_preflight(root, detection, proton)
            self.assertEqual(len(result["proposed_links"]), 1)
            link = result["proposed_links"][0]
            self.assertEqual(Path(link["source"]), source)
            self.assertEqual(Path(link["target"]), game_prefix / "users/steamuser/AppData/Local/ModOrganizer")
            self.assertFalse(result["ready_for_prepare"])
            self.assertFalse((config / "game-mode/game-mode-links.json").exists())

    def test_saved_game_path_without_library_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "Skyrim Special Edition"
            game.mkdir()
            result = build_game_mode_preflight(
                root,
                game_detection={"found": True, "game_dir": str(game),
                                "library": "saved Wyrd selection"},
                proton_builds=[],
            )
            self.assertTrue(any("Could not reliably derive" in b for b in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
