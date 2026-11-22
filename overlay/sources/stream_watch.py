"""Notices when your Twitch stream goes live (for the "I'm live" posts)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import twitch_api as api

log = logging.getLogger(__name__)

REFRESH_S = 45.0
Emit = Callable[[dict[str, Any]], None]


class StreamWatcher:
    def __init__(self, emit: Emit, account: Any, announced_id: str = "") -> None:
        self.emit = emit
        self.account = account
        self.announced_id = announced_id
        self._channel = ""
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def set_channel(self, channel: str) -> None:
        self._channel = str(channel or "").strip().lower()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="twitch-live-check")
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while True:
            channel = self._channel
            if channel and self.account is not None and self.account.logged_in:
                try:
                    token = self.account.fresh_token()
                    if token and self.account.client_id:
                        info = api.stream_info(channel, token, self.account.client_id)
                        self.emit({"src": "twitch", "kind": "live", "info": info})
                        if info and info.get("id") and info["id"] != self.announced_id:
                            self.announced_id = info["id"]  # once per stream
                            self.emit({"src": "twitch", "kind": "went_live", "info": info})
                except api.TwitchAuthError:
                    self.account.fresh_token(force=True)
                except Exception:  # noqa: BLE001
                    log.debug("Live check failed", exc_info=True)
            self._wake.wait(REFRESH_S)
            self._wake.clear()
