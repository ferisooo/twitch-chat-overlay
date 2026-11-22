"""Emote pictures: Twitch's own, plus BTTV, FFZ and 7TV.

Twitch tells us where its emotes are in each message. BTTV, FFZ and 7TV
emotes are just words, so we download each channel's list and match words.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

USER_AGENT = "live-chat-overlay/2.0"


@dataclass(frozen=True)
class Emote:
    key: str  # unique id, e.g. "tw:25" or "7tv:abc"
    name: str
    url: str  # picture for the desktop window (png/gif/webp)
    url_hd: str  # sharper picture for OBS (browsers show any format)


def twitch_emote(emote_id: str, name: str) -> Emote:
    base = f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark"
    return Emote(f"tw:{emote_id}", name, f"{base}/2.0", f"{base}/3.0")


def _https(url: str) -> str:
    url = str(url or "")
    return "https:" + url if url.startswith("//") else url


# ----- parsers for each service's JSON (pure, unit tested) -----

def parse_bttv(items: Iterable[Any]) -> list[Emote]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        eid, code = str(item.get("id") or ""), str(item.get("code") or "")
        if eid and code:
            base = f"https://cdn.betterttv.net/emote/{eid}"
            out.append(Emote(f"bttv:{eid}", code, f"{base}/2x", f"{base}/3x"))
    return out


def parse_ffz_sets(sets: Any, only: Optional[Iterable[Any]] = None) -> list[Emote]:
    out = []
    if not isinstance(sets, dict):
        return out
    wanted = {str(s) for s in only} if only is not None else None
    for set_id, data in sets.items():
        if wanted is not None and str(set_id) not in wanted:
            continue
        for item in (data or {}).get("emoticons") or []:
            if not isinstance(item, dict):
                continue
            urls = item.get("urls") or {}
            eid, name = str(item.get("id") or ""), str(item.get("name") or "")
            small = urls.get("2") or urls.get("1")
            big = urls.get("4") or small
            if eid and name and small:
                out.append(Emote(f"ffz:{eid}", name, _https(small), _https(big)))
    return out


def parse_7tv(emotes: Iterable[Any]) -> list[Emote]:
    out = []
    for item in emotes or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        data = item.get("data") or {}
        host = data.get("host") or {}
        base = _https(host.get("url") or "")
        eid = str(item.get("id") or data.get("id") or "")
        if not (name and base and eid):
            continue
        files = {str(f.get("name")): f for f in host.get("files") or [] if isinstance(f, dict)}

        def pick(*names: str) -> str:
            for n in names:
                if n in files:
                    return f"{base}/{n}"
            return ""

        # Qt shows png/gif/webp; prefer the ones that also animate in a browser.
        small = pick("2x.gif", "2x.png", "2x.webp", "1x.gif", "1x.png", "1x.webp") or f"{base}/2x.webp"
        big = pick("3x.webp", "3x.gif", "2x.webp") or small
        out.append(Emote(f"7tv:{eid}", name, small, big))
    return out


# ----- the index the hub asks "is this word an emote?" -----

Fetch = Callable[[str], Any]


def http_json(url: str, timeout: float = 10.0) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class EmoteIndex:
    def __init__(self, fetch: Fetch = http_json, enabled: bool = True) -> None:
        self.fetch = fetch
        self.enabled = enabled
        self._global: dict[str, Emote] = {}
        self._channel: dict[str, Emote] = {}
        self._room = ""
        self._lock = threading.Lock()

    def lookup(self, word: str) -> Optional[Emote]:
        if not self.enabled:
            return None
        # Channel emotes win over global ones with the same name.
        return self._channel.get(word) or self._global.get(word)

    def load_global_async(self) -> None:
        if self.enabled:
            threading.Thread(target=self.load_global, daemon=True, name="emotes-global").start()

    def set_room_async(self, room_id: str) -> None:
        room_id = str(room_id or "")
        with self._lock:
            if room_id == self._room:
                return
            self._room = room_id
            self._channel = {}
        if room_id and self.enabled:
            threading.Thread(
                target=self.load_room, args=(room_id,), daemon=True, name="emotes-channel"
            ).start()

    def load_global(self) -> None:
        found: list[Emote] = []
        found += self._try(lambda: parse_bttv(self.fetch("https://api.betterttv.net/3/cached/emotes/global")))
        found += self._try(
            lambda: (lambda d: parse_ffz_sets(d.get("sets"), d.get("default_sets")))(
                self.fetch("https://api.frankerfacez.com/v1/set/global")
            )
        )
        found += self._try(
            lambda: parse_7tv((self.fetch("https://7tv.io/v3/emote-sets/global") or {}).get("emotes"))
        )
        with self._lock:
            self._global = {e.name: e for e in found}
        log.info("Loaded %d global BTTV/FFZ/7TV emotes", len(found))

    def load_room(self, room_id: str) -> None:
        found: list[Emote] = []

        def bttv() -> list[Emote]:
            data = self.fetch(f"https://api.betterttv.net/3/cached/users/twitch/{room_id}") or {}
            return parse_bttv((data.get("channelEmotes") or []) + (data.get("sharedEmotes") or []))

        def ffz() -> list[Emote]:
            data = self.fetch(f"https://api.frankerfacez.com/v1/room/id/{room_id}") or {}
            return parse_ffz_sets(data.get("sets"))

        def seventv() -> list[Emote]:
            data = self.fetch(f"https://7tv.io/v3/users/twitch/{room_id}") or {}
            return parse_7tv((data.get("emote_set") or {}).get("emotes"))

        # Lowest priority first so later services win a name clash.
        for loader in (ffz, bttv, seventv):
            found += self._try(loader)
        with self._lock:
            if self._room != room_id:
                return  # switched channel while loading
            self._channel = {e.name: e for e in found}
        log.info("Loaded %d channel emotes for room %s", len(found), room_id)

    @staticmethod
    def _try(fn: Callable[[], list[Emote]]) -> list[Emote]:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one service down must not stop the rest
            log.info("Emote list failed: %s", exc)
            return []
