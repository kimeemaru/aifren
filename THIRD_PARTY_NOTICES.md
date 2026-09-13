# Third-party notices

This inventory is not a substitute for each component's license. The AIFren
Public Source License applies only to AIFren-owned material. Before a public
release or portable binary package, retain the applicable notices and verify
the exact versions and distribution terms below.

The exact current Unity presentation-asset and bundle decision record is
maintained in `docs/DISTRIBUTION_ASSET_MANIFEST.md`. An asset is not cleared
merely because it is present in a development checkout or has been used during
local testing.

## Release inventory

| Component / material | Current status | Public-source / binary guidance |
| --- | --- | --- |
| UniVRM / UniGLTF v0.130.1 | **Safe to publish; needs notice** | The pinned UPM packages are MIT-licensed. Preserve upstream MIT notices and confirm the complete v0.130.1 notice set when creating a public source tree or player. |
| Unity UGUI, TextMeshPro, Vector Graphics, Mathematics | **Needs notice / review** | These Unity packages use the Unity Companion License. Keep their license references and comply with Unity's current editor/player distribution terms. Do not treat them as covered by AIFren's license. |
| Liberation Sans font | **Safe to bundle; needs notice** | The tracked `LiberationSans - OFL.txt` is the required SIL OFL notice and must remain with the font. |
| EmojiOne TMP sample sprites | **Exclude (obsolete)** | The TMP sample/default sprite asset has no AIFren C# usage and is not needed for current text rendering. Exclude it from public and portable release trees. |
| Game-icons.net SVG icons | **Safe to publish; needs notice** | CC BY 3.0; retain the attribution table below. |
| Python packages / native audio dependencies | **Needs notice / review** | Source dependencies can be published as requirements, but build a version-locked SBOM/license inventory from the final runtime environment before distributing a portable runtime. |
| Kokoro inference package | **Safe to publish; needs notice** | The package/model code has an Apache-2.0 release path. This does not clear any separately downloaded model weight or voice asset for bundling. |
| faster-whisper, embedding, and other inference packages | **Needs notice / review** | `faster-whisper` is MIT-licensed; retain its notice. Verify every final package/native dependency in the runtime SBOM. |
| Kokoro, STT, embedding, and other downloaded models | **Needs review** | Model weights are not tracked. Verify each selected model's redistribution terms before bundling; do not infer them from the Python package license. |
| PyTorch/CUDA and NVIDIA runtime components | **Needs review** | A portable GPU runtime needs its own NVIDIA/PyTorch redistribution review and notices. |
| VRoidPreset_A default avatar | **Safe to publish and bundle; conditions apply** | The public tree retains `Assets/Resources/LocalCharacter/model.vrm` and its `.meta` file. The model remains under the official [VRoidPreset_A - Z conditions](https://vroid.pixiv.help/hc/en-us/articles/4402394424089-VRoidPreset-A-Z), not `LICENSE.md`. The conditions permit for-profit/non-profit use, application-avatar use, alteration, and redistribution without attribution. Do not distribute it as CC0 or sell the raw/unmodified sample model/VRM for a fee; comply with the remaining conditions. |
| AIFren heart logo, bedroom backgrounds, and static presentation audio | **AIFren project-generated assets** | `logo.png`, `bedroom_day.png`, `bedroom_night.png`, `ui_tap.wav`, `interrupt_cue.wav`, and `cozy_vn_piano_loop.ogg` were generated specifically for AIFren using ChatGPT. They are covered by the AIFren Public Source License, not an external asset-pack license. |
| Obsolete PTT indicator PNGs and EmojiOne TMP sample | **Exclude** | The current PTT control loads the attributed Game-icons `Presentation/Icons/microphone` SVG and recolors it by state. The standalone indicator PNGs are unused. EmojiOne is a TMP sample/default sprite asset with no AIFren C# usage and is not needed for current text rendering. Exclude these leftovers from public and portable release trees. |

Unity itself and a Unity player are not included in this source repository.
Any future Windows player distribution must comply with Unity's then-current
license and redistribution terms.

## Game-icons.net UI icons

The monochrome SVG source icons in
`unity/AIFrenUnityPoc/Assets/Resources/Presentation/Icons/` are used as
tintable presentation glyphs. They are distributed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), as identified by
Game-icons.net. Attribution is retained here and must accompany redistribution.

| File | Icon | Author | Source |
| --- | --- | --- | --- |
| `archive-register.svg` | Archive register | Delapouite | https://game-icons.net/1x1/delapouite/archive-register.html |
| `archive-research.svg` | Archive research | Delapouite | https://game-icons.net/1x1/delapouite/archive-research.html |
| `bookmark.svg` | Bookmark | Lorc | https://game-icons.net/1x1/lorc/bookmark.html |
| `check-mark.svg` | Check mark | Delapouite | https://game-icons.net/1x1/delapouite/check-mark.html |
| `confirmed.svg` | Confirmed | Delapouite | https://game-icons.net/1x1/delapouite/confirmed.html |
| `expand.svg` | Expand | Delapouite | https://game-icons.net/1x1/delapouite/expand.html |
| `hamburger-menu.svg` | Hamburger menu | Delapouite | https://game-icons.net/1x1/delapouite/hamburger-menu.html |
| `microphone.svg` | Microphone | Delapouite | https://game-icons.net/1x1/delapouite/microphone.html |
| `open-folder.svg` | Open folder | Delapouite | https://game-icons.net/1x1/delapouite/open-folder.html |
| `settings-knobs.svg` | Settings knobs | Delapouite | https://game-icons.net/1x1/delapouite/settings-knobs.html |
| `speaker-off.svg` | Speaker off | Delapouite | https://game-icons.net/1x1/delapouite/speaker-off.html |
| `speaker.svg` | Speaker | Delapouite | https://game-icons.net/1x1/delapouite/speaker.html |

The vector importer is Unity's `com.unity.vectorgraphics` package. The SVGs
remain monochrome source art; Unity UI applies foreground tint and provides all
interactive button/panel chrome.

## Nunito SemiBold (hidden subtitles)

The unmodified static font from [Google Fonts](https://github.com/google/fonts/blob/c7e2740188205a85323c7385547f6f59b4f2245a/ofl/nunito/Nunito-SemiBold.ttf)
is redistributed under SIL OFL 1.1. Copyright 2014 The Nunito Project Authors.
The complete notice is retained at
`unity/AIFrenUnityPoc/Assets/StreamingAssets/ThirdPartyNotices/Nunito-OFL.txt`.
The font keeps its own license; generated TMP resources do not change it.
No newly supplied avatar, external VRMA, motion-trial file or animation pack is
included in this source sync.

## Optional CPU expression classifier

The optional automatic-expression component uses
[Cardiff NLP twitter-roberta-base-emotion-latest, pinned revision](https://huggingface.co/cardiffnlp/twitter-roberta-base-emotion-latest/tree/415620c4fbc8bd82b82b9fd46642fcec6519d537).
Its [model card](https://huggingface.co/cardiffnlp/twitter-roberta-base-emotion-latest/blob/415620c4fbc8bd82b82b9fd46642fcec6519d537/README.md)
declares MIT. Credit Cardiff NLP and the upstream model authors; preserve the
model card and applicable MIT notices when distributing weights or derivatives.
Upstream benchmark claims are not AIFren deployment measurements.

`aifren/dialogue/automatic_expression.py` pins upstream safetensors/configuration/tokenizer and
verifies SHA-256 identities. `aifren/dialogue/expression_model_export.py` performs an explicit
local CPU conversion using built-in Transformers, ONNX export and ONNX Runtime
INT8 quantization. The source safetensors are 498,640,508 bytes. The derived graph
is 125,860,185 bytes; graph/tokenizer/configuration total 127,970,095 bytes.
None of these model files belong in Git. Runtime inference is offline/CPU only.
The tested converter uses ONNX 1.19.1 ([Apache-2.0](https://github.com/onnx/onnx/blob/v1.19.1/LICENSE));
retain its applicable notices as well as Torch/Transformers and runtime notices.

The previously evaluated
[SamLowe GoEmotions ONNX model](https://huggingface.co/SamLowe/roberta-base-go_emotions-onnx/tree/c4e1cea7f2827bc2db2f6a7b8ea4a35f28f3868d)
(MIT) informed the input/uncertainty tests but is not the selected runtime model.

The inspected environment uses these direct runtime packages:

| Package | Inspected version | License / distribution note |
| --- | --- | --- |
| ONNX Runtime | 1.29.0 | [MIT](https://github.com/microsoft/onnxruntime/blob/v1.29.0/LICENSE); retain Microsoft and bundled dependency notices from the distributed wheel. |
| Hugging Face Tokenizers | 0.22.2 | [Apache-2.0](https://github.com/huggingface/tokenizers/blob/v0.22.2/LICENSE); retain the license and applicable notices. |
| NumPy | 2.5.2 | The installed package declares `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`; preserve its complete license collection and any linked native-library notices. |

These version observations do not replace a final runtime SBOM. Model licensing
does not automatically clear every wheel, native library, or future model
revision for packaging. Preserve the upstream model card and applicable MIT
notice with a distributed classifier. The separate optional `gguf` 0.19.0 metadata
reader is MIT-licensed; it reads local chat-template metadata and is not part of
classifier inference.

## Natural companion design references

The milestone's original implementation was informed by the following pinned
source reviews:

- [SillyTavern `8172dcd`](https://github.com/SillyTavern/SillyTavern/tree/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8):
  [AGPLv3](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/LICENSE).
  Expression/speech responsibility separation informed the design. No
  implementation was copied or translated, and no AGPL source is incorporated by
  this milestone. This work does not change AIFren's license.
- [AIRI `3fcae72`](https://github.com/moeru-ai/airi/tree/3fcae726c566d9937672e81b9829e0b41c6252ed)
  and [the inspected fork `d499b0b`](https://github.com/dasilva333/airi/tree/d499b0b2d4c030ac94b19621c0ff203909d5a17a):
  [MIT, copyright Neko Ayaka](https://github.com/moeru-ai/airi/blob/3fcae726c566d9937672e81b9829e0b41c6252ed/LICENSE).
  Ordered speech segments, cancellation ownership, and separated context sources
  informed original AIFren changes. No source port is included. Any later source
  reuse must retain the applicable copyright and MIT permission notice.

The source-to-design mapping is recorded in
[Natural companion delivery](docs/NATURAL_COMPANION_DESIGN.md). Reference review is
not permission to bundle another project's models, voices, artwork, or avatars.
