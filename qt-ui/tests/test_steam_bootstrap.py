"""Tests for Wyrd's native Steam bootstrap argument handling."""
import unittest
from unittest.mock import patch

import steam_bootstrap


class SteamBootstrapTests(unittest.TestCase):
    def test_requires_explicit_bootstrap_flag(self):
        with self.assertRaises(SystemExit):
            steam_bootstrap.main([])

    def test_missing_launcher_returns_error_without_spawning(self):
        with patch.object(steam_bootstrap.Path, "resolve") as resolve:
            resolve.return_value.parent.__truediv__.return_value.is_file.return_value = False
            self.assertEqual(steam_bootstrap.main(["--steam-bootstrap"]), 2)


if __name__ == "__main__":
    unittest.main()
