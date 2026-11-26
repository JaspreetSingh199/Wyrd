#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QVBoxLayout, QInputDialog, QWidget, QTabWidget,
)

from skyrim_detection import detect_skyrim
from wyrd_mo2 import (
    read_settings, write_settings, reset_wyrd_settings,
)
from launch_models import LaunchSession
from ui_styles import APP_STYLE as STYLE
from ui_pages import MainWindowPagesMixin
from launch_workflow import LaunchWorkflowMixin
from process_workflow import ProcessMonitorWorkflowMixin
from environment_workflow import EnvironmentWorkflowMixin
from audio_workflow import AudioWorkflowMixin
from mo2_management_workflow import Mo2ManagementWorkflowMixin
from setup_workflow import SetupWorkflowMixin
class MainWindow(
    LaunchWorkflowMixin,
    ProcessMonitorWorkflowMixin,
    EnvironmentWorkflowMixin,
    AudioWorkflowMixin,
    Mo2ManagementWorkflowMixin,
    SetupWorkflowMixin,
    MainWindowPagesMixin,
    QMainWindow,
):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wyrd — Skyrim Modding Environment")
        self.resize(1100, 820)
        self.setMinimumSize(760, 600)
        self.worker: QThread | None = None
        self.game_state: dict = {"found": False}
        self.proton_builds: list[dict] = []
        self.mo2_state: dict = {"installed": False}
        # Populated by refresh_all(); readiness checks must be safe before first scan.
        self.mo2_discovery_state: dict | None = None
        self.instance_state: dict = {"ok": False, "instances": [], "skyrim_instances": []}
        self.launch_target_state: dict = {"ok": False, "targets": []}
        self.launch_session = LaunchSession()
        self._launch_poll_interval_ms = 2000
        self._launch_poll_limit = 150
        self._launch_monitor = QTimer(self)
        self._launch_monitor.setInterval(self._launch_poll_interval_ms)
        self._launch_monitor.timeout.connect(self._poll_launch_status)
        self._game_watch_timer = QTimer(self)
        self._game_watch_timer.setInterval(self._launch_poll_interval_ms)
        self._game_watch_timer.timeout.connect(self._revalidate_detected_game_processes)
        self._saved_settings = read_settings()
        self._saved_game_path = self._saved_settings.get("game_path")
        # Startup restoration is asynchronous: keep persistence suppressed until
        # refresh_all has populated the runtime list and restored the saved choice.
        self._restoring_selection = True
        self.startup_checks_complete = False

        root = QWidget()
        root.setObjectName("appRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        heading = QLabel("WYRD")
        heading.setObjectName("heading")
        subtitle = QLabel("SKYRIM SPECIAL EDITION  /  MODDING ENVIRONMENT")
        subtitle.setObjectName("muted")
        brand.addWidget(heading)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryAction")
        self.refresh_button.clicked.connect(self.refresh_all)
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs, 1)

        self._build_pages(outer)

        self.setCentralWidget(root)
        self._update_active_instance_from_scan()
        self._restoring_selection = False
        # Let Qt paint the startup checklist before any filesystem/Steam scans
        # begin. refresh_all updates each row as its read-only check completes.
        self.precheck_status.setText("Preparing startup checks…")
        self.precheck_progress.setFormat("Waiting…")
        QTimer.singleShot(150, self.refresh_all)
        # Show onboarding only after initial detection and path population finish.
        QTimer.singleShot(250, self._run_first_launch_setup)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _reset_precheck(self) -> None:
        """Put the visible startup checklist into its pending state."""
        labels = (
            "Skyrim installation",
            "Compatible Proton runtime",
            "Skyrim Steam prefix and MO2 installation",
            "MO2 instances",
            "Active Wyrd environment",
        )
        self.precheck_progress.setRange(0, len(self.precheck_rows))
        self.precheck_progress.setValue(0)
        self.precheck_progress.setFormat("Checking…")
        for checkbox, label in zip(self.precheck_rows, labels):
            checkbox.setChecked(False)
            checkbox.setText(f"{label} — PENDING")
        self.precheck_status.setText("Checking Skyrim, Proton and MO2 instances…")
        QCoreApplication.processEvents()

    def _set_scan_progress(self, value: int, message: str) -> None:
        """Update the shared, prominent progress bar during environment discovery."""
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(value))))
        self.progress_label.setText(message)
        QCoreApplication.processEvents()

    def _update_precheck(self, index: int, label: str, passed: bool | None, detail: str) -> None:
        """Update the visible startup checklist as each read-only check completes."""
        if index < 0 or index >= len(self.precheck_rows):
            return
        row = self.precheck_rows[index]
        row.setChecked(bool(passed))
        state = "OK" if passed else "NOT FOUND" if passed is False else "SKIPPED"
        row.setText(f"{label} — {state}")
        self.precheck_progress.setValue(index + 1)
        self.precheck_progress.setFormat(f"{index + 1}/{len(self.precheck_rows)} checks")
        self.precheck_status.setText(detail)
        QCoreApplication.processEvents()

    def _restore_saved_selection(self) -> None:
        """Restore the last selected Proton when it is still discoverable."""
        saved = self._saved_settings.get("proton")
        if not saved:
            return
        for index in range(self.proton_combo.count()):
            if _same_filesystem_path(self.proton_combo.itemData(index), saved):
                self.proton_combo.setCurrentIndex(index)
                self._log("Restored saved Proton selection: " + saved)
                return
        self._log("Saved Proton selection is unavailable; keeping the detected default.")

    def _save_selection(self, _index: int = -1) -> None:
        if self._restoring_selection:
            return
        proton = self.proton_combo.currentData()
        game = self.game_state.get("game_dir")
        if not proton or not game:
            return
        try:
            write_settings(None, Path(proton), Path(game),
                           xaudio2_compat=self.xaudio_override_checkbox.isChecked())
            self._saved_settings = {
                **self._saved_settings,
                "proton": str(Path(proton).resolve()),
                "game_path": str(Path(game).resolve()),
                "xaudio2_compat": self.xaudio_override_checkbox.isChecked(),
            }
        except OSError as error:
            self._log(f"Could not save Wyrd settings: {error}")

    def reset_wyrd_preferences(self) -> None:
        """Restore defaults now and leave first-run onboarding for the next app launch."""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Reset Wyrd Settings", "Wait for the current task to finish first.")
            return
        answer = QMessageBox.question(
            self, "Reset Wyrd Settings",
            "Reset Wyrd's preferences to their defaults?\n\n"
            "This does NOT uninstall Wyrd or remove MO2, Skyrim, Steam compatdata, game files or saves. "
            "First-launch setup will appear next time you start Wyrd.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = reset_wyrd_settings()
        if not result.get("ok"):
            QMessageBox.warning(self, "Reset Wyrd Settings", result.get("message", "Could not reset Wyrd settings."))
            self._log(result.get("message", "Wyrd settings reset failed."))
            return

        # Reset preferences, but keep the user's existing MO2 connection intact.
        self._saved_settings = {"first_run_completed": False}
        self._saved_game_path = None
        self._restoring_selection = True
        self.proton_combo.blockSignals(True)
        if self.proton_combo.count():
            self.proton_combo.setCurrentIndex(0)
        self.proton_combo.blockSignals(False)
        self.mo2_library_widget.preferred_instance_id = ""
        # Do not schedule onboarding in this session. Leave the completion flag
        # clear so normal startup offers setup on the next app launch.
        self._saved_settings["first_run_completed"] = False
        self._log(result.get("message", "Wyrd preferences reset to defaults."))
        self.refresh_all()
        self._restoring_selection = False
        self.progress_label.setText("Wyrd settings reset. Default settings restored.")

    def closeEvent(self, event) -> None:
        self._stop_launch_monitor("application is closing")
        self._game_watch_timer.stop()
        if self.worker and self.worker.isRunning():
            QMessageBox.information(
                self, "Setup is running",
                "MO2 installation is still running. Wait for it to finish before closing Wyrd."
            )
            event.ignore()
            return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
