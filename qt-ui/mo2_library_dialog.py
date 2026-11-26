"""Read-only MO2 environment inventory widgets.

Wyrd discovers MO2 instances from the live filesystem. It does not maintain
an instance registry, claim ownership of external installations, or edit
MO2 storage configuration.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
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
        refresh = getattr(self.parent(), "refresh_all", None)
        if callable(refresh):
            refresh()
        self._show_inventory()

    def _show_inventory(self):
        inventory = getattr(self.parent(), "instance_state", {}) or {}
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

    def _show_selected(self, row: int):
        item = self.instances.item(row) if row >= 0 else None
        instance = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selected_instance_record = dict(instance) if isinstance(instance, dict) else None
        if self.selected_instance_record:
            self.setup_status.setText(
                f"Selected: {_instance_label(self.selected_instance_record)}. "
                "Wyrd reads this instance directly and leaves its MO2 data in place."
            )


class Mo2AdvancedSettings(QWidget):
    """Read-only MO2 paths and instance inventory for the Advanced tab."""

    def __init__(self, parent=None, *, selected_instance_id=""):
        super().__init__(parent)
        self.setObjectName("mo2AdvancedSettings")
        self.preferred_instance_id = selected_instance_id
        self.selected_instance_record: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        heading = QLabel("MO2 INSPECTION")
        heading.setObjectName("section")
        root.addWidget(heading)
        intro = QLabel(
            "Wyrd inspects the existing MO2 setup. Installation ownership, "
            "registration, storage editing and instance metadata are not managed here."
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        locations = QGroupBox("Detected locations (read-only)")
        form = QFormLayout(locations)
        self.prefix_edit = QLabel("Not detected")
        self.install_edit = QLabel("Not detected")
        self.game_path_edit = QLabel("Not detected")
        self.proton_edit = QLabel("Not selected")
        for field in (self.prefix_edit, self.install_edit, self.game_path_edit, self.proton_edit):
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            field.setWordWrap(True)
        form.addRow("Skyrim Steam prefix:", self.prefix_edit)
        form.addRow("MO2 installation:", self.install_edit)
        form.addRow("Skyrim game:", self.game_path_edit)
        form.addRow("Proton runtime:", self.proton_edit)
        root.addWidget(locations)

        self.detected_instances = QListWidget()
        self.detected_instances.setMinimumHeight(100)
        self.detected_instances.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.detected_instances.currentRowChanged.connect(self._select_detected_instance)
        root.addWidget(self.detected_instances)
        self.detected_instances_summary = QLabel("Instances are scanned automatically.")
        self.detected_instances_summary.setObjectName("muted")
        self.detected_instances_summary.setWordWrap(True)
        root.addWidget(self.detected_instances_summary)
        refresh = QPushButton("Refresh MO2 scan")
        refresh.clicked.connect(self._refresh_parent_mo2_scan)
        root.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self.update_context()
        self._refresh_registered()

    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path=""):
        self.prefix_edit.setText(str(prefix_path or "Not detected"))
        self.install_edit.setText(str(install_path or "Not detected"))
        self.game_path_edit.setText(str(game_path or "Not detected"))
        self.proton_edit.setText(str(proton_path or "Not selected"))

    def _select_detected_instance(self, row: int) -> None:
        item = self.detected_instances.item(row) if row >= 0 else None
        instance = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selected_instance_record = dict(instance) if isinstance(instance, dict) else None

    def _refresh_registered(self, _index: int = -1) -> None:
        """Compatibility name retained for the caller; refreshes live inventory only."""
        self.detected_instances.clear()
        inventory = getattr(self.parent(), "instance_state", {}) or {}
        instances = inventory.get("instances", [])
        for instance in instances:
            item = QListWidgetItem(_instance_label(instance))
            item.setData(Qt.ItemDataRole.UserRole, instance)
            self.detected_instances.addItem(item)
        if instances:
            self.detected_instances.setCurrentRow(0)
            self.detected_instances_summary.setText(
                f"{len(instances)} instance(s) found; "
                f"{len(inventory.get('skyrim_instances', []))} match Skyrim SE. "
                "Live filesystem inventory; no Wyrd registry is involved."
            )
        else:
            self.detected_instances_summary.setText(
                inventory.get("message", "No MO2 instances were found in the known locations.")
            )

    def _refresh_parent_mo2_scan(self) -> None:
        refresh = getattr(self.parent(), "refresh_all", None)
        if callable(refresh):
            refresh()
        self._refresh_registered()
