# AIFren project direction

## Current authority

Memory V2 is the normal prompt-facing long-term-memory authority. It admits
canonical-source-grounded history and maintains governed facts/corrections,
episodes, Truth Scope, Open Threads and Active State. Normal V2 operation is
independent of V1 prompt memory, learned-memory writes and summary writes.
CompanionMemoryRealizer is the normal local-provider surface owner for admitted
memory answers. Optional model reactions are non-authoritative and dispensable.

Memory V1 remains temporary, explicit one-launch rollback/compatibility. V2
startup or lookup failure never silently switches authority. Canonical dialogue
remains permanent source evidence; neither version replaces the raw archive.

## Product direction

AIFren is a local-first, continuing companion, not a disposable chat session.
**Persist the facts that matter; infer the experience.**
**Context is for reasoning. Active State is for continuity.**

Identity, authored personality, dialogue, learned facts, current scene, visual
assets and voice are separate. Models and presentation implementations can change
without creating a new character timeline. Corrections retain their provenance
and history; they do not rewrite old conversations.

## Implemented technical baseline

- Source-grounded historical recall with speaker, scope, polarity and modality
  checks; current versus historical values and bounded before/after ordering.
- Exact-source immediate attribute follow-ups that cannot borrow an unrelated
  source just because it contains the requested kind of detail.
- Durable personal facts, correction/supersession history, source-ranged episodes,
  scoped Open Threads and sparse Active State.
- Incremental observation/recovery and derived MiniLM/FTS/ANN retrieval. An
  unresolved historical operation stays visible without unsafe replay or idle spin.
- CompanionMemoryRealizer: concise grounded prose plus an optional safe present
  reaction, without an extra inference or repair for a rejected reaction.
- Unity direct VRM rendering, independent portrait/landscape framing, hidden
  speech subtitles with word fades/color, paged History, Memory Viewer/Editor,
  and a Current Scene drawer with backend-owned cause-specific removal.
- Provider-neutral local/online operation, bounded synthesis cancellation,
  final-response ownership and isolated Development diagnostics/tests.

This is pre-1.0 software. Technical validation does not imply human subjective
acceptance, universal model quality or finished cross-platform distribution.

## Next work

1. Companion feel and passive continuity within existing authority boundaries.
2. Avatar, animation and audio presentation polish.
3. Character/avatar management and user-controlled backup/export.
4. Windows validation and packaging/distribution.
5. 1.0 durability, usability and performance hardening.
6. Later Relationship State and explicitly controlled external capabilities.

This roadmap is not automatic authorization to implement another stage. Active
State is current reality, not biography or Relationship State. AIFren is not a
physics, inventory, anatomy, pathfinding or general autonomous world simulator.

## Development contract

Use synthetic data for correctness QA. Test the same service and normal client
owners; never depend on another developer's characters, model caches or assets.
Keep code/tests, generated results and subjective experience review distinct.
Preserve canonical records, current corrections, settings and imported originals.
See [ARCHITECTURE.md](ARCHITECTURE.md), [design decisions](docs/DESIGN_DECISIONS.md)
and the [developer guide](docs/DEVELOPER_GUIDE.md).
