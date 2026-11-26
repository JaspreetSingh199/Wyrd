#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QGridLayout, QInputDialog, QDialog,
    QWidget, QTabWidget, QScrollArea,
)

from skyrim_detection import detect_skyrim
from proton_runtime import discover_proton_builds, managed_paths, _steam_root_for_game, _runtime_environment
from process_monitor import (
    launch_process_status, detect_skyrim_processes, _proc_start_time,
    _proc_identity, _is_descendant_process, read_launch_diagnostics,
    classify_launch_diagnostics,
)
from mo2_instances import inspect_mo2_instances
from wyrd_mo2 import (
    read_settings, write_settings, mark_first_run_completed,
    inspect_skyrim_launch_targets, inspect_audio_compatibility, reset_wyrd_settings,
)
from steamcompat import (
    discover_wine_game_path, configure_skyrim_se_registry,
    synchronize_mo2_instance_game_paths, verify_wine_game_path,
)
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_skyrim_prefix_mo2_instances, inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances,
)
from steam_mo2_launch import launch_skyrim_via_mo2_in_skyrim_prefix
from steam_prefix import steam_compat_prefix
from steam_mo2_management import reset_wyrd_mo2
from mo2_library_dialog import Mo2LibraryDialog, Mo2AdvancedSettings
from launch_models import LaunchSession
from ui_components import add_info_button, panel
from steam_workers import SteamPrefixInstallWorker
from ui_styles import APP_STYLE as STYLE
from launch_workflow import LaunchWorkflowMixin
from ui_path_helpers import _steam_prefix_for_game_path
def _same_filesystem_path(left: str | os.PathLike | None,
                         right: str | os.PathLike | None) -> bool:
    """Compare discovered and persisted paths after normalizing aliases."""
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normpath(os.path.expanduser(str(left))) == os.path.normpath(os.path.expanduser(str(right)))




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

    def _run_first_launch_setup(self) -> None:
        """Offer first-run environment setup and let the user choose Proton."""
        if self._saved_settings.get("first_run_completed"):
            return
        game_ready = bool(self.game_state.get("found"))
        if not game_ready:
            QMessageBox.information(
                self, "Welcome to Wyrd",
                "Wyrd could not find Skyrim Special Edition yet. Install or locate it "
                "through Steam, then use Refresh to try again."
            )
            return
        if not self.proton_builds:
            QMessageBox.information(
                self, "Welcome to Wyrd",
                "Wyrd found Skyrim, but no compatible Proton runtime. Install one "
                "through Steam or ProtonUp-Qt, then use Refresh."
            )
            return

        # Use the ranked list as a sensible default, but always let the user
        # choose. Never silently force a community or official runtime.
        choices = [
            f'{item["name"]} ({item["architecture"]})'
            for item in self.proton_builds
        ]
        saved_path = self._saved_settings.get("proton")
        default_index = next(
            (
                i for i, item in enumerate(self.proton_builds)
                if saved_path and _same_filesystem_path(item["path"], saved_path)
            ),
            0,
        )
        chosen, accepted = QInputDialog.getItem(
            self, "Choose Proton for Wyrd",
            "Which compatibility runtime should Wyrd use for Skyrim and MO2?",
            choices, default_index, False,
        )
        if not accepted or not chosen:
            self.progress_label.setText("Setup paused. Choose a Proton runtime when ready.")
            return
        mark_first_run_completed()
        self._saved_settings["first_run_completed"] = True
        selected_index = choices.index(chosen)
        self.proton_combo.blockSignals(True)
        self.proton_combo.setCurrentIndex(selected_index)
        self.proton_combo.blockSignals(False)

        answer = QMessageBox.question(
            self, "Welcome to Wyrd",
            "Use the selected Skyrim and Proton settings?\n\n"
            "Wyrd will use Skyrim's existing Steam compatdata folder. "
            "It will not reset the prefix or change game files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.progress_label.setText("Environment setup skipped. You can configure it later.")
            return

        self._save_selection(selected_index)
        self.manage_mo2_library()
        self.progress_label.setText("Wyrd environment configured.")
        self._log(
            "First-launch setup: selected Proton "
            + str(self.proton_combo.currentText())
            + " for Skyrim."
        )

        if self.mo2_state.get("installed"):
            # MO2 is only entered through the modded Skyrim launch action.
            return

        answer = QMessageBox.question(
            self, "Set up Mod Organizer 2?",
            "Would you like Wyrd to download and install the official Mod Organizer 2 "
            "into Skyrim's existing Steam compatdata prefix now?\n\n"
            "You can skip this and launch unmodded Skyrim instead. MO2 can be installed later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.install_mo2(confirm=False)
        else:
            self.progress_label.setText("MO2 setup skipped. You can launch unmodded Skyrim.")

    def test_selected_proton(self) -> None:
        """Perform a non-destructive preflight of the selected Proton runtime."""
        raw_path = self.proton_combo.currentData()
        if not raw_path:
            QMessageBox.warning(self, "Test Proton", "Choose a Proton runtime first.")
            return
        runtime = Path(str(raw_path)).expanduser()
        executable = runtime / "proton"
        problems = []
        if runtime.is_symlink() or not runtime.is_dir():
            problems.append("The runtime folder is missing or is a symlink.")
        if executable.is_symlink() or not executable.is_file():
            problems.append("The Proton launcher is missing or unsafe.")
        elif not os.access(executable, os.X_OK):
            problems.append("The Proton launcher is not executable.")
        if not (runtime / "files").is_dir() and not (runtime / "dist").is_dir():
            problems.append("The runtime payload folder (files or dist) is missing.")
        if problems:
            message = "Proton check failed:\n\n" + "\n".join(f"• {item}" for item in problems)
            self.runtime_status.setText("Selected Proton needs attention.")
            self._log("Proton preflight failed: " + "; ".join(problems))
            QMessageBox.warning(self, "Proton check failed", message)
            return
        message = (
            f"{self.proton_combo.currentText()} passed the local runtime check.\n\n"
            "Wyrd found an executable Proton launcher and its runtime payload. "
            "This check does not launch a game or prove that the runtime works in-game."
        )
        self.runtime_status.setText("Selected Proton passed the local runtime check.")
        self._log("Proton preflight passed: " + str(runtime))
        QMessageBox.information(self, "Proton check passed", message)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _log_audio_runtime_environment(self, processes: list[dict]) -> None:
        """Log the whitelisted audio environment read from actual game processes."""
        for item in processes:
            modules = item.get("audio_module_mappings", {})
            maps_readable = item.get("audio_module_mappings_readable")
            if maps_readable is False:
                reason = item.get("audio_module_mappings_error") or "access denied"
                self._log(
                    f"Audio DLL mappings from PID {item.get('pid')} ({item.get('binary')}): "
                    f"<unavailable: {reason}>"
                )
            else:
                observed = [name for name, path in modules.items() if path]
                absent = [name for name, path in modules.items() if not path]
                self._log(
                    f"Audio DLL mappings from PID {item.get('pid')} ({item.get('binary')}): "
                    f"observed={', '.join(observed) if observed else 'none'}; "
                    f"not observed={', '.join(absent) if absent else 'none'} "
                    "(not proof of audio functionality)"
                )
            audio_env = item.get("audio_runtime_environment", {})
            readable = item.get("audio_runtime_environment_readable")
            if readable is False:
                reason = item.get("audio_runtime_environment_error") or "access denied"
                self._log(
                    f"Audio runtime env from PID {item.get('pid')} ({item.get('binary')}): "
                    f"<unavailable: {reason}>; values were not inspected"
                )
                continue
            overrides = audio_env.get("WINEDLLOVERRIDES")
            pulse = audio_env.get("PULSE_LATENCY_MSEC")
            self._log(
                f"Audio runtime env from PID {item.get('pid')} ({item.get('binary')}): "
                f"WINEDLLOVERRIDES={overrides if overrides is not None else '<not present in initial environment>'}; "
                f"PULSE_LATENCY_MSEC={pulse if pulse is not None else '<not present in initial environment>'} "
                "(read from /proc; may not reflect later runtime changes)"
            )

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

    def refresh_all(self) -> None:
        self.startup_checks_complete = False
        self._set_scan_progress(2, "Starting environment scan…")
        self.launch_game_button.setEnabled(False)
        self._reset_precheck()
        selected = self.proton_combo.currentData()
        # Suppress currentIndexChanged while rebuilding the combo. Otherwise
        # clearing it can save an empty/default selection before we restore
        # the persisted runtime, overwriting the user's choice.
        self._restoring_selection = True
        self.proton_combo.blockSignals(True)
        self._set_scan_progress(12, "Scanning Steam libraries for Skyrim Special Edition…")
        self.game_state = detect_skyrim()
        saved_game = self._saved_settings.get("game_path")
        if saved_game and Path(saved_game).is_dir():
            detected_game = self.game_state.get("game_dir")
            if not self.game_state.get("found") or detected_game != saved_game:
                self.game_state = {
                    **self.game_state, "found": True,
                    "game_dir": saved_game, "library": "saved Wyrd selection",
                }
        self._update_precheck(
            0, "Skyrim installation", bool(self.game_state.get("found")),
            self.game_state.get("game_dir", "Skyrim Special Edition was not detected."),
        )
        if self.game_state.get("found"):
            game_path = self.game_state["game_dir"]
            self.game_panel.detail_label.setText(
                "Detected:\n" + game_path +
                "\nSteam library: " + self.game_state.get("library", "detected")
            )
            self._log("Skyrim SE detected: " + game_path)
        else:
            roots = self.game_state.get("steam_roots", [])
            self.game_panel.detail_label.setText(
                "Skyrim Special Edition (AppID 489830) was not found. "
                "Install it through Steam and refresh detection.\nSteam roots checked: " +
                (", ".join(roots) if roots else "No Steam library detected.")
            )
            self._log("Skyrim SE not detected.")

        self._set_scan_progress(28, "Discovering compatible Proton runtimes…")
        self.proton_builds = discover_proton_builds()
        self._update_precheck(
            1, "Compatible Proton runtime", bool(self.proton_builds),
            f"{len(self.proton_builds)} compatible Proton runtime(s) found."
            if self.proton_builds else "No compatible Proton runtime was found.",
        )
        self.proton_combo.clear()
        selected_index = -1
        for build in self.proton_builds:
            label = f'{build["name"]} ({build["architecture"]})'
            self.proton_combo.addItem(label, build["path"])
            self.proton_combo.setItemData(self.proton_combo.count() - 1, build["path"], 3)
            if selected and _same_filesystem_path(selected, build["path"]):
                selected_index = self.proton_combo.count() - 1
        saved_proton = self._saved_settings.get("proton")
        preferred_proton = selected or saved_proton
        if preferred_proton:
            selected_index = next(
                (index for index, build in enumerate(self.proton_builds)
                 if _same_filesystem_path(build["path"], preferred_proton)),
                -1,
            )
        if selected_index >= 0:
            self.proton_combo.setCurrentIndex(selected_index)
        if self.proton_builds:
            self.runtime_status.setText(
                f'{len(self.proton_builds)} compatible Proton build(s) found. '
                "The selected runtime will be saved with this Wyrd installation."
            )
            self._log("Proton discovery: " + ", ".join(item["name"] for item in self.proton_builds))
        else:
            self.runtime_status.setText(
                "No compatible Proton build found. Install a current x86_64 Proton build through Steam or ProtonUp-Qt, then refresh."
            )
            self._log("No compatible Proton builds found.")

        self._set_scan_progress(43, "Locating MO2 in Skyrim’s Proton environment…")
        self.mo2_state = inspect_skyrim_prefix_mo2_installation(
            Path(self.game_state["game_dir"])
        ) if self.game_state.get("found") else {
            "installed": False, "managed": False,
            "message": "Skyrim Special Edition was not detected."
        }
        if not self.mo2_state.get("installed") and self.game_state.get("found"):
            self.mo2_state = inspect_external_skyrim_prefix_mo2_installation(
                Path(self.game_state["game_dir"])
            )
        self._set_scan_progress(58, "Verifying Proton drive mappings and synchronizing MO2 game paths…")
        self.mo2_discovery_state = None
        if self.mo2_state.get("installed") and self.game_state.get("found"):
            try:
                game_path = Path(self.game_state["game_dir"]).expanduser()
                steam_root = _steam_root_for_game(game_path, Path.home())
                self.mo2_discovery_state = discover_wine_game_path(
                    Path(self.mo2_state["prefix"]), game_path, steam_root
                )
                if self.mo2_discovery_state.get("ok"):
                    proton_value = self.proton_combo.currentData()
                    if proton_value:
                        wine_path_state = verify_wine_game_path(
                            Path(proton_value), Path(self.mo2_state["prefix"]),
                            steam_root, self.mo2_discovery_state["windows_game_path"],
                        )
                    else:
                        wine_path_state = {
                            "ok": False, "message": "No Proton runtime is selected."
                        }
                    self.mo2_discovery_state["proton_path_verification"] = wine_path_state
                    if not wine_path_state.get("ok"):
                        self.mo2_discovery_state["ok"] = False
                        self._log(
                            "Proton game-path verification failed closed: "
                            + wine_path_state.get("message", "unknown error")
                        )
                    sync_state = (
                        synchronize_mo2_instance_game_paths(
                            Path(self.mo2_state["prefix"]), game_path,
                            self.mo2_discovery_state["windows_game_path"],
                        )
                        if wine_path_state.get("ok") else
                        {"ok": False, "message": wine_path_state.get("message", "Proton path verification failed.")}
                    )
                    self.mo2_discovery_state["instance_sync"] = sync_state
                    if sync_state.get("ok"):
                        changed = sync_state.get("updated", [])
                        unchanged = sync_state.get("unchanged", [])
                        self._log(
                            "MO2 gamePath synchronization: "
                            f"{len(changed)} updated, {len(unchanged)} already current; "
                            + self.mo2_discovery_state["windows_game_path"]
                        )
                    else:
                        self.mo2_discovery_state["ok"] = False
                        self._log(
                            "MO2 gamePath synchronization failed closed: "
                            + sync_state.get("message", "unknown error")
                        )
                    if proton_value and self.mo2_discovery_state.get("ok"):
                        registry_state = configure_skyrim_se_registry(
                            Path(self.mo2_state["prefix"]), game_path, steam_root,
                            Path(proton_value), self.mo2_discovery_state["windows_game_path"],
                        )
                        self.mo2_discovery_state["registry"] = registry_state
                        if registry_state.get("ok"):
                            self._log("MO2 Bethesda registry: " + registry_state["windows_path"])
                        else:
                            self.mo2_discovery_state["ok"] = False
                            self._log("MO2 Bethesda registry provisioning failed closed: " + registry_state.get("message", "unknown error"))
                    elif not proton_value:
                        self.mo2_discovery_state["registry"] = {
                            "ok": False, "message": "No Proton runtime is selected."
                        }
            except (OSError, RuntimeError, ValueError) as error:
                self.mo2_discovery_state = {"ok": False, "message": str(error)}
            if self.mo2_discovery_state.get("ok"):
                self._log(
                    "MO2 Steam discovery: "
                    + self.mo2_discovery_state["windows_game_path"]
                    + " resolves to "
                    + self.mo2_discovery_state["resolved_game_path"]
                )
            else:
                self._log(
                    "MO2 Steam discovery verification failed closed: "
                    + self.mo2_discovery_state.get("message", "unknown mapping error")
                )
        self._update_precheck(
            2, "Skyrim prefix and MO2 installation",
            bool(self.mo2_state.get("installed")),
            self.mo2_state.get("message", "MO2 installation was not found."),
        )
        self.mo2_target_occupied = bool(
            self.mo2_state.get("installed") or self.mo2_state.get("install")
        )
        self.reset_mo2_button.setEnabled(bool(
            self.mo2_state.get("installed") and self.mo2_state.get("managed", True)
        ))
        self._set_scan_progress(78, "Scanning MO2 installations and validating instance configurations…")
        if self.game_state.get("found"):
            # Instance inventory is independent of the conventional MO2
            # executable check. Existing instances remain discoverable even
            # when MO2 is installed elsewhere or its executable is unknown.
            self.instance_state = inspect_any_skyrim_prefix_mo2_instances(
                Path(self.game_state["game_dir"])
            )
        else:
            self.instance_state = {
                "ok": False, "instances": [], "skyrim_instances": [],
                "message": "Skyrim Special Edition was not detected; MO2 instance discovery was skipped."
            }
        skyrim_instances = self.instance_state.get("skyrim_instances", [])
        self._update_precheck(
            3, "MO2 instances",
            bool(self.instance_state.get("ok") and self.instance_state.get("instances")),
            f"{len(self.instance_state.get('instances', []))} MO2 instance(s), "
            f"{len(skyrim_instances)} Skyrim SE instance(s) found."
            if self.instance_state.get("ok") else self.instance_state.get("message", "Instance scan unavailable."),
        )
        if self.mo2_state.get("installed"):
            summary = self.instance_state.get("message", "MO2 instance inventory unavailable.")
            if self.mo2_discovery_state is not None:
                if self.mo2_discovery_state.get("ok"):
                    summary += (
                        "\\nSteam discovery: " + self.mo2_discovery_state["windows_game_path"]
                        + " → " + self.mo2_discovery_state["resolved_game_path"]
                    )
                else:
                    summary += (
                        "\\nSteam discovery NOT VERIFIED: "
                        + self.mo2_discovery_state.get("message", "unknown mapping error")
                    )
            if skyrim_instances:
                summary += "\nSkyrim SE instance(s): " + ", ".join(x["name"] for x in skyrim_instances)
                summary += "\nSelected MO2 profile: " + " | ".join(
                    f'{x["name"]}: {x.get("selected_profile") or "No profile selected"}'
                    for x in skyrim_instances
                )
            elif self.instance_state.get("ok"):
                summary += "\nCreate a Skyrim Special Edition instance in MO2 to continue."
            owner = "Wyrd-managed" if self.mo2_state.get("managed", True) else "External (read-only)"
            # Keep the main screen human-readable. Full filesystem paths belong
            # on Advanced → Paths, not in the primary setup/launch workflow.
            if skyrim_instances:
                instance_summary = (
                    f"{len(skyrim_instances)} Skyrim Special Edition instance(s) detected: "
                    + ", ".join(item["name"] for item in skyrim_instances)
                )
            elif self.instance_state.get("ok"):
                instance_summary = (
                    "MO2 is installed, but no Skyrim Special Edition instance is configured yet. "
                    "Open MO2 and create a Skyrim Special Edition instance, then return here."
                )
            else:
                instance_summary = self.instance_state.get(
                    "message", "MO2 instance discovery is unavailable."
                )
            self.mo2_panel.detail_label.setText(
                f"{owner} installation · {self.mo2_state.get('version', 'MO2')}\n"
                f"{instance_summary}"
            )
            self.install_button.hide()
            self.launch_mo2_button.show()
            self.launch_mo2_button.setEnabled(
                bool(self.mo2_state.get("executable") and self.proton_combo.currentData())
            )
            self.launch_game_button.setEnabled(False)
            self._log("Skyrim-prefix MO2 inspection: " + owner + " installation.")
        else:
            self.mo2_panel.detail_label.setText(self.mo2_state.get("message", "MO2 is not installed."))
            self.install_button.setText("Install MO2")
            self.install_button.show()
            self.launch_mo2_button.hide()
            self.install_button.setEnabled(bool(self.game_state.get("found") and self.proton_builds))
            self.launch_game_button.setEnabled(False)
            self.launch_direct_button.setEnabled(bool(self.game_state.get("found")))

        self._set_scan_progress(92, "Finalizing environment readiness…")
        self._refresh_audio_status()
        ready = bool(self.game_state.get("found") and self.proton_builds)
        self.install_button.setEnabled(
            ready
            and not (self.worker and self.worker.isRunning())
            and not getattr(self, "mo2_target_occupied", self.mo2_state.get("installed"))
        )
        self.proton_combo.setEnabled(bool(self.proton_builds) and not (self.worker and self.worker.isRunning()))
        self._restore_saved_selection()
        self.proton_combo.blockSignals(False)
        # Resume persistence only after a valid runtime is selected. An unavailable
        # saved path must not be replaced with a guessed/default Proton build.
        self._restoring_selection = not bool(self.proton_combo.currentData())
        # Keep both MO2 panels synchronized with detected Skyrim and the
        # selected Proton runtime. The default prefix is Skyrim's existing
        # Steam compatdata directory; this does not create or reset a prefix.
        self.manage_mo2_library()
        # A modded launch stays disabled until every startup check has completed.
        self.startup_checks_complete = True
        # Live MO2 inventory is authoritative; Wyrd registration is not required.
        self._update_active_instance_from_scan()
        active_ready = self.launch_game_button.isEnabled()
        self._update_precheck(
            4, "Detected MO2 environment", active_ready,
            self.active_instance_label.text(),
        )
        self.precheck_progress.setFormat("")
        self._set_scan_progress(100, "Environment scan complete.")
        self.precheck_status.setText(
            "Startup checks complete. " +
            ("A detected MO2 instance is ready for launch." if active_ready
             else "Some setup is still needed. Review the unchecked items above.")
        )

    def _update_active_instance_from_scan(self) -> None:
        """Derive launch readiness exclusively from current Steam/MO2 filesystem scans."""
        instances = self.instance_state.get("skyrim_instances", [])
        installation = self.instance_state.get("installation", {}) or {}
        discovered_executable = (
            installation.get("executable")
            if installation.get("installed") else None
        )
        launch_executable_verified = bool(
            self.mo2_state.get("installed") and self.mo2_state.get("executable")
            or discovered_executable
        )
        if not self.game_state.get("found"):
            detail = "Skyrim Special Edition has not been detected."
        elif not launch_executable_verified:
            if instances:
                names = ", ".join(item.get("name", "Unnamed instance") for item in instances)
                detail = (
                    f"Discovered {len(instances)} Skyrim MO2 instance(s): {names}. "
                    "The MO2 executable is not verified, so modded launch is unavailable."
                )
            else:
                detail = self.instance_state.get(
                    "message", "No MO2 instance or verified MO2 installation was found."
                )
        elif instances:
            names = ", ".join(item.get("name", "Unnamed instance") for item in instances)
            detail = f"Automatically discovered {len(instances)} Skyrim MO2 instance(s): {names}"
        else:
            detail = "MO2 is installed, but no Skyrim Special Edition instance was found. Create one in MO2, then refresh."
        proton = bool(self.proton_combo.currentData())
        mapping_ok = bool(
            self.mo2_discovery_state
            and self.mo2_discovery_state.get("ok")
        )
        busy = bool(self.worker and self.worker.isRunning())
        ready = bool(
            self.startup_checks_complete
            and self.game_state.get("found")
            and launch_executable_verified
            and instances
            and proton
            and mapping_ok
            and not busy
        )
        self.active_instance_label.setText(detail)
        self.launch_game_button.setEnabled(ready)
        self.launch_direct_button.setEnabled(bool(self.game_state.get("found") and not busy))

    def manage_mo2_library(self) -> None:
        """Refresh the read-only MO2 inventory widgets from the current scan."""
        saved_game = self.game_state.get("game_dir", "")
        prefix = _steam_prefix_for_game_path(saved_game) if saved_game else None
        install_path = self.mo2_state.get("install") or (
            str(Path(self.mo2_state["executable"]).parent)
            if self.mo2_state.get("executable") else
            str(prefix / "pfx/drive_c/Modding/MO2") if prefix else ""
        )
        context = dict(
            prefix_path=str(prefix) if prefix else "",
            install_path=install_path or "",
            game_path=saved_game or "",
            proton_path=self.proton_combo.currentData() or "",
        )
        self.mo2_library_widget.update_context(**context)
        self.mo2_advanced_widget.update_context(**context)
        self.mo2_advanced_widget._refresh_registered()

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

    def _save_audio_option(self, enabled: bool) -> None:
        """Persist the optional XAudio2 override preference."""
        self._saved_settings["xaudio2_compat"] = bool(enabled)
        proton = self.proton_combo.currentData()
        game = self.game_state.get("game_dir")
        if proton and game and not self._restoring_selection:
            try:
                write_settings(None, Path(proton), Path(game), xaudio2_compat=bool(enabled))
                self._log("XAudio2 compatibility override preference saved: " + ("enabled" if enabled else "disabled"))
            except OSError as error:
                self._log(f"Could not save XAudio2 compatibility preference: {error}")

    def _refresh_audio_status(self, report: dict | None = None) -> None:
        """Render a read-only audio library inventory."""
        if report is None:
            report = inspect_audio_compatibility()
        self.audio_report = report
        if not report.get("ok") or not report.get("safe"):
            self.audio_detail.setText("Audio inspection is fail-closed: " + report.get("message", "Wyrd prefix ownership could not be verified."))
            return
        components = report.get("components", {})
        labels = [
            ("xact", "XACT (32-bit)"), ("xact_x64", "XACT (64-bit)"),
            ("xaudio2", "XAudio2 (32-bit)"), ("xaudio2_x64", "XAudio2 (64-bit)"),
            ("x3daudio", "X3DAudio 1.7 (32-bit)"), ("x3daudio_x64", "X3DAudio 1.7 (64-bit)"),
            ("xapofx", "XAPOFX 1.5 (32-bit)"), ("xapofx_x64", "XAPOFX 1.5 (64-bit)"),
        ]
        lines = [
            "LEGACY Wyrd-prefix inventory — NOT the Skyrim Steam-prefix runtime.",
            f"Inspected prefix: {report.get('prefix', 'unknown')}",
        ]
        lines += [f"{label}: {components.get(key, {}).get('state', 'unknown')}" for key, label in labels]
        lines += [
            "FAudio / FFmpeg support: UNKNOWN",
            "Skyrim Steam-prefix audio: NOT INSPECTED",
            "Dialogue audio: NOT VERIFIED (test an NPC conversation in Skyrim)",
        ]
        self.audio_detail.setText("\n".join(lines))

    def check_audio_libraries(self) -> None:
        """Manually refresh the read-only inventory of audio libraries."""
        self.audio_check_button.setEnabled(False)
        try:
            report = inspect_audio_compatibility()
            self._refresh_audio_status(report)
            if report.get("ok") and report.get("safe"):
                summary = ", ".join(f"{name}={item.get('state', 'unknown')}" for name, item in report.get("components", {}).items())
                self._log("Audio library check completed (read-only): " + summary)
            else:
                self._log("Audio library check failed closed: " + report.get("message", "ownership could not be verified"))
        except Exception as error:
            self._log(f"Audio library check failed: {error}")
            self.audio_detail.setText(f"Audio inspection failed: {error}")
        finally:
            self.audio_check_button.setEnabled(True)

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

    def reset_managed_mo2(self) -> None:
        """Remove only Wyrd-owned MO2 files; never reset Steam's Skyrim prefix."""
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "MO2 is busy", "Wait for the current setup operation to finish before resetting MO2.")
            return
        state = (
            inspect_skyrim_prefix_mo2_installation(Path(self.game_state["game_dir"]))
            if self.game_state.get("found")
            else {"installed": False}
        )
        if not state.get("installed") or not state.get("managed", True):
            QMessageBox.information(
                self, "MO2 cannot be removed",
                "Wyrd could not verify a Wyrd-managed MO2 installation. External or unverified installations will not be removed.",
            )
            return
        install = state.get("install", "Wyrd-managed MO2 directory")
        answer = QMessageBox.warning(
            self, "Remove Wyrd-managed MO2?",
            "This removes only the verified Wyrd-installed MO2 program directory. Existing MO2 instances and their profiles, mods, downloads and custom storage are preserved.\n\n"
            "NEVER DELETED: Skyrim's Steam compatdata/489830 prefix, Skyrim/game files, saves, external MO2 installations, "
            "and custom storage outside the managed instance folders.\n\n"
            f"Installation: {install}\n\nThis cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = reset_wyrd_mo2(game_path=Path(self.game_state["game_dir"]))
        self._log(result.get("message", "MO2 removal attempted."))
        if result.get("ok"):
            self.progress_label.setText("Wyrd-managed MO2 removed.")
            QMessageBox.information(self, "MO2 removed", result["message"])
        else:
            QMessageBox.warning(self, "MO2 removal incomplete", result.get("message", "Reset failed safely."))
        self.refresh_all()
        self._update_active_instance_from_scan()

    def check_mo2_installation(self) -> None:
        """Show a read-only inventory of Skyrim prefix, MO2 files and instances."""
        lines = []
        game_value = self.game_state.get("game_dir") if self.game_state.get("found") else None
        if not game_value:
            lines.append("Skyrim game: NOT DETECTED")
            prefix = None
        else:
            game = Path(game_value).expanduser()
            lines.append(f"Skyrim game: {'OK' if game.is_dir() else 'MISSING'}")
            lines.append(f"Game path: {game}")
            try:
                state = inspect_skyrim_prefix_mo2_installation(game)
            except Exception as error:
                state = {"installed": False, "message": str(error)}
            prefix_value = state.get("prefix")
            prefix = Path(prefix_value).expanduser() if prefix_value else None
            if prefix is None:
                try:
                    from steamcompat import steam_compat_prefix
                    from wyrd_mo2 import _steam_root_for_game
                    prefix = steam_compat_prefix(_steam_root_for_game(game, Path.home()))
                except Exception as error:
                    lines.append(f"Steam prefix: UNKNOWN ({error})")
            if prefix is not None:
                pfx = prefix / "pfx"
                drive_c = pfx / "drive_c"
                lines.append(f"Skyrim compatdata prefix: {'EXISTS' if prefix.is_dir() else 'MISSING'}")
                lines.append(f"Prefix initialized (pfx/drive_c): {'YES' if drive_c.is_dir() else 'NO'}")
                lines.append(f"Prefix path: {prefix}")
                install = prefix / "pfx/drive_c/Modding/MO2"
                executable = install / "ModOrganizer.exe"
                marker = install / ".wyrd-managed.json"
                lines.append(f"MO2 install directory: {'EXISTS' if install.is_dir() else 'MISSING'}")
                lines.append(f"ModOrganizer.exe: {'EXISTS' if executable.is_file() and not executable.is_symlink() else 'MISSING'}")
                lines.append(f"Wyrd ownership marker: {'EXISTS' if marker.is_file() and not marker.is_symlink() else 'MISSING'}")
                try:
                    from steamcompat import inspect_any_skyrim_prefix_mo2_instances
                    inventory = inspect_any_skyrim_prefix_mo2_instances(game)
                    instances = inventory.get("instances", []) if inventory.get("ok") else []
                    skyrim_instances = inventory.get("skyrim_instances", []) if inventory.get("ok") else []
                    lines.append(f"MO2 instance folders found: {len(instances)}")
                    lines.append(f"Skyrim SE instances found: {len(skyrim_instances)}")
                    for item in instances:
                        lines.append(f"  • {item.get('name', 'Unnamed')} — {item.get('instance_path', 'path unknown')}")
                    if not inventory.get("ok"):
                        lines.append("Instance scan: " + inventory.get("message", "unavailable"))
                except Exception as error:
                    lines.append(f"MO2 instance scan: ERROR ({error})")

        QMessageBox.information(self, "MO2 installation check", "\n".join(lines))

    def launch_mo2_gui(self) -> None:
        """Open MO2's native instance picker in Skyrim's existing Proton prefix."""
        try:
            installation = self.instance_state.get("installation", {}) or {}
            executable_value = (
                self.mo2_state.get("executable")
                or installation.get("executable")
            )
            executable = Path(executable_value or "").expanduser()
            game_path = Path(self.game_state.get("game_dir", "")).expanduser()
            prefix = _steam_prefix_for_game_path(str(game_path)) if game_path else None
            proton_value = self.proton_combo.currentData()
            if not executable_value or executable.is_symlink() or not executable.is_file():
                raise RuntimeError("No verified ModOrganizer.exe was found. Instances can be inspected, but MO2 cannot be launched.")
            if installation.get("installed") and not self.mo2_state.get("installed"):
                try:
                    executable.resolve(strict=True).relative_to(
                        Path(installation.get("install", "")).resolve(strict=True)
                    )
                except (OSError, ValueError, RuntimeError):
                    raise RuntimeError("The discovered external MO2 executable is outside its verified installation directory.")
            if not game_path.is_dir() or game_path.is_symlink() or prefix is None:
                raise RuntimeError("Skyrim's existing Steam compatdata prefix could not be resolved.")
            if not proton_value:
                raise RuntimeError("Select a valid Proton runtime before launching MO2.")
            proton = Path(proton_value).expanduser()
            if proton.is_symlink() or not (proton / "proton").is_file():
                raise RuntimeError("The selected Proton runtime is no longer valid.")
            steam_root = _steam_root_for_game(game_path, Path.home())
            if not steam_root:
                raise RuntimeError("Could not identify the Steam library containing Skyrim.")
            env = _runtime_environment(proton, Path(steam_root), prefix)
            # MO2's own --pick option opens its native instance selector.
            # This deliberately avoids Wyrd registration and does not depend
            # on Wyrd's read-only inventory being able to classify an instance.
            process = subprocess.Popen(
                [str(proton / "proton"), "run", str(executable), "--pick"],
                cwd=str(executable.parent), env=env, stdin=None, stdout=None,
                stderr=None, start_new_session=True,
            )
            self._log(f"MO2 native instance-picker handoff started (PID {process.pid}).")
            self.progress_label.setText("Opening MO2 instance picker…")
        except Exception as error:
            self._log(f"MO2 launch failed: {error}")
            QMessageBox.warning(self, "Launch MO2", str(error))

    def install_mo2(self, *, confirm: bool = True) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Wyrd setup", "Skyrim Special Edition was not detected.")
            return
        proton_path = self.proton_combo.currentData()
        if not proton_path:
            QMessageBox.warning(self, "Wyrd setup", "No compatible Proton build is selected.")
            return
        game_path = Path(self.game_state["game_dir"])
        state = inspect_skyrim_prefix_mo2_installation(game_path)
        if not state.get("installed"):
            state = inspect_external_skyrim_prefix_mo2_installation(game_path)
        if state.get("installed"):
            self._log("MO2 installation already exists in Skyrim's Steam prefix; refreshing detection.")
            self.refresh_all()
            return
        occupied_target = Path(state["install"]).expanduser() if state.get("install") else None
        if occupied_target is not None and (occupied_target.exists() or occupied_target.is_symlink()):
            self._log(f"MO2 installation target is occupied; refusing to overwrite: {occupied_target}")
            QMessageBox.warning(
                self, "MO2 installation target is occupied",
                state.get("message") or (
                    f"The MO2 target already exists but is not a verified Wyrd installation:\n{occupied_target}\n\n"
                    "Wyrd will leave it untouched. Inspect or connect the existing installation instead."
                ),
            )
            self.refresh_all()
            return
        if confirm:
            answer = QMessageBox.question(
                self, "Install Mod Organizer 2",
                "Install official MO2 inside Skyrim's existing Steam compatdata prefix? "
                "The prefix must already be initialized by launching Skyrim through Steam. "
                "Wyrd will not reset the prefix or overwrite an unowned MO2 directory. "
                "Normal Proton/MO2 runtime files may be written there. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.worker = SteamPrefixInstallWorker(str(proton_path), self.game_state["game_dir"])
        self.worker.progress.connect(self._on_install_progress)
        self.worker.completed.connect(self._on_install_completed)
        self.install_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.proton_combo.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("Installing MO2 into Skyrim's Steam prefix…")
        self._log("Starting MO2 installation in Skyrim's Steam prefix.")
        self.worker.start()

    def _on_install_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.progress_label.setText(message)
        self._log(message)

    def _on_install_completed(self, result: dict) -> None:
        if result.get("ok"):
            # The worker result is not the final trust boundary: re-open the
            # installed target and validate both ModOrganizer.exe and the
            # Wyrd ownership marker before reporting success to the user.
            game_dir = self.game_state.get("game_dir")
            verified = (
                inspect_skyrim_prefix_mo2_installation(Path(game_dir))
                if game_dir else {"installed": False, "message": "Skyrim game path is unavailable."}
            )
            if not verified.get("installed"):
                result = {
                    "ok": False,
                    "message": "MO2 installer returned success, but post-install ownership/executable verification failed: "
                               + verified.get("message", "installation could not be verified."),
                }
        if result.get("ok"):
            self.progress.setValue(100)
            self.progress_label.setText("MO2 installed and verified.")
            self._log(result.get("message", "MO2 setup completed."))
            QMessageBox.information(
                self, "MO2 installed",
                result.get("message", "MO2 is ready.")
                + "\n\nContinue by creating or connecting an MO2 instance.",
            )
        else:
            self.progress_label.setText("Setup stopped. Review the log for details.")
            self._log("MO2 setup failed: " + result.get("message", "Unknown error"))
            QMessageBox.warning(self, "Wyrd setup failed", result.get("message", "MO2 setup failed."))
        self.refresh_button.setEnabled(True)
        self.refresh_all()

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
