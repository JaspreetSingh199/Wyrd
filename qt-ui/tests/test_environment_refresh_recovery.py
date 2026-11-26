"""Regression coverage for environment-scan UI recovery."""
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
class EnvironmentRefreshRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_scan_restores_combo_signals_and_suppresses_persistence(self):
        window = MainWindow()
        try:
            with patch(
                "environment_workflow.detect_skyrim",
                side_effect=RuntimeError("simulated discovery failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated discovery failure"):
                    window.refresh_all()

            self.assertFalse(window.proton_combo.signalsBlocked())
            self.assertTrue(window._restoring_selection)
            self.assertFalse(window.startup_checks_complete)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
