# 021 — Drop PyInstaller; install via uv venv

## Problem

`scripts\build.ps1` cold-builds in ~6.5 min, ~2-3 min incremental. The
COLLECT phase (`shutil.copyfile` of 5.4 GB venv → `dist\_internal\`)
dominates wall time. Every iteration of "edit halbot/foo.py → deploy"
pays this cost. Hardlink experiments saved seconds at best — the syscall
count is the real bottleneck and PyInstaller has no incremental COLLECT.

Daemon and tray each ship a full ~5.4 GB / ~600 MB onedir bundle with
duplicated deps. Spec files (hidden imports, datas, collect_*)
churn whenever new top-level imports land. `-Clean` is regularly needed
because PyInstaller's analysis cache silently goes stale on spec edits.

The cumulative complexity (specs, deploy stamp, elevated mirror,
nssm fetch, hardlink helper attempt, `_Clean` ritual, fingerprint
sidecar) all exists to compensate for the fact that the build is slow.

## Decision

Drop PyInstaller entirely. Run from a frozen uv venv installed under
`%ProgramFiles%\Halbot\`. NSSM points the service at
`.venv\Scripts\python.exe -m halbot.daemon run`. Single install
location (no A/B slot — outage on deploy is acceptable).

User decisions:
- Host == build machine. Same uv + Python + repo on disk.
- Bundled standalone Python via `uv python install 3.12`.
- Source ships clear-text under `Program Files\` (single-user bot).
- Hard cut from v0.8 PyInstaller install. One-time migration outage OK.
- Deploy outage OK (subsequent zero-downtime not required).
- Best judgement: **strip every PyInstaller-related complexity** that
  doesn't carry over.

## Layout

```
%ProgramFiles%\Halbot\
├── python\                  uv-managed standalone Python 3.12
│   ├── python.exe
│   └── pythonw.exe
├── .venv\                   uv sync --frozen target
│   ├── Scripts\python.exe   launcher into ..\python\python.exe
│   └── Lib\site-packages\
├── src\                     mirror of repo source dirs
│   ├── halbot\
│   ├── tray\
│   ├── dashboard\
│   ├── frontend\dist\       npm run build output
│   └── proto\               for runtime imports of generated stubs
├── nssm.exe                 unchanged service host
└── halbot-tray.cmd          one-liner: pythonw.exe -m tray
```

NSSM args:
- Application: `%ProgramFiles%\Halbot\.venv\Scripts\python.exe`
- Arguments: `-m halbot.daemon run`
- AppDirectory: `%ProgramFiles%\Halbot\src\`
- LocalSystem account.

`%ProgramData%\Halbot\` (logs, sounds.db, events.db) and
`HKLM\SOFTWARE\Halbot\{Config,Secrets}` unchanged.

## Build

```powershell
scripts\build.ps1   # gen_proto + npm run build  (~5-10 s)
```

That's the entire build. No PyInstaller, no `-Clean`, no spec, no
nssm fetch. `nssm.exe` is bundled once at install time, never
rebuilt.

## Deploy

```powershell
scripts\deploy.ps1
```

Steps (single script, self-elevates once via UAC):
1. `sc stop halbot` (brief outage starts).
2. Robocopy `halbot\`, `tray\`, `dashboard\`, `frontend\dist\`,
   `proto\_gen\` from repo → `%ProgramFiles%\Halbot\src\`.
3. If `uv.lock` differs from install's last-synced lock:
   `uv sync --frozen --project %ProgramFiles%\Halbot\src\` with
   `UV_PROJECT_ENVIRONMENT=%ProgramFiles%\Halbot\.venv`. ~5-30 s
   depending on diff.
4. `sc start halbot`. Tray bounce.

Expected wall times:
- Source-only: ~5 s.
- Lock-changing: ~30 s.
- First full install: ~2-3 min (one-time uv sync of all deps from
  scratch).

## Files

**New**
- `scripts\install.ps1` — first-time setup. Wipes v0.8 layout under
  `Program Files\Halbot\{daemon,tray}\`. Runs `uv python install
  3.12` (idempotent), creates `Program Files\Halbot\` skeleton,
  copies source, runs initial `uv sync --frozen`, installs NSSM
  service via `halbot.installer.install()` adapted for new layout.
- `halbot-tray.cmd` (written by `install.ps1`) — one-line launcher.

**Rewritten**
- `scripts\build.ps1` — strip to `gen_proto.ps1` + `npm run build`.
- `scripts\deploy.ps1` — replace 600 LOC with ~80 LOC: stop service,
  robocopy, conditional uv sync, start service. Drop fingerprint
  stamp, elevated mirror, native-output capture, all the babysitting.
- `halbot\paths.py` — drop `_frozen()` / `sys._MEIPASS` branches.
  `data_dir()` becomes "if `__file__` resolves under
  `%ProgramFiles%\Halbot\src\`, return `%ProgramData%\Halbot\`; else
  `<repo>\_dev_data\`". `frontend_dist_dir()` → `<src>\frontend\dist\`
  in both modes.
- `halbot\installer.py` — `setup --install`/`--uninstall` adapted for
  new layout. NSSM args change. ACL grants unchanged.

**Deleted**
- `build_daemon.spec`, `build_tray.spec`
- `halbot_daemon_entry.py`, `halbot_tray_entry.py` — no PyInstaller
  shim needed; `python -m halbot.daemon` resolves the package
  normally.
- `_pyinstaller_hardlink.py` (the hardlink helper experiment).
- `dist\.deploy-stamp.json` and the supporting fingerprint logic in
  `deploy.ps1`.
- `halbot/_build_info.py` stays but stamping logic moves into
  `install.ps1` / `deploy.ps1`.

**Updated**
- `pyproject.toml` — drop the `build` group (only contained
  `pyinstaller>=6.3`).
- `CLAUDE.md`, `README.md` — rewrite Build, Deploy, Common pitfalls.
  Drop "When to use `-Clean`", spec-edit guidance, deploy-stamp
  pitfall, mirror scope pitfall, nssm cache pitfall.

## Verification

0. `scripts\install.ps1` on the dev box — service starts pre-login,
   `/halbot-stats` works, dashboard launches from tray.
1. `scripts\build.ps1` < 30 s (vs ~6.5 min today).
2. Source-only deploy < 10 s.
3. Lock-changing deploy < 60 s.
4. `uv run python -m halbot.daemon run` (dev) and the installed
   service execute the same import graph; behavior identical.
5. DPAPI roundtrip: `apply-r2-secrets.ps1` writes secret, daemon
   reads same plaintext after install + service restart.
6. Reboot host. Service comes up before login screen, voice + slash
   work without tray running.

## Non-goals

- Zero-downtime deploys (out of scope; outage acceptable).
- Hot-reload (plan 009).
- Cross-platform.
- Removing uv.

## Risk register

- **tkinter in `uv python install 3.12`.** Verify on first install;
  fallback = `winget install Python.Python.3.12` then point `python\`
  at it.
- **Native DLL gaps.** torch / faster-whisper / av / numpy loaded
  fresh from venv may surface MSVC-runtime issues PyInstaller's
  bundle was masking. Smoke before declaring done; if needed copy
  CRT DLLs into `python\`.
- **pywebview .NET.** WebView2 + .NET assemblies loaded via
  pythonnet/clr. Smoke dashboard launch.
- **First-deploy file lock.** Service must be stopped before `uv
  sync` touches `.venv\Lib\site-packages\*.pyd`. `deploy.ps1`
  enforces order.
