"""Read-only MO2 environment inventory widgets.

Wyrd discovers MO2 instances from the live filesystem. It does not maintain
an instance registry, claim ownership of external installations, or edit
MO2 storage configuration.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QFrame, QComboBox,
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
        self.preferred_instance_id = selected_instance_id
        self.discovery: dict = {}
        self.selected_instance_record: dict | None = None
        self.on_launch_requested = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        heading = QLabel("SKYRIM ENVIRONMENTS")
        heading.setObjectName("section")
        layout.addWidget(heading)
        self.setup_status = QLabel("MO2 instances are discovered automatically.")
        self.setup_status.setObjectName("muted")
        self.setup_status.setWordWrap(True)
        layout.addWidget(self.setup_status)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh instances")
        self.refresh_button.setToolTip("Rescan the detected MO2 installation and known instance locations.")
        self.refresh_button.clicked.connect(self.discover)
        actions.addWidget(self.refresh_button)
        self.launch_mo2_button = QPushButton("Open MO2")
        self.launch_mo2_button.setToolTip(
            "Open MO2's native instance manager in Skyrim's existing Proton prefix."
        )
        self.launch_mo2_button.clicked.connect(self._open_mo2)
        actions.addWidget(self.launch_mo2_button)
        self.launch_unmodded_button = QPushButton("Launch Unmodded")
        self.launch_unmodded_button.setToolTip(
            "Launch Skyrim directly through Steam without MO2 or mods."
        )
        self.launch_unmodded_button.clicked.connect(self._launch_unmodded)
        actions.addWidget(self.launch_unmodded_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.instances = QListWidget()
        self.instances.setMinimumHeight(90)
        self.instances.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.instances)
        self._launch_ready = False

    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path=""):
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
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
        selected_path = (
            self.selected_instance_record.get("path")
            if self.selected_instance_record else None
        )
        self.instances.clear()
        self.selected_instance_record = None
        for instance in instances:
            item = QListWidgetItem()
            row = QFrame()
            row.setObjectName("mo2InstanceRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(8)

            launch = QPushButton("Launch Modded")
            launch.setObjectName("primaryAction")
            launch.setMinimumHeight(38)
            reason_getter = getattr(self._owner_window, "mo2_launch_block_reason", None)
            block_reason = (
                reason_getter(instance) if callable(reason_getter)
                else (None if self._launch_ready and instance.get("is_skyrim_se")
                      else "Wyrd's environment checks have not marked modded launch ready.")
            )
            launch.setEnabled(block_reason is None)
            launch.setToolTip(
                block_reason or "Launch Skyrim through this MO2 instance's virtual filesystem."
            )
            row_layout.addWidget(launch)

            open_folder = QPushButton("Open folder")
            open_folder.setMinimumHeight(38)
            open_folder.setToolTip("Open this MO2 instance's folder.")
            open_folder.clicked.connect(
                lambda _checked=False, record=dict(instance): self._open_instance_folder(record)
            )
            row_layout.addWidget(open_folder)

            details = QLabel(_instance_label(instance))
            details.setWordWrap(True)
            details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row_layout.addWidget(details, 1)
            readiness = QLabel("READY" if block_reason is None else "BLOCKED · HOVER")
            readiness.setObjectName("launchReady" if block_reason is None else "launchBlocked")
            readiness.setToolTip(block_reason or "All launch preflight checks currently pass.")
            row_layout.addWidget(readiness)

            profile_picker = QComboBox()
            profile_picker.setObjectName("profilePicker")
            profile_names = list(instance.get("profiles") or [])
            if profile_names:
                profile_picker.addItems(profile_names)
                current_profile = instance.get("selected_profile") or ""
                if current_profile in profile_names:
                    profile_picker.setCurrentText(current_profile)
                profile_picker.setToolTip(
                    "Choose the MO2 profile to use for this launch. This does not change MO2's saved profile."
                )
            else:
                profile_picker.addItem("No verified profiles")
                profile_picker.setEnabled(False)
                profile_picker.setToolTip(
                    "No profile folders could be safely verified for this instance. MO2's current profile will be used."
                )
            profile_label = QLabel("Profile")
            profile_label.setObjectName("muted")
            row_layout.addWidget(profile_label)
            profile_picker.setMinimumWidth(150)
            row_layout.addWidget(profile_picker)
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
        if instances:
            self.setup_status.setText(
                f"Found {len(instances)} MO2 instance(s); "
                f"{len(inventory.get('skyrim_instances', []))} match Skyrim SE. "
                "Each Skyrim instance has its own launch action."
            )
        else:
            message = inventory.get("message", "No MO2 instances were found in the known locations.")
            roots = inventory.get("scan_roots", [])
            errors = inventory.get("errors", [])
            if roots:
                message += "\\n\\nLocations checked:\\n" + "\\n".join(roots)
            if errors:
                message += "\\n\\nRead errors:\\n" + "\\n".join(errors[:6])
            self.setup_status.setText(message)

    def _open_mo2(self) -> None:
        launch = getattr(self._owner_window, "launch_mo2_gui", None)
        if callable(launch):
            launch()

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
            "[WYRD] Launch Modded button clicked: "
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

