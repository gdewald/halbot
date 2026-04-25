import asyncio
import contextvars
import logging
import re
import time

# Propagates the VAD-capture time of the current wake utterance through
# the async call chain so _speak() can refuse to play a late reply.
# Per-task — each guild's handle_voice_command invocation sees its own.
_wake_captured_at: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "wake_captured_at", default=None,
)

import discord

from . import analytics
from .bot_ui import EmbedField, Mode, ReplyPayload, send_halbot_reply
from .db import (
    db_get, db_list, trigger_list, trigger_mark_fired,
    voice_history_append, voice_reconnect_clear, voice_reconnect_set,
    VOICE_HISTORY_TURNS,
)
from .audio import detect_audio_format
from .llm import (
    answer_voice_conversation_async, customize_response_async,
    parse_voice_intent,
)

log = logging.getLogger("halbot")

from . import config as _config

# VOICE_LLM_COMBINE_CALLS config is retained in config.py for schema stability
# but no longer consulted — wake detection is pure STT substring matching,
# intent parsing is a single parse_voice_intent call.

# Cheap substring prefilter: if a transcript contains NONE of these tokens we
# skip the combined LLM call entirely and treat as no_wake. Whisper routinely
# mis-transcribes "robot" as these variants, so we match loosely. Without
# this prefilter every ambient utterance ("Thank you", "Oh jeez") burned a
# 200-token ollama call each and stacked up until read-timeout (120s), which
# cascaded into audible "Voice command processing failed" TTS feedback that
# the bot's own mic re-captured → feedback loop.
def _wake_tokens() -> list[str]:
    """Read the wake-variant dictionary from sqlite.

    Substring scan with ~50 tokens is microseconds; the sqlite read is
    too. Skip the cache so admin slash-command edits are picked up
    on the next utterance with no invalidation dance.
    """
    try:
        from .db import wake_variant_tokens
        toks = wake_variant_tokens()
    except Exception:
        toks = []
    if not toks:
        # Fallback: if the table is somehow empty, keep wake detection
        # alive with the historical seed list rather than going silent.
        toks = [
            "robot", "ro bot", "ro-bot", "roebot", "roe bot",
            "robots", "roboto", "robo ", "row bot", "rowbot",
        ]
    return toks


def _has_wake_candidate(transcript: str) -> bool:
    """True if transcript contains a wake-word token. Substring scan only —
    no LLM arbitration. Authoritative wake signal for the voice path."""
    if not transcript:
        return False
    t = transcript.lower()
    return any(tok in t for tok in _wake_tokens())


def _extract_command(transcript: str) -> str:
    """Strip the first wake-word token + leading punctuation, return the
    remainder as the command text. Called only after _has_wake_candidate
    has returned True."""
    tl = transcript.lower()
    # Pick the earliest-occurring token so "robot, tell the robots" doesn't
    # chop the wrong word.
    best_idx = -1
    best_tok = ""
    for tok in _wake_tokens():
        idx = tl.find(tok)
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
            best_tok = tok
    if best_idx < 0:
        return transcript.strip()
    tail = transcript[best_idx + len(best_tok):]
    return tail.lstrip(" ,.!?;:-\t\n").strip()

try:
    VOICE_IDLE_TIMEOUT_SECONDS = int(_config.get("voice_idle_timeout_seconds"))
except (ValueError, TypeError):
    VOICE_IDLE_TIMEOUT_SECONDS = 1800

# Optional voice-recv module
try:
    from .voice import (
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
    from . import tts as _tts_module  # noqa: F401
    from .tts import get_engine as _get_tts_engine, unload_engine as _unload_tts_engine, preload_engine_async as _preload_tts_engine
except ImportError:
    _get_tts_engine = None
    _unload_tts_engine = None
    _preload_tts_engine = None

# Active voice sessions per guild (guild_id → VoiceSession)
voice_listeners: dict[int, "VoiceSession"] = {}

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

    # Third staleness gate — right before synthesis. If the user's wake
    # utterance was captured too long ago, don't bother speaking the
    # reply. By this point the conversation has moved on; a late TTS is
    # jarring and indistinguishable from a hallucination.
    captured_at = _wake_captured_at.get()
    if captured_at is not None:
        from .voice import STALE_PRE_PLAY_SECONDS
        age = time.monotonic() - captured_at
        if age > STALE_PRE_PLAY_SECONDS:
            log.warning(
                "[tts] dropping stale reply (age=%.1fs > %.1fs): %r",
                age, STALE_PRE_PLAY_SECONDS, clean[:80],
            )
            return False

    _tts_t0 = time.monotonic()
    log.info("[voice-cmd] stage=tts-synth-dispatch engine=%s chars=%d", getattr(engine, "name", "?"), len(clean))
    from . import tts as _tts_mod
    tracker = _tts_mod.synth_begin()
    try:
        try:
            audio, fmt = await asyncio.to_thread(engine.synth, clean)
        except Exception:
            log.exception("[tts] Synthesis failed; falling back to text")
            return False
    finally:
        _tts_mod.synth_end(tracker)
    log.info("[voice-cmd] stage=tts-synth-returned bytes=%d fmt=%s", len(audio) if audio else 0, fmt)
    _tts_latency_ms = int((time.monotonic() - _tts_t0) * 1000)
    try:
        gid = session.vc.channel.guild.id
    except Exception:
        gid = 0
    analytics.record(
        "tts_request",
        guild_id=gid,
        target=getattr(engine, "name", "unknown"),
        latency_ms=_tts_latency_ms,
        chars=len(clean),
        bytes=len(audio) if audio else 0,
        concurrency_start=tracker.start,
        concurrency_peak=tracker.peak,
    )
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
    voice_reconnect_clear(guild_id)
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


def persist_voice_session(guild_id: int, session) -> None:
    """Persist this session's reconnect target to SQLite. Survives hard crashes."""
    try:
        vc_channel_id = session.vc.channel.id
    except AttributeError:
        log.warning("[voice] persist_voice_session: guild %s has no vc.channel", guild_id)
        return
    spec = _sink_to_spec(session.message_sink)
    voice_reconnect_set(guild_id, vc_channel_id, spec)
    log.info("[voice] Persisted reconnect target guild=%s vc=%s sink=%s",
             guild_id, vc_channel_id, spec)


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


def voice_log_dest(vc) -> "discord.abc.Messageable | None":
    """Resolve where to post voice-card embeds.

    Discord voice channels (since 2023) are text-enabled — the
    ``VoiceChannel`` itself implements ``Messageable``. Fall back to
    the guild's system channel if the voice channel isn't sendable
    (permissions, stage channel, or future discord.py version quirks).
    """
    if vc is None:
        return None
    channel = getattr(vc, "channel", None)
    if channel is None:
        return None
    if isinstance(channel, discord.VoiceChannel):
        return channel
    guild = getattr(channel, "guild", None)
    system = getattr(guild, "system_channel", None) if guild else None
    return system


async def _post_wake_card(session, member, transcript: str, reply_text: str | None) -> None:
    """Flow 04 · wake-word card. Amber fenced-transcript embed with optional reply field."""
    try:
        dest = voice_log_dest(session.vc)
        if dest is None:
            return
        fields = [
            EmbedField("From", member.display_name if member else "unknown", inline=True),
            EmbedField("Voice", getattr(session.vc.channel, "name", "?"), inline=True),
            EmbedField("Transcript", f"```\n{transcript.strip()[:900] or '(empty)'}\n```", inline=False),
        ]
        if reply_text:
            fields.append(EmbedField("Halbot", reply_text[:900], inline=False))
        payload = ReplyPayload(
            mode=Mode.WAKE,
            title='"Halbot…"',
            subtext=f"wake · via faster-whisper · turn {len(session.history)}/{VOICE_HISTORY_TURNS}",
            fields=tuple(fields),
            footer="Spoken via TTS · transcript persisted in voice history",
        )
        await send_halbot_reply(dest, payload=payload)
    except Exception:
        log.exception("[voice] wake-card post failed")


async def _post_voice_trigger_card(session, guild, user_id, match_value: str,
                                   action_type: str, action_payload: str,
                                   fire_count: int, transcript: str) -> None:
    """Flow 05 · voice-trigger card. Violet embed showing matched phrase + action."""
    try:
        dest = voice_log_dest(session.vc)
        if dest is None:
            return
        member = guild.get_member(user_id) if guild else None
        speaker = member.display_name if member else f"user {user_id}"
        fields = [
            EmbedField("Matched phrase", f"`{match_value}`", inline=True),
            EmbedField("Action", f"`{action_type}` → {action_payload[:64]}", inline=True),
            EmbedField("Fire count", f"**{fire_count}**", inline=True),
            EmbedField("Speaker", speaker, inline=True),
            EmbedField("Voice", getattr(session.vc.channel, "name", "?"), inline=True),
        ]
        snippet = transcript.strip()
        if snippet:
            fields.append(EmbedField("Transcript", f"```\n{snippet[:600]}\n```", inline=False))
        payload = ReplyPayload(
            mode=Mode.VOICE_TRIGGER,
            title=f"Voice trigger: {match_value}",
            subtext="voice trigger · no wake word needed",
            fields=tuple(fields),
            footer="Tune or mute in the dashboard",
        )
        await send_halbot_reply(dest, payload=payload)
    except Exception:
        log.exception("[voice] trigger-card post failed")


async def _fire_voice_triggers(session, guild, user_id, transcript: str) -> None:
    """Scan a voice transcript for keyword_voice triggers. Fires independently of wake word."""
    try:
        rows = trigger_list("keyword_voice")
    except Exception:
        log.exception("[trigger] list failed")
        return
    if not rows:
        return
    tl = (transcript or "").lower()
    if not tl:
        return
    for r in rows:
        mv = (r.get("match_value") or "").lower().strip()
        if not mv or mv not in tl:
            continue
        at = r.get("action_type")
        ap = r.get("action_payload") or ""
        tid = r.get("id")
        try:
            analytics.record(
                "hook_fired",
                user_id=user_id,
                guild_id=guild.id,
                target=f"trigger:keyword_voice:{tid}",
                reason=mv,
            )
            if at == "voice_play":
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
                            log.exception("[trigger #%s] live read failed", tid)
                if audio:
                    await session.play_sound(audio, fmt)
                    analytics.record(
                        "soundboard_play",
                        user_id=user_id,
                        guild_id=guild.id,
                        target=ap,
                        source="saved" if row else "live",
                        trigger="trigger",
                        bytes=len(audio),
                    )
            elif at == "reply":
                await _voice_feedback(session, session.message_sink, ap)
            else:
                log.warning("[trigger #%s] unknown action_type %r", tid, at)
                continue
            trigger_mark_fired(tid)
            try:
                fire_count = int(r.get("fire_count") or 0) + 1
                await _post_voice_trigger_card(
                    session, guild, user_id, mv, at or "", ap,
                    fire_count, transcript,
                )
            except Exception:
                log.exception("[trigger #%s] voice card failed", tid)
        except Exception:
            log.exception("[trigger #%s] firing failed", tid)


async def handle_voice_command(guild, user_id, transcript, captured_at: float | None = None):
    """Callback from VoiceListener with a raw STT transcript.

    ``captured_at`` is ``time.monotonic()`` at the moment the VAD
    finalized the speech segment. We gate on the audio's wall age to
    refuse stale work — see STALE_PRE_INTENT_SECONDS in voice.py. A
    reply that lands 2 minutes after the user said the wake word is
    worse than no reply.

    Wake detection: substring match against known phonetic variants of
    "robot" in the STT output. No LLM is consulted for wake — only for
    intent parsing on transcripts that already passed the substring gate.
    """
    log.info("[voice-cmd] stage=begin user=%s transcript=%r", user_id, transcript[:120])
    if captured_at is not None:
        _wake_captured_at.set(captured_at)
    session = voice_listeners.get(guild.id)
    if not session:
        return
    # Ambient reflexes: keyword_voice triggers fire on every transcript,
    # regardless of wake word. MUST stay above the wake-candidate prefilter
    # below — triggers are explicitly designed to bypass the wake gate so
    # users can bind reactions to any spoken phrase (e.g. slur → cough sound)
    # without saying "robot" first. Do not move this past the prefilter.
    await _fire_voice_triggers(session, guild, user_id, transcript)
    sink = session.message_sink
    history = list(session.history)

    # Wake detection is now pure STT substring matching — no LLM arbitration.
    # Whisper already transcribes the speech; we just look for the wake token.
    # This replaced a combined wake+intent LLM call and a separate classifier
    # LLM call that were both unreliable under ollama load and produced
    # feedback loops when they timed out.
    if not _has_wake_candidate(transcript):
        log.info("[voice] no wake word in: %r", transcript[:80])
        return

    command = _extract_command(transcript)
    if not command:
        log.info("[voice] wake word alone, no command in: %r", transcript[:80])
        return

    # Second staleness gate — between STT and LLM. If audio has been
    # sitting more than STALE_PRE_INTENT_SECONDS, spending another LLM
    # call on it is wasted. Better to silently drop than reply late.
    if captured_at is not None:
        from .voice import STALE_PRE_INTENT_SECONDS
        age = time.monotonic() - captured_at
        if age > STALE_PRE_INTENT_SECONDS:
            log.warning(
                "[voice] dropping stale wake (age=%.1fs > %.1fs) user=%s cmd=%r",
                age, STALE_PRE_INTENT_SECONDS, user_id, command[:60],
            )
            return

    log.info("[voice] user=%s command: %r", user_id, command)
    try:
        import discord
        sounds = list(await guild.fetch_soundboard_sounds())
    except Exception:
        sounds = []
    saved = db_list()
    _llm_t0 = time.monotonic()
    actions = await asyncio.to_thread(
        parse_voice_intent, command, sounds, saved, history
    )
    analytics.record(
        "llm_call",
        user_id=user_id,
        guild_id=guild.id,
        target="parse_voice_intent",
        latency_ms=int((time.monotonic() - _llm_t0) * 1000),
        action_count=len(actions) if isinstance(actions, list) else 0,
    )
    if not actions:
        actions = [{"action": "unknown",
                    "message": "I heard you but couldn't pick a sound for that."}]

    saved_map = {s["name"]: s for s in saved}
    sound_map = {s.name: s for s in sounds}
    member = guild.get_member(user_id)
    user_name = member.display_name if member else f"user {user_id}"

    # Flow 04 wake card: collect reply summaries so we post one card per
    # wake turn regardless of which branch handled the intent.
    wake_reply: list[str] = []

    def _record(bot_response: str) -> None:
        wake_reply.append(bot_response)
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

    try:
        for intent in actions:
            action = intent.get("action")
            analytics.record(
                "cmd_invoke",
                user_id=user_id,
                guild_id=guild.id,
                target=f"voice:{action or 'unknown'}",
            )

            if action == "voice_play":
                name = intent.get("name", "")
                row = db_get(name) if name else None
                if row:
                    fmt = detect_audio_format(row["audio"])
                    await session.play_sound(row["audio"], fmt)
                    analytics.record(
                        "soundboard_play",
                        user_id=user_id,
                        guild_id=guild.id,
                        target=name,
                        source="saved",
                        trigger="voice",
                        bytes=len(row["audio"]) if row.get("audio") else 0,
                    )
                    _record(f"(played sound: {name})")
                    break

                live = sound_map.get(name)
                if live:
                    try:
                        audio = await live.read()
                        fmt = detect_audio_format(audio)
                        await session.play_sound(audio, fmt)
                        analytics.record(
                            "soundboard_play",
                            user_id=user_id,
                            guild_id=guild.id,
                            target=name,
                            source="live",
                            trigger="voice",
                            bytes=len(audio) if audio else 0,
                        )
                        _record(f"(played sound: {name})")
                    except Exception:
                        log.exception("Failed to read live sound %s for voice playback", name)
                        _record(f"(failed to play: {name})")
                    break

                customized = await customize_response_async(
                    f'Couldn\'t find a sound called "{name}".',
                    context="voice command: sound lookup miss",
                )
                await _voice_feedback(session, sink, customized)
                _record(customized)

            elif action == "conversation":
                # Fast path didn't match a sound; user asked something
                # conversational. Escalate to the FULL text-grade pipeline:
                # same model, same SYSTEM_PROMPT, same persona stacking,
                # full sound + emoji + voice-status context. Slow but
                # thoughtful; output constrained to a single TTS-ready reply.
                _convo_t0 = time.monotonic()
                vc_name = None
                try:
                    vc_name = session.vc.channel.name if session.vc and session.vc.channel else None
                except Exception:
                    vc_name = None
                reply = await answer_voice_conversation_async(
                    transcript,
                    sounds=sounds,
                    saved=saved,
                    history=history,
                    guild=guild,
                    voice_channel_name=vc_name,
                )
                analytics.record(
                    "llm_call",
                    user_id=user_id,
                    guild_id=guild.id,
                    target="voice_conversation",
                    latency_ms=int((time.monotonic() - _convo_t0) * 1000),
                    chars=len(reply or ""),
                )
                await _voice_feedback(session, sink, reply)
                _record(reply)

            elif action == "unknown":
                msg = intent.get("message", "I didn't understand that voice command.")
                customized = await customize_response_async(msg, context="voice command failure")
                await _voice_feedback(session, sink, customized)
                _record(customized)
    finally:
        await _post_wake_card(
            session, member, transcript,
            " · ".join(r for r in wake_reply if r) or None,
        )
