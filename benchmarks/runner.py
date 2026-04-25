"""Benchmark runner skeleton.

A Scenario describes ONE run: the pipeline shape, the models/configs for
each stage, and the input corpus. The runner executes it N times
(warmup + measured), collects per-stage timings, and emits a Result.

Stub only. Real implementation in 016-voice-pipeline-benchmarks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .stages import StageTiming


@dataclass(slots=True)
class Scenario:
    name: str
    # Which stages to run, in order. "stt", "llm", "tts".
    # Omit stages to isolate (e.g. ["llm"] for LLM-only bench).
    pipeline: list[str] = field(default_factory=lambda: ["stt", "llm", "tts"])
    # Per-stage config — interpretation is stage-specific (model name,
    # beam size, voice, quantization, etc.).
    stt: dict = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    tts: dict = field(default_factory=dict)
    # Corpus: list of audio clip paths (for stt) or prompt strings (llm/tts).
    inputs: list[Path | str] = field(default_factory=list)
    warmup: int = 1
    iterations: int = 5


@dataclass(slots=True)
class IterationResult:
    scenario: str
    iteration: int
    input_id: str
    total_ms: float
    stages: list[StageTiming]


@dataclass(slots=True)
class ScenarioResult:
    scenario: Scenario
    iterations: list[IterationResult]
    # Aggregate stats filled in by the runner (p50/p95 total, per-stage).
    summary: dict = field(default_factory=dict)


def run_scenario(scenario: Scenario) -> ScenarioResult:
    raise NotImplementedError("scenario runner — see plan 016")


def run_suite(scenarios: list[Scenario], *, out_dir: Path) -> list[ScenarioResult]:
    raise NotImplementedError("suite runner — see plan 016")
