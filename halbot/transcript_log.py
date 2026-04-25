"""Rotating JSONL transcript logger for voice analytics.

Off by default; gated by `transcript_log_enabled` config (BOOL).
Flip in dashboard → next utterance lands in file. No daemon
restart required — the toggle is read on every emit().

One JSON object per line:
    {"ts": 1777139897.58, "build": "2026-04-25 11:14:42 -07:00",
     "role": "user", "text": "Halbot, hello?", "user_id": 192...}

Roles: "user" (Whisper STT input), "bot" (parsed LLM reply text),
"tts" (string handed to the TTS engine, may include persona
transforms applied after the LLM stage).

File path: paths.log_dir() / transcripts.jsonl, rotated 20MB × 20.
"""

from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler

from . import paths

_MAX_BYTES = 20 * 1024 * 1024  # 20 MB per file
_BACKUP = 20                    # ~400 MB total ceiling

_logger = logging.getLogger("halbot.transcript")
_logger.propagate = False  # don't double-log into halbot.log
_initialized = False
_BUILD = "unknown"


def init() -> None:
    """Attach the rotating handler. Idempotent."""
    global _initialized, _BUILD
    if _initialized:
        return
    try:
        from . import _build_info  # type: ignore
        _BUILD = _build_info.BUILD_TIMESTAMP
    except Exception:
        _BUILD = "source"
    h = RotatingFileHandler(
        paths.transcript_log_file(),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP,
        encoding="utf-8",
    )
    # Raw message — no level/timestamp prefix, every line is JSON.
    h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(h)
    _logger.setLevel(logging.INFO)
    _initialized = True


def emit(role: str, text: str, **meta) -> None:
    """Append one JSON record. No-op when transcript_log_enabled=false.

    `text` is stored verbatim (no truncation). `meta` is merged into
    the record so callers can attach user_id / latency_ms / action
    / audio_seconds / concurrency_peak etc. without schema gymnastics.
    """
    # Read config every call so the dashboard toggle takes effect
    # immediately without daemon restart. Cost: one dict lookup.
    try:
        from . import config
        if str(config.get("transcript_log_enabled")).strip().lower() != "true":
            return
    except Exception:
        return
    if not _initialized:
        # Init was missed; attach handler lazily so we don't drop the
        # first turn after enable.
        try:
            init()
        except Exception:
            return
    rec = {
        "ts": time.time(),
        "build": _BUILD,
        "role": role,
        "text": text,
    }
    if meta:
        rec.update(meta)
    try:
        _logger.info(json.dumps(rec, ensure_ascii=False))
    except Exception:
        # Logging must never break the voice path.
        pass
