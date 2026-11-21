"""Tests for the reversible Wyrd Steam launcher integration."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import steam_shortcut


class SteamShortcutTests(unittest.TestCase):
    def test_desktop_entry_uses_native_bootstrap_and_not_proton(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap = root / "steam_bootstrap.py"
            bootstrap.write_text("# bootstrap", encoding="utf-8")
            entry = steam_shortcut._desktop_entry(bootstrap, root)
            self.assertIn("Name=Wyrd - Skyrim through MO2", entry)
            self.assertIn("--steam-bootstrap", entry)
            self.assertNotIn("proton run", entry.lower())

    def test_install_is_idempotent_and_remove_only_owned_files(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            steam = home / ".steam/root"
            steam.mkdir(parents=True)
            data_home = home / ".local/share"
            with patch.object(steam_shortcut, "find_steam_roots", return_value=[steam]), \
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
                first = steam_shortcut.install_steam_shortcut(home)
                self.assertTrue(first["ok"])
                desktop = Path(first["desktop_file"])
                self.assertTrue(desktop.is_file())
                second = steam_shortcut.install_steam_shortcut(home)
                self.assertTrue(second["ok"])
                self.assertTrue(second["already_installed"])
                desktop.write_text("modified by user", encoding="utf-8")
                refused = steam_shortcut.remove_steam_shortcut(home)
                self.assertFalse(refused["ok"])
                self.assertTrue(desktop.exists())

    def test_install_rolls_back_launcher_if_marker_creation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            steam = home / ".steam/root"
            steam.mkdir(parents=True)
            data_home = home / ".local/share"
            app_dir = data_home / "applications"
            with patch.object(steam_shortcut, "find_steam_roots", return_value=[steam]), \\
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}), \\
                 patch.object(steam_shortcut.os, "open", side_effect=[None, OSError("marker failure")]):
                # Force the second exclusive open (marker) to fail; the first
                # open creates the desktop entry.
                calls = []
                real_open = os.open
                def open_side_effect(path, flags, mode=0o777):
                    calls.append(path)
                    if len(calls) == 2:
                        raise OSError("marker failure")
                    return real_open(path, flags, mode)
                with patch.object(steam_shortcut.os, "open", side_effect=open_side_effect):
                    result = steam_shortcut.install_steam_shortcut(home)
                desktop = app_dir / steam_shortcut.DESKTOP_FILE
                self.assertFalse(result["ok"])
                self.assertFalse(desktop.exists())
                self.assertFalse((app_dir / steam_shortcut.MARKER_NAME).exists())

    def test_refuses_to_overwrite_unowned_desktop_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            steam = home / ".steam/root"
            steam.mkdir(parents=True)
            data_home = home / ".local/share"
            app_dir = data_home / "applications"
            app_dir.mkdir(parents=True)
            desktop = app_dir / steam_shortcut.DESKTOP_FILE
            desktop.write_text("user-owned", encoding="utf-8")
            with patch.object(steam_shortcut, "find_steam_roots", return_value=[steam]), \
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
                result = steam_shortcut.install_steam_shortcut(home)
                self.assertFalse(result["ok"])
                self.assertEqual(desktop.read_text(encoding="utf-8"), "user-owned")
                self.assertFalse((app_dir / steam_shortcut.MARKER_NAME).exists())

    def test_remove_refuses_modified_owned_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            steam = home / ".steam/root"
            steam.mkdir(parents=True)
            data_home = home / ".local/share"
            with patch.object(steam_shortcut, "find_steam_roots", return_value=[steam]), \\
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
                installed = steam_shortcut.install_steam_shortcut(home)
                desktop = Path(installed["desktop_file"])
                desktop.write_text("modified by user", encoding="utf-8")
                result = steam_shortcut.remove_steam_shortcut(home)
                self.assertFalse(result["ok"])
                self.assertTrue(desktop.exists())

    def test_remove_refuses_mismatched_ownership_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            data_home = home / ".local/share"
            app_dir = data_home / "applications"
            app_dir.mkdir(parents=True)
            desktop = app_dir / steam_shortcut.DESKTOP_FILE
            marker = app_dir / steam_shortcut.MARKER_NAME
            desktop.write_text("owned-looking", encoding="utf-8")
            marker.write_text('{"managed_by":"SomeoneElse"}', encoding="utf-8")
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
                result = steam_shortcut.remove_steam_shortcut(home)
                self.assertFalse(result["ok"])
                self.assertTrue(desktop.exists())


if __name__ == "__main__":
    unittest.main()
