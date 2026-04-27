#!/usr/bin/env pwsh
# Start the dashboard in dev mode (FastAPI + uvicorn) for Claude/Playwright.
#
# Builds frontend if dist is missing. Server binds 127.0.0.1 only — no auth,
# do not expose. Default port 51199 (mirrors daemon's 50199, outside the
# http.sys excluded ranges 50000-50059 + 50200-50699 + 50736-50935).
#
# Usage:
#   scripts\dev-dashboard.ps1                 # build + serve on 51199
#   scripts\dev-dashboard.ps1 -Port 51234     # custom port
#   scripts\dev-dashboard.ps1 -NoBuild        # skip frontend build
[CmdletBinding()]
param(
    [int]$Port = 51199,
    [switch]$NoBuild
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "frontend\dist\index.html"

if (-not $NoBuild -or -not (Test-Path $dist)) {
    Write-Host ">> building frontend" -ForegroundColor Cyan
    Push-Location (Join-Path $root "frontend")
    try { npm run build } finally { Pop-Location }
}

if (-not (Test-Path $dist)) {
    Write-Error "frontend/dist/index.html missing after build"
    exit 1
}

$env:HALBOT_DASHBOARD_DEV = "1"
$env:HALBOT_DASHBOARD_DEV_PORT = "$Port"

Write-Host ">> dashboard dev server: http://127.0.0.1:$Port" -ForegroundColor Green
& uv run --project $root python -m dashboard.app
