# Project Restructure — Implementation Plan

Status: draft. Tracks execution of the design in
[002-project-restructure.md](002-project-restructure.md). Only phases
explicitly reviewed live here. Unreviewed future phases sit in an
untracked working draft.

## Approach

- **Big-bang on `main` per phase.** Each phase lands on a branch, merges
  when its validation passes on a clean VM, then pushed.
- Phases are independently runnable at their endpoints.

## Phase 1 — Skeleton: daemon + tray + build/deploy

Prove the build, install, service, gRPC, and tray-to-daemon round-trip
end-to-end **before** any Discord/voice/LLM code moves. Fresh scaffolds,
intentionally **no reuse of current `bot.py` / `halbot_tray.py`**
implementation.

Branch: `restructure/phase-1-skeleton`. Old flat modules at repo root
(`bot.py`, `db.py`, `llm.py`, `audio.py`, `voice.py`, `voice_session.py`,
`tts.py`, `halbot_tray.py`, `prompts/`) are **deleted early in this
phase**. Branch holds only the new skeleton. No coexistence.

**Stable fallback via worktree.** To keep running the current bot while
phase 1 is in flight, check out `main` in a sibling worktree:

```
git worktree add ../halbot-stable main
cd ../halbot-stable && uv sync && uv run bot.py
```

Stable and phase 1 do not collide: gitignored `.env` / `sounds.db` are
per-worktree, gRPC port is unused by stable, NSSM service name is
unused by stable, logs live in different dirs. Only real conflict is
GPU VRAM and Discord identity — run one at a time.

Merge to `main` only when validation checklist below passes on a clean
VM.

### Scope delivered

- New `halbot/` daemon package with no Discord bot code.
- New `tray/` package with no bot imports.
- gRPC surface: `Health`, `GetConfig`, `UpdateConfig`, `PersistConfig`,
  `ResetConfig`. `ConfigState` holds one field: `log_level`.
- Periodic log emitter in daemon (INFO tick + DEBUG tick) so log-level
  toggle is observable in the tray log viewer.
- Tray features: log viewer (file tail), Service Start/Stop/Restart via
  SCM, log-level dropdown (auto-persists — dropdown click calls
  `UpdateConfig` + `PersistConfig` back-to-back), explicit Reset action
  (drops runtime override, reloads from registry).
- Full build / deploy / update pipeline: uv dependency groups, two
  PyInstaller onedir specs, two zips, installer subcommand (NSSM +
  registry ACL + service ACL + ProgramData ACL + HKCU Run),
  `update-daemon.bat` / `update-tray.bat`.

### Explicitly out of scope this phase

- Secrets / DPAPI / `DISCORD_TOKEN` — `log_level` is plaintext registry
  only.
- Discord client, voice, whisper, TTS, LLM — none of it runs in the new
  daemon yet.
- Module-level RPCs (`RestartDiscord`, `LoadWhisper`, …). Proto reserves
  space only if trivially additive; otherwise deferred.
- Discord-specific fields on `HealthReply` — this phase returns uptime +
  daemon_version only.

### Steps

**0. Purge old code**

- Delete `bot.py`, `db.py`, `llm.py`, `audio.py`, `voice.py`,
  `voice_session.py`, `tts.py`, `halbot_tray.py`, `prompts/`.
- Rewrite `pyproject.toml` from scratch (new deps + dependency groups).
- Keep: `infra/`, `docs/`, `.gitignore`, `sounds.db` (gitignored,
  no-op), `.env` (gitignored, no-op), `CLAUDE.md` (stale — update in
  phase 1 wrap-up).
- Commit: "chore: remove pre-restructure sources".

**1. Package scaffolding**

- `halbot/__init__.py`, `tray/__init__.py`.
- `halbot/daemon.py`: CLI entry. Subcommands: `run`, `setup install`,
  `setup uninstall`.
- `halbot/paths.py`: `data_dir()` returns `%ProgramData%\Halbot` when
  frozen, `./_dev_data/` when running from source.

**2. Proto + codegen**

- `proto/mgmt.proto`: `Health`, config RPCs, `ConfigState` with
  `log_level` field + per-field source enum
  (`DEFAULT | REGISTRY | RUNTIME_OVERRIDE`).
- `scripts/gen_proto.ps1`: invokes `grpc_tools.protoc`, emits
  `halbot/_gen/mgmt_pb2.py` + `mgmt_pb2_grpc.py`. Generated files
  committed.

**3. Config + logging plumbing**

- `halbot/config.py`: defaults dict (`{"log_level": "INFO"}`), registry
  I/O against `HKLM\SOFTWARE\Halbot\Config`, layered resolver (default
  → registry → runtime override), per-field source tracking.
- `halbot/logging_setup.py`: root logger configured with rotating file
  handler at `data_dir()/logs/halbot.log` (defaults: 10 MB × 5 files),
  level pulled from `config.log_level`. Exposes `reconfigure(level)` for
  live swap. Level applies globally to root logger — no per-source
  filtering at daemon side this phase. Source filtering moves to log
  viewer UI in a later phase.
- `UpdateConfig` handler: on `log_level` change, calls
  `logging_setup.reconfigure()`.

**4. Daemon body**

- `halbot/mgmt_server.py`: **async gRPC server** (`grpc.aio`) bound
  `127.0.0.1:50737`. Implements `Health` + four config RPCs. Async
  chosen now so discord.py (asyncio) integrates cleanly in later phases.
- `halbot/daemon.py run`: initializes logging, runs asyncio event loop
  hosting (a) ticker task emitting `logger.info("tick")` every 5s and
  `logger.debug("tick")` every 1s, (b) async gRPC server. Blocks until
  stop signal (NSSM `CTRL_BREAK`).
- `Health().daemon_version` = build timestamp in system local timezone
  (baked at PyInstaller build time via a generated
  `halbot/_build_info.py`, produced by `build.ps1`). Source-run
  fallback: current wall-clock at process start.
- `ResetConfig(fields)`: drops the listed runtime overrides; field
  values revert to registry (or code default if no registry entry).
  Does **not** wipe registry. Empty `fields` = reset all.

**5. Tray**

- `tray/mgmt_client.py`: wraps gRPC channel, auto-reconnect on
  `UNAVAILABLE`.
- `tray/tray.py`: pystray icon. Menu:
  - Service → Start / Stop / Restart (via `win32serviceutil`).
  - Open log viewer (tkinter `Text` tailing
    `data_dir()/logs/halbot.log`).
  - Log level → INFO / DEBUG / WARNING / ERROR submenu. Click
    auto-persists: sends `UpdateConfig({log_level: X})` then
    `PersistConfig(["log_level"])`.
  - Reset (drops runtime override for all fields).
  - Quit.

**6. uv groups + build specs**

- `pyproject.toml`: `[dependency-groups]` `daemon`
  (grpcio, grpcio-tools, pywin32) and `tray`
  (pystray, pillow, grpcio, pywin32). Keep scope tight — no torch, no
  discord.py.
- `build_daemon.spec` / `build_tray.spec`: PyInstaller onedir; include
  `halbot/_gen`; distinct output dirs.
- `scripts/build.ps1`: per-group `uv sync --only-group`, generates
  `halbot/_build_info.py` with local-timezone timestamp, runs
  `pyinstaller`, zips onedir → `dist/halbot-daemon.zip` +
  `dist/halbot-tray.zip`. Prints wall-clock duration per stage at end.

**7. Installer (`halbot-daemon setup --install`)**

- Create `%ProgramData%\Halbot\logs\` with ACL (LocalSystem write, user
  read).
- Create `HKLM\SOFTWARE\Halbot\Config`. `RegSetKeySecurity` grants
  installing user `KEY_WRITE`.
- `nssm install halbot <daemon.exe> run` + `AppThrottle 1500` +
  `AppRestartDelay 30000` + `AppExit Default Restart` + stdout/stderr
  redirect to log file.
- `sc sdset halbot ...` grants installing user
  `SERVICE_START / STOP / QUERY_STATUS`.
- **No autostart for tray this phase.** Installer skips HKCU Run /
  Startup shortcut — elevated installer cannot cleanly target the
  invoking user's HKCU. Manual step documented in validation. Automated
  per-user install deferred to a later phase.
- `--uninstall` reverses all of the above.

**8. Update scripts**

- `scripts/update-daemon.bat`: `sc stop halbot` → swap
  `%ProgramFiles%\Halbot\daemon\` → `sc start halbot`.
- `scripts/update-tray.bat`: signal tray exit → swap
  `%ProgramFiles%\Halbot\tray\` → launch new tray.

### Validation

On a clean Windows VM / test account:

1. `scripts/build.ps1` produces both zips.
2. Extract to `%ProgramFiles%\Halbot\daemon\` and `...\tray\`.
3. Elevated: `halbot-daemon.exe setup --install`.
4. Service auto-starts. `halbot.log` fills with INFO ticks only.
5. Launch tray manually (double-click `tray.exe`). Status shows
   "running". Log viewer tails live. Optional: drag tray exe shortcut
   into `shell:startup` for login autostart — not automated this phase.
6. Tray → log level DEBUG. DEBUG ticks appear in viewer within 1s.
7. Persist. SCM Restart. DEBUG level survives restart.
8. Reset. INFO default restored without daemon restart.
9. Tray → Stop. Service stops, status updates.
10. `setup --uninstall`. Registry, service, ProgramData all gone.
11. Record full `build.ps1` wall time (cold and warm). Document in this
    file as the baseline build-cycle cost for future reference.

Merge branch to `main`.

## Future phases

Not committed to this doc until reviewed. Working draft lives at
`docs/plans/drafts/phase-backlog.md` (gitignored).
