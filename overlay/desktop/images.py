"""Downloads emote pictures into the cache folder for the desktop window."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

from ..emotes import USER_AGENT

log = logging.getLogger(__name__)

MAX_BYTES = 3 * 1024 * 1024


def sniff_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    return ""


def safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)[:120]


class EmoteFiles:
    def __init__(self, folder: Path, on_ready: Callable[[], None]) -> None:
        self.folder = folder
        self.on_ready = on_ready
        self._known: dict[str, Path] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="emote-dl")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for path in folder.iterdir():
                if path.suffix in (".png", ".gif", ".webp", ".jpg"):
                    self._known[path.stem] = path
        except OSError:
            log.warning("Emote cache folder isn't usable: %s", folder)

    def path_for(self, key: str, url: str) -> Optional[Path]:
        """The cached picture, or None (and start downloading it)."""
        name = safe_name(key)
        path = self._known.get(name)
        if path is not None:
            return path
        if not url.startswith("https://"):
            return None
        with self._lock:
            if name in self._pending or name in self._failed:
                return None
            self._pending.add(name)
        self._pool.submit(self._download, name, url)
        return None

    def _download(self, name: str, url: str) -> None:
        ok = False
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/png,image/gif,image/webp,*/*"})
            with urlopen(req, timeout=10) as resp:
                data = resp.read(MAX_BYTES + 1)
            ext = sniff_extension(data)
            if ext and len(data) <= MAX_BYTES:
                path = self.folder / f"{name}{ext}"
                tmp = path.with_suffix(path.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(path)
                self._known[name] = path
                ok = True
        except Exception as exc:  # noqa: BLE001
            log.debug("Emote download failed %s: %s", url, exc)
        with self._lock:
            self._pending.discard(name)
            if not ok:
                self._failed.add(name)
        if ok:
            try:
                self.on_ready()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
