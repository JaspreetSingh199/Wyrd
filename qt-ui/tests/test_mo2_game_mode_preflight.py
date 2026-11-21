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
