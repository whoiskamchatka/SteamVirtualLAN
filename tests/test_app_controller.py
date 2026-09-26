"""AppController with a FakeSteam: one PC. test_app_coordination.py has whole networks."""

from ipaddress import IPv4Address

import pytest
from app_fakes import (
    CREATE_CALL,
    GUEST,
    HOST,
    JOIN_CALL,
    LISTEN_SOCKET,
    LOBBY,
    OTHER_LOBBY,
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
from steamlan.app import access, roster, tunnel
from steamlan.app.controller import (
    CALL_TIMEOUT,
    FAREWELL_TIMEOUT,
    NETWORK_GONE,
    AppController,
    Presence,
    Screen,
)
from steamlan.app.state import SavedNetwork, StateStore
from steamlan.steam import ConnectionState, SteamCallback, SteamInitError
from steamlan.steam.native import ChatRoomEnterResponse, EResult

CODE = "7K2QDM9XTE"
CODE_TEXT = "7K2QD-M9XTE"
NETWORK_ID = "0123456789abcdef"
A1 = IPv4Address("10.77.0.1")
A2 = IPv4Address("10.77.0.2")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


def started(steam, clock=None, store=None):
    helpers = []

    def make_helper():
        helpers.append(FakeHelper(steam))
        return helpers[-1]

    controller = AppController(lambda: steam, clock or Clock(), make_helper, store)
    controller.helpers = helpers
    controller.start()
    return controller


def creator(steam=None, store=None):
    steam = steam or FakeSteam(HOST)
    controller = started(steam, store=store)
    controller.create_lobby()
    steam.frames = [[lobby_created()]]
    controller.tick()
    return steam, controller


def network_lobby(steam, members=None):
    """Lobby metadata as the member coordinating a network writes it."""
    steam.lobby_metadata = {
        access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE,
        access.NETWORK_ID_KEY: NETWORK_ID,
        roster.ROSTER_KEY: roster.encode(members or {HOST: A1}),
    }


def joiner(steam=None, store=None, clock=None, **lobby):
    steam = steam or FakeSteam(GUEST, [HOST, GUEST])
    network_lobby(steam)
    controller = started(steam, clock, store)
    controller.show_join()
    controller.join(str(LOBBY), CODE_TEXT)
    steam.frames = [[lobby_entered(**lobby)]]
    controller.tick()
    return steam, controller


def admitted(steam, controller, connection=7):
    """The coordinator (HOST) connects, and admits this member as 10.77.0.2."""
    steam.frames = [
        [status(connection, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(connection, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    controller.tick()
    controller.tick()
    steam.lobby_metadata[roster.ROSTER_KEY] = roster.encode({HOST: A1, GUEST: A2})
    steam.inbox[connection] = [access.accepted_message(CODE)]
    controller.tick()
    return connection


def saved_network(online=True, members=None):
    return SavedNetwork(
        LOBBY,
        NETWORK_ID,
        CODE,
        tuple(sorted((members or {HOST: A1, GUEST: A2}).items())),
        ((HOST, "Host"),),
        online,
    )


# Starting


def test_start_shows_signed_in_user():
    view = started(FakeSteam()).view()

    assert view.screen is Screen.HOME
    assert view.steam_ready
    assert (view.steam_status, view.steam_tone) == ("Signed in to Steam as Me", "ok")
    assert view.presence is Presence.NONE
    assert view.tray_status == "Not in a network"


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
    assert view.tray_status == "Steam is not running"

    controller.create_lobby()
    assert controller.view().busy == ""

    controller.start()
    assert controller.view().steam_ready


# Creating a network


def test_create_network():
    steam = FakeSteam(HOST)
    controller = started(steam)

    controller.create_lobby()
    assert controller.view().busy == "Creating lobby..."
    # Listening before the lobby exists, so no member's first connection fails.
    assert steam.called("create_listen_socket") == [(0,)]
    controller.tick()
    assert controller.view().screen is Screen.HOME

    steam.frames = [[SteamCallback(304, b""), lobby_created()]]
    controller.tick()

    view = controller.view()
    assert view.screen is Screen.NETWORK
    assert view.presence is Presence.ONLINE
    assert view.lobby_id == LOBBY
    assert access.normalize_access_code(view.access_code)
    assert view.network_status == "Online"
    assert view.can_invite
    assert [(m.steam_id, m.status, m.address) for m in view.members] == [
        (HOST, "Online", "10.77.0.1")
    ]
    assert steam.called("request_create_lobby") == [(8,)]
    assert steam.lobby_metadata[access.LOBBY_MARKER_KEY] == "2"
    assert access.is_network_id(steam.lobby_metadata[access.NETWORK_ID_KEY])
    assert roster.decode(steam.lobby_metadata[roster.ROSTER_KEY]) == {HOST: A1}
    assert len(steam.called("create_listen_socket")) == 1


def test_created_network_is_saved(store):
    steam, controller = creator(store=store)

    saved = store.load(HOST)
    assert saved == controller.saved
    assert (saved.lobby_id, saved.members, saved.online) == (LOBBY, ((HOST, A1),), True)
    assert saved.access_code == access.normalize_access_code(controller.view().access_code)
    assert saved.network_id == steam.lobby_metadata[access.NETWORK_ID_KEY]


def test_access_code_is_not_put_in_lobby_metadata():
    steam, controller = creator()
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
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_create_lobby_timeout():
    clock = Clock()
    steam = FakeSteam(HOST)
    controller = started(steam, clock)
    controller.create_lobby()

    clock.now = CALL_TIMEOUT + 1
    controller.tick()

    assert controller.view().error == "Steam did not answer in time"
    assert controller.view().busy == ""
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_create_is_ignored_while_busy():
    steam = FakeSteam(HOST)
    controller = started(steam)

    controller.create_lobby()
    controller.create_lobby()

    assert len(steam.called("request_create_lobby")) == 1


def test_create_result_is_matched_by_call_handle():
    steam = FakeSteam(HOST)
    controller = started(steam)
    controller.create_lobby()
    steam.frames = [[lobby_created(api_call=CREATE_CALL + 50)]]

    controller.tick()

    assert controller.view().busy == "Creating lobby..."


def test_no_second_network_while_in_one():
    steam, controller = creator()

    controller.create_lobby()
    controller.show_join()
    controller.join(str(OTHER_LOBBY), CODE_TEXT)

    assert len(steam.called("request_create_lobby")) == 1
    assert steam.called("request_join_lobby") == []
    assert controller.view().screen is Screen.NETWORK


# Joining a network


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


def test_join_network(store):
    steam, controller = joiner(store=store)

    view = controller.view()
    assert view.screen is Screen.NETWORK
    assert view.presence is Presence.CONNECTING
    assert view.network_status == "Connecting to the network..."
    assert view.tray_status == "Connecting..."
    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert controller.network.access_code == CODE
    # Nothing is saved until the network admitted this member.
    assert store.load(GUEST) is None

    admitted(steam, controller)

    view = controller.view()
    assert view.presence is Presence.ONLINE
    assert view.network_status == "Online"
    assert view.online_summary == "2 of 2 online"
    assert view.can_invite
    saved = store.load(GUEST)
    assert (saved.lobby_id, saved.network_id, saved.access_code) == (LOBBY, NETWORK_ID, CODE)
    assert dict(saved.members) == {HOST: A1, GUEST: A2}


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
    steam, controller = joiner(response=response)

    view = controller.view()
    assert (view.screen, view.error, view.busy) == (Screen.JOIN, error, "")
    assert controller.helpers == []
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {access.LOBBY_MARKER_KEY: "1", access.NETWORK_ID_KEY: NETWORK_ID},
        {access.LOBBY_MARKER_KEY: access.LOBBY_MARKER_VALUE},
    ],
    ids=["other app", "older version", "no network ID"],
)
def test_join_rejects_lobbies_that_are_not_networks(metadata):
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam)
    controller.show_join()
    controller.join(str(LOBBY), CODE_TEXT)
    steam.lobby_metadata = metadata
    steam.frames = [[lobby_entered()]]

    controller.tick()

    assert controller.view().error == "That lobby is not a SteamVirtualLAN network"
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert controller.network is None


def test_wrong_access_code_returns_to_join_form(store):
    steam, controller = joiner(store=store)
    steam.frames = [
        [status(7, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(7, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    controller.tick()
    controller.tick()
    steam.inbox[7] = [access.DENIED]

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (Screen.JOIN, "Incorrect access code")
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]
    assert store.load(GUEST) is None


def test_going_offline_while_joining_a_new_network_goes_home():
    steam, controller = joiner()

    controller.go_offline()

    assert controller.view().screen is Screen.HOME
    assert controller.saved is None
    assert steam.called("leave_lobby") == [(LOBBY,)]


# Invites


def test_open_invite_uses_steam_overlay_with_lobby_and_code():
    steam, controller = creator()
    code = access.normalize_access_code(controller.view().access_code)

    assert controller.open_invite() == "Pick friends to invite in the Steam overlay"
    assert steam.called("open_invite_dialog") == [(f"steamvirtuallan:1:{LOBBY}:{code}",)]


def test_any_member_can_invite():
    steam, controller = joiner()
    admitted(steam, controller)

    controller.open_invite()

    assert steam.called("open_invite_dialog") == [(f"steamvirtuallan:1:{LOBBY}:{CODE}",)]


def test_no_invites_before_being_admitted_or_while_offline(store):
    steam, controller = joiner()
    with pytest.raises(ValueError, match="Go online"):
        controller.open_invite()

    store.save(HOST, saved_network(online=False))
    controller = started(FakeSteam(HOST), store=store)
    with pytest.raises(ValueError, match="Go online"):
        controller.open_invite()
    assert not controller.view().can_invite


def test_open_invite_without_overlay():
    steam, controller = creator()
    steam.overlay_enabled = False

    with pytest.raises(ValueError, match="overlay isn't available"):
        controller.open_invite()
    assert steam.called("open_invite_dialog") == []


def test_open_invite_steam_failure():
    steam, controller = creator()
    steam.fail.add("open_invite_dialog")

    with pytest.raises(ValueError, match="could not open the invite dialog"):
        controller.open_invite()


def test_open_invite_without_steam():
    def open_steam():
        raise SteamInitError("Steam is not running")

    controller = AppController(open_steam, Clock())
    controller.start()

    with pytest.raises(ValueError, match="Steam is not running"):
        controller.open_invite()


def test_accepted_overlay_invite_joins_with_its_code():
    steam = FakeSteam(GUEST, [HOST, GUEST])
    network_lobby(steam)
    controller = started(steam)
    connect = access.invite_connect_string(LOBBY, CODE)
    steam.frames = [[invite_accepted(connect)], [lobby_entered()]]

    controller.tick()
    assert controller.view().busy == "Joining..."
    assert steam.called("request_join_lobby") == [(LOBBY,)]
    controller.tick()

    assert controller.view().screen is Screen.NETWORK
    assert controller.network.access_code == CODE


def test_foreign_connect_string_is_ignored():
    steam = FakeSteam(GUEST)
    controller = started(steam)
    steam.frames = [[invite_accepted("+connect 10.0.0.1:27015")]]

    controller.tick()

    assert controller.view().busy == ""
    assert steam.called("request_join_lobby") == []


def test_plain_lobby_invite_still_joins_without_code():
    steam = FakeSteam(GUEST, [HOST, GUEST])
    network_lobby(steam)
    controller = started(steam)
    steam.frames = [[join_requested()], [lobby_entered()]]

    controller.tick()
    controller.tick()

    assert controller.view().screen is Screen.NETWORK
    assert controller.network.access_code == ""


def test_invite_to_another_network_while_in_one(store):
    store.save(GUEST, saved_network(online=False))
    steam = FakeSteam(GUEST)
    controller = started(steam, store=store)
    steam.frames = [[invite_accepted(access.invite_connect_string(OTHER_LOBBY, CODE))]]

    controller.tick()

    assert steam.called("request_join_lobby") == []
    assert controller.error == "You are already in a network. Leave it to join another one."


def test_invite_to_own_network_while_offline_goes_online(store):
    store.save(GUEST, saved_network(online=False))
    steam = FakeSteam(GUEST)
    controller = started(steam, store=store)
    steam.frames = [[join_requested(LOBBY)]]

    controller.tick()

    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert controller.presence() is Presence.CONNECTING


def test_overlay_needs_present():
    steam = FakeSteam(HOST)
    controller = started(steam)
    assert controller.overlay_needs_present() is False

    steam.needs_present = True
    assert controller.overlay_needs_present() is True

    controller.shutdown()
    assert controller.overlay_needs_present() is False


def test_steam_errors_during_tick_do_not_crash():
    steam, controller = creator()
    steam.frames = [[SteamCallback(1221, b"short")]]

    controller.tick()

    assert controller.view().screen is Screen.NETWORK


# The adapter and packets


def order(steam):
    """The cleanup steps in the order they happened."""
    steps = ("helper_close", "close_connection", "leave_lobby", "close_listen_socket", "close")
    return [call[0] for call in steam.calls if call[0] in steps]


def creator_with_member():
    """A creator whose adapter is ready and who admitted GUEST."""
    steam, controller = creator(FakeSteam(HOST, [HOST, GUEST]))
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
    steam, controller = creator()

    (helper,) = controller.helpers
    assert controller.helper is helper
    assert helper.state is helper.State.STARTING
    view = controller.view()
    assert (view.adapter_status, view.adapter_tone) == (
        "Waiting for Administrator permission...",
        "pending",
    )


def test_creator_adapter_gets_the_first_address():
    steam, controller = creator()
    controller.helper.become_ready()

    controller.tick()

    assert controller.helper.requested == [A1]
    view = controller.view()
    assert (view.adapter_status, view.adapter_tone) == ("Virtual network ready: 10.77.0.1", "ok")
    assert view.tray_status == "Online · 10.77.0.1"


def test_member_adapter_waits_for_its_address():
    steam, controller = joiner()
    controller.helper.become_ready()
    controller.tick()

    assert controller.helper.requested == []
    assert controller.view().adapter_status == "Waiting for an address from the network"

    admitted(steam, controller)
    controller.tick()

    assert controller.helper.requested == [A2]
    assert controller.view().adapter_status == "Virtual network ready: 10.77.0.2"


def test_packets_from_windows_go_to_the_member_that_owns_the_address():
    steam, controller, connection = creator_with_member()
    packet = ipv4_packet(A1, A2)
    controller.helper.from_windows = [packet, ipv4_packet(A1, "10.77.0.9")]

    controller.tick()

    assert steam.unreliable == [(connection, tunnel.packet_message(packet))]


def test_packets_from_members_go_to_windows():
    steam, controller, connection = creator_with_member()
    packet = ipv4_packet(A2, A1, b"reply")
    steam.inbox[connection] = [tunnel.packet_message(packet)]

    controller.tick()

    assert controller.helper.to_windows == [packet]


def test_packets_before_the_adapter_is_ready_are_dropped():
    steam, controller, connection = creator_with_member()
    controller.helper.state = controller.helper.State.STARTING
    steam.inbox[connection] = [tunnel.packet_message(ipv4_packet(A2, A1))]
    controller.tick()

    controller.helper.become_ready()
    controller.tick()

    assert controller.helper.to_windows == []


def test_exit_removes_the_adapter_then_cleans_up_steam_and_stays_a_member(store):
    steam, controller, connection = creator_with_member()
    controller.store = store

    controller.shutdown()
    controller.shutdown()

    assert order(steam) == [
        "helper_close",
        "close_connection",
        "leave_lobby",
        "close_listen_socket",
        "close",
    ]
    saved = store.load(HOST)
    assert saved.online
    assert dict(saved.members) == {HOST: A1, GUEST: A2}


def test_go_offline_removes_the_adapter_and_keeps_the_membership(store):
    steam, controller = creator(store=store)
    helper = controller.helper

    controller.go_offline()

    assert helper.state is helper.State.STOPPED
    assert order(steam) == ["helper_close", "leave_lobby", "close_listen_socket"]
    view = controller.view()
    assert (view.screen, view.presence, view.network_status) == (
        Screen.NETWORK,
        Presence.OFFLINE,
        "Offline",
    )
    assert view.tray_status == "Offline"
    assert [(m.status, m.address, m.is_you) for m in view.members] == [
        ("Offline", "10.77.0.1", True)
    ]
    assert steam.running
    assert store.load(HOST).online is False


def test_leave_network_removes_the_adapter_before_leaving_steam(store):
    steam, controller, connection = creator_with_member()
    controller.store = store
    helper = controller.helper

    controller.leave_network()

    assert helper.state is helper.State.STOPPED
    assert controller.helper is None
    assert order(steam) == [
        "helper_close",
        "close_connection",
        "leave_lobby",
        "close_listen_socket",
    ]
    # The coordinator took itself out of the roster first, and told the others.
    assert roster.decode(steam.lobby_metadata[roster.ROSTER_KEY]) == {GUEST: A2}
    assert (connection, access.LEAVE) in steam.sent
    assert steam.closed[-1] == (connection, True)
    assert controller.view().screen is Screen.HOME
    assert controller.saved is None and store.load(HOST) is None


def test_leave_network_gives_up_telling_the_network_after_a_while(store):
    clock = Clock()
    steam, controller = joiner(store=store, clock=clock)
    admitted(steam, controller)
    steam.frames = [[status(7, ConnectionState.CLOSED_BY_PEER, HOST, LISTEN_SOCKET)]]
    controller.tick()

    controller.leave_network()
    assert controller.view().busy == "Leaving the network..."
    assert controller.view().presence is Presence.NONE
    controller.tick()
    assert steam.called("leave_lobby") == []

    clock.now = FAREWELL_TIMEOUT + 1
    controller.tick()

    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert controller.view().busy == ""
    assert store.load(GUEST) is None


def test_cleanup_continues_after_a_failure():
    steam, controller = creator()
    steam.fail.add("leave_lobby")

    controller.go_offline()

    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_declined_uac_prompt_goes_offline_but_stays_a_member(store):
    steam, controller = creator(store=store)
    controller.helper.fail(UAC_DECLINED)

    controller.tick()

    view = controller.view()
    assert (view.screen, view.presence, view.error) == (
        Screen.NETWORK,
        Presence.OFFLINE,
        UAC_DECLINED,
    )
    assert controller.network is None and controller.helper is None
    assert order(steam) == ["helper_close", "leave_lobby", "close_listen_socket"]
    assert store.load(HOST).online is False


def test_adapter_failure_before_being_admitted_returns_to_the_join_form():
    steam, controller = joiner()
    controller.helper.become_ready()
    controller.helper.fail("The virtual network helper stopped unexpectedly")

    controller.tick()

    view = controller.view()
    assert (view.screen, view.error) == (
        Screen.JOIN,
        "The virtual network helper stopped unexpectedly",
    )
    assert steam.called("leave_lobby") == [(LOBBY,)]


def test_helper_that_cannot_start_leaves_a_new_network():
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


# Coming back to the saved network


def test_start_goes_back_online_in_the_saved_network(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST, [HOST, GUEST])

    controller = started(steam, store=store)

    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert steam.called("create_listen_socket") == [(0,)]
    view = controller.view()
    assert (view.screen, view.presence) == (Screen.NETWORK, Presence.CONNECTING)
    assert view.busy == ""

    network_lobby(steam, {HOST: A1, GUEST: A2})
    steam.frames = [[lobby_entered()]]
    controller.tick()

    view = controller.view()
    assert view.presence is Presence.ONLINE
    assert controller.network.local_address == A2
    assert controller.helper is not None
    # No access code check needed: the network still has this member.
    assert steam.sent == []


def test_start_stays_offline_after_go_offline(store):
    store.save(GUEST, saved_network(online=False))
    steam = FakeSteam(GUEST)

    controller = started(steam, store=store)

    assert steam.called("request_join_lobby") == []
    view = controller.view()
    assert (view.screen, view.presence) == (Screen.NETWORK, Presence.OFFLINE)
    assert view.lobby_id == LOBBY
    assert view.access_code == CODE_TEXT
    assert [(m.name, m.status, m.address) for m in view.members] == [
        ("Me", "Offline", "10.77.0.2"),
        ("Host", "Offline", "10.77.0.1"),
    ]

    controller.go_online()

    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert store.load(GUEST).online


def test_saved_network_of_another_steam_account_is_not_used(store):
    store.save(HOST, saved_network(members={HOST: A1}))

    controller = started(FakeSteam(GUEST), store=store)

    assert controller.presence() is Presence.NONE
    assert controller.view().screen is Screen.HOME


def test_saved_network_that_no_longer_exists_is_forgotten(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST)
    controller = started(steam, store=store)

    steam.frames = [[lobby_entered(ChatRoomEnterResponse.DOESNT_EXIST)]]
    controller.tick()

    view = controller.view()
    assert (view.screen, view.presence, view.error) == (Screen.HOME, Presence.NONE, NETWORK_GONE)
    assert store.load(GUEST) is None
    assert steam.called("close_listen_socket") == [(LISTEN_SOCKET,)]


def test_saved_network_whose_lobby_is_now_something_else_is_forgotten(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam, store=store)
    network_lobby(steam)
    steam.lobby_metadata[access.NETWORK_ID_KEY] = "f" * 16

    steam.frames = [[lobby_entered()]]
    controller.tick()

    assert controller.error == NETWORK_GONE
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert store.load(GUEST) is None


@pytest.mark.parametrize("response", [ChatRoomEnterResponse.ERROR, ChatRoomEnterResponse.LIMITED])
def test_rejoin_failure_keeps_the_membership_offline(store, response):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST)
    controller = started(steam, store=store)

    steam.frames = [[lobby_entered(response)]]
    controller.tick()

    view = controller.view()
    assert (view.screen, view.presence) == (Screen.NETWORK, Presence.OFFLINE)
    assert view.error.startswith("Could not go online")
    assert store.load(GUEST) == saved_network(online=False)


def test_rejoin_timeout_keeps_the_membership_offline(store):
    store.save(GUEST, saved_network())
    clock = Clock()
    steam = FakeSteam(GUEST)
    controller = started(steam, clock, store)

    clock.now = CALL_TIMEOUT + 1
    controller.tick()

    assert controller.presence() is Presence.OFFLINE
    assert controller.error == "Could not go online: Steam did not answer in time"


def test_network_that_no_longer_knows_this_member_is_forgotten(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam, store=store)
    network_lobby(steam, {HOST: A1})
    steam.frames = [[lobby_entered()]]
    controller.tick()
    assert steam.sent == []

    steam.frames = [
        [status(7, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(7, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    controller.tick()
    controller.tick()
    assert steam.sent == [(7, access.auth_message(CODE, A2))]
    steam.inbox[7] = [access.DENIED]
    controller.tick()

    assert controller.presence() is Presence.NONE
    assert controller.error == "You are no longer a member of that network"
    assert store.load(GUEST) is None


def test_going_offline_while_reconnecting_leaves_the_lobby_if_steam_answers_late(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam, store=store)

    controller.go_offline()
    assert controller.presence() is Presence.OFFLINE
    network_lobby(steam, {HOST: A1, GUEST: A2})
    steam.frames = [[lobby_entered(api_call=JOIN_CALL)]]
    controller.tick()

    assert controller.network is None
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert store.load(GUEST).online is False


def test_leave_network_while_offline_goes_in_only_to_say_so(store):
    store.save(GUEST, saved_network(online=False))
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam, store=store)

    controller.leave_network()

    assert store.load(GUEST) is None
    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert controller.view().busy == "Leaving the network..."
    assert controller.view().screen is Screen.HOME

    network_lobby(steam, {HOST: A1, GUEST: A2})
    steam.frames = [
        [lobby_entered()],
        [status(7, ConnectionState.CONNECTING, HOST, LISTEN_SOCKET)],
        [status(7, ConnectionState.CONNECTED, HOST, LISTEN_SOCKET)],
    ]
    for _ in range(3):
        controller.tick()

    assert controller.helpers == []
    assert (7, access.LEAVE) in steam.sent
    assert steam.called("leave_lobby") == [(LOBBY,)]
    assert controller.view().busy == ""
    assert controller.presence() is Presence.NONE


def test_leave_network_while_offline_when_the_network_is_gone(store):
    store.save(GUEST, saved_network(online=False))
    steam = FakeSteam(GUEST)
    controller = started(steam, store=store)
    controller.leave_network()

    steam.frames = [[lobby_entered(ChatRoomEnterResponse.DOESNT_EXIST)]]
    controller.tick()

    assert controller.view().busy == ""
    assert controller.error == ""
    assert controller.presence() is Presence.NONE


def test_leave_network_while_reconnecting_says_so_once_in(store):
    store.save(GUEST, saved_network())
    steam = FakeSteam(GUEST, [HOST, GUEST])
    controller = started(steam, store=store)

    controller.leave_network()
    controller.leave_network()
    network_lobby(steam, {HOST: A1, GUEST: A2})
    steam.frames = [[lobby_entered()]]
    controller.tick()

    assert steam.called("request_join_lobby") == [(LOBBY,)]
    assert controller.helpers == []
    assert controller.view().busy == "Leaving the network..."
    assert store.load(GUEST) is None


def test_network_state_follows_the_roster(store):
    steam, controller = creator(FakeSteam(HOST, [HOST, GUEST]), store=store)
    controller.tick()
    (connection,) = [call[1] for call in steam.called("connect_p2p") if call[0] == GUEST]
    steam.frames = [[status(connection, ConnectionState.CONNECTED, GUEST)]]
    controller.tick()
    steam.inbox[connection] = [access.auth_message(controller.network.access_code, A2)]

    controller.tick()

    assert dict(store.load(HOST).members) == {HOST: A1, GUEST: A2}
