# Unity companion client

Open this project in Unity 2022.3.62f3. The pinned UPM dependencies supply UniVRM,
UGUI, TextMeshPro, Vector Graphics and the test framework. `Assets/Scenes/AIFrenPoc.unity`
is the production scene. The Python backend runs separately over loopback WebSocket.

Current product surfaces include direct VRM, independent portrait/landscape framing,
speech-timed hidden subtitles and text color, paged History, the Memory Viewer/Editor,
Current Scene drawer and settings. Normal backend startup uses Memory V2; Unity does
not create an alternate memory authority or write canonical stores.

The already-published sample avatar remains under its upstream terms. No custom
local avatar, external VRMA or motion trial is included. Optional native VRMA inputs
can be selected locally; procedural semantic gestures remain available. Trial-only
controls require their optional local file and do not affect ordinary startup.
Nunito SemiBold applies only to hidden subtitles; its OFL notice ships with resources.

`HiddenSubtitlePresenter` is the sole page/mesh/opacity owner. Word timing, actual
visibility and fade progress remain separate; UI peek preserves progress and
committed Show cancels. PTT/speech always owns cancellation. Final-response face/body
requests retain capabilities, authored VRM overrides and generation guards.

Run EditMode with actual XML and build the normal Development target after changes.
Fixtures isolate application preferences before initialization; keep your normal
Editor HOME/licensing available. Do not require another developer's data, assets,
monitor or QA artifacts. See the [developer guide](../../docs/DEVELOPER_GUIDE.md)
for exact commands and [distribution manifest](../../docs/DISTRIBUTION_ASSET_MANIFEST.md)
for asset boundaries. Generated tests/builds/captures never belong in Git.
