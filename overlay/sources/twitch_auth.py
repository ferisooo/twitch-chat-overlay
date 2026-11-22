"""Your Twitch login: pasted token or "Log in with Twitch" (device login).

Tokens are kept in Windows Credential Manager. Twitch asks apps to check a
token about once an hour; `fresh_token()` does that and renews device-login
tokens by itself.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError

from . import twitch_api as api

log = logging.getLogger(__name__)

VALIDATE_EVERY_S = 3600.0
Emit = Callable[[dict[str, Any]], None]


class TwitchAccount:
    def __init__(self, emit: Emit, store: Any, legacy_secrets: Optional[Path] = None) -> None:
        self.emit = emit
        self.store = store
        self._lock = threading.RLock()
        self._data: dict[str, Any] = store.load() or {}
        self._validated_at = 0.0
        self._device_stop = threading.Event()
        if not self._data.get("access_token") and legacy_secrets is not None:
            self._import_legacy(legacy_secrets)

    def _import_legacy(self, path: Path) -> None:
        """Bring over the old version's token (the old file is left alone)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        token = api.normalize_oauth((data or {}).get("twitch_oauth") if isinstance(data, dict) else "")
        if token and api.looks_like_oauth_token(token):
            self._data = {"access_token": token, "kind": "paste"}
            self.store.save(self._data)
            log.info("Imported the Twitch login from the old version")

    # ----- what other parts read -----
    @property
    def logged_in(self) -> bool:
        return bool(self._data.get("access_token"))

    @property
    def login(self) -> str:
        return str(self._data.get("login") or "")

    @property
    def client_id(self) -> str:
        return str(self._data.get("client_id") or "")

    @property
    def user_id(self) -> str:
        return str(self._data.get("user_id") or "")

    @property
    def scopes(self) -> set[str]:
        return {str(s).lower() for s in self._data.get("scopes") or []}

    def fresh_token(self, force: bool = False) -> str:
        """A token that Twitch accepts right now, or "" if logged out."""
        with self._lock:
            token = str(self._data.get("access_token") or "")
            if not token:
                return ""
            recent = time.monotonic() - self._validated_at < VALIDATE_EVERY_S
            if not force and recent and self.login and self.client_id:
                return token
            try:
                info = api.validate(token)
            except HTTPError as exc:
                if exc.code != 401:
                    return token
                return self._renew()
            except (URLError, OSError, ValueError):
                return token  # offline: try anyway, chat will retry
            self._remember(info)
            return token

    def _remember(self, info: dict[str, Any]) -> None:
        self._data["login"] = str(info.get("login") or self.login).lower()
        self._data["user_id"] = str(info.get("user_id") or self.user_id)
        self._data["client_id"] = str(info.get("client_id") or self.client_id)
        self._data["scopes"] = list(info.get("scopes") or self._data.get("scopes") or [])
        self._validated_at = time.monotonic()
        self.store.save(self._data)

    def _renew(self) -> str:
        refresh_token = str(self._data.get("refresh_token") or "")
        app_id = str(self._data.get("app_client_id") or "")
        if refresh_token and app_id:
            try:
                tokens = api.refresh(app_id, refresh_token)
                self._data["access_token"] = tokens["access_token"]
                self._data["refresh_token"] = tokens.get("refresh_token") or refresh_token
                self._remember(api.validate(tokens["access_token"]))
                return str(self._data["access_token"])
            except (api.TwitchAuthError, HTTPError, URLError, OSError, ValueError, KeyError):
                log.info("Couldn't renew the Twitch login")
        self._data = {}
        self.store.clear()
        self.emit({"src": "account", "kind": "expired"})
        return ""

    # ----- logging in -----
    def login_with_token(self, raw: str) -> None:
        def work() -> None:
            try:
                info = api.check_pasted_token(raw)
            except api.TwitchAuthError as exc:
                self.emit({"src": "account", "kind": "login_failed", "text": str(exc)})
                return
            with self._lock:
                self._data = {"access_token": info["token"], "kind": "paste"}
                self._remember(info)
            self.emit({"src": "account", "kind": "login_ok", "login": self.login})

        threading.Thread(target=work, daemon=True, name="twitch-login").start()

    def start_device_login(self, app_client_id: str) -> None:
        app_client_id = str(app_client_id or "").strip()
        self._device_stop.set()
        stop = threading.Event()
        self._device_stop = stop

        def work() -> None:
            try:
                start = api.device_start(app_client_id)
            except api.TwitchAuthError as exc:
                self.emit({"src": "account", "kind": "login_failed", "text": str(exc)})
                return
            except (URLError, OSError, ValueError):
                self.emit({"src": "account", "kind": "login_failed", "text": "Couldn't reach Twitch. Try again."})
                return
            self.emit(
                {
                    "src": "account",
                    "kind": "device_code",
                    "device": {
                        "code": str(start.get("user_code") or ""),
                        "url": str(start.get("verification_uri") or "https://www.twitch.tv/activate"),
                    },
                }
            )
            interval = max(1.0, float(start.get("interval") or 5))
            deadline = time.monotonic() + float(start.get("expires_in") or 1800)
            while not stop.wait(interval):
                if time.monotonic() > deadline:
                    self.emit({"src": "account", "kind": "login_failed", "text": "The login code ran out. Try again."})
                    return
                try:
                    state, data = api.device_poll(app_client_id, str(start["device_code"]))
                except (URLError, OSError, ValueError):
                    continue
                if state == "pending":
                    continue
                if state == "slow_down":
                    interval += 2
                    continue
                if state == "failed":
                    self.emit({"src": "account", "kind": "login_failed", "text": "Twitch login was cancelled."})
                    return
                try:
                    info = api.validate(data["access_token"])
                except (HTTPError, URLError, OSError, ValueError, KeyError):
                    self.emit({"src": "account", "kind": "login_failed", "text": "Couldn't finish the login. Try again."})
                    return
                with self._lock:
                    self._data = {
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token") or "",
                        "app_client_id": app_client_id,
                        "kind": "device",
                    }
                    self._remember(info)
                self.emit({"src": "account", "kind": "login_ok", "login": self.login})
                return

        threading.Thread(target=work, daemon=True, name="twitch-device-login").start()

    def cancel_device_login(self) -> None:
        self._device_stop.set()

    def logout(self) -> None:
        self._device_stop.set()
        with self._lock:
            self._data = {}
            self._validated_at = 0.0
            self.store.clear()
