# 007 — GUI Dashboard

Status: draft. Adds a full-window GUI dashboard to the tray app.

**Mockup:** [docs/mockups/dashboard/halbot.html](../mockups/dashboard/halbot.html)
(single-file React + inline JSX; run via `Open with browser` to see
the target look).

## TL;DR for implementers

You are building a `pywebview` window that loads a pre-built static
React app. The window offers four panels — Logs, Daemon, Config,
Stats — driven by the existing `Mgmt` gRPC service plus three new
RPCs. The tray icon stays; it gains an "Open dashboard" menu item.

**Phase-1 reality check:** only `log_level` is a real config field;
no Discord/voice/LLM code is in the repo. Stats panel and most
Config fields are mocked behind a visible `mock` badge. Do not
invent numbers. Every mocked surface is called out explicitly
per-step.

## Reading order

Follow the step files **in order**. Each file is self-contained
and ends with a verification gate you must pass before the next
step. Do not skip ahead or batch steps; later steps assume earlier
commits landed green.

1. [007-step-1-proto.md](007-step-1-proto.md) — proto + config schema
2. [007-step-2-backend.md](007-step-2-backend.md) — dashboard Python backend
3. [007-step-3-frontend-scaffold.md](007-step-3-frontend-scaffold.md) — Vite/React scaffold
4. [007-step-4-logs-panel.md](007-step-4-logs-panel.md) — Logs panel
5. [007-step-5-daemon-panel.md](007-step-5-daemon-panel.md) — Daemon panel
6. [007-step-6-config-panel.md](007-step-6-config-panel.md) — Config panel
7. [007-step-7-stats-and-tray.md](007-step-7-stats-and-tray.md) — Stats + tray wiring
8. [007-step-8-build-deploy.md](007-step-8-build-deploy.md) — build / deploy
9. [007-step-9-validation.md](007-step-9-validation.md) — validation checklist

## Overview reference

For full design rationale (why pywebview over Electron, panel
specs, data-source matrix, R/R2+/M status for each field, open
questions), see:
[007-gui-dashboard-design.md](007-gui-dashboard-design.md).

The step files are authoritative for implementation. The design
doc is reference-only.

## Rules for the implementer

1. **One commit per step file.** Each step file's "Commit" section
   says exactly what to commit. Do not split or merge commits.
2. **Do not touch files the step does not name.** If a step says
   edit `proto/mgmt.proto`, only that file + generated code +
   anything explicitly listed changes.
3. **Paste snippets verbatim** unless a line is marked
   `# TODO(implementer):`. If a snippet looks wrong, stop and
   ask — don't silently "fix" it.
4. **Verification gates are mandatory.** If the gate command
   fails, debug until it passes before committing. Never commit
   a step whose gate is red.
5. **Mock markers are load-bearing.** Where a step says "render
   with a `mock` badge", the badge must be visible. Do not
   silently replace placeholder values with plausible-looking
   real data.
6. **Windows paths.** All commands assume PowerShell on Windows
   11. `%ProgramFiles%` / `%ProgramData%` are env vars; do not
   rewrite them as POSIX paths.
7. **When in doubt, stop.** Better to halt and re-read the step
   than to guess across steps.

## Out of scope (do not attempt)

- Discord / voice / LLM subsystems (later phases land these).
- Dark / light theme toggle.
- Remote dashboard (gRPC over LAN).
- Telemetry export / Prometheus.
- Log-file download / export button.
- Multiple dashboard windows — second `open_window` rejects with
  a tray notification; do not build a window manager.
- Auto-update / in-window self-update.

If you catch yourself about to build one of these, stop.
