<#
.SYNOPSIS
  First-time install (or hard-reinstall) of Halbot under
  %ProgramFiles%\Halbot\.

.DESCRIPTION
  Replaces v0.8's PyInstaller-based install. New layout:

    %ProgramFiles%\Halbot\
      python\        uv-managed standalone Python 3.12
      .venv\         project venv (uv sync --frozen target)
      src\           halbot, tray, dashboard, frontend, proto, pyproject, uv.lock
      nssm.exe       service host
      halbot-tray.cmd  pythonw.exe -m tray launcher

  Steps:
    1. Verify elevated.
    2. Stop + uninstall existing halbot service (NSSM or sc).
    3. Wipe v0.8 layout under %ProgramFiles%\Halbot\{daemon,tray}\.
    4. Ensure uv has a usable Python 3.12 (`uv python install`).
    5. Build + copy the source mirror.
    6. `uv sync --frozen` into .venv\.
    7. Drop nssm.exe into install root (fetch if missing).
    8. Hand off to `halbot.installer:install` (NSSM service create,
       HKLM ACLs, ProgramData ACLs, service-control ACL, autostart).
    9. Write halbot-tray.cmd launcher.

  Idempotent: re-running a clean install does not lose registry config
  or DPAPI secrets (those live under HKLM\SOFTWARE\Halbot, untouched
  here). Use `halbot-daemon.exe setup --uninstall` (legacy) or the
  matching uninstall.ps1 to wipe data + secrets.

.NOTES
  Run from elevated PowerShell at the repo root. uv must be on PATH.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "Halbot"),
    [string]$PythonVersion = "3.12",
    [switch]$SkipBuild,
    [switch]$SkipServiceStart
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "install.ps1 must run from an elevated PowerShell."
    }
}

function Stop-LegacyService {
    $svc = Get-Service -Name halbot -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "[install] stopping existing halbot service"
        Stop-Service -Name halbot -Force -ErrorAction SilentlyContinue
        # Try NSSM first, fall back to sc.
        $nssm = (Join-Path $InstallRoot "nssm.exe")
        if (Test-Path $nssm) {
            & $nssm remove halbot confirm | Out-Null
        } else {
            & sc.exe delete halbot | Out-Null
        }
    }
}

function Wipe-V08Layout {
    foreach ($d in @("daemon", "tray")) {
        $p = Join-Path $InstallRoot $d
        if (Test-Path $p) {
            Write-Host "[install] removing v0.8 layout: $p"
            Remove-Item -Recurse -Force -ErrorAction Ignore $p
        }
    }
    # Also drop the slot dirs if a previous A/B sketch was tested.
    foreach ($d in @("slot-a", "slot-b", "current")) {
        $p = Join-Path $InstallRoot $d
        if (Test-Path $p) {
            Write-Host "[install] removing leftover: $p"
            Remove-Item -Recurse -Force -ErrorAction Ignore $p
        }
    }
}

function Ensure-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "uv not on PATH. Install: winget install --id=astral-sh.uv -e"
    }
    return $cmd.Source
}

function Ensure-Python {
    Write-Host "[install] ensuring uv has Python $PythonVersion"
    & uv python install $PythonVersion
    if ($LASTEXITCODE -ne 0) { throw "uv python install $PythonVersion failed" }
}

function Build-Source {
    if ($SkipBuild) { return }
    Write-Host "[install] building proto + frontend"
    & (Join-Path $PSScriptRoot "gen_proto.ps1")
    if ($LASTEXITCODE -ne 0) { throw "gen_proto failed" }

    $fe = Join-Path $repoRoot "frontend"
    if (Test-Path (Join-Path $fe "package.json")) {
        Push-Location $fe
        try {
            if (-not (Test-Path "node_modules")) {
                & npm ci
                if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
            }
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        } finally { Pop-Location }
    } else {
        Write-Warning "frontend/ missing -- skipping npm build (dashboard will not render)"
    }
}

function Mirror-Sources {
    $srcDst = Join-Path $InstallRoot "src"
    Write-Host "[install] mirroring source -> $srcDst"
    New-Item -ItemType Directory -Force -Path $srcDst | Out-Null

    # Per-package directories.
    $rcArgs = @("/MIR", "/NJH", "/NJS", "/NDL", "/NP", "/NFL", "/MT:8")
    foreach ($d in @("halbot", "tray", "dashboard", "proto")) {
        $src = Join-Path $repoRoot $d
        $dst = Join-Path $srcDst $d
        if (Test-Path $src) {
            & robocopy $src $dst @rcArgs /XD __pycache__ .pytest_cache | Out-Null
            # robocopy exit codes 0-7 = success
            if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed exit=$LASTEXITCODE" }
        }
    }

    # frontend/dist (built artifacts only -- skip src + node_modules).
    $feDist = Join-Path $repoRoot "frontend\dist"
    if (Test-Path $feDist) {
        $feDst = Join-Path $srcDst "frontend\dist"
        & robocopy $feDist $feDst @rcArgs | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy frontend/dist failed exit=$LASTEXITCODE" }
    }

    # Project files needed by uv sync (and runtime imports of the gen
    # stubs use the proto/_gen/ tree above).
    foreach ($f in @("pyproject.toml", "uv.lock")) {
        Copy-Item -Force (Join-Path $repoRoot $f) (Join-Path $srcDst $f)
    }

    # Drop the old PyInstaller _build_info stamp; replace with a fresh one.
    $bi = Join-Path $srcDst "halbot\_build_info.py"
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    Set-Content -Path $bi -Value "BUILD_TIMESTAMP = `"$ts`"`n" -NoNewline
}

function Sync-Venv {
    $srcDst   = Join-Path $InstallRoot "src"
    $venv     = Join-Path $InstallRoot ".venv"
    Write-Host "[install] uv sync --frozen --project $srcDst (env=$venv)"
    $env:UV_PROJECT_ENVIRONMENT = $venv
    try {
        & uv sync --frozen --project $srcDst --no-dev `
            --group daemon --group tray
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed exit=$LASTEXITCODE" }
    } finally {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction Ignore
    }
}

function Ensure-Nssm {
    $nssmDst = Join-Path $InstallRoot "nssm.exe"
    if (Test-Path $nssmDst) { return }
    Write-Host "[install] fetching nssm-2.24"
    $tmp     = Join-Path $env:TEMP "nssm-2.24.zip"
    $extract = Join-Path $env:TEMP "nssm-2.24"
    $exe     = Join-Path $extract "win64\nssm.exe"
    if (-not (Test-Path $exe)) {
        Remove-Item -Recurse -Force -ErrorAction Ignore $extract
        Remove-Item -Force -ErrorAction Ignore $tmp
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $tmp
        Expand-Archive -Path $tmp -DestinationPath $env:TEMP -Force
        if (-not (Test-Path $exe)) { throw "nssm extract failed" }
    }
    Copy-Item -Path $exe -Destination $nssmDst -Force
}

function Install-Service {
    $venvPy = Join-Path $InstallRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) { throw "venv python missing: $venvPy" }
    Write-Host "[install] running halbot.installer:install via venv python"
    & $venvPy -m halbot.daemon setup --install
    if ($LASTEXITCODE -ne 0) { throw "halbot.installer:install failed exit=$LASTEXITCODE" }
}

function Grant-SrcAcl {
    # Let the install user write to src\ without elevation. Source-only
    # deploys (halbot/*.py, tray/*.py, frontend/dist/*) only need this
    # grant; lock-changing deploys still need admin because uv sync
    # writes into .venv\ which stays admin-only.
    $user = $env:USERNAME
    if (-not $user) { return }
    $srcDst = Join-Path $InstallRoot "src"
    Write-Host "[install] granting $user modify on $srcDst"
    & icacls $srcDst /grant "${user}:(OI)(CI)M" /T /C | Out-Null
}

function Write-TrayLauncher {
    $cmd = Join-Path $InstallRoot "halbot-tray.cmd"
    $pyw = Join-Path $InstallRoot ".venv\Scripts\pythonw.exe"
    $body = "@echo off`r`nstart `"`" `"$pyw`" -m tray`r`n"
    [System.IO.File]::WriteAllText($cmd, $body)
    Write-Host "[install] tray launcher: $cmd"
}

# --- main ---------------------------------------------------------------

Require-Admin
Ensure-Uv | Out-Null
Stop-LegacyService
Wipe-V08Layout
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null

Ensure-Python
Build-Source
Mirror-Sources
Ensure-Nssm
Sync-Venv
Install-Service
Grant-SrcAcl
Write-TrayLauncher

if (-not $SkipServiceStart) {
    Write-Host "[install] starting halbot service"
    Start-Service -Name halbot -ErrorAction Continue
}

Write-Host ""
Write-Host "[install] done. Service auto-starts at boot."
Write-Host "[install] tray: $(Join-Path $InstallRoot 'halbot-tray.cmd')"
Pop-Location
