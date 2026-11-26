"""First-run onboarding and Proton preflight UI workflows."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QInputDialog

from wyrd_mo2 import mark_first_run_completed
from ui_path_helpers import _same_filesystem_path


class SetupWorkflowMixin:
    def _run_first_launch_setup(self) -> None:
        """Offer first-run environment setup and let the user choose Proton."""
        if self._saved_settings.get("first_run_completed"):
            return
        game_ready = bool(self.game_state.get("found"))
        if not game_ready:
            QMessageBox.information(
                self, "Welcome to Wyrd",
                "Wyrd could not find Skyrim Special Edition yet. Install or locate it "
                "through Steam, then use Refresh to try again."
            )
            return
        if not self.proton_builds:
            QMessageBox.information(
                self, "Welcome to Wyrd",
                "Wyrd found Skyrim, but no compatible Proton runtime. Install one "
                "through Steam or ProtonUp-Qt, then use Refresh."
            )
            return

        # Use the ranked list as a sensible default, but always let the user
        # choose. Never silently force a community or official runtime.
        choices = [
            f'{item["name"]} ({item["architecture"]})'
            for item in self.proton_builds
        ]
        saved_path = self._saved_settings.get("proton")
        default_index = next(
            (
                i for i, item in enumerate(self.proton_builds)
                if saved_path and _same_filesystem_path(item["path"], saved_path)
            ),
            0,
        )
        chosen, accepted = QInputDialog.getItem(
            self, "Choose Proton for Wyrd",
            "Which compatibility runtime should Wyrd use for Skyrim and MO2?",
            choices, default_index, False,
        )
        if not accepted or not chosen:
            self.progress_label.setText("Setup paused. Choose a Proton runtime when ready.")
            return
        mark_first_run_completed()
        self._saved_settings["first_run_completed"] = True
        selected_index = choices.index(chosen)
        self.proton_combo.blockSignals(True)
        self.proton_combo.setCurrentIndex(selected_index)
        self.proton_combo.blockSignals(False)

        answer = QMessageBox.question(
            self, "Welcome to Wyrd",
            "Use the selected Skyrim and Proton settings?\n\n"
            "Wyrd will use Skyrim's existing Steam compatdata folder. "
            "It will not reset the prefix or change game files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.progress_label.setText("Environment setup skipped. You can configure it later.")
            return

        self._save_selection(selected_index)
        self.manage_mo2_library()
        self.progress_label.setText("Wyrd environment configured.")
        self._log(
            "First-launch setup: selected Proton "
            + str(self.proton_combo.currentText())
            + " for Skyrim."
        )

        if self.mo2_state.get("installed"):
            # MO2 is only entered through the modded Skyrim launch action.
            return

        answer = QMessageBox.question(
            self, "Set up Mod Organizer 2?",
            "Would you like Wyrd to download and install the official Mod Organizer 2 "
            "into Skyrim's existing Steam compatdata prefix now?\n\n"
            "You can skip this and launch unmodded Skyrim instead. MO2 can be installed later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.install_mo2(confirm=False)
        else:
            self.progress_label.setText("MO2 setup skipped. You can launch unmodded Skyrim.")

    def test_selected_proton(self) -> None:
        """Perform a non-destructive preflight of the selected Proton runtime."""
        raw_path = self.proton_combo.currentData()
        if not raw_path:
            QMessageBox.warning(self, "Test Proton", "Choose a Proton runtime first.")
            return
        runtime = Path(str(raw_path)).expanduser()
        executable = runtime / "proton"
        problems = []
        if runtime.is_symlink() or not runtime.is_dir():
            problems.append("The runtime folder is missing or is a symlink.")
        if executable.is_symlink() or not executable.is_file():
            problems.append("The Proton launcher is missing or unsafe.")
        elif not os.access(executable, os.X_OK):
            problems.append("The Proton launcher is not executable.")
        if not (runtime / "files").is_dir() and not (runtime / "dist").is_dir():
            problems.append("The runtime payload folder (files or dist) is missing.")
        if problems:
            message = "Proton check failed:\n\n" + "\n".join(f"• {item}" for item in problems)
            self.runtime_status.setText("Selected Proton needs attention.")
            self._log("Proton preflight failed: " + "; ".join(problems))
            QMessageBox.warning(self, "Proton check failed", message)
            return
        message = (
            f"{self.proton_combo.currentText()} passed the local runtime check.\n\n"
            "Wyrd found an executable Proton launcher and its runtime payload. "
            "This check does not launch a game or prove that the runtime works in-game."
        )
        self.runtime_status.setText("Selected Proton passed the local runtime check.")
        self._log("Proton preflight passed: " + str(runtime))
        QMessageBox.information(self, "Proton check passed", message)
