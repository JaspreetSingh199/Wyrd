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
    inspect_audio_compatibility,
    repair_audio_compatibility,
    _resolve_xact_dlls,
    inspect_proton_build,
    inspect_wyrd_installation,
    managed_paths,
    read_settings, write_settings, inspect_mo2_instances,
    inspect_skyrim_launch_targets, launch_skyrim_via_mo2, launch_mo2,
    read_launch_diagnostics, launch_process_status, detect_skyrim_processes,
    _proc_start_time, _proc_identity, _is_descendant_process, classify_launch_diagnostics,
    inspect_mo2_storage, reset_wyrd_settings,
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

    def test_procfs_detection_distinguishes_unattributed_and_wyrd_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            prefix = Path(tmp) / "managed-prefix"
            proc.mkdir()
            for pid, argv, env, comm in [
                ("100", ["SkyrimSE.exe"], [f"STEAM_COMPAT_DATA_PATH={prefix}"], "SkyrimSE.exe"),
                ("101", ["skse64_loader.exe"], [f"STEAM_COMPAT_DATA_PATH={prefix}", "WYRD_LAUNCHED=1", "WINEDLLOVERRIDES=xaudio2_7=n,b", "PULSE_LATENCY_MSEC=90"], "skse64_loader.exe"),
                ("102", ["python"], [], "python"),
            ]:
                entry = proc / pid
                entry.mkdir()
                (entry / "cmdline").write_bytes(bytes([0]).join(arg.encode() for arg in argv) + bytes([0]))
                (entry / "environ").write_bytes(bytes([0]).join(item.encode() for item in env) + bytes([0]))
                (entry / "comm").write_text(comm, encoding="utf-8")
            def fake_readlink(path):
                return {"100": "/fake/SkyrimSE.exe", "101": "/fake/skse64_loader.exe", "102": "/usr/bin/python"}[Path(path).parent.name]
            with patch("wyrd_mo2.os.readlink", side_effect=fake_readlink):
                result = detect_skyrim_processes(prefix, proc_root=proc)
            self.assertTrue(result["ok"])
            self.assertEqual([item["pid"] for item in result["processes"]], [100, 101])
            self.assertEqual([item["pid"] for item in result["wyrd_processes"]], [101])
            self.assertFalse(result["processes"][0]["attributed_to_wyrd"])
            self.assertTrue(result["processes"][0]["prefix_attributed"])
            self.assertTrue(result["processes"][1]["prefix_attributed"])
            self.assertEqual(
                result["processes"][1]["audio_runtime_environment"],
                {"WINEDLLOVERRIDES": "xaudio2_7=n,b", "PULSE_LATENCY_MSEC": "90"},
            )
            self.assertEqual(
                result["processes"][0]["audio_runtime_environment"],
                {"WINEDLLOVERRIDES": None, "PULSE_LATENCY_MSEC": None},
            )
            self.assertTrue(result["processes"][0]["audio_runtime_environment_readable"])
            self.assertTrue(result["processes"][1]["audio_runtime_environment_readable"])
            self.assertIsNone(result["processes"][1]["audio_runtime_environment_error"])

    def test_procfs_unreadable_environment_never_claims_wyrd_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            entry = proc / "321"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"SkyrimSE.exe\\0")
            (entry / "comm").write_text("SkyrimSE.exe", encoding="utf-8")
            original_read_bytes = Path.read_bytes

            def guarded_read_bytes(path):
                if path.name == "environ":
                    raise PermissionError("environment denied")
                return original_read_bytes(path)

            with patch("pathlib.Path.read_bytes", new=guarded_read_bytes), patch(
                "wyrd_mo2.os.readlink", return_value="/fake/SkyrimSE.exe"
            ):
                result = detect_skyrim_processes(
                    Path("/managed/prefix"), proc_root=proc
                )
            self.assertTrue(result["ok"])
            process = result["processes"][0]
            self.assertFalse(process["audio_runtime_environment_readable"])
            self.assertFalse(process["prefix_attributed"])
            self.assertFalse(process["attributed_to_wyrd"])
            self.assertEqual(result["wyrd_processes"], [])

    def test_procfs_requires_exact_prefix_match_not_prefix_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            entry = proc / "322"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"SkyrimSE.exe\\0")
            (entry / "comm").write_text("SkyrimSE.exe", encoding="utf-8")
            (entry / "environ").write_bytes(
                b"STEAM_COMPAT_DATA_PATH=/managed/prefix-extra\\0WYRD_LAUNCHED=1\\0"
            )
            with patch("wyrd_mo2.os.readlink", return_value="/fake/SkyrimSE.exe"):
                result = detect_skyrim_processes(
                    Path("/managed/prefix"), proc_root=proc
                )
            process = result["processes"][0]
            self.assertFalse(process["prefix_attributed"])
            self.assertFalse(process["attributed_to_wyrd"])
            self.assertEqual(result["wyrd_processes"], [])

    def test_procfs_audio_module_mappings_report_loaded_and_not_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            entry = proc / "321"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"SkyrimSE.exe\\0")
            (entry / "comm").write_text("SkyrimSE.exe", encoding="utf-8")
            (entry / "environ").write_bytes(b"STEAM_COMPAT_DATA_PATH=/managed/prefix\\0")
            (entry / "maps").write_text(
                "7f0000-7f1000 r-xp 0000 00:00 0 /drive_c/windows/system32/xaudio2_7.dll\n"
                "7f1000-7f2000 r-xp 0000 00:00 0 /drive_c/windows/system32/faudio.dll\n",
                encoding="utf-8",
            )
            with patch("wyrd_mo2.os.readlink", return_value="/fake/SkyrimSE.exe"):
                result = detect_skyrim_processes(Path("/managed/prefix"), proc_root=proc)
            self.assertTrue(result["ok"])
            process = result["processes"][0]
            self.assertTrue(process["audio_module_mappings_readable"])
            self.assertIn("xaudio2_7.dll", process["audio_module_mappings"])
            self.assertIn("faudio.dll", process["audio_module_mappings"])
            self.assertEqual(
                process["audio_module_mappings"]["xaudio2_7.dll"],
                "/drive_c/windows/system32/xaudio2_7.dll",
            )
            self.assertEqual(
                process["audio_module_mappings"]["faudio.dll"],
                "/drive_c/windows/system32/faudio.dll",
            )
            self.assertIsNone(process["audio_module_mappings"]["xactengine3_7.dll"])
            self.assertNotIn("audio_verified", process)


    def test_procfs_audio_module_mappings_mark_permission_failure_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            entry = proc / "321"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"SkyrimSE.exe\\0")
            (entry / "comm").write_text("SkyrimSE.exe", encoding="utf-8")
            (entry / "environ").write_bytes(b"")
            original = Path.read_text

            def guarded_read_text(path, *args, **kwargs):
                if path.name == "maps":
                    raise PermissionError("maps denied")
                return original(path, *args, **kwargs)

            with patch("pathlib.Path.read_text", new=guarded_read_text), patch(
                "wyrd_mo2.os.readlink", return_value="/fake/SkyrimSE.exe"
            ):
                result = detect_skyrim_processes(proc_root=proc)
            process = result["processes"][0]
            self.assertFalse(process["audio_module_mappings_readable"])
            self.assertEqual(process["audio_module_mappings"], {})
            self.assertIn("maps denied", process["audio_module_mappings_error"])


    def test_procfs_audio_environment_reports_unreadable_separately_from_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            entry = proc / "321"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"SkyrimSE.exe\\0")
            (entry / "comm").write_text("SkyrimSE.exe", encoding="utf-8")
            original_read_bytes = Path.read_bytes

            def read_bytes_with_denied_environ(path):
                if path.name == "environ":
                    raise PermissionError("permission denied")
                return original_read_bytes(path)

            with patch("pathlib.Path.read_bytes", new=read_bytes_with_denied_environ):
                with patch("wyrd_mo2.os.readlink", return_value="/fake/SkyrimSE.exe"):
                    result = detect_skyrim_processes(proc_root=proc)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["processes"]), 1)
            process = result["processes"][0]
            self.assertFalse(process["audio_runtime_environment_readable"])
            self.assertIn("permission denied", process["audio_runtime_environment_error"])
            self.assertEqual(
                process["audio_runtime_environment"],
                {"WINEDLLOVERRIDES": None, "PULSE_LATENCY_MSEC": None},
            )

    def test_procfs_detection_handles_unreadable_proc_root(self):
        result = detect_skyrim_processes(proc_root=Path("/missing/wyrd-proc"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["processes"], [])

    def test_launch_diagnostics_classify_missing_executable(self):
        findings = classify_launch_diagnostics("MO2: executable not found")
        self.assertEqual([item["code"] for item in findings], ["missing_executable"])
        self.assertIn("selected executable", findings[0]["hint"])

    def test_launch_diagnostics_classify_usvfs_failure_without_bypass_advice(self):
        findings = classify_launch_diagnostics("USVFS hook failed to inject")
        self.assertEqual([item["code"] for item in findings], ["mo2_usvfs"])
        self.assertIn("do not bypass MO2", findings[0]["hint"])

    def test_launch_diagnostics_can_return_multiple_findings(self):
        findings = classify_launch_diagnostics(
            "permission denied while loading; Vulkan failed to create device"
        )
        self.assertEqual(
            {item["code"] for item in findings},
            {"prefix_permission", "graphics_initialization"},
        )

    def test_launch_diagnostics_are_empty_for_unmatched_logs(self):
        self.assertEqual(classify_launch_diagnostics("Everything looks normal"), [])

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
                self.assertFalse(saved["xaudio2_compat"])
                write_settings(home, proton, game, xaudio2_compat=True)
                self.assertTrue(read_settings(home)["xaudio2_compat"])
                write_settings(home, proton, game, xaudio2_compat=False)
                self.assertFalse(read_settings(home)["xaudio2_compat"])
                settings_path = managed_paths(home)["settings"]
                settings_path.write_text("{invalid", encoding="utf-8")
                self.assertEqual(read_settings(home), {})


    def test_reset_wyrd_settings_removes_only_preferences_and_preserves_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_home = home / ".config"
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                paths = managed_paths(home)
                paths["config"].mkdir(parents=True)
                paths["settings"].write_text(json.dumps({
                    "proton": "/old/proton", "first_run_completed": True,
                }), encoding="utf-8")
                registry = paths["config"] / "mo2-registry.json"
                registry.write_text('{"kind":"wyrd-mo2-registry","version":1}', encoding="utf-8")
                result = reset_wyrd_settings(home)
                self.assertTrue(result["ok"])
                self.assertFalse(paths["settings"].exists())
                self.assertTrue(registry.is_file())
                self.assertEqual(read_settings(home), {})

    def test_reset_wyrd_settings_refuses_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_home = home / ".config"
            external = home / "important.json"
            external.write_text('{"keep":true}', encoding="utf-8")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                paths = managed_paths(home)
                paths["config"].mkdir(parents=True)
                paths["settings"].symlink_to(external)
                result = reset_wyrd_settings(home)
                self.assertFalse(result["ok"])
                self.assertEqual(external.read_text(encoding="utf-8"), '{"keep":true}')
                self.assertTrue(paths["settings"].is_symlink())

    def test_read_only_instance_inspection_finds_skyrim_and_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            instances = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances"
            skyrim = instances / "Skyrim Special Edition"
            other = instances / "Fallout"
            skyrim.mkdir(parents=True)
            other.mkdir()
            (skyrim / "profiles/Default").mkdir(parents=True)
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
            detected_game = Path("/games/Skyrim Special Edition")
            detected_game.mkdir(parents=True, exist_ok=True)
            (detected_game / "SkyrimSE.exe").write_bytes(b"skyrim")
            result = inspect_mo2_instances(prefix, detected_game_path=detected_game)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["instances"]), 2)
            self.assertEqual([item["name"] for item in result["skyrim_instances"]],
                             ["Skyrim Special Edition"])
            skyrim_state = result["skyrim_instances"][0]
            self.assertEqual(skyrim_state["selected_profile"], "Default")
            self.assertEqual(skyrim_state["executables"][0]["title"], "SKSE")
            self.assertEqual(ini.read_bytes(), before)

    def test_read_only_instance_inspection_finds_portable_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install = Path(tmp) / "install"
            portable = install / "instances" / "Skyrim SE"
            portable.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").parent.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            ini = portable / "ModOrganizer.ini"
            ini.write_text("[General]\ngameName=Skyrim Special Edition\n", encoding="utf-8")
            result = inspect_mo2_instances(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual([item["name"] for item in result["skyrim_instances"]], ["Skyrim SE"])

    def test_read_only_instance_inspection_finds_root_portable_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install = Path(tmp) / "install"
            install.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").parent.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            ini = install / "ModOrganizer.ini"
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\n",
                encoding="utf-8",
            )
            before = ini.read_bytes()
            result = inspect_mo2_instances(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual(
                [item["name"] for item in result["skyrim_instances"]],
                ["install"],
            )
            self.assertEqual(ini.read_bytes(), before)

    def test_instance_profile_unwraps_qsettings_byte_array_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install = Path(tmp) / "install"
            instance = install / "instances" / "Skyrim SE"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").parent.mkdir(parents=True, exist_ok=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            ini = instance / "ModOrganizer.ini"
            profiles = instance / "profiles"
            (profiles / "Default").mkdir(parents=True)
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "selected_profile=@ByteArray(Default)\n\n"
                "[Settings]\nprofiles_directory=%BASE_DIR%/profiles\n",
                encoding="utf-8",
            )
            result = inspect_mo2_instances(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual(result["skyrim_instances"][0]["selected_profile"], "Default")
            self.assertEqual(
                result["skyrim_instances"][0]["selected_profile_status"], "verified"
            )

    def test_instance_profile_outside_managed_root_is_not_reported_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            install = root / "install"
            instance = install / "instances" / "Skyrim SE"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").parent.mkdir(parents=True, exist_ok=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            external_profiles = root / "external-profiles"
            (external_profiles / "Default").mkdir(parents=True)
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "selected_profile=@ByteArray(Default)\n\n"
                f"[Settings]\nprofiles_directory={external_profiles}\n",
                encoding="utf-8",
            )
            result = inspect_mo2_instances(prefix, install)
            selected = result["skyrim_instances"][0]
            self.assertEqual(selected["selected_profile"], "")
            self.assertEqual(selected["selected_profile_candidate"], "Default")
            self.assertEqual(selected["selected_profile_status"], "unverified")

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


    def test_instance_discovery_rejects_stale_skyrim_labels_when_gamepath_is_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Stale Skyrim"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            game = root / "actual-skyrim"
            game.mkdir()
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            other = root / "other-game"
            other.mkdir()
            (other / "SkyrimSE.exe").write_bytes(b"not-the-detected-game")
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\\n"
                "gameName=Skyrim Special Edition\\n"
                "gamePath=Z:" + str(other).replace("/", "\\\\") + "\\n",
                encoding="utf-8",
            )
            result = inspect_mo2_instances(prefix, detected_game_path=game)
            self.assertTrue(result["ok"])
            self.assertEqual(result["instances"][0]["name"], "Stale Skyrim")
            # The instance name/game identity remains discoverable so Wyrd
            # can pass it to MO2's --instance selector. Its stale gamePath is
            # explicitly reported and cannot authorize an external binary.
            self.assertEqual(
                [item["name"] for item in result["skyrim_instances"]],
                ["Stale Skyrim"],
            )
            self.assertFalse(result["skyrim_instances"][0]["game_path_matches"])
            self.assertEqual(ini.read_text(encoding="utf-8").splitlines()[1], "gameName=Skyrim Special Edition")


    def test_stale_game_path_does_not_authorize_executable_outside_detected_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            game = root / "detected-game"
            stale_game = root / "old-steam-library"
            game.mkdir()
            stale_game.mkdir()
            (game / "SkyrimSE.exe").write_bytes(b"current-game")
            (stale_game / "SkyrimSE.exe").write_bytes(b"stale-game")
            instance = (
                prefix / "pfx/drive_c/users/steamuser/AppData/Local/"
                "ModOrganizer/Skyrim Special Edition"
            )
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            dosdevices = prefix / "pfx/dosdevices"
            dosdevices.mkdir(parents=True)
            (dosdevices / "z:").symlink_to("/", target_is_directory=True)
            stale_win = "Z:" + str(stale_game).replace("/", chr(92))
            binary_win = stale_win + chr(92) + "SkyrimSE.exe"
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath={stale_win}\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\title=Skyrim\n"
                f"1\\binary={binary_win}\n",
                encoding="utf-8",
            )
            result = inspect_skyrim_launch_targets(
                prefix, game, instance_name="Skyrim Special Edition"
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["targets"], [])
            self.assertTrue(any("outside the detected Skyrim directory" in warning
                                for warning in result["warnings"]))

    def test_launch_target_inspection_validates_skyrim_and_skse_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            game = root / "game"
            game.mkdir()
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim SE"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            (game / "skse64_loader.exe").write_bytes(b"skse")
            win_game = str(game).replace("/", "\\\\")
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath=Z:{win_game}\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\\\title=SKSE\n"
                f"1\\\\binary=Z:{win_game}\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            before = ini.read_bytes()
            inventory = inspect_mo2_instances(prefix)
            self.assertTrue(inventory["skyrim_instances"], inventory)
            result = inspect_skyrim_launch_targets(prefix, game)
            self.assertTrue(result["ok"], result)
            self.assertEqual({item["title"] for item in result["targets"]}, {"SKSE"})
            self.assertEqual(ini.read_bytes(), before)


    def test_default_skyrim_target_is_verified_under_detected_root_when_gamepath_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            game = root / "game"
            game.mkdir()
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n",
                encoding="utf-8",
            )
            result = inspect_skyrim_launch_targets(prefix, game, instance_name="Skyrim")
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["targets"], [])
            self.assertIn("No configured Skyrim or SKSE executable entry", result["message"])


    def test_global_instance_direct_child_layout_and_qsettings_array_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            game = root / "game"
            game.mkdir()
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            (game / "skse64_loader.exe").write_bytes(b"skse")
            users = prefix / "pfx/drive_c/users/steamuser"
            instance = users / "AppData/Local/ModOrganizer/Skyrim Special Edition - G"
            instance.mkdir(parents=True)
            portable = root / "managed-mo2"
            portable.mkdir()
            (portable / "ModOrganizer.ini").write_text(
                "[General]\ngameName=Portable unrelated\n", encoding="utf-8"
            )
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            win_game = str(game).replace("/", "\\")
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath=Z:{win_game}\n"
                "selected_profile=Default\n"
                "[customExecutables]\n"
                "size=2\n"
                "1\\title=Skyrim Special Edition\n"
                f"1\\binary=Z:{win_game}\\SkyrimSE.exe\n"
                "2\\title=SKSE\n"
                f"2\\binary=Z:{win_game}\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            inventory = inspect_mo2_instances(prefix, portable)
            self.assertEqual(
                {item["name"] for item in inventory["instances"]},
                {"Skyrim Special Edition - G", "managed-mo2"},
            )
            self.assertEqual(
                [item["name"] for item in inventory["skyrim_instances"]],
                ["Skyrim Special Edition - G"],
            )
            discovered = inventory["skyrim_instances"][0]
            self.assertEqual(discovered["selected_profile_candidate"], "Default")
            self.assertEqual(
                [item["title"] for item in discovered["executables"]],
                ["Skyrim Special Edition", "SKSE"],
            )
            targets = inspect_skyrim_launch_targets(
                prefix, game, instance_name="Skyrim Special Edition - G",
                install_root=portable,
            )
            self.assertTrue(targets["ok"], targets)
            self.assertEqual(
                {item["title"] for item in targets["targets"]},
                {"Skyrim Special Edition", "SKSE"},
            )

    def test_storage_inspection_reports_six_paths_and_preserves_ini(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            install = root / "install"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
            instance.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            base = root / "storage"
            for name in ("mods", "downloads", "webcache", "profiles", "overwrite"):
                (base / name).mkdir(parents=True)
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "[Settings]\n"
                f"base_directory={base}\n"
                "mod_directory=%BASE_DIR%/mods\n"
                "download_directory=%BASE_DIR%/downloads\n"
                "cache_directory=%BASE_DIR%/webcache\n"
                "profiles_directory=%BASE_DIR%/profiles\n"
                "overwrite_directory=%BASE_DIR%/overwrite\n",
                encoding="utf-8",
            )
            before = ini.read_bytes()
            report = inspect_mo2_storage(prefix, install)
            self.assertTrue(report["ok"], report)
            paths = report["instances"][0]["paths"]
            self.assertEqual(set(paths), {
                "base", "mods", "downloads", "cache", "profiles", "overwrite"
            })
            self.assertTrue(all(info["status"] == "available" for info in paths.values()))
            self.assertEqual(ini.read_bytes(), before)

    def test_storage_inspection_classifies_external_path_and_does_not_claim_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install = root / "prefix", root / "install"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
            instance.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            external = root / "external-mods"
            external.mkdir()
            (instance / "ModOrganizer.ini").write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "[Settings]\n"
                f"mod_directory={external}\n", encoding="utf-8"
            )
            info = inspect_mo2_storage(prefix, install)["instances"][0]["paths"]["mods"]
            self.assertEqual(info["status"], "available")
            self.assertEqual(info["ownership_scope"], "external")
            self.assertEqual(info["path"], str(external))

    def test_wine_c_drive_path_escape_is_rejected_even_when_target_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install = root / "prefix", root / "install"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
            instance.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (instance / "ModOrganizer.ini").write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "[Settings]\n"
                "cache_directory=C:\\\\..\\\\..\\\\..\\\\..\\\\not-created\\\\cache\n",
                encoding="utf-8",
            )
            result = inspect_mo2_storage(prefix, install)
            self.assertEqual(
                result["instances"][0]["paths"]["cache"]["status"], "outside-prefix"
            )

    def test_wine_c_path_escape_is_rejected_before_host_resolution(self):
        from wyrd_mo2 import _resolve_mo2_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            drive_c = prefix / "pfx/drive_c"
            drive_c.mkdir(parents=True)
            result = _resolve_mo2_storage_path(
                r"C:\\..\\..\\outside\\cache", drive_c, prefix
            )
            self.assertEqual(result["status"], "outside-prefix")

    def test_storage_inspection_reports_missing_symlink_and_out_of_prefix_c_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install = root / "prefix", root / "install"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
            instance.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            base = root / "storage"
            base.mkdir()
            target = root / "target"
            target.mkdir()
            (base / "link").symlink_to(target, target_is_directory=True)
            (instance / "ModOrganizer.ini").write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "[Settings]\n"
                f"base_directory={base}\n"
                "mod_directory=%BASE_DIR%/missing\n"
                "download_directory=%BASE_DIR%/link\n"
                "cache_directory=C:\\\\..\\\\..\\\\outside\\\\cache\n", encoding="utf-8"
            )
            paths = inspect_mo2_storage(prefix, install)["instances"][0]["paths"]
            self.assertEqual(paths["mods"]["status"], "missing")
            self.assertEqual(paths["downloads"]["status"], "symlink")
            self.assertEqual(paths["cache"]["status"], "outside-prefix")

    def test_storage_inspection_rejects_c_drive_dotdot_escape_before_filesystem_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install = root / "prefix", root / "install"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
            instance.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\ngameName=Skyrim Special Edition\n"
                "[Settings]\n"
                "cache_directory=C:\\\\..\\\\..\\\\..\\\\outside\\\\cache\n",
                encoding="utf-8",
            )
            result = inspect_mo2_storage(prefix, install)
            self.assertEqual(
                result["instances"][0]["paths"]["cache"]["status"], "outside-prefix"
            )

    def test_storage_inspection_detects_shared_roots_between_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install = root / "prefix", root / "install"
            users = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer"
            users.mkdir(parents=True)
            install.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            shared = root / "shared-mods"
            shared.mkdir()
            for name in ("Skyrim", "Skyrim Test"):
                instance = users / name
                instance.mkdir()
                (instance / "ModOrganizer.ini").write_text(
                    "[General]\ngameName=Skyrim Special Edition\n"
                    "[Settings]\n"
                    f"mod_directory={shared}\n", encoding="utf-8"
                )
            report = inspect_mo2_storage(prefix, install)
            self.assertTrue(any(
                entry["path"] == str(shared) and len(entry["references"]) == 2
                for entry in report["shared_paths"]
            ))

    def test_launch_target_inspection_rejects_binary_outside_detected_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            game = Path(tmp) / "game"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim"
            instance.mkdir(parents=True)
            game.mkdir()
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "skse64_loader.exe").write_bytes(b"unsafe")
            ini = instance / "ModOrganizer.ini"
            ini.write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                "[customExecutables]\nsize=1\n"
                "[customExecutables\\\\1]\n"
                "title=SKSE\n"
                f"binary=Z:\\\\{outside.as_posix().lstrip('/').replace('/', '\\\\')}\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            result = inspect_skyrim_launch_targets(prefix, game)
            self.assertFalse(result["ok"])
            self.assertEqual(result["targets"], [])

    def test_launch_target_rejects_skse_title_for_unrelated_game_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            game = root / "game"
            game.mkdir()
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            (game / "Updater.exe").write_bytes(b"unrelated")
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim"
            instance.mkdir(parents=True)
            (prefix / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            win_game = str(game).replace("/", "\\\\")
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath=Z:{win_game}\\\\SkyrimSE.exe\n"
                "[customExecutables]\n"
                "size=1\n"
                "[customExecutables\\\\1]\n"
                "title=SKSE\n"
                f"binary=Z:{win_game}\\\\Updater.exe\n",
                encoding="utf-8",
            )
            result = inspect_skyrim_launch_targets(prefix, game)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["targets"], [])
            self.assertIn("No configured Skyrim or SKSE executable entry", result["message"])

    def test_launch_mo2_never_injects_audio_experiment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "proton": str(proton), "game_path": str(game),
                "steam_root": str(home / ".steam/steam"),
            }), encoding="utf-8")
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "WINEDLLOVERRIDES": "d3d11=n;dxgi=n",
            }), patch("wyrd_mo2.subprocess.Popen") as popen:
                popen.return_value.pid = 6789
                result = launch_mo2(home)
            self.assertTrue(result["ok"], result)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["WINEDLLOVERRIDES"], "d3d11=n;dxgi=n")
            self.assertNotIn("xaudio2_7=", env["WINEDLLOVERRIDES"])


    def test_launch_handoff_uses_mo2_run_executable_and_prefix_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(
                json.dumps({
                    "managed_by": "Wyrd", "proton": str(proton),
                    "game_path": str(game), "steam_root": str(home / ".steam/steam"),
                }), encoding="utf-8"
            )
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            instance = paths["prefix"] / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim SE"
            instance.mkdir(parents=True)
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                "gamePath=Z:" + str(game).replace("/", "\\\\") + "\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\\\title=SKSE\n"
                "1\\\\binary=Z:" + str(game).replace("/", "\\\\") + "\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            (game / "SkyrimSE.exe").write_bytes(b"skyrim")
            (game / "skse64_loader.exe").write_bytes(b"skse")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }):
                with patch("wyrd_mo2.subprocess.Popen") as popen:
                    popen.return_value.pid = 1234
                    with patch("proton_runtime.find_steam_roots", return_value=[home / ".steam/steam"]):
                        result = launch_skyrim_via_mo2(
                            "SKSE", "Skyrim SE", home,
                            expected_binary=game / "skse64_loader.exe",
                        )
            self.assertTrue(result["ok"], result)
            command = popen.call_args.args[0]
            self.assertEqual(
                command[2:5],
                [str(paths["install"] / "ModOrganizer.exe"), "--instance", "Skyrim SE"],
            )
            self.assertEqual(command[5:], ["run", "--executable", "SKSE"])
            self.assertEqual(command[-1], "SKSE")
            self.assertIn(f"binary: {game / 'skse64_loader.exe'}", Path(result["log_path"]).read_text(encoding="utf-8"))
            environment = popen.call_args_list[0].kwargs["env"]
            self.assertEqual(environment["STEAM_COMPAT_DATA_PATH"], str(paths["prefix"]))
            self.assertNotIn("xaudio2_7=", environment.get("WINEDLLOVERRIDES", ""))
            self.assertEqual(popen.call_count, 1)


    def test_explicit_steam_prefix_launch_uses_selected_proton_and_defaults_without_xaudio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            steam = root / ".local/share/Steam"
            prefix = steam / "steamapps/compatdata/489830"
            install = prefix / "pfx/drive_c/Modding/MO2"
            proton = make_proton(root / "Proton - Experimental")
            game = steam / "steamapps/common/Skyrim Special Edition"
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim SE"
            install.mkdir(parents=True)
            (install / "ModOrganizer.exe").write_bytes(b"MO2")
            instance.mkdir(parents=True)
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"Skyrim")
            skse = game / "skse64_loader.exe"
            skse.write_bytes(b"SKSE")
            win_game = str(game).replace("/", "\\\\")
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath=Z:{win_game}\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\\\title=SKSE\n"
                f"1\\\\binary=Z:{win_game}\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            log_path = root / "logs/skyrim-launch.log"
            with patch.dict(os.environ, {"WINEDLLOVERRIDES": ""}), patch(
                "wyrd_mo2.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4242
                result = launch_skyrim_via_mo2(
                    "SKSE", "Skyrim SE", root, log_path=log_path,
                    proton_path=proton, game_path=game,
                    prefix_root=prefix, install_root=install,
                    require_managed=False,
                )
            self.assertTrue(result["ok"], result)
            command = popen.call_args.args[0]
            env = popen.call_args.kwargs["env"]
            self.assertEqual(command[:3], [str(proton / "proton"), "run", str(install / "ModOrganizer.exe")])
            self.assertIn("--instance", command)
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix))
            self.assertEqual(env["STEAM_COMPAT_CLIENT_INSTALL_PATH"], str(steam))
            self.assertEqual(env["SteamAppId"], "489830")
            self.assertEqual(env["SteamGameId"], "489830")
            self.assertNotIn("xaudio2_7=n,b", env.get("WINEDLLOVERRIDES", ""))
            self.assertNotIn("PULSE_LATENCY_MSEC", env)
            self.assertEqual(result["pid"], 4242)


    def test_launch_handoff_xaudio2_override_is_per_process_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "proton": str(proton), "game_path": str(game),
                "steam_root": str(home / ".steam/steam"),
            }), encoding="utf-8")
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            instance = paths["prefix"] / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/instances/Skyrim SE"
            instance.mkdir(parents=True)
            (instance / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                "gamePath=Z:" + str(game).replace("/", "\\\\") + "\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\\\title=SKSE\n"
                "1\\\\binary=Z:" + str(game).replace("/", "\\\\") + "\\\\skse64_loader.exe\n",
                encoding="utf-8",
            )
            target = game / "skse64_loader.exe"
            target.write_bytes(b"skse")
            (game / "SkyrimSE.exe").write_bytes(b"game")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "WINEDLLOVERRIDES": "d3d11=n;dxgi=n",
            }), patch("wyrd_mo2.subprocess.Popen") as popen, patch(
                "proton_runtime.find_steam_roots", return_value=[home / ".steam/steam"]
            ):
                popen.return_value.pid = 5678
                result = launch_skyrim_via_mo2(
                    "SKSE", "Skyrim SE", home, expected_binary=target,
                    audio_mode="xaudio2_compat",
                )
            self.assertTrue(result["ok"], result)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["WINEDLLOVERRIDES"], "d3d11=n;dxgi=n;xaudio2_7=n,b")
            # Wyrd only adds the opt-in XAudio override. It must not inject
            # PulseAudio latency settings into the Steam/Proton runtime.
            self.assertNotIn("PULSE_LATENCY_MSEC", env)
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(paths["prefix"]))
            self.assertIn("WINEDLLOVERRIDES", env)
            log = Path(result["log_path"]).read_text(encoding="utf-8")
            self.assertIn("audio_mode: xaudio2_compat", log)
            self.assertIn("xaudio2_7=n,b", log)
            self.assertNotIn("audio_latency:", log)


    def test_launch_handoff_rejects_ambiguous_duplicate_title_without_binary_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "proton": str(proton), "game_path": str(game),
                "steam_root": str(home / ".steam/steam"),
            }), encoding="utf-8")
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
            duplicate_targets = [
                {"title": "SKSE", "binary": str(game / "skse64_loader.exe")},
                {"title": "SKSE", "binary": str(game / "skse_loader.exe")},
            ]
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }), patch("wyrd_mo2.inspect_mo2_instances", return_value={
                "ok": True, "skyrim_instances": [{"name": "Skyrim SE"}], "instances": []
            }), patch("wyrd_mo2.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": duplicate_targets
            }), patch("wyrd_mo2.subprocess.Popen") as popen:
                result = launch_skyrim_via_mo2("SKSE", "Skyrim SE", home)
            self.assertFalse(result["ok"])
            self.assertIn("ambiguous", result["message"])
            popen.assert_not_called()


    def test_launch_handoff_selects_exact_binary_when_titles_are_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "proton": str(proton), "game_path": str(game),
                "steam_root": str(home / ".steam/steam"),
            }), encoding="utf-8")
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
            wanted = game / "skse64_loader.exe"
            other = game / "skse_loader.exe"
            wanted.write_bytes(b"skse")
            other.write_bytes(b"other")
            duplicate_targets = [
                {"title": "SKSE", "binary": str(wanted)},
                {"title": "SKSE", "binary": str(other)},
            ]
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }), patch("wyrd_mo2.inspect_mo2_instances", return_value={
                "ok": True, "skyrim_instances": [{"name": "Skyrim SE"}], "instances": []
            }), patch("wyrd_mo2.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": duplicate_targets
            }), patch("wyrd_mo2.subprocess.Popen") as popen, patch(
                "proton_runtime.find_steam_roots", return_value=[home / ".steam/steam"]
            ):
                popen.return_value.pid = 77
                result = launch_skyrim_via_mo2(
                    "SKSE", "Skyrim SE", home, expected_binary=wanted
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["command"][-2:], ["--executable", "SKSE"])
            log_text = Path(result["log_path"]).read_text(encoding="utf-8")
            self.assertIn(f"binary: {wanted.resolve()}", log_text)
            self.assertNotIn(f"binary: {other}", log_text)


    def test_launch_handoff_rejects_stale_binary_identity_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "proton": str(proton), "game_path": str(game),
                "steam_root": str(home / ".steam/steam"),
            }), encoding="utf-8")
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
            target = game / "skse64_loader.exe"
            target.write_bytes(b"skse")
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }), patch("wyrd_mo2.inspect_mo2_instances", return_value={
                "ok": True, "skyrim_instances": [{"name": "Skyrim SE"}], "instances": []
            }), patch("wyrd_mo2.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": [{"title": "SKSE", "binary": str(target)}]
            }) as inspection, patch("wyrd_mo2.subprocess.Popen") as popen:
                result = launch_skyrim_via_mo2(
                    "SKSE", "Skyrim SE", home,
                    expected_binary=game / "missing_skse_loader.exe",
                )
            self.assertFalse(result["ok"])
            self.assertIn("no longer available", result["message"])
            popen.assert_not_called()


    def test_launch_diagnostics_reads_tail_and_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs/launch.log"
            missing = read_launch_diagnostics(path)
            self.assertFalse(missing["ok"])
            path.parent.mkdir()
            path.write_text("launch output\\n" * 20, encoding="utf-8")
            result = read_launch_diagnostics(path, max_bytes=32)
            self.assertTrue(result["ok"])
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["content"].encode("utf-8")), 32)

    def test_launch_handoff_revalidates_selected_instance_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = managed_paths(home)
            paths["install"].mkdir(parents=True)
            paths["prefix"].mkdir(parents=True)
            proton = make_proton(home / "Proton")
            game = home / "Skyrim"
            game.mkdir()
            (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
            (paths["install"] / ".wyrd-managed.json").write_text(
                json.dumps({
                    "managed_by": "Wyrd", "proton": str(proton),
                    "game_path": str(game), "steam_root": str(home / ".steam/steam"),
                }), encoding="utf-8"
            )
            (paths["prefix"] / ".wyrd-managed.json").write_text(
                json.dumps({"managed_by": "Wyrd"}), encoding="utf-8"
            )
            (paths["prefix"] / "pfx/drive_c").mkdir(parents=True)
            with patch.dict(os.environ, {
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }), patch("wyrd_mo2.inspect_mo2_instances", return_value={
                "ok": True, "skyrim_instances": [], "instances": []
            }), patch("wyrd_mo2.subprocess.Popen") as popen:
                result = launch_skyrim_via_mo2("SKSE", "Missing", home)
            self.assertFalse(result["ok"])
            popen.assert_not_called()



    def test_external_prefix_launch_can_be_explicitly_authorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            prefix = home / ".steam/steam/steamapps/compatdata/489830"
            install = prefix / "pfx/drive_c/Modding/MO2"
            install.mkdir(parents=True)
            (install / "ModOrganizer.exe").write_bytes(b"MZ")
            instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition"
            instance.mkdir(parents=True)
            game = home / ".steam/steam/steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            game_exe = game / "SkyrimSE.exe"
            game_exe.write_bytes(b"game")
            ini = (
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath=Z:{str(game).replace('/', chr(92))}\n"
                "[customExecutables]\n"
                "size=1\n"
                "1\\title=Skyrim\n"
                f"1\\binary=Z:{str(game).replace('/', chr(92))}\\SkyrimSE.exe\n"
            )
            (instance / "ModOrganizer.ini").write_text(ini, encoding="utf-8")
            proton = make_proton(home / "Proton")
            with patch("proton_runtime.inspect_proton_build", return_value={"ok": True}), \
                 patch("proton_runtime._runtime_environment", return_value={}), \
                 patch("wyrd_mo2.subprocess.Popen") as popen:
                popen.return_value.pid = 456
                result = launch_skyrim_via_mo2(
                    "Skyrim", "Skyrim Special Edition", expected_binary=game_exe,
                    proton_path=proton, game_path=game, prefix_root=prefix,
                    install_root=install, require_managed=False,
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.args[0][-1], "Skyrim")


    def test_launch_process_status_validates_process_start_time(self):
        with patch("wyrd_mo2.os.kill", return_value=None), patch(
            "process_monitor._proc_start_time", return_value=500
        ):
            self.assertEqual(launch_process_status(123, start_time=500)["state"], "running")
            reused = launch_process_status(123, start_time=499)
            self.assertEqual(reused["state"], "exited")
            self.assertIn("reused", reused["message"])
        with patch("wyrd_mo2.os.kill", return_value=None), patch(
            "process_monitor._proc_start_time", return_value=None
        ):
            self.assertEqual(launch_process_status(123, start_time=500)["state"], "unknown")
        with patch("wyrd_mo2.os.kill") as kill:
            kill.side_effect = ProcessLookupError()
            self.assertEqual(launch_process_status(123)["state"], "exited")
            kill.side_effect = PermissionError()
            self.assertEqual(launch_process_status(123)["state"], "unknown")
        self.assertEqual(launch_process_status(-1)["state"], "invalid")

    def test_proc_identity_and_descendant_walk(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            records = {
                100: (1, 1000), 101: (100, 1010), 102: (101, 1020), 103: (1, 1030),
            }
            for pid, (ppid, start_time) in records.items():
                folder = proc / str(pid)
                folder.mkdir()
                fields = ["S", str(ppid)] + [str(i) for i in range(2, 19)] + [str(start_time)]
                (folder / "stat").write_text(
                    f"{pid} (process {pid} with ) char) " + " ".join(fields),
                    encoding="utf-8",
                )
            identity = _proc_identity(100, proc)
            self.assertEqual(identity, {"ppid": 1, "start_time": 1000})
            self.assertTrue(_is_descendant_process(102, 100, proc))
            self.assertFalse(_is_descendant_process(103, 100, proc))

    def test_descendant_walk_handles_missing_entries_and_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid, ppid in ((123, 124), (124, 123)):
                folder = proc / str(pid)
                folder.mkdir()
                fields = ["S", str(ppid)] + [str(i) for i in range(2, 20)] + ["1"]
                (folder / "stat").write_text(
                    f"{pid} (cycle process) " + " ".join(fields), encoding="utf-8"
                )
            self.assertFalse(_is_descendant_process(123, 999, proc, max_depth=8))
            self.assertFalse(_is_descendant_process(999, 123, proc))

    def test_proc_start_time_handles_parentheses_in_command_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "123").mkdir()
            fields = ["S"] + [str(index) for index in range(4, 23)]
            fields[19] = "9876"
            (proc / "123/stat").write_text(
                "123 (name with ) chars) " + " ".join(fields), encoding="utf-8"
            )
            self.assertEqual(_proc_start_time(123, proc), 9876)

    def test_proton_runtime_requires_wrapper_and_matching_wine_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            proton = make_proton(Path(tmp) / "Proton Test", machine=62)
            self.assertTrue(inspect_proton_build(proton, host="x86_64")["ok"])
            mismatch = inspect_proton_build(proton, host="aarch64")
            self.assertFalse(mismatch["ok"])
            self.assertIn("x86_64", mismatch["message"])

    def test_proton_discovery_prefers_official_then_ge_then_other_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / ".steam/steam"
            common = root / "steamapps/common"
            common.mkdir(parents=True)
            official_names = ["Proton - Experimental", "Proton 10.0", "Proton 9.0", "Proton-CachyOS Latest"]
            for name in official_names:
                make_proton(common / name)
            custom_root = root / "compatibilitytools.d"
            for name in ["GE-Proton Latest", "proton-cachyos-slr"]:
                make_proton(custom_root / name)
            with patch("proton_runtime.find_steam_roots", return_value=[root]):
                with patch("pathlib.Path.home", return_value=home):
                    builds = discover_proton_builds(home, system_roots=[])
            self.assertEqual(
                [item["name"] for item in builds],
                [
                    "Proton 10.0",
                    "Proton 9.0",
                    "Proton - Experimental",
                    "GE-Proton Latest",
                    "Proton-CachyOS Latest",
                    "proton-cachyos-slr",
                ],
            )

    def test_wrong_architecture_build_is_not_offered_in_qt_runtime_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / ".steam/steam"
            (root / "steamapps/common").mkdir(parents=True)
            make_proton(root / "steamapps/common/Proton Good", machine=62)
            make_proton(root / "steamapps/common/Proton aarch64", machine=183)
            with patch("proton_runtime.find_steam_roots", return_value=[root]):
                # Do not let host-installed compatibility tools leak into this
                # fixture-based discovery test.
                with patch("pathlib.Path.home", return_value=home):
                    builds = discover_proton_builds(home, system_roots=[])
            self.assertEqual([item["name"] for item in builds], ["Proton Good"])


    def _managed_prefix(self, home: Path):
        paths = managed_paths(home)
        paths["install"].mkdir(parents=True)
        (paths["install"] / "ModOrganizer.exe").write_bytes(b"mo2")
        (paths["install"] / ".wyrd-managed.json").write_text(json.dumps({
            "managed_by": "Wyrd", "game_app_id": "489830",
            "prefix": str(paths["prefix"]),
        }), encoding="utf-8")
        (paths["prefix"] / "pfx/drive_c/windows/system32").mkdir(parents=True)
        (paths["prefix"] / "pfx/drive_c/windows/syswow64").mkdir(parents=True)
        (paths["prefix"] / ".wyrd-managed.json").write_text(json.dumps({
            "managed_by": "Wyrd", "game_app_id": "489830",
        }), encoding="utf-8")
        return paths

    def test_xact_payload_cache_is_fail_closed_and_checks_pe_headers(self):
        from wyrd_mo2 import _resolve_xact_dlls, managed_paths
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = managed_paths(home)["root"] / "cache/xact"
            cache.mkdir(parents=True)
            x86 = cache / "xactengine3_7.dll"
            x64 = cache / "xactengine3_7_x64.dll"
            x86.write_bytes(b"not-a-pe")
            x64.write_bytes(b"MZvalid")
            partial = _resolve_xact_dlls(home)
            self.assertEqual(set(partial), {"xact_x64"})
            x86.write_bytes(b"MZvalid32")
            result = _resolve_xact_dlls(home)
            self.assertEqual(set(result), {"xact", "xact_x64"})
            self.assertEqual(result["xact"]["sha256"], __import__("hashlib").sha256(x86.read_bytes()).hexdigest())

    def test_audio_inspection_reports_xaudio_components(self):
        from wyrd_mo2 import inspect_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._managed_prefix(Path(tmp))
            windows = paths["prefix"] / "pfx/drive_c/windows"
            (windows / "system32/xaudio2_7.dll").write_bytes(b"x64")
            (windows / "syswow64/xaudio2_7.dll").write_bytes(b"x86")
            report = inspect_audio_compatibility(Path(tmp))
            self.assertTrue(report["safe"])
            self.assertEqual(report["components"]["xaudio2"]["state"], "present")
            self.assertEqual(report["components"]["xaudio2_x64"]["state"], "present")
            self.assertFalse(report["runtime_verified"])
            self.assertIn("unknown", report["runtime"]["faudio_ffmpeg_support"])

    def test_repair_never_overwrites_existing_wine_xact_dlls(self):
        from wyrd_mo2 import repair_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = self._managed_prefix(home)
            target = paths["prefix"] / "pfx/drive_c/windows/syswow64/xactengine3_7.dll"
            target.write_bytes(b"wine-builtin")
            source_cache = paths["root"] / "cache/xact"
            source_cache.mkdir(parents=True)
            (source_cache / "xactengine3_7.dll").write_bytes(b"MZx86")
            (source_cache / "xactengine3_7_x64.dll").write_bytes(b"MZx64")
            result = repair_audio_compatibility(home, confirmed=True)
            self.assertFalse(result["ok"])
            self.assertIn("will not overwrite", result["message"])
            self.assertEqual(target.read_bytes(), b"wine-builtin")


    def test_audio_inspection_reports_both_xact_architectures(self):
        from wyrd_mo2 import inspect_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._managed_prefix(Path(tmp))
            system32 = paths["prefix"] / "pfx/drive_c/windows/system32"
            syswow64 = paths["prefix"] / "pfx/drive_c/windows/syswow64"
            (system32 / "xactengine3_7.dll").write_bytes(b"64bit")
            (syswow64 / "xactengine3_7.dll").write_bytes(b"32bit")
            report = inspect_audio_compatibility(Path(tmp))
            self.assertTrue(report["safe"])
            self.assertEqual(report["components"]["xact_x64"]["state"], "present")
            self.assertEqual(report["components"]["xact"]["state"], "present")
            self.assertFalse(report["repair_recommended"])

    def test_audio_inspection_fails_closed_when_prefix_is_not_marked(self):
        from wyrd_mo2 import inspect_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            paths = managed_paths(Path(tmp))
            prefix = paths["prefix"]
            (prefix / "pfx/drive_c/windows/system32").mkdir(parents=True)
            report = inspect_audio_compatibility(Path(tmp))
            self.assertFalse(report["safe"])
            self.assertFalse(report["components"])

    def test_audio_inspection_refuses_symlinked_prefix_components(self):
        from wyrd_mo2 import inspect_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._managed_prefix(Path(tmp))
            windows = paths["prefix"] / "pfx/drive_c/windows"
            target = Path(tmp) / "outside"
            target.mkdir()
            (windows / "system32").rmdir()
            (windows / "system32").symlink_to(target, target_is_directory=True)
            report = inspect_audio_compatibility(Path(tmp))
            self.assertFalse(report["safe"])
            self.assertIn("symlink", report["message"].lower())

    def test_repair_requires_explicit_confirmation_and_never_touches_steam_prefix(self):
        from wyrd_mo2 import inspect_audio_compatibility, repair_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = self._managed_prefix(home)
            steam_prefix = home / ".steam/steam/steamapps/compatdata/489830/pfx/drive_c/windows/system32"
            steam_prefix.mkdir(parents=True)
            sentinel = steam_prefix / "steam-owned.dll"
            sentinel.write_bytes(b"leave-me-alone")
            refused = repair_audio_compatibility(home, confirmed=False)
            self.assertFalse(refused["ok"])
            self.assertFalse((paths["prefix"] / "pfx/drive_c/windows/system32/xactengine3_7.dll").exists())
            self.assertEqual(sentinel.read_bytes(), b"leave-me-alone")

            sources = {}
            for key in ("xact", "xact_x64"):
                source = home / f"{key}.dll"
                source.write_bytes(b"MZ" + key.encode("ascii"))
                sources[key] = {
                    "source": str(source),
                    "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
                }
            with patch("audio_compatibility._resolve_xact_dlls", return_value=sources):
                result = repair_audio_compatibility(home, confirmed=True)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["verified"])
            self.assertEqual(sentinel.read_bytes(), b"leave-me-alone")
            report = inspect_audio_compatibility(home)
            self.assertTrue(report["safe"])
            self.assertEqual(report["components"]["xact"]["state"], "present")
            self.assertEqual(report["components"]["xact_x64"]["state"], "present")
            log = Path(result["log_path"])
            self.assertTrue(log.is_file())
            journal = json.loads(log.read_text(encoding="utf-8").strip())
            self.assertEqual(len(journal["changes"]), 2)
            self.assertFalse(journal["runtime_verified"])

    def test_repair_refuses_if_xact_dll_sources_cannot_be_resolved(self):
        from wyrd_mo2 import repair_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            self._managed_prefix(Path(tmp))
            with patch("audio_compatibility._resolve_xact_dlls", return_value={}):
                result = repair_audio_compatibility(Path(tmp), confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse(result["modified"])
            self.assertFalse(result.get("verified", False))

    def test_repair_refuses_symlink_or_path_escape_targets(self):
        from wyrd_mo2 import repair_audio_compatibility
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._managed_prefix(Path(tmp))
            syswow64 = paths["prefix"] / "pfx/drive_c/windows/syswow64"
            syswow64.rmdir()
            outside = Path(tmp) / "external-target"
            outside.mkdir()
            syswow64.symlink_to(outside, target_is_directory=True)
            with patch("audio_compatibility._resolve_xact_dlls", return_value={
                "xact": {"source": "/runtime/xact.dll"},
                "xact_x64": {"source": "/runtime/xact_x64.dll"},
            }):
                result = repair_audio_compatibility(Path(tmp), confirmed=True)
            self.assertFalse(result["ok"])
            self.assertFalse(result["modified"])
            self.assertFalse(list(outside.iterdir()))

    def test_mo2_instance_roots_ignore_symlinked_users_and_escape_roots(self):
        from wyrd_mo2 import _instance_roots
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "compatdata/489830"
            users = prefix / "pfx/drive_c/users"
            users.mkdir(parents=True)
            safe_user = users / "steamuser"
            safe_root = safe_user / "AppData/Local/ModOrganizer"
            safe_root.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (users / "linked-user").symlink_to(outside, target_is_directory=True)
            (safe_user / "AppData/Roaming").mkdir(parents=True)
            (safe_user / "AppData/Roaming/ModOrganizer").symlink_to(
                outside, target_is_directory=True
            )
            roots = _instance_roots(prefix)
            self.assertIn(safe_root, roots)
            self.assertNotIn(outside, roots)
            self.assertTrue(all(not item.is_symlink() for item in roots))

    def test_mo2_instance_roots_only_accept_portable_install_inside_prefix(self):
        from wyrd_mo2 import _instance_roots
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "compatdata/489830"
            users = prefix / "pfx/drive_c/users"
            users.mkdir(parents=True)
            portable = prefix / "pfx/drive_c/Modding/MO2"
            portable.mkdir(parents=True)
            outside = root / "external/MO2"
            outside.mkdir(parents=True)
            safe_roots = _instance_roots(prefix, portable)
            unsafe_roots = _instance_roots(prefix, outside)
            self.assertIn(portable, safe_roots)
            self.assertNotIn(outside, unsafe_roots)


if __name__ == "__main__":
    unittest.main()