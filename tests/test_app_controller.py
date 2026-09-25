import pytest
from app_fakes import (
    CREATE_CALL,
    GUEST,
    HOST,
    LISTEN_SOCKET,
    LOBBY,
    FakeSteam,
    invite_accepted,
    join_requested,
    lobby_created,
    lobby_entered,
    status,
)

from steamlan.app import access
from steamlan.app.controller import CALL_TIMEOUT, AppController, Screen
from steamlan.steam import ConnectionState, SteamCallback, SteamInitError
from steamlan.steam.native import ChatRoomEnterResponse, EResult

CODE_TEXT = "7K2QD-M9XTE"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def started(steam, clock=None):
    controller = AppController(lambda: steam, clock or Clock())
    controller.start()
    return controller


def host_in_lobby(steam=None):
    steam = steam or FakeSteam(HOST)
    controller = started(steam)
    controller.create_lobby()
    steam.frames = [[lobby_created()]]
    controller.tick()
    return steam, controller


def guest_joining(steam=None, **lobby):
    steam = steam or FakeSteam(GUEST, [HOST, GUEST])
    steam.lobby_metadata = {access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE}
    controller = started(steam)
    controller.show_join()
    controller.join(str(LOBBY), CODE_TEXT)
    steam.frames = [[lobby_entered(**lobby)]]
    controller.tick()
    return steam, controller


def test_start_shows_signed_in_user():
    view = started(FakeSteam()).view()

    assert view.screen is Screen.HOME
    assert view.steam_ready
    assert (view.steam_status, view.steam_tone) == ("Signed in to Steam as Me", "ok")


def test_start_failure_is_shown_and_can_be_retried():
    attempts = []

    def open_steam():
        attempts.append(1)
        if len(attempts) == 1:
            raise SteamInitError("Steam API initialization failed: Steam is not running.")
        return FakeSteam()

    controller = AppController(open_steam, Clock())
    controller.start()
    view = controller.view()
    assert not view.steam_ready
    assert view.steam_tone == "error"
    assert "Steam is not running" in view.steam_status

    controller.create_lobby()
    assert controller.view().busy == ""

    controller.start()
    assert controller.view().steam_ready


def test_create_lobby():
    steam = FakeSteam(HOST)
    controller = started(steam)

    controller.create_lobby()
    assert controller.view().busy == "Creating lobby..."
    controller.tick()
    assert controller.view().screen is Screen.HOME

    steam.frames = [[SteamCallback(304, b""), lobby_created()]]
    controller.tick()

    view = controller.view()
    assert view.screen is Screen.LOBBY
    assert view.is_host
    assert view.lobby_id == LOBBY
    assert access.normalize_access_code(view.access_code)
    assert view.network_status == "Waiting for peers"
    assert [member.steam_id for member in view.members] == [HOST]
    assert steam.called("request_create_lobby") == [(8,)]
    assert steam.lobby_metadata == {access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE}
    assert steam.called("create_listen_socket") == [(0,)]


def test_access_code_is_not_put_in_lobby_metadata():
    steam, controller = host_in_lobby()
    code = access.normalize_access_code(controller.view().access_code)

    assert all(code not in value for value in steam.lobby_metadata.values())


def test_create_lobby_failure():
    steam = FakeSteam(HOST)
    controller = started(steam)
    controller.create_lobby()

    steam.frames = [[lobby_created(EResult.LIMIT_EXCEEDED, lobby_id=0)]]
    controller.tick()

    view = controller.view()
    assert (view.screen, view.busy, view.error) == (Screen.HOME, "", "Could not create lobby")


def test_create_lobby_timeout():
    clock = Clock()
    steam = FakeSteam(HOST)
    controller = started(steam, clock)
    controller.create_lobby()

    clock.now = CALL_TIMEOUT + 1
    controller.tick()

    assert controller.view().error == "Steam did not answer in time"
    assert controller.view().busy == ""


def test_create_is_ignored_while_busy():
    steam = FakeSteam(HOST)
    controller = started(steam)

    controller.create_lobby()
    controller.create_lobby()

    assert len(steam.called("request_create_lobby")) == 1


@pytest.mark.parametrize(
    ("lobby_text", "code_text", "error"),
    [
        ("", CODE_TEXT, "Invalid Lobby ID"),
        ("12345", CODE_TEXT, "Invalid Lobby ID"),
        (str(GUEST), CODE_TEXT, "Invalid Lobby ID"),
        (str(LOBBY), "123", "An access code has 10 letters and digits"),
    ],
)
def test_join_validates_input(lobby_text, code_text, error):
    steam = FakeSteam(GUEST)
    controller = started(steam)
    controller.show_join()

    controller.join(lobby_text, code_text)

    view = controller.view()
    assert (view.screen, view.error, view.busy) == (Screen.JOIN, error, "")
    assert steam.called("request_join_lobby") == []


def test_join_lobby():
    steam, controller = guest_joining()

    view = controller.view()
    assert view.screen is Screen.LOBBY
    assert not view.is_host
    assert view.access_code == ""
    assert view.network_status == "Connecting to host"
    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert controller.network.access_code == "7K2QDM9XTE"
    assert controller.network.host_id == HOST


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (ChatRoomEnterResponse.DOESNT_EXIST, "Lobby not found"),
        (ChatRoomEnterResponse.FULL, "That lobby is full"),
        (ChatRoomEnterResponse.NOT_ALLOWED, "You are not allowed to join that lobby"),
        (ChatRoomEnterResponse.ERROR, "Could not join lobby"),
    ],
)
def test_join_failure(response, error):
    steam, controller = guest_joining(response=response)

    view = controller.view()
    assert (view.screen, view.error, view.busy) == (Screen.JOIN, error, "")
    assert steam.called("create_listen_socket") == []


def test_join_rejects_lobby_of_another_app():
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam)
    controller.show_join()
    controller.join(str(LOBBY), CODE_TEXT)
    steam.frames = [[lobby_entered()]]

    controller.tick()

    assert controller.view().error == "That lobby is not a SteamVirtualLAN network"
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert controller.network is None


def test_wrong_access_code_returns_to_join_form():
    steam, controller = guest_joining()
    steam.frames = [
        [SteamCallback(0, b"")],
        [status(7, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(7, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    for _ in range(3):
        controller.tick()
    steam.inbox[7] = [access.DENIED]

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (Screen.JOIN, "Incorrect access code")
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_leave_cleans_up():
    steam, controller = host_in_lobby(FakeSteam(HOST, [HOST, GUEST]))
    controller.tick()

    controller.leave()

    assert controller.view().screen is Screen.HOME
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]
    assert steam.closed == [(101, False)]
    assert steam.running


def test_cleanup_continues_after_a_failure():
    steam, controller = host_in_lobby()
    steam.fail.add("leave_lobby")

    controller.leave()

    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_shutdown_cleans_up_and_stops_steam():
    steam, controller = host_in_lobby()

    controller.shutdown()
    controller.shutdown()

    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]
    assert steam.called("close") == [()]
    controller.tick()


def test_steam_errors_during_tick_do_not_crash():
    steam, controller = host_in_lobby()
    steam.frames = [[SteamCallback(1221, b"short")]]

    controller.tick()

    assert controller.view().screen is Screen.LOBBY


def test_create_result_is_matched_by_call_handle():
    steam = FakeSteam(HOST)
    controller = started(steam)
    controller.create_lobby()
    steam.frames = [[lobby_created(api_call=CREATE_CALL + 50)]]

    controller.tick()

    assert controller.view().busy == "Creating lobby..."


def test_open_invite_uses_steam_overlay_with_lobby_and_code():
    steam, controller = host_in_lobby()
    code = access.normalize_access_code(controller.view().access_code)

    assert controller.open_invite() == "Pick friends to invite in the Steam overlay"
    assert steam.called("open_invite_dialog") == [(f"steamvirtuallan:1:{LOBBY}:{code}",)]
    assert steam.called("invite_to_lobby") == []


def test_open_invite_without_overlay():
    steam, controller = host_in_lobby()
    steam.overlay_enabled = False

    with pytest.raises(ValueError, match="overlay isn't available"):
        controller.open_invite()
    assert steam.called("open_invite_dialog") == []


def test_open_invite_steam_failure():
    steam, controller = host_in_lobby()
    steam.fail.add("open_invite_dialog")

    with pytest.raises(ValueError, match="could not open the invite dialog"):
        controller.open_invite()


def test_open_invite_without_lobby():
    controller = started(FakeSteam(HOST))

    with pytest.raises(ValueError, match="Create a lobby first"):
        controller.open_invite()


def test_open_invite_without_steam():
    def open_steam():
        raise SteamInitError("Steam is not running")

    controller = AppController(open_steam, Clock())
    controller.start()

    with pytest.raises(ValueError, match="Steam is not running"):
        controller.open_invite()


def test_only_host_invites():
    steam, controller = guest_joining()

    with pytest.raises(ValueError, match="Only the host"):
        controller.open_invite()


def test_accepted_overlay_invite_joins_with_its_code():
    steam = FakeSteam(GUEST, [HOST, GUEST])
    steam.lobby_metadata = {access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE}
    controller = started(steam)
    connect = access.invite_connect_string(LOBBY, "7K2QDM9XTE")
    steam.frames = [[invite_accepted(connect)], [lobby_entered()]]

    controller.tick()
    assert controller.view().busy == "Joining..."
    assert steam.called("request_join_lobby") == [(LOBBY,)]
    controller.tick()

    assert controller.view().screen is Screen.LOBBY
    assert controller.network.access_code == "7K2QDM9XTE"


def test_foreign_connect_string_is_ignored():
    steam = FakeSteam(GUEST)
    controller = started(steam)
    steam.frames = [[invite_accepted("+connect 10.0.0.1:27015")]]

    controller.tick()

    assert controller.view().busy == ""
    assert steam.called("request_join_lobby") == []


def test_plain_lobby_invite_still_joins_without_code():
    steam = FakeSteam(GUEST, [HOST, GUEST])
    steam.lobby_metadata = {access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE}
    controller = started(steam)
    steam.frames = [[join_requested()], [lobby_entered()]]

    controller.tick()
    controller.tick()

    assert controller.view().screen is Screen.LOBBY
    assert controller.network.access_code == ""


def test_overlay_needs_present():
    steam = FakeSteam(HOST)
    controller = started(steam)
    assert controller.overlay_needs_present() is False

    steam.needs_present = True
    assert controller.overlay_needs_present() is True

    controller.shutdown()
    assert controller.overlay_needs_present() is False
