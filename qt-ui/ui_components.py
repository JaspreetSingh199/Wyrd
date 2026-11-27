"""Reusable small Qt UI construction helpers."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout


def add_info_button(layout, parent, title: str, explanation: str) -> QPushButton:
    """Add a compact hover-help control beside an advanced action."""
    info = QPushButton("i")
    info.setObjectName("infoButton")
    info.setFixedSize(20, 20)
    info.setToolTip(explanation)
    info.setToolTipDuration(15000)
    info.setAccessibleName(f"Information about {title}")
    layout.addWidget(info)
    return info


def panel(title: str, description: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(17, 15, 17, 15)
    heading = QLabel(title)
    heading.setObjectName("section")
    detail = QLabel(description)
    detail.setObjectName("muted")
    detail.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(detail)
    frame.detail_label = detail
    return frame
