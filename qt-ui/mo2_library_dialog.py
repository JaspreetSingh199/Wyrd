"""Qt interface for discovering and explicitly registering MO2 installations/instances."""
from __future__ import annotations

import hashlib
import os
import configparser
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QPlainTextEdit, QVBoxLayout, QCheckBox, QGroupBox, QInputDialog,
)

from wyrd_mo2 import managed_paths
from wyrd_mo2_registry_service import RegistryService


class Mo2LibraryDialog(QDialog):
    """Discover existing MO2 resources and register metadata without modifying MO2."""

    def __init__(
        self, parent=None, *, prefix_path: Path | str = "",
        install_path: Path | str = "", game_path: Path | str = "",
        proton_path: Path | str = "", selected_instance_id: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Wyrd — MO2 Library and Instances")
        self.resize(820, 680)
        self.service = RegistryService(managed_paths()["config"] / "mo2-registry.json")
        self.discovery: dict = {}
        self.registered = False
        self.launch_requested = False
        self.associations_updated = False
        self.selected_instance_record: dict | None = None
        self.preferred_instance_id = selected_instance_id

        root = QVBoxLayout(self)
        intro = QLabel(
            "Discover MO2 installations and instances in a known Proton prefix. "
            "Discovery is read-only. Registration stores Wyrd metadata only; it does "
            "not adopt ownership, rewrite ModOrganizer.ini, move files, or change profiles."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.prefix_edit = QLineEdit(str(prefix_path))
        self.install_edit = QLineEdit(str(install_path))
        install_row = QHBoxLayout()
        install_row.addWidget(self.install_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_install)
        install_row.addWidget(browse)
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(self.prefix_edit, 1)
        prefix_browse = QPushButton("Browse prefix…")
        prefix_browse.clicked.connect(self._browse_prefix)
        prefix_row.addWidget(prefix_browse)
        form.addRow("Steam compatdata / Proton prefix:", prefix_row)
        form.addRow("MO2 installation folder:", install_row)

        self.game_name_edit = QLineEdit("Skyrim Special Edition")
        self.game_id_edit = QLineEdit("steam-489830")
        self.game_appid_edit = QLineEdit("489830")
        self.game_path_edit = QLineEdit(str(game_path))
        game_row = QHBoxLayout()
        game_row.addWidget(self.game_path_edit, 1)
        browse_game = QPushButton("Browse…")
        browse_game.clicked.connect(self._browse_game)
        game_row.addWidget(browse_game)
        self.proton_edit = QLineEdit(str(proton_path or ""))
        runtime_row = QHBoxLayout()
        runtime_row.addWidget(self.proton_edit, 1)
        browse_runtime = QPushButton("Browse Proton…")
        browse_runtime.clicked.connect(self._browse_runtime)
        runtime_row.addWidget(browse_runtime)
        form.addRow("Game name:", self.game_name_edit)
        form.addRow("Game association ID:", self.game_id_edit)
        form.addRow("Optional Steam AppID:", self.game_appid_edit)
        form.addRow("Game directory:", game_row)
        form.addRow("Proton runtime:", runtime_row)
        root.addLayout(form)

        actions = QHBoxLayout()
        self.discover_button = QPushButton("Discover MO2")
        self.discover_button.clicked.connect(self.discover)
        actions.addWidget(self.discover_button)
        actions.addStretch(1)
        root.addLayout(actions)

        root.addWidget(QLabel("Registered installations"))
        self.installation_filter = QComboBox()
        self.installation_filter.addItem("All installations", None)
        self.installation_filter.currentIndexChanged.connect(self._refresh_registered)
        root.addWidget(self.installation_filter)
        self.installation_summary = QLabel("Select an installation to inspect its registry references.")
        self.installation_summary.setWordWrap(True)
        root.addWidget(self.installation_summary)
        root.addWidget(QLabel("Registered MO2 instances — select to manage or launch"))
        self.registered_instances = QListWidget()
        self.registered_instances.currentRowChanged.connect(self._select_registered)
        root.addWidget(self.registered_instances, 1)
        self._edit_associations_button = QPushButton("Edit game / Proton association")
        self._edit_associations_button.setEnabled(False)
        self._edit_associations_button.clicked.connect(self.edit_associations)
        root.addWidget(self._edit_associations_button)
        self._launch_existing_button = QPushButton("Launch selected registered instance")
        self._launch_existing_button.setEnabled(False)
        self._launch_existing_button.clicked.connect(self.launch_selected)
        root.addWidget(self._launch_existing_button)
        management_actions = QHBoxLayout()
        self.unregister_instance_button = QPushButton("Unregister selected instance")
        self.unregister_instance_button.setEnabled(False)
        self.unregister_instance_button.clicked.connect(self.unregister_selected_instance)
        self.unregister_installation_button = QPushButton("Unregister unused installation")
        self.unregister_installation_button.setEnabled(False)
        self.unregister_installation_button.clicked.connect(self.unregister_selected_installation)
        management_actions.addWidget(self.unregister_instance_button)
        management_actions.addWidget(self.unregister_installation_button)
        root.addLayout(management_actions)

        root.addWidget(QLabel("Discovered instances"))
        self.instances = QListWidget()
        self.instances.currentRowChanged.connect(self._show_selected)
        root.addWidget(self.instances, 1)

        root.addWidget(QLabel("Inspection details"))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        self.details.setPlaceholderText("Choose Discover MO2 to inspect the selected installation.")
        root.addWidget(self.details)

        storage_box = QGroupBox("Selected instance storage")
        storage_form = QFormLayout(storage_box)
        self.storage_fields = {}
        self.storage_browse_buttons = {}
        for key, label in (("base", "Base"), ("mods", "Mods"), ("downloads", "Downloads"),
                           ("cache", "Cache"), ("profiles", "Profiles"), ("overwrite", "Overwrite")):
            row = QHBoxLayout()
            field = QLineEdit()
            field.setReadOnly(True)
            browse = QPushButton("Browse…")
            browse.setEnabled(False)
            browse.clicked.connect(lambda _checked=False, category=key: self._choose_storage_path(category))
            self.storage_fields[key] = field
            self.storage_browse_buttons[key] = browse
            row.addWidget(field, 1)
            row.addWidget(browse)
            storage_form.addRow(label, row)
        root.addWidget(storage_box)

        self.share_mods_check = QCheckBox("Explicitly share Mods directory with other registered instances")
        self.share_downloads_check = QCheckBox("Explicitly share Downloads directory with other registered instances")
        root.addWidget(self.share_mods_check)
        root.addWidget(self.share_downloads_check)
        storage_actions = QHBoxLayout()
        self.apply_storage_button = QPushButton("Apply storage settings")
        self.restore_storage_button = QPushButton("Restore INI backup…")
        self.apply_storage_button.setEnabled(False)
        self.restore_storage_button.setEnabled(False)
        self.apply_storage_button.clicked.connect(self.apply_storage)
        self.restore_storage_button.clicked.connect(self.restore_config_backup)
        storage_actions.addWidget(self.apply_storage_button)
        storage_actions.addWidget(self.restore_storage_button)
        root.addLayout(storage_actions)

        footer = QHBoxLayout()
        self.register_button = QPushButton("Register discovered instance")
        self.register_button.setEnabled(False)
        self.register_button.clicked.connect(self.register_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(self.register_button)
        footer.addStretch(1)
        footer.addWidget(close)
        root.addLayout(footer)
        self._refresh_registered()

    def _registry_maps(self):
        registry = self.service.load()
        return (
            registry,
            {x["id"]: x for x in registry["installations"]},
            {x["id"]: x for x in registry["games"]},
            {x["id"]: x for x in registry["runtimes"]},
        )

    def _refresh_registered(self, _index: int = -1) -> None:
        selected_filter = self.installation_filter.currentData() if self.installation_filter.count() else None
        self.registered_instances.clear()
        self.selected_instance_record = None
        try:
            registry, installations, games, _runtimes = self._registry_maps()
        except Exception as error:
            self.registered_instances.addItem(f"Registry unavailable: {error}")
            return

        self.installation_filter.blockSignals(True)
        self.installation_filter.clear()
        self.installation_filter.addItem("All installations", None)
        for installation in sorted(
            registry["installations"],
            key=lambda item: Path(item.get("executable", "")).parent.name.casefold(),
        ):
            executable = Path(installation.get("executable", ""))
            label = (
                f"{executable.parent.name or 'MO2'} · {installation.get('owner', 'unknown')} · "
                f"{installation.get('executable', '')}"
            )
            self.installation_filter.addItem(label, installation.get("id"))
        selected_index = self.installation_filter.findData(selected_filter)
        self.installation_filter.setCurrentIndex(max(0, selected_index))
        self.installation_filter.blockSignals(False)
        active_filter = self.installation_filter.currentData()
        selected_installation = installations.get(active_filter) if active_filter else None
        if selected_installation:
            references = sum(1 for item in registry["instances"]
                             if item.get("installation_id") == active_filter)
            self.installation_summary.setText(
                f"Selected installation: {selected_installation.get('executable')}\\n"
                f"Ownership: {selected_installation.get('owner', 'unknown')} · "
                f"Registered instances: {references}\\n"
                "Unregistering removes only Wyrd's reference; it never deletes MO2 files."
            )
            self.unregister_installation_button.setEnabled(references == 0)
        else:
            self.installation_summary.setText(
                f"Installations: {len(registry['installations'])} · "
                f"Registered instances: {len(registry['instances'])}. "
                "Choose one installation to inspect its references."
            )
            self.unregister_installation_button.setEnabled(False)
        self.unregister_instance_button.setEnabled(False)

        if not registry["instances"]:
            self.registered_instances.addItem("No MO2 instances registered yet.")
            return
        visible_count = 0
        for record in registry["instances"]:
            if active_filter and record.get("installation_id") != active_filter:
                continue
            installation = installations.get(record.get("installation_id"), {})
            game = games.get(record.get("game_id"), {})
            item = QListWidgetItem(
                f"{game.get('name', 'Unknown game')} · {record.get('instance_type', 'unknown')} · "
                f"{installation.get('owner', 'unknown')}\n{record.get('instance_path', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.registered_instances.addItem(item)
            visible_count += 1
            if record.get("id") == self.preferred_instance_id:
                self.registered_instances.setCurrentItem(item)
        if not visible_count:
            self.registered_instances.addItem("No instances belong to this MO2 installation.")

    def _select_registered(self, row: int) -> None:
        item = self.registered_instances.item(row) if row >= 0 else None
        record = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record, dict):
            self.selected_instance_record = None
            self._launch_existing_button.setEnabled(False)
            self._edit_associations_button.setEnabled(False)
            self.apply_storage_button.setEnabled(False)
            self.unregister_instance_button.setEnabled(False)
            return
        self.selected_instance_record = record
        self.unregister_instance_button.setEnabled(True)
        try:
            registry, installations, games, runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            game = games[record["game_id"]]
            runtime = runtimes.get(record.get("runtime_id"), {})
        except Exception as error:
            self.details.setPlainText(f"Registry entry is incomplete: {error}")
            self._launch_existing_button.setEnabled(False)
            return
        self.details.setPlainText(
            f"Instance: {record.get('instance_path')}\n"
            f"Installation: {installation.get('executable')} ({installation.get('owner')})\n"
            f"Game: {game.get('name')} — {game.get('path')}\n"
            f"Proton runtime: {runtime.get('path') or 'Not associated'}\n"
            f"Instance INI: {record.get('config')}\n"
            "Existing installation ownership is unchanged."
        )
        paths = record.get("storage_paths", {})
        for category, field in self.storage_fields.items():
            field.setText(str(paths.get(category, {}).get("path", "Not recorded")))
        policies = record.get("storage_policy", {})
        self.share_mods_check.setChecked(policies.get("mods") == "shared")
        self.share_downloads_check.setChecked(policies.get("downloads") == "shared")
        ready = bool(installation.get("executable") and game.get("path") and runtime.get("path")
                     and record.get("prefix_path"))
        self._launch_existing_button.setEnabled(ready)
        self.apply_storage_button.setEnabled(bool(record.get("config")))
        self.restore_storage_button.setEnabled(bool(record.get("config_backups")))
        for button in self.storage_browse_buttons.values():
            button.setEnabled(bool(record.get("config")))
        self._edit_associations_button.setEnabled(True)

    def unregister_selected_instance(self) -> None:
        """Forget a selected instance in Wyrd without changing its files."""
        record = self.selected_instance_record
        if not record:
            return
        answer = QMessageBox.question(
            self, "Unregister MO2 instance",
            "Remove this instance from Wyrd's registry?\\n\\n"
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
            self._refresh_registered()
            self.details.setPlainText(
                "Instance reference removed from Wyrd. No MO2 or game files were changed."
            )
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
                f"Remove this unused installation reference from Wyrd?\\n\\n"
                f"{installation.get('executable')}\\n\\n"
                "The MO2 executable, installation directory, Proton prefix and all data remain untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.service.unregister_installation(installation_id)
            self.preferred_instance_id = ""
            self._refresh_registered()
            self.details.setPlainText(
                "Unused installation reference removed from Wyrd. No files were deleted."
            )
        except Exception as error:
            QMessageBox.warning(self, "Unregister MO2 installation", str(error))

    def _choose_storage_path(self, category: str) -> None:
        current = self.storage_fields[category].text()
        selected = QFileDialog.getExistingDirectory(
            self, f"Select {category} directory", current
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
        record = self.selected_instance_record
        if not record:
            return
        try:
            registry, installations, _games, _runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            if installation.get("owner") != "wyrd":
                raise ValueError("External MO2 is read-only. Wyrd will not rewrite its ModOrganizer.ini.")
            config = Path(record.get("config", "")).expanduser()
            if config.is_symlink() or not config.is_file():
                raise ValueError("The instance ModOrganizer.ini is missing or symlinked.")

            categories = ("base", "mods", "downloads", "cache", "profiles", "overwrite")
            chosen = {key: Path(self.storage_fields[key].text()).expanduser() for key in categories}
            for category, path in chosen.items():
                if path.is_symlink() or not path.is_dir():
                    raise ValueError(f"{category.title()} must be an existing, non-symlink directory.")
                chosen[category] = path.resolve(strict=True)

            instance_root = Path(record["instance_path"]).resolve(strict=True)
            for index, category in enumerate(categories):
                for previous in categories[:index]:
                    if chosen[category] == chosen[previous]:
                        raise ValueError(
                            f"{category.title()} and {previous.title()} cannot use the same directory."
                        )
            shared = {
                "mods": self.share_mods_check.isChecked(),
                "downloads": self.share_downloads_check.isChecked(),
            }
            for category in categories:
                path = chosen[category]
                if category in ("profiles", "overwrite") and path != instance_root and instance_root not in path.parents:
                    # External per-instance paths are allowed, but must never be reused by another instance.
                    pass
                for other in registry["instances"]:
                    if other.get("id") == record["id"]:
                        continue
                    other_info = other.get("storage_paths", {}).get(category, {})
                    other_path_text = other_info.get("path")
                    if not other_path_text:
                        continue
                    other_path = os.path.normcase(os.path.realpath(other_path_text))
                    selected_path = os.path.normcase(str(path))
                    if other_path != selected_path:
                        continue
                    other_is_shared = other.get("storage_policy", {}).get(category) == "shared"
                    both_opted_into_shared = category in shared and shared[category] and other_is_shared
                    if not both_opted_into_shared:
                        raise ValueError(
                            f"{category.title()} path is already registered by another instance. "
                            "For Mods/Downloads, every participating instance must explicitly opt in."
                        )
                    if category not in shared:
                        raise ValueError(f"{category.title()} cannot be shared between instances.")

            answer = QMessageBox.question(
                self, "Apply storage settings",
                "Update the six storage paths in this instance's ModOrganizer.ini?\n\n"
                "Wyrd will create a timestamped backup of this INI first. No folders or mod files "
                "will be copied, moved, merged, created, or deleted. The selected directories must "
                "already contain the data MO2 should use. Profiles and Overwrite are never shared.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            parser = configparser.ConfigParser(interpolation=None)
            with config.open("r", encoding="utf-8-sig") as handle:
                parser.read_file(handle)
            if not parser.has_section("Settings"):
                parser.add_section("Settings")
            ini_keys = {
                "base": "base_directory", "mods": "mod_directory",
                "downloads": "download_directory", "cache": "cache_directory",
                "profiles": "profiles_directory", "overwrite": "overwrite_directory",
            }
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

            record.setdefault("storage_policy", {})
            for category in categories:
                if category in shared:
                    record["storage_policy"][category] = "shared" if shared[category] else "instance-local"
                elif category == "overwrite":
                    record["storage_policy"][category] = "instance-local"
                else:
                    record["storage_policy"].setdefault(category, "instance-local")
            record.setdefault("storage_paths", {})
            for category, path in chosen.items():
                record["storage_paths"][category] = {
                    "path": str(path), "status": "available",
                }
            try:
                self.service.replace_instance(record)
            except Exception:
                # If the INI changed but registry metadata did not, restore the
                # exact pre-change INI bytes from the backup.
                self._restore_ini_atomically(backup, config)
                raise
            self._refresh_registered()
            QMessageBox.information(
                self, "Storage settings saved",
                f"All six MO2 storage paths were updated. No data was moved.\nINI backup: {backup}"
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
            self.accept()

    def _browse_game(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Select game installation directory", self.game_path_edit.text()
        )
        if selected:
            self.game_path_edit.setText(selected)

    def _browse_runtime(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Select Proton runtime directory", self.proton_edit.text()
        )
        if selected:
            self.proton_edit.setText(selected)

    def _browse_prefix(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Select initialized Proton prefix", self.prefix_edit.text()
        )
        if selected:
            self.prefix_edit.setText(selected)

    def _browse_install(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Select MO2 installation folder", self.install_edit.text()
        )
        if selected:
            self.install_edit.setText(selected)

    def discover(self) -> None:
        prefix = Path(self.prefix_edit.text()).expanduser()
        install = Path(self.install_edit.text()).expanduser()
        self.instances.clear()
        self.register_button.setEnabled(False)
        self.details.clear()
        if not self.game_path_edit.text():
            QMessageBox.warning(self, "MO2 discovery", "Select/detect Skyrim Special Edition in Wyrd first.")
            return
        self.discover_button.setEnabled(False)
        try:
            result = self.service.discover(prefix, install)
        except Exception as error:
            result = {"ok": False, "message": str(error), "proposals": []}
        finally:
            self.discover_button.setEnabled(True)
        self.discovery = result
        if not result.get("ok"):
            self.details.setPlainText(result.get("message", "MO2 discovery failed."))
            return

        installation = result.get("installation", {})
        lines = [
            result.get("message", "Read-only discovery complete."),
            f"Installation owner: {installation.get('owner', 'unknown')}",
            f"Executable: {installation.get('executable', 'unknown')}",
            "Ownership is not transferred by registration.",
        ]
        for shared in result.get("shared_paths", []):
            lines.append(f"SHARED PATH: {shared.get('path')} — {shared.get('references')}")
        for error in result.get("errors", []):
            lines.append(f"Inspection warning: {error}")
        proposals = result.get("proposals", [])
        if not proposals:
            lines.append("No readable MO2 instances were found in the selected roots.")
        try:
            registered = self.service.load().get("instances", [])
            registered_paths = {
                os.path.normcase(os.path.realpath(record.get("instance_path", "")))
                for record in registered
            }
        except Exception as error:
            registered_paths = set()
            lines.append(f"Could not compare the Wyrd registry: {error}")
        self.details.setPlainText("\n".join(lines))
        for proposal in proposals:
            identity = os.path.normcase(os.path.realpath(proposal.get("instance_path", "")))
            if identity in registered_paths:
                proposal["registration_status"] = "registered"
            kind = proposal.get("instance_type", "unknown")
            game = proposal.get("game_name") or "Game not identified"
            owner = proposal.get("owner", "unknown")
            status = "REGISTERED" if proposal.get("registration_status") == "registered" else "DISCOVERED"
            item = QListWidgetItem(
                f"{status}  ·  {kind}  ·  {game}  ·  {owner}"
            )
            item.setData(Qt.ItemDataRole.UserRole, proposal)
            self.instances.addItem(item)
        self.register_button.setEnabled(bool(proposals))
        if proposals:
            self.instances.setCurrentRow(0)

    def _show_selected(self, row: int) -> None:
        item = self.instances.item(row) if row >= 0 else None
        proposal = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not proposal:
            self.register_button.setEnabled(False)
            return
        paths = proposal.get("storage", {})
        lines = [
            f"Instance: {proposal.get('instance_path')}",
            f"Type: {proposal.get('instance_type')}",
            f"MO2 game: {proposal.get('game_name') or 'not identified'}",
            f"Owner: {proposal.get('owner')} (registration does not change ownership)",
            f"Config: {proposal.get('config')}",
            "Storage paths (read-only observations):",
        ]
        for category, info in paths.items():
            lines.append(
                f"  {category}: {info.get('path', 'unknown')} [{info.get('status', 'unknown')}]"
            )
        lines.append("Default policy on registration: all storage remains instance-local.")
        self.details.setPlainText("\n".join(lines))
        self.register_button.setEnabled(proposal.get("registration_status") != "registered")

    def register_selected(self) -> None:
        item = self.instances.currentItem()
        proposal = item.data(Qt.ItemDataRole.UserRole) if item else None
        installation = self.discovery.get("installation")
        if not proposal or not installation or proposal.get("registration_status") == "registered":
            return
        game_id = self.game_id_edit.text().strip()
        game_name = self.game_name_edit.text().strip()
        game_path = self.game_path_edit.text().strip()
        app_id = self.game_appid_edit.text().strip()
        if app_id and not app_id.isdigit():
            QMessageBox.warning(self, "Register MO2 instance", "Steam AppID must contain digits only, or be blank for a non-Steam game.")
            return
        if app_id == "489830" and "skyrim" not in game_name.casefold():
            QMessageBox.warning(
                self, "Register MO2 instance",
                "The AppID field still contains Skyrim's 489830. Clear it for a non-Steam game or enter the correct Steam AppID."
            )
            return
        if game_id == "steam-489830" and app_id != "489830":
            identity = hashlib.sha256(
                (str(Path(game_path).expanduser().resolve()) + "|" + app_id).encode()
            ).hexdigest()[:16]
            game_id = f"steam-{app_id}-{identity}" if app_id else f"game-{identity}"
        # The same Steam AppID may be installed in multiple Steam libraries.
        # Keep the legacy Skyrim ID for its first canonical registration, but
        # disambiguate a second physical copy by its resolved game path.
        if app_id:
            try:
                existing_games = self.service.load().get("games", [])
                resolved_game = str(Path(game_path).expanduser().resolve(strict=True))
                collision = next((
                    game for game in existing_games
                    if game.get("id") == game_id
                    and str(game.get("path", "")) != resolved_game
                ), None)
                if collision:
                    identity = hashlib.sha256(
                        (resolved_game + "|" + app_id).encode()
                    ).hexdigest()[:16]
                    game_id = f"steam-{app_id}-{identity}"
            except (OSError, RuntimeError):
                pass
        selected_prefix = Path(self.prefix_edit.text()).expanduser()
        discovered_install = self.discovery.get("installation", {})
        discovered_prefix = Path(discovered_install.get("prefix_path", "")).expanduser()
        discovered_install_root = Path(discovered_install.get("executable", "")).expanduser().parent
        current_install_root = Path(self.install_edit.text()).expanduser()
        try:
            same_prefix = selected_prefix.resolve(strict=True) == discovered_prefix.resolve(strict=True)
            same_install = current_install_root.resolve(strict=True) == discovered_install_root.resolve(strict=True)
        except (OSError, RuntimeError):
            same_prefix = same_install = False
        if not same_prefix or not same_install:
            QMessageBox.warning(
                self, "Discovery is out of date",
                "The selected prefix or MO2 installation changed after discovery. Run Discover MO2 again before registering."
            )
            return
        if selected_prefix.is_symlink() or not (selected_prefix / "pfx/drive_c/users").is_dir():
            QMessageBox.warning(
                self, "Register MO2 instance",
                "The selected Proton prefix is missing or not initialized."
            )
            return
        if not game_id or not game_name or not game_path or not Path(game_path).expanduser().is_dir():
            QMessageBox.warning(self, "Register MO2 instance", "Game name, association ID, and an existing game directory are required.")
            return
        proton_path = self.proton_edit.text().strip()
        if not proton_path or Path(proton_path).is_symlink() or not (Path(proton_path) / "proton").is_file():
            QMessageBox.warning(self, "Register MO2 instance", "Select a valid Proton runtime before registering.")
            return
        answer = QMessageBox.question(
            self, "Register MO2 instance",
            "Register this instance in Wyrd's local registry? This records its paths and "
            "game/runtime association only. It does not modify MO2, its profiles, mods, "
            "downloads, saves, or Proton prefix.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        steam_root = ""
        if app_id:
            game_dir = Path(game_path).expanduser().resolve()
            if game_dir.parent.name.casefold() == "common" and game_dir.parent.parent.name.casefold() == "steamapps":
                steam_root = str(game_dir.parent.parent.parent)
        game = {
            "id": game_id, "name": game_name, "app_id": app_id,
            "path": game_path, "steam_root": steam_root,
            "prefix_path": str(selected_prefix.resolve(strict=True)),
        }
        runtime = None
        proton_path = self.proton_edit.text().strip()
        if proton_path:
            identity = hashlib.sha256(str(Path(proton_path).expanduser()).encode()).hexdigest()[:16]
            runtime = {
                "id": f"proton-{identity}", "name": Path(proton_path).name,
                "path": str(Path(proton_path).expanduser()),
            }
        try:
            self.service.register(installation, proposal, game, runtime)
        except Exception as error:
            QMessageBox.warning(self, "Registration failed", str(error))
            return

        self.registered = True
        self.preferred_instance_id = proposal.get("id", "")
        registry = self.service.load()
        self.selected_instance_record = next(
            (record for record in registry["instances"] if record.get("id") == self.preferred_instance_id),
            None,
        )
        self._refresh_registered()
        for row in range(self.instances.count()):
            candidate = self.instances.item(row)
            data = candidate.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("id") == proposal.get("id"):
                data["registration_status"] = "registered"
                candidate.setData(Qt.ItemDataRole.UserRole, data)
                candidate.setText(candidate.text().replace("DISCOVERED", "REGISTERED"))
                self.instances.setCurrentRow(row)
                break
        self.register_button.setEnabled(False)
        self.details.appendPlainText("\n\nRegistered successfully in Wyrd's local MO2 registry.")
        QMessageBox.information(self, "MO2 instance registered", "Wyrd metadata was saved. No MO2 files or game data were changed.")
