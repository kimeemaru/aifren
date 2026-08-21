# Distribution asset manifest

This is a release-engineering inventory of presentation and runtime assets
referenced by the current Unity client. It records project evidence; it does not grant rights that the repository does not
already document. `SAFE_BOTH` means source-repository publication and portable
binary bundling are both supported by the cited license, subject to retaining
the stated notice.

## Actively referenced assets

| Component / exact path | Current use | Source / evidence | License | Attribution / modification | Public source? | Portable binary? | Classification / blocker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Assets/Resources/Presentation/Backgrounds/bedroom_day.png` | Light-theme fallback background | AIFren project-generated asset; the byte-identical `kit/` copy is reference/duplicate only | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Backgrounds/bedroom_night.png` | Dark-theme fallback background | AIFren project-generated asset; the byte-identical `kit/` copy is reference/duplicate only | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Audio/ui_tap.wav` | UI tap SFX | AIFren project-generated asset, loaded by `PresentationAudio` | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Audio/interrupt_cue.wav` | TTS-interrupt SFX | AIFren project-generated asset, loaded by `PresentationAudio` | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Audio/cozy_vn_piano_loop.ogg` | Optional presentation BGM | AIFren project-generated asset, loaded by `PresentationAudio` | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Branding/logo.png` | Official AIFren heart logo; planned EXE/startup mark | AIFren project-generated asset; the byte-identical `kit/` copy is reference/duplicate only | AIFren Public Source License v1.0 | No third-party attribution; modification/redistribution under the AIFren license | Yes | Yes | `SAFE_BOTH` |
| `Assets/Resources/Presentation/Icons/{archive-register,archive-research,bookmark,check-mark,confirmed,expand,hamburger-menu,microphone,open-folder,settings-knobs,speaker,speaker-off}.svg` | Toolbar, settings, status, and PTT glyphs | Game-icons.net author/source table in `THIRD_PARTY_NOTICES.md` | CC BY 3.0 | Attribution required; modification and commercial redistribution allowed under the license | Yes, with notice | Yes, with notice | `SAFE_BOTH` |
| `Assets/TextMesh Pro/Fonts/LiberationSans.ttf` and generated `LiberationSans SDF*` assets | Current default TMP dialogue/UI font | Adjacent `LiberationSans - OFL.txt` identifies copyright holders and SIL OFL 1.1 | SIL OFL 1.1 | Retain OFL/copyright notice; modification allowed subject to reserved-name terms | Yes, with notice | Yes, with notice | `SAFE_BOTH` |
| `Assets/TextMesh Pro/Sprites/EmojiOne.png`, `.json`, `EmojiOne Attribution.txt`, and `Resources/Sprite Assets/EmojiOne.asset` | TextMeshPro example/default sprite sample | It is a TMP sample. No AIFren C# code references it; current UI text does not use its sprite tags. TMP Settings points at it as the package default, but AIFren does not need it for its current text rendering | Unknown / not required | Do not retain or redistribute it in the release candidate | No | No | `OBSOLETE`; `EXCLUDE_FROM_PUBLIC`; `EXCLUDE_FROM_PORTABLE` |
| `Assets/Resources/LocalCharacter/model.vrm` and `.meta` | Current default avatar resource selected by `CharacterAvatarConfig.json`: VRoidPreset_A | Official [VRoidPreset_A - Z conditions](https://vroid.pixiv.help/hc/en-us/articles/4402394424089-VRoidPreset-A-Z); the public root includes this exact default asset and meta file | VRoidPreset_A - Z conditions of use, not `LICENSE.md` | No attribution required; alteration and redistribution are allowed subject to the conditions | Yes | Yes, as part of the application subject to the conditions | `SAFE_BOTH`; do not distribute under CC0 or sell the raw/unmodified sample VRM for a fee |
| `Assets/Resources/LocalBackground/background` (ignored; no tracked file) | Optional user-selected presentation background; the client falls back to the tracked bedroom images when absent | `.gitignore` excludes `Assets/Resources/LocalBackground/`; no redistributable background license is documented | PRIVATE / UNKNOWN | Explicit authorization required | No | No | `PRIVATE_ONLY`; never treat a local custom background as a package default |

The `kit/Backgrounds/` and `kit/Branding/` copies are project reference/
duplicate material, not third-party asset-pack evidence. The current build
does not directly load the tracked PTT indicator PNGs; it uses the CC-BY
Game-icons microphone SVG at `Presentation/Icons/microphone`, recolored by
runtime state. The retained unused PNG paths are
`Assets/Resources/Presentation/Indicators/{Light,Dark}/{ptt_idle,ptt_listening,ptt_processing}.png`.
They are `OBSOLETE` / `EXCLUDE_FROM_PUBLIC` / `EXCLUDE_FROM_PORTABLE`; no
provenance investigation is needed unless they are intentionally reintroduced.

`kit/Reference/yume_virtual_companion_ui_kit.png` is reference-only and is not
loaded by the player. It is also `UNKNOWN_NEEDS_REVIEW` and excluded from both
release forms.

## Non-asset output and local data

| Material | Distribution decision |
| --- | --- |
| Generated Kokoro/Piper speech, temporary WAVs, and TTS playback output | Never publish or bundle as test/history content. They are runtime output, not application assets. |
| Conversation, summary, Memory V1/V2 data, logs, API keys, local settings | Never publish or bundle in a fresh package. |
| Downloaded Kokoro/Piper/STT/embedding model weights and GPU/native runtime files | Not tracked. They require a version-specific distribution review and notices before portable bundling. |
| Unity caches, builds, virtual environments, and local avatar/background files | Excluded by policy and `.gitignore`; not public-source material. |

## Software and model distribution record

| Component | Evidence presently available | Public source | Portable binary | Status / required action |
| --- | --- | --- | --- | --- |
| UniVRM / UniGLTF v0.130.1 | Pinned UPM dependencies; MIT upstream licensing recorded in `THIRD_PARTY_NOTICES.md` | Yes, with notice | Yes, with upstream notices | `SAFE_BOTH` after preserving the exact upstream notice set |
| Unity UGUI, TextMeshPro, Vector Graphics, Mathematics, Test Framework | Pinned Unity packages; Unity Companion License applies | Yes as project/package references with notice | Review final Unity-player terms and notices | `SAFE_PUBLIC_SOURCE`; `UNKNOWN_NEEDS_REVIEW` for a final binary notice set |
| Kokoro Python package | Requirement pinned at `kokoro==0.9.4`; Apache-2.0 code path recorded in notices | Yes, with notice | Code/runtime bundle needs final SBOM check | `SAFE_PUBLIC_SOURCE`; `UNKNOWN_NEEDS_REVIEW` for weights/runtime bundle |
| Piper, faster-whisper, sentence-transformers, openai, sounddevice, pynput, websockets | Version pins in requirements; `faster-whisper` MIT recorded in notices | Yes as dependency manifests | Needs per-version SBOM, native library, and model review | `SAFE_PUBLIC_SOURCE`; `UNKNOWN_NEEDS_REVIEW` for binary bundle |
| PyTorch CUDA / NVIDIA runtime | Installed separately by setup; no redistributable runtime is tracked | Requirements/setup references only | No clearance recorded | `UNKNOWN_NEEDS_REVIEW`; portable-package blocker for GPU distribution |

## Release decisions

### Cleared for public repository

- AIFren-owned source and documentation already identified in
  `PUBLIC_RELEASE_CANDIDATE.md`.
- The 12 listed Game-icons SVGs, with the existing CC BY 3.0 attribution table.
- Liberation Sans and its existing SIL OFL 1.1 notice.
- Unity project/package references and dependency manifests, with their
  applicable notices retained.
- The AIFren project-generated heart logo, bedroom backgrounds, and three
  static presentation audio clips under `LICENSE.md`.

### Exclude from public repository

- All user data, generated speech/audio, logs, local settings, model weights,
  virtual environments, builds, caches, and custom local avatar/background
  files.
- `kit/` reference/raw material, the obsolete PTT indicator PNGs, and the
  obsolete EmojiOne sample.

### Blocks public repository

- No asset-provenance blocker remains. The public root must include
  VRoidPreset_A with its meta file and official conditions, plus the cleared
  AIFren-generated presentation assets and notices; obsolete sample assets are
  excluded.

### Cleared for portable bundle

- Game-icons SVGs with attribution.
- Liberation Sans with the SIL OFL notice.
- UniVRM/UniGLTF code only after preserving its upstream MIT notices.
- The AIFren project-generated heart logo, default bedroom backgrounds, and
  static presentation audio under `LICENSE.md`.
- VRoidPreset_A as the default avatar, subject to the official VRoid sample
  model conditions.

### Exclude from portable bundle

- Custom local avatar/background files, user data, secrets, generated TTS
  output, logs, local models, builds/caches, the obsolete EmojiOne sample,
  obsolete PTT indicator PNGs, and raw/reference `kit/` material.

### Blocks portable bundle

- Version-locked licensing/SBOM review for selected models, PyTorch/CUDA/NVIDIA
  runtime, STT runtime/model, and other native dependencies.
