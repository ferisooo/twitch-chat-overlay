"""Twitch chat over IRC. Reading needs no login; sending does."""

from __future__ import annotations

import logging
import random
import re
import socket
import ssl
import threading
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 30.0

Emit = Callable[[dict[str, Any]], None]


# ----- parsing (pure functions, unit tested) -----

def parse_irc(line: str) -> tuple[dict[str, str], str, list[str]]:
    tags: dict[str, str] = {}
    if line.startswith("@"):
        tag_str, _, line = line[1:].partition(" ")
        for part in tag_str.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                tags[k] = v
            elif part:
                tags[part] = ""

    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")

    parts = line.split(" ")
    command = parts[0]
    params: list[str] = []
    i = 1
    while i < len(parts):
        if parts[i].startswith(":"):
            params.append(" ".join(parts[i:])[1:])
            break
        if parts[i]:
            params.append(parts[i])
        i += 1

    if prefix and "!" in prefix:
        tags.setdefault("_nick", prefix.split("!", 1)[0])
    return tags, command, params


_TAG_ESCAPES = {"s": " ", ":": ";", "\\": "\\", "r": "", "n": " "}


def twitch_unescape(value: str) -> str:
    # One pass, so an escaped backslash followed by "s" stays "\s", not "\ ".
    return re.sub(r"\\(.)", lambda m: _TAG_ESCAPES.get(m.group(1), m.group(1)), value or "")


def twitch_sub_plan_label(plan: str) -> str:
    return {"Prime": "Prime", "1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(
        plan, plan or "sub"
    )


def format_twitch_usernotice(tags: dict[str, str], trailing: str) -> Optional[str]:
    """Turn a USERNOTICE (sub, raid, announcement...) into one line, or None."""
    kind = (tags.get("msg-id") or "").lower()
    name = tags.get("display-name") or tags.get("login") or tags.get("_nick") or "Someone"
    plan = twitch_sub_plan_label(tags.get("msg-param-sub-plan", ""))
    system = twitch_unescape(tags.get("system-msg", "")).strip()
    extra = trailing.strip()

    def with_extra(line: str) -> str:
        return f"{line} — {extra}" if extra else line

    if kind in ("sub", "resub"):
        months = tags.get("msg-param-cumulative-months") or tags.get("msg-param-months") or ""
        if system:
            return with_extra(system)
        if kind == "sub":
            return with_extra(f"{name} subscribed ({plan})")
        if months:
            return with_extra(f"{name} resubscribed for {months} months ({plan})")
        return with_extra(f"{name} resubscribed ({plan})")

    if kind == "subgift":
        if system:
            return system
        recipient = (
            tags.get("msg-param-recipient-display-name")
            or tags.get("msg-param-recipient-user-name")
            or "someone"
        )
        months = tags.get("msg-param-gift-months") or tags.get("msg-param-months") or ""
        if months and months != "1":
            return f"{name} gifted {months} months of {plan} to {recipient}"
        return f"{name} gifted a {plan} sub to {recipient}"

    if kind in ("submysterygift", "communitypayforward"):
        if system:
            return system
        count = tags.get("msg-param-mass-gift-count") or tags.get("msg-param-sender-count") or ""
        if count:
            return f"{name} is gifting {count} {plan} subs to the community"
        return f"{name} is gifting subs to the community"

    if kind in ("giftpaidupgrade", "primepaidupgrade", "anongiftpaidupgrade", "standardpayforward"):
        return system or f"{name} continued their gifted sub ({plan})"

    if kind == "viewermilestone":
        return system or None

    if kind == "raid":
        raider = tags.get("msg-param-displayName") or tags.get("msg-param-login") or name
        viewers = tags.get("msg-param-viewerCount") or ""
        if system:
            return f"RAID · {system}"
        if viewers:
            return f"RAID · {raider} is raiding with {viewers} viewers"
        return f"RAID · {raider} is raiding"

    if kind == "unraid":
        return system or f"{name} cancelled the raid"

    if kind == "announcement":
        if extra:
            return f"ANNOUNCEMENT · {name}: {extra}"
        if system:
            return f"ANNOUNCEMENT · {system}"
        return None

    if system:
        return with_extra(system)
    return None


def twitch_index_to_py(text: str, index: int) -> int:
    """Twitch counts emote positions in UTF-16 units; Python counts code points."""
    i = 0
    units = 0
    while units < index and i < len(text):
        units += 2 if ord(text[i]) > 0xFFFF else 1
        i += 1
    return i


def parse_emote_ranges(emotes_tag: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    if not emotes_tag:
        return ranges
    for part in emotes_tag.split("/"):
        if ":" not in part:
            continue
        eid, positions = part.split(":", 1)
        for pos in positions.split(","):
            if "-" not in pos:
                continue
            a, b = pos.split("-", 1)
            try:
                ranges.append((int(a), int(b), eid))
            except ValueError:
                continue
    ranges.sort(key=lambda r: r[0])
    return ranges


def message_segments(text: str, emotes_tag: str) -> list[tuple[str, Optional[str]]]:
    """Split a chat line into (text, None) and (emote name, emote id) parts."""
    ranges = parse_emote_ranges(emotes_tag)
    if not ranges:
        return [(text, None)] if text else []
    segs: list[tuple[str, Optional[str]]] = []
    cursor = 0
    for start_u, end_u, eid in ranges:
        start = twitch_index_to_py(text, start_u)
        end = twitch_index_to_py(text, end_u + 1)
        if start < cursor:  # overlapping or broken ranges: skip
            continue
        if start > cursor:
            segs.append((text[cursor:start], None))
        segs.append((text[start:end], eid))
        cursor = end
    if cursor < len(text):
        segs.append((text[cursor:], None))
    return segs


def irc_event(tags: dict[str, str], command: str, params: list[str]) -> Optional[dict[str, Any]]:
    """What an IRC line means for the overlay, as a hub event (or None)."""
    if command == "PRIVMSG" and len(params) >= 2:
        text = params[1]
        action = text.startswith("\x01ACTION ") and text.endswith("\x01")
        if action:
            text = text[8:-1]
        login = tags.get("login") or tags.get("_nick") or ""
        return {
            "src": "twitch",
            "kind": "chat",
            "name": tags.get("display-name") or login or "anon",
            "login": login.lower(),
            "color": tags.get("color") or "",
            "text": text,
            "emotes": tags.get("emotes", ""),
            "action": action,
            "id": tags.get("id", ""),
            "user_id": tags.get("user-id", ""),
        }
    if command == "USERNOTICE":
        trailing = params[-1] if len(params) >= 2 else ""
        line = format_twitch_usernotice(tags, trailing)
        return {"src": "twitch", "kind": "event", "text": line} if line else None
    if command == "ROOMSTATE" and tags.get("room-id"):
        return {"src": "twitch", "kind": "room", "room_id": tags["room-id"]}
    if command == "CLEARMSG":
        target = tags.get("target-msg-id", "")
        return {"src": "twitch", "kind": "delete", "id": target} if target else None
    if command == "CLEARCHAT":
        if len(params) >= 2 and params[1]:
            return {
                "src": "twitch",
                "kind": "clear_user",
                "login": params[1].lower(),
                "user_id": tags.get("target-user-id", ""),
            }
        return {"src": "twitch", "kind": "clear_all"}
    return None


# ----- the connection -----

class TwitchIRC:
    """Keeps one Twitch chat connection alive and reconnects when it drops.

    `account` gives the login: `account.login` and `account.fresh_token()`.
    Without a login it joins anonymously (read only).
    """

    def __init__(self, emit: Emit, account: Any = None) -> None:
        self.emit = emit
        self.account = account
        self._sock: Optional[ssl.SSLSocket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._channel = ""
        self._nick = ""
        self.authed = False
        self._logging_in = False
        self._no_retry = False
        self._got_welcome = False

    @property
    def channel(self) -> str:
        return self._channel

    def connect(self, channel: str) -> None:
        channel = str(channel or "").strip().lstrip("#").lower()
        if not channel:
            self.emit({"src": "twitch", "kind": "error", "text": "Enter a Twitch channel name."})
            return
        self.disconnect()
        stop = threading.Event()  # fresh per connection, so an old thread stays stopped
        self._stop = stop
        self._channel = channel
        self._no_retry = False
        self._thread = threading.Thread(
            target=self._run, args=(channel, stop), daemon=True, name="twitch-irc"
        )
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        self.authed = False
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.5)
        self._thread = None

    def send_chat(self, text: str) -> str:
        """Send a chat line. Returns "" when sent, or a short reason it wasn't."""
        text = re.sub(r"[\r\n]+", " ", str(text or "")).strip()[:500]
        if not text:
            return "Type a message first."
        if not self._channel or self._sock is None:
            return "Twitch is not connected."
        if not self.authed:
            if self._logging_in:
                return "Still connecting to Twitch…"
            return "Log in to send Twitch chat."
        if text.lower().startswith("/me ") and len(text) > 4:
            payload = f"PRIVMSG #{self._channel} :\x01ACTION {text[4:]}\x01"
        else:
            payload = f"PRIVMSG #{self._channel} :{text}"
        try:
            self._send(payload)
        except OSError:
            return "Couldn't send. The Twitch connection dropped; try again in a moment."
        return ""

    def _send(self, msg: str) -> None:
        with self._lock:
            if self._sock is None:
                return
            self._sock.sendall((msg + "\r\n").encode("utf-8"))

    def _login(self) -> tuple[str, str]:
        account = self.account
        if account is None:
            return "", ""
        try:
            token = account.fresh_token() or ""
        except Exception:  # noqa: BLE001
            log.exception("Couldn't refresh the Twitch login")
            token = ""
        nick = str(getattr(account, "login", "") or "")
        return (nick, token) if nick and token else ("", "")

    def _run(self, channel: str, stop: threading.Event) -> None:
        delay = RECONNECT_MIN_S
        while not stop.is_set():
            self._got_welcome = False
            sock: Optional[ssl.SSLSocket] = None
            try:
                nick, token = self._login()
                self._logging_in = bool(token)
                raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=IRC_HOST)
                sock.settimeout(1.0)
                with self._lock:
                    if stop.is_set():
                        break
                    self._sock = sock
                self._nick = nick
                self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                if nick and token:
                    self._send(f"PASS oauth:{token}")
                    self._send(f"NICK {nick}")
                else:
                    self._send("PASS SCHMOOPIIE")
                    self._send(f"NICK justinfan{random.randint(10000, 99999)}")
                self._send(f"JOIN #{channel}")

                buf = ""
                while not stop.is_set() and not self._no_retry:
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    buf += data.decode("utf-8", errors="replace")
                    while "\r\n" in buf:
                        line, buf = buf.split("\r\n", 1)
                        if line:
                            self._handle(line)
            except Exception:  # noqa: BLE001
                if not stop.is_set():
                    self.emit({"src": "twitch", "kind": "status", "text": "Connection lost. Reconnecting…"})
            finally:
                with self._lock:
                    if sock is not None and self._sock is sock:
                        self._sock = None
                        self.authed = False
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                self._logging_in = False

            if stop.is_set() or self._no_retry:
                break
            if self._got_welcome:
                delay = RECONNECT_MIN_S
            self.emit({"src": "twitch", "kind": "status", "text": f"Reconnecting in {int(delay)}s…"})
            if stop.wait(delay):
                break
            delay = min(RECONNECT_MAX_S, max(RECONNECT_MIN_S, delay) * 2)

        if self._stop is stop:
            self.emit({"src": "twitch", "kind": "status", "text": "Disconnected"})

    def _drop_socket(self) -> None:
        with self._lock:
            sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _handle(self, line: str) -> None:
        tags, command, params = parse_irc(line)

        if command == "PING":
            self._send(f"PONG :{params[0] if params else ''}")
            return
        if command == "001":
            self._got_welcome = True
            self.authed = bool(self._nick and self._logging_in)
            self._logging_in = False
            who = f" as {self._nick}" if self.authed else ""
            self.emit({"src": "twitch", "kind": "status", "text": f"Connected to #{self._channel}{who}"})
            return
        if command == "RECONNECT":
            self.emit({"src": "twitch", "kind": "status", "text": "Twitch asked to reconnect…"})
            self._drop_socket()
            return
        if command in ("GLOBALUSERSTATE", "USERSTATE"):
            name = tags.get("display-name") or self._nick
            if name:
                self.emit({"src": "twitch", "kind": "self", "name": name, "color": tags.get("color", "")})
            return
        if command == "NOTICE" and params:
            msg = params[-1]
            low = msg.lower()
            if "authentication failed" in low or "login unsuccessful" in low or "improperly formatted auth" in low:
                self.authed = False
                self._no_retry = True
                self.emit({"src": "twitch", "kind": "error", "text": "Twitch login failed. Log in again."})
                self._drop_socket()
                return
            self.emit({"src": "twitch", "kind": "notice", "text": msg})
            return
        event = irc_event(tags, command, params)
        if event is not None:
            self.emit(event)
