import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from main import MainWindow, GameProcess
except ImportError:
    QApplication = None
    QMessageBox = None
    MainWindow = None


@unittest.skipIf(QApplication is None, "PySide6 is an optional desktop dependency")
class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, state=None, game=None, builds=None, steam_mo2=None):
        game = game or {
            "found": True, "game_dir": "/games/Skyrim Special Edition",
            "steam_root": "/home/test/.steam/steam", "library": "/home/test/.steam/steam/steamapps",
        }
        builds = builds if builds is not None else [{
            "name": "Proton Test", "path": "/home/test/.steam/steam/steamapps/common/Proton Test",
            "architecture": "x86_64",
        }]
        state = state or {
            "installed": False, "prefix_ready": False, "external_installations": [],
            "message": "MO2 is not installed yet.",
        }
        patches = [
            patch("main.detect_skyrim", return_value=game),
            patch("main.discover_proton_builds", return_value=builds),
            patch("main.inspect_wyrd_installation", return_value=state),
            patch("main.inspect_skyrim_prefix_mo2_installation", return_value=steam_mo2 or {"installed": False, "message": "No Wyrd-managed MO2 installation was found."}),
            patch("main.inspect_skyrim_prefix_mo2_instances", return_value={"ok": False, "instances": [], "skyrim_instances": [], "message": "MO2 is not installed."}),
            patch("main.read_settings", return_value={}),
            patch("main.write_settings"),
            patch("main.inspect_mo2_instances", return_value={
                "ok": True, "instances": [], "skyrim_instances": [],
                "message": "No MO2 instances were found in the known prefix locations."
            }),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return MainWindow()


    def test_skyrim_launch_buttons_route_modded_and_unmodded_launches(self):
        window = self._window()
        self.assertEqual(window.launch_game_button.text(), "Launch Skyrim (MO2)")
        self.assertEqual(window.launch_direct_button.text(), "Launch Unmodded (Steam)")
        with patch.object(window, "start_skyrim") as mo2_launch:
            window.launch_game_button.click()
            mo2_launch.assert_called_once()
        with patch.object(window, "start_skyrim_direct") as direct_launch:
            window.launch_direct_button.click()
            direct_launch.assert_called_once()
        window.close()


    def test_check_launch_status_logs_detected_audio_mapping_observations(self):
        window = self._window()
        process = {
            "pid": 4321, "binary": "SkyrimSE.exe", "attributed_to_wyrd": True,
            "start_time": 123, "audio_module_mappings_readable": True,
            "audio_module_mappings": {"xaudio2_7.dll": "/prefix/xaudio2_7.dll", "faudio.dll": None},
            "audio_runtime_environment": {"WINEDLLOVERRIDES": None, "PULSE_LATENCY_MSEC": "90"},
        }
        with patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [process], "wyrd_processes": [process],
            "message": "Detected 1 Skyrim/SKSE process(es) associated with Wyrd's managed prefix."
        }), patch("main.QMessageBox.information"):
            window.check_launch_status()
        joined = "\\n".join(window.log_lines)
        self.assertIn("candidate PID 4321", joined)
        self.assertIn("Audio DLL mappings PID 4321: xaudio2_7.dll", joined)
        self.assertIn("PULSE_LATENCY_MSEC='90'", joined)
        window.close()


    def test_check_launch_status_scans_detected_skyrim_steam_prefix(self):
        window = self._window()
        with patch("main.steam_compat_prefix", return_value=Path("/steam/steamapps/compatdata/489830")) as prefix, \
             patch("main.detect_skyrim_processes", return_value={
                 "ok": True, "processes": [], "message": "No Skyrim processes detected."
             }), patch("main.QMessageBox.information"):
            window.check_launch_status()
        prefix.assert_called_once()
        self.assertEqual(prefix.call_args.args[0], Path("/games/Skyrim Special Edition").parents[2])
        window.close()

    def test_idle_launch_status_explains_manual_mo2_and_exited_game(self):
        window = self._window()
        with patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "message": "No Skyrim/SKSE process was detected."
        }), patch("main.QMessageBox.information") as message:
            window.check_launch_status()
        detail = message.call_args.args[2]
        self.assertIn("expected when Skyrim is launched directly from MO2", detail)
        self.assertIn("currently running", detail)
        window.close()


    def test_check_launch_status_is_available_without_wyrd_launch_session(self):
        window = self._window()
        self.assertTrue(window.launch_status_button.isEnabled())
        window.close()


    def test_v1_does_not_expose_experimental_audio_override(self):
        window = self._window()
        self.assertFalse(hasattr(window, "native_xaudio2_checkbox"))
        window.close()


    def test_saved_proton_is_restored_and_selection_is_persisted(self):
        build_a = "/home/test/Proton A"
        build_b = "/home/test/Proton B"
        with patch("main.read_settings", return_value={
            "proton": build_b, "game_path": "/games/Skyrim Special Edition"
        }), patch("main.write_settings") as save:
            window = self._window(builds=[
                {"name": "Proton A", "path": build_a, "architecture": "x86_64"},
                {"name": "Proton B", "path": build_b, "architecture": "x86_64"},
            ])
            self.assertEqual(window.proton_combo.currentData(), build_b)
            window.proton_combo.setCurrentIndex(0)
            save.assert_called()
            self.assertEqual(save.call_args.args[1], Path(build_a))
            window.close()

    def test_missing_saved_game_path_does_not_override_detection(self):
        with patch("main.read_settings", return_value={
            "proton": "/missing/Proton", "game_path": "/missing/Skyrim"
        }):
            window = self._window()
            self.assertEqual(window.game_state["game_dir"], "/games/Skyrim Special Edition")
            window.close()


    def test_launch_skyrim_enabled_when_game_detected_and_prefix_ready_without_instance(self):
        state = {
            "installed": True, "prefix_ready": True,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2", "game_path": "/games/Skyrim Special Edition"},
            "external_installations": [],
        }
        with patch("main.inspect_mo2_instances", return_value={
            "ok": False, "instances": [], "skyrim_instances": [],
            "message": "No MO2 instances found."
        }):
            window = self._window(state=state)
            self.assertTrue(window.launch_button.isEnabled())
            self.assertTrue(window.launch_game_button.isEnabled())
            window.close()

    def test_start_mo2_refuses_when_instance_inspection_is_unsafe(self):
        state = {
            "installed": True, "prefix_ready": True,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2"},
            "external_installations": [],
        }
        with patch("main.inspect_skyrim_prefix_mo2_instances", return_value={
            "ok": False, "instances": [], "skyrim_instances": [],
            "message": "The Steam prefix could not be safely inspected."
        }), patch("main.QMessageBox.warning") as warning, patch("main.launch_mo2_in_skyrim_prefix") as launch, patch("main.launch_mo2") as legacy_launch:
            window = self._window(state=state)
            window.start_mo2()
            warning.assert_called_once()
            launch.assert_not_called()
            legacy_launch.assert_not_called()
            window.close()

    def test_primary_launch_mo2_uses_verified_steam_prefix_launcher(self):
        window = self._window()
        inventory = {
            "ok": True, "instances": [],
            "skyrim_instances": [{"name": "Skyrim"}],
            "message": "One Skyrim instance found.",
        }
        with patch("main.inspect_skyrim_prefix_mo2_instances", return_value=inventory), patch(
            "main.QInputDialog.getItem", return_value=("Skyrim", True)
        ), patch("main.launch_mo2_in_skyrim_prefix", return_value={
            "ok": True, "message": "Started Steam-prefix MO2."
        }) as steam_launch, patch("main.launch_mo2") as legacy_launch, patch(
            "main.QMessageBox.information"
        ):
            window.start_mo2()
        steam_launch.assert_called_once()
        self.assertEqual(steam_launch.call_args.kwargs["instance_name"], "Skyrim")
        legacy_launch.assert_not_called()
        window.close()

    def test_game_process_identity_rejects_pid_reuse(self):
        process = GameProcess(
            pid=4242, binary="skyrimse.exe", start_time=100,
            detection_source="launch_process_tree",
            attribution_confidence="confirmed",
        )
        with patch("main._proc_identity", return_value={"start_time": 101}):
            self.assertFalse(process.matches_current_process())

    def test_game_process_identity_accepts_matching_start_time(self):
        process = GameProcess(
            pid=4242, binary="skse64.exe", start_time=100,
            detection_source="managed_prefix",
            attribution_confidence="prefix",
        )
        with patch("main._proc_identity", return_value={"start_time": 100}):
            self.assertTrue(process.matches_current_process())

    def test_game_process_identity_rejects_missing_pid(self):
        process = GameProcess(
            pid=4242, binary="skyrimse.exe", start_time=100,
            detection_source="launch_process_tree",
            attribution_confidence="confirmed",
        )
        with patch("main._proc_identity", return_value={}):
            self.assertFalse(process.matches_current_process())

    def test_launch_session_model_has_safe_idle_defaults(self):
        from main import LaunchSession
        session = LaunchSession()
        self.assertIsNone(session.wrapper_pid)
        self.assertIsNone(session.wrapper_start_time)
        self.assertIsNone(session.instance_name)
        self.assertIsNone(session.executable_title)
        self.assertEqual(session.detected_processes, [])
        self.assertEqual(session.state, "idle")

    def test_launch_monitor_starts_after_successful_handoff(self):
        window = self._window()
        with patch("main.QInputDialog.getItem", side_effect=[
            ("Skyrim", True), ("Skyrim", True)
        ]), patch("main.QMessageBox.question", return_value=1), patch(
            "main.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": [{"title": "Skyrim", "binary": "/game/SkyrimSE.exe"}]
            }
        ), patch("main.launch_skyrim_via_mo2", return_value={
            "ok": True, "pid": 501, "log_path": "/tmp/wyrd-launch.log",
            "message": "handoff",
        }), patch("main._proc_start_time", return_value=123), patch(
            "main.QMessageBox.information"
        ):
            window.instance_state = {"skyrim_instances": [{"name": "Skyrim"}]}
            window.mo2_state = {"metadata": {"game_path": "/games/Skyrim"}}
            window.start_skyrim()
        self.assertEqual(window.launch_session.wrapper_pid, 501)
        self.assertEqual(window.launch_session.wrapper_start_time, 123)
        self.assertTrue(window._launch_monitor.isActive())
        window.close()
        self.assertFalse(window._launch_monitor.isActive())

    def test_start_skyrim_default_audio_is_first_and_passed_unchanged(self):
        window = self._window()
        with patch("main.QInputDialog.getItem", side_effect=[
            ("Skyrim", True), ("Skyrim", True), ("Default Proton audio", True)
        ]), patch("main.QMessageBox.question", return_value=1), patch(
            "main.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": [{"title": "Skyrim", "binary": "/game/SkyrimSE.exe"}]
            }
        ), patch("main.launch_skyrim_via_mo2", return_value={
            "ok": True, "pid": 501, "log_path": "/tmp/wyrd-launch.log", "message": "handoff"
        }) as launch, patch("main._proc_start_time", return_value=123), patch(
            "main.QMessageBox.information"
        ):
            window.instance_state = {"skyrim_instances": [{"name": "Skyrim"}]}
            window.mo2_state = {"metadata": {"game_path": "/games/Skyrim"}}
            window.start_skyrim()
        self.assertEqual(launch.call_args.kwargs["audio_mode"], "default")
        window.close()


    def test_launch_monitor_rejects_reused_wrapper_pid_for_tree_attribution(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 901}), patch(
            "main.launch_process_status", return_value={
                "state": "exited", "message": "original wrapper was replaced"
            }
        ), patch("main.detect_skyrim_processes", return_value={
            "ok": True,
            "processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "wyrd_processes": [],
        }), patch("main._is_descendant_process") as ancestry:
            window._poll_launch_status()
        ancestry.assert_not_called()
        self.assertFalse(window._launch_monitor.isActive())
        self.assertIn("wrapper exited before Skyrim", window.progress_label.text())
        window.close()

    def test_launch_monitor_includes_recent_diagnostics_on_early_exit(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.log_path = "/tmp/wyrd-launch.log"
        with patch("main.launch_process_status", return_value={
            "state": "exited", "message": "wrapper exited"
        }), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": []
        }), patch("main._proc_identity", return_value={"ppid": 1, "start_time": 900}), patch(
            "main.read_launch_diagnostics", return_value={
                "ok": True, "content": "MO2 could not resolve executable title",
            }
        ):
            window._poll_launch_status()
        self.assertIn("MO2 could not resolve executable title", window.log_view.toPlainText())
        self.assertIn("diagnostics were added", window.progress_label.text())
        window.close()

    def test_launch_session_records_detected_process_start_times(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        scan = {
            "ok": True,
            "processes": [
                {"pid": 700, "binary": "skyrimse.exe", "start_time": 111},
                {"pid": 701, "binary": "skse64.exe", "start_time": 222},
            ],
            "wyrd_processes": [],
        }
        with patch("main._proc_identity", return_value={"ppid": 500, "start_time": 900}), patch(
            "main.launch_process_status", return_value={"state": "running", "message": "running"}
        ), patch("main.detect_skyrim_processes", return_value=scan), patch(
            "main._is_descendant_process", side_effect=lambda pid, ancestor: True
        ):
            window._poll_launch_status()
        self.assertEqual(window.launch_session.detected_processes, scan["processes"])
        self.assertEqual(window.launch_session.process_identities, {700: 111, 701: 222})
        self.assertEqual(window.launch_session.state, "detected")
        window.close()

    def test_launch_monitor_logs_actionable_diagnostic_classification(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.log_path = "/tmp/wyrd-launch.log"
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 900}), patch(
            "main.launch_process_status", return_value={"state": "exited", "message": "wrapper exited"}
        ), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": []
        }), patch("main.read_launch_diagnostics", return_value={
            "ok": True, "content": "USVFS hook failed to inject"
        }):
            window._poll_launch_status()
        self.assertIn("Launch diagnosis [mo2_usvfs]", window.log_view.toPlainText())
        self.assertIn("do not bypass MO2", window.log_view.toPlainText())
        window.close()

    def test_launch_monitor_stops_when_session_game_process_is_detected(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        scan = {
            "ok": True,
            "processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "wyrd_processes": [],
        }
        with patch("main.launch_process_status", return_value={
            "state": "exited", "message": "wrapper exited"
        }), patch("main.detect_skyrim_processes", return_value=scan), patch(
            "main._is_descendant_process", return_value=True
        ):
            window._poll_launch_status()
        self.assertFalse(window._launch_monitor.isActive())
        self.assertIn("detected in this Wyrd launch process tree", window.progress_label.text())
        window.close()

    def test_post_detection_watch_revalidates_and_records_game_exit(self):
        window = self._window()
        window.launch_session.game_processes = [
            GameProcess(pid=700, binary="skyrimse.exe", start_time=111,
                       detection_source="launch_process_tree",
                       attribution_confidence="confirmed")
        ]
        window._start_game_process_watch()
        self.assertTrue(window._game_watch_timer.isActive())
        with patch.object(window.launch_session.game_processes[0],
                         "matches_current_process", return_value=False):
            window._revalidate_detected_game_processes()
        self.assertFalse(window._game_watch_timer.isActive())
        self.assertEqual(window.launch_session.state, "game-exited")
        self.assertIn("process identities are no longer present", window.log_view.toPlainText())
        window.close()

    def test_launch_monitor_does_not_claim_wyrd_when_shared_prefix_marker_is_present(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        ordinary_steam_process = {
            "pid": 700, "binary": "skyrimse.exe",
            "prefix_attributed": True, "attributed_to_wyrd": False,
        }
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 900}), patch(
            "main.launch_process_status", return_value={
                "state": "running", "message": "wrapper still running"
            }
        ), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [ordinary_steam_process],
            "wyrd_processes": [],
        }), patch("main._is_descendant_process", return_value=False):
            window._poll_launch_status()
        self.assertEqual(window.launch_session.state, "idle")
        self.assertEqual(window.launch_session.game_processes, [])
        self.assertTrue(window._launch_monitor.isActive())
        self.assertNotIn("detected-unconfirmed", window.launch_session.state)
        window.close()

    def test_launch_monitor_stops_when_wrapper_exits_without_game(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        with patch("main.launch_process_status", return_value={
            "state": "exited", "message": "wrapper exited"
        }), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": []
        }):
            window._poll_launch_status()
        self.assertFalse(window._launch_monitor.isActive())
        self.assertIn("wrapper exited before Skyrim", window.progress_label.text())
        window.close()

    def test_launch_monitor_times_out_without_spawning_or_claiming_success(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.poll_count = window._launch_poll_limit
        with patch("main.launch_process_status", return_value={
            "state": "running", "message": "wrapper alive"
        }), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": []
        }):
            window._poll_launch_status()
        self.assertFalse(window._launch_monitor.isActive())
        self.assertIn("timed out", window.progress_label.text().lower())
        window.close()

    def test_launch_status_prefers_process_tree_attribution(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        process_scan = {
            "ok": True,
            "processes": [
                {"pid": 700, "binary": "skyrimse.exe", "attributed_to_wyrd": True},
                {"pid": 701, "binary": "skse64_loader.exe", "attributed_to_wyrd": True},
            ],
            "wyrd_processes": [
                {"pid": 700, "binary": "skyrimse.exe", "attributed_to_wyrd": True},
                {"pid": 701, "binary": "skse64_loader.exe", "attributed_to_wyrd": True},
            ],
            "message": "Detected Skyrim processes in Wyrd prefix.",
        }
        with patch("main.launch_process_status", return_value={
            "state": "exited", "message": "wrapper exited"
        }), patch("main.detect_skyrim_processes", return_value=process_scan), patch(
            "main._is_descendant_process", side_effect=lambda pid, ancestor: pid == 701
        ), patch("main.QMessageBox.information") as info:
            window.check_launch_status()
        self.assertIn("this Wyrd launch process tree", window.progress_label.text())
        self.assertIn("skse64_loader.exe (PID 701)", info.call_args.args[2])
        self.assertNotIn("skyrimse.exe (PID 700)", info.call_args.args[2])
        window.close()

    def test_launch_status_falls_back_to_prefix_attribution_when_tree_is_gone(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        process_scan = {
            "ok": True,
            "processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "wyrd_processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "message": "Detected Skyrim in Wyrd prefix.",
        }
        with patch("main.launch_process_status", return_value={
            "state": "exited", "message": "wrapper exited"
        }), patch("main.detect_skyrim_processes", return_value=process_scan), patch(
            "main._is_descendant_process", return_value=False
        ), patch("main.QMessageBox.information"):
            window.check_launch_status()
        self.assertIn("ancestry is unconfirmed", window.progress_label.text())
        window.close()

    def test_manual_status_keeps_game_running_after_wrapper_exits(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.wrapper_start_time = 900
        window.launch_session.game_processes = [
            GameProcess(
                pid=700, binary="skyrimse.exe", start_time=111,
                detection_source="launch_process_tree",
                attribution_confidence="confirmed",
            )
        ]
        with patch.object(
            window.launch_session.game_processes[0],
            "matches_current_process", return_value=True
        ), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": [],
            "message": "No new process scan matches.",
        }), patch("main.launch_process_status", return_value={
            "state": "exited", "message": "Proton wrapper has exited."
        }), patch("main.QMessageBox.information") as info:
            window.check_launch_status()
        self.assertEqual(window.launch_session.state, "detected")
        self.assertTrue(window._game_watch_timer.isActive())
        self.assertIn("still present", info.call_args.args[2])
        window.close()

    def test_manual_status_records_exit_of_previously_tracked_game(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.game_processes = [
            GameProcess(
                pid=700, binary="skyrimse.exe", start_time=111,
                detection_source="launch_process_tree",
                attribution_confidence="confirmed",
            )
        ]
        with patch.object(
            window.launch_session.game_processes[0],
            "matches_current_process", return_value=False
        ), patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "wyrd_processes": [],
            "message": "No Skyrim processes found.",
        }), patch("main.launch_process_status", return_value={
            "state": "exited", "message": "Wrapper exited."
        }), patch("main.QMessageBox.information") as info:
            window.check_launch_status()
        self.assertEqual(window.launch_session.state, "game-exited")
        self.assertFalse(window._game_watch_timer.isActive())
        self.assertIn("previously detected game process has exited", info.call_args.args[2])
        window.close()

    def test_window_is_branded_wyrd_and_has_only_the_core_setup_flow(self):
        window = self._window()
        self.assertEqual(window.windowTitle(), "Wyrd — Skyrim Modding Environment")
        self.assertTrue(window.game_state["found"])
        self.assertEqual(window.proton_combo.count(), 1)
        self.assertEqual(window.install_button.text(), "Install MO2")
        self.assertEqual(window.launch_game_button.text(), "Launch Skyrim (Steam)")
        self.assertFalse(window.launch_button.isEnabled())
        self.assertFalse(hasattr(window, "nav"))
        window.close()

    def test_verified_steam_prefix_install_keeps_install_button_disabled(self):
        window = self._window(steam_mo2={
            "installed": True,
            "executable": "/steam/compatdata/489830/pfx/drive_c/Modding/MO2/ModOrganizer.exe",
            "prefix": "/steam/compatdata/489830",
            "message": "Verified Wyrd-managed MO2 installation.",
        })
        self.assertFalse(window.install_button.isEnabled())
        self.assertTrue(window.launch_button.isEnabled())
        window.close()

    def test_install_completion_rejects_failed_post_install_verification(self):
        window = self._window()
        window.game_state = {"found": True, "game_dir": "/games/Skyrim Special Edition"}
        with patch("main.inspect_skyrim_prefix_mo2_installation", return_value={
            "installed": False, "message": "Ownership marker is missing."
        }), patch("main.QMessageBox.information") as success, patch(
            "main.QMessageBox.warning"
        ) as warning:
            window._on_install_completed({"ok": True, "message": "Installer finished."})
        success.assert_not_called()
        warning.assert_called_once()
        self.assertIn("post-install ownership/executable verification failed", warning.call_args.args[2])
        self.assertIn("Ownership marker is missing", warning.call_args.args[2])
        self.assertIn("Setup stopped", window.progress_label.text())
        window.close()

    def test_launch_skyrim_button_requires_verified_steam_prefix_instance(self):
        window = self._window()
        self.assertFalse(window.launch_game_button.isEnabled())
        window.close()

    def test_install_is_enabled_only_when_game_and_proton_are_detected(self):
        window = self._window(builds=[])
        self.assertFalse(window.install_button.isEnabled())
        self.assertIn("No compatible Proton", window.runtime_status.text())
        window.close()

    def test_legacy_isolated_prefix_actions_are_hidden(self):
        window = self._window()
        self.assertFalse(window.launch_game_prefix_button.isVisible())
        self.assertFalse(window.copy_instance_button.isVisible())
        self.assertFalse(window.reset_prefix_button.isVisible())
        window.close()

    def test_primary_install_description_uses_steam_prefix(self):
        window = self._window()
        self.assertIn("Steam", window.mo2_panel.detail_label.text())
        self.assertEqual(window.install_button.text(), "Install MO2")
        window.close()

    def test_primary_runtime_and_audio_copy_describe_shared_steam_prefix(self):
        window = self._window()
        labels = window.findChildren(QLabel)
        label_text = "\\n".join(label.text() for label in labels)
        self.assertIn("existing Steam AppID 489830 prefix", label_text)
        self.assertIn("legacy-only", label_text)
        self.assertEqual(window.audio_check_button.text(), "Inspect Legacy Audio Libraries")
        window.close()

    def test_audio_panel_shows_unverified_dialogue_status_and_check_button(self):
        with patch("main.inspect_audio_compatibility", return_value={
            "ok": True, "safe": True, "prefix": "/home/test/.local/share/wyrd/compatdata/mo2-skyrim",
            "components": {
                "xact": {"state": "missing"},
                "xact_x64": {"state": "present"},
                "xaudio2": {"state": "missing"},
                "xaudio2_x64": {"state": "present"},
                "x3daudio": {"state": "present"},
                "x3daudio_x64": {"state": "present"},
            }, "repair_recommended": True, "repair_available": True,
        }):
            window = self._window()
        self.assertIn("XACT (32-bit): missing", window.audio_detail.text())
        self.assertIn("X3DAudio 1.7 (32-bit): present", window.audio_detail.text())
        self.assertIn("XAPOFX 1.5 (64-bit): unknown", window.audio_detail.text())
        self.assertIn("FAudio / FFmpeg support: UNKNOWN", window.audio_detail.text())
        self.assertIn("NOT VERIFIED", window.audio_detail.text())
        self.assertFalse(hasattr(window, "audio_repair_button"))
        window.close()

if __name__ == "__main__":
    unittest.main()


    def test_external_mo2_is_shown_as_read_only_and_disables_install(self):
        external = {
            "installed": True, "managed": False,
            "executable": "/steam/compatdata/489830/pfx/drive_c/Modding/MO2/ModOrganizer.exe",
            "prefix": "/steam/compatdata/489830",
            "install": "/steam/compatdata/489830/pfx/drive_c/Modding/MO2",
            "steam_root": "/steam",
            "message": "Existing externally managed MO2 installation found.",
        }
        inventory = {
            "ok": True, "managed": False, "instances": [],
            "skyrim_instances": [{
                "name": "Skyrim Special Edition", "selected_profile": "Default"
            }],
            "message": "Read-only instance inventory.",
        }
        with patch("main.inspect_skyrim_prefix_mo2_installation", return_value={"installed": False}), \\
             patch("main.inspect_external_skyrim_prefix_mo2_installation", return_value=external), \\
             patch("main.inspect_any_skyrim_prefix_mo2_instances", return_value=inventory):
            window = self._window()
        self.assertFalse(window.install_button.isEnabled())
        self.assertEqual(window.install_button.text(), "Use Existing MO2")
        self.assertTrue(window.launch_button.isEnabled())
        self.assertTrue(window.launch_game_button.isEnabled())
        self.assertIn("External (read-only)", window.mo2_panel.detail_label.text())
        self.assertIn("Default", window.mo2_panel.detail_label.text())
        window.close()

    def test_start_mo2_routes_external_install_to_external_launcher(self):
        window = self._window()
        window.mo2_state = {"installed": True, "managed": False}
        inventory = {
            "ok": True, "managed": False,
            "skyrim_instances": [{"name": "Skyrim Special Edition"}],
        }
        with patch("main.inspect_any_skyrim_prefix_mo2_instances", return_value=inventory), \\
             patch("main.QInputDialog.getItem", return_value=("Skyrim Special Edition", True)), \\
             patch("main.launch_external_mo2_in_skyrim_prefix", return_value={"ok": True, "message": "started"} ) as launch, \\
             patch("main.QMessageBox.information"):
            window.start_mo2()
        launch.assert_called_once()
        window.close()
