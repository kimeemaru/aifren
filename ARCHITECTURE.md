# AIFren architecture

## System boundary

```text
Tkinter GUI --in process--> AssistantService --> Conversation / Memory / LLM / Voice
Unity companion --local WebSocket--> backend_host.py --> same AssistantService
```

`AssistantService` is the frontend-neutral application boundary. It serializes turns, persists canonical messages, builds context, generates replies, coordinates TTS/PTT, processes Memory V1, maintains summaries, and emits events. `backend_host.py` adapts one service to one loopback frontend. Unity and Tkinter are presentation clients, not alternate backends.

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

## Presentation and asset safety

The normal viewer is direct background, direct VRM camera, then Screen Space Overlay UI. Avatar View maps independent orientation X/Y/scale to direct camera controls around a stable full-body baseline. The older RenderTexture route is rollback/debug-only. UI visibility never changes the avatar viewport. UniVRM handles VRM 1.0 and VRM 0.x; generic GLB is unsupported.

Managed libraries are content-addressed but ownership is kind-scoped. Cleanup canonicalizes every target, validates exact managed directories, and never follows metadata to external source files, recursively deletes directories, crosses symlink boundaries, or treats a similarly named root as containment. Bad records are repaired safely while external files remain untouched. This destructive-operation rule applies to future character, memory, import/export, and cache work.

## Audio and dialogue

TTS generation is both playback ID and cancellation token. PTT invalidates it before stopping the stream; synthesis checks the same token and cannot resurrect stale audio. Explicit interruption and natural completion are distinct: matching natural completion retires active playback state. Subtitle events consume this lifecycle but never own it.

Conversation remains canonical. Presentation parsing styles complete paired emotes in visible UI and removes them from speech/hidden subtitles. Hidden subtitle pages are laid out before reveal, and TMP mesh visibility preserves wrapping without inline reveal markup. Kokoro timing is optional validated metadata with immutable fallback timing.

## Transport and recovery

`backend_host.py` binds loopback port 8765 and forwards structured snapshots/events; mutable conversation/memory objects and canonical file internals never cross the WebSocket. Disconnect/reconnect UI is presentation only. The repository-owned backend lifecycle reuses a healthy owned process and refuses unrelated listeners.
