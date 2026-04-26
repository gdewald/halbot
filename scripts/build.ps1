#Requires -Version 5.1
<#
.SYNOPSIS
  Regenerate proto stubs + build the React frontend.

.DESCRIPTION
  Post-PyInstaller world. There is no PyInstaller analysis, no spec
  files, no onedir bundle, no cache invalidation ritual. Whatever you
  edited under halbot/ / tray/ / dashboard/ runs as-is at deploy time.

  This script handles only the two real "build" steps:
    1. gen_proto -- regenerates halbot/_gen/ from proto/mgmt.proto.
       Cheap (~0.3 s) and idempotent. Always run.
    2. npm run build -- compiles frontend/src/ to frontend/dist/.
       Used by the dashboard (tray-launched) and the /halbot-stats
       static snapshot (daemon-rendered).

  Everything else (uv sync, source mirror, service install) lives in
  install.ps1 + deploy.ps1 because it touches the live install, not
  this checkout.

.NOTES
  Stays a thin wrapper so editors / CI can call individual stages too.
#>
param(
    [switch]$NoFrontend,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

function Time-Stage($name, $block) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $block
    $sw.Stop()
    Write-Host ("[stage] {0}: {1:N1}s" -f $name, $sw.Elapsed.TotalSeconds)
}

try {
    $total = [System.Diagnostics.Stopwatch]::StartNew()

    Time-Stage "proto" { & (Join-Path $PSScriptRoot "gen_proto.ps1") }

    if (-not $NoFrontend) {
        $fe = Join-Path $root "frontend"
        if (Test-Path (Join-Path $fe "package.json")) {
            $npm = Get-Command npm -ErrorAction SilentlyContinue
            if (-not $npm) {
                Write-Warning "npm not on PATH; skipping frontend build."
            } else {
                Time-Stage "frontend install" {
                    Push-Location $fe
                    try {
                        if ($Clean) {
                            Remove-Item -Recurse -Force -ErrorAction Ignore "node_modules", "dist"
                        }
                        if (-not (Test-Path "node_modules")) {
                            & npm ci
                            if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
                        }
                    } finally { Pop-Location }
                }
                Time-Stage "frontend build" {
                    Push-Location $fe
                    try {
                        & npm run build
                        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
                    } finally { Pop-Location }
                }
            }
        }
    }

    $total.Stop()
    Write-Host ("[total] {0:N1}s" -f $total.Elapsed.TotalSeconds)
} finally {
    Pop-Location
}
