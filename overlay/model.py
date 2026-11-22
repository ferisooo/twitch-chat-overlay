"""One chat line, in a shape both the desktop window and OBS can draw."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .emotes import Emote, twitch_emote
from .sources.twitch_irc import message_segments

# Line kinds:
#   chat, action  - someone talking (/me is "action")
#   event         - subs, raids, announcements, TikTok subs
#   points        - channel point redeems from Gift Spawner
#   system        - connection news and notices
#   warn          - "TikTok may end your LIVE" style warnings
#   error         - something you need to fix
KINDS = ("chat", "action", "event", "points", "system", "warn", "error")

Segment = dict[str, str]
EmoteLookup = Callable[[str], Optional[Emote]]


@dataclass
class Message:
    id: int
    platform: str  # "twitch" or "tiktok"
    kind: str
    name: str = ""  # who said it (as the platform shows it)
    alias_key: str = ""  # lowercase name, used for nicknames
    login: str = ""  # Twitch login, for moderator deletes
    color: str = ""
    segments: list[Segment] = field(default_factory=list)
    text: str = ""  # plain text for non-chat lines
    mention: bool = False
    twitch_id: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self, display: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "p": self.platform, "k": self.kind}
        if self.kind in ("chat", "action"):
            out["u"] = {"key": self.alias_key, "name": self.name, "show": display or self.name, "color": self.color}
            out["seg"] = self.segments
            if self.mention:
                out["mention"] = True
        else:
            out["text"] = self.text
        return out


def mention_regex(names: Iterable[str]) -> Optional[re.Pattern[str]]:
    """Matches your own names (4+ letters) as whole words, with or without @."""
    parts = sorted(
        {str(n or "").strip().lstrip("#@").lower() for n in names} - {""},
        key=len,
        reverse=True,
    )
    parts = [re.escape(p) for p in parts if len(p) >= 4]
    if not parts:
        return None
    return re.compile(r"(?i)(?<![A-Za-z0-9_])@?(?:" + "|".join(parts) + r")(?![A-Za-z0-9_])")


def _emote_segment(emote: Emote) -> Segment:
    return {"t": "emote", "key": emote.key, "name": emote.name, "url": emote.url, "hd": emote.url_hd}


def _append_text(segs: list[Segment], text: str) -> None:
    if not text:
        return
    if segs and segs[-1]["t"] == "text":
        segs[-1] = {"t": "text", "v": segs[-1]["v"] + text}
    else:
        segs.append({"t": "text", "v": text})


def _split_mentions(segs: list[Segment], rx: Optional[re.Pattern[str]]) -> bool:
    if rx is None:
        return False
    mentioned = False
    out: list[Segment] = []
    for seg in segs:
        if seg["t"] != "text":
            out.append(seg)
            continue
        text, last = seg["v"], 0
        for m in rx.finditer(text):
            _append_text(out, text[last : m.start()])
            out.append({"t": "mention", "v": m.group(0)})
            mentioned = True
            last = m.end()
        _append_text(out, text[last:])
    segs[:] = out
    return mentioned


_WORDS = re.compile(r"(\s+)")


def build_segments(
    text: str,
    emotes_tag: str = "",
    lookup: Optional[EmoteLookup] = None,
    mentions: Optional[re.Pattern[str]] = None,
) -> tuple[list[Segment], bool]:
    """Turn a chat line into text / emote / mention pieces."""
    segs: list[Segment] = []
    for chunk, eid in message_segments(text, emotes_tag):
        if eid:
            segs.append(_emote_segment(twitch_emote(eid, chunk)))
            continue
        if lookup is None:
            _append_text(segs, chunk)
            continue
        for word in _WORDS.split(chunk):
            emote = lookup(word) if word and not word.isspace() else None
            if emote is not None:
                segs.append(_emote_segment(emote))
            else:
                _append_text(segs, word)
    mentioned = _split_mentions(segs, mentions)
    return segs, mentioned


def plain_text(segments: Iterable[Segment]) -> str:
    return "".join(s.get("v") or s.get("name") or "" for s in segments)
