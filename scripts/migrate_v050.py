"""Migrate halbot v0.5.0 state (.env + sounds.db) into v0.6 storage.

Run from elevated shell. Idempotent. See
docs/plans/006-project-restructure-phase3.md.

Usage:
    python scripts/migrate_v050.py --repo . [--dry-run] [--force]
    python scripts/migrate_v050.py --env /path/to/.env --db /path/to/sounds.db

Effects:
    .env DISCORD_TOKEN          -> DPAPI HKLM\\SOFTWARE\\Halbot\\Secrets
    .env LMSTUDIO_*/VOICE_*/... -> HKLM\\SOFTWARE\\Halbot\\Config (registry)
    ./sounds.db                 -> %ProgramData%\\Halbot\\sounds.db
                                   (or ./_dev_data/sounds.db when running
                                    from a source tree)
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

# Allow running as `python scripts/migrate_v050.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halbot import config as cfg
from halbot import paths
from halbot import secrets as secrets_mod

log = logging.getLogger("migrate_v050")

# .env KEY -> registry field name. DISCORD_TOKEN handled separately (DPAPI).
KEY_MAP: Dict[str, str] = {
    "LOG_LEVEL": "log_level",
    "LMSTUDIO_URL": "llm_url",
    "LMSTUDIO_MODEL": "llm_model",
    "VOICE_IDLE_TIMEOUT_SECONDS": "voice_idle_timeout_seconds",
    "VOICE_HISTORY_TURNS": "voice_history_turns",
    "VOICE_LLM_COMBINE_CALLS": "voice_llm_combine_calls",
    "TTS_ENGINE": "tts_engine",
    "KOKORO_VOICE": "tts_voice",
    "KOKORO_LANG": "tts_lang",
    "KOKORO_SPEED": "tts_speed",
}


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _parse_env(path: Path) -> Dict[str, str]:
    """Minimal .env parser. Handles KEY=VALUE, quoted values, # comments, blank lines."""
    out: Dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            log.warning("%s:%d unparseable line: %s", path, lineno, line)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional export prefix.
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        # Strip matched surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Strip trailing inline comment for unquoted values.
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def migrate_secrets(env: Dict[str, str], force: bool, dry_run: bool) -> int:
    token = env.get("DISCORD_TOKEN", "").strip()
    if not token or token.lower() in ("your_discord_bot_token_here", "changeme"):
        log.info("no usable DISCORD_TOKEN in .env; skipping")
        return 0
    existing = secrets_mod.get_secret("DISCORD_TOKEN")
    if existing and not force:
        log.info("DISCORD_TOKEN already set in DPAPI; skipping (--force to overwrite)")
        return 0
    if dry_run:
        log.info("[dry-run] would write DISCORD_TOKEN to DPAPI (len=%d)", len(token))
        return 0
    secrets_mod.set_secret("DISCORD_TOKEN", token)
    log.info("DISCORD_TOKEN stored via DPAPI")
    return 1


def migrate_config(env: Dict[str, str], dry_run: bool) -> int:
    updates: Dict[str, str] = {}
    for env_key, cfg_key in KEY_MAP.items():
        if env_key in env and env[env_key] != "":
            updates[cfg_key] = env[env_key]
    unknown = [k for k in env if k not in KEY_MAP and k != "DISCORD_TOKEN"]
    if unknown:
        log.info("ignoring unknown .env keys: %s", ", ".join(sorted(unknown)))
    if not updates:
        log.info("no config keys to migrate")
        return 0
    if dry_run:
        for k, v in updates.items():
            log.info("[dry-run] would set %s=%s", k, v)
        return 0
    cfg.load()
    cfg.update(updates)
    cfg.persist(list(updates.keys()))
    for k, v in updates.items():
        log.info("config %s=%s persisted", k, v)
    return len(updates)


def migrate_db(src: Path, force: bool, dry_run: bool) -> int:
    if not src.exists():
        log.info("no %s; skipping db copy", src)
        return 0
    dst = paths.data_dir() / "sounds.db"
    if dst.exists() and not force:
        log.info("%s already exists; skipping (--force to overwrite)", dst)
        return 0
    if dry_run:
        log.info("[dry-run] would copy %s -> %s", src, dst)
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    log.info("copied %s -> %s", src, dst)
    return 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="repo root (default: cwd); used to locate .env and sounds.db")
    parser.add_argument("--env", type=Path, help="override .env path")
    parser.add_argument("--db", type=Path, help="override sounds.db path")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing DPAPI secret and sounds.db")
    parser.add_argument("--dry-run", action="store_true", help="print actions, write nothing")
    parser.add_argument("--skip-secrets", action="store_true")
    parser.add_argument("--skip-config", action="store_true")
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.dry_run and not _is_admin():
        log.error("migrate_v050: must run from elevated shell (DPAPI + HKLM writes)")
        return 1

    env_path = args.env or (args.repo / ".env")
    db_path = args.db or (args.repo / "sounds.db")

    env: Dict[str, str] = {}
    if env_path.exists():
        env = _parse_env(env_path)
        log.info("parsed %d keys from %s", len(env), env_path)
    else:
        log.info("no .env at %s; secret/config phases will no-op", env_path)

    total = 0
    if not args.skip_secrets:
        total += migrate_secrets(env, force=args.force, dry_run=args.dry_run)
    if not args.skip_config:
        total += migrate_config(env, dry_run=args.dry_run)
    if not args.skip_db:
        total += migrate_db(db_path, force=args.force, dry_run=args.dry_run)

    log.info("migration complete (%d writes%s)", total, " [dry-run]" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
