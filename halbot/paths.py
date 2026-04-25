"""Filesystem paths used by daemon + tray.

`frozen` build (PyInstaller) → %ProgramData%\\Halbot.
Source run → ./_dev_data/ at repo root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    if _frozen():
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        p = Path(base) / "Halbot"
    else:
        p = Path(__file__).resolve().parent.parent / "_dev_data"
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
