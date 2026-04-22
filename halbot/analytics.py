"""Event recording + query + live-stream layer.

Fire-and-forget API. Callers do not block on disk.

Public API:
    init()               — idempotent. Opens DB, starts writer thread.
    shutdown()           — drain + close writer.
    bind_loop(loop)      — bind asyncio loop for live-feed fan-out.
    record(kind, ...)    — enqueue event; never raises.
    query_stats(...)     — synchronous aggregate query.
    subscribe(backlog)   — asyncio.Queue of live events for one subscriber.
    unsubscribe(q)
    prune_older_than(d)  — delete rows older than d days.
    prune_loop()         — async task; sweeps every 6h.
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
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import config, paths

log = logging.getLogger(__name__)

_MAX_RING = 500
_FLUSH_INTERVAL_S = 0.5
_FLUSH_BATCH = 256

_SCHEMA_V = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix        INTEGER NOT NULL,
    ts_unix_nanos  INTEGER NOT NULL,
    kind           TEXT    NOT NULL,
    guild_id       INTEGER NOT NULL DEFAULT 0,
    user_id        INTEGER NOT NULL DEFAULT 0,
    target         TEXT    NOT NULL DEFAULT '',
    meta_json      TEXT    NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts     ON events (kind, ts_unix);
CREATE INDEX IF NOT EXISTS idx_events_user_kind   ON events (user_id, kind, ts_unix);
CREATE INDEX IF NOT EXISTS idx_events_target_kind ON events (target, kind, ts_unix);
"""


class _EventRec:
    __slots__ = ("ts_ns", "kind", "guild_id", "user_id", "target", "meta_json")

    def __init__(self, ts_ns: int, kind: str, guild_id: int, user_id: int,
                 target: str, meta_json: str) -> None:
        self.ts_ns = ts_ns
        self.kind = kind
        self.guild_id = guild_id
        self.user_id = user_id
        self.target = target
        self.meta_json = meta_json


_writer_queue: "stdqueue.Queue[Optional[_EventRec]]" = stdqueue.Queue(maxsize=8192)
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
    c = _open_db()
    try:
        _migrate(c)
    finally:
        c.close()
    if _writer_thread is None or not _writer_thread.is_alive():
        _stop_event.clear()
        _writer_thread = threading.Thread(
            target=_writer_loop, name="analytics-writer", daemon=True
        )
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
        uid = int(user_id or 0)
        rec = _EventRec(
            ts_ns=time.time_ns(),
            kind=str(kind),
            guild_id=int(guild_id or 0),
            user_id=uid,
            target=str(target or ""),
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
        while True:
            if _stop_event.is_set() and not batch and _writer_queue.empty():
                break
            timeout = _FLUSH_INTERVAL_S - (time.monotonic() - last_flush)
            timeout = max(0.01, timeout)
            try:
                item = _writer_queue.get(timeout=timeout)
            except stdqueue.Empty:
                item = None
            if item is not None:
                batch.append(item)
            should_flush = bool(batch) and (
                len(batch) >= _FLUSH_BATCH
                or (time.monotonic() - last_flush) >= _FLUSH_INTERVAL_S
                or _stop_event.is_set()
            )
            if should_flush:
                try:
                    conn.executemany(
                        "INSERT INTO events "
                        "(ts_unix, ts_unix_nanos, kind, guild_id, user_id, target, meta_json) "
                        "VALUES (?,?,?,?,?,?,?)",
                        [
                            (r.ts_ns // 1_000_000_000, r.ts_ns, r.kind,
                             r.guild_id, r.user_id, r.target, r.meta_json)
                            for r in batch
                        ],
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
    limit = max(1, min(int(limit or 100), 1000))
    ts_to = int(ts_to) if ts_to else int(time.time()) + 1
    ts_from = int(ts_from or 0)
    clauses = ["ts_unix BETWEEN ? AND ?"]
    params: List[Any] = [ts_from, ts_to]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if user_id:
        clauses.append("user_id = ?")
        params.append(int(user_id))
    if target:
        clauses.append("target = ?")
        params.append(target)
    where = " AND ".join(clauses)
    conn = _open_db()
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {where}", params
        ).fetchone()
        total = int(total_row[0] if total_row else 0)
        rows: List[Dict[str, Any]] = []
        col = _GROUP_COLS.get(group_by)
        if col:
            cur = conn.execute(
                f"SELECT {col} AS k, COUNT(*) AS c, MAX(ts_unix) AS lt "
                f"FROM events WHERE {where} "
                f"GROUP BY {col} ORDER BY c DESC LIMIT ?",
                params + [limit],
            )
            for k, c, lt in cur.fetchall():
                rows.append({
                    "key": "" if k is None else str(k),
                    "count": int(c),
                    "last_ts_unix": int(lt or 0),
                })
        return total, rows
    finally:
        conn.close()


# ── Retention ──────────────────────────────────────────────
def prune_older_than(retention_days: int) -> int:
    cutoff = int(time.time()) - max(1, int(retention_days)) * 86400
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
            days_raw = config.get("analytics_retention_days")
            days = int(days_raw) if str(days_raw).isdigit() else 90
            removed = await asyncio.to_thread(prune_older_than, days)
            if removed:
                log.info(
                    "analytics prune removed %d rows (retention=%dd)",
                    removed, days,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("analytics prune failed: %s", e)
        try:
            await asyncio.sleep(6 * 3600)
        except asyncio.CancelledError:
            raise
