# Plan 023 — Claude-driven dashboard via localhost dev mode + Playwright

## Context

Today the Halbot dashboard is a pywebview window loading `frontend/dist/index.html` over `file://` and talking to Python via `window.pywebview.api.*`. That bridge is fast and ergonomic for a human user, but Claude can't observe or drive it — UI changes always end with "redeploy and click around to verify" punted back to the human, which violates the project's "verify your own fix" rule.

The goal of this plan is a dev-only HTTP transport that lets Claude+Playwright drive the same React app end-to-end without touching pywebview, so the iteration loop becomes: edit JSX → `npm run build` → restart dev server → run pytest → read output → fix → green. Production tray UX is unchanged: tray menu still spawns the pywebview window over `file://` exactly as today.

Three confirmed design calls:
1. **Live-first test default with snapshot fallback** — fixture probes `MgmtClient().health()`; daemon up → live tests, daemon down → reduced rendering-only run.
2. **`--dev` is headless server only** — uvicorn on `127.0.0.1:50200`, no pywebview window. Tray menu still opens the regular window in non-dev paths.
3. **Per-panel smoke as day-one test scope** — one navigate-and-render test per panel (Daemon, Logs, Stats, Analytics, Emojis, Config, Soundboard).

After approval, this plan moves to `docs/plans/drafts/023-claude-driven-dashboard-dev-mode.md` per CLAUDE.md convention, then to `docs/plans/023-...-impl.md` once shipped.

## Shape

### Transport layer

Three independent branches in `frontend/src/bridge.js` (the existing wrapper already anticipates this — its header comment names "Browser dev — STUB returns empty data" as mode 3):

```
if (SNAPSHOT)                                  → snapshot bridge (existing)
else if (location.protocol.startsWith('http')) → fetch('/api/{name}', POST, JSON args)
else if (window.pywebview?.api)                → pywebview bridge (existing)
else                                           → STUB
```

Protocol-based detection avoids the `pywebview-not-yet-injected` race that would happen with a runtime check. Inside pywebview, `location.protocol === 'file:'`. Inside `--dev`, it's `http:`.

### HTTP server placement: dashboard subprocess

The `JsApi` class in [dashboard/bridge.py](dashboard/bridge.py) already lives in the dashboard subprocess, instantiates `MgmtClient` (gRPC), binds `LogStream`/`EventStream` workers, and is the bridge process. Adding FastAPI here means:
- Reuse `JsApi` directly — `getattr(api, method_name)(**kwargs)` per request.
- Reuse `LogStream` / `EventStream` workers as-is — frontend already polls `pop_log_batch` / `pop_event_batch`, no SSE needed.
- Daemon (Windows service) untouched. No gRPC schema churn. No daemon-side restart on dev iteration.

UAC posture: dashboard runs as the logged-in user, which already has the `SERVICE_START | SERVICE_STOP` ACL grant from `install.ps1`. Bind 127.0.0.1 only — there is no auth on `service_stop` and no plan to add any.

### File-by-file changes

**`dashboard/dev_server.py`** — new. ~80 lines.

```python
"""FastAPI dev server. Mounts JsApi over POST /api/{method}, serves frontend/dist."""
def build_app(api: JsApi) -> FastAPI:
    app = FastAPI()
    @app.post("/api/{method}")
    async def call(method: str, body: dict | None = Body(None)):
        fn = getattr(api, method, None)
        if not callable(fn) or method.startswith("_") or method.startswith("bind_"):
            raise HTTPException(404)
        return await asyncio.to_thread(fn, **(body or {}))
    app.mount("/", StaticFiles(directory=str(web_dir()), html=True), name="static")
    return app

def serve(host="127.0.0.1", port=50200) -> None:
    if not (web_dir() / "index.html").exists():
        raise SystemExit(f"frontend/dist/index.html missing — run `npm --prefix frontend run build` first")
    api = JsApi()
    log_stream = LogStream(); api.bind_log_stream(log_stream); log_stream.start()
    event_stream = EventStream(); api.bind_event_stream(event_stream); event_stream.start()
    try:
        uvicorn.run(build_app(api), host=host, port=port, log_level="warning")
    finally:
        event_stream.stop(); log_stream.stop()
```

Window-control methods (`window_minimize/maximize/close`) still get routed — their guard `if self._window is not None` already no-ops when there's no window (api.bind_window never called).

**`dashboard/app.py`** — add CLI parsing. Today the file just calls `open_window()` from `main()`. Add:

```python
def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if "--dev" in sys.argv or os.environ.get("HALBOT_DASHBOARD_DEV") == "1":
        from .dev_server import serve
        port = int(os.environ.get("HALBOT_DASHBOARD_DEV_PORT", "50200"))
        serve(port=port)
        return 0
    open_window()
    return 0
```

`_suppress_console()` block at top still runs — harmless for headless server, useful when launched from tray.

**`frontend/src/bridge.js`** — add HTTP branch. Insert above the existing `make()` function:

```js
const IS_HTTP = typeof location !== 'undefined'
  && (location.protocol === 'http:' || location.protocol === 'https:');

async function httpCall(name, args) {
  // Args list → kwargs dict by JsApi method signature would need a manifest;
  // simpler: since current frontend always calls with positional args matching
  // the Python signature order, send {args: [...]} and let server splat.
  const r = await fetch(`/api/${name}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({args}),
  });
  if (!r.ok) throw new Error(`api ${name} → ${r.status}`);
  return r.json();
}
```

…and update `make(name)`:

```js
function make(name) {
  return async (...args) => {
    if (SNAPSHOT_BRIDGE) return SNAPSHOT_BRIDGE[name](...args);
    if (IS_HTTP) return httpCall(name, args);
    const a = api();
    if (!a) return STUB[name](...args);
    return a[name](...args);
  };
}
```

Server-side then needs to accept `{args: [...]}` and splat positionally. Update `dev_server.py` accordingly:

```python
@app.post("/api/{method}")
async def call(method: str, body: dict | None = Body(None)):
    fn = getattr(api, method, None)
    if not callable(fn) or method.startswith("_") or method.startswith("bind_"):
        raise HTTPException(404)
    args = (body or {}).get("args") or []
    return await asyncio.to_thread(fn, *args)
```

**`frontend/src/components/WinTitleBar.jsx`** — hide chrome buttons when not in pywebview. Read the file during impl; expected change is a one-line guard `if (typeof window === 'undefined' || !window.pywebview) return null;` at the top of the component, or render an empty `<div className="titlebar-spacer" />`.

**`pyproject.toml`** — extend two groups:

```toml
[dependency-groups]
tray = [
    # existing...
    "fastapi>=0.110",
    "uvicorn>=0.27",
]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-playwright>=0.4",
    "playwright>=1.40",
    "httpx>=0.27",  # async test client for FastAPI
]
```

`[tool.pytest.ini_options]` block added too: `testpaths = ["tests"]`, `asyncio_mode = "auto"`.

**`tests/dashboard/__init__.py`** — empty marker.

**`tests/dashboard/conftest.py`** — fixtures:
- Session fixture spawns `python -m dashboard.app --dev` as a subprocess on a free port (probe via socket bind, fall back to 50200).
- Wait for `GET /` to return 200, max 10s, fail fast otherwise.
- Yield `{"base_url": ..., "live": bool}` where `live` = `MgmtClient().health()` succeeded once at session start.
- Teardown kills subprocess, drains stdout/stderr to test log on failure.
- Per-test `page` fixture from pytest-playwright, navigated to `base_url`.
- `pytest.mark.live` skips when `live` is False.

**`tests/dashboard/test_smoke.py`** — one render test per panel. Pattern:

```python
@pytest.mark.parametrize("panel,expected_text", [
    ("daemon", "Service"),
    ("logs", "level"),
    ("stats", "soundboard"),
    ("analytics", "events"),
    ("emojis", "emoji"),
    ("soundboard", "saved"),
    ("config", "log_level"),
])
def test_panel_renders(page, dashboard, panel, expected_text):
    page.goto(f"{dashboard['base_url']}/")
    page.click(f"[data-panel='{panel}']")  # nav target — confirm selector with WinTitleBar/nav read
    expect(page.get_by_text(expected_text, exact=False)).to_be_visible(timeout=5000)
    # No console errors
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.wait_for_timeout(500)
    assert not errors, f"console errors: {errors}"
```

Selectors confirmed against the actual nav code during impl — `data-panel` may not exist yet; fall back to text match (`page.get_by_role('button', name='Daemon')`).

**`scripts/dev-dashboard.ps1`** — new convenience launcher:

```powershell
# Start the dashboard in dev mode for Claude/Playwright. Builds frontend if dist missing.
param([int]$Port = 50200, [switch]$NoBuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $NoBuild -and -not (Test-Path "$root\frontend\dist\index.html")) {
    Push-Location "$root\frontend"; npm run build; Pop-Location
}
$env:HALBOT_DASHBOARD_DEV = "1"
$env:HALBOT_DASHBOARD_DEV_PORT = "$Port"
& uv run --project $root python -m dashboard.app --dev
```

**`CLAUDE.md`** — add a "Dev mode for UI verification" subsection under Working Style, naming the dev port (50200), the launch script, the test command (`uv run pytest tests/dashboard`), and the live-vs-snapshot fixture behaviour. Note: dev server binds 127.0.0.1 only, no auth — never bind public.

### Critical files to modify

- [dashboard/app.py](dashboard/app.py) — CLI flag + dispatch.
- [dashboard/bridge.py](dashboard/bridge.py) — read-only reuse, no edit.
- [dashboard/dev_server.py](dashboard/dev_server.py) — new.
- [dashboard/paths.py](dashboard/paths.py) — read-only reuse via `web_dir()`.
- [frontend/src/bridge.js](frontend/src/bridge.js) — HTTP branch + `httpCall` helper.
- [frontend/src/components/WinTitleBar.jsx](frontend/src/components/WinTitleBar.jsx) — hide chrome when no pywebview.
- [pyproject.toml](pyproject.toml) — `tray` + `dev` deps.
- `tests/dashboard/conftest.py` — new.
- `tests/dashboard/test_smoke.py` — new.
- [scripts/dev-dashboard.ps1](scripts/dev-dashboard.ps1) — new.
- [CLAUDE.md](CLAUDE.md) — doc the new loop.

### Existing functions/utilities reused

- `dashboard.bridge.JsApi` — already encapsulates all 23 RPC methods (`bridge.py:36-361`). Used as-is.
- `dashboard.paths.web_dir()` — resolves `frontend/dist/` for source + frozen runs (`paths.py:9-22`). Used by FastAPI `StaticFiles` mount.
- `dashboard.log_stream.LogStream` and `dashboard.event_stream.EventStream` — pull-based polling already (`pop_log_batch`/`pop_event_batch`). No streaming layer needed.
- `frontend/src/bridge.js` `make()` factory and `STUB` table — extended with one new branch, all 23 entry points unchanged.

## Pitfalls (from the design review)

1. **`window.pywebview` injection race** — solved by protocol-based detection, not runtime check.
2. **`emoji_list` payload** — returns base64-inlined images, multi-MB JSON over HTTP. Add a sanity assertion in the smoke test (`response.size_bytes < 50_000_000` or similar) so it surfaces as a clear failure rather than a Playwright timeout.
3. **`StaticFiles` MIME** — verify `.woff2`/`.svg` ship with correct `Content-Type`. uvicorn's defaults are correct, but worth a single curl assertion in `conftest.py` startup probe.
4. **Port 50200** — outside the http.sys excluded `50736-50935` range. Document next to the daemon's 50199 in CLAUDE.md's port table.
5. **`frontend/dist/` missing** — fail loudly in `dev_server.serve()` with a message telling user to run `npm run build`.
6. **Bridge wrapper service-control endpoints** — `service_start/stop/restart` and `nssm_*` are unguarded. Bind 127.0.0.1 only (FastAPI default with `host="127.0.0.1"`). Document in CLAUDE.md that the dev server must never be bound to a routable interface.
7. **`asyncio.to_thread` for blocking gRPC** — required because `JsApi` methods are sync and call gRPC which blocks. Without it, the FastAPI event loop stalls on every request.

## Non-goals

- No Vite HMR / dev-server proxy. `npm run build` is 5–10s; build-then-serve keeps dev surface = ship surface. Fast iteration via `npm run build -- --watch` is a follow-up if needed.
- No production HTTP path. The dev server is dev-only; `tray` package still launches pywebview the same way.
- No auth, CSRF, or token in the dev server. Single-user private-tool scope, 127.0.0.1 only.
- No live integration tests for service-control or daemon-state-mutating actions in the day-one PR. `service_start/stop/restart`, `update_config`/`persist_config` write paths deferred to a follow-up plan once the harness is proven.
- No CI. Tests run locally; nobody else runs this repo. CLAUDE.md is the contract that says "run `uv run pytest tests/dashboard` after touching the dashboard."
- No new transport for log/event push. Polling is unchanged.

## Verification

After impl:

1. **Build green**: `scripts\build.ps1` (gen_proto + npm) succeeds.
2. **Server up**: `scripts\dev-dashboard.ps1` starts uvicorn, `curl http://127.0.0.1:50200/` returns 200 with `<title>halbot</title>` in body.
3. **Asset MIME sanity**: `curl -I http://127.0.0.1:50200/assets/<hashed>.woff2` returns `Content-Type: font/woff2`.
4. **Bridge round-trip**: `curl -X POST http://127.0.0.1:50200/api/health -H 'Content-Type: application/json' -d '{"args":[]}'` returns the JSON shape from `bridge.py:53-65` (live mode) or a `MgmtClient` error JSON (daemon down).
5. **Frontend renders over HTTP**: open Chrome at `http://127.0.0.1:50200/`, all panels load without console errors. Service-control buttons work in live mode (start/stop service via UI, observe `Get-Service halbot`).
6. **pywebview unchanged**: tray menu → "Open dashboard" still opens the original framless pywebview window over `file://`. Window chrome buttons still work.
7. **Tests green**: `uv run pytest tests/dashboard -v` — all 7 panel-render tests pass with daemon up; 7 still pass (or skip) with daemon down (snapshot fallback path).
8. **Negative test**: stop the daemon, run tests — fixture detects `live=False` cleanly, smoke tests still render via fetch (panels show empty/error states, no exceptions).
9. **Deploy unchanged**: `scripts\deploy.ps1` still works — confirm by deploying the new `dashboard/dev_server.py` and verifying the prod tray menu still opens the pywebview window.

## Open follow-ups (out of scope here, list for memory)

- Live integration tests for mutation paths (`update_config`, `service_start/stop/restart`).
- `npm run build -- --watch` integration if iteration time becomes a problem.
- Optional: extend tests to assert log/event polling produces visible UI (requires daemon emitting test events).
- Optional: pywebview-pointed-at-localhost mode if visual debug + Playwright in parallel becomes useful.
