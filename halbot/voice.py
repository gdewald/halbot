"""Halbot voice listener — wake-word triggered STT for voice channel commands.

Connects to a Discord voice channel via discord-ext-voice-recv, runs
energy-based VAD on per-user audio streams, transcribes speech segments
with faster-whisper, and fires a callback when the wake word "Halbot"
is detected followed by a command.

Also hosts the voice-session plumbing (:class:`VoiceSession` and the
:class:`MessageSink` family) that keeps voice decoupled from whatever text
channel triggered the join — see ``docs/plans/voice-text-decoupling.md``.

Requires: discord-ext-voice-recv, faster-whisper, numpy
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import discord

log = logging.getLogger("halbot")

# Optional dependency — graceful fallback if not installed
try:
    from discord.ext import voice_recv  # type: ignore[import-untyped]

    VOICE_RECV_AVAILABLE = True
except ImportError:
    voice_recv = None
    VOICE_RECV_AVAILABLE = False


# ---------------------------------------------------------------------------
# VoiceRecvClient subclass — patch DAVE E2EE decryption into voice_recv
# ---------------------------------------------------------------------------
# discord-ext-voice-recv only strips transport encryption (AEAD / XSalsa).
# Discord now mandates DAVE end-to-end encryption on top of that. After
# transport decryption the payload is still DAVE-encrypted, so Opus decoding
# fails silently.  We monkey-patch the PacketDecryptor's decrypt_rtp method
# to also strip the DAVE layer using the davey session on the connection.

try:
    import davey as _davey  # type: ignore[import-untyped]

    _HAS_DAVEY = True
except ImportError:
    _davey = None  # type: ignore[assignment]
    _HAS_DAVEY = False

if VOICE_RECV_AVAILABLE:

    class HalbotVoiceRecvClient(voice_recv.VoiceRecvClient):
        """VoiceRecvClient that adds DAVE decryption to the receive pipeline."""

        def listen(self, sink, *, after=None):
            super().listen(sink, after=after)
            if _HAS_DAVEY:
                self._patch_dave_decryption()

        def _patch_dave_decryption(self):
            reader = self._reader
            if not reader:
                return

            original_decrypt = reader.decryptor.decrypt_rtp
            vc = self

            def _strip_dave_supplemental(data: bytes) -> bytes:
                """Strip DAVE supplemental bytes from the end of a packet.

                DAVE appends supplemental bytes to every packet.  The last
                byte is the total length of the supplemental section
                (including itself).  When we can't use the session to
                decrypt we still need to strip these so Opus sees clean data.
                """
                if not data:
                    return data
                supp_len = data[-1]
                if 0 < supp_len < len(data):
                    return data[:-supp_len]
                return data

            # Well-known Discord silence Opus packet: 3 bytes, decodes to
            # 20 ms of silence.  Safe replacement when we must emit SOMETHING
            # but the real frame is unusable — prevents OpusError from
            # killing the PacketRouter thread.
            SILENCE_OPUS = b"\xf8\xff\xfe"

            def _dave_decrypt_rtp(packet):
                """Transport-decrypt, then strip DAVE E2EE layer.

                Invariants: must return opus bytes the decoder can handle OR
                the router thread dies on OpusError("corrupted stream") and
                the whole receive pipeline stops until reconnect. On any
                failure past the DAVE-ready handshake we emit the silence
                frame instead of ciphertext.
                """
                try:
                    result = original_decrypt(packet)
                except Exception:
                    log.debug("[dave] transport decrypt raised", exc_info=True)
                    return SILENCE_OPUS
                session = getattr(vc._connection, "dave_session", None)
                if not session or not session.ready:
                    # Pre-handshake window: frames are plain opus with DAVE
                    # supplemental tail. Strip + pass.
                    try:
                        return _strip_dave_supplemental(result)
                    except Exception:
                        return SILENCE_OPUS
                user_id = vc._ssrc_to_id.get(packet.ssrc)
                if user_id is None:
                    # No ssrc→user mapping yet (speaker not announced) —
                    # frame is DAVE-encrypted and we can't decrypt without
                    # the user id. Drop to silence rather than hand opus
                    # ciphertext.
                    return SILENCE_OPUS
                try:
                    return session.decrypt(
                        user_id, _davey.MediaType.audio, result
                    )
                except Exception:
                    log.debug(
                        "[dave] decrypt failed ssrc=%s uid=%s, emitting silence",
                        packet.ssrc, user_id, exc_info=True,
                    )
                    return SILENCE_OPUS

            reader.decryptor.decrypt_rtp = _dave_decrypt_rtp
            log.info("[dave] Patched receive pipeline with DAVE decryption")
else:
    HalbotVoiceRecvClient = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WAKE_WORD = "robot"
# Biasing prompt fed to whisper's decoder — nudges STT toward literal "robot"
# instead of homophones ("row bot", "roebot").
WHISPER_INITIAL_PROMPT = (
    "Robot is the wake word for a Discord bot. "
    "Users say 'robot' to address it, followed by a command."
)
SILENCE_TIMEOUT = 1.5  # seconds of silence to end a speech segment
MIN_SPEECH_DURATION = 0.4  # ignore segments shorter than this (seconds)
MAX_SPEECH_DURATION = 15.0  # force-complete after this (seconds)
ENERGY_THRESHOLD = 0.015  # RMS on float32 [-1, 1]; tune if false-triggers


# ---------------------------------------------------------------------------
# Whisper model (lazy, thread-safe singleton)
# ---------------------------------------------------------------------------
_whisper_model = None
_whisper_lock = threading.Lock()
# Serializes transcribe() calls so concurrent users can't thrash GPU
# memory / kernels. faster-whisper's single-model throughput is higher
# under serial calls than under parallel ones on a single GPU.
_transcribe_lock = threading.Lock()


def _register_nvidia_dll_dirs() -> None:
    """Add nvidia pip-wheel DLL directories to the Windows DLL search path.

    Packages like nvidia-cublas-cu12 install DLLs into
    site-packages/nvidia/<package>/bin/ which Windows won't find automatically.
    os.add_dll_directory() registers each bin/ dir so ctranslate2 can load them.
    Only runs on Windows; no-ops on other platforms.

    Imports the `nvidia` namespace package to get its real on-disk location
    rather than relying on site.getsitepackages(), which does not reliably
    return the active venv's site-packages.
    """
    if os.name != "nt":
        return
    from pathlib import Path
    try:
        import nvidia  # namespace package present when any nvidia-* wheel is installed
    except ImportError:
        log.warning("[whisper] No nvidia-* packages installed; CUDA DLLs may not load")
        return
    bin_dirs: list[str] = []
    for path in nvidia.__path__:
        nvidia_root = Path(path)
        for bin_dir in nvidia_root.glob("*/bin"):
            if bin_dir.is_dir():
                # add_dll_directory covers LoadLibraryEx with LOAD_LIBRARY_SEARCH_*.
                os.add_dll_directory(str(bin_dir))
                bin_dirs.append(str(bin_dir))
                log.info("[whisper] Registered DLL dir: %s", bin_dir)
    # Some native libs (ctranslate2 lazy-loads cublas on first encode) bypass
    # the LOAD_LIBRARY_SEARCH_* flags and fall back to the legacy PATH-based
    # DLL search.  Prepend the nvidia bin dirs to PATH so they are found there
    # as well.  Prepending (not appending) ensures our CUDA 12 DLLs win over
    # any older CUDA install on the system PATH.
    if bin_dirs:
        existing = os.environ.get("PATH", "")
        prepend = os.pathsep.join(bin_dirs)
        os.environ["PATH"] = prepend + (os.pathsep + existing if existing else "")


def load_whisper():
    """Lazy-load faster-whisper large-v3-turbo on CUDA.  Thread-safe."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model
        log.info("[whisper] stage=register-dll-dirs")
        _register_nvidia_dll_dirs()
        log.info("[whisper] stage=import-faster-whisper")
        from faster_whisper import WhisperModel

        log.info("[whisper] stage=construct-model device=cuda compute=float16 model=large-v3-turbo")
        _whisper_model = WhisperModel(
            "large-v3-turbo", device="cuda", compute_type="float16"
        )
        log.info("[whisper] stage=model-loaded")
        return _whisper_model


def unload_whisper() -> None:
    """Release the whisper model and free its VRAM.

    Safe to call when nothing is loaded (no-op).  The next transcribe() call
    will lazily reload.  Called when the bot leaves voice so the ~5-6 GB
    faster-whisper footprint doesn't sit on the GPU while Ollama wants it.
    """
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            return
        _whisper_model = None

    # faster-whisper/ctranslate2 hold the CUDA allocation as long as the
    # model object is reachable; force GC so the destructor runs, then nudge
    # torch (if present) to return the cached CUDA blocks to the driver.
    import gc
    gc.collect()
    try:
        import torch  # type: ignore[import-not-found]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
    log.info("[whisper] Model unloaded")


def transcribe(audio_float32: np.ndarray) -> str:
    """Transcribe 16 kHz mono float32 audio.  **Blocking** — run in executor."""
    log.info("[stt] stage=load-whisper samples=%d", len(audio_float32))
    model = load_whisper()
    with _transcribe_lock:
        log.info("[stt] stage=transcribe-begin")
        segments, info = model.transcribe(
            audio_float32,
            language="en",
            # beam_size=1 (greedy): ~3-5x faster than beam=5 with negligible
            # accuracy loss on short English utterances. Turbo model already
            # has high baseline quality.
            beam_size=1,
            # Skip the fallback-temperature sweep (0.0 → 0.2 → 0.4 …). On
            # short clips the default 5-step fallback rarely improves output
            # but often doubles latency when logprob threshold isn't met.
            temperature=0.0,
            vad_filter=False,
            initial_prompt=WHISPER_INITIAL_PROMPT,
        )
        log.info("[stt] stage=transcribe-iterate")
        texts = []
        for s in segments:
            log.debug("[whisper] segment [%.2fs→%.2fs] (p=%.2f): %r", s.start, s.end, s.avg_logprob, s.text)
            texts.append(s.text)
        text = " ".join(texts).strip()
        if not text:
            log.debug("[whisper] no segments returned (lang=%s prob=%.2f)", info.language, info.language_probability)
        return text


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
def resample_48k_stereo_to_16k_mono(pcm: bytes) -> np.ndarray:
    """48 kHz stereo s16le PCM  ->  16 kHz mono float32 in [-1, 1]."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    left = samples[0::2].astype(np.float32)
    right = samples[1::2].astype(np.float32)
    mono_48k = (left + right) / 2.0
    # Integer-ratio downsample: 48000 / 16000 = 3
    mono_16k = mono_48k[::3]
    return mono_16k / 32768.0


def is_speech(chunk: np.ndarray) -> bool:
    """Energy-based voice activity detection on normalised audio."""
    return len(chunk) > 0 and float(np.sqrt(np.mean(chunk**2))) > ENERGY_THRESHOLD


# ---------------------------------------------------------------------------
# Per-user speech segmenter
# ---------------------------------------------------------------------------
class _UserAudioState:
    """Accumulates PCM for one user, fires when a speech segment completes."""

    __slots__ = ("chunks", "is_speaking", "silence_t", "speech_t", "n_samples")

    def __init__(self):
        self.reset()

    def reset(self):
        self.chunks: list[np.ndarray] = []
        self.is_speaking: bool = False
        self.silence_t: float = 0.0
        self.speech_t: float = 0.0
        self.n_samples: int = 0

    def feed(self, user_id: int, audio_16k: np.ndarray) -> np.ndarray | None:
        """Feed a 20 ms chunk.  Returns the complete segment or *None*."""
        now = time.monotonic()
        speaking = is_speech(audio_16k)

        if speaking:
            if not self.is_speaking:
                # Speech onset
                self.is_speaking = True
                self.speech_t = now
                self.chunks.clear()
                self.n_samples = 0
                log.debug("[vad] user=%s speech start", user_id)
            self.silence_t = 0.0
            self.chunks.append(audio_16k)
            self.n_samples += len(audio_16k)

        elif self.is_speaking:
            # Silence while we were speaking — keep buffering for a bit
            self.chunks.append(audio_16k)
            self.n_samples += len(audio_16k)
            if self.silence_t == 0.0:
                self.silence_t = now

            elapsed = now - self.speech_t
            silent = now - self.silence_t

            if elapsed >= MAX_SPEECH_DURATION:
                duration = self.n_samples / 16000
                log.debug("[vad] user=%s force-complete after %.1fs", user_id, duration)
                seg = np.concatenate(self.chunks)
                self.reset()
                return seg

            if silent >= SILENCE_TIMEOUT:
                duration = self.n_samples / 16000
                if duration >= MIN_SPEECH_DURATION:
                    log.debug("[vad] user=%s segment complete: %.1fs", user_id, duration)
                    seg = np.concatenate(self.chunks)
                    self.reset()
                    return seg
                log.debug("[vad] user=%s segment discarded (too short: %.2fs)", user_id, duration)
                self.reset()

        return None


# ---------------------------------------------------------------------------
# MessageSink — polymorphic destination for voice-session feedback
# ---------------------------------------------------------------------------
# The bot used to post voice-session telemetry (miss, unknown, idle-disconnect
# notice) to whichever text channel triggered the voice_join.  That coupling
# is gone: a voice session owns a MessageSink, and the sink decides where to
# post.  Default: post into the voice channel's own built-in chat pane.
#
# See docs/plans/voice-text-decoupling.md for the design + locked decisions.


@runtime_checkable
class MessageSink(Protocol):
    """Where voice-session feedback messages go."""

    async def send(self, text: str) -> None:
        ...


class TextChannelSink:
    """Post into a specific ``discord.TextChannel`` (or any Messageable)."""

    def __init__(self, channel: discord.abc.Messageable):
        self.channel = channel

    async def send(self, text: str) -> None:
        try:
            await self.channel.send(text)
        except Exception:
            log.exception("[voice] TextChannelSink.send failed")


class VoiceChatSink:
    """Post into the voice channel's own chat pane.

    ``VoiceChannel`` became a Messageable with Discord's voice-channel chat
    feature.  If posting fails (feature disabled, missing permission), degrades
    to :class:`LogOnlySink` per decision 1a — one WARNING log per session,
    silent thereafter.
    """

    def __init__(self, vc_channel: discord.VoiceChannel):
        self.vc_channel = vc_channel
        self._fallback_warned = False

    async def send(self, text: str) -> None:
        try:
            await self.vc_channel.send(text)
        except (discord.Forbidden, discord.HTTPException, AttributeError) as e:
            if not self._fallback_warned:
                log.warning(
                    "[voice] VoiceChatSink fallback for #%s: %s — further "
                    "messages this session will be log-only",
                    getattr(self.vc_channel, "name", "?"),
                    e,
                )
                self._fallback_warned = True
            log.info("[voice] (log-only) %s", text)


class LogOnlySink:
    """Drop messages to the log and nothing else.  Decision 1a fallback."""

    async def send(self, text: str) -> None:
        log.info("[voice] (log-only) %s", text)


# ---------------------------------------------------------------------------
# VoiceSession — aggregates everything a voice session owns
# ---------------------------------------------------------------------------
@dataclass
class VoiceSession:
    """Aggregate for one guild's active voice presence.

    Holds the STT/VAD listener, the feedback sink, and (future) rolling
    history.  Delegates the common "things callers used to call on the raw
    listener" (``.vc``, ``.stop()``, ``.play_sound()``) so the refactor is
    drop-in for current call sites.
    """

    listener: "VoiceListener"
    message_sink: MessageSink
    # History + idle task land in later steps of the plan; kept as stubs so
    # the dataclass shape is stable.
    history: list = field(default_factory=list)

    # -- listener delegation ------------------------------------------------
    @property
    def vc(self):
        return self.listener.vc

    @property
    def guild(self):
        return self.listener.vc.guild

    def stop(self) -> None:
        self.listener.stop()

    async def play_sound(self, audio_bytes: bytes, fmt: str = "mp3") -> None:
        await self.listener.play_sound(audio_bytes, fmt)


# ---------------------------------------------------------------------------
# Voice listener
# ---------------------------------------------------------------------------
class VoiceListener:
    """Receives audio from a voice channel, detects wake word, fires callback."""

    def __init__(
        self,
        vc: discord.VoiceClient,
        on_command,
    ):
        """
        Parameters
        ----------
        vc : VoiceRecvClient (connected)
        on_command : async callback(guild, user_id, transcript).  The callback
            is responsible for looking up the :class:`VoiceSession` (and
            therefore the sink) via the guild — the listener itself has no
            opinion on where feedback goes.
        """
        self.vc = vc
        self.on_command = on_command
        self._users: dict[int, _UserAudioState] = defaultdict(_UserAudioState)
        self._loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self._disconnect_listener = None  # coro, registered in start()

    # -- listening -----------------------------------------------------------

    def start(self):
        """Begin receiving audio from the voice channel."""
        if not VOICE_RECV_AVAILABLE:
            raise RuntimeError("discord-ext-voice-recv is not installed")

        listener = self  # capture for inner class

        class _HalbotSink(voice_recv.AudioSink):
            """Custom AudioSink that feeds per-user audio into our VAD pipeline."""

            def __init__(self):
                super().__init__()
                self._frame_count = 0
                self._first_frame_logged = False

            def wants_opus(self) -> bool:
                return False  # we want decoded PCM

            def write(self, user, data):
                self._frame_count += 1

                if not self._first_frame_logged:
                    self._first_frame_logged = True
                    log.info(
                        "[voice] First audio frame: user=%s pcm_bytes=%d",
                        user,
                        len(data.pcm) if data.pcm else 0,
                    )

                if user is None or not data.pcm:
                    return

                audio_16k = resample_48k_stereo_to_16k_mono(data.pcm)
                state = listener._users[user.id]
                segment = state.feed(user.id, audio_16k)
                if segment is not None:
                    asyncio.run_coroutine_threadsafe(
                        listener._process_segment(user.id, segment),
                        listener._loop,
                    )

            def cleanup(self):
                log.info("[voice] AudioSink stopped (total frames: %d)", self._frame_count)

        sink = _HalbotSink()

        # Suppress noisy INFO logs from voice_recv internals (e.g. "unexpected
        # rtcp packet" fires every second). WARNING still lets real errors through.
        logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)

        self.vc.listen(sink)

        # Drop per-user VAD state when a user disconnects from the voice
        # channel so _users doesn't grow unbounded over long sessions.
        async def _on_member_disconnect(member, ssrc):
            if member is not None:
                removed = listener._users.pop(member.id, None)
                if removed is not None:
                    log.debug("[voice] Cleaned up VAD state for user %s", member.id)

        self._disconnect_listener = _on_member_disconnect
        try:
            self.vc.add_listener(_on_member_disconnect, name="on_voice_member_disconnect")
        except Exception:
            log.exception("[voice] Could not register member-disconnect listener")

        mode = getattr(self.vc, "mode", "?")
        dave_ver = getattr(getattr(self.vc, "_connection", None), "dave_protocol_version", "?")
        log.info(
            "[voice] Listening in #%s (vc=%s, mode=%s, dave=%s)",
            self.vc.channel.name,
            type(self.vc).__name__,
            mode,
            dave_ver,
        )

    def stop(self):
        """Stop listening and clear buffers."""
        if self._disconnect_listener is not None:
            try:
                self.vc.remove_listener(
                    self._disconnect_listener, name="on_voice_member_disconnect"
                )
            except Exception:
                pass
            self._disconnect_listener = None
        try:
            self.vc.stop_listening()
        except Exception:
            pass
        self._users.clear()
        log.info("[voice] Listener stopped")

    # -- transcription -------------------------------------------------------

    async def _process_segment(self, user_id: int, audio: np.ndarray):
        """Transcribe a speech segment, check for wake word."""
        duration = len(audio) / 16000
        log.info("[stt] Transcribing %.1fs from user %s …", duration, user_id)
        t0 = time.monotonic()

        try:
            text = await self._loop.run_in_executor(None, transcribe, audio)
        except Exception:
            log.exception("[stt] Whisper transcription failed")
            return

        elapsed = time.monotonic() - t0

        if not text:
            log.info("[stt] (empty result, %.1fs)", elapsed)
            return

        log.info("[stt] user=%s (%.1fs): %r", user_id, elapsed, text)

        # Hand the full transcript to the command handler. Wake-word
        # detection lives in voice_session.handle_voice_command as a cheap
        # substring match over known phonetic variants of "halbot" — no LLM.
        try:
            await self.on_command(self.vc.guild, user_id, text)
        except Exception:
            log.exception("[voice] Command callback failed")

    # -- playback ------------------------------------------------------------

    async def play_sound(self, audio_bytes: bytes, fmt: str = "mp3"):
        """Play raw audio bytes in the voice channel via ffmpeg."""
        if self.vc.is_playing():
            self.vc.stop()

        log.info("[play] stage=begin bytes=%d fmt=%s channel=#%s", len(audio_bytes), fmt, self.vc.channel.name)

        fd, path = tempfile.mkstemp(suffix=f".{fmt}")
        try:
            os.write(fd, audio_bytes)
            os.close(fd)
            log.info("[play] stage=temp-written path=%s", path)

            from . import _native
            ffmpeg_exe = _native.ffmpeg_path()
            ffmpeg_kwargs = {"executable": ffmpeg_exe} if ffmpeg_exe else {}
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(path, **ffmpeg_kwargs), volume=0.5
            )
            log.info("[play] stage=source-built")

            def _after(error):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                if error:
                    log.error("[play] Playback error: %s", error)

            self.vc.play(source, after=_after)
            log.info("[play] stage=play-dispatched")

        except Exception:
            log.exception("[play] Failed to play sound in voice channel")
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
