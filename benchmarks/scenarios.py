"""Built-in scenario registry.

Add new scenarios here so suites are diff-able and reproducible.

Baseline mirrors the current production voice path. Sweep builders
(``stt_sweep()``, ``llm_sweep()``, ``tts_sweep()``) each hold every
other axis fixed at baseline and vary one knob.
"""
from __future__ import annotations

import json
from pathlib import Path

from .runner import Scenario


REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "benchmarks" / "corpus"

# Production config read from halbot registry as of 2026-04-24:
#   llm_model    = gemma4:e4b
#   llm_url      = http://localhost:11434/v1/chat/completions
#   tts_engine   = kokoro
#   tts_voice    = af_heart
#   tts_lang     = a
#   tts_speed    = 1.0
PROD_LLM_MODEL = "gemma4:e4b"
PROD_LLM_URL = "http://localhost:11434/v1/chat/completions"
PROD_STT = {"model": "large-v3-turbo", "device": "cuda",
            "compute_type": "float16", "beam_size": 1, "language": "en"}
PROD_LLM = {"url": PROD_LLM_URL, "model": PROD_LLM_MODEL,
            "max_tokens": 512, "temperature": 0.8}
PROD_TTS = {"engine": "kokoro", "voice": "af_heart", "lang": "a",
            "speed": 1.0, "device": "cpu"}


# ---------------------------------------------------------------------------
# Corpus loaders
# ---------------------------------------------------------------------------
def _voice_clips() -> list[Path]:
    clips = sorted((CORPUS / "voice").glob("*.wav"))
    if not clips:
        raise FileNotFoundError(
            f"no voice clips under {CORPUS / 'voice'} — "
            f"run `python -m benchmarks.corpus.generate` to create them"
        )
    return clips


def _prompts() -> list[dict]:
    path = CORPUS / "prompts" / "voice_prompts.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"no prompts file at {path} — copy captured payloads there"
        )
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def _texts() -> list[str]:
    path = CORPUS / "texts" / "tts_texts.txt"
    if not path.exists():
        raise FileNotFoundError(f"no texts file at {path}")
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------
def baseline() -> Scenario:
    """Production config, full chain. The number everything else compares to."""
    return Scenario(
        name="baseline",
        pipeline=["stt", "llm", "tts"],
        stt=dict(PROD_STT),
        llm=dict(PROD_LLM),
        tts=dict(PROD_TTS),
        inputs=list(_voice_clips()),
        warmup=1,
        iterations=3,
        description="Production: whisper large-v3-turbo fp16 + gemma4:e4b + kokoro af_heart.",
    )


# ---------------------------------------------------------------------------
# STT sweep — vary one knob, others at baseline
# ---------------------------------------------------------------------------
def _stt_only(name: str, **overrides) -> Scenario:
    stt = dict(PROD_STT)
    stt.update(overrides)
    return Scenario(
        name=name, pipeline=["stt"], stt=stt,
        inputs=list(_voice_clips()), warmup=1, iterations=5,
        description=f"STT isolated: {overrides}",
    )


def stt_sweep() -> list[Scenario]:
    return [
        _stt_only("stt-baseline"),
        _stt_only("stt-large-v3",        model="large-v3"),
        _stt_only("stt-medium",          model="medium"),
        _stt_only("stt-small-en",        model="small.en"),
        _stt_only("stt-beam3",           beam_size=3),
        _stt_only("stt-beam5",           beam_size=5),
        _stt_only("stt-int8-fp16",       compute_type="int8_float16"),
        _stt_only("stt-int8",            compute_type="int8"),
    ]


# ---------------------------------------------------------------------------
# LLM sweep
# ---------------------------------------------------------------------------
def _llm_only(name: str, **overrides) -> Scenario:
    llm = dict(PROD_LLM)
    llm.update(overrides)
    return Scenario(
        name=name, pipeline=["llm"], llm=llm,
        inputs=_prompts(), warmup=1, iterations=3,
        description=f"LLM isolated: {overrides}",
    )


def llm_sweep() -> list[Scenario]:
    # Only gemma4:e4b is installed on the box today. Add other model
    # lines after `ollama pull <name>`. Sweep still runs end-to-end
    # over the token-budget axis without extra pulls.
    return [
        _llm_only("llm-baseline"),
        _llm_only("llm-max256",     max_tokens=256),
        _llm_only("llm-max384",     max_tokens=384),
        _llm_only("llm-max1024",    max_tokens=1024),
        _llm_only("llm-temp02",     temperature=0.2),
        _llm_only("llm-temp12",     temperature=1.2),
        # Uncomment after pulling the model:
        # _llm_only("llm-qwen2.5-7b", model="qwen2.5:7b"),
        # _llm_only("llm-llama3.2-3b", model="llama3.2:3b"),
    ]


# ---------------------------------------------------------------------------
# TTS sweep
# ---------------------------------------------------------------------------
def _tts_only(name: str, inputs: list[str], **overrides) -> Scenario:
    tts = dict(PROD_TTS)
    tts.update(overrides)
    return Scenario(
        name=name, pipeline=["tts"], tts=tts,
        inputs=inputs, warmup=1, iterations=5,
        description=f"TTS isolated: {overrides}",
    )


def tts_sweep() -> list[Scenario]:
    texts = _texts()
    return [
        _tts_only("tts-baseline",  inputs=texts),
        _tts_only("tts-speed-08",  inputs=texts, speed=0.8),
        _tts_only("tts-speed-12",  inputs=texts, speed=1.2),
        # af_bella / af_sarah / am_adam are known kokoro voices.
        _tts_only("tts-voice-bella", inputs=texts, voice="af_bella"),
        _tts_only("tts-voice-sarah", inputs=texts, voice="af_sarah"),
    ]


# ---------------------------------------------------------------------------
# Full-pipeline variants — pick best-of from each sweep manually, update
# as benchmark numbers come in.
# ---------------------------------------------------------------------------
def full_pipeline() -> list[Scenario]:
    return [
        baseline(),
        Scenario(
            name="full-stt-small",
            pipeline=["stt", "llm", "tts"],
            stt={**PROD_STT, "model": "small.en"},
            llm=dict(PROD_LLM),
            tts=dict(PROD_TTS),
            inputs=list(_voice_clips()),
            warmup=1, iterations=3,
            description="Small STT + baseline LLM + baseline TTS.",
        ),
        Scenario(
            name="full-llm-max256",
            pipeline=["stt", "llm", "tts"],
            stt=dict(PROD_STT),
            llm={**PROD_LLM, "max_tokens": 256},
            tts=dict(PROD_TTS),
            inputs=list(_voice_clips()),
            warmup=1, iterations=3,
            description="Baseline STT + 256-cap LLM + baseline TTS.",
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SUITES: dict[str, callable] = {
    "baseline":  lambda: [baseline()],
    "stt":       stt_sweep,
    "llm":       llm_sweep,
    "tts":       tts_sweep,
    "full":      full_pipeline,
    "all":       lambda: [baseline(), *stt_sweep(), *llm_sweep(), *tts_sweep(), *full_pipeline()],
}


def all_suites() -> dict[str, list[Scenario]]:
    return {k: fn() for k, fn in SUITES.items()}


def get_suite(name: str) -> list[Scenario]:
    if name not in SUITES:
        raise KeyError(f"unknown suite {name!r}; known: {sorted(SUITES)}")
    return SUITES[name]()
