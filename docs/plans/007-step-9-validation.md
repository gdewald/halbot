# Step 9 — Manual Validation

**Goal:** run through every feature surface end-to-end against a fresh
install of the tray+dashboard bundle. No code changes in this step —
this is the acceptance gate that closes out plan 007.

**Runnable at end:** yes — if every checkbox below passes, the
dashboard is ready to ship.

## Pre-flight

Build + install from clean (elevated shell):

```powershell
scripts\build.ps1 -Clean
Expand-Archive -Force -Path dist\halbot-daemon.zip -DestinationPath $env:TEMP\halbot-daemon-new
scripts\update-daemon.bat $env:TEMP\halbot-daemon-new
Expand-Archive -Force -Path dist\halbot-tray.zip -DestinationPath $env:TEMP\halbot-tray-new
scripts\update-tray.bat $env:TEMP\halbot-tray-new
```

Confirm service running:

```powershell
sc query halbot
```

Must print `STATE : 4 RUNNING`. If not, daemon logs are at
`%ProgramData%\Halbot\logs\halbot.log` and
`halbot-service.log` — fix daemon before continuing.

## 9.1 Launch + chrome

- [ ] Tray icon appears after `update-tray.bat`.
- [ ] Right-click tray → "Open dashboard" is the top entry and marked
      default (bold).
- [ ] Left-click tray → dashboard window opens within ~3 seconds.
- [ ] Window title bar is custom (dark, not default Windows chrome),
      shows `halbot` + version string.
- [ ] Minimize / maximize / close buttons in custom title bar work.
      Close hides window; tray icon remains; re-click tray re-opens
      window without re-launching process.
- [ ] Sidebar shows all 4 nav items (Logs, Daemon, Config, Stats) with
      icons.
- [ ] Status bar at bottom shows daemon state (Running/Stopped) and
      version.

If window opens blank / white:

- WebView2 runtime missing: `winget install Microsoft.EdgeWebView2Runtime`.
- `frontend/dist/` did not bundle — repeat step 8.5.2 sanity-unzip.

## 9.2 Logs panel

Preconditions: daemon running, has been running >10 s (backlog exists).

- [ ] Opening Logs panel immediately shows the last ~400 backlog lines
      (no blank flash longer than ~300 ms).
- [ ] New log lines from daemon tick appear within ~500 ms of being
      written (poll cadence is 250 ms).
- [ ] Level filter buttons (ALL / DEBUG / INFO / WARN / ERROR) filter
      client-side; DEBUG shows significantly more lines than INFO.
- [ ] Grep box filters by substring (case-insensitive). Clearing grep
      restores all lines.
- [ ] Wrap toggle toggles line wrap; long lines truncate vs wrap.
- [ ] Tail toggle: when **on**, panel sticks to bottom as new lines
      arrive. When **off**, scroll position holds even as new lines
      arrive above.
- [ ] Clear button: buffer visually clears, but toggling level filter
      off/on brings back the same lines (confirms Clear is UI-only,
      not a daemon-side truncate).
- [ ] Level pills render with correct colors: DEBUG muted, INFO
      neutral, WARN yellow, ERROR red.

Toggle log level to DEBUG in Config panel (§9.4), return here — DEBUG
lines must now appear within 2 seconds.

## 9.3 Daemon panel

- [ ] Status card shows "Running" with a breathing / animated dot while
      service is up; PID matches `sc queryex halbot` output.
- [ ] Version string matches the one in the status bar + title bar.
- [ ] Uptime ticks up every second.
- [ ] Memory + CPU cards show live values; change over a 10 s window.
- [ ] "Guilds" card is rendered with a `mock` badge.
- [ ] Event log section is rendered with a `mock` badge and explanatory
      caption ("subsystem events land here once re-implemented" or
      similar).
- [ ] Stop button: confirms, stops service. Status card flips to
      "Stopped" within ~5 s. Memory/CPU cards go blank or show "—".
- [ ] Start button: service returns to Running. PID changes.
- [ ] Restart button: PID changes; status briefly flips Stopped →
      Running.
- [ ] NSSM auto-restart toggle: if NSSM readable, toggle persists a
      round-trip (toggle off, click elsewhere, come back — still off).
      If NSSM not readable on this box, the toggle must be **hidden**,
      not shown disabled.

## 9.4 Config panel

- [ ] All 4 groups render: General, LLM, Voice, TTS. Group headers
      visible.
- [ ] `log_level` field renders as a SELECT with options
      DEBUG/INFO/WARN/ERROR.
- [ ] Number fields (e.g. `voice_*` thresholds) render as range
      sliders or number inputs per SCHEMA.
- [ ] Boolean fields render as toggles.
- [ ] Changing any field flips the row's dirty indicator on.
- [ ] Dirty count in header reflects total dirty rows.
- [ ] Per-row Revert discards that row's draft; dirty indicator off.
- [ ] Global Save: calls UpdateConfig then PersistConfig. After save,
      all dirty indicators clear. Closing and reopening the window
      shows the saved values.
- [ ] Global Reset overrides: calls ResetConfig with empty list.
      Registry values drop back to code defaults; panel reflects
      defaults.
- [ ] Change `log_level` to DEBUG + Save — Logs panel begins receiving
      DEBUG lines within 2 seconds (proves daemon picked up the
      runtime override, not just persisted it).

## 9.5 Stats panel

- [ ] Panel renders 6 sections: Soundboard, Voice Playback, Wake Word,
      STT, TTS, LLM.
- [ ] Every section is visibly filter-blurred and covered with a
      "Preview only" overlay.
- [ ] Latency bars render with avg + p95 tick from `MockData.js`.
- [ ] MiniBar values match the `MOCK_NUMBERS` constants (spot-check
      one).
- [ ] No section claims to be live / real — every number on this page
      comes from the mock file.

## 9.6 Offline behavior (daemon down)

From elevated shell:

```powershell
sc stop halbot
```

Return to dashboard without restarting it:

- [ ] Status bar flips to "Stopped" within 2–3 s.
- [ ] Logs panel stops receiving new lines but does **not** crash;
      existing buffer remains scrollable.
- [ ] Daemon panel Start button re-enables; Stop/Restart disable or
      grey out.
- [ ] Config panel shows last-known values (cached) with a warning
      banner ("daemon offline — edits cannot be saved") OR disables
      Save button. Either is acceptable; must not silently accept
      edits that get dropped.
- [ ] No uncaught JS errors in WebView2 console (open DevTools via
      right-click if available; otherwise skip).

Restart daemon:

```powershell
sc start halbot
```

- [ ] All panels recover within ~5 s without requiring a window
      reopen.

## 9.7 Update flow

Simulate a tray-only code change + redeploy while dashboard is open:

```powershell
# rebuild tray only
scripts\build.ps1 -Target tray
Expand-Archive -Force -Path dist\halbot-tray.zip -DestinationPath $env:TEMP\halbot-tray-new
scripts\update-tray.bat $env:TEMP\halbot-tray-new
```

- [ ] `update-tray.bat` kills the running tray cleanly (no orphan
      webview process left in Task Manager).
- [ ] New tray relaunches; left-click opens dashboard again.

## 9.8 Cross-panel integration

- [ ] Open Daemon panel, click Restart. Switch immediately to Logs
      panel. The backlog replay after restart appears (daemon startup
      lines visible within ~5 s).
- [ ] Open Config, flip `log_level` from INFO → DEBUG, Save. Switch to
      Logs: DEBUG lines begin appearing. Flip back to INFO, Save:
      DEBUG lines stop.
- [ ] Close window via custom title bar X. Tray icon still present.
      Click tray → dashboard re-opens with previously selected panel
      (Logs/Daemon/Config/Stats) and same level/grep filters. *(If
      state is not persisted, it is acceptable for it to reset to
      Logs + defaults — document which behavior is shipped.)*

## 9.9 Known acceptable gaps

These are in-scope for future plans, not bugs for this release:

- Stats panel numbers are mocks — the whole panel is overlaid
  "Preview only".
- Daemon panel Guilds card + Event log are mocked.
- No re-implementation of Discord / voice / LLM subsystems — those
  belong to later plans.
- No per-user tray autostart; tray must still be launched manually
  after reboot.
- Frontend builds only on machines with Node 20 + npm on PATH;
  daemon-only builds still ship without dashboard.

## 9.10 Done gate

If every checkbox in 9.1–9.8 passes and the gaps in 9.9 are
acknowledged, plan 007 is complete.

Release commit (after validation):

```powershell
# bump version in pyproject.toml + any display strings
git add pyproject.toml
git commit -m "v0.7.0: gui dashboard (pywebview + react)"
git tag v0.7.0
```

Do **not** tag until 9.1–9.8 all pass on a fresh install.
