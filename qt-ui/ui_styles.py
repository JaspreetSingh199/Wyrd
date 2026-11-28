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
    border-radius: 8px;
}
QFrame#runtimePanel {
    background: #222a2d;
    border: 1px solid #4b5556;
    border-radius: 8px;
}
QFrame#statusPanel {
    background: #20272a;
    border: 1px solid #465052;
    border-radius: 8px;
}
QFrame#launchPanel {
    background: #242a2b;
    border: 1px solid #746348;
    border-radius: 8px;
}
QFrame#mo2InstanceRow {
    background: #22282b;
    border: 1px solid #41494a;
    border-radius: 8px;
}
QFrame#mo2InstanceRow:hover { border-color: #75694f; }

QPushButton {
    background: #2a3235;
    color: #e2dfd7;
    border: 1px solid #596263;
    border-radius: 8px;
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
    border-radius: 8px;
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
    background: transparent;
    color: #b9aa88;
    border: none;
    border-radius: 0;
    padding: 0;
    min-height: 23px;
    max-height: 23px;
    min-width: 23px;
    max-width: 23px;
    font-size: 15px;
    font-weight: 400;
}
QPushButton#infoButton:hover {
    background: rgba(178, 154, 109, 38);
    color: #f0dfb9;
    border: none;
    border-radius: 11px;
}
QPushButton#infoButton:pressed {
    background: rgba(178, 154, 109, 62);
    color: #fff0ce;
    border: none;
    border-radius: 11px;
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
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .4px;
}
QLabel#launchBlocked {
    color: #e3c27c;
    background: #3a3325;
    border: 1px solid #8c7140;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .4px;
}

QLabel#sessionHeading {
    color: #c8b995;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding-right: 4px;
}
QLabel#sessionStatus {
    color: #d7d2c6;
    background: #263137;
    border: 1px solid #657078;
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#sessionStatus[sessionState="active"] {
    color: #ead6a8;
    background: #393326;
    border-color: #8d7952;
}
QLabel#sessionStatus[sessionState="running"] {
    color: #c5dfb5;
    background: #26372d;
    border-color: #66835f;
}
QLabel#sessionStatus[sessionState="failed"] {
    color: #edc0ad;
    background: #3b2926;
    border-color: #986253;
}
QLabel#sessionDetail {
    color: #c2c1b8;
    font-size: 12px;
}

QComboBox, QLineEdit {
    background: #1b2225;
    color: #e0ddd4;
    border: 1px solid #596263;
    border-radius: 8px;
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
    background: #19252b;
    border: 1px solid #536068;
    border-radius: 8px;
    top: 2px;
    padding: 8px;
}
QTabBar::tab {
    background: #202a2e;
    color: #aeb1aa;
    border: 1px solid #536068;
    border-radius: 8px;
    padding: 9px 17px;
    margin: 0 5px 3px 0;
}
QTabBar::tab:selected {
    background: #30414a;
    color: #eee8da;
    border: 1px solid #a58b5c;
}
QTabBar::tab:hover { color: #f0ecdf; border-color: #8b8068; }

QProgressBar {
    background: #111719;
    border: 1px solid #596263;
    border-radius: 8px;
    height: 20px;
    min-height: 20px;
    text-align: center;
    color: #e9e3d5;
    font-weight: 600;
}
QProgressBar::chunk { background: #88714d; border-radius: 5px; }
QProgressDialog#launchLoader {
    background: #111a1e;
    color: #e9dfcc;
    border: 1px solid #a18a60;
}
QProgressDialog#launchLoader QLabel {
    color: #e8dfce;
    font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 13px;
    padding: 8px 4px;
}
QProgressDialog#launchLoader QProgressBar {
    background: #10191d;
    border: 1px solid #746348;
    border-radius: 6px;
    min-height: 9px;
    max-height: 9px;
    text-align: center;
}
QProgressDialog#launchLoader QProgressBar::chunk {
    background: #b18a4d;
    border-radius: 8px;
}
QPlainTextEdit {
    background: #13191b;
    color: #d1d0c8;
    border: 1px solid #41494b;
    border-radius: 8px;
    padding: 7px;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #171d20; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #505a59; min-height: 24px; border-radius: 5px; }
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
    border-radius: 8px;
}
QCheckBox::indicator:checked { background: #88714d; border-color: #b29a6d; }
QGroupBox {
    border: 1px solid #41494b;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    color: #c8b995;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }

/* Nordic Wyrd shell: carved slate, worn parchment and restrained brass. */
QWidget#appRoot { background: #10191d; }
QWidget#contentRoot { background: #111a1e; }
QFrame#sidebar { background: #0b1419; border-right: 1px solid #6f6048; }
QLabel#sidebarCrest {
    color: #d6c5a5; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 64px; padding: 12px 0 4px 0;
}
QLabel#sidebarBrand {
    color: #e7ddc8; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 30px; font-weight: 700; letter-spacing: 3px; padding-top: 5px;
}
QLabel#sidebarSubtitle {
    color: #b8ae9a; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 12px; letter-spacing: 1px; padding-bottom: 14px;
}
QFrame#sidebarDivider { background: #62563f; max-height: 1px; }
QLabel#sidebarVersion { color: #9d9b91; font-family: "Noto Serif", serif; padding-top: 12px; }
QPushButton#navButton {
    text-align: left; background: transparent; color: #d0c8b8;
    border: 1px solid transparent; border-radius: 8px; padding: 14px 16px;
    font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 16px;
}
QPushButton#navButton:hover { background: #263840; border-color: #8b8068; color: #fff4df; padding-left: 19px; }
QPushButton#navButton:checked { background: #293a42; border: 1px solid #b49a6a; color: #fff1d6; padding-left: 19px; }
QLabel#heroTitle {
    color: #e9dfcc; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 33px; font-weight: 700; padding: 8px 4px 0 4px;
}
QLabel#muted { color: #b0b0a7; }
QFrame#panel, QFrame#runtimePanel, QFrame#statusPanel {
    background: #19252b; border: 1px solid #536068; border-radius: 8px;
}
QFrame#mo2InstancePicker { background: transparent; border: none; }
QFrame#mo2InstanceRow { background: #19262d; border: 1px solid #59636a; border-radius: 8px; }
QFrame#mo2InstanceRow:hover { border-color: #c09b60; background: #22323a; }
QLabel#instanceArtwork {
    background: #17272d;
    border: 1px solid #8a7959;
    border-radius: 7px;
}
QLabel#profileArtwork {
    background: #17272d;
    border: 1px solid #b49a6a;
    border-radius: 5px;
}
QToolButton#artworkMenuButton {
    background: #26363c;
    color: #e6dcc8;
    border: 1px solid #877655;
    border-radius: 6px;
    padding: 7px 10px;
}
QToolButton#artworkMenuButton:hover {
    background: #3a4545;
    border-color: #c5a66f;
    color: #fff2d8;
}
QMenu {
    background: #1b282d;
    color: #e7dfd0;
    border: 1px solid #9b845b;
    padding: 5px;
}
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 4px; }
QMenu::item:selected { background: #39464a; color: #fff0d3; }
QLabel#heroArtwork {
    background: #1b2b31;
    border: 1px solid #8a7959;
    border-radius: 8px;
}
QFrame#heroShade {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(8,16,21,235), stop:0.42 rgba(10,19,24,180),
        stop:0.72 rgba(10,19,24,75), stop:1 rgba(10,19,24,35));
    border: 1px solid #8a7959;
    border-radius: 8px;
}
QLabel#heroEyebrow {
    color: #d0b783; font-size: 10px; font-weight: 700;
    letter-spacing: 2px; padding-left: 5px;
}
QLabel#heroTitle {
    color: #f2e7d1; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 35px; font-weight: 700; padding: 2px 4px 0 4px;
}
QLabel#heroSubtitle {
    color: #e0d7c7; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 15px; padding: 0 5px 3px 5px;
}
QPushButton#primaryAction {
    background: #715632; border: 1px solid #c19c5d; border-radius: 8px;
    color: #fff1d6; padding: 10px 18px;
    font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 14px;
}
QPushButton#primaryAction:hover { background: #8a6a3c; border-color: #dfbb79; color: #fff8e9; }
QPushButton#secondaryAction { background: #202f36; border: 1px solid #879096; color: #e6dfd1; }
QListWidget#mo2Instances { background: transparent; border: none; outline: none; }
QListWidget#mo2Instances::item { border: none; padding: 4px 0; }
QTabWidget#mainTabs::pane { border: none; background: transparent; padding: 0; }
QTabWidget#logsTabs::pane { background: #19252b; border: 1px solid #536068; border-radius: 8px; padding: 8px; }
QFrame#interactionOverlay { background: rgba(5, 10, 13, 188); border: none; }
QFrame#interactionOverlayCard { background: #1b2a31; border: 1px solid #b09666; border-radius: 12px; }
QLabel#interactionOverlayTitle { color: #f0e5ce; font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 21px; font-weight: 700; padding: 2px 0; }
QLabel#interactionOverlayDetail { color: #d0c9b9; font-size: 13px; padding: 2px 8px; }

QLabel#instanceSectionTitle {
    color: #e6ddca; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 24px; font-weight: 700; padding: 6px 2px;
}
QLabel#instanceCount {
    color: #e0d8c8; background: #202c32; border: 1px solid #83775e;
    border-radius: 8px; padding: 6px 14px; font-family: "Noto Serif", serif;
}
QLabel#instanceName {
    color: #eee5d3; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 20px; font-weight: 700;
}
QLabel#instanceMeta { color: #c2bcad; font-family: "Noto Serif", serif; font-size: 12px; }
QFrame#unmoddedLaunchPanel { background: #18242a; border: 1px solid #77684f; border-radius: 8px; }
QLabel#steamMark { color: #d4d7d5; font-size: 27px; padding: 0 8px; }
QLabel#unmoddedTitle {
    color: #e8dfce; font-family: "Noto Serif", serif; font-size: 17px; font-weight: 700;
    padding-right: 10px;
}

QFrame#heroBanner {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #263b47, stop:0.52 #1b2b34, stop:1 #142027);
    border: 1px solid #7d7058;
    border-radius: 8px;
}
QLabel#heroSubtitle {
    color: #d1c9b8; font-family: "Noto Serif", "DejaVu Serif", serif;
    font-size: 15px; padding: 0 5px 3px 5px;
}
"""
