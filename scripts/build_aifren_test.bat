@echo off
setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
cd /d "%REPO%"

set "UNITY_EXE=%UNITY_EXE%"
if not defined UNITY_EXE set "UNITY_EXE=%ProgramFiles%\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe"
set "PROJECT=%CD%\unity\AIFrenUnityPoc"
set "PLAYER=%PROJECT%\Builds\Windows\AIFrenPoc.exe"
set "BUILD_LOG=%TEMP%\aifren-unity-build.log"
set "MAX_ATTEMPTS=2"

if not exist "%UNITY_EXE%" (
  echo Unity 2022.3.62f3 was not found.
  echo Set UNITY_EXE to your Unity.exe path and run this script again.
  if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
  exit /b 1
)

call :build_attempt 1
if errorlevel 1 exit /b 1
exit /b 0

:build_attempt
set "ATTEMPT=%~1"
echo.
echo Building AIFren Windows presentation player ^(attempt %ATTEMPT% of %MAX_ATTEMPTS%^)...
"%UNITY_EXE%" -batchmode -quit -projectPath "%PROJECT%" -executeMethod AIFren.UnityPoc.Editor.BuildAIFrenPoc.BuildWindows -logFile "%BUILD_LOG%"
set "BUILD_RESULT=%ERRORLEVEL%"

if "%BUILD_RESULT%"=="0" (
  if exist "%PLAYER%" (
    echo.
    echo Build succeeded. Standalone player is ready:
    echo %PLAYER%
    exit /b 0
  )
  echo Unity reported success, but the expected player was not created:
  echo %PLAYER%
  echo See %BUILD_LOG% for details.
  if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
  exit /b 1
)

rem A normal Unity compile failure also contains LicensingClient startup lines.
rem Always surface compiler/build diagnostics before considering a licensing retry.
findstr /I /C:"error CS" /C:"Scripts have compiler errors." /C:"Tundra build failed" "%BUILD_LOG%" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo AIFren standalone build reached project compilation, but has compiler errors.
  echo See the real build/compiler error in: %BUILD_LOG%
  if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
  exit /b 1
)

findstr /I /C:"return code 199" /C:"LicensingClient IPC timeout" /C:"Timed out while waiting for LicensingClient" "%BUILD_LOG%" >nul 2>&1
if errorlevel 1 (
  echo.
  echo AIFren standalone build failed with Unity exit code %BUILD_RESULT%.
  echo This was not recognized as a LicensingClient timeout, so it was not retried.
  echo See the real build/compiler error in: %BUILD_LOG%
  if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
  exit /b 1
)

echo.
echo Unity could not start its LicensingClient before project compilation ^(exit 199^).
echo This is an external Unity licensing IPC timeout, not an AIFren build error.
echo Build log: %BUILD_LOG%
if %ATTEMPT% LSS %MAX_ATTEMPTS% (
  echo Waiting 5 seconds, then retrying once...
  powershell -NoProfile -Command "Start-Sleep -Seconds 5"
  call :build_attempt 2
  if errorlevel 1 exit /b 1
  exit /b 0
)

echo Unity licensing did not recover after %MAX_ATTEMPTS% attempts.
echo Open Unity Hub or the Unity Editor once, confirm its license is active, then retry this script.
if not defined AIFREN_LAUNCHER_NONINTERACTIVE pause
exit /b 1
