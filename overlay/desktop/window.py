"""The overlay window: always on top, see-through, and click-through.

Qt draws it with real per-pixel transparency, so there is no colour-key
trick and nothing to flicker. Clicks pass through to your game unless the
mouse is over the title bar, tabs, settings or buttons, or you hold Alt or
Shift, or the overlay is the active window.
"""

from __future__ import annotations

import logging
import re
import time
import webbrowser
from typing import Any, Optional
from urllib.parse import quote

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QKeyEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QStackedLayout, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .. import config
from ..hub import TABS
from . import theme, win32
from .chatview import ChatView
from .dialogs import ConfirmQuitDialog, LoginDialog, RenameDialog
from .images import EmoteFiles

log = logging.getLogger(__name__)

IDLE_HIDE_S = 60.0
HOVER_REVEAL_S = 0.3
SOLID_DELAY_S = 0.15
OBS_POLL_MS = 3000

# The pink link beside the tabs.
LINK_TEXT = "mez.ink/ferisooo"
LINK_URL = "https://mez.ink/ferisooo"


def app_icon() -> QIcon:
    """A small purple chat bubble, drawn in code so no image file is needed."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(4, 6, 56, 42, 12, 12)
    path.moveTo(16, 46)
    path.lineTo(14, 60)
    path.lineTo(30, 46)
    p.fillPath(path, QColor(theme.TWITCH))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    for i, w in enumerate((36, 26)):
        p.drawRoundedRect(14, 16 + i * 13, w, 7, 3, 3)
    p.end()
    return QIcon(pix)


def share_text(template: str, channel: str, info: dict[str, Any]) -> str:
    link = f"https://twitch.tv/{channel}" if channel else "https://twitch.tv"
    text = (
        template.replace("{link}", link)
        .replace("{title}", str(info.get("title") or "").strip())
        .replace("{game}", str(info.get("game") or "").strip())
    )
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text if link in text else f"{text} {link}"


class _Grip(QWidget):
    """Thin edge you drag to resize the window."""

    def __init__(self, parent: "OverlayWindow", edges: Qt.Edges, cursor: Qt.CursorShape) -> None:
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)
        self._start: Optional[tuple[QPoint, QRect]] = None

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        if self.edges == (Qt.RightEdge | Qt.BottomEdge):
            p.setPen(QColor(theme.FAINT))
            for i in range(3):
                o = 4 + i * 4
                p.drawLine(self.width() - 2, o, o, self.height() - 2)
        else:
            p.fillRect(self.rect(), QColor(255, 255, 255, 18))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        handle = self.window().windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            return
        self._start = (event.globalPosition().toPoint(), self.window().geometry())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is None:
            return
        origin, geo = self._start
        delta = event.globalPosition().toPoint() - origin
        w = geo.width() + (delta.x() if self.edges & Qt.RightEdge else 0)
        h = geo.height() + (delta.y() if self.edges & Qt.BottomEdge else 0)
        self.window().resize(max(self.window().minimumWidth(), w), max(self.window().minimumHeight(), h))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._start = None


class AutoModBox(QFrame):
    """Held AutoMod messages with Allow / Deny, above the chat."""

    def __init__(self, on_pick) -> None:
        super().__init__()
        self.setObjectName("automodBox")
        self.on_pick = on_pick
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 6, 6, 2)
        self.lay.setSpacing(4)
        self.more = QLabel("")
        self.more.setObjectName("automodMeta")
        self.cards: list[dict[str, Any]] = []
        self._widgets: dict[str, dict[str, Any]] = {}
        self.hide()

    def set_cards(self, cards: list[dict[str, Any]], waiting: int) -> None:
        self.cards = cards
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget() is not None and item.widget() is not self.more:
                item.widget().deleteLater()
        self._widgets.clear()
        for card in cards:
            self.lay.addWidget(self._card(card))
        self.more.setText(f"{waiting} more waiting" if waiting else "")
        if waiting:
            self.lay.addWidget(self.more)
        self.setVisible(bool(cards))
        self.tick()

    def _card(self, card: dict[str, Any]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("automodCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)
        top = QHBoxLayout()
        title = QLabel(f"🛡 AutoMod held {card['name']}")
        title.setObjectName("automodTitle")
        title.setTextFormat(Qt.PlainText)
        top.addWidget(title, 1)
        count = QLabel("")
        count.setObjectName("automodMeta")
        top.addWidget(count)
        lay.addLayout(top)
        text = QLabel(card["text"] or "(empty message)")
        text.setWordWrap(True)
        text.setTextFormat(Qt.PlainText)
        lay.addWidget(text)
        row = QHBoxLayout()
        allow = QPushButton("✓ Allow")
        allow.setObjectName("allow")
        deny = QPushButton("✗ Deny")
        deny.setObjectName("deny")
        for btn, ok in ((allow, True), (deny, False)):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(not card["busy"])
            btn.clicked.connect(lambda _=False, i=card["id"], a=ok: self.on_pick(i, a))
            row.addWidget(btn)
        row.addStretch(1)
        if card.get("why"):
            why = QLabel(card["why"])
            why.setObjectName("automodMeta")
            row.addWidget(why)
        lay.addLayout(row)
        self._widgets[card["id"]] = {"count": count, "card": card}
        return frame

    def tick(self) -> None:
        now = time.monotonic()
        for item in self._widgets.values():
            card = item["card"]
            if card["busy"]:
                item["count"].setText("Allowing…" if card.get("allow") else "Denying…")
            else:
                item["count"].setText(f"{max(0, int(card['deadline'] - now + 0.99))}s")


class OverlayWindow(QWidget):
    emote_ready = Signal()

    def __init__(self, hub: Any, server: Any) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.hub = hub
        self.server = server
        self.settings = hub.settings
        self.setWindowTitle(config.APP_NAME)
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(240, 180)
        self.setFocusPolicy(Qt.StrongFocus)

        self.images = EmoteFiles(config.cache_dir() / "emotes", self.emote_ready.emit)
        self.emote_ready.connect(self._schedule_rebuild)
        self._rebuild_timer = QTimer(self, singleShot=True, interval=300)
        self._rebuild_timer.timeout.connect(self._rebuild_views)

        self.hwnd = 0
        self.solid: Optional[bool] = None
        self._want_solid = False
        self._want_since = 0.0
        self._clickthrough: Optional[bool] = None
        self._alt_tab_latched = False
        self._idle_hidden = False
        self._hidden_rect: Optional[QRect] = None
        self._hover_since = 0.0
        self._hover_armed = False
        self._last_activity = time.monotonic()
        self._drag_offset: Optional[QPoint] = None
        self._obs_seen = False
        self._obs_misses = 0
        self._pending_send = ""
        self._quitting = False
        self.live_info: Optional[dict[str, Any]] = None
        self.login_dialog: Optional[LoginDialog] = None
        self.rename_dialog: Optional[RenameDialog] = None
        self.quit_dialog: Optional[ConfirmQuitDialog] = None

        self._build()
        self._restore_geometry()
        self._build_tray()
        self._apply_tab(hub.tab)
        self._refresh_account()
        self._apply_look(False)

        self._poll_timer = QTimer(self, interval=30 if win32.IS_WINDOWS else 100)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        self._slow_timer = QTimer(self, interval=1000)
        self._slow_timer.timeout.connect(self._slow_poll)
        self._slow_timer.start()
        self._hover_timer = QTimer(self, interval=100)
        self._hover_timer.timeout.connect(self._poll_hover_reveal)
        self._hover_timer.start()
        self._obs_timer = QTimer(self, interval=OBS_POLL_MS)
        self._obs_timer.timeout.connect(self._poll_obs)
        self._obs_timer.start()

    # ------------------------------------------------------------------ UI
    def _icon_button(self, text: str, tip: str, slot, name: str = "icon") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(name)
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        self.header = QFrame(objectName="header")
        self.header.setFixedHeight(32)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 4, 0)
        hl.setSpacing(2)
        self.title = QLabel("Live Chat", objectName="title")
        self.status = QLabel("idle", objectName="status")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        hl.addWidget(self.title)
        hl.addWidget(self.status, 1)
        self.gear_btn = self._icon_button("⚙", "Settings", lambda: self.toggle_panel())
        hl.addWidget(self.gear_btn)
        hl.addWidget(self._icon_button("−", "Hide until the next message", self.hide_overlay))
        hl.addWidget(self._icon_button("✕", "Close", self.confirm_quit, "close"))
        for w in (self.header, self.title, self.status):
            w.installEventFilter(self)
        root.addWidget(self.header)

        # Tabs
        self.tabs = QFrame(objectName="tabs")
        self.tabs.setFixedHeight(30)
        tl = QHBoxLayout(self.tabs)
        tl.setContentsMargins(6, 3, 6, 3)
        tl.setSpacing(3)
        self.tab_buttons: dict[str, QPushButton] = {}
        for tab, label in (("twitch", "Twitch"), ("tiktok", "TikTok"), ("merge", "Merge")):
            btn = QPushButton(label, objectName="tab")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(lambda _=False, t=tab: self.hub.set_tab(t))
            self.tab_buttons[tab] = btn
            tl.addWidget(btn)
        self.link_btn = self._icon_button(
            LINK_TEXT, f"Open {LINK_TEXT}", lambda: webbrowser.open_new_tab(LINK_URL), name="link"
        )
        tl.addWidget(self.link_btn)
        tl.addStretch(1)
        self.tabs.installEventFilter(self)
        root.addWidget(self.tabs)

        # Settings panel (scrolls when the window is small)
        self.panel_scroll = QScrollArea()
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setFrameShape(QFrame.NoFrame)
        self.panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.panel_scroll.setWidget(self._build_panel())
        self.panel_scroll.setVisible(not (self.settings["channel"] or self.settings["tiktok_user"]))
        root.addWidget(self.panel_scroll)

        # AutoMod cards
        self.automod_box = AutoModBox(self.hub.automod_resolve)
        root.addWidget(self.automod_box)

        # Chat
        self.chat_area = QFrame(objectName="chatArea")
        self.stack = QStackedLayout(self.chat_area)
        self.views: dict[str, ChatView] = {}
        for tab in TABS:
            view = ChatView(tab, self.images.path_for)
            view.name_clicked.connect(self.prompt_rename)
            view.follow_changed.connect(lambda _f, t=tab: self._update_pause_chip())
            self.views[tab] = view
            self.stack.addWidget(view)
        root.addWidget(self.chat_area, 1)
        self.chat_area.installEventFilter(self)

        self.pause_chip = QFrame(self.chat_area, objectName="pauseChip")
        pl = QHBoxLayout(self.pause_chip)
        pl.setContentsMargins(4, 2, 4, 2)
        pl.setSpacing(4)
        up = self._icon_button("↑", "Oldest", lambda: self.views[self.hub.tab].scroll_to_top())
        down = self._icon_button("↓", "Back to live chat", lambda: self.views[self.hub.tab].scroll_to_end())
        pl.addWidget(up)
        pl.addWidget(QLabel("Chat paused"))
        pl.addWidget(down)
        self.pause_chip.hide()

        # Send box
        self.compose = QFrame(objectName="compose")
        cl = QHBoxLayout(self.compose)
        cl.setContentsMargins(8, 6, 14, 6)
        lbl = QLabel("Twitch", objectName="composeLabel")
        cl.addWidget(lbl)
        self.compose_edit = QLineEdit()
        self.compose_edit.setPlaceholderText("Send a message")
        self.compose_edit.setMaxLength(500)
        self.compose_edit.setFocusPolicy(Qt.ClickFocus)
        self.compose_edit.returnPressed.connect(self.send_chat)
        cl.addWidget(self.compose_edit, 1)
        self.send_btn = QPushButton("Send", objectName="primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_chat)
        self.send_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.send_btn.customContextMenuRequested.connect(self._send_menu)
        cl.addWidget(self.send_btn)
        self.compose.hide()
        root.addWidget(self.compose)

        # Resize edges (shown while the overlay is active)
        self.grips = [
            _Grip(self, Qt.RightEdge, Qt.SizeHorCursor),
            _Grip(self, Qt.BottomEdge, Qt.SizeVerCursor),
            _Grip(self, Qt.RightEdge | Qt.BottomEdge, Qt.SizeFDiagCursor),
        ]

    def _build_panel(self) -> QFrame:
        panel = QFrame(objectName="panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(5)

        # Connect row
        row = QHBoxLayout()
        self.prefix = QLabel("#")
        self.channel_edit = QLineEdit()
        self.channel_edit.setFocusPolicy(Qt.ClickFocus)
        self.channel_edit.returnPressed.connect(self.connect_current)
        self.connect_btn = QPushButton("Connect", objectName="primary")
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(self.connect_current)
        self.merge_note = QLabel("Merge shows Twitch + TikTok together")
        row.addWidget(self.prefix)
        row.addWidget(self.channel_edit, 1)
        row.addWidget(self.connect_btn)
        row.addWidget(self.merge_note, 1)
        lay.addLayout(row)

        # Look
        row = QHBoxLayout()
        row.addWidget(QLabel("Opacity"))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(round(self.settings["opacity"] * 100))
        self.opacity.valueChanged.connect(self._on_opacity)
        row.addWidget(self.opacity, 1)
        self.boxes_check = QCheckBox("Text boxes")
        self.boxes_check.setChecked(self.settings["text_backing"])
        self.boxes_check.setToolTip("A dark box behind each message so it's easy to read over games")
        self.boxes_check.toggled.connect(lambda on: self.hub.update_settings({"text_backing": on}))
        row.addWidget(self.boxes_check)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Text size"))
        smaller = QPushButton("A−")
        bigger = QPushButton("A+")
        for btn, step in ((smaller, -1), (bigger, 1)):
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=step: self._change_font(s))
            row.addWidget(btn)
        self.size_label = QLabel(str(self.settings["font_size"]))
        row.addWidget(self.size_label)
        row.addStretch(1)
        self.idle_check = QCheckBox("Hide when chat is quiet")
        self.idle_check.setChecked(self.settings["idle_hide"])
        self.idle_check.toggled.connect(lambda on: self.hub.update_settings({"idle_hide": on}))
        row.addWidget(self.idle_check)
        lay.addLayout(row)

        # Twitch login
        row = QHBoxLayout()
        self.login_btn = QPushButton("Log in to Twitch")
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self.open_login)
        row.addWidget(self.login_btn)
        self.login_label = QLabel("")
        row.addWidget(self.login_label, 1)
        lay.addLayout(row)

        # OBS
        row = QHBoxLayout()
        self.obs_label = QLabel("")
        self.obs_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.obs_label, 1)
        copy_btn = QPushButton("Copy OBS link")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self.copy_obs_link)
        row.addWidget(copy_btn)
        lay.addLayout(row)
        self._update_obs_label()
        self.close_obs_check = QCheckBox("Close when OBS closes")
        self.close_obs_check.setChecked(self.settings["close_with_obs"])
        self.close_obs_check.toggled.connect(lambda on: self.hub.update_settings({"close_with_obs": on}))
        lay.addWidget(self.close_obs_check)

        # Share
        row = QHBoxLayout()
        self.share_check = QCheckBox("Share when I go live")
        self.share_check.setChecked(self.settings["share_on_live"])
        self.share_check.setToolTip("Opens X and Facebook posts when your Twitch stream starts")
        self.share_check.toggled.connect(lambda on: self.hub.update_settings({"share_on_live": on}))
        row.addWidget(self.share_check, 1)
        share_btn = QPushButton("Share now")
        share_btn.setCursor(Qt.PointingHandCursor)
        share_btn.clicked.connect(lambda: self.open_share_posts(self.live_info))
        row.addWidget(share_btn)
        lay.addLayout(row)
        for label, key in (("Live", "share_text"), ("Offline", "share_text_offline")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setFixedWidth(42)
            row.addWidget(name)
            edit = QLineEdit(self.settings[key])
            edit.editingFinished.connect(lambda e=edit, k=key: self.hub.update_settings({k: e.text()}))
            row.addWidget(edit, 1)
            lay.addLayout(row)

        tip = QLabel(
            "Tip: click a name to give someone a nickname. While the chat is see-through, "
            "hold Alt or Shift to click it."
        )
        tip.setObjectName("tip")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        return panel

    def _build_tray(self) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip(config.APP_NAME)
        menu = QMenu()
        menu.addAction("Show overlay", self.show_overlay)
        menu.addAction("Settings", lambda: (self.show_overlay(), self.toggle_panel(True)))
        menu.addAction("Copy OBS link", self.copy_obs_link)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.show_overlay() if reason == QSystemTrayIcon.Trigger else None
        )
        tray.show()
        self.tray = tray
        self._tray_menu = menu

    # ------------------------------------------------------------ geometry
    def _restore_geometry(self) -> None:
        x, y, w, h = self.settings["geometry"]
        rect = QRect(x, y, w, h)
        screens = QGuiApplication.screens()
        if not any(s.availableGeometry().intersects(rect.adjusted(20, 20, -20, -20)) for s in screens):
            avail = QGuiApplication.primaryScreen().availableGeometry()
            rect.moveTopLeft(avail.topLeft() + QPoint(80, 80))
        self.setGeometry(rect)

    def _remember_geometry(self) -> None:
        if self._idle_hidden or not self.isVisible():
            return
        g = self.geometry()
        self.settings.set("geometry", [g.x(), g.y(), g.width(), g.height()])

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._remember_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._remember_geometry()
        w, h = self.width(), self.height()
        self.grips[0].setGeometry(w - 5, 32, 5, h - 32 - 14)
        self.grips[1].setGeometry(0, h - 5, w - 14, 5)
        self.grips[2].setGeometry(w - 14, h - 14, 14, 14)
        for grip in self.grips:
            grip.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.hwnd = int(self.winId())
        self._clickthrough = None

    # -------------------------------------------------------------- events
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj in (self.header, self.title, self.status):
            etype = event.type()
            if etype == QEvent.MouseButtonDblClick:
                self.toggle_panel()
                return True
            if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                handle = self.windowHandle()
                if handle is None or not handle.startSystemMove():
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if etype == QEvent.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if etype == QEvent.MouseButtonRelease:
                self._drag_offset = None
        elif event.type() == QEvent.Resize:
            # The tabs get resized while the window is still being built, before chat_area exists.
            if obj is self.tabs:
                self._fit_link()
            elif obj is self.chat_area:
                self._place_pause_chip()
        return super().eventFilter(obj, event)

    def _fit_link(self) -> None:
        """Show the pink link only when it fits beside the tabs, so it never squeezes them."""
        layout = self.tabs.layout()
        margins = layout.contentsMargins()
        need = margins.left() + margins.right() + self.link_btn.sizeHint().width()
        need += sum(btn.sizeHint().width() + layout.spacing() for btn in self.tab_buttons.values())
        self.link_btn.setVisible(self.tabs.width() >= need)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Typing anywhere on the active overlay goes into the send box.
        text = event.text()
        if (
            text and text.isprintable() and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
            and self.compose.isVisible() and not self.compose_edit.hasFocus()
        ):
            self.compose_edit.setFocus()
            self.compose_edit.insert(text)
            return
        if event.key() == Qt.Key_Escape:
            self.toggle_panel(False)
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # When you click the overlay, typing should go to the send box, not a
        # random button or checkbox in the settings.
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            if not isinstance(QApplication.focusWidget(), QLineEdit):
                self.setFocus(Qt.ActiveWindowFocusReason)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.confirm_quit()

    # -------------------------------------------------------------- the look
    def _apply_look(self, solid: bool) -> None:
        s = self.settings
        self.solid = solid
        alpha = 255 if solid else round(s["opacity"] * 255)
        self.chat_area.setStyleSheet(f"#chatArea {{ background: rgba(14, 14, 16, {alpha}); }}")
        for view in self.views.values():
            view.set_mode(see_through=not solid, boxes=s["text_backing"], font_pt=s["font_size"])
            view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if solid else Qt.ScrollBarAlwaysOff)
        for grip in self.grips:
            grip.setVisible(solid)
        self._update_compose()

    def _on_opacity(self, value: int) -> None:
        self.hub.update_settings({"opacity": value / 100.0})

    def _change_font(self, step: int) -> None:
        size = max(8, min(28, self.settings["font_size"] + step))
        self.size_label.setText(str(size))
        self.hub.update_settings({"font_size": size})

    def _schedule_rebuild(self) -> None:
        if not self._rebuild_timer.isActive():
            self._rebuild_timer.start()

    def _rebuild_views(self) -> None:
        for view in self.views.values():
            if any(seg.get("t") == "emote" for m in view.msgs for seg in m.get("seg") or []):
                view.rebuild()

    # ------------------------------------------------------------- polling
    def _modifier_held(self, over: bool) -> bool:
        """Alt or Shift with the mouse over the overlay (not in a game or Alt+Tab)."""
        if not win32.IS_WINDOWS:
            return False
        alt = win32.key_down(win32.VK_MENU)
        shift = win32.key_down(win32.VK_SHIFT)
        if not alt:
            self._alt_tab_latched = False
        elif win32.key_down(win32.VK_TAB):
            self._alt_tab_latched = True
        if self._alt_tab_latched:
            alt = False
        if not (alt or shift) or not over:
            return False
        return not win32.user_in_game()

    def _focused(self) -> bool:
        if win32.IS_WINDOWS:
            return win32.is_foreground(self.hwnd) or self.isActiveWindow()
        return self.isActiveWindow()

    def _over_interactive(self, pos: QPoint) -> bool:
        zones = [self.header, self.tabs, self.panel_scroll, self.automod_box, self.compose, self.pause_chip]
        zones += [g for g in self.grips]
        for w in zones:
            if w.isVisible() and QRect(w.mapToGlobal(QPoint(0, 0)), w.size()).contains(pos):
                return True
        return False

    def _poll(self) -> None:
        if self._idle_hidden or not self.isVisible():
            return
        pos = QCursor.pos()
        over = self.frameGeometry().contains(pos)
        focused = self._focused()
        hold = self._modifier_held(over)
        want = focused or hold or self.settings["opacity"] >= 0.99
        now = time.monotonic()
        if want != self._want_solid:
            self._want_solid, self._want_since = want, now
        elif want != self.solid and now - self._want_since >= SOLID_DELAY_S:
            self._apply_look(want)
        if win32.IS_WINDOWS:
            self._set_clickthrough(not (focused or hold or self._over_interactive(pos)))

    def _set_clickthrough(self, enabled: bool) -> None:
        native = win32.set_clickthrough(self.hwnd, enabled)
        if native is None and enabled != self._clickthrough:
            handle = self.windowHandle()
            if handle is not None:
                handle.setFlag(Qt.WindowTransparentForInput, enabled)
        self._clickthrough = enabled

    def _slow_poll(self) -> None:
        self.automod_box.tick()
        if win32.IS_WINDOWS and self.isVisible() and not self._idle_hidden:
            allowed = {int(w.winId()) for w in QApplication.topLevelWidgets() if w.isVisible() and w is not self}
            win32.keep_on_top(self.hwnd, allowed)
        self._poll_idle_hide()

    # ----------------------------------------------------------- idle hide
    def _idle_blocked(self) -> bool:
        if any(d is not None and d.isVisible() for d in (self.login_dialog, self.rename_dialog, self.quit_dialog)):
            return True
        if self.automod_box.cards or self.compose_edit.text() or self.compose_edit.hasFocus():
            return True
        if self.panel_scroll.isVisible() or self._focused():
            return True
        # Stay up while the mouse rests on it, unless a game owns the mouse.
        return self.frameGeometry().contains(QCursor.pos()) and not win32.user_in_game()

    def _poll_idle_hide(self) -> None:
        if self._idle_hidden or not self.isVisible() or not self.settings["idle_hide"]:
            return
        if time.monotonic() - self._last_activity >= IDLE_HIDE_S and not self._idle_blocked():
            self._set_idle_hidden(True)

    def _poll_hover_reveal(self) -> None:
        if not self._idle_hidden or self._hidden_rect is None:
            self._hover_since = 0.0
            return
        over = self._hidden_rect.contains(QCursor.pos())
        if not over:
            self._hover_armed = True  # left the spot: coming back means "show it"
            self._hover_since = 0.0
            return
        if not self._hover_armed or win32.user_in_game():
            return
        now = time.monotonic()
        if not self._hover_since:
            self._hover_since = now
        elif now - self._hover_since >= HOVER_REVEAL_S:
            self._last_activity = now
            self._set_idle_hidden(False)

    def _set_idle_hidden(self, hidden: bool) -> None:
        if hidden == self._idle_hidden:
            return
        self._idle_hidden = hidden
        if hidden:
            self._hidden_rect = self.frameGeometry()
            self._hover_armed = False
            self.hide()
        else:
            self.show()
            self._apply_look(False)
        if self.server is not None:
            self.server.set_visible(not hidden)

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()
        if self._idle_hidden:
            self._set_idle_hidden(False)

    def hide_overlay(self) -> None:
        self._set_idle_hidden(True)

    def show_overlay(self) -> None:
        self._last_activity = time.monotonic()
        if self._idle_hidden:
            self._set_idle_hidden(False)
        elif not self.isVisible():
            self.show()

    # ----------------------------------------------------- close with OBS
    def _poll_obs(self) -> None:
        if not self.settings["close_with_obs"]:
            return
        running = win32.obs_running()
        if running:
            self._obs_seen, self._obs_misses = True, 0
        elif running is False and self._obs_seen:
            self._obs_misses += 1
            if self._obs_misses >= 2:  # gone for ~6s: OBS really closed
                self.quit()

    # ------------------------------------------------------------ actions
    def toggle_panel(self, show: Optional[bool] = None) -> None:
        show = not self.panel_scroll.isVisible() if show is None else show
        self.panel_scroll.setVisible(show)
        if show:
            self.panel_scroll.setMaximumHeight(max(140, int(self.height() * 0.6)))
            if not self.channel_edit.text():
                self.channel_edit.setFocus()

    def connect_current(self) -> None:
        tab = self.hub.tab
        if tab in ("twitch", "tiktok"):
            self.hub.connect(tab, self.channel_edit.text())
            self.views[tab].clear_all()

    def copy_obs_link(self) -> None:
        if self.server is not None:
            QGuiApplication.clipboard().setText(self.server.url)
            self.hub.add_line(
                self.hub.tab if self.hub.tab != "merge" else "twitch",
                "system",
                f"Copied the OBS link: {self.server.url}",
                activity=False,
            )

    def _update_obs_label(self) -> None:
        if self.server is None:
            self.obs_label.setText("OBS page is off")
        else:
            self.obs_label.setText(f"OBS: {self.server.url}")

    def open_login(self) -> None:
        if self.login_dialog is not None and self.login_dialog.isVisible():
            self.login_dialog.raise_()
            self.login_dialog.activateWindow()
            return
        self.login_dialog = LoginDialog(self, self.hub)
        self.login_dialog.destroyed.connect(lambda: setattr(self, "login_dialog", None))
        self.login_dialog.place_near(self)
        self.login_dialog.show()
        self.login_dialog.activateWindow()

    def prompt_rename(self, key: str, original: str) -> None:
        if self.rename_dialog is not None:
            self.rename_dialog.close()
        current = self.hub.display_name(key, original)
        self.rename_dialog = RenameDialog(self, original, current, lambda alias: self.hub.rename(key, alias, original))
        self.rename_dialog.destroyed.connect(lambda: setattr(self, "rename_dialog", None))
        self.rename_dialog.place_near(self)
        self.rename_dialog.show()
        self.rename_dialog.activateWindow()

    def confirm_quit(self) -> None:
        if self.quit_dialog is not None and self.quit_dialog.isVisible():
            self.quit_dialog.raise_()
            return
        self.quit_dialog = ConfirmQuitDialog(self, self.quit)
        self.quit_dialog.destroyed.connect(lambda: setattr(self, "quit_dialog", None))
        self.quit_dialog.place_near(self)
        self.quit_dialog.show()
        self.quit_dialog.activateWindow()

    def quit(self) -> None:
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        self.images.shutdown()
        QApplication.quit()

    def open_share_posts(self, info: Optional[dict[str, Any]]) -> None:
        channel = self.settings["channel"]
        live = bool(info)
        template = self.settings["share_text"] if live else self.settings["share_text_offline"]
        text = share_text(template, channel, info or {})
        link = f"https://twitch.tv/{channel}" if channel else "https://twitch.tv"
        # Facebook won't take pre-filled words, so they go on the clipboard to paste.
        QGuiApplication.clipboard().setText(text)
        webbrowser.open_new_tab("https://x.com/intent/tweet?text=" + quote(text, safe=""))
        webbrowser.open_new_tab("https://www.facebook.com/sharer/sharer.php?u=" + quote(link, safe=""))

    # ------------------------------------------------------------ sending
    def _update_compose(self) -> None:
        twitchy = self.hub.tab in ("twitch", "merge") and bool(self.settings["channel"])
        show = (twitchy and bool(self.solid)) or bool(self.compose_edit.text()) or self.compose_edit.hasFocus()
        self.compose.setVisible(show)

    def _refresh_account(self) -> None:
        state = self.hub.account_state()
        logged = state["logged_in"]
        self.send_btn.setText("Send" if logged else "Log in")
        self.login_btn.setText("Twitch login…" if logged else "Log in to Twitch")
        self.login_label.setText(f"as {state['login']}" if logged and state["login"] else "")

    def send_chat(self) -> None:
        text = self.compose_edit.text().strip()
        if not self.hub.account_state()["logged_in"]:
            self._pending_send = text
            self.open_login()
            return
        if not text:
            return
        err = self.hub.send_chat(text)
        if err:
            if "Log in" in err:
                self._pending_send = text
                self.open_login()
            else:
                self.hub.add_line("twitch", "system", err, activity=False)
            return
        self.compose_edit.clear()

    def _send_menu(self, pos: QPoint) -> None:
        if not self.hub.account_state()["logged_in"]:
            return
        menu = QMenu(self)
        menu.addAction("Log out of Twitch", self.hub.logout)
        menu.exec(self.send_btn.mapToGlobal(pos))

    def _flush_pending(self) -> None:
        text, self._pending_send = self._pending_send, ""
        if not text:
            return
        # Chat reconnects with the new login first; send once it's ready.
        def attempt(tries: int = 0) -> None:
            err = self.hub.send_chat(text)
            if err and tries < 10 and ("connecting" in err.lower() or "log in" in err.lower() or "not connected" in err.lower()):
                QTimer.singleShot(800, lambda: attempt(tries + 1))
            elif err:
                self.compose_edit.setText(text)
                self.hub.add_line("twitch", "system", err, activity=False)
            else:
                self.compose_edit.clear()

        QTimer.singleShot(800, attempt)

    # ---------------------------------------------------------- tabs/status
    def _apply_tab(self, tab: str) -> None:
        self.stack.setCurrentWidget(self.views[tab])
        for name, btn in self.tab_buttons.items():
            btn.setProperty("active", tab if name == tab else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        is_merge = tab == "merge"
        for w in (self.prefix, self.channel_edit, self.connect_btn):
            w.setVisible(not is_merge)
        self.merge_note.setVisible(is_merge)
        if tab == "twitch":
            self.prefix.setText("#")
            self.channel_edit.setPlaceholderText("Twitch channel")
            self.channel_edit.setText(self.settings["channel"])
        elif tab == "tiktok":
            self.prefix.setText("@")
            self.channel_edit.setPlaceholderText("TikTok username")
            self.channel_edit.setText(self.settings["tiktok_user"])
        self._update_status()
        self._update_pause_chip()
        self._update_compose()

    def _update_status(self) -> None:
        st = self.hub.status
        tab = self.hub.tab
        if tab == "merge":
            text = f"Twitch: {st['twitch']} · TikTok: {st['tiktok']}"
        else:
            text = st.get(tab, "idle")
        metrics = self.status.fontMetrics()
        self.status.setText(metrics.elidedText(text, Qt.ElideRight, max(40, self.status.width())))
        self.status.setToolTip(text)
        if tab in ("twitch", "tiktok"):
            connected = st.get(tab, "").lower().startswith("connected")
            self.connect_btn.setText("Reconnect" if connected else "Connect")

    def _place_pause_chip(self) -> None:
        self.pause_chip.adjustSize()
        size = self.pause_chip.sizeHint()
        area = self.chat_area.size()
        self.pause_chip.setGeometry(
            (area.width() - size.width()) // 2, area.height() - size.height() - 8, size.width(), size.height()
        )
        self.pause_chip.raise_()

    def _update_pause_chip(self) -> None:
        view = self.views[self.hub.tab]
        paused = not view.follow and bool(view.msgs)
        self.pause_chip.setVisible(paused)
        if paused:
            self._place_pause_chip()

    # ------------------------------------------------------- hub listener
    def on_hub_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "message":
            for tab in data["tabs"]:
                self.views[tab].add(data["msg"])
        elif event == "remove":
            ids = set(data["ids"])
            for view in self.views.values():
                view.remove_ids(ids)
        elif event == "status":
            self._update_status()
        elif event == "tab":
            self._apply_tab(data["tab"])
        elif event == "alias":
            for view in self.views.values():
                view.update_alias(data["key"], data.get("show") or "")
        elif event == "automod":
            self.automod_box.set_cards(data["cards"], data["waiting"])
        elif event == "alert":
            if not win32.beep():
                QApplication.beep()
        elif event == "activity":
            self.note_activity()
        elif event == "account":
            self._refresh_account()
            if self.login_dialog is not None:
                self.login_dialog.on_account(data)
            if data.get("logged_in") and data.get("login") and not data.get("error") and not data.get("device"):
                self._flush_pending()
        elif event == "settings":
            if self.solid is not None:
                self._apply_look(self.solid)
        elif event == "live":
            self.live_info = data.get("info")
        elif event == "went_live":
            if self.settings["share_on_live"]:
                self.open_share_posts(data.get("info") or {})
