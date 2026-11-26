#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QGridLayout, QInputDialog,
    QWidget, QTabWidget, QScrollArea,
)

from skyrim_detection import detect_skyrim
from wyrd_mo2 import (
    read_settings, write_settings, reset_wyrd_settings,
)
from mo2_library_dialog import Mo2LibraryDialog, Mo2AdvancedSettings
from launch_models import LaunchSession, GameProcess
from ui_components import add_info_button, panel
from ui_styles import APP_STYLE as STYLE
from launch_workflow import LaunchWorkflowMixin
from process_workflow import ProcessMonitorWorkflowMixin
from environment_workflow import EnvironmentWorkflowMixin
from audio_workflow import AudioWorkflowMixin
from mo2_management_workflow import Mo2ManagementWorkflowMixin
from setup_workflow import SetupWorkflowMixin
from ui_path_helpers import _steam_prefix_for_game_path, _same_filesystem_path
class MainWindow(
    LaunchWorkflowMixin,
    ProcessMonitorWorkflowMixin,
    EnvironmentWorkflowMixin,
    AudioWorkflowMixin,
    Mo2ManagementWorkflowMixin,
    SetupWorkflowMixin,
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

        setup_page = QWidget()
        setup_layout = QVBoxLayout(setup_page)
        setup_layout.setContentsMargins(8, 12, 8, 12)
        setup_layout.setSpacing(12)

        self.precheck_panel = QFrame()
        self.precheck_panel.setObjectName("statusPanel")
        precheck_layout = QVBoxLayout(self.precheck_panel)
        precheck_layout.setContentsMargins(14, 10, 14, 10)
        precheck_top = QHBoxLayout()
        precheck_heading = QLabel("SYSTEM READINESS")
        precheck_heading.setObjectName("section")
        precheck_top.addWidget(precheck_heading)
        precheck_top.addStretch(1)
        self.precheck_status = QLabel("Checking your setup…")
        self.precheck_status.setObjectName("muted")
        self.precheck_status.setWordWrap(True)
        precheck_top.addWidget(self.precheck_status)
        precheck_layout.addLayout(precheck_top)
        readiness_grid = QGridLayout()
        readiness_grid.setHorizontalSpacing(16)
        readiness_grid.setVerticalSpacing(4)
        self.precheck_rows = []
        for index, label in enumerate((
            "Skyrim installation",
            "Compatible Proton runtime",
            "Skyrim Steam prefix and MO2 installation",
            "MO2 instances",
            "Active Wyrd environment",
        )):
            checkbox = QCheckBox(label)
            checkbox.setEnabled(False)
            readiness_grid.addWidget(checkbox, index // 3, index % 3)
            self.precheck_rows.append(checkbox)
        precheck_layout.addLayout(readiness_grid)
        self.precheck_progress = QProgressBar()
        self.precheck_progress.setRange(0, len(self.precheck_rows))
        self.precheck_progress.setValue(0)
        # The checklist itself communicates readiness. A 6px progress bar with
        # centered text was unreadable and visually collided with the checks.
        self.precheck_progress.hide()
        setup_layout.addWidget(self.precheck_panel)
        self.check_mo2_button = QPushButton("Check installation")
        self.check_mo2_button.setObjectName("secondaryAction")
        self.check_mo2_button.setToolTip(
            "Check whether Skyrim's Proton prefix, MO2 installation and MO2 instances exist."
        )
        self.check_mo2_button.clicked.connect(self.check_mo2_installation)
        progress_panel = QFrame()
        progress_panel.setObjectName("runtimePanel")
        progress_layout = QVBoxLayout(progress_panel)
        progress_layout.setContentsMargins(16, 12, 16, 14)
        progress_layout.setSpacing(9)
        progress_header = QHBoxLayout()
        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("section")
        progress_header.addWidget(self.progress_label, 1)
        progress_header.addWidget(self.check_mo2_button)
        progress_layout.addLayout(progress_header)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(30)
        self.progress.setMinimumWidth(260)
        progress_layout.addWidget(self.progress)
        setup_layout.addWidget(progress_panel)

        configuration_grid = QGridLayout()
        configuration_grid.setHorizontalSpacing(12)
        configuration_grid.setVerticalSpacing(12)
        self.game_panel = panel("SKYRIM", "Detecting your Steam installation…")
        configuration_grid.addWidget(self.game_panel, 0, 0)

        runtime_panel = QFrame()
        runtime_panel.setObjectName("panel")
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_layout.setContentsMargins(16, 14, 16, 14)
        runtime_heading = QLabel("PROTON RUNTIME")
        runtime_heading.setObjectName("section")
        runtime_detail = QLabel("Compatibility layer used by MO2 and Skyrim.")
        runtime_detail.setObjectName("muted")
        runtime_detail.setWordWrap(True)
        runtime_layout.addWidget(runtime_heading)
        runtime_layout.addWidget(runtime_detail)
        runtime_row = QHBoxLayout()
        self.proton_combo = QComboBox()
        self.proton_combo.setMinimumHeight(38)
        self.proton_combo.currentIndexChanged.connect(self._save_selection)
        runtime_row.addWidget(self.proton_combo, 1)
        runtime_layout.addLayout(runtime_row)
        self.runtime_status = QLabel("Looking for Proton…")
        self.runtime_status.setObjectName("muted")
        self.runtime_status.setWordWrap(True)
        runtime_layout.addWidget(self.runtime_status)
        configuration_grid.addWidget(runtime_panel, 0, 1)
        configuration_grid.setColumnStretch(0, 1)
        configuration_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(configuration_grid)

        self.mo2_panel = panel("MOD ORGANIZER 2", "Checking the installation…")
        setup_layout.addWidget(self.mo2_panel)

        detected = detect_skyrim()
        detected_game = detected.get("game_dir", "") if detected.get("found") else ""
        detected_prefix = _steam_prefix_for_game_path(detected_game) if detected_game else None
        detected_install = self.mo2_state.get("install") or (
            str(detected_prefix / "pfx/drive_c/Modding/MO2") if detected_prefix else ""
        )
        self.mo2_library_widget = Mo2LibraryDialog(
            self, prefix_path=str(detected_prefix) if detected_prefix else "",
            install_path=detected_install, game_path=detected_game,
            proton_path=self._saved_settings.get("proton", ""),
        )
        setup_layout.addWidget(self.mo2_library_widget)
        self.mo2_advanced_widget = Mo2AdvancedSettings(
            self
        )

        self.install_button = QPushButton("Install MO2")
        self.install_button.setObjectName("secondaryAction")
        self.install_button.clicked.connect(self.install_mo2)
        self.launch_game_button = QPushButton("Launch Modded Skyrim")
        self.launch_game_button.setObjectName("primaryAction")
        self.launch_game_button.setMinimumHeight(46)
        self.launch_game_button.clicked.connect(self.start_active_game)
        self.launch_game_button.setEnabled(False)
        self.launch_direct_button = QPushButton("Launch Unmodded")
        self.launch_direct_button.setObjectName("secondaryAction")
        self.launch_direct_button.clicked.connect(self.start_skyrim_direct)
        self.launch_direct_button.setEnabled(False)
        self.diagnostics_button = QPushButton("View launch log")
        self.diagnostics_button.clicked.connect(self.show_launch_diagnostics)
        self.launch_status_button = QPushButton("Check launch status")
        self.launch_status_button.clicked.connect(self.check_launch_status)
        self.active_instance_label = QLabel("No active environment selected")
        self.active_instance_label.setObjectName("muted")
        launch_panel = QFrame()
        launch_panel.setObjectName("launchPanel")
        launch_layout = QVBoxLayout(launch_panel)
        launch_layout.setContentsMargins(16, 14, 16, 14)
        launch_layout.setSpacing(8)
        launch_title = QLabel("READY TO PLAY?")
        launch_title.setObjectName("section")
        launch_layout.addWidget(launch_title)
        launch_layout.addWidget(self.active_instance_label)
        launch_actions = QHBoxLayout()
        launch_actions.addWidget(self.launch_game_button, 2)
        launch_actions.addWidget(self.launch_direct_button, 1)
        launch_layout.addLayout(launch_actions)
        self.launch_mo2_button = QPushButton("Launch MO2")
        self.launch_mo2_button.setObjectName("secondaryAction")
        self.launch_mo2_button.clicked.connect(self.launch_mo2_gui)
        utility_actions = QHBoxLayout()
        utility_actions.addWidget(self.install_button)
        utility_actions.addWidget(self.launch_mo2_button)
        utility_actions.addStretch(1)
        setup_layout.addLayout(utility_actions)

        setup_layout.addWidget(launch_panel)

        setup_layout.addStretch(1)
        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        setup_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        setup_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        setup_scroll.setWidget(setup_page)
        self.tabs.addTab(setup_scroll, "ENVIRONMENT")

        advanced_page = QWidget()
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.setContentsMargins(12, 16, 12, 12)
        advanced_layout.setSpacing(12)

        mo2_advanced_heading = QLabel("MO2 CONFIGURATION")
        mo2_advanced_heading.setObjectName("section")
        advanced_layout.addWidget(mo2_advanced_heading)
        mo2_advanced_help = QLabel(
            "Inspect detected paths, manage instance associations and configure per-instance storage. "
            "Audio, launch diagnostics and recovery tools are grouped under Maintenance."
        )
        mo2_advanced_help.setObjectName("muted")
        mo2_advanced_help.setWordWrap(True)
        advanced_layout.addWidget(mo2_advanced_help)
        advanced_layout.addWidget(self.mo2_advanced_widget)

        advanced_layout.addStretch(1)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        advanced_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        advanced_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        advanced_scroll.setWidget(advanced_page)
        self.tabs.addTab(advanced_scroll, "ADVANCED")

        maintenance_page = QWidget()
        maintenance_layout = QVBoxLayout(maintenance_page)
        maintenance_layout.setContentsMargins(12, 16, 12, 12)
        maintenance_layout.setSpacing(12)
        diagnostics_grid = QGridLayout()
        diagnostics_grid.setHorizontalSpacing(12)
        diagnostics_grid.setVerticalSpacing(12)

        proton_diagnostics_panel = panel(
            "PROTON DIAGNOSTICS",
            "Check the selected runtime locally. This does not launch Skyrim or prove in-game compatibility."
        )
        proton_diagnostics_row = QHBoxLayout()
        self.test_proton_button = QPushButton("Test selected Proton")
        self.test_proton_button.setObjectName("secondaryAction")
        self.test_proton_button.clicked.connect(self.test_selected_proton)
        proton_diagnostics_row.addWidget(self.test_proton_button)
        add_info_button(proton_diagnostics_row, self, "Test selected Proton", "Runs a local, read-only preflight of the selected Proton runtime. It checks that the runtime folder, executable launcher and payload are present. It does not launch Skyrim, modify the prefix, or prove that a game will work.")
        proton_diagnostics_row.addStretch(1)
        proton_diagnostics_panel.layout().addLayout(proton_diagnostics_row)
        diagnostics_grid.addWidget(proton_diagnostics_panel, 0, 0)

        self.audio_panel = panel(
            "AUDIO COMPATIBILITY",
            "Inspect audio components or enable the XAudio2 override."
        )
        audio_layout = self.audio_panel.layout()
        self.audio_detail = QLabel("Audio diagnostics will be checked during detection.")
        self.audio_detail.setObjectName("muted")
        self.audio_detail.setWordWrap(True)
        audio_layout.addWidget(self.audio_detail)
        audio_actions = QHBoxLayout()
        self.audio_check_button = QPushButton("Inspect audio libraries")
        self.audio_check_button.clicked.connect(self.check_audio_libraries)
        audio_actions.addWidget(self.audio_check_button)
        add_info_button(audio_actions, self, "Inspect audio libraries", "Checks the Skyrim Proton prefix for relevant audio libraries and reports what Wyrd can detect. This is diagnostic only; it does not install or replace audio files. The XAudio2 checkbox separately controls the launch-time compatibility override.")
        self.xaudio_override_checkbox = QCheckBox("Enable XAudio2 compatibility override")
        self.xaudio_override_checkbox.setChecked(bool(self._saved_settings.get("xaudio2_compat", False)))
        self.xaudio_override_checkbox.toggled.connect(self._save_audio_option)
        audio_actions.addWidget(self.xaudio_override_checkbox)
        audio_actions.addStretch(1)
        audio_layout.addLayout(audio_actions)
        diagnostics_grid.addWidget(self.audio_panel, 0, 1)

        diagnostics_panel = panel(
            "LAUNCH DIAGNOSTICS",
            "Check whether Skyrim or SKSE is running and open the detailed launch log when troubleshooting."
        )
        diagnostic_row = QHBoxLayout()
        diagnostic_row.addWidget(self.diagnostics_button)
        add_info_button(diagnostic_row, self, "View launch log", "Opens the recent Skyrim/MO2 launch log. Use it when a launch fails or when you need the detailed Proton and MO2 output.")
        diagnostic_row.addWidget(self.launch_status_button)
        add_info_button(diagnostic_row, self, "Check launch status", "Scans for Skyrim/SKSE processes and checks the launch handoff Wyrd is tracking. It can report a running process, an exited process, or an inconclusive result; it does not start or stop the game.")
        diagnostic_row.addStretch(1)
        diagnostics_panel.layout().addLayout(diagnostic_row)
        diagnostics_grid.addWidget(diagnostics_panel, 1, 0, 1, 2)
        diagnostics_grid.setColumnStretch(0, 1)
        diagnostics_grid.setColumnStretch(1, 1)
        maintenance_layout.addLayout(diagnostics_grid)

        logs_panel = panel(
            "SETUP LOG",
            "Recent detection, installation and launch messages."
        )
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(400)
        self.log_view.setPlaceholderText("Detection and installation progress will appear here.")
        self.log_view.setMinimumHeight(180)
        self.log_view.setMaximumHeight(260)
        logs_panel.layout().addWidget(self.log_view)
        maintenance_layout.addWidget(logs_panel)



        recovery_panel = panel(
            "RECOVERY & MAINTENANCE",
            "Rarely needed actions. Wyrd's Python packages are separate from MO2's Proton installation; reset actions do not remove Skyrim saves or Steam files."
        )
        maintenance_row = QHBoxLayout()
        self.reset_mo2_button = QPushButton("Remove MO2")
        self.reset_mo2_button.setToolTip(
            "Removes only the verified Wyrd-installed MO2 program directory. Existing instances and Skyrim data are preserved."
        )
        self.reset_mo2_button.clicked.connect(self.reset_managed_mo2)
        maintenance_row.addWidget(self.reset_mo2_button)
        add_info_button(maintenance_row, self, "Remove MO2", "Removes only the verified Wyrd-installed MO2 program directory. Existing MO2 instances, profiles, mods, downloads, custom storage, Skyrim saves and the Steam prefix are preserved.")
        self.reset_settings_button = QPushButton("Reset Wyrd Settings")
        self.reset_settings_button.setToolTip(
            "Resets Wyrd preferences; MO2 files and existing instances are left untouched. First-launch setup appears on the next application launch."
        )
        self.reset_settings_button.clicked.connect(self.reset_wyrd_preferences)
        maintenance_row.addWidget(self.reset_settings_button)
        add_info_button(maintenance_row, self, "Reset Wyrd Settings", "Resets Wyrd preferences without changing MO2 files or instances. It does not launch first-time setup immediately; setup is offered on the next application launch.")
        maintenance_row.addStretch(1)
        recovery_panel.layout().addLayout(maintenance_row)
        maintenance_layout.addWidget(recovery_panel)
        maintenance_layout.addStretch(1)
        maintenance_scroll = QScrollArea()
        maintenance_scroll.setWidgetResizable(True)
        maintenance_scroll.setFrameShape(QFrame.Shape.NoFrame)
        maintenance_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        maintenance_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        maintenance_scroll.setWidget(maintenance_page)
        self.tabs.addTab(maintenance_scroll, "MAINTENANCE")

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
