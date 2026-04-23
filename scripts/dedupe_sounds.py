"""One-time dedupe of saved_sounds rows in a Halbot sqlite db.

Usage:
    uv run python scripts/dedupe_sounds.py [--db PATH] [--dry-run]

Defaults to production db at %ProgramData%\\Halbot\\sounds.db. Soft-deletes
collapsed rows (recoverable via admin undelete). Rewrites parent_id pointers
on surviving rows that referenced a now-tombstoned duplicate.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def default_db() -> str:
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return str(Path(pd) / "Halbot" / "sounds.db")


def main() -> int:
    ap = argparse.ArgumentParser(description="Dedupe saved_sounds rows.")
    ap.add_argument("--db", default=default_db(),
                    help=f"sqlite path (default: {default_db()})")
    ap.add_argument("--dry-run", action="store_true",
                    help="show groups, do not write")
    args = ap.parse_args()

    if not Path(args.db).is_file():
        print(f"ERROR: no db at {args.db}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from halbot.db import db_dedupe_sounds

    print(f"DB: {args.db}")
    print(f"mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    res = db_dedupe_sounds(db_path=args.db, dry_run=args.dry_run)

    print(f"name-dup groups:   {res['name_groups']}")
    print(f"audio-dup groups:  {res['audio_groups']}")
    print(f"rows soft-deleted: {res['soft_deleted']}")
    print(f"parent rewrites:   {res['parent_rewrites']}")

    cmap = res["canonical_map"]
    if cmap:
        print("\ncollapse map (dup_id -> canonical_id):")
        for dup, can in sorted(cmap.items()):
            print(f"  #{dup} -> #{can}")
    prs = res["parent_rewrite_detail"]
    if prs:
        print("\nparent rewrites (row, old, new):")
        for r, o, n in prs:
            print(f"  #{r}: parent #{o} -> #{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
