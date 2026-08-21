# AIFren

AIFren is a local-first, long-lived AI companion project. Its central goal is continuity: reopening the app returns to the same character, durable conversation history, and useful memories rather than a disposable chat.

It currently consists of a Python application backend and a Unity companion presentation client. The project is an active development system, not a consumer release.

## What works today

- Canonical raw conversation archive, derived summaries, and authoritative Memory V1.
- Editable character configuration and base personality, separate from learned memory/history and visual avatar assets.
- Replaceable LLM/TTS/STT boundaries; the current response provider requires a user-supplied key, while Kokoro local TTS has a Piper fallback.
- Loopback WebSocket backend plus Tkinter and Unity presentation clients.
- Global/unfocused and focused PTT routed through the backend, with immediate interruption of active speech.
- Unity direct VRM rendering, full-screen backgrounds, Avatar View framing, managed model/background libraries, display controls, and companion-mode background execution.
- Optional floating hidden-UI subtitles with deterministic page ownership, plus typed dialogue emphasis/emote formatting without changing canonical assistant text.
- Fixed-height masked multiline chat input and a first semantic Humanoid gesture backbone.

## Architecture at a glance

```text
Tkinter GUI --in process--> AssistantService --> Conversation / Memory V1 / LLM / Voice
Unity companion --WebSocket--> backend_host.py --> same AssistantService

direct background -> direct-rendered VRM -> Screen Space Overlay UI
```

Python is authoritative for turns, persistence, Memory V1, STT, TTS playback, and PTT. Unity owns visual presentation and local presentation preferences; it never writes canonical conversation or memory directly.

The direct VRM path is the default because it keeps close views sharp. The old RenderTexture avatar path remains an internal rollback/debug option only. Portrait and landscape retain independent Avatar View/background state. Hiding UI does not resize or reposition the avatar viewport.

## Important invariants

- Raw conversation remains canonical. Summaries, embeddings, indexes, and derived memories never replace it.
- Character identity/personality/memory are distinct from reusable global avatar and background assets.
- A subsystem may delete only files it owns. Deleting an imported asset removes only AIFren's managed copy, never the original source file.
- PTT/audio lifecycle is authoritative. Subtitle timing and playback metadata are presentation-only and must never delay audio startup, interruption, cancellation, or backend readiness.
- Memory V1 is authoritative today. Memory V2 is experimental shadow/evaluation work until an explicitly approved promotion.

## Current focus

The immediate work is friend-build validation and final frontend QA, followed by
licensed animation-source evaluation/gesture polish, facial expressions and
response metadata, then Character Management. Hidden-subtitle correctness is
currently stable, although aesthetic timing may receive later polish. See
[PROJECT.md](PROJECT.md) for the roadmap, [ARCHITECTURE.md](ARCHITECTURE.md)
for ownership boundaries, and [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
for setup, build, and validation workflows.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md). Third-party components and assets retain their own licenses.
