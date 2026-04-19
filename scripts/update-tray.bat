@echo off
REM Swap halbot tray onedir bundle from %~1 (extracted zip), relaunch.
SETLOCAL
IF "%~1"=="" (
    echo usage: update-tray.bat ^<new-tray-dir^>
    exit /b 2
)
SET NEW=%~1
SET DEST=%ProgramFiles%\Halbot\tray

taskkill /IM halbot-tray.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

IF EXIST "%DEST%" (
    rmdir /S /Q "%DEST%"
)
mkdir "%DEST%"
xcopy /E /I /Y "%NEW%\*" "%DEST%\" >nul

start "" "%DEST%\halbot-tray.exe"
ENDLOCAL
