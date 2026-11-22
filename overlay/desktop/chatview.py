"""One tab of chat, drawn with Qt rich text."""

from __future__ import annotations

import html
from typing import Any, Callable, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QTextBrowser

from . import theme

MAX_LINES = 150
PathFor = Callable[[str, str], Optional[Any]]

_KIND_COLOR = {
    "event": theme.EVENT,
    "points": theme.POINTS,
    "system": theme.MUTED,
    "warn": theme.WARN,
    "error": theme.ERROR,
}


class ChatView(QTextBrowser):
    name_clicked = Signal(str, str)  # alias key, original name
    follow_changed = Signal(bool)

    def __init__(self, tab: str, path_for: PathFor) -> None:
        super().__init__()
        self.tab = tab
        self.path_for = path_for
        self.msgs: list[dict[str, Any]] = []
        self.see_through = True
        self.boxes = True
        self.font_pt = 11
        self.follow = True
        self._stripe = False

        self.setFrameShape(QFrame.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(8)
        palette = self.palette()
        palette.setColor(QPalette.Text, QColor(theme.TEXT))
        palette.setColor(QPalette.Base, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.anchorClicked.connect(self._on_anchor)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.verticalScrollBar().rangeChanged.connect(self._on_range)
        self.apply_font()

    # ----- look -----
    def apply_font(self) -> None:
        font = QFont(theme.FONT)
        font.setPointSize(self.font_pt)
        font.setFamilies([theme.FONT, theme.EMOJI_FONT])
        self.document().setDefaultFont(font)

    def set_mode(self, *, see_through: bool, boxes: bool, font_pt: int) -> None:
        if (see_through, boxes, font_pt) == (self.see_through, self.boxes, self.font_pt):
            return
        self.see_through, self.boxes, self.font_pt = see_through, boxes, font_pt
        self.apply_font()
        self.rebuild()

    def emote_px(self) -> int:
        return max(18, round(self.font_pt * 96 / 72 * 1.65))

    # ----- html for one line -----
    def line_parts(self, msg: dict[str, Any], stripe: bool) -> tuple[str, str]:
        """(block background colour or "", inner html) for one chat line."""
        box_on = self.see_through and self.boxes
        block_bg = ""
        if msg.get("mention"):
            block_bg = theme.MENTION_BG if not self.see_through else ""
        elif not self.see_through and stripe:
            block_bg = theme.ROW_B
        span_bg = ""
        if msg.get("mention") and self.see_through:
            span_bg = "rgba(88,40,170,230)"
        elif box_on:
            r, g, b = theme.BOX_RGB
            span_bg = f"rgba({r},{g},{b},220)"

        parts: list[str] = []
        if self.tab == "merge":
            bar = theme.TWITCH if msg.get("p") == "twitch" else theme.TIKTOK
            parts.append(f'<span style="color:{bar}">▍</span>')
        user = msg.get("u")
        if user:
            color = html.escape(user.get("color") or theme.MUTED)
            key = QUrl.toPercentEncoding(user.get("key") or "").data().decode()
            orig = QUrl.toPercentEncoding(user.get("name") or "").data().decode()
            name = html.escape(user.get("show") or user.get("name") or "")
            name_html = (
                f'<a href="user:{key}?{orig}" style="color:{color};text-decoration:none;'
                f'font-weight:700">{name}</a>'
            )
            body = self._segments_html(msg.get("seg") or [])
            if msg.get("k") == "action":
                parts.append(
                    f'<span style="color:{color}">* </span>{name_html}'
                    f'<span style="color:{color};font-style:italic"> {body}</span>'
                )
            else:
                parts.append(f'{name_html}<span style="color:{theme.TEXT}">: {body}</span>')
        else:
            color = _KIND_COLOR.get(msg.get("k", ""), theme.TEXT)
            weight = "500" if msg.get("k") == "system" else "700"
            text = html.escape(msg.get("text") or "")
            parts.append(f'<span style="color:{color};font-weight:{weight}">{text}</span>')
        inner = "".join(parts)
        if span_bg:
            inner = f'<span style="background-color:{span_bg}">&nbsp;{inner}&nbsp;</span>'
        return block_bg, inner

    def _insert(self, cursor: QTextCursor, msg: dict[str, Any], stripe: bool, first: bool) -> None:
        block_bg, inner = self.line_parts(msg, stripe)
        fmt = QTextBlockFormat()
        fmt.setBottomMargin(4)
        fmt.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        if block_bg:
            fmt.setBackground(QColor(block_bg))
        if first:
            cursor.setCharFormat(QTextCharFormat())
        else:
            cursor.insertBlock(fmt, QTextCharFormat())
        cursor.insertHtml(inner)
        # insertHtml can reset the block's look, so set it again afterwards.
        cursor.setBlockFormat(fmt)

    def _segments_html(self, segs: list[dict[str, str]]) -> str:
        out: list[str] = []
        px = self.emote_px()
        for seg in segs:
            t = seg.get("t")
            if t == "emote":
                path = self.path_for(seg.get("key", ""), seg.get("url", ""))
                if path is not None:
                    src = QUrl.fromLocalFile(str(path)).toString()
                    out.append(
                        f'<img src="{html.escape(src)}" height="{px}" style="vertical-align:middle" '
                        f'title="{html.escape(seg.get("name", ""))}">'
                    )
                else:
                    out.append(html.escape(seg.get("name", "")))
            elif t == "mention":
                out.append(f'<b style="color:#ffffff">{html.escape(seg.get("v", ""))}</b>')
            else:
                out.append(html.escape(seg.get("v", "")))
        return "".join(out)

    # ----- adding and removing -----
    def add(self, msg: dict[str, Any]) -> None:
        follow = self.follow
        self.msgs.append(msg)
        self._stripe = not self._stripe
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        self._insert(cursor, msg, self._stripe, first=len(self.msgs) == 1)
        if len(self.msgs) > MAX_LINES:
            self.msgs.pop(0)
            first = QTextCursor(self.document().firstBlock())
            first.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
            first.removeSelectedText()
        if follow:
            self.scroll_to_end()

    def set_messages(self, msgs: list[dict[str, Any]]) -> None:
        self.msgs = list(msgs)[-MAX_LINES:]
        self.rebuild()

    def remove_ids(self, ids: set[int]) -> None:
        before = len(self.msgs)
        self.msgs = [m for m in self.msgs if m.get("id") not in ids]
        if len(self.msgs) != before:
            self.rebuild()

    def clear_all(self) -> None:
        self.msgs = []
        self.rebuild()

    def update_alias(self, key: str, show: str) -> None:
        changed = False
        for m in self.msgs:
            user = m.get("u")
            if user and user.get("key") == key:
                user["show"] = show or user.get("name")
                changed = True
        if changed:
            self.rebuild()

    def rebuild(self) -> None:
        bar = self.verticalScrollBar()
        follow, value = self.follow, bar.value()
        self.document().clear()
        cursor = QTextCursor(self.document())
        stripe = False
        for i, m in enumerate(self.msgs):
            stripe = not stripe
            self._insert(cursor, m, stripe, first=i == 0)
        self._stripe = stripe
        if follow:
            self.scroll_to_end()
        else:
            bar.setValue(value)

    # ----- scrolling -----
    def scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._set_follow(True)

    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)

    def _set_follow(self, follow: bool) -> None:
        if follow != self.follow:
            self.follow = follow
            self.follow_changed.emit(follow)

    def _on_scroll(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._set_follow(value >= bar.maximum() - 4)

    def _on_range(self, _lo: int, hi: int) -> None:
        if self.follow:
            self.verticalScrollBar().setValue(hi)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt name
        follow = self.follow
        super().resizeEvent(event)
        if follow:
            self.scroll_to_end()

    def _on_anchor(self, url: QUrl) -> None:
        if url.scheme() == "user":
            key = QUrl.fromPercentEncoding(url.path(QUrl.FullyEncoded).encode())
            name = QUrl.fromPercentEncoding((url.query(QUrl.FullyEncoded) or "").encode()) or key
            self.name_clicked.emit(key, name)
