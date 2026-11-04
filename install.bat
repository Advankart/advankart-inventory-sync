@echo off
title Advankart Inventory Sync - Setup
cd /d "%~dp0"
echo.
echo  ===  Advankart Inventory Sync - one-time setup  ===
echo.
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo  Python is not installed.
  echo  Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" while installing.
  echo  Then run this setup again.
  pause
  exit /b 1
)
if not exist venv (
  echo  Creating private Python environment...
  %PY% -m venv venv
)
echo  Installing components (needs internet, about 1 minute)...
venv\Scripts\python -m pip install --upgrade pip >nul
venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 ( echo  Install failed - check your internet and try again. & pause & exit /b 1 )

echo  Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Inventory Sync.lnk');" ^
 "$s.TargetPath='%~dp0start.bat';$s.WorkingDirectory='%~dp0';$s.WindowStyle=7;$s.IconLocation='shell32.dll,165';$s.Save()"

echo.
echo  Done!  Double-click "Inventory Sync" on your desktop to open the app.
echo.
pause
call start.bat
