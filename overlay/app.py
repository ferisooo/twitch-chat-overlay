"""Starts everything and wires the parts together."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import VERSION, config


def setup_logging() -> None:
    folder = config.data_dir()
    handlers: list[logging.Handler] = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(folder / "overlay.log", maxBytes=512_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _write_pid() -> None:
    try:
        (config.data_dir() / "overlay.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass


def _clear_pid() -> None:
    try:
        path = config.data_dir() / "overlay.pid"
        if path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def main() -> int:
    setup_logging()
    log = logging.getLogger("overlay")
    log.info("Live Chat Overlay %s starting", VERSION)

    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from .credstore import CredStore
    from .desktop import theme, win32
    from .desktop.window import OverlayWindow
    from .emotes import EmoteIndex
    from .hub import Hub
    from .obs_server import ObsServer
    from .settings import Settings
    from .sources.automod import TwitchAutoMod
    from .sources.giftspawner import GiftSpawnerEvents
    from .sources.stream_watch import StreamWatcher
    from .sources.tiktok import TikTokChat
    from .sources.twitch_auth import TwitchAccount
    from .sources.twitch_irc import TwitchIRC

    win32.set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLE)

    # Only one overlay at a time: a second start just shows the first one.
    instance_name = f"{config.APP_SLUG}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    probe = QLocalSocket()
    probe.connectToServer(instance_name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        log.info("Already running: asked it to show itself")
        return 0
    QLocalServer.removeServer(instance_name)
    instance = QLocalServer()
    instance.listen(instance_name)

    settings = Settings()
    hub = Hub(settings)
    account = TwitchAccount(hub.post, CredStore(), config.legacy_secrets_path())
    emotes = EmoteIndex(enabled=settings["third_party_emotes"])
    hub.account = account
    hub.emotes = emotes
    hub.twitch = TwitchIRC(hub.post, account)
    hub.tiktok = TikTokChat(hub.post)
    hub.automod = TwitchAutoMod(hub.post, account)
    hub.stream = StreamWatcher(hub.post, account, settings["announced_stream_id"])

    server = ObsServer(config.WEB_DIR, settings["obs_port"], hub.snapshot())
    if server.start():
        hub.subscribe(server.on_hub_event)
    else:
        log.error("OBS page is off: %s", server.error)

    window = OverlayWindow(hub, server if server.running else None)
    hub.subscribe(window.on_hub_event)
    instance.newConnection.connect(lambda: (instance.nextPendingConnection(), window.show_overlay()))

    if settings.imported_legacy:
        hub.add_line("twitch", "system", "Brought over your settings from the old version.", activity=False)

    pump = QTimer(interval=30)
    pump.timeout.connect(hub.drain)
    pump.start()
    ticker = QTimer(interval=1000)
    ticker.timeout.connect(hub.tick)
    ticker.start()

    if settings["show_point_redeems"]:
        GiftSpawnerEvents(hub.post, settings["gift_spawner_port"]).start()
    emotes.load_global_async()

    window.show()
    if settings["autoconnect"]:
        QTimer.singleShot(250, hub.connect_saved)

    _write_pid()
    try:
        code = app.exec()
    finally:
        hub.shutdown()
        server.stop()
        _clear_pid()
        log.info("Stopped")
    return code
