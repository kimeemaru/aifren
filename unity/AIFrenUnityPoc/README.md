# AIFren Unity Companion Prototype

This is the companion presentation client for AIFren's local loopback WebSocket
backend. It is a runtime-built desktop UI with a 2D background and VRM 1.0
character preview, not a game scene or full 3D environment.

## Requirements

- Unity 2022.3.62f3 LTS desktop editor.
- The repository's Python 3.10 runtime; see the root
  [developer guide](../../docs/DEVELOPER_GUIDE.md) for supported setup.
- UniVRM is pinned as reproducible UPM Git dependencies at `v0.130.1` in
  `Packages/manifest.json`, alongside its required Unity Mathematics dependency.
- Unity UGUI and TextMeshPro packages included with Unity 2022.3.

## Avatar and background assets

The public checkout includes the approved `VRoidPreset_A` sample model at:

```text
Assets/Resources/LocalCharacter/model.vrm
```

UniVRM imports this VRM 1.0 file in the editor. At runtime the client loads the
imported `Resources/LocalCharacter/model` GameObject; it does not read the raw
`.vrm` itself. `Resources/CharacterAvatarConfig.json` holds the resource path
and preview position, rotation, scale, and camera settings. A replacement model
may be committed or shared only when its source, license, and export permissions
allow it.

`Resources/CompanionPresentationConfig.json` configures presentation resources.
The current background is presentation-only and never changes character,
conversation, or memory data. The intended direction keeps separate portrait
and landscape backgrounds: solid white/light neutral for portrait and the
bundled bedroom for landscape. Light/Dark controls change UI only. Future work
may permit custom PNG backgrounds.

## Run in the Unity editor

1. From the repository root, start the backend with
   `.venv-aifren\\Scripts\\python.exe backend_host.py`.
2. Add and open `unity/AIFrenUnityPoc` in Unity Hub.
3. Open `Assets/Scenes/AIFrenPoc.unity` and press Play.
4. The client connects to `ws://127.0.0.1:8765`, requests a snapshot, and
   displays dialogue and secondary history.

Enter text and select **Send** (or press Enter). The user message appears after
the backend persists and emits its `conversation_message` event. The assistant
appears from `assistant_response`; text reveals word-by-word in the current
dialogue card and can be revealed immediately by clicking the card.

Settings include display, model/API-key status, audio, dialogue, controls,
appearance, and advanced controls. Backend-owned values use local WebSocket
commands; Unity-only preferences use `PlayerPrefs`. The client can show backend
provider status and accepts a locally stored, masked Gemini API key, but does
not implement unsupported runtime provider or model switching.

Window-focused, locally rebindable PTT sends press/release events through the
existing backend voice boundary. Python continues to own conversation
persistence, Memory V1, turn serialization, STT, and TTS playback. Unity owns
only presentation-side idle/reaction behavior and minimal mouth animation from
backend TTS duration/envelope events; final phoneme/viseme lip sync is future
work.

## Presentation status

The padded full-avatar RenderTexture and UI crop/pan/zoom framing are
**transitional**. Do not treat further UV-crop tweaks as final viewer
architecture. Preserve full-avatar capture while moving toward a stable
high-resolution RenderTexture in a presentation container with independent
portrait and landscape X/Y translation plus transform scale. A user
crop/framing editor is not planned. Typography, spacing, word reveal, and fade
polish remain active work.

## Memory authority

Memory V1 (`memories.json` through `memory/memory.py`) is authoritative.
Memory V2 schema, import, retrieval, and shadow/evaluation work are
experimental and non-authoritative. They must not replace V1 persistence or
become prompt input without an approved migration stage.

## License

The AIFren source is licensed under the root [LICENSE.md](../../LICENSE.md):
**AIFren Public Source License v1.0**, Copyright © 2026 kimeemaru. This does
not replace licenses for Unity, UniVRM, TextMeshPro, a VRM/avatar, background,
or other third-party component or asset. The bundled VRoidPreset_A remains
subject to its official sample-model conditions.
