"""TikTok LIVE chat through TikTokLive (an unofficial library).

TikTokLive is optional: without it, only Twitch works. Everything TikTok
does stays in this file, so when TikTok changes something and this breaks,
Twitch keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 30.0
OFFLINE_RETRY_S = 20.0
STALE_S = 45.0
Emit = Callable[[dict[str, Any]], None]

# time.monotonic() of the last TikTok push frame of any kind, heartbeats included.
_last_frame_at = 0.0

TIKTOK_IDLE_RE = re.compile(
    r"inactiv|no activity|not active|no movement|no motion|"
    r"end (your|this|the) live|live will end|about to end|"
    r"haven't moved|been still|"
    r"无操作|未活动|没有活动|即将结束|不活跃|即将关闭",
    re.I,
)

# TikTok app-only shortcodes arrive as "[laugh]" etc. Map to the closest emoji.
TIKTOK_EMOJI_CODES: dict[str, str] = {
    "smile": "😁", "happy": "🙂", "angry": "😡", "cry": "😭", "embarrassed": "😳",
    "surprised": "😮", "wronged": "🥺", "shout": "🗣️", "flushed": "😊", "yummy": "😋",
    "complacent": "😏", "drool": "🤤", "scream": "😱", "weep": "😢", "speechless": "😶",
    "funnyface": "🤪", "laughwithtears": "😂", "wicked": "😈", "facewithrollingeyes": "🙄",
    "sulk": "😒", "thinking": "🤔", "lovely": "🥰", "greedy": "🤑", "wow": "😲",
    "tears": "🥲", "look": "👀", "smileface": "😄", "blink": "😉", "disdain": "😤",
    "astonish": "😧", "rage": "🤬", "cool": "😎", "excited": "🤩", "proud": "😌",
    "smileface2": "😃", "evil": "👿", "angel": "😇", "laugh": "😝", "pridelaugh": "😆",
    "nap": "😴", "loveface": "😍", "awkward": "😅", "bigsmile": "😁", "hehe": "🤭",
    "joyful": "😹", "stun": "😵", "shock": "😱", "lmao": "🤣",
}
_CODE_RE = re.compile(r"\[([A-Za-z0-9_]+)\]")


# ----- text helpers (pure, unit tested) -----

def expand_tiktok_emojis(text: str) -> str:
    """Turn TikTok [laugh]-style codes into emoji."""
    return _CODE_RE.sub(lambda m: TIKTOK_EMOJI_CODES.get(m.group(1).lower(), m.group(0)), text)


def tiktok_user_name(user: Any) -> str:
    if user is None:
        return "anon"
    return str(
        getattr(user, "nickname", None)
        or getattr(user, "nick_name", None)
        or getattr(user, "display_id", None)
        or getattr(user, "unique_id", None)
        or "anon"
    )


def tiktok_emote_label(emote: Any) -> str:
    if emote is None:
        return ""
    eid = str(getattr(emote, "emote_id", "") or "").strip()
    if not eid:
        return "[sticker]"
    short = eid.split("-")[-1].split("_")[-1]
    mapped = TIKTOK_EMOJI_CODES.get(eid.lower()) or TIKTOK_EMOJI_CODES.get(short.lower())
    if mapped:
        return mapped
    if eid.startswith("[") and eid.endswith("]"):
        return expand_tiktok_emojis(eid)
    return expand_tiktok_emojis(f"[{short}]") if short else f"[{eid}]"


def tiktok_comment_text(event: Any) -> str:
    raw = getattr(event, "content", None)
    if raw is None:
        raw = getattr(event, "comment", None)
    text = expand_tiktok_emojis(str(raw or ""))
    emotes = list(getattr(event, "emotes", None) or [])
    if emotes:
        # TikTok stores sticker positions as indices into the comment string.
        chars = list(text)
        for item in sorted(emotes, key=lambda e: int(getattr(e, "index", 0) or 0), reverse=True):
            label = tiktok_emote_label(getattr(item, "emote", None))
            if not label:
                continue
            idx = int(getattr(item, "index", 0) or 0)
            if 0 <= idx < len(chars) and not chars[idx].strip():
                chars[idx] = label
            elif 0 <= idx <= len(chars):
                chars.insert(idx, label)
            else:
                chars.append(label)
        text = "".join(chars)
    extra = [lbl for lbl in (tiktok_emote_label(e) for e in getattr(event, "emote_list", None) or []) if lbl]
    if extra:
        text = (text + " " + " ".join(extra)).strip()
    return text.strip()


def tiktok_text_blob(obj: Any, depth: int = 0) -> str:
    """Pull readable strings out of TikTok proto toasts / dialogs."""
    if obj is None or depth > 6:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, (int, float, bool, bytes)):
        return ""
    chunks: list[str] = []
    for attr in (
        "tips", "key", "default_pattern", "default", "display_text", "string_value", "content",
        "title", "sub_title", "advice_action_text", "default_action_text", "close_action_text",
    ):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and val.strip():
            chunks.append(val.strip())
        elif val is not None and not isinstance(val, (int, float, bool, bytes)):
            nested = tiktok_text_blob(val, depth + 1)
            if nested:
                chunks.append(nested)
    for attr in (
        "toast", "float_text", "dialog", "perception_dialog", "perception_audience_text",
        "punish_info", "sheet", "pieces", "text_piece_list", "notice",
    ):
        val = getattr(obj, attr, None)
        items = val if isinstance(val, (list, tuple)) else [val]
        for item in items:
            nested = tiktok_text_blob(item, depth + 1)
            if nested:
                chunks.append(nested)
    out: list[str] = []
    for chunk in chunks:
        if chunk and chunk not in out:
            out.append(chunk)
    return " ".join(out)


def tiktok_countdown_s(event: Any) -> Optional[int]:
    dialog = getattr(event, "dialog", None) or getattr(event, "perception_dialog", None)
    raws = [
        getattr(event, "end_time", None),
        getattr(dialog, "count_down_time", None) if dialog is not None else None,
        getattr(dialog, "end_time", None) if dialog is not None else None,
    ]
    now = time.time()
    for raw in raws:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        if n > 1_000_000_000_000:
            remain = n / 1000.0 - now
        elif n > 1_000_000_000:
            remain = float(n) - now
        elif n > 10_000:
            remain = n / 1000.0
        else:
            remain = float(n)
        if remain >= 1:
            return int(remain)
    return None


def format_wait(seconds: float) -> str:
    seconds = max(1, int(seconds))
    return f"{round(seconds / 60)} min" if seconds >= 90 else f"{seconds}s"


def rate_limit_wait(exc: BaseException) -> float:
    """Seconds to back off after a sign-server 429 (header can be missing: 5 min)."""
    try:
        wait = float(getattr(exc, "retry_after"))
    except (AttributeError, TypeError, ValueError):
        wait = 300.0
    return min(1800.0, max(60.0, wait))


def tiktok_idle_warning(event: Any) -> Optional[str]:
    blob = tiktok_text_blob(event)
    if not blob or not TIKTOK_IDLE_RE.search(blob):
        return None
    remain = tiktok_countdown_s(event)
    if remain is not None:
        clock = f"{remain // 60}m {remain % 60}s" if remain >= 60 else f"{remain}s"
        return f"TikTok may end this LIVE for no activity — about {clock} left. Move on camera or tap the stream."
    return "TikTok may end this LIVE for no activity. Move or tap the stream."


# ----- TikTokLive fixes (6.6.x) -----

def _patch_ws_uri() -> None:
    """TikTokLive 6.6.x sends fallback WS params unencoded; spaces in the
    User-Agent make the handshake fail, so chat stays empty. 7.x quotes them."""
    try:
        import inspect
        from urllib.parse import quote

        from TikTokLive.client.errors import InitialCursorMissingError, WebsocketURLMissingError
        from TikTokLive.client.ws import ws_connect, ws_utils
    except ImportError:
        return
    original = getattr(ws_utils, "build_webcast_uri", None)
    if original is None:
        return
    try:
        if "quote(" in inspect.getsource(original):
            return
    except (OSError, TypeError):
        pass

    def build_webcast_uri(initial_webcast_response, base_uri_params, base_uri_append_str):  # type: ignore[no-untyped-def]
        if not initial_webcast_response.cursor:
            raise InitialCursorMissingError("Missing cursor in initial fetch response.")
        if not initial_webcast_response.push_server:
            raise WebsocketURLMissingError("No websocket URL received from TikTok.")
        if not initial_webcast_response.route_params:
            raise WebsocketURLMissingError("Websocket parameters missing.")
        uri_params = {
            **{k: quote(str(v), safe="") for k, v in initial_webcast_response.route_params.items() if v},
            **base_uri_params,
        }
        return (
            initial_webcast_response.push_server
            + "?"
            + "&".join(f"{key}={value}" for key, value in uri_params.items())
            + base_uri_append_str
        )

    ws_utils.build_webcast_uri = build_webcast_uri
    ws_connect.build_webcast_uri = build_webcast_uri


def _patch_frame_clock() -> None:
    """TikTokLive drops server heartbeats before they become events, so a
    quiet LIVE looked dead and reconnected every ~50s (burning sign-server
    quota). Note every frame so the watchdog can tell quiet from dead."""
    try:
        from TikTokLive.client.ws import ws_connect
    except ImportError:
        return
    base = getattr(ws_connect, "WebcastPushFrame", None)
    if base is None or getattr(base, "_overlay_clock", False):
        return

    class ClockedPushFrame(base):  # type: ignore[misc, valid-type]
        _overlay_clock = True

        def parse(self, data):  # type: ignore[no-untyped-def]
            global _last_frame_at
            _last_frame_at = time.monotonic()
            return super().parse(data)

    ws_connect.WebcastPushFrame = ClockedPushFrame


_LIB: Optional[dict[str, Any]] = None


def load_library() -> Optional[dict[str, Any]]:
    """Import TikTokLive once. None when it isn't installed."""
    global _LIB
    if _LIB is not None:
        return _LIB or None
    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive import events as ev
    except ImportError:
        _LIB = {}
        return None
    lib: dict[str, Any] = {"client": TikTokLiveClient}
    for name in (
        "CommentEvent", "ConnectEvent", "DisconnectEvent", "EmoteChatEvent", "SubNotifyEvent",
        "LiveEndEvent", "LivePauseEvent", "ControlEvent", "PerceptionEvent", "BarrageEvent",
        "WebsocketResponseEvent",
    ):
        lib[name] = getattr(ev, name, None)
    for patch in (_patch_ws_uri, _patch_frame_clock):
        try:
            patch()
        except Exception:  # noqa: BLE001
            log.exception("TikTokLive patch failed")
    _LIB = lib
    return lib


# ----- the connection -----

class TikTokChat:
    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._session = 0
        self._seen_ids: dict[str, None] = {}
        self._fp_times: dict[str, float] = {}
        self._last_idle_warn = 0.0
        self._last_ws_at = 0.0
        # Kept across connect() calls so Reconnect can't hammer a rate limit.
        self._rate_limited_until = 0.0

    def _say(self, kind: str, text: str) -> None:
        self.emit({"src": "tiktok", "kind": kind, "text": text})

    def connect(self, username: str) -> None:
        if load_library() is None:
            self._say(
                "error",
                "TikTok chat isn't installed. Close the overlay, double-click "
                '"install requirements.bat", pick TikTok or Both, then start it again.',
            )
            return
        username = str(username or "").strip().lstrip("@")
        if not username:
            self._say("error", "Enter a TikTok username.")
            return
        self.disconnect()
        self._seen_ids.clear()
        self._fp_times.clear()
        self._last_idle_warn = 0.0
        self._last_ws_at = 0.0
        session = self._session
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(username, session), daemon=True, name="tiktok")
        self._thread.start()

    def disconnect(self) -> None:
        self._session += 1  # invalidate in-flight handlers right away
        self._stop.set()
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                loop = getattr(client, "_asyncio_loop", None) or getattr(client, "_loop", None)
                if loop is not None and getattr(loop, "is_running", lambda: False)():
                    fut = asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
                    try:
                        fut.result(timeout=2.5)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._thread = None

    # ----- duplicate filters -----
    @staticmethod
    def _msg_id_key(event: Any) -> Optional[str]:
        common = getattr(event, "common", None)
        raw = getattr(common, "msg_id", None) if common is not None else None
        if raw is None or raw is False:
            return None
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return str(raw).strip() or None
        return str(n) if n != 0 else None

    def _remember_id(self, msg_id: str) -> bool:
        if msg_id in self._seen_ids:
            return False
        self._seen_ids[msg_id] = None
        if len(self._seen_ids) > 2000:
            self._seen_ids.pop(next(iter(self._seen_ids)))
        return True

    def _is_repeat_spam(self, name: str, text: str) -> bool:
        """Drop only near-instant echoes of the same line, not later repeats."""
        now = time.monotonic()
        fp = f"{name.casefold()}\0{text}"
        last = self._fp_times.get(fp)
        if last is not None and now - last < 1.5:
            return True
        self._fp_times[fp] = now
        if len(self._fp_times) > 800:
            cutoff = now - 30
            self._fp_times = {k: t for k, t in self._fp_times.items() if t >= cutoff}
        return False

    def _emit_chat(self, session: int, event: Any) -> None:
        if session != self._session:
            return
        try:
            msg_key = self._msg_id_key(event)
            if msg_key is not None and not self._remember_id(msg_key):
                return
            name = tiktok_user_name(getattr(event, "user", None) or getattr(event, "user_info", None))
            text = tiktok_comment_text(event)
            if not text:
                return
            if msg_key is None and self._is_repeat_spam(name, text):
                return
            self.emit({"src": "tiktok", "kind": "chat", "name": name, "text": text})
        except Exception:  # noqa: BLE001 - one bad payload must not kill later comments
            log.debug("Bad TikTok comment", exc_info=True)

    def _warn(self, session: int, msg: str) -> None:
        if session != self._session:
            return
        now = time.monotonic()
        if now - self._last_idle_warn < 25:
            return
        self._last_idle_warn = now
        self._say("status", "Connected — no-activity warning")
        self._say("warn", msg)

    def _on_idle_event(self, session: int, event: Any) -> None:
        try:
            msg = tiktok_idle_warning(event)
        except Exception:  # noqa: BLE001
            return
        if msg:
            self._warn(session, msg)

    def _touch(self) -> None:
        self._last_ws_at = time.monotonic()

    async def _watchdog(self, client: Any, session: int) -> None:
        """TikTok sockets can go silent without a disconnect. Force a reconnect."""
        try:
            while not self._stop.is_set() and session == self._session:
                await asyncio.sleep(10)
                if not self._last_ws_at:
                    continue
                last = max(self._last_ws_at, _last_frame_at)  # heartbeats count
                if time.monotonic() - last < STALE_S:
                    continue
                self._say("status", "TikTok stalled. Reconnecting…")
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                return
        except asyncio.CancelledError:
            return

    def _lookup_room_id(self, lib: dict[str, Any], username: str) -> Optional[int]:
        """TikTok's 'is live' page often calls LIVE Studio rooms offline; ask the API."""
        try:
            from TikTokLive.client.web.routes.fetch_room_id_api import FetchRoomIdAPIRoute
        except ImportError:
            return None
        tmp = lib["client"](unique_id=username)

        async def go() -> Optional[int]:
            data = await FetchRoomIdAPIRoute.fetch_user_room_data(web=tmp.web, unique_id=username)
            raw = ((data.get("data") or {}).get("user") or {}).get("roomId")
            try:
                n = int(raw)
            except (TypeError, ValueError):
                return None
            return n or None

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(go())
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
            asyncio.set_event_loop(None)

    def _wire(self, lib: dict[str, Any], client: Any, session: int, username: str, state: dict[str, Any]) -> None:
        def on(name: str, handler: Callable[[Any], None]) -> None:
            cls = lib.get(name)
            if cls is None:
                return

            async def wrapped(event: Any) -> None:
                handler(event)

            client.on(cls)(wrapped)

        def comment(event: Any) -> None:
            self._touch()
            self._emit_chat(session, event)

        def sub(event: Any) -> None:
            if session != self._session:
                return
            name = tiktok_user_name(getattr(event, "user", None))
            months = int(getattr(event, "sub_month", 0) or 0)
            self._say("event", f"{name} subscribed for {months} months" if months > 1 else f"{name} subscribed")

        def connected(_event: Any) -> None:
            if session != self._session:
                return
            state["live"] = True
            state["since"] = time.monotonic()
            self._touch()
            self._say("status", f"Connected to @{username}")
            task = state.get("watchdog")
            if task is None or task.done():
                state["watchdog"] = asyncio.get_running_loop().create_task(self._watchdog(client, session))

        def control(event: Any) -> None:
            for name in ("LiveEndEvent", "LivePauseEvent"):
                cls = lib.get(name)
                if cls is not None and isinstance(event, cls):
                    return
            self._on_idle_event(session, event)

        def live_end(_event: Any) -> None:
            if session == self._session:
                self._say("status", "LIVE ended")
                self._say("warn", "TikTok ended the LIVE (stream stopped or no activity).")

        def live_pause(_event: Any) -> None:
            if session == self._session:
                self._say("event", "TikTok LIVE paused")

        on("CommentEvent", comment)
        on("EmoteChatEvent", lambda e: self._emit_chat(session, e))
        on("SubNotifyEvent", sub)
        on("ConnectEvent", connected)
        on("WebsocketResponseEvent", lambda _e: self._touch())
        on("PerceptionEvent", lambda e: self._on_idle_event(session, e))
        on("ControlEvent", control)
        on("BarrageEvent", lambda e: self._on_idle_event(session, e))
        on("LiveEndEvent", live_end)
        on("LivePauseEvent", live_pause)

    def _run(self, username: str, session: int) -> None:
        lib = load_library()
        if lib is None:
            return
        unique_id = f"@{username}"
        delay = RECONNECT_MIN_S
        while not self._stop.is_set() and session == self._session:
            hold = self._rate_limited_until - time.monotonic()
            if hold > 0:
                self._say("status", f"TikTok rate limit. Retrying in {format_wait(hold)}…")
                if self._stop.wait(hold) or session != self._session:
                    break
            client = None
            offline = False
            state: dict[str, Any] = {"live": False, "since": 0.0, "watchdog": None}
            self._last_ws_at = 0.0
            try:
                self._say("status", f"Connecting to @{username}…")
                room_id = self._lookup_room_id(lib, username)
                if self._stop.is_set() or session != self._session:
                    break
                client = lib["client"](unique_id=unique_id)
                with self._lock:
                    if session != self._session:
                        break
                    self._client = client
                self._wire(lib, client, session, username, state)
                # Skip the old-comment flood and TikTok's unreliable "is live" check.
                run_kw: dict[str, Any] = {"process_connect_events": False}
                if room_id:
                    run_kw["room_id"] = room_id
                    run_kw["fetch_live_check"] = False
                client.run(**run_kw)
            except Exception as exc:  # noqa: BLE001
                if session != self._session:
                    break
                msg = str(exc)
                if type(exc).__name__ == "SignatureRateLimitError" or "too many connections" in msg.lower():
                    wait_s = rate_limit_wait(exc)
                    self._rate_limited_until = time.monotonic() + wait_s
                    self._say(
                        "notice",
                        "TikTok's connection server is rate-limiting this PC. "
                        f"Trying again in {format_wait(wait_s)}; pressing Connect won't make it sooner.",
                    )
                elif "live" in msg.lower() or "offline" in msg.lower():
                    offline = True
                    self._say("status", f"@{username} offline. Retrying…")
                else:
                    log.info("TikTok dropped: %s", msg)
                    self._say("status", "TikTok dropped. Retrying…")
            finally:
                task = state.get("watchdog")
                if task is not None and not task.done():
                    task.cancel()
                with self._lock:
                    if client is not None and self._client is client:
                        self._client = None

            if self._stop.is_set() or session != self._session:
                break
            lived = time.monotonic() - state["since"] if state["live"] and state["since"] else 0.0
            flapped = state["live"] and lived < 20
            if state["live"] and not flapped:
                delay = RECONNECT_MIN_S
            wait = OFFLINE_RETRY_S if offline else delay
            if flapped:
                wait = max(wait, 8.0)
            self._say("status", f"Reconnecting in {int(wait)}s…")
            if self._stop.wait(wait):
                break
            if not offline:
                delay = min(RECONNECT_MAX_S, max(RECONNECT_MIN_S, delay) * 2)

        if session == self._session:
            self._say("status", "Disconnected")
