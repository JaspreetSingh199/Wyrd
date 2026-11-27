"""Read-only launch preflight checks and confirmation UI for MainWindow."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
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
        """Run checks silently; show a dialog only when a check fails."""
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

        results = []
        for label, check in checks:
            try:
                passed, message = check()
            except Exception as error:
                passed, message = False, str(error)
            results.append((label, bool(passed), str(message)))
            if not passed:
                break

        # A clean preflight is the normal path: no modal, checklist, or extra
        # confirmation. Continue directly to the existing launch adapter.
        if all(passed for _label, passed, _message in results) and len(results) == len(checks):
            return True

        failed_label, _passed, failed_message = results[-1]
        dialog = QDialog(self)
        dialog.setWindowTitle("Skyrim launch blocked")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Launch pre-check failed")
        heading.setObjectName("section")
        layout.addWidget(heading)
        detail = QLabel(
            f"Wyrd could not safely launch Skyrim because {failed_label.lower()} failed."
        )
        detail.setWordWrap(True)
        detail.setObjectName("muted")
        layout.addWidget(detail)

        for label, passed, _message in results:
            checkbox = QCheckBox(f"{label} — {'OK' if passed else 'FAILED'}")
            checkbox.setChecked(passed)
            checkbox.setEnabled(False)
            layout.addWidget(checkbox)

        status = QLabel(f"{failed_label}: {failed_message}")
        status.setWordWrap(True)
        status.setObjectName("muted")
        layout.addWidget(status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.exec()
        self._log(f"Modded launch pre-check failed [{failed_label}]: {failed_message}")
        return False

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
