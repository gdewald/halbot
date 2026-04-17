import argparse
import asyncio
import base64
import io
import json
import logging
import os
import sqlite3

import discord
import requests
from dotenv import load_dotenv
from pydub import AudioSegment
from tinytag import TinyTag

# Voice module — optional, only needed when voice features are used
try:
    from voice import VoiceListener, HalbotVoiceRecvClient, VOICE_RECV_AVAILABLE, load_whisper, unload_whisper
except ImportError:
    VoiceListener = None
    HalbotVoiceRecvClient = None
    VOICE_RECV_AVAILABLE = False
    load_whisper = None
    unload_whisper = None

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
# Model to target in LM Studio. Source-controlled so JIT re-loads after idle unload
# use the same model every time.
LMSTUDIO_MODEL = "google/gemma-4-e4b"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y", "t")


# When true, pass chat_template_kwargs={enable_thinking: false} on every
# LLM request so "thinking" models (Qwen3 / Gemma thinking / DeepSeek-R1) skip
# their chain-of-thought.  Thinking often emits hundreds of reasoning tokens
# before the answer and dominates voice pipeline latency.
LLM_DISABLE_THINKING = _env_bool("LLM_DISABLE_THINKING", True)

# When true, voice wake-word detection and intent parsing are merged into a
# single LLM call, saving one round-trip per utterance.  When false, the two
# are issued sequentially (wake classifier first, then intent parser only if
# the wake word was detected).
VOICE_LLM_COMBINE_CALLS = _env_bool("VOICE_LLM_COMBINE_CALLS", True)


def _apply_thinking_flag(body: dict) -> dict:
    """Stamp chat_template_kwargs.enable_thinking=False on a request body
    when LLM_DISABLE_THINKING is set.  No-op otherwise."""
    if LLM_DISABLE_THINKING:
        tmpl = body.setdefault("chat_template_kwargs", {})
        tmpl["enable_thinking"] = False
    return body


def _lmstudio_base() -> str:
    """Strip the OpenAI path suffix to get the LM Studio server root."""
    for marker in ("/v1/", "/api/"):
        idx = LMSTUDIO_URL.find(marker)
        if idx != -1:
            return LMSTUDIO_URL[:idx]
    return LMSTUDIO_URL.rstrip("/")


def ensure_model_loaded(model: str = LMSTUDIO_MODEL, timeout: int = 180) -> bool:
    """Make sure `model` is loaded in LM Studio, triggering a JIT load if not.

    Returns True if the model ends up loaded (or was already), False otherwise.
    LM Studio auto-unloads models after an idle TTL; this re-loads on demand.
    """
    base = _lmstudio_base()
    # Check current state via LM Studio's native REST API
    try:
        resp = requests.get(f"{base}/api/v0/models", timeout=5)
        resp.raise_for_status()
        entries = resp.json().get("data", []) or []
        match = next(
            (m for m in entries if model in (m.get("id"), m.get("model_key"))),
            None,
        )
        if match and match.get("state") == "loaded":
            return True
        state = match.get("state") if match else "unknown"
        log.info("Model %r not loaded (state=%s) — triggering JIT load", model, state)
    except requests.RequestException as e:
        log.warning("Could not query LM Studio model state: %s — will try a direct load", e)

    # Trigger JIT load with a minimal chat completion carrying the target model.
    # LM Studio with JIT enabled will load the requested model before responding.
    try:
        resp = requests.post(
            LMSTUDIO_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        log.info("JIT load completed for %r", model)
        return True
    except requests.RequestException as e:
        log.error("Failed to load model %r: %s", model, e)
        return False

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log = logging.getLogger("halbot")


def configure_logging(log_path=None) -> None:
    """Install stdout + optional rotating file handler on the root logger.

    Idempotent. Clears existing root handlers first so repeated calls (e.g.
    from bot.py main and halbot_tray.py) don't stack up duplicates.
    """
    from pathlib import Path
    from logging.handlers import RotatingFileHandler

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_path is not None:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(p, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

# ---------------------------------------------------------------------------
# SQLite database for saved sounds
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds.db")
METADATA_MAX_BYTES = 2048


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
    if len(audio) > SOUNDBOARD_MAX_BYTES:
        raise ValueError(f"Audio too large ({len(audio)} bytes). Max is {SOUNDBOARD_MAX_BYTES} bytes.")
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


# ---------------------------------------------------------------------------
# Persona / behavior directives
# ---------------------------------------------------------------------------
PERSONA_MAX_CHARS = 200
PERSONA_MAX_TOTAL = 10


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


# ---------------------------------------------------------------------------
# Emoji tracking
# ---------------------------------------------------------------------------
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


def describe_emoji_image(image_bytes: bytes, name: str) -> str:
    """Send an emoji image to LM Studio vision and get a short description."""
    b64 = base64.b64encode(image_bytes).decode()
    # Discord emojis are PNG or GIF
    mime = "image/gif" if image_bytes[:4] == b"GIF8" else "image/png"
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text",
                 "text": f"This is a Discord custom emoji called '{name}'. "
                         "Describe what it depicts in one short sentence (under 100 characters). "
                         "Just the description, nothing else."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        "temperature": 0.3,
        "max_tokens": 60,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    # Intentionally skip _apply_thinking_flag — this hits a vision backend
    # which may reject unknown chat_template_kwargs.
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Failed to describe emoji %s: %s", name, e)
        return ""


async def sync_emojis(guild: discord.Guild):
    """Sync server emojis to the DB, generating descriptions for new/changed ones."""
    server_ids = set()
    synced, skipped = 0, 0
    for emoji in guild.emojis:
        server_ids.add(emoji.id)
        existing = emoji_db_get(emoji.id)
        # Skip if emoji hasn't changed and we already attempted a description
        if existing and existing["name"] == emoji.name and existing.get("description_attempted"):
            skipped += 1
            continue
        try:
            image = await emoji.read()
            description = existing["description"] if existing else ""
            attempted = existing.get("description_attempted", 0) if existing else False
            if not description and not attempted:
                description = describe_emoji_image(image, emoji.name)
            emoji_db_upsert(emoji.id, emoji.name, emoji.animated, image, description,
                            description_attempted=True)
            synced += 1
            log.info("Synced emoji %s: %s", emoji.name, description or "(no description)")
        except Exception as e:
            log.error("Failed to sync emoji %s: %s", emoji.name, e)
    emoji_db_prune(server_ids)
    log.info("Emoji sync complete: %d synced, %d unchanged, %d pruned",
             synced, skipped, len(server_ids) - synced - skipped)


# ---------------------------------------------------------------------------
# Audio validation
# ---------------------------------------------------------------------------
SOUNDBOARD_MAX_BYTES = 512 * 1024  # 512 KB
SOUNDBOARD_MAX_DURATION = 5.2  # seconds
ALLOWED_CONTENT_TYPES = {"audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp3"}
ALLOWED_EXTENSIONS = {".mp3", ".ogg", ".wav"}


def validate_audio(data: bytes, filename: str) -> tuple[bool, str, float | None]:
    """Validate audio data for soundboard compatibility.

    Returns (ok, message, duration_seconds).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported format `{ext}`. Must be MP3, OGG, or WAV.", None

    if len(data) > SOUNDBOARD_MAX_BYTES:
        size_kb = round(len(data) / 1024, 1)
        return False, f"File too large ({size_kb}KB). Max is 512KB.", None

    try:
        tag = TinyTag.get(file_obj=io.BytesIO(data))
        duration = tag.duration or 0
    except Exception:
        return False, "Couldn't read audio metadata. Is the file valid?", None

    if duration > SOUNDBOARD_MAX_DURATION:
        return False, f"Too long ({duration:.1f}s). Max is {SOUNDBOARD_MAX_DURATION}s.", duration

    return True, "OK", duration


# ---------------------------------------------------------------------------
# Audio effects engine
# ---------------------------------------------------------------------------
SUPPORTED_EFFECTS = {"echo", "reverb", "pitch"}


def detect_audio_format(data: bytes) -> str:
    """Sniff audio format from file header bytes."""
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    return "mp3"  # fallback


def apply_effect(audio_bytes: bytes, fmt: str, effect_type: str, params: dict) -> bytes:
    """Apply a single audio effect. Returns processed audio bytes in the same format."""
    sound = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)

    if effect_type == "echo":
        delay_ms = int(params.get("delay", 300))
        decay_db = float(params.get("decay", 6))
        repeats = int(params.get("repeats", 3))
        result = sound
        for i in range(1, repeats + 1):
            delayed = AudioSegment.silent(duration=delay_ms * i) + (sound - decay_db * i)
            result = result.overlay(delayed)

    elif effect_type == "reverb":
        room_size = max(0.0, min(1.0, float(params.get("room_size", 0.5))))
        num_taps = int(8 + room_size * 20)
        result = sound
        for i in range(1, num_taps + 1):
            tap_delay = int(i * 15 * (1 + room_size))
            tap_decay = 3 * i
            if tap_decay > 40:
                break
            delayed = AudioSegment.silent(duration=tap_delay) + (sound - tap_decay)
            result = result.overlay(delayed)

    elif effect_type == "pitch":
        semitones = float(params.get("semitones", 0))
        rate_change = 2 ** (semitones / 12.0)
        new_rate = int(sound.frame_rate * rate_change)
        pitched = sound._spawn(sound.raw_data, overrides={"frame_rate": new_rate})
        result = pitched.set_frame_rate(sound.frame_rate)

    else:
        raise ValueError(f"Unknown effect: {effect_type}")

    # Truncate to soundboard max duration
    max_ms = int(SOUNDBOARD_MAX_DURATION * 1000)
    if len(result) > max_ms:
        result = result[:max_ms]

    buf = io.BytesIO()
    export_fmt = "mp3" if fmt == "mp3" else fmt
    result.export(buf, format=export_fmt)
    output = buf.getvalue()

    # If output is too large, try re-exporting at lower bitrate
    if len(output) > SOUNDBOARD_MAX_BYTES and fmt == "mp3":
        buf = io.BytesIO()
        result.export(buf, format="mp3", bitrate="128k")
        output = buf.getvalue()

    return output


def apply_effects_chain(original_audio: bytes, effects: list[dict]) -> bytes:
    """Apply a chain of effects sequentially to audio data."""
    fmt = detect_audio_format(original_audio)
    data = original_audio
    for eff in effects:
        data = apply_effect(data, fmt, eff["type"], eff["params"])
    return data


SYSTEM_PROMPT = """\
You are a Discord soundboard manager. You manage both the live soundboard and a local library of saved sounds.

LIVE SOUNDBOARD (currently on the server):
{sound_details}

SAVED LIBRARY (stored locally, can be restored to the soundboard):
{saved_details}

CUSTOM EMOJIS available on this server:
{custom_emojis}

ACTIVE BEHAVIOR DIRECTIVES (follow these when composing any user-facing message):
{persona_directives}

Today's date is {today}.

Based on the user's request, return a JSON response. If the request involves a single action, return a single JSON object. If it involves multiple steps (e.g. "back up and clear"), return a JSON array of action objects — they will be executed in order.

Available actions:

OPTIONAL "message" FIELD: Any action can include an optional "message" field with a short, flavored intro or commentary (1 sentence max). When ACTIVE BEHAVIOR DIRECTIVES are set, you SHOULD include a "message" that reflects the persona. The bot will display your message above the factual data. Example: {{"action": "list", "names": [...], "message": "Arr, here be yer sounds, matey!"}}. Without directives, omit this field.

--- Live soundboard actions ---

{{"action": "list", "names": [...]}}
Return the exact sound names from the LIVE SOUNDBOARD that match what the user is asking about. If they want all sounds, include all names. If they want to filter (by user, date, keyword, or any other criteria), include only the matching names.

{{"action": "remove", "names": [...]}}
Delete sounds from the live soundboard. Include the exact matching name(s).

{{"action": "edit", "name": "<exact sound name>", "emoji": "<unicode emoji>", "new_name": "<new name or null>"}}
Modify a live soundboard sound. Include the exact current name, and whichever fields they want to change. "emoji" can be a unicode emoji (e.g. "🎵") or a custom server emoji in the format "<:name:id>" from the CUSTOM EMOJIS list. "new_name" is for renaming. Omit fields the user didn't ask to change.

{{"action": "clear"}}
Remove ALL sounds from the live soundboard without exception.

--- Saved library actions ---

{{"action": "upload", "name": "<name for the sound>", "filename": "<exact filename>", "metadata": "<optional text>"}}
Store an audio file in the library. Works with files attached to the current message OR files from recent chat history (audio attachments in history are shown as [attached: filename, size]).
"filename" must be the EXACT filename. Use the filename (without extension) as the default name unless the user specifies one.
RULE: If the user wants to store an attached audio file (current or from history), use "upload". The "save" action is ONLY for copying sounds already on the live soundboard.

{{"action": "save", "names": [...], "metadata": "<optional text>"}}
Copy sounds from the LIVE SOUNDBOARD into the local library. "names" must match LIVE SOUNDBOARD entries. ONLY use this when the user wants to back up sounds already on the server, NOT when they attach a file.

{{"action": "saved_list", "names": [...]}}
List sounds in the saved library. Include all saved names, or filter to matching ones. If the user wants all saved sounds, include all names.

{{"action": "saved_update", "name": "<exact saved name>", "new_name": "<new name or null>", "emoji": "<emoji or null>", "metadata": "<new metadata or null>"}}
Update a saved sound's name, emoji, or metadata. Omit fields the user didn't ask to change.

{{"action": "saved_delete", "names": [...]}}
Delete sounds from the saved library. Include the exact matching name(s).

{{"action": "restore", "names": [...]}}
Upload saved sounds from the library back to the live soundboard. "names" must match SAVED LIBRARY entries.

--- Audio effect actions ---

{{"action": "effect_ask", "name": "<exact saved name>", "effect": "<echo|reverb|pitch>", "message": "<your question to the user about effect parameters>"}}
The user wants to apply an effect but hasn't specified parameters. Ask them to choose a preset or give custom values. Use a friendly message listing the presets.
Presets for echo: light (delay=150, decay=8, repeats=2), medium (delay=300, decay=6, repeats=3), heavy (delay=500, decay=4, repeats=5).
Presets for reverb: small room (room_size=0.2), medium room (room_size=0.5), large hall (room_size=0.9).
Presets for pitch: up a little (+2 semitones), up a lot (+5), down a little (-2), down a lot (-5), chipmunk (+8), deep (-8).
IMPORTANT: If the user already specified enough info (e.g. "chipmunk pitch" or "heavy echo"), skip this step and go straight to effect_apply.

{{"action": "effect_apply", "name": "<exact saved name>", "effect": "<echo|reverb|pitch>", "params": {{...}}, "save_as": "<name for modified clip>"}}
Apply an audio effect to a saved sound. The user has provided or confirmed the parameters.
"params" depends on the effect type:
  echo: {{"delay": <ms>, "decay": <dB reduction per repeat>, "repeats": <int>}}
  reverb: {{"room_size": <0.0 to 1.0>}}
  pitch: {{"semitones": <number, positive=higher, negative=lower>}}
"save_as" defaults to "<original_name>-<effect>" unless the user specifies a name.
If the source clip already has effects applied (shown in the SAVED LIBRARY listing), combine the effect names in "save_as" (e.g. "airhorn-echo-pitch").

--- Emoji actions ---

{{"action": "emoji_list", "names": [...]}}
List custom emojis available on this server. Include all emoji names from the CUSTOM EMOJIS list, or filter to matching ones. Use the emoji name (not the full format string).

--- Persona / behavior actions ---

{{"action": "persona_set", "directive": "<short behavior instruction>", "message": "<confirmation to the user>"}}
The user wants to add a NEW behavior or personality trait (e.g. "from now on respond like a pirate", "be more sarcastic", "speak in haiku").
Write the "directive" as a short, clear instruction to yourself (max 200 chars). Distill what the user wants into an actionable behavior rule. Do NOT just copy their message — rephrase it as a directive.
"message" is a fun confirmation to show the user that you've adopted the behavior.
Only use this action when the user is clearly asking you to change your personality or communication style. Normal soundboard requests should NOT trigger this.

{{"action": "persona_update", "id": <directive id>, "directive": "<revised behavior instruction>", "message": "<confirmation>"}}
The user wants to MODIFY an existing directive (e.g. "be a little less grumpy", "tone it down", "make the pirate thing more subtle").
"id" must match a directive from the ACTIVE BEHAVIOR DIRECTIVES list. Write a new "directive" that incorporates the user's adjustment.
Use this instead of persona_set when the user is clearly refining an existing behavior rather than adding a new one.

{{"action": "persona_remove", "id": <directive id>, "message": "<confirmation>"}}
The user wants to REMOVE a specific behavior directive (e.g. "stop being a pirate", "drop the grumpy act").
"id" must match a directive from the ACTIVE BEHAVIOR DIRECTIVES list.

{{"action": "persona_list"}}
The user wants to see what behavior directives are currently active.

--- Voice channel actions ---

{{"action": "voice_join", "channel": "<voice channel name>"}}
Join a voice channel. Once connected the bot listens for the wake word "Halbot" followed by a command, and plays sounds from the library or live soundboard.
"channel" must match one of the VOICE CHANNELS listed below (fuzzy matching is OK).

VOICE CHANNELS on this server:
{voice_channels}

VOICE STATUS: {voice_status}

{{"action": "voice_leave"}}
Leave the current voice channel and stop listening.

{{"action": "voice_play", "name": "<exact sound name>"}}
Play a sound from the saved library (or live soundboard) in the voice channel. Only works when already connected to a voice channel.

--- Fallback ---

{{"action": "unknown", "message": "<your response>"}}
The user's request doesn't match any supported action. Write a short, friendly response explaining what you can do and why their request didn't match.

SOUNDBOARD LIMITS: Max file size 512KB, max duration 5.2 seconds, formats: MP3/OGG/WAV.

{attachments}

IMPORTANT: Names must be EXACT matches from the appropriate list (live or saved). Use your judgement to match typos, abbreviations, or descriptions to the correct names.

IMPORTANT: Messages marked [BOT REPLY] in the conversation history are results I already sent to the user. Do NOT copy or repeat them. Always return a JSON action so the bot can execute it.

Reply with ONLY the JSON (single object or array of objects). No explanation.\
"""

# TODO: Add sound lookup/search support (e.g. Freesound API)
# TODO: Add "add" action to search and add new sounds


def format_sound_details(sounds) -> str:
    """Build a detailed listing of live sounds for the LM Studio prompt."""
    if not sounds:
        return "(none)"
    lines = []
    for s in sounds:
        user_name = str(s.user) if s.user else "unknown"
        date_str = s.created_at.strftime("%Y-%m-%d") if s.created_at else "unknown"
        lines.append(f"- \"{s.name}\" (added by {user_name} on {date_str})")
    return "\n".join(lines)


def format_saved_details(saved: list[dict]) -> str:
    """Build a detailed listing of saved sounds for the LM Studio prompt."""
    if not saved:
        return "(none)"
    lines = []
    for s in saved:
        parts = [f"\"{s['name']}\""]
        if s.get("emoji"):
            parts.append(f"emoji: {s['emoji']}")
        if s.get("saved_by"):
            parts.append(f"saved by {s['saved_by']}")
        parts.append(f"on {s['created_at']}")
        size_kb = round(s.get("size_bytes", 0) / 1024, 1)
        parts.append(f"{size_kb}KB")
        if s.get("effects"):
            try:
                fx = json.loads(s["effects"])
                parts.append(f"effects: {'+'.join(e['type'] for e in fx)}")
            except (json.JSONDecodeError, KeyError):
                pass
        if s.get("parent_id"):
            parts.append(f"derived from id:{s['parent_id']}")
        if s.get("metadata"):
            parts.append(f"notes: {s['metadata']}")
        lines.append(f"- {' | '.join(parts)}")
    return "\n".join(lines)


CHANNEL_HISTORY_LIMIT = 50


def parse_intent(user_text: str, sounds, saved: list[dict], channel_history: list[dict],
                  attachment_info: list[dict] | None = None,
                  guild: discord.Guild | None = None) -> list[dict]:
    """Send user text + context to LM Studio, return a list of actions to execute."""
    from datetime import date

    emoji_records = emoji_db_list()
    if emoji_records:
        emoji_lines = []
        for e in emoji_records:
            prefix = "a" if e["animated"] else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            emoji_lines.append(f"- {e['name']} → <{prefix}:{e['name']}:{e['emoji_id']}>{desc}")
        emojis_str = "\n".join(emoji_lines)
    else:
        emojis_str = "(none)"

    personas = persona_list()
    if personas:
        persona_lines = [f"- [id:{p['id']}] {p['directive']} (set by {p['set_by']})" for p in personas]
        persona_str = "\n".join(persona_lines)
    else:
        persona_str = "(none — use your default personality)"

    if attachment_info:
        att_lines = ["ATTACHMENTS on this message:"]
        for att in attachment_info:
            parts = [f"\"{att['filename']}\"", f"{att['size_kb']}KB"]
            if att.get("duration") is not None:
                parts.append(f"{att['duration']:.1f}s")
            if att.get("valid"):
                parts.append("valid for soundboard")
            else:
                parts.append(f"INVALID: {att.get('reason', 'unknown')}")
            att_lines.append(f"- {' | '.join(parts)}")
        attachments_str = "\n".join(att_lines)
    else:
        attachments_str = "No attachments on this message."

    # Voice channel context
    if guild:
        vc_names = [vc.name for vc in guild.voice_channels]
        voice_channels_str = "\n".join(f"- {n}" for n in vc_names) if vc_names else "(none)"
        listener = voice_listeners.get(guild.id)
        if listener and listener.vc.is_connected():
            voice_status_str = f'Connected to "{listener.vc.channel.name}". Listening for wake word "Halbot".'
        else:
            voice_status_str = "Not connected to any voice channel."
    else:
        voice_channels_str = "(unknown)"
        voice_status_str = "Not connected to any voice channel."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            sound_details=format_sound_details(sounds),
            saved_details=format_saved_details(saved),
            custom_emojis=emojis_str,
            persona_directives=persona_str,
            today=date.today().isoformat(),
            attachments=attachments_str,
            voice_channels=voice_channels_str,
            voice_status=voice_status_str,
        )},
        *channel_history,
        {"role": "user", "content": user_text},
    ]

    body = {
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1536,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    _apply_thinking_flag(body)

    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        if resp.status_code >= 400:
            log.warning("LM Studio %s response: %s", resp.status_code, resp.text[:500])
            # LM Studio may have idle-unloaded the model. Try reload+retry once.
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    log.info("Retrying chat completion after model load")
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=60)
                    if resp.status_code >= 400:
                        log.warning("LM Studio retry %s response: %s",
                                    resp.status_code, resp.text[:500])
        resp.raise_for_status()
        raw_json = resp.json()
        log.debug("LM Studio raw response: %s", json.dumps(raw_json, indent=2))
        choice = raw_json["choices"][0]
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        log.info("LM Studio finish_reason=%s, usage=%s", finish_reason, usage)
        content = (choice["message"].get("content") or "").strip()
        log.info("LM Studio content: %r", content)
        if not content:
            log.error("LM Studio returned empty content (finish_reason=%s)", finish_reason)
            return [{"action": "error", "message": "LM Studio returned an empty response — the prompt may be too long for the model's context window."}]
        # Strip markdown code fences if the model wraps its response
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            log.debug("After stripping code fences: %s", content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Model responded with prose instead of JSON — use it as a friendly message
            log.warning("LM Studio returned non-JSON, treating as unknown: %s", content[:200])
            return [{"action": "unknown", "message": content}]
        log.info("Parsed actions: %s", json.dumps(parsed, indent=2))
        # Normalize to a list of actions
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return [{"action": "unknown", "message": str(parsed)}]
    except requests.ConnectionError:
        log.error("Could not connect to LM Studio at %s", LMSTUDIO_URL)
        return [{"action": "error", "message": "I'm having trouble thinking right now — is LM Studio running?"}]
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Failed to parse intent: %s", e)
        return [{"action": "unknown"}]


# ---------------------------------------------------------------------------
# Voice state
# ---------------------------------------------------------------------------
# Active voice listeners per guild (guild_id → VoiceListener)
voice_listeners: dict[int, VoiceListener] = {} if VoiceListener else {}

# Remembered voice sessions for reconnect after restart.
# Maps guild_id → (voice_channel_id, text_channel_id).
# Populated on clean shutdown, consumed on on_ready.
_voice_reconnect: dict[int, tuple[int, int]] = {}

# Lightweight LLM prompt for voice commands (pick a sound to play)
VOICE_COMMAND_PROMPT = """\
You are a Discord soundboard bot. A user in the voice channel said the wake word \
"Halbot" followed by a command. Pick the best sound to play.

SAVED LIBRARY:
{saved_details}

LIVE SOUNDBOARD:
{sound_details}

{persona_directives_block}

Return JSON:
- To play a sound: {{"action": "voice_play", "name": "<exact sound name>"}}
- If no match or the request is unclear: {{"action": "unknown", "message": "<brief response>"}}

Match creatively — "something scary" → pick a scary-sounding name, \
"play airhorn" → exact match. Names must be EXACT from the lists above.

Reply with ONLY the JSON. No explanation.\
"""


RESPONSE_CUSTOMIZATION_PROMPT = """\
You are Halbot.  Rewrite the plain text message below so it sounds like
something you would say to the user in Discord, shaped by the active
persona directives.

Rules:
- 1 to 2 sentences.  Never more.
- Preserve the original meaning — do not invent new facts or change the
  user-facing outcome.  If the original says something went wrong, yours
  must also convey that.
- Plain text only — no markdown, no JSON, no code blocks, no emoji
  (the caller adds any emoji).
- Do NOT quote the original verbatim; rewrite it in your voice.
{persona_directives_block}
"""


def customize_response(raw_text: str, *, context: str = "") -> str:
    """Rewrite a plain-text response via LM Studio so it matches the bot's
    active persona directives.

    Intended for strings the bot would otherwise send to the channel
    verbatim (error messages, canned fallbacks, etc.) — anywhere we want
    the LLM to flavor the reply.  Falls back to the raw text on any
    failure so a broken LLM never prevents the user from seeing *something*.
    """
    if not raw_text:
        return raw_text
    personas = persona_list()
    if personas:
        pd_block = "\nACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(
            f"- {p['directive']}" for p in personas
        )
    else:
        pd_block = ""
    system = RESPONSE_CUSTOMIZATION_PROMPT.format(persona_directives_block=pd_block)
    user_msg = f"Original: {raw_text}"
    if context:
        user_msg += f"\nContext: {context}"
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.9,
        "max_tokens": 400,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    _apply_thinking_flag(body)
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=15)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        # Handle <think>…</think> wrappers
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        # Fall back to reasoning_content tail if content got truncated
        if not content:
            reasoning = (message.get("reasoning_content") or "").strip()
            if reasoning:
                content = reasoning.splitlines()[-1].strip()
        # Strip surrounding quotes the model sometimes adds
        if len(content) >= 2 and content[0] == content[-1] in ('"', "'"):
            content = content[1:-1].strip()
        if not content:
            log.warning("[customize] empty content; returning raw text")
            return raw_text
        log.info("[customize] %r → %r", raw_text[:80], content[:120])
        return content
    except Exception as e:
        log.warning("[customize] failed (%s); returning raw text", e)
        return raw_text


async def customize_response_async(raw_text: str, *, context: str = "") -> str:
    """Async wrapper around customize_response that runs the blocking HTTP
    call in a worker thread so it doesn't block the event loop."""
    return await asyncio.to_thread(customize_response, raw_text, context=context)


WAKE_WORD_PROMPT = """\
You are a wake-word classifier for a Discord bot named "Halbot".

Given a speech transcription (produced by an imperfect STT engine), decide
whether the speaker is addressing Halbot and, if so, extract the command.

Speech-to-text often mis-hears "Halbot" as phonetically similar words.
Treat ALL of these (and any similar mishearing) as a wake word:
  Halbot, Hal Bot, Albot, Owlbot, Palbot, Walbot, Halbert, Hellboy,
  Hellbot, Howlbot, Holbot, Hal-Bot, Hal Bought, How Bout, Al Bought, etc.

The wake word is usually the first word of the utterance but may appear
later ("play big yoshi, halbot"). The COMMAND is everything the speaker
said to the bot MINUS the wake word itself, with leading punctuation
stripped. If the utterance has no command after the wake word (just the
name alone), return command = "".

Reply with ONLY this JSON object, no prose, no markdown:
  {"wake": <true|false>, "command": "<extracted command or empty string>"}

Err on the side of wake=false when the utterance is clearly not directed
at the bot (e.g. general conversation, no phonetic match). Do not invent
a command that was not spoken.
"""


def check_wake_word(transcript: str) -> tuple[bool, str]:
    """Classify a transcription as a wake-word call and extract the command.

    Returns (wake_detected, command). On any failure (LLM down, malformed
    response, timeout) returns (False, "") so the utterance is silently
    ignored rather than false-triggering.
    """
    body = {
        "messages": [
            {"role": "system", "content": WAKE_WORD_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.0,
        "max_tokens": 128,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    _apply_thinking_flag(body)
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=15)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        wake = bool(parsed.get("wake", False))
        command = str(parsed.get("command", "") or "").strip()
        log.info("[wake-llm] transcript=%r → wake=%s command=%r", transcript, wake, command)
        return wake, command
    except Exception as e:
        log.warning("[wake-llm] classifier failed (%s); ignoring utterance", e)
        return False, ""


VOICE_COMBINED_PROMPT = """\
You are Halbot, a Discord soundboard bot that listens in a voice channel.
A user spoke and an imperfect STT engine transcribed what they said.  In a
SINGLE response, do two things:

1. Decide whether the speaker addressed you (wake word "Halbot" — or any
   close phonetic mishearing: Hal Bot, Albot, Owlbot, Palbot, Walbot,
   Halbert, Hellboy, Hellbot, Howlbot, Holbot, Hal-Bot, Hal Bought,
   How Bout, Al Bought, etc.).  If NOT addressed, return wake=false and
   actions=[].
2. If addressed, pick the best sound to play for the command that follows
   the wake word.

SAVED LIBRARY:
<<SAVED_DETAILS>>

LIVE SOUNDBOARD:
<<SOUND_DETAILS>>

<<PERSONA_DIRECTIVES>>

Reply with ONLY this JSON, no prose, no markdown:
  {"wake": <true|false>, "actions": [<action>, ...]}

Each action is one of:
  {"action": "voice_play", "name": "<exact sound name>"}
  {"action": "unknown", "message": "<brief response>"}

If wake=false, actions MUST be [].
Match creatively — "something scary" → pick a scary-sounding name,
"play airhorn" → exact match.  Names must be EXACT from the lists above.
"""


def parse_voice_combined(
    transcript: str, sounds, saved: list[dict]
) -> tuple[str, list[dict]]:
    """Single-call wake detection + intent parsing.

    Returns (status, actions) where status is one of:
      - "wake":    wake word detected; actions is the intent list (possibly empty)
      - "no_wake": wake word absent; actions is []
      - "error":   LLM/parse failure; actions is []
    The caller uses status to tell "user didn't address us" apart from
    "something went wrong" so real failures don't masquerade as silence.
    """
    personas = persona_list()
    if personas:
        persona_lines = [f"- {p['directive']}" for p in personas]
        pd_block = "ACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(persona_lines)
    else:
        pd_block = ""
    system = (
        VOICE_COMBINED_PROMPT
        .replace("<<SAVED_DETAILS>>", format_saved_details(saved))
        .replace("<<SOUND_DETAILS>>", format_sound_details(sounds))
        .replace("<<PERSONA_DIRECTIVES>>", pd_block)
    )
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    _apply_thinking_flag(body)

    content = ""
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        if resp.status_code >= 400:
            log.warning("Voice combined LLM %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=60)
        resp.raise_for_status()
        raw_json = resp.json()
        choice = raw_json["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        # Some servers leak only the closing </think> even when thinking is
        # disabled mid-stream; always partition on it if present.
        if "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        if not content and reasoning:
            # Pull out the first {...} block from reasoning rather than just
            # the final line, which on multi-line JSON is usually a bare "}".
            start = reasoning.find("{")
            end = reasoning.rfind("}")
            if start != -1 and end > start:
                content = reasoning[start:end + 1]
        log.info("[voice-combined] finish_reason=%s usage=%s content=%r",
                 finish_reason, usage, content[:200])
        if not content:
            return "error", []
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        wake = bool(parsed.get("wake", False))
        actions = parsed.get("actions") or []
        if not isinstance(actions, list):
            actions = [actions] if isinstance(actions, dict) else []
        return ("wake" if wake else "no_wake"), actions
    except json.JSONDecodeError:
        log.warning("[voice-combined] non-JSON response: %r", content[:200])
        return "error", []
    except Exception as e:
        log.warning("[voice-combined] call failed (%s)", e)
        return "error", []


def parse_voice_intent(transcript: str, sounds, saved: list[dict]) -> list[dict]:
    """Lightweight LLM call to pick a sound from a voice command transcript."""
    personas = persona_list()
    if personas:
        persona_lines = [f"- {p['directive']}" for p in personas]
        pd_block = "ACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(persona_lines)
    else:
        pd_block = ""

    system = VOICE_COMMAND_PROMPT.format(
        sound_details=format_sound_details(sounds),
        saved_details=format_saved_details(saved),
        persona_directives_block=pd_block,
    )

    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        # Bumped from 256 — reasoning-capable models emit <think>…</think>
        # tokens that eat into the budget before the JSON answer is produced.
        "max_tokens": 1024,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    _apply_thinking_flag(body)

    content = ""
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=30)
        if resp.status_code >= 400:
            log.warning("Voice LLM %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=60)
        resp.raise_for_status()
        raw_json = resp.json()
        choice = raw_json["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        content = (message.get("content") or "").strip()
        # Some reasoning models (DeepSeek-R1, gemma "thinking" variants) put
        # their chain-of-thought in `reasoning_content` and the final answer
        # in `content`.  Others leak the whole thing into `content` wrapped
        # in <think>…</think>.  Handle both.
        reasoning = (message.get("reasoning_content") or "").strip()
        if "</think>" in content:
            before, _, rest = content.partition("</think>")
            content = rest.strip()
            log.debug("[voice-llm] stripped <think> block (%d chars)", len(before))
        if not content and reasoning:
            # Fallback: some servers forget to populate `content` when
            # reasoning_content is set and the model hit a token ceiling.
            # Extract the first {...} block rather than just the last line
            # (which for multi-line JSON is typically a bare "}").
            log.warning("[voice-llm] content empty, falling back to reasoning_content JSON")
            start = reasoning.find("{")
            end = reasoning.rfind("}")
            if start != -1 and end > start:
                content = reasoning[start:end + 1]
        log.info("[voice-llm] finish_reason=%s usage=%s content=%r",
                 finish_reason, usage, content[:200])
        if not content:
            log.error("[voice-llm] empty content (finish_reason=%s) — bump max_tokens or check model", finish_reason)
            return [{"action": "unknown", "message": f"LLM returned no content (finish_reason={finish_reason})."}]
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return [{"action": "unknown", "message": str(parsed)}]
    except json.JSONDecodeError:
        log.warning("Voice LLM returned non-JSON: %r", content[:200])
        return [{"action": "unknown", "message": content or "(empty LLM response)"}]
    except Exception as e:
        log.error("Voice intent parse failed: %s", e)
        return [{"action": "unknown", "message": "Couldn't process that voice command."}]


async def handle_voice_command(guild, text_channel, user_id, transcript):
    """Callback from VoiceListener with a raw STT transcript.

    Owns wake-word detection: depending on VOICE_LLM_COMBINE_CALLS this is
    either a single combined call (wake + intent together) or two sequential
    calls (wake classifier first, intent parser only on wake).
    """
    listener = voice_listeners.get(guild.id)
    if not listener:
        return

    if VOICE_LLM_COMBINE_CALLS:
        try:
            sounds = list(await guild.fetch_soundboard_sounds())
        except discord.HTTPException:
            sounds = []
        saved = db_list()
        status, actions = await asyncio.to_thread(
            parse_voice_combined, transcript, sounds, saved
        )
        if status == "no_wake":
            log.info("[voice] no wake word in: %r", transcript)
            return
        if status == "error":
            log.warning("[voice] combined LLM call errored on: %r", transcript)
            await text_channel.send("\U0001f3a4 Voice command processing failed.")
            return
        if not actions:
            # Wake heard but LLM didn't pick anything actionable.  Ack so
            # the user isn't left wondering whether we heard them.
            actions = [{"action": "unknown",
                        "message": "I heard you but couldn't pick a sound for that."}]
    else:
        wake, command = await asyncio.to_thread(check_wake_word, transcript)
        if not wake:
            log.info("[voice] no wake word in: %r", transcript)
            return
        if not command:
            log.info("[voice] wake word alone, no command")
            return
        log.info("[voice] user=%s command: %r", user_id, command)
        try:
            sounds = list(await guild.fetch_soundboard_sounds())
        except discord.HTTPException:
            sounds = []
        saved = db_list()
        actions = await asyncio.to_thread(
            parse_voice_intent, command, sounds, saved
        )

    saved_map = {s["name"]: s for s in saved}
    sound_map = {s.name: s for s in sounds}

    for intent in actions:
        action = intent.get("action")

        if action == "voice_play":
            name = intent.get("name", "")
            # Try saved library first (has raw bytes)
            row = db_get(name) if name else None
            if row:
                fmt = detect_audio_format(row["audio"])
                await listener.play_sound(row["audio"], fmt)
                member = guild.get_member(user_id)
                who = member.display_name if member else f"user {user_id}"
                await text_channel.send(f"\U0001f50a Playing **{name}** (requested by {who})")
                return

            # Try live soundboard
            live = sound_map.get(name)
            if live:
                try:
                    audio = await live.read()
                    fmt = detect_audio_format(audio)
                    await listener.play_sound(audio, fmt)
                    member = guild.get_member(user_id)
                    who = member.display_name if member else f"user {user_id}"
                    await text_channel.send(f"\U0001f50a Playing **{name}** (requested by {who})")
                except Exception:
                    log.exception("Failed to read live sound %s for voice playback", name)
                return

            customized = await customize_response_async(
                f'Couldn\'t find a sound called "{name}".',
                context="voice command: sound lookup miss",
            )
            await text_channel.send(f"\U0001f3a4 {customized}")

        elif action == "unknown":
            msg = intent.get("message", "I didn't understand that voice command.")
            customized = await customize_response_async(msg, context="voice command failure")
            await text_channel.send(f"\U0001f3a4 {customized}")


# The discord.Client is built lazily via build_client() so the tray app can
# recreate a fresh client on each Start (a closed Client can't be reused).
# Handlers below reference `client` at call time, so after build_client()
# reassigns it, they operate on the current instance.
client: "discord.Client | None" = None


def snapshot_voice_state() -> None:
    """Capture active voice sessions into _voice_reconnect before shutdown.

    Must be called *before* client.close() — by the time the client disconnects,
    on_voice_state_update has already cleared voice_listeners.
    """
    for gid, listener in list(voice_listeners.items()):
        if listener.vc.is_connected():
            _voice_reconnect[gid] = (listener.vc.channel.id, listener.text_channel.id)
            log.info("[voice] Snapshotted session for guild %s: vc=%s tc=%s", gid, listener.vc.channel.id, listener.text_channel.id)


def build_client() -> discord.Client:
    """Create (or recreate) the module-level discord.Client with handlers wired up."""
    global client
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.voice_states = True  # needed for voice channel awareness
    client = discord.Client(intents=intents)
    client.event(on_ready)
    client.event(on_guild_emojis_update)
    client.event(on_message)
    client.event(on_voice_state_update)
    voice_listeners.clear()
    return client


async def on_ready():
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    for guild in client.guilds:
        await sync_emojis(guild)

    # Reconnect to voice channels that were active before a restart.
    if _voice_reconnect and VOICE_RECV_AVAILABLE:
        for gid, (vc_id, tc_id) in list(_voice_reconnect.items()):
            guild = client.get_guild(gid)
            if not guild:
                continue
            vc_channel = guild.get_channel(vc_id)
            tc_channel = guild.get_channel(tc_id)
            if not vc_channel or not tc_channel:
                log.warning("[voice] Reconnect skipped for guild %s — channel not found", gid)
                continue
            try:
                log.info("[voice] Reconnecting to #%s in %s", vc_channel.name, guild.name)
                vc = await vc_channel.connect(cls=HalbotVoiceRecvClient)
                listener = VoiceListener(vc, tc_channel, handle_voice_command)
                voice_listeners[gid] = listener
                import threading
                threading.Thread(target=load_whisper, daemon=True).start()
                listener.start()
                log.info("[voice] Reconnected to #%s", vc_channel.name)
            except Exception:
                log.exception("[voice] Failed to reconnect to #%s", vc_channel.name)
        _voice_reconnect.clear()


async def on_guild_emojis_update(guild, before, after):
    await sync_emojis(guild)


def _maybe_unload_whisper() -> None:
    """Free whisper VRAM once the last voice session ends.  Run off-thread
    since GC + torch.cuda.empty_cache() can take a beat."""
    if unload_whisper is None or voice_listeners:
        return
    import threading
    threading.Thread(target=unload_whisper, daemon=True).start()


async def on_voice_state_update(member, before, after):
    """Clean up voice listener when the bot is disconnected from voice."""
    if client is None or member != client.user:
        return
    if before.channel and not after.channel:
        guild_id = before.channel.guild.id
        listener = voice_listeners.pop(guild_id, None)
        if listener:
            listener.stop()
            log.info("Voice listener removed (bot left %s)", before.channel.name)
            _maybe_unload_whisper()


async def on_message(message: discord.Message):
    log.info("Message received: %r from %s, mentions: %s", message.content, message.author, message.mentions)
    # Ignore own messages and messages that don't mention the bot
    if message.author == client.user:
        return
    if client.user not in message.mentions:
        return

    # Strip the @mention from the message text
    user_text = message.content
    for mention_str in [f"<@{client.user.id}>", f"<@!{client.user.id}>"]:
        user_text = user_text.replace(mention_str, "")
    user_text = user_text.strip()

    # Process audio attachments
    attachment_data = {}  # filename -> bytes
    attachment_info = []
    for att in message.attachments:
        ext = os.path.splitext(att.filename)[1].lower()
        if ext in ALLOWED_EXTENSIONS or (att.content_type and att.content_type in ALLOWED_CONTENT_TYPES):
            try:
                data = await att.read()
                ok, reason, duration = validate_audio(data, att.filename)
                attachment_data[att.filename] = data
                attachment_info.append({
                    "filename": att.filename,
                    "size_kb": round(len(data) / 1024, 1),
                    "duration": duration,
                    "valid": ok,
                    "reason": reason,
                })
            except Exception as e:
                log.error("Failed to read attachment %s: %s", att.filename, e)
                attachment_info.append({
                    "filename": att.filename,
                    "size_kb": round(att.size / 1024, 1),
                    "duration": None,
                    "valid": False,
                    "reason": f"Failed to read: {e}",
                })

    if not user_text and not attachment_info:
        await message.reply("Hey! Tell me what to do with the soundboard. I can **list**, **remove**, **clear**, or **save** sounds. You can also send me audio files!")
        return

    if not user_text and attachment_info:
        user_text = "save this audio file"

    guild = message.guild
    if not guild:
        await message.reply("This only works in a server.")
        return

    # Fetch current soundboard sounds
    try:
        sounds = list(await guild.fetch_soundboard_sounds())
        sound_map = {s.name: s for s in sounds}
    except discord.HTTPException as e:
        log.error("Failed to fetch soundboard sounds: %s", e)
        await message.reply("Couldn't fetch the soundboard. Do I have the right permissions?")
        return

    # Fetch recent channel history for conversation context
    channel_history = []
    history_attachments = {}  # filename -> url, for downloading from history
    async for msg in message.channel.history(limit=CHANNEL_HISTORY_LIMIT, before=message):
        text = msg.content
        # Note any audio attachments in history
        audio_atts = []
        for att in msg.attachments:
            ext = os.path.splitext(att.filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS or (att.content_type and att.content_type in ALLOWED_CONTENT_TYPES):
                size_kb = round(att.size / 1024, 1)
                audio_atts.append(f"[attached: {att.filename}, {size_kb}KB]")
                history_attachments[att.filename] = att.url
        if audio_atts:
            text = (text + "\n" if text else "") + " ".join(audio_atts)
        if msg.author == client.user:
            channel_history.append({"role": "assistant", "content": f"[BOT REPLY — this is what I said to the user, NOT a JSON action]: {text}"})
        else:
            channel_history.append({"role": "user", "content": f"{msg.author.display_name}: {text}"})
    channel_history.reverse()

    # Fetch saved sounds library
    saved = db_list()
    saved_map = {s["name"]: s for s in saved}

    # Ask LM Studio what to do
    actions = parse_intent(user_text, sounds, saved, channel_history, attachment_info or None, guild=guild)
    replies = []

    def _reply(default: str, intent: dict = None) -> str:
        """Use LLM-provided message as intro if present, otherwise use default."""
        llm_msg = (intent or {}).get("message")
        if llm_msg:
            return f"{llm_msg}\n\n{default}" if default else llm_msg
        return default

    for intent in actions:
        action = intent.get("action")

        if action == "error":
            raw = intent.get("message", "Something went wrong.")
            customized = await customize_response_async(raw, context="bot-wide LLM error")
            replies.append(customized)
            break

        elif action == "list":
            names = intent.get("names", [])
            # Empty names = list all sounds
            matched = [sound_map[n] for n in names if n in sound_map] if names else list(sounds)
            if not matched:
                replies.append(_reply("The soundboard is empty." if not names else "No sounds match that.", intent))
            else:
                lines = []
                for s in matched:
                    emoji_str = ""
                    if s.emoji:
                        if s.emoji.id:
                            emoji_str = f"<{'a' if s.emoji.animated else ''}:{s.emoji.name}:{s.emoji.id}> "
                        elif s.emoji.name:
                            emoji_str = f"{s.emoji.name} "
                    user_str = f" (by {s.user})" if s.user else ""
                    date_str = f" — {s.created_at.strftime('%Y-%m-%d')}" if s.created_at else ""
                    lines.append(f"- {emoji_str}**{s.name}**{user_str}{date_str}")
                replies.append(_reply(f"**Soundboard sounds ({len(matched)}):**\n" + "\n".join(lines), intent))

        elif action == "remove":
            names = intent.get("names", [])
            matched = [sound_map[n] for n in names if n in sound_map]
            if not matched:
                replies.append("Couldn't find any matching sounds.")
            else:
                deleted, errors = [], []
                for sound in matched:
                    try:
                        await sound.delete(reason=f"Removed by {message.author} via Halbot")
                        deleted.append(sound.name)
                    except discord.HTTPException as e:
                        log.error("Failed to delete sound %s: %s", sound.name, e)
                        errors.append(sound.name)
                reply = f"Removed **{', '.join(deleted)}**." if deleted else ""
                if errors:
                    reply += f" Failed to remove: {', '.join(errors)}."
                replies.append(_reply(reply, intent))

        elif action == "edit":
            target_name = intent.get("name", "")
            sound = sound_map.get(target_name)
            if not sound:
                replies.append(f'Couldn\'t find a sound called "{target_name}".')
            else:
                kwargs = {}
                if "emoji" in intent:
                    kwargs["emoji"] = intent["emoji"]
                if "new_name" in intent and intent["new_name"]:
                    kwargs["name"] = intent["new_name"]
                if not kwargs:
                    replies.append("Nothing to change.")
                else:
                    try:
                        await sound.edit(reason=f"Edited by {message.author} via Halbot", **kwargs)
                        changes = []
                        if "emoji" in kwargs:
                            changes.append(f"emoji → {kwargs['emoji']}")
                        if "name" in kwargs:
                            changes.append(f"name → **{kwargs['name']}**")
                        replies.append(_reply(f"Updated **{target_name}**: {', '.join(changes)}", intent))
                    except discord.HTTPException as e:
                        log.error("Failed to edit sound %s: %s", target_name, e)
                        replies.append(f'Failed to edit "{target_name}". Do I have the right permissions?')

        elif action == "clear":
            if not sounds:
                replies.append("The soundboard is already empty.")
            else:
                deleted, errors = 0, 0
                for sound in sounds:
                    try:
                        await sound.delete(reason=f"Cleared by {message.author} via Halbot")
                        deleted += 1
                    except discord.HTTPException:
                        errors += 1
                reply = f"Cleared **{deleted}** sound(s)."
                if errors:
                    reply += f" Failed to remove {errors} sound(s)."
                replies.append(_reply(reply, intent))

        elif action == "upload":
            target_filename = intent.get("filename")
            name = intent.get("name", "")
            metadata = intent.get("metadata", "")

            # Build a map of files to process: current attachments + history
            files_to_process = {}
            if target_filename:
                # LLM specified a specific file
                if target_filename in attachment_data:
                    files_to_process[target_filename] = attachment_data[target_filename]
                elif target_filename in history_attachments:
                    # Download from history URL
                    try:
                        resp = requests.get(history_attachments[target_filename], timeout=15)
                        resp.raise_for_status()
                        files_to_process[target_filename] = resp.content
                    except requests.RequestException as e:
                        log.error("Failed to download %s from history: %s", target_filename, e)
                        replies.append(f"Couldn't download **{target_filename}** — the link may have expired.")
                        continue
                else:
                    replies.append(f"Couldn't find a file called **{target_filename}**.")
                    continue
            else:
                # No specific file — use all current attachments
                files_to_process = dict(attachment_data)

            if not files_to_process:
                replies.append("No audio files found to upload.")
            else:
                saved_names, errors = [], []
                for filename, data in files_to_process.items():
                    ok, reason, duration = validate_audio(data, filename)
                    if not ok:
                        errors.append(f"{filename}: {reason}")
                        continue
                    sound_name = name or os.path.splitext(filename)[0]
                    try:
                        db_save(sound_name, data, None, metadata, str(message.author))
                        saved_names.append(sound_name)
                    except Exception as e:
                        log.error("Failed to save uploaded sound %s: %s", filename, e)
                        errors.append(f"{filename}: {e}")
                reply = f"Saved **{', '.join(saved_names)}** to the library." if saved_names else ""
                if errors:
                    reply += " Errors:\n" + "\n".join(f"- {e}" for e in errors)
                replies.append(_reply(reply or "No valid audio files to save.", intent))

        elif action == "save":
            names = intent.get("names", [])
            metadata = intent.get("metadata", "")
            matched = [sound_map[n] for n in names if n in sound_map]
            if not matched:
                replies.append("Couldn't find any matching sounds on the soundboard.")
            else:
                saved_names, errors = [], []
                for sound in matched:
                    try:
                        audio = await sound.read()
                        emoji_str = None
                        if sound.emoji:
                            emoji_str = sound.emoji.name if sound.emoji.is_unicode_emoji() else str(sound.emoji)
                        db_save(sound.name, audio, emoji_str, metadata, str(message.author))
                        saved_names.append(sound.name)
                    except Exception as e:
                        log.error("Failed to save sound %s: %s", sound.name, e)
                        errors.append(sound.name)
                reply = f"Saved **{', '.join(saved_names)}** to the library." if saved_names else ""
                if errors:
                    reply += f" Failed to save: {', '.join(errors)}."
                replies.append(_reply(reply, intent))

        elif action == "saved_list":
            names = intent.get("names", [])
            matched = [saved_map[n] for n in names if n in saved_map] if names else saved
            if not matched:
                replies.append("No saved sounds match that.")
            else:
                lines = []
                for s in matched:
                    emoji_str = f"{s['emoji']} " if s.get("emoji") else ""
                    size_kb = round(s.get("size_bytes", 0) / 1024, 1)
                    meta_str = f" — {s['metadata']}" if s.get("metadata") else ""
                    lines.append(f"- {emoji_str}**{s['name']}** ({size_kb}KB, saved by {s.get('saved_by', '?')}){meta_str}")
                replies.append(_reply(f"**Saved library ({len(matched)}):**\n" + "\n".join(lines), intent))

        elif action == "saved_update":
            target_name = intent.get("name", "")
            if target_name not in saved_map:
                replies.append(f'No saved sound called "{target_name}".')
            else:
                try:
                    new_name = intent.get("new_name")
                    emoji = intent.get("emoji", ...)
                    metadata = intent.get("metadata")
                    db_update(target_name, new_name=new_name, emoji=emoji, metadata=metadata)
                    changes = []
                    if new_name:
                        changes.append(f"name → **{new_name}**")
                    if emoji is not ...:
                        changes.append(f"emoji → {emoji}")
                    if metadata is not None:
                        changes.append("metadata updated")
                    replies.append(_reply(f"Updated saved sound **{target_name}**: {', '.join(changes)}", intent))
                except ValueError as e:
                    replies.append(str(e))

        elif action == "saved_delete":
            names = intent.get("names", [])
            deleted = [n for n in names if db_delete(n)]
            missed = [n for n in names if n not in deleted]
            reply = f"Deleted **{', '.join(deleted)}** from the library." if deleted else ""
            if missed:
                reply += f" Not found: {', '.join(missed)}."
            replies.append(_reply(reply or "No matching saved sounds found.", intent))

        elif action == "restore":
            names = intent.get("names", [])
            restored, errors = [], []
            for name in names:
                row = db_get(name)
                if not row:
                    errors.append(f"{name} (not found)")
                    continue
                try:
                    emoji = row["emoji"] if row.get("emoji") else None
                    await guild.create_soundboard_sound(
                        name=row["name"],
                        sound=row["audio"],
                        emoji=emoji,
                        reason=f"Restored by {message.author} via Halbot",
                    )
                    restored.append(name)
                except discord.HTTPException as e:
                    log.error("Failed to restore sound %s: %s", name, e)
                    errors.append(f"{name} ({e.text if hasattr(e, 'text') else str(e)})")
            reply = f"Restored **{', '.join(restored)}** to the soundboard." if restored else ""
            if errors:
                reply += f" Failed: {', '.join(errors)}."
            replies.append(_reply(reply or "Nothing to restore.", intent))

        elif action == "effect_ask":
            # LLM is asking the user to pick effect parameters — just relay the message
            replies.append(intent.get("message", "What settings would you like for the effect?"))

        elif action == "effect_apply":
            target_name = intent.get("name", "")
            effect_type = intent.get("effect", "")
            params = intent.get("params", {})
            save_as = intent.get("save_as", f"{target_name}-{effect_type}")

            if effect_type not in SUPPORTED_EFFECTS:
                replies.append(f"Unknown effect `{effect_type}`. Supported: {', '.join(sorted(SUPPORTED_EFFECTS))}.")
                continue

            # Fetch the source clip
            source = db_get(target_name) if target_name else None
            if not source:
                replies.append(f'No saved sound called "{target_name}".')
                continue

            # Short-circuit grandchildren: always derive from the true original
            if source.get("parent_id"):
                original_id = source["parent_id"]
                existing_effects = json.loads(source.get("effects") or "[]")
            else:
                original_id = source["id"]
                existing_effects = []

            # Build combined effects chain
            new_effect = {"type": effect_type, "params": params}
            combined_effects = existing_effects + [new_effect]

            # Fetch the original's raw audio and re-apply the full chain
            original = db_get_by_id(original_id)
            if not original:
                replies.append("The original sound this was derived from no longer exists.")
                continue

            try:
                processed = apply_effects_chain(original["audio"], combined_effects)
            except Exception as e:
                log.error("Effect processing failed for %s: %s", target_name, e)
                replies.append(f"Failed to apply {effect_type}: {e}")
                continue

            # Validate the processed output
            fmt = detect_audio_format(processed)
            ok, reason, duration = validate_audio(processed, f"{save_as}.{fmt}")
            if not ok:
                replies.append(f"The processed audio is invalid: {reason}")
                continue

            # Save as a new clip, child of the original
            try:
                new_id = db_save(
                    save_as, processed, source.get("emoji"),
                    source.get("metadata", ""), str(message.author),
                    parent_id=original_id,
                    effects=json.dumps(combined_effects),
                )
                effect_desc = " + ".join(e["type"] for e in combined_effects)
                default_msg = (
                    f"Created **{save_as}** ({effect_desc} applied to **{original['name']}**). "
                    f"Saved to the library."
                )
                replies.append(_reply(default_msg, intent))
                # Refresh saved_map so subsequent actions in this batch can see it
                saved_map[save_as] = db_get(save_as)
            except Exception as e:
                log.error("Failed to save processed sound %s: %s", save_as, e)
                replies.append(f"Effect applied but failed to save: {e}")

        elif action == "persona_set":
            directive = intent.get("directive", "")
            confirm_msg = intent.get("message", "Got it!")
            try:
                persona_add(directive, str(message.author))
                log.info("Persona directive added by %s: %s", message.author, directive)
                replies.append(confirm_msg)
            except ValueError as e:
                replies.append(str(e))

        elif action == "persona_update":
            pid = intent.get("id")
            directive = intent.get("directive", "")
            confirm_msg = intent.get("message", "Updated!")
            if pid is None:
                replies.append("No directive ID specified.")
            else:
                try:
                    if persona_update(int(pid), directive):
                        log.info("Persona directive %s updated by %s: %s", pid, message.author, directive)
                        replies.append(confirm_msg)
                    else:
                        replies.append(f"Couldn't find directive #{pid}.")
                except ValueError as e:
                    replies.append(str(e))

        elif action == "persona_remove":
            pid = intent.get("id")
            confirm_msg = intent.get("message", "Removed!")
            if pid is None:
                replies.append("No directive ID specified.")
            elif persona_remove(int(pid)):
                log.info("Persona directive %s removed by %s", pid, message.author)
                replies.append(confirm_msg)
            else:
                replies.append(f"Couldn't find directive #{pid}.")

        elif action == "persona_list":
            personas = persona_list()
            if not personas:
                replies.append("No behavior directives are active. I'm using my default personality.")
            else:
                lines = []
                for p in personas:
                    lines.append(f"- [#{p['id']}] \"{p['directive']}\" (set by {p['set_by']} on {p['created_at']})")
                replies.append(_reply(f"**Active behavior directives ({len(personas)}):**\n" + "\n".join(lines), intent))

        elif action == "emoji_list":
            names = intent.get("names", [])
            emoji_records = emoji_db_list()
            emoji_map = {e["name"]: e for e in emoji_records}
            matched = [emoji_map[n] for n in names if n in emoji_map] if names else emoji_records
            if not matched:
                replies.append("No custom emojis found on this server.")
            else:
                lines = []
                for e in matched:
                    prefix = "a" if e["animated"] else ""
                    fmt = f"<{prefix}:{e['name']}:{e['emoji_id']}>"
                    desc = f" — {e['description']}" if e.get("description") else ""
                    lines.append(f"- {fmt} **{e['name']}**{desc}")
                replies.append(_reply(f"**Custom emojis ({len(matched)}):**\n" + "\n".join(lines), intent))

        # -- Voice channel actions ------------------------------------------

        elif action == "voice_join":
            if not VOICE_RECV_AVAILABLE:
                replies.append("Voice features are not available — install `discord-ext-voice-recv` and `faster-whisper`.")
                continue

            channel_name = intent.get("channel", "")
            # Exact match first, then case-insensitive, then substring
            vc_channel = discord.utils.get(guild.voice_channels, name=channel_name)
            if not vc_channel:
                vc_channel = discord.utils.find(
                    lambda c: c.name.lower() == channel_name.lower(),
                    guild.voice_channels,
                )
            if not vc_channel:
                vc_channel = discord.utils.find(
                    lambda c: channel_name.lower() in c.name.lower(),
                    guild.voice_channels,
                )
            if not vc_channel:
                vc_names = ", ".join(c.name for c in guild.voice_channels)
                replies.append(f'Couldn\'t find voice channel "{channel_name}". Available: {vc_names}')
                continue

            # Disconnect from existing voice in this guild if any
            existing = voice_listeners.pop(guild.id, None)
            if existing:
                existing.stop()
                try:
                    await existing.vc.disconnect()
                except Exception:
                    pass

            try:
                vc = await vc_channel.connect(cls=HalbotVoiceRecvClient)
                listener = VoiceListener(vc, message.channel, handle_voice_command)
                voice_listeners[guild.id] = listener

                # Pre-load whisper model in background so first command is fast
                import threading
                threading.Thread(target=load_whisper, daemon=True).start()

                listener.start()
                replies.append(
                    _reply(f'Joined **{vc_channel.name}**. Say "Halbot" followed by a command!', intent)
                )
            except Exception as e:
                log.exception("Failed to join voice channel %s", vc_channel.name)
                replies.append(f"Failed to join **{vc_channel.name}**: {e}")

        elif action == "voice_leave":
            listener = voice_listeners.pop(guild.id, None)
            if listener:
                listener.stop()
                try:
                    await listener.vc.disconnect()
                except Exception:
                    pass
                _maybe_unload_whisper()
                replies.append(_reply("Left the voice channel.", intent))
            else:
                replies.append("I'm not in a voice channel.")

        elif action == "voice_play":
            listener = voice_listeners.get(guild.id)
            if not listener or not listener.vc.is_connected():
                replies.append("I need to be in a voice channel first. Ask me to join one!")
                continue

            name = intent.get("name", "")
            # Try saved library
            row = db_get(name) if name else None
            if row:
                fmt = detect_audio_format(row["audio"])
                await listener.play_sound(row["audio"], fmt)
                replies.append(_reply(f"\U0001f50a Playing **{name}** in voice.", intent))
                continue

            # Try live soundboard
            live = sound_map.get(name)
            if live:
                try:
                    audio = await live.read()
                    fmt = detect_audio_format(audio)
                    await listener.play_sound(audio, fmt)
                    replies.append(_reply(f"\U0001f50a Playing **{name}** in voice.", intent))
                except Exception:
                    log.exception("Failed to read live sound %s for voice playback", name)
                    replies.append(f"Failed to play **{name}**.")
                continue

            replies.append(f'Couldn\'t find a sound called "{name}".')

        else:
            replies.append(intent.get("message", "I didn't understand that."))

    if replies:
        full_text = "\n\n".join(replies)
        # Discord max message length is 2000 chars; split into chunks
        chunks = []
        while len(full_text) > 2000:
            # Find a newline to split on near the limit
            split_at = full_text.rfind("\n", 0, 2000)
            if split_at == -1:
                split_at = 2000
            chunks.append(full_text[:split_at])
            full_text = full_text[split_at:].lstrip("\n")
        if full_text:
            chunks.append(full_text)

        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk)
            else:
                await message.channel.send(chunk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Halbot — Discord soundboard manager")
    parser.add_argument("--clear-personas", action="store_true",
                        help="Clear all persona/behavior directives and exit")
    parser.add_argument("--list-personas", action="store_true",
                        help="List all persona/behavior directives and exit")
    args = parser.parse_args()

    configure_logging()

    if not DISCORD_TOKEN and not (args.clear_personas or args.list_personas):
        print("Error: DISCORD_TOKEN not set. Copy .env.example to .env and fill it in.")
        raise SystemExit(1)

    db_init()

    if args.list_personas:
        personas = persona_list()
        if not personas:
            print("No active persona directives.")
        else:
            for p in personas:
                print(f"  [{p['id']}] \"{p['directive']}\" — set by {p['set_by']} on {p['created_at']}")
        raise SystemExit(0)

    if args.clear_personas:
        count = persona_clear()
        print(f"Cleared {count} persona directive(s).")
        raise SystemExit(0)

    build_client()
    client.run(DISCORD_TOKEN)
