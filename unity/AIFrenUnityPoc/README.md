# AIFren Unity Companion Prototype

This is the first companion-client presentation for the local AIFren WebSocket
backend. It is a runtime-built desktop UI with a 2D background and an optional
VRM 1.0 character preview, not a game scene or full 3D environment.

## Requirements

- Unity 2022.3.62f3 LTS desktop editor.
- The repository's reproducible Python 3.10 runtime. Create it once from the
  repository root with `setup_aifren_runtime.bat`.
- UniVRM is pinned as reproducible UPM Git dependencies at `v0.130.1` when
  Unity resolves this project's `Packages/manifest.json`. The manifest also
  explicitly includes its required Unity Mathematics dependency.
- The project uses Unity UGUI and TextMeshPro packages included with Unity
  2022.3 for the companion UI.

## Local VRM asset

The user-created VRoid export is intentionally excluded from Git. Put it here
after cloning or opening the project:

```text
Assets/Resources/LocalCharacter/model.vrm
```

UniVRM imports this VRM 1.0 file in the Unity editor. At runtime the POC loads
the imported `Resources/LocalCharacter/model` GameObject; it does not read the
raw `.vrm` file itself. `Resources/CharacterAvatarConfig.json` holds the
reusable resource path and preview position, rotation, scale, and camera
settings. Change that path/configuration for a different testing character.

If the file is absent or fails to import, the client remains usable, shows a
clear status error, and continues to handle chat.

## Local background asset

The client ships with a neutral built-in gradient. To use your own background
without committing it, create this ignored directory and put an imported Sprite
there:

```text
Assets/Resources/LocalBackground/background.png
```

Set the texture's **Texture Type** to **Sprite (2D and UI)** in Unity. The
runtime reads `Resources/CompanionPresentationConfig.json`; change its
`backgroundResourcePath` if you use another local resource path. The background
is presentation-only and does not alter character, conversation, or memory
data.

## Run in the Unity editor

1. In the repository root, start the backend:
   `.\.venv-aifren\Scripts\python.exe backend_host.py`.
2. In Unity Hub, add and open this `unity/AIFrenUnityPoc` directory.
3. Open `Assets/Scenes/AIFrenPoc.unity` and press Play.
4. The companion client connects to `ws://127.0.0.1:8765`, requests a
   snapshot, and shows the current dialogue and secondary history panel.
5. If `model.vrm` is present at the path above, it is framed over the 2D
   background with a small presentation-only idle sway. The included preview
   camera/light position it in view and face it toward the camera.

Enter text and select **Send** (or press Enter). The user message is displayed
only after the backend persists and emits its `conversation_message` event. The
assistant is displayed from its `assistant_response` event, preventing the
later assistant conversation-persistence event from creating a duplicate
bubble. Assistant text reveals word-by-word in the current dialogue card;
click the card to reveal its full response immediately.

**Settings** groups display, model/API-key status, audio, dialogue, controls,
appearance, and advanced controls. Backend-owned values use the existing local
WebSocket commands; Unity-only preferences are stored with `PlayerPrefs`. The
current Models page can show backend provider status and accepts a locally
stored, masked Gemini API key, but it does not provide unsupported runtime
provider/model switching.

The Unity client also supports window-focused, locally rebindable PTT. Its
press/release events use the same backend voice boundary as other turns; it is
not a global operating-system hotkey service and Unity does not own the STT
engine or canonical conversation persistence.

The Python backend continues to own memory, conversation persistence, turn
serialization, STT, and TTS. Audio remains on the computer speakers; Unity does
not receive or play streamed audio data in this proof of concept.

The model's license and export/source permissions must be reviewed before any
VRM asset is committed or shared. Keeping user-created models in the ignored
local asset directory is the default for this POC.

## License and portable test packages

The AIFren source is licensed under the root [LICENSE.md](../../LICENSE.md):
**AIFren Public Source License v1.0**, Copyright © 2026 kimeemaru. This does
not replace licenses for Unity, UniVRM, TextMeshPro, a VRM/avatar, background,
or any other third-party component or asset. A future portable friend-test
package must include the root `LICENSE.md` and all applicable third-party
notices separately.

## Standalone developer test build

From the repository root, run `scripts\\build_aifren_test.bat` to create a safe ignored
development build at `unity/AIFrenUnityPoc/Builds/Windows/AIFrenPoc.exe`.
For the normal private local test player, double-click
`scripts\\build_aifren_private_test.bat`; it explicitly permits the ignored local VRM
and background only for that local build. `rebuild_and_run_aifren_test.bat`
performs that private build and launches the player only when it succeeds.
Then `scripts\\run_aifren_test.bat` starts the local backend if needed, waits for its
loopback WebSocket listener, opens a 1920x1080 borderless-fullscreen player on
the primary monitor, and leaves the titled **AIFren Backend** console
available for further tests. Press Ctrl+C in that console after testing to stop
the backend cleanly. Use `scripts\\run_aifren_test.bat portrait` for 900x1600
borderless-fullscreen presentation on monitor 2. Unity monitor indices are
one-based; set `AIFREN_LANDSCAPE_MONITOR` or `AIFREN_PORTRAIT_MONITOR` before
launching to override the development presets. In particular,
`AIFREN_PORTRAIT_MONITOR=1` explicitly selects Unity's primary monitor;
use `AIFREN_PORTRAIT_MONITOR=2` for Unity's second enumerated display. Windows
display labels are not Unity display indices. The launcher writes its resolved
command and runtime display enumeration to `%TEMP%\aifren-unity-player.log`.

Set `UNITY_EXE` before building if Unity is not installed at the standard Unity
Hub 2022.3.62f3 location. Builds remain local and Git-ignored; they do not
include repository conversation or memory data. To prevent a local VRM or
background from being embedded accidentally, the build also refuses ignored
`LocalCharacter`/`LocalBackground` assets by default. For a private local
avatar test only, use `scripts\\build_aifren_private_test.bat` (or set
`AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1` in the same Command Prompt before
running the build); do not distribute that player. The build helper retries the
known Unity LicensingClient startup timeout (exit 199) once after five seconds,
but does not retry actual compiler or build failures. It leaves its window open
with the relevant `%TEMP%\aifren-unity-build.log` path on failure.
