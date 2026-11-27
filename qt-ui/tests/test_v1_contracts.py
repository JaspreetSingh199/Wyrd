"""Fast, UI-independent contract tests for Wyrd V1.

These tests cover the safety and launch boundaries that must remain stable.
Interactive Qt behaviour and real Skyrim audio are verified manually on the
target Steam/Proton installation, not simulated in headless CI.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import steam_mo2_launch
import steam_game_registry
import steam_client
import mo2_management_workflow
from mo2_library_dialog import Mo2LibraryDialog
import launch_workflow
import process_workflow
import process_monitor
from launch_models import LaunchSession
from steam_prefix import steam_compat_prefix
from wyrd_mo2 import managed_paths, read_settings, reset_wyrd_settings


class Mo2ProcessDetectionTests(unittest.TestCase):
    def test_zombie_mo2_process_is_not_reported_as_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "compatdata" / "489830"
            prefix.mkdir(parents=True)
            proc = root / "proc"
            proc.mkdir()
            zombie = proc / "4321"
            zombie.mkdir()
            (zombie / "cmdline").write_bytes(
                b"Z:\\ModOrganizer.exe\0--instance\0Skyrim\0"
            )
            (zombie / "environ").write_bytes(
                f"STEAM_COMPAT_DATA_PATH={prefix}".encode() + bytes([0])
            )
            (zombie / "stat").write_text("4321 (ModOrganizer.exe) Z 1 0 0 0 0 0")

            result = process_monitor.detect_mo2_processes(prefix, proc)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["processes"], [])

    def test_closed_mo2_session_stops_timer_and_refreshes_inventory(self):
        class Timer:
            def __init__(self):
                self.stopped = False
            def stop(self):
                self.stopped = True

        window = SimpleNamespace(
            _mo2_session_prefix=Path("/steam/steamapps/compatdata/489830"),
            _mo2_session_timer=Timer(),
            _mo2_session_polls=4,
            _mo2_session_launch_token=None,
            _mo2_session_seen=True,
            _log=Mock(),
            progress_label=SimpleNamespace(setText=Mock()),
            refresh_all=Mock(),
        )
        with patch.object(
            mo2_management_workflow,
            "detect_mo2_processes",
            return_value={"ok": True, "processes": []},
        ):
            mo2_management_workflow.Mo2ManagementWorkflowMixin._poll_mo2_session(window)

        self.assertTrue(window._mo2_session_timer.stopped)
        self.assertIsNone(window._mo2_session_prefix)
        self.assertFalse(window._mo2_session_seen)
        window.refresh_all.assert_called_once_with()

    def test_reopen_clears_undetected_session_after_startup_grace(self):
        class Timer:
            def __init__(self):
                self.active = True
                self.stopped = False
            def isActive(self):
                return self.active
            def stop(self):
                self.active = False
                self.stopped = True

        timer = Timer()
        visible_loader = SimpleNamespace(isVisible=lambda: True)
        window = SimpleNamespace(
            _mo2_session_timer=timer,
            _mo2_session_prefix=Path("/steam/steamapps/compatdata/489830"),
            _mo2_session_seen=False,
            _mo2_session_polls=30,
            _mo2_session_loader_active=True,
            _mo2_session_launch_token="old-token",
            _mo2_session_wrapper_pid=1234,
            _launch_progress_dialog=visible_loader,
            _log=Mock(),
        )
        with (
            patch.object(mo2_management_workflow, "detect_mo2_processes",
                         return_value={"ok": True, "processes": []}),
            patch.object(mo2_management_workflow.QMessageBox, "information") as info,
        ):
            mo2_management_workflow.Mo2ManagementWorkflowMixin.launch_mo2_gui(
                window, instance_name="Skyrim SE",
            )

        self.assertTrue(timer.stopped)
        self.assertIsNone(window._mo2_session_prefix)
        self.assertFalse(window._mo2_session_seen)
        self.assertIsNone(window._mo2_session_launch_token)
        info.assert_called_once()
        self.assertEqual(info.call_args.args[1], "Another launch is in progress")

    def test_live_mo2_process_is_still_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "compatdata" / "489830"
            prefix.mkdir(parents=True)
            proc = root / "proc"
            proc.mkdir()
            live = proc / "4322"
            live.mkdir()
            (live / "cmdline").write_bytes(
                b"Z:\\ModOrganizer.exe\0--instance\0Skyrim\0"
            )
            (live / "environ").write_bytes(
                f"STEAM_COMPAT_DATA_PATH={prefix}".encode() + bytes([0])
            )
            (live / "stat").write_text("4322 (ModOrganizer.exe) S 1 0 0 0 0 0")

            result = process_monitor.detect_mo2_processes(prefix, proc)

        self.assertTrue(result["ok"], result)
        self.assertEqual([item["pid"] for item in result["processes"]], [4322])


class OpenMo2ActionTests(unittest.TestCase):
    def test_environment_open_mo2_uses_the_only_discovered_skyrim_instance(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        window = SimpleNamespace(
            instance_state={"skyrim_instances": [{"name": "Skyrim SE"}]},
            mo2_library_widget=SimpleNamespace(preferred_instance_id=""),
            launch_mo2_gui=Mock(),
        )

        mo2_management_workflow.Mo2ManagementWorkflowMixin.open_mo2_instance(window)

        window.launch_mo2_gui.assert_called_once_with(instance_name="Skyrim SE")

    def test_environment_open_mo2_prompts_when_multiple_instances_exist(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        window = SimpleNamespace(
            instance_state={"skyrim_instances": [
                {"name": "Skyrim SE"},
                {"name": "Skyrim Test"},
            ]},
            mo2_library_widget=SimpleNamespace(preferred_instance_id=""),
            launch_mo2_gui=Mock(),
        )
        with patch.object(
            mo2_management_workflow.QInputDialog,
            "getItem",
            return_value=("Skyrim Test", True),
        ) as picker:
            mo2_management_workflow.Mo2ManagementWorkflowMixin.open_mo2_instance(window)

        picker.assert_called_once()
        window.launch_mo2_gui.assert_called_once_with(instance_name="Skyrim Test")

    def test_open_mo2_loader_closes_when_mo2_process_is_detected(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        class Timer:
            def __init__(self):
                self.stopped = False
            def stop(self):
                self.stopped = True

        window = SimpleNamespace(
            _mo2_session_prefix=Path("/existing/skyrim-prefix"),
            _mo2_session_timer=Timer(),
            _mo2_session_polls=0,
            _mo2_session_launch_token=None,
            _mo2_session_seen=False,
            _mo2_session_loader_active=True,
            _log=Mock(),
            progress_label=SimpleNamespace(setText=Mock()),
            _close_launch_loader=Mock(),
        )
        with patch.object(
            mo2_management_workflow,
            "detect_mo2_processes",
            return_value={"ok": True, "processes": [{"pid": 4321}]},
        ):
            mo2_management_workflow.Mo2ManagementWorkflowMixin._poll_mo2_session(window)

        self.assertTrue(window._mo2_session_seen)
        self.assertFalse(window._mo2_session_loader_active)
        window._close_launch_loader.assert_called_once()
        self.assertEqual(window._close_launch_loader.call_args.args[0], "MO2 opened")


class OpenMo2HeaderActionTests(unittest.TestCase):
    def test_header_action_delegates_to_established_main_window_workflow(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        owner = SimpleNamespace(_log=Mock(), open_mo2_instance=Mock())
        widget = SimpleNamespace(_owner_window=owner, setup_status=SimpleNamespace(
            setText=Mock(), show=Mock(),
        ))

        Mo2LibraryDialog._open_selected_mo2(widget)

        owner._log.assert_called_once_with("Open MO2 header action clicked.")
        owner.open_mo2_instance.assert_called_once_with()

    def test_header_action_reports_missing_main_window_handler(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        owner = SimpleNamespace(_log=Mock())
        widget = SimpleNamespace(_owner_window=owner, setup_status=SimpleNamespace(
            setText=Mock(), show=Mock(),
        ))

        Mo2LibraryDialog._open_selected_mo2(widget)

        widget.setup_status.setText.assert_called_once()
        widget.setup_status.show.assert_called_once()
        owner._log.assert_called_once()


class SteamClientActivationTests(unittest.TestCase):
    def test_explicit_activation_opens_steam_without_launching_a_game(self):
        with (
            patch.object(steam_client.shutil, "which", return_value="/usr/bin/steam"),
            patch.object(steam_client.subprocess, "Popen") as popen,
        ):
            popen.return_value.pid = 1234
            result = steam_client.activate_steam_client()

        self.assertTrue(result["ok"], result)
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/steam"])
        self.assertNotIn("-applaunch", popen.call_args.args[0])
        self.assertEqual(result["pid"], 1234)

    def test_missing_steam_command_fails_without_starting_a_process(self):
        with (
            patch.object(steam_client.shutil, "which", return_value=None),
            patch.object(steam_client.subprocess, "Popen") as popen,
        ):
            result = steam_client.activate_steam_client()

        self.assertFalse(result["ok"])
        popen.assert_not_called()


    def test_steam_client_waits_for_a_webhelper_bound_to_the_main_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            bin_dir = root / "bin"
            proc.mkdir()
            bin_dir.mkdir()
            steam_exe = bin_dir / "steam"
            webhelper_exe = bin_dir / "steamwebhelper"
            steam_exe.write_text("#!/bin/sh\n", encoding="utf-8")
            webhelper_exe.write_text("#!/bin/sh\n", encoding="utf-8")

            main = proc / "100"
            main.mkdir()
            (main / "comm").write_text("steam\n", encoding="utf-8")
            (main / "cmdline").write_bytes(b"/usr/bin/steam\0")
            (main / "exe").symlink_to(steam_exe)

            status = steam_client.steam_client_status(proc)
            self.assertFalse(status["ready"])

            helper = proc / "101"
            helper.mkdir()
            (helper / "comm").write_text("steamwebhelper\n", encoding="utf-8")
            (helper / "cmdline").write_bytes(b"./steamwebhelper\0-steampid=100\0")
            (helper / "exe").symlink_to(webhelper_exe)

            status = steam_client.steam_client_status(proc)
            self.assertTrue(status["ready"], status)
            self.assertEqual(status["steam_pids"], [100])

    def test_steam_webhelper_for_another_pid_does_not_mark_client_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            bin_dir = root / "bin"
            proc.mkdir()
            bin_dir.mkdir()
            steam_exe = bin_dir / "steam"
            helper_exe = bin_dir / "steamwebhelper"
            steam_exe.write_text("#!/bin/sh\n", encoding="utf-8")
            helper_exe.write_text("#!/bin/sh\n", encoding="utf-8")
            for pid, comm, argv, exe in (
                (100, "steam", b"/usr/bin/steam\0", steam_exe),
                (101, "steamwebhelper", b"./steamwebhelper\0-steampid=999\0", helper_exe),
            ):
                entry = proc / str(pid)
                entry.mkdir()
                (entry / "comm").write_text(comm + "\n", encoding="utf-8")
                (entry / "cmdline").write_bytes(argv)
                (entry / "exe").symlink_to(exe)

            status = steam_client.steam_client_status(proc)
            self.assertFalse(status["ready"])

    def test_post_steam_skyrim_check_verifies_app_manifest_and_existing_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam_root = Path(tmp) / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            (game.parent.parent / "appmanifest_489830.acf").write_text(
                '"AppState"\n{\n'
                '    "appid" "489830"\n'
                '    "installdir" "Skyrim Special Edition"\n'
                '}\n',
                encoding="utf-8",
            )
            prefix = steam_root / "steamapps/compatdata/489830"
            (prefix / "pfx/drive_c/users/steamuser").mkdir(parents=True)

            result = steam_client.verify_skyrim_launch_context(game, steam_root, prefix)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["prefix"], str(prefix.resolve()))

    def test_post_steam_skyrim_check_refuses_missing_or_wrong_app_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam_root = Path(tmp) / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            prefix = steam_root / "steamapps/compatdata/489830"
            (prefix / "pfx/drive_c/users/steamuser").mkdir(parents=True)

            result = steam_client.verify_skyrim_launch_context(game, steam_root, prefix)

            self.assertFalse(result["ok"])
            self.assertIn("app manifest", result["message"].casefold())


class LaunchLoaderWorkflowTests(unittest.TestCase):
    def test_loader_stays_open_after_mo2_appears_until_game_detection(self):
        window = SimpleNamespace(
            _mo2_launch_wait_pending={
                "prefix": Path("/steam/steamapps/compatdata/489830"),
                "selected_title": "SKSE",
            },
            _mo2_launch_wait_attempts=0,
            _mo2_launch_wait_seen=False,
            _mo2_launch_visibility_logged=False,
            progress_label=SimpleNamespace(setText=Mock()),
            _log=Mock(),
            _update_launch_loader=Mock(),
            _close_launch_loader=Mock(),
        )
        with patch.object(
            launch_workflow,
            "detect_mo2_processes",
            return_value={"ok": True, "processes": [{"pid": 456}]},
        ):
            launch_workflow.LaunchWorkflowMixin._poll_mo2_launch_visibility(window)

        self.assertTrue(window._mo2_launch_wait_seen)
        self.assertIsNotNone(window._mo2_launch_wait_pending)
        window._close_launch_loader.assert_not_called()
        window._update_launch_loader.assert_called_once()
        self.assertIn("waiting for skyrim", window._update_launch_loader.call_args.args[0].casefold())

    def test_exited_proton_wrapper_is_not_failure_while_mo2_gui_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = SimpleNamespace(
                launch_session=LaunchSession(
                    wrapper_pid=321, wrapper_start_time=100,
                    app_id="489830", prefix_root=tmp,
                    target_binary="skse64_loader.exe", launch_token="launch-token",
                ),
                _launch_poll_limit=150,
                _launch_monitor=SimpleNamespace(isActive=Mock(return_value=True), stop=Mock()),
                game_state={"game_dir": str(Path(tmp) / "Skyrim")},
                progress_label=SimpleNamespace(setText=Mock()),
                _log=Mock(),
                _update_launch_loader=Mock(),
                _close_launch_loader=Mock(),
                _start_game_process_watch=Mock(),
            )
            with (
                patch.object(process_workflow, "_proc_identity", return_value={"start_time": 99}),
                patch.object(
                    process_workflow, "launch_process_status",
                    return_value={"state": "exited", "message": "Proton wrapper exited"},
                ),
                patch.object(
                    process_workflow, "detect_skyrim_processes",
                    return_value={"ok": True, "processes": [], "wyrd_processes": []},
                ),
                patch.object(
                    process_workflow, "detect_mo2_processes",
                    return_value={"ok": True, "processes": [{"pid": 654}]},
                ),
            ):
                process_workflow.ProcessMonitorWorkflowMixin._poll_launch_status(window)

            self.assertEqual(window.launch_session.state, "waiting")
            window._close_launch_loader.assert_not_called()
            window._update_launch_loader.assert_called_once()
            self.assertIn("MO2 is running", window.progress_label.setText.call_args.args[0])


class WyrdV1ContractTests(unittest.TestCase):
    def _launch_fixture(self, home: Path):
        steam_root = home / ".local/share/Steam"
        game = steam_root / "steamapps/common/Skyrim Special Edition"
        game.mkdir(parents=True)
        expected_binary = game / "skse64_loader.exe"
        expected_binary.write_bytes(b"SKSE fixture")

        prefix = steam_compat_prefix(steam_root)
        (prefix / "pfx/drive_c/users/steamuser").mkdir(parents=True)
        install = prefix / "pfx/drive_c/Modding/Skyrim SE"
        install.mkdir(parents=True)
        (install / "ModOrganizer.exe").write_bytes(b"MO2 fixture")

        # Model a real portable MO2 instance. The launch adapter deliberately
        # re-discovers the selected instance immediately before starting it.
        dosdevices = prefix / "pfx/dosdevices"
        dosdevices.mkdir(parents=True, exist_ok=True)
        (dosdevices / "s:").symlink_to(steam_root, target_is_directory=True)
        (install / "ModOrganizer.ini").write_text(
            "[General]\n"
            "gameName=Skyrim Special Edition\n"
            r"gamePath=S:\\steamapps\\common\\Skyrim Special Edition" + "\n",
            encoding="utf-8",
        )

        proton = home / "Proton"
        proton.mkdir()
        wrapper = proton / "proton"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)
        return steam_root, game, prefix, install, proton, expected_binary

    def test_skyrim_compat_prefix_is_appid_489830(self):
        self.assertEqual(
            steam_compat_prefix(Path("/steam")),
            Path("/steam/steamapps/compatdata/489830"),
        )

    def test_skyrim_launch_uses_mo2_and_existing_steamcompat_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root, game, prefix, install, proton, binary = self._launch_fixture(home)
            targets = {"ok": True, "targets": [{
                "title": "SKSE", "binary": str(binary),
            }]}
            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch, "inspect_skyrim_launch_targets", return_value=targets),
                patch.object(steam_mo2_launch, "_runtime_environment", return_value={"PATH": "/usr/bin"}),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                popen.return_value.pid = 4321
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", binary,
                    prefix_path=prefix, install_root=install,
                    steam_root_path=steam_root, home=home,
                )

            self.assertTrue(result["ok"], result)
            command = popen.call_args.args[0]
            self.assertEqual(command, [
                str(proton / "proton"), "run", str(install / "ModOrganizer.exe"),
                "--logs", "--instance", "Skyrim SE", "run", "--executable", "SKSE",
            ])
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["SteamAppId"], "489830")
            self.assertEqual(env["SteamGameId"], "489830")
            self.assertEqual(env["STEAM_COMPAT_APP_ID"], "489830")
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix.resolve()))
            self.assertNotIn("xaudio2_7=", env.get("WINEDLLOVERRIDES", ""))
            self.assertEqual(result["prefix"], str(prefix.resolve()))
            self.assertEqual(popen.call_count, 1)

    def test_skyrim_launch_refuses_to_create_a_missing_steam_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\n")
            prefix = steam_compat_prefix(steam_root)

            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", game / "skse64_loader.exe",
                    prefix_path=prefix, steam_root_path=steam_root, home=home,
                )

            self.assertFalse(result["ok"])
            self.assertFalse(prefix.exists())
            popen.assert_not_called()

    def test_skyrim_launch_rejects_a_stale_or_mismatched_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root, game, prefix, install, proton, binary = self._launch_fixture(home)
            other_binary = game / "SkyrimSE.exe"
            other_binary.write_bytes(b"Skyrim fixture")
            targets = {"ok": True, "targets": [{
                "title": "SKSE", "binary": str(other_binary),
            }]}
            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch, "inspect_skyrim_launch_targets", return_value=targets),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", binary,
                    prefix_path=prefix, install_root=install,
                    steam_root_path=steam_root, home=home,
                )

            self.assertFalse(result["ok"])
            popen.assert_not_called()

    def test_reset_wyrd_settings_preserves_mo2_registry(self):
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
                registry.write_text(
                    '{"kind":"wyrd-mo2-registry","version":1}', encoding="utf-8"
                )

                result = reset_wyrd_settings(home)

                self.assertTrue(result["ok"], result)
                self.assertFalse(paths["settings"].exists())
                self.assertTrue(registry.is_file())
                self.assertEqual(read_settings(home), {})

    def test_reset_wyrd_settings_refuses_symlinked_preferences(self):
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


class SteamGameRegistryRefreshTests(unittest.TestCase):
    def test_registry_path_parser_decodes_wine_escaped_backslashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "system.reg"
            registry.write_text(
                '[Software\\\\WOW6432Node\\\\Bethesda Softworks\\\\Skyrim Special Edition]\n'
                '"Installed Path"="S:\\\\steamapps\\\\common\\\\Skyrim Special Edition\\\\"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                steam_game_registry._read_installed_path(registry),
                "S:\\steamapps\\common\\Skyrim Special Edition\\",
            )

    def test_matching_registry_path_does_not_start_proton_on_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            prefix = steam_game_registry.steam_compat_prefix(steam_root)
            (prefix / "pfx").mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\n", encoding="utf-8")
            windows_path = r"S:\steamapps\common\Skyrim Special Edition"

            with (
                patch.object(steam_game_registry, "_resolve_windows_path", return_value=game),
                patch.object(steam_game_registry, "_read_installed_path", return_value=windows_path + "\\"),
            ):
                result = steam_game_registry.configure_skyrim_se_registry(
                    prefix, game, steam_root, proton, windows_path,
                )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["already_configured"])

    def test_missing_registry_value_fails_read_only_without_proton_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            prefix = steam_game_registry.steam_compat_prefix(steam_root)
            (prefix / "pfx").mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\\n", encoding="utf-8")
            windows_path = r"S:\\steamapps\\common\\Skyrim Special Edition"

            with (
                patch.object(steam_game_registry, "_resolve_windows_path", return_value=game),
                patch.object(steam_game_registry, "_read_installed_path", return_value=None),
            ):
                result = steam_game_registry.configure_skyrim_se_registry(
                    prefix, game, steam_root, proton, windows_path,
                )

            self.assertFalse(result["ok"])
            self.assertIn("did not start Proton", result["message"])


if __name__ == "__main__":
    unittest.main()
