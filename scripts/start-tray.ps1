# Launch the installed tray app without rebuilding. Kills any running tray
# instance first, then spawns it detached. Same convention as deploy.ps1's
# Bounce-Tray: pythonw.exe -m tray, not the uv [project.gui-scripts]
# launcher (its GUI trampoline leaks a console window on Win11).
[CmdletBinding()]
param(
    [switch]$Foreground  # attach stdout/stderr to this window instead of detaching
)

$ErrorActionPreference = "Stop"
$installRoot = Join-Path $env:ProgramFiles "Halbot"
$pyw = Join-Path $installRoot ".venv\Scripts\pythonw.exe"
$src = Join-Path $installRoot "src"

if (-not (Test-Path $pyw)) {
    throw "tray python missing at $pyw -- run scripts\install.ps1 first."
}
if (-not (Test-Path (Join-Path $src "tray\__init__.py"))) {
    throw "tray package missing under $src -- run scripts\deploy.ps1 -Tray first."
}

# Match by Path so unrelated pythonw processes survive.
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "$installRoot\.venv\*"
} | ForEach-Object {
    Write-Host "[start-tray] killing pid $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

if ($Foreground) {
    Write-Host "[start-tray] launching in foreground: $pyw -m tray"
    & $pyw -m tray
} else {
    Write-Host "[start-tray] launching detached: $pyw -m tray"
    Start-Process -FilePath $pyw -ArgumentList @("-m", "tray") -WorkingDirectory $src -WindowStyle Hidden
    Write-Host "[start-tray] OK."
}
