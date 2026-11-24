#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import sys
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QInputDialog,
    QWidget, QTabWidget, QScrollArea,
)

from skyrim_detection import detect_skyrim
from wyrd_mo2 import (
    discover_proton_builds, inspect_wyrd_installation,
    install_mo2_environment, launch_mo2, managed_paths,
    read_settings, write_settings, inspect_mo2_instances,
    inspect_skyrim_launch_targets, launch_skyrim_via_mo2,
    launch_process_status, detect_skyrim_processes, _proc_start_time,
    _proc_identity, _is_descendant_process, read_launch_diagnostics, classify_launch_diagnostics,
    inspect_audio_compatibility, reset_mo2_compatdata,
)
from steamcompat import (
    install_mo2_into_skyrim_prefix, inspect_skyrim_prefix_mo2_installation,
    inspect_skyrim_prefix_mo2_instances, inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances, launch_external_mo2_in_skyrim_prefix,
    launch_mo2_in_skyrim_prefix, steam_compat_prefix, copy_wyrd_instance_to_skyrim_prefix,
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


STYLE = """
QWidget { background: #17191b; color: #e7dfc8; font-size: 14px; }
QMainWindow, QScrollArea, QTabWidget::pane { background: #17191b; }
QLabel { background: transparent; border: none; }
QFrame#panel { background: #202326; border: 1px solid #665b43; border-radius: 5px; }
QLabel#heading { color: #e9dfc3; font-family: Georgia, serif; font-size: 31px; font-weight: 600; letter-spacing: 2px; padding: 3px 0; }
QLabel#section { color: #d9c28b; font-family: Georgia, serif; font-size: 17px; font-weight: 600; }
QLabel#muted { color: #aaa18d; }
QPushButton { background: #292d30; color: #e7dfc8; border: 1px solid #756746; border-radius: 3px; padding: 10px 16px; min-height: 20px; }
QPushButton:hover { background: #39434a; color: #fff3cf; border-color: #c2a76b; }
QPushButton:pressed { background: #202629; }
QPushButton:disabled { color: #716d62; background: #242627; border-color: #454239; }
QComboBox { background: #24282a; color: #e7dfc8; border: 1px solid #756746; border-radius: 3px; padding: 8px 10px; min-height: 20px; }
QComboBox QAbstractItemView { background: #202326; color: #e7dfc8; selection-background-color: #43515a; }
QTabWidget::pane { border: 1px solid #665b43; top: -1px; }
QTabBar::tab { background: #202326; color: #aaa18d; border: 1px solid #514a3a; padding: 10px 22px; margin-right: 4px; }
QTabBar::tab:selected { background: #343a3b; color: #e9dfc3; border-bottom: 2px solid #c2a76b; }
QTabBar::tab:hover { color: #fff3cf; }
QProgressBar { background: #24282a; border: 1px solid #665b43; border-radius: 3px; height: 13px; text-align: center; color: #e7dfc8; }
QProgressBar::chunk { background: #8c7546; }
QPlainTextEdit { background: #111416; color: #cfc6ad; border: 1px solid #514a3a; border-radius: 3px; padding: 6px; }
QScrollArea { border: none; background: transparent; }
QListWidget#mo2AdvancedCategories { background: #202326; border: 1px solid #514a3a; padding: 4px; outline: none; }
QListWidget#mo2AdvancedCategories::item { color: #aaa18d; padding: 12px 10px; border-bottom: 1px solid #35383a; }
QListWidget#mo2AdvancedCategories::item:hover { color: #f0e4c5; background: #292d30; }
QListWidget#mo2AdvancedCategories::item:selected { color: #f0d89b; background: #343a3b; border-left: 3px solid #c2a76b; }
QGroupBox { border: 1px solid #514a3a; border-radius: 3px; margin-top: 12px; padding: 10px 8px 8px 8px; color: #d9c28b; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
"""


def _steam_prefix_for_game_path(game_path: str | Path | None) -> Path | None:
    """Resolve the Steam root only when the game path has the expected library depth."""
    if not game_path:
        return None
    path = Path(game_path)
    if len(path.parents) <= 2:
        return None
    return steam_compat_prefix(path.parents[2])


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


class InstallWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(dict)

    def __init__(self, proton_path: str, game_path: str):
        super().__init__()
        self.proton_path = Path(proton_path)
        self.game_path = Path(game_path)

    def run(self) -> None:
        try:
            result = install_mo2_environment(
                self.proton_path, self.game_path,
                callback=lambda value, message: self.progress.emit(value, message),
            )
        except Exception as error:
            result = {"ok": False, "message": str(error)}
        self.completed.emit(result)


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
        self.resize(1040, 780)
        self.setMinimumSize(820, 640)
        self.worker: QThread | None = None
        self.game_state: dict = {"found": False}
        self.proton_builds: list[dict] = []
        self.mo2_state: dict = {"installed": False}
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
        self.selected_mo2_instance_id = self._saved_settings.get("selected_mo2_instance_id", "")
        self._restoring_selection = True

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        heading = QLabel("WYRD")
        heading.setObjectName("heading")
        subtitle = QLabel("THE ELDER SCROLLS • SKYRIM SPECIAL EDITION")
        subtitle.setObjectName("muted")
        outer.addWidget(heading)
        outer.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs, 1)

        setup_page = QWidget()
        setup_layout = QVBoxLayout(setup_page)
        setup_layout.setContentsMargins(12, 16, 12, 12)
        setup_layout.setSpacing(12)

        self.game_panel = panel("SKYRIM SPECIAL EDITION", "Detecting Steam libraries…")
        setup_layout.addWidget(self.game_panel)

        runtime_panel = QFrame()
        runtime_panel.setObjectName("panel")
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_layout.setContentsMargins(17, 15, 17, 15)
        runtime_heading = QLabel("PROTON RUNTIME")
        runtime_heading.setObjectName("section")
        runtime_detail = QLabel("Choose the compatibility runtime used to launch MO2 and Skyrim.")
        runtime_detail.setObjectName("muted")
        runtime_detail.setWordWrap(True)
        runtime_layout.addWidget(runtime_heading)
        runtime_layout.addWidget(runtime_detail)
        runtime_row = QHBoxLayout()
        self.proton_combo = QComboBox()
        self.proton_combo.setMinimumWidth(320)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_all)
        self.proton_combo.currentIndexChanged.connect(self._save_selection)
        runtime_row.addWidget(self.proton_combo, 1)
        runtime_row.addWidget(self.refresh_button)
        runtime_layout.addLayout(runtime_row)
        self.runtime_status = QLabel("Looking for Proton…")
        self.runtime_status.setObjectName("muted")
        runtime_layout.addWidget(self.runtime_status)
        setup_layout.addWidget(runtime_panel)

        self.mo2_panel = panel("MOD ORGANIZER 2", "Checking Wyrd's managed installation…")
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
            selected_instance_id=self.selected_mo2_instance_id,
        )
        self.mo2_library_widget.on_registry_changed = self._on_mo2_registry_changed
        self.mo2_library_widget.on_launch_requested = self._launch_embedded_mo2_instance
        setup_layout.addWidget(self.mo2_library_widget)
        self.mo2_advanced_widget = Mo2AdvancedSettings(
            self, selected_instance_id=self.selected_mo2_instance_id
        )
        self.mo2_advanced_widget.on_registry_changed = self._on_mo2_registry_changed

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.install_button = QPushButton("Install MO2")
        self.install_button.clicked.connect(self.install_mo2)
        self.launch_button = QPushButton("Launch MO2")
        self.launch_button.clicked.connect(self.start_mo2)
        self.launch_button.setEnabled(False)
        self.launch_game_button = QPushButton("Launch Modded Skyrim")
        self.launch_game_button.clicked.connect(self.start_active_game)
        self.launch_game_button.setEnabled(False)
        self.launch_direct_button = QPushButton("Launch Unmodded")
        self.launch_direct_button.clicked.connect(self.start_skyrim_direct)
        self.launch_direct_button.setEnabled(False)
        self.diagnostics_button = QPushButton("View launch log")
        self.diagnostics_button.clicked.connect(self.show_launch_diagnostics)
        self.launch_status_button = QPushButton("Check launch status")
        self.launch_status_button.clicked.connect(self.check_launch_status)
        self.active_instance_label = QLabel("Active instance: none selected")
        self.active_instance_label.setObjectName("muted")
        setup_layout.addWidget(self.active_instance_label)

        primary_actions = QHBoxLayout()
        primary_actions.addWidget(self.install_button)
        primary_actions.addWidget(self.launch_button)
        primary_actions.addStretch(1)
        setup_layout.addLayout(primary_actions)

        game_actions = QHBoxLayout()
        game_actions.addWidget(self.launch_game_button)
        game_actions.addWidget(self.launch_direct_button)
        game_actions.addStretch(1)
        setup_layout.addLayout(game_actions)


        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("muted")
        setup_layout.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        setup_layout.addWidget(self.progress)
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
            "Change MO2 paths, game or Proton links, and storage options here. "
            "Most people can leave these settings as they are."
        )
        mo2_advanced_help.setObjectName("muted")
        mo2_advanced_help.setWordWrap(True)
        advanced_layout.addWidget(mo2_advanced_help)
        advanced_layout.addWidget(self.mo2_advanced_widget)

        maintenance_panel = panel(
            "MO2 MAINTENANCE",
            "Rarely needed. Reset is available only for a verified Wyrd-managed installation; external MO2 is never reset.",
        )
        maintenance_row = QHBoxLayout()
        self.reset_mo2_button = QPushButton("Reset Wyrd-managed MO2")
        self.reset_mo2_button.setToolTip(
            "Resets only a verified Wyrd-managed installation. External MO2 installations are never reset."
        )
        self.reset_mo2_button.clicked.connect(self.reset_managed_mo2)
        maintenance_row.addWidget(self.reset_mo2_button)
        maintenance_row.addStretch(1)
        maintenance_panel.layout().addLayout(maintenance_row)
        advanced_layout.addWidget(maintenance_panel)

        self.audio_panel = panel(
            "AUDIO COMPATIBILITY",
            "Advanced diagnostics only. A library inventory cannot prove in-game audio; verify dialogue and music in Skyrim.",
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
        self.xaudio_override_checkbox = QCheckBox("Enable XAudio2 compatibility override")
        self.xaudio_override_checkbox.setChecked(bool(self._saved_settings.get("xaudio2_compat", False)))
        self.xaudio_override_checkbox.toggled.connect(self._save_audio_option)
        audio_actions.addWidget(self.xaudio_override_checkbox)
        audio_actions.addStretch(1)
        audio_layout.addLayout(audio_actions)
        advanced_layout.addWidget(self.audio_panel)

        diagnostics_panel = panel(
            "LAUNCH DIAGNOSTICS",
            "Check whether Skyrim or SKSE is running and open the detailed launch log when troubleshooting."
        )
        diagnostic_row = QHBoxLayout()
        diagnostic_row.addWidget(self.diagnostics_button)
        diagnostic_row.addWidget(self.launch_status_button)
        diagnostic_row.addStretch(1)
        diagnostics_panel.layout().addLayout(diagnostic_row)
        advanced_layout.addWidget(diagnostics_panel)

        logs_heading = QLabel("SETUP LOG")
        logs_heading.setObjectName("section")
        advanced_layout.addWidget(logs_heading)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(400)
        self.log_view.setPlaceholderText("Detection and installation progress will appear here.")
        self.log_view.setMinimumHeight(220)
        advanced_layout.addWidget(self.log_view)
        advanced_layout.addStretch(1)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        advanced_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        advanced_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        advanced_scroll.setWidget(advanced_page)
        self.tabs.addTab(advanced_scroll, "ADVANCED")

        self.setCentralWidget(root)
        self._restore_registered_instance()
        self.refresh_all()
        self._restoring_selection = False
        self._restore_saved_selection()
        self._save_selection(self.proton_combo.currentIndex())

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

    def refresh_all(self) -> None:
        selected = self.proton_combo.currentData()
        self.game_state = detect_skyrim()
        saved_game = self._saved_settings.get("game_path")
        if saved_game and Path(saved_game).is_dir():
            detected_game = self.game_state.get("game_dir")
            if not self.game_state.get("found") or detected_game != saved_game:
                self.game_state = {
                    **self.game_state, "found": True,
                    "game_dir": saved_game, "library": "saved Wyrd selection",
                }
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

        self.proton_builds = discover_proton_builds()
        self.proton_combo.clear()
        selected_index = -1
        for build in self.proton_builds:
            label = f'{build["name"]} ({build["architecture"]})'
            self.proton_combo.addItem(label, build["path"])
            self.proton_combo.setItemData(self.proton_combo.count() - 1, build["path"], 3)
            if selected and selected == build["path"]:
                selected_index = self.proton_combo.count() - 1
        saved_proton = self._saved_settings.get("proton")
        preferred_proton = selected or saved_proton
        if preferred_proton:
            selected_index = next(
                (index for index, build in enumerate(self.proton_builds)
                 if str(Path(build["path"]).expanduser()) == str(Path(preferred_proton).expanduser())),
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
        self.mo2_target_occupied = bool(
            self.mo2_state.get("installed") or self.mo2_state.get("install")
        )
        self.reset_mo2_button.setEnabled(bool(
            self.mo2_state.get("installed") and self.mo2_state.get("managed", True)
        ))
        if self.mo2_state.get("installed"):
            self.instance_state = inspect_any_skyrim_prefix_mo2_instances(
                Path(self.game_state["game_dir"])
            )
        else:
            self.instance_state = {
                "ok": False, "instances": [], "skyrim_instances": [],
                "message": self.mo2_state.get("message", "MO2 is not installed.")
            }
        skyrim_instances = self.instance_state.get("skyrim_instances", [])
        if self.mo2_state.get("installed"):
            summary = self.instance_state.get("message", "MO2 instance inventory unavailable.")
            if skyrim_instances:
                summary += "\nSkyrim SE instance(s): " + ", ".join(x["name"] for x in skyrim_instances)
                summary += "\nSelected MO2 profile: " + " | ".join(
                    f'{x["name"]}: {x.get("selected_profile") or "No profile selected"}'
                    for x in skyrim_instances
                )
            elif self.instance_state.get("ok"):
                summary += "\nCreate a Skyrim Special Edition instance in MO2 to continue."
            owner = "Wyrd-managed" if self.mo2_state.get("managed", True) else "External (read-only)"
            self.mo2_panel.detail_label.setText(
                f"{owner} MO2 is available in Skyrim's Steam prefix.\n"
                f'Executable: {self.mo2_state.get("executable")}\n'
                f'Steam compatdata: {self.mo2_state.get("prefix")}\n{summary}'
            )
            self.install_button.setText("MO2 Ready" if self.mo2_state.get("managed", True) else "Use Existing MO2")
            self.install_button.setEnabled(False)
            self.launch_button.setEnabled(True)
            self.launch_game_button.setEnabled(bool(skyrim_instances))
            self._log("Skyrim-prefix MO2 inspection: " + owner + " installation.")
        else:
            self.mo2_panel.detail_label.setText(self.mo2_state.get("message", "MO2 is not installed."))
            self.install_button.setText("Install MO2")
            self.install_button.setEnabled(bool(self.game_state.get("found") and self.proton_builds))
            self.launch_button.setEnabled(False)
            self.launch_game_button.setEnabled(False)
            self.launch_direct_button.setEnabled(bool(self.game_state.get("found")))

        self._refresh_audio_status()
        ready = bool(self.game_state.get("found") and self.proton_builds)
        self.install_button.setEnabled(
            ready
            and not (self.worker and self.worker.isRunning())
            and not getattr(self, "mo2_target_occupied", self.mo2_state.get("installed"))
        )
        self.proton_combo.setEnabled(bool(self.proton_builds) and not (self.worker and self.worker.isRunning()))
        # Keep both MO2 panels synchronized with detected Skyrim and the
        # selected Proton runtime. The default prefix is Skyrim's existing
        # Steam compatdata directory; this does not create or reset a prefix.
        self.manage_mo2_library()
        # Registered instances own their own game/runtime selections and can
        # launch even when Skyrim detection is empty.
        self._restore_registered_instance()

    def _restore_registered_instance(self) -> dict | None:
        """Resolve the saved active instance and refresh the application's registry snapshot."""
        from wyrd_mo2_registry_service import RegistryService
        try:
            registry = RegistryService(managed_paths()["config"] / "mo2-registry.json").load()
            self.registered_instance_registry = registry
        except Exception as error:
            self._log(f"Could not load MO2 registry: {error}")
            self.registered_instance_registry = None
            return None
        record = next((item for item in registry["instances"]
                       if item.get("id") == self.selected_mo2_instance_id), None)
        if not record:
            self.active_instance_label.setText(
                f"Active MO2 instance: none selected · {len(registry['instances'])} registered"
            )
            if hasattr(self, "launch_game_button"):
                legacy_ready = bool(self.game_state.get("found") and self.instance_state.get("skyrim_instances"))
                busy = bool(self.worker and self.worker.isRunning())
                self.launch_game_button.setEnabled(legacy_ready and not busy)
            return None
        games = {item["id"]: item for item in registry["games"]}
        installations = {item["id"]: item for item in registry["installations"]}
        runtimes = {item["id"]: item for item in registry["runtimes"]}
        game = games.get(record.get("game_id"), {})
        install = installations.get(record.get("installation_id"), {})
        runtime = runtimes.get(record.get("runtime_id"), {})
        prefix_value = record.get("prefix_path") or game.get("prefix_path") or install.get("prefix_path")
        paths = {
            "executable": Path(install.get("executable", "")).expanduser(),
            "instance": Path(record.get("instance_path", "")).expanduser(),
            "config": Path(record.get("config", "")).expanduser(),
            "game": Path(game.get("path", "")).expanduser(),
            "runtime": Path(runtime.get("path", "")).expanduser(),
            "prefix": Path(prefix_value).expanduser() if prefix_value else None,
        }
        ready = bool(
            install.get("owner") in ("wyrd", "external")
            and paths["executable"].is_file()
            and paths["instance"].is_dir()
            and paths["config"].is_file()
            and paths["game"].is_dir()
            and paths["runtime"].is_dir()
            and (paths["runtime"] / "proton").is_file()
            and paths["prefix"] is not None
            and paths["prefix"].is_dir()
            and (paths["prefix"] / "pfx/drive_c/users").is_dir()
            and all(not path.is_symlink() for path in paths.values() if path is not None)
        )
        label = record.get("display_name") or paths["instance"].name
        self.active_instance_label.setText(
            f"Active MO2 instance: {label} · {game.get('name', 'unknown game')} · "
            f"{runtime.get('name', 'no runtime')} · "
            + ("Ready" if ready else "Association/path unavailable — open MO2 setup")
        )
        if hasattr(self, "launch_game_button"):
            busy = bool(self.worker and self.worker.isRunning())
            self.launch_game_button.setEnabled(ready and not busy)
        return record

    def _set_active_instance(self, record: dict | None) -> None:
        # Passing None explicitly clears a primary selection that was unregistered.
        self.selected_mo2_instance_id = record.get("id", "") if record else ""
        settings = dict(self._saved_settings)
        settings["selected_mo2_instance_id"] = self.selected_mo2_instance_id
        self._saved_settings = settings
        # The restore helper owns launch-button readiness; do not enable an incomplete association.
        self._restore_registered_instance()
        # Preserve the normal Proton/game/audio preferences while saving the
        # additional selection key using the same atomic settings-file pattern.
        import json, os, tempfile
        path = managed_paths()["settings"]
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        payload["selected_mo2_instance_id"] = self.selected_mo2_instance_id
        fd, temp_name = tempfile.mkstemp(prefix=".wyrd-settings-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def manage_mo2_library(self) -> None:
        """Refresh context for the embedded Environment and Advanced widgets."""
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
        self.mo2_library_widget._refresh_connected()

    def _on_mo2_registry_changed(self, record: dict | None, changed: bool = False) -> None:
        if record:
            self._set_active_instance(record)
        elif changed:
            self._set_active_instance(None)
        self.mo2_advanced_widget.preferred_instance_id = self.selected_mo2_instance_id
        self.mo2_advanced_widget._refresh_registered()
        self.mo2_library_widget.preferred_instance_id = self.selected_mo2_instance_id
        self.mo2_library_widget._refresh_connected()
        self._restore_registered_instance()

    def _launch_embedded_mo2_instance(self, record: dict) -> None:
        result = self._launch_registered_instance(record)
        self._log(result.get("message", "MO2 launch did not start."))
        if result.get("ok"):
            self.progress_label.setText("Selected MO2 instance started.")
        else:
            QMessageBox.warning(self, "MO2 launch refused", result.get("message", "Could not launch selected instance."))

    def _restore_saved_selection(self) -> None:
        """Restore the last selected Proton when it is still discoverable."""
        saved = self._saved_settings.get("proton")
        if not saved:
            return
        for index in range(self.proton_combo.count()):
            if self.proton_combo.itemData(index) == saved:
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
        if proton and game:
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

    def reset_mo2_prefix(self) -> None:
        """Reset only Wyrd-owned MO2 compatdata after two explicit confirmations."""
        prefix = managed_paths()["prefix"]
        if not prefix.exists() or prefix.is_symlink():
            QMessageBox.warning(self, "Reset MO2 compatdata", "The managed MO2 prefix is absent or unsafe to reset.")
            return
        first = QMessageBox.warning(
            self, "Nuke MO2 Compatdata",
            "This permanently deletes Wyrd's MO2 Proton compatdata (prefix), including its Wine settings and installed Windows components.\n\n"
            "It does NOT delete Wyrd's MO2 installation files, Skyrim, Steam compatdata, mods stored outside the prefix, or your game files. "
            "MO2 may need to be configured again after reset.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        second = QMessageBox.question(
            self, "Final confirmation — delete MO2 prefix",
            f"Delete this Wyrd-managed prefix now?\n\n{prefix}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if second != QMessageBox.StandardButton.Yes:
            return
        result = reset_mo2_compatdata(confirmed=True)
        self._log(result.get("message", "MO2 compatdata reset attempted."))
        if result.get("ok"):
            self.progress_label.setText("MO2 compatdata removed. Use Repair MO2 runtime to recreate it.")
            QMessageBox.information(self, "MO2 compatdata reset", result["message"])
        else:
            QMessageBox.warning(self, "MO2 compatdata reset failed", result.get("message", "Reset failed."))
        self.refresh_all()


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
                self, "Nothing to reset",
                "Wyrd could not verify a Wyrd-managed MO2 installation. External or unverified installations will not be removed.",
            )
            return
        install = state.get("install", "Wyrd-managed MO2 directory")
        answer = QMessageBox.warning(
            self, "Reset Wyrd-managed MO2?",
            "This permanently removes Wyrd's verified MO2 installation and registered global instances. "
            "Instance-local modding data is removed with those instances. Custom storage outside the instance folders is preserved.\n\n"
            "It will also clear Wyrd's registry entries and active instance selection for this installation.\n\n"
            "NEVER DELETED: Skyrim's Steam compatdata/489830 prefix, Skyrim/game files, saves, external MO2 installations, "
            "and custom storage outside the managed instance folders.\n\n"
            f"Installation: {install}\n\nThis cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = reset_wyrd_mo2(game_path=Path(self.game_state["game_dir"]))
        self._log(result.get("message", "MO2 reset attempted."))
        if result.get("ok"):
            self.selected_mo2_instance_id = ""
            self._saved_settings.pop("selected_mo2_instance_id", None)
            self.progress_label.setText("Wyrd-managed MO2 reset complete.")
            QMessageBox.information(self, "MO2 reset complete", result["message"])
        else:
            QMessageBox.warning(self, "MO2 reset incomplete", result.get("message", "Reset failed safely."))
        self.refresh_all()
        self._restore_registered_instance()

    def install_mo2(self) -> None:
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
        if state.get("installed") or state.get("install"):
            self.refresh_all()
            return
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
        self.launch_button.setEnabled(False)
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
                self, "Wyrd setup complete",
                result.get("message", "MO2 is ready.") +
                "\n\nPress Launch MO2. On first launch, create a Skyrim Special Edition instance in MO2."
            )
        else:
            self.progress_label.setText("Setup stopped. Review the log for details.")
            self._log("MO2 setup failed: " + result.get("message", "Unknown error"))
            QMessageBox.warning(self, "Wyrd setup failed", result.get("message", "MO2 setup failed."))
        self.refresh_button.setEnabled(True)
        self.refresh_all()

    def _instance_inspection(self) -> dict:
        if not self.game_state.get("found"):
            return {"ok": False, "instances": [], "skyrim_instances": [],
                    "message": "Skyrim Special Edition was not detected."}
        return inspect_skyrim_prefix_mo2_instances(Path(self.game_state["game_dir"]))

    def _launch_registered_instance(self, record: dict) -> dict:
        """Open the registered MO2 instance with its exact install and Proton association."""
        import subprocess
        from wyrd_mo2 import inspect_proton_build, _runtime_environment
        from wyrd_mo2_registry_service import RegistryService

        registry = RegistryService(managed_paths()["config"] / "mo2-registry.json").load()
        installations = {item["id"]: item for item in registry["installations"]}
        games = {item["id"]: item for item in registry["games"]}
        runtimes = {item["id"]: item for item in registry["runtimes"]}
        # Resolve the canonical current registry record; never trust a stale dialog copy.
        record = next((item for item in registry["instances"] if item.get("id") == record.get("id")), None)
        if record is None:
            return {"ok": False, "message": "The selected MO2 instance is no longer registered."}
        installation = installations.get(record.get("installation_id"))
        game = games.get(record.get("game_id"))
        runtime = runtimes.get(record.get("runtime_id"))
        if not installation or not game or not runtime:
            return {"ok": False, "message": "This instance needs an installation, game and Proton runtime association."}

        executable = Path(installation.get("executable", "")).expanduser()
        instance_path = Path(record.get("instance_path", "")).expanduser()
        config = Path(record.get("config", "")).expanduser()
        game_path = Path(game.get("path", "")).expanduser()
        proton = Path(runtime.get("path", "")).expanduser()
        prefix_value = record.get("prefix_path") or game.get("prefix_path") or installation.get("prefix_path")
        if not prefix_value:
            return {"ok": False, "message": "This instance has no Proton prefix association. Edit its game/runtime association first."}
        prefix = Path(prefix_value).expanduser()

        if installation.get("owner") not in ("wyrd", "external"):
            return {"ok": False, "message": "Installation ownership is not classified; launch refused."}
        checked_paths = (executable, instance_path, config, game_path, proton, prefix)
        if any(path.is_symlink() for path in checked_paths):
            return {"ok": False, "message": "A registered launch path is symlinked; launch refused."}
        if not executable.is_file() or not instance_path.is_dir() or not config.is_file() or not game_path.is_dir():
            return {"ok": False, "message": "Registered MO2 executable, instance, INI or game directory is missing."}
        if record.get("instance_type") == "portable" and instance_path.resolve() != executable.parent.resolve():
            return {"ok": False, "message": "Portable MO2 instance must be the registered installation root."}
        if not (prefix / "pfx/drive_c/users").is_dir():
            return {"ok": False, "message": "The associated Proton prefix is not initialized."}
        validation = inspect_proton_build(proton)
        if not validation.get("ok"):
            return {"ok": False, "message": validation.get("message", "The associated Proton runtime is unavailable.")}

        try:
            executable.resolve(strict=True).relative_to(executable.parent.resolve(strict=True))
            config.resolve(strict=True).relative_to(instance_path.resolve(strict=True))
            import configparser
            parser = configparser.ConfigParser(interpolation=None)
            with config.open("r", encoding="utf-8-sig") as stream:
                parser.read_file(stream)
            general = parser["General"] if parser.has_section("General") else {}
            configured_name = general.get("gameName", "").strip()
            if configured_name and game.get("name"):
                expected = str(game["name"]).casefold()
                actual = configured_name.casefold()
                # Keep the game-name association explicit, but tolerate common
                # display-name variants and edition suffixes.
                if expected not in actual and actual not in expected:
                    return {"ok": False, "message": f"MO2 reports game '{configured_name}', but Wyrd associates this instance with '{game['name']}'."}
            live_inventory = __import__("wyrd_mo2").inspect_mo2_instances(
                prefix, executable.parent, require_managed=False
            )
            if not live_inventory.get("ok"):
                return {"ok": False, "message": live_inventory.get("message", "Could not re-inspect the registered Proton prefix.")}
            live = next(
                (item for item in live_inventory.get("instances", [])
                 if Path(item.get("path", "")).resolve() == instance_path.resolve()),
                None,
            )
            if not live or Path(live.get("config", "")).resolve() != config.resolve():
                return {"ok": False, "message": "The registered MO2 instance is no longer present in the selected prefix."}
            if record.get("instance_type") == "portable" and instance_path.resolve() != executable.parent.resolve():
                return {"ok": False, "message": "Portable instance root does not match the registered installation."}
        except (OSError, ValueError, RuntimeError) as error:
            return {"ok": False, "message": f"Registered instance validation failed safely: {error}"}

        steam_root_value = game.get("steam_root")
        if steam_root_value:
            steam_root = Path(steam_root_value).expanduser()
        else:
            candidates = (
                Path.home() / ".steam/steam",
                Path.home() / ".local/share/Steam",
                Path.home() / ".steam/root",
            )
            steam_root = next((candidate for candidate in candidates if candidate.is_dir()), Path(""))
        if not steam_root.is_dir():
            return {"ok": False, "message": "Steam client root required by Proton could not be found."}

        env = _runtime_environment(proton, steam_root, prefix)
        app_id = str(game.get("app_id") or "").strip()
        if app_id:
            env.update({"SteamAppId": app_id, "SteamGameId": app_id, "STEAM_COMPAT_APP_ID": app_id})
        else:
            for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                env.pop(key, None)
        env["STEAM_COMPAT_DATA_PATH"] = str(prefix.resolve())
        if self.xaudio_override_checkbox.isChecked() and app_id == "489830":
            current = env.get("WINEDLLOVERRIDES", "")
            overrides = [item for item in current.split(";") if item and not item.lower().startswith("xaudio2_7=")]
            overrides.append("xaudio2_7=n,b")
            env["WINEDLLOVERRIDES"] = ";".join(overrides)

        # MO2 expects --instance to identify a global instance by its registered
        # instance name. Portable instances launch from their own install root.
        command = [str(proton / "proton"), "run", str(executable)]
        if record.get("instance_type") == "portable":
            if instance_path.resolve() != executable.parent.resolve():
                return {"ok": False, "message": "Portable instance root does not match its MO2 installation root."}
            # MO2 selects a portable instance when the explicit instance name
            # is empty. Keep the empty value in the same argv token: Proton's
            # launcher can discard a standalone empty argv element, causing MO2
            # to fall back to GlobalSettings.currentInstance() instead.
            # Boost.Program_options accepts --instance= as an explicit empty
            # value, which survives Proton's shell wrapper.
            command.append("--instance=")
            cwd = str(instance_path)
        else:
            command.extend(["--instance", instance_path.name])
            cwd = str(executable.parent)
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, start_new_session=True,
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            return {"ok": False, "message": f"Could not launch registered MO2 instance: {error}"}
        return {
            "ok": True, "pid": process.pid, "instance": instance_path.name,
            "game": game.get("name"), "runtime": runtime.get("name"),
            "installation_owner": installation.get("owner"),
            "message": f"Opened MO2 instance '{record.get('display_name') or instance_path.name}' for {game.get('name')} using {runtime.get('name')}.",
        }


    def start_mo2(self) -> None:
        game_path = Path(self.game_state.get("game_dir", ""))
        proton_path = self.proton_combo.currentData()
        if not game_path.is_dir() or not proton_path:
            QMessageBox.warning(self, "Launch MO2", "Skyrim or the selected Proton build is unavailable.")
            return
        inventory = inspect_any_skyrim_prefix_mo2_instances(game_path)
        if not inventory.get("ok"):
            QMessageBox.warning(self, "MO2 instance status unavailable", inventory.get("message", "Could not inspect Skyrim's Steam prefix."))
            return
        candidates = inventory.get("skyrim_instances", [])
        instance_name = None
        if candidates:
            instance_name, accepted = QInputDialog.getItem(
                self, "Select MO2 instance", "Skyrim Special Edition instance:",
                [x["name"] for x in candidates], 0, False,
            )
            if not accepted or not instance_name:
                return
        if inventory.get("managed", True):
            result = launch_mo2_in_skyrim_prefix(
                Path(proton_path), game_path, instance_name=instance_name,
                xaudio2_compat=self.xaudio_override_checkbox.isChecked(),
            )
        else:
            result = launch_external_mo2_in_skyrim_prefix(
                Path(proton_path), game_path, instance_name=instance_name,
                xaudio2_compat=self.xaudio_override_checkbox.isChecked(),
            )
        self._log(result.get("message", "MO2 launch failed."))
        if result.get("ok"):
            self.progress_label.setText("MO2 wrapper started in Skyrim's Steam prefix.")
            QMessageBox.information(self, "Launch MO2", result["message"])
        else:
            QMessageBox.warning(self, "Launch MO2", result.get("message", "Could not launch MO2."))

    def start_mo2_in_skyrim_prefix(self) -> None:
        """Launch MO2 in Skyrim's Steam compatdata prefix (experimental)."""
        answer = QMessageBox.warning(
            self, "Experimental: MO2 in Skyrim Prefix",
            "This launches Wyrd's MO2 installation using Skyrim's existing Steam Proton compatdata prefix. "
            "It does not reset or intentionally modify that prefix, but MO2/Proton may write normal runtime "
            "configuration files into it. This is separate from Standalone MO2.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        inventory = inspect_skyrim_prefix_mo2_instances()
        if not inventory.get("ok"):
            self._log(inventory.get("message", "Could not inspect Skyrim-prefix MO2 instances."))
            QMessageBox.warning(self, "Steam-prefix MO2", inventory.get("message", "Could not inspect MO2 instances."))
            return
        candidates = inventory.get("skyrim_instances", [])
        if not candidates:
            message = inventory.get("message", "No Skyrim MO2 instance exists in Skyrim's Proton prefix.")
            self._log(message)
            QMessageBox.warning(self, "Steam-prefix MO2", message)
            return
        names = [item["name"] for item in candidates]
        instance_name, accepted = QInputDialog.getItem(
            self, "MO2 instance in Skyrim prefix", "Select the Skyrim MO2 instance:",
            names, 0, False,
        )
        if not accepted or not instance_name:
            return
        result = launch_mo2_in_skyrim_prefix(instance_name=instance_name)
        self._log(result.get("message", "Steam-prefix MO2 launch failed."))
        if result.get("ok"):
            self.progress_label.setText("MO2 wrapper started in Skyrim's Steam prefix.")
            QMessageBox.information(self, "Steam-prefix MO2", result["message"])
        else:
            QMessageBox.warning(self, "Steam-prefix MO2 launch failed", result.get("message", "Could not launch MO2."))


    def copy_instance_to_skyrim_prefix(self) -> None:
        """Offer a non-destructive copy of an existing Wyrd Skyrim instance."""
        state = inspect_wyrd_installation()
        if not state.get("installed") or not state.get("prefix_ready"):
            QMessageBox.warning(self, "MO2 instance copy", "Wyrd's managed MO2 prefix is not ready.")
            return
        source = inspect_mo2_instances(
            Path(state["paths"]["prefix"]), Path(state["executable"]).parent
        )
        if not source.get("ok"):
            QMessageBox.warning(self, "MO2 instance copy", source.get("message", "Could not inspect Wyrd MO2."))
            return
        candidates = source.get("skyrim_instances", [])
        if not candidates:
            QMessageBox.information(self, "MO2 instance copy", "No Skyrim Special Edition instance was found in Wyrd's MO2 prefix.")
            return
        names = [item["name"] for item in candidates]
        name, accepted = QInputDialog.getItem(
            self, "Copy Skyrim MO2 instance", "Choose the original Wyrd instance to copy:",
            names, 0, False,
        )
        if not accepted or not name:
            return
        item = next(candidate for candidate in candidates if candidate["name"] == name)
        storage = item.get("storage", {})
        explanation = (
            f"Original instance: {item['path']}\n"
            f"Profile: {item.get('selected_profile_candidate') or 'not selected'}\n"
            f"Profiles: {storage.get('profiles', {}).get('path', 'unknown')}\n"
            f"Mods: {storage.get('mods', {}).get('path', 'unknown')}\n"
            f"Downloads: {storage.get('downloads', {}).get('path', 'unknown')}\n\n"
            "Wyrd will copy the instance into Skyrim's Proton prefix. The source remains untouched. "
            "For safety, copies with external storage roots or symlinks are refused. Continue?"
        )
        answer = QMessageBox.warning(
            self, "Confirm non-destructive instance copy", explanation,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = copy_wyrd_instance_to_skyrim_prefix(name)
        self._log(result.get("message", "MO2 instance copy failed."))
        if result.get("ok"):
            self.progress_label.setText("MO2 instance copied; original Wyrd instance preserved.")
            QMessageBox.information(self, "MO2 instance copy", result["message"])
        else:
            QMessageBox.warning(self, "MO2 instance copy refused", result.get("message", "Copy failed safely."))

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
        """Launch the configured executable for the persistent registered instance."""
        from wyrd_mo2_registry_service import RegistryService
        from wyrd_mo2 import inspect_mo2_instances, _resolve_windows_path

        record = self._restore_registered_instance()
        if not record:
            if self.game_state.get("found") and self.instance_state.get("skyrim_instances"):
                self.start_skyrim()
                return
            QMessageBox.warning(self, "No MO2 instance selected", "Open MO2 setup and choose an instance first.")
            return
        registry = RegistryService(managed_paths()["config"] / "mo2-registry.json").load()
        installations = {item["id"]: item for item in registry["installations"]}
        games = {item["id"]: item for item in registry["games"]}
        runtimes = {item["id"]: item for item in registry["runtimes"]}
        game = games.get(record.get("game_id"), {})
        if str(game.get("app_id") or "") == "489830":
            self.start_skyrim()
            return
        installation = installations.get(record.get("installation_id"), {})
        runtime = runtimes.get(record.get("runtime_id"), {})
        executable = Path(installation.get("executable", "")).expanduser()
        instance_path = Path(record.get("instance_path", "")).expanduser()
        config = Path(record.get("config", "")).expanduser()
        prefix = Path(record.get("prefix_path") or installation.get("prefix_path") or "").expanduser()
        game_path = Path(game.get("path", "")).expanduser()
        proton = Path(runtime.get("path", "")).expanduser()
        if installation.get("owner") not in ("wyrd", "external"):
            QMessageBox.warning(self, "MO2 launch unavailable", "Installation ownership is not verified.")
            return
        for path, label in (
            (executable, "MO2 executable"), (instance_path, "MO2 instance"),
            (config, "instance configuration"), (prefix, "Proton prefix"),
            (game_path, "game directory"), (proton, "Proton runtime"),
        ):
            if path.is_symlink() or not path.exists():
                QMessageBox.warning(self, "MO2 launch unavailable", f"Registered {label} is missing or unsafe: {path}")
                return
        if not executable.is_file() or not config.is_file() or not instance_path.is_dir() or not game_path.is_dir() or not proton.is_dir():
            QMessageBox.warning(self, "MO2 launch unavailable", "A registered path does not have the expected file/directory type.")
            return
        if not (prefix / "pfx/drive_c/users").is_dir() or not (proton / "proton").is_file():
            QMessageBox.warning(self, "MO2 launch unavailable", "The registered Proton prefix or Proton launcher is unavailable.")
            return

        inventory = inspect_mo2_instances(prefix, executable.parent, require_managed=False)
        if not inventory.get("ok"):
            QMessageBox.warning(self, "MO2 launch unavailable", inventory.get("message", "Could not inspect registered MO2 prefix."))
            return
        expected_instance = instance_path.resolve(strict=True)
        discovered = next(
            (item for item in inventory.get("instances", [])
             if Path(item.get("path", "")).resolve() == expected_instance),
            None,
        )
        if not discovered:
            QMessageBox.warning(self, "MO2 launch unavailable", "The registered instance is no longer present in its associated prefix.")
            return
        configured_game = str(discovered.get("game_name", "")).strip()
        registered_game = str(game.get("name", "")).strip()
        if configured_game and registered_game:
            configured_folded = configured_game.casefold()
            registered_folded = registered_game.casefold()
            if configured_folded not in registered_folded and registered_folded not in configured_folded:
                QMessageBox.warning(
                    self, "Game association mismatch",
                    f"The MO2 instance reports '{configured_game}', but Wyrd associates it with "
                    f"'{registered_game}'. Change the game in MO2 setup before launching."
                )
                return

        targets = []
        for entry in discovered.get("executables", []):
            title = str(entry.get("title", "")).strip()
            binary = _resolve_windows_path(str(entry.get("binary", "")), prefix, game_path)
            if not title or binary is None or not binary.is_file() or binary.is_symlink():
                continue
            targets.append({**entry, "title": title, "resolved_binary": str(binary)})
        if not targets:
            QMessageBox.warning(
                self, "No configured executable",
                "This MO2 instance has no custom executable whose target can be safely resolved. "
                "Add the game executable in MO2's Executables settings, then refresh the library."
            )
            return

        labels = [
            f"{item['title']} — {Path(item['resolved_binary']).name}"
            for item in targets
        ]
        selected_label, accepted = QInputDialog.getItem(
            self, "Launch active game", "Configured MO2 executable:", labels, 0, False
        )
        if not accepted or not selected_label:
            return
        target = targets[labels.index(selected_label)]
        answer = QMessageBox.question(
            self, "Confirm game launch",
            f"Launch '{target['title']}' through MO2 instance '{instance_path.name}' "
            f"for {game.get('name', 'the registered game')}? Wyrd will not edit the MO2 profile or executable settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self._launch_registered_custom_executable(
            record, installation, game, runtime, target
        )
        self._log(result.get("message", "Game launch handoff did not start."))
        if not result.get("ok"):
            QMessageBox.warning(self, "Game launch failed", result.get("message", "Could not start the configured executable."))
            return
        self.progress_label.setText("MO2 game launch handoff started; process detection is pending.")
        self.launch_status_button.setEnabled(True)
        QMessageBox.information(self, "Game launch handoff", result.get("message", ""))

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
        inventory = inspect_mo2_instances(prefix, executable.parent, require_managed=False)
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
        """Launch the active registered Skyrim instance, falling back to legacy discovery."""
        registered = self._restore_registered_instance()
        selected_record = None
        if registered:
            from wyrd_mo2_registry_service import RegistryService
            registry = RegistryService(managed_paths()["config"] / "mo2-registry.json").load()
            games = {item["id"]: item for item in registry["games"]}
            installations = {item["id"]: item for item in registry["installations"]}
            runtimes = {item["id"]: item for item in registry["runtimes"]}
            game = games.get(registered.get("game_id"), {})
            if str(game.get("app_id") or "") == "489830":
                selected_record = registered

        if selected_record:
            registry = RegistryService(managed_paths()["config"] / "mo2-registry.json").load()
            games = {item["id"]: item for item in registry["games"]}
            installations = {item["id"]: item for item in registry["installations"]}
            runtimes = {item["id"]: item for item in registry["runtimes"]}
            game = games[selected_record["game_id"]]
            installation = installations[selected_record["installation_id"]]
            runtime = runtimes.get(selected_record.get("runtime_id"), {})
            game_path = Path(game.get("path", "")).expanduser()
            prefix = Path(selected_record.get("prefix_path") or game.get("prefix_path") or "").expanduser()
            install_root = Path(installation.get("executable", "")).expanduser().parent
            proton = Path(runtime.get("path", "")).expanduser()
            instance_path = Path(selected_record.get("instance_path", "")).expanduser()
            if installation.get("owner") not in ("wyrd", "external"):
                QMessageBox.warning(self, "MO2 launch unavailable", "Registered installation ownership is not verified.")
                return
            for path, label in ((game_path, "game"), (prefix, "Proton prefix"),
                                (install_root, "MO2 installation"), (proton, "Proton runtime"),
                                (instance_path, "MO2 instance")):
                if not str(path) or path.is_symlink() or not path.is_dir():
                    QMessageBox.warning(self, "MO2 launch unavailable", f"Registered {label} path is unavailable or unsafe: {path}")
                    return
            if not (install_root / "ModOrganizer.exe").is_file() or not (proton / "proton").is_file():
                QMessageBox.warning(self, "MO2 launch unavailable", "Registered MO2 executable or Proton launcher is missing.")
                return
            inventory = inspect_mo2_instances(
                prefix, install_root, require_managed=(installation.get("owner") == "wyrd" and
                    prefix.name != "489830")
            )
            if not inventory.get("ok"):
                # Steam AppID prefixes are not Wyrd-owned; inspect them explicitly.
                inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
            if not inventory.get("ok"):
                QMessageBox.warning(self, "MO2 launch unavailable", inventory.get("message", "Could not inspect registered MO2 prefix."))
                return
            instance_name = instance_path.name
            found = next((item for item in inventory.get("instances", [])
                          if Path(item.get("path", "")).resolve() == instance_path.resolve()), None)
            if not found:
                QMessageBox.warning(self, "MO2 launch unavailable", "Registered instance no longer exists in the associated prefix.")
                return
            target_state = inspect_skyrim_launch_targets(
                prefix, game_path, instance_name=instance_name, install_root=install_root,
                require_managed=False,
            )
            self._launch_registered_skyrim_target(
                selected_record, game, installation, runtime, target_state,
                game_path, prefix, install_root, proton,
                Path(game["steam_root"]) if game.get("steam_root") else None,
            )
            return

        # Legacy path remains unchanged for installations not yet in the registry.
        instances = self.instance_state.get("skyrim_instances", [])
        if not instances:
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                self.instance_state.get("message", "No Skyrim Special Edition MO2 instance is available."),
            )
            return
        instance_names = [item["name"] for item in instances]
        instance_name, accepted = QInputDialog.getItem(
            self, "Select MO2 instance", "Skyrim Special Edition instance:",
            instance_names, 0, False,
        )
        if not accepted or not instance_name:
            return
        game_path = Path(self.game_state["game_dir"])
        mo2_state = inspect_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            mo2_state = inspect_external_skyrim_prefix_mo2_installation(game_path)
        if not mo2_state.get("installed"):
            QMessageBox.warning(self, "MO2 unavailable", mo2_state.get("message", "MO2 install is not available."))
            return
        target_state = inspect_skyrim_launch_targets(
            Path(mo2_state["prefix"]), game_path,
            instance_name=instance_name, install_root=Path(mo2_state["install"]),
            require_managed=bool(mo2_state.get("managed", True)),
        )
        self._launch_skyrim_target_from_inventory(
            instance_name, target_state, game_path, Path(mo2_state["prefix"]),
            Path(mo2_state["install"]), Path(self.proton_combo.currentData()),
            bool(mo2_state.get("managed", True)),
            Path(mo2_state["steam_root"]) if mo2_state.get("steam_root") else None,
        )

    def _launch_registered_skyrim_target(
        self, record: dict, game: dict, installation: dict, runtime: dict,
        target_state: dict, game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None = None,
    ) -> None:
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

    def _launch_skyrim_target_from_inventory(
        self, instance_name: str, target_state: dict, game_path: Path,
        prefix: Path, install_root: Path, proton: Path, require_managed: bool,
        steam_root: Path | None = None,
    ) -> None:
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
        answer = QMessageBox.question(
            self, "Confirm Skyrim launch",
            f"MO2 will launch '{selected}' in instance '{instance_name}' using its virtual filesystem. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        audio_mode = "xaudio2_compat" if self.xaudio_override_checkbox.isChecked() else "default"
        result = launch_skyrim_via_mo2(
            selected, instance_name, expected_binary=selected_target["binary"],
            audio_mode=audio_mode, proton_path=proton, game_path=game_path,
            prefix_root=prefix, install_root=install_root,
            require_managed=require_managed, steam_root_path=steam_root,
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
