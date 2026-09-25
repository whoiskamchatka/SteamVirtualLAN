import ctypes
import os
import time
from dataclasses import dataclass
from typing import Any

from steamlan.steam.loader import load_steam_api
from steamlan.steam.native import (
    API_CALL_INVALID,
    LOBBY_CREATED,
    LOBBY_ENTER,
    NET_HANDLE_INVALID,
    SEND_RELIABLE,
    SEND_UNRELIABLE_NO_NAGLE,
    STEAM_API_CALL_COMPLETED,
    CallbackMsg,
    ChatRoomEnterResponse,
    EResult,
    LobbyCreated,
    LobbyEnter,
    LobbyType,
    SteamAPICallCompleted,
    SteamAPIInitResult,
    SteamErrMsg,
    SteamNetworkingIdentity,
    SteamNetworkingMessage,
    bind,
    identity_steam_id,
    steam_friends,
    steam_matchmaking,
    steam_networking_sockets,
    steam_user,
    steam_utils,
)

_CALL_POLL_INTERVAL = 0.01


class SteamInitError(Exception):
    pass


class SteamError(Exception):
    pass


@dataclass(frozen=True)
class SteamCallback:
    callback_id: int
    payload: bytes
    # Set for the result of an asynchronous call (SteamAPICall_t); 0 otherwise.
    api_call: int = 0
    failed: bool = False


def _init_error_message(result: int, err: ctypes.Array[ctypes.c_char]) -> str:
    detail = err.value.decode("utf-8", errors="replace").strip()
    if not detail:
        try:
            detail = SteamAPIInitResult(result).name
        except ValueError:
            detail = f"result {result}"
    return f"Steam API initialization failed: {detail}"


def _read_callback(lib: ctypes.CDLL, pipe: int, msg: CallbackMsg) -> SteamCallback:
    size = msg.m_cubParam
    if size < 0 or (size and not msg.m_pubParam):
        raise SteamError(f"Steam returned a malformed callback (id {msg.m_iCallback})")
    payload = ctypes.string_at(msg.m_pubParam, size) if size else b""

    if msg.m_iCallback != STEAM_API_CALL_COMPLETED:
        return SteamCallback(msg.m_iCallback, payload)

    if size < ctypes.sizeof(SteamAPICallCompleted):
        raise SteamError("Steam returned a truncated SteamAPICallCompleted_t")
    completed = SteamAPICallCompleted.from_buffer_copy(payload)

    # Valve's manual dispatch loop fetches the call result while the completion
    # callback is still the current one, i.e. before FreeLastCallback. Steam
    # tells us the result's callback id and size.
    result = ctypes.create_string_buffer(completed.m_cubParam)
    io_failed = ctypes.c_bool()
    ok = lib.SteamAPI_ManualDispatch_GetAPICallResult(
        pipe,
        completed.m_hAsyncCall,
        result,
        completed.m_cubParam,
        completed.m_iCallback,
        io_failed,
    )
    return SteamCallback(
        completed.m_iCallback,
        result.raw if ok else b"",
        api_call=completed.m_hAsyncCall,
        failed=not ok or io_failed.value,
    )


def _result_name(result: int) -> str:
    try:
        return EResult(result).name
    except ValueError:
        return f"result {result}"


def read_lobby_created(callback: SteamCallback) -> int:
    """Lobby ID from the result of request_create_lobby(); raises SteamError on failure."""
    if callback.failed:
        raise SteamError("CreateLobby failed: Steam could not deliver the result")
    if callback.callback_id != LOBBY_CREATED:
        raise SteamError(f"CreateLobby returned unexpected callback {callback.callback_id}")
    if len(callback.payload) != ctypes.sizeof(LobbyCreated):
        raise SteamError(f"CreateLobby returned {len(callback.payload)} bytes for LobbyCreated_t")

    created = LobbyCreated.from_buffer_copy(callback.payload)
    if created.m_eResult != EResult.OK:
        raise SteamError(f"CreateLobby failed: {_result_name(created.m_eResult)}")
    if not created.m_ulSteamIDLobby:
        raise SteamError("CreateLobby succeeded but returned no lobby ID")
    return created.m_ulSteamIDLobby


def read_lobby_enter(callback: SteamCallback, lobby_id: int) -> int:
    """Lobby ID from the result of request_join_lobby(); raises SteamError on failure."""
    if callback.failed:
        raise SteamError("JoinLobby failed: Steam could not deliver the result")
    if callback.callback_id != LOBBY_ENTER:
        raise SteamError(f"JoinLobby returned unexpected callback {callback.callback_id}")
    if len(callback.payload) != ctypes.sizeof(LobbyEnter):
        raise SteamError(f"JoinLobby returned {len(callback.payload)} bytes for LobbyEnter_t")

    entered = LobbyEnter.from_buffer_copy(callback.payload)
    response = entered.m_EChatRoomEnterResponse
    if response != ChatRoomEnterResponse.SUCCESS:
        try:
            reason = ChatRoomEnterResponse(response).name
        except ValueError:
            reason = f"response {response}"
        raise SteamError(f"JoinLobby failed: {reason}")
    if entered.m_ulSteamIDLobby != lobby_id:
        raise SteamError("JoinLobby entered a different lobby than requested")
    return entered.m_ulSteamIDLobby


def _check_steam_id(steam_id: int, what: str) -> None:
    if not 0 < steam_id < 2**64:
        raise ValueError(f"invalid {what}: {steam_id}")


def _check_result(result: int, call: str) -> None:
    if result != EResult.OK:
        raise SteamError(f"{call} failed: {_result_name(result)}")


def _message_payload(pointer: Any, connection: int) -> bytes:
    if not pointer:
        raise SteamError("Steam returned a null message")
    message = pointer.contents
    size = message.m_cbSize
    if size < 0 or (size and not message.m_pData):
        raise SteamError(f"Steam returned a malformed message ({size} bytes)")
    if message.m_conn != connection:
        raise SteamError(f"Steam returned a message for connection {message.m_conn}")
    return ctypes.string_at(message.m_pData, size) if size else b""


class SteamClient:
    def __init__(self, dll_path: str | os.PathLike[str]):
        self.dll_path = dll_path
        self._lib: ctypes.CDLL | None = None
        # Callbacks received while waiting for a call result, returned by the
        # next run_callbacks().
        self._pending: list[SteamCallback] = []

    @property
    def running(self) -> bool:
        return self._lib is not None

    def start(self) -> None:
        if self._lib is not None:
            return

        lib = bind(load_steam_api(self.dll_path))

        err = SteamErrMsg()
        # ctypes passes the array by reference because argtypes declares a pointer to it.
        result = lib.SteamAPI_InitFlat(err)
        if result != SteamAPIInitResult.OK:
            raise SteamInitError(_init_error_message(result, err))

        # Callbacks are delivered as plain messages instead of through C++
        # callback objects. This must happen right after init, and
        # SteamAPI_RunCallbacks must not be used from then on.
        lib.SteamAPI_ManualDispatch_Init()
        self._lib = lib

    def close(self) -> None:
        if self._lib is None:
            return

        lib, self._lib = self._lib, None
        self._pending = []
        lib.SteamAPI_Shutdown()

    @property
    def steam_id(self) -> int:
        lib = self._running_lib()
        user = steam_user(lib)
        if not user:
            raise SteamError("could not get the ISteamUser interface")

        steam_id = lib.SteamAPI_ISteamUser_GetSteamID(user)
        if not steam_id:
            raise SteamError("Steam returned an empty SteamID")
        return steam_id

    @property
    def persona_name(self) -> str:
        lib = self._running_lib()
        name = lib.SteamAPI_ISteamFriends_GetPersonaName(self._friends(lib))
        if name is None:
            raise SteamError("Steam returned no persona name")
        return name.decode("utf-8", errors="replace")

    @property
    def networking_steam_id(self) -> int:
        """SteamID of the local SteamNetworkingSockets identity."""
        lib = self._running_lib()
        identity = SteamNetworkingIdentity()
        if not lib.SteamAPI_ISteamNetworkingSockets_GetIdentity(self._sockets(lib), identity):
            raise SteamError("Steam networking identity is not known yet")
        steam_id = identity_steam_id(identity)
        if not steam_id:
            raise SteamError(
                f"Steam networking identity is not a SteamID (type {identity.m_eType})"
            )
        return steam_id

    def run_callbacks(self) -> list[SteamCallback]:
        callbacks = self._pump(self._running_lib())
        callbacks, self._pending = self._pending + callbacks, []
        return callbacks

    def create_lobby(
        self,
        max_members: int,
        lobby_type: LobbyType = LobbyType.FRIENDS_ONLY,
        timeout: float = 10.0,
    ) -> int:
        api_call = self.request_create_lobby(max_members, lobby_type)
        return read_lobby_created(self._wait_for_call(api_call, timeout))

    def request_create_lobby(
        self, max_members: int, lobby_type: LobbyType = LobbyType.FRIENDS_ONLY
    ) -> int:
        """Start CreateLobby without waiting; the result arrives from run_callbacks()."""
        lib = self._running_lib()
        api_call = lib.SteamAPI_ISteamMatchmaking_CreateLobby(
            self._matchmaking(lib), lobby_type, max_members
        )
        if api_call == API_CALL_INVALID:
            raise SteamError("CreateLobby could not be started")
        return api_call

    def join_lobby(self, lobby_id: int, timeout: float = 10.0) -> int:
        api_call = self.request_join_lobby(lobby_id)
        return read_lobby_enter(self._wait_for_call(api_call, timeout), lobby_id)

    def request_join_lobby(self, lobby_id: int) -> int:
        """Start JoinLobby without waiting; the result arrives from run_callbacks()."""
        lib = self._running_lib()
        _check_steam_id(lobby_id, "lobby ID")
        api_call = lib.SteamAPI_ISteamMatchmaking_JoinLobby(self._matchmaking(lib), lobby_id)
        if api_call == API_CALL_INVALID:
            raise SteamError("JoinLobby could not be started")
        return api_call

    def lobby_owner(self, lobby_id: int) -> int:
        lib = self._running_lib()
        owner = lib.SteamAPI_ISteamMatchmaking_GetLobbyOwner(self._matchmaking(lib), lobby_id)
        if not owner:
            raise SteamError("Steam returned no lobby owner")
        return owner

    def set_lobby_data(self, lobby_id: int, key: str, value: str) -> None:
        """Set lobby metadata. Anyone who knows the lobby ID can read it."""
        lib = self._running_lib()
        if not lib.SteamAPI_ISteamMatchmaking_SetLobbyData(
            self._matchmaking(lib), lobby_id, key.encode(), value.encode()
        ):
            raise SteamError(f"SetLobbyData failed for {key!r}")

    def lobby_data(self, lobby_id: int, key: str) -> str:
        """Lobby metadata value, or "" when it is not set."""
        lib = self._running_lib()
        value = lib.SteamAPI_ISteamMatchmaking_GetLobbyData(
            self._matchmaking(lib), lobby_id, key.encode()
        )
        return (value or b"").decode("utf-8", errors="replace")

    def friend_persona_name(self, steam_id: int) -> str:
        """Name of another user, or "" while Steam does not know it yet."""
        lib = self._running_lib()
        name = lib.SteamAPI_ISteamFriends_GetFriendPersonaName(self._friends(lib), steam_id)
        name = (name or b"").decode("utf-8", errors="replace")
        return "" if name == "[unknown]" else name

    def leave_lobby(self, lobby_id: int) -> None:
        lib = self._running_lib()
        lib.SteamAPI_ISteamMatchmaking_LeaveLobby(self._matchmaking(lib), lobby_id)

    def invite_to_lobby(self, lobby_id: int, friend_id: int) -> None:
        lib = self._running_lib()
        _check_steam_id(lobby_id, "lobby ID")
        _check_steam_id(friend_id, "friend SteamID")
        if not lib.SteamAPI_ISteamMatchmaking_InviteUserToLobby(
            self._matchmaking(lib), lobby_id, friend_id
        ):
            raise SteamError("InviteUserToLobby failed; Steam may not be connected")

    def lobby_member_count(self, lobby_id: int) -> int:
        lib = self._running_lib()
        count = lib.SteamAPI_ISteamMatchmaking_GetNumLobbyMembers(self._matchmaking(lib), lobby_id)
        if count < 0:
            raise SteamError(f"Steam returned an invalid lobby member count ({count})")
        return count

    def lobby_members(self, lobby_id: int) -> list[int]:
        lib = self._running_lib()
        matchmaking = self._matchmaking(lib)
        members = [
            lib.SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex(matchmaking, lobby_id, i)
            for i in range(self.lobby_member_count(lobby_id))
        ]
        if not all(members):
            raise SteamError("Steam returned an empty SteamID for a lobby member")
        return members

    def create_listen_socket(self, virtual_port: int = 0) -> int:
        lib = self._running_lib()
        listen_socket = lib.SteamAPI_ISteamNetworkingSockets_CreateListenSocketP2P(
            self._sockets(lib), virtual_port, 0, None
        )
        if listen_socket == NET_HANDLE_INVALID:
            raise SteamError("CreateListenSocketP2P failed")
        return listen_socket

    def close_listen_socket(self, listen_socket: int) -> bool:
        """Close a listen socket; returns False if Steam no longer knew the handle."""
        lib = self._running_lib()
        return lib.SteamAPI_ISteamNetworkingSockets_CloseListenSocket(
            self._sockets(lib), listen_socket
        )

    def connect_p2p(self, remote_steam_id: int, virtual_port: int = 0) -> int:
        lib = self._running_lib()
        _check_steam_id(remote_steam_id, "remote SteamID")
        identity = SteamNetworkingIdentity()
        lib.SteamAPI_SteamNetworkingIdentity_SetSteamID64(identity, remote_steam_id)
        connection = lib.SteamAPI_ISteamNetworkingSockets_ConnectP2P(
            self._sockets(lib), identity, virtual_port, 0, None
        )
        if connection == NET_HANDLE_INVALID:
            raise SteamError("ConnectP2P failed")
        return connection

    def accept_connection(self, connection: int) -> None:
        lib = self._running_lib()
        result = lib.SteamAPI_ISteamNetworkingSockets_AcceptConnection(
            self._sockets(lib), connection
        )
        _check_result(result, "AcceptConnection")

    def close_connection(self, connection: int, debug: str = "", linger: bool = False) -> bool:
        """Close a connection; returns False if Steam no longer knew the handle.

        Also required after the peer closed it or it failed, to free the handle.
        """
        lib = self._running_lib()
        # Reason 0 is k_ESteamNetConnectionEnd_App_Generic. Without linger,
        # reliable data that has not been sent yet is dropped; with it, Steam
        # keeps sending from this process for a while after the call.
        return lib.SteamAPI_ISteamNetworkingSockets_CloseConnection(
            self._sockets(lib), connection, 0, debug.encode(), linger
        )

    def send_message(self, connection: int, data: bytes, reliable: bool = True) -> None:
        """Send data as one message: reliable, or unreliable and without delay."""
        lib = self._running_lib()
        data = bytes(data)
        flags = SEND_RELIABLE if reliable else SEND_UNRELIABLE_NO_NAGLE
        result = lib.SteamAPI_ISteamNetworkingSockets_SendMessageToConnection(
            self._sockets(lib), connection, data, len(data), flags, None
        )
        _check_result(result, "SendMessageToConnection")

    def receive_messages(self, connection: int, max_messages: int = 32) -> list[bytes]:
        lib = self._running_lib()
        received = (ctypes.POINTER(SteamNetworkingMessage) * max_messages)()
        count = lib.SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnConnection(
            self._sockets(lib), connection, received, max_messages
        )
        if count < 0:
            raise SteamError(f"ReceiveMessagesOnConnection failed for connection {connection}")

        messages = received[: min(count, max_messages)]
        try:
            if count > max_messages:
                raise SteamError(f"Steam returned {count} messages for {max_messages} slots")
            return [_message_payload(message, connection) for message in messages]
        finally:
            # Steam owns every returned message. Payloads are copied above, and
            # each message is released exactly once, even if one was malformed.
            for message in messages:
                if message:
                    lib.SteamAPI_SteamNetworkingMessage_t_Release(message)

    def _wait_for_call(self, api_call: int, timeout: float) -> SteamCallback:
        lib = self._running_lib()
        deadline = time.monotonic() + timeout
        while True:
            result = None
            for callback in self._pump(lib):
                if result is None and callback.api_call == api_call:
                    result = callback
                else:
                    self._pending.append(callback)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise SteamError(f"timed out after {timeout:g}s waiting for Steam API call")
            time.sleep(_CALL_POLL_INTERVAL)

    @property
    def overlay_enabled(self) -> bool:
        """Whether the Steam overlay has hooked this process and can be shown.

        Steam needs a few seconds after start to hook a window, and can only hook
        one presented with Direct3D, OpenGL or Vulkan.
        """
        lib = self._running_lib()
        return lib.SteamAPI_ISteamUtils_IsOverlayEnabled(self._utils(lib))

    def overlay_needs_present(self) -> bool:
        """Whether the overlay is waiting for the window to present a new frame."""
        lib = self._running_lib()
        return lib.SteamAPI_ISteamUtils_BOverlayNeedsPresent(self._utils(lib))

    def open_invite_dialog(self, connect_string: str) -> None:
        """Open the Steam overlay's invite dialog; invites carry connect_string.

        The chosen friends' copy of the app receives it as a rich presence join
        request when they accept.
        """
        data = connect_string.encode()
        # It must fit a char[k_cchMaxRichPresenceValueLength] with its terminator.
        if not data or len(data) >= 256 or b"\0" in data:
            raise ValueError("invite connect string must be 1 to 255 bytes without NUL")
        lib = self._running_lib()
        lib.SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialogConnectString(
            self._friends(lib), data
        )

    def _utils(self, lib: ctypes.CDLL) -> int:
        utils = steam_utils(lib)
        if not utils:
            raise SteamError("could not get the ISteamUtils interface")
        return utils

    def _friends(self, lib: ctypes.CDLL) -> int:
        friends = steam_friends(lib)
        if not friends:
            raise SteamError("could not get the ISteamFriends interface")
        return friends

    def _sockets(self, lib: ctypes.CDLL) -> int:
        sockets = steam_networking_sockets(lib)
        if not sockets:
            raise SteamError("could not get the ISteamNetworkingSockets interface")
        return sockets

    def _matchmaking(self, lib: ctypes.CDLL) -> int:
        matchmaking = steam_matchmaking(lib)
        if not matchmaking:
            raise SteamError("could not get the ISteamMatchmaking interface")
        return matchmaking

    def _pump(self, lib: ctypes.CDLL) -> list[SteamCallback]:
        pipe = lib.SteamAPI_GetHSteamPipe()
        lib.SteamAPI_ManualDispatch_RunFrame(pipe)

        callbacks = []
        msg = CallbackMsg()
        while lib.SteamAPI_ManualDispatch_GetNextCallback(pipe, msg):
            # msg.m_pubParam points into memory Steam releases in
            # FreeLastCallback, which must be called before fetching the next one.
            try:
                callbacks.append(_read_callback(lib, pipe, msg))
            finally:
                lib.SteamAPI_ManualDispatch_FreeLastCallback(pipe)
        return callbacks

    def _running_lib(self) -> ctypes.CDLL:
        if self._lib is None:
            raise SteamError("Steam API is not running; call start() first")
        return self._lib

    def __enter__(self) -> "SteamClient":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
