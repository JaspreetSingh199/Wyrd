"""Tests for read-only Steam shortcut/runtime inspection."""
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
            inspection._parse_binary_vdf(b"\x00shortcuts\0\x07key\0value\0\x08")

    def test_shortcut_scan_reports_roots_and_files_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            steam_root = Path(temp) / ".steam" / "root"
            account = steam_root / "userdata" / "12345" / "config"
            account.mkdir(parents=True)
            payload = vdf_object("shortcuts", vdf_object("0", (
                vdf_string(1, "appname", "Wyrd - Skyrim through MO2")
                + vdf_string(1, "exe", '"/home/user/Wyrd/qt-ui/run.sh"')
            )))
            shortcut_file = account / "shortcuts.vdf"
            shortcut_file.write_bytes(payload)
            with patch.object(inspection, "find_steam_roots", return_value=[steam_root]):
                report = inspection.inspect_steam_shortcuts(Path(temp))
            self.assertEqual(report["detected_steam_roots"], [str(steam_root)])
            self.assertEqual(report["checked_shortcut_files"], [str(shortcut_file)])
            self.assertEqual(len(report["shortcuts"]), 1)
            self.assertIn("across 1 shortcuts.vdf file(s)", report["message"])

    def test_shortcut_scan_reports_no_userdata_files_without_claiming_parse_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            steam_root = Path(temp) / ".steam" / "root"
            (steam_root / "userdata").mkdir(parents=True)
            with patch.object(inspection, "find_steam_roots", return_value=[steam_root]):
                report = inspection.inspect_steam_shortcuts(Path(temp))
            self.assertEqual(report["checked_shortcut_files"], [])
            self.assertEqual(report["userdata_directories"], [str(steam_root / "userdata")])
            self.assertEqual(report["shortcuts"], [])
            self.assertEqual(report["warnings"], [])
            self.assertIn("across 0 shortcuts.vdf file(s)", report["message"])

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

    def test_signed_shortcut_id_converts_to_unsigned_without_abs(self):
        self.assertEqual(inspection._unsigned_appid(-12345678), 0xFFFFFFFF - 12345677)

    def test_compat_mapping_inspection_is_explicitly_unparsed_and_read_only(self):
        with patch.object(inspection, "inspect_steam_shortcuts", return_value={
            "ok": True, "shortcuts": [{"appid": -12345678}], "warnings": [],
        }):
            report = inspection.inspect_steam_compat_mappings(Path("/not-used"))
        self.assertEqual(report["mappings"], [])
        self.assertEqual(report["shortcut_appids"], [0xFFFFFFFF - 12345677])
        self.assertIn("not parsed yet", report["message"])


    def test_inspection_ui_wiring_is_present_in_main_source(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        source = main_path.read_text(encoding="utf-8")
        self.assertIn("def inspect_steam_runtime(self)", source)
        self.assertIn('QPushButton("Inspect Steam shortcut/runtime")', source)


if __name__ == "__main__":
    unittest.main()
