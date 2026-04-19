import os
import sqlite3

from . import paths

DB_PATH = str(paths.data_dir() / "sounds.db")
METADATA_MAX_BYTES = 2048
PERSONA_MAX_CHARS = 200
PERSONA_MAX_TOTAL = 10

try:
    VOICE_HISTORY_TURNS = max(0, int(os.getenv("VOICE_HISTORY_TURNS", "10")))
except (ValueError, TypeError):
    VOICE_HISTORY_TURNS = 10


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y", "t")


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


def db_save(name: str, audio: bytes, emoji: str | None, metadata: str | None, saved_by: str,
            parent_id: int | None = None, effects: str = "") -> int:
    metadata = metadata or ""
    if len(metadata.encode()) > METADATA_MAX_BYTES:
        raise ValueError(f"Metadata exceeds {METADATA_MAX_BYTES} bytes")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO saved_sounds (name, audio, emoji, metadata, saved_by, parent_id, effects) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, audio, emoji, metadata, saved_by, parent_id, effects),
        )
        return cur.lastrowid


def db_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, metadata, saved_by, created_at, length(audio) as size_bytes, parent_id, effects FROM saved_sounds ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def db_get(name: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, audio, emoji, metadata, saved_by, created_at, parent_id, effects FROM saved_sounds WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None


def db_get_by_id(sound_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, audio, emoji, metadata, saved_by, created_at, parent_id, effects FROM saved_sounds WHERE id = ?",
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
        cur = conn.execute(f"UPDATE saved_sounds SET {', '.join(fields)} WHERE name = ?", values)
        return cur.rowcount > 0


def db_delete(name: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM saved_sounds WHERE name = ?", (name,))
        return cur.rowcount > 0


def persona_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, directive, set_by, created_at FROM personas ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def persona_add(directive: str, set_by: str) -> int:
    if len(directive) > PERSONA_MAX_CHARS:
        raise ValueError(f"Directive too long ({len(directive)} chars). Max is {PERSONA_MAX_CHARS}.")
    current = persona_list()
    if len(current) >= PERSONA_MAX_TOTAL:
        raise ValueError(f"Too many directives ({len(current)}). Max is {PERSONA_MAX_TOTAL}. Ask someone to clear some first.")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO personas (directive, set_by) VALUES (?, ?)",
            (directive, set_by),
        )
        return cur.lastrowid


def persona_update(persona_id: int, directive: str) -> bool:
    if len(directive) > PERSONA_MAX_CHARS:
        raise ValueError(f"Directive too long ({len(directive)} chars). Max is {PERSONA_MAX_CHARS}.")
    with _db() as conn:
        cur = conn.execute(
            "UPDATE personas SET directive = ? WHERE id = ?",
            (directive, persona_id),
        )
        return cur.rowcount > 0


def persona_remove(persona_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
        return cur.rowcount > 0


def persona_clear() -> int:
    with _db() as conn:
        cur = conn.execute("DELETE FROM personas")
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


def emoji_db_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT emoji_id, name, animated, description FROM emojis ORDER BY name").fetchall()
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
