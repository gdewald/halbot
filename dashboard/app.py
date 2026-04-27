"""Entry point to open the dashboard window."""

from __future__ import annotations

# Pre-allocate a hidden console BEFORE importing webview / pywebview.
# pywebview pulls in pythonnet -> CLR, and CLR's Console class calls
# AllocConsole() if no console exists. On Win11 22H2+ Windows Terminal
# is the default terminal app and intercepts AllocConsole via COM,
# creating a visible CASCADIA_HOSTING_WINDOW_CLASS frame.
#
# Fix: pre-allocate the console ourselves, hide it, and keep it.
# CLR finds a console already present and skips AllocConsole entirely.
# Windows Terminal creates this console hidden because the tray spawns
# this process with STARTUPINFO.wShowWindow=SW_HIDE (see tray/tray.py).
def _suppress_console() -> None:
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        if not k32.GetConsoleWindow():
            k32.AllocConsole()
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            u32.ShowWindow(hwnd, 0)  # SW_HIDE
        # DON'T FreeConsole: CLR reuses this one instead of calling AllocConsole.
    except Exception:
        pass


_suppress_console()

import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

import webview  # noqa: E402

from .bridge import JsApi  # noqa: E402
from .event_stream import EventStream  # noqa: E402
from .log_stream import LogStream  # noqa: E402
from .paths import web_dir  # noqa: E402

log = logging.getLogger(__name__)

_window_lock = threading.Lock()
_window = None


def open_window() -> None:
    """Open the dashboard window. Second call is a no-op."""
    global _window
    with _window_lock:
        if _window is not None:
            log.info("dashboard window already open; ignoring")
            return

        api = JsApi()
        stream = LogStream()
        api.bind_log_stream(stream)
        events = EventStream()
        api.bind_event_stream(events)

        index = web_dir() / ("index.html" if (web_dir() / "index.html").exists() else "_stub.html")
        if not index.exists():
            raise FileNotFoundError(f"no dashboard HTML found at {index}")

        # Frameless: no native chrome (the white title bar looks out of
        # place against the dark UI). Trade-off: pywebview 6.x has no
        # WM_NCHITTEST hook for frameless EdgeChromium windows, so the
        # window can't be edge-resized. Custom WinTitleBar provides the
        # min/max/close buttons via js_api.
        window = webview.create_window(
            title="halbot",
            url=index.as_uri(),
            js_api=api,
            width=1080, height=680,
            min_size=(720, 480),
            frameless=True,
            easy_drag=True,
            background_color="#0c0c0f",
        )
        api.bind_window(window)
        _window = window

    stream.start()
    events.start()
    try:
        webview.start(gui="edgechromium", debug=False)
    finally:
        events.stop()
        stream.stop()
        with _window_lock:
            _window = None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if "--dev" in sys.argv[1:] or os.environ.get("HALBOT_DASHBOARD_DEV") == "1":
        from .dev_server import serve
        port = int(os.environ.get("HALBOT_DASHBOARD_DEV_PORT", "51199"))
        serve(port=port)
        return 0
    open_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
