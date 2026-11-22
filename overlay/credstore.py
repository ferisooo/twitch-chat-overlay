"""Keeps the Twitch login safe.

On Windows it goes into Windows Credential Manager (the same place browsers
and Git keep passwords), not a plain file. Elsewhere it falls back to a file
only you can read.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

TARGET = "LiveChatOverlay/twitch"
FALLBACK_NAME = "token.json"


class CredStore:
    def __init__(self, target: str = TARGET, fallback_dir: Path | None = None) -> None:
        self.target = target
        self.fallback_path = (fallback_dir or config.data_dir()) / FALLBACK_NAME
        self._win = sys.platform == "win32" and not os.environ.get("LCO_NO_CREDMAN")

    def load(self) -> dict[str, Any]:
        raw = None
        if self._win:
            raw = _win_read(self.target)
        if raw is None:
            try:
                raw = self.fallback_path.read_bytes()
            except OSError:
                raw = None
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        blob = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if self._win and _win_write(self.target, blob):
            # Remove any older fallback copy so the token is only in one place.
            try:
                self.fallback_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self.fallback_path.write_bytes(blob)
            try:
                os.chmod(self.fallback_path, 0o600)
            except OSError:
                pass
        except OSError:
            log.exception("Couldn't save the Twitch login")

    def clear(self) -> None:
        if self._win:
            _win_delete(self.target)
        try:
            self.fallback_path.unlink(missing_ok=True)
        except OSError:
            pass


# ----- Windows Credential Manager (advapi32) -----

def _advapi():  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    adv.CredFree.restype = None
    return ctypes, adv, CREDENTIALW


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


def _win_read(target: str) -> bytes | None:  # pragma: no cover - Windows only
    try:
        ctypes, adv, CREDENTIALW = _advapi()
        ptr = ctypes.POINTER(CREDENTIALW)()
        if not adv.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
            return None
        try:
            cred = ptr.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            adv.CredFree(ptr)
    except Exception:  # noqa: BLE001
        log.exception("Credential Manager read failed")
        return None


def _win_write(target: str, blob: bytes) -> bool:  # pragma: no cover - Windows only
    try:
        ctypes, adv, CREDENTIALW = _advapi()
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "twitch"
        return bool(adv.CredWriteW(ctypes.byref(cred), 0))
    except Exception:  # noqa: BLE001
        log.exception("Credential Manager write failed")
        return False


def _win_delete(target: str) -> None:  # pragma: no cover - Windows only
    try:
        _, adv, _ = _advapi()
        adv.CredDeleteW(target, _CRED_TYPE_GENERIC, 0)
    except Exception:  # noqa: BLE001
        pass
