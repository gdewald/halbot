"""Entry point for `python -m tray`.

Routes the optional `--dashboard` flag to the pywebview window
(spawned from the tray menu in a separate subprocess) and otherwise
launches the pystray icon.

Always invoked via `pythonw.exe -m tray`.
"""

from __future__ import annotations

import sys


def _entry() -> int:
    if "--dashboard" in sys.argv:
        from dashboard.app import main as dashboard_main
        return dashboard_main()
    from tray.tray import main as tray_main
    return tray_main()


if __name__ == "__main__":
    raise SystemExit(_entry())
