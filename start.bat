@echo off
:: Launch Halbot tray app in the background (no console window).
:: Usage: start.bat
cd /d "%~dp0"
start "" /B uv run --all-extras pythonw halbot_tray.py
