"""SteamVirtualLAN's icon in the Windows notification area.

The app keeps running there when its window is closed, so this PC stays
online in its network: Steam callbacks, the lobby, the connections to the
other members and the virtual adapter all keep going. Only Exit in the menu
ends the app.
"""

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from steamlan.app.controller import Presence, View
from steamlan.gui.style import ACCENT, TONES

_PRESENCE_TONES = {
    Presence.ONLINE: "ok",
    Presence.CONNECTING: "pending",
    Presence.RESTORING: "pending",
    Presence.OFFLINE: "neutral",
    Presence.NONE: "neutral",
}


def tray_icon(tone: str) -> QIcon:
    """Three connected nodes, with a status dot in the corner."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)

    nodes = [QPointF(20, 20), QPointF(44, 20), QPointF(32, 42)]
    painter.setPen(QPen(QColor("white"), 4))
    for index, node in enumerate(nodes):
        painter.drawLine(node, nodes[(index + 1) % len(nodes)])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white"))
    for node in nodes:
        painter.drawEllipse(node, 7, 7)

    painter.setBrush(QColor("#111316"))
    painter.drawEllipse(QPointF(50, 50), 13, 13)
    painter.setBrush(QColor(TONES.get(tone, TONES["neutral"])))
    painter.drawEllipse(QPointF(50, 50), 9, 9)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    open_requested = Signal()
    toggle_online_requested = Signal()
    leave_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._icons = {tone: tray_icon(tone) for tone in set(_PRESENCE_TONES.values())}
        self.icon = QSystemTrayIcon(self._icons["neutral"], self)
        self.menu = QMenu()
        self.open_action = self._add("Open SteamVirtualLAN", self.open_requested)
        self.menu.setDefaultAction(self.open_action)
        self.menu.addSeparator()
        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.toggle_action = self._add("Go Offline", self.toggle_online_requested)
        self.leave_action = self._add("Leave Network", self.leave_requested)
        self.menu.addSeparator()
        self.exit_action = self._add("Exit", self.exit_requested)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.setToolTip("SteamVirtualLAN")

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def notify(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, self._icons["neutral"], 5000)

    def render(self, view: View) -> None:
        status = view.tray_status
        self.status_action.setText(status)
        in_network = view.presence is not Presence.NONE
        self.toggle_action.setVisible(in_network)
        self.toggle_action.setEnabled(view.steam_ready)
        self.toggle_action.setText(
            "Go Online" if view.presence is Presence.OFFLINE else "Go Offline"
        )
        self.leave_action.setVisible(in_network)
        self.icon.setIcon(self._icons[_PRESENCE_TONES[view.presence]])
        self.icon.setToolTip(f"SteamVirtualLAN\n{status}")

    def _add(self, text: str, signal) -> QAction:
        action = self.menu.addAction(text)
        action.triggered.connect(signal)
        return action

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()
