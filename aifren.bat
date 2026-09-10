@echo off
cd /d "%~dp0"
set "PLAYER=unity\AIFrenUnityPoc\Builds\Windows\AIFrenPoc.exe"
if not exist "%PLAYER%" (
  echo The Unity AIFren player is not built: %PLAYER%
  echo Build the Unity player, then launch this Unity-only entry point.
  exit /b 1
)
start "AIFren" "%PLAYER%"
