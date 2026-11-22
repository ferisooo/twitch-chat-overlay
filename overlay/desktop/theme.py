"""Colours and the Qt style sheet for the desktop window."""

from __future__ import annotations

import sys

CHROME = "#18181b"
PANEL = "#1f1f23"
INPUT = "#0e0e10"
SOLID_BG = "#0e0e10"
ROW_B = "#17171b"
TEXT = "#efeff1"
MUTED = "#adadb8"
FAINT = "#6e6e78"
TWITCH = "#9146ff"
TWITCH_HOVER = "#772ce8"
TIKTOK = "#fe2c55"
PINK = "#ff69b4"
PINK_HOVER = "#ff9ccf"
EVENT = "#c9a6ff"
POINTS = "#ffd56b"
WARN = "#ffb347"
ERROR = "#ff6b6b"
MENTION_BG = "#3a2266"
BOX_RGB = (14, 14, 16)

if sys.platform == "win32":
    FONT = "Segoe UI"
    EMOJI_FONT = "Segoe UI Emoji"
else:
    FONT = "DejaVu Sans"
    EMOJI_FONT = "Noto Color Emoji"

STYLE = f"""
QWidget {{ color: {TEXT}; font-family: "{FONT}", "{EMOJI_FONT}"; font-size: 9pt; }}
#header, #tabs {{ background: {CHROME}; }}
#title {{ font-weight: 700; font-size: 10pt; padding-left: 10px; }}
#status {{ color: {MUTED}; font-size: 8pt; padding-left: 6px; }}
#panel {{ background: {PANEL}; }}
#panel QLabel {{ color: {MUTED}; font-size: 8pt; }}
#panel QLabel#tip {{ color: {FAINT}; }}
#panel QCheckBox {{ font-size: 8pt; spacing: 5px; }}
QLineEdit {{
    background: {INPUT}; border: 1px solid #2a2a30; border-radius: 4px;
    padding: 3px 6px; selection-background-color: {TWITCH};
}}
QLineEdit:focus {{ border-color: {TWITCH}; }}
QPushButton {{
    background: #3a3a3d; border: none; border-radius: 4px; padding: 4px 10px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #4a4a52; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton#primary {{ background: {TWITCH}; }}
QPushButton#primary:hover {{ background: {TWITCH_HOVER}; }}
QPushButton#icon {{
    background: transparent; font-size: 11pt; padding: 0px; min-width: 26px; min-height: 24px;
}}
QPushButton#icon:hover {{ background: #2f2f35; }}
QPushButton#close:hover {{ background: #c42b1c; }}
QPushButton#tab {{
    background: transparent; color: {MUTED}; padding: 3px 9px; font-weight: 700;
}}
QPushButton#tab:hover {{ background: #26262c; color: {TEXT}; }}
QPushButton#tab[active="twitch"] {{ background: {TWITCH}; color: white; }}
QPushButton#tab[active="tiktok"] {{ background: {TIKTOK}; color: white; }}
QPushButton#tab[active="merge"] {{ background: #3a3a3d; color: white; }}
QPushButton#link {{
    background: transparent; color: {PINK}; padding: 3px 6px; font-weight: 700; font-size: 8pt;
}}
QPushButton#link:hover {{ background: #26262c; color: {PINK_HOVER}; }}
QSlider::groove:horizontal {{ height: 4px; background: {INPUT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TWITCH}; width: 12px; margin: -5px 0; border-radius: 6px;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a3a42; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
#automodBox {{ background: {CHROME}; }}
#automodCard {{
    background: #241c33; border: 1px solid {TWITCH}; border-radius: 6px;
}}
#automodCard QLabel {{ background: transparent; }}
#automodTitle {{ color: #bf94ff; font-weight: 700; }}
#automodMeta {{ color: {MUTED}; font-size: 8pt; }}
QPushButton#allow {{ background: #1f7a4d; }}
QPushButton#allow:hover {{ background: #26915c; }}
QPushButton#deny {{ background: #a8322d; }}
QPushButton#deny:hover {{ background: #c23b35; }}
#pauseChip {{ background: {CHROME}; border: 1px solid #3a3a42; border-radius: 12px; }}
#pauseChip QLabel {{ font-size: 8pt; font-weight: 700; }}
#compose {{ background: {CHROME}; }}
#composeLabel {{ color: #bf94ff; font-weight: 700; font-size: 8pt; }}
QDialog {{ background: {CHROME}; }}
QDialog QLabel#dialogTitle {{ font-size: 11pt; font-weight: 700; }}
QDialog QLabel#dialogText {{ color: {MUTED}; font-size: 8pt; }}
QDialog QLabel#deviceCode {{
    font-size: 18pt; font-weight: 800; letter-spacing: 3px; color: white;
    background: {INPUT}; border-radius: 6px; padding: 6px 12px;
}}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid #3a3a42; }}
QMenu {{ background: {PANEL}; border: 1px solid #3a3a42; }}
QMenu::item:selected {{ background: {TWITCH}; }}
"""
