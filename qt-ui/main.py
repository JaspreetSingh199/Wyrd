#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, QTimer, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QVBoxLayout, QWidget, QTabWidget, QFrame,
)

from wyrd_settings import read_settings, write_settings, reset_wyrd_settings
from launch_models import LaunchSession
from logs_workflow import LogsWorkflowMixin
from ui_styles import APP_STYLE as STYLE
from ui_pages import MainWindowPagesMixin
from launch_workflow import LaunchWorkflowMixin
from registered_executable_workflow import RegisteredExecutableWorkflowMixin
from launch_preflight_workflow import LaunchPreflightWorkflowMixin
from process_workflow import ProcessMonitorWorkflowMixin
from launch_diagnostics_workflow import LaunchDiagnosticsWorkflowMixin
from manual_process_diagnostics_workflow import ManualProcessDiagnosticsWorkflowMixin
from environment_workflow import EnvironmentWorkflowMixin
from audio_workflow import AudioWorkflowMixin
from mo2_management_workflow import Mo2ManagementWorkflowMixin
from setup_workflow import SetupWorkflowMixin
from ui_path_helpers import _same_filesystem_path
from proton_runtime import managed_paths


class MainWindow(
    LaunchWorkflowMixin,
    RegisteredExecutableWorkflowMixin,
    LaunchPreflightWorkflowMixin,
    ProcessMonitorWorkflowMixin,
    LaunchDiagnosticsWorkflowMixin,
    LogsWorkflowMixin,
    ManualProcessDiagnosticsWorkflowMixin,
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
        self.resize(1440, 900)
        self.setMinimumSize(980, 680)
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
        self._mo2_session_timer = QTimer(self)
        self._mo2_session_timer.setInterval(1500)
        self._mo2_session_timer.timeout.connect(self._poll_mo2_session)
        self._mo2_session_prefix: Path | None = None
        self._mo2_session_seen = False
        self._mo2_session_polls = 0
        self._session_ui_timer = QTimer(self)
        self._session_ui_timer.setInterval(1500)
        self._session_ui_timer.timeout.connect(self._refresh_session_ui_status)
        self._logs_refresh_timer = QTimer(self)
        self._logs_refresh_timer.setInterval(1000)
        self._logs_refresh_timer.timeout.connect(self.refresh_launch_log_view)
        self._saved_settings = read_settings()
        self._saved_game_path = self._saved_settings.get("game_path")
        # Startup restoration is asynchronous: keep persistence suppressed until
        # refresh_all has populated the runtime list and restored the saved choice.
        self._restoring_selection = True
        self.startup_checks_complete = False

        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        sidebar_layout.setSpacing(10)
        crest = QLabel("ᛉ")
        crest.setObjectName("sidebarCrest")
        crest.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(crest)
        brand = QLabel("WYRD")
        brand.setObjectName("sidebarBrand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(brand)
        brand_subtitle = QLabel("SKYRIM MODDING\nENVIRONMENT")
        brand_subtitle.setObjectName("sidebarSubtitle")
        brand_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(brand_subtitle)
        sidebar_layout.addSpacing(30)

        self.environment_nav = QPushButton("⌂   Environment")
        self.environment_nav.setObjectName("navButton")
        self.environment_nav.setCheckable(True)
        self.environment_nav.setChecked(True)
        self.maintenance_nav = QPushButton("⚒   Maintenance")
        self.logs_nav = QPushButton("☷   Logs")
        self.logs_nav.setObjectName("navButton")
        self.logs_nav.setCheckable(True)
        self.maintenance_nav.setObjectName("navButton")
        self.maintenance_nav.setCheckable(True)
        self.environment_nav.clicked.connect(lambda: self._select_page(0))
        self.maintenance_nav.clicked.connect(lambda: self._select_page(1))
        self.logs_nav.clicked.connect(lambda: self._select_page(2))
        sidebar_layout.addWidget(self.environment_nav)
        sidebar_layout.addWidget(self.maintenance_nav)
        sidebar_layout.addWidget(self.logs_nav)
        sidebar_layout.addStretch(1)
        divider = QFrame()
        divider.setObjectName("sidebarDivider")
        divider.setFixedHeight(1)
        sidebar_layout.addWidget(divider)
        version = QLabel("v1.0.0 · MO2")
        version.setObjectName("sidebarVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(version)
        root_layout.addWidget(sidebar)

        content_root = QWidget()
        content_root.setObjectName("contentRoot")
        outer = QVBoxLayout(content_root)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.addStretch(1)
        self.refresh_button = QPushButton("↻  Refresh")
        self.refresh_button.setObjectName("secondaryAction")
        self.refresh_button.clicked.connect(self.refresh_all)
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().hide()
        outer.addWidget(self.tabs, 1)
        root_layout.addWidget(content_root, 1)

        self._build_pages(outer)
        self._session_ui_timer.start()
        QTimer.singleShot(0, self._refresh_session_ui_status)

        self.setCentralWidget(root)
        self._interaction_root = root
        self._interaction_lock_reason = None
        # A real overlay communicates why the workspace is unavailable while
        # keeping the underlying widgets enabled for status repainting/timers.
        self._interaction_overlay = QFrame(root)
        self._interaction_overlay.setObjectName("interactionOverlay")
        self._interaction_overlay.setGeometry(root.rect())
        overlay_layout = QVBoxLayout(self._interaction_overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        overlay_layout.addStretch(1)
        overlay_card = QFrame()
        overlay_card.setObjectName("interactionOverlayCard")
        overlay_card.setMaximumWidth(520)
        card_layout = QVBoxLayout(overlay_card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(10)
        self._interaction_overlay_title = QLabel("Wyrd is busy")
        self._interaction_overlay_title.setObjectName("interactionOverlayTitle")
        self._interaction_overlay_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._interaction_overlay_detail = QLabel("Please wait while the current operation finishes.")
        self._interaction_overlay_detail.setObjectName("interactionOverlayDetail")
        self._interaction_overlay_detail.setWordWrap(True)
        self._interaction_overlay_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._interaction_overlay_title)
        card_layout.addWidget(self._interaction_overlay_detail)
        overlay_layout.addWidget(overlay_card, 0, Qt.AlignmentFlag.AlignHCenter)
        overlay_layout.addStretch(1)
        self._interaction_overlay.hide()
        self._update_active_instance_from_scan()
        self._restoring_selection = False
        # Let Qt paint the startup checklist before any filesystem/Steam scans
        # begin. refresh_all updates each row as its read-only check completes.
        self.precheck_status.setText("Preparing startup checks…")
        self.precheck_progress.setFormat("Waiting…")
        QTimer.singleShot(150, self.refresh_all)
        # Show onboarding only after initial detection and path population finish.
        QTimer.singleShot(250, self._run_first_launch_setup)

    def _set_interaction_locked(self, locked: bool, reason: str = "") -> None:
        """Prevent main-window interaction during scans and active MO2/game sessions.

        The native window controls remain available, so users can still minimize
        or close Wyrd. Background process/session timers continue updating status.
        """
        root = getattr(self, "_interaction_root", None)
        overlay = getattr(self, "_interaction_overlay", None)
        if root is None or overlay is None:
            return
        if locked:
            self._interaction_lock_reason = reason or "A Wyrd operation is active."
            self._interaction_overlay_title.setText(
                "Environment checks in progress" if "check" in self._interaction_lock_reason.lower()
                or "scan" in self._interaction_lock_reason.lower()
                else "Wyrd is managing an active session"
            )
            self._interaction_overlay_detail.setText(
                self._interaction_lock_reason + "\n\nYour Skyrim and MO2 session is safe. "
                "Workspace controls will return when this operation finishes."
            )
            overlay.setGeometry(root.rect())
            overlay.show()
            overlay.raise_()
        else:
            self._interaction_lock_reason = None
            overlay.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        overlay = getattr(self, "_interaction_overlay", None)
        root = getattr(self, "_interaction_root", None)
        if overlay is not None and root is not None and overlay.isVisible():
            overlay.setGeometry(root.rect())
            overlay.raise_()

    def _select_page(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        self.environment_nav.setChecked(index == 0)
        self.maintenance_nav.setChecked(index == 1)
        self.logs_nav.setChecked(index == 2)
        if index == 2:
            self.refresh_application_log_view()
            self.refresh_launch_log_view()
            self.refresh_system_log_view()
            self._logs_refresh_timer.start()
        else:
            self._logs_refresh_timer.stop()

    def _log(self, message: str) -> None:
        """Append to the visible session and bounded persistent application log."""
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = f"[{stamp}] {message}"
        self.log_view.appendPlainText(entry)
        try:
            root = managed_paths()["root"]
            if root.is_symlink():
                return
            logs_dir = root / "logs"
            if logs_dir.is_symlink():
                return
            logs_dir.mkdir(parents=True, exist_ok=True)
            path = logs_dir / "application.log"
            rotated = logs_dir / "application.log.1"
            if path.is_symlink() or rotated.is_symlink():
                return
            if path.is_file() and path.stat().st_size > 2 * 1024 * 1024:
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(entry + "\n")
        except OSError:
            # Logging must never interrupt launch, detection or recovery workflows.
            pass

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
        """Show transient progress only while environment discovery is running."""
        self.progress_panel.show()
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
        self._mo2_session_timer.stop()
        self._session_ui_timer.stop()
        mo2_steam_timer = getattr(self, "_mo2_steam_wait_timer", None)
        if mo2_steam_timer is not None:
            mo2_steam_timer.stop()
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
