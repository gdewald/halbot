#Requires -Version 5.1
<#
.SYNOPSIS
  Deploy local repo source to the live install at %ProgramFiles%\Halbot\.

.DESCRIPTION
  Modes:
    -Tray            tray-only deploy. Mirrors tray\, dashboard\,
                     frontend\dist\ to the install. Does NOT touch the
                     daemon, the service, or .venv\. No UAC required
                     when install.ps1 already granted you modify on
                     src\.
    -Daemon          daemon-only deploy. Mirrors halbot\, proto\.
                     Stops + starts the service. No UAC required when
                     the lock file is unchanged (uv sync skipped).
    (default = both) full deploy: mirror everything, stop service,
                     uv sync if lock changed, start service, bounce tray.

  UAC is only required when:
    - The lock file changed (uv sync writes to admin-only .venv\), OR
    - install.ps1 hasn't granted the user modify on src\ yet.
  In every other case the script runs unelevated.
#>
[CmdletBinding()]
param(
    [switch]$Daemon,
    [switch]$Tray,
    [switch]$NoBuild,
    [switch]$NoTrayBounce,
    [switch]$DryRun,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "Halbot")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try { $Host.UI.RawUI.WindowTitle = "halbot deploy" } catch { }

# Mode resolution. Default = both.
if (-not ($Daemon -or $Tray)) { $Daemon = $true; $Tray = $true }

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

function Test-CanWriteSrc {
    $probe = Join-Path $InstallRoot "src\.deploy-probe-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    try {
        Set-Content -Path $probe -Value "x" -ErrorAction Stop
        Remove-Item -Path $probe -Force -ErrorAction Ignore
        return $true
    } catch {
        return $false
    }
}

function Stop-HalbotService {
    $svc = Get-Service -Name halbot -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Stopped") {
        Stop-Service -Name halbot -Force -ErrorAction SilentlyContinue
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
    param([string[]]$Dirs, [bool]$IncludeFrontend, [bool]$IncludeProjectFiles)
    $srcDst = Join-Path $InstallRoot "src"
    $rcArgs = @("/MIR", "/NJH", "/NJS", "/NDL", "/NP", "/NFL", "/MT:8")
    foreach ($d in $Dirs) {
        $src = Join-Path $repoRoot $d
        $dst = Join-Path $srcDst $d
        if (Test-Path $src) {
            & robocopy $src $dst @rcArgs /XD __pycache__ .pytest_cache | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed exit=$LASTEXITCODE" }
        }
    }
    if ($IncludeFrontend) {
        $feDist = Join-Path $repoRoot "frontend\dist"
        if (Test-Path $feDist) {
            $feDst = Join-Path $srcDst "frontend\dist"
            & robocopy $feDist $feDst @rcArgs | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy frontend/dist failed exit=$LASTEXITCODE" }
        }
    }
    if ($IncludeProjectFiles) {
        foreach ($f in @("pyproject.toml", "uv.lock")) {
            Copy-Item -Force (Join-Path $repoRoot $f) (Join-Path $srcDst $f)
        }
    }
    # Refresh build stamp on any deploy.
    $bi = Join-Path $srcDst "halbot\_build_info.py"
    if (Test-Path (Split-Path $bi -Parent)) {
        $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
        Set-Content -Path $bi -Value "BUILD_TIMESTAMP = `"$ts`"`n" -NoNewline
    }
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
    # Kill the old pythonw spawn (if any) before relaunching. Match by
    # path so we don't kill unrelated pythonw processes.
    Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "$InstallRoot\.venv\*"
    } | Stop-Process -Force -ErrorAction SilentlyContinue
    # pythonw.exe -m tray, NOT the uv [project.gui-scripts] launcher.
    # uv-trampoline-gui leaks a console window on Win11 (subsystem 2 but
    # AllocConsole still happens). pythonw direct is clean.
    $pyw = Join-Path $InstallRoot ".venv\Scripts\pythonw.exe"
    $src = Join-Path $InstallRoot "src"
    if (Test-Path $pyw) {
        Start-Process -FilePath $pyw -ArgumentList @("-m", "tray") -WorkingDirectory $src -WindowStyle Hidden
    }
}

# --- main ---------------------------------------------------------------

if (-not (Test-Path (Join-Path $InstallRoot ".venv\Scripts\python.exe"))) {
    throw "no install at $InstallRoot. Run scripts\install.ps1 first."
}

# Decide what dirs to mirror based on mode flags.
$mirrorDirs    = @()
$includeFE     = $false
if ($Daemon) { $mirrorDirs += @("halbot", "proto") }
if ($Tray)   { $mirrorDirs += @("tray", "dashboard"); $includeFE = $true }

# Lock changes always force daemon-side concerns (it's a venv update).
$lockChanged    = Test-LockChanged
$includeProject = $Daemon -or $lockChanged
$needsService   = $Daemon -or $lockChanged
$needsAdmin     = $false

# Quick admin-need check.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($lockChanged) {
    $needsAdmin = $true   # uv sync writes to .venv\
} elseif (-not (Test-CanWriteSrc)) {
    $needsAdmin = $true   # install.ps1 didn't grant src ACL
}

if ($DryRun) {
    Write-Host "[deploy] dry-run plan:"
    Write-Host ("  mode:            daemon=$Daemon tray=$Tray")
    Write-Host ("  build:           $([bool](-not $NoBuild))")
    Write-Host ("  mirror dirs:     $($mirrorDirs -join ', ')")
    Write-Host ("  include frontend: $includeFE")
    Write-Host ("  include project files: $includeProject")
    Write-Host ("  stop+start svc:  $needsService")
    Write-Host ("  uv sync:         $lockChanged")
    Write-Host ("  needs admin:     $needsAdmin (currently admin: $isAdmin)")
    Write-Host ("  bounce tray:     $([bool]($Tray -and -not $NoTrayBounce))")
    Pop-Location
    return
}

# Self-elevate only when the operation actually requires admin.
if ($needsAdmin -and -not $isAdmin) {
    Write-Host "[deploy] elevating via UAC (lock changed or no src ACL)..."
    $passThru = @()
    foreach ($sw in @("Daemon", "Tray", "NoBuild", "NoTrayBounce")) {
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

$total = [System.Diagnostics.Stopwatch]::StartNew()

if (-not $NoBuild) {
    Time-Stage "build" { & (Join-Path $PSScriptRoot "build.ps1") }
}

if ($needsService) {
    Time-Stage "stop service" { Stop-HalbotService }
}
Time-Stage "mirror sources" {
    Mirror-Sources -Dirs $mirrorDirs -IncludeFrontend $includeFE -IncludeProjectFiles $includeProject
}
if ($lockChanged) {
    Time-Stage "uv sync" { Sync-Venv }
}
if ($needsService) {
    Time-Stage "start service" { Start-HalbotService }
}
if ($Tray -and -not $NoTrayBounce) {
    Time-Stage "bounce tray" { Bounce-Tray }
}

$total.Stop()
Write-Host ("[deploy] done in {0:N1}s" -f $total.Elapsed.TotalSeconds)
Pop-Location
