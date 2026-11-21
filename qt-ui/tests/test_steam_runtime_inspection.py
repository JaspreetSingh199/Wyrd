"""Tests for read-only Steam shortcut/runtime inspection."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import steam_runtime_inspection as inspection


def vdf_string(kind: int, key: str, value: str) -> bytes:
    return bytes([kind]) + key.encode() + b"\0" + value.encode() + b"\0"


def vdf_object(key: str, payload: bytes) -> bytes:
    return b"\0" + key.encode() + b"\0" + payload + b"\x08"


class SteamRuntimeInspectionTests(unittest.TestCase):
    def test_parses_nested_binary_shortcut_entry(self):
        shortcut = (
            vdf_string(1, "appname", "Wyrd - Skyrim through MO2")
            + vdf_string(1, "exe", '"/home/user/Wyrd/qt-ui/run.sh"')
            + vdf_string(1, "StartDir", "/home/user/Wyrd/qt-ui")
            + bytes([2]) + b"appid\0" + struct.pack("<i", -12345678)
            + vdf_string(1, "LaunchOptions", "")
        )
        parsed = inspection._parse_binary_vdf(
            vdf_object("shortcuts", vdf_object("0", shortcut))
        )
        entry = parsed["shortcuts"]["0"]
        self.assertEqual(entry["appname"], "Wyrd - Skyrim through MO2")
        self.assertEqual(entry["exe"], '"/home/user/Wyrd/qt-ui/run.sh"')
        self.assertEqual(inspection._signed_appid(entry["appid"]), -12345678)

    def test_rejects_truncated_and_unsupported_binary_vdf(self):
        with self.assertRaises(ValueError):
            inspection._parse_binary_vdf(b"\x00shortcuts\0")
        with self.assertRaises(ValueError):
            inspection._parse_binary_vdf(b"\x07key\0value\0")

    def test_runtime_summary_does_not_infer_runtime_from_appid_alone(self):
        summary = inspection.steam_runtime_environment({
            "SteamAppId": "123456789",
            "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
        })
        self.assertEqual(summary["steam_identity"], "123456789")
        self.assertFalse(summary["has_runtime_markers"])
        self.assertTrue(summary["has_pulse_server"])

    def test_runtime_summary_reports_runtime_markers(self):
        summary = inspection.steam_runtime_environment({
            "STEAM_COMPAT_RUNTIME": "sniper",
            "STEAM_RUNTIME_LIBRARY_PATH": "/steam/runtime/lib",
            "PIPEWIRE_REMOTE": "pipewire-0",
        })
        self.assertTrue(summary["has_runtime_markers"])
        self.assertTrue(summary["has_pipewire_remote"])

    def test_shortcut_inspection_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            userdata = root / "userdata/123/config"
            userdata.mkdir(parents=True)
            file = userdata / "shortcuts.vdf"
            original = vdf_object("shortcuts", vdf_object("0",
                vdf_string(1, "appname", "Wyrd - Skyrim through MO2")
                + vdf_string(1, "exe", "/tmp/run.sh")
                + bytes([2]) + b"appid\0" + struct.pack("<i", 4444)
            ))
            file.write_bytes(original)
            with patch.object(inspection, "find_steam_roots", return_value=[root]):
                report = inspection.inspect_steam_shortcuts(root)
            self.assertTrue(report["ok"])
            self.assertEqual(len(report["shortcuts"]), 1)
            self.assertEqual(report["shortcuts"][0]["appid"], 4444)
            self.assertEqual(file.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
