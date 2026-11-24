"""MO2 Environment picker and Advanced configuration widgets."""
from __future__ import annotations

import configparser
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QInputDialog, QVBoxLayout, QWidget,
)

from wyrd_mo2 import managed_paths
from wyrd_mo2_registry_service import RegistryService


class Mo2LibraryDialog(QWidget):
    """Compact Environment widget for finding and selecting MO2 instances."""

    def __init__(self, parent=None, *, prefix_path="", install_path="", game_path="", proton_path="", selected_instance_id=""):
        super().__init__(parent)
        self.setObjectName("mo2InstancePicker")
        self.service = RegistryService(managed_paths()["config"] / "mo2-registry.json")
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")
        self.preferred_instance_id = selected_instance_id
        self.discovery: dict = {}
        self.selected_instance_record: dict | None = None
        self.selection_changed = False
        self.registered = False
        self.on_registry_changed = None
        self.on_launch_requested = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.intro = QLabel("Connect an existing MO2 instance to Wyrd. Your MO2 files will not be changed.")
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.setup_status = QLabel()
        self.setup_status.setObjectName("muted")
        self.setup_status.setWordWrap(True)
        layout.addWidget(self.setup_status)

        self.discover_button = QPushButton("Find existing MO2")
        self.discover_button.clicked.connect(self.discover)
        layout.addWidget(self.discover_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.instances = QListWidget()
        self.instances.setMinimumHeight(55)
        self.instances.setMaximumHeight(120)
        self.instances.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.instances.currentRowChanged.connect(self._show_selected)
        self.instances.hide()
        layout.addWidget(self.instances)

        self.register_button = QPushButton("Add to Wyrd")
        self.register_button.clicked.connect(self.register_selected)
        self.register_button.setEnabled(False)
        self.register_button.hide()
        layout.addWidget(self.register_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.connected_label = QLabel()
        self.connected_label.setObjectName("muted")
        self.connected_label.setWordWrap(True)
        layout.addWidget(self.connected_label)
        self._refresh_connected()

    def update_context(self, *, prefix_path="", install_path="", game_path="", proton_path=""):
        self.prefix_path = str(prefix_path or "")
        self.install_path = str(install_path or "")
        self.game_path = str(game_path or "")
        self.proton_path = str(proton_path or "")

    def _registry_maps(self):
        registry = self.service.load()
        return (
            registry,
            {x["id"]: x for x in registry["installations"]},
            {x["id"]: x for x in registry["games"]},
            {x["id"]: x for x in registry["runtimes"]},
        )

    def _refresh_connected(self):
        self.connected_label.clear()
        try:
            registry, installations, games, runtimes = self._registry_maps()
        except Exception as error:
            self.setup_status.setText(f"Could not load MO2 connections: {error}")
            return
        self.connected_records = registry["instances"]
        record = next((x for x in self.connected_records if x.get("id") == self.preferred_instance_id), None)
        if record:
            game = games.get(record.get("game_id"), {})
            self.setup_status.setText(f"Connected: {record.get('display_name') or game.get('name', 'MO2')}")
            self.connected_label.setText("Your MO2 instance is connected. Use Launch MO2 or Launch Modded Skyrim above.")
        elif self.connected_records:
            self.setup_status.setText(f"{len(self.connected_records)} MO2 instance(s) connected. Choose one below.")
            self.connected_label.setText("Select a connected instance to make it active.")
            self.instances.clear()
            for item in self.connected_records:
                game = games.get(item.get("game_id"), {})
                row = QListWidgetItem(record_name := f"{item.get('display_name') or game.get('name', 'MO2')} · {item.get('instance_type', 'instance')}")
                row.setData(Qt.ItemDataRole.UserRole, item)
                self.instances.addItem(row)
            self.instances.show()
        else:
            self.setup_status.setText("MO2 is not connected yet. Find your existing instance to get started.")
            self.connected_label.setText("")

    def _select_connected(self, row: int):
        item = self.instances.item(row) if row >= 0 else None
        record = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record, dict):
            self.selected_instance_record = None
            return
        self.selected_instance_record = dict(record)
        self.selection_changed = True
        self.preferred_instance_id = record.get("id", "")
        if callable(self.on_registry_changed):
            self.on_registry_changed(self.selected_instance_record, False)

    def discover(self):
        self.instances.clear()
        self.instances.hide()
        self.register_button.hide()
        self.register_button.setEnabled(False)
        if not self.game_path or not self.prefix_path or not self.install_path:
            self.setup_status.setText("Wyrd needs to detect Skyrim and its Proton prefix before searching.")
            return
        self.discover_button.setEnabled(False)
        try:
            result = self.service.discover(Path(self.prefix_path).expanduser(), Path(self.install_path).expanduser())
        except Exception as error:
            result = {"ok": False, "message": str(error), "proposals": []}
        finally:
            self.discover_button.setEnabled(True)
        self.discovery = result
        if not result.get("ok"):
            self.setup_status.setText(result.get("message", "Could not find MO2 instances."))
            return
        try:
            registered_paths = {
                os.path.normcase(os.path.realpath(item.get("instance_path", "")))
                for item in self.service.load().get("instances", [])
            }
        except Exception as error:
            self.setup_status.setText(f"Could not check saved instances: {error}")
            return
        proposals = result.get("proposals", [])
        for proposal in proposals:
            identity = os.path.normcase(os.path.realpath(proposal.get("instance_path", "")))
            already_registered = identity in registered_paths
            proposal["registration_status"] = "registered" if already_registered else "discovered"
            game = proposal.get("game_name") or "Skyrim Special Edition"
            kind = proposal.get("instance_type", "instance")
            row = QListWidgetItem(f"{game} · {kind}" + (" · Already added" if already_registered else ""))
            row.setData(Qt.ItemDataRole.UserRole, proposal)
            self.instances.addItem(row)
        if proposals:
            self.instances.show()
            self.instances.setCurrentRow(0)
            self.setup_status.setText(f"Found {len(proposals)} MO2 instance(s). Select one to add it to Wyrd.")
        else:
            self.setup_status.setText("MO2 was found, but no instances were found in its usual folders.")

    def _show_selected(self, row: int):
        item = self.instances.item(row) if row >= 0 else None
        proposal = item.data(Qt.ItemDataRole.UserRole) if item else None
        is_new = isinstance(proposal, dict) and proposal.get("registration_status") != "registered"
        self.register_button.setVisible(bool(is_new))
        self.register_button.setEnabled(bool(is_new))

    def register_selected(self):
        item = self.instances.currentItem()
        proposal = item.data(Qt.ItemDataRole.UserRole) if item else None
        installation = self.discovery.get("installation")
        if not isinstance(proposal, dict) or not installation or proposal.get("registration_status") == "registered":
            return
        game_path = Path(self.game_path).expanduser()
        prefix = Path(self.prefix_path).expanduser()
        install = Path(self.install_path).expanduser()
        runtime = Path(self.proton_path).expanduser()
        game_id, game_name, app_id = "steam-489830", "Skyrim Special Edition", "489830"
        if not game_path.is_dir():
            self.setup_status.setText("Skyrim's game folder could not be found. Refresh detection and try again.")
            return
        if prefix.is_symlink() or not (prefix / "pfx/drive_c/users").is_dir():
            self.setup_status.setText("The Skyrim Proton prefix is missing or unsafe.")
            return
        if runtime.is_symlink() or not (runtime / "proton").is_file():
            self.setup_status.setText("Choose a valid Proton runtime in Environment before adding this instance.")
            return
        discovered_install = self.discovery.get("installation", {})
        discovered_prefix = Path(discovered_install.get("prefix_path", "")).expanduser()
        discovered_root = Path(discovered_install.get("executable", "")).expanduser().parent
        try:
            if prefix.resolve(strict=True) != discovered_prefix.resolve(strict=True) or install.resolve(strict=True) != discovered_root.resolve(strict=True):
                self.setup_status.setText("The detected MO2 location changed. Search again before adding it.")
                return
        except (OSError, RuntimeError):
            self.setup_status.setText("The detected MO2 location is no longer available. Search again.")
            return
        try:
            current_games = self.service.load().get("games", [])
            resolved_game = str(game_path.resolve(strict=True))
            collision = next((x for x in current_games if x.get("id") == game_id and str(x.get("path", "")) != resolved_game), None)
            if collision:
                identity = hashlib.sha256((resolved_game + "|" + app_id).encode()).hexdigest()[:16]
                game_id = f"steam-{app_id}-{identity}"
        except (OSError, RuntimeError):
            pass
        answer = QMessageBox.question(
            self, "Add MO2 to Wyrd",
            "Add this instance to Wyrd? This saves its location and game settings only. MO2 files, profiles, mods, downloads, saves and the Proton prefix stay unchanged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        game = {
            "id": game_id, "name": game_name, "app_id": app_id,
            "path": str(game_path.resolve(strict=True)),
            "steam_root": str(game_path.resolve(strict=True).parent.parent.parent)
                if game_path.resolve(strict=True).parent.name.casefold() == "common"
                and game_path.resolve(strict=True).parent.parent.name.casefold() == "steamapps" else "",
            "prefix_path": str(prefix.resolve(strict=True)),
        }
        runtime_id = "proton-" + hashlib.sha256(str(runtime.resolve(strict=True)).encode()).hexdigest()[:16]
        runtime_record = {"id": runtime_id, "name": runtime.name, "path": str(runtime.resolve(strict=True))}
        try:
            self.service.register(installation, proposal, game, runtime_record)
        except Exception as error:
            QMessageBox.warning(self, "Could not add MO2", str(error))
            return
        self.registered = True
        self.preferred_instance_id = proposal.get("id", "")
        registry = self.service.load()
        self.selected_instance_record = next((x for x in registry["instances"] if x.get("id") == self.preferred_instance_id), None)
        self.selection_changed = True
        if callable(self.on_registry_changed):
            self.on_registry_changed(self.selected_instance_record, True)
        self.setup_status.setText("MO2 is connected to Wyrd. You can launch it from the main window.")
        self.register_button.hide()
        self._refresh_connected()



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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.prefix_edit = QLineEdit()
        self.install_edit = QLineEdit()
        self.game_name_edit = QLineEdit("Skyrim Special Edition")
        self.game_id_edit = QLineEdit("steam-489830")
        self.game_appid_edit = QLineEdit("489830")
        self.game_path_edit = QLineEdit()
        self.proton_edit = QLineEdit()
        for field in (self.prefix_edit,self.install_edit,self.game_name_edit,self.game_id_edit,
                      self.game_appid_edit,self.game_path_edit,self.proton_edit):
            field.setMinimumHeight(30)
        def browse_row(field, title):
            row=QHBoxLayout(); row.addWidget(field,1)
            button=QPushButton("Browse…")
            button.clicked.connect(lambda: self._browse_directory(title, field))
            row.addWidget(button)
            return row
        form.addRow("Proton prefix:", browse_row(self.prefix_edit,"Select Proton prefix"))
        form.addRow("MO2 folder:", browse_row(self.install_edit,"Select MO2 folder"))
        form.addRow("Game name:",self.game_name_edit)
        form.addRow("Game ID:",self.game_id_edit)
        form.addRow("Steam AppID:",self.game_appid_edit)
        form.addRow("Game folder:",browse_row(self.game_path_edit,"Select game folder"))
        form.addRow("Proton runtime:",browse_row(self.proton_edit,"Select Proton runtime"))
        layout.addWidget(QLabel("MO2 paths and game settings"))
        layout.addLayout(form)
        self.installation_filter=QComboBox()
        self.installation_filter.addItem("All installations",None)
        self.installation_filter.currentIndexChanged.connect(self._refresh_registered)
        layout.addWidget(QLabel("Registered installations"))
        layout.addWidget(self.installation_filter)
        self.installation_summary=QLabel("Select an installation.")
        self.installation_summary.setWordWrap(True)
        layout.addWidget(self.installation_summary)
        self.registered_instances=QListWidget()
        self.registered_instances.setMaximumHeight(120)
        self.registered_instances.currentRowChanged.connect(self._select_registered)
        layout.addWidget(QLabel("Registered instances"))
        layout.addWidget(self.registered_instances)
        self._edit_associations_button=QPushButton("Change game or Proton")
        self._edit_associations_button.clicked.connect(self.edit_associations)
        layout.addWidget(self._edit_associations_button)
        self.rename_instance_button=QPushButton("Rename in Wyrd")
        self.rename_instance_button.clicked.connect(self.rename_selected_instance)
        layout.addWidget(self.rename_instance_button)
        actions=QHBoxLayout()
        self.unregister_instance_button=QPushButton("Remove instance from Wyrd")
        self.unregister_instance_button.clicked.connect(self.unregister_selected_instance)
        self.unregister_installation_button=QPushButton("Remove unused installation from Wyrd")
        self.unregister_installation_button.clicked.connect(self.unregister_selected_installation)
        actions.addWidget(self.unregister_instance_button); actions.addWidget(self.unregister_installation_button)
        layout.addLayout(actions)

        storage_box=QGroupBox("Storage locations")
        storage_form=QFormLayout(storage_box)
        self.storage_fields={}; self.storage_browse_buttons={}
        for key,label in (("base","Base"),("mods","Mods"),("downloads","Downloads"),("cache","Cache"),("profiles","Profiles"),("overwrite","Overwrite")):
            row=QHBoxLayout(); field=QLineEdit(); field.setReadOnly(True)
            browse=QPushButton("Browse…"); browse.clicked.connect(lambda _checked=False, category=key: self._choose_storage_path(category))
            row.addWidget(field,1); row.addWidget(browse); self.storage_fields[key]=field; self.storage_browse_buttons[key]=browse
            storage_form.addRow(label,row)
        layout.addWidget(storage_box)
        self.share_mods_check=QCheckBox("Share Mods folder")
        self.share_downloads_check=QCheckBox("Share Downloads folder")
        self.create_storage_check=QCheckBox("Create missing folders (existing files are never moved or copied)")
        layout.addWidget(self.share_mods_check); layout.addWidget(self.share_downloads_check); layout.addWidget(self.create_storage_check)
        storage_actions=QHBoxLayout()
        self.apply_storage_button=QPushButton("Save storage settings")
        self.restore_storage_button=QPushButton("Restore settings backup…")
        self.apply_storage_button.clicked.connect(self.apply_storage)
        self.restore_storage_button.clicked.connect(self.restore_config_backup)
        storage_actions.addWidget(self.apply_storage_button); storage_actions.addWidget(self.restore_storage_button)
        layout.addLayout(storage_actions)
        self._refresh_registered()

    def _browse_directory(self, title, field):
        selected=QFileDialog.getExistingDirectory(self,title,field.text())
        if selected: field.setText(selected)

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
        selected_filter=self.installation_filter.currentData() if self.installation_filter.count() else None
        self.registered_instances.clear(); self.selected_instance_record=None
        try:
            registry, installations, games, _runtimes=self._registry_maps()
        except Exception as error:
            self.registered_instances.addItem(f"Registry unavailable: {error}"); return
        self.installation_filter.blockSignals(True); self.installation_filter.clear()
        self.installation_filter.addItem("All installations",None)
        for installation in sorted(registry["installations"],key=lambda item: Path(item.get("executable","")).parent.name.casefold()):
            executable=Path(installation.get("executable",""))
            self.installation_filter.addItem(f"{executable.parent.name or 'MO2'} · {installation.get('owner','unknown')} · {installation.get('executable','')}",installation.get("id"))
        selected_index=self.installation_filter.findData(selected_filter)
        self.installation_filter.setCurrentIndex(max(0,selected_index)); self.installation_filter.blockSignals(False)
        active_filter=self.installation_filter.currentData()
        selected_installation=installations.get(active_filter) if active_filter else None
        if selected_installation:
            references=sum(1 for item in registry["instances"] if item.get("installation_id")==active_filter)
            self.installation_summary.setText(f"{selected_installation.get('executable')}\nOwner: {selected_installation.get('owner','unknown')} · Instances: {references}\nRemoving a reference never deletes files.")
            self.unregister_installation_button.setEnabled(references==0)
        else:
            self.installation_summary.setText(f"{len(registry['installations'])} installation(s) · {len(registry['instances'])} instance(s)")
            self.unregister_installation_button.setEnabled(False)
        active=self.installation_filter.currentData()
        for record in registry["instances"]:
            if active and record.get("installation_id")!=active: continue
            game=games.get(record.get("game_id"),{})
            row=QListWidgetItem(f"{record.get('display_name') or game.get('name','MO2')} · {record.get('instance_type','instance')}")
            row.setData(Qt.ItemDataRole.UserRole,record); self.registered_instances.addItem(row)
        self._edit_associations_button.setEnabled(False); self.rename_instance_button.setEnabled(False)
        self.unregister_instance_button.setEnabled(False); self.apply_storage_button.setEnabled(False); self.restore_storage_button.setEnabled(False)

    def _select_registered(self,row):
        item=self.registered_instances.item(row) if row>=0 else None
        record=item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record,dict):
            self.selected_instance_record=None
            return
        self.selected_instance_record=dict(record); self.preferred_instance_id=record.get("id","")
        self._edit_associations_button.setEnabled(True); self.rename_instance_button.setEnabled(True); self.unregister_instance_button.setEnabled(True)
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
        self.create_storage_check.setEnabled(writable); self.share_mods_check.setEnabled(writable); self.share_downloads_check.setEnabled(writable)
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
            self.details.setPlainText(
                "Wyrd display name updated. MO2's instance directory, INI, profiles, mods, "
                "downloads and game files were not changed."
            )
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
            self.details.setPlainText(
                "Instance reference removed from Wyrd. No MO2 or game files were changed."
            )
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
            self.details.setPlainText(
                "Unused installation reference removed from Wyrd. No files were deleted."
            )
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


