# AIFren

AIFren is a local-first, long-lived AI companion project. Its central goal is
continuity: reopening the app returns to the same character, canonical history,
memories, unresolved threads, and current situation rather than a disposable
chat session.

The project currently consists of a frontend-neutral Python backend and a Unity
companion client. It is an actively developed public technical baseline, not a
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

- Canonical local conversation history plus derived summaries and Memory V1.
- Persistent character creation/selection with isolated personality, history,
  memory, continuity state, and per-character managed VRM selection.
- Character-scoped Memory V2 continuity infrastructure: source-grounded
  episodes, narrow governed durable facts, Truth Scope, Open Threads, and
  Active State. Generic V2 retrieval remains non-authoritative.
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
  detailed Scene inspection, and an optional lightweight Current Scene overlay.
- Replaceable online/local LLM, TTS, STT, and embedding implementations;
  managed llama.cpp, Kokoro resource failover, and authoritative PTT.
- Privacy-safe Development flight recording and headless production-path QA.

## Architecture at a glance

```text
Unity companion --loopback WebSocket--> backend_host.py --> AssistantService
                                                        |-> canonical archive
                                                        |-> Memory V1
                                                        |-> Memory V2 continuity
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
- Memory V1 remains broad prompt-facing memory authority. Memory V2 owns
  validated structured continuity lanes and source-grounded derived context,
  not universal memory truth.
- Active State is current reality, not durable biography and not relationship
  state. Relationship State is explicitly deferred.
- Profile/default-scene facts are lower authority than explicit current
  evidence and remain distinct from learned memory.
- Capability effects are derived from current relation semantics; they are not
  duplicated as ordinary memory facts.
- Character identity/personality/history are separate from reusable visual and
  audio assets. Managed avatars remain global assets while each character owns
  only its stable selected-avatar reference.
- A subsystem may delete only data it owns. Imported source files and canonical
  evidence are never implicit deletion targets.
- PTT/audio lifecycle is authoritative. Presentation timing cannot delay or
  resurrect capture, synthesis, or playback.

## Current status

The broad Active State architecture and its production seams are substantially
implemented and hardened. An ordinary manual Linux Development-player
acceptance cycle remains required before release. Further Active State work
should be driven by concrete real-use regressions, not speculative semantic
breadth.

The next major product-facing memory tranche remains a non-destructive Memory
Viewer/Editor. Relationship State remains deferred.

See [PROJECT.md](PROJECT.md) for current direction,
[ARCHITECTURE.md](ARCHITECTURE.md) for technical ownership,
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) for durable decisions, and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) for setup and validation.

## License

AIFren is licensed under the [AIFren Public Source License v1.0](LICENSE.md).
Third-party components and assets retain their own licenses.
