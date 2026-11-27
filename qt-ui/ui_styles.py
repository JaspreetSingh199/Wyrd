"""Application-wide Wyrd Qt stylesheet."""
from __future__ import annotations


APP_STYLE = """
/* Wyrd: worn slate, warm iron and restrained brass. Keep it a desktop tool. */
QWidget {
    background: #171b1e;
    color: #dedbd2;
    font-family: "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QMainWindow, QScrollArea, QTabWidget::pane { background: #171b1e; }
QLabel { background: transparent; border: none; }
QFrame#panel {
    background: #20262a;
    border: 1px solid #41494b;
    border-radius: 7px;
}
QFrame#runtimePanel {
    background: #222a2d;
    border: 1px solid #4b5556;
    border-radius: 7px;
}
QFrame#statusPanel {
    background: #20272a;
    border: 1px solid #465052;
    border-radius: 7px;
}
QFrame#launchPanel {
    background: #242a2b;
    border: 1px solid #746348;
    border-radius: 7px;
}
QFrame#mo2InstanceRow {
    background: #22282b;
    border: 1px solid #41494a;
    border-radius: 6px;
}
QFrame#mo2InstanceRow:hover { border-color: #75694f; }

QPushButton {
    background: #2a3235;
    color: #e2dfd7;
    border: 1px solid #596263;
    border-radius: 5px;
    padding: 8px 13px;
    min-height: 20px;
}
QPushButton:hover {
    background: #343e40;
    border-color: #8b8068;
    color: #fffdf7;
}
QPushButton:pressed { background: #202729; }
QPushButton:disabled {
    color: #7e8582;
    background: #242a2c;
    border-color: #3b4344;
}
QPushButton#primaryAction {
    background: #756044;
    color: #fff8e9;
    border: 1px solid #ad9060;
    border-radius: 5px;
    padding: 9px 15px;
    font-weight: 600;
}
QPushButton#primaryAction:hover {
    background: #8a704e;
    border-color: #c0a477;
}
QPushButton#primaryAction:disabled {
    background: #292d2d;
    color: #858983;
    border-color: #4a504e;
}
QPushButton#secondaryAction {
    background: #293235;
    border-color: #626c6d;
}
QPushButton#infoButton {
    background: #242c2e;
    color: #c8b995;
    border: 1px solid #756b56;
    border-radius: 11px;
    padding: 0;
    min-height: 22px;
    font-weight: 700;
}
QPushButton#infoButton:hover {
    background: #3b403c;
    color: #f3e7ca;
    border-color: #b29a6d;
}

QLabel#heading {
    color: #eee9dc;
    font-size: 27px;
    font-weight: 650;
    letter-spacing: 1px;
    padding: 2px 0;
}
QLabel#heroTitle {
    color: #eee7d8;
    font-size: 25px;
    font-weight: 650;
    letter-spacing: .35px;
    padding: 8px 2px 0 2px;
}
QLabel#section {
    color: #e0d7c5;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: .45px;
}
QLabel#muted { color: #a8aaa3; }
QLabel#launchReady {
    color: #a8d58c;
    background: #26352a;
    border: 1px solid #52764d;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .4px;
}
QLabel#launchBlocked {
    color: #e3c27c;
    background: #3a3325;
    border: 1px solid #8c7140;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .4px;
}

QComboBox, QLineEdit {
    background: #1b2225;
    color: #e0ddd4;
    border: 1px solid #596263;
    border-radius: 5px;
    padding: 7px 10px;
    min-height: 21px;
    selection-background-color: #62543f;
}
QComboBox:hover, QLineEdit:focus { border-color: #a18a61; }
QComboBox QAbstractItemView {
    background: #22292c;
    color: #e0ddd4;
    selection-background-color: #514a3b;
    border: 1px solid #756b56;
}
QComboBox#profilePicker {
    background: #1b2224;
    border-color: #6b624f;
    min-width: 145px;
}
QComboBox#profilePicker:disabled { color: #898d88; }

QTabWidget::pane {
    background: #1a2023;
    border: 1px solid #41494b;
    top: -1px;
}
QTabBar::tab {
    background: #20282b;
    color: #aeb1aa;
    border: 1px solid #41494b;
    padding: 10px 19px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #303a3b;
    color: #eee8da;
    border-bottom: 2px solid #a58b5c;
}
QTabBar::tab:hover { color: #f0ecdf; }

QProgressBar {
    background: #111719;
    border: 1px solid #596263;
    border-radius: 5px;
    height: 20px;
    min-height: 20px;
    text-align: center;
    color: #e9e3d5;
    font-weight: 600;
}
QProgressBar::chunk { background: #88714d; border-radius: 4px; }
QPlainTextEdit {
    background: #13191b;
    color: #d1d0c8;
    border: 1px solid #41494b;
    border-radius: 5px;
    padding: 7px;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #171d20; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #505a59; min-height: 24px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #81765f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QListWidget#mo2AdvancedCategories {
    background: #20272a;
    border: 1px solid #41494b;
    padding: 4px;
    outline: none;
}
QListWidget#mo2AdvancedCategories::item {
    color: #a8aaa3;
    padding: 11px 10px;
    border-bottom: 1px solid #343d3e;
}
QListWidget#mo2AdvancedCategories::item:hover { color: #eee8da; background: #2b3436; }
QListWidget#mo2AdvancedCategories::item:selected {
    color: #eee8da;
    background: #343d3c;
    border-left: 3px solid #a58b5c;
}
QListWidget { background: #1c2427; color: #dedbd2; border: 1px solid #41494b; outline: none; }
QListWidget::item { padding: 7px; border-bottom: 1px solid #30393a; }
QListWidget::item:selected { background: #343d3c; color: #f0ecdf; }
QCheckBox { spacing: 8px; color: #d0cec5; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    background: #1b2225;
    border: 1px solid #687170;
    border-radius: 3px;
}
QCheckBox::indicator:checked { background: #88714d; border-color: #b29a6d; }
QGroupBox {
    border: 1px solid #41494b;
    border-radius: 5px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    color: #c8b995;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
"""
