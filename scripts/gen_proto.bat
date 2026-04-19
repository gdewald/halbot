@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0gen_proto.ps1" %*
