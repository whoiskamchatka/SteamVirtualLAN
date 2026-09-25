import ctypes
import os

from steamlan.steam.loader import load_steam_api
from steamlan.steam.native import SteamAPIInitResult, SteamErrMsg, bind


class SteamInitError(Exception):
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

    def __enter__(self) -> "SteamClient":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
