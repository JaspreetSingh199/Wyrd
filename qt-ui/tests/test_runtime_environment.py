"""Tests for Proton runtime environment inheritance."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wyrd_mo2 import APP_ID, _runtime_environment


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_preserves_steam_non_steam_shortcut_identity_and_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam, prefix, proton = root / "steam", root / "prefix", root / "proton"
            with patch.dict(os.environ, {
                "SteamAppId": "3456789012",
                "SteamGameId": "3456789012",
                "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
                "LD_LIBRARY_PATH": "/steam/runtime/lib",
                "WINEDLLOVERRIDES": "dinput8=n,b",
            }, clear=True):
                env = _runtime_environment(proton, steam, prefix)
            self.assertEqual(env["SteamAppId"], "3456789012")
            self.assertEqual(env["SteamGameId"], "3456789012")
            self.assertEqual(env["STEAM_COMPAT_APP_ID"], "3456789012")
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix))
            self.assertEqual(env["STEAM_COMPAT_CLIENT_INSTALL_PATH"], str(steam))
            self.assertEqual(env["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
            self.assertEqual(env["LD_LIBRARY_PATH"], "/steam/runtime/lib")
            self.assertEqual(env["WINEDLLOVERRIDES"], "dinput8=n,b")

    def test_direct_desktop_launch_keeps_skyrim_appid_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {}, clear=True):
                env = _runtime_environment(root / "proton", root / "steam", root / "prefix")
            self.assertEqual(env["SteamAppId"], APP_ID)
            self.assertEqual(env["SteamGameId"], APP_ID)
            self.assertEqual(env["STEAM_COMPAT_APP_ID"], APP_ID)


if __name__ == "__main__":
    unittest.main()
