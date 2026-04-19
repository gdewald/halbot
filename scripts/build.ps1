#Requires -Version 5.1
# Build halbot-daemon + halbot-tray onedir bundles and zip them.
#
# -Target all|daemon|tray (default: all)
# -Clean   wipe build/ + dist/ before building (default: incremental; reuses
#          PyInstaller analysis cache which is the big win on rebuilds)
# -NoZip   skip archive step
param(
    [ValidateSet("all","daemon","tray")]
    [string]$Target = "all",
    [switch]$Clean,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

function Time-Stage($name, $block) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $block
    $sw.Stop()
    Write-Host ("[stage] {0}: {1:N1}s" -f $name, $sw.Elapsed.TotalSeconds)
    return $sw.Elapsed
}

function Find-SevenZip {
    $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "$env:ProgramFiles(x86)\7-Zip\7z.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Zip-Dir($srcDir, $zipPath, $sevenZip) {
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    if ($sevenZip) {
        # -mx=3 = fast deflate; -mmt=on = multithread. Output .zip for
        # Expand-Archive compatibility on install side.
        & $sevenZip a -tzip -mx=3 -mmt=on $zipPath (Join-Path $srcDir "*") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z failed: $LASTEXITCODE" }
    } else {
        Compress-Archive -Force -Path (Join-Path $srcDir "*") -DestinationPath $zipPath
    }
}

$buildDaemon = ($Target -eq "all") -or ($Target -eq "daemon")
$buildTray   = ($Target -eq "all") -or ($Target -eq "tray")

try {
    $total = [System.Diagnostics.Stopwatch]::StartNew()

    # Stamp build info (local timezone).
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    $buildInfo = "BUILD_TIMESTAMP = `"$ts`"`n"
    Set-Content -Path (Join-Path $root "halbot\_build_info.py") -Value $buildInfo -NoNewline
    Write-Host "stamped build: $ts  target=$Target clean=$([bool]$Clean)"

    # Regenerate proto stubs (cheap, always).
    Time-Stage "proto" { & (Join-Path $PSScriptRoot "gen_proto.ps1") }

    if ($Clean) {
        Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "build")
        Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "dist")
    } else {
        # Per-target clean of dist output only; keep build/ cache.
        if ($buildDaemon) {
            Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "dist\halbot-daemon")
            Remove-Item -Force -ErrorAction Ignore (Join-Path $root "dist\halbot-daemon.zip")
        }
        if ($buildTray) {
            Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "dist\halbot-tray")
            Remove-Item -Force -ErrorAction Ignore (Join-Path $root "dist\halbot-tray.zip")
        }
    }

    $sevenZip = Find-SevenZip
    if ($sevenZip) { Write-Host "using 7zip: $sevenZip" } else { Write-Host "7zip not found; falling back to Compress-Archive" }

    if ($buildDaemon) {
        Time-Stage "uv sync daemon" {
            uv sync --only-group daemon --only-group build
        }
        Time-Stage "pyinstaller daemon" {
            uv run pyinstaller --noconfirm --distpath dist --workpath build build_daemon.spec
        }

        Time-Stage "fetch nssm" {
            $nssmDest = Join-Path $root "dist\halbot-daemon\nssm.exe"
            if (-not (Test-Path $nssmDest)) {
                $tmp = Join-Path $env:TEMP "nssm-2.24.zip"
                if (-not (Test-Path $tmp)) {
                    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $tmp
                }
                $extract = Join-Path $env:TEMP "nssm-2.24"
                if (-not (Test-Path $extract)) {
                    Expand-Archive -Path $tmp -DestinationPath $env:TEMP -Force
                }
                Copy-Item -Path (Join-Path $extract "win64\nssm.exe") -Destination $nssmDest -Force
            }
        }

        if (-not $NoZip) {
            Time-Stage "zip daemon" {
                Zip-Dir (Join-Path $root "dist\halbot-daemon") (Join-Path $root "dist\halbot-daemon.zip") $sevenZip
            }
        }
    }

    if ($buildTray) {
        Time-Stage "uv sync tray" {
            uv sync --only-group tray --only-group build
        }
        Time-Stage "pyinstaller tray" {
            uv run pyinstaller --noconfirm --distpath dist --workpath build build_tray.spec
        }

        if (-not $NoZip) {
            Time-Stage "zip tray" {
                Zip-Dir (Join-Path $root "dist\halbot-tray") (Join-Path $root "dist\halbot-tray.zip") $sevenZip
            }
        }
    }

    $total.Stop()
    Write-Host ("[total] {0:N1}s" -f $total.Elapsed.TotalSeconds)
} finally {
    Pop-Location
}
