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
    QPlainTextEdit, QVBoxLayout,
)

from wyrd_mo2 import managed_paths
from wyrd_mo2_registry_service import RegistryService


class Mo2LibraryDialog(QDialog):
    """Discover existing MO2 resources and register metadata without modifying MO2."""

    def __init__(
        self, parent=None, *, prefix_path: Path | str = "",
        install_path: Path | str = "", game_path: Path | str = "",
        proton_path: Path | str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Wyrd — MO2 Library and Instances")
        self.resize(820, 680)
        self.service = RegistryService(managed_paths()["config"] / "mo2-registry.json")
        self.discovery: dict = {}
        self.registered = False

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
        self.prefix_edit.setReadOnly(True)
        self.install_edit = QLineEdit(str(install_path))
        install_row = QHBoxLayout()
        install_row.addWidget(self.install_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_install)
        install_row.addWidget(browse)
        form.addRow("Steam compatdata prefix:", self.prefix_edit)
        form.addRow("MO2 installation folder:", install_row)

        self.game_name_edit = QLineEdit("Skyrim Special Edition")
        self.game_id_edit = QLineEdit("steam-489830")
        self.game_path_edit = QLineEdit(str(game_path))
        self.game_path_edit.setReadOnly(True)
        self.proton_edit = QLineEdit(str(proton_path or ""))
        self.proton_edit.setReadOnly(True)
        form.addRow("Game:", self.game_name_edit)
        form.addRow("Game association ID:", self.game_id_edit)
        form.addRow("Game directory:", self.game_path_edit)
        form.addRow("Selected Proton runtime:", self.proton_edit)
        root.addLayout(form)

        actions = QHBoxLayout()
        self.discover_button = QPushButton("Discover MO2")
        self.discover_button.clicked.connect(self.discover)
        actions.addWidget(self.discover_button)
        actions.addStretch(1)
        root.addLayout(actions)

        root.addWidget(QLabel("Registered MO2 instances — select to manage or launch"))
        self.registered_instances = QListWidget()
        self.registered_instances.currentRowChanged.connect(self._select_registered)
        root.addWidget(self.registered_instances, 1)
        self._refresh_registered()

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
        for key, label in (("base", "Base"), ("mods", "Mods"), ("downloads", "Downloads"),
                           ("cache", "Cache"), ("profiles", "Profiles"), ("overwrite", "Overwrite")):
            field = QLineEdit()
            field.setReadOnly(True)
            self.storage_fields[key] = field
            storage_form.addRow(label, field)
        root.addWidget(storage_box)

        self.share_mods_check = QCheckBox("Explicitly share Mods directory")
        self.share_downloads_check = QCheckBox("Explicitly share Downloads directory")
        root.addWidget(self.share_mods_check)
        root.addWidget(self.share_downloads_check)
        storage_actions = QHBoxLayout()
        self.choose_mods_button = QPushButton("Choose shared Mods…")
        self.choose_downloads_button = QPushButton("Choose shared Downloads…")
        self.apply_storage_button = QPushButton("Apply storage settings")
        for control in (self.choose_mods_button, self.choose_downloads_button, self.apply_storage_button):
            control.setEnabled(False)
            storage_actions.addWidget(control)
        self.choose_mods_button.clicked.connect(lambda: self._choose_shared("mods"))
        self.choose_downloads_button.clicked.connect(lambda: self._choose_shared("downloads"))
        self.apply_storage_button.clicked.connect(self.apply_storage)
        root.addLayout(storage_actions)

        footer = QHBoxLayout()
        self.register_button = QPushButton("Register discovered instance")
        self.register_button.setEnabled(False)
        self.register_button.clicked.connect(self.register_selected)
        self.launch_selected_button = QPushButton("Launch registered instance")
        self.launch_selected_button.setEnabled(False)
        self.launch_selected_button.clicked.connect(self.launch_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(self.register_button)
        footer.addWidget(self.launch_selected_button)
        footer.addStretch(1)
        footer.addWidget(close)
        root.addLayout(footer)

    def _registry_maps(self):
        registry = self.service.load()
        return (
            registry,
            {x["id"]: x for x in registry["installations"]},
            {x["id"]: x for x in registry["games"]},
            {x["id"]: x for x in registry["runtimes"]},
        )

    def _refresh_registered(self) -> None:
        self.registered_instances.clear()
        self.selected_instance_record = None
        try:
            registry, installations, games, _runtimes = self._registry_maps()
        except Exception as error:
            self.registered_instances.addItem(f"Registry unavailable: {error}")
            return
        if not registry["instances"]:
            self.registered_instances.addItem("No MO2 instances registered yet.")
            return
        for record in registry["instances"]:
            installation = installations.get(record.get("installation_id"), {})
            game = games.get(record.get("game_id"), {})
            item = QListWidgetItem(
                f"{game.get('name', 'Unknown game')} · {record.get('instance_type', 'unknown')} · "
                f"{installation.get('owner', 'unknown')}\n{record.get('instance_path', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.registered_instances.addItem(item)

    def _select_registered(self, row: int) -> None:
        item = self.registered_instances.item(row) if row >= 0 else None
        record = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record, dict):
            self.selected_instance_record = None
            self.launch_selected_button.setEnabled(False)
            self.apply_storage_button.setEnabled(False)
            return
        self.selected_instance_record = record
        try:
            registry, installations, games, runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            game = games[record["game_id"]]
            runtime = runtimes.get(record.get("runtime_id"), {})
        except Exception as error:
            self.details.setPlainText(f"Registry entry is incomplete: {error}")
            self.launch_selected_button.setEnabled(False)
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
        self.launch_selected_button.setEnabled(bool(
            installation.get("executable") and game.get("path") and runtime.get("path")
        ))
        self.apply_storage_button.setEnabled(bool(record.get("config")))

    def _choose_shared(self, category: str) -> None:
        path = QFileDialog.getExistingDirectory(
            self, f"Select shared {category} directory", self.storage_fields[category].text()
        )
        if path:
            self.storage_fields[category].setText(path)
            (self.share_mods_check if category == "mods" else self.share_downloads_check).setChecked(True)

    def apply_storage(self) -> None:
        record = self.selected_instance_record
        if not record:
            return
        try:
            registry, installations, _games, _runtimes = self._registry_maps()
            installation = installations[record["installation_id"]]
            if installation.get("owner") != "wyrd":
                QMessageBox.warning(
                    self, "External MO2 is read-only",
                    "Wyrd will not modify ModOrganizer.ini for an externally owned installation."
                )
                return
            config = Path(record.get("config", "")).expanduser()
            if config.is_symlink() or not config.is_file():
                raise ValueError("The instance ModOrganizer.ini is missing or symlinked.")
            shared = {
                "mods": self.share_mods_check.isChecked(),
                "downloads": self.share_downloads_check.isChecked(),
            }
            chosen = {key: self.storage_fields[key].text().strip() for key in shared}
            for category, enabled in shared.items():
                if not enabled:
                    continue
                candidate = Path(chosen[category]).expanduser()
                if candidate.is_symlink() or not candidate.is_dir():
                    raise ValueError(f"Shared {category} must already exist and must not be a symlink.")
                resolved = candidate.resolve(strict=True)
                instance_root = Path(record["instance_path"]).resolve(strict=True)
                if resolved == instance_root or instance_root in resolved.parents:
                    raise ValueError(f"Shared {category} cannot be inside this instance directory.")
                for other in registry["instances"]:
                    if other.get("id") == record["id"]:
                        continue
                    other_path = other.get("storage_paths", {}).get(category, {}).get("path")
                    if other_path and os.path.normcase(os.path.realpath(other_path)) == os.path.normcase(str(resolved)):
                        raise ValueError(
                            f"Shared {category} conflicts with another registered instance's configured directory. "
                            "Review that instance before sharing this path."
                        )
            answer = QMessageBox.question(
                self, "Apply storage settings",
                "Update only this instance's MO2 path settings? This does not move, merge, "
                "or delete any files. The destination must already contain the data MO2 should use. "
                "Profiles and Overwrite remain instance-local. Continue?",
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
            existing = record.get("storage_paths", {})
            base = Path(existing.get("base", {}).get("path") or record["instance_path"]).expanduser()
            if base.is_symlink() or not base.is_dir():
                raise ValueError("Base directory must already exist and must not be a symlink.")
            parser["Settings"]["base_directory"] = str(base)
            parser["Settings"]["mod_directory"] = chosen["mods"] if shared["mods"] else str(base / "mods")
            parser["Settings"]["download_directory"] = chosen["downloads"] if shared["downloads"] else str(base / "downloads")
            parser["Settings"]["profiles_directory"] = str(existing.get("profiles", {}).get("path") or base / "profiles")
            parser["Settings"]["cache_directory"] = str(existing.get("cache", {}).get("path") or base / "webcache")
            parser["Settings"]["overwrite_directory"] = str(existing.get("overwrite", {}).get("path") or base / "overwrite")
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
            record["storage_policy"]["mods"] = "shared" if shared["mods"] else "instance-local"
            record["storage_policy"]["downloads"] = "shared" if shared["downloads"] else "instance-local"
            record.setdefault("storage_paths", {})
            for category in ("mods", "downloads"):
                path = chosen[category] if shared[category] else str(base / category)
                record["storage_paths"][category] = {
                    "path": path, "status": "available" if Path(path).is_dir() else "missing"
                }
            self.service.replace_instance(record)
            self._refresh_registered()
            QMessageBox.information(
                self, "Storage settings saved",
                "The selected instance's MO2 path settings were updated. No files were moved."
            )
        except Exception as error:
            QMessageBox.warning(self, "Storage settings", str(error))

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
        if not game_id or not game_name or not game_path:
            QMessageBox.warning(self, "Register MO2 instance", "Game name, association ID, and game directory are required.")
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

        game = {
            "id": game_id, "name": game_name, "app_id": "489830",
            "path": game_path,
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
        self._refresh_registered()
        item.setText(item.text() + "  ·  REGISTERED")
        item.setData(Qt.ItemDataRole.UserRole, {**proposal, "registration_status": "registered"})
        self.register_button.setEnabled(False)
        self.details.appendPlainText("\n\nRegistered successfully in Wyrd's local MO2 registry.")
        QMessageBox.information(self, "MO2 instance registered", "Wyrd metadata was saved. No MO2 files or game data were changed.")
