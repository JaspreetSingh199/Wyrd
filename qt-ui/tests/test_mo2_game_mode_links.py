"""Tests for safe MO2 Game Mode symlink ownership."""
import json
import tempfile
import unittest
from pathlib import Path

from mo2_game_mode_links import create_owned_link, inspect_link, rollback_owned_link


class GameModeLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "profile.ini").write_text("safe")
        self.target = self.root / "target" / "ModOrganizer"
        self.state = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def test_create_and_rollback_owned_link(self):
        created = create_owned_link(self.source, self.target, self.state)
        self.assertEqual(created["state"], "created")
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(self.target.resolve(), self.source.resolve())
        manifest = self.state / "game-mode-links.json"
        self.assertEqual(len(json.loads(manifest.read_text())["links"]), 1)
        removed = rollback_owned_link(self.source, self.target, self.state)
        self.assertTrue(removed["ok"])
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.is_symlink())

    def test_existing_real_directory_is_never_overwritten(self):
        self.target.mkdir(parents=True)
        (self.target / "user-file.txt").write_text("preserve")
        result = create_owned_link(self.source, self.target, self.state)
        self.assertFalse(result["ok"])
        self.assertTrue((self.target / "user-file.txt").exists())

    def test_foreign_symlink_is_never_adopted(self):
        other = self.root / "other"
        other.mkdir()
        self.target.parent.mkdir()
        self.target.symlink_to(other, target_is_directory=True)
        result = create_owned_link(self.source, self.target, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(self.target.resolve(), other.resolve())

    def test_rollback_refuses_unowned_link(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.source, target_is_directory=True)
        result = rollback_owned_link(self.source, self.target, self.state)
        self.assertFalse(result["ok"])
        self.assertTrue(self.target.is_symlink())


if __name__ == "__main__":
    unittest.main()
