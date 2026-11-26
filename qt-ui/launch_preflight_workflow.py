"""Read-only launch preflight checks and confirmation UI for MainWindow."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout,
)

from mo2_instances import inspect_mo2_instances
from steam_prefix import steam_compat_prefix


class LaunchPreflightWorkflowMixin:
    def _run_launch_preflight(
        self, instance_name: str, target_state: dict, selected_target: dict,
        game_path: Path, prefix: Path, install_root: Path, proton: Path,
        steam_root: Path | None,
    ) -> bool:
        """Show a visible, read-only checklist and require all checks before launch."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preparing modded Skyrim")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Launch pre-check")
        heading.setObjectName("section")
        layout.addWidget(heading)
        detail = QLabel("Wyrd is checking the selected environment before starting MO2.")
        detail.setWordWrap(True)
        detail.setObjectName("muted")
        layout.addWidget(detail)
        rows = []
        checks = [
            ("Skyrim installation", lambda: (
                not game_path.is_symlink() and game_path.is_dir(),
                f"Detected game folder: {game_path}",
            )),
            ("Skyrim Steam prefix", lambda: (
                not prefix.is_symlink()
                and prefix.is_dir()
                and (prefix / "pfx/drive_c/users").is_dir()
                and (steam_root is None or prefix.resolve(strict=True) == steam_compat_prefix(steam_root).resolve(strict=True)),
                f"AppID 489830 prefix: {prefix}",
            )),
            ("MO2 application", lambda: (
                not (install_root / "ModOrganizer.exe").is_symlink()
                and (install_root / "ModOrganizer.exe").is_file(),
                f"MO2 executable: {install_root / 'ModOrganizer.exe'}",
            )),
            ("Selected Proton runtime", lambda: (
                not proton.is_symlink()
                and proton.is_dir()
                and not (proton / "proton").is_symlink()
                and (proton / "proton").is_file()
                and os.access(proton / "proton", os.X_OK),
                f"Runtime: {proton}",
            )),
            ("Selected MO2 instance", lambda: self._preflight_instance_exists(
                prefix, install_root, instance_name
            )),
            ("Configured Skyrim executable", lambda: (
                bool(target_state.get("ok"))
                and any(item.get("title") == selected_target.get("title")
                        and item.get("binary") == selected_target.get("binary")
                        for item in target_state.get("targets", [])),
                f"MO2 target: {selected_target.get('title', 'Unknown')}",
            )),
        ]
        for label, _ in checks:
            checkbox = QCheckBox(label)
            checkbox.setEnabled(False)
            layout.addWidget(checkbox)
            rows.append(checkbox)
        progress = QProgressBar()
        progress.setRange(0, len(checks))
        progress.setValue(0)
        layout.addWidget(progress)
        status = QLabel("Starting checks…")
        status.setWordWrap(True)
        status.setObjectName("muted")
        layout.addWidget(status)
        buttons = QHBoxLayout()
        continue_button = QPushButton("Continue to launch")
        continue_button.setObjectName("primaryAction")
        continue_button.setEnabled(False)
        cancel_button = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)

        failed = False
        for index, (label, check) in enumerate(checks):
            detail.setText(f"Checking {label.lower()}…")
            QCoreApplication.processEvents()
            try:
                passed, message = check()
            except Exception as error:
                passed, message = False, str(error)
            rows[index].setChecked(bool(passed))
            rows[index].setText(f"{label} — {'OK' if passed else 'FAILED'}")
            progress.setValue(index + 1)
            status.setText(message)
            QCoreApplication.processEvents()
            if not passed:
                failed = True
                detail.setText("Pre-check failed. Wyrd will not launch Skyrim.")
                status.setText(f"{label}: {message}")
                break

        if failed:
            cancel_button.setText("Close")
            continue_button.hide()
            dialog.exec()
            return False
        detail.setText("All checks passed. Review the checklist, then continue.")
        status.setText(f"Ready to launch '{selected_target.get('title')}' through MO2 instance '{instance_name}'.")
        continue_button.setEnabled(True)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _preflight_instance_exists(prefix: Path, install_root: Path, instance_name: str) -> tuple[bool, str]:
        inventory = inspect_mo2_instances(prefix, install_root, require_managed=False)
        if not inventory.get("ok"):
            return False, inventory.get("message", "MO2 instance inventory is unavailable.")
        item = next((entry for entry in inventory.get("instances", [])
                     if entry.get("name") == instance_name), None)
        if not item:
            return False, f"MO2 instance '{instance_name}' was not found in the selected prefix."
        instance_path = Path(item.get("path", ""))
        config_value = item.get("config")
        config_path = Path(config_value) if config_value else instance_path / "ModOrganizer.ini"
        valid = (
            not instance_path.is_symlink() and instance_path.is_dir()
            and not config_path.is_symlink() and config_path.is_file()
        )
        return valid, f"Instance folder: {instance_path}; configuration: {config_path}"
