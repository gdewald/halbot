"""Filesystem paths used by daemon + tray.

Two run modes:
  - **Installed** (under `%ProgramFiles%\\Halbot\\src\\halbot\\paths.py`):
    persistent data lives in `%ProgramData%\\Halbot\\`. Frontend assets
    are at `<src>\\frontend\\dist\\`.
  - **Source** (cloned repo, `uv run python -m halbot.daemon run`):
    data lives in `<repo>\\_dev_data\\` (gitignored). Frontend assets
    at `<repo>\\frontend\\dist\\`.

Detection: if this file resolves under `%ProgramFiles%\\Halbot\\`,
we're installed; otherwise dev mode. No more PyInstaller `_frozen()` /
`sys._MEIPASS` branching -- code runs as plain Python in both modes.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _installed() -> bool:
    """True when the package lives under `%ProgramFiles%\\Halbot\\src\\`."""
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    install_prefix = Path(program_files) / "Halbot"
    try:
        _REPO_ROOT.relative_to(install_prefix)
        return True
    except ValueError:
        return False


def data_dir() -> Path:
    if _installed():
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        p = Path(base) / "Halbot"
    else:
        p = _REPO_ROOT / "_dev_data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return log_dir() / "halbot.log"


def transcript_log_file() -> Path:
    return log_dir() / "transcripts.jsonl"


def events_db() -> Path:
    return data_dir() / "events.db"


def frontend_dist_dir() -> Path:
    """React dashboard build output. Same path in both run modes -- the
    installer mirrors `frontend\\dist\\` next to the package."""
    return _REPO_ROOT / "frontend" / "dist"
