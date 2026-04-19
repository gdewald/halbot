"""Async gRPC Mgmt server."""

from __future__ import annotations

import asyncio
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

_RESTART_DISCORD_MIN_INTERVAL = 10.0


def _state_msg() -> mgmt_pb2.ConfigState:
    snap = config.snapshot()
    fields = {
        name: mgmt_pb2.StringValue(value=str(val), source=_SOURCE_MAP[src])
        for name, (val, src) in snap.items()
    }
    return mgmt_pb2.ConfigState(fields=fields)


class MgmtService(mgmt_pb2_grpc.MgmtServicer):
    def __init__(self, started: float, version: str) -> None:
        self._started = started
        self._version = version
        self._last_restart_discord = 0.0
        self._discord_lock = asyncio.Lock()
        self._whisper_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()

    async def Health(self, request, context):
        from . import bot as bot_module
        from . import llm as llm_module
        try:
            from . import voice_session
        except ImportError:
            voice_session = None

        voice_msg = mgmt_pb2.VoiceState(idle=True)
        whisper_loaded = False
        tts_loaded = False
        if voice_session is not None:
            active = getattr(voice_session, "voice_listeners", {})
            if active:
                gid, sess = next(iter(active.items()))
                try:
                    cid = sess.vc.channel.id
                except Exception:
                    cid = 0
                voice_msg = mgmt_pb2.VoiceState(
                    in_channel=mgmt_pb2.VoiceInChannel(guild_id=gid, channel_id=cid)
                )
            try:
                from . import voice as voice_mod
                whisper_loaded = voice_mod._whisper_model is not None
            except Exception:
                pass
            try:
                from . import tts as tts_mod
                tts_loaded = tts_mod.engine_loaded()
            except Exception:
                pass

        return mgmt_pb2.HealthReply(
            uptime_seconds=time.time() - self._started,
            daemon_version=self._version,
            discord=bot_module.discord_state_proto(),
            llm_reachable=await asyncio.to_thread(llm_module.is_reachable_cached),
            voice=voice_msg,
            whisper_loaded=whisper_loaded,
            tts_loaded=tts_loaded,
        )

    async def GetConfig(self, request, context):
        return _state_msg()

    async def UpdateConfig(self, request, context):
        updates = {k: v for k, v in request.updates.items() if k in config.DEFAULTS}
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
        logging_setup.reconfigure(config.get("log_level"))
        return _state_msg()

    async def SetSecret(self, request, context):
        from . import secrets as secrets_mod
        from . import bot as bot_module

        if not request.name:
            return mgmt_pb2.StatusReply(ok=False, message="name required")
        try:
            secrets_mod.set_secret(request.name, request.value)
        except Exception as e:
            log.exception("SetSecret persist failed")
            return mgmt_pb2.StatusReply(ok=False, message=f"persist failed: {e}")
        if request.name == "DISCORD_TOKEN":
            asyncio.create_task(bot_module.reconnect())
        return mgmt_pb2.StatusReply(ok=True, message="secret stored")

    async def RestartDiscord(self, request, context):
        if self._discord_lock.locked():
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(ok=False, message="restart already in progress")
        now = time.time()
        if now - self._last_restart_discord < _RESTART_DISCORD_MIN_INTERVAL:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(
                ok=False,
                message=f"rate-limited: wait {_RESTART_DISCORD_MIN_INTERVAL:.0f}s between restarts",
            )
        self._last_restart_discord = now
        async with self._discord_lock:
            from . import bot as bot_module
            try:
                await bot_module.reconnect()
            except Exception as e:
                return mgmt_pb2.StatusReply(ok=False, message=f"reconnect failed: {e}")
        return mgmt_pb2.StatusReply(ok=True, message="discord reconnected")

    async def LeaveVoice(self, request, context):
        try:
            from . import voice_session
        except ImportError:
            return mgmt_pb2.StatusReply(ok=False, message="voice unavailable")
        active = list(voice_session.voice_listeners.values())
        for sess in active:
            try:
                await sess.vc.disconnect(force=True)
            except Exception:
                pass
        voice_session.voice_listeners.clear()
        return mgmt_pb2.StatusReply(ok=True, message=f"left {len(active)} voice session(s)")

    async def LoadWhisper(self, request, context):
        if self._whisper_lock.locked():
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(ok=False, message="whisper op in progress")
        async with self._whisper_lock:
            try:
                from . import voice as voice_mod
            except ImportError:
                return mgmt_pb2.StatusReply(ok=False, message="voice module unavailable")
            try:
                await asyncio.to_thread(voice_mod.load_whisper)
            except Exception as e:
                return mgmt_pb2.StatusReply(ok=False, message=f"load failed: {e}")
        return mgmt_pb2.StatusReply(ok=True, message="whisper loaded")

    async def UnloadWhisper(self, request, context):
        if self._whisper_lock.locked():
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(ok=False, message="whisper op in progress")
        try:
            from . import voice_session
            if voice_session.voice_listeners:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                return mgmt_pb2.StatusReply(
                    ok=False, message="voice session active; leave voice first"
                )
        except ImportError:
            pass
        async with self._whisper_lock:
            try:
                from . import voice as voice_mod
                voice_mod.unload_whisper()
            except Exception as e:
                return mgmt_pb2.StatusReply(ok=False, message=f"unload failed: {e}")
        return mgmt_pb2.StatusReply(ok=True, message="whisper unloaded")

    async def LoadTTS(self, request, context):
        if self._tts_lock.locked():
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(ok=False, message="tts op in progress")
        async with self._tts_lock:
            try:
                from . import tts as tts_mod
                await asyncio.to_thread(tts_mod.get_engine)
            except Exception as e:
                return mgmt_pb2.StatusReply(ok=False, message=f"load failed: {e}")
        return mgmt_pb2.StatusReply(ok=True, message="tts loaded")

    async def UnloadTTS(self, request, context):
        if self._tts_lock.locked():
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            return mgmt_pb2.StatusReply(ok=False, message="tts op in progress")
        async with self._tts_lock:
            try:
                from . import tts as tts_mod
                tts_mod.unload_engine()
            except Exception as e:
                return mgmt_pb2.StatusReply(ok=False, message=f"unload failed: {e}")
        return mgmt_pb2.StatusReply(ok=True, message="tts unloaded")


async def serve(started: float, version: str) -> grpc.aio.Server:
    server = grpc.aio.server()
    mgmt_pb2_grpc.add_MgmtServicer_to_server(
        MgmtService(started=started, version=version), server
    )
    server.add_insecure_port(BIND)
    await server.start()
    log.info("mgmt gRPC listening on %s", BIND)
    return server
