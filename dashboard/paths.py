"""Resolve the frontend web-asset directory for source + frozen runs."""

from __future__ import annotations

import sys
from pathlib import Path


def web_dir() -> Path:
    """Return dir containing index.html for pywebview to load.

    Frozen (PyInstaller): <_MEIPASS>/dashboard/web
    Source run:           <repo>/frontend/dist
    Step-2 fallback:      <this_file>/_stub.html (no frontend yet)
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "dashboard" / "web"
    here = Path(__file__).resolve().parent
    dist = here.parent / "frontend" / "dist"
    if (dist / "index.html").exists():
        return dist
    return here  # _stub.html lives next to this module
