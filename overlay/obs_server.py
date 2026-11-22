"""A tiny local web server for the OBS Browser Source.

OBS loads http://127.0.0.1:4848/ and gets a see-through chat page. The page
keeps a WebSocket open and receives new chat lines as they arrive. It only
listens on this PC (127.0.0.1) and only sends chat, never your login.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from . import wsproto

log = logging.getLogger(__name__)

MAX_LINES = 100
MAX_HEADER = 16384
STATIC = {
    "/": ("obs.html", "text/html; charset=utf-8"),
    "/obs": ("obs.html", "text/html; charset=utf-8"),
    "/obs.css": ("obs.css", "text/css; charset=utf-8"),
    "/obs.js": ("obs.js", "application/javascript; charset=utf-8"),
}
# Things the page needs. AutoMod cards and login details are never sent.
FORWARDED = ("message", "remove", "tab", "status", "settings", "alias")


class _Client:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue(maxsize=2000)


class ObsServer:
    def __init__(self, web_dir: Path, port: int, snapshot: dict[str, Any]) -> None:
        self.web_dir = web_dir
        self.port = port
        self.error = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.base_events.Server] = None
        self._clients: set[_Client] = set()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # A mirror of what the page needs, owned by the server thread.
        self._tab = snapshot.get("tab", "twitch")
        self._status = dict(snapshot.get("status") or {})
        self._settings = dict(snapshot.get("settings") or {})
        self._visible = True
        self._history: dict[str, deque[dict[str, Any]]] = {
            tab: deque(lines[-MAX_LINES:], maxlen=MAX_LINES)
            for tab, lines in (snapshot.get("history") or {}).items()
        }

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # ----- start / stop (called from the main thread) -----
    def start(self) -> bool:
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="obs-server")
        self._thread.start()
        self._ready.wait(5)
        return self._server is not None

    def stop(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def shutdown() -> None:
            if self._server is not None:
                self._server.close()
            for client in list(self._clients):
                self._drop(client)
            await asyncio.sleep(0.05)
            loop.stop()

        asyncio.run_coroutine_threadsafe(shutdown(), loop)
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        # Try the saved port, then a few after it if something else uses it.
        for port in range(self.port, self.port + 10):
            try:
                self._server = loop.run_until_complete(
                    asyncio.start_server(self._handle, "127.0.0.1", port)
                )
                self.port = port
                break
            except OSError as exc:
                self.error = str(exc)
        self._ready.set()
        if self._server is None:
            log.error("OBS page server couldn't start: %s", self.error)
            return
        log.info("OBS page at %s", self.url)
        try:
            loop.run_forever()
        finally:
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()

    # ----- updates from the hub (any thread) -----
    def on_hub_event(self, event: str, data: dict[str, Any]) -> None:
        if event in FORWARDED and self._loop is not None:
            # A private copy: the server thread must not share dicts with the window.
            self._loop.call_soon_threadsafe(self._apply, event, copy.deepcopy(data))

    def set_visible(self, visible: bool) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._apply, "visible", {"visible": bool(visible)})

    def _apply(self, event: str, data: dict[str, Any]) -> None:
        if event == "message":
            for tab in data.get("tabs") or []:
                self._history.setdefault(tab, deque(maxlen=MAX_LINES)).append(data["msg"])
        elif event == "remove":
            ids = set(data.get("ids") or [])
            for tab, lines in self._history.items():
                self._history[tab] = deque((m for m in lines if m.get("id") not in ids), maxlen=MAX_LINES)
        elif event == "tab":
            self._tab = data.get("tab", self._tab)
        elif event == "status":
            self._status[data.get("platform", "")] = data.get("text", "")
        elif event == "settings":
            self._settings.update(data)
        elif event == "alias":
            for lines in self._history.values():
                for m in lines:
                    user = m.get("u")
                    if user and user.get("key") == data.get("key"):
                        user["show"] = data.get("show") or user.get("name")
        elif event == "visible":
            self._visible = data["visible"]
        self._broadcast({"type": event, **data})

    def _hello(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "tab": self._tab,
            "status": self._status,
            "settings": self._settings,
            "visible": self._visible,
            "history": {tab: list(lines) for tab, lines in self._history.items()},
        }

    def _broadcast(self, payload: dict[str, Any]) -> None:
        frame = wsproto.encode_frame(wsproto.OP_TEXT, json.dumps(payload).encode("utf-8"))
        for client in list(self._clients):
            try:
                client.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._drop(client)

    def _drop(self, client: _Client) -> None:
        if client not in self._clients:
            return
        self._clients.discard(client)
        try:
            client.queue.put_nowait(None)
        except (asyncio.QueueFull, RuntimeError):
            pass
        client.writer.close()

    # ----- HTTP + WebSocket -----
    def _host_ok(self, host: str) -> bool:
        # Blocks other websites from reading chat through DNS tricks.
        return host.lower() in {
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
            f"[::1]:{self.port}",
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        if len(head) > MAX_HEADER:
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name:
                headers[name.strip().lower()] = value.strip()
        path = target.split("?", 1)[0]

        if not self._host_ok(headers.get("host", "")):
            await self._respond(writer, 403, "text/plain", b"Forbidden")
            return
        if method != "GET":
            await self._respond(writer, 405, "text/plain", b"Method not allowed")
            return
        if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
            await self._websocket(reader, writer, headers)
            return
        entry = STATIC.get(path)
        if entry is None:
            await self._respond(writer, 404, "text/plain", b"Not found")
            return
        try:
            body = (self.web_dir / entry[0]).read_bytes()
        except OSError:
            await self._respond(writer, 500, "text/plain", b"Missing file")
            return
        await self._respond(writer, 200, entry[1], body)

    async def _respond(self, writer: asyncio.StreamWriter, status: int, ctype: str, body: bytes) -> None:
        reason = {200: "OK", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 500: "Error"}[status]
        writer.write(
            (
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                "X-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n"
            ).encode("latin-1")
            + body
        )
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()

    async def _websocket(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict[str, str]
    ) -> None:
        key = headers.get("sec-websocket-key", "")
        if not key:
            await self._respond(writer, 403, "text/plain", b"Bad websocket request")
            return
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {wsproto.accept_key(key)}\r\n\r\n"
            ).encode("ascii")
        )
        client = _Client(writer)
        client.queue.put_nowait(
            wsproto.encode_frame(wsproto.OP_TEXT, json.dumps(self._hello()).encode("utf-8"))
        )
        self._clients.add(client)
        sender = asyncio.ensure_future(self._send_loop(client))
        try:
            await self._read_loop(reader, client)
        finally:
            self._drop(client)
            sender.cancel()

    async def _send_loop(self, client: _Client) -> None:
        try:
            while True:
                frame = await client.queue.get()
                if frame is None:
                    return
                client.writer.write(frame)
                await client.writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            return

    async def _read_loop(self, reader: asyncio.StreamReader, client: _Client) -> None:
        """The page sends nothing useful; just answer pings and notice closes."""
        try:
            while True:
                b0, b1 = await reader.readexactly(2)
                _, opcode, masked, n = wsproto.decode_header(b0, b1)
                if n == 126:
                    n = int.from_bytes(await reader.readexactly(2), "big")
                elif n == 127:
                    n = int.from_bytes(await reader.readexactly(8), "big")
                if n > 65536:
                    return
                key = await reader.readexactly(4) if masked else b""
                payload = await reader.readexactly(n) if n else b""
                if key:
                    payload = wsproto._mask(payload, key)
                if opcode == wsproto.OP_CLOSE:
                    client.queue.put_nowait(wsproto.encode_frame(wsproto.OP_CLOSE, payload[:2]))
                    return
                if opcode == wsproto.OP_PING:
                    client.queue.put_nowait(wsproto.encode_frame(wsproto.OP_PONG, payload))
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.QueueFull):
            return
