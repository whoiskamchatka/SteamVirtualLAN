from ipaddress import IPv4Address

import pytest
from app_fakes import (
    CREATE_CALL,
    GUEST,
    HOST,
    LISTEN_SOCKET,
    LOBBY,
    FakeHelper,
    FakeSteam,
    invite_accepted,
    ipv4_packet,
    join_requested,
    lobby_created,
    lobby_entered,
    status,
)

from steamlan.adapter.launcher import UAC_DECLINED
from steamlan.app import access, tunnel
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
    helpers = []

    def make_helper():
        helpers.append(FakeHelper(steam))
        return helpers[-1]

    controller = AppController(lambda: steam, clock or Clock(), make_helper)
    controller.helpers = helpers
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


A1 = IPv4Address("10.77.0.1")
A2 = IPv4Address("10.77.0.2")


def order(steam):
    """The cleanup steps in the order they happened."""
    steps = ("helper_close", "close_connection", "leave_lobby", "close_listen_socket", "close")
    return [call[0] for call in steam.calls if call[0] in steps]


def host_with_guest():
    """A host whose adapter is ready and whose guest has been admitted."""
    steam, controller = host_in_lobby(FakeSteam(HOST, [HOST, GUEST]))
    controller.tick()
    (connection,) = [call[1] for call in steam.called("connect_p2p") if call[0] == GUEST]
    steam.frames = [[status(connection, ConnectionState.CONNECTED, GUEST)]]
    controller.tick()
    steam.inbox[connection] = [access.auth_message(controller.network.access_code)]
    controller.tick()
    controller.helper.become_ready()
    controller.tick()
    return steam, controller, connection


def test_entering_a_network_starts_the_adapter_helper():
    steam, controller = host_in_lobby()

    (helper,) = controller.helpers
    assert controller.helper is helper
    assert helper.state is helper.State.STARTING
    view = controller.view()
    assert view.screen is Screen.LOBBY
    assert (view.adapter_status, view.adapter_tone) == (
        "Waiting for Administrator permission...",
        "pending",
    )


def test_joining_starts_the_adapter_helper():
    steam, controller = guest_joining()

    assert len(controller.helpers) == 1
    assert controller.helper.state is controller.helper.State.STARTING


def test_failed_join_starts_no_helper():
    steam, controller = guest_joining(response=ChatRoomEnterResponse.DOESNT_EXIST)

    assert controller.helpers == []


def test_host_adapter_gets_the_host_address():
    steam, controller = host_in_lobby()
    controller.helper.become_ready()

    controller.tick()

    assert controller.helper.requested == [A1]
    view = controller.view()
    assert (view.adapter_status, view.adapter_tone) == ("Virtual network ready: 10.77.0.1", "ok")
    assert view.members[0].address == "10.77.0.1"


def test_guest_adapter_waits_for_the_host_to_assign_an_address():
    steam, controller = guest_joining()
    controller.helper.become_ready()
    steam.frames = [
        [status(7, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(7, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    controller.tick()
    controller.tick()

    assert controller.helper.requested == []
    assert controller.view().adapter_status == "Waiting for the host to assign an address"

    steam.inbox[7] = [access.ACCEPTED, access.members_message({HOST: A1, GUEST: A2})]
    controller.tick()

    assert controller.helper.requested == [A2]
    view = controller.view()
    assert view.adapter_status == "Virtual network ready: 10.77.0.2"
    assert {member.steam_id: member.address for member in view.members} == {
        HOST: "10.77.0.1",
        GUEST: "10.77.0.2",
    }


def test_packets_from_windows_go_to_the_member_that_owns_the_address():
    steam, controller, connection = host_with_guest()
    packet = ipv4_packet(A1, A2)
    controller.helper.from_windows = [packet, ipv4_packet(A1, "10.77.0.9")]

    controller.tick()

    assert steam.unreliable == [(connection, tunnel.packet_message(packet))]


def test_packets_from_members_go_to_windows():
    steam, controller, connection = host_with_guest()
    packet = ipv4_packet(A2, A1, b"reply")
    steam.inbox[connection] = [tunnel.packet_message(packet)]

    controller.tick()

    assert controller.helper.to_windows == [packet]


def test_packets_before_the_adapter_is_ready_are_dropped():
    steam, controller, connection = host_with_guest()
    controller.helper.state = controller.helper.State.STARTING
    steam.inbox[connection] = [tunnel.packet_message(ipv4_packet(A2, A1))]
    controller.tick()

    controller.helper.become_ready()
    controller.tick()

    assert controller.helper.to_windows == []
    assert controller.network.take_packets() == []


def test_leave_removes_the_adapter_before_leaving_steam():
    steam, controller, connection = host_with_guest()
    helper = controller.helper

    controller.leave()

    assert helper.state is helper.State.STOPPED
    assert controller.helper is None
    assert order(steam) == [
        "helper_close",
        "close_connection",
        "leave_lobby",
        "close_listen_socket",
    ]


def test_shutdown_removes_the_adapter_then_cleans_up_steam():
    steam, controller, connection = host_with_guest()

    controller.shutdown()
    controller.shutdown()

    assert order(steam) == [
        "helper_close",
        "close_connection",
        "leave_lobby",
        "close_listen_socket",
        "close",
    ]


def test_declined_uac_prompt_leaves_the_network():
    steam, controller = host_in_lobby()
    controller.helper.fail(UAC_DECLINED)

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (Screen.HOME, UAC_DECLINED)
    assert controller.network is None and controller.helper is None
    assert order(steam) == ["helper_close", "leave_lobby", "close_listen_socket"]
    # Nothing is left behind, and a new network can be created.
    controller.create_lobby()
    assert controller.view().busy == "Creating lobby..."


def test_adapter_failure_sends_a_member_back_to_the_join_form():
    steam, controller = guest_joining()
    controller.helper.become_ready()
    controller.helper.fail("The virtual network helper stopped unexpectedly")

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (
        Screen.JOIN,
        "The virtual network helper stopped unexpectedly",
    )
    assert steam.called("leave_lobby") == [(LOBBY,)]


def test_helper_that_cannot_start_leaves_the_network():
    steam = FakeSteam(HOST)

    class BrokenHelper(FakeHelper):
        def start(self):
            raise OSError("no pipe")

    controller = AppController(lambda: steam, Clock(), lambda: BrokenHelper(steam))
    controller.start()
    controller.create_lobby()
    steam.frames = [[lobby_created()]]

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (Screen.HOME, "Could not start the virtual network")
    assert order(steam) == ["helper_close", "leave_lobby", "close_listen_socket"]
