@echo off
REM One-shot smart deploy. Builds stale targets, deploys both atomically,
REM elevates once, streams log back. See scripts\deploy.ps1 for flags.
REM
REM Examples:
REM   scripts\deploy.bat                  build what changed + deploy both
REM   scripts\deploy.bat -Daemon          only touch daemon
REM   scripts\deploy.bat -Force           rebuild + redeploy regardless
REM   scripts\deploy.bat -DryRun          print plan, do nothing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
exit /b %ERRORLEVEL%
