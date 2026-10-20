@echo off
setlocal
chcp 65001 >nul
title FishingNet Stopper
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\stop.ps1"
if errorlevel 1 (
  echo.
  echo FishingNet could not stop cleanly. See the error above.
  echo Logs and database: %~dp0.runtime
  pause
  exit /b 1
)
echo.
echo FishingNet stopped.
exit /b 0
