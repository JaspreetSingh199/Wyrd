import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication, QMessageBox, QLabel
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

    def _window(self, state=None, game=None, builds=None, steam_mo2=None, settings=None, inventory=None):
        if game is None:
            temporary_game_root = tempfile.TemporaryDirectory()
            self.addCleanup(temporary_game_root.cleanup)
            game_dir = Path(temporary_game_root.name) / ".steam/steam/steamapps/common/Skyrim Special Edition"
            game_dir.mkdir(parents=True)
            game = {
                "found": True, "game_dir": str(game_dir),
                "steam_root": str(game_dir.parents[2]), "library": str(game_dir.parents[1]),
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
            patch("main.inspect_external_skyrim_prefix_mo2_installation", return_value={"installed": False, "managed": False, "message": "No external MO2 installation was found in Skyrim's Steam prefix."}),
            patch("main.inspect_any_skyrim_prefix_mo2_instances", return_value=inventory or {"ok": False, "instances": [], "skyrim_instances": [], "message": "No MO2 instance was found."}),
            patch("main.inspect_audio_compatibility", return_value={"ok": False, "safe": False, "message": "Legacy audio prefix is unavailable."}),
            patch("main.read_settings", return_value=settings or {}),
            patch("main.write_settings"),
            # Default-deny dialogs keep offscreen tests deterministic. Individual
            # tests may override these mocks when asserting a specific interaction.
            patch("main.QMessageBox.information"),
            patch("main.QMessageBox.warning"),
            patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
            patch("main.QInputDialog.getItem", return_value=("", False)),
            patch("main.QInputDialog.getText", return_value=("", False)),
            patch("main.inspect_mo2_instances", return_value={
                "ok": True, "instances": [], "skyrim_instances": [],
                "message": "No MO2 instances were found in the known prefix locations."
            }),
            patch("main.QTimer.singleShot"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        window = MainWindow()
        # Keep offscreen tests deterministic: run startup detection synchronously
        # instead of relying on the GUI event loop to deliver delayed callbacks.
        window.refresh_all()
        return window



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
        joined = window.log_view.toPlainText()
        self.assertIn("candidate PID 4321", joined)
        self.assertIn("Audio DLL mappings PID 4321: xaudio2_7.dll", joined)
        self.assertIn("PULSE_LATENCY_MSEC='90'", joined)
        window.close()


    def test_check_launch_status_scans_detected_skyrim_steam_prefix(self):
        window = self._window()
        expected_steam_root = Path(window.game_state["game_dir"]).parents[2]
        with patch("main.steam_compat_prefix", return_value=Path("/steam/steamapps/compatdata/489830")) as prefix, \
             patch("main.detect_skyrim_processes", return_value={
                 "ok": True, "processes": [], "message": "No Skyrim processes detected."
             }), patch("main.QMessageBox.information"):
            window.check_launch_status()
        prefix.assert_called_once()
        self.assertEqual(prefix.call_args.args[0], expected_steam_root)
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



    def test_missing_saved_game_path_does_not_override_detection(self):
        window = self._window(settings={
            "proton": "/missing/Proton", "game_path": "/missing/Skyrim"
        })
        self.assertNotEqual(window.game_state["game_dir"], "/missing/Skyrim")
        window.close()


    def test_launch_skyrim_enabled_when_game_detected_and_prefix_ready_without_instance(self):
        state = {
            "installed": True, "prefix_ready": True,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2", "game_path": "/home/test/.steam/steam/steamapps/common/Skyrim Special Edition"},
            "external_installations": [],
        }
        with patch("main.inspect_mo2_instances", return_value={
            "ok": False, "instances": [], "skyrim_instances": [],
            "message": "No MO2 instances found."
        }):
            window = self._window(steam_mo2={
                "installed": True, "managed": True,
                "executable": "/test/MO2/ModOrganizer.exe",
                "prefix": "/test/compatdata/489830", "install": "/test/MO2",
            })
            self.assertFalse(window.launch_game_button.isEnabled())
            self.assertFalse(window.install_button.isVisible())
            window.close()

    def test_missing_legacy_registration_does_not_disable_discovered_mo2(self):
        window = self._window()
        window.instance_state = {
            "ok": True,
            "skyrim_instances": [{"name": "Skyrim Special Edition", "selected_profile": "Default"}],
        }
        window.mo2_state = {"installed": True, "managed": False}
        window.mo2_discovery_state = {"ok": True}
        window.startup_checks_complete = True
        with patch(
            "wyrd_mo2_registry_service.RegistryService.load",
            return_value={"instances": [], "games": [], "installations": [], "runtimes": []},
        ):
            window._restore_registered_instance()
        self.assertTrue(window.launch_game_button.isEnabled())
        self.assertIn("Automatically discovered", window.active_instance_label.text())
        window.close()

    def test_inventory_launch_handoff_refuses_unverified_s_drive(self):
        window = self._window()
        window.mo2_discovery_state = {"ok": False, "message": "S: target mismatch"}
        with patch("main.QInputDialog.getItem") as choose, patch(
            "main.QMessageBox.warning"
        ) as warning:
            window._launch_skyrim_target_from_inventory(
                "Skyrim Special Edition",
                {"ok": True, "targets": [{"title": "Skyrim", "binary": "/game/SkyrimSE.exe"}]},
                Path("/game"), Path("/prefix"), Path("/mo2"), Path("/proton"), False,
            )
        warning.assert_called_once()
        choose.assert_not_called()
        window.close()

    def test_unverified_s_drive_blocks_discovered_mo2_launch(self):
        window = self._window()
        window.instance_state = {
            "ok": True,
            "skyrim_instances": [{"name": "Skyrim Special Edition"}],
        }
        window.mo2_state = {"installed": True, "managed": False}
        window.mo2_discovery_state = {"ok": False, "message": "S: target mismatch"}
        window.startup_checks_complete = True
        window._update_active_instance_from_scan()
        self.assertFalse(window.launch_game_button.isEnabled())
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
        ]), patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch(
            "main.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "managed": True, "prefix": "/test/compatdata/489830",
                "install": "/test/MO2", "executable": "/test/MO2/ModOrganizer.exe"
            }
        ), patch("main.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": [{"title": "Skyrim", "binary": "/game/SkyrimSE.exe"}]
            }
        ), patch("main.launch_skyrim_via_mo2_in_skyrim_prefix", return_value={
            "ok": True, "pid": 501, "log_path": "/tmp/wyrd-launch.log",
            "message": "handoff",
        }), patch("main._proc_start_time", return_value=123), patch(
            "main.QMessageBox.information"
        ):
            window.instance_state = {"skyrim_instances": [{"name": "Skyrim"}]}
            window.mo2_state = {"installed": True, "managed": True, "metadata": {"game_path": window.game_state["game_dir"]}}
            window.mo2_discovery_state = {"ok": True, "windows_game_path": r"S:\\steamapps\\common\\Skyrim Special Edition"}
            window.start_skyrim()
        self.assertEqual(window.launch_session.wrapper_pid, 501)
        self.assertEqual(window.launch_session.wrapper_start_time, 123)
        self.assertTrue(window._launch_monitor.isActive())
        window.close()
        self.assertFalse(window._launch_monitor.isActive())

    def test_start_skyrim_default_audio_is_first_and_passed_unchanged(self):
        window = self._window()
        with patch("main.QInputDialog.getItem", side_effect=[
            ("Skyrim", True), ("Skyrim", True)
        ]), patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch(
            "main.inspect_skyrim_prefix_mo2_installation", return_value={
                "installed": True, "managed": True, "prefix": "/test/compatdata/489830",
                "install": "/test/MO2", "executable": "/test/MO2/ModOrganizer.exe"
            }
        ), patch("main.inspect_skyrim_launch_targets", return_value={
                "ok": True, "targets": [{"title": "Skyrim", "binary": "/game/SkyrimSE.exe"}]
            }
        ), patch("main.launch_skyrim_via_mo2_in_skyrim_prefix", return_value={
            "ok": True, "pid": 501, "log_path": "/tmp/wyrd-launch.log", "message": "handoff"
        }) as launch, patch("main._proc_start_time", return_value=123), patch(
            "main.QMessageBox.information"
        ):
            window.instance_state = {"skyrim_instances": [{"name": "Skyrim"}]}
            window.mo2_state = {"installed": True, "managed": True, "metadata": {"game_path": window.game_state["game_dir"]}}
            window.mo2_discovery_state = {"ok": True, "windows_game_path": r"S:\\steamapps\\common\\Skyrim Special Edition"}
            window.start_skyrim()
        self.assertFalse(launch.call_args.kwargs["xaudio2_compat"])
        window.close()


    def test_launch_monitor_rejects_reused_wrapper_pid_for_tree_attribution(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.app_id = "489830"
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
        window.launch_session.app_id = "489830"
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
        self.assertEqual(
            [{key: item.get(key) for key in ("pid", "binary", "start_time")}
             for item in window.launch_session.detected_processes],
            scan["processes"],
        )
        self.assertTrue(all(item.get("_launch_source") == "descendant"
                            for item in window.launch_session.detected_processes))
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
        window.launch_session.app_id = "489830"
        window.launch_session.wrapper_start_time = 900
        scan = {
            "ok": True,
            "processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "wyrd_processes": [],
        }
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 900}), patch(
            "main.launch_process_status", return_value={
                "state": "exited", "message": "wrapper exited"
            }
        ), patch("main.detect_skyrim_processes", return_value=scan), patch(
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
        window.launch_session.app_id = "489830"
        window.launch_session.wrapper_start_time = 900
        window.launch_session.app_id = "489830"
        window._launch_monitor.start()
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
        self.assertEqual(window.launch_session.state, "waiting")
        self.assertEqual(window.launch_session.game_processes, [])
        self.assertTrue(window._launch_monitor.isActive())
        self.assertNotIn("detected-unconfirmed", window.launch_session.state)
        window.close()

    def test_launch_monitor_stops_when_wrapper_exits_without_game(self):
        window = self._window()
        window.launch_session.wrapper_pid = 500
        window.launch_session.app_id = "489830"
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
        window.launch_session.app_id = "489830"
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
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 900}), patch(
            "main.launch_process_status", return_value={
                "state": "exited", "message": "wrapper exited"
            }
        ), patch("main.detect_skyrim_processes", return_value=process_scan), patch(
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
        window.launch_session.app_id = "489830"
        window.launch_session.wrapper_start_time = 900
        process_scan = {
            "ok": True,
            "processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "wyrd_processes": [{"pid": 700, "binary": "skyrimse.exe"}],
            "message": "Detected Skyrim in Wyrd prefix.",
        }
        with patch("main._proc_identity", return_value={"ppid": 1, "start_time": 901}), patch(
            "main.launch_process_status", return_value={
                "state": "exited", "message": "wrapper exited"
            }
        ), patch("main.detect_skyrim_processes", return_value=process_scan), patch(
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


    def test_refresh_restores_saved_proton_without_rewriting_default(self):
        preferred = Path("/steam/compatibilitytools.d/Proton-GE")
        default = Path("/steam/steamapps/common/Proton - Experimental")
        window = self._window(settings={"proton": str(preferred), "first_run_completed": True})
        builds = [
            {"name": "Proton - Experimental", "path": str(default), "architecture": "x86_64"},
            {"name": "Proton-GE", "path": str(preferred), "architecture": "x86_64"},
        ]
        with patch("main.detect_skyrim", return_value={"found": False, "steam_roots": []}), patch(
            "main.discover_proton_builds", return_value=builds
        ), patch("main.inspect_wyrd_installation", return_value={"installed": False}), patch(
            "main.inspect_skyrim_prefix_mo2_installation", return_value={"installed": False, "message": "none"}
        ), patch("main.inspect_external_skyrim_prefix_mo2_installation", return_value={"installed": False, "message": "none"}), patch.object(
            window, "manage_mo2_library"
        ), patch("main.inspect_audio_compatibility", return_value={"ok": True, "dlls": {}}), patch(
            "main.read_launch_diagnostics", return_value={}
        ), patch("main.write_settings") as save:
            window.refresh_all()
            self.assertEqual(window.proton_combo.currentData(), str(preferred))
            save.assert_not_called()
        window.close()

    def test_first_launch_handles_missing_saved_proton_path(self):
        window = self._window(settings={})
        yes = QMessageBox.StandardButton.Yes
        with patch("main.QInputDialog.getItem", return_value=("Proton Test (x86_64)", True)) as choose, patch(
            "main.QMessageBox.question", side_effect=[yes, QMessageBox.StandardButton.No]
        ), patch("main.mark_first_run_completed"), patch.object(
            window, "_save_selection"
        ), patch.object(window, "manage_mo2_library"):
            window._run_first_launch_setup()
        self.assertEqual(choose.call_args.args[3], ["Proton Test (x86_64)"])
        window.close()

    def test_cancelled_first_launch_runtime_choice_does_not_mark_setup_complete(self):
        window = self._window()
        with patch("main.mark_first_run_completed") as mark, patch(
            "main.QInputDialog.getItem", return_value=("", False)
        ):
            window._run_first_launch_setup()
        mark.assert_not_called()
        self.assertFalse(window._saved_settings.get("first_run_completed", False))
        window.close()

    def test_test_selected_proton_reports_valid_runtime_without_launching_game(self):
        window = self._window()
        runtime = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(runtime, ignore_errors=True))
        launcher = runtime / "proton"
        launcher.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
        launcher.chmod(0o755)
        (runtime / "files").mkdir()
        window.proton_combo.addItem("Test runtime", str(runtime))
        window.proton_combo.setCurrentIndex(window.proton_combo.count() - 1)
        with patch("main.QMessageBox.information") as info:
            window.test_selected_proton()
        info.assert_called_once()
        self.assertIn("passed", window.runtime_status.text())
        window.close()



    def test_first_launch_prompt_is_not_repeated_after_completion(self):
        window = self._window(settings={"first_run_completed": True})
        with patch("main.mark_first_run_completed") as mark, patch(
            "main.QMessageBox.question"
        ) as question:
            window._run_first_launch_setup()
        mark.assert_not_called()
        question.assert_not_called()
        window.close()


    def test_reset_wyrd_settings_preserves_mo2_and_does_not_replay_setup(self):
        active_id = "instance-existing-123"
        window = self._window(settings={
            "selected_mo2_instance_id": active_id,
            "proton": "/steam/Proton - Experimental",
            "first_run_completed": True,
        })
        self.assertEqual(window.reset_settings_button.text(), "Reset Wyrd Settings")
        with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch(
            "main.reset_wyrd_settings", return_value={"ok": True, "message": "Wyrd preferences cleared."}
        ) as reset, patch.object(window, "_set_active_instance") as set_active, patch.object(
            window, "refresh_all"
        ) as refresh, patch("main.mark_first_run_completed") as mark_completed, patch(
            "main.QTimer.singleShot"
        ) as schedule:
            window.reset_wyrd_preferences()
        reset.assert_called_once_with()
        set_active.assert_called_once_with({"id": active_id})
        mark_completed.assert_not_called()
        refresh.assert_called_once()
        schedule.assert_not_called()
        self.assertEqual(window.selected_mo2_instance_id, active_id)
        self.assertEqual(window._saved_settings.get("selected_mo2_instance_id"), active_id)
        self.assertFalse(window._saved_settings.get("first_run_completed"))
        self.assertIn("Default settings restored", window.progress_label.text())
        window.close()

    def test_reset_wyrd_settings_without_mo2_does_not_start_onboarding(self):
        window = self._window(settings={"first_run_completed": False})
        with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch(
            "main.reset_wyrd_settings", return_value={"ok": True, "message": "Wyrd preferences cleared."}
        ), patch.object(window, "refresh_all"), patch("main.mark_first_run_completed") as mark_completed, patch(
            "main.QTimer.singleShot"
        ) as schedule:
            window.reset_wyrd_preferences()
        mark_completed.assert_not_called()
        schedule.assert_not_called()
        self.assertFalse(window._saved_settings["first_run_completed"])
        window.close()


    def test_legacy_isolated_prefix_actions_are_not_created(self):
        window = self._window()
        self.assertFalse(hasattr(window, "launch_game_prefix_button"))
        self.assertFalse(hasattr(window, "copy_instance_button"))
        self.assertFalse(hasattr(window, "reset_prefix_button"))
        self.assertTrue(hasattr(window, "reset_mo2_button"))
        window.close()



    def test_audio_panel_shows_unverified_dialogue_status_and_check_button(self):
        window = self._window()
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
            window._refresh_audio_status() 
        self.assertIn("XACT (32-bit): missing", window.audio_detail.text())
        self.assertIn("X3DAudio 1.7 (32-bit): present", window.audio_detail.text())
        self.assertIn("XAPOFX 1.5 (64-bit): unknown", window.audio_detail.text())
        self.assertIn("FAudio / FFmpeg support: UNKNOWN", window.audio_detail.text())
        self.assertIn("NOT VERIFIED", window.audio_detail.text())
        self.assertFalse(hasattr(window, "audio_repair_button"))
        window.close()

    def test_check_launch_status_handles_unexpected_shallow_game_path(self):
        window = self._window()
        window.game_state["game_dir"] = "/games/Skyrim"
        with patch("main.detect_skyrim_processes", return_value={
            "ok": True, "processes": [], "message": "No Skyrim processes detected."
        }), patch("main.QMessageBox.information"):
            window.check_launch_status()
        window.close()

    def test_reset_mo2_refuses_external_installation(self):
        window = self._window()
        with patch("main.inspect_skyrim_prefix_mo2_installation", return_value={
            "installed": True, "managed": False, "install": "/external/MO2",
        }), patch("main.QMessageBox.information") as info, patch(
            "main.reset_wyrd_mo2"
        ) as reset:
            window.reset_managed_mo2()
        info.assert_called_once()
        reset.assert_not_called()
        window.close()

    def test_reset_mo2_cancel_never_calls_reset_service(self):
        window = self._window()
        window.selected_mo2_instance_id = "wyrd-instance"
        with patch("main.inspect_skyrim_prefix_mo2_installation", return_value={
            "installed": True, "managed": True, "install": "/managed/MO2",
        }), patch("main.QMessageBox.warning", return_value=QMessageBox.StandardButton.Cancel), patch(
            "main.reset_wyrd_mo2"
        ) as reset:
            window.reset_managed_mo2()
        reset.assert_not_called()
        self.assertEqual(window.selected_mo2_instance_id, "wyrd-instance")
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
        with patch("main.inspect_skyrim_prefix_mo2_installation", return_value={"installed": False}), \
             patch("main.inspect_external_skyrim_prefix_mo2_installation", return_value=external), \
             patch("main._steam_root_for_game", return_value=Path("/steam")), \
             patch("main.configure_steam_library_drive", return_value={
                 "ok": True, "windows_game_path": r"S:\\steamapps\\common\\Skyrim Special Edition",
                 "resolved_game_path": "/games/Skyrim Special Edition",
             }), \
             patch("main.inspect_any_skyrim_prefix_mo2_instances", return_value=inventory):
            window = self._window()
        self.assertFalse(window.install_button.isEnabled())
        self.assertEqual(window.install_button.text(), "Use Existing MO2")
        self.assertTrue(window.launch_game_button.isEnabled())
        self.assertIn("External (read-only)", window.mo2_panel.detail_label.text())
        self.assertIn("Default", window.mo2_panel.detail_label.text())
        window.close()


    def test_current_v1_navigation_and_primary_actions(self):
        window = self._window()
        self.assertEqual(
            [window.tabs.tabText(i) for i in range(window.tabs.count())],
            ["ENVIRONMENT", "ADVANCED", "MAINTENANCE"],
        )
        self.assertEqual(window.launch_game_button.text(), "Launch Modded Skyrim")
        self.assertEqual(window.launch_direct_button.text(), "Launch Unmodded")
        self.assertEqual(window.install_button.text(), "Install MO2")
        self.assertTrue(window.launch_status_button.isEnabled())
        self.assertFalse(hasattr(window, "use_existing_mo2_button"))
        window.close()

    def test_modded_and_unmodded_handlers_are_separate(self):
        window = self._window()
        with patch.object(window, "start_active_game") as modded, patch.object(
            window, "start_skyrim_direct"
        ) as unmodded:
            window.start_active_game()
            window.start_skyrim_direct()
        modded.assert_called_once()
        unmodded.assert_called_once()
        window.close()

    def test_maintenance_contains_audio_and_diagnostics(self):
        window = self._window()
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertTrue(any("AUDIO COMPATIBILITY" in value for value in labels))
        self.assertTrue(any("LAUNCH DIAGNOSTICS" in value for value in labels))
        self.assertEqual(window.tabs.tabText(2), "MAINTENANCE")
        self.assertEqual(window.audio_check_button.text(), "Inspect audio libraries")
        window.close()
