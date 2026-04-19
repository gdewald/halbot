"""Consume StreamLogs RPC; buffer for pull-style frontend polling."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

import grpc

from halbot._gen import mgmt_pb2, mgmt_pb2_grpc

log = logging.getLogger(__name__)

TARGET = "127.0.0.1:50199"
MAX_BUFFER = 2000


class LogStream:
    def __init__(self, target: str = TARGET) -> None:
        self._target = target
        self._ring: Deque[Dict] = deque(maxlen=MAX_BUFFER)
        self._pending: Deque[Dict] = deque(maxlen=MAX_BUFFER)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="log-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with grpc.insecure_channel(self._target) as ch:
                    stub = mgmt_pb2_grpc.MgmtStub(ch)
                    req = mgmt_pb2.StreamLogsRequest(backlog=200, min_level="")
                    for line in stub.StreamLogs(req):
                        if self._stop.is_set():
                            break
                        rec = {
                            "ts_ns": int(line.ts_unix_nanos),
                            "level": line.level,
                            "source": line.source,
                            "message": line.message,
                        }
                        with self._lock:
                            self._ring.append(rec)
                            self._pending.append(rec)
            except grpc.RpcError as e:
                log.info("log stream disconnect (%s); retrying in 2s", e.code() if hasattr(e, "code") else e)
                time.sleep(2.0)
            except Exception as e:
                log.warning("log stream error: %s", e)
                time.sleep(2.0)

    def backlog(self, n: int) -> List[Dict]:
        with self._lock:
            return list(self._ring)[-max(0, n):]

    def pop_batch(self, max_n: int) -> List[Dict]:
        with self._lock:
            out: List[Dict] = []
            while self._pending and len(out) < max_n:
                out.append(self._pending.popleft())
            return out
