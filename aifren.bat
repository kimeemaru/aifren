@echo off
cd /d "%~dp0"
if not exist ".venv-aifren\Scripts\python.exe" (
  echo AIFren's Python 3.10 runtime is missing.
  echo Run setup_aifren_runtime.bat once, then launch again.
  exit /b 1
)
.venv-aifren\Scripts\python.exe gui.py
