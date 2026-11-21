"""Tests for explicit, transactional Game Mode preparation."""
import tempfile
import unittest
from pathlib import Path

from mo2_game_mode_prepare import prepare_game_mode


class GameModePrepareTests(unittest.TestCase):
    def _report(self, source, target):
        return {"mode": "read-only-preflight", "ok": True,
                "proposed_links": [{"source": str(source), "target": str(target)}]}

    def test_requires_confirmation_without_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            target = root / "target"
            report = self._report(source, target)
            result = prepare_game_mode(report, root / "state")
            self.assertFalse(result["ok"])
            self.assertFalse(target.exists())
            self.assertFalse((root / "state").exists())

    def test_rejects_failed_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "source", root / "target"
            source.mkdir()
            report = self._report(source, target)
            report["ok"] = False
            result = prepare_game_mode(report, root / "state", confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse(target.exists())

    def test_creates_owned_link_after_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "source", root / "target"
            source.mkdir()
            result = prepare_game_mode(self._report(source, target), root / "state", confirmed=True)
            self.assertTrue(result["ok"])
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), source.resolve())
            self.assertEqual(len(result["created"]), 1)

    def test_does_not_adopt_existing_unowned_matching_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "source", root / "target"
            source.mkdir()
            target.symlink_to(source, target_is_directory=True)
            result = prepare_game_mode(self._report(source, target), root / "state", confirmed=True)
            self.assertFalse(result["ok"])
            self.assertTrue(target.is_symlink())
            self.assertFalse((root / "state/game-mode-links.json").exists())

    def test_failure_rolls_back_links_created_by_this_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source1, target1 = root / "source1", root / "target1"
            source2, target2 = root / "source2", root / "target2"
            source1.mkdir()
            source2.mkdir()
            target2.mkdir()  # occupied; must not be overwritten
            report = self._report(source1, target1)
            report["proposed_links"].append({"source": str(source2), "target": str(target2)})
            result = prepare_game_mode(report, root / "state", confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse(target1.exists())
            self.assertTrue(target2.is_dir())
            self.assertEqual(len(result["rolled_back"]), 1)
            self.assertTrue(result["rolled_back"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
