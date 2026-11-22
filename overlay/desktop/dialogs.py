"""Small pop-up windows: nickname, Twitch login, and "close?"."""

from __future__ import annotations

import webbrowser
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..sources.twitch_api import TOKEN_GENERATOR_URL
from . import theme

DEV_CONSOLE_URL = "https://dev.twitch.tv/console/apps"


def _label(text: str, name: str = "dialogText", wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    lbl.setTextFormat(Qt.PlainText)
    return lbl


def _button(text: str, name: str = "", on_click: Optional[Callable[[], None]] = None) -> QPushButton:
    btn = QPushButton(text)
    if name:
        btn.setObjectName(name)
    btn.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


class _Dialog(QDialog):
    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 14, 16, 14)
        self.layout_.setSpacing(8)

    def place_near(self, widget: QWidget) -> None:
        self.adjustSize()
        geo = widget.frameGeometry()
        self.move(geo.left() + 24, geo.top() + 60)


class RenameDialog(_Dialog):
    def __init__(self, parent: QWidget, original: str, current: str, on_save: Callable[[str], None]) -> None:
        super().__init__(parent, "Nickname")
        self.on_save = on_save
        self.layout_.addWidget(_label(f"Nickname for {original}", "dialogTitle"))
        self.layout_.addWidget(_label("Only you see this, in the overlay and in OBS."))
        self.edit = QLineEdit(current if current != original else "")
        self.edit.setPlaceholderText(original)
        self.edit.returnPressed.connect(self._save)
        self.layout_.addWidget(self.edit)
        row = QHBoxLayout()
        row.addWidget(_button("Save", "primary", self._save))
        row.addWidget(_button("Clear", "", self._clear))
        row.addStretch(1)
        row.addWidget(_button("Cancel", "", self.close))
        self.layout_.addLayout(row)
        self.setMinimumWidth(280)
        self.edit.setFocus()

    def _save(self) -> None:
        self.on_save(self.edit.text())
        self.close()

    def _clear(self) -> None:
        self.on_save("")
        self.close()


class ConfirmQuitDialog(_Dialog):
    def __init__(self, parent: QWidget, on_quit: Callable[[], None]) -> None:
        super().__init__(parent, "Close overlay?")
        self.layout_.addWidget(_label("Close Live Chat?", "dialogTitle"))
        self.layout_.addWidget(_label("The overlay and its OBS page will stop. Your Twitch login stays saved."))
        row = QHBoxLayout()
        close_btn = _button("Close", "", lambda: (self.close(), on_quit()))
        close_btn.setStyleSheet("background: #c42b1c;")
        row.addWidget(close_btn)
        row.addStretch(1)
        stay = _button("Stay", "", self.close)
        row.addWidget(stay)
        self.layout_.addLayout(row)
        self.setMinimumWidth(300)
        stay.setFocus()


class LoginDialog(_Dialog):
    """Paste a token (quick), or log in with your own Twitch app (no third-party site)."""

    def __init__(self, parent: QWidget, hub: Any) -> None:
        super().__init__(parent, "Twitch login")
        self.hub = hub
        lay = self.layout_
        self.setMinimumWidth(360)

        lay.addWidget(_label("Log in to Twitch", "dialogTitle"))
        lay.addWidget(_label(
            "Reading chat needs no login. Logging in lets you send chat, approve AutoMod "
            "messages, and share when you go live."
        ))

        self.logged_box = QFrame()
        logged = QHBoxLayout(self.logged_box)
        logged.setContentsMargins(0, 0, 0, 0)
        self.logged_label = _label("", "dialogText")
        logged.addWidget(self.logged_label, 1)
        logged.addWidget(_button("Log out", "", self._logout))
        lay.addWidget(self.logged_box)

        # --- quick way: paste a token ---
        lay.addWidget(_label("Quick way", "dialogTitle"))
        lay.addWidget(_label(
            "1. Click Get token and authorize.  2. Copy the Access token.  3. Paste it here."
        ))
        row = QHBoxLayout()
        row.addWidget(_button("Get token", "", lambda: webbrowser.open(TOKEN_GENERATOR_URL)))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Paste the Access token")
        self.token.returnPressed.connect(self._save_token)
        row.addWidget(self.token, 1)
        lay.addLayout(row)
        self.save_btn = _button("Save && log in", "primary", self._save_token)
        lay.addWidget(self.save_btn, 0, Qt.AlignLeft)
        lay.addWidget(_label(
            "twitchtokengenerator.com is made by someone else, not Twitch. Only use it if you trust it. "
            "You can remove its access any time in Twitch Settings → Connections."
        ))

        # --- safer way: your own Twitch app ---
        lay.addWidget(_label("Safer way (your own Twitch app)", "dialogTitle"))
        lay.addWidget(_label(
            "Make a free app at dev.twitch.tv (Category: Chat Bot, Client Type: Public, "
            "OAuth Redirect URL: http://localhost). Paste its Client ID once, then use Log in with Twitch."
        ))
        row2 = QHBoxLayout()
        row2.addWidget(_button("Open dev.twitch.tv", "", lambda: webbrowser.open(DEV_CONSOLE_URL)))
        self.client_id = QLineEdit(str(hub.settings["twitch_client_id"]))
        self.client_id.setPlaceholderText("Client ID")
        row2.addWidget(self.client_id, 1)
        lay.addLayout(row2)
        self.device_btn = _button("Log in with Twitch", "primary", self._device_login)
        lay.addWidget(self.device_btn, 0, Qt.AlignLeft)
        self.code_box = QFrame()
        code_lay = QHBoxLayout(self.code_box)
        code_lay.setContentsMargins(0, 0, 0, 0)
        self.code_label = _label("", "deviceCode", wrap=False)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_lay.addWidget(self.code_label)
        self.code_url = "https://www.twitch.tv/activate"
        code_lay.addWidget(_button("Open twitch.tv/activate", "", lambda: webbrowser.open(self.code_url)))
        code_lay.addStretch(1)
        self.code_box.hide()
        lay.addWidget(self.code_box)

        self.message = _label("", "dialogText")
        lay.addWidget(self.message)
        self.on_account(hub.account_state())

    def _set_message(self, text: str, error: bool = False) -> None:
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {theme.ERROR if error else theme.MUTED};")

    def _save_token(self) -> None:
        if not self.token.text().strip():
            self._set_message("Paste a token first.", error=True)
            return
        self._set_message("Checking the token…")
        self.save_btn.setEnabled(False)
        self.hub.account.login_with_token(self.token.text())

    def _device_login(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id:
            self._set_message("Paste your app's Client ID first.", error=True)
            return
        self.hub.update_settings({"twitch_client_id": client_id})
        self.hub.settings.save()
        self._set_message("Asking Twitch for a login code…")
        self.device_btn.setEnabled(False)
        self.hub.account.start_device_login(client_id)

    def _logout(self) -> None:
        self.hub.logout()

    def on_account(self, state: dict[str, Any]) -> None:
        logged_in = bool(state.get("logged_in"))
        login = state.get("login") or ""
        self.logged_box.setVisible(logged_in)
        self.logged_label.setText(f"Logged in as {login}" if login else "Logged in")
        if state.get("error"):
            self.save_btn.setEnabled(True)
            self.device_btn.setEnabled(True)
            self.code_box.hide()
            self._set_message(state["error"], error=True)
        elif state.get("device"):
            device = state["device"]
            self.code_label.setText(device.get("code", ""))
            self.code_url = device.get("url") or self.code_url
            self.code_box.show()
            self._set_message("Enter this code on the Twitch page that just opened.")
            webbrowser.open(self.code_url)
        elif logged_in and login:
            self.save_btn.setEnabled(True)
            self.device_btn.setEnabled(True)
            self.code_box.hide()
            self.token.clear()
            self._set_message(f"Logged in as {login}.")
        self.adjustSize()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt name
        if self.code_box.isVisible():
            self.hub.account.cancel_device_login()
        super().closeEvent(event)
