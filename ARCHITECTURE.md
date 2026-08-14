# AIFren Architecture

For the current developer workflow, runtime sequencing, settings ownership,
avatar presentation pipeline, and Windows build notes, see
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md). This document remains the
concise architectural boundary and persistent-data reference.

## Scope and status

AIFren has two presentation clients over one Python backend:

```text
Tkinter GUI --in process--> AssistantService --> Conversation / Memory / LLM / Voice
Unity companion --local WebSocket--> backend_host.py --> AssistantService
                                                     --> same backend components
```

The backend remains authoritative for conversation, memory, personality, turn
serialization, TTS/STT, and persistent data. Unity is a desktop proof of
concept, not a replacement backend or game architecture.

## Ownership boundaries

- `assistant_service.py` is the frontend-neutral boundary. It owns turn
  serialization, persistence, response generation, memory processing, summary
  maintenance, TTS, and structured events.
- `gui.py` is the Tkinter frontend: rendering, controls, settings,
  memory-manager presentation, and Tkinter dispatch. It uses the service
  directly for text and legacy F8/PTT turns.
- `backend_host.py` adapts one service instance to one loopback-only WebSocket
  client. It binds only to `127.0.0.1` and has no Unity-specific logic.
- `unity/AIFrenUnityPoc` is a separate Unity 2022.3.62f3 client. It renders
  snapshots/events and submits commands; it never touches Python persistence.

## Conversation, memory, and personality

`conversation/conversation.py` owns canonical conversation messages, saved
summary, recent-message selection, and context assembly.
`Conversation.build_context(memory, user_message)` retrieves relevant memory,
adds the summary, and appends recent conversation including the new user turn.

`memory/memory.py` owns long-term memory JSON validation, atomic saves,
locking/mutations, derived embeddings/keywords, user-message-only extraction,
and retrieval. New/updated records have compatible provenance. Frontends must
not mutate memory lists directly.

`characters/default/character.json` and `personality.md` hold the active
character's explicit configuration and authoritative base personality. They
are separate from memory/history. Relationship state does not exist yet and
must remain separate from base identity when introduced.

## Persistent data

Current paths are relative to the application working directory. Do not move or
change them without a planned migration.

| File | Canonical role | Current format |
|---|---|---|
| `conversation.json` | Complete message history | JSON array of `{role, content, timestamp}` |
| `conversation_summary.json` | Compression of older context | JSON object with `summary` and `summarized_messages` |
| `memories.json` | Durable long-term memories | JSON array of memory records |
| `characters/default/character.json` | Character configuration | JSON object |
| `characters/default/personality.md` | Base identity/personality | Markdown |

Raw conversation is the durable archive. Summaries, embeddings, retrieval
metadata, and extracted memories are derived aids—not substitutes for it.
Memory writes use a same-directory temporary file, optional `.bak`, and
`os.replace`; corrupt JSON raises a clear `MemoryDataError`.

### Future character scoping

Current files describe one active character. Future multi-character work must
scope every canonical and derived record to one identity: raw history,
summaries, memories, relationship state, personality/configuration, avatar,
voice, and other character state. This is a requirement, not a current
file-layout decision or permission to migrate existing data.

## Service turn and event flow

```text
user text/accepted transcription
  -> AssistantService persists user message
  -> Conversation builds context; LLM generates reply
  -> service emits response and coordinates local TTS
  -> service persists assistant message, processes memory, updates summary
  -> service emits ready/error/status events
```

Events include `turn_started`, `status`, `conversation_message`,
`assistant_response`, `memory_updated`, `tts_state`, `voice_state`,
`voice_transcription`, `voice_event`, and `error`.

The existing backend F8 listener stops active speech, records/transcribes with
local STT, then submits nonempty text through the same turn path. The Unity
development client can also send window-focused, locally rebindable PTT press
and release events through the existing backend boundary; it is not a global
OS hotkey service.

## TTS/runtime boundary

`tts/` exposes provider-independent speak, stop, and volume controls. Kokoro
(`af_heart`, speed `1.0`, no pitch post-processing) is selected with Piper
fallback. The reproducible real runtime is `.venv-aifren`, created with Python
3.10 by `setup_aifren_runtime.bat`. The setup script installs PyTorch's
official CUDA 12.8 wheels before the remaining
`requirements-aifren-runtime.txt` dependencies. `KOKORO_DEVICE = "auto"`
selects CUDA when the installed runtime and host support it, otherwise CPU.
Chatterbox and MiniMax experiments are intentionally outside that runtime.

## Local WebSocket transport

Launch the Unity-facing host from the repository with:

```text
.venv-aifren\Scripts\python.exe backend_host.py
```

It listens at `ws://127.0.0.1:8765`. Commands are:

```json
{"command": "get_snapshot"}
{"command": "submit_text", "text": "Hello"}
{"command": "stop_tts"}
{"command": "set_tts_volume", "volume": 0.7}
```

Snapshots copy conversation, basic character identity, backend status, and TTS
volume. Events are forwarded as JSON. No mutable Conversation or Memory object
crosses the boundary. Host shutdown saves state and closes service/PTT/TTS/client
resources cleanly.

## Current Unity boundary

The Unity client provides a 2D companion presentation with optional local VRM,
snapshot/history display, text submission, backend state, TTS volume/stop,
word reveal, bounded scrollable dialogue, and secondary history. Local
VRM/background assets are Git-ignored and presentation-only.

Unity does not own persistence, LLM context, memory, STT/PTT, TTS audio data,
lip sync, or character configuration. Python TTS plays through computer
speakers.

## Future architecture guidance

- Existing-character lifecycle has no New Chat. Startup restores the last-used
  character or shows selection only when necessary.
- Memory should grow into layered canonical history, working context,
  durable/episodic/temporal memories, relationship state, summaries, and
  retrieval metadata. Retrieval must resist repetition through relevance,
  recency, importance, diversity, and suppression controls.
- Base personality is authoritative and highly persistent. Relationship and
  experience state may accumulate but remains corrigible and cannot redefine
  core identity autonomously.
- Future UI needs windowed/fullscreen landscape and deliberate portrait/TATE
  compositions, hide-UI, approachable Settings plus Advanced/debug tools, and
  secondary memory inspection/editing.
- Voice/PTT should become globally/configurably bindable where practical.
  Proactivity must be sparse, configurable, and quiet during sleep periods.
- Presentation priority is a polished avatar viewport, animation, expression,
  and automatic lip sync—not a game world, locomotion, or VR.

- Future Memory V2 preserves a conceptual type boundary between user-profile
  facts and character episodic/shared experiences. Both remain source-backed,
  character-scoped, temporally aware derived claims over canonical events.
- Future correction handling records evidence, status, and disputes rather than
  rewriting canonical history. User corrections are normally strong evidence;
  ambiguity and contradiction are handled conservatively.
- Derived embedding/index data records provider, model, dimensions,
  preprocessing/content fingerprints, and current/stale/failed state. It is
  rebuildable, never authoritative.
- Development-only Memory V2 shadow comparison can explicitly rebuild a
  Git-ignored V1-derived SQLite snapshot and emit `memory_shadow` diagnostic
  events. It never contributes to a prompt, response, V1 memory mutation, or
  canonical persistence; missing/stale/invalid snapshots are reported rather
  than silently used.
- Future retrieval uses bounded, typed, traceable context with abstention, a
  protected recent window, diversity/repetition suppression, and no
  assistant-generated text as evidence of user facts. Personal memory and
  future external-document RAG remain separate layers.
- Future archive browsers query and paginate; they never load a whole life.
- Local-first portability keeps canonical data independent of the checkout,
  virtual environment, model files, hardware, and operating system.

## Testing

```text
python -m unittest discover -s tests -v
python test_tts.py
python -m unittest discover -s tests -p test_websocket_transport.py -v
git diff --check
```

Unity has EditMode tests for protocol/configuration/reveal behavior. Run them
from Unity 2022.3.62f3 when the editor licensing service is available.
