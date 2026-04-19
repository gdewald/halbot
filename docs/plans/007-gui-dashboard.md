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
