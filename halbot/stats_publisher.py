"""/halbot-stats backend: snapshot dashboard data + push as a static site.

`publish_now(client, force=False)` is the single entry point used by the slash
handler. Steps:

1. Snapshot live data (dashboard rollup + 4 analytics aggregates + soundboard
   + emojis) into a JSON-safe dict. Discord user IDs are resolved to display
   names because the resulting URL is shared on a public-ish channel.
2. Copy the bundled ``frontend/dist`` tree into a tmp staging dir, inject
   ``window.__STATS_SNAPSHOT__`` into ``index.html``, hand the dir to the
   configured publisher (``halbot.publishers.get_publisher``).
3. Throttle: a successful publish caches its URL for
   ``stats_min_publish_interval_seconds``; subsequent calls within that
   window return the cached URL with ``cached=True`` unless ``force=True``.

Everything below the slash handler is sync — boto3 is sync, so the slash
handler runs ``publish_now`` via ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import analytics, config, paths
from . import db as sounds_db
from .publishers import get_publisher

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_SNAPSHOT_GLOBAL = "__STATS_SNAPSHOT__"


@dataclass
class PublishResult:
    url: str
    generated_at_utc: str
    cached: bool
    bytes_uploaded: int
    file_count: int
    snapshot_ms: int
    publish_ms: int


_lock = threading.Lock()
_last_result: Optional[PublishResult] = None
_last_publish_ts: float = 0.0


# ── User display-name resolution ──────────────────────────────


def _user_label(client: Any, user_id: int, cache: Dict[int, str]) -> str:
    if not user_id:
        return ""
    if user_id in cache:
        return cache[user_id]
    label = ""
    try:
        # Walk caches first — fast and avoids ratelimits.
        u = None
        if hasattr(client, "get_user"):
            u = client.get_user(int(user_id))
        if u is None:
            for g in getattr(client, "guilds", []):
                m = g.get_member(int(user_id))
                if m is not None:
                    u = m
                    break
        if u is not None:
            label = (
                getattr(u, "display_name", None)
                or getattr(u, "global_name", None)
                or getattr(u, "name", None)
                or ""
            )
    except Exception:
        label = ""
    if not label:
        label = f"user_{str(user_id)[-4:]}"
    cache[int(user_id)] = label
    return label


_FETCH_MEMBER_BUDGET = 25  # match bot.py:1378 — avoid rate-limit


async def resolve_user_labels(client: Any, user_ids: List[int],
                              *, known: Optional[Dict[int, str]] = None,
                              ) -> Dict[int, str]:
    """Async pre-resolve user IDs → display names for the snapshot.

    Three-tier resolution per Discord's nickname/global-name model:

      1. `guild.get_member(uid)` — cached per-guild Member; gives nickname
         (the user-set per-server name shown in chat).
      2. `client.get_user(uid)` — cached global User; gives global_name
         (the user-set cross-server display name) or login name.
      3. `guild.fetch_member(uid)` (HTTP, bounded to _FETCH_MEMBER_BUDGET);
         returns a Member with nickname. Bot's existing stats handler
         uses this same pattern (bot.py:1378).
      4. `client.fetch_user(uid)` (HTTP) — last resort, returns User with
         no nickname info.

    The sync `_user_label` only walks (1)+(2). Slash callers prefill
    this cache via `await resolve_user_labels(...)` before handing the
    snapshot to a worker thread; otherwise tier-3/4 never run and rows
    fall through to the `user_NNNN` placeholder.

    `known` is a pre-populated {uid: label} cache; IDs in `known` are
    skipped entirely (no cache walk, no HTTP). Callers that hold a
    persistent cache pass it here to avoid re-fetching names every
    request — fetch_member is rate-limited and Discord display names
    rarely change.
    """
    out: Dict[int, str] = {}
    if not client or not user_ids:
        return out
    known_map = known or {}
    uids = sorted({int(u) for u in user_ids
                   if u and int(u) not in known_map})
    if not uids:
        return out
    guilds = list(getattr(client, "guilds", []))
    needs_fetch: list[int] = []

    def _label_of(u: Any) -> str:
        return (
            getattr(u, "display_name", None)
            or getattr(u, "global_name", None)
            or getattr(u, "name", None)
            or ""
        )

    # Tier 1+2: cache walk
    for uid in uids:
        u = None
        for g in guilds:
            m = g.get_member(uid)
            if m is not None:
                u = m
                break
        if u is None and hasattr(client, "get_user"):
            u = client.get_user(uid)
        if u is not None:
            lab = _label_of(u)
            if lab:
                out[uid] = lab
                continue
        needs_fetch.append(uid)

    # Tier 3+4: HTTP, bounded
    for uid in needs_fetch[:_FETCH_MEMBER_BUDGET]:
        u = None
        for g in guilds:
            try:
                u = await g.fetch_member(uid)
                if u is not None:
                    break
            except Exception:
                continue  # not a member here; try next guild or fall through
        if u is None and hasattr(client, "fetch_user"):
            try:
                u = await client.fetch_user(uid)
            except Exception as e:
                log.debug("[stats_publisher] fetch_user(%s) failed: %s", uid, e)
                u = None
        if u is not None:
            lab = _label_of(u)
            if lab:
                out[uid] = lab

    if needs_fetch and len(needs_fetch) > _FETCH_MEMBER_BUDGET:
        log.info(
            "[stats_publisher] resolve_user_labels: budget exhausted (%d > %d)",
            len(needs_fetch), _FETCH_MEMBER_BUDGET,
        )
    return out


def _treat_user_rows(client: Any, rows: List[Dict[str, Any]],
                     cache: Dict[int, str]) -> List[Dict[str, Any]]:
    """Replace ``key`` (user_id string) with display-name per config.

    Drops rows with user_id == 0 (system / bot self) entirely — they
    aren't a user and cluttered the leaderboard with a bare "0" entry.
    """
    treatment = (config.get("stats_user_id_treatment") or "display_name").strip()
    out: List[Dict[str, Any]] = []
    for r in rows:
        new = dict(r)
        raw = str(new.get("key") or "")
        try:
            uid = int(raw)
        except ValueError:
            uid = 0
        if not uid:
            continue  # skip system/null user rows
        if treatment == "raw":
            pass
        elif treatment == "omit":
            new["key"] = ""
        elif treatment == "hash":
            new["key"] = f"u#{uid % 10000:04d}"
        else:  # display_name
            new["key"] = _user_label(client, uid, cache) or new["key"]
        out.append(new)
    return out


# ── Snapshot ──────────────────────────────────────────────────


def _query(**kw) -> Dict[str, Any]:
    try:
        total, rows = analytics.query_stats(**kw)
        return {"total_count": int(total), "rows": rows}
    except Exception as e:
        log.warning("[stats_publisher] query_stats(%s) failed: %s", kw, e)
        return {"total_count": 0, "rows": []}


def _soundboard_table() -> List[Dict[str, Any]]:
    """Same join Stats.jsx renders: sounds.db rows + 30d play counts."""
    try:
        rows = sounds_db.db_list()
    except Exception as e:
        log.warning("[stats_publisher] sounds_db.db_list failed: %s", e)
        rows = []
    ts_from = int(time.time()) - 30 * 86400
    try:
        _total, play_rows = analytics.query_stats(
            kind="soundboard_play", ts_from=ts_from,
            group_by="target", limit=1000,
        )
        plays = {r["key"]: (int(r["count"]), int(r["last_ts_unix"])) for r in play_rows}
    except Exception as e:
        log.warning("[stats_publisher] play count query failed: %s", e)
        plays = {}

    out: List[Dict[str, Any]] = []
    saved_names = set()
    for row in rows:
        name = row.get("name") or ""
        saved_names.add(name)
        count, last = plays.get(name, (0, 0))
        out.append({
            "id": int(row.get("id") or 0),
            "parent_id": int(row["parent_id"]) if row.get("parent_id") else None,
            "effects": row.get("effects") or "",
            "name": name,
            "emoji": row.get("emoji") or "",
            "metadata": row.get("metadata") or "",
            "size_bytes": int(row.get("size_bytes") or 0),
            "saved_by": row.get("saved_by") or "",
            "created_at": row.get("created_at") or "",
            "plays": count,
            "last_played_unix": last,
        })
    for name, (count, last) in plays.items():
        if name and name not in saved_names:
            out.append({
                "id": 0, "parent_id": None, "effects": "",
                "name": name, "emoji": "", "metadata": "",
                "size_bytes": 0, "saved_by": "(live)",
                "created_at": "", "plays": count, "last_played_unix": last,
            })
    out.sort(key=lambda r: r["plays"], reverse=True)
    return out


_CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")


def _emoji_table(referenced_ids: Optional[set] = None,
                 referenced_names: Optional[set] = None) -> List[Dict[str, Any]]:
    """Emoji rollup; only includes rows referenced by visible soundboard rows.

    `emoji_id` emitted as a string because Discord snowflakes (~1e18) exceed
    JS Number.MAX_SAFE_INTEGER (~9e15) and silently lose precision when
    parsed by the browser. Image bytes are inlined as base64 only for rows
    we ship — unreferenced emojis would balloon the snapshot (~1.7 MB
    saved on a typical bot).
    """
    import base64
    try:
        rows = sounds_db.emoji_db_list_full()
    except Exception as e:
        log.warning("[stats_publisher] emoji db list failed: %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        emoji_id = str(r.get("emoji_id") or "")
        name = r.get("name") or ""
        if referenced_ids is not None or referenced_names is not None:
            if (emoji_id and referenced_ids and emoji_id in referenced_ids):
                pass
            elif (name and referenced_names and name in referenced_names):
                pass
            else:
                continue
        img = r.get("image") or b""
        mime = "image/gif" if img[:4] == b"GIF8" else "image/png"
        b64 = base64.b64encode(img).decode("ascii") if img else ""
        out.append({
            "emoji_id": emoji_id,
            "name": name,
            "animated": bool(r.get("animated")),
            "description": r.get("description") or "",
            "image_data_url": f"data:{mime};base64,{b64}" if b64 else "",
            "size_bytes": len(img),
        })
    return out


def _referenced_emoji_keys(*row_lists: List[Dict[str, Any]]) -> tuple[set, set]:
    """Extract custom Discord emoji IDs + names from any number of row lists.

    Returns (ids, names) — both used by `_emoji_table` to filter the bundled
    rows. `names` mirrors Stats.jsx's `byName` fallback, which kicks in when
    an emoji was re-uploaded with a new ID but the same name.
    Unicode emoji cells (no `<:name:id>` form) need no lookup row.

    Accepts multiple row lists so soundboard, top_sounds, etc. all
    contribute referenced emoji keys to the bundled emoji table.
    """
    ids: set = set()
    names: set = set()
    for rows in row_lists:
        for row in rows or []:
            raw = row.get("emoji") or ""
            m = _CUSTOM_EMOJI_RE.search(raw)
            if m:
                names.add(m.group(1))
                ids.add(m.group(2))
    return ids, names


def snapshot_stats(client: Any,
                   user_label_cache: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """Build the dict that becomes ``window.__STATS_SNAPSHOT__``.

    `user_label_cache` is a pre-resolved {user_id: display_name} map.
    Pass results from `await resolve_user_labels(...)` here so the sync
    snapshot path doesn't fall back to `user_NNNN` for IDs not in
    discord.py's caches.
    """
    now = int(time.time())
    user_cache: Dict[int, str] = dict(user_label_cache or {})
    # Match the four aggregates the Analytics panel renders against the 30d window.
    ts_30d = now - 30 * 86400

    sounds_30d = _query(kind="soundboard_play", ts_from=ts_30d, group_by="target", limit=20)
    users_30d = _query(ts_from=ts_30d, group_by="user_id", limit=20)
    cmds_30d = _query(kind="cmd_invoke", ts_from=ts_30d, group_by="target", limit=15)
    kinds_30d = _query(ts_from=ts_30d, group_by="kind", limit=12)

    users_30d["rows"] = _treat_user_rows(client, users_30d.get("rows", []), user_cache)
    # Enrich top-sounds rows with emoji icons. Same two sources as
    # mgmt_server.QueryStats: saved_sounds (user uploads) + Discord
    # built-in defaults (cached at on_ready in bot.py).
    sound_emoji: Dict[str, str] = {}
    try:
        from . import bot as _bot
        sound_emoji.update(getattr(_bot, "_default_sound_emojis", {}) or {})
    except Exception:
        log.exception("[stats_publisher] default-sound emoji map unavailable")
    try:
        for r in sounds_db.db_list():
            nm = (r.get("name") or "").strip()
            em = (r.get("emoji") or "").strip()
            if nm and em:
                sound_emoji[nm] = em
    except Exception:
        log.exception("[stats_publisher] saved_sounds emoji enrichment failed")
    for row in sounds_30d.get("rows", []):
        em = sound_emoji.get(row.get("key") or "", "")
        if em:
            row["emoji"] = em

    soundboard = _soundboard_table()
    ref_ids, ref_names = _referenced_emoji_keys(soundboard, sounds_30d.get("rows", []))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "window_seconds": 30 * 86400,
        "stats": analytics.compute_dashboard_stats(),
        "analytics": {
            "top_sounds": sounds_30d,
            "top_users": users_30d,
            "top_commands": cmds_30d,
            "kind_mix": kinds_30d,
        },
        "soundboard": soundboard,
        "emoji": _emoji_table(referenced_ids=ref_ids, referenced_names=ref_names),
    }


def collect_user_ids_for_resolution() -> List[int]:
    """Return the user_ids that will appear in the next snapshot's top_users.

    Used by the slash handler to pre-resolve display names async before
    handing control to the sync `publish_now` thread.
    """
    now = int(time.time())
    ts_30d = now - 30 * 86400
    try:
        _total, rows = analytics.query_stats(
            ts_from=ts_30d, group_by="user_id", limit=20,
        )
    except Exception as e:
        log.warning("[stats_publisher] collect_user_ids failed: %s", e)
        return []
    out: List[int] = []
    for r in rows:
        try:
            uid = int(r.get("key") or 0)
        except (TypeError, ValueError):
            continue
        if uid:
            out.append(uid)
    return out


# ── HTML injection ────────────────────────────────────────────


def _js_string_literal(payload: str) -> str:
    """Encode `payload` as a JS string literal safe for inline <script>.

    Belt-and-suspenders against `</script>` and U+2028/U+2029 line separators
    that JSON allows but JavaScript doesn't.
    """
    out = json.dumps(payload, ensure_ascii=False)
    out = out.replace("</", "<\\/")
    out = out.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return out


def render_snapshot_html(template_html: str, snapshot: Dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    literal = _js_string_literal(payload)
    inject = (
        f"<script>window.{_SNAPSHOT_GLOBAL} = JSON.parse({literal});</script>"
    )
    head_close = template_html.find("</head>")
    if head_close < 0:
        # Fallback: prepend; React will still pick it up if loaded later.
        return inject + template_html
    return template_html[:head_close] + inject + template_html[head_close:]


# ── Orchestrator ──────────────────────────────────────────────


def _staging_copy(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(
            f"frontend dist not found at {src} — run `cd frontend && npm run build`"
        )
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _stage_size(staging: Path) -> tuple[int, int]:
    files = [p for p in staging.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _record_event(*, latency_ms: int, bytes_uploaded: int, file_count: int,
                  cached: bool, target: str, error: Optional[str] = None,
                  user_id: int = 0) -> None:
    try:
        analytics.record(
            "stats_publish",
            user_id=int(user_id or 0),
            target=target or "stats",
            latency_ms=int(latency_ms),
            bytes=int(bytes_uploaded),
            files=int(file_count),
            cached=bool(cached),
            status=("error" if error else "ok"),
            error=error or "",
        )
    except Exception:
        pass


def publish_now(client: Any, *, force: bool = False, user_id: int = 0,
                user_label_cache: Optional[Dict[int, str]] = None) -> PublishResult:
    """Build a snapshot, upload it via the configured publisher, return URL.

    Throttled by ``stats_min_publish_interval_seconds``. Thread-safe: only one
    physical publish runs at a time; concurrent callers within the throttle
    window receive the cached result.
    """
    global _last_result, _last_publish_ts

    with _lock:
        try:
            min_interval = float(config.get("stats_min_publish_interval_seconds") or 0)
        except (TypeError, ValueError):
            min_interval = 0.0
        now = time.monotonic()
        if (
            not force
            and _last_result is not None
            and (now - _last_publish_ts) < min_interval
        ):
            cached = PublishResult(
                url=_last_result.url,
                generated_at_utc=_last_result.generated_at_utc,
                cached=True,
                bytes_uploaded=_last_result.bytes_uploaded,
                file_count=_last_result.file_count,
                snapshot_ms=_last_result.snapshot_ms,
                publish_ms=_last_result.publish_ms,
            )
            _record_event(
                latency_ms=0, bytes_uploaded=cached.bytes_uploaded,
                file_count=cached.file_count, cached=True,
                target=(config.get("stats_publisher") or "s3"),
                user_id=user_id,
            )
            log.info("[stats_publisher] cached: %s", cached.url)
            return cached

        publisher_name = (config.get("stats_publisher") or "s3").strip().lower()
        publisher = get_publisher(publisher_name)
        dist_root = paths.frontend_dist_dir()

        snap_t0 = time.monotonic()
        snapshot = snapshot_stats(client, user_label_cache=user_label_cache)
        snap_ms = int((time.monotonic() - snap_t0) * 1000)

        with tempfile.TemporaryDirectory(prefix="halbot-stats-") as tmpdir:
            staging = Path(tmpdir) / "site"
            _staging_copy(dist_root, staging)
            index = staging / "index.html"
            html = index.read_text(encoding="utf-8")
            index.write_text(render_snapshot_html(html, snapshot), encoding="utf-8")
            file_count, bytes_total = _stage_size(staging)

            pub_t0 = time.monotonic()
            try:
                url = publisher.publish(staging)
            except Exception as e:
                pub_ms = int((time.monotonic() - pub_t0) * 1000)
                log.exception("[stats_publisher] publish failed (%s)", publisher_name)
                _record_event(
                    latency_ms=pub_ms, bytes_uploaded=bytes_total,
                    file_count=file_count, cached=False,
                    target=publisher_name, user_id=user_id,
                    error=f"{type(e).__name__}: {str(e)[:200]}",
                )
                raise
            pub_ms = int((time.monotonic() - pub_t0) * 1000)

        result = PublishResult(
            url=url,
            generated_at_utc=snapshot["generated_at_utc"],
            cached=False,
            bytes_uploaded=bytes_total,
            file_count=file_count,
            snapshot_ms=snap_ms,
            publish_ms=pub_ms,
        )
        _last_result = result
        _last_publish_ts = now
        _record_event(
            latency_ms=snap_ms + pub_ms, bytes_uploaded=bytes_total,
            file_count=file_count, cached=False,
            target=publisher_name, user_id=user_id,
        )
        log.info(
            "[stats_publisher] published %s (snapshot=%dms upload=%dms files=%d bytes=%d)",
            url, snap_ms, pub_ms, file_count, bytes_total,
        )
        return result
