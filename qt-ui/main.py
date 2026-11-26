#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import os
import sys
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, Signal, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QGridLayout, QInputDialog, QDialog,
    QWidget, QTabWidget, QScrollArea,
)

from skyrim_detection import detect_skyrim
from wyrd_mo2 import (
    discover_proton_builds,
    managed_paths, _steam_root_for_game, _runtime_environment,
    read_settings, write_settings, mark_first_run_completed, inspect_mo2_instances,
    inspect_skyrim_launch_targets,
    launch_process_status, detect_skyrim_processes, _proc_start_time,
    _proc_identity, _is_descendant_process, read_launch_diagnostics, classify_launch_diagnostics,
    inspect_audio_compatibility, reset_wyrd_settings,
)
from steamcompat import (
    install_mo2_into_skyrim_prefix, discover_wine_game_path, configure_skyrim_se_registry,
    synchronize_mo2_instance_game_paths, verify_wine_game_path,
    inspect_skyrim_prefix_mo2_installation,
    inspect_skyrim_prefix_mo2_instances, inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances, launch_skyrim_via_mo2_in_skyrim_prefix,
    steam_compat_prefix,
    reset_wyrd_mo2,
)
from mo2_library_dialog import Mo2LibraryDialog, Mo2AdvancedSettings
@dataclass
class LaunchSession:
    """Metadata for one MO2-mediated launch handoff and monitoring session."""

    wrapper_pid: int | None = None
    wrapper_start_time: int | None = None
    log_path: str | None = None
    instance_name: str | None = None
    executable_title: str | None = None
    started_at: float | None = None
    poll_count: int = 0
    detected_processes: list[dict] = field(default_factory=list)
    process_identities: dict[int, int | None] = field(default_factory=dict)
    game_processes: list[GameProcess] = field(default_factory=list)
    prefix_root: str | None = None
    game_path: str | None = None
    app_id: str | None = None
    target_binary: str | None = None
    launch_token: str | None = None
    state: str = "idle"


@dataclass
class GameProcess:
    """Stable identity for a detected Skyrim/SKSE process."""

    pid: int
    binary: str
    start_time: int | None
    detection_source: str
    attribution_confidence: str

    def matches_current_process(self) -> bool:
        """Return false if the PID vanished or now belongs to a different process."""
        identity = _proc_identity(self.pid)
        return bool(identity and (
            self.start_time is None or identity.get("start_time") == self.start_time
        ))


def _same_filesystem_path(left: str | os.PathLike | None,
                         right: str | os.PathLike | None) -> bool:
    """Compare discovered and persisted paths after normalizing aliases."""
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normpath(os.path.expanduser(str(left))) == os.path.normpath(os.path.expanduser(str(right)))


STYLE = """
/* Nordic Frost: SkyUI-inspired charcoal, slate and glacial-blue palette. */
QWidget { background: #11191f; color: #dce5e9; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 13px; }
QMainWindow, QScrollArea, QTabWidget::pane { background: #11191f; }
QLabel { background: transparent; border: none; }
QFrame#panel { background: #19252d; border: 1px solid #40515c; border-radius: 6px; }
QFrame#runtimePanel { background: #1b2a34; border: 1px solid #4b6270; border-radius: 6px; }
QFrame#statusPanel { background: #18252c; border: 1px solid #344b57; border-radius: 8px; }
QFrame#launchPanel { background: #1c2d36; border: 1px solid #587586; border-radius: 8px; }
QFrame#panel { border-radius: 8px; }
QFrame#runtimePanel { border-radius: 8px; }
QPushButton { border-radius: 6px; padding: 9px 14px; }
QPushButton#primaryAction { background: #527f93; border: 1px solid #8db8ca; border-radius: 7px; padding: 11px 18px; font-size: 14px; }
QPushButton#primaryAction:hover { background: #6394a9; }
QPushButton#secondaryAction { border-radius: 6px; }
QTabBar::tab { border-radius: 5px 5px 0 0; padding: 11px 20px; }

QPushButton { background: #263640; color: #dce5e9; border: 1px solid #526875; border-radius: 3px; padding: 9px 15px; min-height: 20px; }
QPushButton:hover { background: #334955; color: #f0f6f8; border-color: #8eafbf; }
QPushButton:pressed { background: #1a2931; }
QPushButton:disabled { color: #71818a; background: #1b252b; border-color: #34434b; }
QPushButton#primaryAction { background: #385666; color: #f0f6f8; border: 1px solid #82a9bc; font-weight: 600; }
QPushButton#primaryAction:hover { background: #466a7c; }
QPushButton#secondaryAction { background: #202f38; border-color: #536b78; }
QPushButton#infoButton { background: #1b2a33; color: #a9cbd9; border: 1px solid #63899c; border-radius: 12px; padding: 0; min-height: 22px; font-weight: 700; }
QPushButton#infoButton:hover { background: #385666; color: #f0f6f8; border-color: #a9cbd9; }
QLabel#heading { color: #eef4f6; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 28px; font-weight: 700; letter-spacing: 2px; padding: 2px 0; }
QLabel#section { color: #d5e4ea; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 14px; font-weight: 700; letter-spacing: .6px; }
QLabel#muted { color: #a5b3ba; }
QComboBox, QLineEdit { background: #17232b; color: #dce5e9; border: 1px solid #526875; border-radius: 3px; padding: 8px 10px; min-height: 21px; selection-background-color: #456779; }
QComboBox:hover, QLineEdit:focus { border-color: #91b6c8; }
QComboBox QAbstractItemView { background: #19252d; color: #dce5e9; selection-background-color: #385666; border: 1px solid #526875; }
QTabWidget::pane { background: #141f26; border: 1px solid #40515c; top: -1px; }
QTabBar::tab { background: #17232b; color: #9eafb8; border: 1px solid #344650; padding: 10px 22px; margin-right: 3px; }
QTabBar::tab:selected { background: #243640; color: #e5edf0; border-bottom: 2px solid #8fb4c6; }
QTabBar::tab:hover { color: #e5edf0; }
QProgressBar { background: #0d171d; border: 1px solid #63899c; border-radius: 6px; height: 24px; min-height: 24px; text-align: center; color: #f0f6f8; font-weight: 700; }
QProgressBar::chunk { background: #527f93; border-radius: 5px; }
QPlainTextEdit { background: #0d151a; color: #c7d4da; border: 1px solid #40515c; border-radius: 3px; padding: 7px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #11191f; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: #405763; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #64899b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QListWidget#mo2AdvancedCategories { background: #19252d; border: 1px solid #40515c; padding: 4px; outline: none; }
QListWidget#mo2AdvancedCategories::item { color: #a5b3ba; padding: 11px 10px; border-bottom: 1px solid #2b3a43; }
QListWidget#mo2AdvancedCategories::item:hover { color: #e5edf0; background: #243640; }
QListWidget#mo2AdvancedCategories::item:selected { color: #d9edf5; background: #2a414e; border-left: 3px solid #8fb4c6; }
QListWidget { background: #17232b; color: #dce5e9; border: 1px solid #40515c; outline: none; }
QListWidget::item { padding: 7px; border-bottom: 1px solid #293942; }
QListWidget::item:selected { background: #2a414e; color: #eef6f8; }
QCheckBox { spacing: 8px; color: #cbd7dc; }
QCheckBox::indicator { width: 15px; height: 15px; background: #17232b; border: 1px solid #617985; }
QCheckBox::indicator:checked { background: #63899c; border-color: #a9c8d6; }
QGroupBox { border: 1px solid #40515c; border-radius: 3px; margin-top: 12px; padding: 10px 8px 8px 8px; color: #a9c3d0; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
"""


def _steam_prefix_for_game_path(game_path: str | Path | None) -> Path | None:
    """Resolve AppID 489830 under the Steam client that owns this game library."""
    if not game_path:
        return None
    try:
        steam_root = _steam_root_for_game(Path(game_path), Path.home())
        return steam_compat_prefix(steam_root)
    except (OSError, RuntimeError, ValueError):
        return None


def add_info_button(layout, parent, title: str, explanation: str) -> QPushButton:
    """Add a compact hover-help control beside an advanced action."""
    info = QPushButton("i")
    info.setObjectName("infoButton")
    info.setFixedSize(24, 24)
    info.setToolTip(explanation)
    info.setToolTipDuration(15000)
    info.setAccessibleName(f"Information about {title}")
    layout.addWidget(info)
    return info


def panel(title: str, description: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(17, 15, 17, 15)
    heading = QLabel(title)
    heading.setObjectName("section")
    detail = QLabel(description)
    detail.setObjectName("muted")
    detail.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(detail)
    frame.detail_label = detail
    return frame


class SteamPrefixInstallWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(dict)

    def __init__(self, proton_path: str, game_path: str):
        super().__init__()
        self.proton_path = Path(proton_path)
        self.game_path = Path(game_path)

    def run(self) -> None:
        try:
            result = install_mo2_into_skyrim_prefix(
                self.proton_path, self.game_path,
                callback=lambda value, message: self.progress.emit(value, message),
            )
        except Exception as error:
            result = {"ok": False, "message": str(error)}
        self.completed.emit(result)


class MainWindow(QMainWindow):
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

    def start_skyrim_direct(self) -> None:
        """Launch Skyrim through Steam's own AppID path, independent of MO2 instances."""
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Skyrim unavailable", "Skyrim Special Edition was not detected.")
            return
        steam = shutil.which("steam")
        if not steam:
            QMessageBox.warning(self, "Steam unavailable", "The Steam command was not found on PATH.")
            return
        answer = QMessageBox.question(
            self, "Launch Skyrim through Steam",
            "This launches Skyrim Special Edition directly through Steam, without MO2 or mods. "
            "Use it for an unmodded audio baseline.\\n\\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            process = subprocess.Popen(
                [steam, "-applaunch", "489830"],
                stdin=None, stdout=None, stderr=None, start_new_session=True,
            )
        except OSError as error:
            QMessageBox.warning(self, "Steam launch failed", f"Could not start Steam: {error}")
            self._log(f"Direct Steam launch failed: {error}")
            return
        self._log(f"Direct Steam launch requested (AppID 489830), PID {process.pid}. This launch bypasses MO2.")
        self.progress_label.setText("Direct Steam launch requested; game startup is not yet confirmed.")
        QMessageBox.information(
            self, "Steam launch requested",
            "Steam was asked to launch Skyrim Special Edition directly. This is an unmodded launch and bypasses MO2.",
        )

    def start_active_game(self) -> None:
        """Launch the active Skyrim MO2 instance from the live filesystem scan."""
        self.start_skyrim()

    def _launch_registered_custom_executable(
        self, record: dict, installation: dict, game: dict, runtime: dict, target: dict
    ) -> dict:
        """Start an MO2 custom executable under its registered Proton/game context."""
        import json
        import subprocess
        from datetime import datetime, timezone
        from wyrd_mo2 import inspect_proton_build, _runtime_environment

        executable = Path(installation.get("executable", "")).expanduser()
        instance_path = Path(record.get("instance_path", "")).expanduser()
        config = Path(record.get("config", "")).expanduser()
        prefix = Path(record.get("prefix_path") or installation.get("prefix_path") or "").expanduser()
        game_path = Path(game.get("path", "")).expanduser()
        proton = Path(runtime.get("path", "")).expanduser()
        expected_binary = Path(target.get("resolved_binary", "")).expanduser()
        title = str(target.get("title", "")).strip()
        if not title or executable.is_symlink() or not executable.is_file():
            return {"ok": False, "message": "MO2 executable or configured title is invalid."}
        if any(path.is_symlink() for path in (instance_path, config, prefix, game_path, proton, expected_binary)):
            return {"ok": False, "message": "A registered launch path became a symlink; launch refused."}
        if not instance_path.is_dir() or not config.is_file() or not game_path.is_dir():
            return {"ok": False, "message": "MO2 instance, configuration, or game directory is missing."}
        if not (prefix / "pfx/drive_c/users").is_dir() or not (proton / "proton").is_file():
            return {"ok": False, "message": "Registered Proton prefix or runtime is unavailable."}
        validation = inspect_proton_build(proton)
        if not validation.get("ok"):
            return {"ok": False, "message": validation.get("message", "Proton runtime validation failed.")}
        if not expected_binary.is_file():
            return {"ok": False, "message": "Configured game executable disappeared after selection."}

        # Re-read the actual MO2 INI at launch time. The executable list shown
        # before the confirmation dialog is only a preview and may have changed.
        from wyrd_mo2 import inspect_mo2_instances, _resolve_windows_path
        inventory = inspect_mo2_instances(
            prefix, executable.parent, require_managed=False,
            detected_game_path=Path(self.game_state["game_dir"]),
        )
        if not inventory.get("ok"):
            return {"ok": False, "message": inventory.get("message", "Could not re-inspect the selected MO2 prefix.")}
        live_instance = next(
            (item for item in inventory.get("instances", [])
             if Path(item.get("path", "")).resolve() == instance_path.resolve()),
            None,
        )
        if live_instance is None or Path(live_instance.get("config", "")).resolve() != config.resolve():
            return {"ok": False, "message": "The registered MO2 instance or its configuration changed after selection."}
        live_targets = []
        for entry in live_instance.get("executables", []):
            if str(entry.get("title", "")).strip() != title:
                continue
            binary = _resolve_windows_path(str(entry.get("binary", "")), prefix, game_path)
            if binary and binary.is_file() and not binary.is_symlink():
                live_targets.append(binary.resolve())
        if len(live_targets) != 1 or live_targets[0] != expected_binary.resolve():
            return {"ok": False, "message": "The selected executable is no longer uniquely verified in the current MO2 configuration."}
        try:
            config.resolve(strict=True).relative_to(instance_path.resolve(strict=True))
            expected_binary.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": "Instance configuration or game executable no longer resolves safely."}

        steam_root_text = game.get("steam_root")
        if steam_root_text:
            steam_root = Path(steam_root_text).expanduser()
        else:
            candidates = (
                Path.home() / ".steam/steam",
                Path.home() / ".local/share/Steam",
                Path.home() / ".steam/root",
            )
            steam_root = next((path for path in candidates if path.is_dir()), Path(""))
        if not steam_root.is_dir():
            return {"ok": False, "message": "A Steam client install root is needed by this Proton runtime but was not found."}

        env = _runtime_environment(proton, steam_root, prefix)
        app_id = str(game.get("app_id") or "").strip()
        if app_id:
            env.update({"SteamAppId": app_id, "SteamGameId": app_id, "STEAM_COMPAT_APP_ID": app_id})
        else:
            for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                env.pop(key, None)
        env["STEAM_COMPAT_DATA_PATH"] = str(prefix.resolve())
        import uuid
        launch_token = uuid.uuid4().hex
        env["WYRD_LAUNCHED"] = "1"
        env["WYRD_LAUNCH_ID"] = launch_token
        command = [
            str(proton / "proton"), "run", str(executable),
            "--instance", instance_path.name, "run", "--executable", title,
        ]
        logs = managed_paths()["root"] / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{record.get('id', 'instance')}-game-launch.log"
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n=== Wyrd registered game launch ===\n")
                log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\n")
                log.write(f"game: {game.get('name')}\ninstance: {instance_path}\ntitle: {title}\n")
                log.write(f"binary: {expected_binary}\napp_id: {app_id or 'non-Steam'}\n")
                log.write("command: " + json.dumps(command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    command, cwd=str(executable.parent), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                log.write(f"wrapper_pid: {process.pid}\n")
                log.flush()
        except OSError as error:
            return {"ok": False, "message": f"Could not launch configured MO2 executable: {error}"}

        self.launch_session.wrapper_pid = process.pid
        self.launch_session.wrapper_start_time = _proc_start_time(process.pid)
        self.launch_session.log_path = str(log_path)
        self.launch_session.instance_name = instance_path.name
        self.launch_session.executable_title = title
        self.launch_session.started_at = time.time()
        self.launch_session.detected_processes = []
        self.launch_session.process_identities = {}
        self.launch_session.game_processes = []
        self.launch_session.prefix_root = str(prefix.resolve())
        self.launch_session.game_path = str(game_path.resolve())
        self.launch_session.app_id = app_id
        self.launch_session.target_binary = expected_binary.name
        self.launch_session.launch_token = launch_token
        self.launch_session.state = "handoff"
        self.launch_session.poll_count = 0
        if app_id == "489830" or not app_id:
            self._launch_monitor.start()
        else:
            self._launch_monitor.start()
        return {
            "ok": True, "pid": process.pid, "log_path": str(log_path),
            "message": f"Started '{title}' through MO2 instance '{instance_path.name}'.",
        }

    def start_skyrim(self) -> None:
        """Launch Skyrim from the live MO2 inventory; no Wyrd registration is required."""
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Skyrim unavailable", "Skyrim Special Edition was not detected.")
            return
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd could not verify MO2's dynamically derived Proton game path. "
                "The modded launch is blocked to avoid using a stale game location.\\n\\n"
                + discovery.get("message", "Unknown Steam discovery mapping error."),
            )
            return

        instances = self.instance_state.get("skyrim_instances", [])
        if not instances:
            QMessageBox.warning(
                self, "No Skyrim MO2 instance found",
                self.instance_state.get("message", "Create a Skyrim Special Edition instance in MO2, then refresh Wyrd."),
            )
            return
        if len(instances) == 1:
            instance_name = instances[0]["name"]
        else:
            instance_name, accepted = QInputDialog.getItem(
                self, "Select MO2 instance", "Skyrim Special Edition instance:",
                [item["name"] for item in instances], 0, False,
            )
            if not accepted or not instance_name:
                return

        game_path = Path(self.game_state["game_dir"]).expanduser()
        mo2_state = inspect_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            mo2_state = inspect_external_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            QMessageBox.warning(
                self, "MO2 unavailable",
                mo2_state.get("message", "MO2 installation could not be discovered."),
            )
            return
        proton_value = self.proton_combo.currentData()
        if not proton_value:
            QMessageBox.warning(self, "Proton unavailable", "Select a valid Proton runtime before launching.")
            return
        prefix = Path(mo2_state["prefix"])
        install_root = Path(mo2_state["install"])
        target_state = inspect_skyrim_launch_targets(
            prefix, game_path, instance_name=instance_name,
            install_root=install_root, require_managed=bool(mo2_state.get("managed", True)),
        )
        self._launch_skyrim_target_from_inventory(
            instance_name, target_state, game_path, prefix, install_root,
            Path(proton_value), bool(mo2_state.get("managed", True)),
            Path(mo2_state["steam_root"]) if mo2_state.get("steam_root") else None,
        )

    def _launch_registered_skyrim_target(
        self, record: dict, game: dict, installation: dict, runtime: dict,
        target_state: dict, game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None = None,
    ) -> None:
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd has not verified the dynamically derived Skyrim Proton game path. "
                "The modded launch is blocked until a successful refresh.\\n\\n"
                + (discovery or {}).get("message", "No verified Steam discovery result is available."),
            )
            return
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No configured Skyrim/SKSE executable was found."),
            )
            return
        instance_name = Path(record["instance_path"]).name
        self._launch_skyrim_target_from_inventory(
            instance_name, target_state, game_path, prefix, install_root, proton, False, steam_root,
        )

    def _run_launch_preflight(
        self, instance_name: str, target_state: dict, selected_target: dict,
        game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None,
    ) -> bool:
        """Show a visible, read-only checklist and require all checks before launch."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preparing modded Skyrim")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Launch pre-check")
        heading.setObjectName("section")
        layout.addWidget(heading)
        detail = QLabel("Wyrd is checking the selected environment before starting MO2.")
        detail.setWordWrap(True)
        detail.setObjectName("muted")
        layout.addWidget(detail)
        rows = []
        checks = [
            ("Skyrim installation", lambda: (
                not game_path.is_symlink() and game_path.is_dir(),
                f"Detected game folder: {game_path}",
            )),
            ("Skyrim Steam prefix", lambda: (
                not prefix.is_symlink()
                and prefix.is_dir()
                and (prefix / "pfx/drive_c/users").is_dir()
                and (steam_root is None or prefix.resolve(strict=True) == steam_compat_prefix(steam_root).resolve(strict=True)),
                f"AppID 489830 prefix: {prefix}",
            )),
            ("MO2 application", lambda: (
                not (install_root / "ModOrganizer.exe").is_symlink()
                and (install_root / "ModOrganizer.exe").is_file(),
                f"MO2 executable: {install_root / 'ModOrganizer.exe'}",
            )),
            ("Selected Proton runtime", lambda: (
                not proton.is_symlink()
                and proton.is_dir()
                and not (proton / "proton").is_symlink()
                and (proton / "proton").is_file()
                and os.access(proton / "proton", os.X_OK),
                f"Runtime: {proton}",
            )),
            ("Selected MO2 instance", lambda: self._preflight_instance_exists(
                prefix, install_root, instance_name
            )),
            ("Configured Skyrim executable", lambda: (
                bool(target_state.get("ok"))
                and any(item.get("title") == selected_target.get("title")
                        and item.get("binary") == selected_target.get("binary")
                        for item in target_state.get("targets", [])),
                f"MO2 target: {selected_target.get('title', 'Unknown')}",
            )),
        ]
        for label, _ in checks:
            checkbox = QCheckBox(label)
            checkbox.setEnabled(False)
            layout.addWidget(checkbox)
            rows.append(checkbox)
        progress = QProgressBar()
        progress.setRange(0, len(checks))
        progress.setValue(0)
        layout.addWidget(progress)
        status = QLabel("Starting checks…")
        status.setWordWrap(True)
        status.setObjectName("muted")
        layout.addWidget(status)
        buttons = QHBoxLayout()
        continue_button = QPushButton("Continue to launch")
        continue_button.setObjectName("primaryAction")
        continue_button.setEnabled(False)
        cancel_button = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)

        failed = False
        for index, (label, check) in enumerate(checks):
            detail.setText(f"Checking {label.lower()}…")
            QCoreApplication.processEvents()
            try:
                passed, message = check()
            except Exception as error:
                passed, message = False, str(error)
            rows[index].setChecked(bool(passed))
            rows[index].setText(f"{label} — {'OK' if passed else 'FAILED'}")
            progress.setValue(index + 1)
            status.setText(message)
            QCoreApplication.processEvents()
            if not passed:
                failed = True
                detail.setText("Pre-check failed. Wyrd will not launch Skyrim.")
                status.setText(f"{label}: {message}")
                break

        if failed:
            cancel_button.setText("Close")
            continue_button.hide()
            dialog.exec()
            return False
        detail.setText("All checks passed. Review the checklist, then continue.")
        status.setText(f"Ready to launch '{selected_target.get('title')}' through MO2 instance '{instance_name}'.")
        continue_button.setEnabled(True)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _preflight_instance_exists(prefix: Path, install_root: Path, instance_name: str) -> tuple[bool, str]:
        inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
        if not inventory.get("ok"):
            return False, inventory.get("message", "MO2 instance inventory is unavailable.")
        item = next((entry for entry in inventory.get("instances", [])
                     if entry.get("name") == instance_name), None)
        if not item:
            return False, f"MO2 instance '{instance_name}' was not found in the selected prefix."
        instance_path = Path(item.get("path", ""))
        config_value = item.get("config")
        config_path = Path(config_value) if config_value else instance_path / "ModOrganizer.ini"
        valid = (
            not instance_path.is_symlink() and instance_path.is_dir()
            and not config_path.is_symlink() and config_path.is_file()
        )
        return valid, f"Instance folder: {instance_path}; configuration: {config_path}"

    def _launch_skyrim_target_from_inventory(
        self, instance_name: str, target_state: dict, game_path: Path,
        prefix: Path, install_root: Path, proton: Path, require_managed: bool,
        steam_root: Path | None = None,
    ) -> None:
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            QMessageBox.warning(
                self, "MO2 game discovery is not verified",
                "Wyrd has not verified the dynamically derived Skyrim Proton game path. "
                "The modded launch is blocked until a successful refresh.\\n\\n"
                + (discovery or {}).get("message", "No verified Steam discovery result is available."),
            )
            return
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No verified Skyrim/SKSE executable is available in this instance."),
            )
            self._log(target_state.get("message", "No valid targets found."))
            return
        targets = target_state["targets"]
        title_counts = {}
        for target in targets:
            title_counts[target["title"]] = title_counts.get(target["title"], 0) + 1
        labels = [
            (f"{target['title']} — {Path(target['binary']).name}"
             if title_counts[target["title"]] > 1 else target["title"])
            for target in targets
        ]
        selected_label, accepted = QInputDialog.getItem(
            self, "Launch Skyrim through MO2", "Configured Skyrim executable:",
            labels, 0, False,
        )
        if not accepted or not selected_label:
            return
        selected_target = targets[labels.index(selected_label)]
        selected = selected_target["title"]
        if not self._run_launch_preflight(
            instance_name, target_state, selected_target,
            game_path, prefix, install_root, proton, steam_root,
        ):
            return
        answer = QMessageBox.question(
            self, "Confirm Skyrim launch",
            f"MO2 will launch '{selected}' in instance '{instance_name}' using its virtual filesystem. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        audio_mode = "xaudio2_compat" if self.xaudio_override_checkbox.isChecked() else "default"
        result = launch_skyrim_via_mo2_in_skyrim_prefix(
            proton, game_path, instance_name, selected,
            expected_binary=selected_target["binary"],
            prefix_path=prefix, install_root=install_root,
            steam_root_path=steam_root,
            xaudio2_compat=(audio_mode == "xaudio2_compat"),
        )
        self._log(result.get("message", "Skyrim launch handoff failed."))
        if result.get("ok"):
            self.launch_session.wrapper_pid = result.get("pid")
            self.launch_session.wrapper_start_time = _proc_start_time(self.launch_session.wrapper_pid) if self.launch_session.wrapper_pid else None
            self.launch_session.log_path = result.get("log_path")
            self.launch_session.instance_name = instance_name
            self.launch_session.executable_title = selected
            self.launch_session.started_at = time.time()
            self.launch_session.detected_processes = []
            self.launch_session.process_identities = {}
            self.launch_session.game_processes = []
            self.launch_session.prefix_root = str(prefix.resolve())
            self.launch_session.game_path = str(game_path.resolve())
            self.launch_session.app_id = "489830"
            self.launch_session.target_binary = Path(selected_target["binary"]).name
            self.launch_session.launch_token = result.get("launch_token")
            self.launch_session.state = "handoff"
            self.launch_session.poll_count = 0
            if self.launch_session.wrapper_pid:
                self._launch_monitor.start()
            self.launch_status_button.setEnabled(True)
            self.progress_label.setText("MO2 launch handoff started; game startup is not yet confirmed.")
            self._log("Launch diagnostics: " + result.get("log_path", "unavailable"))
            QMessageBox.information(self, "MO2 launch handoff started", result.get("message", ""))
        else:
            QMessageBox.warning(self, "Skyrim launch failed", result.get("message", "Could not launch Skyrim through MO2."))

    def _stop_launch_monitor(self, reason: str | None = None) -> None:
        if self._launch_monitor.isActive():
            self._launch_monitor.stop()
            if reason:
                self._log("Launch monitoring stopped: " + reason)

    def _poll_launch_status(self) -> None:
        """Refresh launch status without modal dialogs until a terminal state is reached."""
        if not self.launch_session.wrapper_pid:
            self._stop_launch_monitor("no launch session is active")
            return
        self.launch_session.poll_count += 1
        if self.launch_session.poll_count > self._launch_poll_limit:
            self.launch_session.state = "timeout"
            self._stop_launch_monitor("monitoring time limit reached")
            self.progress_label.setText(
                "Launch monitoring timed out; use Check launch status or inspect diagnostics."
            )
            return
        wrapper_identity = _proc_identity(self.launch_session.wrapper_pid)
        result = launch_process_status(
            self.launch_session.wrapper_pid, self.launch_session.wrapper_start_time
        )
        wrapper_identity_matches = (
            self.launch_session.wrapper_start_time is not None
            and wrapper_identity.get("start_time") == self.launch_session.wrapper_start_time
        )
        scan_prefix = (
            Path(self.launch_session.prefix_root)
            if self.launch_session.prefix_root else
            _steam_prefix_for_game_path(self.game_state.get("game_dir"))
        )
        if self.launch_session.app_id == "489830":
            process_scan = detect_skyrim_processes(
                scan_prefix, launch_token=self.launch_session.launch_token
            )
            session_processes = []
            seen_pids = set()
            if process_scan.get("ok") and wrapper_identity_matches:
                for item in process_scan.get("processes", []):
                    if _is_descendant_process(item["pid"], self.launch_session.wrapper_pid):
                        session_processes.append({**item, "_launch_source": "descendant"})
                        seen_pids.add(item["pid"])
            if process_scan.get("ok") and not session_processes:
                # Prefer confirmed process-tree ancestry. Use the unique
                # launch-token/prefix attribution only when ancestry is gone.
                for item in process_scan.get("wyrd_processes", []):
                    if item["pid"] not in seen_pids:
                        session_processes.append({**item, "_launch_source": "token-prefix"})
                        seen_pids.add(item["pid"])
        elif self.launch_session.target_binary:
            from wyrd_mo2 import detect_game_processes
            process_scan = detect_game_processes(
                self.launch_session.wrapper_pid, self.launch_session.target_binary,
                self.launch_session.prefix_root,
                launch_token=self.launch_session.launch_token,
                ancestor_start_time=self.launch_session.wrapper_start_time,
            )
            # The scanner independently verifies either the original wrapper
            # identity (for descendants) or this launch's unique environment token
            # and prefix (for reparented game processes).
            session_processes = process_scan.get("processes", []) if process_scan.get("ok") else []
        else:
            process_scan = {"ok": False, "processes": [], "message": "No configured target binary is available for process attribution."}
            session_processes = []
        if session_processes:
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})" for item in session_processes
            )
            skyrim_prefix_match = (
                self.launch_session.app_id == "489830"
                and any(item.get("_launch_source") == "token-prefix" for item in session_processes)
                and not any(item.get("_launch_source") == "descendant" for item in session_processes)
            )
            self.progress_label.setText(
                "Skyrim/SKSE detected in this Wyrd launch process tree."
                if self.launch_session.app_id == "489830" and not skyrim_prefix_match
                else "Skyrim/SKSE detected with this launch's unique Wyrd prefix token."
                if skyrim_prefix_match
                else "Configured game executable detected in this Wyrd launch process tree."
            )
            self.launch_session.detected_processes = list(session_processes)
            self.launch_session.process_identities = {
                item["pid"]: item.get("start_time") for item in session_processes
            }
            self.launch_session.game_processes = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source=item.get("_launch_source", "launch_process_tree"),
                    attribution_confidence="confirmed",
                ) for item in session_processes
            ]
            self.launch_session.state = "detected"
            self._log("Launch session detected: " + detected)
            if self.launch_session.app_id == "489830":
                self._log_audio_runtime_environment(session_processes)
            self._stop_launch_monitor(
                "Skyrim/SKSE process detected"
                if self.launch_session.app_id == "489830"
                else "registered game executable detected"
            )
            self._start_game_process_watch()
            return
        if result["state"] in {"exited", "invalid"}:
            self.progress_label.setText(
                "Proton wrapper exited before Skyrim was detected; review launch diagnostics."
                if self.launch_session.app_id == "489830"
                else "MO2 game wrapper exited before the configured executable was detected; review diagnostics."
            )
            self._log(
                ("Launch handoff ended without detected Skyrim: " if self.launch_session.app_id == "489830"
                 else "MO2 custom-executable handoff ended without a matching game process: ")
                + result["message"]
            )
            if self.launch_session.log_path:
                diagnostics = read_launch_diagnostics(Path(self.launch_session.log_path))
                if diagnostics.get("ok") and diagnostics.get("content", "").strip():
                    excerpt = diagnostics["content"][-2400:].strip()
                    self._log("Recent MO2/Proton diagnostics:\\n" + excerpt)
                    for finding in classify_launch_diagnostics(excerpt):
                        self._log("Launch diagnosis [" + finding["code"] + "]: " + finding["hint"])
                    self.progress_label.setText(
                        "Launch handoff exited. Recent diagnostics were added to the setup log."
                    )
                elif not diagnostics.get("ok"):
                    self._log("Launch diagnostics: " + diagnostics.get("message", "Unavailable."))
            self.launch_session.state = "failed"
            self._stop_launch_monitor("wrapper exited without detected game process")
            return
        if result["state"] == "unknown":
            self.progress_label.setText(
                "Launch status is unknown; automatic monitoring will continue briefly."
            )
            self.launch_session.state = "unknown"
            self._log("Launch status unknown: " + result["message"])
        else:
            self.launch_session.state = "waiting"
            self.progress_label.setText(
                (
                    f"Waiting for Skyrim/SKSE… (check {self.launch_session.poll_count}/{self._launch_poll_limit})"
                    if self.launch_session.app_id == "489830"
                    else f"Waiting for {self.launch_session.target_binary or 'game process'}… "
                         f"(check {self.launch_session.poll_count}/{self._launch_poll_limit})"
                )
            )

    def _start_game_process_watch(self) -> None:
        """Keep watching detected game PIDs after the Proton wrapper has exited."""
        if not self.launch_session.game_processes:
            return
        self._game_watch_timer.start()
        self._log("Post-detection process watch started for " + ", ".join(
            f"{process.binary} (PID {process.pid})"
            for process in self.launch_session.game_processes
        ))

    def _revalidate_detected_game_processes(self) -> None:
        """Revalidate saved process identities and report when the game exits."""
        processes = self.launch_session.game_processes
        if not processes:
            self._game_watch_timer.stop()
            return
        active = [process for process in processes if process.matches_current_process()]
        if active:
            self.launch_session.game_processes = active
            return
        self._game_watch_timer.stop()
        self.launch_session.game_processes = []
        self.launch_session.state = "game-exited"
        is_skyrim = self.launch_session.app_id == "489830"
        self.progress_label.setText(
            "Skyrim/SKSE processes exited; launch session ended."
            if is_skyrim else "Registered game process exited; launch session ended."
        )
        self._log(
            "Detected Skyrim/SKSE process identities are no longer present. Game exit recorded."
            if is_skyrim else "Detected registered game process identities are no longer present. Game exit recorded."
        )

    def check_launch_status(self) -> None:
        """Report the current tracked game processes, even after Proton's wrapper exits."""
        session = self.launch_session
        game_dir = session.game_path or self.game_state.get("game_dir")
        steam_prefix = (
            Path(session.prefix_root) if session.prefix_root
            else _steam_prefix_for_game_path(game_dir)
        )
        if session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None):
            process_scan = detect_skyrim_processes(
                steam_prefix, launch_token=session.launch_token
            )
            self._log("Skyrim process scan: " + process_scan.get("message", "Detection unavailable."))
            for item in process_scan.get("processes", []):
                self._log(
                    f"Skyrim/SKSE candidate PID {item.get('pid')} ({item.get('binary')}): "
                    f"prefix attribution={item.get('attributed_to_wyrd')}; "
                    f"start_time={item.get('start_time')}"
                )
                mappings = item.get("audio_module_mappings", {})
                if item.get("audio_module_mappings_readable"):
                    observed = [name for name, path in mappings.items() if path]
                    self._log(
                        f"Audio DLL mappings PID {item.get('pid')}: "
                        + (", ".join(observed) if observed else "none observed")
                    )
                else:
                    self._log(
                        f"Audio DLL mappings PID {item.get('pid')}: unavailable "
                        f"({item.get('audio_module_mappings_error') or 'access denied'})"
                    )
                env = item.get("audio_runtime_environment", {})
                self._log(
                    f"Audio runtime environment PID {item.get('pid')}: "
                    f"WINEDLLOVERRIDES={env.get('WINEDLLOVERRIDES')!r}; "
                    f"PULSE_LATENCY_MSEC={env.get('PULSE_LATENCY_MSEC')!r}"
                )
        elif session.wrapper_pid and session.target_binary:
            from wyrd_mo2 import detect_game_processes
            process_scan = detect_game_processes(
                session.wrapper_pid, session.target_binary, session.prefix_root,
                launch_token=session.launch_token,
                ancestor_start_time=session.wrapper_start_time,
            )
            self._log("Registered game process scan: " + process_scan.get("message", "Detection unavailable."))
        else:
            process_scan = {"ok": False, "processes": [], "message": "No generic target is available for process attribution."}
            self._log(process_scan["message"])

        # First revalidate processes already attributed to this launch. This avoids
        # losing a still-running game when Proton's short-lived wrapper has exited.
        tracked = [process for process in session.game_processes
                   if process.matches_current_process()]
        if tracked:
            session.game_processes = tracked
            session.state = (
                "detected" if any(p.attribution_confidence == "confirmed" for p in tracked)
                else "detected-unconfirmed"
            )
            self._start_game_process_watch()
            detected = "\\n".join(
                f"{p.binary} (PID {p.pid}) — {p.attribution_confidence}"
                for p in tracked
            )
            is_skyrim = session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None)
            self.progress_label.setText(
                "Skyrim/SKSE is running; tracked process identity verified."
                if is_skyrim else "Registered game process is running; tracked identity verified."
            )
            detail = (
                ("Tracked Skyrim/SKSE processes are still present:\\n" if is_skyrim
                 else "Tracked registered game processes are still present:\\n") + detected
            )
            QMessageBox.information(self, "Launch process status", detail)
            return

        if session.game_processes:
            session.game_processes = []
            session.state = "game-exited"
            self._game_watch_timer.stop()
            is_skyrim = session.app_id == "489830" or not session.app_id
            self.progress_label.setText(
                "Previously detected Skyrim/SKSE processes have exited."
                if is_skyrim else "Previously detected registered game processes have exited."
            )
            self._log(
                "Tracked Skyrim/SKSE process identities are no longer present."
                if is_skyrim else "Tracked registered game process identities are no longer present."
            )

        if not session.wrapper_pid:
            detail = (
                "No Wyrd launch handoff has been recorded in this session. "
                "That is expected when Skyrim is launched directly from MO2.\\n\\n"
                + process_scan.get("message", "Process detection unavailable.")
                + "\\n\\nThis check detects currently running Skyrim/SKSE processes; "
                "it cannot confirm a game that has already exited."
            )
            QMessageBox.information(self, "Launch status", detail)
            return

        wrapper_identity = _proc_identity(session.wrapper_pid)
        wrapper_matches = (
            session.wrapper_start_time is not None
            and wrapper_identity.get("start_time") == session.wrapper_start_time
        )
        result = launch_process_status(session.wrapper_pid, session.wrapper_start_time)
        self._log(
            f"Launch PID {session.wrapper_pid}: {result['state']} — {result['message']}"
        )

        session_processes = []
        if process_scan.get("ok"):
            if session.app_id == "489830" or (session.wrapper_pid is None and session.app_id is None):
                if wrapper_matches and session.wrapper_pid:
                    for item in process_scan.get("processes", []):
                        if _is_descendant_process(item["pid"], session.wrapper_pid):
                            session_processes.append({**item, "_launch_source": "descendant"})
            elif session.wrapper_pid and session.target_binary:
                # Generic scanner has already validated the exact launch token,
                # registered prefix, and original wrapper identity.
                session_processes = list(process_scan.get("processes", []))

        if session_processes:
            tracked = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="launch_process_tree",
                    attribution_confidence="confirmed",
                )
                for item in session_processes
            ]
            session.game_processes = tracked
            session.detected_processes = list(session_processes)
            session.process_identities = {
                item["pid"]: item.get("start_time") for item in session_processes
            }
            session.state = "detected"
            self._start_game_process_watch()
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})" for item in session_processes
            )
            self.progress_label.setText("Skyrim/SKSE detected in this Wyrd launch process tree.")
            detail = result["message"] + "\\n\\nProcesses descended from this launch:\\n" + detected
        elif process_scan.get("ok") and process_scan.get("wyrd_processes"):
            items = process_scan["wyrd_processes"]
            tracked = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="managed_prefix",
                    attribution_confidence="prefix",
                )
                for item in items
            ]
            session.game_processes = tracked
            session.detected_processes = list(items)
            session.process_identities = {
                item["pid"]: item.get("start_time") for item in items
            }
            session.state = "detected-unconfirmed"
            self._start_game_process_watch()
            detected = ", ".join(f"{item['binary']} (PID {item['pid']})" for item in items)
            self.progress_label.setText(
                "Skyrim detected in Wyrd's prefix; launch ancestry is unconfirmed."
            )
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "") + "\\n" + detected
        elif process_scan.get("ok") and process_scan.get("processes"):
            self.progress_label.setText("Skyrim detected, but Wyrd ownership could not be confirmed.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        elif session.state == "game-exited":
            detail = "The previously detected game process has exited.\\n\\n" + result["message"]
        elif result["state"] == "exited":
            self.progress_label.setText("Proton wrapper exited. Skyrim process not detected; review diagnostics.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        elif result["state"] == "running":
            self.progress_label.setText("Proton wrapper is running; Skyrim process not detected yet.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        else:
            self.progress_label.setText("Launch status is unknown; inspect diagnostics.")
            detail = result["message"] + "\\n\\n" + process_scan.get("message", "")
        QMessageBox.information(self, "Launch process status", detail)


    def show_launch_diagnostics(self) -> None:
        path = managed_paths()["root"] / "logs" / "skyrim-launch.log"
        result = read_launch_diagnostics(path)
        if not result.get("ok"):
            QMessageBox.information(self, "Launch diagnostics", result.get("message", "No diagnostics available."))
            return
        self._log("Launch diagnostics opened: " + result["path"])
        QMessageBox.information(self, "Launch diagnostics", result["content"][-5000:])

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
