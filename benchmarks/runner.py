"""Benchmark runner.

One Scenario = one fixed pipeline shape + config, exercised N times over
a corpus. Runner collects per-stage timings, aggregates to p50/p95/mean,
emits a ScenarioResult. Suite = list of scenarios run sequentially with
GPU isolation between runs.
"""
from __future__ import annotations

import gc
import io
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import stages
from .stages import StageTiming

log = logging.getLogger("benchmarks")


@dataclass(slots=True)
class Scenario:
    name: str
    pipeline: list[str] = field(default_factory=lambda: ["stt", "llm", "tts"])
    stt: dict = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    tts: dict = field(default_factory=dict)
    inputs: list[Any] = field(default_factory=list)
    warmup: int = 1
    iterations: int = 5
    description: str = ""


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
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# GPU telemetry
# ---------------------------------------------------------------------------
def _gpu_mem_used_mb() -> float | None:
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(h)
            return info.used / (1024 * 1024)
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage dispatch
# ---------------------------------------------------------------------------
def _run_stage(stage: str, stage_input: Any, scenario: Scenario) -> tuple[Any, StageTiming]:
    if stage == "stt":
        return stages.run_stt(stage_input, config=scenario.stt)
    if stage == "llm":
        return stages.run_llm(stage_input, config=scenario.llm)
    if stage == "tts":
        return stages.run_tts(stage_input, config=scenario.tts)
    raise ValueError(f"unknown stage: {stage!r}")


def _input_id(inp: Any) -> str:
    if isinstance(inp, Path):
        return inp.name
    if isinstance(inp, str):
        return inp[:40].replace("\n", " ")
    if isinstance(inp, dict):
        return inp.get("id") or f"dict#{id(inp) & 0xffff:04x}"
    return repr(inp)[:40]


def _run_once(scenario: Scenario, inp: Any, iteration: int) -> IterationResult:
    stage_input = inp
    timings: list[StageTiming] = []
    total_t0 = time.perf_counter()
    for stage in scenario.pipeline:
        output, timing = _run_stage(stage, stage_input, scenario)
        timings.append(timing)
        # Chain: stt -> str, llm -> str, tts -> (bytes, fmt). Next stage
        # only consumes the first two; tts is always terminal.
        stage_input = output
    total_ms = (time.perf_counter() - total_t0) * 1000.0
    return IterationResult(
        scenario=scenario.name,
        iteration=iteration,
        input_id=_input_id(inp),
        total_ms=total_ms,
        stages=timings,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    def pct(p: float) -> float:
        if len(s) == 1:
            return s[0]
        k = (len(s) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)
    return {
        "n": len(s),
        "mean": statistics.fmean(s),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "min": s[0],
        "max": s[-1],
        "stdev": statistics.pstdev(s) if len(s) > 1 else 0.0,
    }


def _summarize(iters: list[IterationResult], pipeline: list[str]) -> dict:
    summary: dict = {"total": _stats([it.total_ms for it in iters])}
    for i, stage in enumerate(pipeline):
        summary[stage] = _stats([it.stages[i].wall_ms for it in iters if i < len(it.stages)])
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_scenario(scenario: Scenario, *, progress: Callable[[str], None] | None = None) -> ScenarioResult:
    if progress is None:
        progress = lambda msg: log.info("%s", msg)
    if not scenario.inputs:
        raise ValueError(f"scenario {scenario.name!r} has no inputs")

    progress(f"[{scenario.name}] warmup x{scenario.warmup}")
    # Warmup absorbs cold-load. Use the first corpus input.
    for w in range(scenario.warmup):
        _run_once(scenario, scenario.inputs[0], iteration=-1 - w)

    iterations: list[IterationResult] = []
    total = scenario.iterations * len(scenario.inputs)
    done = 0
    for i in range(scenario.iterations):
        for inp in scenario.inputs:
            it = _run_once(scenario, inp, iteration=i)
            iterations.append(it)
            done += 1
            stage_summary = " ".join(f"{t.stage}={t.wall_ms:.0f}ms" for t in it.stages)
            progress(f"[{scenario.name}] {done}/{total} total={it.total_ms:.0f}ms {stage_summary}")

    summary = _summarize(iterations, scenario.pipeline)
    summary["gpu_mem_used_mb"] = _gpu_mem_used_mb()
    return ScenarioResult(scenario=scenario, iterations=iterations, summary=summary)


def run_suite(scenarios: list[Scenario], *, progress: Callable[[str], None] | None = None) -> list[ScenarioResult]:
    if progress is None:
        progress = lambda msg: log.info("%s", msg)
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        try:
            res = run_scenario(scenario, progress=progress)
            results.append(res)
        except Exception as e:
            progress(f"[{scenario.name}] SKIPPED: {type(e).__name__}: {e}")
            log.exception("scenario %s failed", scenario.name)
        # Isolation between scenarios — don't let a huge model resident
        # from scenario N skew the load cost of scenario N+1.
        stages.unload_all()
        gc.collect()
    return results
