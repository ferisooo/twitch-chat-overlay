"""Your settings, saved as JSON in your AppData folder (never in the repo)."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

DEFAULT_SHARE_TEXT = "🔴 I'm live! {title} Come watch: {link}"
DEFAULT_OFFLINE_SHARE_TEXT = (
    "Come hang out on my Twitch, follow so you catch the next stream: {link}"
)
DEFAULT_OPACITY = 0.82
MAX_SAVED_NAME_COLORS = 600
PLATFORMS = ("twitch", "tiktok", "merge")

DEFAULTS: dict[str, Any] = {
    "platform": "twitch",
    "channel": "",
    "tiktok_user": "",
    "autoconnect": True,
    # Window spot and size: [x, y, width, height]
    "geometry": [80, 80, 380, 520],
    "opacity": DEFAULT_OPACITY,
    "text_backing": True,
    "font_size": 11,
    "idle_hide": True,
    "close_with_obs": True,
    "share_on_live": False,
    "share_text": DEFAULT_SHARE_TEXT,
    "share_text_offline": DEFAULT_OFFLINE_SHARE_TEXT,
    "announced_stream_id": "",
    "twitch_nick": "",
    "twitch_client_id": "",
    "third_party_emotes": True,
    "show_point_redeems": True,
    "gift_spawner_port": config.GIFT_SPAWNER_PORT,
    "obs_port": config.DEFAULT_OBS_PORT,
    "name_aliases": {},
    "name_colors": {},
    "name_color_next": 0,
}

# Keys the old version saved that must never be copied into the new file.
_DROPPED_LEGACY_KEYS = ("twitch_oauth", "deepseek_api_key")

_TK_GEOMETRY = re.compile(r"^\s*(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)\s*$")


def parse_tk_geometry(text: Any) -> list[int] | None:
    """'380x520+80+80' (the old Tk format) -> [80, 80, 380, 520]."""
    match = _TK_GEOMETRY.match(str(text or ""))
    if not match:
        return None
    w, h, x, y = match.groups()

    def offset(raw: str) -> int:
        # Tk writes "+-10" for a window pushed past the left edge.
        return int(raw[1:]) if raw.startswith("+") else int(raw)

    return [offset(x), offset(y), int(w), int(h)]


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _convert_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in raw.items() if k not in _DROPPED_LEGACY_KEYS}
    if isinstance(data.get("geometry"), str):
        geometry = parse_tk_geometry(data["geometry"])
        if geometry is None:
            data.pop("geometry", None)
        else:
            data["geometry"] = geometry
    return data


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


class Settings:
    """A dict of settings with defaults, cleanup and atomic saving."""

    def __init__(self, path: Path | None = None, legacy_path: Path | None = None) -> None:
        self.path = path or (config.data_dir() / "settings.json")
        self.legacy_path = config.LEGACY_SETTINGS_PATH if legacy_path is None else legacy_path
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.imported_legacy = False
        self.dirty = False
        self.load()

    # ----- dict-like access -----
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.dirty = True

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value)
        self._normalize()

    # ----- load / save -----
    def load(self) -> None:
        raw = _read_json(self.path)
        if raw is None:
            legacy = _read_json(self.legacy_path) if self.legacy_path else None
            if legacy is not None:
                raw = _convert_legacy(legacy)
                self.imported_legacy = True
                log.info("Imported settings from %s", self.legacy_path)
        if raw:
            self.data.update(raw)
        self._normalize()
        if self.imported_legacy:
            self.save()

    def save(self) -> bool:
        self._normalize()
        self._trim_name_colors()
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Couldn't save settings to %s", self.path)
            return False
        self.dirty = False
        return True

    def save_if_dirty(self) -> None:
        if self.dirty:
            self.save()

    # ----- cleanup -----
    def _normalize(self) -> None:
        d = self.data
        if d.get("platform") not in PLATFORMS:
            d["platform"] = "twitch"
        d["channel"] = str(d.get("channel") or "").strip().lstrip("#").lower()
        d["tiktok_user"] = str(d.get("tiktok_user") or "").strip().lstrip("@")
        d["twitch_nick"] = str(d.get("twitch_nick") or "").strip().lstrip("@").lower()
        d["twitch_client_id"] = str(d.get("twitch_client_id") or "").strip()
        d["opacity"] = _as_float(d.get("opacity"), DEFAULT_OPACITY, 0.0, 1.0)
        d["font_size"] = _as_int(d.get("font_size"), 11, 8, 28)
        d["obs_port"] = _as_int(d.get("obs_port"), config.DEFAULT_OBS_PORT, 1024, 65535)
        d["gift_spawner_port"] = _as_int(
            d.get("gift_spawner_port"), config.GIFT_SPAWNER_PORT, 1, 65535
        )
        d["name_color_next"] = _as_int(d.get("name_color_next"), 0, 0, 10**9)
        for key in (
            "autoconnect", "text_backing", "idle_hide", "close_with_obs", "share_on_live",
            "third_party_emotes", "show_point_redeems",
        ):
            d[key] = bool(d.get(key, DEFAULTS[key]))
        for key, fallback in (
            ("share_text", DEFAULT_SHARE_TEXT),
            ("share_text_offline", DEFAULT_OFFLINE_SHARE_TEXT),
        ):
            d[key] = str(d.get(key) or fallback)
        d["announced_stream_id"] = str(d.get("announced_stream_id") or "")

        geometry = d.get("geometry")
        if (
            not isinstance(geometry, (list, tuple))
            or len(geometry) != 4
            or not all(isinstance(n, (int, float)) for n in geometry)
        ):
            geometry = DEFAULTS["geometry"]
        x, y, w, h = (int(n) for n in geometry)
        d["geometry"] = [x, y, max(240, w), max(180, h)]

        aliases = d.get("name_aliases")
        d["name_aliases"] = (
            {
                str(k).strip().lower(): str(v).strip()
                for k, v in aliases.items()
                if str(k).strip() and str(v).strip()
            }
            if isinstance(aliases, dict)
            else {}
        )
        colors = d.get("name_colors")
        d["name_colors"] = (
            {
                str(k): str(v)
                for k, v in colors.items()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(v))
            }
            if isinstance(colors, dict)
            else {}
        )

    def _trim_name_colors(self) -> None:
        colors = self.data["name_colors"]
        if len(colors) > MAX_SAVED_NAME_COLORS:
            # Dicts keep insertion order, so the newest chatters are at the end.
            self.data["name_colors"] = dict(list(colors.items())[-MAX_SAVED_NAME_COLORS:])
