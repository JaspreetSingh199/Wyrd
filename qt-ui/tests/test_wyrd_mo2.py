import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wyrd_mo2 import (
    discover_proton_builds,
    inspect_proton_build,
    inspect_wyrd_installation,
    install_mo2_environment,
    managed_paths,
    _validate_archive_members, read_settings, write_settings, inspect_mo2_instances,
)


def make_proton(path: Path, machine: int = 62) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    wrapper = path / "proton"
    wrapper.write_text("#!/usr/bin/env python3\n")
    wrapper.chmod(0o755)
    wine = path / "files/bin/wine"
    wine.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = machine.to_bytes(2, "little")
    wine.write_bytes(header)
    return path


class WyrdMO2Tests(unittest.TestCase):

    def test_settings_round_trip_and_missing_or_invalid_settings_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            self.assertEqual(read_settings(home), {})
            proton = home / "Proton"
            game = home / "Skyrim"
            proton.mkdir()
            game.mkdir()
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }):
                write_settings(home, proton, game)
                saved = read_settings(home)
                self.assertEqual(saved["proton"], str(proton.resolve()))
                self.assertEqual(saved["game_path"], str(game.resolve()))
                settings_path = managed_paths(home)["settings"]
                settings_path.write_text("{invalid", encoding="utf-8")
                self.assertEqual(read_settings(home), {})


    def test_read_only_instance_inspection_finds_skyrim_and_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            instances = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances"
            skyrim = instances / "Skyrim Special Edition"
            other = instances / "Fallout"
            skyrim.mkdir(parents=True)
            other.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            ini = skyrim / "ModOrganizer.ini"
            ini.write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                "gamePath=Z:\\\\games\\\\Skyrim Special Edition\\\\SkyrimSE.exe\n"
                "selected_profile=Default\n\n"
                "[customExecutables]\n"
                "size=1\n\n"
                "[customExecutables\\\\1]\n"
                "title=SKSE\n"
                "binary=Z:\\\\games\\\\Skyrim Special Edition\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            (other / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Fallout 4\n", encoding="utf-8"
            )
            before = ini.read_bytes()
            result = inspect_mo2_instances(prefix)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["instances"]), 2)
            self.assertEqual([item["name"] for item in result["skyrim_instances"]],
                             ["Skyrim Special Edition"])
            skyrim_state = result["skyrim_instances"][0]
            self.assertEqual(skyrim_state["selected_profile"], "Default")
            self.assertEqual(skyrim_state["executables"][0]["title"], "SKSE")
            self.assertEqual(ini.read_bytes(), before)

    def test_instance_inspection_refuses_unowned_or_unreadable_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            self.assertFalse(inspect_mo2_instances(prefix)["ok"])
            marker = prefix / ".wyrd-managed.json"
            marker.write_text("not json", encoding="utf-8")
            self.assertFalse(inspect_mo2_instances(prefix)["ok"])

    def test_instance_inspection_reports_malformed_instance_ini_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            root = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances"
            instance = root / "Broken"
            instance.mkdir(parents=True)
            marker = prefix / ".wyrd-managed.json"
            marker.write_text(json.dumps({"managed_by": "Wyrd"}), encoding="utf-8")
            ini = instance / "ModOrganizer.ini"
            ini.write_text("[General\\ninvalid", encoding="utf-8")
            before = ini.read_bytes()
            result = inspect_mo2_instances(prefix)
            self.assertTrue(result["ok"])
            self.assertEqual(result["instances"], [])
            self.assertTrue(result["errors"])
            self.assertEqual(ini.read_bytes(), before)

    def test_proton_runtime_requires_wrapper_and_matching_wine_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            proton = make_proton(Path(tmp) / "Proton Test", machine=62)
            self.assertTrue(inspect_proton_build(proton, host="x86_64")["ok"])
            mismatch = inspect_proton_build(proton, host="aarch64")
            self.assertFalse(mismatch["ok"])
            self.assertIn("x86_64", mismatch["message"])

    def test_wrong_architecture_build_is_not_offered_in_qt_runtime_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / ".steam/steam"
            (root / "steamapps/common").mkdir(parents=True)
            make_proton(root / "steamapps/common/Proton Good", machine=62)
            make_proton(root / "steamapps/common/Proton aarch64", machine=183)
            with patch("wyrd_mo2.find_steam_roots", return_value=[root]):
                builds = discover_proton_builds(home)
            self.assertEqual([item["name"] for item in builds], ["Proton Good"])

    def test_unmanaged_prefix_is_never_initialized_or_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["prefix"].mkdir(parents=True)
            (paths["prefix"] / "important-user-data.txt").write_text("keep")
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }):
                with patch("wyrd_mo2.find_steam_roots", return_value=[home / ".steam/steam"]):
                    with patch("wyrd_mo2._latest_mo2_archive") as release:
                        with self.assertRaisesRegex(RuntimeError, "not marked as Wyrd-managed"):
                            install_mo2_environment(proton, game, home)
                        release.assert_not_called()
            self.assertEqual((paths["prefix"] / "important-user-data.txt").read_text(), "keep")

    def test_install_creates_isolated_verified_install_without_touching_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam = home / ".steam/steam"
            (steam / "steamapps").mkdir(parents=True)
            game = steam / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            game_file = game / "SkyrimSE.exe"
            game_file.write_bytes(b"game-fixture")
            proton = make_proton(home / "Proton Test")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }):
                with patch("wyrd_mo2._latest_mo2_archive", return_value=("v2.5.2", "MO2.7z", "https://example.invalid/MO2.7z")):
                    with patch("wyrd_mo2._download_archive", side_effect=lambda url, dest, callback=None: dest.write_bytes(b"archive")):
                        def fake_extract(archive, stage, callback=None):
                            stage.mkdir(parents=True, exist_ok=True)
                            (stage / "ModOrganizer.exe").write_bytes(b"mo2-fixture")
                            return stage
                        with patch("wyrd_mo2._extract_mo2", side_effect=fake_extract):
                            def fake_prefix(proton_path, steam_root, prefix_root, game_path, callback=None):
                                (prefix_root / "pfx/drive_c").mkdir(parents=True)
                                (prefix_root / ".wyrd-managed.json").write_text('{"managed_by":"Wyrd"}')
                            with patch("wyrd_mo2._initialize_prefix", side_effect=fake_prefix):
                                result = install_mo2_environment(proton, game, home)
            self.assertTrue(result["ok"], result)
            self.assertTrue(Path(result["executable"]).is_file())
            self.assertTrue(Path(result["prefix"]).joinpath("pfx/drive_c").is_dir())
            self.assertEqual(game_file.read_bytes(), b"game-fixture")
            self.assertTrue(inspect_wyrd_installation(home)["installed"])

    def test_unmanaged_mo2_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            exe = paths["install"] / "ModOrganizer.exe"
            exe.write_bytes(b"not-owned")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }):
                state = inspect_wyrd_installation(home)
            self.assertFalse(state["installed"])
            self.assertTrue(exe.is_file())
            self.assertEqual(exe.read_bytes(), b"not-owned")

    def test_archive_path_traversal_is_rejected(self):
        _validate_archive_members(["ModOrganizer.exe", "plugins/game_skyrim.dll"])
        with self.assertRaisesRegex(RuntimeError, "Unsafe path"):
            _validate_archive_members(["../../outside.txt"])

    def test_windows_absolute_archive_path_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unsafe path"):
            _validate_archive_members(["C:/Windows/evil.dll"])


if __name__ == "__main__":
    unittest.main()
