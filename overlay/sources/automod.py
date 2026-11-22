"""Messages AutoMod holds back, plus Allow/Deny for them.

Twitch never sends held messages over chat (IRC). They come from EventSub
over a websocket, and Allow/Deny goes through the Helix API. Needs a login
with moderator:manage:automod, from the streamer or one of their mods.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError

from ..wsproto import WebSocketClient
from . import twitch_api as api

log = logging.getLogger(__name__)

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"
Emit = Callable[[dict[str, Any]], None]
Creds = tuple[str, str, str, str]  # token, client_id, moderator id, broadcaster id


def parse_automod_hold(event: Any) -> Optional[dict[str, str]]:
    """A held message from an automod.message.hold event (version 1 or 2)."""
    if not isinstance(event, dict):
        return None
    msg_id = str(event.get("message_id") or "").strip()
    if not msg_id:
        return None
    message = event.get("message")
    text = str(message.get("text") or "") if isinstance(message, dict) else str(message or "")
    automod = event.get("automod") if isinstance(event.get("automod"), dict) else {}
    category = str(automod.get("category") or event.get("category") or "").strip()
    if event.get("reason") == "blocked_term":
        why = "blocked term"
    elif category:
        why = category.replace("_", " ")
    else:
        why = ""
    return {
        "id": msg_id,
        "login": str(event.get("user_login") or "").strip().lower(),
        "name": str(event.get("user_name") or event.get("user_login") or "someone").strip(),
        "text": re.sub(r"\s+", " ", text).strip(),
        "why": why,
    }


class TwitchAutoMod:
    def __init__(self, emit: Emit, account: Any) -> None:
        self.emit = emit
        self.account = account
        self._lock = threading.Lock()
        self._session = 0
        self._ws: Optional[WebSocketClient] = None
        self._creds: Optional[Creds] = None
        self._tipped: set[tuple[str, str]] = set()

    def connect(self, channel: str) -> None:
        channel = str(channel or "").strip().lstrip("#").lower()
        with self._lock:
            self._session += 1
            session = self._session
            ws, self._ws = self._ws, None
            self._creds = None
        if ws is not None:
            ws.close()
        if not channel or self.account is None or not self.account.logged_in:
            return
        threading.Thread(
            target=self._run, args=(session, channel), daemon=True, name="twitch-automod"
        ).start()

    def stop(self) -> None:
        self.connect("")

    def _alive(self, session: int) -> bool:
        return session == self._session

    def _say(self, session: int, text: str) -> None:
        if self._alive(session):
            self.emit({"src": "twitch", "kind": "notice", "text": text})

    def _tip_once(self, session: int, key: str, text: str) -> None:
        # One tip per login and problem, not one per reconnect.
        token_key = (self.account.login if self.account else "", key)
        if token_key in self._tipped:
            return
        self._tipped.add(token_key)
        self._say(session, text)

    def _sleep(self, session: int, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._alive(session) and time.monotonic() < end:
            time.sleep(0.5)

    def _run(self, session: int, channel: str) -> None:
        creds: Optional[Creds] = None
        delay = 2.0
        announced = False
        while self._alive(session):
            try:
                if creds is None:
                    creds = self._setup(session, channel)
                    if creds is None:
                        return
                    with self._lock:
                        if not self._alive(session):
                            return
                        self._creds = creds
                result = self._serve(session, creds, announce=not announced)
                if result == "stop":
                    return
                announced = True
                delay = 2.0
            except HTTPError as exc:
                if exc.code in (400, 401):
                    creds = None  # token may have been renewed: set up again
            except Exception:  # noqa: BLE001
                log.debug("AutoMod connection dropped", exc_info=True)
            self._sleep(session, delay)
            delay = min(60.0, delay * 2)

    def _setup(self, session: int, channel: str) -> Optional[Creds]:
        token = self.account.fresh_token(force=True)
        if not token:
            return None
        if api.AUTOMOD_SCOPE not in self.account.scopes:
            self._tip_once(
                session,
                "scope",
                "Tip: to approve AutoMod messages here, log in again so Twitch gives the AutoMod permission.",
            )
            return None
        client_id, mod_id = self.account.client_id, self.account.user_id
        if not client_id or not mod_id:
            return None
        if self.account.login == channel:
            broadcaster_id = mod_id
        else:
            broadcaster_id = api.user_id(channel, token, client_id)
            if not broadcaster_id:
                return None
        return token, client_id, mod_id, broadcaster_id

    def _subscribe(self, creds: Creds, session_id: str, sub_type: str) -> int:
        token, client_id, mod_id, broadcaster_id = creds
        status = 0
        for version in ("2", "1"):
            status, _ = api.helix(
                "POST",
                "/eventsub/subscriptions",
                token,
                client_id,
                {
                    "type": sub_type,
                    "version": version,
                    "condition": {"broadcaster_user_id": broadcaster_id, "moderator_user_id": mod_id},
                    "transport": {"method": "websocket", "session_id": session_id},
                },
            )
            if status in (200, 202, 409):
                return 202
            if status != 400:
                return status
        return status

    def _serve(self, session: int, creds: Creds, *, announce: bool) -> str:
        url = EVENTSUB_WS_URL
        subscribe = True
        ws: Optional[WebSocketClient] = None
        try:
            while self._alive(session):
                fresh = WebSocketClient(url, timeout=15)
                with self._lock:
                    if not self._alive(session):
                        fresh.close()
                        return "stop"
                    old, ws = ws, fresh
                    self._ws = ws
                welcome = json.loads(ws.recv_text() or "{}")
                if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                    raise OSError("no welcome from EventSub")
                info = welcome.get("payload", {}).get("session", {}) or {}
                if old is not None:
                    old.close()  # moved to the new address; subscriptions carry over
                ws.settimeout(float(info.get("keepalive_timeout_seconds") or 10) + 10)
                if subscribe:
                    session_id = str(info.get("id") or "")
                    status = self._subscribe(creds, session_id, "automod.message.hold")
                    if status == 403:
                        self._tip_once(
                            session, "mod", "AutoMod: only the streamer or a mod of this channel can see held messages."
                        )
                        return "stop"
                    if status == 401:
                        raise HTTPError(url, 401, "unauthorized", None, None)  # type: ignore[arg-type]
                    if status != 202:
                        raise OSError(f"AutoMod subscribe failed ({status})")
                    # Optional: lets a card go away when it's handled somewhere else.
                    self._subscribe(creds, session_id, "automod.message.update")
                    subscribe = False
                    if announce:
                        self._say(session, "AutoMod connected: held messages will show here")
                        announce = False
                next_url = self._pump(session, ws)
                if not next_url:
                    return "closed"
                url = next_url
            return "stop"
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws = None
            if ws is not None:
                ws.close()

    def _pump(self, session: int, ws: WebSocketClient) -> Optional[str]:
        """Read events until the socket closes (None) or Twitch moves us (new URL)."""
        while self._alive(session):
            raw = ws.recv_text()
            if raw is None:
                return None
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            meta = msg.get("metadata") or {}
            kind = meta.get("message_type")
            payload = msg.get("payload") or {}
            if kind == "notification":
                sub_type = meta.get("subscription_type") or (payload.get("subscription") or {}).get("type")
                event = payload.get("event") or {}
                if sub_type == "automod.message.hold":
                    held = parse_automod_hold(event)
                    if held is not None and self._alive(session):
                        self.emit({"src": "twitch", "kind": "automod_hold", "held": held})
                elif sub_type == "automod.message.update":
                    msg_id = str(event.get("message_id") or "")
                    if msg_id and self._alive(session):
                        self.emit({"src": "twitch", "kind": "automod_done", "id": msg_id})
            elif kind == "session_reconnect":
                return str((payload.get("session") or {}).get("reconnect_url") or "") or None
        return None

    def resolve(self, message_id: str, allow: bool) -> None:
        """Allow or deny a held message. The answer comes back as automod_result."""
        creds = self._creds

        def work() -> None:
            result: dict[str, Any] = {"id": message_id, "allow": allow, "ok": False, "error": ""}
            if creds is None:
                result["error"] = "AutoMod isn't connected right now."
            else:
                token, client_id, mod_id, _ = creds
                try:
                    status, data = api.helix(
                        "POST",
                        "/moderation/automod/message",
                        token,
                        client_id,
                        {"user_id": mod_id, "msg_id": message_id, "action": "ALLOW" if allow else "DENY"},
                    )
                except (OSError, ValueError) as exc:
                    status, data = 0, {"message": str(exc)}
                if status in (200, 204):
                    result["ok"] = True
                elif status == 400:
                    result["error"] = "it was already handled or it expired."
                elif status == 404:
                    result["error"] = "Twitch can't find that message anymore."
                elif status in (401, 403):
                    result["error"] = "your login can't do that. Log in again."
                else:
                    detail = str((data or {}).get("message") or "") if isinstance(data, dict) else ""
                    result["error"] = detail or "Twitch didn't answer. Try again."
            self.emit({"src": "twitch", "kind": "automod_result", "result": result})

        threading.Thread(target=work, daemon=True, name="twitch-automod-resolve").start()
