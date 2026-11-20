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
    inspect_skyrim_launch_targets, launch_skyrim_via_mo2, read_launch_diagnostics, launch_process_status, detect_skyrim_processes, _proc_start_time, _proc_identity, _is_descendant_process,
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
                ("100", ["SkyrimSE.exe"], [], "SkyrimSE.exe"),
                ("101", ["skse64_loader.exe"], [f"STEAM_COMPAT_DATA_PATH={prefix}"], "skse64_loader.exe"),
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

    def test_procfs_detection_handles_unreadable_proc_root(self):
        result = detect_skyrim_processes(proc_root=Path("/missing/wyrd-proc"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["processes"], [])

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
            self.assertEqual({item["title"] for item in result["targets"]}, {"Skyrim", "SKSE"})
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
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["targets"][0]["title"], "Skyrim")
            self.assertEqual(Path(result["targets"][0]["binary"]), game / "SkyrimSE.exe")

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
                    with patch("wyrd_mo2.find_steam_roots", return_value=[home / ".steam/steam"]):
                        result = launch_skyrim_via_mo2("SKSE", "Skyrim SE", home)
            self.assertTrue(result["ok"], result)
            command = popen.call_args.args[0]
            self.assertIn("--instance", command)
            self.assertIn("Skyrim SE", command)
            self.assertIn("--executable", command)
            self.assertEqual(command[-1], "SKSE")
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["STEAM_COMPAT_DATA_PATH"], str(paths["prefix"]))


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


    def test_launch_process_status_validates_process_start_time(self):
        with patch("wyrd_mo2.os.kill", return_value=None), patch(
            "wyrd_mo2._proc_start_time", return_value=500
        ):
            self.assertEqual(launch_process_status(123, start_time=500)["state"], "running")
            reused = launch_process_status(123, start_time=499)
            self.assertEqual(reused["state"], "exited")
            self.assertIn("reused", reused["message"])
        with patch("wyrd_mo2.os.kill", return_value=None), patch(
            "wyrd_mo2._proc_start_time", return_value=None
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
