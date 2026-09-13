# AIFren

AIFren is a local-first, long-lived AI companion project. Its central goal is
continuity: reopening the app returns to the same character, canonical history,
memories, unresolved threads, and current situation rather than a disposable
chat session.

The project currently consists of a frontend-neutral Python backend and a Unity
companion client. It is an actively developed technical baseline, not a
finished consumer release.

## Product principle

**Persist the facts that matter; infer the experience.**

**Context is for reasoning. Active State is for continuity.**

AIFren provides **bounded improvisational world-awareness**. The backend keeps
sparse, evidence-backed current facts, actors, relations, scopes, lifecycles,
and capability constraints. The language model interprets those facts and
reacts creatively inside the authoritative envelope. AIFren is not a physics,
inventory, anatomy, pathfinding, or general world simulator.

## What works today

- Permanent canonical conversation, with Memory V2 as normal long-term-memory
  authority: source-grounded historical recall, durable facts/corrections,
  episodes, Truth Scope, Open Threads, and Active State. MiniLM/FTS and episode
  representations are derived retrieval aids, not replacement truth.
- CompanionMemoryRealizer renders admitted memory answers as ordinary dialogue.
  Optional present-moment model reactions can be dropped without losing the
  grounded answer. V1 remains explicit one-launch rollback/compatibility.
- Responsive committed speech: playback starts from a prepared opening unit while
  the rest is synthesized in order. Natural/Roleplay delivery is selectable;
  ACT preview and automatic CPU expressions are optional, with temporary automatic
  expression ownership. See [controls and limits](docs/NATURAL_COMPANION_DESIGN.md).
- Character-local continuity and database storage, confirmed management operations,
  live switching and per-character/avatar/orientation framing. Settings > Character
  > Manage includes Reset timeline, Delete and selected legacy-storage migration.
- A generic current-scene model with actor-aware subjects, attributes,
  relations, corrections, transfers, replacements, locations, and
  current/dormant/retired lifecycle.
- Derived vision, hearing, smell, taste, touch, speech, manual, locomotion,
  awareness, and posture capability effects with independent multiple causes.
- Post-mutation response authority, deterministic direct-query requirements,
  bounded companion-action proposals, and constrained reactions that preserve
  the model's remaining expressive channels.
- Persistent real-world and roleplay/scenario scopes without ordinary
  cross-scope leakage.
- Conservative proactive check-ins with a configurable opportunity interval,
  startup grace, background publication boundary, and ignored-check-in
  backoff.
- Unity direct VRM presentation, scalable Year/Month/Day/paged History,
  Memory Viewer/Editor, Scene inspection, and a hover/click/focus Current Scene
  drawer. Portrait/landscape presentation and speech-timed hidden subtitles
  retain independent framing and presentation preferences.
- Replaceable online/local LLM, TTS, STT, and embedding implementations;
  managed llama.cpp, Kokoro resource failover, and authoritative PTT.
- Privacy-safe Development flight recording and headless production-path QA.

## Architecture at a glance

```text
Unity companion --loopback WebSocket--> backend_host.py --> AssistantService
                                                        |-> canonical archive
                                                        |-> Memory V2 authority
                                                        |   facts/history/episodes
                                                        |   state/threads/scopes
                                                        |   MiniLM/FTS (derived)
                                                        |-> CompanionMemoryRealizer
                                                        |-> V1 explicit rollback
                                                        |-> LLM / TTS / STT
```

Python owns turns, canonical persistence, memory processing, governed current
state, provider lifecycle, TTS/STT, PTT, and backend events. Unity owns product
presentation and local presentation preferences; it never writes canonical
conversation or memory directly.

The normal avatar path is direct VRM rendering. The RenderTexture path is
rollback/debug-only. Portrait and landscape framing/background preferences are
independent, and global UI hide does not resize the avatar.

## Important boundaries

- Raw conversation is canonical. Derived summaries, episodes, indexes, and
  structured state never justify rewriting it.
- Memory V2 is normal prompt-facing memory authority. Only admitted, scoped,
  source-grounded evidence can establish a remembered proposition. V1 is
  explicit rollback only; V2 failure never silently selects it.
- CompanionMemoryRealizer performs no retrieval and owns only the surface of an
  already-admitted answer. Optional reactions have no historical truth authority.
- Active State is current reality, not durable biography and not relationship
  state. Relationship State is explicitly deferred.
- Profile/default-scene facts are lower authority than explicit current
  evidence and remain distinct from learned memory.
- Capability effects are derived from current relation semantics; they are not
  duplicated as ordinary memory facts.
- Character identity/personality/history are separate from reusable avatar,
  background, provider, and voice assets.
- A subsystem may delete only data it owns. Imported source files and canonical
  evidence are never implicit deletion targets.
- PTT/audio lifecycle is authoritative. Presentation timing cannot delay or
  resurrect capture, synthesis, or playback.

## Current status

Memory V2, the responsive Context Governor, Memory Viewer/Editor and character
management are implemented. Named-topic personal recall after restart now routes
through source admission. Intermittent blank History/Memory panels after reset and
switch remain unresolved. Natural-mode quality, automatic acting, CPU synthesis
cancellation and optional memory commentary have practical limits. This is pre-1.0;
it does not claim human acceptance or general grounding of ordinary model prose.
Presentation polish, backup/export, cross-platform packaging and hardening remain.

See [PROJECT.md](PROJECT.md) for current direction,
[ARCHITECTURE.md](ARCHITECTURE.md) for technical ownership,
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) for durable decisions, and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) for setup and validation.

## Source navigation

Python implementation lives in `aifren/`, with Unity in `unity/`, supported helpers
in `scripts/`, developer evaluations in `tools/` and `benchmarks/`, and synthetic
regressions in `tests/`. See the [owner and entry-point map](docs/SOURCE_LAYOUT.md).

## Getting started

Use Python 3.10–3.12 and Unity 2022.3.62f3. The public tree starts with a generic
companion; it contains no conversations, personal memory or configured credentials.
Model weights are installed separately. See the [developer guide](docs/DEVELOPER_GUIDE.md)
for Linux setup, model configuration, tests and the explicit V1 rollback command.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md).
Third-party components and assets retain their own licenses.
