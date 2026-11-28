from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artwork_manager


class ArtworkManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "wyrd-artwork"
        self.instance = {
            "name": "Skyrim Special Edition",
            "path": "/tmp/mo2-instance-a",
            "selected_profile": "Default",
        }
        self.data_patch = patch.object(artwork_manager, "_data_dir", return_value=self.data_dir)
        self.data_patch.start()

    def tearDown(self):
        self.data_patch.stop()
        self.temp_dir.cleanup()

    def test_default_instance_artwork_is_persisted(self):
        first = artwork_manager.get_artwork(self.instance, "instance")
        second = artwork_manager.get_artwork(self.instance, "instance")
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())

    def test_profile_artwork_has_a_separate_persisted_key(self):
        instance_art = artwork_manager.get_artwork(self.instance, "instance")
        profile_art = artwork_manager.get_artwork(self.instance, "profile", "Default")
        mapping = artwork_manager._read_mapping()
        self.assertIn("instance:/tmp/mo2-instance-a", mapping)
        self.assertIn("profile:/tmp/mo2-instance-a:Default", mapping)
        self.assertTrue(instance_art.is_file())
        self.assertTrue(profile_art.is_file())

    def test_custom_artwork_is_copied_into_wyrd_data(self):
        source = Path(self.temp_dir.name) / "custom.png"
        source.write_bytes(b"test image payload")
        imported = artwork_manager.set_custom_artwork(
            self.instance, "profile", source, "Default"
        )
        self.assertTrue(imported.is_file())
        self.assertNotEqual(imported, source)
        self.assertTrue(str(imported).startswith(str(self.data_dir)))
        self.assertEqual(
            artwork_manager.get_artwork(self.instance, "profile", "Default"),
            imported,
        )

    def test_randomise_and_reset_keep_artwork_within_bundled_set(self):
        artwork_manager.get_artwork(self.instance, "instance")
        randomised = artwork_manager.randomize_artwork(self.instance, "instance")
        self.assertIn(randomised.name, artwork_manager.BUILTIN_ARTWORK)
        restored = artwork_manager.reset_artwork(self.instance, "instance")
        self.assertIn(restored.name, artwork_manager.BUILTIN_ARTWORK)

    def test_rejects_symlink_and_oversized_custom_artwork(self):
        source = Path(self.temp_dir.name) / "large.jpg"
        source.write_bytes(b"x")
        link = Path(self.temp_dir.name) / "linked.jpg"
        link.symlink_to(source)
        with self.assertRaises(OSError):
            artwork_manager.set_custom_artwork(self.instance, "instance", link)
        oversized = Path(self.temp_dir.name) / "oversized.jpg"
        with oversized.open("wb") as handle:
            handle.truncate(21 * 1024 * 1024)
        with self.assertRaises(OSError):
            artwork_manager.set_custom_artwork(self.instance, "instance", oversized)


if __name__ == "__main__":
    unittest.main()
