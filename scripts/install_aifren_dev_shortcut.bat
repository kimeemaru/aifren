@echo off
setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
cd /d "%REPO%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\install_aifren_dev_shortcut.ps1"
if errorlevel 1 (
  echo Could not create the AIFren Dev desktop shortcut.
  pause
  exit /b 1
)
echo.
echo AIFren Dev is ready on your Desktop.
pause
