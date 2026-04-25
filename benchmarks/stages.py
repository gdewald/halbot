"""Stage wrappers for the STT -> LLM -> TTS pipeline.

Each wrapper is a thin adapter over the production code in ``halbot/``.
It takes the stage input, calls the underlying component, and returns
``(output, StageTiming)``. Wrappers are intentionally dumb — all
configuration/model selection happens in the Scenario passed to the
runner.

Stubs only. Real implementations land with 016-voice-pipeline-benchmarks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StageTiming:
    stage: str  # "stt" | "llm" | "tts"
    wall_ms: float
    # Populated opportunistically from the stage's own telemetry:
    model_load_ms: float | None = None
    input_size: int | None = None   # stt: samples; llm: prompt tokens; tts: chars
    output_size: int | None = None  # stt: chars; llm: completion tokens; tts: samples
    extra: dict[str, Any] | None = None


def run_stt(audio_float32, *, config: dict) -> tuple[str, StageTiming]:
    raise NotImplementedError("stt stage wrapper — see plan 016")


def run_llm(transcript: str, *, config: dict) -> tuple[str, StageTiming]:
    raise NotImplementedError("llm stage wrapper — see plan 016")


def run_tts(text: str, *, config: dict) -> tuple[tuple[bytes, str], StageTiming]:
    raise NotImplementedError("tts stage wrapper — see plan 016")
