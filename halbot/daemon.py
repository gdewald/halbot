"""Daemon CLI entry. Subcommands: run, setup install/uninstall."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Capture Python-level traceback on native segfault. Writes to a dedicated
# file so it survives the process exit (stderr goes to nssm's rotating
# service log which the user has to hunt through).
_fault_log_path = None
try:
    fault_dir = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Halbot" / "logs"
    fault_dir.mkdir(parents=True, exist_ok=True)
    _fault_log_path = fault_dir / "fault.log"
    _fault_log = open(_fault_log_path, "a", buffering=1)
    faulthandler.enable(file=_fault_log, all_threads=True)
except Exception:
    faulthandler.enable()  # fall back to stderr

# OpenMP runtime clash mitigation: torch/ctranslate2/numpy all ship their own
# copy of libiomp5md.dll; when two get loaded into the same process OpenMP
# asserts and aborts the interpreter with an access violation inside
# msvcp140.dll (seen during `import kokoro` on voice-join). Allowing
# duplicate lib loads trades an extra ~0.1% runtime for surviving.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Keep MKL on sequential threading so it never races with torch's OpenMP pool.
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

# Phone-home / telemetry suppression. MUST be set before any HF / spacy /
# huggingface_hub import — voice.py and tts.py defer those imports inside
# their lazy loaders, so setting here covers them. Always-on (no downside):
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
# HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE flipped after config load below
# (gated on `models_offline`).

from . import config, logging_setup

log = logging.getLogger(__name__)


def _version() -> str:
    try:
        from . import _build_info  # type: ignore
        return _build_info.BUILD_TIMESTAMP
    except Exception:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z") + " (source)"


async def _run_bot(bot_module) -> None:
    try:
        await bot_module.run()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("discord bot task crashed")


async def _run_async() -> int:
    from .mgmt_server import serve

    config.load()
    # Apply offline-mode env vars now that config is loaded; these only need
    # to be in place before the lazy whisper/kokoro imports inside voice.py /
    # tts.py, both of which fire well after this point.
    if str(config.get("models_offline")).lower() in ("1", "true", "yes"):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    logging_setup.init(level=config.get("log_level"))
    from . import transcript_log
    transcript_log.init()
    log.info("halbot daemon starting, version=%s", _version())

    started = time.time()
    server = await serve(started=started, version=_version())

    from . import analytics
    analytics.init()
    analytics.bind_loop(asyncio.get_running_loop())
    import os as _os
    analytics.record("daemon_boot", target=_version(), pid=_os.getpid())

    stop_event = asyncio.Event()

    def _stop(*_a) -> None:
        log.info("stop signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, _stop)

    from . import bot as bot_module

    from . import llm as llm_mod

    # Optional: preload Kokoro at boot so the first user-facing voice turn
    # never eats the ~3-25s cold load. RSS climbs ~350 MB and stays.
    try:
        if str(config.get("keep_kokoro_warm")).lower() in ("1", "true", "yes", "on"):
            from . import tts as tts_mod
            log.info("[boot] keep_kokoro_warm=true; kicking background Kokoro preload")
            tts_mod.preload_engine_async()
    except Exception:
        log.exception("[boot] Kokoro preload kickoff failed")

    tasks = [
        asyncio.create_task(_run_bot(bot_module), name="discord-bot"),
        asyncio.create_task(analytics.prune_loop(), name="analytics-prune"),
        asyncio.create_task(llm_mod.keepalive_loop(), name="llm-keepalive"),
    ]

    await stop_event.wait()
    for t in tasks:
        t.cancel()
    analytics.record("daemon_shutdown", target=_version())
    analytics.shutdown()
    await server.stop(grace=2)
    log.info("halbot daemon stopped")
    return 0


def _cmd_run(_args) -> int:
    return asyncio.run(_run_async())


def _cmd_setup(args) -> int:
    from . import installer

    if getattr(args, "install", False):
        return installer.install()
    if getattr(args, "uninstall", False):
        return installer.uninstall()
    if getattr(args, "set_secret", None):
        from . import secrets as secrets_mod
        name, value = args.set_secret
        try:
            secrets_mod.set_secret(name, value)
        except PermissionError as e:
            print(f"setup set-secret: permission denied ({e}). Run from elevated shell.", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"setup set-secret: failed: {e}", file=sys.stderr)
            return 1
        print(f"secret '{name}' stored")
        return 0
    print("setup: specify --install, --uninstall, or set-secret NAME VALUE", file=sys.stderr)
    return 2


def _cmd_synth_test(_args) -> int:
    """Load the TTS engine and synthesize one line. Repros voice-join
    crash without needing Discord. Prints which stage crashes."""
    print(f"[synth-test] python={sys.version}", flush=True)
    print(f"[synth-test] frozen={getattr(sys, 'frozen', False)}", flush=True)
    print(f"[synth-test] KMP_DUPLICATE_LIB_OK={os.environ.get('KMP_DUPLICATE_LIB_OK')}", flush=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("[synth-test] stage=import-tts-module", flush=True)
    from . import tts
    print("[synth-test] stage=get-engine", flush=True)
    engine = tts.get_engine()
    if engine is None:
        print("[synth-test] no engine configured", flush=True)
        return 1
    print(f"[synth-test] stage=synth engine={engine.name}", flush=True)
    audio, fmt = engine.synth("Hello from the test path.")
    print(f"[synth-test] OK bytes={len(audio)} fmt={fmt}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="halbot-daemon")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="run the daemon (foreground)")
    sub.add_parser("synth-test", help="run one TTS synth and exit (crash repro)")

    sp = sub.add_parser("setup", help="install/uninstall Windows service + registry, or set-secret")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    g.add_argument(
        "--set-secret",
        nargs=2,
        metavar=("NAME", "VALUE"),
        help="Encrypt VALUE via DPAPI and store under HKLM\\SOFTWARE\\Halbot\\Secrets\\NAME",
    )

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "setup":
        return _cmd_setup(args)
    if args.cmd == "synth-test":
        return _cmd_synth_test(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
