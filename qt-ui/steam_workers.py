"""Background workers for potentially long Steam/Proton operations."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from steam_mo2_management import install_mo2_into_skyrim_prefix


class SteamPrefixInstallWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(dict)

    def __init__(self, proton_path: str, game_path: str):
        super().__init__()
        self.proton_path = Path(proton_path)
        self.game_path = Path(game_path)

    def run(self) -> None:
        try:
            result = install_mo2_into_skyrim_prefix(
                self.proton_path, self.game_path,
                callback=lambda value, message: self.progress.emit(value, message),
            )
        except Exception as error:
            result = {"ok": False, "message": str(error)}
        self.completed.emit(result)
