"""Live Chat Overlay. Double-click run.bat to start it (or run: pythonw main.py)."""

import sys


def _missing_qt() -> int:
    msg = (
        "Live Chat Overlay needs PySide6, which isn't installed yet.\n\n"
        'Double-click "install requirements.bat", then start the overlay again.'
    )
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "Live Chat Overlay", 0x30)
    else:
        print(msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.exit(_missing_qt())
    from overlay.app import main

    sys.exit(main())
