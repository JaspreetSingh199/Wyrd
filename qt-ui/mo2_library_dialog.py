"""Read-only MO2 environment inventory widgets.

Wyrd discovers MO2 instances from the live filesystem. It does not maintain
an instance registry, claim ownership of external installations, or edit
MO2 storage configuration.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QFrame,
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
        self.open_profiles_button = QPushButton("Open profiles folder")
        self.open_profiles_button.setToolTip(
            "Open the profiles directory configured by the selected MO2 instance."
        )
        self.open_profiles_button.setEnabled(False)
        self.open_profiles_button.clicked.connect(self._open_profiles_folder)
        actions.addWidget(self.open_profiles_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.instances = QListWidget()
        self.instances.setMinimumHeight(55)
        self.instances.setMaximumHeight(150)
        self.instances.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.instances.currentRowChanged.connect(self._show_selected)
        layout.addWidget(self.instances)

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

    def _show_inventory(self):
        inventory = getattr(self._owner_window, "instance_state", {}) or {}
        instances = inventory.get("instances", [])
        selected_path = (
            self.selected_instance_record.get("path")
            if self.selected_instance_record else None
        )
        self.instances.blockSignals(True)
        self.instances.clear()
        selected_row = -1
        for instance in instances:
            item = QListWidgetItem(_instance_label(instance))
            item.setData(Qt.ItemDataRole.UserRole, instance)
            self.instances.addItem(item)
            if selected_path and instance.get("path") == selected_path:
                selected_row = self.instances.count() - 1
        self.instances.blockSignals(False)
        if selected_row >= 0:
            self.instances.setCurrentRow(selected_row)
        elif instances and self.instances.currentRow() < 0:
            self.instances.setCurrentRow(0)
        self.instances.setVisible(bool(instances))
        if instances:
            self.setup_status.setText(
                f"Found {len(instances)} MO2 instance(s); "
                f"{len(inventory.get('skyrim_instances', []))} match Skyrim SE. "
                "This is a live, read-only scan—no registration step is needed."
            )
        else:
            message = inventory.get("message", "No MO2 instances were found in the known locations.")
            roots = inventory.get("scan_roots", [])
            errors = inventory.get("errors", [])
            if roots:
                message += "\n\nLocations checked:\n" + "\n".join(roots)
            if errors:
                message += "\n\nRead errors:\n" + "\n".join(errors[:6])
            self.setup_status.setText(message)

    def _open_profiles_folder(self) -> None:
        """Open the selected instance's configured MO2 profiles directory."""
        instance = self.selected_instance_record or {}
        profiles = instance.get("storage", {}).get("profiles", {})
        raw_path = profiles.get("path")
        if not raw_path:
            self.setup_status.setText("The selected MO2 instance has no verified profiles directory.")
            self.open_profiles_button.setEnabled(False)
            return
        try:
            path = Path(raw_path).expanduser()
            if path.is_symlink() or not path.is_dir():
                raise OSError("The configured profiles directory is missing or is a symlink.")
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise OSError("The configured profiles path is not a directory.")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved))):
                raise OSError("The desktop environment could not open the profiles directory.")
        except (OSError, RuntimeError) as error:
            self.setup_status.setText(f"Could not open profiles folder: {error}")

    def _show_selected(self, row: int):
        item = self.instances.item(row) if row >= 0 else None
        instance = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selected_instance_record = dict(instance) if isinstance(instance, dict) else None
        profiles = (
            (self.selected_instance_record or {}).get("storage", {}).get("profiles", {})
        )
        profiles_path = Path(profiles.get("path", "")) if profiles.get("path") else None
        profiles_available = bool(
            profiles_path and not profiles_path.is_symlink()
            and profiles_path.is_dir()
        )
        self.open_profiles_button.setEnabled(profiles_available)
        if self.selected_instance_record:
            self.setup_status.setText(
                f"Selected: {_instance_label(self.selected_instance_record)}. "
                "Wyrd reads this instance directly and leaves its MO2 data in place."
            )
