# Halbot — Agent Configuration

## Gemma 4 Primary Directives

You are running as a local Gemma 4 e4b model through Claude Code. You have limited capacity compared to cloud models. Follow these rules strictly to maximize your effectiveness.

### Core Rules

1. **One step at a time.** Never plan more than one tool call ahead. Execute, read the result, then decide the next step.
2. **Read before writing.** Always read a file before editing it. Never guess at file contents.
3. **Read errors carefully.** When a tool call fails, read the FULL error message. The fix is usually in the error. Do not retry the same command.
4. **Stay in scope.** Only do what was asked. Do not refactor, add features, or "improve" things beyond the request.
5. **Ask if stuck.** If you fail at the same step twice, stop and ask the user what to do. Do not loop.

### Tool Use Patterns

#### Creating a file
1. Write the file
2. Verify it exists (ls or read first few lines)

#### Editing a file
1. Read the file first
2. Make ONE edit at a time
3. If the edit fails, re-read the file and try again with correct context

#### Running code
1. Run the command
2. If it errors, read the error
3. Make ONE fix based on the error
4. Run again

#### Debugging
1. Read the error message or failing test output
2. Read the relevant source file
3. Identify the specific line causing the issue
4. Make a targeted fix
5. Run the test again

### What You're Good At
- Writing single files (scripts, functions, classes)
- Explaining code
- Answering questions
- Simple edits to existing files
- Running commands and interpreting output

### What To Avoid
- Editing more than 2 files in one task
- Long chains of dependent tool calls (>5 in a row)
- Complex git workflows
- Generating large amounts of boilerplate
- Retrying failed operations without changing approach

## Project state

**Currently mid-restructure, phase 1 of [003](docs/plans/003-project-restructure-impl.md).**
Phase 1 skeleton: daemon + tray + build/deploy. **No Discord / voice /
LLM code in repo right now.** Original bot code wiped; features re-land
phase-by-phase on top of new architecture.

Single-user private-server toy. No harden for public/multi-tenant.

## Architecture (phase 1)

Two PyInstaller onedir bundles talking over local gRPC:

- **Daemon** (`halbot/` package, runs as Windows service `halbot` via
  NSSM, LocalSystem). Async gRPC server on `127.0.0.1:50199` exposing
  `Health`, `GetConfig`, `UpdateConfig`, `PersistConfig`, `ResetConfig`.
  Currently only emits periodic INFO + DEBUG tick logs so log-level
  toggle observable in tray.
- **Tray** (`tray/` package, user-mode pystray). Service Start/Stop/
  Restart, log viewer, log-level radio (auto-persists), reset overrides.
  Menu handlers run in worker threads so UI never blocks.

Config has three layers (lowest → highest precedence): code default →
`HKLM\SOFTWARE\Halbot\Config` registry → runtime override. Runtime
override lives in daemon process memory; `PersistConfig` promotes it to
registry; `ResetConfig` drops it. Only field this phase: `log_level`.

## Repo layout

```
halbot/                 daemon package
  _gen/                 generated gRPC stubs (committed)
  daemon.py             CLI entry: run / setup --install|--uninstall
  mgmt_server.py        async gRPC server
  config.py             layered config, per-field Source tracking
  logging_setup.py      rotating file handler, runtime reconfigure()
  installer.py          NSSM + HKLM registry + ACL grants
  paths.py              data_dir(): %ProgramData%\Halbot (frozen) / ./_dev_data (source)
tray/                   tray package (pystray + grpc client + tkinter log viewer)
halbot_daemon_entry.py  PyInstaller entry shim (keeps package imports valid)
halbot_tray_entry.py    ditto
proto/mgmt.proto
build_daemon.spec       PyInstaller onedir spec
build_tray.spec
scripts/
  build.ps1             full build: stamp _build_info.py, gen_proto, uv sync, pyinstaller, zip
  gen_proto.ps1
  update-daemon.bat     swap ProgramFiles\Halbot\daemon + restart service
  update-tray.bat       swap ProgramFiles\Halbot\tray + relaunch
infra/                  Terraform (unchanged, legacy GCP VM config — not used this phase)
docs/plans/             design (002) + impl (003) plans
```

Runtime paths (frozen): `%ProgramFiles%\Halbot\{daemon,tray}\` binaries,
`%ProgramData%\Halbot\logs\halbot.log` (+ `halbot-service.log` nssm
stdout), `HKLM\SOFTWARE\Halbot\Config` registry.

## Build / deploy

```powershell
# Full build: proto stubs + uv sync + pyinstaller (daemon + tray) + zip.
# Output: dist\halbot-daemon.zip, dist\halbot-tray.zip (nssm.exe bundled in daemon).
scripts\build.ps1

# Install (elevated):
$src = "<repo>\dist"
$dst = "$env:ProgramFiles\Halbot"
New-Item -ItemType Directory -Force -Path "$dst\daemon","$dst\tray" | Out-Null
Expand-Archive -Force -Path "$src\halbot-daemon.zip" -DestinationPath "$dst\daemon"
Expand-Archive -Force -Path "$src\halbot-tray.zip"   -DestinationPath "$dst\tray"
& "$dst\daemon\halbot-daemon.exe" setup --install

# Launch tray (non-elevated):
& "$env:ProgramFiles\Halbot\tray\halbot-tray.exe"

# Uninstall (elevated):
& "$env:ProgramFiles\Halbot\daemon\halbot-daemon.exe" setup --uninstall
```

`setup --install` creates NSSM service, grants installing user
`KEY_WRITE` on HKLM config key (via win32api DACL), grants
`SERVICE_START|STOP|QUERY_STATUS` via `sc sdset`, grants user modify on
`%ProgramData%\Halbot` via icacls, auto-starts service.

No per-user autostart this phase — tray launched manually.

## Source run (no build)

```powershell
uv sync --only-group daemon
uv run python -m halbot.daemon run
# Data dir becomes .\_dev_data\ (gitignored).
# Source run cannot PersistConfig: HKLM write requires admin /
# post-install ACL grant.
```

## Code conventions

- No ORM, no frameworks beyond grpc + pystray + pywin32. Keep simple.
- Service Start/Stop/Query in tray: open handle with minimum access mask
  (`SERVICE_START | SERVICE_STOP | SERVICE_QUERY_STATUS`) rather than
  `win32serviceutil.StopService` which opens with `SERVICE_ALL_ACCESS`
  and fails for non-admin.
- Pystray menu handlers: always dispatch real work to
  `threading.Thread(daemon=True)`. Handler runs on UI thread; any block
  freezes tray. Likewise `checked` callbacks must be O(1) — read cached
  state, refresh in background loop.
- gRPC stubs committed under `halbot/_gen/`. Regenerate via
  `scripts\gen_proto.ps1` after editing `proto/mgmt.proto`.
- PyInstaller entry scripts are shims at repo root
  (`halbot_daemon_entry.py`, `halbot_tray_entry.py`). Directly pointing
  PyInstaller at `halbot/daemon.py` breaks relative imports.
- Build stamp: `scripts\build.ps1` writes
  `halbot/_build_info.py` (gitignored) with local-timezone timestamp;
  exposed via `Health().daemon_version`. Source run falls back to
  process-start wall time with `(source)` suffix.
- Log file path from `halbot.paths.log_file()`. Never hardcode.

## Common pitfalls

- **Port 50199, not 50051/50737.** Surrounding range `50736-50935` is
  excluded by `http.sys` on dev box — grpc bind to 50737 fails with
  `Failed to add port to server`. Check
  `netsh interface ipv4 show excludedportrange protocol=tcp` before
  picking a new port.
- **nssm.cc occasional 503.** Download of bundled nssm.exe in
  `build.ps1` can transient-fail. Retry or cache `nssm.exe` next to
  daemon.exe manually; installer resolves via `shutil.which("nssm")`
  first, then `sys.executable`'s dir.
- **Registry ACL grant uses `winreg.KEY_ALL_ACCESS`** constants, not
  `ntsecuritycon`. `ntsecuritycon.KEY_ALL_ACCESS` does not exist — an
  earlier build raised "module has no attribute KEY_ALL_ACCESS".
- **`win32serviceutil` opens SERVICE_ALL_ACCESS.** See conventions.
- **Tkinter from non-main thread** is fragile on Windows. Log viewer
  runs its own `mainloop()` in a daemon thread, which works in practice
  but limit scope — one viewer window, destroy cleanly on close.
- **`PermissionError: WinError 5` on PersistConfig when running from
  source** is expected: daemon runs under current user (not LocalSystem)
  and user lacks HKLM write until `setup --install` has granted it
  (grant is persisted on the HKLM key, not the process).

## Explicitly absent this phase

- Discord client, voice receiving, faster-whisper, TTS, LLM calls,
  `sounds.db` usage, `persona` system — all gone until later phases
  re-introduce them on top of this skeleton.
- Secrets / DPAPI / `DISCORD_TOKEN` handling. `log_level` is plaintext
  registry only.
- Module-level RPCs (`RestartDiscord`, `LoadWhisper`, etc.).
- Per-user tray autostart (HKCU Run / Startup shortcut).
- `README.md` **intentionally stale** — still describes pre-restructure
  flat `bot.py` + `uv run bot.py` world. Do not read or edit README
  during phase 1; re-anchor to this file.
