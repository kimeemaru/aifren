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

Reference-conditioned speech is a separate optional runtime. The adapter pins
official GPT-SoVITS source and verifies registered source/model digests before
starting its CPU worker. MIT source licensing does not by itself clear the model,
phonemizer, codec, CUDA or other transitive binaries for redistribution. No private
recording, transcript, conditioning cache or generated cloned speech is a package
input. See [the voice installation boundary](CHARACTER_VOICE.md).

## Optional expression runtime

The Cardiff expression model is a separately installed, pinned MIT-declared local
resource; source/configuration hashes are in automatic_expression.py. No weights,
converted graph, tokenizer cache or generated voice data is included. Preserve
model/card and runtime dependency notices for any later distribution. Existing
public binary resources are unchanged in this catch-up; no new avatar, motion pack,
font, background or recording is admitted.
