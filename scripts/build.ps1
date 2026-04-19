#Requires -Version 5.1
# Build halbot-daemon + halbot-tray onedir bundles and zip them.
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

try {
    $total = [System.Diagnostics.Stopwatch]::StartNew()

    # Stamp build info (local timezone).
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    $buildInfo = "BUILD_TIMESTAMP = `"$ts`"`n"
    Set-Content -Path (Join-Path $root "halbot\_build_info.py") -Value $buildInfo -NoNewline
    Write-Host "stamped build: $ts"

    # Regenerate proto stubs.
    Time-Stage "proto" { & (Join-Path $PSScriptRoot "gen_proto.ps1") }

    # Clean prior build.
    Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "build")
    Remove-Item -Recurse -Force -ErrorAction Ignore (Join-Path $root "dist")

    # Sync + build daemon.
    Time-Stage "uv sync daemon" {
        uv sync --only-group daemon --only-group build
    }
    Time-Stage "pyinstaller daemon" {
        uv run pyinstaller --noconfirm --distpath dist --workpath build build_daemon.spec
    }

    # Sync + build tray.
    Time-Stage "uv sync tray" {
        uv sync --only-group tray --only-group build
    }
    Time-Stage "pyinstaller tray" {
        uv run pyinstaller --noconfirm --distpath dist --workpath build build_tray.spec
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

    Time-Stage "zip daemon" {
        Compress-Archive -Force -Path (Join-Path $root "dist\halbot-daemon\*") `
            -DestinationPath (Join-Path $root "dist\halbot-daemon.zip")
    }
    Time-Stage "zip tray" {
        Compress-Archive -Force -Path (Join-Path $root "dist\halbot-tray\*") `
            -DestinationPath (Join-Path $root "dist\halbot-tray.zip")
    }

    $total.Stop()
    Write-Host ("[total] {0:N1}s" -f $total.Elapsed.TotalSeconds)
} finally {
    Pop-Location
}
