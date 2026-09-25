import ctypes
import os

from steamlan.steam.loader import load_steam_api
from steamlan.steam.native import (
    SteamAPIInitResult,
    SteamErrMsg,
    bind,
    steam_friends,
    steam_user,
)


class SteamInitError(Exception):
    pass


class SteamError(Exception):
    pass


def _init_error_message(result: int, err: ctypes.Array[ctypes.c_char]) -> str:
    detail = err.value.decode("utf-8", errors="replace").strip()
    if not detail:
        try:
            detail = SteamAPIInitResult(result).name
        except ValueError:
            detail = f"result {result}"
    return f"Steam API initialization failed: {detail}"


class SteamClient:
    def __init__(self, dll_path: str | os.PathLike[str]):
        self.dll_path = dll_path
        self._lib: ctypes.CDLL | None = None

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

        self._lib = lib

    def close(self) -> None:
        if self._lib is None:
            return

        lib, self._lib = self._lib, None
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
        friends = steam_friends(lib)
        if not friends:
            raise SteamError("could not get the ISteamFriends interface")

        name = lib.SteamAPI_ISteamFriends_GetPersonaName(friends)
        if name is None:
            raise SteamError("Steam returned no persona name")
        return name.decode("utf-8", errors="replace")

    def _running_lib(self) -> ctypes.CDLL:
        if self._lib is None:
            raise SteamError("Steam API is not running; call start() first")
        return self._lib

    def __enter__(self) -> "SteamClient":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
