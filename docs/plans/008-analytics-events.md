# Plan 008 — Analytics Events

**Goal:** capture per-user interaction + Discord action events (soundboard
plays, command invocations, voice joins, LLM calls). Expose query +
live-stream over gRPC. Surface in dashboard; later drive a `/stats`
Discord command.

**Scope split:**

- **This plan (phase 1):** storage layer, `record()` emitter API, gRPC
  `QueryStats` + `StreamEvents` RPCs, retention prune job, dashboard
  "Analytics" panel wired to real data (empty until emitters land),
  privacy / retention registry knobs.
- **Deferred to phase 2 (Discord re-introduction):** actual `record()`
  call sites at soundboard / command / voice / LLM hooks.
- **Deferred to phase 3 (polish):** `/stats` Discord command, web share
  link.

**Why now:** lock the event schema + RPC surface before Discord code
returns so emitters drop into stable API. Avoids a second rewrite.

## Runnable at end

Yes. Daemon starts, `events` table created, `QueryStats` returns empty
rows, `StreamEvents` streams nothing. Dashboard Analytics panel renders
empty-state placeholder. No regressions to existing panels.

## Design decisions (locked)

- **Storage:** SQLite `%ProgramData%\Halbot\events.db` (source run:
  `_dev_data\events.db`). Single `events` table, JSON meta column.
- **Emission:** fire-and-forget. `record()` enqueues onto a
  `queue.Queue`; background writer thread drains in batches every 500ms
  or 256 rows, whichever first. Writer failure → log warning, drop
  event, never propagate to caller.
- **Schema versioning:** `schema_version INTEGER DEFAULT 1` column.
  Migrations at daemon startup keyed off `PRAGMA user_version`.
- **Retention:** registry field `analytics_retention_days` (default 90).
  Nightly task deletes `WHERE ts_unix < now - retention_days * 86400`.
- **No opt-out.** Private single-user server, not commercial, not GDPR
  scope. All user actions tracked unconditionally. Do not reintroduce
  opt-out logic without explicit operator request.
- **Query surface:** narrow. `QueryStats` takes filter fields, grouping,
  limit. No raw-SQL passthrough. Aggregations computed server-side.
- **Live feed:** `StreamEvents` mirrors `StreamLogs` design — ring
  buffer + fan-out queues in `halbot/analytics.py`.
- **No Discord API lookups in daemon:** `user_id` stored as snowflake
  only. Dashboard resolves to display name at render via cached Discord
  API call (later phase).

## Files touched (this plan)

**New:**
- `halbot/analytics.py`
- `frontend/src/panels/Analytics.jsx`

**Edited:**
- `proto/mgmt.proto`
- `halbot/_gen/mgmt_pb2.py` (regenerated)
- `halbot/_gen/mgmt_pb2_grpc.py` (regenerated)
- `halbot/config.py` (DEFAULTS + SCHEMA: retention)
- `halbot/mgmt_server.py` (QueryStats + StreamEvents + StartupHooks)
- `halbot/daemon.py` (init analytics, start prune task)
- `halbot/paths.py` (events_db() helper)
- `tray/mgmt_client.py` (client wrappers)
- `dashboard/bridge.py` (js_api bindings)
- `frontend/src/App.jsx` (route to Analytics panel)
- `frontend/src/components/navItems.jsx` (add nav item)

## Step 1 — Proto

Add to `proto/mgmt.proto`:

```proto
service Mgmt {
  // ... existing RPCs ...
  rpc QueryStats (QueryStatsRequest) returns (QueryStatsReply);
  rpc StreamEvents (StreamEventsRequest) returns (stream Event);
}

message Event {
  int64  ts_unix_nanos = 1;
  string kind          = 2;   // soundboard_play | cmd_invoke | voice_join | llm_call | tts_request | mention
  uint64 guild_id      = 3;
  uint64 user_id       = 4;
  string target        = 5;   // sound name / command / model, empty if n/a
  string meta_json     = 6;   // free-form, may be empty
}

message QueryStatsRequest {
  string kind      = 1;   // empty = any
  uint64 user_id   = 2;   // 0 = any
  string target    = 3;   // empty = any
  int64  ts_from   = 4;   // unix seconds, 0 = epoch
  int64  ts_to     = 5;   // unix seconds, 0 = now
  string group_by  = 6;   // "target" | "user_id" | "kind" | "" (= no grouping, returns total count)
  int32  limit     = 7;   // 0 = 100, max 1000
}

message StatsRow {
  string key    = 1;   // grouping key (target/user_id/kind as string)
  int64  count  = 2;
  int64  last_ts_unix = 3;
}

message QueryStatsReply {
  int64 total_count = 1;
  repeated StatsRow rows = 2;
}

message StreamEventsRequest {
  int32  backlog = 1;     // events to replay on connect; 0 = none
  string kind    = 2;     // filter; empty = all
  uint64 user_id = 3;     // filter; 0 = all
}
```

Regenerate:

```powershell
scripts\gen_proto.ps1
```

## Step 2 — `halbot/analytics.py`

New module. Single SQLite connection owned by writer thread; readers
open short-lived connections.

```python
"""Event recording + query + live-stream layer.

Fire-and-forget API. Callers do not block on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue as stdqueue
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from . import config, paths

log = logging.getLogger(__name__)

_MAX_RING = 500
_FLUSH_INTERVAL_S = 0.5
_FLUSH_BATCH = 256

_SCHEMA_V = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix       INTEGER NOT NULL,
    ts_unix_nanos INTEGER NOT NULL,
    kind          TEXT    NOT NULL,
    guild_id      INTEGER NOT NULL DEFAULT 0,
    user_id       INTEGER NOT NULL DEFAULT 0,
    target        TEXT    NOT NULL DEFAULT '',
    meta_json     TEXT    NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts     ON events (kind, ts_unix);
CREATE INDEX IF NOT EXISTS idx_events_user_kind   ON events (user_id, kind, ts_unix);
CREATE INDEX IF NOT EXISTS idx_events_target_kind ON events (target, kind, ts_unix);
"""


class _EventRec:
    __slots__ = ("ts_ns", "kind", "guild_id", "user_id", "target", "meta_json")
    def __init__(self, ts_ns, kind, guild_id, user_id, target, meta_json):
        self.ts_ns = ts_ns; self.kind = kind
        self.guild_id = guild_id; self.user_id = user_id
        self.target = target; self.meta_json = meta_json


_writer_queue: "stdqueue.Queue[_EventRec | None]" = stdqueue.Queue(maxsize=8192)
_writer_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

_ring_lock = threading.Lock()
_ring: Deque[_EventRec] = deque(maxlen=_MAX_RING)
_subscribers: List[asyncio.Queue] = []
_bound_loop: Optional[asyncio.AbstractEventLoop] = None


def _open_db() -> sqlite3.Connection:
    p = paths.events_db()
    c = sqlite3.connect(str(p), isolation_level=None, timeout=5.0)
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    return c


def _migrate(c: sqlite3.Connection) -> None:
    cur_v = c.execute("PRAGMA user_version;").fetchone()[0]
    c.executescript(_SCHEMA)
    if cur_v < _SCHEMA_V:
        c.execute(f"PRAGMA user_version = {_SCHEMA_V};")


def init() -> None:
    """Idempotent. Called from daemon startup."""
    global _writer_thread
    with _open_db() as c:
        _migrate(c)
    if _writer_thread is None or not _writer_thread.is_alive():
        _stop_event.clear()
        _writer_thread = threading.Thread(target=_writer_loop, name="analytics-writer", daemon=True)
        _writer_thread.start()
        log.info("analytics writer started")


def shutdown() -> None:
    _stop_event.set()
    try:
        _writer_queue.put_nowait(None)
    except stdqueue.Full:
        pass
    t = _writer_thread
    if t is not None:
        t.join(timeout=3.0)


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _bound_loop
    _bound_loop = loop


def record(kind: str, *, user_id: int = 0, guild_id: int = 0,
           target: str = "", **meta: Any) -> None:
    """Fire-and-forget. Never raises."""
    try:
        rec = _EventRec(
            ts_ns=time.time_ns(),
            kind=str(kind),
            guild_id=int(guild_id),
            user_id=int(user_id),
            target=str(target),
            meta_json=json.dumps(meta, separators=(",", ":")) if meta else "",
        )
        try:
            _writer_queue.put_nowait(rec)
        except stdqueue.Full:
            log.warning("analytics queue full, dropping event kind=%s", kind)
            return
        with _ring_lock:
            _ring.append(rec)
            subs = list(_subscribers)
        loop = _bound_loop
        if loop is not None:
            for q in subs:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, rec)
                except Exception:
                    pass
    except Exception as e:
        log.warning("analytics.record failed: %s", e)


def _writer_loop() -> None:
    try:
        conn = _open_db()
    except Exception as e:
        log.exception("analytics writer: failed to open db: %s", e)
        return
    batch: List[_EventRec] = []
    last_flush = time.monotonic()
    try:
        while not _stop_event.is_set():
            timeout = _FLUSH_INTERVAL_S - (time.monotonic() - last_flush)
            timeout = max(0.01, timeout)
            try:
                item = _writer_queue.get(timeout=timeout)
            except stdqueue.Empty:
                item = None
            if item is None:
                if _stop_event.is_set() and not batch:
                    break
            else:
                batch.append(item)
            if batch and (len(batch) >= _FLUSH_BATCH or (time.monotonic() - last_flush) >= _FLUSH_INTERVAL_S):
                try:
                    conn.executemany(
                        "INSERT INTO events (ts_unix, ts_unix_nanos, kind, guild_id, user_id, target, meta_json) VALUES (?,?,?,?,?,?,?)",
                        [(r.ts_ns // 1_000_000_000, r.ts_ns, r.kind, r.guild_id, r.user_id, r.target, r.meta_json) for r in batch],
                    )
                except Exception as e:
                    log.warning("analytics flush failed: %s", e)
                batch.clear()
                last_flush = time.monotonic()
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Live feed subscription ─────────────────────────────────
def subscribe(backlog: int = 0, kind: str = "", user_id: int = 0) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    with _ring_lock:
        _subscribers.append(q)
        if backlog > 0:
            tail = list(_ring)[-backlog:]
            for rec in tail:
                if kind and rec.kind != kind:
                    continue
                if user_id and rec.user_id != user_id:
                    continue
                try:
                    q.put_nowait(rec)
                except asyncio.QueueFull:
                    break
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _ring_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


# ── Query ──────────────────────────────────────────────────
_GROUP_COLS = {"target": "target", "user_id": "user_id", "kind": "kind"}


def query_stats(
    *, kind: str = "", user_id: int = 0, target: str = "",
    ts_from: int = 0, ts_to: int = 0, group_by: str = "", limit: int = 100,
) -> Tuple[int, List[Dict[str, Any]]]:
    limit = max(1, min(limit or 100, 1000))
    ts_to = ts_to or int(time.time()) + 1
    clauses = ["ts_unix BETWEEN ? AND ?"]
    params: List[Any] = [ts_from, ts_to]
    if kind:
        clauses.append("kind = ?"); params.append(kind)
    if user_id:
        clauses.append("user_id = ?"); params.append(user_id)
    if target:
        clauses.append("target = ?"); params.append(target)
    where = " AND ".join(clauses)
    conn = _open_db()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params).fetchone()[0]
        rows: List[Dict[str, Any]] = []
        col = _GROUP_COLS.get(group_by)
        if col:
            cur = conn.execute(
                f"SELECT {col} AS k, COUNT(*) AS c, MAX(ts_unix) AS lt FROM events WHERE {where} "
                f"GROUP BY {col} ORDER BY c DESC LIMIT ?",
                params + [limit],
            )
            for k, c, lt in cur.fetchall():
                rows.append({"key": str(k), "count": int(c), "last_ts_unix": int(lt or 0)})
        return int(total), rows
    finally:
        conn.close()


# ── Retention ──────────────────────────────────────────────
def prune_older_than(retention_days: int) -> int:
    cutoff = int(time.time()) - max(1, retention_days) * 86400
    conn = _open_db()
    try:
        cur = conn.execute("DELETE FROM events WHERE ts_unix < ?", (cutoff,))
        return cur.rowcount or 0
    finally:
        conn.close()


async def prune_loop() -> None:
    """Run forever. One sweep every 6 hours."""
    while True:
        try:
            days = int(config.get("analytics_retention_days") or 90)
            removed = await asyncio.to_thread(prune_older_than, days)
            if removed:
                log.info("analytics prune removed %d rows (retention=%dd)", removed, days)
        except Exception as e:
            log.warning("analytics prune failed: %s", e)
        await asyncio.sleep(6 * 3600)
```

## Step 3 — `halbot/paths.py`

Append:

```python
def events_db() -> Path:
    return data_dir() / "events.db"
```

## Step 4 — `halbot/config.py`

Append to `DEFAULTS`:

```python
    "analytics_retention_days": "90",
```

Append to `SCHEMA`:

```python
    "analytics_retention_days": {
        "type": "NUMBER", "min": 1.0, "max": 3650.0, "step": 1.0,
        "description": "Days of analytics history retained on disk",
        "group": "general", "label": "ANALYTICS_RETENTION_DAYS",
    },
```

Verify `set(DEFAULTS) == set(SCHEMA)`:

```powershell
uv run python -c "from halbot.config import DEFAULTS, SCHEMA; assert set(DEFAULTS) == set(SCHEMA), set(DEFAULTS) ^ set(SCHEMA)"
```

## Step 5 — `halbot/daemon.py`

Init + prune task. In `_run_async()` after `server = await serve(...)`:

```python
    from . import analytics
    analytics.init()
    analytics.bind_loop(asyncio.get_running_loop())
```

Add to `tasks`:

```python
    asyncio.create_task(analytics.prune_loop(), name="analytics-prune"),
```

After `await stop_event.wait()`, add before `server.stop(...)`:

```python
    analytics.shutdown()
```

## Step 6 — `halbot/mgmt_server.py`

Add two methods on `MgmtService`:

```python
    async def QueryStats(self, request, context):
        from . import analytics
        total, rows = await asyncio.to_thread(
            analytics.query_stats,
            kind=request.kind, user_id=request.user_id, target=request.target,
            ts_from=request.ts_from, ts_to=request.ts_to,
            group_by=request.group_by, limit=request.limit,
        )
        reply = mgmt_pb2.QueryStatsReply(total_count=total)
        for r in rows:
            reply.rows.add(key=r["key"], count=r["count"], last_ts_unix=r["last_ts_unix"])
        return reply

    async def StreamEvents(self, request, context):
        from . import analytics
        q = analytics.subscribe(
            backlog=max(0, min(request.backlog, 500)),
            kind=request.kind or "",
            user_id=request.user_id or 0,
        )
        try:
            while True:
                rec = await q.get()
                if request.kind and rec.kind != request.kind:
                    continue
                if request.user_id and rec.user_id != request.user_id:
                    continue
                yield mgmt_pb2.Event(
                    ts_unix_nanos=rec.ts_ns, kind=rec.kind,
                    guild_id=rec.guild_id, user_id=rec.user_id,
                    target=rec.target, meta_json=rec.meta_json,
                )
        finally:
            analytics.unsubscribe(q)
```

## Step 7 — `tray/mgmt_client.py`

Add wrappers:

```python
    def query_stats(self, *, kind="", user_id=0, target="",
                    ts_from=0, ts_to=0, group_by="", limit=100):
        req = mgmt_pb2.QueryStatsRequest(
            kind=kind, user_id=user_id, target=target,
            ts_from=ts_from, ts_to=ts_to, group_by=group_by, limit=limit,
        )
        return self._call("QueryStats", req)

    def stream_events(self, *, backlog=0, kind="", user_id=0):
        req = mgmt_pb2.StreamEventsRequest(backlog=backlog, kind=kind, user_id=user_id)
        stub = self._stub_ready()
        return stub.StreamEvents(req)
```

## Step 8 — `dashboard/bridge.py`

Add js_api methods:

```python
    def query_stats(self, kind="", user_id=0, target="",
                    ts_from=0, ts_to=0, group_by="", limit=100) -> Dict[str, Any]:
        r = self._client.query_stats(
            kind=kind, user_id=int(user_id or 0), target=target,
            ts_from=int(ts_from or 0), ts_to=int(ts_to or 0),
            group_by=group_by, limit=int(limit or 100),
        )
        return {
            "total_count": int(r.total_count),
            "rows": [
                {"key": x.key, "count": int(x.count), "last_ts_unix": int(x.last_ts_unix)}
                for x in r.rows
            ],
        }
```

Live event stream: reuse existing log-stream pattern (background thread
consuming `stream_events`, push to a bounded deque, dashboard polls
`pop_event_batch`). Follow `dashboard/log_stream.py` as template.
New file `dashboard/event_stream.py` mirrors its structure; bridge
exposes `pop_event_batch(max_n)`.

## Step 9 — Frontend

**New nav item** in `frontend/src/components/navItems.jsx`: `analytics`.

**New panel** `frontend/src/panels/Analytics.jsx`. Three sections:

1. **Top soundboard plays (30d)** — table from `query_stats(kind="soundboard_play", group_by="target", limit=20)`.
2. **Top users by activity (30d)** — table from `query_stats(group_by="user_id", limit=20)`. Render user_id verbatim this phase; later replace with display name lookup.
3. **Live events** — scrolling feed from `pop_event_batch` polling @ 500ms.

Empty state (total_count=0): centered "No events recorded yet. Phase 2
wires emitters at Discord action sites." placeholder. Match Stats panel
mock-overlay style so it's visually consistent.

Route in `App.jsx`: add `case 'analytics': return <AnalyticsPanel />`.

## Step 10 — Verification gate

Run:

```powershell
scripts\gen_proto.ps1
uv run python -c "from halbot._gen import mgmt_pb2; assert hasattr(mgmt_pb2, 'Event'); assert hasattr(mgmt_pb2, 'QueryStatsRequest')"
uv run python -c "from halbot.config import DEFAULTS, SCHEMA; assert set(DEFAULTS) == set(SCHEMA)"
uv run python -c "from halbot import analytics; analytics.init(); analytics.record('test', user_id=1, target='sanity'); import time; time.sleep(1); t,r = analytics.query_stats(kind='test', group_by='target'); assert t >= 1, (t,r); analytics.shutdown(); print('OK', t, r)"
uv run python -m halbot.daemon run
```

Daemon must start, log `analytics writer started`, stay up. Ctrl+C
stops cleanly (writer drains, prune task cancels).

Frontend build:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

No new warnings. Analytics panel renders empty-state.

## Step 11 — Commit

```powershell
git add proto/mgmt.proto halbot/_gen/ halbot/analytics.py halbot/paths.py halbot/config.py halbot/mgmt_server.py halbot/daemon.py tray/mgmt_client.py dashboard/bridge.py dashboard/event_stream.py frontend/src/panels/Analytics.jsx frontend/src/components/navItems.jsx frontend/src/App.jsx
git commit -m "feat(008): analytics event storage + QueryStats/StreamEvents + dashboard panel"
```

Do not land emitter call sites in this commit — they belong to phase 2
when Discord/voice/LLM code returns.

## Phase 2 landing notes (not in scope)

When Discord code re-enters the repo:

- Soundboard playback: `analytics.record("soundboard_play", user_id=msg.author.id, guild_id=msg.guild.id, target=sound_name, duration_ms=dur)`.
- Command dispatcher: decorator `@track_command` emitting `cmd_invoke`.
- Voice join: `record("voice_join", user_id=..., guild_id=..., target=channel_name)`.
- LLM call: `record("llm_call", user_id=..., target=model, tokens_in=..., tokens_out=..., latency_ms=...)`.
- TTS render: `record("tts_request", user_id=..., target=engine, latency_ms=...)`.
- Mention: `record("mention", user_id=...)`.

Schema supports all without further migration.

## Phase 3 polish (not in scope)

- `/stats` Discord slash command calling `analytics.query_stats(...)` directly (in-process, no gRPC hop).
- Display-name cache in dashboard: resolve `user_id` via Discord API once per session.
- Opt-in "public analytics" registry flag to expose a limited read-only view.

## Pitfalls

- **SQLite WAL on Windows:** journal + wal files created alongside db.
  `%ProgramData%\Halbot\events.db-wal/-shm` must inherit ACL from parent.
  Installer already grants user modify on `%ProgramData%\Halbot\` so OK.
- **Writer thread not flushing on crash:** acceptable; events are
  best-effort. No cross-process locking — single writer process by
  design.
- **Clock skew:** `ts_unix` from `time.time_ns()` — system clock. Events
  survive clock jumps but ordering within ±1 minute of NTP adjust may
  be scrambled. Non-issue for private bot.
