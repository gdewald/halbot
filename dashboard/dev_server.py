"""FastAPI dev server. Mounts JsApi over POST /api/{method}, serves frontend/dist.

Dev-only transport for Claude+Playwright iteration. Production tray opens
pywebview directly (dashboard.app.open_window); this module is only invoked
when dashboard.app sees `--dev` or HALBOT_DASHBOARD_DEV=1.

Bind 127.0.0.1 only — JsApi has zero auth and includes service_start/stop.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .bridge import JsApi
from .event_stream import EventStream
from .log_stream import LogStream
from .paths import web_dir

log = logging.getLogger(__name__)

# Windows mimetypes ships without entries for web fonts; StaticFiles falls
# back to text/plain and Chrome refuses to apply the @font-face. Register
# explicitly before any StaticFiles instance runs guess_type().
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

_FORBIDDEN_PREFIXES = ("_", "bind_")


def _is_callable_method(api: JsApi, name: str) -> bool:
    if any(name.startswith(p) for p in _FORBIDDEN_PREFIXES):
        return False
    fn = getattr(api, name, None)
    return callable(fn)


def build_app(api: JsApi) -> FastAPI:
    app = FastAPI(title="halbot-dashboard-dev")

    @app.post("/api/{method}")
    async def call(method: str, body: dict | None = Body(default=None)) -> Any:
        if not _is_callable_method(api, method):
            raise HTTPException(status_code=404, detail=f"unknown method {method!r}")
        args = (body or {}).get("args") or []
        if not isinstance(args, list):
            raise HTTPException(status_code=400, detail="args must be a list")
        fn = getattr(api, method)
        try:
            result = await asyncio.to_thread(fn, *args)
        except Exception as e:
            log.exception("api %s failed", method)
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
        return result

    root = web_dir()
    index = root / "index.html"

    @app.get("/")
    async def index_html() -> FileResponse:
        return FileResponse(str(index))

    app.mount("/", StaticFiles(directory=str(root), html=True), name="static")
    return app


def serve(host: str = "127.0.0.1", port: int = 51199) -> None:
    root = web_dir()
    if not (root / "index.html").exists():
        raise SystemExit(
            f"frontend/dist/index.html missing at {root}. "
            f"Run `npm --prefix frontend run build` first."
        )

    api = JsApi()
    log_stream = LogStream()
    api.bind_log_stream(log_stream)
    event_stream = EventStream()
    api.bind_event_stream(event_stream)

    log_stream.start()
    event_stream.start()
    log.info("dashboard dev server starting on http://%s:%d", host, port)
    try:
        uvicorn.run(build_app(api), host=host, port=port, log_level="warning")
    finally:
        event_stream.stop()
        log_stream.stop()
