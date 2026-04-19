"""Pluggable text-to-speech engines.

Default: Kokoro-82M (Apache 2.0, ~300 MB VRAM, 24 kHz output).  Pick a
different engine via ``TTS_ENGINE=<name>``; ``none`` / ``off`` disables TTS
entirely and the bot falls back to text replies.

To add a new engine (e.g. Coqui XTTS v2): subclass :class:`TTSEngine`,
register it in ``_ENGINES`` below, and add its optional-dependency group
in pyproject.toml.  The engine only needs to turn a string into
ffmpeg-readable audio bytes + format — everything else (playback, VRAM
lifecycle, fallback) is handled by the bot.
"""
from __future__ import annotations

import abc
import io
import logging
import os
import threading

log = logging.getLogger("halbot")


class TTSEngine(abc.ABC):
    """Abstract TTS engine.  Implementations must be thread-safe for synth()."""

    name: str = "base"

    @abc.abstractmethod
    def synth(self, text: str) -> tuple[bytes, str]:
        """Synthesize *text* and return ``(audio_bytes, ffmpeg_format)``.

        ``ffmpeg_format`` is a container hint used as a temp-file suffix
        (e.g. ``"wav"``, ``"mp3"``).  Blocking — call via
        ``asyncio.to_thread`` from the bot.
        """

    def unload(self) -> None:
        """Release any VRAM/RAM held by the engine.  Safe no-op by default."""
        return


# ---------------------------------------------------------------------------
# Kokoro-82M — default local engine
# ---------------------------------------------------------------------------
class KokoroEngine(TTSEngine):
    """Kokoro-82M wrapper.  Lazy-loads on first synth, thread-safe."""

    name = "kokoro"
    SAMPLE_RATE = 24000  # kokoro output rate; do not change

    def __init__(self):
        self._pipeline = None
        self._lock = threading.Lock()
        # See https://huggingface.co/hexgrad/Kokoro-82M for the voice list.
        # af_heart is a warm, neutral American-English default.
        from . import config as _config
        self.voice = _config.get("tts_voice")
        self.lang = _config.get("tts_lang")
        try:
            self.speed = float(_config.get("tts_speed"))
        except (ValueError, TypeError):
            self.speed = 1.0

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            log.info("[tts] Loading Kokoro-82M (lang=%s voice=%s) …", self.lang, self.voice)
            from kokoro import KPipeline  # type: ignore[import-not-found]
            self._pipeline = KPipeline(lang_code=self.lang)
            log.info("[tts] Kokoro loaded")
            return self._pipeline

    def synth(self, text: str) -> tuple[bytes, str]:
        import numpy as np
        import soundfile as sf  # type: ignore[import-not-found]

        pipeline = self._load()
        chunks: list[np.ndarray] = []
        # KPipeline yields (graphemes, phonemes, audio) per chunk.  audio is
        # a torch.Tensor or numpy array on CPU; concatenate all chunks into
        # one waveform and encode as 16-bit WAV for ffmpeg.
        for _, _, audio in pipeline(text, voice=self.voice, speed=self.speed):
            if audio is None:
                continue
            arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            chunks.append(arr.astype(np.float32))
        if not chunks:
            raise RuntimeError(f"Kokoro produced no audio for text: {text!r}")
        waveform = np.concatenate(chunks)
        buf = io.BytesIO()
        sf.write(buf, waveform, self.SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "wav"

    def unload(self) -> None:
        with self._lock:
            if self._pipeline is None:
                return
            self._pipeline = None
        import gc
        gc.collect()
        try:
            import torch  # type: ignore[import-not-found]
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        log.info("[tts] Kokoro unloaded")


# ---------------------------------------------------------------------------
# Registry + singleton accessor
# ---------------------------------------------------------------------------
_ENGINES: dict[str, type[TTSEngine]] = {
    "kokoro": KokoroEngine,
    # Future: "xtts": XTTSEngine, "openai": OpenAITTSEngine, ...
}

_engine: TTSEngine | None = None
_engine_lock = threading.Lock()
_engine_tried: bool = False


def get_engine() -> TTSEngine | None:
    """Return the configured TTS engine singleton, or ``None`` if disabled.

    Safe to call repeatedly; instantiation happens once and failures are
    remembered (we return ``None`` forever after a failed import rather than
    re-attempting on every message).
    """
    global _engine, _engine_tried
    if _engine is not None or _engine_tried:
        return _engine
    with _engine_lock:
        if _engine is not None or _engine_tried:
            return _engine
        _engine_tried = True
        from . import config as _config
        name = str(_config.get("tts_engine") or "kokoro").strip().lower()
        if name in ("", "none", "off", "disabled"):
            log.info("[tts] Disabled via TTS_ENGINE=%s", name or "<empty>")
            return None
        cls = _ENGINES.get(name)
        if cls is None:
            log.warning("[tts] Unknown TTS_ENGINE=%r; known: %s", name, list(_ENGINES))
            return None
        try:
            _engine = cls()
        except Exception:
            log.exception("[tts] Failed to instantiate engine %s", name)
            return None
        log.info("[tts] Engine selected: %s", name)
        return _engine


def engine_loaded() -> bool:
    """True if a TTS engine singleton has been instantiated."""
    return _engine is not None


def unload_engine() -> None:
    """Release whatever the current engine is holding.  Safe no-op if none."""
    with _engine_lock:
        engine = _engine
    if engine is None:
        return
    try:
        engine.unload()
    except Exception:
        log.exception("[tts] Unload failed")


def preload_engine_async() -> None:
    """Kick the current engine's model load on a background thread.

    Called on voice-join so the first spoken reply doesn't eat the ~10s
    cold-start.  No-op if TTS is disabled.
    """
    engine = get_engine()
    if engine is None:
        return

    def _warm():
        try:
            # Trigger load without synthesizing; each engine picks its own path.
            if hasattr(engine, "_load"):
                engine._load()
        except Exception:
            log.exception("[tts] Pre-load failed")

    threading.Thread(target=_warm, daemon=True).start()
