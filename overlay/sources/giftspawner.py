"""Channel point redeems from Gift Spawner's local event stream.

Gift Spawner (a separate app on the same PC) sends an `activity` event on
http://127.0.0.1:<port>/api/events for every redeem it handles. If it isn't
running, this keeps trying quietly in the background.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from typing import Any, Callable, Optional

RETRY_MIN_S = 5.0
RETRY_MAX_S = 30.0
IDLE_S = 600.0  # reconnect if the stream says nothing this long
Emit = Callable[[dict[str, Any]], None]


def parse_points_redeem(payload: Any) -> Optional[dict[str, Any]]:
    """A channel point redeem from an `activity` event, or None.

    Only redeems that actually did something (status spawned or partial)
    count. Gift Spawner's own Test button sends kind "test".
    """
    if not isinstance(payload, dict):
        return None
    entry = payload.get("entry")
    if not isinstance(entry, dict):
        return None
    gift = entry.get("gift") if isinstance(entry.get("gift"), dict) else {}
    cost = gift.get("points")
    has_cost = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    kind = entry.get("kind")
    if kind == "points":
        test = False
    elif kind == "test" and has_cost:
        test = True
    else:
        return None
    if entry.get("status") not in ("spawned", "partial"):
        return None
    viewer = entry.get("viewer") if isinstance(entry.get("viewer"), dict) else {}
    return {
        "id": str(entry.get("id") or ""),
        "login": str(viewer.get("uniqueId") or "").strip(),
        "name": str(viewer.get("nickname") or "").strip(),
        "reward": str(gift.get("name") or "").strip() or "a reward",
        "cost": int(cost) if has_cost else None,
        "test": test,
    }


class GiftSpawnerEvents:
    def __init__(self, emit: Emit, port: int) -> None:
        self.emit = emit
        self.port = port
        self._shown: dict[str, None] = {}
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="gift-spawner")
            self._thread.start()

    def _run(self) -> None:
        delay = RETRY_MIN_S
        announced = False
        while True:
            connected = False
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.request("GET", "/api/events", headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
                resp = conn.getresponse()
                if resp.status != 200:
                    raise OSError(f"HTTP {resp.status}")
                connected = True
                delay = RETRY_MIN_S
                if conn.sock is not None:
                    conn.sock.settimeout(IDLE_S)
                if not announced:
                    announced = True
                    self.emit({"src": "twitch", "kind": "notice", "text": "Gift Spawner found: channel point redeems will show here"})
                self._read_stream(resp)
            except (OSError, http.client.HTTPException):
                if not connected:
                    announced = False
            finally:
                conn.close()
            if connected:
                time.sleep(1.0)  # stream ended or went quiet: try again soon
                continue
            time.sleep(delay)
            delay = min(RETRY_MAX_S, delay * 2)

    def _read_stream(self, resp: http.client.HTTPResponse) -> None:
        event, data = "message", []
        while True:
            raw = resp.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if data:
                    self.handle(event, "\n".join(data))
                event, data = "message", []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)

    def handle(self, event: str, data: str) -> None:
        # The "hello" event replays older activity; only new redeems are shown.
        if event != "activity":
            return
        try:
            payload = json.loads(data)
        except ValueError:
            return
        info = parse_points_redeem(payload)
        if info is None:
            return
        key = info["id"]
        if key:
            if key in self._shown:  # resent when its result changes: show once
                return
            self._shown[key] = None
            if len(self._shown) > 500:
                for old in list(self._shown)[:250]:
                    self._shown.pop(old, None)
        elif isinstance(payload, dict) and payload.get("update"):
            return
        self.emit({"src": "twitch", "kind": "points", "info": info})
