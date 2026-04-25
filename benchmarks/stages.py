"""Stage wrappers for the STT -> LLM -> TTS pipeline.

Each wrapper is a thin adapter over the underlying library. It does NOT
route through ``halbot/``'s production wrappers, because the benchmark
needs to vary model/compute/beam/voice per scenario and the prod path
locks those to registry config.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Module-level singletons so adjacent scenarios sharing the same model
# don't re-pay the load cost. The runner is responsible for calling
# ``unload_*`` when model config changes between scenarios.
_whisper_model = None
_whisper_key: tuple | None = None
_tts_engine = None
_tts_key: tuple | None = None


@dataclass(slots=True)
class StageTiming:
    stage: str  # "stt" | "llm" | "tts"
    wall_ms: float
    model_load_ms: float | None = None
    input_size: int | None = None
    output_size: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# STT — faster-whisper direct
# ---------------------------------------------------------------------------
def _load_whisper(model: str, device: str, compute_type: str):
    """Lazy-load WhisperModel. Reuses the cached instance if its key matches."""
    global _whisper_model, _whisper_key
    key = (model, device, compute_type)
    if _whisper_model is not None and _whisper_key == key:
        return _whisper_model, 0.0
    unload_stt()
    # Register the nvidia DLL dirs the same way halbot.voice does — the
    # frozen bundle doesn't matter here, but source runs on Windows still
    # need the cublas DLLs resolvable for ctranslate2.
    from halbot.voice import _register_nvidia_dll_dirs  # type: ignore
    _register_nvidia_dll_dirs()
    from faster_whisper import WhisperModel  # type: ignore
    t0 = time.perf_counter()
    _whisper_model = WhisperModel(model, device=device, compute_type=compute_type)
    _whisper_key = key
    return _whisper_model, (time.perf_counter() - t0) * 1000.0


def unload_stt() -> None:
    global _whisper_model, _whisper_key
    if _whisper_model is None:
        return
    _whisper_model = None
    _whisper_key = None
    import gc
    gc.collect()
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def run_stt(audio_path: Path, *, config: dict) -> tuple[str, StageTiming]:
    """Transcribe a 16 kHz mono wav. Config keys:
    - model (str, default "large-v3-turbo")
    - device (str, default "cuda")
    - compute_type (str, default "float16")
    - beam_size (int, default 1)
    - language (str, default "en")
    """
    import numpy as np
    import soundfile as sf  # type: ignore

    model_name = config.get("model", "large-v3-turbo")
    device = config.get("device", "cuda")
    compute_type = config.get("compute_type", "float16")
    beam_size = int(config.get("beam_size", 1))
    language = config.get("language", "en")

    audio, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        # Whisper expects 16 kHz. Corpus should already be 16 kHz, but
        # resample defensively so a stray clip doesn't silently distort
        # the numbers.
        from scipy.signal import resample_poly  # type: ignore
        from math import gcd
        g = gcd(sr, 16000)
        audio = resample_poly(audio, 16000 // g, sr // g).astype("float32")

    model, load_ms = _load_whisper(model_name, device, compute_type)

    t0 = time.perf_counter()
    segments, info = model.transcribe(
        audio, language=language, beam_size=beam_size, temperature=0.0,
    )
    text = " ".join(s.text for s in segments).strip()
    wall_ms = (time.perf_counter() - t0) * 1000.0

    return text, StageTiming(
        stage="stt",
        wall_ms=wall_ms,
        model_load_ms=load_ms if load_ms > 0 else None,
        input_size=len(audio),
        output_size=len(text),
        extra={
            "audio_seconds": len(audio) / 16000.0,
            "language_probability": float(info.language_probability),
            "transcript": text,
            "audio_path": str(audio_path),
        },
    )


# ---------------------------------------------------------------------------
# LLM — ollama chat-completions direct
# ---------------------------------------------------------------------------
def run_llm(prompt: str | dict, *, config: dict) -> tuple[str, StageTiming]:
    """POST one completion request. Config keys:
    - url (str, default http://localhost:11434/v1/chat/completions)
    - model (str, default gemma4:e4b)
    - max_tokens (int, default 512)
    - temperature (float, default 0.8)
    - system (str, optional) prepended as a system message
    - timeout (int, default 180)

    ``prompt`` may be a plain user string or a dict with a ``messages``
    list (for reusing captured real-prompt payloads verbatim).
    """
    import requests

    url = config.get("url", "http://localhost:11434/v1/chat/completions")
    model = config.get("model", "gemma4:e4b")
    max_tokens = int(config.get("max_tokens", 512))
    temperature = float(config.get("temperature", 0.8))
    timeout = int(config.get("timeout", 180))

    if isinstance(prompt, dict) and "messages" in prompt:
        messages = prompt["messages"]
    else:
        messages = []
        system = config.get("system")
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": str(prompt)})

    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "think": False,
        "reasoning_effort": "none",
    }

    t0 = time.perf_counter()
    resp = requests.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    wall_ms = (time.perf_counter() - t0) * 1000.0

    choice = data["choices"][0]
    message = choice.get("message", {})
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning_content") or message.get("reasoning") or "").strip()

    usage = data.get("usage", {}) or {}
    return content, StageTiming(
        stage="llm",
        wall_ms=wall_ms,
        input_size=int(usage.get("prompt_tokens", 0)) or None,
        output_size=int(usage.get("completion_tokens", 0)) or None,
        extra={
            "model": model,
            "finish_reason": choice.get("finish_reason"),
            "completion": content,
            "usage": usage,
        },
    )


# ---------------------------------------------------------------------------
# TTS — kokoro (only engine in repo; abstracted so new engines drop in)
# ---------------------------------------------------------------------------
def _load_tts(engine: str, voice: str, lang: str, speed: float, device: str):
    global _tts_engine, _tts_key
    key = (engine, voice, lang, float(speed), device)
    if _tts_engine is not None and _tts_key == key:
        return _tts_engine, 0.0
    unload_tts()
    t0 = time.perf_counter()
    if engine == "kokoro":
        from kokoro import KPipeline  # type: ignore
        pipe = KPipeline(lang_code=lang, device=device)
        if device == "cpu":
            try:
                for p in pipe.model.parameters():
                    p.data = p.data.to("cpu")
            except Exception:
                pass
        _tts_engine = ("kokoro", pipe, voice, speed)
    else:
        raise ValueError(f"unknown tts engine: {engine!r}")
    _tts_key = key
    return _tts_engine, (time.perf_counter() - t0) * 1000.0


def unload_tts() -> None:
    global _tts_engine, _tts_key
    _tts_engine = None
    _tts_key = None
    import gc
    gc.collect()
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def run_tts(text: str, *, config: dict) -> tuple[tuple[bytes, str], StageTiming]:
    """Synthesize TTS. Config keys:
    - engine (str, default "kokoro")
    - voice (str, default "af_heart")
    - lang (str, default "a")
    - speed (float, default 1.0)
    - device (str, default "cpu" — matches prod; kokoro on same GPU as
      ollama has caused driver crashes per halbot/tts.py)
    """
    import numpy as np
    import soundfile as sf  # type: ignore

    engine_name = config.get("engine", "kokoro")
    voice = config.get("voice", "af_heart")
    lang = config.get("lang", "a")
    speed = float(config.get("speed", 1.0))
    device = config.get("device", "cpu")

    loaded, load_ms = _load_tts(engine_name, voice, lang, speed, device)

    t0 = time.perf_counter()
    if loaded[0] == "kokoro":
        _, pipeline, voice, speed = loaded
        chunks: list[np.ndarray] = []
        for _g, _p, audio in pipeline(text, voice=voice, speed=speed):
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio, dtype="float32"))
        waveform = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
        buf = io.BytesIO()
        sf.write(buf, waveform, 24000, format="WAV", subtype="PCM_16")
        audio_bytes, fmt = buf.getvalue(), "wav"
        sample_count = int(waveform.shape[0])
        sample_rate = 24000
    else:
        raise ValueError(f"unknown engine: {loaded[0]!r}")
    wall_ms = (time.perf_counter() - t0) * 1000.0

    return (audio_bytes, fmt), StageTiming(
        stage="tts",
        wall_ms=wall_ms,
        model_load_ms=load_ms if load_ms > 0 else None,
        input_size=len(text),
        output_size=sample_count,
        extra={
            "engine": engine_name,
            "voice": voice,
            "sample_rate": sample_rate,
            "audio_seconds": sample_count / float(sample_rate) if sample_rate else 0.0,
            "text": text,
        },
    )


def unload_all() -> None:
    unload_stt()
    unload_tts()
