"""MainWindow page construction, kept separate from window lifecycle/state."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QGridLayout, QWidget, QScrollArea, QTabWidget, QStackedLayout,
)
from mo2_library_dialog import Mo2LibraryDialog
from ui_components import add_info_button, panel
from ui_path_helpers import initial_mo2_dialog_paths


class MainWindowPagesMixin:
    def _build_pages(self, outer) -> None:
        setup_page = QWidget()
        setup_layout = QVBoxLayout(setup_page)
        setup_layout.setContentsMargins(16, 18, 16, 18)
        setup_layout.setSpacing(18)

        hero_banner = QFrame()
        hero_banner.setObjectName("heroBanner")
        hero_banner.setMinimumHeight(190)
        hero_stack = QStackedLayout(hero_banner)
        hero_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        hero_stack.setContentsMargins(0, 0, 0, 0)

        hero_art = QLabel()
        hero_art.setObjectName("heroArtwork")
        hero_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_art.setScaledContents(True)
        hero_image = QPixmap(str(Path(__file__).resolve().parent / "assets" / "skyrim_hero.svg"))
        if not hero_image.isNull():
            hero_art.setPixmap(hero_image)
        hero_stack.addWidget(hero_art)

        hero_shade = QFrame()
        hero_shade.setObjectName("heroShade")
        hero_shade.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hero_stack.addWidget(hero_shade)

        hero_content = QWidget()
        hero_content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        hero_layout = QHBoxLayout(hero_content)
        hero_layout.setContentsMargins(30, 20, 30, 20)
        hero_layout.setSpacing(20)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(7)
        hero_eyebrow = QLabel("THE NORDIC MODDING WORKSPACE")
        hero_eyebrow.setObjectName("heroEyebrow")
        hero_text.addWidget(hero_eyebrow)
        hero_title = QLabel("Your Skyrim Environment")
        hero_title.setObjectName("heroTitle")
        hero_text.addWidget(hero_title)
        hero_subtitle = QLabel("Manage Skyrim SE, Proton and Mod Organizer 2 instances.")
        hero_subtitle.setObjectName("heroSubtitle")
        hero_text.addWidget(hero_subtitle)

        session_row = QHBoxLayout()
        session_row.setSpacing(10)
        session_heading = QLabel("SESSION")
        session_heading.setObjectName("sessionHeading")
        session_row.addWidget(session_heading)
        self.session_status_label = QLabel("Idle")
        self.session_status_label.setObjectName("sessionStatus")
        session_row.addWidget(self.session_status_label)
        self.session_detail_label = QLabel("No MO2 or Skyrim session detected.")
        self.session_detail_label.setObjectName("sessionDetail")
        self.session_detail_label.setWordWrap(True)
        session_row.addWidget(self.session_detail_label, 1)
        hero_text.addSpacing(4)
        hero_text.addLayout(session_row)
        hero_text.addStretch(1)
        hero_layout.addLayout(hero_text, 1)
        hero_stack.addWidget(hero_content)
        setup_layout.addWidget(hero_banner)

        self.precheck_panel = QFrame()
        self.precheck_panel.setObjectName("statusPanel")
        precheck_layout = QVBoxLayout(self.precheck_panel)
        precheck_layout.setContentsMargins(18, 14, 18, 14)
        precheck_layout.setSpacing(10)
        precheck_top = QHBoxLayout()
        precheck_heading = QLabel("ENVIRONMENT CHECKS")
        precheck_heading.setObjectName("section")
        precheck_top.addWidget(precheck_heading)
        precheck_top.addStretch(1)
        self.precheck_status = QLabel("Checking your setup…")
        self.precheck_status.setObjectName("muted")
        self.precheck_status.setWordWrap(True)
        precheck_top.addWidget(self.precheck_status)
        precheck_layout.addLayout(precheck_top)
        readiness_grid = QGridLayout()
        readiness_grid.setHorizontalSpacing(22)
        readiness_grid.setVerticalSpacing(9)
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
        self.check_mo2_button = QPushButton("Check installation")
        self.check_mo2_button.setObjectName("secondaryAction")
        self.check_mo2_button.setToolTip(
            "Check whether Skyrim's Proton prefix, MO2 installation and MO2 instances exist."
        )
        self.check_mo2_button.clicked.connect(self.check_mo2_installation)
        self.open_mo2_install_folder_button = QPushButton("Open MO2 installation folder")
        self.open_mo2_install_folder_button.setObjectName("secondaryAction")
        self.open_mo2_install_folder_button.setToolTip(
            "Open the verified MO2 program directory in your file manager."
        )
        self.open_mo2_install_folder_button.clicked.connect(self.open_mo2_installation_folder)
        progress_panel = QFrame()
        progress_panel.setObjectName("runtimePanel")
        progress_layout = QVBoxLayout(progress_panel)
        progress_layout.setContentsMargins(20, 16, 20, 18)
        progress_layout.setSpacing(12)
        progress_header = QHBoxLayout()
        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("section")
        progress_header.addWidget(self.progress_label, 1)
        progress_layout.addLayout(progress_header)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(30)
        self.progress.setMinimumWidth(260)
        progress_layout.addWidget(self.progress)
        self.progress_panel = progress_panel
        progress_panel.hide()
        setup_layout.addWidget(progress_panel)

        configuration_grid = QGridLayout()
        configuration_grid.setHorizontalSpacing(18)
        configuration_grid.setVerticalSpacing(18)
        self.game_panel = panel("SKYRIM SPECIAL EDITION", "Detecting your Steam installation…")
        self.game_panel.setMinimumHeight(154)
        configuration_grid.addWidget(self.game_panel, 0, 0)

        runtime_panel = QFrame()
        runtime_panel.setObjectName("panel")
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_layout.setContentsMargins(18, 16, 18, 16)
        runtime_layout.setSpacing(8)
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
        runtime_panel.setMinimumHeight(154)
        configuration_grid.addWidget(runtime_panel, 0, 1)
        configuration_grid.setColumnStretch(0, 1)
        configuration_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(configuration_grid)

        self.mo2_panel = panel("MOD ORGANIZER 2", "Checking the installation…")

        prefix_path, install_path, game_path = initial_mo2_dialog_paths(
            self.mo2_state
        )
        self.mo2_library_widget = Mo2LibraryDialog(
            self, prefix_path=prefix_path, install_path=install_path,
            game_path=game_path, proton_path=self._saved_settings.get("proton", ""),
        )
        setup_layout.addWidget(self.mo2_library_widget, 1)
        # Installation is exposed contextually in the MO2 Instances header.
        # Keep the button object for the existing installer workflow state,
        # but do not render a duplicate Install MO2 action on this page.

        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        setup_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        setup_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        setup_scroll.setWidget(setup_page)
        self.tabs.addTab(setup_scroll, "LAUNCH")

        maintenance_page = QWidget()
        maintenance_layout = QVBoxLayout(maintenance_page)
        maintenance_layout.setContentsMargins(18, 20, 18, 18)
        maintenance_layout.setSpacing(18)
        maintenance_layout.addWidget(self.precheck_panel)
        mo2_tools_panel = panel(
            "MO2 INSTALLATION TOOLS",
            "Inspect the current MO2 installation or open its verified program folder. "
            "These actions do not modify your instances, profiles or mod data."
        )
        mo2_tools_actions = QHBoxLayout()
        mo2_tools_actions.setSpacing(10)
        mo2_tools_actions.addWidget(self.check_mo2_button)
        add_info_button(
            mo2_tools_actions, self, "Check installation",
            "Runs a read-only diagnostic of Skyrim, its Proton prefix, the MO2 program files and discovered MO2 instances. It does not install, remove or modify anything."
        )
        mo2_tools_actions.addSpacing(8)
        mo2_tools_actions.addWidget(self.open_mo2_install_folder_button)
        add_info_button(
            mo2_tools_actions, self, "Open MO2 installation folder",
            "Opens the verified MO2 program installation directory in your file manager. This is the application folder, not an instance's mods, profiles or downloads."
        )
        mo2_tools_actions.addStretch(1)
        mo2_tools_panel.layout().addLayout(mo2_tools_actions)
        maintenance_layout.addWidget(mo2_tools_panel)
        diagnostics_grid = QGridLayout()
        diagnostics_grid.setHorizontalSpacing(18)
        diagnostics_grid.setVerticalSpacing(18)

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
            "AUDIO OPTIONS",
            "Optional launch-time compatibility setting. This does not inspect or modify Wine/Proton audio libraries."
        )
        audio_layout = self.audio_panel.layout()
        audio_actions = QHBoxLayout()
        self.xaudio_override_checkbox = QCheckBox("Enable XAudio2 compatibility override")
        self.xaudio_override_checkbox.setChecked(bool(self._saved_settings.get("xaudio2_compat", False)))
        self.xaudio_override_checkbox.toggled.connect(self._save_audio_option)
        audio_actions.addWidget(self.xaudio_override_checkbox)
        add_info_button(
            audio_actions, self, "XAudio2 compatibility override",
            "Adds the XAudio2 compatibility override to Wyrd's existing SteamCompat launch. "
            "Leave it disabled unless you need it for Skyrim audio troubleshooting."
        )
        audio_actions.addStretch(1)
        audio_layout.addLayout(audio_actions)
        diagnostics_grid.addWidget(self.audio_panel, 0, 1)

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

        logs_page = QWidget()
        logs_layout = QVBoxLayout(logs_page)
        logs_layout.setContentsMargins(16, 16, 16, 16)
        logs_layout.setSpacing(16)
        self.logs_tabs = QTabWidget()
        self.logs_tabs.setObjectName("logsTabs")

        application_page = QWidget()
        application_layout = QVBoxLayout(application_page)
        application_layout.setContentsMargins(8, 8, 8, 8)
        application_layout.setSpacing(12)
        application_panel = panel(
            "APPLICATION ACTIVITY",
            "Wyrd detection, installation, settings and application errors. Persistent, newest entries at the bottom."
        )
        application_header = QHBoxLayout()
        application_header.addStretch(1)
        self.refresh_application_logs_button = QPushButton("Refresh")
        self.refresh_application_logs_button.setObjectName("secondaryAction")
        self.refresh_application_logs_button.clicked.connect(self.refresh_application_log_view)
        application_header.addWidget(self.refresh_application_logs_button)
        application_panel.layout().addLayout(application_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setPlaceholderText("Wyrd messages will appear here as you use the application.")
        application_panel.layout().addWidget(self.log_view, 1)
        application_layout.addWidget(application_panel, 1)
        self.logs_tabs.addTab(application_page, "Application")

        launch_page = QWidget()
        launch_layout = QVBoxLayout(launch_page)
        launch_layout.setContentsMargins(8, 8, 8, 8)
        launch_layout.setSpacing(12)
        launch_panel = panel(
            "STEAMCOMPAT LAUNCH",
            "Persistent output from the MO2 to Proton to Skyrim launch handoff."
        )
        launch_header = QHBoxLayout()
        launch_header.addStretch(1)
        self.refresh_launch_logs_button = QPushButton("Refresh")
        self.refresh_launch_logs_button.setObjectName("secondaryAction")
        self.refresh_launch_logs_button.clicked.connect(self.refresh_launch_log_view)
        launch_header.addWidget(self.refresh_launch_logs_button)
        launch_panel.layout().addLayout(launch_header)
        self.launch_log_view = QPlainTextEdit()
        self.launch_log_view.setReadOnly(True)
        self.launch_log_view.setMaximumBlockCount(3000)
        self.launch_log_view.setPlaceholderText("The persistent Skyrim launch log will appear here.")
        launch_panel.layout().addWidget(self.launch_log_view, 1)
        launch_layout.addWidget(launch_panel, 1)
        self.logs_tabs.addTab(launch_page, "Launch")

        system_page = QWidget()
        system_layout = QVBoxLayout(system_page)
        system_layout.setContentsMargins(8, 8, 8, 8)
        system_layout.setSpacing(12)
        system_panel = panel(
            "SYSTEM SNAPSHOT",
            "Read-only host memory and Wine / Proton process inventory. This is a diagnostic snapshot, not a complete system journal."
        )
        system_header = QHBoxLayout()
        system_header.addStretch(1)
        self.refresh_system_logs_button = QPushButton("Refresh snapshot")
        self.refresh_system_logs_button.setObjectName("secondaryAction")
        self.refresh_system_logs_button.clicked.connect(self.refresh_system_log_view)
        system_header.addWidget(self.refresh_system_logs_button)
        system_panel.layout().addLayout(system_header)
        self.system_log_view = QPlainTextEdit()
        self.system_log_view.setReadOnly(True)
        self.system_log_view.setMaximumBlockCount(500)
        self.system_log_view.setPlaceholderText("Refresh to inspect current host memory and Wine / Proton processes.")
        system_panel.layout().addWidget(self.system_log_view, 1)
        system_layout.addWidget(system_panel, 1)
        self.logs_tabs.addTab(system_page, "System")

        logs_layout.addWidget(self.logs_tabs, 1)
        logs_scroll = QScrollArea()
        logs_scroll.setWidgetResizable(True)
        logs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        logs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        logs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        logs_scroll.setWidget(logs_page)
        self.tabs.addTab(logs_scroll, "LOGS")

