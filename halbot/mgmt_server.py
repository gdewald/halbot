"""Async gRPC Mgmt server."""

from __future__ import annotations

import asyncio
import logging
import os
import time

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

_TYPE_MAP = {
    "STRING": mgmt_pb2.CONFIG_FIELD_TYPE_STRING,
    "NUMBER": mgmt_pb2.CONFIG_FIELD_TYPE_NUMBER,
    "BOOL":   mgmt_pb2.CONFIG_FIELD_TYPE_BOOL,
    "SELECT": mgmt_pb2.CONFIG_FIELD_TYPE_SELECT,
    "URL":    mgmt_pb2.CONFIG_FIELD_TYPE_URL,
    "RANGE":  mgmt_pb2.CONFIG_FIELD_TYPE_RANGE,
}

_RESTART_DISCORD_MIN_INTERVAL = 10.0

# Persistent user_id -> display_name cache shared across QueryStats RPCs.
# Without this, every dashboard refresh / filter click re-fired
# fetch_member HTTP for the same user_ids, hammering Discord's rate
# limit. Display names rarely change so cache lifetime tied to process
# lifetime is fine; on display-name change the user re-launches the
# tray to clear it. Bounded growth: aggregates only return top-N IDs.
_USER_LABEL_CACHE: dict[int, str] = {}


def _state_msg() -> mgmt_pb2.ConfigState:
    snap = config.snapshot()
    fields = {}
    for name, (val, src) in snap.items():
        schema = config.SCHEMA.get(name, {})
        fields[name] = mgmt_pb2.StringValue(
            value=str(val),
            source=_SOURCE_MAP[src],
            type=_TYPE_MAP.get(schema.get("type", "STRING"), mgmt_pb2.CONFIG_FIELD_TYPE_STRING),
            options=list(schema.get("options", [])),
            description=schema.get("description", ""),
            group=schema.get("group", "general"),
            min=float(schema.get("min", 0.0)),
            max=float(schema.get("max", 0.0)),
            step=float(schema.get("step", 0.0)),
            label=schema.get("label", name.upper()),
        )
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
            except ImportError:
                pass
            except Exception as e:
                log.warning(f"Could not check whisper load status: {e}")
                whisper_loaded = False
            try:
                from . import tts as tts_mod
                tts_loaded = tts_mod.engine_loaded()
            except Exception:
                pass

        pid = os.getpid()
        rss_bytes = 0
        cpu_percent = 0.0
        try:
            import psutil
            proc = self._psutil_proc()
            rss_bytes = int(proc.memory_info().rss)
            cpu_percent = float(proc.cpu_percent(interval=None))
        except Exception:
            pass

        guild_count = 0
        try:
            client = getattr(bot_module, "client", None)
            if client is not None and getattr(client, "guilds", None) is not None:
                guild_count = len(client.guilds)
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
            pid=pid,
            rss_bytes=rss_bytes,
            cpu_percent=cpu_percent,
            guild_count=guild_count,
        )

    def _psutil_proc(self):
        # cpu_percent needs a prior call to seed its delta; cache the Process
        # so successive calls return real percentages instead of 0.0.
        import psutil
        if not hasattr(self, "_psu_proc"):
            self._psu_proc = psutil.Process()
            self._psu_proc.cpu_percent(interval=None)
        return self._psu_proc

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
            log.error(f"SetSecret persist failed due to exception: {type(e).__name__}: {str(e)}")
            return mgmt_pb2.StatusReply(ok=False, message=f"persist failed: {type(e).__name__} - {str(e)[:60]}...")
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
                log.error(f"Discord reconnect failed due to exception: {type(e).__name__}: {str(e)}")
                return mgmt_pb2.StatusReply(ok=False, message=f"reconnect failed: {type(e).__name__} - {str(e)[:60]}...")
        return mgmt_pb2.StatusReply(ok=True, message="discord reconnected")

    async def LeaveVoice(self, request, context):
        try:
            from . import voice_session
        except ImportError:
            return mgmt_pb2.StatusReply(ok=False, message="voice unavailable")
        active = list(voice_session.voice_listeners.items())
        for gid, sess in active:
            try:
                voice_session.emit_voice_leave(sess, reason="rpc")
            except Exception:
                log.exception("[voice] emit_voice_leave failed for guild %s", gid)
            try:
                await sess.vc.disconnect(force=True)
            except Exception as e:
                log.warning(f"Failed to disconnect from voice session for {sess}: {type(e).__name__} - {str(e)}")
                pass
            try:
                from .db import voice_reconnect_clear
                voice_reconnect_clear(gid)
            except Exception:
                log.exception("[voice] Failed to clear reconnect row for guild %s", gid)
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
                log.error(f"Whisper load failed due to exception: {type(e).__name__}: {str(e)}")
                return mgmt_pb2.StatusReply(ok=False, message=f"load failed: {type(e).__name__} - {str(e)[:60]}...")
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
                log.error(f"Whisper unload failed due to exception: {type(e).__name__}: {str(e)}")
                return mgmt_pb2.StatusReply(ok=False, message=f"unload failed: {type(e).__name__} - {str(e)[:60]}...")
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
                log.error(f"TTS load failed due to exception: {type(e).__name__}: {str(e)}")
                return mgmt_pb2.StatusReply(ok=False, message=f"load failed: {type(e).__name__} - {str(e)[:60]}...")
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
                log.error(f"TTS unload failed due to exception: {type(e).__name__}: {str(e)}")
                return mgmt_pb2.StatusReply(ok=False, message=f"unload failed: {type(e).__name__} - {str(e)[:60]}...")
        return mgmt_pb2.StatusReply(ok=True, message="tts unloaded")

    async def StreamLogs(self, request, context):
        from . import log_ring
        min_level = (request.min_level or "").upper()
        level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
        floor = level_rank.get(min_level, 0)
        q = log_ring.handler().subscribe(backlog=max(0, min(request.backlog, 1000)))
        try:
            while True:
                rec = await q.get()
                if floor and level_rank.get(rec.level, 0) < floor:
                    continue
                yield mgmt_pb2.LogLine(
                    ts_unix_nanos=rec.ts_ns,
                    level=rec.level,
                    source=rec.source,
                    message=rec.message,
                )
        finally:
            log_ring.handler().unsubscribe(q)

    async def GetStats(self, request, context):
        from . import analytics
        try:
            data = await asyncio.to_thread(analytics.compute_dashboard_stats)
        except Exception as e:
            log.warning("GetStats compute failed: %s", e)
            return mgmt_pb2.StatsReply(mock=True)
        sb = data.get("soundboard", {})
        vp = data.get("voice_playback", {})
        ww = data.get("wake_word", {})
        stt = data.get("stt", {})
        tts = data.get("tts", {})
        llm = data.get("llm", {})
        return mgmt_pb2.StatsReply(
            soundboard=mgmt_pb2.SoundboardStats(
                sounds_backed_up=int(sb.get("sounds_backed_up", 0)),
                storage_bytes=int(sb.get("storage_bytes", 0)),
                last_sync_unix=int(sb.get("last_sync_unix", 0)),
                new_since_last=int(sb.get("new_since_last", 0)),
            ),
            voice_playback=mgmt_pb2.VoicePlaybackStats(
                played_today=int(vp.get("played_today", 0)),
                played_all_time=int(vp.get("played_all_time", 0)),
                session_seconds_today=int(vp.get("session_seconds_today", 0)),
                avg_response_ms=int(vp.get("avg_response_ms", 0)),
            ),
            wake_word=mgmt_pb2.WakeWordStats(
                detections_today=int(ww.get("detections_today", 0)),
                detections_all_time=int(ww.get("detections_all_time", 0)),
                false_positives_today=int(ww.get("false_positives_today", 0)),
            ),
            stt=mgmt_pb2.LatencyStats(
                avg_ms=int(stt.get("avg_ms", 0)),
                p95_ms=int(stt.get("p95_ms", 0)),
                count_today=int(stt.get("count_today", 0)),
                chunk_avg_ms=int(stt.get("chunk_avg_ms", 0)),
                chunk_p95_ms=int(stt.get("chunk_p95_ms", 0)),
                avg_audio_seconds=float(stt.get("avg_audio_seconds", 0.0)),
            ),
            tts=mgmt_pb2.LatencyStats(
                avg_ms=int(tts.get("avg_ms", 0)),
                p95_ms=int(tts.get("p95_ms", 0)),
                count_today=int(tts.get("count_today", 0)),
            ),
            llm=mgmt_pb2.LlmStats(
                response_avg_ms=int(llm.get("response_avg_ms", 0)),
                response_p95_ms=int(llm.get("response_p95_ms", 0)),
                tokens_per_sec=int(llm.get("tokens_per_sec", 0)),
                requests_today=int(llm.get("requests_today", 0)),
                avg_tokens_out=int(llm.get("avg_tokens_out", 0)),
                context_usage_pct=int(llm.get("context_usage_pct", 0)),
                timeouts_today=int(llm.get("timeouts_today", 0)),
            ),
            mock=bool(data.get("mock", False)),
        )

    async def QueryStats(self, request, context):
        from . import analytics, stats_publisher, bot as _bot
        total, rows = await asyncio.to_thread(
            analytics.query_stats,
            kind=request.kind,
            user_id=request.user_id,
            target=request.target,
            ts_from=request.ts_from,
            ts_to=request.ts_to,
            group_by=request.group_by,
            limit=request.limit,
        )
        reply = mgmt_pb2.QueryStatsReply(total_count=total)
        # Pre-resolve user_id → display_name via the async resolver, which
        # walks Member/User caches AND falls through to bounded HTTP
        # fetch_member when the cache misses. Module-level
        # `_USER_LABEL_CACHE` persists across RPCs so repeated dashboard
        # polls don't re-fire HTTP fetch_member (rate-limit hit).
        label_cache: dict[int, str] = dict(_USER_LABEL_CACHE)
        if request.group_by == "user_id":
            user_ids = [int(r["key"]) for r in rows
                        if str(r["key"] or "").isdigit() and int(r["key"]) > 0]
            missing = [uid for uid in user_ids if uid not in _USER_LABEL_CACHE]
            if missing:
                try:
                    resolved = await stats_publisher.resolve_user_labels(
                        _bot.client, missing, known=_USER_LABEL_CACHE,
                    )
                    _USER_LABEL_CACHE.update(resolved)
                    label_cache.update(resolved)
                except Exception:
                    log.exception("QueryStats: resolve_user_labels failed")
        for r in rows:
            label = ""
            if request.group_by == "user_id":
                try:
                    label = stats_publisher._user_label(_bot.client, int(r["key"]), label_cache)
                except Exception:
                    pass
            reply.rows.add(
                key=r["key"], count=r["count"], last_ts_unix=r["last_ts_unix"], label=label
            )
        return reply

    async def WakeHistory(self, request, context):
        from . import analytics
        n = int(request.limit or 0)
        rows = await asyncio.to_thread(analytics.wake_history, n or 25)
        reply = mgmt_pb2.WakeHistoryReply()
        for r in rows:
            reply.rows.add(
                ts_unix=int(r["ts"]),
                phrase=r["phrase"],
                outcome=r["outcome"],
                ok=bool(r["ok"]),
            )
        return reply

    async def StreamEvents(self, request, context):
        from . import analytics, stats_publisher, bot as _bot
        kind = request.kind or ""
        uid = int(request.user_id or 0)
        q = analytics.subscribe(
            backlog=max(0, min(request.backlog, 500)),
            kind=kind,
            user_id=uid,
        )
        label_cache: dict[int, str] = {}
        try:
            while True:
                rec = await q.get()
                if kind and rec.kind != kind:
                    continue
                if uid and rec.user_id != uid:
                    continue
                user_label = ""
                if rec.user_id:
                    try:
                        user_label = stats_publisher._user_label(_bot.client, rec.user_id, label_cache)
                    except Exception:
                        pass
                yield mgmt_pb2.Event(
                    ts_unix_nanos=rec.ts_ns,
                    kind=rec.kind,
                    guild_id=rec.guild_id,
                    user_id=rec.user_id,
                    target=rec.target,
                    meta_json=rec.meta_json,
                    user_label=user_label,
                )
        finally:
            analytics.unsubscribe(q)


async def serve(started: float, version: str) -> grpc.aio.Server:
    from . import log_ring
    log_ring.handler().bind_loop(asyncio.get_running_loop())
    server = grpc.aio.server()
    mgmt_pb2_grpc.add_MgmtServicer_to_server(
        MgmtService(started=started, version=version), server
    )
    server.add_insecure_port(BIND)
    await server.start()
    log.info("mgmt gRPC listening on %s", BIND)
    return server
