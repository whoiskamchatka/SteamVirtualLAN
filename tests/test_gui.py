import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from steamlan.app.controller import Screen, View  # noqa: E402
from steamlan.app.network import MemberView  # noqa: E402
from steamlan.gui.style import STYLE  # noqa: E402
from steamlan.gui.window import MainWindow  # noqa: E402

LOBBY = 109775240917097000
MEMBERS = (
    MemberView(1, "Host", False, True, "Connected", "ok"),
    MemberView(2, "Me", True, False, "Connected", "ok"),
)


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    return app


class FakeController:
    def __init__(self, view):
        self.current = view
        self.calls = []
        self.invite_error = ""

    def view(self):
        return self.current

    def tick(self):
        self.calls.append("tick")

    def open_invite(self):
        self.calls.append("open_invite")
        if self.invite_error:
            raise ValueError(self.invite_error)
        return "Pick friends to invite in the Steam overlay"

    def overlay_needs_present(self):
        return False

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


@pytest.fixture
def window(app):
    controller = FakeController(View(Screen.HOME, True, "Signed in to Steam as Me", "ok"))
    window = MainWindow(controller)
    window.timer.stop()
    yield window, controller
    window.close()


def show(window, controller, view):
    controller.current = view
    window.render()
    return window.pages.currentWidget()


def test_every_screen_renders(window):
    window, controller = window
    host_view = View(
        Screen.LOBBY,
        True,
        "",
        "ok",
        lobby_id=LOBBY,
        access_code="7K2QD-M9XTE",
        is_host=True,
        network_status="Connected to 1 of 1",
        network_tone="ok",
        members=MEMBERS,
    )

    assert show(window, controller, controller.current) is window.home
    assert (
        show(window, controller, View(Screen.JOIN, True, "", "ok", error="Invalid Lobby ID"))
        is window.join
    )
    assert window.join.message.text() == "Invalid Lobby ID"
    assert show(window, controller, host_view) is window.lobby
    assert window.lobby.access_code.value.text() == "7K2QD-M9XTE"
    assert len(window.lobby._rows) == 2

    member_view = View(Screen.LOBBY, True, "", "ok", lobby_id=LOBBY, members=MEMBERS[:1])
    show(window, controller, member_view)
    assert window.lobby.access_code.isHidden()
    assert window.lobby.invite_button.isHidden()
    assert len(window.lobby._rows) == 1


def test_steam_error_disables_actions(window):
    window, controller = window
    show(window, controller, View(Screen.HOME, False, "Steam is not running", "error"))

    assert not window.home.create_button.isEnabled()
    assert not window.home.retry_button.isHidden()


def test_buttons_call_the_controller(window):
    window, controller = window
    window.home.create_button.click()
    window.home.join_button.click()

    show(window, controller, View(Screen.JOIN, True, "", "ok"))
    window.join.lobby_id.setText(str(LOBBY))
    window.join.access_code.setText("7K2QD-M9XTE")
    window.join.join_button.click()

    assert ("create_lobby",) in controller.calls
    assert ("show_join",) in controller.calls
    assert ("join", str(LOBBY), "7K2QD-M9XTE") in controller.calls


def test_join_button_needs_both_fields(window):
    window, controller = window
    show(window, controller, View(Screen.JOIN, True, "", "ok"))
    window.join.lobby_id.setText(str(LOBBY))

    assert not window.join.join_button.isEnabled()


def test_closing_the_window_shuts_down(window):
    window, controller = window
    window.close()

    assert ("shutdown",) in controller.calls


HOST_VIEW = View(
    Screen.LOBBY,
    True,
    "",
    "ok",
    lobby_id=LOBBY,
    access_code="7K2QD-M9XTE",
    is_host=True,
    network_status="Waiting for peers",
    members=MEMBERS[:1],
)


def test_invite_button_opens_steam_overlay(window):
    window, controller = window
    show(window, controller, HOST_VIEW)

    window.lobby.invite_button.click()

    assert "open_invite" in controller.calls
    assert window.lobby.notice.text() == "Pick friends to invite in the Steam overlay"
    assert window.lobby.notice.objectName() == "notice"


def test_invite_failure_is_shown(window):
    window, controller = window
    controller.invite_error = "The Steam overlay isn't available."
    show(window, controller, HOST_VIEW)

    window.lobby.invite_button.click()

    assert window.lobby.notice.text() == "The Steam overlay isn't available."
    assert window.lobby.notice.objectName() == "error"
