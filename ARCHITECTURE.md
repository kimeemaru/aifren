# AIFren architecture

## System boundary

```text
Unity production frontend --local WebSocket--> backend_host.py --> AssistantService
```

`AssistantService` is the frontend-neutral boundary. It serializes turns,
persists canonical messages, builds context, generates replies, coordinates
TTS/PTT, processes Memory V1, maintains summaries, and emits events.
`backend_host.py` adapts one service to the Unity loopback frontend.

Unity is the only product/user-facing frontend. Backend services stay
frontend-neutral. There is no Tkinter companion application; the Linux AIFren
Dev Tk control window is developer-only tooling around the Unity shell launcher,
not an alternate product frontend or backend.

## Continuity model

The governing principle is **persist the facts that matter; infer the
experience**. Context is for reasoning; Active State is for continuity. AIFren
provides bounded improvisational world-awareness rather than a simulated world.
The backend owns sparse current truth, evidence, actor/scope identity,
lifecycle, correction, capabilities, and validation. The model owns creative
interpretation and characterful prose inside that envelope.

Continuity has deliberately separate authorities:

- canonical conversation is the permanent source record;
- Memory V1 remains broad/general prompt-facing learned-memory authority;
- Memory V2 supplies source-grounded episodes and governed structured lanes;
- Active State represents current reality and derived capability consequences;
- Open Threads represent unresolved/waiting/planned continuity;
- profile/default scene supplies lower-authority stable character defaults;
- Relationship State is deferred and has no current authority.

No derived lane may rewrite canonical conversation merely to clean or correct
its own state.

## Model and speech runtime

Context assembly, character continuity, Memory V2, and canonical history are
provider-neutral. The configured adapter may be online Gemini, another
OpenAI-compatible service, or a local OpenAI-compatible endpoint; changing it
does not create a separate memory architecture. The current working context is
composed from fixed/core instructions and Memory V1, selected older derived
episode context when valid, and a recent raw suffix processed by Context
Hygiene. The latest user turn remains verbatim. Raw archive size, model size,
provider context capacity, and transient prompt budget are separate concepts.

Raw conversation selection and active working context are deliberately
separate. `Conversation.build_context()` applies Context Hygiene V1 only to a
transient recent-message list. Within the bounded recent window it retains all
standalone user records, the latest user message, and the newest assistant
instance. Older assistant records are omitted only when high ordered phrase
overlap proves a near-duplicate, or when multiple high-confidence near-copies
of their immediately preceding user messages form a repeated echo behavior.
An older complete user/assistant exchange may be omitted only when both sides
strongly match a newer nearby exchange that remains verbatim. Summary
boundaries, Memory V1/V2, archive JSON, and provider adapters are not mutated.
The same filtered working context is used for Local and Online providers.

For the normal Local path, the loopback host can manage one ignored GGUF through
the installed llama.cpp-compatible server. Installed GGUF discovery is separate
from `/v1/models` discovery: a selected model is not reported as active until
the owned server is healthy. An already-running compatible endpoint is marked
external and is never stopped or restarted by AIFren. Managed model process
lifecycle is a provider/runtime concern only; its OpenAI-compatible adapter
receives the same assembled context as Online providers.

Managed launches also persist a local, ignored recovery descriptor containing
the exact process start identity, process/session group, ownership nonce, argv,
and selected configuration. After an abnormal backend exit, a later backend may
attach to that process only when Linux `/proc` still proves every identity field
and the endpoint advertises the matching model. A proven but unhealthy or
incompatible owned group is stopped and restarted. Missing, malformed, stale,
or mismatched proof is forgotten without signaling the live PID; a responding
port then remains an external endpoint. Port number alone is never ownership.

On NVIDIA Linux hosts setup builds the pinned `llama-cpp-python` CUDA backend
from source with detected compute capability and a portable CPU baseline
(`GGML_NATIVE=OFF`; AVX-512 disabled). This avoids the official CUDA wheel's
AVX-512 SIGILL on CPUs that do not implement it. The runtime reports `CUDA`
only after the managed server confirms actual offload, and backend shutdown
stops only its owned server process.

The owned server command must include `--logits_all false`. AIFren never asks
for per-prompt-token log-probabilities, while enabling them retains an
`n_ctx x n_vocab` score matrix that can consume several GiB for large
vocabularies. Disabling it does not change normal AIFren output semantics.

Ordinary Local assistant requests supply an explicit fresh system-random seed.
Qwen3.5 Local models receive their model-specific non-thinking/general sampler
preset; Online and unrelated local families retain their own behavior. This
stochastic live-turn policy is separate from derived compaction, whose local
requests use provenance-derived deterministic seeds for rebuildability.
Model choice remains a runtime quality and capacity decision rather than a
continuity boundary.

Local/Online selection changes only the active adapter/runtime. Local to Online
stops an owned managed llama process; Online to Local applies saved Local state
and auto-starts only when configured. External compatible servers are never
owned. Missing credentials or an unavailable provider produce recoverable
configuration state and never reset or silently reroute character continuity.

Generation streams presentation deltas while the final parsed assistant
dialogue is persisted once. Speech synthesis strategy remains provider-specific.
Kokoro normally uses bounded early speech: an exact, separate semantic
projection admits the first complete sentence immediately and conservatively
groups later adjacent sentences. A single synthesis worker feeds a bounded PCM
queue and one continuous PortAudio stream for the assistant turn. Sentence
boundaries never mutate canonical display, persistence, or memory text;
incomplete markup is retained until its emote/emphasis meaning is stable.
Interruption invalidates the current stream, pending groups, and stale synthesis
results by turn/playback identity. Unity's persisted “Speak while response is
generating” setting can select the stable whole-response fallback. Piper is
removed. Experimental voice runtimes and protected voice material are not part
of the public repository.

## Ownership

| State | Owner | Notes |
|---|---|---|
| Raw archive/summary | `Conversation` / JSON | Raw archive is canonical. |
| Memory V1 | `Memory` / `memories.json` | Use its mutation API only. |
| Memory V2 | Structured/source-grounded continuity infrastructure | Active State, Open Threads, Truth Scope, closed durable facts, and source-ranged episodes have governed seams; generic retrieval remains non-authoritative. |
| Active State | Memory V2 repository and governed backend APIs | Current actor/scene truth and lifecycle; capability effects are derived projections, not stored memory facts. |
| Profile/default scene | Character definition | Lower authority than explicit current evidence; not repeatedly written into Active State. |
| Relationship State | None yet | Explicitly deferred future subsystem. |
| Character identity/personality | character data | Separate from memory and visual assets. |
| Turn, STT, TTS, PTT | Python backend | Frontends request/display supported state. |
| Display/theme/avatar UI | Unity local preferences | Presentation-only. |
| Managed avatar/backgrounds | Unity asset library | Reusable visual assets. |

Character IDs are stable UUIDs with persisted selection. Character scope owns
personality, canonical history/summary, V1 memory, V2 structured continuity,
and a stable reference to its selected managed avatar. The avatar library,
background, lighting, framing, and voice configuration remain reusable global
assets/settings. Switching characters atomically replaces character-owned
service state while retaining the loopback connection and replacing Unity's
visible history and avatar selection.

Unity's History/Log is not canonical storage. It maintains a lightweight date
index and navigates Year -> Month -> Day -> bounded message pages. Opening it
does not instantiate the lifetime archive. Empty legacy/non-renderable rows are
skipped only in the derived view, visible updates affect the current date/page,
and canonical conversation remains untouched. The initial transport snapshot
may still carry more canonical history than the selected page needs; future
transport pagination is a compatible optimization, not a change of authority.

## Memory V2 boundary

V1 remains authoritative and prompt-facing for general memory. V2 SQLite is a
fail-open store whose production authority is restricted to validated,
closed-schema structured continuity. Generic retrieval and derived episode
text remain non-authoritative, and broader promotion still requires explicit
evidence and a Memory Viewer/Editor. Raw conversation is canonical. V2 claims,
evidence, lifecycle/status history, and governed relations remain inspectable
source-grounded structured records. Episode accounts, FTS, embeddings, ANN,
and capability/display projections are versioned rebuildable views. The
current SQLite schema version is 17.

The first episode-compaction foundation reuses V2's existing `summaries` and
`summary_source_ranges` ownership rather than creating another memory store.
An explicit rebuild divides a canonical prefix into deterministic complete
user/assistant exchange ranges, asks the currently configured provider for
neutral third-person episode accounts, and publishes the complete generation
atomically. Every row records its compaction/segmentation version, exact source
range, source-record fingerprints, generation ID, non-secret compactor identity,
and derived-generation seed. Lower-summary requests derive an explicit seed
from that stable provenance instead of consuming the fresh random seed policy
used by live assistant turns. Unchanged inputs therefore rebuild byte-identical
with AIFren's seeded local llama provider. Online providers may honor the seed
only best-effort; their stored provenance remains auditable, but AIFren does not
claim byte reproducibility when the provider does not guarantee it. Missing,
corrupt, stale, or version-mismatched rows fail open to the established
raw-history path.

Lower episode generation preserves both a compact neutral narrative and a
bounded set of distinctive continuity details. Rebuild time extracts at most
six source-grounded anchors, verifies their retention, permits one
deterministic refinement, then uses a bounded source-grounded fallback if
verification still fails. Anchor schema/version and result metadata are part
of cache validity. This does not create authoritative facts: anchors and
summaries remain derived from exact canonical source ranges and disposable.

One bounded higher derived level prevents a single repetitive historical era
from consuming several selected prompt slots. A maximal contiguous run of at
least four lower episodes that the selector would otherwise admit together is
submitted for a conservative provider decision, anchored by the lower accounts
and deterministic canonical excerpts. The provider may reject consolidation.
Before publication, a separate deterministic-seed retention verifier compares
the proposed account against every covered lower account. An era is selectable
only when versioned metadata records an unambiguous pass with no missing
distinctive continuity items. Errors, uncertainty, missing/stale gate metadata,
or omitted details fall back to validated lower rows. An accepted era has exact
combined source provenance and its own version; its covered lower rows remain
rebuildable in V2 but are not simultaneously placed in the prompt.

When that derived prefix validates, prompt construction admits a bounded sample
of older episode/era accounts plus the newest 24 exchanges verbatim; Context
Hygiene still operates only on that raw suffix. This transient seam replaces
the legacy rolling summary for that request, not Memory V1, personality, or the
canonical archive. It is provider-neutral and non-authoritative: provider/model
switching does not rebuild continuity, and canonical data always wins. The
stored compactor identity is validated as part of that derived generation but
is deliberately not compared with the currently selected conversational model:
a model switch keeps valid continuity, while an intentional rebuild records the
compactor then used. The current explicit rebuild command is
foundation/backfill tooling; eventual ordinary migration should schedule
bounded background rebuilds and atomically publish them rather than blocking
application startup.

The historical context-regression harness reconstructs an authorized public
test fixture, builds legacy/lower/consolidated contexts, and runs matched
sampling seeds without persistence or speech. A broad era that loses
distinctive continuity is rejected; zero selectable eras is valid. Older
episode selection remains bounded, source-grounded, and subordinate to recent
raw dialogue; it is not broader V2 authority.

The governed durable lane extends the original `identity.name` reference to a
closed set of explicit real-world profile, residence, occupation/school,
device, pet, project, preference, recurring-interest, and possession facts.
The deterministic curator is proposal-only; exact canonical user evidence,
contract validation, truth scope, and lifecycle APIs decide writes. Current
corrections create superseding claims and preserve predecessor evidence.
Prompt admission is relevance-gated, typed, capped at three facts, and filters
only clear duplicate V1 rows for the slots it actually admitted.

Active State is a sparse, mention-driven current companion scene. It keeps
actor-scoped activity/posture, bounded character-local subjects, independently
governed attributes, current relations, truth scope, and non-destructive
subject lifecycle. Subjects use opaque `scene-<uuid>` IDs internally; normal
prompt and UI text never exposes them. Current reads are bounded/indexed exact
or per-subject operations, never FTS/ANN/archive scans.

The closed mutation vocabulary is deliberately small: establish a subject, set
an attribute, establish or clear a relation, replace, transfer, locate,
correct, retire, and explicitly reactivate an eligible distinct subject.
Attribute mutation and replacement are different operations. Changing a hat's
color keeps the same subject; swapping that hat for another closes the old
current relation and establishes a distinct replacement. Multi-update batches
validate before one transaction so destructive replacement cannot half-apply.

Active State proposals are untrusted, bounded inputs tied to persisted
same-character evidence. Backend validation governs actor, slot/facet,
attribute/relation, source spans, scope, atomic application, lifecycle, and
evidence roles. Assistant prose has no authority. Time is derived from
validity/evidence timestamps and never mutates state on its own.

For ownership/attachment lifecycle, semantic `holding`/`carrying` and
`wearing` relations are primary authority; `held_by` and `worn_by` subject
attributes are synchronized compatibility projections. Parity is checked in
the mutation transaction. Release closes the current holding relation before
applying an explicit location. Transfer preserves subject identity while
changing the authoritative holder.

Subjects move through `current`, `dormant`, and `retired`. Active relation or
capability sources cannot retire. Relation-free subjects enter a bounded
dormant roster and eligible old rows retire without deleting evidence. Distinct
explicit objects may reactivate when identity is strong; an old generic cup,
book, box, or hat is not resurrected merely because one historical match
exists. Retired subjects are absent from ordinary prompt/UI/reference
resolution.

`active_scene_relations` stores only closed, evidence-backed relation semantics:
target actor/scene subject, optional body facet/side, predicate, cause, optional
subject identity/quantity/equipment family, scope, and validity lifecycle.
Multiple relations may affect the same facet. Capability composition retains
every current cause and applies the strongest deterministic restriction;
clearing one cause removes only its contribution.

Conventional blindfold application is one bounded semantic mapping: applying
a normal blindfold over the eyes establishes vision obstruction immediately.
A later statement that the same blindfold covers the eyes confirms/refines the
same logical application instead of stacking another independently removable
cause. This is equipment semantics, not general object physics.

`compose_capability_effects` is the single authoritative derivation seam. It
derives perception (vision, hearing, smell, taste, touch), communication
(speech), side-aware hand/arm manipulation, locomotion mode/constraint,
awareness, and posture from semantic relations and actor state. Object names
alone do not create effects: a decorative wrist item differs from a tethered
wrist, carried rollerblades from worn rollerblades, a nearby wheelchair from
one in use, and a held blindfold from one covering eyes. Environmental causes
record only explicit interaction consequences such as darkness, smoke, or loud
music; AIFren does not simulate light, acoustics, anatomy, or physics.

Hearing has `normal`, `constrained`, and `unavailable` states. Constrained
hearing remains usable with limitations. When interaction semantics explicitly
establish that a user utterance is delivered through unavailable hearing, the
canonical record is retained but its new semantic content is excluded from
understood model context, Memory V1/V2 processing, durable facts, Open Threads,
summaries, and episode compaction. Restoring hearing does not retroactively
reveal it. AIFren does not simulate acoustics or partial transcripts and does
not universally assume typed text is spoken.

Posture is actor state (`standing`, `sitting`, or `lying`), not a location or
support value. “Lying on the couch” may establish posture `lying` plus a
separate couch location/support relation. Explicit tether consequences can
strengthen locomotion constraints without simulating tether length or inferring
total immobility from every restraint.

The same composed envelope is consumed by prompt admission, sleep and ordinary
response validation, companion-action validation, semantic presentation, gaze,
gesture eligibility, and TTS/lip-sync policy. Effect rows are never persisted
as duplicate memory truth. Losing a capability removes that channel; it does
not make the companion unable to respond through remaining channels.

`interaction_policy.py` applies awareness-specific rules to that shared
envelope. User sleep blocks proactive output. Companion sleep permits bounded
VN-style physical reactions and short mumbling but forbids informative awake
conversation or model-authored waking. Other simultaneous vision, speech,
manual, locomotion, and posture constraints still apply.

The compact Unity scene overlay is a projection of the backend-bounded
snapshot; opaque action tokens are never displayed. Overlay X is an immersive
interaction: the backend validates revision/scope/target, applies the selected
cause mutation first, recomputes effects, publishes the snapshot, records one
canonical synthetic user event with structured `scene_ui/generated_event`
origin, and offers one reaction from post-mutation state. The frontend cannot
supply authoritative event prose, and generated event text is never reparsed
as evidence. Rapid actions cancel or suppress stale contradictory reactions.
Detailed/debug administrative clears remain silent corrections and create no
in-world event.

The assistant response contract has one minimal authority: canonical dialogue.
Plain canonical dialogue or `{"dialogue":"..."}` is valid; response mode,
exact `spoken_content`, semantic presentation, capability diagnostics, and one
closed companion action are optional. Omitted metadata receives deterministic
backend defaults, while supplied fields remain strictly validated.

Governed response requirements have two modes. An accepted ordinary mutation
creates `must_respect` facts: the model may omit them but cannot contradict the
post-mutation state. A direct deterministic question creates
`must_communicate` facts: the model must render the answer, receives one bounded
repair, then uses a state-derived factual fallback if necessary. Unknown is
never treated as none, and fallback cannot invent current scene truth.
Several independently recognized direct questions in one turn compose several
`must_communicate` facts without requiring a fixed answer order. Ordinary
responses have a soft target near 100 words and no general hard ceiling;
constrained/nonverbal reactions target roughly 50--80 words and have a
100-word validation ceiling followed by one concise repair. Generated text is
never raw-truncated.

The mutation boundary is always canonical evidence -> validate -> transactional
state mutation -> capability recomposition -> generation from the post-mutation
snapshot -> response/action validation -> persistence/publication ->
presentation/TTS. A rejected mutation cannot be narrated as completed.
Truth-scope transitions complete before context retrieval and generation, so
the immediate response after leaving RP cannot use inactive scenario state or
Open Threads as current truth.

The bounded companion-action slice supports activity, posture, one current
wear/remove or hold/release operation, and supported equipment use/dismount. A
private closed proposal is validated against actor, subject, scope, lifecycle,
and capabilities before application. Ordinary assistant prose is
non-authoritative, rejected actions cannot be narrated as successful, and the
slice is not a general autonomous-agent loop.

Schema migration v12 introduced provider-neutral Truth Scope to prevent scenario/world truth
from contaminating real-world continuity. Each character gets a stable default
real-world scope; bounded persistent scenario scopes have opaque IDs, governed
evidence-backed activation, and no time-driven expiry. Active State, scene
subjects/attributes, and Open Threads are filtered by the active scope. Leaving
a scenario hides its current continuity from normal context without deleting
it; resuming restores the same scoped state. Existing V2 rows migrate to the
real-world scope. Durable facts remain real-world only, so roleplay identity or
home claims cannot overwrite them. Scope is not an RPG inventory, entity graph,
or world simulator; current governed object attributes remain deliberately
small and independently lifecycle-managed.

Schema migration v13 introduced a structural proactive-check-in lifecycle without duplicating
assistant prose outside canonical conversation. A slow host poll only asks for
eligibility. Deterministic checks require one current unresolved Open Thread,
a plausible follow-up delay, an inactive interaction, the enabled setting, and
no sleep/busy suppression. Persistent cooldown and ignored-check-in backoff
permit zero eligible output indefinitely. Generation receives exactly one
bounded reason and cannot select arbitrary hidden memory. The saved preference
is a discrete minimum opportunity interval. Ignored check-ins impose a
deterministic escalating AFK backoff with no relationship semantics; canonical
user interaction clears the unanswered streak.

Proactive generation is private until a publishable response exists. Backend,
provider, and frontend snapshot readiness establish a separate startup grace
boundary (currently 60 seconds by default) independent of the chosen interval.
Empty, invalid, overlength, timed-out, or failed drafts create no visible turn,
no canonical assistant record, and no Thinking state; the failed opportunity is
persistently throttled so an old thread cannot hammer every poll after restart.
Every externally announced turn retains one terminal outcome.

Continuity V2.1 adds prospective scope provenance to canonical dialogue. At
the accepted-turn boundary, `AssistantService` reads the authoritative active
scope and writes the same `{kind, scope_id}` value on the user and assistant
records when the pair reaches the canonical save point. An entry command is
therefore a real-world exchange and the next exchange is scenario-scoped; an
exit command remains in its scenario and the next exchange is real-world. This
keeps a turn coherent and preserves the established failed/interrupted-turn
ownership rules. Provider adapters project canonical records back to only
`role` and `content`, so provenance IDs never enter ordinary prompts.

No historical archive is rewritten. Untagged legacy exchanges remain usable as
non-authoritative compatibility context in every scope; AIFren does not guess
whether they were scenario or real-world. Malformed/unknown tagged exchanges
remain readable canonical data but are excluded from scoped prompt history and
new episode derivation. Prospectively tagged records are admitted only for the
exact active scope. Episode segmentation splits on every legacy/tagged or
scope-identity boundary, persists that identity with exact source ranges, and
filters entity/temporal retrieval by the same rule. The unscoped rolling summary
is not admitted once authoritative scoped assembly is available.

Unity's Scene Details/Current Context inspection surface renders the complete
already-backend-bounded active-scope snapshot in grouped actors/activity,
subjects, relations, derived effects/causes, and profile-baseline sections,
plus at most six active Open Threads. Its scroll region is bounded independently
of Settings layout. Clear/resolve/cancel/leave actions use UUID commands plus an
expected revision. The backend revalidates target ownership and scope, stores
an idempotent acknowledgement event, and returns the authoritative replacement
snapshot; Unity never changes visible continuity optimistically. These controls
close only their governed structured state and never delete dialogue, thread
provenance, or scenario contents. The always-small RP indicator remains an
independent presentation owner.

Open Thread natural references use a rebuildable derived identity, not a new
authority or knowledge graph. The backend derives a compact subject,
pending-event family, and evidence-backed aliases from each unchanged thread
description plus its bounded canonical user evidence. Scope, kind,
participant, lifecycle, and materially different pending actions remain hard
boundaries. One matching logical identity becomes governed reconfirmation;
ambiguous matches abstain. Existing semantically duplicate rows are never
deleted or silently rewritten. They form one bounded logical candidate, and a
later high-confidence resolve/cancel may close every member atomically while
preserving each row's provenance.

Proposal interpretation may inspect at most six prior canonical user turns and
900 characters from the contiguous active truth scope. Assistant text, legacy
untagged records, malformed provenance, and earlier scope segments are
excluded. The window supplies only closed reference frames for thread subjects
and adjacent RP enactment/labels; it cannot write state. Hypotheticals,
quotations, media discussion, stale references, and multiple plausible targets
still abstain. Vague/temporal lifetime episodic retrieval remains separate
research and is not solved by this bounded current-state layer.

## Presentation

The normal path is direct viewer rendering: background, direct VRM camera, then
Screen Space Overlay UI. Avatar View maps independent orientation X/Y/scale to
direct camera controls around a stable full-body baseline. The old RenderTexture
path is rollback/debug-only. UI visibility is overlay-only and never changes
the avatar viewport, saved framing, camera fit, or background cover behavior.

The optional Current Scene overlay is a lightweight, translucent left-side
projection with content-driven height and a bounded maximum. It groups
important current worn/held/equipped items, restrictions, capability effects,
and causes; it does not display database identifiers or provenance internals.
Scrolling remains available without a permanently visible scrollbar. The
overlay participates in global UI Hide and returns only when UI is visible and
its persisted setting is enabled. Scene Details remains the fuller bounded
development inspection surface.

Scene-UI event wording is derived from the accepted operation and relation
predicate/semantic family before body facet. Decorative wristwear therefore
renders as removing the worn item, while a wrist tether renders as releasing a
restraint. Snapshot rows and delayed transport echoes carry a stable canonical
message identity so one record renders once regardless of arrival order;
identical text with different identities remains distinct.

The RP scope label is anchored against the root canvas safe area at the actual
bottom-left in portrait and landscape. Its established hidden-UI visibility
policy is independent of chat input, settings, History, and Current Scene
layout.

UniVRM loads VRM 1.0 and VRM 0.x through the avatar-loading layer when a
`.vrm` or `.glb` container embeds VRM metadata. Plain generic GLB is not
supported. Managed VRM files remain one global reusable asset library; Unity
stores only a stable managed-asset selection per character, with the former
global choice as the compatibility fallback for characters without an
explicit preference. Model swaps preserve continuity state and global viewer
framing. Avatar lighting, backgrounds, and framing are shared presentation
state rather than per-model or character-specific settings.

## Managed asset safety

Managed libraries are content-addressed but ownership stays kind-scoped.
Filesystem cleanup canonicalizes and validates each target against exact managed
directories. It never follows metadata to an external source, recursively
deletes a directory, crosses a symlink boundary, or treats a similarly named
root as containment. Bad records are repaired safely; external files remain
untouched. This project-wide destructive-operation rule applies to future
character, memory, personality, import/export, and cache work too.

## Audio and dialogue

TTS generation is both playback ID and cancellation token. PTT invalidates it
before stopping the stream; synthesis checks the same token and cannot
resurrect stale audio. Explicit interruption and natural completion are distinct:
only matching natural completion retires active playback state. Subtitle events
consume this lifecycle but never own it.

Streaming early speech and direct/governed speech share one synthesis resource
manager. It preserves the unresolved utterance/chunk and exact ordering across
recognized accelerator resource failures, may retry after provider idle, and
can move Kokoro to CPU for the remainder of the runtime before retrying that
same unit once. Cancellation invalidates pending retry/failover work. Resource
classification is accelerator-neutral across PyTorch CUDA and HIP/ROCm error
shapes; ROCm confidence is currently injected/architectural rather than real
hardware validation.

Microphone shutdown is also bounded. Graceful PortAudio stop/close remains the
normal path; a close that exceeds the bound invokes owned abort/cleanup,
discards the capture, and exits the worker without transcription. Capture IDs
prevent a late close result from mutating a newer capture. When this produces
voice `ready` without a transcription/turn, Unity's explicit PTT presentation
state restores the pre-placeholder dialogue; `turn_started` retires that
restoration so generic or late ready events cannot roll back real generation.

Conversation remains raw/canonical. Presentation parsing styles complete paired
emotes in visible UI and removes them from speech/hidden subtitles. Hidden
subtitles are separate overlays. `HiddenSubtitlePresenter` is their single
presentation owner: it keeps timing-due words separate from visibly-presented
words, owns renderability/alpha/page swaps, and requires contiguous exact
global word ownership across page text and ranges. Pagination advances by the
actual adjusted page count, preventing gaps and overlap. Complete pages are
measured before reveal by an inactive measurement-only TMP. A single visible
TMP owns style and uses `maxVisibleWords`, avoiding duplicate render hierarchies
and per-vertex color mutation. Temporary edge peek suppresses only rendering; committed Show
cancels the session. Kokoro timing is optional validated metadata with
immutable fallback timing.

Dialogue parsing produces typed `PlainText`, `Emphasis`, and `Emote` spans
without changing canonical assistant text. Single-asterisk spans use
contextual ownership: an action-shaped or standalone roleplay segment is an
`Emote`, while emphasis embedded in spoken dialogue remains spoken regardless
of word count. Double-asterisk spans are emphasis unless owned by an outer
action. Once an outer action span is established, nested formatting cannot
close it: the entire outer action remains nonspoken. Physical model actions are
requested in `*...*`; parentheses remain ordinary prose. Whole-text and every
streamed prefix/split must project the same spoken sequence. Unity and the
backend TTS cleaner intentionally mirror this interpretation.

The configured WPM/WPS is the maximum normal visual reveal rate. Valid audio
timing may make reveal slower, but short, absent, or constrained speech cannot
force a large action-heavy response to reveal faster than the user's reading
preference. Explicit instant-text mode remains authoritative.

Generated assistant emoji/pictographic clusters are removed at the assistant
output boundary before canonical assistant persistence, presentation, and TTS;
user-authored emoji and ordinary Unicode/Japanese text remain canonical.
Emotes remain available to `AvatarGestureMapper`, which
requests semantic `AvatarGestureIntent` values from `AvatarAnimationController`
through standard Humanoid mappings; blink and lip-sync stay separate.

Whisper resource recovery follows the same ownership principle at its own STT
boundary: a recognized accelerator failure can retire GPU state, switch to
CPU/int8, retry the same captured WAV once, and remain on CPU for that runtime.
Capture identity prevents stale work from affecting a later PTT session.

The chat field is a fixed-height multiline `TMP_InputField` with a masked Text
Area. Text is vertically centered while it fits and switches to top-aligned
internal scrolling only when TMP's preferred height exceeds the viewport.

Ordinary dialogue uses the true inner TMP wrapping width when calculating
rendered height and ScrollRect content reach. Natural streamed completion,
skip-to-end, and persisted restoration therefore expose the final reveal unit
at a reachable true bottom; the previous undersized-content/RectMask clipping
path is fixed.

## Local transport and recovery

`backend_host.py` binds loopback port 8765 and forwards structured snapshots and
events; mutable conversation/memory objects and canonical file internals never
cross the WebSocket. Disconnect/reconnect UI is presentation only. Linux backend
recovery reuses a healthy checkout-owned backend and refuses unrelated listeners.

## Development performance diagnostics

Development builds keep a bounded, privacy-safe flight recorder: about 30
seconds of per-frame summaries, low-rate process/system/GPU samples, and
structural lifecycle markers. It records IDs, counts, timings, queue state, and
resource use, never dialogue, prompts, subtitles, memories, credentials, or
private paths. Automatic hitch/resource triggers and the `6666666` manual dump
write incident bundles under `/tmp/aifren-flight-recorder-<timestamp>/`; normal
rolling capture performs no high-frequency file I/O.

Synthetic microbenchmarks are supporting diagnostic evidence. Performance is
accepted only after ordinary Linux Development-player interaction with a
synthetic/test character through transport, context assembly, model,
canonicalization, speech, playback, Unity presentation, and persistence.

The intended release boundary is stricter: a release/1.0 player keeps the
diagnostic recorder off by default, instrumentation becomes cheap/no-op when
disabled, and no unbounded persistent log growth is allowed. A later explicit
Diagnostics opt-in may enable bounded retention, but it must reuse this central
recorder rather than creating another logging subsystem.

Future context-resource settings may expose a simple detail level plus advanced
working-token budget and model-capacity controls. The selector should report
core, recent raw, derived episode, and memory costs together with current and
recent prompt use; hardware UI may show measured VRAM used/free and cautious
estimates for higher context capacity. These are planned controls, not current
claims or fixed limits, and Local/Online must continue sharing the same
AIFren-owned selection semantics.
