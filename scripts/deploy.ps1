#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot smart deploy: builds stale targets, then swaps %ProgramFiles%\Halbot
  atomically. Handles elevation and streams the elevated log back to the
  caller so the non-elevated window is not left guessing.

.DESCRIPTION
  Fingerprints daemon and tray source trees independently. Rebuilds only the
  ones whose inputs changed since the last successful deploy. Refuses to
  deploy if either requested target has no dist\ output. Elevates once,
  swaps both, restarts service + tray, streams transcript back.

  Typical usage:
    scripts\deploy.ps1                  # build what changed, deploy both
    scripts\deploy.ps1 -Daemon          # only touch daemon
    scripts\deploy.ps1 -Force           # rebuild + redeploy both regardless
    scripts\deploy.ps1 -BuildOnly       # build, skip deploy
    scripts\deploy.ps1 -DryRun          # print plan, do nothing

.PARAMETER Daemon
  Only consider daemon. Mutually exclusive with -Tray.

.PARAMETER Tray
  Only consider tray. Mutually exclusive with -Daemon.

.PARAMETER Force
  Rebuild + redeploy even if fingerprint matches.

.PARAMETER BuildOnly
  Skip deploy step (no elevation, no service restart).

.PARAMETER NoBuild
  Skip build step (deploy whatever is already in dist\). Still refuses if
  dist\ output missing for a requested target.

.PARAMETER Clean
  Forwarded to build.ps1 -Clean.

.PARAMETER DryRun
  Print plan, exit.
#>
param(
    [switch]$Daemon,
    [switch]$Tray,
    [switch]$Force,
    [switch]$BuildOnly,
    [switch]$NoBuild,
    [switch]$Clean,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

# ---- target selection ----
if ($Daemon -and $Tray) { throw "-Daemon and -Tray are mutually exclusive" }
$wantDaemon = $Daemon -or (-not $Daemon -and -not $Tray)
$wantTray   = $Tray   -or (-not $Daemon -and -not $Tray)

# ---- source sets ----
# Each entry is either:
#   @{ File = "rel\path\to\file" }                         single file
#   @{ Dir  = "rel\dir"; Include = @("*.ext", ...) }       recursive dir scan
# Shared entries appear in both targets so a dep bump rebuilds both.
$sharedSpecs = @(
    @{ File = "pyproject.toml" },
    @{ File = "uv.lock" },
    @{ File = "scripts\gen_proto.ps1" },
    @{ File = "scripts\build.ps1" },
    @{ Dir = "proto"; Include = @("*.proto") }
)

$daemonSpecs = $sharedSpecs + @(
    @{ Dir = "halbot"; Include = @("*.py") },
    @{ File = "build_daemon.spec" },
    @{ File = "halbot_daemon_entry.py" }
)

$traySpecs = $sharedSpecs + @(
    @{ Dir = "tray"; Include = @("*.py", "*.ico", "*.png") },
    @{ Dir = "dashboard"; Include = @("*.py", "*.html") },
    @{ Dir = "frontend\src"; Include = @("*") },
    @{ File = "frontend\index.html" },
    @{ File = "frontend\vite.config.js" },
    @{ File = "frontend\package.json" },
    @{ File = "frontend\package-lock.json" },
    @{ File = "build_tray.spec" },
    @{ File = "halbot_tray_entry.py" }
)

# Exclusions: generated / ephemeral. Match against lowercased relative path
# using -like semantics (\ separator, * wildcard).
$excludePatterns = @(
    "halbot\_gen\*",          # regenerated from proto by build
    "halbot\_build_info.py",  # stamped every build
    "*\__pycache__\*",
    "*.pyc"
)

function Resolve-SourceSet($specs) {
    $files = @{}
    foreach ($spec in $specs) {
        if ($spec.ContainsKey('File')) {
            $p = Join-Path $root $spec.File
            if (Test-Path -LiteralPath $p -PathType Leaf) {
                $fi = Get-Item -LiteralPath $p
                $files[$spec.File.ToLowerInvariant()] = $fi
            }
            continue
        }
        $dirPath = Join-Path $root $spec.Dir
        if (-not (Test-Path -LiteralPath $dirPath)) { continue }
        $found = Get-ChildItem -LiteralPath $dirPath -File -Recurse -ErrorAction SilentlyContinue -Include $spec.Include
        foreach ($m in $found) {
            $rel = $m.FullName.Substring($root.Length).TrimStart('\')
            $relLc = $rel.ToLowerInvariant()
            $skip = $false
            foreach ($ex in $excludePatterns) {
                if ($relLc -like $ex) { $skip = $true; break }
            }
            if (-not $skip) { $files[$relLc] = $m }
        }
    }
    return $files.Values | Sort-Object FullName
}

function Compute-Fingerprint($fileList) {
    # Hash = SHA256 over "rel|size|mtime-ticks\n" lines. Fast; no file reads.
    $sb = New-Object System.Text.StringBuilder
    foreach ($f in $fileList) {
        $rel = $f.FullName.Substring($root.Length).TrimStart('\').ToLowerInvariant()
        [void]$sb.Append($rel); [void]$sb.Append('|')
        [void]$sb.Append($f.Length); [void]$sb.Append('|')
        [void]$sb.Append($f.LastWriteTimeUtc.Ticks); [void]$sb.Append("`n")
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($sb.ToString())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    $sha.Dispose()
    return -join ($hash | ForEach-Object { $_.ToString("x2") })
}

# ---- stamps ----
$stampPath = Join-Path $root "dist\.deploy-stamp.json"
function Read-Stamp {
    if (-not (Test-Path $stampPath)) { return @{} }
    try {
        $raw = Get-Content -Raw $stampPath
        $obj = $raw | ConvertFrom-Json
        $h = @{}
        foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = $p.Value }
        return $h
    } catch { return @{} }
}
function Write-Stamp($h) {
    $dir = Split-Path $stampPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    ($h | ConvertTo-Json -Depth 5) | Set-Content -Path $stampPath -Encoding utf8
}

# ---- plan ----
Write-Host "[deploy] scanning sources..." -ForegroundColor Cyan
$daemonFiles = if ($wantDaemon) { Resolve-SourceSet $daemonSpecs } else { @() }
$trayFiles   = if ($wantTray)   { Resolve-SourceSet $traySpecs }   else { @() }

$daemonFp = if ($wantDaemon) { Compute-Fingerprint $daemonFiles } else { $null }
$trayFp   = if ($wantTray)   { Compute-Fingerprint $trayFiles }   else { $null }

$stamp = Read-Stamp

$daemonBuildFp  = $stamp["daemon_build"]
$daemonDeployFp = $stamp["daemon_deploy"]
$trayBuildFp    = $stamp["tray_build"]
$trayDeployFp   = $stamp["tray_deploy"]

$daemonDistOk = (Test-Path (Join-Path $root "dist\halbot-daemon\halbot-daemon.exe"))
$trayDistOk   = (Test-Path (Join-Path $root "dist\halbot-tray\halbot-tray.exe"))

# Build?  Yes if: -Force, dist missing, or fingerprint changed vs last build stamp.
$buildDaemon = $wantDaemon -and (-not $NoBuild) -and ($Force -or -not $daemonDistOk -or ($daemonFp -ne $daemonBuildFp))
$buildTray   = $wantTray   -and (-not $NoBuild) -and ($Force -or -not $trayDistOk   -or ($trayFp   -ne $trayBuildFp))

# Deploy? Yes if: -Force, or fingerprint differs vs last deploy stamp, or dist newer than deploy stamp.
$deployDaemon = $wantDaemon -and (-not $BuildOnly) -and ($Force -or ($daemonFp -ne $daemonDeployFp))
$deployTray   = $wantTray   -and (-not $BuildOnly) -and ($Force -or ($trayFp   -ne $trayDeployFp))

function Reason-Build($will, $want, $noBuild, $force, $distOk, $fpNow, $fpStamp) {
    if (-not $want)  { return "skip (target not selected)" }
    if ($noBuild)    { return "skip (-NoBuild)" }
    if (-not $will)  { return "skip (up to date)" }
    if ($force)      { return "build (-Force)" }
    if (-not $distOk){ return "build (dist missing)" }
    if ($fpNow -ne $fpStamp) { return "build (source changed)" }
    return "build"
}
function Reason-Deploy($will, $want, $buildOnly, $force, $fpNow, $fpStamp) {
    if (-not $want)   { return "skip (target not selected)" }
    if ($buildOnly)   { return "skip (-BuildOnly)" }
    if (-not $will)   { return "skip (up to date)" }
    if ($force)       { return "deploy (-Force)" }
    if ($fpNow -ne $fpStamp) { return "deploy (source changed)" }
    return "deploy"
}

Write-Host ""
Write-Host "Plan:" -ForegroundColor Yellow
Write-Host ("  daemon  files={0,4}  {1,-32}  {2}" -f `
    (@($daemonFiles).Count), `
    (Reason-Build  $buildDaemon  $wantDaemon $NoBuild $Force $daemonDistOk $daemonFp $daemonBuildFp), `
    (Reason-Deploy $deployDaemon $wantDaemon $BuildOnly $Force $daemonFp $daemonDeployFp))
Write-Host ("  tray    files={0,4}  {1,-32}  {2}" -f `
    (@($trayFiles).Count), `
    (Reason-Build  $buildTray  $wantTray $NoBuild $Force $trayDistOk $trayFp $trayBuildFp), `
    (Reason-Deploy $deployTray $wantTray $BuildOnly $Force $trayFp $trayDeployFp))
Write-Host ""

if ($DryRun) {
    Write-Host ("  fingerprints: daemon={0} tray={1}" -f $daemonFp, $trayFp) -ForegroundColor DarkGray
    Write-Host ("  last build:   daemon={0} tray={1}" -f $daemonBuildFp, $trayBuildFp) -ForegroundColor DarkGray
    Write-Host ("  last deploy:  daemon={0} tray={1}" -f $daemonDeployFp, $trayDeployFp) -ForegroundColor DarkGray
    Write-Host "[deploy] -DryRun: exiting without running anything." -ForegroundColor Cyan
    Pop-Location
    return
}

# ---- build ----
if ($buildDaemon -or $buildTray) {
    $buildTarget =
        if     ($buildDaemon -and $buildTray) { "all" }
        elseif ($buildDaemon)                 { "daemon" }
        else                                  { "tray" }
    # Hashtable splat (not array) — array splat treats "-Target" as a
    # positional value, which hits build.ps1's ValidateSet on $Target and
    # errors with "-Target does not belong to the set all,daemon,tray".
    $buildArgs = @{ Target = $buildTarget }
    if ($Clean) { $buildArgs.Clean = $true }
    Write-Host "[deploy] building ($buildTarget)..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "build.ps1") @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "build failed" }

    # Stamp only what we built. If fingerprint recomputes the same it's a
    # no-op; but if build rewrote _build_info.py we don't want that change to
    # bust our stamp (we already excluded it).
    if ($buildDaemon) { $stamp["daemon_build"] = $daemonFp }
    if ($buildTray)   { $stamp["tray_build"]   = $trayFp }
    Write-Stamp $stamp
} else {
    Write-Host "[deploy] build: nothing to do." -ForegroundColor DarkGray
}

# ---- pre-deploy safety gate ----
# Never deploy a target whose dist output is absent or whose fingerprint
# drifted from the latest build stamp (means build failed silently, or
# someone edited sources between build and deploy).
function Verify-Deployable($target, $want, $distOk, $fpNow, $buildFp) {
    if (-not $want) { return $true }
    if (-not $distOk) {
        Write-Host "[deploy] ABORT: $target requested for deploy but dist\ output is missing." -ForegroundColor Red
        return $false
    }
    if ($fpNow -ne $buildFp) {
        Write-Host "[deploy] ABORT: $target source fingerprint ($fpNow) does not match last build ($buildFp). Rerun without -NoBuild." -ForegroundColor Red
        return $false
    }
    return $true
}

if (-not $BuildOnly) {
    $ok = $true
    if ($deployDaemon) {
        if (-not (Verify-Deployable "daemon" $true $daemonDistOk $daemonFp $stamp["daemon_build"])) { $ok = $false }
    }
    if ($deployTray) {
        if (-not (Verify-Deployable "tray" $true $trayDistOk $trayFp $stamp["tray_build"])) { $ok = $false }
    }
    if (-not $ok) { throw "deploy gate failed" }
}

# ---- deploy ----
if ($BuildOnly) {
    Write-Host "[deploy] -BuildOnly: skipping deploy step." -ForegroundColor Cyan
    Pop-Location
    return
}

if (-not $deployDaemon -and -not $deployTray) {
    Write-Host "[deploy] deploy: nothing to do." -ForegroundColor DarkGray
    Pop-Location
    return
}

$deployDaemonPath = if ($deployDaemon) { Join-Path $root "dist\halbot-daemon" } else { "" }
$deployTrayPath   = if ($deployTray)   { Join-Path $root "dist\halbot-tray"   } else { "" }

# ---- elevated swap with streamed output ----
# Child writes to $logFile, creates $doneFile when finished. Parent tails
# $logFile until $doneFile shows up, preserving exit code via $exitFile.
$logFile   = Join-Path $env:TEMP ("halbot-deploy-{0}.log" -f ([Guid]::NewGuid().ToString("N").Substring(0,8)))
$doneFile  = "$logFile.done"
$exitFile  = "$logFile.exit"
$childPs1  = "$logFile.ps1"

$childBody = @"
`$ErrorActionPreference = 'Stop'
`$code = 0
`$script:LogFile = '$logFile'

function Log {
    param([string]`$msg)
    # Append to log file unbuffered so the parent tailer sees it immediately.
    `$fs = [System.IO.File]::Open(`$script:LogFile, 'Append', 'Write', 'ReadWrite')
    `$sw = New-Object System.IO.StreamWriter(`$fs)
    `$sw.WriteLine(`$msg)
    `$sw.Flush(); `$sw.Close(); `$fs.Close()
    # Also emit to local console so user sees it if the window is visible.
    Write-Host `$msg
}

function Run-Native {
    param([string]`$exe, [string[]]`$exeArgs)
    Log "> `$exe `$(`$exeArgs -join ' ')"
    `$psi = New-Object System.Diagnostics.ProcessStartInfo
    `$psi.FileName = `$exe
    `$psi.Arguments = (`$exeArgs | ForEach-Object {
        if (`$_ -match '\s') { '"' + `$_ + '"' } else { `$_ }
    }) -join ' '
    `$psi.RedirectStandardOutput = `$true
    `$psi.RedirectStandardError  = `$true
    `$psi.UseShellExecute = `$false
    `$psi.CreateNoWindow = `$true
    `$p = [System.Diagnostics.Process]::Start(`$psi)
    while (-not `$p.StandardOutput.EndOfStream) { Log `$p.StandardOutput.ReadLine() }
    `$err = `$p.StandardError.ReadToEnd()
    `$p.WaitForExit()
    if (`$err) { Log `$err.TrimEnd() }
    return `$p.ExitCode
}

try {
    `$deployDaemon = [bool]::Parse('$deployDaemon')
    `$deployTray   = [bool]::Parse('$deployTray')
    `$daemonSrc    = '$deployDaemonPath'
    `$traySrc      = '$deployTrayPath'
    `$daemonDst    = Join-Path `$env:ProgramFiles 'Halbot\daemon'
    `$trayDst      = Join-Path `$env:ProgramFiles 'Halbot\tray'

    Log "[elevated] running as `$([Security.Principal.WindowsIdentity]::GetCurrent().Name)"

    if (`$deployDaemon) {
        Log "[elevated] stopping service halbot..."
        Run-Native 'sc.exe' @('stop','halbot') | Out-Null
        for (`$i = 0; `$i -lt 20; `$i++) {
            Start-Sleep -Milliseconds 500
            `$q = & sc.exe query halbot 2>&1
            if (`$q -match 'STATE.*STOPPED') { break }
        }
    }
    if (`$deployTray) {
        Log "[elevated] killing halbot-tray.exe..."
        Get-Process -Name halbot-tray -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }

    if (`$deployDaemon) {
        Log "[elevated] swapping daemon bundle -> `$daemonDst"
        if (-not (Test-Path `$daemonDst)) { New-Item -ItemType Directory -Force -Path `$daemonDst | Out-Null }
        # robocopy mirror, multi-thread, quiet.
        `$rc = Run-Native 'robocopy.exe' @(`$daemonSrc, `$daemonDst, '/MIR','/MT:16','/R:2','/W:1','/NFL','/NDL','/NP')
        if (`$rc -ge 8) { throw "robocopy daemon failed exit=`$rc" }
    }
    if (`$deployTray) {
        Log "[elevated] swapping tray bundle -> `$trayDst"
        if (-not (Test-Path `$trayDst)) { New-Item -ItemType Directory -Force -Path `$trayDst | Out-Null }
        `$rc = Run-Native 'robocopy.exe' @(`$traySrc, `$trayDst, '/MIR','/MT:16','/R:2','/W:1','/NFL','/NDL','/NP')
        if (`$rc -ge 8) { throw "robocopy tray failed exit=`$rc" }
    }

    if (`$deployDaemon) {
        Log "[elevated] starting service halbot..."
        Run-Native 'sc.exe' @('start','halbot') | Out-Null
    }
    if (`$deployTray) {
        Log "[elevated] relaunching tray..."
        Start-Process -FilePath (Join-Path `$trayDst 'halbot-tray.exe')
    }

    Log "[elevated] done."
} catch {
    Log ""
    Log "[elevated] ERROR: `$(`$_.Exception.Message)"
    Log `$_.ScriptStackTrace
    `$code = 1
}
Set-Content -Path '$exitFile' -Value `$code
Set-Content -Path '$doneFile' -Value 'done'
"@

Set-Content -Path $childPs1 -Value $childBody -Encoding utf8

# Pre-create empty log so our tailer has something to open.
Set-Content -Path $logFile -Value "" -Encoding utf8

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

if ($isAdmin) {
    Write-Host "[deploy] already elevated; running swap inline." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $childPs1
    $childExit = if (Test-Path $exitFile) { [int](Get-Content $exitFile -Raw).Trim() } else { $LASTEXITCODE }
} else {
    Write-Host "[deploy] elevating for service/ProgramFiles access; streaming log..." -ForegroundColor Cyan
    $proc = Start-Process powershell.exe -Verb RunAs -PassThru -WindowStyle Hidden -ArgumentList @(
        "-NoProfile","-ExecutionPolicy","Bypass","-File",$childPs1
    )

    # Tail loop.
    $pos = 0
    $timeoutSec = 180
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while (-not (Test-Path $doneFile)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "[deploy] timeout waiting for elevated child (${timeoutSec}s)." -ForegroundColor Red
            break
        }
        if (Test-Path $logFile) {
            try {
                $fs = [System.IO.File]::Open($logFile, 'Open', 'Read', 'ReadWrite')
                if ($fs.Length -gt $pos) {
                    $fs.Seek($pos, 'Begin') | Out-Null
                    $sr = New-Object System.IO.StreamReader($fs)
                    $chunk = $sr.ReadToEnd()
                    $pos = $fs.Position
                    $sr.Close()
                    if ($chunk) { Write-Host $chunk -NoNewline }
                }
                $fs.Close()
            } catch { }
        }
        Start-Sleep -Milliseconds 150
    }
    # Final flush.
    if (Test-Path $logFile) {
        try {
            $fs = [System.IO.File]::Open($logFile, 'Open', 'Read', 'ReadWrite')
            if ($fs.Length -gt $pos) {
                $fs.Seek($pos, 'Begin') | Out-Null
                $sr = New-Object System.IO.StreamReader($fs)
                $chunk = $sr.ReadToEnd()
                $sr.Close()
                if ($chunk) { Write-Host $chunk -NoNewline }
            }
            $fs.Close()
        } catch { }
    }
    Write-Host ""
    $childExit = if (Test-Path $exitFile) { [int](Get-Content $exitFile -Raw).Trim() } else { 1 }
}

# Cleanup scratch files.
Remove-Item -Force -ErrorAction Ignore $logFile, $doneFile, $exitFile, $childPs1, "$childPs1.inner.ps1"

if ($childExit -ne 0) {
    Pop-Location
    throw "elevated swap failed (exit=$childExit)"
}

# ---- stamp deploy ----
if ($deployDaemon) { $stamp["daemon_deploy"] = $daemonFp }
if ($deployTray)   { $stamp["tray_deploy"]   = $trayFp }
Write-Stamp $stamp

Write-Host ""
Write-Host "[deploy] OK." -ForegroundColor Green
Pop-Location
