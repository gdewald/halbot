@echo off
REM Swap halbot daemon onedir bundle with a fresh one from %~1 (extracted zip).
SETLOCAL
IF "%~1"=="" (
    echo usage: update-daemon.bat ^<new-daemon-dir^>
    exit /b 2
)
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
