#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python_executable="${AIFREN_PYTHON:-python3}"
runtime="$repository_root/.venv-aifren/bin/python"

required_packages=(
    python3.12-venv
    python3.12-dev
    build-essential
    libportaudio2
    portaudio19-dev
)
missing_packages=()
if command -v dpkg-query >/dev/null 2>&1; then
    for package in "${required_packages[@]}"; do
        if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" != "installed" ]]; then
            missing_packages+=("$package")
        fi
    done
    if (( ${#missing_packages[@]} > 0 )); then
        echo "Missing Ubuntu setup prerequisites: ${missing_packages[*]}" >&2
        echo "Install them with: sudo apt install ${missing_packages[*]}" >&2
        exit 1
    fi
else
    echo "Warning: dpkg-query is unavailable; cannot verify Ubuntu prerequisites: ${required_packages[*]}" >&2
fi

if ! command -v "$python_executable" >/dev/null 2>&1; then
    echo "Python executable was not found: $python_executable" >&2
    exit 1
fi

"$python_executable" - <<'PY'
import sys
if not ((3, 10) <= sys.version_info[:2] <= (3, 12)):
    raise SystemExit("AIFren requires Python 3.10 through 3.12.")
PY

if [[ ! -x "$runtime" ]]; then
    "$python_executable" -m venv "$repository_root/.venv-aifren"
fi

"$runtime" -m pip install --upgrade pip
"$runtime" -m pip install --upgrade \
    torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 \
    --index-url https://download.pytorch.org/whl/cu128
"$runtime" -m pip install -r "$repository_root/requirements-aifren-runtime.txt"

# The official llama-cpp-python CUDA wheels are produced with a host-specific
# CPU instruction baseline.  That can include AVX-512 even on a CUDA-capable
# machine whose CPU does not implement it, so build the pinned package from
# source with a portable CPU baseline instead.  The isolated CUDA toolkit is
# installed under the ignored AIFren venv: no system-wide toolkit or root access
# is required.  The chosen GPU architecture is detected at setup time, rather
# than hard-coded for one development card.
llama_package='llama-cpp-python[server]==0.3.35'
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
        echo "CUDA llama.cpp setup needs curl and tar to provision its isolated compiler." >&2
        exit 1
    fi
    "$runtime" -m pip install --upgrade \
        cmake==3.31.10 ninja==1.13.0 patchelf==0.19.1.0

    cuda_bootstrap_root="$repository_root/.venv-aifren/.llama-cuda-bootstrap"
    cuda_toolkit_root="$repository_root/.venv-aifren/.llama-cuda-toolkit"
    micromamba="$cuda_bootstrap_root/micromamba"
    mkdir -p "$cuda_bootstrap_root"
    if [[ ! -x "$micromamba" ]]; then
        cuda_bootstrap_archive="$(mktemp)"
        trap 'rm -f "$cuda_bootstrap_archive"' EXIT
        curl -fsSL --retry 3 -o "$cuda_bootstrap_archive" \
            https://micro.mamba.pm/api/micromamba/linux-64/latest
        tar -xjf "$cuda_bootstrap_archive" -C "$cuda_bootstrap_root" bin/micromamba
        mv "$cuda_bootstrap_root/bin/micromamba" "$micromamba"
        rmdir "$cuda_bootstrap_root/bin"
        rm -f "$cuda_bootstrap_archive"
        trap - EXIT
    fi
    if [[ ! -x "$cuda_toolkit_root/bin/nvcc" ]]; then
        echo "Provisioning isolated CUDA 12.8 compiler for llama.cpp..."
        MAMBA_ROOT_PREFIX="$cuda_bootstrap_root/mamba-root" "$micromamba" create -y \
            -p "$cuda_toolkit_root" -c 'nvidia/label/cuda-12.8.1' -c conda-forge \
            cuda-nvcc=12.8 libcublas-dev=12.8
    fi

    cuda_compute_capability="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n 1 | tr -d '[:space:].')"
    if [[ ! "$cuda_compute_capability" =~ ^[0-9]+$ ]]; then
        echo "Could not determine the NVIDIA CUDA compute capability." >&2
        exit 1
    fi
    echo "Building CUDA-enabled llama.cpp for compute capability $cuda_compute_capability..."
    CUDACXX="$cuda_toolkit_root/bin/nvcc" \
    CUDA_HOME="$cuda_toolkit_root" \
    CUDAToolkit_ROOT="$cuda_toolkit_root" \
    CMAKE_ARGS="-DGGML_CUDA=ON -DGGML_NATIVE=OFF -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF -DCMAKE_CUDA_ARCHITECTURES=$cuda_compute_capability" \
    CMAKE_BUILD_PARALLEL_LEVEL="${AIFREN_LLAMA_BUILD_JOBS:-4}" \
        "$runtime" -m pip install --force-reinstall --no-cache-dir --no-binary=llama-cpp-python "$llama_package"

    # Source builds retain an absolute build-toolkit RUNPATH.  Replace it with
    # a venv-relative location so AIFren continues to work after the checkout
    # is moved, without requiring a shell-level LD_LIBRARY_PATH workaround.
    llama_library_dir="$($runtime - <<'PY'
from pathlib import Path
import site
for root in site.getsitepackages():
    candidate = Path(root) / 'llama_cpp' / 'lib'
    if candidate.is_dir():
        print(candidate)
        break
else:
    raise SystemExit('llama_cpp shared-library directory was not installed')
PY
)"
    find "$llama_library_dir" -maxdepth 1 -type f -name '*.so*' -print0 | \
        xargs -0 -r -n1 "$repository_root/.venv-aifren/bin/patchelf" --set-rpath \
        '$ORIGIN:$ORIGIN/../../../../../.llama-cuda-toolkit/lib'
    if ! "$runtime" - <<'PY'
import llama_cpp
if not llama_cpp.llama_cpp.llama_supports_gpu_offload():
    raise SystemExit('llama.cpp GPU offload is unavailable after CUDA installation')
print('llama.cpp GPU offload: available')
PY
    then
        echo "CUDA-capable NVIDIA hardware was detected but llama.cpp GPU offload could not be verified." >&2
        exit 1
    fi
else
    echo "NVIDIA CUDA runtime unavailable; installing llama.cpp CPU fallback."
    "$runtime" -m pip install --upgrade "$llama_package"
    "$runtime" - <<'PY'
import llama_cpp
print('llama.cpp GPU offload: unavailable; CPU fallback')
PY
fi
"$runtime" -m tts.kokoro_assets --install \
    --model-dir "$repository_root/models/kokoro-82m" \
    --voice af_heart

if ! "$runtime" -c 'import en_core_web_sm' >/dev/null 2>&1; then
    "$runtime" -m pip install \
        'en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
fi

echo "AIFren Linux runtime is ready: $runtime"
