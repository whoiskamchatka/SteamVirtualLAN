import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from steamlan.app.controller import AppController, Screen
from steamlan.gui.pages import HomePage, JoinPage, LobbyPage
from steamlan.gui.style import STYLE

# Steam callbacks are pumped on the UI thread; every call involved returns
# quickly, and results that take longer arrive through later pumps. Steam asks
# apps that only redraw on events to check for overlay frames at about 33 Hz.
PUMP_INTERVAL_MS = 30


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("SteamVirtualLAN")
        self.resize(440, 560)
        self.setMinimumSize(400, 480)

        self.home = HomePage()
        self.join = JoinPage()
        self.lobby = LobbyPage()
        self.pages = QStackedWidget()
        self.pages.setObjectName("root")
        for page in (self.home, self.join, self.lobby):
            self.pages.addWidget(page)
        self.setCentralWidget(self.pages)

        self.home.create.connect(self._action(controller.create_lobby))
        self.home.join.connect(self._action(controller.show_join))
        self.home.retry.connect(self._action(controller.start))
        self.join.back.connect(self._action(controller.show_home))
        self.join.submit.connect(lambda lobby, code: self._run(controller.join, lobby, code))
        self.lobby.invite.connect(self._invite)
        self.lobby.leave.connect(self._action(controller.leave))

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
        page = {Screen.HOME: self.home, Screen.JOIN: self.join, Screen.LOBBY: self.lobby}
        current = page[view.screen]
        if self.pages.currentWidget() is not current:
            self.lobby.show_notice("")
        self.pages.setCurrentWidget(current)
        current.render(view)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.controller.shutdown()
        event.accept()

    def _tick(self) -> None:
        self.controller.tick()
        self.render()
        if self.controller.overlay_needs_present():
            self.update()

    def _action(self, method):
        return lambda: self._run(method)

    def _run(self, method, *args) -> None:
        method(*args)
        self.render()

    def _invite(self) -> None:
        try:
            self.lobby.show_notice(self.controller.open_invite())
        except ValueError as exc:
            self.lobby.show_notice(str(exc), error=True)


def run() -> int:
    # The Steam overlay can only draw into windows presented with Direct3D,
    # OpenGL or Vulkan, so have Qt present its widgets with Direct3D 11.
    os.environ.setdefault("QT_WIDGETS_RHI", "1")
    os.environ.setdefault("QT_WIDGETS_RHI_BACKEND", "d3d11")
    app = QApplication(sys.argv)
    app.setApplicationName("SteamVirtualLAN")
    app.setStyleSheet(STYLE)
    controller = AppController()
    # Start Steam before the window exists: its overlay has to be loaded before
    # the window's Direct3D device is created to be able to draw into it.
    controller.start()
    app.aboutToQuit.connect(controller.shutdown)
    window = MainWindow(controller)
    window.show()
    return app.exec()
