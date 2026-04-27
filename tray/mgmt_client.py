"""Sync gRPC client wrapper with auto-reconnect."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import grpc

from halbot._gen import mgmt_pb2, mgmt_pb2_grpc

log = logging.getLogger(__name__)

TARGET = "127.0.0.1:50199"


class MgmtClient:
    def __init__(self, target: str = TARGET) -> None:
        self._target = target
        self._lock = threading.Lock()
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[mgmt_pb2_grpc.MgmtStub] = None

    def _stub_ready(self) -> mgmt_pb2_grpc.MgmtStub:
        with self._lock:
            if self._stub is None:
                self._channel = grpc.insecure_channel(self._target)
                self._stub = mgmt_pb2_grpc.MgmtStub(self._channel)
            return self._stub

    def _reset(self) -> None:
        with self._lock:
            if self._channel is not None:
                try:
                    self._channel.close()
                except Exception:
                    pass
            self._channel = None
            self._stub = None

    def _call(self, method_name: str, request, timeout: float = 2.0):
        try:
            stub = self._stub_ready()
            method = getattr(stub, method_name)
            return method(request, timeout=timeout)
        except grpc.RpcError as e:
            self._reset()
            raise e

    def health(self):
        return self._call("Health", mgmt_pb2.HealthRequest())

    def get_config(self):
        return self._call("GetConfig", mgmt_pb2.GetConfigRequest())

    def update_log_level(self, level: str):
        return self.update_config({"log_level": level})

    def update_config(self, updates: dict):
        req = mgmt_pb2.UpdateConfigRequest(updates=updates)
        return self._call("UpdateConfig", req)

    def set_secret(self, name: str, value: str):
        return self._call("SetSecret", mgmt_pb2.SetSecretRequest(name=name, value=value))

    def restart_discord(self):
        return self._call("RestartDiscord", mgmt_pb2.Empty())

    def leave_voice(self):
        return self._call("LeaveVoice", mgmt_pb2.Empty())

    def persist(self, fields=None):
        return self._call(
            "PersistConfig", mgmt_pb2.PersistConfigRequest(fields=fields or [])
        )

    def reset(self, fields=None):
        return self._call(
            "ResetConfig", mgmt_pb2.ResetConfigRequest(fields=fields or [])
        )

    def get_stats(self):
        return self._call("GetStats", mgmt_pb2.Empty())

    def query_stats(self, *, kind="", user_id=0, target="",
                    ts_from=0, ts_to=0, group_by="", limit=100):
        req = mgmt_pb2.QueryStatsRequest(
            kind=kind, user_id=int(user_id or 0), target=target,
            ts_from=int(ts_from or 0), ts_to=int(ts_to or 0),
            group_by=group_by, limit=int(limit or 100),
        )
        return self._call("QueryStats", req, timeout=5.0)

    def wake_history(self, limit=25):
        return self._call(
            "WakeHistory",
            mgmt_pb2.WakeHistoryRequest(limit=int(limit or 0)),
        )

    def stream_events(self, *, backlog=0, kind="", user_id=0):
        req = mgmt_pb2.StreamEventsRequest(
            backlog=int(backlog or 0), kind=kind, user_id=int(user_id or 0),
        )
        stub = self._stub_ready()
        return stub.StreamEvents(req)

    def wait_ready(self, deadline: float = 5.0) -> bool:
        end = time.time() + deadline
        while time.time() < end:
            try:
                self.health()
                return True
            except grpc.RpcError:
                time.sleep(0.2)
        return False
