@echo off
title Advankart Inventory Sync (keep this window open)
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Please run install.bat first.
  pause
  exit /b 1
)
set INVSYNC_LAUNCHER=1
set ARGS=
:run
venv\Scripts\python app.py %ARGS%
if "%errorlevel%"=="3" (
  echo Restarting after update...
  set ARGS=--no-browser --wait-port
  goto run
)
