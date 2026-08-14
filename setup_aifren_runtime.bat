@echo off
setlocal
cd /d "%~dp0"

set "AIFREN_PYTHON=py"
set "AIFREN_PYTHON_ARGS=-3.10"
"%AIFREN_PYTHON%" %AIFREN_PYTHON_ARGS% -c "import sys" >nul 2>&1
if errorlevel 1 (
  if not exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    echo Python 3.10 was not found through the Windows Python Launcher or the standard per-user install path.
    echo Install Python 3.10, then run this script again.
    exit /b 1
  )
  set "AIFREN_PYTHON=%LocalAppData%\Programs\Python\Python310\python.exe"
  set "AIFREN_PYTHON_ARGS="
)

if not exist ".venv-aifren\Scripts\python.exe" (
  "%AIFREN_PYTHON%" %AIFREN_PYTHON_ARGS% -m venv .venv-aifren
)

.venv-aifren\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv-aifren\Scripts\python.exe -m pip install --upgrade torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 exit /b 1
.venv-aifren\Scripts\python.exe -m pip install -r requirements-aifren-runtime.txt
if errorlevel 1 exit /b 1
.venv-aifren\Scripts\python.exe -m tts.kokoro_assets --install --model-dir models\kokoro-82m --voice af_heart
if errorlevel 1 exit /b 1
.venv-aifren\Scripts\python.exe -c "import en_core_web_sm" >nul 2>&1
if errorlevel 1 (
  .venv-aifren\Scripts\python.exe -m pip install "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
  if errorlevel 1 exit /b 1
)
echo.
echo AIFren runtime is ready with the official CUDA-enabled PyTorch runtime. Launch with aifren.bat.
