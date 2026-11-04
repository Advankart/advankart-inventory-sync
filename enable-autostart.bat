@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Inventory Sync.lnk');" ^
 "$s.TargetPath='%~dp0start.bat';$s.WorkingDirectory='%~dp0';$s.WindowStyle=7;$s.Save()"
echo Inventory Sync will now start automatically when Windows starts.
pause
