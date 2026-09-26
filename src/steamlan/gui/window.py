import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
)

from steamlan.app.controller import AppController, Presence, Screen
from steamlan.gui.instance import SingleInstance
from steamlan.gui.pages import HomePage, JoinPage, NetworkPage
from steamlan.gui.style import STYLE
from steamlan.gui.tray import Tray, tray_icon

# Steam callbacks are pumped on the UI thread, also while the window is hidden
# in the tray; every call involved returns quickly, and results that take
# longer arrive through later pumps. Steam asks apps that only redraw on
# events to check for overlay frames at about 33 Hz.
PUMP_INTERVAL_MS = 30


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, tray_available: bool | None = None):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("SteamVirtualLAN")
        self.setWindowIcon(tray_icon("ok"))
        self.resize(460, 600)
        self.setMinimumSize(420, 500)

        self.home = HomePage()
        self.join = JoinPage()
        self.network = NetworkPage()
        self.pages = QStackedWidget()
        self.pages.setObjectName("root")
        for page in (self.home, self.join, self.network):
            self.pages.addWidget(page)
        self.setCentralWidget(self.pages)

        self.home.create.connect(self._action(controller.create_lobby))
        self.home.join.connect(self._action(controller.show_join))
        self.home.retry.connect(self._action(controller.start))
        self.join.back.connect(self._action(controller.show_home))
        self.join.submit.connect(lambda lobby, code: self._run(controller.join, lobby, code))
        self.network.invite.connect(self._invite)
        self.network.toggle_online.connect(self.toggle_online)
        self.network.leave.connect(self.leave_network)

        if tray_available is None:
            tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray: Tray | None = None
        if tray_available:
            self.tray = Tray(self)
            self.tray.open_requested.connect(self.show_window)
            self.tray.toggle_online_requested.connect(self.toggle_online)
            self.tray.leave_requested.connect(self.leave_network)
            self.tray.exit_requested.connect(self.exit_app)
            self.tray.show()
        self._exiting = False
        self._told_about_tray = False

        self._last_view = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(PUMP_INTERVAL_MS)
        self.render()

    def render(self) -> None:
        view = self.controller.view()
        if view == self._last_view:
            return
        self._last_view = view
        page = {Screen.HOME: self.home, Screen.JOIN: self.join, Screen.NETWORK: self.network}
        current = page[view.screen]
        if self.pages.currentWidget() is not current:
            self.network.show_notice("")
        self.pages.setCurrentWidget(current)
        current.render(view)
        if self.tray is not None:
            self.tray.render(view)

    def closeEvent(self, event) -> None:
        """The X only hides the window: this PC stays online in its network.
        Exit, in the tray menu, ends the app."""
        if self._exiting:
            event.accept()
            return
        event.ignore()
        if self.tray is None:
            # Without a notification area the window must stay reachable.
            self.showMinimized()
            return
        self.hide()
        if not self._told_about_tray:
            self._told_about_tray = True
            self.tray.notify(
                "SteamVirtualLAN is still running",
                "You stay online in your network. Use Exit in this icon's menu to quit.",
            )

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_online(self) -> None:
        if self.controller.view().presence is Presence.OFFLINE:
            self._run(self.controller.go_online)
        else:
            self._run(self.controller.go_offline)

    def leave_network(self) -> None:
        if self.controller.view().presence is Presence.NONE:
            return
        if self.confirm_leave():
            self._run(self.controller.leave_network)

    def confirm_leave(self) -> bool:
        self.show_window()
        answer = QMessageBox.question(
            self,
            "Leave Network",
            "Leave this network for good?\n\n"
            "Your virtual IP address is given up, and you need an invite or the "
            "access code to join again. To only be unavailable for a while, use "
            "Go Offline instead.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def exit_app(self) -> None:
        """Stop SteamVirtualLAN: the adapter, the connections and Steam. The
        network is not left; the next start goes back online in it."""
        if self._exiting:
            return
        self._exiting = True
        self.timer.stop()
        self.controller.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def prepare_for_session_end(self) -> None:
        """Windows is logging off or shutting down: let the window close."""
        self._exiting = True

    def _tick(self) -> None:
        self.controller.tick()
        self.render()
        if self.isVisible() and self.controller.overlay_needs_present():
            self.update()

    def _action(self, method):
        return lambda: self._run(method)

    def _run(self, method, *args) -> None:
        method(*args)
        self.render()

    def _invite(self) -> None:
        try:
            self.network.show_notice(self.controller.open_invite())
        except ValueError as exc:
            self.network.show_notice(str(exc), error=True)


def run() -> int:
    # The Steam overlay can only draw into windows presented with Direct3D,
    # OpenGL or Vulkan, so have Qt present its widgets with Direct3D 11.
    os.environ.setdefault("QT_WIDGETS_RHI", "1")
    os.environ.setdefault("QT_WIDGETS_RHI_BACKEND", "d3d11")
    app = QApplication(sys.argv)
    app.setApplicationName("SteamVirtualLAN")
    instance = SingleInstance()
    if not instance.claim():
        # Already running, probably hidden in the tray; it shows itself.
        return 0
    # Closing the window hides it in the tray; only Exit quits.
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    controller = AppController()
    # Start Steam before the window exists: its overlay has to be loaded before
    # the window's Direct3D device is created to be able to draw into it.
    controller.start()
    app.aboutToQuit.connect(controller.shutdown)
    window = MainWindow(controller)
    instance.activated.connect(window.show_window)
    app.commitDataRequest.connect(lambda manager: window.prepare_for_session_end())
    window.show()
    return app.exec()
