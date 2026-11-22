"""Just enough WebSocket (RFC 6455) for this app, using only Python's
built-in modules: a client for Twitch EventSub and frame helpers for the
local OBS page server."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
from typing import Optional

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode("ascii")).digest()).decode("ascii")


def _mask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload
    # XOR in one go: much faster than a Python loop for big messages.
    full = (mask * (len(payload) // 4 + 1))[: len(payload)]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(full, "big")).to_bytes(len(payload), "big")


def encode_frame(opcode: int, payload: bytes = b"", *, mask: bool = False) -> bytes:
    header = bytearray([0x80 | opcode])
    n = len(payload)
    mask_bit = 0x80 if mask else 0
    if n < 126:
        header.append(mask_bit | n)
    elif n < 65536:
        header.append(mask_bit | 126)
        header += n.to_bytes(2, "big")
    else:
        header.append(mask_bit | 127)
        header += n.to_bytes(8, "big")
    if not mask:
        return bytes(header) + payload
    key = os.urandom(4)
    return bytes(header) + key + _mask(payload, key)


def decode_header(b0: int, b1: int) -> tuple[bool, int, bool, int]:
    """(fin, opcode, masked, short length) from the first two bytes."""
    return bool(b0 & 0x80), b0 & 0x0F, bool(b1 & 0x80), b1 & 0x7F


class WebSocketClient:
    """A blocking wss:// client, used by the AutoMod reader thread."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        if not url.startswith("wss://"):
            raise ValueError("only wss:// is supported")
        hostport, _, path = url[len("wss://") :].partition("/")
        host, _, port_text = hostport.partition(":")
        raw = socket.create_connection((host, int(port_text or 443)), timeout=timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        self._buf = b""
        self._closed = False
        try:
            self._handshake(host, "/" + path)
        except Exception:
            self.close()
            raise

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\nUser-Agent: LiveChatOverlay\r\n\r\n"
            ).encode("ascii")
        )
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("websocket handshake: connection closed")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise OSError("websocket handshake: reply too long")
        head, _, self._buf = self._buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in f"{lines[0]} ":
            raise OSError(f"websocket handshake refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise OSError("websocket handshake: bad accept key")

    def settimeout(self, seconds: Optional[float]) -> None:
        self.sock.settimeout(seconds)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise OSError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        self.sock.sendall(encode_frame(opcode, payload, mask=True))

    def recv_text(self) -> Optional[str]:
        """Next text message, or None once the server closes the socket."""
        parts: list[bytes] = []
        while True:
            fin, opcode, masked, n = decode_header(*self._read_exact(2))
            if n == 126:
                n = int.from_bytes(self._read_exact(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read_exact(8), "big")
            key = self._read_exact(4) if masked else b""
            payload = self._read_exact(n) if n else b""
            if key:
                payload = _mask(payload, key)
            if opcode == OP_PING:
                self.send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                try:
                    self.send_frame(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                self.close()
                return None
            if opcode in (OP_TEXT, OP_BINARY, OP_CONT):
                parts.append(payload)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass
