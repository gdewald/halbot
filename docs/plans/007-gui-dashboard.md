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

## Document structure

Sections below grow in separate commits:

1. Panel specs + data-source matrix (Logs, Daemon, Config, Stats).
2. Tech choice + frontend bundling.
3. RPC surface additions (proto changes).
4. Implementation increments (per-commit phasing).
5. Build / deploy changes.
6. Validation + open questions.
