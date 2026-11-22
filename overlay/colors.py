"""Every chatter gets their own name colour that is easy to read on dark."""

from __future__ import annotations

import colorsys
import re

# Hand-picked colours that are easy to tell apart on a dark background.
# After these run out, new chatters get generated colours (never repeats).
UNIQUE_NAME_COLORS = (
    "#FF7B7B", "#4DABF7", "#69DB7C", "#FFD43B", "#DA77F2", "#FF922B",
    "#3BC9DB", "#F783AC", "#A9E34B", "#A992FF", "#FFA8A8", "#63E6BE",
    "#FFC078", "#74C0FC", "#E599F7", "#C0EB75", "#FF8787", "#66D9E8",
    "#FAB005", "#B197FC", "#8CE99A", "#F77FAA", "#A5D8FF", "#FFE066",
)
MIN_NAME_LUMINANCE = 0.35
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _srgb_to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    r = int(hex_color[1:3], 16) / 255.0
    g = int(hex_color[3:5], 16) / 255.0
    b = int(hex_color[5:7], 16) / 255.0
    return 0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g) + 0.0722 * _srgb_to_lin(b)


def readable_name_color(hex_color: str) -> str:
    """Lighten a colour until it is readable on the dark chat."""
    if not _HEX.fullmatch(hex_color or ""):
        return "#ADADB8"
    hex_color = hex_color.upper()
    if relative_luminance(hex_color) >= MIN_NAME_LUMINANCE:
        return hex_color
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    if r + g + b < 40:
        return "#9146FF"
    for _ in range(24):
        r = min(255, int(r + (255 - r) * 0.22))
        g = min(255, int(g + (255 - g) * 0.22))
        b = min(255, int(b + (255 - b) * 0.22))
        candidate = f"#{r:02X}{g:02X}{b:02X}"
        if relative_luminance(candidate) >= MIN_NAME_LUMINANCE:
            return candidate
    return "#EFEFF1"


def generated_name_color(index: int) -> str:
    """Nth colour after the hand-picked list: spread around the colour wheel."""
    hue = ((index * 137.508) % 360) / 360.0
    light = (0.62, 0.72, 0.56)[index % 3]
    while True:
        r, g, b = colorsys.hls_to_rgb(hue, light, 0.80)
        color = f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"
        # Blues and purples look dark at the same lightness: brighten them.
        if relative_luminance(color) >= MIN_NAME_LUMINANCE + 0.03 or light >= 0.9:
            return color
        light += 0.02


class NameColors:
    """Gives each chatter a colour and remembers it between streams."""

    def __init__(self, saved: dict[str, str] | None = None, next_index: int = 0) -> None:
        self.colors: dict[str, str] = dict(saved or {})
        self.next_index = max(int(next_index or 0), 0)
        self.changed = False

    @staticmethod
    def key(platform: str, name: str) -> str:
        return f"{platform}:{str(name).strip().lower()}"

    def color_for(self, platform: str, name: str) -> str:
        key = self.key(platform, name)
        color = self.colors.get(key)
        if color:
            return color
        taken = set(self.colors.values())
        while True:
            n = self.next_index
            self.next_index += 1
            if n < len(UNIQUE_NAME_COLORS):
                color = UNIQUE_NAME_COLORS[n]
            else:
                color = generated_name_color(n - len(UNIQUE_NAME_COLORS))
            if color not in taken:
                break
        self.colors[key] = color
        self.changed = True
        return color
