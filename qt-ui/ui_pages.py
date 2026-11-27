"""MainWindow page construction, kept separate from window lifecycle/state."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QGridLayout, QWidget, QScrollArea,
)
from mo2_library_dialog import Mo2LibraryDialog
from ui_components import add_info_button, panel
from ui_path_helpers import initial_mo2_dialog_paths


class MainWindowPagesMixin:
    def _build_pages(self, outer) -> None:
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

        prefix_path, install_path, game_path = initial_mo2_dialog_paths(
            self.mo2_state
        )
        self.mo2_library_widget = Mo2LibraryDialog(
            self, prefix_path=prefix_path, install_path=install_path,
            game_path=game_path, proton_path=self._saved_settings.get("proton", ""),
        )
        setup_layout.addWidget(self.mo2_library_widget)
        self.install_button = QPushButton("Install MO2")
        self.install_button.setObjectName("secondaryAction")
        self.install_button.clicked.connect(self.install_mo2)
        self.launch_direct_button = QPushButton("Launch Unmodded")
        self.launch_direct_button.setObjectName("secondaryAction")
        self.launch_direct_button.clicked.connect(self.start_skyrim_direct)
        self.launch_direct_button.setEnabled(False)
        self.diagnostics_button = QPushButton("View launch log")
        self.diagnostics_button.clicked.connect(self.show_launch_diagnostics)
        self.launch_status_button = QPushButton("Check launch status")
        self.launch_status_button.clicked.connect(self.check_launch_status)
        self.active_instance_label = QLabel("Modded launch is available from each Skyrim instance row above.")
        self.active_instance_label.setObjectName("muted")
        launch_panel = QFrame()
        launch_panel.setObjectName("launchPanel")
        launch_layout = QVBoxLayout(launch_panel)
        launch_layout.setContentsMargins(16, 14, 16, 14)
        launch_layout.setSpacing(8)
        launch_title = QLabel("UNMODDED LAUNCH")
        launch_title.setObjectName("section")
        launch_layout.addWidget(launch_title)
        launch_layout.addWidget(self.active_instance_label)
        launch_actions = QHBoxLayout()
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
