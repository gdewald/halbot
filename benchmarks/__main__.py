"""CLI:

    uv run python -m benchmarks list
    uv run python -m benchmarks run baseline
    uv run python -m benchmarks run stt           # full sweep
    uv run python -m benchmarks compare a.json b.json
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import results as results_mod
from . import scenarios as scenarios_mod
from .runner import run_suite


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_list(args: argparse.Namespace) -> int:
    for name, fn in scenarios_mod.SUITES.items():
        suite = fn()
        print(f"{name}: {len(suite)} scenario(s)")
        for sc in suite:
            pipe = "->".join(sc.pipeline)
            print(f"    {sc.name:<30} [{pipe}]  {sc.description}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _configure_logging()
    try:
        suite = scenarios_mod.get_suite(args.suite)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[out] {out_dir}")

    all_results = run_suite(suite, progress=lambda m: print(m))

    for r in all_results:
        path = results_mod.write_jsonl(r, out_dir)
        print(f"[jsonl] {path}")
    summary_path = results_mod.write_summary(all_results, out_dir)
    print(f"[summary] {summary_path}")

    md = results_mod.render_markdown(all_results)
    md_path = out_dir / "report.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"[report] {md_path}")
    print()
    print(md)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    md = results_mod.compare(
        Path(args.baseline), Path(args.candidate),
        regression_pct=args.regression_pct,
    )
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="benchmarks")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Show known suites and scenarios.")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="Run a named suite.")
    p_run.add_argument("suite", help=f"One of: {', '.join(scenarios_mod.SUITES)}")
    p_run.add_argument("--out", default="benchmarks/_out",
                       help="Output root (default: benchmarks/_out).")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="Diff two summary.json files.")
    p_cmp.add_argument("baseline")
    p_cmp.add_argument("candidate")
    p_cmp.add_argument("--regression-pct", type=float, default=10.0,
                       help="Flag a scenario if its p95 grew past this percent.")
    p_cmp.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
