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


# ── Free-form stats Q&A fetch ──────────────────────────────
def fetch_recent_events(days: int = 60, limit: int = 3000,
                        guild_id: int = 0) -> List[Dict[str, Any]]:
    """Pull recent events for LLM-driven stats Q&A. Most-recent-first.

    Returns list of dicts: ts, kind, guild_id, user_id, target, meta (parsed dict).
    """
    now = int(time.time())
    cutoff = now - max(1, int(days)) * 86400
    clauses = ["ts_unix >= ?"]
    params: List[Any] = [cutoff]
    if guild_id:
        clauses.append("guild_id = ?")
        params.append(int(guild_id))
    where = " AND ".join(clauses)
    try:
        conn = _open_db()
    except Exception:
        return []
    try:
        cur = conn.execute(
            f"SELECT ts_unix, kind, guild_id, user_id, target, meta_json "
            f"FROM events WHERE {where} ORDER BY ts_unix DESC LIMIT ?",
            params + [int(limit)],
        )
        out: List[Dict[str, Any]] = []
        for ts, kind, gid, uid, target, meta_raw in cur.fetchall():
            meta: Dict[str, Any] = {}
            if meta_raw:
                try:
                    parsed = json.loads(meta_raw)
                    if isinstance(parsed, dict):
                        meta = parsed
                except Exception:
                    pass
            out.append({
                "ts": int(ts),
                "kind": str(kind or ""),
                "guild_id": int(gid or 0),
                "user_id": int(uid or 0),
                "target": str(target or ""),
                "meta": meta,
            })
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def wake_history(limit: int = 25) -> List[Dict[str, Any]]:
    """Last N wake-word events: user transcript + first-action outcome.

    Pulled from `parse_voice_intent` analytics records, which voice_session
    enriches with `phrase` (transcript) + `outcome` (action.type or 'no_match')
    in their meta_json. Most-recent-first.
    """
    n = max(1, min(100, int(limit) or 25))
    try:
        conn = _open_db()
    except Exception:
        return []
    try:
        cur = conn.execute(
            "SELECT ts_unix, meta_json FROM events "
            "WHERE kind = 'llm_call' AND target = 'parse_voice_intent' "
            "ORDER BY ts_unix DESC LIMIT ?",
            (n,),
        )
        out: List[Dict[str, Any]] = []
        for ts, meta_raw in cur.fetchall():
            meta: Dict[str, Any] = {}
            if meta_raw:
                try:
                    parsed = json.loads(meta_raw)
                    if isinstance(parsed, dict):
                        meta = parsed
                except Exception:
                    pass
            phrase = str(meta.get("phrase") or "").strip()
            outcome = str(meta.get("outcome") or "").strip()
            action_count = int(meta.get("action_count") or 0)
            if not outcome:
                outcome = "matched" if action_count > 0 else "no_match"
            out.append({
                "ts": int(ts),
                "phrase": phrase,
                "outcome": outcome,
                "ok": action_count > 0,
            })
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Retention ──────────────────────────────────────────────
def prune_older_than(retention_days: int) -> int:
    cutoff = int(time.time()) - max(1, int(retention_days)) * 86400
    conn = _open_db()
    try:
        cur = conn.execute("DELETE FROM events WHERE ts_unix < ?", (cutoff,))
        return cur.rowcount or 0
    finally:
        conn.close()


# ── Dashboard stats ────────────────────────────────────────
def _percentile(sorted_vals: List[int], pct: float) -> int:
    if not sorted_vals:
        return 0
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * (len(sorted_vals) - 1)))))
    return int(sorted_vals[k])


def _latency_bundle(conn: sqlite3.Connection, kind: str,
                    t_today: int, target_filter: str = "",
                    target_exclude: str = "",
                    fields: Tuple[str, ...] = ("latency_ms",)) -> Dict[str, int]:
    """(avg_ms, p50_ms, p95_ms, count_today) over a 30 d sample.

    `fields` is COALESCEd in priority order — first non-null wins. Lets
    callers prefer e.g. `llm_ms` (LLM HTTP-only span) but fall back to
    legacy `latency_ms` rows. `target_exclude` filters a stale target.
    """
    parts = [f"json_extract(meta_json,'$.{f}')" for f in fields]
    extract = parts[0] if len(parts) == 1 else " COALESCE(" + ", ".join(parts) + ")"
    where = f"kind = ? AND {extract} IS NOT NULL"
    params: List[Any] = [kind]
    if target_filter:
        where += " AND target = ?"
        params.append(target_filter)
    if target_exclude:
        where += " AND target != ?"
        params.append(target_exclude)
    # Today's count
    cur_today = conn.execute(
        f"SELECT COUNT(*) FROM events WHERE {where} AND ts_unix >= ?",
        params + [t_today],
    ).fetchone()
    count_today = int(cur_today[0] if cur_today else 0)
    # Sample up to 2000 most recent for avg/p50/p95 (bound memory)
    cur = conn.execute(
        f"SELECT CAST({extract} AS INTEGER) AS lat "
        f"FROM events WHERE {where} AND ts_unix >= ? "
        f"ORDER BY ts_unix DESC LIMIT 2000",
        params + [t_today - 30 * 86400],
    )
    vals = sorted(int(r[0]) for r in cur.fetchall() if r[0] is not None)
    if not vals:
        return {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "count_today": count_today}
    avg = sum(vals) // len(vals)
    p50 = _percentile(vals, 50)
    p95 = _percentile(vals, 95)
    return {"avg_ms": int(avg), "p50_ms": int(p50),
            "p95_ms": int(p95), "count_today": count_today}


def _tts_latency_bundle(conn: sqlite3.Connection, t_today: int) -> Dict[str, int]:
    """TTS panel: prefer `synth_ms` (cold-load excluded), fall back to `latency_ms`."""
    return _latency_bundle(conn, "tts_request", t_today,
                           fields=("synth_ms", "latency_ms"))


def _llm_latency_bundle(conn: sqlite3.Connection, t_today: int) -> Dict[str, int]:
    """LLM panel: prefer `llm_ms` (HTTP-only), fall back to `latency_ms`.

    Excludes legacy `parse_voice_combined` target (dead code path).
    """
    return _latency_bundle(conn, "llm_call", t_today,
                           target_exclude="parse_voice_combined",
                           fields=("llm_ms", "latency_ms"))


def _meta_field_floats(conn: sqlite3.Connection, kind: str, field_name: str,
                       since_unix: int, limit: int = 2000,
                       require_positive: bool = True) -> List[float]:
    """Pull non-null numeric values of meta_json.<field_name> for `kind` events.

    Returns most-recent-first (capped at `limit`) — order does not matter to
    callers that compute avg/p95.
    """
    extract = f"json_extract(meta_json,'$.{field_name}')"
    where = f"kind = ? AND {extract} IS NOT NULL"
    if require_positive:
        where += f" AND CAST({extract} AS REAL) > 0"
    cur = conn.execute(
        f"SELECT CAST({extract} AS REAL) FROM events "
        f"WHERE {where} AND ts_unix >= ? "
        f"ORDER BY ts_unix DESC LIMIT ?",
        (kind, since_unix, limit),
    )
    return [float(r[0]) for r in cur.fetchall() if r[0] is not None]


def compute_dashboard_stats() -> Dict[str, Any]:
    """Roll up events DB into the StatsReply shape. All-numeric, never raises."""
    now = int(time.time())
    # "today" = local midnight. struct_time → mktime gives correct DST handling.
    lt = time.localtime(now)
    t_today = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                               0, 0, 0, 0, 0, -1)))
    out: Dict[str, Any] = {
        "soundboard": {"sounds_backed_up": 0, "storage_bytes": 0,
                       "last_sync_unix": 0, "new_since_last": 0},
        "voice_playback": {"played_today": 0, "played_all_time": 0,
                           "session_seconds_today": 0, "avg_response_ms": 0},
        "wake_word": {"detections_today": 0, "detections_all_time": 0,
                      "false_positives_today": 0},
        "stt": {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "count_today": 0,
                "chunk_avg_ms": 0, "chunk_p50_ms": 0, "chunk_p95_ms": 0,
                "avg_audio_seconds": 0.0},
        "tts": {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "count_today": 0},
        "llm": {"response_avg_ms": 0, "response_p50_ms": 0,
                "response_p95_ms": 0,
                "tokens_per_sec": 0, "requests_today": 0,
                "avg_tokens_out": 0, "context_usage_pct": 0,
                "timeouts_today": 0},
        "mock": False,
    }
    try:
        conn = _open_db()
    except Exception as e:
        log.warning("compute_dashboard_stats open_db failed: %s", e)
        out["mock"] = True
        return out
    try:
        # Soundboard plays
        sb_all = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'soundboard_play'"
        ).fetchone()
        sb_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'soundboard_play' AND ts_unix >= ?",
            (t_today,),
        ).fetchone()
        out["voice_playback"]["played_all_time"] = int(sb_all[0] if sb_all else 0)
        out["voice_playback"]["played_today"] = int(sb_today[0] if sb_today else 0)

        # Voice session seconds today: sum voice_leave.duration_seconds for
        # sessions that ended today. Sessions still in progress are NOT
        # included until they emit voice_leave on disconnect.
        vl_dur = conn.execute(
            "SELECT COALESCE(SUM(CAST(json_extract(meta_json,'$.duration_seconds') AS INTEGER)), 0) "
            "FROM events WHERE kind = 'voice_leave' AND ts_unix >= ?",
            (t_today,),
        ).fetchone()
        out["voice_playback"]["session_seconds_today"] = int(vl_dur[0] if vl_dur else 0)

        # Avg voice response ms = TTS p50 latency over 30 d window. Prefer
        # synth_ms (cold-load excluded) when populated; fall back to
        # latency_ms for old rows that predate the split.
        tts_today = _tts_latency_bundle(conn, t_today)
        out["tts"] = tts_today
        out["voice_playback"]["avg_response_ms"] = tts_today["p50_ms"]

        # Wake-word proxy: voice-path LLM calls succeed => detection.
        # (True wake event emitter TBD; this counts parsed voice commands.)
        vc_all = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'llm_call' AND target LIKE 'parse_voice%'"
        ).fetchone()
        vc_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'llm_call' AND target LIKE 'parse_voice%' AND ts_unix >= ?",
            (t_today,),
        ).fetchone()
        out["wake_word"]["detections_all_time"] = int(vc_all[0] if vc_all else 0)
        out["wake_word"]["detections_today"] = int(vc_today[0] if vc_today else 0)
        fp_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'llm_call' "
            "AND target LIKE 'parse_voice%' "
            "AND json_extract(meta_json,'$.status') = 'no_wake' "
            "AND ts_unix >= ?",
            (t_today,),
        ).fetchone()
        out["wake_word"]["false_positives_today"] = int(fp_today[0] if fp_today else 0)

        # LLM latency (excludes legacy parse_voice_combined; prefers HTTP-only
        # llm_ms when present).
        llm_today = _llm_latency_bundle(conn, t_today)
        out["llm"]["response_avg_ms"] = llm_today["avg_ms"]
        out["llm"]["response_p50_ms"] = llm_today["p50_ms"]
        out["llm"]["response_p95_ms"] = llm_today["p95_ms"]
        out["llm"]["requests_today"] = llm_today["count_today"]

        # LLM tokens / throughput / context — sampled from same 30d window
        # as latency. tokens_out is averaged; tokens_per_sec is the avg of
        # per-event ratios (more representative than total/total when calls
        # vary widely in size); context_usage_pct uses configured window.
        since30 = t_today - 30 * 86400
        toks_out = _meta_field_floats(conn, "llm_call", "tokens_out", since30)
        if toks_out:
            out["llm"]["avg_tokens_out"] = int(sum(toks_out) / len(toks_out))
        # Per-event throughput: pull (tokens_out, latency_ms) pairs.
        cur = conn.execute(
            "SELECT CAST(json_extract(meta_json,'$.tokens_out') AS REAL), "
            "       CAST(json_extract(meta_json,'$.latency_ms') AS REAL) "
            "FROM events WHERE kind = 'llm_call' "
            "  AND json_extract(meta_json,'$.tokens_out') IS NOT NULL "
            "  AND CAST(json_extract(meta_json,'$.tokens_out') AS REAL) > 0 "
            "  AND CAST(json_extract(meta_json,'$.latency_ms') AS REAL) > 0 "
            "  AND ts_unix >= ? "
            "ORDER BY ts_unix DESC LIMIT 2000",
            (since30,),
        )
        rates = [(t / lat) * 1000.0 for t, lat in cur.fetchall() if t and lat]
        if rates:
            out["llm"]["tokens_per_sec"] = int(sum(rates) / len(rates))
        # Context window: prompt_tokens / configured window × 100, averaged.
        try:
            ctx_window = int(config.get("llm_context_window") or 8192)
        except (TypeError, ValueError):
            ctx_window = 8192
        if ctx_window <= 0:
            ctx_window = 8192
        prompt_toks = _meta_field_floats(conn, "llm_call", "prompt_tokens", since30)
        if prompt_toks:
            avg_pct = (sum(prompt_toks) / len(prompt_toks)) / ctx_window * 100.0
            out["llm"]["context_usage_pct"] = int(round(min(100.0, max(0.0, avg_pct))))
        # Timeouts today: count events whose meta outcome is 'timeout'.
        to_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'llm_call' "
            "AND json_extract(meta_json,'$.outcome') = 'timeout' "
            "AND ts_unix >= ?",
            (t_today,),
        ).fetchone()
        out["llm"]["timeouts_today"] = int(to_today[0] if to_today else 0)

        # STT latency + chunk decode + utterance length
        stt_today = _latency_bundle(conn, "stt_request", t_today)
        out["stt"]["avg_ms"] = stt_today["avg_ms"]
        out["stt"]["p50_ms"] = stt_today["p50_ms"]
        out["stt"]["p95_ms"] = stt_today["p95_ms"]
        out["stt"]["count_today"] = stt_today["count_today"]
        chunk_vals = sorted(int(v) for v in _meta_field_floats(
            conn, "stt_request", "decode_ms", since30,
        ))
        if chunk_vals:
            out["stt"]["chunk_avg_ms"] = sum(chunk_vals) // len(chunk_vals)
            out["stt"]["chunk_p50_ms"] = _percentile(chunk_vals, 50)
            out["stt"]["chunk_p95_ms"] = _percentile(chunk_vals, 95)
        audio_secs = _meta_field_floats(conn, "stt_request", "audio_seconds", since30)
        if audio_secs:
            out["stt"]["avg_audio_seconds"] = round(sum(audio_secs) / len(audio_secs), 2)

        # Soundboard table totals from sounds DB
        try:
            from . import db as _sounds_db
            rows = _sounds_db.db_list()
            out["soundboard"]["sounds_backed_up"] = len(rows)
            out["soundboard"]["storage_bytes"] = int(sum(int(r.get("size_bytes") or 0) for r in rows))
            if rows:
                # created_at is ISO text; best-effort parse
                last = max((r.get("created_at") or "") for r in rows)
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    out["soundboard"]["last_sync_unix"] = int(dt.timestamp())
                except Exception:
                    out["soundboard"]["last_sync_unix"] = 0
                day_ago = now - 86400
                if out["soundboard"]["last_sync_unix"]:
                    out["soundboard"]["new_since_last"] = sum(
                        1 for r in rows
                        if (r.get("created_at") or "") and
                           _iso_ts(r["created_at"]) >= day_ago
                    )
        except Exception as e:
            log.warning("sounds db rollup failed: %s", e)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _iso_ts(s: str) -> int:
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


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
