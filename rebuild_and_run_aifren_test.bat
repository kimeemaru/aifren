@echo off
setlocal
cd /d "%~dp0"

call "%CD%\scripts\build_aifren_test.bat"
if errorlevel 1 exit /b 1

call "%CD%\scripts\run_aifren_test.bat" %*
if errorlevel 1 exit /b 1
exit /b 0
