# 018 — Persistent transcript capture for analytics

## Problem

Voice transcripts (Whisper STT input + LLM/TTS reply) are currently
only visible inside the rolling daemon log
(`%ProgramData%\Halbot\logs\halbot.log`), which is:

1. Mixed with debug noise (gRPC heartbeats, voice-rx framing, …).
2. Rotated on a 10MB × 5 cycle — analytics windows >5 files get
   silently truncated.
3. Not parseable: STT lines are quoted Python reprs, LLM lines are
   stringified JSON-in-quoted-string, sometimes wrapped in
   ` ```json ` fences. Encoding is mojibake'd for emoji.
4. Disjoint from `events.db`. The `stt_request` event records
   `text_chars` count but not the text itself; `tts_request` doesn't
   store the spoken string. `bot_reply` doesn't exist.

`scripts/extract_transcripts.py` is a one-shot regex scrape that
recovers ~1237 user lines + ~102 bot lines into
`_data/transcripts.jsonl` for ad-hoc review, but it is fragile and
backwards-only. We want a forward-looking capture that survives
log rotation, is cleanly parseable, and doesn't lose text to
encoding.

## Shape

Single dedicated rotating JSONL logger, **off by default**, gated
by a runtime-toggleable BOOL config field surfaced in the dashboard.
No events.db changes (`text_chars` stays as the analytics gauge
there). Transcripts live as their own file so retention / archive
can move independently from metrics retention.

### Config field

```python
# halbot/config.py
DEFAULTS["transcript_log_enabled"] = "false"
SCHEMA["transcript_log_enabled"] = {
    "type": "BOOL",
    "description": "Persist voice transcripts to logs/transcripts.jsonl (rotating file)",
    "group": "voice", "label": "TRANSCRIPT_LOG_ENABLED",
}
```

Frontend already renders BOOL as a toggle (`FieldInput.jsx:15`).
Schema-driven dashboard auto-picks it up — zero frontend changes.

Toggle is read live by `transcript_log.emit()`. Flip on in
dashboard → next utterance lands in file. Flip off → emits
short-circuit immediately. Handler stays attached either way
(zero cost when no calls).

### `halbot/transcript_log.py` (new, ~40 lines)

```python
import json, logging, time
from logging.handlers import RotatingFileHandler
from . import paths

_logger = logging.getLogger("halbot.transcript")
_logger.propagate = False  # don't double into halbot.log

_BUILD = "unknown"

def init() -> None:
    global _BUILD
    try:
        from . import _build_info
        _BUILD = _build_info.BUILD_TIMESTAMP
    except Exception:
        _BUILD = "source"
    h = RotatingFileHandler(
        paths.data_dir() / "logs" / "transcripts.jsonl",
        maxBytes=20 * 1024 * 1024, backupCount=20,  # ~400MB ceiling
        encoding="utf-8",
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(h)
    _logger.setLevel(logging.INFO)

def emit(role: str, text: str, **meta) -> None:
    from . import config
    if str(config.get("transcript_log_enabled")).lower() != "true":
        return                              # off by default — short-circuit
    rec = {
        "ts": time.time(),
        "build": _BUILD,
        "role": role,
        "text": text,
        **meta,
    }
    _logger.info(json.dumps(rec, ensure_ascii=False))
```

Each line is one JSON object with at minimum:
- `ts` — unix epoch float
- `build` — daemon build timestamp (stamped once at init from
  `halbot/_build_info.py`, falls back to `"source"` for `uv run`)
- `role` — `"user"` | `"bot"` | `"tts"`
- `text` — the actual transcript / reply / spoken string

Plus call-site-supplied meta:
- user side: `user_id`, `audio_seconds`, `lock_wait_ms`, `lang_prob`
- bot side: `action`, `reply_to`, `latency_ms`
- tts side: `voice`, `latency_ms`, `concurrency_peak`

### Call sites

- `halbot/voice_session.py` after STT yields a transcript:
  `transcript_log.emit("user", text, user_id=uid, audio_seconds=...)`
- `halbot/voice_session.py` (or `bot.py`) after the LLM reply parses:
  `transcript_log.emit("bot", reply_msg, action=..., reply_to=uid, latency_ms=...)`
- `halbot/voice_session.py::_speak()` right before TTS render:
  `transcript_log.emit("tts", text, voice=..., latency_ms=...)`

### Init

One call from `halbot/daemon.py` after `logging_setup.init()`:
```python
from . import transcript_log
transcript_log.init()
```

Source-run picks up the same path under `_dev_data/logs/`.

### Rotation

20MB × 20 backups = ~400MB ceiling. At ~100 turns/day with
~200 chars/turn, one file ≈ 100K turns ≈ several years. Files roll
as `transcripts.jsonl`, `transcripts.jsonl.1`, …,
`transcripts.jsonl.20`. Same `RotatingFileHandler` mechanics as
`halbot.log` so behavior is familiar.

### Build field

Stamped once at `init()` from `halbot._build_info.BUILD_TIMESTAMP`
(the same string `Health().daemon_version` returns — local-tz
build timestamp). Lets us correlate analytics shifts with deploys
("did wake-detect rate drop after build X?"). Source runs get
`"source"` so a `uv run` session is distinguishable.

Cost: ~3 extra lines in `init()`, one extra dict key per record.

## Files touched

- `halbot/config.py` — `transcript_log_enabled` in DEFAULTS
  (`"false"`) + SCHEMA (BOOL, group `voice`).
- `halbot/transcript_log.py` — new module (~45 lines).
- `halbot/daemon.py` — single `transcript_log.init()` call after
  `logging_setup.init()`.
- `halbot/voice_session.py` — three `emit(...)` calls (user / bot
  / tts) at the matching record sites.

No schema migrations. No events.db changes. No retention knob (file
rotation is the retention). No frontend changes — `FieldInput.jsx`
already renders BOOL.

## Settled

1. **Path.** Alongside `halbot.log` →
   `paths.data_dir() / "logs" / "transcripts.jsonl"`.
2. **Truncation.** None. Store text verbatim.
3. **TTS dup.** Keep all three roles (`user` / `bot` / `tts`).
   Single-pane timeline beats marginal dedup.

## Non-goals

- Real-time transcript streaming to dashboard.
- PII scrubbing (names, addresses). Trust boundary is the same as
  `sounds.db`; everything is private-server, friends-only.
- Speaker diarization beyond `user_id`.
- Re-encoding existing `halbot.log` (separate mojibake bug).
- Encrypted-at-rest. Same trust boundary as everything else in
  `%ProgramData%\Halbot`.
