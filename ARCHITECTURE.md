# AIFren architecture

## System boundary

```text
Tkinter GUI --in process--> AssistantService --> Conversation / Memory / LLM / Voice
Unity companion --local WebSocket--> backend_host.py --> same AssistantService
```

`AssistantService` is the frontend-neutral boundary. It serializes turns,
persists canonical messages, builds context, generates replies, coordinates
TTS/PTT, processes Memory V1, maintains summaries, and emits events.
`backend_host.py` adapts one service to one loopback frontend; Unity and Tkinter
are presentation clients, not alternate backends.

## Ownership

| State | Owner | Notes |
|---|---|---|
| Raw archive/summary | `Conversation` / JSON | Raw archive is canonical. |
| Memory V1 | `Memory` / `memories.json` | Use its mutation API only. |
| Memory V2 | Shadow/evaluation infrastructure | Non-authoritative, not prompt input. |
| Character identity/personality | character data | Separate from memory and visual assets. |
| Turn, STT, TTS, PTT | Python backend | Frontends request/display supported state. |
| Display/theme/avatar UI | Unity local preferences | Presentation-only. |
| Managed avatar/backgrounds | Unity asset library | Reusable visual assets. |

## Presentation

The normal path is direct viewer rendering: background, direct VRM camera, then
Screen Space Overlay UI. Avatar View maps independent orientation X/Y/scale to
direct camera controls around a stable full-body baseline. The old RenderTexture
path is rollback/debug-only. UI visibility is overlay-only and never changes
the avatar viewport, saved framing, camera fit, or background cover behavior.

UniVRM loads VRM 1.0 and VRM 0.x through the avatar-loading layer. Generic GLB
is not supported. Model swaps preserve visual viewer state and do not alter
character data.

## Managed asset safety

Managed libraries are content-addressed but ownership stays kind-scoped.
Filesystem cleanup canonicalizes and validates each target against exact managed
directories. It never follows metadata to an external source, recursively
deletes a directory, crosses a symlink boundary, or treats a similarly named
root as containment. Bad records are repaired safely; external files remain
untouched. This project-wide destructive-operation rule applies to future
character, memory, personality, import/export, and cache work too.

## Audio and dialogue

TTS generation is both playback ID and cancellation token. PTT invalidates it
before stopping the stream; synthesis checks the same token and cannot
resurrect stale audio. Explicit interruption and natural completion are distinct:
only matching natural completion retires active playback state. Subtitle events
consume this lifecycle but never own it.

Conversation remains raw/canonical. Presentation parsing styles complete paired
emotes in visible UI and removes them from speech/hidden subtitles. Hidden
subtitles are separate overlays. `HiddenSubtitlePresenter` is their single
presentation owner: it keeps timing-due words separate from visibly-presented
words, owns renderability/alpha/page swaps, and requires contiguous exact
global word ownership across page text and ranges. Pagination advances by the
actual adjusted page count, preventing gaps and overlap. Complete pages are
laid out before reveal; TMP mesh visibility preserves wrapping without inline
reveal markup. Temporary edge peek suppresses only rendering; committed Show
cancels the session. Kokoro timing is optional validated metadata with
immutable fallback timing.

Dialogue parsing produces typed `PlainText`, `Emphasis`, and `Emote` spans
without changing canonical assistant text. A paired single-asterisk span is an
emote when it is a known action/stage direction or has four or more normalized
words; otherwise it is spoken italic emphasis. A paired double-asterisk span
is always emphasis. Unity and the backend TTS cleaner intentionally mirror
this interpretation. Emotes remain available to `AvatarGestureMapper`, which
requests semantic `AvatarGestureIntent` values from `AvatarAnimationController`
through standard Humanoid mappings; blink and lip-sync stay separate.

The chat field is a fixed-height multiline `TMP_InputField` with a masked Text
Area. Text is vertically centered while it fits and switches to top-aligned
internal scrolling only when TMP's preferred height exceeds the viewport.

## Local transport and recovery

`backend_host.py` binds loopback port 8765 and forwards structured snapshots and
events; mutable conversation/memory objects and canonical file internals never
cross the WebSocket. Disconnect/reconnect UI is presentation only. Linux backend
recovery reuses a healthy checkout-owned backend and refuses unrelated listeners.
