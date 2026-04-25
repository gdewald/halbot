"""CLI entry: ``uv run python -m benchmarks [scenario-name] [--compare path]``.

Stub only. Real CLI parsing + scenario dispatch in 016-voice-pipeline-benchmarks.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmarks")
    parser.add_argument("scenario", nargs="?", default="baseline",
                        help="Scenario name (default: baseline).")
    parser.add_argument("--list", action="store_true",
                        help="List registered scenarios and exit.")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "CANDIDATE"),
                        help="Render a diff between two result files.")
    parser.add_argument("--out", default="benchmarks/_out",
                        help="Output directory for JSONL + summary.")
    args = parser.parse_args(argv)
    _ = args  # silence until real dispatch lands
    print("benchmarks: scaffold only — see docs/plans/drafts/016-voice-pipeline-benchmarks.md",
          file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
