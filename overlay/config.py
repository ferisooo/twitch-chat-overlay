"""Names, folders and ports shared by the whole app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Live Chat Overlay"
APP_SLUG = "LiveChatOverlay"

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"

# The old version kept settings.json next to main.py. It is imported once.
LEGACY_SETTINGS_PATH = APP_DIR / "settings.json"

DEFAULT_OBS_PORT = 4848
GIFT_SPAWNER_PORT = 4850


def data_dir() -> Path:
    """Where settings live: %APPDATA%\\LiveChatOverlay on Windows."""
    override = os.environ.get("LCO_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_SLUG
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "live-chat-overlay"


def cache_dir() -> Path:
    """Downloaded emote pictures. Safe to delete at any time."""
    override = os.environ.get("LCO_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_SLUG / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "live-chat-overlay"


def legacy_secrets_path() -> Path:
    """The old version's login token file (read once, never changed)."""
    return Path(os.environ.get("APPDATA") or Path.home()) / APP_SLUG / "secrets.json"
