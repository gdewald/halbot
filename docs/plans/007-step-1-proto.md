# Step 1 — Proto + Config Schema

**Goal:** extend `proto/mgmt.proto` so config fields carry type,
options, description, group, and min/max/step; regenerate stubs;
add a `SCHEMA` dict in `halbot/config.py`; add `GetStats` +
`StreamLogs` RPCs with stub implementations.

**Runnable at end:** yes — daemon still works unchanged.

## Files you will touch

- `proto/mgmt.proto` (edit)
- `halbot/_gen/mgmt_pb2.py` (regenerated)
- `halbot/_gen/mgmt_pb2_grpc.py` (regenerated)
- `halbot/config.py` (edit — add `SCHEMA`, no logic changes)
- `halbot/mgmt_server.py` (edit — fill new proto fields, add
  `GetStats`, `StreamLogs`)
- `halbot/log_ring.py` (new — bounded log ring buffer)
- `halbot/logging_setup.py` (edit — attach ring handler)

Do not touch anything else.

## 1.1 Edit `proto/mgmt.proto`

Add this enum below the existing `ConfigSource` enum:

```proto
enum ConfigFieldType {
  CONFIG_FIELD_TYPE_UNSPECIFIED = 0;
  CONFIG_FIELD_TYPE_STRING = 1;
  CONFIG_FIELD_TYPE_NUMBER = 2;
  CONFIG_FIELD_TYPE_BOOL = 3;
  CONFIG_FIELD_TYPE_SELECT = 4;
  CONFIG_FIELD_TYPE_URL = 5;
  CONFIG_FIELD_TYPE_RANGE = 6;
}
```

Replace the existing `StringValue` message with:

```proto
message StringValue {
  string value = 1;
  ConfigSource source = 2;
  ConfigFieldType type = 3;
  repeated string options = 4;
  string description = 5;
  string group = 6;          // "general" | "llm" | "voice" | "tts"
  double min = 7;
  double max = 8;
  double step = 9;
  string label = 10;         // display label, e.g. "LOG_LEVEL"
}
```

Add these RPCs inside the `service Mgmt { ... }` block:

```proto
rpc StreamLogs (StreamLogsRequest) returns (stream LogLine);
rpc GetStats (Empty) returns (StatsReply);
```

Add these messages at the bottom of the file:

```proto
message StreamLogsRequest {
  int32 backlog = 1;         // lines to replay on connect; 0 = none
  string min_level = 2;      // DEBUG|INFO|WARNING|ERROR; empty = all
}

message LogLine {
  int64 ts_unix_nanos = 1;
  string level = 2;
  string source = 3;
  string message = 4;
}

message SoundboardStats {
  int32 sounds_backed_up = 1;
  int64 storage_bytes = 2;
  int64 last_sync_unix = 3;
  int32 new_since_last = 4;
}

message VoicePlaybackStats {
  int32 played_today = 1;
  int32 played_all_time = 2;
  int64 session_seconds_today = 3;
  int32 avg_response_ms = 4;
}

message WakeWordStats {
  int32 detections_today = 1;
  int32 detections_all_time = 2;
  int32 false_positives_today = 3;
  int32 avg_join_latency_ms = 4;
}

message LatencyStats {
  int32 avg_ms = 1;
  int32 p95_ms = 2;
  int32 count_today = 3;
}

message LlmStats {
  int32 response_avg_ms = 1;
  int32 response_p95_ms = 2;
  int32 ttft_avg_ms = 3;
  int32 ttft_p95_ms = 4;
  int32 tokens_per_sec = 5;
  int32 requests_today = 6;
  int32 avg_tokens_out = 7;
  int32 context_usage_pct = 8;
  int32 timeouts_today = 9;
}

message StatsReply {
  SoundboardStats soundboard = 1;
  VoicePlaybackStats voice_playback = 2;
  WakeWordStats wake_word = 3;
  LatencyStats stt = 4;
  LatencyStats tts = 5;
  LlmStats llm = 6;
  bool mock = 99;            // true until real telemetry lands
}
```

## 1.2 Regenerate stubs

Run from repo root in PowerShell:

```powershell
scripts\gen_proto.ps1
```

Commit the changed `halbot/_gen/mgmt_pb2.py` and
`halbot/_gen/mgmt_pb2_grpc.py`.

## 1.3 Add `SCHEMA` to `halbot/config.py`

Append below the existing `DEFAULTS` dict (do not modify
`DEFAULTS`). Every key in `DEFAULTS` must have an entry. Groups:
`general`, `llm`, `voice`, `tts`.

```python
# Field schema drives Config panel widgets over the wire.
# Keys must mirror DEFAULTS exactly. Missing key = hidden in UI.
SCHEMA: Dict[str, Dict[str, Any]] = {
    "log_level": {
        "type": "SELECT", "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "description": "Minimum log level emitted",
        "group": "general", "label": "LOG_LEVEL",
    },
    "llm_backend": {
        "type": "SELECT", "options": ["lmstudio", "ollama"],
        "description": "LLM backend driver",
        "group": "llm", "label": "LLM_BACKEND",
    },
    "llm_url": {
        "type": "URL",
        "description": "LLM API endpoint",
        "group": "llm", "label": "LLM_URL",
    },
    "llm_model": {
        "type": "STRING",
        "description": "Model name as shown by the backend",
        "group": "llm", "label": "LLM_MODEL",
    },
    "llm_max_tokens_text": {
        "type": "NUMBER", "min": 16.0, "max": 4096.0, "step": 16.0,
        "description": "Max output tokens for text LLM calls",
        "group": "llm", "label": "LLM_MAX_TOKENS_TEXT",
    },
    "llm_max_tokens_voice": {
        "type": "NUMBER", "min": 16.0, "max": 2048.0, "step": 16.0,
        "description": "Max output tokens for voice LLM calls",
        "group": "llm", "label": "LLM_MAX_TOKENS_VOICE",
    },
    "voice_wake_word": {
        "type": "STRING",
        "description": "Wake word phrase",
        "group": "voice", "label": "VOICE_WAKE_WORD",
    },
    "voice_idle_timeout_seconds": {
        "type": "NUMBER", "min": 30.0, "max": 7200.0, "step": 30.0,
        "description": "Seconds before bot leaves empty channel",
        "group": "voice", "label": "VOICE_IDLE_TIMEOUT_SECONDS",
    },
    "voice_energy_threshold": {
        "type": "RANGE", "min": 0.0, "max": 0.2, "step": 0.005,
        "description": "Voice activity detection threshold",
        "group": "voice", "label": "VOICE_ENERGY_THRESHOLD",
    },
    "voice_history_turns": {
        "type": "NUMBER", "min": 0.0, "max": 50.0, "step": 1.0,
        "description": "Conversation turns kept in voice LLM context",
        "group": "voice", "label": "VOICE_HISTORY_TURNS",
    },
    "voice_llm_combine_calls": {
        "type": "BOOL",
        "description": "Batch voice LLM calls for lower latency",
        "group": "voice", "label": "VOICE_LLM_COMBINE_CALLS",
    },
    "tts_engine": {
        "type": "SELECT", "options": ["kokoro", "espeak", "piper"],
        "description": "TTS backend",
        "group": "tts", "label": "TTS_ENGINE",
    },
    "tts_voice": {
        "type": "STRING",
        "description": "TTS voice ID",
        "group": "tts", "label": "TTS_VOICE",
    },
    "tts_lang": {
        "type": "STRING",
        "description": "TTS language code",
        "group": "tts", "label": "TTS_LANG",
    },
    "tts_speed": {
        "type": "RANGE", "min": 0.5, "max": 2.0, "step": 0.05,
        "description": "TTS playback speed multiplier",
        "group": "tts", "label": "TTS_SPEED",
    },
}
```

**Sanity check:** every `SCHEMA` key must exist in `DEFAULTS`.
Every `DEFAULTS` key must exist in `SCHEMA`. If a mismatch, the
Config panel will either hide a field or crash on render. Run
this one-liner before committing:

```powershell
uv run python -c "from halbot.config import DEFAULTS, SCHEMA; assert set(DEFAULTS) == set(SCHEMA), set(DEFAULTS) ^ set(SCHEMA)"
```

## 1.4 Create `halbot/log_ring.py`

Bounded ring buffer + fan-out queues for `StreamLogs`. Paste
verbatim:

```python
"""In-memory log ring + subscriber fan-out for StreamLogs RPC."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Deque, List

MAX_RING = 1000


class LogRecord:
    __slots__ = ("ts_ns", "level", "source", "message")

    def __init__(self, ts_ns: int, level: str, source: str, message: str) -> None:
        self.ts_ns = ts_ns
        self.level = level
        self.source = source
        self.message = message


class _RingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._ring: Deque[LogRecord] = deque(maxlen=MAX_RING)
        self._queues: List[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        rec = LogRecord(
            ts_ns=time.time_ns(),
            level=record.levelname,
            source=record.name,
            message=msg,
        )
        with self._lock:
            self._ring.append(rec)
            queues = list(self._queues)
        loop = self._loop
        if loop is None:
            return
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, rec)
            except Exception:
                pass

    def subscribe(self, backlog: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._queues.append(q)
            if backlog > 0:
                tail = list(self._ring)[-backlog:]
                for rec in tail:
                    try:
                        q.put_nowait(rec)
                    except asyncio.QueueFull:
                        break
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass


_handler = _RingHandler()


def handler() -> _RingHandler:
    return _handler
```

## 1.5 Edit `halbot/logging_setup.py`

Attach the ring handler to the root logger during `configure()`.
Do not remove any existing handlers. In the function that sets up
logging, add after the existing handlers are added:

```python
from . import log_ring
logging.getLogger().addHandler(log_ring.handler())
```

If the file does not already import `logging`, add the import. Do
not change log format, rotation, or file path.

## 1.6 Edit `halbot/mgmt_server.py`

### 1.6.1 — bind the ring handler's loop in `serve()`

Inside `serve(started, version)`, right before `server.start()`,
add:

```python
from . import log_ring
log_ring.handler().bind_loop(asyncio.get_running_loop())
```

### 1.6.2 — update `_state_msg()` to fill the new `StringValue` fields

Replace the existing `_state_msg()` with:

```python
_TYPE_MAP = {
    "STRING": mgmt_pb2.CONFIG_FIELD_TYPE_STRING,
    "NUMBER": mgmt_pb2.CONFIG_FIELD_TYPE_NUMBER,
    "BOOL":   mgmt_pb2.CONFIG_FIELD_TYPE_BOOL,
    "SELECT": mgmt_pb2.CONFIG_FIELD_TYPE_SELECT,
    "URL":    mgmt_pb2.CONFIG_FIELD_TYPE_URL,
    "RANGE":  mgmt_pb2.CONFIG_FIELD_TYPE_RANGE,
}


def _state_msg() -> mgmt_pb2.ConfigState:
    snap = config.snapshot()
    fields = {}
    for name, (val, src) in snap.items():
        schema = config.SCHEMA.get(name, {})
        fields[name] = mgmt_pb2.StringValue(
            value=str(val),
            source=_SOURCE_MAP[src],
            type=_TYPE_MAP.get(schema.get("type", "STRING"), mgmt_pb2.CONFIG_FIELD_TYPE_STRING),
            options=list(schema.get("options", [])),
            description=schema.get("description", ""),
            group=schema.get("group", "general"),
            min=float(schema.get("min", 0.0)),
            max=float(schema.get("max", 0.0)),
            step=float(schema.get("step", 0.0)),
            label=schema.get("label", name.upper()),
        )
    return mgmt_pb2.ConfigState(fields=fields)
```

### 1.6.3 — add `StreamLogs` and `GetStats` on `MgmtService`

Inside the `MgmtService` class, add these two methods:

```python
async def StreamLogs(self, request, context):
    from . import log_ring
    min_level = (request.min_level or "").upper()
    level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    floor = level_rank.get(min_level, 0)
    q = log_ring.handler().subscribe(backlog=max(0, min(request.backlog, 1000)))
    try:
        while True:
            rec = await q.get()
            if floor and level_rank.get(rec.level, 0) < floor:
                continue
            yield mgmt_pb2.LogLine(
                ts_unix_nanos=rec.ts_ns,
                level=rec.level,
                source=rec.source,
                message=rec.message,
            )
    finally:
        log_ring.handler().unsubscribe(q)

async def GetStats(self, request, context):
    return mgmt_pb2.StatsReply(mock=True)
```

## 1.7 Verification gate

Run in PowerShell from repo root:

```powershell
uv run python -c "from halbot._gen import mgmt_pb2; assert hasattr(mgmt_pb2, 'ConfigFieldType'); assert hasattr(mgmt_pb2, 'LogLine'); assert hasattr(mgmt_pb2, 'StatsReply')"
uv run python -c "from halbot.config import DEFAULTS, SCHEMA; assert set(DEFAULTS) == set(SCHEMA)"
uv run python -m halbot.daemon run
```

The daemon must start, log `mgmt gRPC listening on 127.0.0.1:50199`,
and stay up. Ctrl+C to stop. If any of the three commands errors,
fix and retry — do not proceed to step 2 with a red gate.

## Commit

Stage only the files listed in "Files you will touch":

```powershell
git add proto/mgmt.proto halbot/_gen/mgmt_pb2.py halbot/_gen/mgmt_pb2_grpc.py halbot/config.py halbot/mgmt_server.py halbot/log_ring.py halbot/logging_setup.py
git commit -m "feat(007): proto v2 fields, config SCHEMA, StreamLogs + GetStats"
```

Do not add `frontend/`, `dashboard/`, or `tray/` changes in this
commit.
