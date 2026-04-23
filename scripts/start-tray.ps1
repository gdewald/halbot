# Launch installed tray app without rebuilding.
# Kills any running tray first, then spawns the installed exe detached.
[CmdletBinding()]
param(
    [switch]$Foreground  # attach stdout/stderr to this window instead of detaching
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $env:ProgramFiles "Halbot\tray\halbot-tray.exe"
if (-not (Test-Path $exe)) {
    throw "tray exe not found at $exe -- run scripts\deploy.ps1 -Tray first"
}

Get-Process -Name "halbot-tray" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "[start-tray] killing pid $($_.Id)"
    Stop-Process -Id $_.Id -Force
}
Start-Sleep -Milliseconds 400

if ($Foreground) {
    Write-Host "[start-tray] launching in foreground: $exe"
    & $exe
} else {
    Write-Host "[start-tray] launching detached: $exe"
    Start-Process -FilePath $exe -WindowStyle Hidden
    Write-Host "[start-tray] OK."
}
