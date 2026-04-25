"""Result persistence + reporting.

Writes each run to ``benchmarks/_out/<timestamp>-<scenario>.jsonl`` (raw
per-iteration rows) + ``summary.json`` (aggregate stats). Comparison
reports diff two runs or render a markdown table across scenarios.

Stub only. Real implementation in 016-voice-pipeline-benchmarks.
"""
from __future__ import annotations

from pathlib import Path

from .runner import ScenarioResult


def write_jsonl(result: ScenarioResult, out_dir: Path) -> Path:
    raise NotImplementedError("results writer — see plan 016")


def render_markdown(results: list[ScenarioResult]) -> str:
    raise NotImplementedError("markdown report — see plan 016")


def compare(baseline_path: Path, candidate_path: Path) -> str:
    raise NotImplementedError("run-to-run diff — see plan 016")
