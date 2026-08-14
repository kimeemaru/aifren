# AIFren

AIFren is a local-first, long-lived AI companion project. Its central goal is
continuity: reopening the app should return to the same character, accumulated
conversation history, and useful memories—not a disposable new chat.

It is currently an active Windows development project, not a finished consumer
release. The emphasis is a believable persistent character: responsive
conversation, consistent editable personality, careful long-term continuity,
and a simple companion presentation rather than a large game or an endlessly
autonomous streamer.

## What works today

- Persistent raw conversation archive, summaries, and long-term memory
- Separate, editable character configuration and base personality
- Gemini-powered responses with local embedding-based memory retrieval
- Kokoro local TTS using `af_heart` at normal speed, with Piper fallback
- Local-capable STT and push-to-talk: global F8 in the Tkinter client and
  window-focused, rebindable PTT in the Unity development client
- A loopback-only WebSocket backend for a separate frontend
- A Unity companion client with text chat, response reveal, history, settings,
  display controls, and an optional locally supplied VRM avatar

The Python backend remains authoritative for conversation, memory, personality,
turn ordering, speech, and stored data. Unity is a companion presentation, not
a second AI backend.

## Local-first, with current dependencies

AIFren keeps its canonical history, summaries, memories, and character files
locally. The current response provider is Gemini, so generating responses
currently needs a user-provided API key and network access. That cloud provider
is replaceable; it is not the source of character continuity.

Speech recognition and speech playback are designed to run locally. Kokoro is
the default local TTS provider and can use a compatible NVIDIA GPU when
available; Piper remains a dependable fallback. Installing runtime/model assets
is a one-time setup step. The project does not bundle personal voice references,
avatars, conversations, or API keys.

## Avatar and conversation presentation

The Unity client uses a simple companion layout rather than a game world. A
locally supplied VRM 1.0 avatar can be shown over a 2D background. The full
avatar is rendered with animation-safe padding, then the presentation crops that
render for portrait or landscape framing. This keeps the underlying body
available for future animation while allowing a closer visual composition.

Assistant text is revealed visually while the Python backend owns the complete
turn and plays TTS through the computer speakers. Unity does not receive a
streamed audio file, perform lip sync, or alter conversation/memory data.

## Privacy and continuity

The current character uses repository-relative local data:

- `conversation.json` — the complete canonical conversation archive
- `conversation_summary.json` — a derived summary for older context
- `memories.json` — durable long-term memory records
- `characters/default/` — explicit character configuration and base personality

Treat this data as personal and durable. Do not casually delete, relocate, or
commit it. Summaries, embeddings, and extracted memories are useful derived
data; they never replace the raw conversation archive. Local private assets,
build artefacts, virtual environments, logs, and local settings are kept out of
version control.

## Settings

The Unity development client includes display, model-status/API-key, audio,
dialogue, controls, appearance, and advanced settings. Backend-owned values are
sent through the existing local transport; Unity-only preferences are kept
locally. API keys are intentionally local, masked in the UI, and never bundled
into a shared build. Settings and framing controls are still under active
development—see the limitations below.

## Windows development launch

The reproducible development runtime uses Python 3.10. From the repository
root, create it once:

```bat
setup_aifren_runtime.bat
```

For the established local Unity workflow, run this once to create/update the
current-user Desktop shortcut:

```bat
scripts\install_aifren_dev_shortcut.bat
```

Open **AIFren Dev** and choose **Start Current Build** or **Rebuild + Start**.
The launcher verifies that the local backend is the expected one, checks its
loopback transport before starting Unity, and keeps developer output available.
It will not automatically terminate an unrelated program using the same port.

You can also run the existing frontends directly:

```bat
aifren.bat
```

```powershell
.\.venv-aifren\Scripts\python.exe backend_host.py
```

The latter starts the Unity-facing backend at `ws://127.0.0.1:8765`.

## Troubleshooting

- **The Unity client cannot connect:** start the backend through **AIFren Dev**
  or run `backend_host.py`; another application on port 8765 is intentionally
  not terminated automatically.
- **No generated response:** configure your own Gemini API key in the local
  Models settings or the supported development environment configuration.
- **Kokoro is unavailable:** run the Python 3.10 setup script; Piper should
  remain available as a fallback.
- **No avatar appears:** the optional, ignored local VRM asset has not been
  supplied or imported. Chat remains usable without it.
- **Unity batch build stops before compilation:** Unity LicensingClient can
  occasionally fail during startup. The developer build helper makes a bounded
  retry; ordinary compiler errors still need to be corrected normally.

## Current limitations

- The Unity client is a development companion, not a polished release.
- Avatar startup/reset framing, portrait default composition, and framing
  controls remain transitional.
- Low-resolution windowed scaling, top-control alignment, typography, and
  render-quality polish still need dedicated presentation work.
- Character Select, final Memory V2 authority, local LLM packaging, Linux, and
  SteamOS support are not complete.
- Unity has no final lip sync, movement, VR, game world, or streamed TTS audio.

## Roadmap

Near-term work focuses on memory quality and recovery, a stable companion
viewport, better animation/expression and eventual lip sync, deliberate
character identity boundaries, and a reproducible local-first runtime. Larger
world simulation, constant autonomous chatter, VR, and a game-like shell are
not the project direction.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md),
Copyright © 2026 kimeemaru. Under its terms, AIFren source may be used,
modified, forked, built, and redistributed free of charge. Free
AIFren-specific mods are permitted, and their creators may accept voluntary
donations. Monetized videos, livestreams, reviews, tutorials, and similar
content are also permitted.

Selling AIFren, paid forks, paid access, or paid AIFren-specific mods requires
separate permission from the copyright holder. The license applies only to
rights controlled by AIFren: third-party components and assets remain subject
to their own licenses. Any future portable friend-test package must include
`LICENSE.md` and the applicable separate third-party notices. See
[LICENSE.md](LICENSE.md) for the actual terms.

## Learn more

- [Project vision and roadmap](PROJECT.md)
- [Current architecture and durable data boundaries](ARCHITECTURE.md)
- [Unity companion notes](unity/AIFrenUnityPoc/README.md)
- [Distribution asset manifest](docs/DISTRIBUTION_ASSET_MANIFEST.md)
