#!/usr/bin/env python3
"""Apply two reviewed, hash-bound optional-dependency patches to clean staging.

Never call this on a developer environment. Kokoro remains Apache-2.0;
faster-whisper remains MIT. See docs/DISTRIBUTION_ASSET_MANIFEST.md for the
separate model/native obligations. Original and modified sources and notices
must accompany the staged runtime. No patch removes permitted spoken words.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PATCHES = {
    "kokoro/pipeline.py": "09da32aab781f7a163cbf9f6e379d53db40957cbbab50bc26a21c8440c0eabd6",
    "faster_whisper/audio.py": "60a1d8638f718cbf6d245aed3e5a5aa61c1f822a0b0fe9b48a7c928d47c23909",
}
METADATA_HASHES = {
    "kokoro-0.9.4.dist-info": "df4f6feeac0374af0e6fc7d3f1fb533614dd1dc0633ad1a47ca208e29c2ba391",
    "faster_whisper-1.2.1.dist-info": "b85622132a43897972924db98e024c0d40dcec19c577b1ee5640f1bc1b59aada",
}


# CUDA 12.8 Attachment A, https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html#attachment-a
# Literal grant list; unnamed runtime headers are not distribution inputs.
CUDA_RUNTIME_HEADERS = frozenset(('nvrtc.h', 'cuda_occupancy.h', 'cuda_fp16.h', 'cuda_fp16.hpp', 'cuda_bf16.h', 'cuda_bf16.hpp', 'cuda_fp8.h', 'cuda_fp8.hpp', 'cuda_fp6.h', 'cuda_fp6.hpp', 'cuda_fp4.h', 'cuda_fp4.hpp', 'crt/host_defines.h', 'cuComplex.h', 'cuda_awbarrier_helpers.h', 'cuda_awbarrier_primitives.h', 'cuda_awbarrier.h', 'cuda_pipeline_helpers.h', 'ccuda_pipeline_primitives.h', 'ccuda_pipeline.h', 'cuda_runtime_api.h', 'cuda.h', 'cuda/std/tuple', 'cuda/std/type_traits', 'cuda/std/utility', 'device_types.h', 'vector_functions.h', 'vector_types.h', 'cufile.h'))

def prune(site: Path):
    """Remove unused or unlicensed development files from owned stock staging."""
    import shutil
    for name in ("torch/include", "torch/share/cmake", "torch/test",
                 "cupy/_core/include/cupy/_cuda/cuda-11"):
        directory = site / name
        if directory.is_symlink():
            raise ValueError("Staged runtime directory must not be a link")
        if directory.exists():
            shutil.rmtree(directory)
    headers = site / "nvidia/cuda_runtime/include"
    if headers.exists():
        for path in headers.rglob("*"):
            if path.is_symlink():
                raise ValueError("Staged CUDA headers must not be links")
            if path.is_file() and path.relative_to(headers).as_posix() not in CUDA_RUNTIME_HEADERS:
                path.unlink()
    if (site / "nvidia/cuda_runtime").is_dir():
        # llama-cpp-python's Windows loader registers CUDA_PATH/lib even when
        # only runtime DLLs (bin) and NVRTC headers are needed, not import libs.
        directory = site / "nvidia/cuda_runtime/lib"
        directory.mkdir(exist_ok=True)
        (directory / "README.txt").write_text(
            "Runtime-only CUDA layout. DLLs are in ../bin; no import libraries or compiler are required.\n")
    audio = site / "_sounddevice_data/portaudio-binaries"
    for pattern in ("*asio.dll", "*32bit*"):
        for path in audio.glob(pattern):
            if path.is_symlink():
                raise ValueError("Staged audio libraries must not be links")
            path.unlink()


def kokoro_patch(source):
    source = source.replace("from misaki import en, espeak", "from misaki import en")
    source = source.replace("        device: Optional[str] = None\n",
                            "        device: Optional[str] = None,\n        en_fallback: Optional[Callable] = None\n")
    begin = source.index("            try:\n                fallback = espeak.EspeakFallback")
    end = source.index("        elif lang_code == 'j':", begin)
    source = source[:begin] + '''            if en_fallback is None:
                raise RuntimeError("This stock runtime requires its configured English pronunciation provider.")
            self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=en_fallback, unk='')
''' + source[end:]
    begin = source.index("            language = LANG_CODES[lang_code]")
    end = source.index("\n    def load_single_voice", begin)
    return source[:begin] + '''            raise RuntimeError("This stock runtime includes English voices only.")
''' + source[end:]


def whisper_patch(source):
    # NumPy PCM takes the upstream transcribe path unchanged. Compressed media
    # decoding is optional, absent in this bundle, and fails explicitly if used.
    source = source.replace("import av\n", "")
    source = source.replace("    resampler = av.audio.resampler.AudioResampler(",
        "    import av  # Optional external media decoding, not used by AIFren PCM input.\n"
        "    resampler = av.audio.resampler.AudioResampler(")
    source = source.replace("def _ignore_invalid_frames(frames):\n",
                            "def _ignore_invalid_frames(frames):\n    import av\n")
    source = source.replace("def _group_frames(frames, num_samples=None):\n",
                            "def _group_frames(frames, num_samples=None):\n    import av\n")
    return source


def prepare(site: Path, materials: Path):
    if site.is_symlink() or any(p.is_symlink() for p in site.parents):
        raise ValueError("Runtime staging may not follow symlinks")
    records = []
    for name, expected in PATCHES.items():
        target = site / name
        if target.is_symlink():
            raise ValueError("Runtime source may not be a symlink")
        original = target.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected:
            raise ValueError("Runtime source does not match its reviewed upstream version: " + name)
        updated = (kokoro_patch if name.startswith("kokoro/") else whisper_patch)(original.decode()).encode()
        compile(updated, name, "exec")
        records.append((target, original, updated, name, expected))
    for directory in ("kokoro-0.9.4.dist-info", "faster_whisper-1.2.1.dist-info"):
        name = directory + "/METADATA"
        target = site / name
        if target.is_symlink():
            raise ValueError("Runtime metadata may not be a symlink")
        original = target.read_bytes()
        if hashlib.sha256(original).hexdigest() != METADATA_HASHES[directory]:
            raise ValueError("Runtime metadata differs from the reviewed version")
        source = original.decode()
        if directory.startswith("kokoro"):
            if source.count("Requires-Dist: misaki[en]>=0.9.4") != 1:
                raise ValueError("Unexpected Kokoro dependency metadata")
            source = source.replace("Requires-Dist: misaki[en]>=0.9.4",
                "Requires-Dist: misaki>=0.9.4\nRequires-Dist: spacy\nRequires-Dist: num2words")
        else:
            if source.count("Requires-Dist: av>=11") != 1:
                raise ValueError("Unexpected Whisper dependency metadata")
            source = source.replace("Requires-Dist: av>=11",
                'Provides-Extra: media\nRequires-Dist: av>=11; extra == "media"')
            source = source.replace("Requires-Dist: onnxruntime<2,>=1.14",
                                    "Requires-Dist: onnxruntime-gpu<2,>=1.14")
        records.append((target, original, source.encode(), name,
                        hashlib.sha256(original).hexdigest()))
    # Verify both before changing either. Stage is disposable, never a live env.
    for target, original, updated, name, expected in records:
        for version, content in (("original", original), ("modified", updated)):
            copy = materials / version / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(content)
        target.write_bytes(updated)
    inventory = [{"path": name, "original_sha256": expected,
                  "modified_sha256": hashlib.sha256(updated).hexdigest()}
                 for _, _, updated, name, expected in records]
    (materials / "patches.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.site, args.materials)
    prune(args.site)
