import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from steamlan.app.controller import Presence, Screen, View  # noqa: E402
from steamlan.app.network import MemberView  # noqa: E402
from steamlan.gui.instance import SingleInstance  # noqa: E402
from steamlan.gui.style import STYLE  # noqa: E402
from steamlan.gui.window import MainWindow  # noqa: E402

LOBBY = 109775240917097000
MEMBERS = (
    MemberView(2, "Me", True, True, "Online", "ok", "10.77.0.2"),
    MemberView(1, "Alice", False, True, "Online", "ok", "10.77.0.1"),
    MemberView(3, "Carol", False, False, "Offline", "neutral", "10.77.0.3"),
)
HOME = View(Screen.HOME, True, "Signed in to Steam as Me", "ok")
ONLINE = View(
    Screen.NETWORK,
    True,
    "",
    "ok",
    presence=Presence.ONLINE,
    lobby_id=LOBBY,
    access_code="7K2QD-M9XTE",
    can_invite=True,
    network_status="Online",
    network_tone="ok",
    online_summary="2 of 3 online",
    adapter_status="Virtual network ready: 10.77.0.2",
    adapter_tone="ok",
    members=MEMBERS,
)
OFFLINE = View(
    Screen.NETWORK,
    True,
    "",
    "ok",
    presence=Presence.OFFLINE,
    lobby_id=LOBBY,
    access_code="7K2QD-M9XTE",
    network_status="Offline",
    members=tuple(
        MemberView(m.steam_id, m.name, m.is_you, False, "Offline", "neutral", m.address)
        for m in MEMBERS
    ),
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


def make_window(tray_available=True, view=HOME):
    controller = FakeController(view)
    window = MainWindow(controller, tray_available=tray_available)
    window.timer.stop()
    window.notices = []
    if window.tray is not None:
        window.tray.notify = lambda title, text: window.notices.append(title)
    return window, controller


@pytest.fixture
def window(app):
    window, controller = make_window()
    yield window, controller
    window._exiting = True
    window.close()
    if window.tray is not None:
        window.tray.hide()


def show(window, controller, view):
    controller.current = view
    window.render()
    return window.pages.currentWidget()


def texts(widget):
    return [label.text() for label in widget.findChildren(QLabel) if label.isVisibleTo(widget)]


def test_every_screen_renders(window):
    window, controller = window

    assert show(window, controller, HOME) is window.home
    assert (
        show(window, controller, View(Screen.JOIN, True, "", "ok", error="Invalid Lobby ID"))
        is window.join
    )
    assert window.join.message.text() == "Invalid Lobby ID"
    assert show(window, controller, ONLINE) is window.network
    assert window.network.access_code.value.text() == "7K2QD-M9XTE"
    assert window.network.member_count.text() == "2 of 3 online"
    assert len(window.network._rows) == 3


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


def test_members_show_address_and_online_state_and_no_roles(window):
    window, controller = window

    show(window, controller, ONLINE)

    rows = window.network._rows
    assert rows[1].address.text() == "10.77.0.1"
    assert rows[1].status.text.text() == "Online"
    assert rows[3].status.text.text() == "Offline"
    assert rows[3].name.objectName() == "memberNameOffline"
    assert not rows[2].you_badge.isHidden()
    assert rows[1].you_badge.isHidden()
    shown = texts(window.network)
    for role in ("Host", "Owner", "Coordinator", "Admin", "Member"):
        assert role not in shown
    assert window.network.adapter_status.text.text() == "Virtual network ready: 10.77.0.2"


def test_member_without_an_address_yet(window):
    window, controller = window
    joining = (MemberView(3, "Friend", False, True, "Joining", "pending"),)

    show(window, controller, View(Screen.NETWORK, True, "", "ok", members=joining))

    assert window.network._rows[3].address.text() == ""


def test_online_network_page(window):
    window, controller = window
    show(window, controller, ONLINE)

    assert not window.network.invite_button.isHidden()
    assert window.network.online_button.text() == "Go Offline"
    assert not window.network.leave_button.isHidden()


def test_offline_network_page(window):
    window, controller = window
    show(window, controller, OFFLINE)

    assert window.network.invite_button.isHidden()
    assert window.network.online_button.text() == "Go Online"
    assert window.network.adapter_status.isHidden()
    assert [row.status.text.text() for row in window.network._rows.values()] == ["Offline"] * 3


def test_network_page_buttons(window):
    window, controller = window
    window.confirm_leave = lambda: True
    show(window, controller, ONLINE)

    window.network.online_button.click()
    show(window, controller, OFFLINE)
    window.network.online_button.click()
    window.network.leave_button.click()

    assert controller.calls.count(("go_offline",)) == 1
    assert controller.calls.count(("go_online",)) == 1
    assert controller.calls.count(("leave_network",)) == 1


def test_leave_network_needs_confirmation(window):
    window, controller = window
    window.confirm_leave = lambda: False
    show(window, controller, ONLINE)

    window.network.leave_button.click()

    assert ("leave_network",) not in controller.calls


def test_invite_button_opens_steam_overlay(window):
    window, controller = window
    show(window, controller, ONLINE)

    window.network.invite_button.click()

    assert "open_invite" in controller.calls
    assert window.network.notice.text() == "Pick friends to invite in the Steam overlay"
    assert window.network.notice.objectName() == "notice"


def test_invite_failure_is_shown(window):
    window, controller = window
    controller.invite_error = "The Steam overlay isn't available."
    show(window, controller, ONLINE)

    window.network.invite_button.click()

    assert window.network.notice.text() == "The Steam overlay isn't available."
    assert window.network.notice.objectName() == "error"


def test_errors_are_shown_on_the_network_page_and_cleared(window):
    window, controller = window
    show(window, controller, View(**{**OFFLINE.__dict__, "error": "Could not go online"}))
    assert window.network.notice.text() == "Could not go online"

    show(window, controller, OFFLINE)
    assert window.network.notice.text() == ""


# The tray


def test_closing_the_window_hides_it_and_keeps_running(window):
    window, controller = window
    window.show()

    window.close()

    assert window.isHidden()
    assert ("shutdown",) not in controller.calls
    assert window.timer is not None
    assert window.notices == ["SteamVirtualLAN is still running"]
    window.show()
    window.close()
    assert window.notices == ["SteamVirtualLAN is still running"]


def test_the_timer_keeps_pumping_steam_while_hidden(app):
    window, controller = make_window()
    window.timer.start()
    window.show()
    window.close()

    assert window.timer.isActive()
    window._tick()
    assert "tick" in controller.calls
    window.exit_app()


def test_tray_open_shows_the_window_again(window):
    window, controller = window
    window.show()
    window.close()

    window.tray.open_action.trigger()

    assert window.isVisible()


def test_exit_shuts_down(window, monkeypatch):
    window, controller = window
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", lambda *args: quit_calls.append(1))
    window.show()

    window.tray.exit_action.trigger()
    window.tray.exit_action.trigger()

    assert controller.calls.count(("shutdown",)) == 1
    assert not window.timer.isActive()
    assert window.isHidden()
    assert quit_calls


def test_tray_menu_without_a_network(window):
    window, controller = window
    show(window, controller, HOME)

    tray = window.tray
    assert tray.open_action.text() == "Open SteamVirtualLAN"
    assert tray.status_action.text() == "Not in a network"
    assert not tray.status_action.isEnabled()
    assert not tray.toggle_action.isVisible()
    assert not tray.leave_action.isVisible()
    assert tray.exit_action.text() == "Exit"


def test_tray_menu_online(window):
    window, controller = window
    show(window, controller, ONLINE)

    tray = window.tray
    assert tray.status_action.text() == "Online · 10.77.0.2"
    assert (tray.toggle_action.isVisible(), tray.toggle_action.text()) == (True, "Go Offline")
    assert tray.leave_action.isVisible()
    assert "Online" in tray.icon.toolTip()


def test_tray_menu_offline(window):
    window, controller = window
    show(window, controller, OFFLINE)

    assert window.tray.status_action.text() == "Offline"
    assert window.tray.toggle_action.text() == "Go Online"


def test_tray_actions_call_the_controller(window):
    window, controller = window
    window.confirm_leave = lambda: True
    show(window, controller, ONLINE)

    window.tray.toggle_action.trigger()
    show(window, controller, OFFLINE)
    window.tray.toggle_action.trigger()
    window.tray.leave_action.trigger()

    assert [call for call in controller.calls if call != "tick"] == [
        ("go_offline",),
        ("go_online",),
        ("leave_network",),
    ]


def test_without_a_tray_closing_minimizes(app):
    window, controller = make_window(tray_available=False)
    window.show()

    window.close()

    assert window.tray is None
    assert window.isMinimized() or window.isVisible()
    assert ("shutdown",) not in controller.calls
    window._exiting = True
    window.close()


def test_session_end_lets_the_window_close(app):
    window, controller = make_window()
    window.show()

    window.prepare_for_session_end()
    window.close()

    assert window.isHidden()
    window.tray.hide()


# One instance


def test_second_instance_asks_the_first_to_show_itself(app):
    name = f"SteamVirtualLAN-test-{os.getpid()}"
    first = SingleInstance(name)
    activated = []
    first.activated.connect(lambda: activated.append(1))
    assert first.claim()

    second = SingleInstance(name)
    assert not second.claim()
    deadline = time.monotonic() + 5
    while not activated and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert activated
    first.close()
    third = SingleInstance(name)
    assert third.claim()
    third.close()
