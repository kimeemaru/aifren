# Public distribution asset boundary

This is a source repository, not a cleared portable runtime or consumer release.
Only listed, already-published assets and the licensed subtitle font are bundled.
No locally supplied avatar, external VRMA or original motion trial is added.

| Input | Source/provenance | Public treatment |
| --- | --- | --- |
| `Assets/Resources/LocalCharacter/model.vrm` and importer metadata | Existing public VRoidPreset_A sample | Retained unchanged from the public tree, under the official sample conditions and existing notice. Build guard verifies exact bytes; never substitute a private model under this path. |
| `Assets/Resources/Presentation/Backgrounds/bedroom_day.png`, `bedroom_night.png` | Existing AIFren project-generated backgrounds | Retained under the AIFren Public Source License. |
| Presentation branding `logo.png` and static UI audio/BGM | Existing AIFren project-generated resources | Retained under the project license; these are product assets, not recorded/generated dialogue. |
| Presentation SVG icons | Game-icons.net | CC BY 3.0; retain the author/source table in THIRD_PARTY_NOTICES.md. |
| Liberation Sans and TMP font resources | Existing public font resources | SIL OFL 1.1; retain adjacent copyright/license. |
| `Assets/Resources/Subtitles/Nunito-SemiBold.ttf` | Unmodified Google Fonts static font | SIL OFL 1.1; complete notice in StreamingAssets/ThirdPartyNotices. Used only for hidden subtitles. |
| UniVRM/UniGLTF | Pinned v0.130.1 UPM references | MIT; retain upstream notices when redistributing resolved packages. |
| Unity UGUI/TMP/Vector Graphics/Mathematics/Test Framework | Pinned package references | Follow each package's included license and Unity distribution terms. |

Paths above are relative to `unity/AIFrenUnityPoc` unless indicated otherwise.
See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for upstream links and
attribution. A local build's use of an asset does not grant redistribution rights.

## Excluded

Characters, canonical conversations, summaries, learned memories, SQLite/runtime
stores, settings, credentials, prepared caches, model weights, recorded/reference
voices, generated speech, captures, diagnostic artifacts, builds and Editor caches
are excluded. So are local custom avatars/backgrounds, supplied motion files,
reference kits, obsolete indicator artwork and unused EmojiOne samples.

## Build and packaging

Public builds accept only the reviewed sample in the bundled-avatar directory and
reject local background additions or substituted resources. Import custom resources
for local use only; distribution requires a separate explicit review. Missing optional
animation files must not break ordinary procedural gesture behavior.
An included sample must import as a humanoid with renderable geometry before the
build can be published. Retrying an incomplete initial import does not replace or
modify the supplied model bytes.

The Linux and Windows package selectors use one explicit source allowlist and create
empty/generic runtime data. External runtime resources require separately reviewed
regular-file paths and digests; an allowlist is not a license grant. Do not package
a live environment or data directory wholesale. Portable Python/native/model/GPU
redistribution still needs a final version-specific SBOM/license review. No portable
package, public release or new tag is implied by this source sync.

The current Linux Development client has also been exercised from an extracted
candidate with bundled Python/models, network access restricted to loopback,
development checkouts/caches unavailable, and a spaces/Unicode installation path.
Closing and relocating that candidate preserved its synthetic history and saved
voice/presentation choices. This is runtime evidence, not redistribution clearance.
The pinned eSpeak data-path limit is handled with a bounded, process-owned temporary
copy of generic bundled phoneme resources; installed files and user data stay put.
Windows Unicode installation paths use that same copy before Kokoro imports its
phonemizer. The pinned native library needs an ASCII-compatible temporary path;
if Windows cannot supply one (including a short-path alias), startup reports the
limitation instead of falling back to the library builder's absolute path.

A Windows x86_64 client cross-build and a separately assembled Windows embedded
Python/backend candidate now exist. Native Windows CI checks the embedded runtime
imports, character locking and synthetic source-backed recall. This does not prove
native graphical/audio operation or GPU transcription. Those Windows gates remain
open; neither candidate is a cleared distribution or release.

## Current candidate contract

| Component | Linux candidate | Windows candidate |
| --- | --- | --- |
| Platform | x86_64, Ubuntu 24.04 / glibc 2.39 baseline | x86_64, Windows 10/11 target; headless checks on Windows Server 2022 |
| Client | Current Unity 2022.3 Development player, reviewed public sample/resources | Current cross-built Unity 2022.3 Development player, same public resources |
| Backend | Bundled CPython 3.12, explicit application source inventory, Linux native libraries | CPython 3.12.10 embedded distribution and genuine Windows wheels/native libraries; never a copied Linux environment |
| Local chat | Qwen3.5-4B Q4_K_M, bartowski conversion of stock Qwen weights; llama-cpp-python 0.3.35 | Same GGUF, pinned official llama-cpp-python 0.3.35 Windows CPU wheel; GPU chat acceleration is not claimed |
| Speech | Kokoro 0.9.4 / Kokoro-82M v1.0, `af_heart`, CPU | Same voice/model, CPU PyTorch 2.7.0 |
| Retrieval / input | MiniLM-L6-v2; faster-whisper-small and CTranslate2 | Same resources; Windows native hnswlib, CTranslate2 and NVIDIA libraries |
| Optional face proposal | Included pinned Cardiff ONNX resource; user-selectable | Same resource, CPU ONNX runtime |
| Voice cloning | Not bundled; separately installed/verified GPT-SoVITS runtime remains optional | No validated Windows cloning runtime pack included |

The declared local resources are present in staging; no first-launch download,
user Python, compiler or development checkout is intended. The existing GPU STT
startup requires a compatible NVIDIA GPU/driver even when the selected chat wheel
and Kokoro run on CPU. A GPU-free full application is not claimed. RAM/VRAM demand
includes the client, transcription and chat context, not just GGUF file size.

`run-aifren.sh --portable` or `Launch AIFren.cmd --portable` keeps writable state
in the extracted directory's `UserData`. Without `--portable`, the launcher uses
the platform's per-user application-data location. Resources remain package-local;
developer configuration/cache overrides are removed. Relocate only after closing.
The launcher validates client compatibility and retains exact owned child-process
handles; it never terminates an unrelated process occupying a port.

Windows dependency URLs and SHA-256 values are pinned in
[`scripts/windows_runtime_requirements.txt`](../scripts/windows_runtime_requirements.txt).
The public-code-only Windows workflow records interpreter/index digests, assembles
an isolated embedded runtime, and runs the synthetic checks. It uploads no binaries,
models, private voice inputs or application data. Package composition still uses
the explicit per-file manifest; successful imports do not certify every device.

### Distribution gates that remain open

Kokoro's Misaki fallback imports `phonemizer-fork` 3.3.2 (GPLv3+) and loads eSpeak NG
1.52.0 (GPLv3) in the application process. Compatibility with the current AIFren
license's no-sale restrictions has **not** been cleared. Adding a notice is not a
resolution, and removing the fallback can silently lose out-of-vocabulary words.
No license change or degraded speech fallback is implied. See the actual
[eSpeak terms](https://github.com/espeak-ng/espeak-ng/blob/1.52.0/COPYING) and
[application license](../LICENSE.md).

The candidate inventories record file digests, model revisions, wheel provenance
and retained notices. Native FFmpeg/codec, NVIDIA, libc/runtime and copyleft source
obligations still require completed version-specific review/materials before
redistribution. An upstream Python package's MIT/BSD label does not clear all of
its bundled binaries. Model-card declarations are recorded separately from code
licenses. Current candidates stay local and explicitly uncleared; no replacement
archive is promoted merely because runtime checks pass.

Reference-conditioned speech is a separate optional runtime. The adapter pins
official GPT-SoVITS source and verifies registered source/model digests before
starting its CPU worker. MIT source licensing does not by itself clear the model,
phonemizer, codec, CUDA or other transitive binaries for redistribution. No private
recording, transcript, conditioning cache or generated cloned speech is a package
input. See [the voice installation boundary](CHARACTER_VOICE.md).

## Optional expression runtime

The Cardiff expression model is a pinned MIT-declared optional local resource;
source/configuration hashes are in automatic_expression.py. Its weights and graph
are excluded from Git; the local candidate includes a separately inventoried graph
and tokenizer. Preserve model/card and runtime dependency notices for distribution. Existing
public binary resources are unchanged in this catch-up; no new avatar, motion pack,
font, background or recording is admitted.

## Stock NVIDIA tester delta

The Windows stock profile replaces the optional GPL pronunciation fallback,
excludes the unused compressed-media codecs, and uses pinned CUDA runtimes.
See [the profile and preparation boundary](WINDOWS_TESTER.md). Prior CPU candidate
clearance does not imply this changed subset is cleared; final component-specific
notices/source materials and file inventory accompany each tested artifact.
