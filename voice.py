"""Halbot voice listener — wake-word triggered STT for voice channel commands.

Connects to a Discord voice channel via discord-ext-voice-recv, runs
energy-based VAD on per-user audio streams, transcribes speech segments
with faster-whisper, and fires a callback when the wake word "Halbot"
is detected followed by a command.

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

            def _dave_decrypt_rtp(packet):
                """Transport-decrypt, then strip DAVE E2EE layer."""
                result = original_decrypt(packet)
                session = getattr(vc._connection, "dave_session", None)
                if not session or not session.ready:
                    return _strip_dave_supplemental(result)
                user_id = vc._ssrc_to_id.get(packet.ssrc)
                if user_id is None:
                    return _strip_dave_supplemental(result)
                try:
                    return session.decrypt(
                        user_id, _davey.MediaType.audio, result
                    )
                except Exception:
                    log.debug(
                        "[dave] decrypt failed ssrc=%s uid=%s, stripping supplemental",
                        packet.ssrc, user_id, exc_info=True,
                    )
                    return _strip_dave_supplemental(result)

            reader.decryptor.decrypt_rtp = _dave_decrypt_rtp
            log.info("[dave] Patched receive pipeline with DAVE decryption")
else:
    HalbotVoiceRecvClient = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WAKE_WORD = "halbot"
# Biasing prompt fed to whisper's decoder — dramatically improves the chance
# that the STT engine actually emits the literal string "Halbot" instead of
# phonetic approximations like "Albot" / "Owlbot" / "Palbot".
WHISPER_INITIAL_PROMPT = (
    "Halbot is the name of a Discord bot. "
    "Users say 'Halbot' to address it, followed by a command."
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
        _register_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        log.info("[whisper] Loading large-v3-turbo on CUDA …")
        _whisper_model = WhisperModel(
            "large-v3-turbo", device="cuda", compute_type="float16"
        )
        log.info("[whisper] Model loaded")
        return _whisper_model


def transcribe(audio_float32: np.ndarray) -> str:
    """Transcribe 16 kHz mono float32 audio.  **Blocking** — run in executor."""
    model = load_whisper()
    segments, info = model.transcribe(
        audio_float32,
        language="en",
        beam_size=5,
        # vad_filter disabled — the pipeline already gates on energy VAD before
        # sending audio here; applying whisper's VAD on top over-filters short
        # utterances and produces empty results.
        vad_filter=False,
        # Bias whisper's decoder toward the wake word so it emits the literal
        # spelling "Halbot" instead of phonetic approximations.
        initial_prompt=WHISPER_INITIAL_PROMPT,
    )
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
# Voice listener
# ---------------------------------------------------------------------------
class VoiceListener:
    """Receives audio from a voice channel, detects wake word, fires callback."""

    def __init__(
        self,
        vc: discord.VoiceClient,
        text_channel: discord.TextChannel,
        on_command,
    ):
        """
        Parameters
        ----------
        vc : VoiceRecvClient (connected)
        text_channel : where to post text feedback
        on_command : async callback(guild, text_channel, user_id, transcript)
        """
        self.vc = vc
        self.text_channel = text_channel
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

        # Hand the transcription to the LLM wake-word classifier.  It handles
        # all phonetic mishearings ("Albot", "Owlbot", "Hal Bot", etc.) and
        # extracts the command in one shot.  Imported lazily to avoid a hard
        # import cycle between bot.py and voice.py at module load.
        from bot import check_wake_word  # type: ignore[import-not-found]

        try:
            wake, command = await self._loop.run_in_executor(
                None, check_wake_word, text
            )
        except Exception:
            log.exception("[wake] Classifier call failed")
            return

        if not wake:
            log.info("[stt] no wake word in: %r", text)
            return

        if not command:
            log.info("[wake] Wake word detected but no command followed")
            return

        log.info("[wake] user=%s command: %r", user_id, command)
        try:
            await self.on_command(
                self.vc.guild, self.text_channel, user_id, command
            )
        except Exception:
            log.exception("[wake] Voice command callback failed")

    # -- playback ------------------------------------------------------------

    async def play_sound(self, audio_bytes: bytes, fmt: str = "mp3"):
        """Play raw audio bytes in the voice channel via ffmpeg."""
        if self.vc.is_playing():
            self.vc.stop()

        log.info("[play] Playing %d bytes (%s) in #%s", len(audio_bytes), fmt, self.vc.channel.name)

        fd, path = tempfile.mkstemp(suffix=f".{fmt}")
        try:
            os.write(fd, audio_bytes)
            os.close(fd)

            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(path), volume=0.5
            )

            def _after(error):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                if error:
                    log.error("[play] Playback error: %s", error)

            self.vc.play(source, after=_after)

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
