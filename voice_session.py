import asyncio
import logging
import re

from db import _env_bool, db_get, db_list, voice_history_append, voice_history_load, VOICE_HISTORY_TURNS
from audio import detect_audio_format
from llm import (
    check_wake_word, customize_response_async,
    parse_voice_combined, parse_voice_intent,
)

log = logging.getLogger("halbot")

VOICE_LLM_COMBINE_CALLS = _env_bool("VOICE_LLM_COMBINE_CALLS", True)

try:
    VOICE_IDLE_TIMEOUT_SECONDS = int(__import__("os").getenv("VOICE_IDLE_TIMEOUT_SECONDS", "1800"))
except (ValueError, TypeError):
    VOICE_IDLE_TIMEOUT_SECONDS = 1800

# Optional voice-recv module
try:
    from voice import (
        VoiceListener,
        VoiceSession,
        TextChannelSink,
        VoiceChatSink,
        LogOnlySink,
        HalbotVoiceRecvClient,
        VOICE_RECV_AVAILABLE,
        load_whisper,
        unload_whisper,
    )
except ImportError:
    VoiceListener = None
    VoiceSession = None
    TextChannelSink = None
    VoiceChatSink = None
    LogOnlySink = None
    HalbotVoiceRecvClient = None
    VOICE_RECV_AVAILABLE = False
    load_whisper = None
    unload_whisper = None

# Optional TTS module
try:
    import tts as _tts_module  # noqa: F401
    from tts import get_engine as _get_tts_engine, unload_engine as _unload_tts_engine, preload_engine_async as _preload_tts_engine
except ImportError:
    _get_tts_engine = None
    _unload_tts_engine = None
    _preload_tts_engine = None

# Active voice sessions per guild (guild_id → VoiceSession)
voice_listeners: dict[int, "VoiceSession"] = {}

# Saved sessions for reconnect after restart: guild_id → (vc_channel_id, sink_spec)
_voice_reconnect: dict[int, tuple] = {}

# Pending idle-disconnect tasks per guild
_voice_idle_tasks: dict[int, asyncio.Task] = {}

# Discriminated-union sink_spec shapes:
#   ("voice_chat",)               -> VoiceChatSink(vc_channel)
#   ("text_channel", channel_id)  -> TextChannelSink(channel)
#   ("log_only",)                 -> LogOnlySink()
SinkSpec = tuple

_MD_STRIP_RE = re.compile(r"[*_`~]+")
_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
_URL_RE = re.compile(r"https?://\S+")


def _sanitize_for_speech(text: str) -> str:
    """Strip Discord markdown/custom-emoji/URLs so TTS engines can read the text."""
    text = _EMOJI_RE.sub(r"\1", text)
    text = _URL_RE.sub("link", text)
    text = _MD_STRIP_RE.sub("", text)
    return text.strip()


async def _speak(session, text: str) -> bool:
    """Synthesize text and play it in the session's voice channel.

    Returns True if playback started, False if TTS unavailable or synthesis failed.
    """
    if _get_tts_engine is None:
        return False
    engine = _get_tts_engine()
    if engine is None:
        return False
    clean = _sanitize_for_speech(text)
    if not clean:
        return False
    try:
        audio, fmt = await asyncio.to_thread(engine.synth, clean)
    except Exception:
        log.exception("[tts] Synthesis failed; falling back to text")
        return False
    try:
        await session.play_sound(audio, fmt)
    except Exception:
        log.exception("[tts] Playback failed; falling back to text")
        return False
    log.info("[tts] Spoke (%d chars): %r", len(clean), clean[:120])
    return True


async def _voice_feedback(session, sink, text: str) -> None:
    """Deliver voice-session feedback: speak if TTS available, else send to sink."""
    log.info("[voice-feedback] %s", text)
    if await _speak(session, text):
        return
    await sink.send(text)


def _maybe_unload_whisper() -> None:
    """Free whisper + TTS VRAM once the last voice session ends."""
    if voice_listeners:
        return
    import threading
    if unload_whisper is not None:
        threading.Thread(target=unload_whisper, daemon=True).start()
    if _unload_tts_engine is not None:
        threading.Thread(target=_unload_tts_engine, daemon=True).start()


def _channel_has_humans(channel) -> bool:
    return channel is not None and any(not m.bot for m in channel.members)


async def _voice_idle_disconnect(guild_id: int, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    session = voice_listeners.get(guild_id)
    if not session:
        return
    if _channel_has_humans(session.vc.channel):
        return
    voice_listeners.pop(guild_id, None)
    channel_name = session.vc.channel.name if session.vc.channel else "?"
    sink = session.message_sink
    session.stop()
    try:
        await session.vc.disconnect()
    except Exception:
        pass
    _maybe_unload_whisper()
    log.info("[voice] Idle-disconnect from %s (guild %s) after %ds", channel_name, guild_id, delay)
    await sink.send(
        f"\U0001f44b Left **{channel_name}** — empty for {delay // 60} min."
    )


def cancel_voice_idle_timer(guild_id: int) -> None:
    task = _voice_idle_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


def schedule_voice_idle_timer(guild_id: int) -> None:
    if VOICE_IDLE_TIMEOUT_SECONDS <= 0:
        return
    cancel_voice_idle_timer(guild_id)
    log.info("[voice] Scheduling idle-disconnect for guild %s in %ds",
             guild_id, VOICE_IDLE_TIMEOUT_SECONDS)
    _voice_idle_tasks[guild_id] = asyncio.create_task(
        _voice_idle_disconnect(guild_id, VOICE_IDLE_TIMEOUT_SECONDS)
    )


def _sink_to_spec(sink) -> SinkSpec:
    if isinstance(sink, VoiceChatSink):
        return ("voice_chat",)
    if isinstance(sink, TextChannelSink):
        return ("text_channel", getattr(sink.channel, "id", None))
    if isinstance(sink, LogOnlySink):
        return ("log_only",)
    log.warning("[voice] Unknown sink type %s — falling back to log_only spec",
                type(sink).__name__)
    return ("log_only",)


def _spec_to_sink(spec: SinkSpec, guild, vc_channel):
    """Rebuild a sink from a stored spec. Falls back to LogOnlySink on lookup failure."""
    kind = spec[0] if spec else None
    if kind == "voice_chat":
        return VoiceChatSink(vc_channel)
    if kind == "text_channel":
        tc_id = spec[1] if len(spec) > 1 else None
        channel = guild.get_channel(tc_id) if tc_id else None
        if channel is None:
            log.warning("[voice] Reconnect sink: text channel %s gone; using log-only", tc_id)
            return LogOnlySink()
        return TextChannelSink(channel)
    if kind == "log_only":
        return LogOnlySink()
    log.warning("[voice] Reconnect sink: unknown spec %r; using log-only", spec)
    return LogOnlySink()


def snapshot_voice_state() -> None:
    """Capture active voice sessions into _voice_reconnect before shutdown."""
    for gid, session in list(voice_listeners.items()):
        if not session.vc.is_connected():
            continue
        sink_spec = _sink_to_spec(session.message_sink)
        _voice_reconnect[gid] = (session.vc.channel.id, sink_spec)
        log.info("[voice] Snapshotted session for guild %s: vc=%s sink=%s",
                 gid, session.vc.channel.id, sink_spec)


async def handle_voice_command(guild, user_id, transcript):
    """Callback from VoiceListener with a raw STT transcript.

    Owns wake-word detection: single combined call or two sequential calls
    depending on VOICE_LLM_COMBINE_CALLS.
    """
    session = voice_listeners.get(guild.id)
    if not session:
        return
    sink = session.message_sink
    history = list(session.history)

    if VOICE_LLM_COMBINE_CALLS:
        try:
            import discord
            sounds = list(await guild.fetch_soundboard_sounds())
        except Exception:
            sounds = []
        saved = db_list()
        status, actions = await asyncio.to_thread(
            parse_voice_combined, transcript, sounds, saved, history
        )
        if status == "no_wake":
            log.info("[voice] no wake word in: %r", transcript)
            return
        if status == "error":
            log.warning("[voice] combined LLM call errored on: %r", transcript)
            await _voice_feedback(session, sink, "Voice command processing failed.")
            return
        if not actions:
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
            import discord
            sounds = list(await guild.fetch_soundboard_sounds())
        except Exception:
            sounds = []
        saved = db_list()
        actions = await asyncio.to_thread(
            parse_voice_intent, command, sounds, saved, history
        )

    saved_map = {s["name"]: s for s in saved}
    sound_map = {s.name: s for s in sounds}
    member = guild.get_member(user_id)
    user_name = member.display_name if member else f"user {user_id}"

    def _record(bot_response: str) -> None:
        if VOICE_HISTORY_TURNS <= 0:
            return
        turn = {
            "user_display_name": user_name,
            "transcript": transcript,
            "bot_response": bot_response,
        }
        session.history.append(turn)
        while len(session.history) > VOICE_HISTORY_TURNS:
            session.history.pop(0)
        try:
            voice_history_append(guild.id, user_name, transcript, bot_response)
        except Exception:
            log.exception("[voice-history] persist failed")

    for intent in actions:
        action = intent.get("action")

        if action == "voice_play":
            name = intent.get("name", "")
            row = db_get(name) if name else None
            if row:
                fmt = detect_audio_format(row["audio"])
                await session.play_sound(row["audio"], fmt)
                _record(f"(played sound: {name})")
                return

            live = sound_map.get(name)
            if live:
                try:
                    audio = await live.read()
                    fmt = detect_audio_format(audio)
                    await session.play_sound(audio, fmt)
                    _record(f"(played sound: {name})")
                except Exception:
                    log.exception("Failed to read live sound %s for voice playback", name)
                    _record(f"(failed to play: {name})")
                return

            customized = await customize_response_async(
                f'Couldn\'t find a sound called "{name}".',
                context="voice command: sound lookup miss",
            )
            await _voice_feedback(session, sink, customized)
            _record(customized)

        elif action == "unknown":
            msg = intent.get("message", "I didn't understand that voice command.")
            customized = await customize_response_async(msg, context="voice command failure")
            await _voice_feedback(session, sink, customized)
            _record(customized)
