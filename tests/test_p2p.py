import ctypes
from unittest import mock

import pytest

from steamlan.steam import SteamClient, SteamError
from steamlan.steam.native import (
    EResult,
    SteamAPIInitResult,
    SteamNetworkingMessage,
    identity_steam_id,
)

SOCKETS = 0x4000
REMOTE_ID = 76561197960265730
LISTEN_SOCKET = 0x55
CONNECTION = 0x1234


@pytest.fixture
def lib():
    lib = mock.Mock()
    lib.SteamAPI_InitFlat.return_value = SteamAPIInitResult.OK
    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.return_value = SOCKETS
    lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P.return_value = LISTEN_SOCKET
    lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.return_value = CONNECTION
    lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection.return_value = EResult.OK
    lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection.return_value = EResult.OK
    lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.return_value = 0
    return lib


@pytest.fixture
def load(lib):
    with mock.patch("steamlan.steam.client.load_steam_api", return_value=lib) as load:
        yield load


@pytest.fixture
def steam(load):
    with SteamClient("steam_api64.dll") as steam:
        yield steam


def set_steam_id64(identity, steam_id):
    # What SteamNetworkingIdentity::SetSteamID64 does in steamnetworkingtypes.h.
    identity.m_eType = 16
    identity.m_cbSize = 8
    identity.m_data[:8] = steam_id.to_bytes(8, "little")


P2P_OPERATIONS = {
    "create_listen_socket": lambda steam: steam.create_listen_socket(0),
    "close_listen_socket": lambda steam: steam.close_listen_socket(LISTEN_SOCKET),
    "connect_p2p": lambda steam: steam.connect_p2p(REMOTE_ID, 0),
    "accept_connection": lambda steam: steam.accept_connection(CONNECTION),
    "close_connection": lambda steam: steam.close_connection(CONNECTION),
    "send_message": lambda steam: steam.send_message(CONNECTION, b"x"),
    "receive_messages": lambda steam: steam.receive_messages(CONNECTION),
}


@pytest.mark.parametrize("operation", P2P_OPERATIONS.values(), ids=P2P_OPERATIONS)
def test_p2p_before_start(load, lib, operation):
    with pytest.raises(SteamError, match="not running"):
        operation(SteamClient("steam_api64.dll"))

    load.assert_not_called()


@pytest.mark.parametrize("operation", P2P_OPERATIONS.values(), ids=P2P_OPERATIONS)
def test_p2p_after_close(load, lib, operation):
    steam = SteamClient("steam_api64.dll")
    steam.start()
    steam.close()

    with pytest.raises(SteamError, match="not running"):
        operation(steam)

    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.assert_not_called()


@pytest.mark.parametrize("operation", P2P_OPERATIONS.values(), ids=P2P_OPERATIONS)
def test_p2p_without_sockets_interface(steam, lib, operation):
    lib.SteamAPI_SteamNetworkingSockets_SteamAPI_v013.return_value = None

    with pytest.raises(SteamError, match="ISteamNetworkingSockets"):
        operation(steam)


def test_create_listen_socket(steam, lib):
    assert steam.create_listen_socket(0) == LISTEN_SOCKET

    lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P.assert_called_once_with(
        SOCKETS, 0, 0, None
    )


def test_create_listen_socket_invalid_handle(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P.return_value = 0

    with pytest.raises(SteamError, match="CreateListenSocketP2P failed"):
        steam.create_listen_socket(0)


def test_close_listen_socket(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket.return_value = True

    assert steam.close_listen_socket(LISTEN_SOCKET) is True
    lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket.assert_called_once_with(
        SOCKETS, LISTEN_SOCKET
    )


def test_close_unknown_listen_socket(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket.return_value = False

    assert steam.close_listen_socket(LISTEN_SOCKET) is False


def test_connect_p2p(steam, lib):
    seen = {}
    lib.SteamAPI_SteamNetworkingIdentity_SetSteamID64.side_effect = set_steam_id64

    def connect(sockets, identity, virtual_port, option_count, options):
        seen["remote"] = identity_steam_id(identity)
        seen["args"] = (sockets, virtual_port, option_count, options)
        return CONNECTION

    lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.side_effect = connect

    assert steam.connect_p2p(REMOTE_ID, 0) == CONNECTION
    assert seen == {"remote": REMOTE_ID, "args": (SOCKETS, 0, 0, None)}
    (identity, steam_id) = lib.SteamAPI_SteamNetworkingIdentity_SetSteamID64.call_args.args
    assert steam_id == REMOTE_ID
    assert identity is lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.call_args.args[1]


def test_connect_p2p_invalid_handle(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.return_value = 0

    with pytest.raises(SteamError, match="ConnectP2P failed"):
        steam.connect_p2p(REMOTE_ID, 0)


@pytest.mark.parametrize("steam_id", [0, -1, 2**64])
def test_connect_p2p_invalid_steam_id(steam, lib, steam_id):
    with pytest.raises(ValueError, match="remote SteamID"):
        steam.connect_p2p(steam_id, 0)

    lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P.assert_not_called()


def test_accept_connection(steam, lib):
    steam.accept_connection(CONNECTION)

    lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection.assert_called_once_with(
        SOCKETS, CONNECTION
    )


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (EResult.INVALID_STATE, "INVALID_STATE"),
        (EResult.INVALID_PARAM, "INVALID_PARAM"),
        (99, "result 99"),
    ],
)
def test_accept_connection_failed(steam, lib, result, message):
    lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection.return_value = result

    with pytest.raises(SteamError, match=f"AcceptConnection failed: {message}"):
        steam.accept_connection(CONNECTION)


def test_close_connection(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_CloseConnection.return_value = True

    assert steam.close_connection(CONNECTION, "done") is True
    lib.SteamAPI_ISteamNetworkingSockets_CloseConnection.assert_called_once_with(
        SOCKETS, CONNECTION, 0, b"done", False
    )


def test_close_unknown_connection(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_CloseConnection.return_value = False

    assert steam.close_connection(CONNECTION) is False


@pytest.mark.parametrize(
    "data", [b"hello", b"\x00\x01\x00", b"", bytearray(b"ab"), memoryview(b"cd")]
)
def test_send_message(steam, lib, data):
    steam.send_message(CONNECTION, data)

    sockets, connection, sent, size, flags, message_number = (
        lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection.call_args.args
    )
    assert (sockets, connection) == (SOCKETS, CONNECTION)
    assert type(sent) is bytes
    assert sent == bytes(data)
    assert size == len(bytes(data))
    assert flags == 8
    assert message_number is None


@pytest.mark.parametrize(
    ("result", "message"),
    [(EResult.NO_CONNECTION, "NO_CONNECTION"), (EResult.LIMIT_EXCEEDED, "LIMIT_EXCEEDED")],
)
def test_send_message_failed(steam, lib, result, message):
    lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection.return_value = result

    with pytest.raises(SteamError, match=f"SendMessageToConnection failed: {message}"):
        steam.send_message(CONNECTION, b"hello")


class FakeMessages:
    """Hands out native-looking messages and wipes their payload when released."""

    def __init__(self, lib, messages, count=None):
        self.messages = []
        self.buffers = []
        for payload, size, connection in messages:
            data = payload or b""
            buffer = ctypes.create_string_buffer(data, max(len(data), 1))
            message = SteamNetworkingMessage()
            message.m_pData = ctypes.addressof(buffer) if payload is not None else None
            message.m_cbSize = len(data) if size is None else size
            message.m_conn = connection
            self.messages.append(message)
            self.buffers.append(buffer)
        self.count = len(self.messages) if count is None else count
        self.released = []
        lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.side_effect = self.receive
        lib.SteamAPI_SteamNetworkingMessage_t_Release.side_effect = self.release

    def receive(self, sockets, connection, out, max_messages):
        for index, message in enumerate(self.messages[:max_messages]):
            out[index] = ctypes.pointer(message)
        return self.count

    def release(self, pointer):
        message = pointer.contents
        self.released.append(ctypes.addressof(message))
        if message.m_pData and message.m_cbSize > 0:
            ctypes.memset(message.m_pData, 0xDD, message.m_cbSize)


def message(payload, size=None, connection=CONNECTION):
    return (payload, size, connection)


def test_receive_no_messages(steam, lib):
    fake = FakeMessages(lib, [])

    assert steam.receive_messages(CONNECTION) == []
    assert fake.released == []


def test_receive_one_message(steam, lib):
    fake = FakeMessages(lib, [message(b"hello from host")])

    assert steam.receive_messages(CONNECTION) == [b"hello from host"]
    assert len(fake.released) == 1
    sockets, connection, _, max_messages = (
        lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.call_args.args
    )
    assert (sockets, connection, max_messages) == (SOCKETS, CONNECTION, 32)


def test_receive_multiple_messages_releases_each_once(steam, lib):
    payloads = [b"one", b"\x00binary\x00\xff", b"", b"three"]
    fake = FakeMessages(lib, [message(payload) for payload in payloads])

    received = steam.receive_messages(CONNECTION)

    assert received == payloads
    assert all(type(payload) is bytes for payload in received)
    assert sorted(fake.released) == sorted(ctypes.addressof(m) for m in fake.messages)
    assert len(set(fake.released)) == len(payloads)


def test_payload_is_copied_before_release(steam, lib):
    fake = FakeMessages(lib, [message(b"payload")])

    (payload,) = steam.receive_messages(CONNECTION)

    assert payload == b"payload"
    assert fake.buffers[0].raw[:7] == b"\xdd" * 7


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        (message(None, size=5), "malformed message"),
        (message(b"abc", size=-1), "malformed message"),
        (message(b"abc", connection=0x999), "connection 2457"),
    ],
)
def test_bad_message_still_releases_all(steam, lib, bad, error):
    fake = FakeMessages(lib, [message(b"ok"), bad, message(b"after")])

    with pytest.raises(SteamError, match=error):
        steam.receive_messages(CONNECTION)

    assert len(fake.released) == 3


def test_null_message_pointer(steam, lib):
    def receive(sockets, connection, out, max_messages):
        return 1

    lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.side_effect = receive

    with pytest.raises(SteamError, match="null message"):
        steam.receive_messages(CONNECTION)

    lib.SteamAPI_SteamNetworkingMessage_t_Release.assert_not_called()


def test_receive_invalid_connection(steam, lib):
    lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection.return_value = -1

    with pytest.raises(SteamError, match="ReceiveMessagesOnConnection failed"):
        steam.receive_messages(CONNECTION)

    lib.SteamAPI_SteamNetworkingMessage_t_Release.assert_not_called()


def test_receive_more_messages_than_slots(steam, lib):
    fake = FakeMessages(lib, [message(b"a"), message(b"b")], count=5)

    with pytest.raises(SteamError, match="5 messages for 2 slots"):
        steam.receive_messages(CONNECTION, max_messages=2)

    assert len(fake.released) == 2
