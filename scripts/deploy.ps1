#Requires -Version 5.1
<#
.SYNOPSIS
  Deploy local repo source to the live install at %ProgramFiles%\Halbot\.

.DESCRIPTION
  Single deploy path. Whether Claude or the user invokes, this is the
  one script that touches the live install. No flags-that-only-Claude-
  uses, no fingerprint stamps, no elevated mirror dance.

  Steps (self-elevates once via UAC):
    1. Run scripts\build.ps1 (gen_proto + frontend) unless -NoBuild.
    2. sc stop halbot.
    3. Robocopy halbot\, tray\, dashboard\, frontend\dist\, proto\
       and pyproject.toml + uv.lock from repo to %ProgramFiles%\Halbot\src\.
    4. If pyproject.toml or uv.lock changed since last sync:
       uv sync --frozen against the install's .venv\.
    5. sc start halbot.
    6. Bounce the tray (kill + relaunch via halbot-tray.cmd).

  Brief outage during steps 2-5. That's the trade.

.NOTES
  uv must be on PATH (the build host's uv reaches the install dir's
  pyproject.toml via --project).
#>
[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$NoTrayBounce,
    [switch]$DryRun,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "Halbot")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

# Title both the launching shell and the elevated child so the user sees
# "halbot deploy" instead of an empty title bar while UAC + the elevated
# PS window are open.
try { $Host.UI.RawUI.WindowTitle = "halbot deploy" } catch { }

# --- self-elevate -------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun) {
    Write-Host "[deploy] elevating via UAC..."
    # -Command (not -File) so we can set the elevated window title BEFORE
    # the script runs, and pause at the end so the user can read output
    # before the window closes (deploys are usually fast enough that
    # auto-close means you miss the [deploy] done line).
    $passThru = @()
    foreach ($sw in @("NoBuild", "NoTrayBounce")) {
        if ($PSBoundParameters[$sw]) { $passThru += "-$sw" }
    }
    if ($PSBoundParameters.ContainsKey("InstallRoot")) {
        $passThru += @("-InstallRoot", "'$InstallRoot'")
    }
    $passThruStr = $passThru -join " "
    $inner = "`$Host.UI.RawUI.WindowTitle = 'halbot deploy (elevated)'; " +
             "& '$PSCommandPath' $passThruStr; " +
             "`$code = `$LASTEXITCODE; " +
             "Write-Host ''; Write-Host '[deploy] press enter to close' -ForegroundColor DarkGray; " +
             "[void][System.Console]::ReadLine(); exit `$code"
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $inner)
    $proc = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -PassThru -Wait
    Pop-Location
    exit $proc.ExitCode
}

function Time-Stage($name, $block) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $block
    $sw.Stop()
    Write-Host ("[stage] {0}: {1:N1}s" -f $name, $sw.Elapsed.TotalSeconds)
}

function Test-LockChanged {
    foreach ($f in @("pyproject.toml", "uv.lock")) {
        $repoFile = Join-Path $repoRoot $f
        $instFile = Join-Path $InstallRoot "src\$f"
        if (-not (Test-Path $instFile)) { return $true }
        if ((Get-FileHash $repoFile).Hash -ne (Get-FileHash $instFile).Hash) { return $true }
    }
    return $false
}

function Stop-HalbotService {
    $svc = Get-Service -Name halbot -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Stopped") {
        Stop-Service -Name halbot -Force -ErrorAction SilentlyContinue
        # Wait briefly for handles to release before robocopy.
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Service -Name halbot).Status -ne "Stopped" -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
    }
}

function Start-HalbotService {
    Start-Service -Name halbot -ErrorAction Continue
}

function Mirror-Sources {
    $srcDst = Join-Path $InstallRoot "src"
    $rcArgs = @("/MIR", "/NJH", "/NJS", "/NDL", "/NP", "/NFL", "/MT:8")
    foreach ($d in @("halbot", "tray", "dashboard", "proto")) {
        $src = Join-Path $repoRoot $d
        $dst = Join-Path $srcDst $d
        if (Test-Path $src) {
            & robocopy $src $dst @rcArgs /XD __pycache__ .pytest_cache | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed exit=$LASTEXITCODE" }
        }
    }
    $feDist = Join-Path $repoRoot "frontend\dist"
    if (Test-Path $feDist) {
        $feDst = Join-Path $srcDst "frontend\dist"
        & robocopy $feDist $feDst @rcArgs | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy frontend/dist failed exit=$LASTEXITCODE" }
    }
    foreach ($f in @("pyproject.toml", "uv.lock")) {
        Copy-Item -Force (Join-Path $repoRoot $f) (Join-Path $srcDst $f)
    }
    # Refresh build stamp (cheap, helps Health() report deploy time).
    $bi = Join-Path $srcDst "halbot\_build_info.py"
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    Set-Content -Path $bi -Value "BUILD_TIMESTAMP = `"$ts`"`n" -NoNewline
}

function Sync-Venv {
    $srcDst = Join-Path $InstallRoot "src"
    $venv   = Join-Path $InstallRoot ".venv"
    $env:UV_PROJECT_ENVIRONMENT = $venv
    try {
        & uv sync --frozen --project $srcDst --no-dev `
            --group daemon --group tray
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed exit=$LASTEXITCODE" }
    } finally {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction Ignore
    }
}

function Bounce-Tray {
    if ($NoTrayBounce) { return }
    Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "$InstallRoot\.venv\*"
    } | Stop-Process -Force -ErrorAction SilentlyContinue
    $cmd = Join-Path $InstallRoot "halbot-tray.cmd"
    if (Test-Path $cmd) {
        Start-Process -FilePath $cmd -WindowStyle Hidden
    }
}

# --- main ---------------------------------------------------------------

if (-not (Test-Path (Join-Path $InstallRoot ".venv\Scripts\python.exe"))) {
    throw "no install at $InstallRoot. Run scripts\install.ps1 first."
}

if ($DryRun) {
    $lockChanged = Test-LockChanged
    Write-Host "[deploy] dry-run plan:"
    Write-Host "  build:           $([bool](-not $NoBuild))"
    Write-Host "  stop service:    yes"
    Write-Host "  mirror sources:  yes"
    Write-Host "  uv sync:         $lockChanged"
    Write-Host "  start service:   yes"
    Write-Host "  bounce tray:     $([bool](-not $NoTrayBounce))"
    Pop-Location
    return
}

$total = [System.Diagnostics.Stopwatch]::StartNew()

if (-not $NoBuild) {
    Time-Stage "build" { & (Join-Path $PSScriptRoot "build.ps1") }
}

$lockChanged = Test-LockChanged
Write-Host "[deploy] lock changed: $lockChanged"

Time-Stage "stop service" { Stop-HalbotService }
Time-Stage "mirror sources" { Mirror-Sources }
if ($lockChanged) {
    Time-Stage "uv sync" { Sync-Venv }
}
Time-Stage "start service" { Start-HalbotService }
Time-Stage "bounce tray" { Bounce-Tray }

$total.Stop()
Write-Host ("[deploy] done in {0:N1}s" -f $total.Elapsed.TotalSeconds)
Pop-Location
