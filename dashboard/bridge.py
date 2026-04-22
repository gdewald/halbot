"""pywebview js_api bridge. Frontend calls window.pywebview.api.*."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

import psutil

from halbot._gen import mgmt_pb2
from tray import service_ctl
from tray.mgmt_client import MgmtClient

log = logging.getLogger(__name__)


_FIELD_TYPE_NAMES = {
    mgmt_pb2.CONFIG_FIELD_TYPE_UNSPECIFIED: "STRING",
    mgmt_pb2.CONFIG_FIELD_TYPE_STRING: "STRING",
    mgmt_pb2.CONFIG_FIELD_TYPE_NUMBER: "NUMBER",
    mgmt_pb2.CONFIG_FIELD_TYPE_BOOL: "BOOL",
    mgmt_pb2.CONFIG_FIELD_TYPE_SELECT: "SELECT",
    mgmt_pb2.CONFIG_FIELD_TYPE_URL: "URL",
    mgmt_pb2.CONFIG_FIELD_TYPE_RANGE: "RANGE",
}

_SOURCE_NAMES = {
    mgmt_pb2.CONFIG_SOURCE_UNSPECIFIED: "DEFAULT",
    mgmt_pb2.CONFIG_SOURCE_DEFAULT: "DEFAULT",
    mgmt_pb2.CONFIG_SOURCE_REGISTRY: "REGISTRY",
    mgmt_pb2.CONFIG_SOURCE_RUNTIME_OVERRIDE: "RUNTIME_OVERRIDE",
}


class JsApi:
    def __init__(self) -> None:
        self._client = MgmtClient()
        self._window = None  # set by app.py after window creation
        self._log_stream = None  # set by app.py
        self._event_stream = None  # set by app.py

    def bind_window(self, window) -> None:
        self._window = window

    def bind_log_stream(self, stream) -> None:
        self._log_stream = stream

    def bind_event_stream(self, stream) -> None:
        self._event_stream = stream

    # ── Health ───────────────────────────────────────────────
    def health(self) -> Dict[str, Any]:
        h = self._client.health()
        return {
            "uptime_seconds": h.uptime_seconds,
            "daemon_version": h.daemon_version,
            "llm_reachable": h.llm_reachable,
            "whisper_loaded": h.whisper_loaded,
            "tts_loaded": h.tts_loaded,
        }

    # ── Config ───────────────────────────────────────────────
    def get_config(self) -> Dict[str, Any]:
        state = self._client.get_config()
        out = {}
        for name, sv in state.fields.items():
            out[name] = {
                "value": sv.value,
                "source": _SOURCE_NAMES.get(sv.source, "DEFAULT"),
                "type": _FIELD_TYPE_NAMES.get(sv.type, "STRING"),
                "options": list(sv.options),
                "description": sv.description,
                "group": sv.group or "general",
                "label": sv.label or name.upper(),
                "min": sv.min, "max": sv.max, "step": sv.step,
            }
        return out

    def update_config(self, updates: Dict[str, str]) -> Dict[str, Any]:
        self._client.update_config(updates)
        return self.get_config()

    def persist_config(self, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        self._client.persist(fields or [])
        return self.get_config()

    def reset_config(self, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        self._client.reset(fields or [])
        return self.get_config()

    # ── Service control ──────────────────────────────────────
    def service_query(self) -> Dict[str, Any]:
        return service_ctl.query()

    def service_start(self) -> None:
        service_ctl.start()

    def service_stop(self) -> None:
        service_ctl.stop()

    def service_restart(self) -> None:
        service_ctl.restart()

    # ── Process stats via psutil (no daemon RPC) ─────────────
    def proc_stats(self, pid: int) -> Dict[str, Any]:
        if not pid:
            return {"memory_mb": 0, "cpu_pct": 0.0}
        try:
            p = psutil.Process(int(pid))
            mem = p.memory_info().rss / (1024 * 1024)
            cpu = p.cpu_percent(interval=None)
            return {"memory_mb": round(mem, 1), "cpu_pct": round(cpu, 1)}
        except Exception as e:
            log.warning("proc_stats failed for pid %s: %s", pid, e)
            return {"memory_mb": 0, "cpu_pct": 0.0}

    # ── NSSM auto-restart toggle ─────────────────────────────
    def nssm_auto_restart_get(self) -> Optional[bool]:
        # Returns None if NSSM not present or key unreadable — UI hides toggle.
        try:
            import shutil, subprocess
            nssm = shutil.which("nssm")
            if not nssm:
                return None
            r = subprocess.run(
                [nssm, "get", "halbot", "AppExit", "Default"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return None
            return "Restart" in r.stdout
        except Exception:
            return None

    def nssm_auto_restart_set(self, enabled: bool) -> bool:
        try:
            import shutil, subprocess
            nssm = shutil.which("nssm")
            if not nssm:
                return False
            val = "Restart" if enabled else "Exit"
            r = subprocess.run(
                [nssm, "set", "halbot", "AppExit", "Default", val],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    # ── Logs ─────────────────────────────────────────────────
    def backlog_logs(self, n: int = 200) -> List[Dict[str, Any]]:
        if self._log_stream is None:
            return []
        return self._log_stream.backlog(n)

    def pop_log_batch(self, max_n: int = 100) -> List[Dict[str, Any]]:
        if self._log_stream is None:
            return []
        return self._log_stream.pop_batch(max_n)

    # ── Stats ────────────────────────────────────────────────
    def get_stats(self) -> Dict[str, Any]:
        r = self._client.get_stats() if hasattr(self._client, "get_stats") else None
        if r is None:
            return {"mock": True}
        return {"mock": bool(r.mock)}

    # ── Analytics (events) ───────────────────────────────────
    def query_stats(self, kind: str = "", user_id: int = 0, target: str = "",
                    ts_from: int = 0, ts_to: int = 0,
                    group_by: str = "", limit: int = 100) -> Dict[str, Any]:
        try:
            r = self._client.query_stats(
                kind=kind, user_id=int(user_id or 0), target=target,
                ts_from=int(ts_from or 0), ts_to=int(ts_to or 0),
                group_by=group_by, limit=int(limit or 100),
            )
        except Exception as e:
            log.warning("query_stats failed: %s", e)
            return {"total_count": 0, "rows": [], "error": str(e)}
        return {
            "total_count": int(r.total_count),
            "rows": [
                {"key": x.key, "count": int(x.count), "last_ts_unix": int(x.last_ts_unix)}
                for x in r.rows
            ],
        }

    def backlog_events(self, n: int = 50) -> List[Dict[str, Any]]:
        if self._event_stream is None:
            return []
        return self._event_stream.backlog(n)

    def pop_event_batch(self, max_n: int = 100) -> List[Dict[str, Any]]:
        if self._event_stream is None:
            return []
        return self._event_stream.pop_batch(max_n)

    # ── Window chrome ────────────────────────────────────────
    def window_minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def window_maximize(self) -> None:
        if self._window is not None:
            self._window.toggle_fullscreen()

    def window_close(self) -> None:
        if self._window is not None:
            self._window.destroy()
