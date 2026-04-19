# Step 2 — Dashboard Python Backend

**Goal:** create a `dashboard/` Python package inside the tray
codebase that can open a pywebview window and exposes a `JsApi`
bridge object the frontend will call via
`window.pywebview.api.*`. This step opens a placeholder window
(no React yet) so the Python + WebView2 plumbing is proven before
the frontend lands in step 3.

**Runnable at end:** yes — tray still works as before; a new
manual command `uv run python -m dashboard.app` opens a stub
window.

## Files you will touch

- `pyproject.toml` (edit — add pywebview + psutil to tray group)
- `dashboard/__init__.py` (new)
- `dashboard/app.py` (new)
- `dashboard/bridge.py` (new)
- `dashboard/log_stream.py` (new)
- `dashboard/paths.py` (new)
- `dashboard/_stub.html` (new — placeholder HTML for this step)
- `tray/service_ctl.py` (edit — add `query()` returning
  `{state, pid}`)

Do not touch `frontend/` (does not exist yet) or `tray/tray.py`
in this step.

## 2.1 Add dependencies

Edit `pyproject.toml`. Find the `tray` dependency group (uv
`[dependency-groups]` or `[project.optional-dependencies]`
depending on current project config — look for `pystray` to locate
the right group). Add:

```toml
"pywebview>=5.0",
"psutil>=5.9",
```

Do not touch the daemon group.

Sync:

```powershell
uv sync --only-group tray
```

## 2.2 Create `dashboard/__init__.py`

Empty file.

## 2.3 Create `dashboard/paths.py`

```python
"""Resolve the frontend web-asset directory for source + frozen runs."""

from __future__ import annotations

import sys
from pathlib import Path


def web_dir() -> Path:
    """Return dir containing index.html for pywebview to load.

    Frozen (PyInstaller): <_MEIPASS>/dashboard/web
    Source run:           <repo>/frontend/dist
    Step-2 fallback:      <this_file>/_stub.html (no frontend yet)
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "dashboard" / "web"
    here = Path(__file__).resolve().parent
    dist = here.parent / "frontend" / "dist"
    if (dist / "index.html").exists():
        return dist
    return here  # _stub.html lives next to this module
```

## 2.4 Create `dashboard/_stub.html`

Placeholder to prove WebView2 loads before the frontend exists:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>halbot dashboard — stub</title>
<style>
html,body{margin:0;background:#0c0c0f;color:#e2e2ef;font-family:sans-serif;
          height:100%;display:flex;align-items:center;justify-content:center}
.card{padding:24px;border:1px solid rgba(255,255,255,0.1);border-radius:10px}
.k{color:#5865F2;font-weight:600}
button{background:#5865F2;color:#fff;border:none;padding:8px 14px;
       border-radius:6px;font-size:13px;cursor:pointer;margin-top:12px}
</style>
</head>
<body>
<div class="card">
  <div>halbot dashboard — <span class="k">stub</span></div>
  <div id="out" style="margin-top:12px;font-family:monospace;font-size:12px;color:#aaa"></div>
  <button onclick="ping()">call api.health()</button>
</div>
<script>
async function ping(){
  try {
    const r = await window.pywebview.api.health();
    document.getElementById('out').textContent = JSON.stringify(r);
  } catch(e) {
    document.getElementById('out').textContent = 'error: ' + e;
  }
}
</script>
</body>
</html>
```

When the web dir fallback resolves to the `dashboard/` directory,
pywebview loads `_stub.html`. Once step 3 lands
`frontend/dist/index.html`, `_stub.html` stops being used.

## 2.5 Create `dashboard/bridge.py`

The `JsApi` class is the **only** object the frontend may call
directly. Every method returns plain JSON-serializable values
(dict / list / primitives). Errors raise `RuntimeError` with a
human-readable message — the frontend catches and displays.

```python
"""pywebview js_api bridge. Frontend calls window.pywebview.api.*."""

from __future__ import annotations

import logging
import os
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

    def bind_window(self, window) -> None:
        self._window = window

    def bind_log_stream(self, stream) -> None:
        self._log_stream = stream

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
```

**`get_stats` RPC:** `MgmtClient` does not yet have a `get_stats`
method. Add one in `tray/mgmt_client.py`:

```python
def get_stats(self):
    return self._call("GetStats", mgmt_pb2.Empty())
```

## 2.6 Extend `tray/service_ctl.py` with `query()`

Current `status()` returns a string. Add a new function (do not
modify `status()`):

```python
def query() -> dict:
    """Return {'state': str, 'pid': int}. pid=0 if not running."""
    import win32service
    try:
        scm, svc = _open()
    except Exception as e:
        return {"state": f"unknown ({e})", "pid": 0}
    try:
        info = win32service.QueryServiceStatusEx(svc)
        state_code = info["CurrentState"]
        pid = int(info.get("ProcessId", 0) or 0)
    finally:
        _close(scm, svc)
    name = {
        1: "stopped", 2: "start-pending", 3: "stop-pending",
        4: "running", 5: "continue-pending", 6: "pause-pending",
        7: "paused",
    }.get(state_code, str(state_code))
    return {"state": name, "pid": pid}
```

## 2.7 Create `dashboard/log_stream.py`

Background thread that consumes `StreamLogs` and buffers into a
thread-safe deque for pull-style consumption by the frontend.

```python
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
```

## 2.8 Create `dashboard/app.py`

```python
"""Entry point to open the dashboard window."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import webview

from .bridge import JsApi
from .log_stream import LogStream
from .paths import web_dir

log = logging.getLogger(__name__)

_window_lock = threading.Lock()
_window = None


def open_window() -> None:
    """Open the dashboard window. Second call is a no-op."""
    global _window
    with _window_lock:
        if _window is not None:
            log.info("dashboard window already open; ignoring")
            return

        api = JsApi()
        stream = LogStream()
        api.bind_log_stream(stream)

        index = web_dir() / ("index.html" if (web_dir() / "index.html").exists() else "_stub.html")
        if not index.exists():
            raise FileNotFoundError(f"no dashboard HTML found at {index}")

        window = webview.create_window(
            title="halbot",
            url=index.as_uri(),
            js_api=api,
            width=1080, height=680,
            min_size=(720, 480),
            frameless=True,
            easy_drag=False,
            background_color="#0c0c0f",
        )
        api.bind_window(window)
        _window = window

    stream.start()
    try:
        webview.start(gui="edgechromium", debug=False)
    finally:
        stream.stop()
        with _window_lock:
            _window = None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    open_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## 2.9 Verification gate

**Terminal 1 — run the daemon from step 1:**

```powershell
uv run python -m halbot.daemon run
```

Wait for `mgmt gRPC listening on 127.0.0.1:50199`.

**Terminal 2 — open the stub window:**

```powershell
uv run python -m dashboard.app
```

Expected:

- A borderless ~1080×680 dark window opens.
- The stub HTML renders "halbot dashboard — stub".
- Clicking the button shows a JSON health response (uptime,
  version, etc.).
- Closing the window ends the process cleanly (no hang).
- Re-running the command opens the window again.

If any of these fail:

- `pywebview.WebViewException` → WebView2 Runtime missing; install
  from Microsoft.
- `ModuleNotFoundError: pywebview` → re-run `uv sync --only-group tray`.
- Button returns "error: ..." → daemon not running on 50199; check
  terminal 1.

## Commit

```powershell
git add pyproject.toml uv.lock dashboard/__init__.py dashboard/app.py dashboard/bridge.py dashboard/log_stream.py dashboard/paths.py dashboard/_stub.html tray/mgmt_client.py tray/service_ctl.py
git commit -m "feat(007): dashboard python backend + pywebview stub"
```

Do not stage any `frontend/` or `build_tray.spec` changes.
