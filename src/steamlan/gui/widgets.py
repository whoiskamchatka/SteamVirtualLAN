"""Small building blocks shared by the pages."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from steamlan.app.network import MemberView
from steamlan.gui.style import TONES


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def set_style_name(widget: QWidget, name: str) -> None:
    """Switch a widget to another style sheet role."""
    if widget.objectName() != name:
        widget.setObjectName(name)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def button(text: str, name: str = "") -> QPushButton:
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    return frame, layout


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


class StatusDot(QFrame):
    def __init__(self, size: int = 8):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.set_tone("neutral")

    def set_tone(self, tone: str) -> None:
        color = TONES.get(tone, TONES["neutral"])
        self.setStyleSheet(f"background: {color}; border-radius: {self._size // 2}px;")


class StatusLine(QWidget):
    """A status dot followed by its text."""

    def __init__(self, name: str = "", dot_size: int = 8):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.dot = StatusDot(dot_size)
        self.text = label(name=name, wrap=True)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text, 1)

    def set(self, text: str, tone: str) -> None:
        self.text.setText(text)
        self.dot.set_tone(tone)


class CopyField(QWidget):
    """A caption, a value and a Copy button."""

    def __init__(self, caption: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label(caption.upper(), "caption"))
        self.value = label(name="value")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.value)
        layout.addLayout(column, 1)
        self.copy = button("Copy", "small")
        self.copy.clicked.connect(self._copy)
        layout.addWidget(self.copy, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_value(self, text: str) -> None:
        self.value.setText(text)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.value.text())
        self.copy.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy.setText("Copy"))


class MemberRow(QFrame):
    """One lobby member, with its virtual IP address on the right."""

    def __init__(self):
        super().__init__()
        self.setObjectName("memberRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self.avatar = label(name="avatar")
        self.avatar.setFixedSize(32, 32)
        layout.addWidget(self.avatar)

        column = QVBoxLayout()
        column.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name = label(name="memberName")
        self.host_badge = label("Host", "badgeAccent")
        self.you_badge = label("You", "badge")
        top.addWidget(self.name)
        top.addWidget(self.host_badge)
        top.addWidget(self.you_badge)
        top.addStretch(1)
        column.addLayout(top)
        self.status = StatusLine("memberStatus", dot_size=6)
        column.addWidget(self.status)
        layout.addLayout(column, 1)

        self.address = label(name="address")
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.address, 0, Qt.AlignmentFlag.AlignVCenter)

    def show_member(self, member: MemberView) -> None:
        self.name.setText(member.name)
        self.avatar.setText(member.name[:1].upper() or "?")
        self.host_badge.setVisible(member.is_host)
        self.you_badge.setVisible(member.is_you)
        self.status.set(member.status, member.tone)
        self.address.setText(member.address)
        self.setToolTip(f"SteamID {member.steam_id}")
