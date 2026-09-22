# NVIDIA stock tester profile

The `nvidia-stock-v1` distribution profile targets Windows x86-64 and NVIDIA
CUDA. The profile version is unrelated to memory authority: Memory V2 remains
normal. It uses the included stock Qwen GGUF at 16,384 context capacity, the
unchanged 4,608-token working target, Kokoro's American English `af_heart`,
Whisper small, MiniLM and optional local expression inference. Cloning is not
included. Existing development settings and external resource routes are unchanged.

The Windows launcher explicitly uses bundle-local `UserData`. Keep that directory
when moving the closed application. No pip, compiler, Git or Editor is needed at
runtime. Startup/device errors preserve character data and management access.
GPU mode never retries inference on CPU. Hardware and native graphical/audio
acceptance must be reported separately from assembly and headless import tests.
See the validation matrix supplied with each particular artifact.

## Device contract

The deployment owner selects `AIFREN_INFERENCE_DEVICE=cuda`. Chat requires the
CUDA build and confirmed complete layer offload. Kokoro and MiniLM execute on
CUDA; Kokoro's small spaCy tagger uses CuPy. Whisper receives validated mono
16 kHz PCM16 samples and uses CUDA float16. Automatic expressions use the same
pinned original FP32 weights on CUDA, preserving confidence and lifetime rules;
the CPU quantized ONNX graph is not advertised as GPU execution. ONNX Runtime GPU
is included for dependency compatibility, not as proof that an active graph uses it.
Non-neural pronunciation rules, tokenization, database operations and audio I/O
remain host work. Compatibility CPU selection is explicit, not resource fallback.

## Speech distribution boundary

Kokoro and Misaki core/model licenses are distinct from their optional eSpeak /
phonemizer fallback. The stock English runtime replaces that fallback with the
permissively licensed CMU Flite letter-to-sound tables and an original bounded
interpreter. See [pronunciation provenance](ENGLISH_G2P_PROVENANCE.md). No word-count
heuristic or omission substitutes for an unknown word. Pronunciation remains
approximate, especially for names; phoneme coverage is not acoustic validation.

`prepare_stock_runtime.py` verifies exact upstream source and metadata digests,
patches only disposable staging, and retains original/modified bytes. It removes
Kokoro's eager optional eSpeak import and makes faster-whisper media decoding
optional. The recorder's PCM route needs no PyAV/FFmpeg. The bundle excludes
those unused components, ASIO and unlisted proprietary CUDA headers. The same
preparation runs in native Windows reconstruction; new digests describe modified
files rather than pretending they are unchanged wheel contents.

AIFren's license is unchanged. Keep component-specific NVIDIA, Intel, Microsoft,
model and other notices, plus applicable corresponding source. LGPL library
replacement/debugging rights must not inherit restrictions specific to vendor
binaries. No top-level license alone clears an entire runtime. Final selection,
notices, file digests, extracted-archive checks and platform evidence are required
before any tester handoff. Candidate archives stay local; this is not a release.
