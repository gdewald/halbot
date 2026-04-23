import argparse
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import discord
import requests

from . import analytics
from .audio import (
    ALLOWED_CONTENT_TYPES, ALLOWED_EXTENSIONS, SUPPORTED_EFFECTS,
    apply_effects_chain, detect_audio_format, validate_audio,
)
from .db import (
    db_delete, db_get, db_get_by_id, db_init, db_list, db_save, db_update,
    emoji_db_list, emoji_db_get, emoji_db_prune, emoji_db_upsert,
    fact_add, fact_clear, fact_list, fact_remove,
    grudge_clear, grudge_list, grudge_remove, grudge_set,
    persona_add, persona_clear, persona_list, persona_remove, persona_update,
    trigger_add, trigger_clear, trigger_list, trigger_mark_fired, trigger_remove,
    voice_history_load,
    admin_hard_purge, admin_kinds, admin_list_deleted, admin_panic_clear,
    admin_stats, admin_undelete, admin_undelete_all,
)
from .llm import (
    CHANNEL_HISTORY_LIMIT, answer_stats_question_async, customize_response_async,
    customize_response_rich_async,
    describe_emoji_image, format_events_for_prompt, parse_intent,
)
from .bot_ui import EmbedField, Mode, ReplyPayload, refusal_payload, send_halbot_reply
from .interactions import SoundboardActionsView, register_persistent_views
from .voice_session import (
    VOICE_RECV_AVAILABLE, HalbotVoiceRecvClient, VoiceChatSink, VoiceListener,
    VoiceSession, _channel_has_humans, _maybe_unload_whisper, _preload_tts_engine,
    _spec_to_sink, cancel_voice_idle_timer, handle_voice_command, load_whisper,
    schedule_voice_idle_timer, voice_listeners, _voice_reconnect
)

log = logging.getLogger("halbot")

_discord_state: str = "DISCONNECTED"


def _set_discord_state(state: str) -> None:
    global _discord_state
    _discord_state = state
    log.debug("discord state -> %s", state)


def discord_state_proto() -> int:
    """Return current Discord connection state as the proto enum int."""
    from ._gen import mgmt_pb2
    return {
        "UNKNOWN": mgmt_pb2.DISCORD_STATE_UNKNOWN,
        "DISCONNECTED": mgmt_pb2.DISCORD_STATE_DISCONNECTED,
        "CONNECTING": mgmt_pb2.DISCORD_STATE_CONNECTING,
        "CONNECTED": mgmt_pb2.DISCORD_STATE_CONNECTED,
        "RECONNECTING": mgmt_pb2.DISCORD_STATE_RECONNECTING,
        "RATE_LIMITED": mgmt_pb2.DISCORD_STATE_RATE_LIMITED,
        "TOKEN_INVALID": mgmt_pb2.DISCORD_STATE_TOKEN_INVALID,
        "NO_TOKEN": mgmt_pb2.DISCORD_STATE_NO_TOKEN,
    }.get(_discord_state, mgmt_pb2.DISCORD_STATE_UNKNOWN)


async def reconnect() -> None:
    """Stop current Discord client and start a fresh one with current token."""
    global client
    old = client
    if old is not None and not old.is_closed():
        try:
            await old.close()
        except Exception:
            log.exception("reconnect: close failed")
    await run()


def configure_logging(log_path=None) -> None:
    """Install stdout + optional rotating file handler on the root logger. Idempotent."""
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


# The discord.Client is built lazily via build_client() so the tray app can
# recreate a fresh client on each Start (a closed Client can't be reused).
client: "discord.Client | None" = None


async def sync_emojis(guild: discord.Guild):
    """Sync server emojis to the DB, generating descriptions for new/changed ones."""
    server_ids = set()
    synced, skipped = 0, 0
    for emoji in guild.emojis:
        server_ids.add(emoji.id)
        existing = emoji_db_get(emoji.id)
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


_MD_STRIP_RE = re.compile(r"[*_`~]+")
_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
_URL_RE = re.compile(r"https?://\S+")


async def _deliver(message: "discord.Message", full_text: str) -> None:
    """Send full_text to the user via TTS if bot is in voice, else text.

    Handles chunked text fallback (Discord's 2000-char cap).
    """
    if not full_text:
        return
    from .voice_session import _speak
    guild = message.guild
    session = voice_listeners.get(guild.id) if guild else None
    if session and session.vc.is_connected():
        if await _speak(session, full_text):
            return

    chunks = []
    remaining = full_text
    while len(remaining) > 2000:
        split_at = remaining.rfind("\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.reply(chunk)
        else:
            await message.channel.send(chunk)


def build_client() -> discord.Client:
    """Create (or recreate) the module-level discord.Client with handlers wired up."""
    global client
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.voice_states = True
    client = discord.Client(intents=intents)
    client.event(on_ready)
    client.event(on_guild_emojis_update)
    client.event(on_message)
    client.event(on_voice_state_update)
    client.event(on_voice_channel_effect)
    voice_listeners.clear()
    return client


async def on_ready():
    _set_discord_state("CONNECTED")
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    register_persistent_views(client)
    for guild in client.guilds:
        await sync_emojis(guild)

    if _voice_reconnect and VOICE_RECV_AVAILABLE:
        for gid, (vc_id, sink_spec) in list(_voice_reconnect.items()):
            guild = client.get_guild(gid)
            if not guild:
                continue
            vc_channel = guild.get_channel(vc_id)
            if not vc_channel:
                log.warning("[voice] Reconnect skipped for guild %s — voice channel %s gone", gid, vc_id)
                continue
            try:
                log.info("[voice] Reconnecting to #%s in %s", vc_channel.name, guild.name)
                vc = await vc_channel.connect(cls=HalbotVoiceRecvClient)
                listener = VoiceListener(vc, handle_voice_command)
                session = VoiceSession(
                    listener=listener,
                    message_sink=_spec_to_sink(sink_spec, guild, vc_channel),
                    history=voice_history_load(gid),
                )
                voice_listeners[gid] = session
                import threading
                threading.Thread(target=load_whisper, daemon=True).start()
                listener.start()
                if not _channel_has_humans(vc_channel):
                    schedule_voice_idle_timer(gid)
                log.info("[voice] Reconnected to #%s", vc_channel.name)
            except Exception:
                log.exception("[voice] Failed to reconnect to #%s", vc_channel.name)
        _voice_reconnect.clear()


async def on_guild_emojis_update(guild, before, after):
    await sync_emojis(guild)


async def on_voice_channel_effect(effect: "discord.VoiceChannelEffect") -> None:
    """Track native Discord UI soundboard plays (NOT bot-triggered).

    Fires for every voice-channel effect the gateway dispatches to us,
    regardless of whether the bot is connected to that channel. We
    distinguish native UI plays from bot plays by checking the sender:
    if it's the bot's own user, skip (bot plays are already recorded by
    the soundboard_play/voice_play handlers in bot.py and voice_session.py).
    """
    try:
        sound = getattr(effect, "sound", None)
        if sound is None:
            return  # emoji-only effect, not a soundboard play
        user = getattr(effect, "user", None)
        user_id = int(getattr(user, "id", 0) or 0)
        # Skip bot's own plays — those are already tracked via voice_play / soundboard_play.
        if client and client.user and user_id == client.user.id:
            return
        channel = getattr(effect, "channel", None)
        guild = getattr(effect, "guild", None) or getattr(channel, "guild", None)
        guild_id = int(getattr(guild, "id", 0) or 0)
        sound_id = int(getattr(sound, "id", 0) or 0)
        # Resolve sound id → name via guild cache when available.
        name = ""
        if guild is not None and sound_id:
            try:
                for s in getattr(guild, "soundboard_sounds", []) or []:
                    if getattr(s, "id", 0) == sound_id:
                        name = getattr(s, "name", "") or ""
                        break
            except Exception:
                pass
            if not name:
                try:
                    getter = getattr(guild, "get_soundboard_sound", None)
                    if callable(getter):
                        s = getter(sound_id)
                        if s is not None:
                            name = getattr(s, "name", "") or ""
                except Exception:
                    pass
        analytics.record(
            "soundboard_play",
            user_id=user_id,
            guild_id=guild_id,
            target=name or f"sound_{sound_id}",
            source="discord_ui",
            trigger="user",
            sound_id=sound_id,
            channel_id=int(getattr(channel, "id", 0) or 0),
            volume=float(getattr(sound, "volume", 0.0) or 0.0),
        )
        log.info("[soundboard] native-UI play by user=%s sound=%r (id=%s) in guild=%s",
                 user_id, name or "?", sound_id, guild_id)
    except Exception:
        log.exception("on_voice_channel_effect: record failed")


async def on_voice_state_update(member, before, after):
    """React to voice state changes: clean up on bot kick, arm/disarm idle timer."""
    if client is None:
        return

    if member == client.user and before.channel and not after.channel:
        guild_id = before.channel.guild.id
        cancel_voice_idle_timer(guild_id)
        session = voice_listeners.pop(guild_id, None)
        if session:
            session.stop()
            log.info("Voice listener removed (bot left %s)", before.channel.name)
            _maybe_unload_whisper()
        return

    if member.bot:
        return
    guild = (after.channel or before.channel).guild if (after.channel or before.channel) else None
    if guild is None:
        return
    session = voice_listeners.get(guild.id)
    if not session:
        return
    bot_channel = session.vc.channel
    if bot_channel is None:
        return
    entered = after.channel == bot_channel and before.channel != bot_channel
    left = before.channel == bot_channel and after.channel != bot_channel
    if entered:
        cancel_voice_idle_timer(guild.id)
    elif left and not _channel_has_humans(bot_channel):
        schedule_voice_idle_timer(guild.id)


ADMIN_PREFIX = "!halbot admin"
ADMIN_HELP = (
    "**Halbot admin (owner-only) commands** — recovery / panic.\n"
    "Kinds: `sounds`, `personas`, `facts`, `triggers`, `grudges`.\n\n"
    "```\n"
    "!halbot admin status\n"
    "    → counts of live + tombstoned rows per kind.\n"
    "!halbot admin deleted <kind> [limit]\n"
    "    → list soft-deleted rows, newest first (default limit 25).\n"
    "!halbot admin undelete <kind> <id>\n"
    "    → restore one soft-deleted row.\n"
    "!halbot admin undelete-all <kind>\n"
    "    → restore every soft-deleted row of that kind.\n"
    "!halbot admin panic\n"
    "    → soft-clear ALL personas, facts, triggers, grudges.\n"
    "    (Sounds are NOT touched — too expensive to re-upload.)\n"
    "!halbot admin panic all\n"
    "    → same as above but ALSO soft-clears sounds.\n"
    "!halbot admin purge <kind> [--older-than=DAYS]\n"
    "    → PERMANENT delete of tombstoned rows. Irreversible.\n"
    "!halbot admin help\n"
    "```"
)


def _is_guild_owner(message: discord.Message) -> bool:
    guild = message.guild
    if not guild:
        return False
    return getattr(guild, "owner_id", None) == message.author.id


async def _admin_send(message: discord.Message, text: str) -> None:
    remaining = text
    while len(remaining) > 1990:
        split_at = remaining.rfind("\n", 0, 1990)
        if split_at == -1:
            split_at = 1990
        await message.channel.send(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        await message.channel.send(remaining)


async def _handle_admin_command(message: discord.Message) -> bool:
    """Owner-only command backdoor. Returns True if the message was an admin
    command (and therefore consumed — skip normal LLM flow).
    """
    content = (message.content or "").strip()
    if not content.lower().startswith(ADMIN_PREFIX):
        return False
    if not _is_guild_owner(message):
        await message.reply("⛔ Admin commands are owner-only.")
        return True
    argline = content[len(ADMIN_PREFIX):].strip()
    if not argline or argline.lower() in ("help", "?"):
        await _admin_send(message, ADMIN_HELP)
        return True
    parts = argline.split()
    cmd = parts[0].lower()
    rest = parts[1:]

    def _kind_or_error(tok: str) -> str | None:
        if tok in admin_kinds():
            return tok
        return None

    try:
        if cmd == "status":
            stats = admin_stats()
            lines = ["**Admin status** (live / tombstoned):"]
            for k, v in stats.items():
                lines.append(f"- **{k}**: {v['live']} live, {v['deleted']} recoverable")
            await _admin_send(message, "\n".join(lines))
            return True

        if cmd == "deleted":
            if not rest:
                await message.reply(f"Usage: `!halbot admin deleted <kind> [limit]`. Kinds: {admin_kinds()}")
                return True
            kind = _kind_or_error(rest[0])
            if not kind:
                await message.reply(f"Unknown kind `{rest[0]}`. Allowed: {admin_kinds()}")
                return True
            limit = 25
            if len(rest) > 1:
                try:
                    limit = max(1, min(200, int(rest[1])))
                except ValueError:
                    pass
            rows = admin_list_deleted(kind, limit)
            if not rows:
                await message.reply(f"No tombstoned {kind}.")
                return True
            lines = [f"**Tombstoned {kind} ({len(rows)}, newest first):**"]
            for r in rows:
                # Build a terse summary per row, stripping binary/audio blobs.
                summary = ", ".join(
                    f"{k}={v}"
                    for k, v in r.items()
                    if k not in ("audio",) and v not in (None, "")
                )
                lines.append(f"- `#{r['id']}` {summary}")
            await _admin_send(message, "\n".join(lines))
            return True

        if cmd == "undelete":
            if len(rest) < 2:
                await message.reply("Usage: `!halbot admin undelete <kind> <id>`")
                return True
            kind = _kind_or_error(rest[0])
            if not kind:
                await message.reply(f"Unknown kind `{rest[0]}`. Allowed: {admin_kinds()}")
                return True
            try:
                row_id = int(rest[1])
            except ValueError:
                await message.reply("Row id must be an integer.")
                return True
            ok = admin_undelete(kind, row_id)
            if ok:
                log.info("[admin] %s restored %s #%s", message.author, kind, row_id)
                await message.reply(f"✅ Restored `{kind}` #{row_id}.")
            else:
                await message.reply(f"No tombstoned `{kind}` #{row_id} found.")
            return True

        if cmd == "undelete-all":
            if not rest:
                await message.reply("Usage: `!halbot admin undelete-all <kind>`")
                return True
            kind = _kind_or_error(rest[0])
            if not kind:
                await message.reply(f"Unknown kind `{rest[0]}`. Allowed: {admin_kinds()}")
                return True
            n = admin_undelete_all(kind)
            log.info("[admin] %s restored ALL %s (%s rows)", message.author, kind, n)
            await message.reply(f"✅ Restored {n} `{kind}` row(s).")
            return True

        if cmd == "panic":
            include_sounds = bool(rest) and rest[0].lower() == "all"
            kinds = ["personas", "facts", "triggers", "grudges"]
            if include_sounds:
                kinds.append("sounds")
            result = admin_panic_clear(kinds)
            total = sum(result.values())
            log.warning("[admin] %s invoked panic (include_sounds=%s): %s",
                        message.author, include_sounds, result)
            lines = ["🚨 **PANIC** — soft-cleared:"]
            for k, n in result.items():
                lines.append(f"- {k}: {n}")
            lines.append(f"\n_All {total} row(s) recoverable via `!halbot admin undelete-all <kind>`._")
            await _admin_send(message, "\n".join(lines))
            return True

        if cmd == "purge":
            if not rest:
                await message.reply("Usage: `!halbot admin purge <kind> [--older-than=DAYS]`")
                return True
            kind = _kind_or_error(rest[0])
            if not kind:
                await message.reply(f"Unknown kind `{rest[0]}`. Allowed: {admin_kinds()}")
                return True
            days = None
            for tok in rest[1:]:
                if tok.startswith("--older-than="):
                    try:
                        days = int(tok.split("=", 1)[1])
                    except ValueError:
                        await message.reply("--older-than=DAYS must be an integer.")
                        return True
            n = admin_hard_purge(kind, days)
            log.warning("[admin] %s hard-purged %s (%s rows, older_than=%s)",
                        message.author, kind, n, days)
            scope = f" older than {days}d" if days is not None else ""
            await message.reply(f"🗑️ Permanently purged {n} tombstoned `{kind}`{scope} row(s). Irreversible.")
            return True

        await message.reply(f"Unknown admin command `{cmd}`. Try `!halbot admin help`.")
        return True
    except ValueError as e:
        await message.reply(f"⚠️ {e}")
        return True
    except Exception:
        log.exception("[admin] command failed: %r", argline)
        await message.reply("💥 Admin command errored — check logs.")
        return True


async def _fire_text_triggers(message: discord.Message) -> None:
    """Scan an incoming text message for keyword_text triggers and fire any that match.

    Fires independently of wake/mention gating — triggers are ambient reflexes.
    """
    try:
        rows = trigger_list("keyword_text")
    except Exception:
        log.exception("[trigger] list failed")
        return
    if not rows:
        return
    text_lower = (message.content or "").lower()
    if not text_lower:
        return
    guild = message.guild
    for r in rows:
        mv = (r.get("match_value") or "").lower().strip()
        if not mv:
            continue
        if mv not in text_lower:
            continue
        at = r.get("action_type")
        ap = r.get("action_payload") or ""
        tid = r.get("id")
        try:
            analytics.record(
                "hook_fired",
                user_id=message.author.id,
                guild_id=guild.id if guild else 0,
                target=f"trigger:keyword_text:{tid}",
                reason=mv,
            )
            if at == "reply":
                await message.channel.send(ap[:2000])
            elif at == "voice_play":
                if not guild:
                    continue
                session = voice_listeners.get(guild.id)
                if not session or not session.vc.is_connected():
                    log.info("[trigger #%s] voice_play skipped — not connected", tid)
                    continue
                row = db_get(ap)
                audio = None
                fmt = None
                if row:
                    audio = row["audio"]
                    fmt = detect_audio_format(audio)
                else:
                    try:
                        sounds = list(await guild.fetch_soundboard_sounds())
                    except Exception:
                        sounds = []
                    match = next((s for s in sounds if s.name == ap), None)
                    if match:
                        try:
                            audio = await match.read()
                            fmt = detect_audio_format(audio)
                        except Exception:
                            log.exception("[trigger #%s] live sound read failed", tid)
                if audio:
                    await session.play_sound(audio, fmt)
                    analytics.record(
                        "soundboard_play",
                        user_id=message.author.id,
                        guild_id=guild.id,
                        target=ap,
                        source="saved" if row else "live",
                        trigger="trigger",
                        bytes=len(audio),
                    )
                else:
                    log.info("[trigger #%s] sound %r not found", tid, ap)
            else:
                log.warning("[trigger #%s] unknown action_type %r", tid, at)
                continue
            trigger_mark_fired(tid)
        except Exception:
            log.exception("[trigger #%s] firing failed", tid)


async def on_message(message: discord.Message):
    log.info("Message received: %r from %s, mentions: %s", message.content, message.author, message.mentions)
    if message.author == client.user:
        return
    # Ambient reflexes: scan for keyword_text triggers regardless of whether
    # the bot is mentioned. These run independently of the main LLM flow.
    await _fire_text_triggers(message)
    # Owner-only admin backdoor for recovery / panic (handled before LLM gate).
    if await _handle_admin_command(message):
        return
    mentioned = client.user in message.mentions
    is_reply_to_bot = False
    ref = message.reference
    if not mentioned and ref is not None:
        ref_msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if ref_msg is None and ref.message_id:
            try:
                ref_msg = await message.channel.fetch_message(ref.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                ref_msg = None
        if ref_msg is not None and ref_msg.author == client.user:
            is_reply_to_bot = True
    if not mentioned and not is_reply_to_bot:
        return

    analytics.record(
        "mention",
        user_id=message.author.id,
        guild_id=message.guild.id if message.guild else 0,
        target="reply" if is_reply_to_bot else "mention",
    )

    # Show Discord's "Halbot is typing…" indicator while the LLM churns.
    # Manual enter/exit so we don't have to reindent the entire mention
    # body; typing() also auto-disappears when we send a message.
    _typing_ctx = message.channel.typing()
    _typing_entered = False
    try:
        await _typing_ctx.__aenter__()
        _typing_entered = True
    except Exception:
        log.debug("typing() enter failed", exc_info=True)

    # Track which sounds actually played this turn; if any, the final
    # embed gets the SoundboardActionsView attached.
    played_sounds: list[str] = []

    # Set when the LLM emits a {"action": "refuse", ...} — short-circuits
    # all subsequent action handling and renders a REFUSED embed instead.
    refusal_reason: str | None = None

    user_text = message.content
    for mention_str in [f"<@{client.user.id}>", f"<@!{client.user.id}>"]:
        user_text = user_text.replace(mention_str, "")
    user_text = user_text.strip()

    attachment_data = {}
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

    try:
        sounds = list(await guild.fetch_soundboard_sounds())
        sound_map = {s.name: s for s in sounds}
    except discord.HTTPException as e:
        log.error("Failed to fetch soundboard sounds: %s", e)
        await message.reply("Couldn't fetch the soundboard. Do I have the right permissions?")
        return

    channel_history = []
    history_attachments = {}
    async for msg in message.channel.history(limit=CHANNEL_HISTORY_LIMIT, before=message):
        text = msg.content
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
            continue
        channel_history.append({"role": "user", "content": f"{msg.author.display_name}: {text}"})
    channel_history.reverse()

    saved = db_list()
    saved_map = {s["name"]: s for s in saved}

    # Build voice context strings for the LLM prompt
    if guild:
        vc_names = [vc.name for vc in guild.voice_channels]
        voice_channels_str = "\n".join(f"- {n}" for n in vc_names) if vc_names else "(none)"
        session = voice_listeners.get(guild.id)
        if session and session.vc.is_connected():
            voice_status_str = f'Connected to "{session.vc.channel.name}". Listening for wake word "Halbot".'
        else:
            voice_status_str = "Not connected to any voice channel."
    else:
        voice_channels_str = "(unknown)"
        voice_status_str = "Not connected to any voice channel."

    _llm_t0 = time.monotonic()
    actions = await asyncio.to_thread(
        parse_intent,
        user_text, sounds, saved, channel_history,
        attachment_info or None,
        guild=guild,
        voice_channels_str=voice_channels_str,
        voice_status_str=voice_status_str,
    )
    analytics.record(
        "llm_call",
        user_id=message.author.id,
        guild_id=guild.id,
        target="parse_intent",
        latency_ms=int((time.monotonic() - _llm_t0) * 1000),
        action_count=len(actions) if isinstance(actions, list) else 0,
    )
    replies = []

    def _reply(default: str, intent: dict = None) -> str:
        llm_msg = (intent or {}).get("message")
        if llm_msg:
            return f"{llm_msg}\n\n{default}" if default else llm_msg
        return default

    for intent in actions:
        action = intent.get("action")
        analytics.record(
            "cmd_invoke",
            user_id=message.author.id,
            guild_id=guild.id,
            target=str(action or "unknown"),
        )

        if action == "error":
            raw = intent.get("message", "Something went wrong.")
            customized = await customize_response_async(raw, context="bot-wide LLM error")
            replies.append(customized)
            break

        elif action == "list":
            names = intent.get("names", [])
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

            files_to_process = {}
            if target_filename:
                if target_filename in attachment_data:
                    files_to_process[target_filename] = attachment_data[target_filename]
                elif target_filename in history_attachments:
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
            replies.append(intent.get("message", "What settings would you like for the effect?"))

        elif action == "effect_apply":
            target_name = intent.get("name", "")
            effect_type = intent.get("effect", "")
            params = intent.get("params", {})
            save_as = intent.get("save_as", f"{target_name}-{effect_type}")

            if effect_type not in SUPPORTED_EFFECTS:
                replies.append(f"Unknown effect `{effect_type}`. Supported: {', '.join(sorted(SUPPORTED_EFFECTS))}.")
                continue

            source = db_get(target_name) if target_name else None
            if not source:
                replies.append(f'No saved sound called "{target_name}".')
                continue

            if source.get("parent_id"):
                original_id = source["parent_id"]
                existing_effects = json.loads(source.get("effects") or "[]")
            else:
                original_id = source["id"]
                existing_effects = []

            new_effect = {"type": effect_type, "params": params}
            combined_effects = existing_effects + [new_effect]

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

            fmt = detect_audio_format(processed)
            ok, reason, duration = validate_audio(processed, f"{save_as}.{fmt}")
            if not ok:
                replies.append(f"The processed audio is invalid: {reason}")
                continue

            try:
                db_save(
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

        elif action == "persona_clear":
            confirm_msg = intent.get("message")
            n = persona_clear()
            log.info("Persona clear by %s: %s rows", message.author, n)
            if confirm_msg:
                replies.append(f"{confirm_msg}\n\n_Cleared {n} directive(s). Recoverable by the server owner via `!halbot admin undelete-all personas`._")
            else:
                replies.append(f"Cleared {n} directive(s). (Recoverable by the server owner.)")

        elif action == "persona_list":
            personas = persona_list()
            if not personas:
                replies.append("No behavior directives are active. I'm using my default personality.")
            else:
                lines = []
                for p in personas:
                    lines.append(f"- [#{p['id']}] \"{p['directive']}\" (set by {p['set_by']} on {p['created_at']})")
                replies.append(_reply(f"**Active behavior directives ({len(personas)}):**\n" + "\n".join(lines), intent))

        elif action == "fact_add":
            subject = intent.get("subject", "")
            claim = intent.get("claim", "")
            confirm_msg = intent.get("message", "Noted.")
            try:
                fid = fact_add(subject, claim, str(message.author))
                log.info("Fact #%s added by %s: %s — %s", fid, message.author, subject, claim)
                replies.append(confirm_msg)
            except ValueError as e:
                replies.append(str(e))

        elif action == "fact_remove":
            fid = intent.get("id")
            confirm_msg = intent.get("message", "Forgotten.")
            if fid is None:
                replies.append("No fact id specified.")
            elif fact_remove(int(fid)):
                log.info("Fact #%s removed by %s", fid, message.author)
                replies.append(confirm_msg)
            else:
                replies.append(f"Couldn't find fact #{fid}.")

        elif action == "fact_list":
            subject = intent.get("subject") or None
            rows = fact_list(subject)
            if not rows:
                scope = f" about {subject}" if subject else ""
                replies.append(f"No facts{scope} recorded.")
            else:
                lines = [f"- [#{r['id']}] **{r['subject']}**: {r['claim']} _(by {r['set_by']} on {r['created_at']})_"
                         for r in rows]
                header = f"**Facts{' about ' + subject if subject else ''} ({len(rows)}):**\n"
                replies.append(_reply(header + "\n".join(lines), intent))

        elif action == "fact_clear":
            subject = intent.get("subject") or None
            confirm_msg = intent.get("message")
            n = fact_clear(subject)
            log.info("Fact clear by %s (subject=%r): %s rows", message.author, subject, n)
            if confirm_msg:
                replies.append(f"{confirm_msg}\n\n_Cleared {n} fact(s)._")
            else:
                replies.append(f"Cleared {n} fact(s).")

        elif action == "trigger_add":
            mk = intent.get("match_kind", "")
            mv = intent.get("match_value", "")
            at = intent.get("action_type", "")
            ap = intent.get("action_payload", "")
            confirm_msg = intent.get("message", "Wired up.")
            try:
                tid = trigger_add(mk, mv, at, ap, str(message.author))
                log.info("Trigger #%s added by %s: %s=%r → %s:%r",
                         tid, message.author, mk, mv, at, ap)
                replies.append(confirm_msg)
            except ValueError as e:
                replies.append(str(e))

        elif action == "trigger_remove":
            tid = intent.get("id")
            confirm_msg = intent.get("message", "Unwired.")
            if tid is None:
                replies.append("No trigger id specified.")
            elif trigger_remove(int(tid)):
                log.info("Trigger #%s removed by %s", tid, message.author)
                replies.append(confirm_msg)
            else:
                replies.append(f"Couldn't find trigger #{tid}.")

        elif action == "trigger_list":
            mk = intent.get("match_kind") or None
            rows = trigger_list(mk)
            if not rows:
                scope = f" of kind `{mk}`" if mk else ""
                replies.append(f"No triggers{scope} installed.")
            else:
                lines = []
                for r in rows:
                    last = r.get("last_fired_at") or "never"
                    lines.append(
                        f"- [#{r['id']}] `{r['match_kind']}`=\"{r['match_value']}\" → "
                        f"**{r['action_type']}**: {r['action_payload']}  "
                        f"_(by {r['set_by']}, fired {r.get('fire_count', 0)}×, last: {last})_"
                    )
                header = f"**Triggers ({len(rows)}):**\n"
                replies.append(_reply(header + "\n".join(lines), intent))

        elif action == "trigger_clear":
            mk = intent.get("match_kind") or None
            confirm_msg = intent.get("message")
            n = trigger_clear(mk)
            log.info("Trigger clear by %s (kind=%r): %s rows", message.author, mk, n)
            if confirm_msg:
                replies.append(f"{confirm_msg}\n\n_Cleared {n} trigger(s)._")
            else:
                replies.append(f"Cleared {n} trigger(s).")

        elif action == "grudge_set":
            tname = intent.get("target_name", "")
            polarity = intent.get("polarity", 0)
            note = intent.get("note", "") or ""
            confirm_msg = intent.get("message", "Relationship logged.")
            try:
                gid = grudge_set(tname, polarity, note, str(message.author))
                log.info("Grudge #%s %s=%s by %s (note=%r)",
                         gid, tname, polarity, message.author, note)
                replies.append(confirm_msg)
            except ValueError as e:
                replies.append(str(e))

        elif action == "grudge_remove":
            gid = intent.get("id")
            confirm_msg = intent.get("message", "Cleared.")
            if gid is None:
                replies.append("No grudge id specified.")
            elif grudge_remove(int(gid)):
                log.info("Grudge #%s removed by %s", gid, message.author)
                replies.append(confirm_msg)
            else:
                replies.append(f"Couldn't find grudge #{gid}.")

        elif action == "grudge_list":
            rows = grudge_list()
            if not rows:
                replies.append("No grudges or devotions logged. I love everyone equally.")
            else:
                lines = []
                for r in rows:
                    pol = r["polarity"]
                    tag = f"+{pol}" if pol > 0 else str(pol)
                    note = f" — _{r['note']}_" if r.get("note") else ""
                    lines.append(f"- [#{r['id']}] **{r['target_name']}** ({tag}){note}  _(by {r['set_by']})_")
                header = f"**Relationships ({len(rows)}):**\n"
                replies.append(_reply(header + "\n".join(lines), intent))

        elif action == "grudge_clear":
            confirm_msg = intent.get("message")
            n = grudge_clear()
            log.info("Grudge clear by %s: %s rows", message.author, n)
            if confirm_msg:
                replies.append(f"{confirm_msg}\n\n_Cleared {n} relationship(s)._")
            else:
                replies.append(f"Cleared {n} relationship(s).")

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

        elif action == "voice_join":
            if not VOICE_RECV_AVAILABLE:
                replies.append("Voice features are not available — install `discord-ext-voice-recv` and `faster-whisper`.")
                continue

            channel_name = intent.get("channel", "")
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

            cancel_voice_idle_timer(guild.id)
            existing = voice_listeners.pop(guild.id, None)
            if existing:
                existing.stop()
                try:
                    await existing.vc.disconnect()
                except Exception:
                    pass

            try:
                vc = await vc_channel.connect(cls=HalbotVoiceRecvClient)
                listener = VoiceListener(vc, handle_voice_command)
                session = VoiceSession(
                    listener=listener,
                    message_sink=VoiceChatSink(vc_channel),
                    history=voice_history_load(guild.id),
                )
                voice_listeners[guild.id] = session

                import threading
                threading.Thread(target=load_whisper, daemon=True).start()
                if _preload_tts_engine is not None:
                    _preload_tts_engine()

                listener.start()
                if not _channel_has_humans(vc_channel):
                    schedule_voice_idle_timer(guild.id)
                analytics.record(
                    "voice_join",
                    user_id=message.author.id,
                    guild_id=guild.id,
                    target=vc_channel.name,
                    channel_id=vc_channel.id,
                )
                replies.append(
                    _reply(f'Joined **{vc_channel.name}**. Say "Halbot" followed by a command!', intent)
                )
            except Exception as e:
                log.exception("Failed to join voice channel %s", vc_channel.name)
                replies.append(f"Failed to join **{vc_channel.name}**: {e}")

        elif action == "voice_leave":
            cancel_voice_idle_timer(guild.id)
            session = voice_listeners.pop(guild.id, None)
            if session:
                session.stop()
                try:
                    await session.vc.disconnect()
                except Exception:
                    pass
                _maybe_unload_whisper()
                replies.append(_reply("Left the voice channel.", intent))
            else:
                replies.append("I'm not in a voice channel.")

        elif action == "voice_play":
            session = voice_listeners.get(guild.id)
            if not session or not session.vc.is_connected():
                replies.append("I need to be in a voice channel first. Ask me to join one!")
                continue

            name = intent.get("name", "")
            row = db_get(name) if name else None
            if row:
                fmt = detect_audio_format(row["audio"])
                await session.play_sound(row["audio"], fmt)
                analytics.record(
                    "soundboard_play",
                    user_id=message.author.id,
                    guild_id=guild.id,
                    target=name,
                    source="saved",
                    bytes=len(row["audio"]) if row.get("audio") else 0,
                )
                played_sounds.append(name)
                llm_msg = (intent or {}).get("message")
                if llm_msg:
                    replies.append(llm_msg)
                continue

            live = sound_map.get(name)
            if live:
                try:
                    audio = await live.read()
                    fmt = detect_audio_format(audio)
                    await session.play_sound(audio, fmt)
                    analytics.record(
                        "soundboard_play",
                        user_id=message.author.id,
                        guild_id=guild.id,
                        target=name,
                        source="live",
                        bytes=len(audio) if audio else 0,
                    )
                    played_sounds.append(name)
                    llm_msg = (intent or {}).get("message")
                    if llm_msg:
                        replies.append(llm_msg)
                except Exception:
                    log.exception("Failed to read live sound %s for voice playback", name)
                    replies.append(f"Failed to play **{name}**.")
                continue

            replies.append(f'Couldn\'t find a sound called "{name}".')

        elif action == "stats":
            try:
                rollup = await asyncio.to_thread(analytics.compute_dashboard_stats)
                events = await asyncio.to_thread(
                    analytics.fetch_recent_events, 60, 3000, guild.id
                )
            except Exception:
                log.exception("stats fetch failed")
                replies.append(_reply("Couldn't pull stats right now.", intent))
                continue
            # Resolve user_ids → display names via the guild cache; fall back
            # to fetch_member for active uids not in cache (bounded to 25 to
            # avoid rate-limit).
            uid_to_name: dict[int, str] = {}
            seen_uids: list[int] = []
            for e in events:
                uid = e.get("user_id") or 0
                if uid and uid not in uid_to_name:
                    m = guild.get_member(uid)
                    if m:
                        uid_to_name[uid] = m.display_name
                    else:
                        seen_uids.append(uid)
                        uid_to_name[uid] = f"user_{uid}"
            # Best-effort: enrich up to 25 unknown active users via API.
            missing = [u for u in seen_uids if uid_to_name.get(u, "").startswith("user_")][:25]
            for uid in missing:
                try:
                    m = await guild.fetch_member(uid)
                    if m:
                        uid_to_name[uid] = m.display_name
                except Exception:
                    pass
            events_block = format_events_for_prompt(events, uid_to_name)
            rollup_block = _format_stats_for_discord(rollup)
            now_unix = int(time.time())
            try:
                answer = await answer_stats_question_async(
                    user_text,
                    rollup_block=rollup_block,
                    events_block=events_block,
                    now_unix=now_unix,
                )
            except Exception:
                log.exception("answer_stats_question failed")
                answer = rollup_block
            # Do NOT prepend intent["message"] — the stats answer is already
            # persona-shaped inside answer_stats_question, and a second
            # persona pass on top tends to leak refusals/haikus that
            # crowd out the real numbers.
            final = answer
            # Force text delivery (bypass voice-TTS path of _deliver): stats
            # output is markdown tables/lists, useless spoken aloud.
            remaining = final
            while len(remaining) > 2000:
                split_at = remaining.rfind("\n", 0, 2000)
                if split_at == -1:
                    split_at = 2000
                await message.channel.send(remaining[:split_at])
                remaining = remaining[split_at:].lstrip("\n")
            if remaining:
                await message.channel.send(remaining)
            continue

        elif action == "reply":
            msg = (intent.get("message") or "").strip()
            replies.append(msg or "...")

        elif action == "refuse":
            reason = (intent.get("reason") or intent.get("message") or "").strip()
            if not reason:
                reason = "Not doing that. Stays in character."
            refusal_reason = reason
            analytics.record(
                "hook_fired",
                user_id=message.author.id,
                guild_id=guild.id,
                target="persona.refuse",
                reason=reason[:200],
            )
            log.info("[persona] refused request from %s: %r", message.author, reason[:120])
            # Persona refusal is terminal for the turn — drop everything else.
            replies = []
            played_sounds.clear()
            break

        else:
            log.warning("Unhandled action %r in text-channel response; falling back to message field", action)
            replies.append(intent.get("message", "I didn't understand that."))

    if refusal_reason is not None:
        # Persona refusal short-circuits everything: no replies list to
        # join, no voice playback, no customize pass. Reason is already
        # in-persona voice from the LLM.
        session = voice_listeners.get(guild.id) if guild else None
        voice_connected = bool(session and session.vc.is_connected())
        if voice_connected:
            await _deliver(message, refusal_reason)
        else:
            await send_halbot_reply(
                message, payload=refusal_payload(refusal_reason),
                reply_to=message,
            )
    elif replies:
        joined = "\n\n".join(replies)
        # Voice-connected path: still TTS the reply (plain text). The
        # voice-card flows land in phase 5 of plan 014.
        session = voice_listeners.get(guild.id) if guild else None
        voice_connected = bool(session and session.vc.is_connected())
        if voice_connected:
            await _deliver(message, joined)
        else:
            await _dispatch_text_embed(
                message, joined, played_sounds=played_sounds,
                actions=actions,
            )

    if _typing_entered:
        try:
            await _typing_ctx.__aexit__(None, None, None)
        except Exception:
            log.debug("typing() exit failed", exc_info=True)


async def _dispatch_text_embed(
    message: discord.Message,
    body_text: str,
    *,
    played_sounds: list[str],
    actions: list[dict],
) -> None:
    """Render the joined reply string as a Halbot embed.

    Runs one LLM pass to split the plaintext into (subtext, body); falls
    back to a templated subtext if the model errors out. Soundboard-play
    turns get the Stop/Replay/Louder view attached.

    Phase 1 scope — more per-action polish (structured fields like
    From/Voice/Requested, upload flow's Slot/Size/Length/Emoji grid,
    etc.) lands in later phases of plan 014.
    """
    # Short-circuit empty body — shouldn't happen, but defensive.
    if not body_text.strip():
        return

    # Build a resolution hint from the actual intent actions so the LLM
    # can produce an accurate subtext without re-guessing.
    action_names = [a.get("action") for a in actions if isinstance(a, dict) and a.get("action")]
    hint_parts: list[str] = []
    if played_sounds:
        hint_parts.append(f"played {', '.join(played_sounds)}")
    if action_names:
        hint_parts.append("actions: " + ", ".join(action_names))
    resolution_hint = " · ".join(hint_parts)

    try:
        subtext, body = await customize_response_rich_async(
            body_text, resolution_hint=resolution_hint,
        )
    except Exception:
        log.exception("customize_response_rich_async failed; falling back")
        subtext = resolution_hint or "Halbot handled your request"
        body = body_text

    # Truncate description to Discord's 4096-char limit; overflow goes
    # as a follow-up text message to avoid dropping content silently.
    EMBED_DESC_MAX = 4000
    overflow: str | None = None
    if len(body) > EMBED_DESC_MAX:
        overflow = body[EMBED_DESC_MAX:]
        body = body[:EMBED_DESC_MAX] + "…"

    if played_sounds:
        title = f"▶ Playing {played_sounds[0]}"
        if len(played_sounds) > 1:
            title += f" (+{len(played_sounds) - 1} more)"
        mode = Mode.SOUNDBOARD
        view: discord.ui.View | None = SoundboardActionsView()
    else:
        title = "Halbot"
        mode = Mode.ACTIONED
        view = None

    payload = ReplyPayload(
        mode=mode, title=title, description=body, subtext=subtext,
    )
    await send_halbot_reply(message, payload=payload, view=view, reply_to=message)

    if overflow:
        # Chunk overflow at 2000-char boundaries (Discord content limit).
        remaining = overflow
        while len(remaining) > 2000:
            split_at = remaining.rfind("\n", 0, 2000)
            if split_at == -1:
                split_at = 2000
            await message.channel.send(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            await message.channel.send(remaining)


def _format_stats_for_discord(stats: dict) -> str:
    """Format analytics.compute_dashboard_stats() output as Discord markdown."""
    sb = stats.get("soundboard", {}) or {}
    vp = stats.get("voice_playback", {}) or {}
    ww = stats.get("wake_word", {}) or {}
    tts = stats.get("tts", {}) or {}
    stt = stats.get("stt", {}) or {}
    llm = stats.get("llm", {}) or {}

    storage_mb = (sb.get("storage_bytes", 0) or 0) / (1024 * 1024)

    lines = ["**📊 Halbot stats**"]
    lines.append(
        f"**Soundboard:** {sb.get('sounds_backed_up', 0)} saved "
        f"({storage_mb:.1f} MB), {sb.get('new_since_last', 0)} new in last 24h"
    )
    lines.append(
        f"**Playback:** {vp.get('played_today', 0)} today · "
        f"{vp.get('played_all_time', 0)} all-time"
    )
    lines.append(
        f"**Wake word:** {ww.get('detections_today', 0)} today · "
        f"{ww.get('detections_all_time', 0)} all-time"
        + (f" ({ww.get('false_positives_today', 0)} false positives today)"
           if ww.get('false_positives_today') else "")
    )
    lines.append(
        f"**LLM:** {llm.get('requests_today', 0)} calls today · "
        f"avg {llm.get('response_avg_ms', 0)} ms · p95 {llm.get('response_p95_ms', 0)} ms"
    )
    lines.append(
        f"**TTS:** {tts.get('count_today', 0)} today · "
        f"avg {tts.get('avg_ms', 0)} ms · p95 {tts.get('p95_ms', 0)} ms"
    )
    if stt.get("count_today") or stt.get("avg_ms"):
        lines.append(
            f"**STT:** {stt.get('count_today', 0)} today · "
            f"avg {stt.get('avg_ms', 0)} ms · p95 {stt.get('p95_ms', 0)} ms"
        )
    if stats.get("mock"):
        lines.append("_(analytics DB unavailable — values may be stale)_")
    return "\n".join(lines)


def _resolve_token() -> str | None:
    """Resolve DISCORD_TOKEN from DPAPI-encrypted HKLM registry. No env fallback."""
    from . import secrets as secrets_mod
    return secrets_mod.get_secret("DISCORD_TOKEN")


async def run() -> None:
    """Entrypoint called from halbot.daemon. Initializes DB, builds client, runs until stopped."""
    import discord as _discord

    token = _resolve_token()
    if not token:
        _set_discord_state("NO_TOKEN")
        log.error("DISCORD_TOKEN not set; Discord subsystem idle. Run `halbot-daemon setup set-secret DISCORD_TOKEN <value>`.")
        return
    db_init()
    c = build_client()
    _set_discord_state("CONNECTING")
    try:
        await c.start(token)
    except _discord.LoginFailure:
        _set_discord_state("TOKEN_INVALID")
        log.error("Discord login failed: invalid token")
    except Exception:
        _set_discord_state("DISCONNECTED")
        log.exception("Discord client crashed")
    finally:
        if not c.is_closed():
            await c.close()
        _set_discord_state("DISCONNECTED")
