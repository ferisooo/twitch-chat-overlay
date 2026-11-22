"""Smoke test: the desktop window builds and draws chat (no real network)."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from overlay.desktop import theme  # noqa: E402
from overlay.desktop.window import OverlayWindow, share_text  # noqa: E402
from overlay.hub import Hub  # noqa: E402
from overlay.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(theme.STYLE)
    return instance


def test_window_shows_messages_and_reacts(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LCO_CACHE_DIR", str(tmp_path / "cache"))
    s = Settings(tmp_path / "s.json", tmp_path / "none.json")
    s.update({"channel": "chan", "platform": "merge"})
    hub = Hub(s)
    win = OverlayWindow(hub, None)
    hub.subscribe(win.on_hub_event)
    win._poll_timer.stop()
    win.show()
    hub.post({"src": "twitch", "kind": "chat", "name": "Bob", "login": "bob", "text": "hello", "id": "x1"})
    hub.post({"src": "tiktok", "kind": "chat", "name": "Tok", "text": "yo [laugh]"})
    hub.drain()
    app.processEvents()
    merge = win.views["merge"]
    assert len(merge.msgs) == 2
    assert "Bob" in merge.toPlainText() and "Tok" in merge.toPlainText()

    hub.rename("bob", "Bobby", "Bob")
    assert "Bobby: hello" in merge.toPlainText()

    hub.post({"src": "twitch", "kind": "delete", "id": "x1"})
    hub.drain()
    assert "hello" not in merge.toPlainText()

    hub.set_tab("tiktok")
    assert win.stack.currentWidget() is win.views["tiktok"]
    assert win.channel_edit.placeholderText() == "TikTok username"

    was_open = win.panel_scroll.isVisible()
    win.gear_btn.click()
    assert win.panel_scroll.isVisible() != was_open
    win.gear_btn.click()
    assert win.panel_scroll.isVisible() == was_open

    win._apply_look(True)
    assert not win.compose.isVisible()  # no Twitch send box on the TikTok tab
    hub.set_tab("merge")
    assert win.compose.isVisible()  # Twitch channel set and overlay active
    win._apply_look(False)
    win.hide_overlay()
    assert not win.isVisible()
    hub.post({"src": "twitch", "kind": "chat", "name": "Amy", "text": "back"})
    hub.drain()
    assert win.isVisible()
    win._quitting = True
    win.close()


def test_share_text():
    assert share_text("Live! {title}", "me", {"title": "Speedrun"}) == "Live! Speedrun https://twitch.tv/me"
    assert share_text("{link} now", "me", {}) == "https://twitch.tv/me now"


def test_pink_link_opens_page_and_hides_when_narrow(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LCO_CACHE_DIR", str(tmp_path / "cache"))
    opened = []
    monkeypatch.setattr("overlay.desktop.window.webbrowser.open_new_tab", opened.append)
    hub = Hub(Settings(tmp_path / "s.json", tmp_path / "none.json"))
    win = OverlayWindow(hub, None)
    win._poll_timer.stop()
    win.show()
    win.resize(400, 400)
    app.processEvents()
    assert win.link_btn.text() == "mez.ink/ferisooo"
    assert win.link_btn.isVisible()
    win.link_btn.click()
    assert opened == ["https://mez.ink/ferisooo"]

    win.resize(win.minimumWidth(), 400)
    app.processEvents()
    assert not win.link_btn.isVisible()  # no room: the tabs keep their space
    win.resize(400, 400)
    app.processEvents()
    assert win.link_btn.isVisible()
