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


async def _run_async() -> int:
    from .mgmt_server import serve

    config.load()
    logging_setup.init(level=config.get("log_level"))
    log.info("halbot daemon starting, version=%s", _version())

    started = time.time()
    server = await serve(started=started, version=_version())

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

    tasks = [
        asyncio.create_task(_tick_info(), name="tick-info"),
        asyncio.create_task(_tick_debug(), name="tick-debug"),
    ]

    await stop_event.wait()
    for t in tasks:
        t.cancel()
    await server.stop(grace=2)
    log.info("halbot daemon stopped")
    return 0


def _cmd_run(_args) -> int:
    return asyncio.run(_run_async())


def _cmd_setup(args) -> int:
    from . import installer

    if args.install:
        return installer.install()
    if args.uninstall:
        return installer.uninstall()
    print("setup: specify --install or --uninstall", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="halbot-daemon")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="run the daemon (foreground)")

    sp = sub.add_parser("setup", help="install/uninstall Windows service + registry")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--uninstall", action="store_true")

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
