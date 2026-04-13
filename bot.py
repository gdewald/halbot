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

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("showmebot")

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


def db_save(name: str, audio: bytes, emoji: str | None, metadata: str, saved_by: str,
            parent_id: int | None = None, effects: str = "") -> int:
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
    try:
        resp = requests.post(
            LMSTUDIO_URL,
            json={
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
            },
            timeout=30,
        )
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

Today's date is {today}.

Based on the user's request, return a JSON response. If the request involves a single action, return a single JSON object. If it involves multiple steps (e.g. "back up and clear"), return a JSON array of action objects — they will be executed in order.

Available actions:

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

--- Fallback ---

{{"action": "unknown", "message": "<your response>"}}
The user's request doesn't match any supported action. Write a short, friendly response explaining what you can do and why their request didn't match.

SOUNDBOARD LIMITS: Max file size 512KB, max duration 5.2 seconds, formats: MP3/OGG/WAV.

{attachments}

IMPORTANT: Names must be EXACT matches from the appropriate list (live or saved). Use your judgement to match typos, abbreviations, or descriptions to the correct names.

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
                  attachment_info: list[dict] | None = None) -> list[dict]:
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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            sound_details=format_sound_details(sounds),
            saved_details=format_saved_details(saved),
            custom_emojis=emojis_str,
            today=date.today().isoformat(),
            attachments=attachments_str,
        )},
        *channel_history,
        {"role": "user", "content": user_text},
    ]

    try:
        resp = requests.post(
            LMSTUDIO_URL,
            json={
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 300,
            },
            timeout=15,
        )
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
        parsed = json.loads(content)
        log.info("Parsed actions: %s", json.dumps(parsed, indent=2))
        # Normalize to a list of actions
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return [{"action": "unknown"}]
    except requests.ConnectionError:
        log.error("Could not connect to LM Studio at %s", LMSTUDIO_URL)
        return [{"action": "error", "message": "I'm having trouble thinking right now — is LM Studio running?"}]
    except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
        log.error("Failed to parse intent: %s", e)
        return [{"action": "unknown"}]


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    for guild in client.guilds:
        await sync_emojis(guild)


@client.event
async def on_guild_emojis_update(guild, before, after):
    await sync_emojis(guild)


@client.event
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
            channel_history.append({"role": "assistant", "content": text})
        else:
            channel_history.append({"role": "user", "content": f"{msg.author.display_name}: {text}"})
    channel_history.reverse()

    # Fetch saved sounds library
    saved = db_list()
    saved_map = {s["name"]: s for s in saved}

    # Ask LM Studio what to do
    actions = parse_intent(user_text, sounds, saved, channel_history, attachment_info or None)
    replies = []

    for intent in actions:
        action = intent.get("action")

        if action == "error":
            replies.append(intent.get("message", "Something went wrong."))
            break

        elif action == "list":
            names = intent.get("names", [])
            matched = [sound_map[n] for n in names if n in sound_map]
            if not matched:
                replies.append("No sounds match that.")
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
                replies.append(f"**Soundboard sounds ({len(matched)}):**\n" + "\n".join(lines))

        elif action == "remove":
            names = intent.get("names", [])
            matched = [sound_map[n] for n in names if n in sound_map]
            if not matched:
                replies.append("Couldn't find any matching sounds.")
            else:
                deleted, errors = [], []
                for sound in matched:
                    try:
                        await sound.delete(reason=f"Removed by {message.author} via ShowMeBot")
                        deleted.append(sound.name)
                    except discord.HTTPException as e:
                        log.error("Failed to delete sound %s: %s", sound.name, e)
                        errors.append(sound.name)
                reply = f"Removed **{', '.join(deleted)}**." if deleted else ""
                if errors:
                    reply += f" Failed to remove: {', '.join(errors)}."
                replies.append(reply)

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
                        await sound.edit(reason=f"Edited by {message.author} via ShowMeBot", **kwargs)
                        changes = []
                        if "emoji" in kwargs:
                            changes.append(f"emoji → {kwargs['emoji']}")
                        if "name" in kwargs:
                            changes.append(f"name → **{kwargs['name']}**")
                        replies.append(f"Updated **{target_name}**: {', '.join(changes)}")
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
                        await sound.delete(reason=f"Cleared by {message.author} via ShowMeBot")
                        deleted += 1
                    except discord.HTTPException:
                        errors += 1
                reply = f"Cleared **{deleted}** sound(s)."
                if errors:
                    reply += f" Failed to remove {errors} sound(s)."
                replies.append(reply)

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
                replies.append(reply or "No valid audio files to save.")

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
                replies.append(reply)

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
                replies.append(f"**Saved library ({len(matched)}):**\n" + "\n".join(lines))

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
                    replies.append(f"Updated saved sound **{target_name}**: {', '.join(changes)}")
                except ValueError as e:
                    replies.append(str(e))

        elif action == "saved_delete":
            names = intent.get("names", [])
            deleted = [n for n in names if db_delete(n)]
            missed = [n for n in names if n not in deleted]
            reply = f"Deleted **{', '.join(deleted)}** from the library." if deleted else ""
            if missed:
                reply += f" Not found: {', '.join(missed)}."
            replies.append(reply or "No matching saved sounds found.")

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
                        reason=f"Restored by {message.author} via ShowMeBot",
                    )
                    restored.append(name)
                except discord.HTTPException as e:
                    log.error("Failed to restore sound %s: %s", name, e)
                    errors.append(f"{name} ({e.text if hasattr(e, 'text') else str(e)})")
            reply = f"Restored **{', '.join(restored)}** to the soundboard." if restored else ""
            if errors:
                reply += f" Failed: {', '.join(errors)}."
            replies.append(reply or "Nothing to restore.")

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
                replies.append(
                    f"Created **{save_as}** ({effect_desc} applied to **{original['name']}**). "
                    f"Saved to the library."
                )
                # Refresh saved_map so subsequent actions in this batch can see it
                saved_map[save_as] = db_get(save_as)
            except Exception as e:
                log.error("Failed to save processed sound %s: %s", save_as, e)
                replies.append(f"Effect applied but failed to save: {e}")

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
                replies.append(f"**Custom emojis ({len(matched)}):**\n" + "\n".join(lines))

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
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not set. Copy .env.example to .env and fill it in.")
        raise SystemExit(1)
    db_init()
    client.run(DISCORD_TOKEN)
