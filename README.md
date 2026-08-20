# AIFren

AIFren is a local-first, long-lived AI companion project. Its central goal is
continuity: reopening the app should return to the same character, accumulated
conversation history, and useful memories—not a disposable new chat.

It is an active Windows development project, not a finished consumer release.
The emphasis is a believable persistent character: responsive conversation,
consistent editable personality, careful long-term continuity, and a companion
presentation rather than a large game or an endlessly autonomous streamer.

## What works today

- Persistent raw conversation archive, summaries, and long-term Memory V1
- Separate, editable character configuration and base personality
- Gemini-powered responses with local embedding-based memory retrieval
- Kokoro local TTS using `af_heart` at normal speed, with Piper fallback
- Local-capable STT and push-to-talk: global F8 in Tkinter and window-focused,
  rebindable PTT in the Unity client
- A loopback-only WebSocket backend for a separate frontend
- A Unity companion client with text chat, response reveal, history, settings,
  display controls, an optional VRM avatar, and minimal presentation-side
  idle/reaction and mouth animation

The Python backend remains authoritative for conversation, Memory V1,
personality, turn ordering, speech, and stored data. Unity is a companion
presentation, not a second AI backend. Memory V2 is experimental shadow and
evaluation work only; it is not authoritative or prompt input.

## Local-first, with current dependencies

AIFren keeps canonical history, summaries, memories, and character files
locally. The current response provider is Gemini, so generating responses
needs a user-provided API key and network access. That provider is replaceable;
it is not the source of character continuity.

Speech recognition and playback are designed to run locally. Kokoro is the
default local TTS provider and can use a compatible NVIDIA GPU when available;
Piper remains a fallback. The project does not bundle personal voice references,
conversations, or API keys.

## Avatar and dialogue presentation

The current priority is a visual avatar-viewer and dialogue-presentation remake.
The existing UV crop/pan/zoom framing is transitional. The intended direction
preserves a full-avatar capture and presents it through a stable,
high-resolution RenderTexture in a container with X/Y translation and transform
scale, with independent portrait and landscape presentation state.

Light/Dark controls affect UI only; background choice is independent of theme.
Portrait and landscape retain separate backgrounds. The intended portrait
default is solid white/light neutral and the intended landscape default is the
bundled bedroom. Future work may support custom PNG backgrounds, but not a user
crop/framing editor.

Assistant text is revealed visually while Python owns the complete turn and
plays TTS through the computer speakers. Unity receives no streamed audio and
does not alter conversation or memory data. Current mouth motion is minimal and
envelope-driven; final phoneme/viseme lip sync remains future work. Typography,
spacing, and reveal/fade polish are active presentation goals.

## Privacy and continuity

The current character uses repository-relative local data:

- `conversation.json` — complete canonical conversation archive
- `conversation_summary.json` — derived summary for older context
- `memories.json` — durable long-term Memory V1 records
- `characters/default/` — explicit character configuration and base personality

Treat this data as personal and durable. Do not casually delete, relocate, or
commit it. Summaries, embeddings, and extracted memories are useful derived
data; they never replace the raw conversation archive.

## Development

See the [developer guide](docs/DEVELOPER_GUIDE.md) for supported contributor
workflow, validation, and the local WebSocket transport. The Unity companion
notes cover its editor setup and presentation boundaries.

## Current limitations and roadmap

The Unity client is a development companion, not a polished release. Framing,
low-resolution windowed scaling, top-control alignment, typography, and render
quality remain active presentation work. Character Select, final Memory V2
authority, local LLM packaging, Linux, and SteamOS support are incomplete.

Near-term work focuses on the avatar viewer and dialogue presentation:
VRoid Hub-level sharpness, stable high-resolution full-avatar capture, and
independent portrait/landscape state. Portable friend packaging,
multi-character work, deeper animation, and Linux packaging are deferred unless
separately approved. A game-like shell, constant autonomous chatter, VR, and a
large world simulation are not the project direction.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md),
Copyright © 2026 kimeemaru. The license applies only to rights controlled by
AIFren; third-party components and assets retain their own licenses. See
[LICENSE.md](LICENSE.md) for the terms.
## Learn more

- [Project vision and roadmap](PROJECT.md)
- [Current architecture and durable data boundaries](ARCHITECTURE.md)
- [Developer guide](docs/DEVELOPER_GUIDE.md)
- [Unity companion notes](unity/AIFrenUnityPoc/README.md)
