#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import sys
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QInputDialog,
    QWidget,
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
)
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
QWidget { background: #15171b; color: #e5e7eb; font-size: 14px; }
QLabel { background: transparent; border: none; }
QFrame#panel { background: #20232a; border: 1px solid #343943; border-radius: 12px; }
QLabel#heading { font-size: 28px; font-weight: 700; padding: 2px 0; }
QLabel#section { font-size: 16px; font-weight: 650; color: #f0f2f5; }
QLabel#muted { color: #a2aab6; }
QPushButton { background: #354255; border: 1px solid #526176; border-radius: 8px; padding: 10px 14px; min-height: 20px; }
QPushButton:hover { background: #40516a; border-color: #71849c; }
QPushButton:pressed { background: #2c394b; }
QPushButton:disabled { color: #818995; background: #262a31; border-color: #353b45; }
QComboBox { background: #252a33; border: 1px solid #414a58; border-radius: 7px; padding: 8px 10px; min-height: 20px; }
QProgressBar { background: #252a33; border: 1px solid #343943; border-radius: 6px; height: 14px; text-align: center; }
QProgressBar::chunk { background: #5b8def; border-radius: 5px; }
QPlainTextEdit { background: #111318; border: 1px solid #303641; border-radius: 8px; padding: 6px; }
QScrollArea { border: none; background: transparent; }
"""


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
        self._restoring_selection = True

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel("Wyrd")
        heading.setObjectName("heading")
        subtitle = QLabel("A focused Skyrim Special Edition + Mod Organizer 2 setup for Linux")
        subtitle.setObjectName("muted")
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        self.game_panel = panel("1. Skyrim Special Edition", "Detecting Steam libraries…")
        layout.addWidget(self.game_panel)

        runtime_panel = QFrame()
        runtime_panel.setObjectName("panel")
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_layout.setContentsMargins(17, 15, 17, 15)
        runtime_heading = QLabel("2. Proton runtime")
        runtime_heading.setObjectName("section")
        runtime_detail = QLabel("Wyrd uses the selected Proton build to run MO2 inside Skyrim’s existing Steam AppID 489830 prefix. The prefix is shared with Steam; Wyrd does not create or reset it.")
        runtime_detail.setObjectName("muted")
        runtime_detail.setWordWrap(True)
        runtime_layout.addWidget(runtime_heading)
        runtime_layout.addWidget(runtime_detail)
        runtime_row = QHBoxLayout()
        self.proton_combo = QComboBox()
        self.proton_combo.setMinimumWidth(420)
        self.refresh_button = QPushButton("Refresh detection")
        self.refresh_button.clicked.connect(self.refresh_all)
        self.proton_combo.currentIndexChanged.connect(self._save_selection)
        runtime_row.addWidget(self.proton_combo, 1)
        runtime_row.addWidget(self.refresh_button)
        runtime_layout.addLayout(runtime_row)
        self.runtime_status = QLabel("Looking for Proton…")
        self.runtime_status.setObjectName("muted")
        runtime_layout.addWidget(self.runtime_status)
        layout.addWidget(runtime_panel)

        self.mo2_panel = panel("3. Mod Organizer 2", "Checking Wyrd's managed installation…")
        layout.addWidget(self.mo2_panel)

        self.audio_panel = panel(
            "4. Audio compatibility",
            "The library check below is legacy-only: it inspects Wyrd’s old isolated MO2 prefix, not Skyrim’s Steam prefix. Skyrim audio must be verified in an actual game launch.",
        )
        audio_layout = self.audio_panel.layout()
        self.audio_detail = QLabel("Audio library inventory will be checked during detection.")
        self.audio_detail.setObjectName("muted")
        self.audio_detail.setWordWrap(True)
        audio_layout.addWidget(self.audio_detail)
        audio_actions = QHBoxLayout()
        self.audio_check_button = QPushButton("Inspect Legacy Audio Libraries")
        self.audio_check_button.clicked.connect(self.check_audio_libraries)
        audio_actions.addWidget(self.audio_check_button)
        self.xaudio_override_checkbox = QCheckBox("Enable XAudio2 compatibility override")
        self.xaudio_override_checkbox.setChecked(bool(self._saved_settings.get("xaudio2_compat", False)))
        self.xaudio_override_checkbox.toggled.connect(self._save_audio_option)
        audio_actions.addWidget(self.xaudio_override_checkbox)
        audio_actions.addStretch(1)
        audio_layout.addLayout(audio_actions)
        layout.addWidget(self.audio_panel)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.install_button = QPushButton("Install MO2")
        self.install_button.clicked.connect(self.install_mo2)
        self.launch_button = QPushButton("Launch MO2")
        self.launch_button.clicked.connect(self.start_mo2)
        self.launch_button.setEnabled(False)
        self.launch_game_prefix_button = QPushButton("Launch MO2 in Skyrim Prefix")
        self.launch_game_prefix_button.clicked.connect(self.start_mo2_in_skyrim_prefix)
        self.launch_game_prefix_button.setEnabled(False)
        self.copy_instance_button = QPushButton("Copy Existing Skyrim Instance")
        self.copy_instance_button.clicked.connect(self.copy_instance_to_skyrim_prefix)
        self.copy_instance_button.setEnabled(False)
        self.reset_prefix_button = QPushButton("Nuke MO2 Compatdata")
        self.reset_prefix_button.setEnabled(False)
        self.reset_prefix_button.clicked.connect(self.reset_mo2_prefix)
        self.launch_game_button = QPushButton("Launch Skyrim (MO2)")
        self.launch_game_button.clicked.connect(self.start_skyrim)
        self.launch_game_button.setEnabled(False)
        self.launch_direct_button = QPushButton("Launch Unmodded (Steam)")
        self.launch_direct_button.clicked.connect(self.start_skyrim_direct)
        self.launch_direct_button.setEnabled(False)
        self.diagnostics_button = QPushButton("View launch log")
        self.diagnostics_button.clicked.connect(self.show_launch_diagnostics)
        self.launch_status_button = QPushButton("Check launch status")
        self.launch_status_button.clicked.connect(self.check_launch_status)
        action_row.addWidget(self.install_button)
        action_row.addWidget(self.launch_button)
        self.launch_game_prefix_button.hide()
        self.copy_instance_button.hide()
        self.reset_prefix_button.hide()
        action_row.addWidget(self.launch_game_button)
        action_row.addWidget(self.launch_direct_button)
        action_row.addWidget(self.diagnostics_button)
        action_row.addWidget(self.launch_status_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("muted")
        layout.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        instructions = panel(
            "5. Launch Skyrim from MO2",
            "On the first MO2 launch, create a new instance and choose “Skyrim Special Edition”. "
            "MO2 runs in Skyrim’s existing Steam AppID 489830 prefix. Wyrd does not create or reset that prefix. "
            "After the instance is created, select Skyrim or SKSE in MO2's executable dropdown and press Run. "
            "MO2 then launches the game through its mod virtual filesystem.",
        )
        layout.addWidget(instructions)

        logs_heading = QLabel("Setup log")
        logs_heading.setObjectName("section")
        layout.addWidget(logs_heading)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(400)
        self.log_view.setPlaceholderText("Detection and installation progress will appear here.")
        self.log_view.setMinimumHeight(130)
        self.log_view.setMaximumHeight(190)
        layout.addWidget(self.log_view)

        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
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
            self.launch_game_prefix_button.setEnabled(False)
            self._log("Skyrim-prefix MO2 inspection: " + owner + " installation.")
        else:
            self.mo2_panel.detail_label.setText(self.mo2_state.get("message", "MO2 is not installed."))
            self.install_button.setText("Install MO2")
            self.install_button.setEnabled(bool(self.game_state.get("found") and self.proton_builds))
            self.launch_button.setEnabled(False)
            self.launch_game_button.setEnabled(False)
            self.launch_game_prefix_button.setEnabled(False)
        self.launch_direct_button.setEnabled(bool(self.game_state.get("found")))

        self._refresh_audio_status()
        prefix_path = managed_paths()['prefix']
        self.reset_prefix_button.setEnabled(prefix_path.exists() and not prefix_path.is_symlink())
        ready = bool(self.game_state.get("found") and self.proton_builds)
        self.install_button.setEnabled(
            ready
            and not (self.worker and self.worker.isRunning())
            and not self.mo2_state.get("installed")
        )
        self.proton_combo.setEnabled(bool(self.proton_builds) and not (self.worker and self.worker.isRunning()))

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
        state = inspect_skyrim_prefix_mo2_installation(Path(self.game_state["game_dir"]))
        if state.get("installed"):
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

    def start_skyrim(self) -> None:
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
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No verified Skyrim/SKSE executable is available in this instance."),
            )
            self._log(target_state.get("message", "No valid targets found."))
            return
        targets = target_state["targets"]
        # Include the binary basename when titles collide; keep the original
        # MO2 title separately because that is what its CLI expects.
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
            f"MO2 will launch '{selected}' in instance '{instance_name}' "
            "using its virtual filesystem. Wyrd will not edit the instance configuration.\\n\\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        audio_mode = "xaudio2_compat" if self.xaudio_override_checkbox.isChecked() else "default"
        self._log("Skyrim audio launch mode: " + audio_mode)
        result = launch_skyrim_via_mo2(
            selected, instance_name, expected_binary=selected_target["binary"],
            audio_mode=audio_mode,
            proton_path=Path(self.proton_combo.currentData()),
            game_path=game_path, prefix_root=Path(mo2_state["prefix"]),
            install_root=Path(mo2_state["install"]),
            require_managed=bool(mo2_state.get("managed", True)),
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
            self.launch_session.wrapper_start_time is None
            or wrapper_identity.get("start_time") == self.launch_session.wrapper_start_time
        )
        process_scan = detect_skyrim_processes(steam_compat_prefix(Path(self.game_state['game_dir']).parents[2]))
        session_processes = []
        if process_scan.get("ok") and wrapper_identity_matches:
            for item in process_scan.get("processes", []):
                if _is_descendant_process(item["pid"], self.launch_session.wrapper_pid):
                    session_processes.append(item)
        if session_processes:
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})" for item in session_processes
            )
            self.progress_label.setText(
                "Skyrim/SKSE detected in this Wyrd launch process tree."
            )
            self.launch_session.detected_processes = list(session_processes)
            self.launch_session.process_identities = {
                item["pid"]: item.get("start_time") for item in session_processes
            }
            self.launch_session.game_processes = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="launch_process_tree",
                    attribution_confidence="confirmed",
                ) for item in session_processes
            ]
            self.launch_session.state = "detected"
            self._log("Launch session detected: " + detected)
            self._log_audio_runtime_environment(session_processes)
            self._stop_launch_monitor("Skyrim/SKSE process detected")
            self._start_game_process_watch()
            return
        if result["state"] in {"exited", "invalid"}:
            self.progress_label.setText(
                "Proton wrapper exited before Skyrim was detected; review launch diagnostics."
            )
            self._log("Launch handoff ended without detected Skyrim: " + result["message"])
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
                f"Waiting for Skyrim/SKSE… (check {self.launch_session.poll_count}/{self._launch_poll_limit})"
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
        self.progress_label.setText("Skyrim/SKSE processes exited; launch session ended.")
        self._log("Detected Skyrim/SKSE process identities are no longer present. Game exit recorded.")

    def check_launch_status(self) -> None:
        """Report the current tracked game processes, even after Proton's wrapper exits."""
        session = self.launch_session
        game_dir = self.game_state.get("game_dir")
        steam_prefix = steam_compat_prefix(Path(game_dir).parents[2]) if game_dir else None
        process_scan = detect_skyrim_processes(steam_prefix)
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
            self.progress_label.setText(
                "Skyrim/SKSE is running; tracked process identity verified."
            )
            detail = "Tracked Skyrim/SKSE processes are still present:\\n" + detected
            QMessageBox.information(self, "Launch process status", detail)
            return

        if session.game_processes:
            session.game_processes = []
            session.state = "game-exited"
            self._game_watch_timer.stop()
            self.progress_label.setText("Previously detected Skyrim/SKSE processes have exited.")
            self._log("Tracked Skyrim/SKSE process identities are no longer present.")

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
            session.wrapper_start_time is None
            or wrapper_identity.get("start_time") == session.wrapper_start_time
        )
        result = launch_process_status(session.wrapper_pid, session.wrapper_start_time)
        self._log(
            f"Launch PID {session.wrapper_pid}: {result['state']} — {result['message']}"
        )

        session_processes = []
        if process_scan.get("ok") and wrapper_matches:
            for item in process_scan.get("processes", []):
                if _is_descendant_process(item["pid"], session.wrapper_pid):
                    session_processes.append(item)

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
