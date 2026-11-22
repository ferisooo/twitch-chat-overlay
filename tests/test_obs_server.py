import base64
import json
import os
import socket
import time

from overlay import config, wsproto
from overlay.obs_server import ObsServer


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(port, path, host=None):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\n\r\n".encode())
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return data


class WsTestClient:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode()
        )
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(4096)
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        assert b" 101 " in head
        assert wsproto.accept_key(key).encode() in head

    def _read(self, n):
        while len(self.buf) < n:
            self.buf += self.sock.recv(65536)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_json(self):
        b0, b1 = self._read(2)
        _, opcode, _, n = wsproto.decode_header(b0, b1)
        if n == 126:
            n = int.from_bytes(self._read(2), "big")
        elif n == 127:
            n = int.from_bytes(self._read(8), "big")
        assert opcode == wsproto.OP_TEXT
        return json.loads(self._read(n))

    def close(self):
        self.sock.sendall(wsproto.encode_frame(wsproto.OP_CLOSE, b"\x03\xe8", mask=True))
        self.sock.close()


def make_server():
    snap = {
        "tab": "merge",
        "status": {"twitch": "idle"},
        "settings": {"font_size": 12, "text_backing": True},
        "history": {"twitch": [{"id": 1, "p": "twitch", "k": "chat", "u": {"key": "bob", "name": "Bob", "show": "Bob", "color": "#fff"}, "seg": []}], "merge": [], "tiktok": []},
    }
    server = ObsServer(config.WEB_DIR, free_port(), snap)
    assert server.start(), server.error
    return server


def test_accept_key_known_vector():
    assert wsproto.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frame_roundtrip_masked():
    frame = wsproto.encode_frame(wsproto.OP_TEXT, b"hello world", mask=True)
    fin, opcode, masked, n = wsproto.decode_header(frame[0], frame[1])
    assert (fin, opcode, masked, n) == (True, wsproto.OP_TEXT, True, 11)
    assert wsproto._mask(frame[6:], frame[2:6]) == b"hello world"


def test_serves_page_and_blocks_other_hosts():
    server = make_server()
    try:
        page = http_get(server.port, "/")
        assert page.startswith(b"HTTP/1.1 200") and b"obs.js" in page
        assert http_get(server.port, "/obs.js").startswith(b"HTTP/1.1 200")
        assert http_get(server.port, "/../settings.json").startswith(b"HTTP/1.1 404")
        assert http_get(server.port, "/", host="evil.example").startswith(b"HTTP/1.1 403")
    finally:
        server.stop()


def test_websocket_hello_and_live_updates():
    server = make_server()
    try:
        client = WsTestClient(server.port)
        hello = client.recv_json()
        assert hello["type"] == "hello"
        assert hello["tab"] == "merge"
        assert hello["history"]["twitch"][0]["u"]["name"] == "Bob"

        msg = {"id": 2, "p": "twitch", "k": "chat", "u": {"key": "amy", "name": "Amy", "show": "Amy", "color": "#f00"}, "seg": [{"t": "text", "v": "hi"}]}
        server.on_hub_event("message", {"tabs": ["twitch", "merge"], "msg": msg})
        got = client.recv_json()
        assert got["type"] == "message" and got["msg"]["u"]["name"] == "Amy"

        server.on_hub_event("automod", {"cards": [{"text": "secret"}]})  # never forwarded
        server.on_hub_event("alias", {"key": "amy", "show": "A"})
        assert client.recv_json() == {"type": "alias", "key": "amy", "show": "A"}

        server.on_hub_event("remove", {"ids": [1]})
        assert client.recv_json()["type"] == "remove"
        server.set_visible(False)
        assert client.recv_json() == {"type": "visible", "visible": False}
        client.close()

        # A new page sees the updated mirror.
        time.sleep(0.1)
        client2 = WsTestClient(server.port)
        hello2 = client2.recv_json()
        assert [m["id"] for m in hello2["history"]["twitch"]] == [2]
        assert hello2["history"]["twitch"][0]["u"]["show"] == "A"
        assert hello2["visible"] is False
        client2.close()
    finally:
        server.stop()
