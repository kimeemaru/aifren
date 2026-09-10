@echo off
setlocal
set "AIFREN_PACKAGE_ROOT=%~dp0"
for %%I in ("%AIFREN_PACKAGE_ROOT%.") do set "AIFREN_PACKAGE_ROOT=%%~fI"
set "PYTHONDONTWRITEBYTECODE=1"
set "AIFREN_PYTHON=%AIFREN_PACKAGE_ROOT%\runtime\python\python.exe"
if not exist "%AIFREN_PYTHON%" set "AIFREN_PYTHON=%AIFREN_PACKAGE_ROOT%\runtime\python\Scripts\python.exe"
if not exist "%AIFREN_PYTHON%" (
  echo The packaged AIFren Python runtime is unavailable.
  exit /b 1
)
"%AIFREN_PYTHON%" "%AIFREN_PACKAGE_ROOT%\runtime\app\scripts\launch_friend.py" --package-root "%AIFREN_PACKAGE_ROOT%"
exit /b %ERRORLEVEL%
