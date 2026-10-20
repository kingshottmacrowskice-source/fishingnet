@echo off
setlocal
chcp 65001 >nul
title FishingNet Launcher
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\launch.ps1"
if errorlevel 1 (
  echo.
  echo FishingNet could not start. See the error above.
  echo Logs: %~dp0.runtime
  pause
  exit /b 1
)
exit /b 0
