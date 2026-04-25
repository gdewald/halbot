"""Result persistence + reporting.

JSONL per scenario (one row per iteration), summary.json per run,
markdown table for commit-message embedding, run-to-run diff.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .runner import ScenarioResult


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in name)


def write_jsonl(result: ScenarioResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(result.scenario.name)}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for it in result.iterations:
            row = {
                "scenario": it.scenario,
                "iteration": it.iteration,
                "input_id": it.input_id,
                "total_ms": it.total_ms,
                "stages": [asdict(t) for t in it.stages],
            }
            f.write(json.dumps(row) + "\n")
    return path


def write_summary(results: list[ScenarioResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "summary.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenarios": [
            {
                "name": r.scenario.name,
                "pipeline": r.scenario.pipeline,
                "iterations": r.scenario.iterations,
                "warmup": r.scenario.warmup,
                "stt": r.scenario.stt,
                "llm": r.scenario.llm,
                "tts": r.scenario.tts,
                "description": r.scenario.description,
                "summary": r.summary,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _fmt_ms(v) -> str:
    if v is None:
        return "-"
    return f"{v:.0f}ms"


def render_markdown(results: list[ScenarioResult]) -> str:
    """Table: scenario | n | stt p50/p95 | llm p50/p95 | tts p50/p95 | total p50/p95 | vs baseline."""
    base_total = None
    for r in results:
        if r.scenario.name == "baseline":
            base_total = r.summary.get("total", {}).get("p50")
            break

    headers = [
        "scenario", "n", "stt p50", "stt p95",
        "llm p50", "llm p95", "tts p50", "tts p95",
        "total p50", "total p95", "vs base",
    ]
    rows = [headers]
    for r in results:
        s = r.summary
        stt = s.get("stt", {})
        llm = s.get("llm", {})
        tts = s.get("tts", {})
        total = s.get("total", {})
        delta = ""
        if base_total is not None and total.get("p50") is not None:
            d = total["p50"] - base_total
            delta = f"{d:+.0f}ms"
        rows.append([
            r.scenario.name,
            str(total.get("n", 0)),
            _fmt_ms(stt.get("p50")), _fmt_ms(stt.get("p95")),
            _fmt_ms(llm.get("p50")), _fmt_ms(llm.get("p95")),
            _fmt_ms(tts.get("p50")), _fmt_ms(tts.get("p95")),
            _fmt_ms(total.get("p50")), _fmt_ms(total.get("p95")),
            delta,
        ])

    widths = [max(len(str(row[i])) for row in rows) for i in range(len(headers))]
    def line(row):
        return "| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    out = [line(rows[0]), sep]
    for row in rows[1:]:
        out.append(line(row))
    return "\n".join(out)


def compare(baseline_path: Path, candidate_path: Path, *, regression_pct: float = 10.0) -> str:
    """Render a run-to-run diff. Flags scenarios whose p95 grew past
    ``regression_pct`` percent above baseline."""
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    cand = json.loads(candidate_path.read_text(encoding="utf-8"))
    base_by = {s["name"]: s["summary"] for s in base["scenarios"]}
    cand_by = {s["name"]: s["summary"] for s in cand["scenarios"]}

    lines = [f"# Compare: {baseline_path.name} -> {candidate_path.name}", ""]
    lines.append(f"| scenario | base p50 | cand p50 | d p50 | base p95 | cand p95 | d p95 | flag |")
    lines.append("|----------|----------|----------|-------|----------|----------|-------|------|")
    for name in sorted(set(base_by) | set(cand_by)):
        b = base_by.get(name, {}).get("total", {})
        c = cand_by.get(name, {}).get("total", {})
        bp50, cp50 = b.get("p50"), c.get("p50")
        bp95, cp95 = b.get("p95"), c.get("p95")
        dp50 = (cp50 - bp50) if (bp50 is not None and cp50 is not None) else None
        dp95 = (cp95 - bp95) if (bp95 is not None and cp95 is not None) else None
        flag = ""
        if bp95 and cp95 and cp95 > bp95 * (1 + regression_pct / 100.0):
            flag = f"REGRESSION (+{(cp95 / bp95 - 1) * 100:.1f}%)"
        lines.append(
            f"| {name} | {_fmt_ms(bp50)} | {_fmt_ms(cp50)} | {_fmt_ms(dp50) if dp50 is not None else '-'} "
            f"| {_fmt_ms(bp95)} | {_fmt_ms(cp95)} | {_fmt_ms(dp95) if dp95 is not None else '-'} | {flag} |"
        )
    return "\n".join(lines)
