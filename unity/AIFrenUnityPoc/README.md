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

## Current companion and character controls

The normal Development target is `Builds/LinuxDevelopment/AIFrenPoc.x86_64`.
Settings use the existing Save/Cancel owner: Dialogue > Natural / Roleplay,
Audio > Responsive speech, Appearance > ACT preview and Automatic expressions.
Roleplay and responsive speech are the defaults; both expression options are off.
Automatic expressions are temporary leased overlays; explicit/manual state and
procedural facial channels are preserved when they expire.

Character > Manage exposes confirmed storage migration/cleanup, Reset and Delete,
folder opening and readable identity/status. Scene/Viewer controls and asynchronous
loads use character/session generations. Framing is character/asset/orientation
specific and restored after the matching avatar-ready event. Hidden UI overlays
framing without moving the avatar. The intermittent blank History/Memory-panel report
remains unresolved; tests must keep delayed-event fences, not force-accept stale data.

Responsive speech keeps one utterance and continuing subtitle timing. Full speech
projection precedes chunking: emphasis stays spoken and outer actions own nested
formatting. ACT syntax is excluded from actual control output before publication.
Refer to [architecture](../../ARCHITECTURE.md) and
[delivery design](../../docs/NATURAL_COMPANION_DESIGN.md) for ownership and limits.
