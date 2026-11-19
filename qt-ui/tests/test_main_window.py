import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication
    from main import MainWindow
except ImportError:
    QApplication = None
    MainWindow = None


@unittest.skipIf(QApplication is None, "PySide6 is an optional desktop dependency")
class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, state=None, game=None, builds=None):
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


    def test_launch_is_disabled_when_instance_inspection_is_unavailable(self):
        state = {
            "installed": True, "prefix_ready": True,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2"},
            "external_installations": [],
        }
        with patch("main.inspect_mo2_instances", return_value={
            "ok": False, "instances": [], "skyrim_instances": [],
            "message": "Ownership marker is unreadable."
        }):
            window = self._window(state=state)
            self.assertFalse(window.launch_button.isEnabled())
            window.close()

    def test_start_mo2_refuses_when_instance_inspection_is_unsafe(self):
        state = {
            "installed": True, "prefix_ready": True,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2"},
            "external_installations": [],
        }
        with patch("main.inspect_mo2_instances", return_value={
            "ok": False, "instances": [], "skyrim_instances": [],
            "message": "The prefix is not marked as Wyrd-managed."
        }), patch("main.QMessageBox.warning") as warning, patch("main.launch_mo2") as launch:
            window = self._window(state=state)
            window.start_mo2()
            warning.assert_called_once()
            launch.assert_not_called()
            window.close()

    def test_window_is_branded_wyrd_and_has_only_the_core_setup_flow(self):
        window = self._window()
        self.assertEqual(window.windowTitle(), "Wyrd — Skyrim Modding Environment")
        self.assertTrue(window.game_state["found"])
        self.assertEqual(window.proton_combo.count(), 1)
        self.assertEqual(window.install_button.text(), "Install MO2")
        self.assertFalse(window.launch_button.isEnabled())
        self.assertFalse(hasattr(window, "nav"))
        window.close()

    def test_install_is_enabled_only_when_game_and_proton_are_detected(self):
        window = self._window(builds=[])
        self.assertFalse(window.install_button.isEnabled())
        self.assertIn("No compatible Proton", window.runtime_status.text())
        window.close()

    def test_external_mo2_is_reported_but_not_adopted(self):
        state = {
            "installed": False, "prefix_ready": False,
            "external_installations": ["/home/test/Games/ModOrganizer"],
            "message": "An MO2 installation exists elsewhere. Wyrd will not overwrite or adopt it.",
        }
        window = self._window(state=state)
        self.assertIn("left untouched", window.mo2_panel.detail_label.text())
        self.assertTrue(window.install_button.isEnabled())
        window.close()

    def test_incomplete_wyrd_prefix_offers_repair_not_launch(self):
        state = {
            "installed": True, "prefix_ready": False,
            "executable": "/home/test/.local/share/wyrd/mo2/ModOrganizer.exe",
            "metadata": {"version": "v2.5.2"},
            "external_installations": [],
        }
        window = self._window(state=state)
        self.assertEqual(window.install_button.text(), "Repair MO2 runtime")
        self.assertTrue(window.install_button.isEnabled())
        self.assertFalse(window.launch_button.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
