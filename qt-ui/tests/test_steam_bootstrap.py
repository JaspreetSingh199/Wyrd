"""Tests for Wyrd's native Steam bootstrap argument handling."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import steam_bootstrap


class SteamBootstrapTests(unittest.TestCase):
    def test_requires_explicit_bootstrap_flag(self):
        with self.assertRaises(SystemExit):
            steam_bootstrap.main([])

    def test_missing_main_script_returns_error(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_script = Path(temp) / "steam_bootstrap.py"
            fake_script.write_text("# bootstrap", encoding="utf-8")
            with patch.object(steam_bootstrap, "__file__", str(fake_script)):
                self.assertEqual(steam_bootstrap.main(["--steam-bootstrap"]), 2)

    def test_runs_qt_main_with_steam_bootstrap_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "steam_bootstrap.py"
            main_script = root / "main.py"
            main_script.write_text("", encoding="utf-8")
            script.write_text("", encoding="utf-8")
            with patch.object(steam_bootstrap, "__file__", str(script)), \\
                 patch.object(steam_bootstrap.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(steam_bootstrap.main(["--steam-bootstrap"]), 0)
                command = run.call_args.args[0]
                self.assertEqual(command[0], steam_bootstrap.sys.executable)
                self.assertEqual(command[1], str(main_script))
                self.assertEqual(command[2], "--steam-bootstrap")
                self.assertTrue(run.call_args.kwargs["check"] is False)


if __name__ == "__main__":
    unittest.main()
