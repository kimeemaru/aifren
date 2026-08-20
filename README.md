# AIFren

AIFren is a local-first, long-lived AI companion project. Its goal is continuity: reopening the application should return to the same character, durable conversation history, and useful memories rather than a disposable chat.

## What works today

- Canonical raw conversation, derived summaries, and authoritative Memory V1.
- Editable character configuration/base personality, separate from learned memory/history and visual assets.
- Replaceable LLM/TTS/STT/embedding boundaries; loopback Python backend with Tkinter and Unity presentation clients.
- Backend-owned PTT/TTS interruption lifecycle.
- Unity direct VRM rendering, Avatar View, managed model/background libraries, display controls, and companion-mode execution.
- Optional floating hidden-UI subtitles and presentation-only emote formatting.

```text
Tkinter GUI --in process--> AssistantService --> Conversation / Memory V1 / LLM / Voice
Unity companion --WebSocket--> backend_host.py --> same AssistantService

direct background -> direct-rendered VRM -> Screen Space Overlay UI
```

Python owns canonical data, turns, STT, TTS playback, and PTT. Unity owns presentation/local preferences only. Direct VRM rendering is default; the older RenderTexture path is rollback/debug-only. Portrait and landscape retain independent presentation/background state, and UI hide/show never changes the avatar viewport.

## Continue from here

Read [PROJECT.md](PROJECT.md) for direction and roadmap, [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries, and [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) for build/test workflow. The immediate work is hidden-subtitle final QA and broad frontend QA, followed by expressions/basic animation and Character Management.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md). Third-party components and assets retain their own licenses.
