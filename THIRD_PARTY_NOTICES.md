# Third-party notices

This inventory is not a substitute for each component's license. The AIFren
Public Source License applies only to AIFren-owned material. Before a public
release or portable binary package, retain the applicable notices and verify
the exact versions and distribution terms below.

The exact current Unity presentation-asset and bundle decision record is
maintained in `docs/DISTRIBUTION_ASSET_MANIFEST.md`. An asset is not cleared
merely because it is present in the private repository or has been used during
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
| Piper, faster-whisper, embedding, and other inference packages | **Needs notice / review** | `faster-whisper` is MIT-licensed; retain its notice. Verify every final package/native dependency in the runtime SBOM. |
| Kokoro, Piper, STT, embedding, and other downloaded models | **Needs review** | Model weights are not tracked. Verify each selected model's redistribution terms before bundling; do not infer them from the Python package license. |
| PyTorch/CUDA and NVIDIA runtime components | **Needs review** | A portable GPU runtime needs its own NVIDIA/PyTorch redistribution review and notices. |
| VRoidPreset_A default avatar | **Safe to publish and bundle; conditions apply** | The clean public root will include `Assets/Resources/LocalCharacter/model.vrm` and its `.meta` file. The model remains under the official [VRoidPreset_A - Z conditions](https://vroid.pixiv.help/hc/en-us/articles/4402394424089-VRoidPreset-A-Z), not `LICENSE.md`. The conditions permit for-profit/non-profit use, application-avatar use, alteration, and redistribution without attribution. Do not distribute it as CC0 or sell the raw/unmodified sample model/VRM for a fee; comply with the remaining conditions. |
| AIFren heart logo, bedroom backgrounds, and static presentation audio | **AIFren project-generated assets** | `logo.png`, `bedroom_day.png`, `bedroom_night.png`, `ui_tap.wav`, `interrupt_cue.wav`, and `cozy_vn_piano_loop.ogg` were generated specifically for AIFren using ChatGPT. Their `kit/` copies are duplicates/reference material. They are covered by the AIFren Public Source License, not an external asset-pack license. |
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
