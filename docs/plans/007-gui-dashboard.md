# 007 — GUI Dashboard

Status: draft. Adds a full-window GUI dashboard to the tray app. Mockup:
[docs/mockups/dashboard/halbot.html](../mockups/dashboard/halbot.html).

## Goal

Replace the tiny Tkinter log viewer + pystray menu with a single rich
window exposing four panels — **Logs, Daemon, Config, Stats** — driven
by the existing `Mgmt` gRPC service. Tray icon stays as the launcher
and quit point; it gains one menu item "Open dashboard".

Phase-1 constraint: only `log_level` is a real config field and no
Discord/voice/LLM code is in repo yet. So **most of the Stats panel
and several Config fields are mocked**; the plan calls this out field
by field so nothing ships as a fake real number by accident.

## Approach

- **Stack:** `pywebview` (Edge WebView2 on Windows) hosting a bundled
  static React app derived from the mockup. Keeps Python ecosystem,
  reuses `MgmtClient` in-process for RPCs, avoids Electron / Qt.
- **Bridge:** `pywebview`'s `js_api` exposes a thin Python object
  wrapping `MgmtClient` + log tail + telemetry reads. No browser-side
  gRPC (grpc-web would force another proxy).
- **Incremental:** dashboard lands behind a tray menu item, old log
  viewer stays until dashboard reaches parity, then deleted.
- **Mocking:** panels render with real data where available, static
  placeholders + a visible `mock` badge where not. No invented numbers.

## Panels & data sources

Data status legend: **R** = real backend exists now · **R2+** = will
become real when a later restructure phase lands the subsystem ·
**M** = mocked in the UI with a visible `mock` badge until R/R2+.

### Logs panel

Toolbar: level filter (ALL/DEBUG/INFO/WARN/ERROR + per-level count),
grep input, wrap toggle, tail (autoscroll) toggle, clear button.
Row: timestamp · level pill · source · message. Bottom status bar:
visible line count, error + warning totals.

| Element              | Source                                                | Status |
|----------------------|-------------------------------------------------------|--------|
| log lines            | tail `paths.log_file()` (same file the tray viewer reads) | R      |
| level pill / counts  | parse each line's 4th field (existing log format)     | R      |
| source column        | parse logger name (e.g. `halbot.bot`); may be blank   | R      |
| grep / filter / wrap | frontend-only, no RPC                                 | R      |
| clear button         | empties the in-memory window buffer only; does not truncate the file | R      |

### Daemon panel

Status card: running/stopped/loading spinner, PID, "auto-restart"
toggle, Start/Stop/Restart buttons. Stats row: Uptime, Memory, CPU,
Guilds. Event history list.

| Element              | Source                                                | Status |
|----------------------|-------------------------------------------------------|--------|
| running / stopped    | `service_ctl.query()` via SCM                         | R      |
| PID                  | `QueryServiceStatusEx` — already exposed by SCM       | R      |
| Start/Stop/Restart   | existing `service_ctl`                                | R      |
| uptime               | `Health().uptime_seconds`                             | R      |
| memory / CPU         | `psutil.Process(pid)` in dashboard backend            | R      |
| auto-restart toggle  | NSSM `AppExit` flag — read/write via `nssm get/set`   | R      |
| guilds count         | no Discord code yet                                   | R2+    |
| event history        | see "Event log source" note below                     | R2+    |

**Event log source.** Mockup shows service lifecycle events
("Started", "Stopped (manual)", "Crashed — SIGTERM"). Sources:

- NSSM writes service transitions to Windows Event Log; we can read
  via `win32evtlog` filtered on source `halbot`.
- Crash reasons need daemon cooperation: capture last-N exception
  summaries in a ring buffer and expose via new RPC.

Phase-1 ship: empty list with a `mock` placeholder row showing
"event history wires up in phase 2". Don't guess.

### Config panel

Per-field row: key label · description · type-appropriate widget
(text, number, select, bool toggle, range slider) · revert button.
Dirty-row highlight, "N unsaved changes" counter, Save-to-disk +
Revert-all toolbar.

| Element            | Source                                               | Status |
|--------------------|------------------------------------------------------|--------|
| field list         | `GetConfig` (currently just `log_level`)             | R      |
| widget type        | **not in proto today** — see RPC additions below     | R (needs proto)      |
| save / revert      | `UpdateConfig` + `PersistConfig` / `ResetConfig`     | R      |
| LLM / Voice / TTS groups in the mockup | subsystems not in repo this phase | R2+    |

Groups the mockup shows (LMSTUDIO_URL, KOKORO_VOICE, etc.) render as
a read-only "planned" section with a `mock` badge until the phase
that lands that subsystem also adds the matching field to
`config.DEFAULTS`. UI reads the group list from a static JSON in
the frontend; groups only appear if at least one key in them is
present in `GetConfig()` — otherwise the whole group is hidden.
This lets groups light up automatically as phases land.

### Stats panel

Sections: Soundboard Backup, Voice Playback, Wake Word, STT, TTS,
Text LLM. Every one is a subsystem that does not exist in the repo
this phase.

| Section           | Status |
|-------------------|--------|
| Soundboard Backup | R2+ (needs `sounds.db` + backup job back) |
| Voice Playback    | R2+ |
| Wake Word         | R2+ |
| STT               | R2+ |
| TTS               | R2+ |
| Text LLM          | R2+ |

Phase-1 ship: entire Stats panel renders behind a single full-panel
`mock` overlay reading "Stats wire up in phase 2+. Preview only."
with the mockup content visible but disabled. This keeps the panel
present for design review without leaking fabricated numbers.

## Tech choice

### Why pywebview

Rejected:

- **Electron** — extra 150MB, separate node toolchain, would need a
  second proto-stub bundle on the JS side; overkill for a single
  in-process window.
- **Qt (PySide6 / PyQt)** — full-native, but 60MB+ runtime pulled
  into the tray PyInstaller bundle, and porting the React mockup
  would be a full rewrite in QML or QtWidgets.
- **Tkinter** — already used for log viewer; not viable for the
  mockup's density (grid layouts, animated toggles, range sliders,
  CSS-heavy pill styling) without a multi-week rewrite.

Chosen: **pywebview** with Edge WebView2 runtime (already on
Windows 10 21H2+ and bundled as evergreen on Win11 — our sole
target). Adds ~4MB to the tray bundle. Loads a static HTML file
from the frozen resources dir. Exposes a Python `js_api` object
for RPC — no HTTP server, no gRPC-web proxy.

Hard dependency: `WebView2Loader.dll` must be present. Verify
during `scripts\build.ps1` that PyInstaller `--collect-binaries
webview` picks it up; otherwise add `--add-binary` explicitly.

### Frontend bundling

The mockup as shipped uses CDN scripts (react, react-dom, babel
standalone, Google Fonts) and in-browser JSX transpilation. Ship
is different:

- **No CDN at runtime.** Dashboard must work offline — daemon box
  may be air-gapped, and WebView2 with no internet is common. All
  JS and fonts bundled as static assets.
- **No in-browser Babel.** Compile JSX ahead of time — ~800ms
  cold-start delay otherwise, on every window open.

Tooling: add a `frontend/` dir with minimal Vite + React setup
(Vite chosen for zero-config JSX; no need for a framework-level
router). `npm run build` produces `frontend/dist/` which
`scripts\build.ps1` copies into the tray PyInstaller bundle as a
datas entry. Vite config sets `base: './'` so relative asset paths
work when loaded via `file://`.

Mockup HTML becomes `frontend/src/App.jsx` (the single-file mockup
split into per-panel files under `frontend/src/panels/`). The
four panels (`LogsPanel`, `DaemonPanel`, `ConfigPanel`,
`StatsPanel`), `WinTitleBar`, and `StatusBar` components map
1:1 from the mockup. Tokens object `T` moves to
`frontend/src/tokens.js`.

### Window chrome

Mockup has custom title bar with Windows min/max/close. Keep it —
pass `frameless=True` to `webview.create_window`, then wire the
title-bar buttons through `js_api.{minimize,maximize,close}`
helpers that call `webview.Window.minimize()` etc. Drag region
uses `-webkit-app-region: drag` which Edge WebView2 respects.

### Fonts

Bundle JetBrains Mono and DM Sans woff2 under
`frontend/src/fonts/` and declare via `@font-face` in the entry
CSS. Remove the Google Fonts `<link>`.

## RPC surface additions

None of the existing RPCs are removed. Three additions in
`proto/mgmt.proto`, regenerated via `scripts\gen_proto.ps1`.

### 1. Field type hint on `StringValue`

Needed so the Config panel can pick the right widget without a
hard-coded map. Currently every value is a bare string.

```proto
enum ConfigFieldType {
  CONFIG_FIELD_TYPE_UNSPECIFIED = 0;
  CONFIG_FIELD_TYPE_STRING = 1;
  CONFIG_FIELD_TYPE_NUMBER = 2;
  CONFIG_FIELD_TYPE_BOOL = 3;
  CONFIG_FIELD_TYPE_SELECT = 4;
  CONFIG_FIELD_TYPE_URL = 5;
  CONFIG_FIELD_TYPE_RANGE = 6;
}

message StringValue {
  string value = 1;
  ConfigSource source = 2;
  ConfigFieldType type = 3;
  repeated string options = 4;   // for SELECT
  string description = 5;        // one-line help text
  string group = 6;              // "general" | "llm" | "voice" | "tts"
  double min = 7;                // for NUMBER / RANGE
  double max = 8;
  double step = 9;
}
```

Type + options + description + group move from frontend constants
into `halbot/config.py` as a per-field schema dict alongside
`DEFAULTS`. Single source of truth. Phase-1 only `log_level` gets
populated (type=SELECT, options=[DEBUG,INFO,WARNING,ERROR],
group=general). Later phases add their fields as they land.

### 2. `StreamLogs` — bidirectional log tail

Pushing raw log lines over the existing file tail works but forces
the tray bundle to know where daemon's log file lives (not true
when dashboard runs under a different user or future remote setup).
Stream via gRPC instead:

```proto
rpc StreamLogs (StreamLogsRequest) returns (stream LogLine);

message StreamLogsRequest {
  int32 backlog = 1;       // lines to replay on connect; 0 = none
  string min_level = 2;    // filter server-side; empty = all
}

message LogLine {
  int64 ts_unix_nanos = 1;
  string level = 2;        // DEBUG|INFO|WARNING|ERROR
  string source = 3;       // logger name
  string message = 4;
}
```

Server impl: add a `logging.Handler` that pushes records into a
per-subscriber `asyncio.Queue` plus a bounded ring buffer (1000
lines) for backlog replay. Cheap. No file parsing on the client.

Phase-1 fallback if stream proves flaky: keep the file-tail path in
`js_api` and flip via a config flag. Do not block dashboard ship on
the stream.

### 3. `GetStats` — stub for later panels

Define the message now so the Stats panel has a stable shape;
return empty / zero values this phase. Keeps the frontend from
needing a breaking change when real telemetry lands.

```proto
rpc GetStats (Empty) returns (StatsReply);

message StatsReply {
  SoundboardStats soundboard = 1;
  VoicePlaybackStats voice_playback = 2;
  WakeWordStats wake_word = 3;
  LatencyStats stt = 4;
  LatencyStats tts = 5;
  LlmStats llm = 6;
  bool mock = 99;  // true until real impl lands
}
```

Sub-messages left as a TODO in the proto file, one-liner stubs,
filled in per phase. `mock=true` drives the overlay in the Stats
panel.

### Event history source

Not a new RPC for phase 1. When phase 2 lands the crash ring
buffer, add:

```proto
rpc GetEventLog (Empty) returns (EventLogReply);
```

Deferred — not part of this plan's delivery scope.

## Implementation increments

Each step ends at a commit; repo stays runnable at every boundary.
The tray still runs as it does today until step 7 wires the menu
item in.

### Step 1 — proto + config schema

- Extend `proto/mgmt.proto` with `ConfigFieldType`, the new
  `StringValue` fields, `StreamLogs`, `GetStats`, and the Stats
  sub-messages (stub shapes).
- Regenerate `halbot/_gen/` via `scripts\gen_proto.ps1`.
- Add `SCHEMA` dict in `halbot/config.py` keyed by field name,
  holding `(type, options, description, group, min, max, step)`.
  Populate only `log_level`.
- Extend `mgmt_server._state_msg()` to fill the new `StringValue`
  fields from `SCHEMA`.
- `GetStats` returns `StatsReply(mock=True)` with zeroed
  sub-messages.
- `StreamLogs` implemented but not yet consumed.
- Rebuild daemon with `-Clean` (proto change — see CLAUDE.md
  pitfall on PyInstaller cache).

Runnable: yes. Tray unchanged.

### Step 2 — dashboard Python backend

- New package `dashboard/` inside the tray codebase:
  - `dashboard/__init__.py`
  - `dashboard/app.py` — `open_window()` creates a pywebview
    window pointed at the bundled `index.html`.
  - `dashboard/bridge.py` — `JsApi` class: `health()`,
    `get_config()`, `update_config(updates)`, `persist(fields)`,
    `reset(fields)`, `service_start/stop/restart/query()`,
    `nssm_auto_restart_get/set()`, `proc_stats(pid)`,
    `window_minimize/maximize/close()`.
  - `dashboard/log_stream.py` — background thread running the
    `StreamLogs` RPC, fan-out to webview via
    `window.evaluate_js('window.__pushLog(...)')` OR
    `js_api.pop_log_batch()` polling — pick one during impl.
- Add `pywebview` + `psutil` to `[project.optional-dependencies]
  tray` in `pyproject.toml`.
- No UI yet — smoke-test by calling `open_window()` from a
  throwaway script; verify WebView2 loads a placeholder HTML.

Runnable: yes.

### Step 3 — frontend scaffold

- `frontend/` dir at repo root: `package.json`, `vite.config.js`
  (`base: './'`, `build.outDir: 'dist'`), `index.html`, entry
  `src/main.jsx`.
- Extract `T` tokens into `src/tokens.js`.
- Move `WinTitleBar`, `StatusBar`, the three sidebar variants,
  and shell `App` from the mockup into `src/App.jsx` +
  `src/components/`. Panels stub out as empty divs for now.
- Add bundled fonts under `src/fonts/`.
- `src/bridge.js` — thin wrapper over `window.pywebview.api.*`
  promising the same shape the panels will call.
- `npm run build` produces `frontend/dist/index.html` + assets.
- `dashboard/app.py` points pywebview at `frontend/dist/index.html`
  resolved via `halbot.paths` (source run) or `sys._MEIPASS`
  (frozen).
- Smoke: open window, see empty shell with title bar + sidebar.

Runnable: yes.

### Step 4 — Logs panel

- Port `LogsPanel` + `LogRow` + `ToggleBtn` from the mockup into
  `src/panels/Logs.jsx`.
- Wire log source: either
  - a) push path — backend calls
    `window.evaluate_js('window.__pushLog(json)')` per line, or
  - b) pull path — backend buffers and frontend polls
    `api.pop_log_batch()` every 250ms.
  - Default to (b) for simpler lifecycle; swap to (a) if polling
    cost shows up.
- Backlog: call `api.backlog_logs(200)` on mount.
- Frontend filter / grep / wrap / tail stay client-side.
- Delete `tray/log_viewer.py` usages — kept as a file for one more
  step in case the window fails to open.

### Step 5 — Daemon panel

- Port `DaemonPanel` + `ActionBtn` into `src/panels/Daemon.jsx`.
- Wire real RPCs:
  - status / PID: `api.service_query()` → `{state, pid}`,
    polled every 2s.
  - uptime: `api.health().uptime_seconds`.
  - memory / CPU: `api.proc_stats(pid)` using `psutil`.
  - Start / Stop / Restart: existing `service_ctl` calls.
  - auto-restart: `nssm get halbot AppExit` / `set` wrapped in
    `api.nssm_auto_restart_get/set`. If NSSM lookup fails, hide
    the toggle rather than lie.
- Event history: render a single row "event history wires up in
  phase 2" with a `mock` badge.
- Guilds card shows `—` with the `mock` badge until Discord lands.

### Step 6 — Config panel

- Port `ConfigPanel` + `ConfigRow` + `FieldInput` into
  `src/panels/Config.jsx`.
- Groups list comes from `GetConfig()`'s `group` field;
  collapsed into `Map<group, field[]>` client-side. Hide
  any group with zero fields (see panel spec).
- `FieldInput` switch on `field.type` using the proto enum.
- Save: `api.update_config(updates)` then `api.persist(keys)`
  on success. Revert-all: `api.reset([])`.
- Show a dimmed "planned" card under the real groups listing
  field names from the mockup that are not present in
  `GetConfig()`, with a `mock` badge, so design review still
  shows the full mockup surface without implying they work.

### Step 7 — Stats panel + tray wiring

- Port `StatsPanel` and sub-components into `src/panels/Stats.jsx`
  (soundboard table, stat cards, latency cards, wake history).
- Mount a single full-panel overlay when `api.get_stats().mock`
  is `true`. Everything behind the overlay renders from the
  mockup's seed data so design stays reviewable.
- Tray menu: insert `Item("Open dashboard", on_open_dashboard)`
  at the top; on click spawn `dashboard.app.open_window()` on a
  daemon thread (pywebview must run on its own thread, not the
  pystray UI thread).
- Delete `tray/log_viewer.py` and the "Open log viewer" menu
  item.

### Step 8 — build + deploy

Covered in the next section.

### Step 9 — validation

Covered two sections down.

## Build / deploy changes

### Frontend build

Add to `scripts\build.ps1` before the PyInstaller step, gated on
`-Target tray` or `-Target all`:

```powershell
if (Test-Path frontend/package.json) {
  Push-Location frontend
  if (-not (Test-Path node_modules) -or $Clean) {
    npm ci
  }
  npm run build
  Pop-Location
}
```

Requires Node.js on the dev box. Pin Node version in
`frontend/.nvmrc`. Build-time only; the zip ships no Node.

### PyInstaller tray spec

In `build_tray.spec`:

- Add `frontend/dist` as a `datas` entry → `dashboard/web/` in
  the bundle: `('frontend/dist', 'dashboard/web')`.
- Hidden imports: `pywebview`, `webview.platforms.edgechromium`,
  `psutil`.
- Add `--collect-binaries webview` via the `Analysis.binaries`
  hook so `WebView2Loader.dll` follows. Verify post-build that
  the DLL is present in `dist\halbot-tray\_internal\webview\`.
- Resolve the frontend dir at runtime through a helper in
  `dashboard/app.py`:

  ```python
  def _web_dir() -> Path:
      if getattr(sys, "frozen", False):
          return Path(sys._MEIPASS) / "dashboard" / "web"
      return Path(__file__).resolve().parent.parent / "frontend" / "dist"
  ```

### Pyproject groups

`pyproject.toml`:

- Add `pywebview>=5.0`, `psutil` to the `tray` group.
- Leave daemon group alone.

### `-Clean` triggers

Update CLAUDE.md's "When to use `-Clean`" list to add:

- `frontend/package.json` or `frontend/src/**` edited (Vite build
  only runs in source tree; PyInstaller still cache-reuses its
  analysis for `halbot-tray.exe`, so the dev workflow tolerates
  incremental — but a spec-datas change still forces `-Clean`).

### WebView2 runtime

Target is Windows 11, evergreen WebView2 ships with the OS. No
bootstrapper needed. If a future Windows 10 box appears, add a
one-time check in `dashboard/app.py`:

```python
try:
    import webview
    webview.create_window(...)
except webview.WebViewException:
    # surface a tray notification with a download link
    ...
```

### Update flow

`scripts\update-tray.bat` unchanged — the dashboard ships as part
of the tray bundle, so a single tray swap delivers both icon and
dashboard updates. Daemon updates independently as today.

### Install flow

`setup --install` unchanged — no new services, no new registry
keys. The frontend is static content inside the tray install dir.

Binary size delta expected: ~8-12 MB (pywebview + psutil +
frontend assets + fonts). Acceptable.

## Validation

Manual checklist, run on a built install (not source run) after
step 8. Source-run path works but can't exercise the frozen asset
resolution.

### Launch & chrome

- Tray icon shows "Open dashboard" as the first menu item.
- Clicking it opens a 1080×680 borderless window with the custom
  title bar drawn in-page.
- Min / max / close buttons in the title bar work.
- Dragging the title bar moves the window.
- Closing the window does not kill the tray process.
- Re-opening the window after close works (no stale WebView2
  state, no second process).

### Logs panel

- Window opens with ≥1 backlog log line visible.
- Emitting a daemon INFO tick (wait ~2s) appears live.
- Level filter buttons flip colors + counts; filtering hides rows.
- Grep field narrows rows.
- Tail toggle pauses autoscroll; resuming scrolls to bottom.
- Wrap toggle changes line wrap behavior.
- Switching daemon log level in the Config panel changes the
  stream's emitted levels visibly.

### Daemon panel

- Running/stopped pill matches real SCM state.
- PID matches `sc queryex halbot`.
- Stop → pill flips to STOPPED, start button appears, action
  buttons disable during loading.
- Start → pill flips back, memory/CPU numbers update.
- Uptime ticks every second.
- auto-restart toggle round-trips through NSSM (verify with
  `nssm get halbot AppExit`).
- Event history row shows the mock placeholder, not fake events.

### Config panel

- Log level select shows current value.
- Changing it dirties the row (indicator + "N unsaved").
- Save persists — reboot service, value still present.
- Revert drops the draft back to saved value.
- "planned" section lists LLM / Voice / TTS fields dimmed with
  the mock badge; widgets disabled.

### Stats panel

- Full-panel mock overlay visible.
- Mockup content visible (faded) behind it.
- No fabricated numbers shown as "real".

### Offline

- Disable internet on the box, re-open window. All fonts,
  scripts, icons still render (no CDN fallback).

### Update flow

- Edit a panel, rebuild with `scripts\build.ps1 -Target tray`,
  run `scripts\update-tray.bat`. Re-open window — change visible.

## Open questions

- **Log stream backpressure.** If a log burst (>1000 lines/s)
  fires, does `evaluate_js` flood the WebView2 message bus?
  Decide between batched push (coalesce 100ms windows) vs pull
  with a server-side bounded queue during step 4.
- **Multiple dashboard windows.** Allow? Simplest: reject the
  second `open_window` with a tray notification. Ship that.
- **Dashboard ↔ daemon version skew.** When the user updates only
  the tray bundle, new RPCs may not exist on the daemon. Surface
  an "update daemon" banner on `Health()` mismatch. Defer.
- **HiDPI.** Need to test on a 150% scaling monitor; WebView2
  should handle it, but verify the 1080×680 default isn't
  oversized on 1920×1080 scaled.

## Backlog link

Related entries in
[docs/plans/drafts/phase-backlog.md](drafts/phase-backlog.md):

- Tray GUI secret-update dialog — lands naturally as a new
  Config field once `SetSecret` is wired into the dashboard
  bridge. Track as a phase-2 follow-up, not this plan.

## Out of scope

- Discord / voice / LLM subsystems (R2+ phases re-introduce
  these; Stats + Config "planned" sections light up then).
- Dark/light theme toggle (mockup is dark-only; fine).
- Remote dashboard (gRPC over LAN). Loopback only.
- Telemetry export / Prometheus scrape.
- Log file download / export button (mockup doesn't show one).
