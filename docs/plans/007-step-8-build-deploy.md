# Step 8 — Build + Deploy

**Goal:** wire the frontend build into `scripts\build.ps1`, update
`build_tray.spec` to bundle `frontend/dist/` + pywebview binaries
into the tray onedir, update `pyproject.toml` entrypoints, update
`CLAUDE.md`'s `-Clean` list, and produce a shippable
`dist\halbot-tray.zip` containing the dashboard.

**Runnable at end:** yes — a fresh tray install opened from the
shipped zip can open the dashboard.

## Files you will touch

- `scripts/build.ps1` (edit — add frontend npm step)
- `build_tray.spec` (edit — add datas + hidden imports)
- `pyproject.toml` (edit — add `dashboard` to tray scripts,
  confirm pywebview/psutil dependencies)
- `frontend/.gitignore` (confirmed from step 3)
- `CLAUDE.md` (edit — update -Clean triggers + repo layout)
- `halbot_tray_entry.py` (verify no changes needed)

Do not touch `halbot/`, `frontend/src/`, or `dashboard/*.py`
logic in this step.

## 8.1 Extend `scripts\build.ps1`

Insert a new frontend-build stage *before* the `uv sync tray`
stage. Gated on `-Target all|tray`. Skips gracefully if Node is
missing so daemon-only builds still work.

Find the block at around line 136 that starts with
`if ($buildTray) {`. Immediately before it, insert:

```powershell
    if ($buildTray) {
        $frontendDir = Join-Path $root "frontend"
        $hasFrontend = Test-Path (Join-Path $frontendDir "package.json")
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if ($hasFrontend -and $npm) {
            Time-Stage "frontend install" {
                Push-Location $frontendDir
                try {
                    if (-not (Test-Path "node_modules") -or $Clean) {
                        npm ci
                        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
                    }
                } finally { Pop-Location }
            }
            Time-Stage "frontend build" {
                Push-Location $frontendDir
                try {
                    npm run build
                    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
                } finally { Pop-Location }
            }
        } elseif ($hasFrontend -and -not $npm) {
            Write-Warning "frontend/ present but npm not on PATH; dashboard will be missing from the tray bundle."
        }
    }
```

Do not modify the existing `if ($buildTray)` block below — it
still runs the `uv sync` + `pyinstaller tray` stages unchanged.

## 8.2 Update `build_tray.spec`

Replace the file contents with:

```python
# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir spec for halbot-tray (with dashboard).

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (
    collect_submodules("grpc")
    + collect_submodules("tray")
    + collect_submodules("dashboard")
    + [
        "halbot._gen.mgmt_pb2",
        "halbot._gen.mgmt_pb2_grpc",
        "pystray._win32",
        "webview",
        "webview.platforms.edgechromium",
        "psutil",
    ]
)

datas = []

# Bundle the built frontend (if present) under dashboard/web/.
_fe_dist = Path("frontend/dist")
if (_fe_dist / "index.html").exists():
    datas += [(str(_fe_dist), "dashboard/web")]

# Always bundle the step-2 stub so the window still opens if the
# frontend build was skipped (e.g. Node missing on a daemon-only
# build). dashboard/paths.py falls back to it.
datas += [("dashboard/_stub.html", "dashboard")]

# pywebview carries platform-specific JS shim files it loads via
# importlib.resources. collect_data_files picks them up.
datas += collect_data_files("webview")

a = Analysis(
    ["halbot_tray_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="halbot-tray",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="halbot-tray",
)
```

Key points:

- `collect_data_files("webview")` is **required** — pywebview's
  Edge Chromium platform loads JS shims as package data; without
  this, the frozen window opens blank.
- `dashboard/_stub.html` is bundled unconditionally so a tray
  without a frontend still opens a window (important for dev
  builds that skip `npm run build`).
- `frontend/dist/` bundles as `dashboard/web/` to match
  `dashboard/paths.py::web_dir()` from step 2.

## 8.3 Add `halbot-tray` dashboard entrypoint (optional)

In `pyproject.toml`, confirm the tray entrypoint still launches
`halbot_tray_entry.py`. Add a second script for running the
dashboard headless (useful for QA without the tray icon running):

```toml
[project.scripts]
halbot-daemon = "halbot.daemon:main"
halbot-tray = "tray.tray:main"
halbot-dashboard = "dashboard.app:main"
```

Only the third line is new. Leave the other two as they are.

## 8.4 Update `CLAUDE.md`

### 8.4.1 — Add `frontend/` to the repo-layout block

Find the ```` ``` ```` block under "Repo layout" and extend it
with:

```
frontend/               dashboard Vite/React app (step 3+)
  src/                  tokens.js, panels/, components/, fonts/
  dist/                 built output (gitignored)
dashboard/              tray-side dashboard package (step 2+)
  app.py                pywebview entry
  bridge.py             js_api bridge
  log_stream.py         StreamLogs consumer
  paths.py              web_dir() resolver
```

### 8.4.2 — Extend the "When to use `-Clean`" list

Append:

```
- frontend/src changes that require a fresh npm ci (rare — usually
  an incremental `npm run build` is enough).
- dashboard/ spec/datas changes (same rule as any PyInstaller
  datas edit: cache invalidation is unreliable).
```

### 8.4.3 — Note the dashboard in "Project state"

Find the "Currently mid-restructure, phase 1 of 003." sentence and
extend the paragraph:

```
v0.7 (WIP) adds a pywebview dashboard launched from the tray —
see docs/plans/007-gui-dashboard.md for the step-by-step plan.
```

## 8.5 Verification gate

### 8.5.1 — Clean build

```powershell
scripts\build.ps1 -Clean -Target tray
```

Expected stages:

```
[stage] frontend install: ...
[stage] frontend build: ...
[stage] uv sync tray: ...
[stage] pyinstaller tray: ...
[stage] zip tray: ...
```

`dist\halbot-tray.zip` exists and is >20 MB (pywebview + fonts +
JS bundles bring size up from the pre-dashboard baseline). If
the zip is suspiciously small (<10 MB), the frontend assets did
not make it in — recheck step 8.2 datas block.

### 8.5.2 — Sanity-unzip

```powershell
$tmp = Join-Path $env:TEMP "halbot-tray-sanity"
Remove-Item -Recurse -Force $tmp -ErrorAction Ignore
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Expand-Archive -Path dist\halbot-tray.zip -DestinationPath $tmp
Test-Path (Join-Path $tmp "_internal\dashboard\web\index.html")
Test-Path (Join-Path $tmp "_internal\dashboard\_stub.html")
```

Both `Test-Path` calls must print `True`. If either prints
`False`:

- `index.html` missing → frontend build skipped or
  `frontend/dist/` was empty at PyInstaller time. Re-run
  `npm run build` manually, then rebuild.
- `_stub.html` missing → datas entry typo in `build_tray.spec`.

### 8.5.3 — Fresh install + smoke

Follow the existing "Deploy — operational (update existing
install)" instructions in `CLAUDE.md`:

```powershell
# elevated
Expand-Archive -Force -Path dist\halbot-tray.zip -DestinationPath $env:TEMP\halbot-tray-new
scripts\update-tray.bat $env:TEMP\halbot-tray-new
```

Then:

- Tray icon appears.
- Left-click → dashboard window opens within 2–3 seconds.
- All four panels functional.
- Close window → tray icon still running, can re-open.

If the window opens blank / white:

- WebView2 runtime missing on the box (rare on Win11):
  `winget install Microsoft.EdgeWebView2Runtime`
- `collect_data_files("webview")` forgot some hidden assets. Run
  the tray from an unzipped dist with a console and inspect
  stderr.

### 8.5.4 — Incremental dev rebuild

Verify the fast path still works:

```powershell
# Touch a CSS rule in frontend/src/styles.css, then:
scripts\build.ps1 -Target tray
```

Should reuse the PyInstaller analysis cache and finish in
~30–60s. If it re-runs full analysis every time, inspect the
spec file for syntax that forces reanalysis.

## Commit

```powershell
git add scripts/build.ps1 build_tray.spec pyproject.toml CLAUDE.md docs/plans/007-step-8-build-deploy.md
git commit -m "feat(007): bundle dashboard into halbot-tray build"
```

Do not bump a version number in this commit — that belongs to the
final release commit after step 9's validation passes.
