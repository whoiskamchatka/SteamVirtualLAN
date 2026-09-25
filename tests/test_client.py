import ctypes
from unittest import mock

import pytest

from steamlan.steam import (
    LobbyType,
    SteamAPILoadError,
    SteamAPINotFoundError,
    SteamCallback,
    SteamClient,
    SteamError,
    SteamInitError,
)
from steamlan.steam.native import (
    LOBBY_CREATED,
    STEAM_API_CALL_COMPLETED,
    EResult,
    LobbyCreated,
    SteamAPICallCompleted,
    SteamAPIInitResult,
    SteamErrMsg,
    SteamNetworkingIdentity,
)

DLL_PATH = r"C:\steam\steam_api64.dll"
USER = 0x1000
FRIENDS = 0x2000
MATCHMAKING = 0x3000
SOCKETS = 0x4000
STEAM_ID = 76561197960265729
OTHER_STEAM_ID = 76561197960265730
LOBBY_ID = 109775240917097000
PIPE = 7


def init_result(result, message=b""):
    def init(err):
        err.value = message
        return result

    return init


def fill_identity(steam_id, identity_type=16, known=True):
    def get_identity(sockets, identity):
        identity.m_eType = identity_type
        identity.m_cbSize = 8
        identity.m_data[:8] = steam_id.to_bytes(8, "little")
        return known

    return get_identity


@pytest.fixture
def lib():
    lib = mock.Mock()
    lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.OK)
    lib.SteamAPI_SteamUser_v023.return_value = USER
    lib.SteamAPI_ISteamUser_GetSteamID.return_value = STEAM_ID
    lib.SteamAPI_SteamFriends_v018.return_value = FRIENDS
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = b"Test User"
    lib.SteamAPI_GetHSteamPipe.return_value = PIPE
    lib.SteamAPI_ManualDispatch_GetNextCallback.return_value = False
    lib.SteamAPI_SteamMatchmaking_v009.return_value = MATCHMAKING
    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.return_value = SOCKETS
    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.side_effect = fill_identity(STEAM_ID)
    return lib


@pytest.fixture
def load(lib):
    with mock.patch("steamlan.steam.client.load_steam_api", return_value=lib) as load:
        yield load


@pytest.fixture
def failing_lib(lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(
        SteamAPIInitResult.NO_STEAM_CLIENT, b"Steam is not running."
    )
    return lib


def test_construction_does_not_load(load):
    steam = SteamClient(DLL_PATH)

    assert not steam.running
    load.assert_not_called()


def test_start(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()

    load.assert_called_once_with(DLL_PATH)
    lib.SteamAPI_InitFlat.assert_called_once()
    (err,) = lib.SteamAPI_InitFlat.call_args.args
    assert isinstance(err, SteamErrMsg)
    lib.SteamAPI_ManualDispatch_Init.assert_called_once_with()
    assert steam.running


def test_start_twice_initializes_once(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.start()

    lib.SteamAPI_InitFlat.assert_called_once()


def test_init_failure(load, failing_lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="Steam is not running."):
        steam.start()

    assert not steam.running
    failing_lib.SteamAPI_ManualDispatch_Init.assert_not_called()


def test_init_failure_without_message(load, lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.VERSION_MISMATCH)
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="initialization failed: VERSION_MISMATCH"):
        steam.start()

    assert not steam.running


def test_init_failure_with_unknown_result(load, lib):
    lib.SteamAPI_InitFlat.side_effect = init_result(99)
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamInitError, match="result 99"):
        steam.start()


def test_close_after_init_failure_does_not_shut_down(load, failing_lib):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    steam.close()

    failing_lib.SteamAPI_Shutdown.assert_not_called()


def test_start_after_init_failure(load, failing_lib):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    failing_lib.SteamAPI_InitFlat.side_effect = init_result(SteamAPIInitResult.OK)
    steam.start()

    assert steam.running


def test_loader_error_propagates(load, lib):
    error = SteamAPINotFoundError("steam_api64.dll not found")
    load.side_effect = error
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamAPINotFoundError) as excinfo:
        steam.start()

    assert excinfo.value is error
    assert not steam.running
    lib.SteamAPI_InitFlat.assert_not_called()


def test_missing_export_is_a_load_error(load):
    load.return_value = mock.Mock(spec=[])
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamAPILoadError):
        steam.start()

    assert not steam.running


def test_close(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_close_twice(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()
    steam.close()

    lib.SteamAPI_Shutdown.assert_called_once_with()


def test_close_before_start(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.close()

    load.assert_not_called()
    lib.SteamAPI_Shutdown.assert_not_called()
    assert not steam.running


def test_context_manager(load, lib):
    with SteamClient(DLL_PATH) as steam:
        assert steam.running
        lib.SteamAPI_Shutdown.assert_not_called()

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_context_manager_shuts_down_on_error(load, lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(ValueError), steam:
        raise ValueError

    lib.SteamAPI_Shutdown.assert_called_once_with()
    assert not steam.running


def test_context_manager_init_failure(load, failing_lib):
    with pytest.raises(SteamInitError), SteamClient(DLL_PATH):
        pytest.fail("body should not run")

    failing_lib.SteamAPI_Shutdown.assert_not_called()


@pytest.fixture
def steam(load):
    with SteamClient(DLL_PATH) as steam:
        yield steam


def test_steam_id(steam, lib):
    assert steam.steam_id == STEAM_ID
    assert type(steam.steam_id) is int
    lib.SteamAPI_ISteamUser_GetSteamID.assert_called_with(USER)


def test_steam_id_without_user_interface(steam, lib):
    lib.SteamAPI_SteamUser_v023.return_value = None

    with pytest.raises(SteamError, match="ISteamUser"):
        _ = steam.steam_id


def test_empty_steam_id(steam, lib):
    lib.SteamAPI_ISteamUser_GetSteamID.return_value = 0

    with pytest.raises(SteamError, match="empty SteamID"):
        _ = steam.steam_id


def test_persona_name(steam, lib):
    assert steam.persona_name == "Test User"
    lib.SteamAPI_ISteamFriends_GetPersonaName.assert_called_with(FRIENDS)


def test_persona_name_is_utf8(steam, lib):
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = "Jürgen 猫".encode()

    assert steam.persona_name == "Jürgen 猫"


def test_persona_name_is_not_cached(steam, lib):
    assert steam.persona_name == "Test User"
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = b"Renamed"

    assert steam.persona_name == "Renamed"


def test_persona_name_without_friends_interface(steam, lib):
    lib.SteamAPI_SteamFriends_v018.return_value = None

    with pytest.raises(SteamError, match="ISteamFriends"):
        _ = steam.persona_name


def test_null_persona_name(steam, lib):
    lib.SteamAPI_ISteamFriends_GetPersonaName.return_value = None

    with pytest.raises(SteamError, match="no persona name"):
        _ = steam.persona_name


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_before_start(load, attr):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)

    load.assert_not_called()
    assert not steam.running


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_after_close(load, lib, attr):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)

    lib.SteamAPI_Shutdown.assert_called_once_with()


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_after_init_failure(load, failing_lib, attr):
    steam = SteamClient(DLL_PATH)
    with pytest.raises(SteamInitError):
        steam.start()

    with pytest.raises(SteamError, match="not running"):
        getattr(steam, attr)


class FakeCallbackQueue:
    """Hands out callbacks like Steam and wipes their memory on FreeLastCallback."""

    def __init__(self, lib, messages):
        self.messages = list(messages)
        self.current = None
        self.freed = 0
        lib.SteamAPI_ManualDispatch_GetNextCallback.side_effect = self.get_next
        lib.SteamAPI_ManualDispatch_FreeLastCallback.side_effect = self.free

    def get_next(self, pipe, msg):
        assert pipe == PIPE
        assert self.current is None, "previous callback was not freed"
        if not self.messages:
            return False

        callback_id, payload, size = self.messages.pop(0)
        self.current = ctypes.create_string_buffer(payload, max(len(payload), 1))
        msg.m_hSteamUser = 1
        msg.m_iCallback = callback_id
        msg.m_pubParam = ctypes.cast(self.current, ctypes.POINTER(ctypes.c_uint8))
        msg.m_cubParam = len(payload) if size is None else size
        return True

    def free(self, pipe):
        assert pipe == PIPE
        assert self.current is not None, "freed without a current callback"
        ctypes.memset(self.current, 0xDD, len(self.current))
        self.current = None
        self.freed += 1


def callback(callback_id, payload, size=None):
    return (callback_id, payload, size)


def call_completed(api_call, result_id, result_size):
    return callback(
        STEAM_API_CALL_COMPLETED,
        bytes(SteamAPICallCompleted(api_call, result_id, result_size)),
    )


@pytest.mark.parametrize("attr", ["steam_id", "persona_name"])
def test_identity_still_works_with_callbacks(steam, attr):
    steam.run_callbacks()

    assert getattr(steam, attr)


def test_run_callbacks_before_start(load, lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamError, match="not running"):
        steam.run_callbacks()

    load.assert_not_called()
    lib.SteamAPI_ManualDispatch_RunFrame.assert_not_called()


def test_run_callbacks_after_close(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        steam.run_callbacks()

    lib.SteamAPI_ManualDispatch_RunFrame.assert_not_called()


def test_run_callbacks_without_pending_callbacks(steam, lib):
    queue = FakeCallbackQueue(lib, [])

    assert steam.run_callbacks() == []

    lib.SteamAPI_ManualDispatch_RunFrame.assert_called_once_with(PIPE)
    assert queue.freed == 0
    assert steam.running


def test_run_callbacks_runs_frame_before_reading(steam, lib):
    FakeCallbackQueue(lib, [])
    steam.run_callbacks()

    names = [name for name, _, _ in lib.mock_calls]
    assert names.index("SteamAPI_ManualDispatch_RunFrame") < names.index(
        "SteamAPI_ManualDispatch_GetNextCallback"
    )


def test_one_callback(steam, lib):
    queue = FakeCallbackQueue(lib, [callback(304, b"\x01\x02\x03")])

    assert steam.run_callbacks() == [SteamCallback(304, b"\x01\x02\x03")]
    assert queue.freed == 1


def test_multiple_callbacks(steam, lib):
    queue = FakeCallbackQueue(
        lib, [callback(304, b"first"), callback(331, b""), callback(304, b"third")]
    )

    callbacks = steam.run_callbacks()

    assert callbacks == [
        SteamCallback(304, b"first"),
        SteamCallback(331, b""),
        SteamCallback(304, b"third"),
    ]
    assert queue.freed == 3


def test_payload_survives_free(steam, lib):
    FakeCallbackQueue(lib, [callback(304, b"payload")])

    (cb,) = steam.run_callbacks()

    assert type(cb.payload) is bytes
    assert cb.payload == b"payload"


def test_callbacks_are_not_returned_twice(steam, lib):
    FakeCallbackQueue(lib, [callback(304, b"x")])

    assert len(steam.run_callbacks()) == 1
    assert steam.run_callbacks() == []


@pytest.mark.parametrize(
    "message",
    [
        callback(304, b"abc", size=-1),
        callback(STEAM_API_CALL_COMPLETED, b"\x00" * 8),
    ],
)
def test_malformed_callback_is_freed_and_raised(steam, lib, message):
    queue = FakeCallbackQueue(lib, [message])

    with pytest.raises(SteamError):
        steam.run_callbacks()

    assert queue.freed == 1
    assert queue.current is None


def test_null_payload_pointer(steam, lib):
    queue = FakeCallbackQueue(lib, [callback(304, b"abc")])
    get_next = queue.get_next

    def null_payload(pipe, msg):
        found = get_next(pipe, msg)
        msg.m_pubParam = None
        return found

    lib.SteamAPI_ManualDispatch_GetNextCallback.side_effect = null_payload

    with pytest.raises(SteamError, match="malformed callback"):
        steam.run_callbacks()

    assert queue.freed == 1


def test_call_result(steam, lib):
    queue = FakeCallbackQueue(lib, [call_completed(0xABCDEF, 1234, 8)])

    def get_result(pipe, api_call, buffer, size, callback_id, io_failed):
        assert queue.current is not None, "call result read after FreeLastCallback"
        assert (pipe, api_call, size, callback_id) == (PIPE, 0xABCDEF, 8, 1234)
        ctypes.memmove(buffer, b"12345678", size)
        io_failed.value = False
        return True

    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = get_result

    assert steam.run_callbacks() == [SteamCallback(1234, b"12345678", api_call=0xABCDEF)]
    assert queue.freed == 1


def test_call_result_io_failure(steam, lib):
    FakeCallbackQueue(lib, [call_completed(0xABCDEF, 1234, 8)])

    def get_result(pipe, api_call, buffer, size, callback_id, io_failed):
        io_failed.value = True
        return True

    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = get_result

    (cb,) = steam.run_callbacks()

    assert cb.api_call == 0xABCDEF
    assert cb.failed


def test_call_result_unavailable(steam, lib):
    FakeCallbackQueue(lib, [call_completed(0xABCDEF, 1234, 8)])
    lib.SteamAPI_ManualDispatch_GetAPICallResult.return_value = False

    assert steam.run_callbacks() == [SteamCallback(1234, b"", api_call=0xABCDEF, failed=True)]


def test_callback_is_freed_when_processing_raises(steam, lib):
    queue = FakeCallbackQueue(lib, [call_completed(0xABCDEF, 1234, 8), callback(304, b"")])
    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        steam.run_callbacks()

    assert queue.freed == 1
    assert queue.current is None

    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = None
    assert steam.run_callbacks() == [SteamCallback(304, b"")]


CREATE_CALL = 0x5000
OTHER_CALL = 0x6000


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept += seconds


@pytest.fixture
def clock():
    clock = FakeClock()
    with mock.patch("steamlan.steam.client.time", clock):
        yield clock


@pytest.fixture
def create_call(lib):
    lib.SteamAPI_ISteamMatchmaking_CreateLobby.return_value = CREATE_CALL
    return CREATE_CALL


def lobby_created(result=EResult.OK, lobby_id=LOBBY_ID):
    return bytes(LobbyCreated(result, lobby_id))


def serve_call_results(lib, results):
    def get_result(pipe, api_call, buffer, size, callback_id, io_failed):
        payload = results[api_call]
        ctypes.memmove(buffer, payload, min(size, len(payload)))
        io_failed.value = False
        return True

    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = get_result


def lobby_created_result(api_call=CREATE_CALL, size=None):
    return call_completed(api_call, LOBBY_CREATED, 16 if size is None else size)


def test_create_lobby(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [lobby_created_result()])
    serve_call_results(lib, {CREATE_CALL: lobby_created()})

    lobby_id = steam.create_lobby(4)

    assert lobby_id == LOBBY_ID
    assert type(lobby_id) is int
    lib.SteamAPI_ISteamMatchmaking_CreateLobby.assert_called_once_with(
        MATCHMAKING, LobbyType.FRIENDS_ONLY, 4
    )
    assert clock.slept == 0


def test_create_lobby_type(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [lobby_created_result()])
    serve_call_results(lib, {CREATE_CALL: lobby_created()})

    steam.create_lobby(2, LobbyType.PRIVATE)

    lib.SteamAPI_ISteamMatchmaking_CreateLobby.assert_called_once_with(
        MATCHMAKING, LobbyType.PRIVATE, 2
    )


def test_create_lobby_waits_for_result(steam, lib, clock, create_call):
    queue = FakeCallbackQueue(lib, [])
    serve_call_results(lib, {CREATE_CALL: lobby_created()})
    frames = []

    def run_frame(pipe):
        frames.append(pipe)
        if len(frames) == 3:
            queue.messages.append(lobby_created_result())

    lib.SteamAPI_ManualDispatch_RunFrame.side_effect = run_frame

    assert steam.create_lobby(4) == LOBBY_ID
    assert len(frames) == 3
    assert 0 < clock.slept < 1


def test_create_lobby_keeps_other_callbacks(steam, lib, clock, create_call):
    other_result = lobby_created(lobby_id=111)
    FakeCallbackQueue(
        lib,
        [
            lobby_created_result(OTHER_CALL),
            callback(LOBBY_CREATED, lobby_created(lobby_id=222)),
            callback(304, b"before"),
            lobby_created_result(),
            callback(504, b"after"),
        ],
    )
    serve_call_results(lib, {CREATE_CALL: lobby_created(), OTHER_CALL: other_result})

    assert steam.create_lobby(4) == LOBBY_ID

    assert steam.run_callbacks() == [
        SteamCallback(LOBBY_CREATED, other_result, api_call=OTHER_CALL),
        SteamCallback(LOBBY_CREATED, lobby_created(lobby_id=222)),
        SteamCallback(304, b"before"),
        SteamCallback(504, b"after"),
    ]
    assert steam.run_callbacks() == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (EResult.LIMIT_EXCEEDED, "LIMIT_EXCEEDED"),
        (EResult.NO_CONNECTION, "NO_CONNECTION"),
        (999, "result 999"),
    ],
)
def test_create_lobby_failed(steam, lib, clock, create_call, result, message):
    FakeCallbackQueue(lib, [lobby_created_result()])
    serve_call_results(lib, {CREATE_CALL: lobby_created(result, lobby_id=0)})

    with pytest.raises(SteamError, match=f"CreateLobby failed: {message}"):
        steam.create_lobby(4)


def test_create_lobby_without_lobby_id(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [lobby_created_result()])
    serve_call_results(lib, {CREATE_CALL: lobby_created(lobby_id=0)})

    with pytest.raises(SteamError, match="no lobby ID"):
        steam.create_lobby(4)


def test_create_lobby_truncated_result(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [lobby_created_result(size=8)])
    serve_call_results(lib, {CREATE_CALL: lobby_created()[:8]})

    with pytest.raises(SteamError, match="8 bytes"):
        steam.create_lobby(4)


def test_create_lobby_unexpected_result_type(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [call_completed(CREATE_CALL, 512, 16)])
    serve_call_results(lib, {CREATE_CALL: lobby_created()})

    with pytest.raises(SteamError, match="unexpected callback 512"):
        steam.create_lobby(4)


def test_create_lobby_io_failure(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [lobby_created_result()])

    def get_result(pipe, api_call, buffer, size, callback_id, io_failed):
        io_failed.value = True
        return True

    lib.SteamAPI_ManualDispatch_GetAPICallResult.side_effect = get_result

    with pytest.raises(SteamError, match="could not deliver"):
        steam.create_lobby(4)


def test_create_lobby_invalid_call(steam, lib, clock):
    lib.SteamAPI_ISteamMatchmaking_CreateLobby.return_value = 0

    with pytest.raises(SteamError, match="could not be started"):
        steam.create_lobby(4)

    lib.SteamAPI_ManualDispatch_RunFrame.assert_not_called()


def test_create_lobby_timeout(steam, lib, clock, create_call):
    FakeCallbackQueue(lib, [callback(304, b"x")])

    with pytest.raises(SteamError, match="timed out after 5s"):
        steam.create_lobby(4, timeout=5)

    assert 5 <= clock.slept < 5.1
    assert steam.run_callbacks() == [SteamCallback(304, b"x")]


def test_close_drops_pending_callbacks(load, lib, clock, create_call):
    FakeCallbackQueue(lib, [callback(304, b"x")])
    steam = SteamClient(DLL_PATH)
    steam.start()
    with pytest.raises(SteamError, match="timed out"):
        steam.create_lobby(4, timeout=1)
    steam.close()

    steam.start()

    assert steam.run_callbacks() == []


LOBBY_OPERATIONS = {
    "create_lobby": lambda steam: steam.create_lobby(4),
    "leave_lobby": lambda steam: steam.leave_lobby(LOBBY_ID),
    "invite_to_lobby": lambda steam: steam.invite_to_lobby(LOBBY_ID, OTHER_STEAM_ID),
    "lobby_member_count": lambda steam: steam.lobby_member_count(LOBBY_ID),
    "lobby_members": lambda steam: steam.lobby_members(LOBBY_ID),
}


@pytest.mark.parametrize("operation", LOBBY_OPERATIONS.values(), ids=LOBBY_OPERATIONS)
def test_lobby_operation_before_start(load, lib, operation):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamError, match="not running"):
        operation(steam)

    load.assert_not_called()
    lib.SteamAPI_SteamMatchmaking_v009.assert_not_called()


@pytest.mark.parametrize("operation", LOBBY_OPERATIONS.values(), ids=LOBBY_OPERATIONS)
def test_lobby_operation_after_close(load, lib, operation):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        operation(steam)

    lib.SteamAPI_SteamMatchmaking_v009.assert_not_called()
    lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.assert_not_called()


@pytest.mark.parametrize("operation", LOBBY_OPERATIONS.values(), ids=LOBBY_OPERATIONS)
def test_lobby_operation_without_matchmaking_interface(steam, lib, operation):
    lib.SteamAPI_SteamMatchmaking_v009.return_value = None

    with pytest.raises(SteamError, match="ISteamMatchmaking"):
        operation(steam)


def test_lobby_member_count(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.return_value = 2

    assert steam.lobby_member_count(LOBBY_ID) == 2
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.assert_called_with(MATCHMAKING, LOBBY_ID)


def test_invalid_lobby_member_count(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.return_value = -1

    with pytest.raises(SteamError, match="member count"):
        steam.lobby_member_count(LOBBY_ID)


def test_lobby_members(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.return_value = 2
    lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.side_effect = (
        lambda matchmaking, lobby_id, index: [STEAM_ID, OTHER_STEAM_ID][index]
    )

    assert steam.lobby_members(LOBBY_ID) == [STEAM_ID, OTHER_STEAM_ID]
    lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.assert_called_with(
        MATCHMAKING, LOBBY_ID, 1
    )


def test_empty_lobby(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.return_value = 0

    assert steam.lobby_members(LOBBY_ID) == []
    lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.assert_not_called()


def test_empty_lobby_member_id(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers.return_value = 1
    lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex.return_value = 0

    with pytest.raises(SteamError, match="lobby member"):
        steam.lobby_members(LOBBY_ID)


def test_leave_lobby(steam, lib):
    steam.leave_lobby(LOBBY_ID)

    lib.SteamAPI_ISteamMatchmaking_LeaveLobby.assert_called_once_with(MATCHMAKING, LOBBY_ID)
    lib.SteamAPI_Shutdown.assert_not_called()
    assert steam.running


def test_invite_to_lobby(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.return_value = True

    steam.invite_to_lobby(LOBBY_ID, OTHER_STEAM_ID)

    lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.assert_called_once_with(
        MATCHMAKING, LOBBY_ID, OTHER_STEAM_ID
    )
    assert steam.running


def test_invite_to_lobby_not_sent(steam, lib):
    lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.return_value = False

    with pytest.raises(SteamError, match="InviteUserToLobby failed"):
        steam.invite_to_lobby(LOBBY_ID, OTHER_STEAM_ID)


@pytest.mark.parametrize(
    ("lobby_id", "friend_id", "message"),
    [(0, OTHER_STEAM_ID, "lobby ID"), (LOBBY_ID, 0, "friend SteamID")],
)
def test_invite_to_lobby_invalid_ids(steam, lib, lobby_id, friend_id, message):
    with pytest.raises(ValueError, match=message):
        steam.invite_to_lobby(lobby_id, friend_id)

    lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby.assert_not_called()


def test_networking_steam_id(steam, lib):
    steam_id = steam.networking_steam_id

    assert steam_id == STEAM_ID == steam.steam_id
    assert type(steam_id) is int
    sockets, identity = lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.call_args.args
    assert sockets == SOCKETS
    assert isinstance(identity, SteamNetworkingIdentity)


def test_networking_steam_id_before_start(load, lib):
    steam = SteamClient(DLL_PATH)

    with pytest.raises(SteamError, match="not running"):
        _ = steam.networking_steam_id

    load.assert_not_called()
    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.assert_not_called()


def test_networking_steam_id_after_close(load, lib):
    steam = SteamClient(DLL_PATH)
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        _ = steam.networking_steam_id

    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.assert_not_called()


def test_networking_steam_id_without_sockets_interface(steam, lib):
    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.return_value = None

    with pytest.raises(SteamError, match="ISteamNetworkingSockets"):
        _ = steam.networking_steam_id

    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.assert_not_called()


def test_networking_identity_not_known(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.side_effect = fill_identity(
        0, identity_type=0, known=False
    )

    with pytest.raises(SteamError, match="not known yet"):
        _ = steam.networking_steam_id


@pytest.mark.parametrize("identity_type", [0, 1, 2])
def test_networking_identity_not_a_steam_id(steam, lib, identity_type):
    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.side_effect = fill_identity(
        STEAM_ID, identity_type=identity_type
    )

    with pytest.raises(SteamError, match=f"not a SteamID \\(type {identity_type}\\)"):
        _ = steam.networking_steam_id


def test_networking_identity_empty_steam_id(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_GetIdentity.side_effect = fill_identity(0)

    with pytest.raises(SteamError, match="not a SteamID"):
        _ = steam.networking_steam_id
