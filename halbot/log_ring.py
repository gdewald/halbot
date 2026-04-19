"""In-memory log ring + subscriber fan-out for StreamLogs RPC."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Deque, List

MAX_RING = 1000


class LogRecord:
    __slots__ = ("ts_ns", "level", "source", "message")

    def __init__(self, ts_ns: int, level: str, source: str, message: str) -> None:
        self.ts_ns = ts_ns
        self.level = level
        self.source = source
        self.message = message


class _RingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._ring: Deque[LogRecord] = deque(maxlen=MAX_RING)
        self._queues: List[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        rec = LogRecord(
            ts_ns=time.time_ns(),
            level=record.levelname,
            source=record.name,
            message=msg,
        )
        with self._lock:
            self._ring.append(rec)
            queues = list(self._queues)
        loop = self._loop
        if loop is None:
            return
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, rec)
            except Exception:
                pass

    def subscribe(self, backlog: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._queues.append(q)
            if backlog > 0:
                tail = list(self._ring)[-backlog:]
                for rec in tail:
                    try:
                        q.put_nowait(rec)
                    except asyncio.QueueFull:
                        break
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass


_handler = _RingHandler()


def handler() -> _RingHandler:
    return _handler