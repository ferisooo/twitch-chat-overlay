"""Windows-only helpers. Every function is safe to call on other systems
(it just does nothing or returns a neutral answer)."""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
HWND_TOPMOST = -1
GW_HWNDPREV = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_QUIET = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
VK_LBUTTON, VK_SHIFT, VK_MENU, VK_TAB = 0x01, 0x10, 0x12, 0x09
OBS_PROCESS_NAMES = ("obs64.exe", "obs32.exe", "obs.exe")


def _user32():  # pragma: no cover - Windows only
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return user32


_U32 = None


def u32():  # pragma: no cover - Windows only
    global _U32
    if _U32 is None:
        _U32 = _user32()
    return _U32


def set_app_id() -> None:
    """Gives the app its own taskbar/notification identity on Windows."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiveChatOverlay.App")
    except Exception:  # noqa: BLE001
        pass


def set_clickthrough(hwnd: int, enabled: bool) -> Optional[bool]:
    """Let mouse clicks pass through the window to the game underneath.

    Only flips WS_EX_TRANSPARENT, and only when it actually needs to change.
    Returns None when this can't be done natively (then Qt's flag is used).
    """
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        user32 = u32()
        style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        if not style & WS_EX_LAYERED:
            return None
        want = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        if want != style:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want)
        return enabled
    except Exception:  # noqa: BLE001
        return None


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return int(u32().GetForegroundWindow() or 0) == int(hwnd)
    except Exception:  # noqa: BLE001
        return False


def key_down(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(u32().GetAsyncKeyState(vk) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


def user_in_game() -> bool:
    """Best guess that a game has the mouse: the cursor is hidden (mouselook)
    or the window in front covers its whole monitor (fullscreen/borderless)."""
    if not IS_WINDOWS:
        return False
    from ctypes import wintypes

    class CURSORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
        ]

    try:
        user32 = u32()
        cursor = CURSORINFO()
        cursor.cbSize = ctypes.sizeof(CURSORINFO)
        if user32.GetCursorInfo(ctypes.byref(cursor)) and not (cursor.flags & 0x1):
            return True
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(fg, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(fg, ctypes.byref(rect)):
            return False
        monitor = user32.MonitorFromWindow(fg, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        mon = info.rcMonitor
        return rect.left <= mon.left and rect.top <= mon.top and rect.right >= mon.right and rect.bottom >= mon.bottom
    except Exception:  # noqa: BLE001
        return False


def keep_on_top(hwnd: int, allowed_above: set[int]) -> None:
    """Games sometimes push the overlay down. Put it back on top, quietly,
    and only when a window we don't own is above it."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        user32 = u32()
        above = int(user32.GetWindow(hwnd, GW_HWNDPREV) or 0)
        seen = 0
        while above and seen < 16:
            if above not in allowed_above:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_QUIET)
                return
            seen += 1
            above = int(user32.GetWindow(above, GW_HWNDPREV) or 0)
    except Exception:  # noqa: BLE001
        pass


def obs_running() -> Optional[bool]:
    """True/False on Windows; None if we can't tell (then we never auto-close)."""
    if not IS_WINDOWS:
        return None
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    try:
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            if not ok:
                return None
            while ok:
                if entry.szExeFile.lower() in OBS_PROCESS_NAMES:
                    return True
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
            return False
        finally:
            k32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        return None


def beep() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return True
    except Exception:  # noqa: BLE001
        return False
