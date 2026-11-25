"""MO2 Environment picker and Advanced configuration widgets."""
from __future__ import annotations

import configparser
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QInputDialog, QVBoxLayout, QWidget, QStackedWidget,
)

from wyrd_mo2 import _runtime_environment, managed_paths, _steam_root_for_game
from wyrd_mo2_registry_service import RegistryService


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


class Mo2LibraryDialog(QWidget):
    """Compact Environment widget for finding and selecting MO2 instances."""

    def __init__(self, parent=None, *, prefix_path="", install_path="", game_path="", proton_path="", selected_instance_id=""):
        super().__init__(parent)
        self.setObjectName("mo2InstancePicker")
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
        self.preferred_instance_id = selected_instance_id
        self.discovery: dict = {}
        self.selected_instance_record: dict | None = None
        self.selection_changed = False
        self.on_registry_changed = None
        self.on_launch_requested = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.intro = QLabel("Your Skyrim environments. Each one keeps its own MO2 instance and mod setup.")
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.setup_status = QLabel()
        self.setup_status.setObjectName("muted")
        self.setup_status.setWordWrap(True)
        layout.addWidget(self.setup_status)

        self.discover_button = QPushButton("+ Add environment")
        self.discover_button.setToolTip(
            "Open Mod Organizer 2's Instance Manager using the selected Proton runtime."
        )
        self.discover_button.clicked.connect(self.create_environment)
        self.connect_existing_button = QPushButton("Connect existing instance")
        self.connect_existing_button.clicked.connect(self.discover)
        environment_entry_actions = QHBoxLayout()
        environment_entry_actions.setSpacing(8)
        environment_entry_actions.addWidget(self.discover_button)
        environment_entry_actions.addWidget(self.connect_existing_button)
        environment_entry_actions.addStretch(1)
        layout.addLayout(environment_entry_actions)

        self.instances = QListWidget()
        self.instances.setMinimumHeight(55)
        self.instances.setMaximumHeight(120)
        self.instances.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.instances.currentRowChanged.connect(self._show_selected)
        self.instances.hide()
        layout.addWidget(self.instances)

        # Discovered MO2 instances are used directly; no Wyrd registration step is needed.

    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path=""):
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
        self._show_inventory()

    def discover(self):
        parent = self.parent()
        refresh = getattr(parent, "refresh_all", None)
        if callable(refresh):
            refresh()
        self._show_inventory()

    def _show_inventory(self):
        self.instances.clear()
        inventory = getattr(self.parent(), "instance_state", {}) or {}
        instances = inventory.get("skyrim_instances", [])
        for instance in instances:
            name = instance.get("name", "Unnamed MO2 instance")
            profile = instance.get("selected_profile") or "No profile selected"
            row = QListWidgetItem(f"{name} · {profile}")
            row.setData(Qt.ItemDataRole.UserRole, instance)
            self.instances.addItem(row)
        self.instances.setVisible(bool(instances))
        if instances:
            self.setup_status.setText(
                f"Automatically discovered {len(instances)} Skyrim MO2 instance(s). "
                "Select one to inspect it; no Wyrd registration is needed."
            )
        else:
            self.setup_status.setText(
                inventory.get("message", "No Skyrim Special Edition MO2 instances were found.")
            )

    def _show_selected(self, row: int):
        item = self.instances.item(row) if row >= 0 else None
        proposal = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selected_instance_record = dict(proposal) if isinstance(proposal, dict) else None
        if self.selected_instance_record:
            self.setup_status.setText(
                f"Discovered: {self.selected_instance_record.get('name') or 'Skyrim Special Edition'} · "
                f"{self.selected_instance_record.get('selected_profile') or 'No profile selected'}. "
                "No Wyrd registration is required."
            )



class Mo2AdvancedSettings(QWidget):
    """Advanced MO2 maintenance and storage settings, hosted by Wyrd's parent Advanced tab."""

    def __init__(self, parent=None, *, selected_instance_id=""):
        super().__init__(parent)
        self.setObjectName("mo2AdvancedSettings")
        self.service = RegistryService(managed_paths()["config"] / "mo2-registry.json")
        self.preferred_instance_id = selected_instance_id
        self.selected_instance_record = None
        self.associations_updated = False
        self.advanced_page = self
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        heading = QLabel("MO2 SETTINGS")
        heading.setObjectName("section")
        intro = QLabel("Pick a section on the left. Most Skyrim setups only need the Environment page.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(intro)

        self.category_selector = QComboBox()
        self.category_selector.setObjectName("mo2AdvancedCategories")
        self.category_selector.setMinimumHeight(36)
        self.category_selector.setMaximumWidth(280)
        self.category_pages = QStackedWidget()
        # Keep the selected settings page close to its contents. Letting the
        # Paths page stretch vertically pushed diagnostics far below the fold.
        self.category_pages.setMinimumHeight(250)
        self.category_pages.setMaximumHeight(390)
        root.addWidget(self.category_selector, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.category_pages, 0)

        def add_category(title, page, description):
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(14, 12, 14, 12)
            page_layout.setSpacing(10)
            label = QLabel(title.upper())
            label.setObjectName("section")
            help_text = QLabel(description)
            help_text.setObjectName("muted")
            help_text.setWordWrap(True)
            page_layout.addWidget(label)
            page_layout.addWidget(help_text)
            self.category_selector.addItem(title)
            self.category_pages.addWidget(page)
            return page_layout

        locations_page = QWidget()
        locations_layout = add_category(
            "Paths",
            locations_page,
            "Read-only locations detected by Wyrd. Change game and Proton associations from the Instances page."
        )
        paths_box = QGroupBox("Detected locations")
        form = QFormLayout(paths_box)
        self.prefix_edit = QLineEdit()
        self.install_edit = QLineEdit()
        self.game_path_edit = QLineEdit()
        self.proton_edit = QLineEdit()
        for field in (self.prefix_edit, self.install_edit, self.game_path_edit, self.proton_edit):
            field.setMinimumHeight(30)
            field.setReadOnly(True)
            field.setCursorPosition(0)
        self.prefix_edit.setPlaceholderText("No Skyrim Steam compatdata prefix detected")
        self.install_edit.setPlaceholderText("No MO2 installation detected")
        self.game_path_edit.setPlaceholderText("No Skyrim game folder detected")
        self.proton_edit.setPlaceholderText("No Proton runtime selected")
        form.addRow("Skyrim Steam compatdata:", self.prefix_edit)
        form.addRow("MO2 installation:", self.install_edit)
        form.addRow("Skyrim game:", self.game_path_edit)
        form.addRow("Proton runtime:", self.proton_edit)
        locations_layout.addWidget(paths_box)
        locations_layout.addStretch(1)

        instances_page = QWidget()
        instances_layout = add_category(
            "Detected instances",
            instances_page,
            "Read-only inventory from the MO2 installation Wyrd discovered. No Wyrd registration or connection records are needed."
        )
        self.detected_instances = QListWidget()
        self.detected_instances.setMinimumHeight(120)
        self.detected_instances.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        instances_layout.addWidget(self.detected_instances, 1)
        self.detected_instances_summary = QLabel("Instances are scanned automatically.")
        self.detected_instances_summary.setObjectName("muted")
        self.detected_instances_summary.setWordWrap(True)
        instances_layout.addWidget(self.detected_instances_summary)
        refresh_detected = QPushButton("Refresh MO2 scan")
        refresh_detected.clicked.connect(self._refresh_parent_mo2_scan)
        instances_layout.addWidget(refresh_detected, 0, Qt.AlignmentFlag.AlignLeft)

        storage_page = QWidget()
        storage_layout = add_category(
            "Storage",
            storage_page,
            "Configure this instance's storage locations. Shared-folder controls are hidden in V1; existing settings are preserved."
        )
        storage_box=QGroupBox("Storage folders")
        storage_form=QFormLayout(storage_box)
        self.storage_fields={}; self.storage_browse_buttons={}
        for key,label in (("base","Base"),("mods","Mods"),("downloads","Downloads"),("cache","Cache"),("profiles","Profiles"),("overwrite","Overwrite")):
            row=QHBoxLayout()
            field=QLineEdit()
            field.setReadOnly(True)
            browse=QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, category=key: self._choose_storage_path(category))
            row.addWidget(field,1)
            row.addWidget(browse)
            add_info_button(row, self, f"Choose {label} folder", f"Select the {label.lower()} storage directory MO2 should use for this instance. Saving changes the instance's ModOrganizer.ini and creates only explicitly approved missing empty folders. Existing files are never moved, copied, merged, or deleted.")
            self.storage_fields[key]=field
            self.storage_browse_buttons[key]=browse
            storage_form.addRow(label,row)
        storage_layout.addWidget(storage_box)
        self.share_mods_check=QCheckBox("Share Mods folder with other instances")
        self.share_downloads_check=QCheckBox("Share Downloads folder with other instances")
        self.create_storage_check=QCheckBox("Create missing empty folders when saving")
        # Sharing is intentionally not exposed in V1. Keep existing values loaded
        # so saving other paths does not silently rewrite a prior sharing policy.
        self.share_mods_check.hide()
        self.share_downloads_check.hide()
        self.share_mods_check.setEnabled(False)
        self.share_downloads_check.setEnabled(False)
        storage_layout.addWidget(self.create_storage_check)
        storage_actions=QHBoxLayout()
        self.apply_storage_button=QPushButton("Save storage settings")
        self.restore_storage_button=QPushButton("Restore INI backup…")
        self.apply_storage_button.clicked.connect(self.apply_storage)
        self.restore_storage_button.clicked.connect(self.restore_config_backup)
        storage_actions.addWidget(self.apply_storage_button)
        add_info_button(storage_actions, self, "Save storage settings", "Writes the selected storage paths to this managed instance's ModOrganizer.ini after validation and confirmation. Wyrd backs up the INI first. Existing mod, download, profile and overwrite data is never moved, copied, merged, or deleted; changing paths can make existing content appear missing if the chosen folders are not already prepared.")
        storage_actions.addWidget(self.restore_storage_button)
        add_info_button(storage_actions, self, "Restore INI backup", "Restores a previously created Wyrd backup of this instance's ModOrganizer.ini. Wyrd backs up the current INI before restoring. This changes configuration only; it does not restore, move, or delete mod files.")
        storage_layout.addLayout(storage_actions)
        storage_layout.addStretch(1)

        self.category_selector.currentIndexChanged.connect(self.category_pages.setCurrentIndex)
        self.category_selector.setCurrentIndex(0)
        self._refresh_detected_instances()


    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path=""):
        self.prefix_edit.setText(str(prefix_path or ""))
        self.install_edit.setText(str(install_path or ""))
        self.game_path_edit.setText(str(game_path or ""))
        self.proton_edit.setText(str(proton_path or ""))



    def _registry_maps(self):
        registry = self.service.load()
        return (
            registry,
            {x["id"]: x for x in registry["installations"]},
            {x["id"]: x for x in registry["games"]},
            {x["id"]: x for x in registry["runtimes"]},
        )

    def _refresh_registered(self, _index: int = -1) -> None:
        """Compatibility hook: refresh the live MO2 inventory, not registrations."""
        self._refresh_detected_instances()

    def _refresh_parent_mo2_scan(self) -> None:
        parent = self.parent()
        refresh = getattr(parent, "refresh_all", None)
        if callable(refresh):
            refresh()
        self._refresh_detected_instances()

    def _refresh_detected_instances(self) -> None:
        widget = getattr(self, "detected_instances", None)
        summary = getattr(self, "detected_instances_summary", None)
        if widget is None or summary is None:
            return
        widget.clear()
        parent = self.parent()
        inventory = getattr(parent, "instance_state", {}) or {}
        instances = inventory.get("skyrim_instances", [])
        for instance in instances:
            name = instance.get("name", "Unnamed MO2 instance")
            profile = instance.get("selected_profile") or "No profile selected"
            widget.addItem(f"{name} · {profile}")
        if instances:
            summary.setText(
                f"Automatically discovered {len(instances)} Skyrim Special Edition instance(s). "
                "The scan reads MO2 directly; it does not create Wyrd registration records."
            )
        else:
            summary.setText(
                inventory.get("message", "No Skyrim Special Edition instances were found in the detected MO2 installation.")
            )


    def _select_registered(self,row):
        item=self.registered_instances.item(row) if row>=0 else None
        record=item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record,dict):
            self.selected_instance_record=None
            return
        self.selected_instance_record=dict(record); self.preferred_instance_id=record.get("id","")
        self._edit_associations_button.setEnabled(True); self.rename_instance_button.setEnabled(True); self.unregister_instance_button.setEnabled(True)
        callback = getattr(self, "on_registry_changed", None)
        if callable(callback):
            callback(self.selected_instance_record, False)
        registry,installations,games,runtimes=self._registry_maps()
        installation=installations[record["installation_id"]]
        paths=record.get("storage_paths",{})
        base=Path(paths.get("base",{}).get("path") or record.get("instance_path","")).expanduser()
        defaults={"base":base,"mods":base/"mods","downloads":base/"downloads","cache":base/"webcache","profiles":base/"profiles","overwrite":base/"overwrite"}
        for category,field in self.storage_fields.items():
            field.setText(str(paths.get(category,{}).get("path") or defaults[category]))
        policies=record.get("storage_policy",{})
        self.share_mods_check.setChecked(policies.get("mods")=="shared")
        self.share_downloads_check.setChecked(policies.get("downloads")=="shared")
        writable=bool(record.get("config")) and installation.get("owner")=="wyrd"
        self.apply_storage_button.setEnabled(writable); self.restore_storage_button.setEnabled(writable and bool(record.get("config_backups")))
        for button in self.storage_browse_buttons.values(): button.setEnabled(writable)
        self.create_storage_check.setEnabled(writable)
        self.associations_updated=False


    def rename_selected_instance(self) -> None:
        """Rename only Wyrd's display label; never rename MO2 directories or profiles."""
        record = self.selected_instance_record
        if not record:
            return
        current = record.get("display_name", "")
        name, accepted = QInputDialog.getText(
            self, "Rename MO2 instance",
            "Display name in Wyrd (leave blank to use the game's name):",
            QLineEdit.EchoMode.Normal, current,
        )
        if not accepted:
            return
        name = name.strip()
        try:
            self.service.rename_instance(record["id"], name)
            updated = dict(record)
            if name:
                updated["display_name"] = name
            else:
                updated.pop("display_name", None)
            self.selected_instance_record = updated
            self._refresh_registered()
            QMessageBox.information(self, "Name updated", "The name shown in Wyrd was updated. MO2 files were not changed.")
        except Exception as error:
            QMessageBox.warning(self, "Rename MO2 instance", str(error))

    def unregister_selected_instance(self) -> None:
        """Forget a selected instance in Wyrd without changing its files."""
        record = self.selected_instance_record
        if not record:
            return
        answer = QMessageBox.question(
            self, "Unregister MO2 instance",
            "Remove this instance from Wyrd's registry?\n\n"
            "The MO2 instance, profiles, mods, downloads, saves, Proton prefix and installation "
            "will remain exactly where they are. This action only removes Wyrd metadata.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.unregister_instance(record["id"])
            if self.preferred_instance_id == record["id"]:
                self.preferred_instance_id = ""
            self.selected_instance_record = None
            self.selection_changed = True
            self._refresh_registered()
            callback = getattr(self, "on_registry_changed", None)
            if callable(callback):
                callback(None, True)
            callback = getattr(self, "on_registry_changed", None)
            if callable(callback):
                callback(None, True)
        except Exception as error:
            QMessageBox.warning(self, "Unregister MO2 instance", str(error))

    def unregister_selected_installation(self) -> None:
        """Forget an unused installation reference, never its on-disk files."""
        installation_id = self.installation_filter.currentData()
        if not installation_id:
            return
        try:
            registry, installations, _games, _runtimes = self._registry_maps()
            installation = installations.get(installation_id)
            if not installation:
                raise ValueError("Selected installation is no longer registered.")
            references = [item for item in registry["instances"]
                          if item.get("installation_id") == installation_id]
            if references:
                raise ValueError(
                    f"This installation still has {len(references)} registered instance(s). "
                    "Unregister those instance references first."
                )
            answer = QMessageBox.question(
                self, "Unregister MO2 installation",
                f"Remove this unused installation reference from Wyrd?\n\n"
                f"{installation.get('executable')}\n\n"
                "The MO2 executable, installation directory, Proton prefix and all data remain untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.service.unregister_installation(installation_id)
            self.preferred_instance_id = ""
            self.selected_instance_record = None
            self.selection_changed = True
            self._refresh_registered()
            self._refresh_registered()
        except Exception as error:
            QMessageBox.warning(self, "Unregister MO2 installation", str(error))

    def _choose_storage_path(self, category: str) -> None:
        current = self.storage_fields[category].text()
        selected = QFileDialog.getExistingDirectory(
            self, f"Select {category} directory", current if current and current != "Not recorded" else ""
        )
        if selected:
            self.storage_fields[category].setText(selected)


    @staticmethod
    def _restore_ini_atomically(backup: Path, destination: Path) -> None:
        """Restore a saved INI with an atomic same-directory replacement."""
        fd, temporary = tempfile.mkstemp(
            prefix=".wyrd-ini-rollback-", suffix=".ini", dir=str(destination.parent)
        )
        try:
            with os.fdopen(fd, "wb") as target, backup.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _backup_instance_ini(self, record: dict, config: Path) -> Path:
        """Back up only the instance INI; never copy or migrate mod data."""
        from datetime import datetime
        import shutil

        backup_root = managed_paths()["config"] / "mo2-config-backups"
        if backup_root.is_symlink():
            raise ValueError("The Wyrd INI backup directory is a symlink; backup creation refused.")
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_root / f"{record['id']}-{stamp}.ini"
        shutil.copy2(config, backup)
        record.setdefault("config_backups", []).append(str(backup))
        return backup

    def restore_config_backup(self) -> None:
        record = self.selected_instance_record
        if not record:
            return
        try:
            _registry, installations, _games, _runtimes = self._registry_maps()
            if installations[record["installation_id"]].get("owner") != "wyrd":
                raise ValueError("External MO2 configuration is read-only in Wyrd.")
            backups = record.get("config_backups", [])
            if not backups:
                raise ValueError("No Wyrd-created ModOrganizer.ini backups are recorded.")
            backup_root = managed_paths()["config"] / "mo2-config-backups"
            if backup_root.is_symlink() or not backup_root.is_dir():
                raise ValueError("The Wyrd INI backup directory is missing or unsafe.")
            choices = [Path(value).expanduser() for value in backups]
            labels = [f"{path.name} — {path}" for path in reversed(choices)]
            selected, accepted = QInputDialog.getItem(
                self, "Choose INI backup", "Restore which Wyrd-created backup?",
                labels, 0, False,
            )
            if not accepted or not selected:
                return
            backup = next(path for path in choices if f"{path.name} — {path}" == selected)
            config = Path(record.get("config", "")).expanduser()
            if backup.is_symlink() or not backup.is_file():
                raise ValueError("The selected INI backup is missing or unsafe.")
            resolved_backup = backup.resolve(strict=True)
            resolved_backup_root = backup_root.resolve(strict=True)
            if resolved_backup.parent != resolved_backup_root:
                raise ValueError("The selected backup is outside the managed INI backup directory.")
            if config.is_symlink() or not config.is_file():
                raise ValueError("The current ModOrganizer.ini is missing or unsafe.")
            answer = QMessageBox.question(
                self, "Restore ModOrganizer.ini",
                f"Restore this Wyrd backup?\n\n{backup}\n\n"
                "This restores configuration only. It does not restore, move, or delete mod files.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            current_backup = self._backup_instance_ini(record, config)
            fd, temporary = tempfile.mkstemp(prefix=".wyrd-restore-", suffix=".ini", dir=str(config.parent))
            try:
                with os.fdopen(fd, "wb") as handle, backup.open("rb") as source:
                    handle.write(source.read())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, config)
            except Exception:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            try:
                self.service.replace_instance(record)
            except Exception:
                # A failed registry write must not leave the restored INI paired
                # with stale Wyrd metadata.
                self._restore_ini_atomically(current_backup, config)
                raise
            self._refresh_registered()
            QMessageBox.information(
                self, "INI restored",
                f"Configuration restored. The pre-restore INI was backed up to:\n{current_backup}"
            )
        except Exception as error:
            QMessageBox.warning(self, "Restore INI backup", str(error))

    def apply_storage(self) -> None:
        """Apply explicitly approved storage roots; never migrate or merge files."""
        record = self.selected_instance_record
        if not record:
            return
        created: list[Path] = []
        try:
            registry, installations, _games, _runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            if installation.get("owner") != "wyrd":
                raise ValueError("External MO2 is read-only. Wyrd will not rewrite its ModOrganizer.ini.")
            config = Path(record.get("config", "")).expanduser()
            if config.is_symlink() or not config.is_file():
                raise ValueError("The instance ModOrganizer.ini is missing or symlinked.")

            categories = ("base", "mods", "downloads", "cache", "profiles", "overwrite")
            ini_keys = {
                "base": "base_directory", "mods": "mod_directory",
                "downloads": "download_directory", "cache": "cache_directory",
                "profiles": "profiles_directory", "overwrite": "overwrite_directory",
            }
            chosen: dict[str, Path] = {}
            missing: list[tuple[str, Path]] = []
            for category in categories:
                raw = self.storage_fields[category].text().strip()
                if not raw or raw == "Not recorded":
                    raise ValueError(f"Choose a path for {category.title()}.")
                path = Path(raw).expanduser()
                if path.is_symlink():
                    raise ValueError(f"{category.title()} path is a symlink; refusing to use it.")
                if path.exists() and not path.is_dir():
                    raise ValueError(f"{category.title()} path exists but is not a directory.")
                if not path.exists():
                    missing.append((category, path))
                else:
                    chosen[category] = path.resolve(strict=True)

            if missing and not self.create_storage_check.isChecked():
                names = ", ".join(f"{name}: {path}" for name, path in missing)
                raise ValueError(
                    "These storage directories do not exist. Enable 'Create missing empty storage folders' "
                    f"to create only empty directories, or choose existing folders:\n{names}"
                )
            for category, path in missing:
                if path.parent.is_symlink() or not path.parent.is_dir():
                    raise ValueError(f"Parent of {category.title()} must exist and must not be a symlink: {path.parent}")

            # Existing default roots are kept, and every category has its own
            # independent path. Profiles and Overwrite may never overlap another instance.
            for index, category in enumerate(categories):
                for previous in categories[:index]:
                    if chosen.get(category) and chosen.get(category) == chosen.get(previous):
                        raise ValueError(f"{category.title()} and {previous.title()} cannot use the same directory.")

            shared = {
                "mods": self.share_mods_check.isChecked(),
                "downloads": self.share_downloads_check.isChecked(),
            }
            for category, enabled in shared.items():
                if enabled and category not in chosen:
                    raise ValueError(f"Select an existing {category.title()} directory before opting into sharing.")
            if any(shared.values()):
                summary = "\n".join(
                    f"{category.title()}: {chosen[category]}"
                    for category, enabled in shared.items() if enabled
                )
                answer = QMessageBox.warning(
                    self, "Confirm shared storage",
                    "Shared storage can cause mod/download collisions and cross-instance interference. "
                    "Wyrd will not merge, deduplicate, lock, or reconcile contents. Every participating "
                    "instance must separately opt in.\n\n" + summary
                    + "\n\nContinue only if these instances are deliberately sharing the same data roots.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            collisions: list[str] = []
            for category in categories:
                selected = chosen.get(category)
                if selected is None:
                    continue
                for other in registry["instances"]:
                    if other.get("id") == record["id"]:
                        continue
                    for other_category, info in other.get("storage_paths", {}).items():
                        raw = info.get("path")
                        if not raw:
                            continue
                        try:
                            other_path = Path(raw).expanduser().resolve(strict=True)
                        except (OSError, RuntimeError):
                            other_path = Path(os.path.realpath(raw))
                        if selected != other_path:
                            continue
                        allowed = (
                            category == other_category
                            and category in shared
                            and shared[category]
                            and other.get("storage_policy", {}).get(category) == "shared"
                        )
                        if not allowed:
                            collisions.append(
                                f"{category.title()} {selected} is already referenced by "
                                f"{other.get('display_name') or other.get('id')} ({other_category})"
                            )
            if collisions:
                raise ValueError(
                    "Storage collision detected; no INI changes were made:\n"
                    + "\n".join(collisions)
                    + "\nOnly Mods-to-Mods and Downloads-to-Downloads sharing is allowed, "
                    "and every participating instance must opt in."
                )

            parser = configparser.ConfigParser(interpolation=None)
            with config.open("r", encoding="utf-8-sig") as stream:
                parser.read_file(stream)
            if not parser.has_section("Settings"):
                parser.add_section("Settings")
            old_values = {
                category: parser["Settings"].get(key, "").strip()
                for category, key in ini_keys.items()
            }
            data_changes = [
                f"{category.title()}: {old}"
                for category, old in old_values.items()
                if category in ("mods", "downloads", "profiles", "overwrite")
                and old and old != str(chosen.get(category, ""))
            ]
            if data_changes:
                answer = QMessageBox.warning(
                    self, "Existing data remains in place",
                    "These data roots will be replaced in ModOrganizer.ini. Wyrd will not move, copy, "
                    "merge, or delete files. MO2 may show existing content as missing unless the "
                    "destination is already prepared:\n\n" + "\n".join(data_changes)
                    + "\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            # Only create explicitly opted-in, missing leaf directories.
            try:
                for category, path in missing:
                    if path.exists() or path.is_symlink():
                        raise ValueError(f"{category.title()} path changed during configuration.")
                    path.mkdir()
                    created.append(path)
                    chosen[category] = path.resolve(strict=True)
                for index, category in enumerate(categories):
                    for previous in categories[:index]:
                        if chosen[category] == chosen[previous]:
                            raise ValueError(f"{category.title()} and {previous.title()} cannot use the same directory.")

                for category, key in ini_keys.items():
                    parser["Settings"][key] = str(chosen[category])
                backup = self._backup_instance_ini(record, config)
                fd, temporary = tempfile.mkstemp(prefix=".wyrd-mo2-", suffix=".ini", dir=str(config.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        parser.write(stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, config)
                except Exception:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise

                updated = dict(record)
                updated["storage_policy"] = {
                    category: ("shared" if category in shared and shared[category] else "instance-local")
                    for category in categories
                }
                # Overwrite and Profiles are never eligible for shared storage.
                updated["storage_paths"] = dict(record.get("storage_paths", {}))
                for category, path in chosen.items():
                    updated["storage_paths"][category] = {"path": str(path), "status": "available"}
                try:
                    self.service.replace_instance(updated)
                    self.selected_instance_record = updated
                except Exception:
                    self._restore_ini_atomically(backup, config)
                    raise
            except Exception:
                for path in reversed(created):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                raise

            self._refresh_registered()
            for row in range(self.registered_instances.count()):
                item = self.registered_instances.item(row)
                value = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(value, dict) and value.get("id") == record.get("id"):
                    self.registered_instances.setCurrentRow(row)
                    break
            QMessageBox.information(
                self, "Storage settings saved",
                f"All six storage paths were saved. ModOrganizer.ini backup:\n{backup}\n\n"
                "No existing data was moved, copied, merged, or deleted."
            )
        except Exception as error:
            QMessageBox.warning(self, "Storage settings", str(error))


    def edit_associations(self) -> None:
        record = self.selected_instance_record
        if not record:
            return
        try:
            _registry, installations, games, runtimes = self._registry_maps()
            current_game = games[record["game_id"]]
            installation = installations[record["installation_id"]]
            current_runtime = runtimes.get(record.get("runtime_id"), {})
            game_name, accepted = QInputDialog.getText(
                self, "Edit game association", "Game name:", text=current_game.get("name", "")
            )
            if not accepted or not game_name.strip():
                return
            game_path = QFileDialog.getExistingDirectory(
                self, "Select game installation directory", current_game.get("path", "")
            )
            if not game_path:
                return
            app_id, accepted = QInputDialog.getText(
                self, "Edit game association", "Steam AppID (blank for non-Steam game):",
                text=str(current_game.get("app_id", ""))
            )
            if not accepted:
                return
            if app_id.strip() and not app_id.strip().isdigit():
                raise ValueError("Steam AppID must contain digits only, or be blank for a non-Steam game.")
            if not Path(game_path).expanduser().is_dir():
                raise ValueError("Selected game directory is no longer available.")
            prefix_path = QFileDialog.getExistingDirectory(
                self, "Select initialized Proton prefix", str(record.get("prefix_path") or "")
            )
            if not prefix_path:
                return
            prefix = Path(prefix_path).expanduser()
            if (
                prefix.is_symlink()
                or (prefix / "pfx").is_symlink()
                or (prefix / "pfx/drive_c").is_symlink()
                or (prefix / "pfx/drive_c/users").is_symlink()
                or not (prefix / "pfx/drive_c/users").is_dir()
            ):
                raise ValueError("Selected prefix is not initialized or contains a symlink.")
            from wyrd_mo2 import inspect_mo2_instances
            install_root = Path(installation.get("executable", "")).expanduser().parent
            inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
            expected_instance = Path(record.get("instance_path", "")).expanduser().resolve(strict=True)
            available = {
                Path(item.get("path", "")).expanduser().resolve(strict=True)
                for item in inventory.get("instances", [])
                if item.get("path")
            } if inventory.get("ok") else set()
            if expected_instance not in available:
                raise ValueError(
                    "The selected Proton prefix does not contain this MO2 instance. "
                    "Choose the prefix already associated with the instance."
                )
            runtime_path = QFileDialog.getExistingDirectory(
                self, "Select Proton runtime", current_runtime.get("path", "")
            )
            if not runtime_path:
                return
            runtime = Path(runtime_path).expanduser()
            if runtime.is_symlink() or not (runtime / "proton").is_file():
                raise ValueError("Selected runtime does not contain a Proton launcher.")
            game_id = "game-" + hashlib.sha256(
                (game_path + "|" + app_id.strip()).encode()
            ).hexdigest()[:16]
            runtime_id = "proton-" + hashlib.sha256(str(runtime.resolve()).encode()).hexdigest()[:16]
            game_directory = Path(game_path).expanduser().resolve(strict=True)
            steam_root = ""
            if (
                app_id.strip()
                and game_directory.parent.name.casefold() == "common"
                and game_directory.parent.parent.name.casefold() == "steamapps"
            ):
                steam_root = str(game_directory.parent.parent.parent)
            game = {
                "id": game_id, "name": game_name.strip(), "path": str(game_directory),
                "app_id": app_id.strip(), "steam_root": steam_root,
                "prefix_path": str(prefix.resolve(strict=True)),
            }
            runtime_record = {
                "id": runtime_id, "name": runtime.name, "path": str(runtime.resolve()),
            }
            answer = QMessageBox.question(
                self, "Save game/runtime association",
                "Update this registered instance's game and Proton association? "
                "Only Wyrd registry metadata changes. MO2 and game files remain untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.service.update_associations(record["id"], game=game, runtime=runtime_record)
            self._refresh_registered()
            for row in range(self.registered_instances.count()):
                item = self.registered_instances.item(row)
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == record["id"]:
                    self.registered_instances.setCurrentRow(row)
                    break
            self.associations_updated = True
        except Exception as error:
            QMessageBox.warning(self, "Edit associations", str(error))

    def launch_selected(self) -> None:
        if not self.selected_instance_record:
            return
        answer = QMessageBox.question(
            self, "Launch registered instance",
            "Launch this instance using its registered installation, game and Proton runtime?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.launch_requested = True
            self.preferred_instance_id = self.selected_instance_record.get("id", "")
            callback = getattr(self, "on_launch_requested", None)
            if callable(callback):
                callback(self.selected_instance_record)


    def _choose_storage_path(self, category: str) -> None:
        current = self.storage_fields[category].text()
        selected = QFileDialog.getExistingDirectory(self, f"Select {category} directory", current if current and current != "Not recorded" else "")
        if selected: self.storage_fields[category].setText(selected)


    def _restore_ini_atomically(backup: Path, destination: Path) -> None:
        """Restore a saved INI with an atomic same-directory replacement."""
        fd, temporary = tempfile.mkstemp(
            prefix=".wyrd-ini-rollback-", suffix=".ini", dir=str(destination.parent)
        )
        try:
            with os.fdopen(fd, "wb") as target, backup.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _backup_instance_ini(self, record: dict, config: Path) -> Path:
        """Back up only the instance INI; never copy or migrate mod data."""
        from datetime import datetime
        import shutil

        backup_root = managed_paths()["config"] / "mo2-config-backups"
        if backup_root.is_symlink():
            raise ValueError("The Wyrd INI backup directory is a symlink; backup creation refused.")
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_root / f"{record['id']}-{stamp}.ini"
        shutil.copy2(config, backup)
        record.setdefault("config_backups", []).append(str(backup))
        return backup

    def restore_config_backup(self) -> None:
        record = self.selected_instance_record
        if not record:
            return
        try:
            _registry, installations, _games, _runtimes = self._registry_maps()
            if installations[record["installation_id"]].get("owner") != "wyrd":
                raise ValueError("External MO2 configuration is read-only in Wyrd.")
            backups = record.get("config_backups", [])
            if not backups:
                raise ValueError("No Wyrd-created ModOrganizer.ini backups are recorded.")
            backup_root = managed_paths()["config"] / "mo2-config-backups"
            if backup_root.is_symlink() or not backup_root.is_dir():
                raise ValueError("The Wyrd INI backup directory is missing or unsafe.")
            choices = [Path(value).expanduser() for value in backups]
            labels = [f"{path.name} — {path}" for path in reversed(choices)]
            selected, accepted = QInputDialog.getItem(
                self, "Choose INI backup", "Restore which Wyrd-created backup?",
                labels, 0, False,
            )
            if not accepted or not selected:
                return
            backup = next(path for path in choices if f"{path.name} — {path}" == selected)
            config = Path(record.get("config", "")).expanduser()
            if backup.is_symlink() or not backup.is_file():
                raise ValueError("The selected INI backup is missing or unsafe.")
            resolved_backup = backup.resolve(strict=True)
            resolved_backup_root = backup_root.resolve(strict=True)
            if resolved_backup.parent != resolved_backup_root:
                raise ValueError("The selected backup is outside the managed INI backup directory.")
            if config.is_symlink() or not config.is_file():
                raise ValueError("The current ModOrganizer.ini is missing or unsafe.")
            answer = QMessageBox.question(
                self, "Restore ModOrganizer.ini",
                f"Restore this Wyrd backup?\n\n{backup}\n\n"
                "This restores configuration only. It does not restore, move, or delete mod files.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            current_backup = self._backup_instance_ini(record, config)
            fd, temporary = tempfile.mkstemp(prefix=".wyrd-restore-", suffix=".ini", dir=str(config.parent))
            try:
                with os.fdopen(fd, "wb") as handle, backup.open("rb") as source:
                    handle.write(source.read())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, config)
            except Exception:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            try:
                self.service.replace_instance(record)
            except Exception:
                # A failed registry write must not leave the restored INI paired
                # with stale Wyrd metadata.
                self._restore_ini_atomically(current_backup, config)
                raise
            self._refresh_registered()
            QMessageBox.information(
                self, "INI restored",
                f"Configuration restored. The pre-restore INI was backed up to:\n{current_backup}"
            )
        except Exception as error:
            QMessageBox.warning(self, "Restore INI backup", str(error))

    def apply_storage(self) -> None:
        """Apply explicitly approved storage roots; never migrate or merge files."""
        record = self.selected_instance_record
        if not record:
            return
        created: list[Path] = []
        try:
            registry, installations, _games, _runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            if installation.get("owner") != "wyrd":
                raise ValueError("External MO2 is read-only. Wyrd will not rewrite its ModOrganizer.ini.")
            config = Path(record.get("config", "")).expanduser()
            if config.is_symlink() or not config.is_file():
                raise ValueError("The instance ModOrganizer.ini is missing or symlinked.")

            categories = ("base", "mods", "downloads", "cache", "profiles", "overwrite")
            ini_keys = {
                "base": "base_directory", "mods": "mod_directory",
                "downloads": "download_directory", "cache": "cache_directory",
                "profiles": "profiles_directory", "overwrite": "overwrite_directory",
            }
            chosen: dict[str, Path] = {}
            missing: list[tuple[str, Path]] = []
            for category in categories:
                raw = self.storage_fields[category].text().strip()
                if not raw or raw == "Not recorded":
                    raise ValueError(f"Choose a path for {category.title()}.")
                path = Path(raw).expanduser()
                if path.is_symlink():
                    raise ValueError(f"{category.title()} path is a symlink; refusing to use it.")
                if path.exists() and not path.is_dir():
                    raise ValueError(f"{category.title()} path exists but is not a directory.")
                if not path.exists():
                    missing.append((category, path))
                else:
                    chosen[category] = path.resolve(strict=True)

            if missing and not self.create_storage_check.isChecked():
                names = ", ".join(f"{name}: {path}" for name, path in missing)
                raise ValueError(
                    "These storage directories do not exist. Enable 'Create missing empty storage folders' "
                    f"to create only empty directories, or choose existing folders:\n{names}"
                )
            for category, path in missing:
                if path.parent.is_symlink() or not path.parent.is_dir():
                    raise ValueError(f"Parent of {category.title()} must exist and must not be a symlink: {path.parent}")

            # Existing default roots are kept, and every category has its own
            # independent path. Profiles and Overwrite may never overlap another instance.
            for index, category in enumerate(categories):
                for previous in categories[:index]:
                    if chosen.get(category) and chosen.get(category) == chosen.get(previous):
                        raise ValueError(f"{category.title()} and {previous.title()} cannot use the same directory.")

            shared = {
                "mods": self.share_mods_check.isChecked(),
                "downloads": self.share_downloads_check.isChecked(),
            }
            for category, enabled in shared.items():
                if enabled and category not in chosen:
                    raise ValueError(f"Select an existing {category.title()} directory before opting into sharing.")
            if any(shared.values()):
                summary = "\n".join(
                    f"{category.title()}: {chosen[category]}"
                    for category, enabled in shared.items() if enabled
                )
                answer = QMessageBox.warning(
                    self, "Confirm shared storage",
                    "Shared storage can cause mod/download collisions and cross-instance interference. "
                    "Wyrd will not merge, deduplicate, lock, or reconcile contents. Every participating "
                    "instance must separately opt in.\n\n" + summary
                    + "\n\nContinue only if these instances are deliberately sharing the same data roots.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            collisions: list[str] = []
            for category in categories:
                selected = chosen.get(category)
                if selected is None:
                    continue
                for other in registry["instances"]:
                    if other.get("id") == record["id"]:
                        continue
                    for other_category, info in other.get("storage_paths", {}).items():
                        raw = info.get("path")
                        if not raw:
                            continue
                        try:
                            other_path = Path(raw).expanduser().resolve(strict=True)
                        except (OSError, RuntimeError):
                            other_path = Path(os.path.realpath(raw))
                        if selected != other_path:
                            continue
                        allowed = (
                            category == other_category
                            and category in shared
                            and shared[category]
                            and other.get("storage_policy", {}).get(category) == "shared"
                        )
                        if not allowed:
                            collisions.append(
                                f"{category.title()} {selected} is already referenced by "
                                f"{other.get('display_name') or other.get('id')} ({other_category})"
                            )
            if collisions:
                raise ValueError(
                    "Storage collision detected; no INI changes were made:\n"
                    + "\n".join(collisions)
                    + "\nOnly Mods-to-Mods and Downloads-to-Downloads sharing is allowed, "
                    "and every participating instance must opt in."
                )

            parser = configparser.ConfigParser(interpolation=None)
            with config.open("r", encoding="utf-8-sig") as stream:
                parser.read_file(stream)
            if not parser.has_section("Settings"):
                parser.add_section("Settings")
            old_values = {
                category: parser["Settings"].get(key, "").strip()
                for category, key in ini_keys.items()
            }
            data_changes = [
                f"{category.title()}: {old}"
                for category, old in old_values.items()
                if category in ("mods", "downloads", "profiles", "overwrite")
                and old and old != str(chosen.get(category, ""))
            ]
            if data_changes:
                answer = QMessageBox.warning(
                    self, "Existing data remains in place",
                    "These data roots will be replaced in ModOrganizer.ini. Wyrd will not move, copy, "
                    "merge, or delete files. MO2 may show existing content as missing unless the "
                    "destination is already prepared:\n\n" + "\n".join(data_changes)
                    + "\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            # Only create explicitly opted-in, missing leaf directories.
            try:
                for category, path in missing:
                    if path.exists() or path.is_symlink():
                        raise ValueError(f"{category.title()} path changed during configuration.")
                    path.mkdir()
                    created.append(path)
                    chosen[category] = path.resolve(strict=True)
                for index, category in enumerate(categories):
                    for previous in categories[:index]:
                        if chosen[category] == chosen[previous]:
                            raise ValueError(f"{category.title()} and {previous.title()} cannot use the same directory.")

                for category, key in ini_keys.items():
                    parser["Settings"][key] = str(chosen[category])
                backup = self._backup_instance_ini(record, config)
                fd, temporary = tempfile.mkstemp(prefix=".wyrd-mo2-", suffix=".ini", dir=str(config.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        parser.write(stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, config)
                except Exception:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise

                updated = dict(record)
                updated["storage_policy"] = {
                    category: ("shared" if category in shared and shared[category] else "instance-local")
                    for category in categories
                }
                # Overwrite and Profiles are never eligible for shared storage.
                updated["storage_paths"] = dict(record.get("storage_paths", {}))
                for category, path in chosen.items():
                    updated["storage_paths"][category] = {"path": str(path), "status": "available"}
                try:
                    self.service.replace_instance(updated)
                    self.selected_instance_record = updated
                except Exception:
                    self._restore_ini_atomically(backup, config)
                    raise
            except Exception:
                for path in reversed(created):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                raise

            self._refresh_registered()
            for row in range(self.registered_instances.count()):
                item = self.registered_instances.item(row)
                value = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(value, dict) and value.get("id") == record.get("id"):
                    self.registered_instances.setCurrentRow(row)
                    break
            QMessageBox.information(
                self, "Storage settings saved",
                f"All six storage paths were saved. ModOrganizer.ini backup:\n{backup}\n\n"
                "No existing data was moved, copied, merged, or deleted."
            )
        except Exception as error:
            QMessageBox.warning(self, "Storage settings", str(error))



    def edit_associations(self) -> None:
        record = self.selected_instance_record
        if not record:
            return
        try:
            _registry, installations, games, runtimes = self._registry_maps()
            current_game = games[record["game_id"]]
            installation = installations[record["installation_id"]]
            current_runtime = runtimes.get(record.get("runtime_id"), {})
            game_name, accepted = QInputDialog.getText(
                self, "Edit game association", "Game name:", text=current_game.get("name", "")
            )
            if not accepted or not game_name.strip():
                return
            game_path = QFileDialog.getExistingDirectory(
                self, "Select game installation directory", current_game.get("path", "")
            )
            if not game_path:
                return
            app_id, accepted = QInputDialog.getText(
                self, "Edit game association", "Steam AppID (blank for non-Steam game):",
                text=str(current_game.get("app_id", ""))
            )
            if not accepted:
                return
            if app_id.strip() and not app_id.strip().isdigit():
                raise ValueError("Steam AppID must contain digits only, or be blank for a non-Steam game.")
            if not Path(game_path).expanduser().is_dir():
                raise ValueError("Selected game directory is no longer available.")
            prefix_path = QFileDialog.getExistingDirectory(
                self, "Select initialized Proton prefix", str(record.get("prefix_path") or "")
            )
            if not prefix_path:
                return
            prefix = Path(prefix_path).expanduser()
            if (
                prefix.is_symlink()
                or (prefix / "pfx").is_symlink()
                or (prefix / "pfx/drive_c").is_symlink()
                or (prefix / "pfx/drive_c/users").is_symlink()
                or not (prefix / "pfx/drive_c/users").is_dir()
            ):
                raise ValueError("Selected prefix is not initialized or contains a symlink.")
            from wyrd_mo2 import inspect_mo2_instances
            install_root = Path(installation.get("executable", "")).expanduser().parent
            inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
            expected_instance = Path(record.get("instance_path", "")).expanduser().resolve(strict=True)
            available = {
                Path(item.get("path", "")).expanduser().resolve(strict=True)
                for item in inventory.get("instances", [])
                if item.get("path")
            } if inventory.get("ok") else set()
            if expected_instance not in available:
                raise ValueError(
                    "The selected Proton prefix does not contain this MO2 instance. "
                    "Choose the prefix already associated with the instance."
                )
            runtime_path = QFileDialog.getExistingDirectory(
                self, "Select Proton runtime", current_runtime.get("path", "")
            )
            if not runtime_path:
                return
            runtime = Path(runtime_path).expanduser()
            if runtime.is_symlink() or not (runtime / "proton").is_file():
                raise ValueError("Selected runtime does not contain a Proton launcher.")
            game_id = "game-" + hashlib.sha256(
                (game_path + "|" + app_id.strip()).encode()
            ).hexdigest()[:16]
            runtime_id = "proton-" + hashlib.sha256(str(runtime.resolve()).encode()).hexdigest()[:16]
            game_directory = Path(game_path).expanduser().resolve(strict=True)
            steam_root = ""
            if (
                app_id.strip()
                and game_directory.parent.name.casefold() == "common"
                and game_directory.parent.parent.name.casefold() == "steamapps"
            ):
                steam_root = str(game_directory.parent.parent.parent)
            game = {
                "id": game_id, "name": game_name.strip(), "path": str(game_directory),
                "app_id": app_id.strip(), "steam_root": steam_root,
                "prefix_path": str(prefix.resolve(strict=True)),
            }
            runtime_record = {
                "id": runtime_id, "name": runtime.name, "path": str(runtime.resolve()),
            }
            answer = QMessageBox.question(
                self, "Save game/runtime association",
                "Update this registered instance's game and Proton association? "
                "Only Wyrd registry metadata changes. MO2 and game files remain untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.service.update_associations(record["id"], game=game, runtime=runtime_record)
            self._refresh_registered()
            for row in range(self.registered_instances.count()):
                item = self.registered_instances.item(row)
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == record["id"]:
                    self.registered_instances.setCurrentRow(row)
                    break
            self.associations_updated = True
        except Exception as error:
            QMessageBox.warning(self, "Edit associations", str(error))

    def launch_selected(self) -> None:
        if not self.selected_instance_record:
            return
        answer = QMessageBox.question(
            self, "Launch registered instance",
            "Launch this instance using its registered installation, game and Proton runtime?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.launch_requested = True
            self.preferred_instance_id = self.selected_instance_record.get("id", "")
            callback = getattr(self, "on_launch_requested", None)
            if callable(callback):
                callback(self.selected_instance_record)


