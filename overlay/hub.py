"""The middle of the app.

Chat sources post events here from their own threads. The hub turns them
into chat lines, remembers recent lines for each tab, and tells everyone who
listens (the desktop window and the OBS page) what changed. It has no Qt in
it, so it is easy to test.
"""

from __future__ import annotations

import logging
import queue
import re
import time
from collections import deque
from typing import Any, Callable, Optional

from .colors import NameColors
from .model import Message, build_segments, mention_regex
from .settings import Settings

log = logging.getLogger(__name__)

TABS = ("twitch", "tiktok", "merge")
PLATFORMS = ("twitch", "tiktok")
MAX_LINES = 150
AUTOMOD_CARD_S = 30
AUTOMOD_MAX_CARDS = 2
AUTOMOD_MAX_WAITING = 20

# Connection news only updates the status in the title bar.
_LINK_NOISE = (
    "connecting", "connected", "reconnect", "retrying", "offline", "dropped",
    "stalled", "connection lost", "disconnected", "joining", "live ended",
)

Listener = Callable[[str, dict[str, Any]], None]


def is_link_noise(text: str) -> bool:
    low = str(text).lower()
    return any(token in low for token in _LINK_NOISE)


class Hub:
    def __init__(
        self,
        settings: Settings,
        *,
        account: Any = None,
        twitch: Any = None,
        tiktok: Any = None,
        automod: Any = None,
        stream: Any = None,
        emotes: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.account = account
        self.twitch = twitch
        self.tiktok = tiktok
        self.automod = automod
        self.stream = stream
        self.emotes = emotes
        self.clock = clock

        self.inbox: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.listeners: list[Listener] = []
        self.history: dict[str, deque[Message]] = {t: deque(maxlen=MAX_LINES) for t in TABS}
        self.status: dict[str, str] = {p: "idle" for p in PLATFORMS}
        self.tab: str = settings["platform"]
        self.colors = NameColors(settings["name_colors"], settings["name_color_next"])
        self.automod_cards: list[dict[str, Any]] = []
        self.automod_waiting: list[dict[str, Any]] = []
        self.self_name = ""
        self.live_info: Optional[dict[str, str]] = None
        self._next_id = 1
        self._mention_key: tuple[str, ...] = ()
        self._mention_rx: Optional[re.Pattern[str]] = None

    # ----- plumbing -----
    def subscribe(self, listener: Listener) -> None:
        self.listeners.append(listener)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        for listener in list(self.listeners):
            try:
                listener(event, data)
            except Exception:  # noqa: BLE001 - one bad listener must not stop the rest
                log.exception("Listener failed on %s", event)

    def post(self, event: dict[str, Any]) -> None:
        """Thread-safe: sources call this from their own threads."""
        self.inbox.put(event)

    def drain(self, limit: int = 500) -> None:
        """Handle waiting events. Call often from the main thread."""
        for _ in range(limit):
            try:
                event = self.inbox.get_nowait()
            except queue.Empty:
                return
            try:
                self.handle(event)
            except Exception:  # noqa: BLE001
                log.exception("Couldn't handle %r", event.get("kind"))

    def tick(self) -> None:
        """Call about once a second: expires AutoMod cards, saves settings."""
        now = self.clock()
        expired = [c for c in self.automod_cards if not c["busy"] and c["deadline"] <= now]
        if expired:
            for card in expired:
                self.automod_cards.remove(card)
            self._automod_promote()
            self._emit_automod()
        self.persist()

    def persist(self) -> None:
        if self.colors.changed:
            self.colors.changed = False
            self.settings.set("name_colors", dict(self.colors.colors))
            self.settings.set("name_color_next", self.colors.next_index)
        self.settings.save_if_dirty()

    # ----- what listeners can ask for -----
    def display_name(self, alias_key: str, name: str = "", login: str = "") -> str:
        aliases = self.settings["name_aliases"]
        return aliases.get(alias_key) or (aliases.get(login) if login else None) or name

    def render(self, msg: Message) -> dict[str, Any]:
        return msg.to_dict(self.display_name(msg.alias_key, msg.name, msg.login))

    def view_settings(self) -> dict[str, Any]:
        s = self.settings
        return {
            "font_size": s["font_size"],
            "text_backing": s["text_backing"],
            "opacity": s["opacity"],
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "tab": self.tab,
            "status": dict(self.status),
            "settings": self.view_settings(),
            "history": {t: [self.render(m) for m in self.history[t]] for t in TABS},
        }

    def mention_rx(self) -> Optional[re.Pattern[str]]:
        s = self.settings
        key = (s["twitch_nick"], s["channel"], s["tiktok_user"])
        if key != self._mention_key:
            self._mention_key = key
            self._mention_rx = mention_regex(key)
        return self._mention_rx

    # ----- adding lines -----
    def _new_id(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    def _add(self, msg: Message, *, activity: bool = True) -> Message:
        tabs = [msg.platform, "merge"]
        for tab in tabs:
            self.history[tab].append(msg)
        self._emit("message", {"tabs": tabs, "msg": self.render(msg)})
        if activity:
            self._emit("activity", {})
        return msg

    def add_line(self, platform: str, kind: str, text: str, *, activity: bool = True) -> Message:
        return self._add(Message(self._new_id(), platform, kind, text=str(text)), activity=activity)

    def _add_chat(
        self,
        platform: str,
        name: str,
        text: str,
        *,
        emotes: str = "",
        action: bool = False,
        login: str = "",
        twitch_id: str = "",
    ) -> Message:
        name = str(name or "anon")
        alias_key = name.strip().lower()
        lookup = None
        if platform == "twitch" and self.emotes is not None and self.settings["third_party_emotes"]:
            lookup = self.emotes.lookup
        segs, mentioned = build_segments(text, emotes, lookup, self.mention_rx())
        msg = Message(
            self._new_id(),
            platform,
            "action" if action else "chat",
            name=name,
            alias_key=alias_key,
            login=login,
            color=self.colors.color_for(platform, name),
            segments=segs,
            mention=mentioned,
            twitch_id=twitch_id,
        )
        return self._add(msg)

    def _remove(self, match: Callable[[Message], bool]) -> None:
        ids: list[int] = []
        for tab in TABS:
            keep = deque(maxlen=MAX_LINES)
            for m in self.history[tab]:
                if match(m):
                    if m.id not in ids:
                        ids.append(m.id)
                else:
                    keep.append(m)
            self.history[tab] = keep
        if ids:
            self._emit("remove", {"ids": ids})

    def clear_platform(self, platform: str) -> None:
        self._remove(lambda m: m.platform == platform)

    # ----- events from sources -----
    def handle(self, ev: dict[str, Any]) -> None:
        src, kind = ev.get("src"), ev.get("kind")
        if src == "account":
            self._handle_account(kind, ev)
            return
        platform = src if src in PLATFORMS else "twitch"

        if kind == "chat":
            self._add_chat(
                platform,
                ev.get("name", ""),
                ev.get("text", ""),
                emotes=ev.get("emotes", ""),
                action=bool(ev.get("action")),
                login=ev.get("login", ""),
                twitch_id=ev.get("id", ""),
            )
        elif kind == "status":
            self._set_status(platform, ev.get("text", ""))
        elif kind == "event":
            self.add_line(platform, "event", ev.get("text", ""))
        elif kind == "notice":
            self.add_line(platform, "system", ev.get("text", ""), activity=False)
        elif kind == "warn":
            self.add_line(platform, "warn", ev.get("text", ""))
            self._emit("alert", {})
        elif kind == "error":
            self.status[platform] = "error"
            self._emit("status", {"platform": platform, "text": "error"})
            self.add_line(platform, "error", ev.get("text", ""), activity=False)
        elif kind == "points":
            self._add_points(ev.get("info") or {})
        elif kind == "room":
            if self.emotes is not None:
                self.emotes.set_room_async(ev.get("room_id", ""))
        elif kind == "self":
            self.self_name = str(ev.get("name") or "")
        elif kind == "delete":
            target = str(ev.get("id") or "")
            if target:
                self._remove(lambda m: m.platform == "twitch" and m.twitch_id == target)
        elif kind == "clear_user":
            login = str(ev.get("login") or "").lower()
            if login:
                self._remove(lambda m: m.platform == "twitch" and (m.login or m.alias_key) == login)
        elif kind == "clear_all":
            self.clear_platform("twitch")
            self.add_line("twitch", "system", "A moderator cleared the chat", activity=False)
        elif kind == "automod_hold":
            self._automod_hold(ev.get("held") or {})
        elif kind == "automod_done":
            self._automod_drop(str(ev.get("id") or ""))
        elif kind == "automod_result":
            self._automod_result(ev.get("result") or {})
        elif kind == "live":
            self.live_info = ev.get("info")
            self._emit("live", {"info": self.live_info})
        elif kind == "went_live":
            info = ev.get("info") or {}
            self.settings.set("announced_stream_id", str(info.get("id") or ""))
            self.settings.save()
            self._emit("went_live", {"info": info})

    def _set_status(self, platform: str, text: str) -> None:
        self.status[platform] = text
        self._emit("status", {"platform": platform, "text": text})
        if text and not is_link_noise(text):
            self.add_line(platform, "system", text, activity=False)

    def _add_points(self, info: dict[str, Any]) -> None:
        login, name = str(info.get("login") or ""), str(info.get("name") or "")
        aliases = self.settings["name_aliases"]
        who = next((aliases[k.lower()] for k in (login, name) if k and k.lower() in aliases), "")
        who = who or name or login
        reward = str(info.get("reward") or "a reward")
        cost = info.get("cost")
        cost_text = f" ({cost:,} points)" if isinstance(cost, int) else ""
        if info.get("test") or not who:
            text = f"✨ Test redeem: {reward}{cost_text}"
        else:
            text = f"✨ {who} redeemed {reward}{cost_text}"
        self.add_line("twitch", "points", text)

    def _handle_account(self, kind: Optional[str], ev: dict[str, Any]) -> None:
        if kind == "login_ok":
            login = str(ev.get("login") or "")
            self.settings.set("twitch_nick", login)
            self.settings.save()
            self.add_line("twitch", "system", f"Logged in to Twitch as {login}", activity=False)
            self._emit("account", self.account_state())
            self.reconnect_twitch()
        elif kind == "login_failed":
            self._emit("account", {**self.account_state(), "error": str(ev.get("text") or "")})
        elif kind == "device_code":
            self._emit("account", {**self.account_state(), "device": ev.get("device") or {}})
        elif kind == "expired":
            self.add_line(
                "twitch", "error", "Your Twitch login expired. Log in again to send chat.", activity=False
            )
            self._emit("account", self.account_state())

    # ----- AutoMod -----
    def _automod_card(self, held: dict[str, Any]) -> dict[str, Any]:
        login = str(held.get("login") or "")
        alias = self.settings["name_aliases"].get(login) if login else None
        return {
            "id": str(held.get("id") or ""),
            "name": alias or str(held.get("name") or "someone"),
            "text": str(held.get("text") or ""),
            "why": str(held.get("why") or ""),
            "deadline": self.clock() + AUTOMOD_CARD_S,
            "busy": False,
        }

    def _emit_automod(self) -> None:
        self._emit(
            "automod",
            {"cards": [dict(c) for c in self.automod_cards], "waiting": len(self.automod_waiting)},
        )

    def _automod_hold(self, held: dict[str, Any]) -> None:
        msg_id = str(held.get("id") or "")
        if not msg_id:
            return
        if any(c["id"] == msg_id for c in self.automod_cards):
            return
        if any(str(w.get("id")) == msg_id for w in self.automod_waiting):
            return
        if len(self.automod_cards) < AUTOMOD_MAX_CARDS:
            self.automod_cards.append(self._automod_card(held))
        else:
            self.automod_waiting.append(held)
            del self.automod_waiting[:-AUTOMOD_MAX_WAITING]
        self._emit_automod()
        self._emit("activity", {})

    def _automod_promote(self) -> None:
        while self.automod_waiting and len(self.automod_cards) < AUTOMOD_MAX_CARDS:
            self.automod_cards.append(self._automod_card(self.automod_waiting.pop(0)))

    def _automod_drop(self, msg_id: str) -> None:
        before = (len(self.automod_cards), len(self.automod_waiting))
        self.automod_cards = [c for c in self.automod_cards if c["id"] != msg_id]
        self.automod_waiting = [w for w in self.automod_waiting if str(w.get("id")) != msg_id]
        if (len(self.automod_cards), len(self.automod_waiting)) != before:
            self._automod_promote()
            self._emit_automod()

    def _automod_result(self, result: dict[str, Any]) -> None:
        msg_id = str(result.get("id") or "")
        card = next((c for c in self.automod_cards if c["id"] == msg_id), None)
        if card is None:
            return
        if not result.get("ok"):
            verb = "allow" if result.get("allow") else "deny"
            self.add_line(
                "twitch",
                "system",
                f"Couldn't {verb} {card['name']}'s message: {result.get('error') or 'try again.'}",
                activity=False,
            )
        self._automod_drop(msg_id)

    # ----- commands from the window -----
    def set_tab(self, tab: str) -> None:
        if tab not in TABS:
            return
        self.tab = tab
        self.settings.set("platform", tab)
        self._emit("tab", {"tab": tab})

    def connect(self, platform: str, name: str) -> None:
        name = str(name or "").strip()
        if platform == "twitch":
            name = name.lstrip("#").lower()
            self.settings.set("channel", name)
            self.settings.save()
            self.clear_platform("twitch")
            if self.emotes is not None:
                self.emotes.set_room_async("")
            if self.twitch is not None:
                self.twitch.connect(name)
            self._restart_twitch_extras()
        elif platform == "tiktok":
            name = name.lstrip("@")
            self.settings.set("tiktok_user", name)
            self.settings.save()
            self.clear_platform("tiktok")
            if self.tiktok is not None:
                self.tiktok.connect(name)

    def connect_saved(self) -> None:
        if self.settings["channel"]:
            self.connect("twitch", self.settings["channel"])
        if self.settings["tiktok_user"]:
            self.connect("tiktok", self.settings["tiktok_user"])

    def reconnect_twitch(self) -> None:
        if self.settings["channel"] and self.twitch is not None:
            self.twitch.connect(self.settings["channel"])
        self._restart_twitch_extras()

    def _restart_twitch_extras(self) -> None:
        channel = self.settings["channel"]
        logged_in = bool(self.account is not None and self.account.logged_in)
        if self.automod is not None:
            if channel and logged_in:
                self.automod.connect(channel)
            else:
                self.automod.stop()
        if self.stream is not None:
            self.stream.set_channel(channel if logged_in else "")
        if not logged_in:
            self.automod_cards.clear()
            self.automod_waiting.clear()
            self._emit_automod()

    def send_chat(self, text: str) -> str:
        """Send to Twitch chat. Returns "" when sent, or why it wasn't."""
        if self.twitch is None:
            return "Twitch is not connected."
        err = self.twitch.send_chat(text)
        if err:
            return err
        # Twitch doesn't echo your own messages back, so show it here.
        text = re.sub(r"[\r\n]+", " ", str(text)).strip()
        action = text.lower().startswith("/me ") and len(text) > 4
        body = text[4:].strip() if action else text
        name = self.self_name or (self.account.login if self.account else "") or "me"
        self._add_chat("twitch", name, body, action=action, login=name.lower())
        return ""

    def rename(self, key: str, alias: str, original: str = "") -> None:
        key = str(key or "").strip().lower()
        if not key:
            return
        alias = str(alias or "").strip()
        aliases = dict(self.settings["name_aliases"])
        if not alias or alias.lower() == key:
            aliases.pop(key, None)
            alias = ""
        else:
            aliases[key] = alias
        self.settings.set("name_aliases", aliases)
        self.settings.save()
        self._emit("alias", {"key": key, "show": alias})
        original = original or key
        text = f"Renamed {original} → {alias}" if alias else f"Cleared nickname for {original}"
        tab = self.tab if self.tab in PLATFORMS else "twitch"
        self.add_line(tab, "system", text, activity=False)

    def automod_resolve(self, msg_id: str, allow: bool) -> None:
        card = next((c for c in self.automod_cards if c["id"] == msg_id), None)
        if card is None or card["busy"]:
            return
        card["busy"] = True
        card["allow"] = allow
        self._emit_automod()
        if self.automod is not None:
            self.automod.resolve(msg_id, allow)

    def update_settings(self, values: dict[str, Any]) -> None:
        self.settings.update(values)
        if "third_party_emotes" in values and self.emotes is not None:
            self.emotes.enabled = self.settings["third_party_emotes"]
        self._emit("settings", self.view_settings())

    def account_state(self) -> dict[str, Any]:
        acc = self.account
        return {
            "logged_in": bool(acc is not None and acc.logged_in),
            "login": (acc.login if acc is not None else "") or "",
        }

    def logout(self) -> None:
        if self.account is not None:
            self.account.logout()
        self.settings.set("twitch_nick", "")
        self.settings.save()
        self.add_line("twitch", "system", "Logged out of Twitch", activity=False)
        self._emit("account", self.account_state())
        self.reconnect_twitch()

    def shutdown(self) -> None:
        for source in (self.twitch, self.tiktok):
            if source is not None:
                try:
                    source.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        if self.automod is not None:
            try:
                self.automod.stop()
            except Exception:  # noqa: BLE001
                pass
        self.persist()
