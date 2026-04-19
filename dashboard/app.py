"""Entry point to open the dashboard window."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import webview

from .bridge import JsApi
from .log_stream import LogStream
from .paths import web_dir

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

        index = web_dir() / ("index.html" if (web_dir() / "index.html").exists() else "_stub.html")
        if not index.exists():
            raise FileNotFoundError(f"no dashboard HTML found at {index}")

        window = webview.create_window(
            title="halbot",
            url=index.as_uri(),
            js_api=api,
            width=1080, height=680,
            min_size=(720, 480),
            frameless=True,
            easy_drag=False,
            background_color="#0c0c0f",
        )
        api.bind_window(window)
        _window = window

    stream.start()
    try:
        webview.start(gui="edgechromium", debug=False)
    finally:
        stream.stop()
        with _window_lock:
            _window = None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    open_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
