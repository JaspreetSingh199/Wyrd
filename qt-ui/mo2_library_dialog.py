"""Read-only MO2 environment inventory widgets.

Wyrd discovers MO2 instances from the live filesystem. It does not maintain
an instance registry, claim ownership of external installations, or edit
MO2 storage configuration.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QWidget, QFrame, QComboBox, QSizePolicy,
)


def _instance_label(instance: dict) -> str:
    name = instance.get("name") or "Unnamed MO2 instance"
    profile = instance.get("selected_profile") or "No profile selected"
    game = (
        "Skyrim SE" if instance.get("is_skyrim_se")
        else f"Other / unknown game ({instance.get('game_name') or 'not configured'})"
    )
    return f"{name} · {profile} · {game}"


class Mo2LibraryDialog(QFrame):
    """Compact, read-only picker for instances found by the current scan."""

    def __init__(
        self, parent=None, *, prefix_path="", install_path="", game_path="",
        proton_path="", selected_instance_id="",
    ):
        super().__init__(parent)
        # Qt reparents widgets to the page that owns their layout. Keep an
        # explicit reference to MainWindow; QWidget.parent() is not stable
        # after the widget is inserted into the setup-page layout.
        self._owner_window = parent
        self.setObjectName("mo2InstancePicker")
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
        self.mo2_installed = False
        self.preferred_instance_id = selected_instance_id
        self.discovery: dict = {}
        self.selected_instance_record: dict | None = None
        self.on_launch_requested = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        heading_row = QHBoxLayout()
        heading = QLabel("MO2 Instances")
        heading.setObjectName("instanceSectionTitle")
        heading_row.addWidget(heading)
        self.instance_count = QLabel("0 Instances")
        self.instance_count.setObjectName("instanceCount")
        heading_row.addWidget(self.instance_count)
        heading_row.addStretch(1)
        self.open_mo2_button = QPushButton("Open MO2")
        self.open_mo2_button.setObjectName("secondaryAction")
        self.open_mo2_button.setToolTip(
            "Open a discovered Skyrim Special Edition MO2 instance to review its mods and profiles."
        )
        self.open_mo2_button.clicked.connect(lambda _checked=False: self._open_selected_mo2())
        heading_row.addWidget(self.open_mo2_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(heading_row)
        self.setup_status = QLabel("MO2 instances are discovered automatically.")
        self.setup_status.setObjectName("muted")
        self.setup_status.setWordWrap(True)
        layout.addWidget(self.setup_status)

        self.instances = QListWidget()
        self.instances.setObjectName("mo2Instances")
        self.instances.setMinimumHeight(90)
        self.instances.setSpacing(8)
        self.instances.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.instances.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.instances, 1)
        unmodded_panel = QFrame()
        unmodded_panel.setObjectName("unmoddedLaunchPanel")
        unmodded_row = QHBoxLayout(unmodded_panel)
        unmodded_row.setContentsMargins(14, 9, 14, 9)
        steam_mark = QLabel("◉")
        steam_mark.setObjectName("steamMark")
        unmodded_row.addWidget(steam_mark)
        unmodded_title = QLabel("Unmodded Launch")
        unmodded_title.setObjectName("unmoddedTitle")
        unmodded_row.addWidget(unmodded_title)
        self.launch_unmodded_button = QPushButton("▶  Launch Skyrim (Steam)")
        self.launch_unmodded_button.setObjectName("secondaryAction")
        self.launch_unmodded_button.clicked.connect(self._launch_unmodded)
        unmodded_row.addWidget(self.launch_unmodded_button)
        unmodded_description = QLabel("Launch directly through Steam, without MO2.")
        unmodded_description.setObjectName("muted")
        unmodded_row.addWidget(unmodded_description, 1)
        layout.addWidget(unmodded_panel)
        self._launch_ready = False

    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path="", installed=False):
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
        self.mo2_installed = bool(installed)
        self.open_mo2_button.setText("Open MO2" if self.mo2_installed else "Install MO2")
        self.open_mo2_button.setToolTip(
            "Open MO2 Instance Manager to create or manage instances." if self.mo2_installed
            else "Install MO2. Existing instance data will be preserved."
        )
        self._show_inventory()

    def discover(self):
        refresh = getattr(self._owner_window, "refresh_all", None)
        if callable(refresh):
            refresh()
        self._show_inventory()

    def set_launch_ready(self, ready: bool) -> None:
        self._launch_ready = bool(ready)
        self._show_inventory()

    def _show_inventory(self):
        inventory = getattr(self._owner_window, "instance_state", {}) or {}
        instances = inventory.get("instances", [])
        if not self.mo2_installed:
            self.instances.clear()
            self.instances.hide()
            self.instance_count.setText("MO2 not installed")
            self.setup_status.setText(
                "Mod Organizer 2 is not installed. Existing instance folders have been left untouched "
                "and will be rediscovered after MO2 is installed again."
            )
            self.setup_status.show()
            return
        selected_path = (
            self.selected_instance_record.get("path")
            if self.selected_instance_record else None
        )
        self.instances.clear()
        self.selected_instance_record = None
        for instance_index, instance in enumerate(instances):
            item = QListWidgetItem()
            row = QFrame()
            row.setObjectName("mo2InstanceRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 9, 10, 9)
            row_layout.setSpacing(14)


            art_label = QLabel()
            art_label.setObjectName("instanceArtwork")
            art_label.setFixedSize(180, 96)
            art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(art_label, 0, Qt.AlignmentFlag.AlignVCenter)

            details_column = QVBoxLayout()
            details_column.setSpacing(6)
            name = QLabel(instance.get("name") or "Unnamed MO2 instance")
            name.setObjectName("instanceName")
            details_column.addWidget(name)
            profile_name = instance.get("selected_profile") or "No profile selected"
            mode = "Portable instance" if instance.get("portable") else "MO2 instance"
            profile_meta_row = QHBoxLayout()
            profile_meta_row.setSpacing(8)
            meta = QLabel(f"◈  {mode}    ·    Profile: {profile_name}")
            meta.setObjectName("instanceMeta")
            profile_meta_row.addWidget(meta)
            profile_art_label = QLabel()
            profile_art_label.setObjectName("profileArtwork")
            profile_art_label.setFixedSize(34, 24)
            profile_art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            profile_meta_row.addWidget(profile_art_label)
            profile_meta_row.addStretch(1)
            details_column.addLayout(profile_meta_row)

            action_row = QHBoxLayout()
            launch = QPushButton("▶  Launch Modded Skyrim")
            launch.setObjectName("primaryAction")
            launch.setMinimumHeight(34)
            reason_getter = getattr(self._owner_window, "mo2_launch_block_reason", None)
            block_reason = (
                reason_getter(instance) if callable(reason_getter)
                else (None if self._launch_ready and instance.get("is_skyrim_se")
                      else "Wyrd's environment checks have not marked modded launch ready.")
            )
            launch.setEnabled(block_reason is None)
            launch.setToolTip(block_reason or "Launch Skyrim through this MO2 instance's virtual filesystem.")
            action_row.addWidget(launch)

            open_mo2 = QPushButton("Open MO2 Mod List")
            open_mo2.setMinimumHeight(34)
            open_mo2.setToolTip(
                "Open MO2 in Skyrim's existing Proton prefix to review or change the selected instance's mod list."
            )
            action_row.addWidget(open_mo2)

            open_folder = QPushButton("▱  Open Folder")
            open_folder.setMinimumHeight(34)
            open_folder.clicked.connect(
                lambda _checked=False, record=dict(instance): self._open_instance_folder(record)
            )
            action_row.addWidget(open_folder)
            action_row.addStretch(1)
            details_column.addLayout(action_row)
            row_layout.addLayout(details_column, 1)

            right_column = QVBoxLayout()
            readiness = QLabel("✓  Ready" if block_reason is None else "⚠  Check Required")
            readiness.setObjectName("launchReady" if block_reason is None else "launchBlocked")
            detail_getter = getattr(self._owner_window, "mo2_launch_status_detail", None)
            readiness.setToolTip(
                detail_getter(instance) if callable(detail_getter)
                else block_reason or "All required launch preflight checks currently pass."
            )
            right_column.addWidget(readiness, 0, Qt.AlignmentFlag.AlignRight)

            profile_picker = QComboBox()
            profile_picker.setObjectName("profilePicker")
            profile_names = list(instance.get("profiles") or [])
            if profile_names:
                profile_picker.addItems(profile_names)
                if profile_name in profile_names:
                    profile_picker.setCurrentText(profile_name)
                profile_picker.setToolTip(
                    "Choose the MO2 profile for this launch. Wyrd saves it as this instance's preferred profile."
                )
            else:
                profile_picker.addItem("No verified profiles")
                profile_picker.setEnabled(False)
                profile_picker.setToolTip(
                    "No profile folders could be safely verified for this instance. MO2's current profile will be used."
                )

            self._refresh_artwork_labels(instance, art_label, profile_art_label,
                                          profile_picker.currentText() if profile_picker.isEnabled() else None)
            profile_picker.currentTextChanged.connect(
                lambda selected, record=dict(instance), image=profile_art_label:
                    self._refresh_profile_artwork(record, selected, image)
            )

            artwork_button = QToolButton()
            artwork_button.setObjectName("artworkMenuButton")
            artwork_button.setText("Artwork  ▾")
            artwork_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            artwork_menu = QMenu(artwork_button)
            instance_menu = artwork_menu.addMenu("Instance artwork")
            profile_menu = artwork_menu.addMenu("Profile artwork")
            for menu, scope in ((instance_menu, "instance"), (profile_menu, "profile")):
                choose_action = menu.addAction("Choose image…")
                choose_action.triggered.connect(
                    lambda _checked=False, selected_scope=scope, record=dict(instance),
                           picker=profile_picker, instance_image=art_label, profile_image=profile_art_label:
                        self._choose_artwork(record, selected_scope, picker, instance_image, profile_image)
                )
                random_action = menu.addAction("Randomise artwork")
                random_action.triggered.connect(
                    lambda _checked=False, selected_scope=scope, record=dict(instance),
                           picker=profile_picker, instance_image=art_label, profile_image=profile_art_label:
                        self._randomize_artwork(record, selected_scope, picker, instance_image, profile_image)
                )
                reset_action = menu.addAction("Restore default")
                reset_action.triggered.connect(
                    lambda _checked=False, selected_scope=scope, record=dict(instance),
                           picker=profile_picker, instance_image=art_label, profile_image=profile_art_label:
                        self._reset_artwork(record, selected_scope, picker, instance_image, profile_image)
                )
            profile_menu.setEnabled(profile_picker.isEnabled())
            artwork_button.setMenu(artwork_menu)
            action_row.addWidget(artwork_button)

            open_mo2.clicked.connect(
                lambda _checked=False, record=dict(instance), picker=profile_picker:
                    self._open_mo2(
                        str(record.get("name") or ""),
                        picker.currentText() if picker.isEnabled() else None,
                    )
            )
            right_column.addStretch(1)
            right_column.addWidget(profile_picker)
            row_layout.addLayout(right_column)

            launch.clicked.connect(
                lambda _checked=False, record=dict(instance), picker=profile_picker:
                    self._launch_instance(record, picker.currentText() if picker.isEnabled() else None)
            )
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, instance)
            self.instances.addItem(item)
            self.instances.setItemWidget(item, row)
            if selected_path and instance.get("path") == selected_path:
                self.selected_instance_record = dict(instance)

        self.instances.setVisible(bool(instances))
        self.instance_count.setText(f"{len(inventory.get('skyrim_instances', []))} Instances")
        self.instances.setVisible(bool(instances))
        if instances:
            self.setup_status.setText("")
            self.setup_status.hide()
        else:
            self.setup_status.show()
            message = inventory.get("message", "No MO2 instances were found in the known locations.")
            roots = inventory.get("scan_roots", [])
            errors = inventory.get("errors", [])
            if roots:
                message += "\n\nLocations checked:\n" + "\n".join(roots)
            if errors:
                message += "\n\nRead errors:\n" + "\n".join(errors[:6])
            self.setup_status.setText(message)

    def _refresh_artwork_labels(self, instance: dict, instance_label: QLabel,
                                profile_label: QLabel, profile: str | None) -> None:
        self._set_artwork_pixmap(instance_label, get_artwork(instance, "instance"))
        if profile:
            self._set_artwork_pixmap(profile_label, get_artwork(instance, "profile", profile))
            profile_label.setToolTip(f"Artwork for profile: {profile}")
        else:
            profile_label.clear()
            profile_label.setToolTip("No verified profile artwork")

    @staticmethod
    def _set_artwork_pixmap(label: QLabel, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                label.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setPixmap(pixmap)
        else:
            label.clear()

    def _refresh_profile_artwork(self, instance: dict, profile: str,
                                 profile_label: QLabel) -> None:
        if profile:
            self._set_artwork_pixmap(profile_label, get_artwork(instance, "profile", profile))
            profile_label.setToolTip(f"Artwork for profile: {profile}")

    def _artwork_scope_profile(self, scope: str, picker: QComboBox) -> str | None:
        if scope != "profile":
            return None
        return picker.currentText() if picker.isEnabled() else None

    def _refresh_artwork_after_change(self, instance: dict, picker: QComboBox,
                                      instance_label: QLabel, profile_label: QLabel) -> None:
        profile = picker.currentText() if picker.isEnabled() else None
        self._refresh_artwork_labels(instance, instance_label, profile_label, profile)

    def _choose_artwork(self, instance: dict, scope: str, picker: QComboBox,
                        instance_label: QLabel, profile_label: QLabel) -> None:
        profile = self._artwork_scope_profile(scope, picker)
        if scope == "profile" and not profile:
            self.setup_status.setText("Choose a verified MO2 profile before assigning profile artwork.")
            self.setup_status.show()
            return
        source, _filter = QFileDialog.getOpenFileName(
            self, "Choose Wyrd artwork", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not source:
            return
        preview = QPixmap(source)
        if preview.isNull():
            self.setup_status.setText("That file could not be opened as an image.")
            self.setup_status.show()
            return
        try:
            set_custom_artwork(instance, scope, source, profile)
        except (OSError, ValueError) as error:
            self.setup_status.setText(f"Could not save artwork: {error}")
            self.setup_status.show()
            return
        self._refresh_artwork_after_change(instance, picker, instance_label, profile_label)

    def _randomize_artwork(self, instance: dict, scope: str, picker: QComboBox,
                           instance_label: QLabel, profile_label: QLabel) -> None:
        profile = self._artwork_scope_profile(scope, picker)
        if scope == "profile" and not profile:
            return
        randomize_artwork(instance, scope, profile)
        self._refresh_artwork_after_change(instance, picker, instance_label, profile_label)

    def _reset_artwork(self, instance: dict, scope: str, picker: QComboBox,
                       instance_label: QLabel, profile_label: QLabel) -> None:
        profile = self._artwork_scope_profile(scope, picker)
        if scope == "profile" and not profile:
            return
        reset_artwork(instance, scope, profile)
        self._refresh_artwork_after_change(instance, picker, instance_label, profile_label)

    def _open_selected_mo2(self) -> None:
        """Delegate to the established MainWindow Open MO2 workflow.

        Keep instance selection and launch validation in one place so the
        header action follows the same path as the original Environment action.
        """
        owner = self._owner_window
        if not self.mo2_installed:
            install_action = getattr(owner, "install_mo2", None)
            if callable(install_action):
                install_action()
            else:
                self.setup_status.setText("Wyrd's MO2 installer is unavailable. Refresh and try again.")
                self.setup_status.show()
            return
        launch_action = getattr(owner, "open_mo2_instance", None)
        if not callable(launch_action):
            self.setup_status.setText("Wyrd's Open MO2 action is unavailable. Refresh the environment and try again.")
            self.setup_status.show()
            log = getattr(owner, "_log", None)
            if callable(log):
                log("Open MO2 action failed: MainWindow.open_mo2_instance is unavailable.")
            return
        log = getattr(owner, "_log", None)
        if callable(log):
            log("Open MO2 header action clicked.")
        launch_action()

    def _open_mo2(self, instance_name: str, profile_name: str | None = None) -> None:
        """Open this instance and its currently selected profile in MO2."""
        launch = getattr(self._owner_window, "open_mo2_instance", None)
        if callable(launch):
            launch(instance_name=instance_name, profile_name=profile_name)

    def _launch_unmodded(self) -> None:
        launch = getattr(self._owner_window, "start_skyrim_direct", None)
        if callable(launch):
            launch()

    def _launch_instance(self, instance: dict, profile_name: str | None = None) -> None:
        if not instance.get("is_skyrim_se"):
            return

        instance_name = str(instance.get("name") or "<unnamed>")
        selected_profile = profile_name or "<MO2 saved selection>"
        message = (
            "[WYRD] Launch Modded Skyrim button clicked: "
            f"instance={instance_name!r}, profile={selected_profile!r}, "
            f"instance_path={instance.get('path')!r}"
        )
        # Visible in the terminal that started run.sh and in Wyrd's Maintenance log.
        print(message, flush=True)
        owner_log = getattr(self._owner_window, "_log", None)
        if callable(owner_log):
            owner_log(message)

        # Keep the click event alongside the Proton/MO2 launch output so a
        # click that fails preflight is still visible in the same diagnostic file.
        try:
            from proton_runtime import managed_paths
            diagnostics = managed_paths(Path.home())["root"] / "logs" / "skyrim-launch.log"
            diagnostics.parent.mkdir(parents=True, exist_ok=True)
            with diagnostics.open("a", encoding="utf-8") as log:
                log.write("\n=== WYRD UI ACTION ===\n")
                log.write(message + "\n")
        except (OSError, KeyError, TypeError) as error:
            warning = f"[WYRD] Could not append click event to launch log: {error}"
            print(warning, flush=True)
            if callable(owner_log):
                owner_log(warning)

        launch = getattr(self._owner_window, "start_skyrim", None)
        if callable(launch):
            launch(instance_name=instance_name, profile_name=profile_name)

    def _open_instance_folder(self, instance: dict) -> None:
        raw_path = instance.get("path")
        try:
            if not raw_path:
                raise OSError("The instance path is unavailable.")
            path = Path(raw_path).expanduser()
            if path.is_symlink() or not path.is_dir():
                raise OSError("The instance folder is missing or is a symlink.")
            resolved = path.resolve(strict=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved))):
                raise OSError("The desktop environment could not open the instance folder.")
        except (OSError, RuntimeError) as error:
            self.setup_status.setText(f"Could not open instance folder: {error}")

