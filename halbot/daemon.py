"""Daemon CLI entry. Subcommands: run, setup install/uninstall."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from datetime import datetime

from . import config, logging_setup

log = logging.getLogger(__name__)


def _version() -> str:
    try:
        from . import _build_info  # type: ignore
        return _build_info.BUILD_TIMESTAMP
    except Exception:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z") + " (source)"


async def _tick_info() -> None:
    while True:
        log.info("tick")
        await asyncio.sleep(5)


async def _tick_debug() -> None:
    while True:
        log.debug("tick")
        await asyncio.sleep(1)


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
    logging_setup.init(level=config.get("log_level"))
    log.info("halbot daemon starting, version=%s", _version())

    started = time.time()
    server = await serve(started=started, version=_version())

    from . import analytics
    analytics.init()
    analytics.bind_loop(asyncio.get_running_loop())

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

    tasks = [
        asyncio.create_task(_tick_info(), name="tick-info"),
        asyncio.create_task(_tick_debug(), name="tick-debug"),
        asyncio.create_task(_run_bot(bot_module), name="discord-bot"),
        asyncio.create_task(analytics.prune_loop(), name="analytics-prune"),
    ]

    await stop_event.wait()
    for t in tasks:
        t.cancel()
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="halbot-daemon")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="run the daemon (foreground)")

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
    return 2


if __name__ == "__main__":
    sys.exit(main())
