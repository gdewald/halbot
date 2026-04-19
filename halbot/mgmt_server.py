"""Async gRPC Mgmt server."""

from __future__ import annotations

import logging
import time
from typing import Optional

import grpc

from . import config, logging_setup
from ._gen import mgmt_pb2, mgmt_pb2_grpc

log = logging.getLogger(__name__)

BIND = "127.0.0.1:50199"

_SOURCE_MAP = {
    config.Source.DEFAULT: mgmt_pb2.CONFIG_SOURCE_DEFAULT,
    config.Source.REGISTRY: mgmt_pb2.CONFIG_SOURCE_REGISTRY,
    config.Source.RUNTIME_OVERRIDE: mgmt_pb2.CONFIG_SOURCE_RUNTIME_OVERRIDE,
}


def _state_msg() -> mgmt_pb2.ConfigState:
    snap = config.snapshot()
    val, src = snap["log_level"]
    return mgmt_pb2.ConfigState(
        log_level=mgmt_pb2.StringValue(value=str(val), source=_SOURCE_MAP[src])
    )


class MgmtService(mgmt_pb2_grpc.MgmtServicer):
    def __init__(self, started: float, version: str) -> None:
        self._started = started
        self._version = version

    async def Health(self, request, context):
        return mgmt_pb2.HealthReply(
            uptime_seconds=time.time() - self._started,
            daemon_version=self._version,
        )

    async def GetConfig(self, request, context):
        return _state_msg()

    async def UpdateConfig(self, request, context):
        updates = {}
        if request.config.log_level.value:
            updates["log_level"] = request.config.log_level.value
        if updates:
            config.update(updates)
            if "log_level" in updates:
                logging_setup.reconfigure(updates["log_level"])
        return _state_msg()

    async def PersistConfig(self, request, context):
        config.persist(list(request.fields) or None)
        return _state_msg()

    async def ResetConfig(self, request, context):
        fields = list(request.fields) or None
        config.reset(fields)
        # Re-apply log level in case it changed.
        logging_setup.reconfigure(config.get("log_level"))
        return _state_msg()


async def serve(started: float, version: str) -> grpc.aio.Server:
    server = grpc.aio.server()
    mgmt_pb2_grpc.add_MgmtServicer_to_server(
        MgmtService(started=started, version=version), server
    )
    server.add_insecure_port(BIND)
    await server.start()
    log.info("mgmt gRPC listening on %s", BIND)
    return server
