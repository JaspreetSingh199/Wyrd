"""Read-only presentation of saved launch diagnostics."""
from __future__ import annotations

from proton_runtime import managed_paths
from launch_log_diagnostics import read_launch_diagnostics
from PySide6.QtWidgets import QMessageBox


class LaunchDiagnosticsWorkflowMixin:
    def show_launch_diagnostics(self) -> None:
        """Display the latest read-only Skyrim launch log excerpt."""
        path = managed_paths()["root"] / "logs" / "skyrim-launch.log"
        result = read_launch_diagnostics(path)
        if not result.get("ok"):
            QMessageBox.information(
                self, "Launch diagnostics",
                result.get("message", "No diagnostics available."),
            )
            return
        self._log("Launch diagnostics opened: " + result["path"])
        QMessageBox.information(
            self, "Launch diagnostics", result["content"][-5000:]
        )
