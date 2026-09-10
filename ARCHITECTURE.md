# AIFren architecture

## Runtime and authority

```text
Unity companion
    | loopback WebSocket (one client)
backend_host.py
    | AssistantService
    |-- canonical conversation
    |-- Memory V2 authority
    |     |-- facts / corrections
    |     |-- historical occurrences / episodes
    |     |-- Active State / Open Threads / Truth Scope
    |     `-- MiniLM / FTS / ANN (derived retrieval)
    |-- CompanionMemoryRealizer
    |-- V1 explicit rollback
    `-- replaceable LLM / TTS / STT
```

`AssistantService` owns turns, persistence, memory processing, context, speech/PTT
and backend events. `backend_host.py` is a loopback transport adapter. Unity is
the sole production frontend and never writes canonical stores directly.

V2 is normal prompt-facing long-term-memory authority. Explicit process-local
`AIFREN_MEMORY_AUTHORITY=v1` selects compatibility; the Development launcher
exposes `development v1-memory` for one launch. An ordinary next start selects V2.
V2 failure stays visible and cannot silently activate V1. Normal V2 contributes
zero V1 prompt memory and makes no V1 learned-memory or rolling-summary writes.

## Durable owners and derived representations

| Material | Owner and boundary |
| --- | --- |
| Canonical dialogue | `Conversation`: atomic JSON replacement; permanent original evidence. |
| Original V2 evidence | Character-scoped SQLite repository: source events, Viewer corrections and administrative actions retain their provenance. |
| Facts and corrections | Closed typed contracts and supersession/lifecycle records; old values never replace current corrections through indexing. |
| Active State | Current subjects, attributes, relations, literal loci and independent capability causes. |
| Open Threads / Truth Scope | Scoped unresolved continuity and explicit scenario control, separate from biography. |
| Historical occurrences | Exact canonical record/index, speaker, scope, hash and source classification. |
| Episodes, FTS, MiniLM, ANN | Rebuildable representations; retrieval relevance never establishes truth by itself. |
| Presentation | Unity preferences, avatar, backgrounds and framing; not character identity. |

The entire V2 database cannot be reconstructed from conversation alone: Viewer
corrections and other original SQLite evidence must be retained. Summaries and
prepared caches are neither migration authority nor replacements for live truth.

## Initialization and recovery

`config.configured_memory_authority`, the service factory and character rebind
select the same authority. Normal V2 opens/version-upgrades the existing store,
ensures the selected character without overwriting it, and uses existing bounded
canonical observation and idle index/episode owners. It does not import V1 memory
or require a prepared acceptance cache.

`memory_v2_initialization.initialize_v2_derived_state` supplies finite explicit catch-up
through those owners. Independent progress cursors, exact source-prefix digests,
source identities and durable per-vector state make work idempotent and resumable.
Already-current work is a no-op; appends continue rather than triggering a full
rebuild. Source edits, unsafe old scope operations and ambiguous replay stay
visible. Unchanged idle work does not reread/retry indefinitely; independently
valid ranges can progress beyond excluded gaps. Never suppress an unresolved
operation simply because it is old.

Optional episode acceleration must be strictly attested against exact source and
policy identities, installed additively, and validated by the shared episode
owner. It cannot replace current facts, corrections, state, threads or scopes.

## Query, admission and exact follow-up

One immutable `MemoryQueryDecision` travels through retrieval, projection, evidence
sufficiency, context containment, answer requirements and diagnostics. A question
must actually request memory; incidental temporal words are not enough.

Typed before/after requests retain their anchor, canonical ordering, speaker and
scope. A before answer needs evidence preceding the identified correction/event.
Multiple eligible predecessors clarify instead of guessing. Current durable state
cannot substitute for historical-before evidence.

Historical source projection selects up to two complete local passages sharing
220 source characters. Separate passages retain distinct offsets; they are never
spliced into a fake contiguous quote. Candidate/context bounds remain enforced.
Budget or projection failure is unavailable, not healthy absence.

A published grounded answer can establish one unique provenance-only anchor for
an immediate concrete attribute question. The next lookup is restricted to that
exact source/event's permitted evidence neighborhood. It cannot search unrelated
history for a place/language/color. Missing or ambiguous attributes fail closed.
The handle is not a new fact and cannot derive truth from assistant wording.
Intervening unrelated input, scope/character changes, cancellation and unpublished
answers invalidate it. Retired callbacks cannot establish a new anchor.

## Companion memory realization

```text
canonical / V2 evidence
    -> typed admitted answer
    -> CompanionMemoryRealizer: immutable grounded core
    -> optional non-authoritative present reaction
    -> final service governance and canonical commit
    -> one publication / subtitle session / TTS utterance
```

The realizer performs no retrieval and stores no memory. Relation/ownership-specific
forms express current facts, historical attribution, before/after relations,
source-bound attributes and supported/missing slots. A session/turn hash selects
bounded reproducible surface variation without varying facts. Narrow grammatical
person projection is allowed; unrepresentable source syntax remains an exact
attributed report rather than an invented paraphrase.

The memory validator checks the complete immutable factual core. The optional
reaction has its own whole-tail present-subjective grammar: it cannot add past
feelings, repetition, circumstances, causes, questions or new historical claims.
Invalid or unavailable reaction output is dropped without repair and the grounded
core remains. The existing response inference supplies this optional tail; there
is no second style/emotion call. Other adapters retain governed provider-direct
realization. Emergency safe responses remain observable internal invariant paths,
not normal grounded-answer presentation.

Complete service governance still applies to capabilities, temporal content,
persistence, cancellation and publication. No core is shown before commit, no
late reaction becomes a second utterance, and the source anchor uses admitted
proposition identities rather than rendered words.

## State, publication and audio

Accepted scene transactions precede capability recomputation and response eligibility.
Removing one cause does not restore a capability while another remains. A scene
interaction creates its canonical event independently of whether its reaction
succeeds. Silent administrative corrections remain a separate source class.

User and assistant persistence have explicit commit/failure boundaries. Cancelled,
failed and superseded drafts cannot publish or apply final presentation metadata.
Canonical JSON and SQLite retain separate transactional owners and recovery proofs.
Provider readiness belongs to a configuration/operation identity; a stale worker
cannot certify a replacement. Only demonstrably owned model processes are stopped.
Managed llama.cpp uses `--logits_all false`.

PTT immediately retires synthesis/playback. Bounded synthesis worker ownership,
queue draining and playback dispatch keep obsolete work from blocking replacement
turns. Natural audio completion and interruption are different retirement events.
Subtitle layout, fade and dwell never gate capture, synthesis, playback or readiness.

## Unity presentation

Direct VRM rendering is normal; RenderTexture is rollback/debug-only. UI visibility
never resizes/reframes the avatar. Portrait/landscape framing and backgrounds are
independent; visual asset swaps cannot alter a character's durable identity.

`HiddenSubtitlePresenter` owns renderability, page changes, per-word TMP alpha and
transitions. Timing-due, actually-shown and fade progress are distinct. Temporary
peek suppresses rendering without replaying settled fades; committed Show cancels
the session. Base text color is persisted through `PresentationPreferences` and
multiplied by transient alpha. Typed action/emphasis semantics match backend TTS.

Final accepted semantic metadata and bounded explicit facial-emote/body fallback
share one resolver. Explicit emotion wins; absent emotion preserves the face and
neutral clears it. Persistent expression, transient body, blink, lips and gaze
retain separate owners and authored VRM overrides. Capabilities are applied before
eligibility, with at most one deliberate gesture per reply. Missing assets or
presets degrade gracefully; optional local motion trials are not required assets.

History loads bounded newest pages. The Memory Viewer uses bounded requests,
provenance/health labels and backend corrections; stale character/request results
are rejected. The Current Scene drawer is a visibility owner over existing rows:
hover/click/focus never mutates state, and X uses the exact cause/token/revision.

Development diagnostics are bounded and content-free by default. Finite automation
must isolate application data/preferences before initialization; normal launches
have no implicit test plan. See the developer guide for public-safe validation.

## Historical research

Modules named `*_shadow` and `*_evaluation` include earlier disconnected experiments
and reusable typed helpers. A module name does not grant runtime authority. Current
production follows the owners above; no historical V1-default or Development-only
V2 experiment overrides normal startup.
