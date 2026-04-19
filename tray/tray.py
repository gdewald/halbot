"""pystray tray app: service control, log viewer, log-level toggle."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from halbot import paths

from . import service_ctl
from .log_viewer import LogViewer
from .mgmt_client import MgmtClient

log = logging.getLogger(__name__)

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), "#1e1e1e")
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill="#4caf50")
    d.text((22, 22), "H", fill="white")
    return img


def _notify(icon, title: str, msg: str) -> None:
    try:
        icon.notify(msg, title)
    except Exception:
        pass


def main() -> int:
    import pystray
    from pystray import MenuItem as Item, Menu

    client = MgmtClient()
    log_path = paths.log_file()
    viewer = LogViewer(log_path)

    def _bg(target, icon, label: str):
        def _run():
            try:
                target()
                _notify(icon, "Halbot", f"{label} ok")
            except Exception as e:
                _notify(icon, "Halbot", f"{label} failed: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def on_start(icon, _item):
        _bg(service_ctl.start, icon, "service start")

    def on_stop(icon, _item):
        _bg(service_ctl.stop, icon, "service stop")

    def on_restart(icon, _item):
        _bg(service_ctl.restart, icon, "service restart")

    def on_open_log(_icon, _item):
        threading.Thread(target=viewer.open, daemon=True).start()

    def make_level_handler(level: str):
        def _h(icon, _item):
            def _run():
                try:
                    client.update_log_level(level)
                    client.persist(["log_level"])
                    _notify(icon, "Halbot", f"log level -> {level}")
                except Exception as e:
                    _notify(icon, "Halbot", f"set level failed: {e}")
            threading.Thread(target=_run, daemon=True).start()
        return _h

    current_level = {"value": "INFO"}

    def _refresh_level_loop():
        import time as _t
        while True:
            try:
                state = client.get_config()
                lvl = state.fields.get("log_level")
                if lvl is not None:
                    current_level["value"] = lvl.value.upper()
            except Exception:
                pass
            _t.sleep(2)

    threading.Thread(target=_refresh_level_loop, daemon=True).start()

    def level_checked(level: str):
        def _c(_item):
            return current_level["value"] == level
        return _c

    def on_reset(icon, _item):
        _bg(lambda: client.reset([]), icon, "reset")

    def on_quit(icon, _item):
        icon.stop()

    level_menu = Menu(*[
        Item(lvl, make_level_handler(lvl), checked=level_checked(lvl), radio=True)
        for lvl in LEVELS
    ])

    service_menu = Menu(
        Item("Start", on_start),
        Item("Stop", on_stop),
        Item("Restart", on_restart),
    )

    menu = Menu(
        Item("Service", service_menu),
        Item("Open log viewer", on_open_log),
        Item("Log level", level_menu),
        Item("Reset overrides", on_reset),
        Menu.SEPARATOR,
        Item("Quit", on_quit),
    )

    icon = pystray.Icon("halbot", _icon_image(), "Halbot", menu)
    icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
