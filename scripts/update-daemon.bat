@echo off
REM Swap halbot daemon onedir bundle with a fresh one from %~1 (extracted zip).
IF "%~1"=="" (
    echo usage: update-daemon.bat ^<new-daemon-dir^>
    exit /b 2
)

REM Self-elevate if not already admin.
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo Elevating...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '\"%~1\"' -Verb RunAs"
    exit /b
)

SETLOCAL
SET NEW=%~1
SET DEST=%ProgramFiles%\Halbot\daemon

sc stop halbot
REM give SCM a moment.
timeout /t 3 /nobreak >nul

IF EXIST "%DEST%" (
    rmdir /S /Q "%DEST%"
)
mkdir "%DEST%"
xcopy /E /I /Y "%NEW%\*" "%DEST%\" >nul

sc start halbot
ENDLOCAL
