# AIFren Design Decisions

> **Status:** Durable product/system design record.
> **Updated:** 2026-08-22.
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

### Planned / decided — very large archives

A multi-year archive can become extremely large. UI and memory tools must therefore assume that loading the entire archive into memory or rendering it all at once will eventually be unacceptable.

Future history browsing should use bounded access such as:

- pagination or virtualization,
- date/range filters,
- search,
- lazy loading,
- export tools,
- scoped correction/deletion tools.

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

---

## 5. Memory architecture

Memory is the central long-term differentiator and requires stronger boundaries than a generic vector database.

### 5.1 Current authority: Memory V1

**Implemented / current:** Memory V1 is the authoritative long-term memory system in the current runtime. It persists durable memory JSON, validates data, uses atomic save behavior, and supports provenance/source-aware extraction. The current memory path is part of the canonical application data that must not be casually relocated or rewritten.

**Implemented / current:** assistant-generated statements are not automatically treated as evidence of user facts. User-message evidence is the important current integrity boundary.

### 5.2 Memory V2

**Exploratory / current shadow work:** Memory V2 exists only as partial/experimental/shadow work. It is **not authoritative** and must not silently replace Memory V1.

**Planned / decided:** Before V2 can become authoritative it needs, at minimum:

- trustworthy retrieval diagnostics,
- source tracing/provenance,
- safe structured editing/inspection,
- character scoping,
- correction/supersession semantics,
- temporal handling,
- scalable browsing,
- migration/recovery confidence.

The V2 curator is a **logical role**, not a requirement for a permanently resident second large model. On constrained hardware, curation may be scheduled, batched, or use the same model at another time.

### 5.3 Conceptual memory categories

The long-term system should keep conceptually different information distinct even if implementation later stores some categories in shared tables.

#### Stable user/profile facts

Examples include durable preferences, biographical facts, important constraints, and other user-provided information likely to remain true.

These are not the same as shared episodic experiences.

#### Shared episodic experiences

AIFren should remember things that happened **between the character and the user**, not merely extract user profile facts.

Examples include:

- promises,
- repairs after a disagreement,
- milestones,
- recurring jokes,
- shared projects,
- meaningful past conversations,
- events the pair experienced together.

This category is important to the long-term relationship goal.

#### Active/current state

Temporary state should not rely on ordinary semantic retrieval. When relevant, it belongs in deterministic prompt/context assembly.

Examples include:

- current location,
- current clothing,
- held objects,
- current activity,
- current environment/temporary conditions,
- other presently true situational facts.

Active state needs explicit lifecycle semantics. Candidate lifecycles include:

- **until changed**,
- **session**,
- **time-limited**,
- **explicit clear**,
- **cautious inference** with lower confidence.

An old semantically similar state must not randomly reappear as current merely because vector retrieval ranked it highly.

#### Relationship state

Relationship state is a separate concept described in its own section. It should not be encoded only as factual memories or base personality.

### 5.4 Evidence and provenance

**Planned / decided:** Derived memories should be backed by source/evidence where practical.

A durable memory should be able to answer questions such as:

- what conversation/event supported this claim?
- which character does it belong to?
- when was the evidence created?
- was the statement direct user evidence, inference, correction, or consolidation?
- has newer evidence superseded it?
- is it disputed?

The canonical conversation remains available when a derived claim must be audited or rebuilt.

Assistant-generated text must not automatically become evidence that a user fact is true. Otherwise the model can manufacture a claim and then "remember" its own invention as user history.

### 5.5 Corrections, conflicts, and trust

**Planned / decided:** Explicit, plausible user corrections are high-authority evidence. If the user says an old memory is wrong or outdated, AIFren should generally accept the correction.

That does not require blindly rewriting history. Where evidence conflicts, the system may preserve the old evidence while marking the derived claim as:

- superseded,
- corrected,
- disputed,
- no longer current.

The policy should be cautiously trusting rather than adversarial. AIFren is not meant to argue with the user or behave like an anti-gaslighting security system, but obvious contradictions should not destroy the provenance of what previously happened.

### 5.6 Retrieval

**Planned / decided:** Retrieval should balance more than raw semantic similarity. Relevant factors include:

- relevance,
- recency,
- importance,
- diversity,
- repetition suppression,
- character scope,
- temporal validity/currentness.

The prompt should not be flooded with near-duplicate memories, and a memorable phrase/running joke should not recur in every conversation simply because it scores highly.

Retrieval metadata and indexes are derived and rebuildable.

### 5.7 Accessibility, importance, and forgetting

**Planned / decided:** accessibility is not the same as importance, and forgetting is not the same as deletion.

A future character may occasionally fail to recall obscure information. If imperfect recall is implemented, it should be conservative and believable rather than frustrating or arbitrary.

Failure to recall something in one turn must not destroy the canonical archive or erase the underlying memory evidence. Recurrence can reinforce accessibility without rewriting history.

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

### Planned / decided

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

### Implemented / current

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
- temporary-state expiration,
- continuity across days, months, and years.

Temporal awareness should help the character distinguish:

- "this is true now,"
- "this happened yesterday,"
- "this used to be true,"
- "we have not talked about this for months."

This should be built from reliable timestamps/state semantics rather than asking the LLM to guess chronology from a bag of semantically retrieved memories.

### Implemented / current

Canonical conversation timestamps exist. Full temporal reasoning and temporary-state lifecycle management do not.

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

1. a known action/stage-direction phrase is `Emote`;
2. otherwise, a span with four or more normalized words is `Emote`;
3. otherwise it is `Emphasis`.

**Double paired `**...**`:** always `Emphasis`, regardless of length.

`Emphasis` remains spoken content: its markers are removed for TTS and it is presented as spoken emphasis, currently italicized in visible dialogue.

`Emote` is presentation/action content: it remains visible in normal dialogue with distinct styling, is omitted from TTS and hidden spoken subtitles, and may feed semantic gesture mapping.

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

### Planned / decided — one interpretation boundary

Presentation semantics should increasingly become structured meaning once rather than being independently re-parsed with subtly different heuristics by display, TTS, animation, logging, and future frontends.

The long-term direction is structured response/presentation metadata while retaining canonical natural-language response text. Raw punctuation conventions should not become an unbounded protocol.

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

Avatar models and backgrounds are global managed visual assets rather than character identity. Imported assets are copied into AIFren-owned managed storage with stable identity, friendly naming, and previews/thumbnails where available.

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

### Planned / decided — metadata from the same response

The LLM should ideally choose response emotion explicitly.

Avoid an extra LLM call solely to classify emotion if the main response generation can return structured presentation metadata in the same turn.

A future `ResponsePresentationMetadata` contract may include fields conceptually equivalent to:

- `emotion`,
- `intensity`,
- optional `gesture`.

The exact wire/schema format is not fixed by this document.

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

### Implemented / current

There is no complete authoritative mood system. Persistent mood, richer semantic vocabularies, and autonomous gaze remain future work; the current bounded response-presentation metadata is deliberately not mood or character-memory state.

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

AIFren has a provider-independent TTS/service boundary. Kokoro is the current main local TTS path and Piper remains available as a fallback/dependency path. STT is local-capable. The backend remains authoritative for synthesis/playback lifecycle.

The current Kokoro configuration does not depend on runtime pitch post-processing to force a character voice.

### Implemented / current — TTS result semantics

The durable provider-independent concept is a synthesis/playback result that can provide:

- audio,
- total duration,
- a stable `playback_id`,
- optional word/phoneme/viseme timing information.

Kokoro can expose predicted token/word timing when a trustworthy one-to-one mapping is available. Timing metadata is enhancement data, not lifecycle authority.

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

### Planned / decided

Proactive behavior should be limited and conservative.

The companion should not constantly interrupt the user simply because the application is open.

The rough product direction discussed is an occasional check-in on the order of **about once per hour** when the user is not actively interacting. This is an approximate scale, not a required fixed timer.

Proactivity should consider context such as:

- recent activity,
- whether the user is already interacting,
- quiet/sleep indications,
- whether prior check-ins were ignored,
- explicit user preferences.

Ignoring a proactive suggestion is at most a weak back-off signal. It must not be interpreted as relationship rejection.

"Good night" or equivalent language can suppress interruptions until later activity suggests the user is available again; the system should not require a rigid fixed sleep schedule.

### Implemented / current

No mature proactive behavior system is authoritative today.

### Undecided

The exact scheduler, context signals, UI controls, and local notification behavior are not settled.

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

Current response generation uses a cloud provider, while STT/TTS/memory are designed around local operation.

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
- game-world / locomotion systems;
- constant autonomous chatter;
- final Memory V2 authority until inspection/provenance/safety tooling is ready;
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

- What exact schema and authority transition should Memory V2 use?
- What are the final relationship-state dimensions and update rules?
- How should confidence/dispute/supersession be represented in the user-facing memory tools?
- What, if any, deliberate imperfect-recall model feels natural without becoming frustrating?

### Emotion / animation

- What exact mood dimensions and decay mathematics should be used?
- What exact structured response metadata schema should connect LLM response, expression, gesture, and TTS?
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

The major visual/direct-rendering, background, dialogue, subtitle, input, and friend-build milestones have already been completed far enough to move the project forward.

Current order when development quota/time allows:

1. establish reliable long-term episodic and temporal memory retrieval before
   any Memory V2 prompt-facing canary;
2. design active/current state, relationship state, and a scalable
   non-destructive Memory Viewer / Editor;
3. continue friend-build/package and Unity frontend validation;
4. improve licensed authored animation and presentation only after memory
   correctness is no longer the release blocker;
5. expand Voice/AI settings and provider choices;
6. perform the dedicated Windows compatibility pass;
7. continue 1.0 packaging/productization.

Friend-build testing may continue opportunistically, but producing a portable Linux friend archive is no longer a missing milestone.

### Stability constraints while pursuing the roadmap

- Do not casually rewrite the hidden-subtitle system; it is structurally stable and should change only for a confirmed reproducible bug.
- Do not return to RenderTexture/crop architecture as the normal avatar presentation path.
- Preserve backend authority over canonical conversation/memory and speech lifecycle.
- Preserve semantic gesture intent even if authored clips replace procedural motion.
- Keep future emotion/mood work restrained and separate from durable personality/relationship semantics.

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
