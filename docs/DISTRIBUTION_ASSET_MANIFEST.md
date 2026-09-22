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

## Windows NVIDIA tester contract

The `nvidia-stock-v1` package profile is a Windows x86-64 tester preview, not a
certified release. It combines the current public Unity Development client,
CPython 3.12.10, genuine Windows CUDA wheels and the reviewed sample resources.
The launcher explicitly selects package-local `UserData`; the developer application's
normal data route is unchanged. See [profile details](WINDOWS_TESTER.md).

Included resources are stock Qwen3.5-4B Q4_K_M (the reviewed bartowski conversion),
Kokoro-82M v1.0 / American-English `af_heart`, MiniLM-L6-v2, Whisper small and
optional Cardiff expression weights. The selected chat model retains 16,384 context
capacity and the normal responsive working target. Cloning/private voice references
are not bundled. No first-launch dependency or model download is intended.

CUDA inference is explicit. The CPU-only chat/Torch wheels from the earlier local
candidate are not tester inputs. The stock runtime removes the optional GPL
phonemizer/eSpeak dependency and unused PyAV/FFmpeg codecs through hash-bound
staging changes, with Flite rules preserving unknown-word pronunciation. This does
not change Kokoro's Apache license or AIFren's license. Earlier Linux candidates
still carry their old, uncleared speech closure and are not promoted by this work.

A new file inventory describes the actual redistributed subset, including modified
source/METADATA/RECORD bytes. Keep original and modified source, upstream notices,
LGPL/MPL corresponding source, selected NVIDIA header permissions and exact
vendor binary/version mappings. Component-only restrictions do not override open
library replacement/debugging rights. A top-level MIT/BSD/Apache label is never
blanket clearance for a wheel's native dependencies.

The Microsoft MSVC runtime is a separate distribution condition: its listed DLL
redistribution grant requires a valid Visual Studio license. Preparing a local
candidate or running CI does not establish the distributor's entitlement. Confirm
that right before calling an archive shareable; do not change AIFren's license as
a workaround. The pinned private DLL extraction never runs an installer or modifies
a tester's Windows installation.

The public Windows workflow checks the embedded interpreter, patched imports,
pronunciation and synthetic ownership/recall. The official CUDA llama library
imports the NVIDIA driver directly; absence of that driver on a headless runner
is an explicit hardware gate, not permission to replace it with the CPU wheel.
Windows GPU, desktop, playback and microphone acceptance remain separate from
these checks. Linux GPU component execution is useful evidence, not a Windows
certification. The supplied artifact matrix must retain failures and NOT RUN gates.

The full enabled stack exceeded available memory for Whisper transcription on an
8-GiB GPU with the rendered client and other models resident. Chat/speech/retrieval/
expressions executed on CUDA; transcription did not silently fall back. Do not
advertise that measurement as full-stack 8-GiB support or guarantee larger devices
without testing. No model/context substitution is made to conceal the limitation.

The existing Linux Development player and both normal launcher actions remain
supported. Older CPU distribution candidates remain local and separately labelled;
no package, release or binary upload is authorized merely by source publication.

## Optional expression runtime

The Cardiff expression model is a pinned MIT-declared optional local resource;
source/configuration hashes are in automatic_expression.py. Its weights and graph
are excluded from Git; the local candidates inventory their graph/tokenizer and, for CUDA, the pinned
original FP32 weights separately. Preserve model/card and runtime dependency notices for distribution. Existing
public binary resources are unchanged in this catch-up; no new avatar, motion pack,
font, background or recording is admitted.

## Stock NVIDIA tester delta

The Windows stock profile replaces the optional GPL pronunciation fallback,
excludes the unused compressed-media codecs, and uses pinned CUDA runtimes.
See [the profile and preparation boundary](WINDOWS_TESTER.md). Prior CPU candidate
clearance does not imply this changed subset is cleared; final component-specific
notices/source materials and file inventory accompany each tested artifact.
