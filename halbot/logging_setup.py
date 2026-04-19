"""Rotating-file logger. Level swappable at runtime."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

from . import paths

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP = 5
_FMT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"

_handler: Optional[RotatingFileHandler] = None


def init(level: str = "INFO") -> None:
    global _handler
    root = logging.getLogger()
    if _handler is None:
        _handler = RotatingFileHandler(
            paths.log_file(), maxBytes=_MAX_BYTES, backupCount=_BACKUP, encoding="utf-8"
        )
        _handler.setFormatter(logging.Formatter(_FMT))
        root.addHandler(_handler)
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(_FMT))
        root.addHandler(stream)
        from . import log_ring
        logging.getLogger().addHandler(log_ring.handler())
    reconfigure(level)


def reconfigure(level: str) -> None:
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logging.getLogger().setLevel(lvl)
