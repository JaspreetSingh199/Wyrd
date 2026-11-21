"""Tests for Wyrd's native Steam bootstrap."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import steam_bootstrap


class SteamBootstrapTests(unittest.TestCase):
    def test_requires_explicit_bootstrap_flag(self):
        with self.assertRaises(SystemExit):
            steam_bootstrap.main([])

    def test_environment_snapshot_is_allow_listed_and_detects_shortcut_identity(self):
        snapshot = steam_bootstrap.environment_snapshot({
            "SteamAppId": "3456789012",
            "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
            "SECRET_TOKEN": "must-not-appear",
        })
        self.assertTrue(snapshot["launched_from_steam"])
        self.assertEqual(snapshot["steam_shortcut_app_id"], "3456789012")
        self.assertEqual(snapshot["environment"]["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
        self.assertNotIn("SECRET_TOKEN", snapshot["environment"])

    def test_missing_runner_returns_error(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_script = Path(temp) / "steam_bootstrap.py"
            fake_script.write_text("# bootstrap", encoding="utf-8")
            with patch.object(steam_bootstrap, "__file__", str(fake_script)):
                self.assertEqual(steam_bootstrap.main(["--steam-bootstrap"]), 2)

    def test_runs_native_runner_and_writes_environment_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "steam_bootstrap.py"
            runner = root / "run.sh"
            runner.write_text("#!/bin/sh\n", encoding="utf-8")
            with patch.object(steam_bootstrap, "__file__", str(script)), \
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "xdg"), "SteamAppId": "1234567890"}), \
                 patch.object(steam_bootstrap.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(steam_bootstrap.main(["--steam-bootstrap"]), 0)
                self.assertEqual(
                    run.call_args.args[0],
                    ["bash", str(runner), "--steam-bootstrap"],
                )
                self.assertEqual(run.call_args.kwargs["check"], False)
                self.assertEqual(run.call_args.kwargs["env"]["SteamAppId"], "1234567890")
            snapshot_path = root / "xdg/wyrd/logs/steam-bootstrap-environment.json"
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertTrue(snapshot["launched_from_steam"])
            self.assertEqual(snapshot["steam_shortcut_app_id"], "1234567890")
            self.assertEqual(snapshot["run_script"], str(runner))
            self.assertEqual(snapshot["bootstrap"], str(script))


if __name__ == "__main__":
    unittest.main()
