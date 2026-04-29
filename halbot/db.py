import os
import sqlite3
import time

from . import paths

DB_PATH = str(paths.data_dir() / "sounds.db")
METADATA_MAX_BYTES = 2048
PERSONA_MAX_CHARS = 500
PERSONA_MAX_TOTAL = 10

# Hooks (Facts / Triggers / Grudges) — per plan 012.
FACT_MAX_CHARS = 400
FACT_MAX_TOTAL = 100
TRIGGER_MAX_CHARS = 300
TRIGGER_MAX_TOTAL = 50
GRUDGE_NOTE_MAX_CHARS = 200
GRUDGE_MAX_TOTAL = 100
GRUDGE_POLARITY_MIN = -3
GRUDGE_POLARITY_MAX = 3

TRIGGER_MATCH_KINDS = ("keyword_text", "keyword_voice")
TRIGGER_ACTIONS = ("reply", "voice_play")

from . import config as _config


def _cfg_int(name: str, default: int) -> int:
    try:
        return max(0, int(_config.get(name)))
    except (ValueError, TypeError):
        return default


def _cfg_bool(name: str, default: bool) -> bool:
    raw = _config.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y", "t")


try:
    VOICE_HISTORY_TURNS = _cfg_int("voice_history_turns", 10)
except Exception:
    VOICE_HISTORY_TURNS = 10


def _env_bool(name: str, default: bool) -> bool:
    # Back-compat shim for callers that still import _env_bool. Reads
    # registry config, not env.
    return _cfg_bool(name, default)


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_sounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                audio BLOB NOT NULL,
                emoji TEXT,
                metadata TEXT DEFAULT '',
                saved_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                parent_id INTEGER REFERENCES saved_sounds(id) ON DELETE SET NULL,
                effects TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emojis (
                emoji_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                animated INTEGER DEFAULT 0,
                image BLOB NOT NULL,
                description TEXT DEFAULT '',
                description_attempted INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directive TEXT NOT NULL,
                set_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                user_display_name TEXT NOT NULL,
                transcript TEXT NOT NULL,
                bot_response TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_history_guild_ts "
                     "ON voice_history(guild_id, ts DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_reconnect (
                guild_id INTEGER PRIMARY KEY,
                vc_channel_id INTEGER NOT NULL,
                sink_kind TEXT NOT NULL,
                sink_arg INTEGER,
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wake_variants (
                token TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        # Seed the wake-variant dictionary on first boot. Keeps wake
        # detection working before any /halbot-admin wake-variants
        # generate has run.
        seed_count = conn.execute(
            "SELECT COUNT(*) FROM wake_variants"
        ).fetchone()[0]
        if seed_count == 0:
            now = int(time.time())
            seed_tokens = (
                "robot", "ro bot", "ro-bot", "roebot", "roe bot",
                "robots", "roboto", "robo ", "row bot", "rowbot",
            )
            conn.executemany(
                "INSERT OR IGNORE INTO wake_variants (token, source, created_at) "
                "VALUES (?, 'seed', ?)",
                [(t, now) for t in seed_tokens],
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                claim TEXT NOT NULL,
                set_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject "
                     "ON facts(subject COLLATE NOCASE)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_kind TEXT NOT NULL,
                match_value TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_payload TEXT NOT NULL,
                set_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fire_count INTEGER NOT NULL DEFAULT 0,
                last_fired_at TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_triggers_kind "
                     "ON triggers(match_kind)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grudges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_name TEXT NOT NULL,
                polarity INTEGER NOT NULL,
                note TEXT DEFAULT '',
                set_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_grudges_target "
                     "ON grudges(target_name COLLATE NOCASE)")
        # Migrate: add description_attempted column if missing
        cols = {row[1] for row in conn.execute("PRAGMA table_info(emojis)").fetchall()}
        if "description_attempted" not in cols:
            conn.execute("ALTER TABLE emojis ADD COLUMN description_attempted INTEGER DEFAULT 0")
        # Migrate: add effects tracking columns to saved_sounds if missing
        sound_cols = {row[1] for row in conn.execute("PRAGMA table_info(saved_sounds)").fetchall()}
        if "parent_id" not in sound_cols:
            conn.execute("ALTER TABLE saved_sounds ADD COLUMN parent_id INTEGER REFERENCES saved_sounds(id) ON DELETE SET NULL")
        if "effects" not in sound_cols:
            conn.execute("ALTER TABLE saved_sounds ADD COLUMN effects TEXT DEFAULT ''")
        # Soft-delete migration: every kind of "hook" (plus sounds) gets a
        # deleted_at column. NULL = live. Non-null = tombstoned and
        # invisible to normal list queries. Recoverable via admin undelete.
        for tbl in ("saved_sounds", "personas", "facts", "triggers", "grudges"):
            try:
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            except sqlite3.OperationalError:
                continue
            if cols and "deleted_at" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN deleted_at TIMESTAMP")
        # Persona scope migration: "user" (per-user directive) or "guild" (applies to everyone).
        persona_cols = {row[1] for row in conn.execute("PRAGMA table_info(personas)").fetchall()}
        if persona_cols and "scope" not in persona_cols:
            conn.execute("ALTER TABLE personas ADD COLUMN scope TEXT NOT NULL DEFAULT 'user'")
        if persona_cols and "fire_count" not in persona_cols:
            conn.execute("ALTER TABLE personas ADD COLUMN fire_count INTEGER NOT NULL DEFAULT 0")


class DuplicateSound(Exception):
    """Raised by db_save when a live row collides by name or audio content.

    Carries the existing row's id/name and which dimension matched so callers
    can tell the user which slot is occupied without running another query.
    """

    def __init__(self, existing_id: int, existing_name: str, kind: str) -> None:
        super().__init__(f"duplicate by {kind}: existing #{existing_id} {existing_name!r}")
        self.existing_id = existing_id
        self.existing_name = existing_name
        self.kind = kind  # "name" or "audio"


def db_save(name: str, audio: bytes, emoji: str | None, metadata: str | None, saved_by: str,
            parent_id: int | None = None, effects: str = "") -> int:
    metadata = metadata or ""
    if len(metadata.encode()) > METADATA_MAX_BYTES:
        raise ValueError(f"Metadata exceeds {METADATA_MAX_BYTES} bytes")
    with _db() as conn:
        name_row = conn.execute(
            "SELECT id, name FROM saved_sounds "
            "WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL LIMIT 1",
            (name,),
        ).fetchone()
        if name_row:
            raise DuplicateSound(name_row["id"], name_row["name"], "name")
        # Byte-identical audio under a different name: catches the
        # "save the soundboard sound twice" case AND the "apply the same
        # effect chain to the same source" case where save_as differs but
        # the output BLOB is the same. length() gate lets SQLite short-
        # circuit most rows before the BLOB comparison.
        audio_row = conn.execute(
            "SELECT id, name FROM saved_sounds "
            "WHERE length(audio) = ? AND audio = ? AND deleted_at IS NULL LIMIT 1",
            (len(audio), audio),
        ).fetchone()
        if audio_row:
            raise DuplicateSound(audio_row["id"], audio_row["name"], "audio")
        cur = conn.execute(
            "INSERT INTO saved_sounds (name, audio, emoji, metadata, saved_by, parent_id, effects) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, audio, emoji, metadata, saved_by, parent_id, effects),
        )
        return cur.lastrowid


def db_dedupe_sounds(db_path: str | None = None, dry_run: bool = False) -> dict:
    """One-shot: collapse name + audio duplicates among live saved_sounds rows.

    Pass 1 (name): group live rows by lower(name). Lowest id in each group =
    canonical; others soft-deleted.
    Pass 2 (audio): on rows still live, group by (length(audio), audio). Lowest
    id in each group = canonical; others soft-deleted.
    Pass 3 (parent rewrite): any still-live row whose parent_id now points at
    a soft-deleted row is rewritten to point at that group's canonical id
    (or cleared if the canonical itself ended up dead — shouldn't happen).

    db_path: pass a path to operate on a specific sqlite file (e.g. production
    %ProgramData%\\Halbot\\sounds.db from a source checkout). Defaults to
    DB_PATH.
    dry_run: compute + print groups; do not write.

    Returns {"name_groups", "audio_groups", "soft_deleted", "parent_rewrites",
    "canonical_map"} for audit.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, length(audio) as n, audio, parent_id "
            "FROM saved_sounds WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()

        name_groups: dict[str, list[int]] = {}
        for r in rows:
            key = (r["name"] or "").strip().lower()
            name_groups.setdefault(key, []).append(r["id"])
        name_dupes = {k: v for k, v in name_groups.items() if len(v) > 1}

        # canonical_map: soft-deleted id -> canonical id it was collapsed into.
        canonical_map: dict[int, int] = {}
        dead: set[int] = set()
        for ids in name_dupes.values():
            canonical = ids[0]
            for dup in ids[1:]:
                canonical_map[dup] = canonical
                dead.add(dup)

        # Pass 2: audio dedupe on survivors only.
        survivors = [r for r in rows if r["id"] not in dead]
        audio_groups: dict[tuple[int, bytes], list[int]] = {}
        for r in survivors:
            audio_groups.setdefault((r["n"], bytes(r["audio"])), []).append(r["id"])
        audio_dupes = {k: v for k, v in audio_groups.items() if len(v) > 1}
        for ids in audio_dupes.values():
            canonical = ids[0]
            for dup in ids[1:]:
                canonical_map[dup] = canonical
                dead.add(dup)

        # Pass 3: parent rewrites — any still-live row pointing into dead set.
        parent_rewrites: list[tuple[int, int, int]] = []  # (row_id, old_parent, new_parent)
        for r in rows:
            if r["id"] in dead:
                continue
            p = r["parent_id"]
            if p is None or p not in dead:
                continue
            # Follow chain in case canonical was itself collapsed (shouldn't
            # happen since we only collapse into survivors, but be defensive).
            target = p
            seen: set[int] = set()
            while target in canonical_map and target not in seen:
                seen.add(target)
                target = canonical_map[target]
            parent_rewrites.append((r["id"], p, target))

        result = {
            "name_groups": len(name_dupes),
            "audio_groups": len(audio_dupes),
            "soft_deleted": len(dead),
            "parent_rewrites": len(parent_rewrites),
            "canonical_map": canonical_map,
            "parent_rewrite_detail": parent_rewrites,
        }

        if dry_run:
            return result

        with conn:
            for dup_id in dead:
                conn.execute(
                    "UPDATE saved_sounds SET deleted_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (dup_id,),
                )
            for row_id, _old, new_parent in parent_rewrites:
                conn.execute(
                    "UPDATE saved_sounds SET parent_id = ? WHERE id = ?",
                    (new_parent, row_id),
                )
        return result
    finally:
        conn.close()


def db_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, metadata, saved_by, created_at, length(audio) as size_bytes, parent_id, effects "
            "FROM saved_sounds WHERE deleted_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def db_get(name: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, audio, emoji, metadata, saved_by, created_at, parent_id, effects "
            "FROM saved_sounds WHERE name = ? AND deleted_at IS NULL",
            (name,),
        ).fetchone()
        return dict(row) if row else None


def db_get_by_id(sound_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, audio, emoji, metadata, saved_by, created_at, parent_id, effects "
            "FROM saved_sounds WHERE id = ? AND deleted_at IS NULL",
            (sound_id,),
        ).fetchone()
        return dict(row) if row else None


def db_update(name: str, new_name: str | None = None, emoji: str | None = ..., metadata: str | None = None):
    fields, values = [], []
    if new_name is not None:
        fields.append("name = ?")
        values.append(new_name)
    if emoji is not ...:
        fields.append("emoji = ?")
        values.append(emoji)
    if metadata is not None:
        if len(metadata.encode()) > METADATA_MAX_BYTES:
            raise ValueError(f"Metadata exceeds {METADATA_MAX_BYTES} bytes")
        fields.append("metadata = ?")
        values.append(metadata)
    if not fields:
        return False
    values.append(name)
    with _db() as conn:
        cur = conn.execute(
            f"UPDATE saved_sounds SET {', '.join(fields)} WHERE name = ? AND deleted_at IS NULL",
            values,
        )
        return cur.rowcount > 0


def db_delete(name: str) -> bool:
    """Soft-delete: tombstone the row. Recoverable via admin_undelete('saved_sounds', id)."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE saved_sounds SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE name = ? AND deleted_at IS NULL",
            (name,),
        )
        return cur.rowcount > 0


def persona_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, directive, set_by, scope, fire_count, created_at FROM personas "
            "WHERE deleted_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def persona_mark_fired_all() -> int:
    """Bump fire_count for every active persona (one tick per delivered reply)."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET fire_count = fire_count + 1 WHERE deleted_at IS NULL"
        )
        return cur.rowcount


def persona_add(directive: str, set_by: str, scope: str = "user") -> int:
    if len(directive) > PERSONA_MAX_CHARS:
        raise ValueError(f"Directive too long ({len(directive)} chars). Max is {PERSONA_MAX_CHARS}.")
    if scope not in ("user", "guild"):
        raise ValueError(f"scope must be 'user' or 'guild', got {scope!r}")
    current = persona_list()
    if len(current) >= PERSONA_MAX_TOTAL:
        raise ValueError(f"Too many directives ({len(current)}). Max is {PERSONA_MAX_TOTAL}. Ask someone to clear some first.")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO personas (directive, set_by, scope) VALUES (?, ?, ?)",
            (directive, set_by, scope),
        )
        return cur.lastrowid


def persona_set_scope(persona_id: int, scope: str) -> bool:
    if scope not in ("user", "guild"):
        raise ValueError(f"scope must be 'user' or 'guild', got {scope!r}")
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET scope = ? WHERE id = ? AND deleted_at IS NULL",
            (scope, persona_id),
        )
        return cur.rowcount > 0


def persona_update(persona_id: int, directive: str) -> bool:
    if len(directive) > PERSONA_MAX_CHARS:
        raise ValueError(f"Directive too long ({len(directive)} chars). Max is {PERSONA_MAX_CHARS}.")
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET directive = ? WHERE id = ? AND deleted_at IS NULL",
            (directive, persona_id),
        )
        return cur.rowcount > 0


def persona_remove(persona_id: int) -> bool:
    """Soft-delete."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (persona_id,),
        )
        return cur.rowcount > 0


def persona_clear() -> int:
    """Soft-clear all active personas. Restorable via admin_undelete('personas', id)."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET deleted_at = CURRENT_TIMESTAMP WHERE deleted_at IS NULL"
        )
        return cur.rowcount


def voice_history_append(guild_id: int, user_display_name: str,
                         transcript: str, bot_response: str) -> None:
    """Record one completed voice turn. Prunes rows beyond VOICE_HISTORY_TURNS for this guild."""
    if VOICE_HISTORY_TURNS <= 0:
        return
    with _db() as conn:
        conn.execute(
            "INSERT INTO voice_history (guild_id, ts, user_display_name, transcript, bot_response) "
            "VALUES (?, strftime('%s','now'), ?, ?, ?)",
            (guild_id, user_display_name, transcript, bot_response),
        )
        conn.execute(
            """
            DELETE FROM voice_history
            WHERE guild_id = ?
              AND id NOT IN (
                  SELECT id FROM voice_history
                  WHERE guild_id = ?
                  ORDER BY ts DESC, id DESC
                  LIMIT ?
              )
            """,
            (guild_id, guild_id, VOICE_HISTORY_TURNS),
        )


def voice_history_load(guild_id: int, limit: int | None = None) -> list[dict]:
    """Return the guild's voice history oldest→newest, capped at limit."""
    if limit is None:
        limit = VOICE_HISTORY_TURNS
    if limit <= 0:
        return []
    with _db() as conn:
        rows = conn.execute(
            "SELECT user_display_name, transcript, bot_response "
            "FROM voice_history WHERE guild_id = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()
    turns = [
        {
            "user_display_name": r["user_display_name"],
            "transcript": r["transcript"],
            "bot_response": r["bot_response"],
        }
        for r in rows
    ]
    turns.reverse()
    return turns


def voice_history_clear(guild_id: int | None = None) -> int:
    """Drop history for one guild, or all guilds if guild_id is None."""
    with _db() as conn:
        if guild_id is None:
            cur = conn.execute("DELETE FROM voice_history")
        else:
            cur = conn.execute("DELETE FROM voice_history WHERE guild_id = ?", (guild_id,))
        return cur.rowcount


# ---------------------------------------------------------------------------
# Voice reconnect target — durable across hard crashes (TDR / OOM / segfault).
# Mirror of the in-process state that used to live in voice_session._voice_reconnect:
# write on session start, clear on session stop, read at bot startup.
# ---------------------------------------------------------------------------

def voice_reconnect_set(guild_id: int, vc_channel_id: int, sink_spec: tuple) -> None:
    kind = sink_spec[0] if sink_spec else "log_only"
    arg = sink_spec[1] if len(sink_spec) > 1 else None
    with _db() as conn:
        conn.execute(
            "INSERT INTO voice_reconnect (guild_id, vc_channel_id, sink_kind, sink_arg, updated_at) "
            "VALUES (?, ?, ?, ?, strftime('%s','now')) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "vc_channel_id=excluded.vc_channel_id, sink_kind=excluded.sink_kind, "
            "sink_arg=excluded.sink_arg, updated_at=excluded.updated_at",
            (guild_id, vc_channel_id, kind, arg),
        )


def voice_reconnect_clear(guild_id: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM voice_reconnect WHERE guild_id = ?", (guild_id,))


def voice_reconnect_load_all() -> dict[int, tuple]:
    """Return {guild_id: (vc_channel_id, sink_spec)} for every persisted target."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT guild_id, vc_channel_id, sink_kind, sink_arg FROM voice_reconnect"
        ).fetchall()
    out: dict[int, tuple] = {}
    for r in rows:
        kind = r["sink_kind"]
        if kind == "text_channel":
            spec = ("text_channel", r["sink_arg"])
        else:
            spec = (kind,)
        out[r["guild_id"]] = (r["vc_channel_id"], spec)
    return out


# ---------------------------------------------------------------------------
# Wake-word variant dictionary — substring tokens consulted by the voice
# matcher. Three sources cohabit the same table: 'seed' (the original
# hardcoded list, planted on first boot), 'llm' (output of the
# /halbot-admin wake-variants generate command), 'manual' (admin add).
# generate replaces only the 'llm' slice so a bad LLM run can't break
# wake detection — seed + manual stay live.
# ---------------------------------------------------------------------------
_VARIANT_SOURCES = ("seed", "llm", "manual")


def _normalize_variant(token: str) -> str:
    return (token or "").strip().lower()


def wake_variant_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT token, source, created_at FROM wake_variants ORDER BY token"
        ).fetchall()
    return [dict(r) for r in rows]


def wake_variant_tokens() -> list[str]:
    """Just the tokens, lower-cased — what the runtime matcher reads."""
    with _db() as conn:
        rows = conn.execute("SELECT token FROM wake_variants").fetchall()
    return [r["token"] for r in rows]


def wake_variant_replace_llm(tokens: list[str]) -> int:
    """Atomically swap the 'llm' slice. Seed + manual untouched."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        n = _normalize_variant(t)
        if not n or n in seen:
            continue
        seen.add(n)
        cleaned.append(n)
    now = int(time.time())
    with _db() as conn:
        conn.execute("DELETE FROM wake_variants WHERE source = 'llm'")
        conn.executemany(
            "INSERT OR IGNORE INTO wake_variants (token, source, created_at) "
            "VALUES (?, 'llm', ?)",
            [(t, now) for t in cleaned],
        )
    return len(cleaned)


def wake_variant_add(token: str) -> bool:
    n = _normalize_variant(token)
    if not n:
        raise ValueError("token is empty")
    now = int(time.time())
    with _db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO wake_variants (token, source, created_at) "
            "VALUES (?, 'manual', ?)",
            (n, now),
        )
        return cur.rowcount > 0


def wake_variant_remove(token: str) -> bool:
    n = _normalize_variant(token)
    if not n:
        return False
    with _db() as conn:
        cur = conn.execute("DELETE FROM wake_variants WHERE token = ?", (n,))
        return cur.rowcount > 0


def wake_variant_clear() -> int:
    """Drop every row except seed entries. Seed stays so wake never breaks."""
    with _db() as conn:
        cur = conn.execute("DELETE FROM wake_variants WHERE source != 'seed'")
        return cur.rowcount


def emoji_db_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT emoji_id, name, animated, description FROM emojis ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def emoji_db_list_full() -> list[dict]:
    """Same as emoji_db_list but includes image bytes. For dashboard render."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT emoji_id, name, animated, image, description FROM emojis ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def emoji_db_get(emoji_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT emoji_id, name, animated, image, description, description_attempted FROM emojis WHERE emoji_id = ?",
                           (emoji_id,)).fetchone()
        return dict(row) if row else None


def emoji_db_upsert(emoji_id: int, name: str, animated: bool, image: bytes, description: str,
                     description_attempted: bool = False):
    with _db() as conn:
        conn.execute("""
            INSERT INTO emojis (emoji_id, name, animated, image, description, description_attempted)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(emoji_id) DO UPDATE SET name=?, animated=?, image=?, description=?, description_attempted=?
        """, (emoji_id, name, int(animated), image, description, int(description_attempted),
              name, int(animated), image, description, int(description_attempted)))


def emoji_db_prune(valid_ids: set[int]):
    """Remove emojis from DB that are no longer on the server."""
    with _db() as conn:
        if valid_ids:
            placeholders = ",".join("?" * len(valid_ids))
            conn.execute(f"DELETE FROM emojis WHERE emoji_id NOT IN ({placeholders})", list(valid_ids))
        else:
            conn.execute("DELETE FROM emojis")


# ---------------------------------------------------------------------------
# Facts (plan 012 — canonical truths)
# ---------------------------------------------------------------------------

def fact_add(subject: str, claim: str, set_by: str) -> int:
    subject = (subject or "").strip()
    claim = (claim or "").strip()
    if not subject or not claim:
        raise ValueError("Fact needs both a subject and a claim.")
    if len(claim) > FACT_MAX_CHARS:
        raise ValueError(f"Claim too long ({len(claim)} chars). Max is {FACT_MAX_CHARS}.")
    with _db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM facts WHERE deleted_at IS NULL").fetchone()[0]
        if count >= FACT_MAX_TOTAL:
            raise ValueError(f"Too many facts ({count}/{FACT_MAX_TOTAL}). Forget some first.")
        cur = conn.execute(
            "INSERT INTO facts (subject, claim, set_by) VALUES (?, ?, ?)",
            (subject, claim, set_by),
        )
        return cur.lastrowid


def fact_list(subject: str | None = None) -> list[dict]:
    with _db() as conn:
        if subject:
            rows = conn.execute(
                "SELECT id, subject, claim, set_by, created_at FROM facts "
                "WHERE subject = ? COLLATE NOCASE AND deleted_at IS NULL "
                "ORDER BY created_at",
                (subject,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, subject, claim, set_by, created_at FROM facts "
                "WHERE deleted_at IS NULL ORDER BY subject COLLATE NOCASE, created_at"
            ).fetchall()
        return [dict(r) for r in rows]


def fact_remove(fact_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE facts SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (fact_id,),
        )
        return cur.rowcount > 0


def fact_clear(subject: str | None = None) -> int:
    with _db() as conn:
        if subject:
            cur = conn.execute(
                "UPDATE facts SET deleted_at = CURRENT_TIMESTAMP "
                "WHERE subject = ? COLLATE NOCASE AND deleted_at IS NULL",
                (subject,),
            )
        else:
            cur = conn.execute(
                "UPDATE facts SET deleted_at = CURRENT_TIMESTAMP WHERE deleted_at IS NULL"
            )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Triggers (plan 012 — reflex bindings)
# ---------------------------------------------------------------------------

def trigger_add(match_kind: str, match_value: str, action_type: str,
                action_payload: str, set_by: str) -> int:
    match_kind = (match_kind or "").strip()
    if match_kind not in TRIGGER_MATCH_KINDS:
        raise ValueError(f"Unknown match_kind {match_kind!r}. Allowed: {list(TRIGGER_MATCH_KINDS)}")
    match_value = (match_value or "").strip()
    if not match_value:
        raise ValueError("Trigger needs a match value (keyword / phrase).")
    if len(match_value) > TRIGGER_MAX_CHARS:
        raise ValueError(f"Match value too long. Max is {TRIGGER_MAX_CHARS}.")
    action_type = (action_type or "").strip()
    if action_type not in TRIGGER_ACTIONS:
        raise ValueError(f"Unknown trigger action {action_type!r}. Allowed: {list(TRIGGER_ACTIONS)}")
    action_payload = (action_payload or "").strip()
    if not action_payload:
        raise ValueError("Trigger needs an action payload.")
    if len(action_payload) > TRIGGER_MAX_CHARS:
        raise ValueError(f"Payload too long. Max is {TRIGGER_MAX_CHARS}.")
    with _db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM triggers WHERE deleted_at IS NULL").fetchone()[0]
        if count >= TRIGGER_MAX_TOTAL:
            raise ValueError(f"Too many triggers ({count}/{TRIGGER_MAX_TOTAL}). Remove some first.")
        cur = conn.execute(
            "INSERT INTO triggers (match_kind, match_value, action_type, action_payload, set_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (match_kind, match_value, action_type, action_payload, set_by),
        )
        return cur.lastrowid


def trigger_list(match_kind: str | None = None) -> list[dict]:
    with _db() as conn:
        if match_kind:
            rows = conn.execute(
                "SELECT id, match_kind, match_value, action_type, action_payload, "
                "set_by, created_at, fire_count, last_fired_at FROM triggers "
                "WHERE match_kind = ? AND deleted_at IS NULL ORDER BY created_at",
                (match_kind,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, match_kind, match_value, action_type, action_payload, "
                "set_by, created_at, fire_count, last_fired_at FROM triggers "
                "WHERE deleted_at IS NULL ORDER BY match_kind, created_at"
            ).fetchall()
        return [dict(r) for r in rows]


def trigger_remove(trigger_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE triggers SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (trigger_id,),
        )
        return cur.rowcount > 0


def trigger_clear(match_kind: str | None = None) -> int:
    with _db() as conn:
        if match_kind:
            cur = conn.execute(
                "UPDATE triggers SET deleted_at = CURRENT_TIMESTAMP "
                "WHERE match_kind = ? AND deleted_at IS NULL",
                (match_kind,),
            )
        else:
            cur = conn.execute(
                "UPDATE triggers SET deleted_at = CURRENT_TIMESTAMP WHERE deleted_at IS NULL"
            )
        return cur.rowcount


def trigger_mark_fired(trigger_id: int) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE triggers SET fire_count = fire_count + 1, "
            "last_fired_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (trigger_id,),
        )


# ---------------------------------------------------------------------------
# Grudges / Devotions (plan 012 — per-user relational bias)
# ---------------------------------------------------------------------------

def grudge_set(target_name: str, polarity: int, note: str, set_by: str) -> int:
    """Upsert a grudge/devotion. Replaces any existing row for target_name."""
    target_name = (target_name or "").strip()
    if not target_name:
        raise ValueError("Grudge needs a target name.")
    try:
        polarity = int(polarity)
    except (TypeError, ValueError):
        raise ValueError("Polarity must be an integer between "
                         f"{GRUDGE_POLARITY_MIN} and {GRUDGE_POLARITY_MAX}.")
    if not (GRUDGE_POLARITY_MIN <= polarity <= GRUDGE_POLARITY_MAX):
        raise ValueError(f"Polarity must be between {GRUDGE_POLARITY_MIN} and {GRUDGE_POLARITY_MAX}.")
    note = (note or "").strip()
    if len(note) > GRUDGE_NOTE_MAX_CHARS:
        raise ValueError(f"Note too long. Max is {GRUDGE_NOTE_MAX_CHARS}.")
    with _db() as conn:
        existing = conn.execute(
            "SELECT id FROM grudges WHERE target_name = ? COLLATE NOCASE AND deleted_at IS NULL",
            (target_name,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE grudges SET polarity = ?, note = ?, set_by = ?, "
                "created_at = CURRENT_TIMESTAMP WHERE id = ?",
                (polarity, note, set_by, existing["id"]),
            )
            return existing["id"]
        count = conn.execute("SELECT COUNT(*) FROM grudges WHERE deleted_at IS NULL").fetchone()[0]
        if count >= GRUDGE_MAX_TOTAL:
            raise ValueError(f"Too many grudges ({count}/{GRUDGE_MAX_TOTAL}). Forgive someone first.")
        cur = conn.execute(
            "INSERT INTO grudges (target_name, polarity, note, set_by) VALUES (?, ?, ?, ?)",
            (target_name, polarity, note, set_by),
        )
        return cur.lastrowid


def grudge_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, target_name, polarity, note, set_by, created_at "
            "FROM grudges WHERE deleted_at IS NULL ORDER BY polarity DESC, target_name"
        ).fetchall()
        return [dict(r) for r in rows]


def grudge_remove(grudge_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE grudges SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (grudge_id,),
        )
        return cur.rowcount > 0


def grudge_clear() -> int:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE grudges SET deleted_at = CURRENT_TIMESTAMP WHERE deleted_at IS NULL"
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Admin recovery helpers (plan 012 — panic button / undelete)
# ---------------------------------------------------------------------------

# Per-kind config: (table, display_cols for the admin list views)
_ADMIN_KINDS: dict[str, dict] = {
    "sounds":   {"table": "saved_sounds",
                 "cols": "id, name, saved_by, created_at, deleted_at, length(audio) as size_bytes"},
    "personas": {"table": "personas",
                 "cols": "id, directive, set_by, created_at, deleted_at"},
    "facts":    {"table": "facts",
                 "cols": "id, subject, claim, set_by, created_at, deleted_at"},
    "triggers": {"table": "triggers",
                 "cols": "id, match_kind, match_value, action_type, action_payload, set_by, created_at, deleted_at"},
    "grudges":  {"table": "grudges",
                 "cols": "id, target_name, polarity, note, set_by, created_at, deleted_at"},
}


def admin_kinds() -> list[str]:
    return list(_ADMIN_KINDS.keys())


def admin_list_deleted(kind: str, limit: int = 50) -> list[dict]:
    """List soft-deleted rows of one kind, most-recently-deleted first."""
    cfg = _ADMIN_KINDS.get(kind)
    if not cfg:
        raise ValueError(f"Unknown kind {kind!r}. Allowed: {admin_kinds()}")
    with _db() as conn:
        rows = conn.execute(
            f"SELECT {cfg['cols']} FROM {cfg['table']} "
            f"WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]


def admin_undelete(kind: str, row_id: int) -> bool:
    """Restore a soft-deleted row by id."""
    cfg = _ADMIN_KINDS.get(kind)
    if not cfg:
        raise ValueError(f"Unknown kind {kind!r}. Allowed: {admin_kinds()}")
    with _db() as conn:
        cur = conn.execute(
            f"UPDATE {cfg['table']} SET deleted_at = NULL "
            f"WHERE id = ? AND deleted_at IS NOT NULL",
            (int(row_id),),
        )
        return cur.rowcount > 0


def admin_undelete_all(kind: str) -> int:
    """Restore EVERY soft-deleted row of one kind."""
    cfg = _ADMIN_KINDS.get(kind)
    if not cfg:
        raise ValueError(f"Unknown kind {kind!r}. Allowed: {admin_kinds()}")
    with _db() as conn:
        cur = conn.execute(
            f"UPDATE {cfg['table']} SET deleted_at = NULL WHERE deleted_at IS NOT NULL"
        )
        return cur.rowcount


def admin_panic_clear(kinds: list[str] | None = None) -> dict[str, int]:
    """Soft-clear all active rows for the listed hook kinds (default: all hook
    kinds EXCEPT sounds — sounds are too expensive to re-upload).

    Returns {kind: rows_soft_deleted}. Everything is recoverable via
    admin_undelete_all.
    """
    if kinds is None:
        kinds = ["personas", "facts", "triggers", "grudges"]
    out: dict[str, int] = {}
    with _db() as conn:
        for k in kinds:
            cfg = _ADMIN_KINDS.get(k)
            if not cfg:
                continue
            cur = conn.execute(
                f"UPDATE {cfg['table']} SET deleted_at = CURRENT_TIMESTAMP "
                f"WHERE deleted_at IS NULL"
            )
            out[k] = cur.rowcount
    return out


def admin_hard_purge(kind: str, older_than_days: int | None = None) -> int:
    """Permanently drop tombstoned rows. Irreversible. Use sparingly.

    If older_than_days is given, only purge tombstones older than N days.
    Otherwise purge all tombstones of that kind.
    """
    cfg = _ADMIN_KINDS.get(kind)
    if not cfg:
        raise ValueError(f"Unknown kind {kind!r}. Allowed: {admin_kinds()}")
    with _db() as conn:
        if older_than_days is not None:
            cur = conn.execute(
                f"DELETE FROM {cfg['table']} "
                f"WHERE deleted_at IS NOT NULL "
                f"AND deleted_at < datetime('now', ?)",
                (f"-{int(older_than_days)} days",),
            )
        else:
            cur = conn.execute(
                f"DELETE FROM {cfg['table']} WHERE deleted_at IS NOT NULL"
            )
        return cur.rowcount


def admin_stats() -> dict[str, dict[str, int]]:
    """Return {kind: {live, deleted}} counts for each admin kind."""
    out: dict[str, dict[str, int]] = {}
    with _db() as conn:
        for kind, cfg in _ADMIN_KINDS.items():
            live = conn.execute(
                f"SELECT COUNT(*) FROM {cfg['table']} WHERE deleted_at IS NULL"
            ).fetchone()[0]
            dead = conn.execute(
                f"SELECT COUNT(*) FROM {cfg['table']} WHERE deleted_at IS NOT NULL"
            ).fetchone()[0]
            out[kind] = {"live": live, "deleted": dead}
    return out
