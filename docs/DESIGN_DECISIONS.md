# AIFren Design Decisions

> **Status:** Durable product/system design record.
> **Updated:** 2026-08-29.
> **Purpose:** Preserve the detailed reasoning, invariants, and future-system decisions that are intentionally kept concise in `PROJECT.md`.
>
> - See [`../PROJECT.md`](../PROJECT.md) for the concise direction and roadmap.
> - See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for current implementation topology and ownership boundaries.
> - See [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) for implementation and contributor workflow guidance.
>
> This document mixes current behavior with future design requirements. Each subsystem explicitly distinguishes **Implemented / Current**, **Planned / Decided**, **Exploratory / Parked**, and **Undecided** where needed. A future decision is not a claim that the feature exists today.
>
> **For ChatGPT/Codex/other agents:** treat **Planned / Decided** items as durable constraints and intended behavior, not as permission to implement them without an explicit task. Treat old chat transcripts, stale roadmap text, and rollback/debug paths as non-authoritative when they conflict with a newer decision recorded here. For exact current implementation details, prefer current code and `ARCHITECTURE.md`; for product intent and future-system constraints, prefer this document. Explicit new user direction overrides this document and should be recorded deliberately rather than silently.

---

## 1. Product philosophy

### Planned / decided — long-lived companion software

AIFren is intended as long-lived "forever software": a companion that can remain useful to one owner for many years. The target experience is closer to a familiar long-term personal partner than to a disposable assistant, a sequence of chat sessions, a game NPC, or a constantly autonomous streamer.

The product differentiator is **continuity**. Raw model quality matters, but a stronger model is not allowed to become the source of identity. The same character should survive model upgrades, TTS/STT changes, frontend rewrites, operating-system changes, and hardware replacement.

This creates several durable implications:

- An existing character does not have an ordinary **New Chat** lifecycle.
- Reopening AIFren should return to the same continuing character and relationship.
- A new independent history belongs to an explicitly new character identity, not to a cleared chat window.
- Canonical character/history data must outlive replaceable inference components.
- Data migration, backup, provenance, portability, and recovery are first-class product concerns rather than afterthoughts.
- Storage efficiency is secondary to preserving a trustworthy history. AIFren should not destroy canonical history merely to keep a database small.
- Features that make the companion feel coherent over years are more important than constant novelty or autonomous activity.

### Implemented / current — local canonical ownership

Canonical conversation history, summaries, memories, and character configuration are stored locally. The Python backend is authoritative for the current conversation/memory flow rather than delegating continuity to a cloud chat session.

### Planned / decided — local-first end state

Local-first is a foundational requirement. The long-term target is fully local core operation: LLM, STT, TTS, memory, and canonical storage should be capable of operating without a mandatory cloud service.

Cloud providers used during development are transitional provider choices, not the permanent architectural owner of the character. Cloud backends may remain optional integrations, but loss of a provider must never imply loss of the character.

The user should be able to move the durable character archive between machines, operating systems, checkout locations, and eventually removable/external storage without resetting identity.

---

## 2. Character identity and lifecycle

### Planned / decided — identity is not the avatar

A character is a durable logical identity. The following are separate concepts even when the UI presents them together:

- base personality / authored identity,
- canonical conversation history,
- derived memories,
- relationship state,
- current/active state,
- voice profile,
- visual avatar/VRM,
- presentation preferences,
- provider/model choices.

Changing a visual model must not create a new person. A character may use different VRMs over time, and an imported VRM is a visual asset rather than the definition of the character.

Likewise, changing the LLM, TTS engine, STT engine, embedding model, or frontend must not silently create a new identity.

### Implemented / current

The current application is effectively one active character. Character configuration/base personality are already separate from conversation history and memory.

### Planned / decided — multiple characters

AIFren should technically support multiple independent characters. One primary character is expected to be the common daily-use case, so multi-character support must not force a game-like roster experience on a user who only wants one companion.

Each character must eventually scope all identity-bearing state, including:

- canonical history,
- summaries,
- memories,
- relationship state,
- personality/configuration,
- active state,
- voice profile,
- avatar/presentation assignment,
- other character-specific durable state.

Shared assets may be referenced by multiple characters without becoming character-owned.

### Planned / decided — startup and navigation

There should be no game-like title screen or main menu.

Normal startup should:

1. open directly to the last-used character when that is unambiguous, or
2. show Character Select only when selection is actually needed.

Character Select / Character Management is planned as a product-management surface, not a game menu.

### Planned / decided — new-character flow

Creating a new character is the clean way to start an independent personality/history/memory lifecycle.

The new-character flow should have good ordinary-user UX rather than requiring manual file editing. Desirable options include:

- friendly personality presets,
- editable personality fields,
- avatar/model presets for users who do not want to source a VRM,
- voice choices/profiles,
- the option to import compatible assets.

A new character should be able to become acquainted naturally. The product should avoid turning first use into an interrogation, relationship-level tutorial, or game mechanic.

### Planned / decided — deletion and ownership safety

Deleting a character must delete only character-owned state after explicit confirmation.

Character deletion must **not** implicitly delete shared/global assets merely because the character references them. In particular, a character record does not own a globally shared:

- VRM,
- background,
- voice/reference asset,
- reusable model,
- other shared resource.

Where practical, destructive actions should offer archive/deactivate/reversible alternatives before irreversible deletion.

### Undecided

The exact persistent file/database layout for multiple characters is not settled. The scope requirement is settled; the storage migration is not.

A future explicit **fork character** operation has been discussed conceptually as a way to preserve history while intentionally creating a diverging identity, but the exact UX and semantics are not settled.

---

## 3. Personality system

### Implemented / current

The current character has explicit file-backed personality/configuration separate from raw history and learned memory. This explicit personality is the current authoritative base identity.

### Planned / decided — stable authored base

Base personality, core identity, authored interests, and moral/personality anchors should be highly persistent. Learned memories and model-generated interpretations must not silently rewrite them.

The system should distinguish at least:

- **base personality** — authored, explicit, high-authority identity;
- **learned memory** — source-backed facts/experiences learned over time;
- **relationship state** — accumulated state between this character and the user;
- **active/current state** — temporary situational facts.

These layers may influence one another at response time, but they are not interchangeable storage categories.

### Planned / decided — limited drift

A character may react differently because of experience, relationship context, or current mood, but innocuous interactions must not create runaway personality drift.

Examples of unacceptable behavior include:

- a single ambiguous negative comment permanently making the character hostile;
- a model interpretation silently changing core values;
- a learned interest overriding an authored interest so strongly that the character becomes dismissive of a topic the user still cares about;
- accidental fourth-wall or out-of-character output becoming a durable personality change merely because the assistant said it once.

Personality should instead act partly as a set of stable reaction tendencies and thresholds. A more patient personality may require stronger evidence before becoming annoyed; a more expressive personality may show stronger transient reactions. This should not make the base personality self-modifying.

### Planned / decided — editing

Explicit user editing of base personality should remain possible. Normal personality editing should preserve the same identity, relationship, history, and memories unless the user explicitly chooses a new-character/fork lifecycle.

Personality presets are desirable for new-character creation, but they are starting points rather than game classes.

Character-specific personality data must stay separate from globally shared visual assets.

### Planned / decided — reset behavior

A normal settings reset must not erase or regenerate character personality.

A dedicated personality reset, if added, should be explicit and scoped to personality data. Starting a truly independent personality/history lifecycle belongs to a new-character operation.

### Undecided

The final structured personality schema, if any, is not settled. Markdown/freeform authored personality is valid current behavior; future structured fields must not force a loss of nuance.

---

## 4. Conversation history

### Implemented / current — canonical raw archive

Raw conversation history is the authoritative record of what was said. Summaries, embeddings, retrieval indexes, memory extractions, and other derived structures exist to make the archive usable; they do not replace it.

### Planned / decided — indefinite retention by default

Normal memory maintenance must not permanently delete old raw conversation simply because it is old, unimportant, or rarely retrieved.

If data becomes obsolete or inconvenient for active retrieval, prefer:

- archive,
- supersede,
- deactivate,
- hide from retrieval,
- rebuild derived indexes,

rather than destroying canonical evidence.

Explicit user-requested deletion remains possible, subject to ownership/scope and confirmation rules.

### Planned / decided — provider/frontend independence

Conversation history must survive:

- LLM replacement,
- cloud-to-local migration,
- frontend replacement,
- TTS/STT replacement,
- embedding/index replacement,
- OS/hardware migration.

No provider-specific session identifier should become the only copy of conversation continuity.

### Implemented / current — scalable History browsing

A multi-year archive cannot be rendered as one TMP hierarchy. Unity's derived
History viewer therefore navigates Year -> Month -> Day -> bounded/paged
messages and instantiates only the selected page. Large days have older/newer
navigation, non-renderable empty legacy records are skipped in the derived
view, and visible updates affect only the current index/page. Canonical
conversation is unchanged.

Future search should jump directly to a date/range without populating the
archive first. Transport-side history pagination remains a compatible future
optimization because the initial snapshot can still carry more canonical
records than the current page displays.

### Implemented / current — timestamps

Canonical messages currently retain timestamps.

### Planned / decided — temporal usefulness

Timestamps are not decorative metadata. Future systems should be able to reason about when an event occurred, how long ago it happened, whether it is still likely to be current, and whether a temporary state has expired.

### Implemented / current — Log presentation decisions

The Conversation Log is a secondary history/backlog surface. Current product behavior uses:

- one date separator per local day,
- times on individual entries,
- a distinct "Older history — date unavailable" grouping for legacy undated entries.

The log is not a second canonical history store.

Canonical/local conversation state updates immediately even when History is
hidden. The lightweight date index updates incrementally; no lifetime-scale
row hierarchy is rebuilt from each message event. Do not regress this into a
single full-archive TMP view.

---

## 5. Memory architecture

Memory is the central long-term differentiator and requires stronger boundaries than a generic vector database.

### 5.1 Current authority: Memory V1

**Implemented / current:** Memory V1 is the broad/general learned-memory
authority and remains prompt-facing in the current runtime. It persists durable
memory JSON, validates data, uses atomic save behavior, and supports
provenance/source-aware extraction. Separately governed V2 lanes have only
their closed structured authority described below. Neither lane replaces the
canonical conversation archive, and neither may be casually relocated or
rewritten.

**Implemented / current:** assistant-generated statements are not automatically treated as evidence of user facts. User-message evidence is the important current integrity boundary.

### 5.2 Memory V2: current boundary and intended breadth

**Implemented / current:** Memory V2 is character-scoped SQLite structured
continuity infrastructure. Memory V1 remains broad/general prompt-facing memory
authority, while validated V2 lanes have narrower current production authority:
Truth Scope, Active State, Open Threads, closed-schema durable facts, and
source-grounded derived episode context. Generic V2 retrieval remains
non-authoritative/fail-open. A Memory Viewer/Editor and explicit promotion
evidence remain required before any broader authority.

Raw conversation is canonical. Claims, evidence, lifecycle records, FTS/embedding/ANN indexes, and scene state are derived/rebuildable views; they never justify deleting source history.

V2 now also owns a narrow provider-neutral working-context seam for derived
episode accounts. Complete canonical exchanges are deterministically segmented
into source-ranged, versioned, rebuildable lower episodes while the recent raw
suffix remains verbatim and passes through Context Hygiene. This is derived
context, not general V2 prompt authority: invalid caches fail open, provider
switching does not reset continuity, and no summary is inserted into canonical
dialogue or promoted to Memory V1 truth.

**Planned / decided:** 1.0 continuity has distinct layers that must not be collapsed into one salience score or vector search:

- durable facts and corrections;
- sparse deterministic Active State / current scene;
- recent/session continuity;
- open or unresolved threads where appropriate;
- important shared episodes;
- a separately designed relationship-state layer if and when that deferred
  subsystem is explicitly resumed.

Ordinary vague ancient low-value detail may be best-effort or gracefully forgotten. Durable/core facts, explicit retention, corrections, and important recorded continuity must not fade merely because they are old or unrepeated.

### 5.3 Durable facts and episodes

**Implemented / current governed lane:** The durable vertical path covers a
small closed set of explicit real-world identity/profile, residence,
occupation/school, device, pet, long-running project, preference,
recurring-interest, and ownership facts. Canonical user evidence, validation,
truth scope, correction/supersession, bounded lookup, and typed admission remain
authoritative. This is intentionally not an arbitrary ontology or universal V2
retrieval system.

**Planned / decided:** stable facts and shared episodes remain different concepts. Important shared episodes should remain retrievable; perfect recall of arbitrary vague lifetime episodes is post-1.0 research, not a release gate. Historical corrections preserve predecessor evidence but cannot answer ordinary current-truth queries.

**Implemented / current episode foundation:** Lower episode accounts preserve a
bounded neutral narrative plus at most six distinctive source-grounded
continuity anchors. Rebuild verifies anchor retention, permits at most one
deterministic refinement, and uses a bounded source-grounded fallback with
metadata if verification still fails. Local derived requests use deterministic
provenance seeds while live Local dialogue uses fresh system-random seeds.

Optional contiguous-era accounts must pass a separate retention verifier
against every covered lower account. Any missing/uncertain detail rejects the
era and keeps the lower summaries; zero selected eras is valid. The public
matched-seed historical harness is the regression gate for continuity, topic
adherence/reversion, poison recurrence, coherence, and prompt cost. Fixture
results do not make derived summaries canonical facts.

### 5.4 Active State / current companion scene

**Implemented / current:** Active State is the sparse, mention-driven current
reality for the user, persistent companion, shared/current context, and
materially mentioned scene subjects. It is not ordinary learned memory,
relationship state, avatar configuration, or an inventory/world simulator.

The generic mutation contract supports:

- establish/create subject;
- set an attribute;
- establish or clear a relation;
- replace a subject/relation;
- transfer a subject between actors;
- set an explicit location;
- correct current state;
- retire a current/dormant subject;
- explicitly reactivate an eligible distinct retired subject.

`set attribute` is not `replace subject`. “Your blue hat is red now” changes
the current hat's color. “Replace your blue hat with a red one” closes the old
current wear relation and establishes a distinct replacement while preserving
history. Complete multi-update batches validate before one transaction.

Subjects use opaque backend-generated `scene-<uuid>` IDs, are not global
identity records, and retain independently evolving governed attributes such as
kind, location, state/activity, condition, color, wearer/holder projections,
wetness, and stains. Internal IDs never enter normal prompt or UI text.

For ownership/attachment lifecycle, current semantic relations are the primary
authority. `holding`/`carrying` and `wearing` relations own holder/wearer truth;
`held_by` and `worn_by` are compatibility projections for bounded scene
snapshots and older consumers. Every governed scene mutation synchronizes those
projections in the same store transaction. A release closes the holding relation
before an explicit location is established, and a parity failure rejects the
transaction instead of leaving two current truths that disagree.

Updating one attribute never erases unaffected facts. A white shirt worn by the companion may later gain `wet=true` and `stain=wine`; its color and wearer remain current unless changed. Likewise, a user's kitchen location can survive food cooking/burning/plating updates.

#### Sparse, mention-driven boundary

Track only facts explicitly established as currently true, materially relevant objects/entities mentioned in conversation, and conservative immediate consequences strongly implied by an explicit event. Preserve unaffected current facts across turns.

Do not invent unmentioned details, simulate rooms/physics/time, infer off-screen consequence chains, model everything that plausibly exists, or build a game-world/ECS simulation. The test is whether a normal conversational companion would keep the fact in mind because it was mentioned or immediately implied; if it requires imagining unseen details, omit it.

#### Actor scope and authority

Active State naturally covers both actors:

- user location/activity and relevant conditions/objects;
- companion location/context, activity, appearance/outfit state, and relevant conditions/objects;
- shared/current context when mentioned;
- bounded scene subjects such as food, clothing, drinks, or devices.

Assistant-generated free-form prose must never establish authoritative state merely because the model said it. Trusted state must come from canonical user evidence, governed backend/app-avatar transitions, or another explicitly approved runtime source.

#### Temporal and lifecycle semantics

Every current attribute has a validity start/end. `valid_from_us` marks the value start; replacement, clear, or retirement closes it through `valid_to_us`; `last_confirmed_at_us` is derived from later explicit confirming user evidence; elapsed duration is derived at read time. Reasserting the same normalized value adds confirmation evidence without replacing the claim or resetting its start.

Time never autonomously mutates state. Wet clothing does not silently dry, food does not cool/spoil, and people/objects do not move merely because time passed.

Scene subjects have three non-destructive lifecycle states:

- **current** — has an active relation/attribute/capability dependency;
- **dormant** — has no active relation but remains within the bounded salient
  working roster;
- **retired** — preserved historically but absent from ordinary current
  prompt/UI/reference resolution.

Active relation sources, capability causes, and profile defaults cannot retire.
The dormant roster is bounded; least-recent eligible generic subjects retire
conservatively before distinct named/qualified subjects. Explicit strong
identity can reactivate a distinct subject. A generic old cup, book, box, or hat
must not resurrect merely because it is the only historical noun match.

#### Proposal/application boundary

Active State proposals are bounded and may target governed actor/global state, introduce a scene subject, update an existing current subject, clear an attribute, or retire a subject. Backend validation is authoritative: registry/attribute/type checks, canonical persisted evidence, same-character scope, and atomic application happen before state changes.

Proposals distinguish `explicit` updates from `immediate_consequence` updates.
The latter require a narrowly governed consequence rule, remain tied to an
explicit trusted event, and cannot become authority for another inferred
consequence. Deterministic or bounded closed-schema semantic extraction may
only propose; it never writes state directly.

#### Relation-semantic capability derivation

Current relations retain target actor/scene subject, optional body facet and
side, predicate, cause, optional stable subject/quantity/equipment semantics,
scope, evidence, and validity lifecycle. Capability effects derive from that
meaning, not merely from the object's name.

Examples:

- a sparkly scrunchie worn on a wrist is decorative and does not occupy a hand;
- a wrist tethered to a pole constrains the affected side/reach;
- rollerblades carried are not rollerblades worn;
- a blindfold held is not a blindfold covering eyes;
- a wheelchair nearby is not a wheelchair in use.

A conventional blindfold applied over the eyes is a bounded known equipment
semantic and directly establishes vision unavailability. A later explicit
coverage statement for that same blindfold confirms/refines the same logical
application; it does not create another independently removable blindfold
cause.

The shared derived envelope currently covers:

- perception: vision, hearing, smell, taste, and touch, each
  normal/constrained/unavailable;
- communication: speech normal/constrained/unavailable;
- manipulation: bounded side-aware hand and arm availability;
- locomotion: semantic mode plus constraint;
- awareness: normal/reduced/asleep;
- posture: standing/sitting/lying when established.

Sparse environmental causes record only explicit interaction consequences,
such as pitch darkness preventing sight, smoke constraining vision, or loud
music preventing hearing. AIFren does not compute physics, acoustics, light,
collision, pain, or medical consequences. Sparse body availability likewise
records only explicit bounded facts; a missing limb does not automatically
invent every downstream disability, and explicitly established assistance can
alter effective capability.

Unavailable hearing has one bounded information-authority consequence. When
the interaction explicitly establishes that a user utterance is delivered
through that unavailable channel, canonical evidence is preserved but the new
content is excluded from understood model context, Memory V1 semantic
processing, Memory V2 shadow processing, durable-fact admission, Open Threads,
summaries, and episode compaction. Hearing restoration does not reveal it
retroactively. `constrained` hearing remains usable with limitations; AIFren
does not simulate acoustics, partial transcripts, or treat all typed text as
spoken by default.

Multiple causes compose independently and deterministically. Removing one eye,
mouth, hearing, hand, or locomotion cause removes only that contribution. The
final cause removal restores the capability immediately. Derived effects are
not persisted as duplicate memory facts.

**Capability unavailable does not mean response unavailable.** Removing one
channel leaves every other valid channel available for characterful VN-style
reaction. Speech unavailable still permits gaze, expression, gesture, posture,
and physical action when their capabilities allow it. Fallback means
generation/validation failure; it is not the normal representation of a
constrained character.

Posture stores only `standing`, `sitting`, or `lying`. Location/support is a
separate relation: “lying on the couch” conceptually establishes `lying` plus
a couch relation, never posture `couch`. The compact overlay need not surface
every such fact; Scene Details remains the fuller inspection surface. Likewise,
a tether has bounded default effects, while explicit user evidence such as an
inability to move away may strengthen locomotion without distance/physics
simulation.

#### Planned / decided — extensible scene loci

This direction is **not fully implemented**. Future relation storage should use
an open vocabulary for body/attachment/scene loci while keeping the important
capability consequences closed and validated. Common built-ins such as eyes,
ears, mouth, head, neck, arms, wrists, hands, torso, waist, legs, and feet
remain valuable deterministic anchors. Evidence may also preserve normalized
custom loci such as finger, left index finger, ankle, toes, nose, hair, tail,
horns, wings, or character-specific anatomy.

An unknown/custom locus should normally degrade to a valid scene relation with
no automatically inferred capability effect, rather than rejecting the entire
mutation because the locus is absent from a global enum. Conservative parent
normalization may map `left index finger` to `left hand` or `ankle` toward a
leg/foot family when evidence is sufficient; it must not guess aggressively.
Character-specific anatomy may eventually seed known loci without adding
global enums.

Capabilities continue to derive from relation semantics and explicit
consequences, not the locus name. A ring worn on a finger is decorative; a
trapped finger plus explicit inability to use the hand may constrain manual
capability. A scrunchie worn on a wrist is not a wrist tether, and a tail ribbon
does not require a global “tail equipment slot.” This is a body/attachment/scene
locus model, not game inventory or equipment-slot simulation.

#### Response authority boundary

A validated user-evidence mutation is applied before response generation so
context assembly, capability validation, and narration share the same
post-mutation snapshot. It creates a **must-respect** response requirement: the
model may react naturally or omit the changed fact, but may not question,
reverse, or contradict it. Only a bounded direct current-state question creates
a **must-communicate** requirement whose authoritative answer must actually be
rendered. These are separate typed modes; an ordinary mutation must never force
a mechanical restatement merely to pass validation.

The authoritative response envelope permits canonical dialogue as its minimal
form. Response mode, spoken projection, presentation, and companion-action
metadata remain optional unless a capability or action requires them; supplied
metadata is strictly validated, while safe defaults are derived backend-side.
This keeps local-model formatting fallibility separate from state and
capability enforcement.

Direct deterministic queries receive one bounded repair and then a
state-derived factual fallback. Unknown is not none: lack of authoritative
attire, holder, activity, location, or capability evidence cannot become a
negative claim. Any fallback that states a scene fact must derive it from the
same authoritative snapshot.

Several independently recognized direct questions in one user turn may create
several `must-communicate` facts. The model may answer naturally without a fixed
order, but every fact remains required. Ordinary replies have a soft preference
near 100 words and no general hard ceiling. Constrained/nonverbal reactions
target roughly 50--80 words and have a bounded 100-word validation ceiling plus
one concise repair; no path raw-truncates canonical dialogue.

The same composed capability envelope governs normal constrained turns, sleep,
response validation, companion actions, semantic presentation, gaze,
gesture eligibility, and spoken projection. No constrained mode may bypass
another active domain.

#### Scene UI interaction versus administrative correction

The optional compact Current Scene overlay is an immersive interaction
surface. Its X sends only an opaque snapshot-relative token/revision/scope.
After validation, the backend applies the mutation and recomputes capabilities
before publishing a snapshot or generating prose. It then constructs one
deterministic natural user-side scene event, stores it canonically with
`scene_ui/generated_event` origin, and offers one reaction opportunity from
post-mutation state. Generated event prose is narration, not evidence, and is
never reparsed into state. Rapid actions cancel or suppress stale
contradictory reactions.

Event wording follows the accepted operation and predicate/semantic family
before body facet. Removing decorative wristwear therefore cannot become a
restraint-release event. Snapshot conversation projection and delayed event
echoes use stable canonical message identity; deduplication is never based on
text, so legitimate identical messages remain distinct.

Detailed Scene Details/debug correction remains a separate silent operation:
it changes authoritative current state without creating an in-world canonical
event or companion reaction. Both operations preserve canonical evidence and
close causes rather than editing derived capability values.

#### Bounded companion actions

The model may propose a closed companion-only activity, posture, one current
wear/remove or hold/release action, or supported equipment use/dismount. The
backend validates actor, subject, scope, lifecycle, capabilities, and operation
before applying it. Rejected or failed actions cannot be narrated as completed.
Ordinary prose remains non-authoritative, and this is not a general autonomous
agent loop.

#### Truth / scenario scope

**Implemented / current:** Schema v12 adds a small character-scoped truth-scope boundary. Every character has one stable backend-derived `real_world` scope and may have bounded persistent `scenario` scopes with opaque IDs and non-authoritative compact labels. A scope is created, activated, or deactivated only through governed canonical user evidence; a model cannot silently create or switch one.

Active State (including scene subjects/attributes) and Open Threads are scoped. Current reads use only the active scope; inactive scenario continuity remains stored, historical, and available again when that same scope is resumed. Existing V2 rows migrate safely to the default real-world scope. Durable facts are real-world only: roleplay claims cannot overwrite real identity or biographical facts.

Activation/deactivation completes before context retrieval and response
generation. The first response after leaving RP therefore uses the restored
real-world Active State and Open Threads, not inactive scenario state as current
truth.

Scope is a truth boundary, not an expiry system. Long-lived active state stays active until changed, cleared, retired, or its scope is made inactive; age, repetition, context rollover, and elapsed time never promote it to durable memory, delete it, switch scope, or cross it into real-world truth. Stable identity may still be used when relevant in a scenario, while scenario-specific scene state and threads must not leak to ordinary real-world context.

The model remains sparse and generic. The current governed scene attributes can preserve independently changing properties but do not provide arbitrary RPG modifiers, inventory mechanics, an entity graph, or a game/world simulation.

### 5.5 Evidence, corrections, and trust

Derived claims require inspectable source/evidence where practical: character, canonical event, user/trusted actor, evidence role, timestamps, lifecycle, and supersession history. Assistant text alone is not user-fact or companion-state authority.

Explicit user corrections are high-authority evidence. Preserve prior evidence/history as superseded or historical rather than destructively rewriting it; old values cannot answer ordinary current queries.

### 5.6 Retrieval, admission, and prompt safety

Use the explicit pipeline:

```text
lookup → relevance/selection → admission → typed rendering
```

A lookup hit is not prompt admission. Retrieved memory/state is background evidence, not an instruction, compulsory topic, or personality override. Typed rendering uses governed validated values, never raw claim text, source excerpts, hashes, IDs, or ordinary provenance internals. The latest explicit user turn overrides stale remembered assumptions.

### 5.7 Accessibility and forgetting

Accessibility is not importance, and forgetting is not deletion. A low-salience old detail may be unavailable without damaging canonical history. Important/core continuity must be evaluated separately from acceptable abstention on mundane ambiguous history.

### 5.8 Memory Viewer / Editor

**Planned / decided:** A trustworthy Memory Viewer/Editor is required before a more ambitious Memory V2 becomes authoritative.

It must scale to lifetime-sized data and therefore must not render/load everything at once.

Expected capabilities include:

- bounded browsing,
- search,
- filters,
- provenance/source inspection,
- character scoping,
- correction,
- supersession/deactivation,
- cautious deletion,
- clear distinction between canonical events and derived memories.

A memory UI should not expose internal embeddings or implementation details as the normal user mental model.

### 5.9 Deletion

Destructive memory operations obey the project-wide ownership/scope invariant.

Prefer reversible operations such as archive, supersede, or deactivate where they satisfy the user intent. Irreversible deletion should be explicit and scoped.

Deleting a derived memory must not implicitly delete its source conversation. Deleting a character may delete character-owned memories after confirmation, but must not delete unrelated/shared assets.

---

## 6. Relationship state

### Explicitly deferred

Relationship state is distinct from:

- base personality,
- factual/profile memory,
- episodic memory,
- transient mood.

It represents accumulated relational context between a specific character and a specific user.

Interactions may influence relationship state. Negative experiences can matter and may persist, but the system must resist twitchy or runaway change.

A durable negative shift should generally require:

- a clear significant event,
- repeated evidence,
- an explicit relational event,
- or another contextually strong reason.

A single ambiguous remark, weak sentiment classification, or model interpretation must not create permanent hostility, resentment, personality change, or relationship damage.

Relationship state may later affect emotional thresholds and reaction strength. Major changes should be gradual/contextual rather than a meter that jumps every turn.

There should be no overt game-like relationship meter in the ordinary experience.

### Implemented / current boundary

No authoritative relationship-state subsystem exists yet. Current memory/history can preserve relationship-relevant events, but that is not the same as a dedicated relationship state.

### Undecided

The exact representation, dimensions, update algorithm, and user-editing surface are not settled.

---

## 7. Time and temporal awareness

### Planned / decided

A long-lived character needs explicit real-time continuity. Future context/memory systems should understand:

- current real-world time,
- elapsed time since the last interaction,
- when memories/events occurred,
- how long ago a shared event happened,
- whether a fact was true only during a past period,
- session-limited versus long-lived state,
- explicit current-state lifecycle,
- continuity across days, months, and years.

Temporal awareness should help the character distinguish:

- "this is true now,"
- "this happened yesterday,"
- "this used to be true,"
- "we have not talked about this for months."

This should be built from reliable timestamps/state semantics rather than asking the LLM to guess chronology from a bag of semantically retrieved memories.

### Implemented / current

Canonical conversation timestamps exist. Direct local time/date requirements,
validity intervals, correction/supersession, and the current/dormant/retired
Active State lifecycle are implemented. AIFren does not autonomously expire
scene facts based on guessed physics or elapsed time, and broad temporal
reasoning over arbitrary history remains future work.

---

## 8. Dialogue and roleplay presentation

### Implemented / current — canonical response versus presentation

The backend owns the complete canonical assistant response. Presentation effects must not change what is persisted as the actual response.

Conversation persistence is independent of whether Unity is visible, whether dialogue reveal is active, whether the main UI is hidden, or whether hidden subtitles are enabled. Presentation failure must not cause canonical dialogue to disappear from history.

### Implemented / current — typed dialogue markup

`DialoguePresentationParser` produces typed spans:

- `PlainText`,
- `Emphasis`,
- `Emote`.

The current paired-markup semantics are deliberately explicit and mirrored by the Unity presentation parser and backend TTS cleaning:

**Single paired `*...*`:**

1. an action-shaped or standalone roleplay segment is `Emote`;
2. otherwise, emphasis embedded in surrounding spoken dialogue is `Emphasis`,
   regardless of its word count.

**Double paired `**...**`:** `Emphasis`, unless it is nested inside an outer
action that already owns the whole nonspoken span.

`Emphasis` remains spoken content: its markers are removed for TTS and it is presented as spoken emphasis, currently italicized in visible dialogue.

`Emote` is presentation/action content: it remains visible in normal dialogue with distinct styling, is omitted from TTS and hidden spoken subtitles, and may feed semantic gesture mapping.

An outer roleplay action remains nonspoken even when it contains nested
single- or double-asterisk emphasis. Inner formatting cannot prematurely close
the outer action span. Ordinary emphasis outside an action remains spoken.
Whole-response and incremental speech projections share this deterministic
policy so action prose cannot leak into TTS at streaming boundaries.
Generated physical actions are requested in `*...*`; parentheses remain
ordinary parenthetical prose and are not universally reclassified as actions.

Generated assistant emoji are removed at the assistant-output boundary before
canonical persistence and presentation. User-authored emoji remain untouched,
and ordinary Unicode text, including Japanese text and punctuation, is not
treated as emoji merely because it is non-ASCII.

The canonical raw assistant response remains unchanged. Markup interpretation is a presentation derivative rather than a rewrite of history.

### Implemented / current — deterministic hidden subtitles

Hidden subtitles are structurally downstream from the authoritative TTS/PTT lifecycle.

`HiddenSubtitlePresenter` is the single production owner of hidden-subtitle presentation state, including:

- current page/range ownership,
- renderability,
- `CanvasGroup` alpha,
- page text,
- TMP word visibility,
- page transitions,
- `timingDue` versus `presentationShown` state.

It is a deterministic Tick-driven state machine rather than a collection of competing presentation coroutines.

Pagination and reveal obey hard ownership invariants:

- pages advance by the **actual adjusted page word count**, not the requested maximum;
- flattened page ownership maps every global spoken word ID `0..N-1` exactly once and in order;
- actual presented global word IDs are likewise `0..N-1` exactly once and in order;
- no page may create a gap, overlap, duplicate, or skipped word;
- page-local reveal state starts from zero for each page and maps back to the page's assigned global range.

`timingDue` and `presentationShown` remain distinct. A word may become due while a page is not yet renderable; that does not permit the word to be silently consumed without visible presentation.

A temporary edge/input UI peek suppresses hidden-subtitle rendering without cancelling the active subtitle session. Restoring the hidden UI reconstructs the current page/progress. An explicit committed **Show UI** action cancels hidden subtitles.

Provider word timing is optional. Valid one-to-one timings may improve synchronization, while missing/invalid timing uses a deterministic fallback plan. Subtitle timing, pagination, fade, and reveal must never gate PTT readiness, speech interruption, synthesis cancellation, playback startup, or backend readiness.

Configured WPM/WPS is the maximum normal visual reveal speed. Audio alignment
may make reveal slower, but a short or empty spoken projection cannot accelerate
a long action-heavy response past the user's reading preference. Explicit
instant-text mode remains authoritative.

### Implemented / current — stable interpretation boundary

Within each runtime projection, dialogue is parsed once into stable typed spans
and display, speech, subtitle ownership, pagination, and streaming decisions are
derived from that representation. Python and Unity intentionally mirror the
same bounded policy and cross-projection tests guard their equivalence rather
than allowing each downstream consumer to invent an asterisk heuristic.

The longer-term direction remains more explicit structured
response/presentation metadata while retaining canonical natural-language
response text. Raw punctuation conventions must not become an unbounded
protocol.

---

## 9. Avatar and visual identity

### Implemented / current — direct avatar rendering

AIFren currently uses Unity/UniVRM for desktop companion presentation. The normal/default presentation architecture is:

```text
viewer background
-> directly rendered VRM
-> Screen Space Overlay UI
```

The old RenderTexture avatar path is retained only as rollback/debug behavior where available. It is **not** the planned future framing architecture and should not be revived as the normal solution to avatar crop/quality problems.

Direct rendering keeps the full avatar available to the scene and avoids raster magnification/crop blur caused by treating a pre-rendered avatar texture as the primary framing mechanism.

### Implemented / current — Avatar View

Avatar View provides presentation controls without changing character identity or destructively cropping the model:

- drag/presentation X and Y,
- zoom/scale,
- synchronized controls for editing,
- Save/Cancel/Reset behavior,
- independent portrait and landscape presentation state.

The durable rule is that avatar framing is a **presentation transform**, not character data and not a destructive alteration of the VRM.

### Implemented / current — managed visual assets

Avatar models and backgrounds are global managed visual assets rather than
character identity. Imported assets are copied into AIFren-owned managed
storage with stable identity, friendly naming, and previews/thumbnails where
available. Each character stores only a local stable reference to its selected
managed avatar; background, lighting, and framing preferences remain global.

A visual asset may be referenced by a character without becoming character-owned. Changing the avatar or background must not reset or rewrite personality, memory, history, relationship, or voice.

### Implemented / current — VRM compatibility direction

UniVRM loading supports VRM 1.0 and VRM 0.x migration paths. Compatible humanoid VRMs should use the same general presentation and animation abstractions rather than requiring per-character hard-coded bone names.

Missing avatar capabilities should degrade gracefully where possible.

### Implemented / current — background system

Background selection is independent of Light/Dark UI theme.

Current defaults include:

- portrait: **Light neutral**;
- landscape: **Bedroom**.

Portrait and landscape remember independent active background selections. Custom images use automatic aspect-cover presentation with slight physical-pixel overscan to avoid visible seams.

There is no automatic clock/day-night background cycle in the decided product direction, and users should not be forced through a crop editor simply to use a normal background image.

### Planned / decided — arbitrary compatible avatars

The long-term user experience should support importing a standards-compliant compatible humanoid `.vrm` and having AIFren adapt automatically as far as that model permits.

The current character model is not assumed to be final. Animation, expression capability discovery, presentation settings, and other avatar-facing systems should remain portable across compatible VRMs.

### Exploratory / parked

Generic GLB support is parked. VRM is the intended avatar format for the foreseeable core companion presentation.

---

## 10. Animations, gestures, and expressions

### Implemented / current — semantic gesture backbone

AIFren now has a semantic gesture layer rather than coupling language-model output directly to clip names.

Implemented components include:

- `AvatarGestureIntent`,
- `AvatarGestureMapper`,
- `AvatarAnimationController`.

Current semantic intents include:

- Nod,
- HeadShake,
- Wave,
- Shrug,
- HeadTilt,
- Thinking.

The first supported deliberate gesture per assistant response may be selected from parsed action/emote content. Unsupported actions remain textual/presentational rather than forcing an unrelated physical animation.

The controller uses standard Humanoid bone mappings where practical, preserves blink/lip-sync as separate presentation layers, restores captured base rotations, applies eased procedural motion, and uses cooldown/suppression rules to reduce repetitive gestures.

### Implemented / current — manual quality status

The semantic architecture works, but the current procedural motions are not considered finished animation quality.

Current manual QA status:

- Nod triggers and is acceptable;
- HeadShake triggers but remains somewhat choppy;
- HeadTilt triggers;
- Shrug triggers but looks robotic;
- Thinking triggers but looks robotic;
- Wave still needs trigger/visibility reliability work.

This distinction matters: the **gesture abstraction is implemented**, while the visual quality of several gesture implementations remains unfinished.

### Planned / decided — preserve semantic intent when animation sources change

Future animation improvements should keep `AvatarGestureIntent` as the stable semantic boundary. The actual implementation behind an intent may later be:

- an authored Humanoid clip,
- a procedural fallback,
- an avatar-specific capability mapping,
- or another compatible animation source.

The LLM should not need to know Unity clip filenames or model-specific bone names.

### Current direction — authored animation evaluation

AIFren prefers authored **VRMA** animation where practical for portable VRM humanoid body animation. Native VRMA is a presentation format, not a new semantic API: `AvatarGestureIntent` remains the behavior boundary and a VRMA filename/path must never reach backend or LLM behavior.

Procedural gestures remain useful fallback, debug, and micro-motion mechanisms even if VRMA becomes the preferred polished authored format. Standard/default mappings should be reusable across compatible VRMs; optional per-character overrides are a future extension, not a requirement to maintain complete animation libraries per character.

Gesture VRMAs should normally be body-focused. Body gesture, facial expression, lip sync, blink, and gaze are separately arbitrated channels so a deliberate gesture can coexist with AIFren-controlled face/speech presentation.

Successful VRMA loading is not, by itself, proof of portable visual compatibility. Before shipping an authored animation, validate its normalized Humanoid/rest-pose behavior on multiple compatible target VRMs and prefer assets with predictable, portable pose data.

Conversational authored gestures should normally be in-place: preserve the avatar's world/root placement unless an animation is explicitly classified as locomotion or another intentional full-body repositioning action.

For UniVRM runtime VRMA playback, create the runtime ControlRig while the imported VRM is still in its reference pose, before applying AIFren's presentation-only relaxed pose. Conversational FullBodyInPlace playback uses one captured AIFren presentation hips baseline for entry, playback, and exit. Source reference placement must not reposition the character, while authored hips motion remains relative to the VRMA reference pose; this permits a gesture that intentionally starts crouched to preserve that crouch. Authored body gestures should transition into and out of that persistent baseline gracefully; these body transitions must remain separate from face, lip sync, blink, and gaze ownership.

The next animation-quality step is to investigate **free/permissively licensed authored animation assets** that can legally be redistributed in a future public/commercial release.

Before integrating a source, verify:

- redistribution rights,
- commercial-use rights where relevant,
- attribution requirements,
- compatibility with the intended Unity/VRM Humanoid pipeline.

No specific authored-animation source is selected yet.

### Planned / decided — restraint and cross-avatar behavior

Initially, use at most **one deliberate gesture per response by default**. Not every response needs one. Repetitive motion is worse than occasional meaningful motion.

The same semantic gesture set should work across compatible humanoid VRMs where practical. Missing capabilities should degrade gracefully.

Avatar compatibility is determined from embedded glTF metadata, not a filename extension alone. A `.vrm` or `.glb` container with top-level `VRM` (VRM 0.x) or `VRMC_vrm` (VRM 1.0) metadata is a valid VRM avatar input and follows the shared VRM loader. A plain generic GLB remains unsupported; AIFren does not infer a humanoid rig or construct one for arbitrary GLB content.

Avatar lighting is global presentation state, not character identity or an
imported-model property. A neutral default lighting baseline should remain
model-agnostic, preserve texture/material and white-clothing detail, and keep
the avatar readable over different 2D backgrounds. Per-model lighting fixes
are not the default compatibility strategy.

### Planned / decided — layered presentation model

The intended expression/animation model has three conceptual layers:

1. **persistent mood baseline** — longer-lived emotional presentation;
2. **transient response facial expression** — response-specific facial state;
3. **brief body gesture/animation** — a deliberate physical gesture.

A brief gesture can finish while the facial expression or mood presentation remains.

Deliberate gestures are temporary presentation events: they return to baseline presentation after completion. Each assistant response has zero or one deliberate semantic gesture by default; multiple deliberate gestures are not queued within one response unless a later explicit design decision changes that rule.

### Implemented / current — avatar expression capability layer

Unity now enumerates the expression capabilities exposed by the active compatible VRM, including preset and custom expressions, and can apply a concrete expression at a continuous weight. This remains a presentation-layer capability: backend and dialogue code do not select blendshape indices, raw morph names, or asset-specific expression identifiers.

Expression capabilities retain their VRM categories. Mouth/vowel and blink presets are available avatar capabilities but are not treated as semantic emotions; arbitrary custom names are likewise model capabilities rather than LLM vocabulary. The procedural look-direction presets are driven by UniVRM's separate LookAt runtime, not by persistent-expression weight selection. UniVRM's expression override rules continue to arbitrate blink, mouth, and look-at behavior, allowing a persistent facial expression to coexist with those channels unless the active VRM expression explicitly overrides one.

A selected visible expression blends between weights and remains until it is explicitly changed or cleared. It is not timed like an emote, and body-gesture completion does not reset it. Switching models safely clears the previous model's concrete expression and enumerates the new model instead.

### Planned / decided — initial semantic expression vocabulary

A useful initial semantic expression vocabulary is expected to include concepts such as:

- neutral / relaxed,
- happy / smile,
- sad,
- angry / annoyed,
- surprised,
- embarrassed / shy,
- concerned,
- thinking.

The dialogue response path may carry this bounded semantic emotion/intensity vocabulary and one optional semantic gesture in the same inference that produces dialogue. Python keeps that metadata frontend-neutral alongside the authoritative response; Unity resolves it through the active avatar's available capabilities rather than addressing concrete morphs directly. An absent emotion preserves the current persistent visible expression, while explicit neutral clears it.

### Exploratory / parked

More elaborate gaze, authored idle sets, richer gesture libraries, and detailed physical interaction are later work after the immediate gesture/expression foundation is satisfactory.

---

## 11. Emotion and mood design

### Implemented / current — metadata from the same response

The unified assistant response contract may carry optional bounded semantic
presentation metadata in the same inference as canonical dialogue. Current
fields cover a closed emotion/intensity vocabulary and an optional semantic
gesture; models do not address avatar blendshape or animation filenames.
Canonical dialogue alone remains a valid minimal response, and the backend
derives safe presentation defaults when optional metadata is absent.

An absent/no-change emotion update is valid. Each assistant turn may change the persistent visible facial expression/intensity or intentionally leave the current expression unchanged. Emotion semantics remain independent of VRM blendshape/expression names; Unity maps semantic emotion onto the active VRM's available expression capabilities.

The expression decision should represent the character's reaction to the full conversational context: the user's message, character personality, relevant relationship/context, previous facial/presentation state, and the response being produced.

### Planned / decided — visible expression persistence

Emotion should not flip arbitrarily every sentence.

Desired behavior:

- the previous expression remains while waiting for interaction;
- after the user interacts, the dialogue-and-metadata result updates the expression or leaves it unchanged;
- that resulting expression persists through speech completion and while awaiting the next interaction;
- transient gesture completion does not require facial expression to reset;
- expression transitions should blend smoothly when technically practical.

Variety is desirable, but random twitchiness is not.

The intended lifecycle is:

```text
previous expression remains while waiting
    -> user interacts
    -> LLM produces dialogue + presentation metadata
    -> expression updates or remains unchanged
    -> speech begins
    -> zero or one deliberate gesture occurs
    -> gesture returns to baseline
    -> speech ends
    -> resulting expression persists while awaiting the next interaction
```

### Planned / decided — future mood remains separate

Persistent mood is separate from the currently visible expression. Mood may later bias reaction thresholds, expression transitions, and reaction strength, but it is not required for the first expression implementation.

### Planned / decided — relationship to personality

Emotion/mood is not permission to rewrite personality.

Personality influences **how strongly and how easily** a character reacts. Mood is a temporary state. Relationship state can bias later reactions. These concepts should remain distinct.

### Implemented / current boundary

There is no complete authoritative mood system. Persistent mood, richer
semantic vocabularies, and autonomous gaze remain future work; the current
bounded response-presentation metadata is deliberately presentation state, not
mood, relationship state, or character-memory authority.

### Undecided

Exact mood dimensions, decay mathematics, persistence storage, prompt format, and correction/editing semantics remain unsettled.

---

## 12. Asterisk emotes and animation

### Implemented / current

The current dialogue parser provides typed `Emote` spans from the settled single-asterisk heuristic described in Section 8. Those emote spans are available to `AvatarGestureMapper` without changing canonical response text.

The current gesture mapper scans meaningful action language and may select the first supported deliberate semantic gesture for the response.

### Planned / decided

The boundary remains semantic:

- supported actions can map to semantic gestures;
- unsupported physical/world actions remain text-only/presentation-only;
- the LLM does not select raw animation clip names;
- initially, at most one deliberate gesture is used per response;
- a textual action does not create an obligation to animate something physically impossible in the current presentation.

Over time, structured response presentation metadata should reduce dependence on heuristic action detection while retaining compatibility with natural asterisk roleplay text.

---

## 13. Voice, TTS, and STT

### Implemented / current — provider boundary

AIFren has provider-neutral model and TTS boundaries. Model choice (Gemini,
another online service, or a local OpenAI-compatible endpoint) never changes
character, history, Memory V2, or context-assembly semantics. Unity is the sole
user-facing frontend. Kokoro is the active production baseline; Piper and the
Tkinter application path are removed. Streamed assistant text remains
presentation-only until one final canonical assistant message is persisted.
Current Kokoro scheduling defaults to bounded complete-sentence early speech.
A separate semantic projection emits the first sentence immediately, groups
small later adjacent sentences, and feeds one synthesis worker and one
continuous per-turn playback stream. Canonical output remains untouched, and a
persisted Unity setting selects the stable `whole_response` fallback. STT is
local-capable and the backend remains authoritative for synthesis/playback
lifecycle. Experimental voice runtimes and protected voice material are not
part of the public repository. Any further speech
scheduling change must preserve exact canonical whitespace, wait for complete
semantic action/emote spans, retain spoken emphasis and interruption identity,
and pass the full ordinary Development-player path rather than only synthetic
benchmarks.

The current Kokoro configuration does not depend on runtime pitch post-processing to force a character voice.

### Implemented / current — TTS result semantics

The durable provider-independent concept is a synthesis/playback result that can provide:

- audio,
- total duration,
- a stable `playback_id`,
- optional word/phoneme/viseme timing information.

Kokoro can expose predicted token/word timing when a trustworthy one-to-one mapping is available. Timing metadata is enhancement data, not lifecycle authority.

Streaming early speech and direct/governed speech use the same ordered resource
manager. A recognized accelerator out-of-memory/resource failure preserves the
unresolved chunk, retries according to the bounded policy, and can move Kokoro
to CPU for the rest of the runtime before retrying that same chunk. Ordering is
exact-once: later chunks cannot skip past or duplicate an unresolved one, and
interruption cancels pending retry/failover work. Classification is
accelerator-neutral across PyTorch CUDA and HIP/ROCm interfaces; ROCm evidence
is injected/architectural until tested on actual ROCm hardware.

The spoken projection removes generated assistant emoji and omits complete
outer action/emote spans, including actions containing nested emphasis.
Canonical/display text and speech projection remain separate. Current STT
resource recovery likewise retries the same captured WAV once on CPU/int8 after
a recognized Whisper accelerator failure and remains on CPU for that runtime.

### Implemented / current — authoritative interruption lifecycle

PTT/audio state is authoritative. The service distinguishes synthesis from active playback and uses generation/playback identity so stale work cannot later become current.

Durable invariants include:

- PTT interruption invalidates current synthesis/playback generation immediately;
- active streaming/playback is aborted without waiting on subtitle presentation;
- interrupted or stale synthesis cannot later start audio;
- natural completion retires the correct active playback exactly once;
- stale completion from an older playback cannot clear a newer playback;
- subtitle timing/events cannot determine whether the backend is ready for another turn.

### Planned / decided — graceful timing degradation

Subtitles/lip presentation should work with providers that expose:

1. exact word/phoneme timings;
2. partial/coarse timings;
3. duration only;
4. no explicit timing metadata.

Better timing should improve synchronization without making a provider unusable when those extras are absent.

### Planned / decided — voice profile

A character-specific **voice profile** is separate from both visual model and durable identity. A voice can be replaced without resetting the person, just as a VRM can.

Generic stock voices remain useful for easy setup, low-spec hardware, fallback, and distributable defaults.

Reference-conditioned/cloned voice systems are desirable as an advanced option because they can provide a more intentional character voice than stock-speaker selection.

### Exploratory

GPT-SoVITS is a future candidate for a reference-conditioned/cloned voice provider. It is not a committed dependency or default. Other suitable systems may be evaluated.

### Planned / decided — pitch shifting

Runtime pitch shifting is not the preferred primary method for achieving a character-specific voice when it produces unnatural results. A suitable source/reference voice or appropriate TTS model is preferred over heavy post-processing.

### Undecided

The final default TTS provider for a mature release is not settled.

The exact cloned-voice workflow, licensing/consent policy for reference voices, and resource budget alongside a local LLM are not settled.

---

## 14. Input, PTT, and interaction

### Planned / decided — voice first, keyboard always available

Voice/PTT is intended to become the primary interaction mode. Keyboard input remains a critical fallback and must stay usable.

### Implemented / current — PTT behavior

Current PTT routes into the same backend turn path as accepted text/transcription. The backend owns speech interruption and voice state.

Current development behavior includes configurable/focused Unity PTT and backend/global-listener support where available.

PortAudio microphone close is a bounded owned-resource operation. Graceful
stop/close remains normal; a close exceeding 250 ms is safely aborted, the
capture is discarded before WAV/Whisper, and capture identity prevents stale
cleanup from affecting a newer attempt. Python threads are never forcibly
terminated. A recovered no-turn voice `ready` explicitly reconciles Unity's
capture-owned Thinking placeholder by restoring the prior dialogue (or clearing
an empty prior state); `turn_started` retires that authority so legitimate
generation cannot be rolled back.

### Planned / decided — VOIP-like PTT

PTT should feel like a normal VOIP client:

- configurable key/button;
- mouse thumb buttons where supported;
- global activation where the platform allows it;
- immediate interruption of current assistant speech;
- clear unavailable-state reporting rather than silently substituting an unexpected binding.

Speech/listening flow must not depend on subtitle presentation.

### Implemented / current — hide UI

Most UI is available by default, with an intentional Hide UI presentation mode.

Typing/Enter may temporarily reveal the input controls needed to type without permanently abandoning the clean presentation.

### Planned / decided — temporary peek versus committed show

A temporary interaction-triggered UI peek and an explicit **Show UI** command are distinct concepts.

Temporary input reveal should not necessarily change the user's committed hide/show preference.

---

## 15. Proactive behavior

### Implemented / current

Proactive behavior is deliberately conservative and user-configurable through a
closed interval set from Off and short QA intervals through six hours. The
selected interval is a minimum opportunity, not a promise that a message will
be sent.

Eligibility considers recent user activity, open/waiting continuity, sleep or
quiet suppression, and unanswered-check-in backoff. Startup has a separate
settling grace after backend, provider, and frontend readiness. Background
generation remains private until a draft is publishable; failed, empty,
overlength, timed-out, or rejected attempts create no visible turn and count
for failed-attempt throttling. Every published turn has a terminal lifecycle,
so startup cannot be left in phantom Thinking.

Published proactive messages alone enter canonical conversation and normal
ignored-check-in tracking. Ignoring one is a weak scheduling backoff signal,
not relationship rejection. Genuine user activity relaxes the appropriate
backoff. Relationship interpretation is outside this subsystem.

### Undecided

Future notification surfaces and richer product-level availability controls
remain unsettled; they must preserve the private-before-publication lifecycle.

---

## 16. UI / UX philosophy

### Planned / decided — companion first

AIFren should feel like a companion application, not a game shell.

Avoid:

- game-style main menus,
- quests,
- relationship meters,
- unnecessary feature screens between launch and the character.

The avatar/background/dialogue are the primary presentation.

### Implemented / current — settings structure

The Unity client already has a broad Settings surface. Technical/debug controls belong behind Advanced/secondary UI rather than dominating ordinary use.

### Planned / decided — future management UX

Future Character Management, memory tools, model selection, voice selection, and asset selection need approachable ordinary-user UX.

Visual asset selection should prefer:

- friendly display names,
- thumbnails/previews where useful,
- clear availability/error states,

rather than raw file paths or internal IDs.

### Planned / decided — ordinary UI should not expose developer internals

Implementation hashes, absolute paths, build fingerprints, protocol details, and similar diagnostics belong in developer/debug surfaces, not normal user-facing presentation.

### Implemented / current — settings transaction rules

Global Settings Save/Cancel should apply to reversible staged settings. Immediate/irreversible actions should not pretend to be staged by the same Save button.

### Implemented / current — Reset to Defaults safety

"Reset to Defaults" means safe global settings reset, **not factory reset**.

It must not erase:

- conversation/history,
- memories,
- character identity/personality,
- framing/assets that are intentionally outside that reset's scope,
- API keys where the product has explicitly excluded them from global defaults reset.

Recovery switches such as UI/display reset are recovery mechanisms, not data-reset mechanisms, and must not touch user conversations, memories, or audio history.

### Implemented / current — reconnect and notification restraint

Normal backend reconnect should happen silently when possible. A permanent "Reconnect" control is not intended as ordinary UI.

A generic toast system is not a product requirement; feedback should be contextual and purposeful rather than adding notification infrastructure for its own sake.

---

## 17. Local-first and model architecture

### Implemented / current

The Python `AssistantService` is the authoritative frontend-independent service boundary. The Unity client connects through loopback transport and does not own conversation/memory persistence.

Current response generation supports Online providers and managed/external
OpenAI-compatible Local providers through the same continuity boundary. Managed
Qwen 3.5 CUDA is validated on Linux; provider switching does not reset character
state.

AIFren owns only managed processes it starts. Local to Online stops that owned
llama process; an external compatible endpoint is never killed or restarted.
Online to Local applies saved model/Auto-start state. Missing credentials or no
configured provider leaves Settings accessible, and a Local failure never
silently selects Online.

Owned `llama_cpp.server` launch explicitly sets `--logits_all false`. Ordinary
chat does not consume prompt-token log-probabilities, and retaining the
`n_ctx x n_vocab` score matrix can allocate several GiB on
large-vocabulary models without changing normal response semantics.

Live Local turns use fresh explicit system-random seeds. Qwen3.5 gets its
model-specific recommended non-thinking/general sampler preset; Online and
other local families are not silently changed. Deterministic seeds are reserved
for rebuildable derived compaction and must never make ordinary companion
dialogue deterministic.

### Planned / decided — replaceable providers

The following should remain independently replaceable where practical:

- LLM,
- TTS,
- STT,
- embeddings/retrieval models,
- frontend/presentation.

Provider replacement must not reset character identity/history.

### Planned / decided — concurrent local runtime

The architecture should support running LLM + TTS + STT concurrently or with sensible scheduling on one machine.

This does not mean all future large models must remain permanently resident at full size. Resource-aware scheduling, loading, and provider choices are acceptable.

### Planned / decided — accelerator portability

AIFren must not architecturally depend on a single GPU vendor.

Local inference components should support interchangeable accelerator backends where their upstream runtimes permit it, including:

- NVIDIA CUDA
- AMD ROCm
- CPU fallback where practical

Provider and model selection should account for available accelerator capabilities rather than assuming CUDA globally.

The current Linux friend build's NVIDIA/CUDA requirement is an implementation and packaging limitation of the present STT/runtime stack, not a long-term product constraint.

Future AMD/ROCm deployment must be supported without changing canonical character identity, conversation history, memory, personality, relationship state, or provider-independent service interfaces.

Accelerator-specific concerns should remain isolated to inference/runtime/provider layers so that changing GPU vendor does not require redesigning the rest of AIFren.

### Planned / decided — hardware tiers

A personal high-end deployment may use significantly larger/better local models than the general distributed default.

A mature 1.0 should aim to provide or recommend a lower-spec local model appropriate for broader hardware where licensing, size, and quality permit. Advanced users should be able to substitute larger models without changing the rest of AIFren.

### Planned / decided — context detail and resource tradeoffs

Working-context detail should eventually be a user-visible resource tradeoff,
not a hidden model-size assumption. A likely ordinary control is Context Detail
Low/Balanced/High/Custom, backed by an explicit working-context token budget.
Advanced diagnostics may expose model `n_ctx` capacity, current and recent
average/peak prompt tokens, raw-dialogue/episode/memory composition, measured
VRAM used/free, and cautious estimated VRAM impact for larger capacities.

Model parameter count and context capacity are separate. A small local model
may use a modest useful context, stronger local hardware may retain more
high-resolution dialogue, and Online providers may permit much larger budgets.
All must use the same AIFren-owned archive/memory/selection semantics. Exact
future limits and the UI are not implemented or settled.

### Undecided

No specific future local LLM is permanently selected.

Model sizes, quantization, context strategy, and packaging format remain hardware- and ecosystem-dependent.

---

## 18. Data ownership and deletion safety

### Planned / decided — project-wide invariant

**A subsystem may destructively modify only data/files it owns. A reference does not imply ownership.**

This applies across:

- assets,
- characters,
- memories,
- personalities,
- voices,
- backgrounds,
- models,
- shared resources.

Examples:

- A character can reference a shared VRM without owning the VRM file.
- A character can reference a voice asset without owning the source/reference.
- A memory can reference canonical conversation evidence without owning/deleting the conversation.
- A frontend can display persistent data without receiving permission to mutate the underlying store directly.

### Planned / decided — destructive operations

Prefer:

- archive,
- supersede,
- deactivate,
- hide from active use,
- explicit confirmation,

when those satisfy the user need.

Irreversible deletion should be used cautiously and scoped precisely.

Character deletion must never imply deletion of shared global VRMs, backgrounds, or voice assets merely because the character used them.

### Implemented / current

The current architecture already keeps Unity/frontends away from direct Memory mutation and treats backend persistence as authoritative.

---

## 19. Remote / mobile companion

### Parked / long term

A remote/mobile companion is considered useful long-term, but it is not current scope.

The durable direction discussed is:

- the PC remains authoritative for canonical companion state;
- a phone/mobile app is a thin client;
- start LAN-first;
- likely stream or transmit audio/video/presentation/data rather than independently running a second authoritative companion;
- phone microphone/PTT/text input should be possible;
- a portrait-oriented mobile presentation would be useful;
- local pairing could use a simple QR/pairing flow;
- secure remote access can come later.

The goal is to avoid split-brain character state between PC and phone.

This direction has been considered more practically useful than prioritizing VR.

### Undecided

Exact transport, codec/streaming architecture, authentication, pairing protocol, and remote-access security design are not settled.

---

## 20. VR

### Parked / lower priority

VR experiments are possible, but VR is not a current product priority.

The companion should first be excellent as a desktop/voice application. Remote/mobile access currently has higher practical priority than VR.

No current architecture decision should force the core companion to depend on a VR runtime or game-world architecture.

---

## 21. Packaging, distribution, and 1.0

### Implemented / current milestone — friend build

A self-contained Linux x86-64 friend/test distribution has been produced and clean-extraction smoke-tested outside the development checkout.

The current friend build demonstrates that AIFren can package:

- a Linux Unity player,
- a relocatable Python/backend runtime,
- required current local TTS/STT/embedding runtime/model files,
- a relative-path launcher,
- public-safe test configuration without personal history, memories, secrets, private assets, Git metadata, or developer caches.

The extracted package starts its own copied backend and Unity player without requiring the development repository, developer virtual environment, or Unity installation.

This is a **testing distribution milestone**, not the final 1.0 installer architecture.

Current friend-build limitations/requirements include the present x86-64 Linux target, NVIDIA/CUDA dependence in the bundled faster-whisper path, working audio/microphone, network/API access for the current cloud LLM path, and user-supplied/licensed avatar content where the distribution does not bundle one.

### Planned / decided — release diagnostics boundary

Development builds enable the privacy-safe rolling flight recorder, automatic
incident capture, and manual `6666666` dump because they are essential to
intermittent in-player diagnosis. Release/1.0 builds keep recording off by
default. Disabled instrumentation should be a cheap/no-op path, and persistent
diagnostics must never grow without a bound. A future explicit Diagnostics
opt-in may retain bounded captures, but it must reuse the central recorder
rather than introduce another logging subsystem.

### Planned / decided — ordinary-user packaging

A mature 1.0 should be easy to install and run. Ordinary users should not have to manually assemble Python virtual environments, install model dependencies one by one, or understand the development checkout.

Packaging should include or automate runtime dependencies that the chosen distribution is legally and technically able to ship.

### Planned / decided — local model distribution direction

Where licensing, package size, and target hardware allow, a mature release should include or guide setup of a lower-spec local model suitable for broader machines.

Advanced users should be able to substitute larger local models without changing the rest of AIFren. A personal high-end setup may therefore use a substantially larger model than the general bundled/recommended default.

### Planned / decided — backup/import/export

A mature product needs user-facing backup, import/export, and migration support for durable character data.

Portability should cover moving across machines, installation locations, operating systems, hardware upgrades, and provider/model changes.

### Exploratory / undecided — commercial distribution

A long-term idea is to keep AIFren publicly developed/source-available and potentially move toward a formally open-source arrangement, while also offering an inexpensive official Steam distribution around the low single-digit-dollar level.

The Steam value proposition would be convenient packaging, installation, updates, and dependency handling rather than locking basic functionality behind a proprietary fork.

This is **not a settled commercial or licensing plan**. The current public-source license should not be described as OSI-style open source unless the license is deliberately changed.

### Planned / decided — commercial release licensing and IP hygiene

The current public development repository may contain explicitly approved public/default/demo material whose presence is accepted for the current non-commercial development phase. That acceptance is not itself a conclusion that every such element is suitable for paid commercial distribution.

Before any paid/Steam/commercial release:

- review and replace copyrighted-character-derived default identity/personality/branding where necessary;
- verify every bundled avatar, background, animation, audio, model, dependency, and other asset is commercially redistributable;
- preserve third-party notices and attribution/license obligations;
- distinguish software-code licenses from model-weight, voice-data, artwork, and character/IP rights;
- do not rewrite public history merely because previously approved development/demo defaults existed there unless genuinely sensitive, private, secret, or nonredistributable material requires a separate response.

### Undecided

Final installer/package technologies, update mechanism, supported distribution formats, exact bundled local model, and final licensing/business structure remain unsettled.

---

## 22. Music and audio extras

### Planned / decided — non-priority

Built-in ambient/background music is not a priority.

Audio engineering effort should focus first on functional companion interaction:

- TTS quality,
- microphone/STT,
- PTT,
- interruption,
- synchronization,
- useful cues.

Avoid feature creep into a music-player/ambient-sound system unless later user testing shows a clear product need.

---

## 23. Windows and platform support

### Implemented / current — Linux development platform

Ubuntu/Linux is the current active development and runtime platform.

Current Linux-specific work includes:

- Linux Unity builds;
- X11/EWMH handling for borderless/fullscreen behavior on rotated displays;
- Always on Top support;
- global PTT/input support where available;
- backend lifecycle/reconnect/recovery behavior;
- a verified self-contained Linux friend/test distribution.

The project should not be described as merely "moving to Linux"; the migration has already progressed far enough that Linux is the current development baseline.

### Planned / decided — cross-platform boundaries

Unity/backend logic should remain cross-platform where practical. Platform-specific behavior should stay isolated behind testable boundaries, especially:

- global hotkeys/PTT,
- fullscreen/display behavior,
- process/bootstrap integration,
- audio-device behavior,
- native GPU/runtime dependencies.

Windows compatibility remains important and needs a dedicated pass later. Linux-specific solutions must not leak into canonical character/history formats or make Windows support impossible.

### Undecided

Final supported Linux packaging formats, Wayland/X11 global-input strategy, GPU dependency packaging, and exact Windows installer/runtime integration remain unsettled.

---

## 24. Explicitly parked / deferred items

These items are intentionally not the current development focus:

- remote/mobile companion;
- VR experiments;
- generic GLB avatar support;
- advanced mood/emotion behavior beyond the initial restrained expression/metadata system;
- deeper gaze and physical-interaction systems;
- full game-world simulation, physics, pathfinding, maps, and inventory systems;
- constant autonomous chatter;
- broad generic Memory V2 authority beyond the current governed lanes until
  inspection/provenance/safety tooling is ready;
- Relationship State (a separate future subsystem);
- local-LLM **distribution/productization** work beyond what is needed for development;
- full Windows/distribution hardening until the current Linux-first feature work is further along.

"Parked" does not mean rejected; it means intentionally outside current scope.

Authored animation evaluation is **not parked**: it is part of the current gesture-quality priority. Character Management is likewise a near-term planned system rather than a parked idea.

---

## 25. Open questions

The following remain genuinely unresolved and should not be silently converted into implementation assumptions.

### Models and inference

- Which local LLM, if any, should be bundled or recommended by default for 1.0?
- What hardware tiers should the official local setup support?
- How aggressively should models be kept resident versus loaded/scheduled around TTS/STT?
- Which CUDA/ROCm-compatible runtime stack provides the best shared abstraction across local LLM, STT, TTS, embeddings, and future image-generation workloads?

### TTS / voice

- What is the final default TTS provider?
- Should an official release include a generic voice only, an optional reference-conditioned workflow, or both?
- What voice-reference licensing/consent rules should the product enforce or document?
- Which cloned/reference-conditioned engine provides the right quality/resource/licensing tradeoff?

### Memory / relationship

- What evidence and product gate should justify broad Memory V2 authority after
  the settled claim/scene foundations and Memory Viewer/Editor exist?
- When Relationship State is deliberately resumed, what evidence, user
  controls, dimensions, and update rules should it use?
- How should confidence/dispute/supersession be represented in the user-facing memory tools?
- What, if any, deliberate imperfect-recall model feels natural without becoming frustrating?

### Emotion / animation

- What exact mood dimensions and decay mathematics should be used?
- Which bounded presentation metadata extensions, if any, are justified beyond
  the current response contract and semantic emotion/gesture fields?
- Which authored Humanoid animation source(s), if any, meet quality and redistribution/commercial requirements?
- How much expression should persist during listening before it feels unnatural?

### Avatar / presentation

- Which additional Avatar View/camera controls, if any, belong in ordinary UI versus advanced settings?
- How should expression/capability mapping differ between VRM 0.x and VRM 1.0 where APIs/features diverge?
- Which graphics-quality defaults best preserve avatar sharpness across common hardware?

### Mobile / remote

- What transport should a thin mobile client use?
- Should it stream rendered video, transmit avatar state and render locally, or support multiple modes?
- What pairing/authentication model is appropriate for LAN and eventual remote access?

### Packaging / commercial

- What is the final Windows installer/portable strategy for 1.0?
- What is the final Linux packaging/update strategy beyond the current friend/test archive?
- Will AIFren remain under the current public-source license, move toward a formally open-source license, or use another distribution strategy?
- Will an official Steam version be pursued, and if so at what price/support scope?

---

## 26. Additional durable decisions

This section records settled decisions that cut across the topic boundaries above and are easy to lose in a short roadmap.

### 26.1 Backend authority versus presentation

**Implemented / current:** `AssistantService`/the Python backend remains authoritative for conversation, memory, personality, turn ordering, and speech lifecycle.

Unity is a presentation client. A frontend may render, submit commands, and keep frontend-local preferences, but it must not become a second canonical memory/history implementation.

This separation is what permits future desktop/mobile/frontends without splitting identity.

### 26.2 Conversation-message durability is independent of reveal state

**Implemented / current:** the canonical conversation event/persistence path is independent of the presentation-only assistant-response reveal path.

Hiding the UI, changing reveal behavior, or presentation transitions must not cause a message to be lost from history.

### 26.3 Visual theme is not world simulation

**Planned / decided:** Light/Dark mode affects UI theme. It does not imply a simulated day/night world state or automatic background schedule.

This deliberately keeps presentation predictable and avoids turning AIFren into a world-simulation project.

### 26.4 Reset/recovery operations are scoped

**Implemented / current:** UI/display recovery operations are not factory resets.

A recovery command may restore a usable window or UI state, but it must not touch canonical conversations, memories, relationship data, or other durable user content.

### 26.5 No unnecessary user-authored framing work

**Implemented / current principle:** AIFren should adapt ordinary visual assets automatically where reasonable. Users should not be forced to perform manual crop/framing work merely because the program can provide sensible presentation defaults.

This is explicit in the current background system and direct-rendered Avatar View: background images use automatic cover behavior, while avatar framing is handled as reversible presentation X/Y/scale rather than destructive model cropping. The same principle should guide future arbitrary-VRM support.

### 26.6 Immersion without pretending the software layer does not exist

**Planned / decided:** The ordinary experience strongly favors staying in-character and maintaining immersion. Software awareness exists as an occasional practical layer rather than the dominant persona.

Accidental out-of-character/fourth-wall output must not automatically become canonical character memory or personality.

The product should not expose a game-like "kayfabe meter"; this is a behavioral design philosophy.

---

## 27. Current priority snapshot

This section is intentionally more time-sensitive than the durable design sections above. `PROJECT.md` should remain the concise roadmap, and explicit current user direction may supersede this ordering without changing the underlying durable principles.

### Current priority

The hardened Active State foundation, governed durable-fact lanes, Open
Threads, scalable History hierarchy, proactive lifecycle, response authority,
and production Unity integration are implemented. The latest targeted
production fixes passed automated validation, and Active State is now frozen at
another manual Development-player product-acceptance cycle. Further changes
should be driven by concrete real-use regressions, not speculative world-model
expansion. Extensible body/attachment loci are an agreed future bounded
improvement, not current accepted functionality.

After that acceptance gate, the next important user-facing continuity tranche
is a bounded Memory Viewer/Editor with evidence, provenance, correction, and
scope inspection. Broader generic V2 authority must wait for those controls and
promotion evidence. Relationship State remains explicitly deferred and is not
part of the current roadmap tranche.

Packaging, authored-animation quality, Voice/AI settings, Windows
compatibility, and 1.0 productization remain separate later priorities. The
portable Linux friend archive is already a completed testing milestone.

### Stability constraints while pursuing the roadmap

- Do not casually rewrite the hidden-subtitle system; it is structurally stable and should change only for a confirmed reproducible bug.
- Do not return to RenderTexture/crop architecture as the normal avatar presentation path.
- Preserve backend authority over canonical conversation/memory and speech lifecycle.
- Preserve semantic gesture intent even if authored clips replace procedural motion.
- Keep future emotion/mood work restrained and separate from durable personality/relationship semantics.
- Keep Relationship State deferred until explicitly resumed; do not infer it
  from Active State, Open Threads, or ignored proactive messages.
- Keep owned llama launch on `--logits_all false`; do not trade away the
  intentional 16k context to conceal that corrected server configuration bug.
- Treat synthetic benchmarks as diagnostic support. Ordinary Development-player
  interaction with a synthetic/test character is the performance acceptance
  gate, and the privacy-safe flight recorder is the preferred intermittent
  incident record.
- Keep Kokoro early speech bounded and provider-specific; retain the persisted
  `whole_response` fallback even though grouped streaming has passed human QA.
- Keep Context Hygiene at its conservative lexical limit. Broader semantic
  concentration belongs in bounded episode selection/retrieval, not looser
  deletion-like heuristics.

---

## 28. Reading and using this document

When documents appear to disagree, interpret them by role:

- `README.md` — public overview;
- `PROJECT.md` — concise direction and near-term roadmap;
- `ARCHITECTURE.md` — current technical topology, data paths, and ownership;
- `docs/DESIGN_DECISIONS.md` — durable product/system decisions and future constraints;
- `docs/DEVELOPER_GUIDE.md` — implementation/workflow guidance.

A future feature described here must not be presented as implemented unless current architecture/code agrees.

Conversely, an old implementation detail found in historical chat, an obsolete branch, or a rollback/debug path must not be promoted back into the intended architecture merely because it existed previously.

### Guidance for ChatGPT/Codex agents

- **Implemented / Current** describes behavior that should be preserved unless the user explicitly requests a change or a verified bug requires one.
- **Planned / Decided** describes intended behavior and architectural constraints, not an automatic task queue.
- **Exploratory / Parked** must not be treated as an approved implementation plan.
- **Undecided** must remain undecided; do not invent a choice to make implementation easier.
- Current priority ordering is sequencing guidance, not permission to stack unrelated work into one large change.
- Prefer focused, coherent implementation passes and preserve established subsystem invariants.
- When a future implementation conflicts with a durable invariant here, change the invariant deliberately and document why rather than silently coding around it.
- Explicit newer user direction always wins. Update this document when that direction becomes a durable decision so future chats and agents do not resurrect superseded plans.

---
