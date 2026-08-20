# AIFren contributor guide

Read [PROJECT.md](../PROJECT.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and
[AGENTS.md](../AGENTS.md) before changing code. This guide describes current
ownership and the supported contributor workflow; it is not a specification for
unapproved future work.

## Current architecture

```text
Tkinter GUI --in process-->
                           AssistantService -> Conversation / Memory V1 / LLM
Unity client --WebSocket--> backend_host.py -> STT / TTS / persistent files
```

`AssistantService` is the frontend-neutral turn boundary. It serializes turns,
persists messages, calls `Conversation.build_context(...)`, generates replies,
coordinates TTS, processes Memory V1, maintains summaries, and emits events.
Tkinter uses it in process. Unity connects through the loopback-only
`backend_host.py` WebSocket adapter, which owns one service instance.

Gemini is the current response provider and requires a configured API key and
network access for responses. STT and TTS are local-capable. Kokoro is the
selected local TTS provider and Piper is its fallback.

## Repository map

| Path | Purpose |
|---|---|
| `assistant_service.py` | Frontend-independent application/turn boundary. |
| `backend_host.py` | One-client, loopback-only WebSocket adapter. |
| `gui.py` | Existing Tkinter frontend and legacy global F8 PTT. |
| `conversation/` | Canonical archive access, summaries, recent context, and prompt construction. |
| `memory/` | Authoritative Memory V1 records, validation, mutation, retrieval, and atomic persistence. |
| `memory_v2_*`, `memory_v2_store/` | Experimental, non-authoritative shadow/evaluation infrastructure. |
| `llm/`, `stt/`, `tts/`, `voice/` | Replaceable provider/integration boundaries. |
| `characters/default/` | Explicit character configuration and base personality. |
| `unity/AIFrenUnityPoc/` | Unity 2022.3.62f3 companion client and EditMode tests. |
| `tests/` | Python unit and transport tests. |

## Ownership and data safety

| State | Authoritative owner | Notes |
|---|---|---|
| Raw conversation archive and summary | `Conversation` / JSON files | Raw history is canonical; the summary is derived context. |
| Long-term memory | `Memory` / `memories.json` | Use the mutation API; frontend code must not edit memory records directly. |
| Memory V2 shadow data | Experimental SQLite/evaluation lane | Non-authoritative; it is not prompt or canonical persistence input. |
| Character identity/base personality | `characters/default/` files | Separate from learned memory/history. |
| LLM, TTS, STT, and PTT lifecycle | Python backend | Unity renders state and sends supported commands only. |
| Idle/reaction and minimal mouth animation | Unity presentation client | Presentation-only; driven by backend TTS duration/envelope events. |
| Display/theme/local presentation preferences | Unity `PlayerPrefs` | Local presentation state only. |

Do not casually relocate or rewrite `conversation.json`,
`conversation_summary.json`, `memories.json`, or character configuration. Raw
history is the durable archive; summaries, embeddings, retrieval metadata, and
extracted memories are derived aids rather than replacements.

## Local transport

Start the Unity-facing host from the repository root:

```text
.venv-aifren\Scripts\python.exe backend_host.py
```

It listens only at `ws://127.0.0.1:8765`. Core commands are `get_snapshot`,
`submit_text`, `stop_tts`, and `set_tts_volume`. The Unity client also uses its
supported window-focused PTT and settings commands. The adapter must not expose
mutable Python objects, secrets, or canonical file internals.

## Unity presentation status

The Unity client is a companion presentation, not a second backend. It owns
visual UI, word reveal, local presentation preferences, minimal idle/reaction
behavior, and envelope-driven mouth motion. Python owns canonical data, turn
serialization, STT, TTS playback, and provider selection.

The complete VRM body is rendered with padded camera bounds to a RenderTexture.
The current UI crop/pan/zoom framing is **transitional**. Do not treat further
UV-crop adjustments as final architecture. The preferred direction preserves
full-avatar capture and uses a stable high-resolution RenderTexture in a
presentation container with X/Y translation and transform scale, with separate
portrait and landscape state.

Light/Dark changes UI only. Background selection is independent from theme;
the intended presentation direction has separate portrait and landscape
backgrounds, a solid white/light-neutral portrait default, a bundled anime
bedroom landscape default, and future user PNG backgrounds. A user
crop/framing editor is not planned.

## Validation

For backend or transport changes:

```text
.venv-aifren\Scripts\python.exe -m unittest discover -s tests -v
.venv-aifren\Scripts\python.exe test_tts.py
git diff --check
```

For Unity changes, run relevant EditMode tests in Unity 2022.3.62f3 and build
the Windows player. Do not call uncompiled C# validated.
