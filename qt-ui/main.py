#!/usr/bin/env python3
"""Qt 6 desktop application for Wyrd's Skyrim + Mod Organizer 2 workflow."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
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
    inspect_audio_compatibility, repair_audio_compatibility,
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wyrd — Skyrim Modding Environment")
        self.resize(1040, 780)
        self.setMinimumSize(820, 640)
        self.worker: InstallWorker | None = None
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
        runtime_detail = QLabel("Wyrd uses a compatible Proton build to run MO2 in its own isolated prefix.")
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

        audio_panel = QFrame()
        audio_panel.setObjectName("panel")
        audio_layout = QVBoxLayout(audio_panel)
        audio_layout.setContentsMargins(17, 15, 17, 15)
        audio_heading = QLabel("4. Audio compatibility (XACT)")
        audio_heading.setObjectName("section")
        self.audio_detail = QLabel(
            "Inspect XACT components in Wyrd's managed Proton prefix. "
            "A file-level repair does not prove in-game dialogue audio works."
        )
        self.audio_detail.setObjectName("muted")
        self.audio_detail.setWordWrap(True)
        audio_layout.addWidget(audio_heading)
        audio_layout.addWidget(self.audio_detail)
        audio_buttons = QHBoxLayout()
        self.audio_check_button = QPushButton("Check audio components")
        self.audio_check_button.clicked.connect(self.check_audio_compatibility)
        self.audio_repair_button = QPushButton("Repair XACT components")
        self.audio_repair_button.clicked.connect(self.repair_audio)
        self.audio_repair_button.setEnabled(False)
        audio_buttons.addWidget(self.audio_check_button)
        audio_buttons.addWidget(self.audio_repair_button)
        audio_buttons.addStretch(1)
        audio_layout.addLayout(audio_buttons)
        layout.addWidget(audio_panel)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.install_button = QPushButton("Install MO2")
        self.install_button.clicked.connect(self.install_mo2)
        self.launch_button = QPushButton("Launch MO2")
        self.launch_button.clicked.connect(self.start_mo2)
        self.launch_button.setEnabled(False)
        self.launch_game_button = QPushButton("Launch Skyrim")
        self.launch_game_button.clicked.connect(self.start_skyrim)
        self.launch_game_button.setEnabled(False)
        self.diagnostics_button = QPushButton("View launch log")
        self.diagnostics_button.clicked.connect(self.show_launch_diagnostics)
        self.launch_status_button = QPushButton("Check launch status")
        self.launch_status_button.clicked.connect(self.check_launch_status)
        self.launch_status_button.setEnabled(False)
        action_row.addWidget(self.install_button)
        action_row.addWidget(self.launch_button)
        action_row.addWidget(self.launch_game_button)
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
            "Wyrd registers your detected game path only inside its own Proton prefix. "
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

        self.mo2_state = inspect_wyrd_installation()
        if self.mo2_state.get("installed") and self.mo2_state.get("prefix_ready"):
            self.instance_state = inspect_mo2_instances(managed_paths()["prefix"])
            self.launch_target_state = inspect_skyrim_launch_targets(
                managed_paths()["prefix"], Path(self.mo2_state["metadata"].get("game_path", ""))
            )
        else:
            self.instance_state = {"ok": False, "instances": [], "skyrim_instances": []}
            self.launch_target_state = {"ok": False, "targets": []}
        if self.mo2_state.get("installed"):
            version = self.mo2_state.get("metadata", {}).get("version", "unknown version")
            prefix = "ready" if self.mo2_state.get("prefix_ready") else "incomplete"
            instance_summary = self.instance_state.get("message", "Instance inventory unavailable.")
            skyrim_instances = self.instance_state.get("skyrim_instances", [])
            if skyrim_instances:
                executable_names = sorted({
                    entry.get("title") or entry.get("binary", "")
                    for instance in skyrim_instances for entry in instance.get("executables", [])
                    if entry.get("title") or entry.get("binary")
                })
                instance_summary += "\nSkyrim SE instance: " + ", ".join(
                    instance["name"] for instance in skyrim_instances
                )
                profiles = [
                    f"{instance['name']}: {instance.get('selected_profile') or 'No profile selected'}"
                    for instance in skyrim_instances
                ]
                instance_summary += "\nSelected MO2 profile: " + " | ".join(profiles)
                if executable_names:
                    instance_summary += "\nConfigured executables: " + ", ".join(executable_names)
            elif self.instance_state.get("ok"):
                instance_summary += "\nCreate a Skyrim Special Edition instance in MO2 to continue."
            self.mo2_panel.detail_label.setText(
                f"MO2 {version} is installed and verified.\n"
                f'Executable: {self.mo2_state.get("executable")}\n'
                f'Wyrd Proton prefix: {prefix}\n'
                f'{instance_summary}'
            )
            self.launch_button.setEnabled(bool(self.mo2_state.get("prefix_ready") and self.instance_state.get("ok")))
            self.launch_game_button.setEnabled(bool(self.launch_target_state.get("ok")))
            self.install_button.setText("Recheck MO2" if self.mo2_state.get("prefix_ready") else "Repair MO2 runtime")
            self._log("Wyrd MO2 inspection: verified " + version)
        else:
            external = self.mo2_state.get("external_installations", [])
            description = self.mo2_state.get("message", "MO2 is not installed.")
            if external:
                description += "\nExisting installation(s):\n" + "\n".join(external)
                description += "\nThese copies are left untouched; Wyrd installs into its own managed location."
            self.mo2_panel.detail_label.setText(description)
            self.launch_button.setEnabled(False)
            self.launch_game_button.setEnabled(False)
            self.install_button.setText("Install MO2")
            self._log("Wyrd MO2 inspection: " + description)

        ready = bool(self.game_state.get("found") and self.proton_builds)
        self.install_button.setEnabled(ready and not (self.worker and self.worker.isRunning()))
        self.proton_combo.setEnabled(bool(self.proton_builds) and not (self.worker and self.worker.isRunning()))
        self.check_audio_compatibility()

    def check_audio_compatibility(self) -> None:
        report = inspect_audio_compatibility()
        self.audio_report = report
        self.audio_repair_button.setEnabled(
            bool(report.get("safe") and report.get("repair_recommended"))
        )
        if not report.get("safe"):
            self.audio_detail.setText(
                "Audio inspection blocked (fail-closed): " + report.get("message", "Prefix ownership could not be verified.")
            )
            self._log("Audio compatibility: inspection blocked: " + report.get("message", "unknown reason"))
            return
        components = report.get("components", {})
        lines = [
            "Managed prefix: " + str(report.get("prefix")),
            "XACT (32-bit): " + components.get("xact", {}).get("state", "unknown"),
            "XACT (64-bit): " + components.get("xact_x64", {}).get("state", "unknown"),
            "Runtime dialogue audio: NOT VERIFIED — test dialogue in Skyrim.",
        ]
        self.audio_detail.setText("\\n".join(lines))
        self._log("Audio compatibility inspection: " + ", ".join(
            f"{key}={value.get('state')}" for key, value in components.items()
        ))

    def repair_audio(self) -> None:
        report = inspect_audio_compatibility()
        if not report.get("safe"):
            QMessageBox.warning(self, "Audio repair blocked", report.get("message", "Wyrd prefix ownership could not be verified."))
            self.check_audio_compatibility()
            return
        if not report.get("repair_recommended"):
            QMessageBox.information(self, "Audio compatibility", "Both XACT targets are already present. No repair is recommended.")
            return
        answer = QMessageBox.question(
            self, "Repair Wyrd audio components",
            "Wyrd will copy its selected Proton runtime's XACT DLLs into ONLY Wyrd's managed MO2 prefix. "
            "Existing target files will be backed up and the resulting file hashes verified. "
            "Steam's Skyrim prefix, Skyrim files, MO2 instances, profiles and mods will not be changed.\\n\\n"
            "This does not guarantee that Skyrim dialogue audio will work. You must test dialogue in-game.\\n\\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = repair_audio_compatibility(confirmed=True)
        self._log("Audio repair: " + result.get("message", "no result"))
        if result.get("log_path"):
            self._log("Audio repair log: " + result["log_path"])
        if result.get("ok"):
            QMessageBox.information(self, "XACT file repair complete", result.get("message", ""))
        else:
            QMessageBox.warning(self, "XACT file repair failed", result.get("message", "No changes were made."))
        self.check_audio_compatibility()

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
            write_settings(None, Path(proton), Path(game))
            self._saved_settings = {
                "proton": str(Path(proton).resolve()),
                "game_path": str(Path(game).resolve()),
            }
        except OSError as error:
            self._log(f"Could not save Wyrd settings: {error}")

    def install_mo2(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.game_state.get("found"):
            QMessageBox.warning(self, "Wyrd setup", "Skyrim Special Edition was not detected. Install it through Steam, then refresh.")
            return
        proton_path = self.proton_combo.currentData()
        if not proton_path:
            QMessageBox.warning(self, "Wyrd setup", "No compatible Proton build is selected.")
            return
        if self.mo2_state.get("installed") and self.mo2_state.get("prefix_ready"):
            self.start_mo2()
            return
        answer = QMessageBox.question(
            self, "Install Mod Organizer 2",
            "Wyrd will download the official MO2 release, install it into a Wyrd-managed folder, "
            "and initialize a separate Proton prefix for MO2.\n\n"
            "Your Skyrim files, Steam prefix, saves, and any existing MO2 installation will not be modified.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        game_path = self.game_state["game_dir"]
        self.worker = InstallWorker(str(proton_path), str(game_path))
        self.worker.progress.connect(self._on_install_progress)
        self.worker.completed.connect(self._on_install_completed)
        self.install_button.setEnabled(False)
        self.launch_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.proton_combo.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("Starting MO2 setup…")
        self._log("Starting Wyrd-owned MO2 installation.")
        self.worker.start()

    def _on_install_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.progress_label.setText(message)
        self._log(message)

    def _on_install_completed(self, result: dict) -> None:
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
        """Return a fresh read-only inventory for the current managed prefix."""
        return inspect_mo2_instances(managed_paths()["prefix"])

    def _has_instance(self) -> bool:
        state = self._instance_inspection()
        return bool(state.get("ok") and state.get("instances"))

    def start_mo2(self) -> None:
        state = self._instance_inspection()
        if not state.get("ok"):
            QMessageBox.warning(
                self, "MO2 instance status unavailable",
                state.get("message", "Wyrd could not safely inspect its managed MO2 prefix."),
            )
            self._log("MO2 instance inspection blocked launch: " +
                      state.get("message", "unknown inspection error"))
            return
        first_run = not state.get("instances")
        result = launch_mo2(pick_instance=first_run)
        self._log(result.get("message", "MO2 launch failed."))
        if result.get("ok"):
            self.progress_label.setText("MO2 launched through Proton.")
            if first_run:
                QMessageBox.information(
                    self, "First MO2 launch",
                    "MO2 has been opened in its instance picker.\n\n"
                    "Create a new instance and select Skyrim Special Edition. "
                    "Then choose Skyrim or SKSE in MO2's executable dropdown and press Run."
                )
        else:
            QMessageBox.warning(self, "Launch MO2", result.get("message", "Could not launch MO2."))

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
        target_state = inspect_skyrim_launch_targets(
            managed_paths()["prefix"],
            Path(self.mo2_state.get("metadata", {}).get("game_path", "")),
            instance_name=instance_name,
        )
        if not target_state.get("ok") or not target_state.get("targets"):
            QMessageBox.warning(
                self, "Skyrim launch unavailable",
                target_state.get("message", "No verified Skyrim/SKSE executable is available in this instance."),
            )
            self._log(target_state.get("message", "No valid targets found."))
            return
        targets = target_state["targets"]
        titles = [target["title"] for target in targets]
        selected, accepted = QInputDialog.getItem(
            self, "Launch Skyrim through MO2", "Configured Skyrim executable:",
            titles, 0, False,
        )
        if not accepted or not selected:
            return
        answer = QMessageBox.question(
            self, "Confirm Skyrim launch",
            f"MO2 will launch '{selected}' in instance '{instance_name}' "
            "using its virtual filesystem. Wyrd will not edit the instance configuration.\\n\\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = launch_skyrim_via_mo2(selected, instance_name)
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
            self.launch_status_button.setEnabled(bool(self.launch_session.wrapper_pid))
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
        process_scan = detect_skyrim_processes(managed_paths()["prefix"])
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
            self._stop_launch_monitor("Skyrim/SKSE process detected")
            self._start_game_process_watch()
            return
        if process_scan.get("ok") and process_scan.get("wyrd_processes"):
            detected = ", ".join(
                f"{item['binary']} (PID {item['pid']})"
                for item in process_scan["wyrd_processes"]
            )
            self.progress_label.setText(
                "Skyrim detected in Wyrd's prefix; launch-session ancestry is unconfirmed."
            )
            self._log(
                "Prefix-attributed Skyrim process(es), ancestry unconfirmed: " + detected
            )
            self.launch_session.detected_processes = list(process_scan["wyrd_processes"])
            self.launch_session.process_identities = {
                item["pid"]: item.get("start_time") for item in process_scan["wyrd_processes"]
            }
            self.launch_session.game_processes = [
                GameProcess(
                    pid=item["pid"], binary=item["binary"],
                    start_time=item.get("start_time"),
                    detection_source="managed_prefix",
                    attribution_confidence="prefix",
                ) for item in process_scan["wyrd_processes"]
            ]
            self.launch_session.state = "detected-unconfirmed"
            self._stop_launch_monitor("managed-prefix Skyrim process detected")
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
        process_scan = detect_skyrim_processes(managed_paths()["prefix"])
        self._log("Skyrim process scan: " + process_scan.get("message", "Detection unavailable."))

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
                "No Wyrd launch handoff has been recorded in this session.\\n\\n"
                + process_scan.get("message", "Process detection unavailable.")
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
