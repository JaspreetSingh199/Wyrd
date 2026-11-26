"""Application-wide Wyrd Qt stylesheet."""
from __future__ import annotations


APP_STYLE = """
/* Nordic Frost: SkyUI-inspired charcoal, slate and glacial-blue palette. */
QWidget { background: #11191f; color: #dce5e9; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 13px; }
QMainWindow, QScrollArea, QTabWidget::pane { background: #11191f; }
QLabel { background: transparent; border: none; }
QFrame#panel { background: #19252d; border: 1px solid #40515c; border-radius: 6px; }
QFrame#runtimePanel { background: #1b2a34; border: 1px solid #4b6270; border-radius: 6px; }
QFrame#statusPanel { background: #18252c; border: 1px solid #344b57; border-radius: 8px; }
QFrame#launchPanel { background: #1c2d36; border: 1px solid #587586; border-radius: 8px; }
QFrame#panel { border-radius: 8px; }
QFrame#runtimePanel { border-radius: 8px; }
QPushButton { border-radius: 6px; padding: 9px 14px; }
QPushButton#primaryAction { background: #527f93; border: 1px solid #8db8ca; border-radius: 7px; padding: 11px 18px; font-size: 14px; }
QPushButton#primaryAction:hover { background: #6394a9; }
QPushButton#secondaryAction { border-radius: 6px; }
QTabBar::tab { border-radius: 5px 5px 0 0; padding: 11px 20px; }

QPushButton { background: #263640; color: #dce5e9; border: 1px solid #526875; border-radius: 3px; padding: 9px 15px; min-height: 20px; }
QPushButton:hover { background: #334955; color: #f0f6f8; border-color: #8eafbf; }
QPushButton:pressed { background: #1a2931; }
QPushButton:disabled { color: #71818a; background: #1b252b; border-color: #34434b; }
QPushButton#primaryAction { background: #385666; color: #f0f6f8; border: 1px solid #82a9bc; font-weight: 600; }
QPushButton#primaryAction:hover { background: #466a7c; }
QPushButton#secondaryAction { background: #202f38; border-color: #536b78; }
QPushButton#infoButton { background: #1b2a33; color: #a9cbd9; border: 1px solid #63899c; border-radius: 12px; padding: 0; min-height: 22px; font-weight: 700; }
QPushButton#infoButton:hover { background: #385666; color: #f0f6f8; border-color: #a9cbd9; }
QLabel#heading { color: #eef4f6; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 28px; font-weight: 700; letter-spacing: 2px; padding: 2px 0; }
QLabel#section { color: #d5e4ea; font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 14px; font-weight: 700; letter-spacing: .6px; }
QLabel#muted { color: #a5b3ba; }
QComboBox, QLineEdit { background: #17232b; color: #dce5e9; border: 1px solid #526875; border-radius: 3px; padding: 8px 10px; min-height: 21px; selection-background-color: #456779; }
QComboBox:hover, QLineEdit:focus { border-color: #91b6c8; }
QComboBox QAbstractItemView { background: #19252d; color: #dce5e9; selection-background-color: #385666; border: 1px solid #526875; }
QTabWidget::pane { background: #141f26; border: 1px solid #40515c; top: -1px; }
QTabBar::tab { background: #17232b; color: #9eafb8; border: 1px solid #344650; padding: 10px 22px; margin-right: 3px; }
QTabBar::tab:selected { background: #243640; color: #e5edf0; border-bottom: 2px solid #8fb4c6; }
QTabBar::tab:hover { color: #e5edf0; }
QProgressBar { background: #0d171d; border: 1px solid #63899c; border-radius: 6px; height: 24px; min-height: 24px; text-align: center; color: #f0f6f8; font-weight: 700; }
QProgressBar::chunk { background: #527f93; border-radius: 5px; }
QPlainTextEdit { background: #0d151a; color: #c7d4da; border: 1px solid #40515c; border-radius: 3px; padding: 7px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #11191f; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: #405763; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #64899b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QListWidget#mo2AdvancedCategories { background: #19252d; border: 1px solid #40515c; padding: 4px; outline: none; }
QListWidget#mo2AdvancedCategories::item { color: #a5b3ba; padding: 11px 10px; border-bottom: 1px solid #2b3a43; }
QListWidget#mo2AdvancedCategories::item:hover { color: #e5edf0; background: #243640; }
QListWidget#mo2AdvancedCategories::item:selected { color: #d9edf5; background: #2a414e; border-left: 3px solid #8fb4c6; }
QListWidget { background: #17232b; color: #dce5e9; border: 1px solid #40515c; outline: none; }
QListWidget::item { padding: 7px; border-bottom: 1px solid #293942; }
QListWidget::item:selected { background: #2a414e; color: #eef6f8; }
QCheckBox { spacing: 8px; color: #cbd7dc; }
QCheckBox::indicator { width: 15px; height: 15px; background: #17232b; border: 1px solid #617985; }
QCheckBox::indicator:checked { background: #63899c; border-color: #a9c8d6; }
QGroupBox { border: 1px solid #40515c; border-radius: 3px; margin-top: 12px; padding: 10px 8px 8px 8px; color: #a9c3d0; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
"""
