@echo off
setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
cd /d "%REPO%"

set "MODE=landscape"
set "RESET_ARGUMENTS="
:parse_arguments
if "%~1"=="" goto arguments_done
if /I "%~1"=="portrait" set "MODE=portrait"
if /I "%~1"=="landscape" set "MODE=landscape"
if /I "%~1"=="reset-console" set "RESET_ARGUMENTS=%RESET_ARGUMENTS% -aifren-reset-console-unlock"
if /I "%~1"=="reset-ui" set "RESET_ARGUMENTS=%RESET_ARGUMENTS% -aifren-reset-ui"
shift
goto parse_arguments
:arguments_done
if not "%RESET_ARGUMENTS%"=="" echo Applying requested local presentation reset:%RESET_ARGUMENTS%
if /I "%MODE%"=="portrait" (
  set "WIDTH=900"
  set "HEIGHT=1600"
  if not defined AIFREN_PORTRAIT_MONITOR set "AIFREN_PORTRAIT_MONITOR=2"
  set "MONITOR=%AIFREN_PORTRAIT_MONITOR%"
) else (
  set "WIDTH=1920"
  set "HEIGHT=1080"
  if not defined AIFREN_LANDSCAPE_MONITOR set "AIFREN_LANDSCAPE_MONITOR=1"
  set "MONITOR=%AIFREN_LANDSCAPE_MONITOR%"
)
if not defined MONITOR set "MONITOR=1"

set "PLAYER=%CD%\unity\AIFrenUnityPoc\Builds\Windows\AIFrenPoc.exe"
if not exist "%PLAYER%" (
  call "%CD%\scripts\build_aifren_test.bat"
  if errorlevel 1 (
    echo The standalone player was not built. See the build output above.
    if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
    exit /b 1
  )
)

set "BACKEND_PY=%CD%\.venv-aifren\Scripts\python.exe"
if not defined AIFREN_OWNERSHIP_FILE set "AIFREN_OWNERSHIP_FILE=%TEMP%\aifren-dev-backend-%RANDOM%-%RANDOM%.pid"
set "OWNERSHIP_FILE=%AIFREN_OWNERSHIP_FILE%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\ensure_aifren_backend.ps1" -RepositoryRoot "%CD%" -PythonPath "%BACKEND_PY%" -OwnershipFile "%OWNERSHIP_FILE%"
if errorlevel 1 (
  echo AIFren backend was not started. Review the process details above.
    if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
  exit /b 1
)

set "PLAYER_LOG=%TEMP%\aifren-unity-player.log"
echo.
echo Launching AIFrenPoc.exe with: -screen-width %WIDTH% -screen-height %HEIGHT% -screen-fullscreen 1 -window-mode borderless -monitor %MONITOR%
echo Display diagnostics will be written to: %PLAYER_LOG%
"%PLAYER%" -screen-width %WIDTH% -screen-height %HEIGHT% -screen-fullscreen 1 -window-mode borderless -monitor %MONITOR% -display-diagnostics %RESET_ARGUMENTS% -logFile "%PLAYER_LOG%"
set "PLAYER_EXIT=%ERRORLEVEL%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\stop_aifren_backend.ps1" -RepositoryRoot "%CD%" -PythonPath "%BACKEND_PY%" -OwnershipFile "%OWNERSHIP_FILE%"
echo.
if /I "%MODE%"=="dev" (
  echo The private player exited. Its launcher-owned backend was stopped cleanly.
) else (
  echo AIFren player exited. Backend logs remain local and the launcher-owned backend was stopped.
)
endlocal
exit /b %PLAYER_EXIT%
