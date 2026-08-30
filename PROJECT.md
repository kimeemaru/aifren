# AIFren project direction

## Vision

AIFren is local-first, long-lived companion software. Continuity over years is
the product: a character should retain canonical history, personality, useful
memory, unresolved intentions, and current reality across launches and across
replaceable models, voices, frontends, operating systems, and hardware.

The project principle is:

> **Persist the facts that matter; infer the experience.**
>
> **Context is for reasoning. Active State is for continuity.**

AIFren aims for **bounded improvisational world-awareness**. It preserves only
sparse, interaction-relevant truth and constraints, then lets the model write
naturally inside that authority. It does not attempt full world simulation.

## Durable principles

1. Raw conversation is canonical. Summaries, embeddings, indexes, episodes,
   and structured state are aids, never a replacement archive.
2. Character identity, authored personality, learned memory, current state,
   future relationship state, voice, and visual assets are distinct.
3. LLM, TTS, STT, embedding, indexing, and frontend implementations remain
   replaceable without forking continuity.
4. Data durability, provenance, correction, scope, ownership, portability, and
   recovery take priority over short-term convenience.
5. A continuing character has no ordinary “New Chat” lifecycle.
6. Backend authority constrains facts and capabilities; the model owns
   characterful interpretation and prose.

## Current product boundary

`AssistantService` owns backend turns, canonical persistence, context assembly,
Memory V1 processing, governed Memory V2 continuity, provider requests,
TTS/STT/PTT, and backend events. `backend_host.py` is the loopback WebSocket
adapter. Unity is the sole product frontend and cannot mutate canonical memory
or conversation directly.

Provider selection is configuration, not identity. Online and Local adapters
receive the same provider-neutral character and continuity context. AIFren owns
only the managed local processes it starts; external compatible endpoints are
never killed or restarted. Managed llama.cpp launches retain the required
`--logits_all false` setting.

The normal presentation path is direct VRM rendering behind Screen Space
Overlay UI. The managed avatar library remains global and reusable rather than
character identity, while each character keeps a local stable reference to its
last selected VRM. Background, lighting, and portrait/landscape framing remain
global presentation preferences, and global UI hide never changes framing.

## Continuity ownership

### Canonical conversation

The permanent source record of what occurred. It is retained independently of
History rendering, model context windows, summaries, or derived structured
state. Corrections supersede derived truth; they do not rewrite old dialogue.

### Memory V1

The broad, general, prompt-facing learned-memory authority in the current
architecture. It remains separate from the raw archive and from current scene
state.

### Memory V2

Character-scoped, source-grounded structured continuity infrastructure. Current
implemented production lanes include:

- exact source events, evidence, lifecycle, and Truth Scope;
- source-ranged lower episode accounts and conservative derived context;
- narrow closed-schema durable facts and correction/supersession;
- Open Threads;
- Active State subjects, attributes, relations, and lifecycle.

These lanes are independently governed. Memory V2 is neither speculative-only
nor a monolithic replacement for conversation or Memory V1. Generic V2
retrieval remains non-authoritative and fail-open.

### Active State

Sparse current reality: actor activity/posture, material scene subjects,
current attributes and relations, environmental consequences, and derived
capability constraints. State is evidence-bound, actor-aware, truth-scoped,
transactional, lifecycle-managed, and applied before response generation.

Current generic scene operations cover establish, set attribute, establish or
clear relation, replace, transfer, locate, correct, retire, and conservative
explicit reactivation. Attribute change and replacement are different:

- “Your blue hat is red now” updates the same subject.
- “Replace your blue hat with a red one” closes the old current relation and
  establishes a distinct replacement.

Capability effects derive from relation meaning, affected actor/facet,
side/quantity, cause, and explicit consequences—not object-name flags.
Decorative wristwear therefore differs from a tethered wrist; carried
rollerblades differ from worn rollerblades; nearby equipment differs from
equipment in use.

Current derived domains are perception (vision, hearing, smell, taste, touch),
communication/speech, side-aware manipulation, semantic locomotion,
awareness, and posture. Multiple causes compose independently. Removing one
cause never restores a capability while another cause remains.

Hearing distinguishes normal, constrained, and unavailable. Constrained
hearing remains usable with limitations. When an interaction explicitly makes
a user utterance inaudible, its canonical record is preserved but its new
semantic content is excluded from understood context and memory/continuity
derivatives; restoring hearing does not reveal it retroactively. A conventional
blindfold applied over the eyes directly removes vision, and later coverage
wording refines that same logical application rather than stacking another.

Posture is limited actor state (`standing`, `sitting`, `lying`), distinct from
location/support. “Lying on the couch” can establish both posture and a couch
relation. Unity does not yet provide strong authored presentation for every
sitting/lying posture.

**Capability unavailable does not mean response unavailable.** A constrained
channel removes only that channel. Remaining gaze, expression, gesture,
posture, touch, movement, or nonverbal prose remain available. Fallback denotes
generation or validation failure, not an ordinary state presentation.

### Open Threads

Unresolved, waiting, planned, or follow-up continuity. They are not ordinary
permanent facts and remain truth-scoped.

### Profile/default scene

Lower-authority stable character defaults such as usual attire. Explicit
current evidence overrides them; explicit removal can suppress reassertion.
Defaults remain distinct from learned memory and Active State writes.

### Relationship State

Explicitly deferred. No authoritative relationship-state subsystem exists.
Relationship-relevant history is not a substitute for that future layer.

## Response and mutation authority

Canonical dialogue is the minimal valid assistant response. A compact
structured envelope may additionally supply response mode, exact spoken
projection, semantic presentation metadata, capability diagnostics, or one
bounded companion-action proposal. Optional fields may be omitted; supplied
fields remain strictly validated.

Two response requirements are intentionally distinct:

- **must-respect** — an accepted mutation fact may be omitted from prose but
  cannot be contradicted;
- **must-communicate** — a direct deterministic state/time/date question must
  actually communicate the authoritative answer.

One turn may compose several independently recognized direct questions into
several `must-communicate` facts. Ordinary replies have a soft preference near
100 words without a general hard ceiling; constrained/nonverbal reactions use
a tighter 50--80-word target and bounded 100-word validation ceiling with one
repair. No response is raw-truncated.

Direct requirements receive at most one bounded repair followed by a
state-derived deterministic fallback. Unknown is never converted into none,
and fallback cannot invent scene facts.

The authoritative order is:

```text
canonical user evidence
  -> validate mutation
  -> apply authoritative transaction
  -> recompute capability envelope
  -> generate from post-mutation state
  -> validate/repair
  -> persist and publish
  -> presentation/TTS
```

A rejected mutation cannot be narrated as completed. Ordinary assistant prose
is never state authority.

The bounded companion-action slice permits validated companion-only activity,
posture, one current wear/remove or hold/release operation, and supported
equipment use/dismount. A private structured proposal is checked against actor,
subject, scope, lifecycle, and current capabilities before mutation; narration
must match the accepted result. This is not a general autonomous-agent loop.

## Unity continuity surfaces

History is a derived scalable browser:

```text
Year -> Month -> Day -> bounded/paged messages
```

It does not instantiate the lifetime archive at once. Empty legacy records are
omitted from rendering, canonical history stays untouched, and visible updates
are incremental. Future transport-side pagination may further reduce initial
snapshot size without changing the canonical archive.

Scene Details is the full bounded development inspection surface. The optional
left Current Scene overlay is a lightweight, translucent, portrait-first
summary of important current items, relations, capability effects, and causes.
It uses content-driven bounded height, has no permanently visible scrollbar,
and participates in global UI Hide.

Overlay X is an in-world interaction. The backend validates the opaque token,
revision, and scope; mutates authority first; recomputes effects; publishes the
snapshot; creates one deterministic synthetic user-side scene event; and offers
one reaction opportunity. The event is stored canonically with structured
`scene_ui/generated_event` origin but is never reparsed as evidence. Detailed
administrative correction remains silent and creates no in-world event.

Scene-event wording follows relation predicate/semantic family before body
facet, so decorative wristwear cannot render as restraint removal. Stable
canonical message identity prevents a snapshot row and delayed event echo from
creating duplicate visible entries without collapsing legitimate identical
messages.

Truth Scope isolates `real_world` from persistent RP/scenario state. Leaving a
scope hides rather than deletes it; re-entry restores it. The RP indicator is
anchored to the root safe-area bottom-left independently of the chat layout.

## Proactive, History, and speech status

Proactive behavior is production-facing and conservative. Its saved discrete
interval is a minimum opportunity, not a send timer. Startup waits for backend,
provider, and frontend readiness plus a grace period. Background generation is
invisible until publishable; failed attempts are throttled and cannot create a
phantom Thinking turn. Ignored messages produce weak AFK backoff, never
relationship judgment, and real user activity relaxes the appropriate state.

Kokoro streaming and direct governed speech share ordered resource handling.
Recognized accelerator resource failures retry the same unresolved speech and
can move Kokoro to CPU for the runtime without duplication or skipped middle
speech. Accelerator classification supports CUDA and HIP/ROCm semantics;
ROCm evidence is injected/architectural until tested on real ROCm hardware.

Generated assistant emoji are removed consistently before assistant
persistence/presentation/speech, while user-authored Unicode remains canonical.
Outer action/emote spans are nonspoken even when they contain nested emphasis;
ordinary emphasis embedded in spoken dialogue remains spoken regardless of
word count. Physical generated actions use `*...*`, while parentheses remain
ordinary prose. Streamed, whole-text, subtitle, and Unity projections follow
the same semantic policy. Configured WPM/WPS is the maximum normal reveal rate;
short or absent audio may slow but never accelerate large nonspoken responses.

Whisper can similarly move from an accelerator resource failure to CPU/int8,
retry the same WAV once, and remain on CPU for that runtime. PTT capture and
playback identities prevent stale work from resuming.

## Current status and next work

Active State's generic relation/capability architecture, lifecycle, response
authority, automated regressions, Unity inspection surfaces, and current
integration seams are substantially implemented and conservatively validated.
Before a release, the foundation still requires an ordinary manual Linux
Development-player acceptance cycle. Future Active State changes should begin
with a concrete reproducible real-use failure and become permanent production
regressions; speculative breadth is not the next task.

Open-vocabulary body/attachment/scene loci with closed capability consequences
are an agreed future bounded improvement, not current accepted behavior. It
should preserve custom loci conservatively without turning anatomy into global
equipment-slot enums or inferring capability effects from locus names alone.

Current priorities after manual acceptance:

1. Fix only demonstrated release-blocking regressions in the frozen foundation.
2. Build the non-destructive, bounded Memory Viewer/Editor as the next major
   product-facing memory tranche.
3. Maintain conservative episode/retrieval behavior without promoting generic
   V2 retrieval to universal authority.
4. Continue bounded Unity polish, packaging, voice/animation validation, and
   platform work without displacing continuity correctness.
5. Keep Relationship State deferred until separately designed and authorized.

The project is not finished: character-management expansion, backup/export,
Memory Viewer/Editor, broader packaging, Windows validation, and 1.0 product
work remain.

## Parked or excluded from current scope

- Relationship State implementation.
- Full world simulation, physics, maps, inventory, pathfinding, and arbitrary
  agent/tool loops.
- Generic GLB avatars, remote/mobile companion, and VR.
- Automatic day/night world simulation or game-like relationship meters.
- Broad Memory V2 authority without provenance, bounded admission, inspection,
  and explicit promotion evidence.
