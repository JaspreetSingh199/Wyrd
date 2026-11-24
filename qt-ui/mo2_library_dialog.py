"""Qt interface for discovering and explicitly registering MO2 installations/instances."""
from __future__ import annotations

import hashlib
import os
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

        root.addWidget(QLabel("Already registered with Wyrd"))
        self.registered_instances = QListWidget()
        self.registered_instances.setMaximumHeight(120)
        root.addWidget(self.registered_instances)
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

        footer = QHBoxLayout()
        self.register_button = QPushButton("Register selected instance")
        self.register_button.setEnabled(False)
        self.register_button.clicked.connect(self.register_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(self.register_button)
        footer.addStretch(1)
        footer.addWidget(close)
        root.addLayout(footer)

    def _refresh_registered(self) -> None:
        self.registered_instances.clear()
        try:
            registry = self.service.load()
        except Exception as error:
            self.registered_instances.addItem(f"Registry unavailable: {error}")
            return
        games = {item.get("id"): item.get("name", item.get("id")) for item in registry.get("games", [])}
        installations = {item.get("id"): item for item in registry.get("installations", [])}
        self.registered_instances.addItem(
            f"{len(registry.get('installations', []))} installation(s) · "
            f"{len(registry.get('instances', []))} instance(s) registered"
        )
        for instance in registry.get("instances", []):
            installation = installations.get(instance.get("installation_id"), {})
            owner = installation.get("owner", "unknown")
            game = games.get(instance.get("game_id"), instance.get("game_id", "unknown game"))
            self.registered_instances.addItem(
                f"{instance.get('instance_type', 'unknown')} · {game} · {owner}\n"
                f"{instance.get('instance_path', '')}"
            )

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
