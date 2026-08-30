# AIFren developer guide

Read [PROJECT.md](../PROJECT.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and
[AGENTS.md](../AGENTS.md) before changing code.

## Prerequisites and run

- Use the repository `.venv-aifren` Python environment.
- Use Unity 2022.3.62f3 LTS for the companion client.
- Online providers need user-supplied credentials; Local does not. Having no
  configured provider is a recoverable state: the player must still open and
  Settings must remain accessible. Do not commit credentials. Local
  models/assets are machine-local and ignored.

Start the loopback backend from the repository root:

```bash
.venv-aifren/bin/python backend_host.py
```

The endpoint is `ws://127.0.0.1:8765`. Linux developer actions are available
through `scripts/aifren_dev_linux.sh current` and `rebuild`; the GUI/desktop
launcher uses the same owned-backend lifecycle and must not kill arbitrary port
8765 listeners.

`scripts/aifren_dev_launcher_linux.py` is permitted developer-only Tk tooling:
it wraps the Unity shell launcher, exposes normal/Development start modes and
reset controls, and displays launcher diagnostics. It is not the removed
Tkinter companion frontend. Install its per-user AIFren Dev application entry
with `scripts/install_aifren_dev_launcher_linux.sh` (the `...shortcut...`
spelling is retained as a compatibility alias).

Local Linux development builds that intentionally include ignored presentation
assets use:

```bash
AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 scripts/build_aifren_linux.sh --development
```

The ordinary build allows the tracked bundled default VRM and rejects any
additional ignored presentation assets. Preserve that boundary.
Windows launcher/build behavior is separate from Linux X11 behavior.

The local Development player is written to:

```text
unity/AIFrenUnityPoc/Builds/LinuxDevelopment/AIFrenPoc.x86_64
```

## Local model runtime

The Unity **Local** setting discovers ignored `models/llama/*.gguf` files while
the server is off, and can start the selected one through the installed
llama.cpp-compatible server. It owns and stops only that process. A responding
endpoint is instead treated as external: it is usable but never stopped or
restarted. This remains one provider-neutral context path.

An external development server is still supported when needed:

```bash
.venv-aifren/bin/python -m llama_cpp.server \
  --model /absolute/path/to/model.gguf --host 127.0.0.1 --port 8000 \
  --n_ctx 16384 --logits_all false
```

Choose Local in Unity, use `http://127.0.0.1:8000/v1`, discover/select the
model, and keep model-context limits as a runtime capacity choice. Do not
lower AIFren's generic recent-context configuration merely for one small test
model.

The normal managed model directory is ignored. Its checked-out development
machine currently may contain small and larger GGUF smoke models, but no model
filename is hardcoded into the application. On a supported NVIDIA host, Linux
setup provisions an ignored per-venv CUDA compiler and builds the pinned
`llama-cpp-python` version with `GGML_CUDA` plus a portable CPU baseline; it
detects the host compute capability and verifies
`llama_supports_gpu_offload()`. This avoids prebuilt CUDA wheels whose native
CPU baseline can require unsupported instructions. Without NVIDIA support it
installs the CPU fallback. Managed launch requests full GPU-layer offload only
after that verified capability, and reports `CUDA` only after the server
confirms actual offload. The default managed capacity is 16,384 tokens with an
explicit 48k-character provider budget that drops oldest raw turns before
admitted authoritative continuity or the latest user turn. Override either
capacity through the documented local runtime environment variables only when
the selected model/hardware requires it.

Every AIFren-owned `llama_cpp.server` launch must explicitly include
`--logits_all false`. Ordinary AIFren chat does not request per-prompt-token
log-probabilities, and retaining them can allocate several GiB on
large-vocabulary models without changing normal output semantics.

Ordinary Local assistant turns receive one explicit fresh seed from system
randomness. Qwen3.5 local configurations apply the vendor-recommended
non-thinking/general preset (`temperature=0.7`, `top_p=0.8`, `top_k=20`,
`min_p=0.0`, `presence_penalty=1.5`, `repeat_penalty=1.0`). Online and unrelated
local model families are unchanged. Derived episode rebuilds deliberately use
stable provenance-derived seeds instead; never reuse that deterministic policy
for live dialogue.

Switching Local to Online stops only the process owned by AIFren. Switching
back applies the saved Local model and starts it only when Auto-start is set.
External OpenAI-compatible endpoints are never terminated. A missing Online
key must not prevent mode selection or silently fall back to Local, and Local
failure must not silently select Online.

An owned launch writes `.aifren_managed_local_runtime.json` beside the backend
application root. The file is ignored, mode `0600`, and contains no credentials;
it exists only so the next backend can prove a surviving process after an
abnormal teardown. Recovery requires the same boot ID, PID/start ticks,
process/session group, per-launch environment nonce, exact argv, endpoint,
model path, context size, and thinking configuration. Healthy exact matches are
reused. Proven stale/incompatible matches are stopped by their owned process
group and restarted. Any failed identity proof is discarded without signaling
the PID. Never weaken this to port-, command-name-, or model-name matching.

## Character switching and avatar preferences

`characters/registry.json` is local canonical selection data and is ignored by
Git. On first use, the registry creates one deterministic ID for the legacy
`characters/default` character without moving its root-level conversation,
summary, or Memory V1 files. New characters use UUID directories and keep their
own character definition, personality, conversation, summary, Memory V1, and
Memory V2 state.

Create/select commands are frontend-neutral transport operations. Selection
first invalidates active generation and speech, waits for the serialized turn
boundary, saves the old owner, constructs the replacement character state, and
then publishes one replacement snapshot on the existing socket. A failed rebind
restores the prior registry selection. LLM, STT, TTS, model processes, and
device resources are runtime-owned and survive the switch.

Managed VRM files remain in one reusable Unity asset library. Unity stores only
the selected managed-asset ID (or bundled-avatar choice) under a character UUID
in local presentation preferences. Characters without an explicit preference
use the former global selection as a compatibility fallback. Deleting a managed
avatar repairs affected preferences without deleting character identity or any
canonical continuity data.

## Continuity V2.1 scope provenance and controls

New successful canonical user/assistant pairs carry an identical
`truth_scope` object captured from the authoritative V2 repository before
generation. Scope transitions are applied only after that pair is saved. Thus
the scenario-entry pair belongs to real-world scope, the scenario-exit pair
belongs to that scenario, and the following turn reflects the new scope.
Failed or replaced turns retain the existing rollback behavior and never leave
a half-persisted provenance pair. Do not infer scope from dialogue or provider
output, and do not expose `scope_id` in provider messages or Unity labels.

Historical compatibility is deliberately conservative:

- records without `truth_scope` are legacy, non-authoritative context usable in
  every scope; no migration guesses their meaning;
- valid prospective records are compatible only with the exact active scope;
- malformed or unknown tagged records remain readable in the archive but are
  excluded from scoped prompt/episode use;
- the legacy rolling summary is omitted during authoritative scoped assembly
  because it has no record-level scope provenance.

Episode rebuilds split before every incompatible scope boundary and store the
scope identity beside their exact canonical source range. Entity and temporal
retrieval select only legacy plus exact-active-scope episodes and source spans.
This is a prospective contamination boundary, not generic Memory V2 authority,
and legacy dialogue may still contain pre-V2 scenario material that cannot be
mechanically identified.

Unity consumes only the backend's bounded continuity snapshot. Scene Details
renders the complete already-bounded actor/activity, subject, relation,
capability/cause, profile-default, and active-scope sections in a scrollable
inspection surface. The compact Current Scene overlay shows only important
current worn/held/equipped items, restrictive relations, conditions, and
capability causes. It is lightweight, portrait-first, and participates in the
global UI Hide lifecycle.

Activity clear, thread resolve/cancel, scenario exit, and scene controls send a
UUID command, snapshot revision, scope, and opaque action token where required.
Wait for the matching backend acknowledgement before changing display state. A
lost acknowledgement is retried with the same command UUID after reconnect; a
new UUID with a stale revision is rejected. Controls own only current
structured continuity: they never delete canonical conversation, closed thread
evidence, or persisted scenario history.

Natural Open Thread reference resolution is derived on demand from the
original compact description and bounded canonical user-evidence rows. There
is intentionally no normalized-identity migration: lexical subject aliases and
pending-event families are rebuildable after restart, subordinate to exact
evidence, and constrained by truth scope, kind, participant, and lifecycle.
Before opening a thread, one logical match becomes reconfirmation, no match may
open, and multiple logical matches abstain. Semantic duplicate rows from older
runtimes remain stored; a later unambiguous closure can govern all members of
the bounded logical group without deleting provenance.

Multi-turn reference interpretation reads only the preceding contiguous
same-scope canonical user records, capped at six turns and 900 characters.
Never expand this to assistant prose or generic conversation authority. The
window exists only to combine closed proposal frames such as adjacent RP
enactment plus scenario label; all resulting mutations still pass through the
existing proposal validators and store lifecycle APIs.

## Active State production workflow

Active State is sparse current reality, not general memory or a world
simulator. The governing principle is: **persist the facts that matter; infer
the experience**. Context helps the model reason; Active State preserves
continuity.

The closed scene mutation families establish subjects, set attributes, set or
clear relations, replace, transfer, locate, correct, retire, and explicitly
reactivate strongly identified subjects. Attribute change and replacement are
different operations. Proposals retain exact canonical user evidence, actor,
scope, and source span; a complete batch validates before one store
transaction. Semantic relations are primary for wearing/holding authority, and
compatibility attributes are projections checked for parity.

Current relations derive one composed capability envelope across perception,
communication, manipulation, locomotion, awareness, and posture. Derivation
uses relation meaning, actor, facet/side, quantity, explicit consequence, and
environment/equipment state—not item-name flags. Multiple causes remain
inspectable and clear independently. A capability restriction removes a
channel; it does not make model generation unavailable.

Unavailable-hearing admission is earlier than output validation. When the
interaction explicitly establishes that the current utterance is inaudible,
retain its canonical record and structural marker but feed only the inaccessible
projection into understood context and skip Memory V1/V2 semantic observers,
durable facts, Open Threads, summaries, and episode derivation. Do not apply
this rule to merely constrained hearing or assume every typed message is spoken.

The production turn order is:

```text
canonical user evidence
-> validate and atomically mutate current state
-> recompute capabilities
-> build response requirements from the post-mutation snapshot
-> generate / validate / bounded repair
-> persist and publish
-> presentation and TTS
```

Ordinary successful mutations create `must_respect` facts, which prohibit
contradiction without requiring mechanical restatement. Direct deterministic
questions create `must_communicate` facts. After one bounded repair, any
factual fallback is constructed from authoritative state; unknown never means
none.

The compact overlay X is an in-world interaction: the backend validates and
applies the selected cause, then constructs one non-authoritative natural
scene event from the accepted operation, saves it as a synthetic user event
with `scene_ui/generated_event` origin, and offers one reaction from the
post-mutation snapshot. Never reparse that generated event as evidence. The
detailed Scene Details/admin correction path remains silent and creates no
canonical roleplay event.

Build scene-event wording from operation plus relation predicate/semantic
family before facet. Transport snapshots and event echoes must carry the same
stable canonical message identity; deduplicate by identity, never content.

Relationship State is deferred. Do not infer it from Active State, Open
Threads, memories, or proactive response history.

## Context Hygiene V1

The raw conversation archive remains complete and canonical; it is not the
same object as the transient provider working context. Before recent dialogue
is added to a request, `Conversation.build_context()` runs a bounded local
lexical hygiene pass over at most the configured 100 recent messages. It never
filters standalone user messages and always keeps the latest user message and
newest assistant message.

An older assistant message is considered self-redundant only when it contains
at least ten normalized words, has a length ratio of at least 0.68 against a
nearby newer assistant response, and passes conservative ordered sequence plus
3/4-gram overlap thresholds. A user echo requires at least ten words, assistant
length no greater than 1.35 times the preceding user message, at least 0.72
trigram and 0.60 four-gram directional coverage, and no more than 0.30 novel
assistant vocabulary. A single echo is retained as a possible intentional
quotation; only repeated echo behavior permits an older echo to be omitted.
These thresholds intentionally prefer false negatives over erasing legitimate
recurring subjects, facts, or character vocabulary.

An older complete exchange can be removed as a pair only when its user message
and assistant response both strongly match a newer exchange within the next
eight exchanges. Exact normalized user text qualifies even when short; a
non-exact user match requires at least five words, length ratio at least 0.82,
sequence similarity at least 0.90, trigram containment at least 0.85, and
four-gram containment at least 0.75. The assistant side must independently pass
the stricter existing self-redundancy test. The newer representative is
protected from assistant-only suppression, so pair filtering never leaves its
replacement partial or synthetic. Unique questions, corrections, facts, topic
changes, and same-topic exchanges with different answers remain verbatim.

Development flight-recorder `context_hygiene` events contain only candidate,
suppression, echo/redundancy/run, message, character, and approximate-token
counts, including separate assistant-only and exchange-pair suppression totals.
Exact local prompt-token counts remain available from the generic model timing
marker. No dialogue, n-grams, or embeddings are logged.

## TTS baseline

Kokoro is the active production baseline. In Audio Settings, **Speak while
response is generating** is persisted and defaults on. The enabled path keeps
canonical response assembly untouched, admits only complete semantically safe
sentences, sends the first sentence immediately, and groups small later pairs
where available. One bounded synthesis worker feeds one continuous per-turn
PortAudio stream. Turning the setting off restores one natural
`whole_response` synthesis after model completion.

Streaming and direct/governed synthesis share the same ordered Kokoro resource
manager. Recognized accelerator OOM/resource pressure preserves the unresolved
chunk, performs the bounded retry, and can move Kokoro to CPU for the rest of
the runtime before retrying that exact chunk. This classification is based on
PyTorch accelerator behavior rather than `nvidia-smi`; CUDA and injected
HIP/ROCm cases share the contract, while real ROCm hardware remains untested.
Interruption cancels all pending retry/failover work.

Assistant emoji are removed before assistant persistence/presentation, without
altering user-authored emoji or ordinary Unicode. Speech projection omits an
entire outer roleplay action even when nested emphasis appears inside it;
ordinary emphasis in spoken text remains spoken. Whole-response and streaming
projection must remain byte-equivalent after semantic cleanup. A recognized
Whisper accelerator failure retries the same WAV once on CPU/int8 and leaves
STT on CPU for the runtime.

`AIFREN_KOKORO_EARLY_SPEECH` is an optional developer override, not required by the
launcher. When set, it overrides the effective value while preserving the saved
user preference; leave it unset for normal Development use. Earlier incremental
attempts must not be restored from history: only the current separate semantic
projection preserves exact fragment whitespace, completed emote/action spans,
spoken emphasis, turn identities, and bounded queues.

Piper is removed. Experimental runtimes, voice references, and transcripts are
not part of the public repository. Text generation remains usable when speech
is unavailable.

## PTT close recovery and dialogue presentation

PortAudio microphone stream shutdown can block inside `stream.close()`. The
normal graceful path remains preferred, but owned shutdown has a 250 ms bound;
timeout uses the existing safe abort/cleanup path, discards that capture, and
returns PTT to a usable state without WAV preparation, Whisper, or a bogus
transcription. Capture IDs reject stale completion from an older close worker.
Do not lengthen this bound, kill Python threads, or transcribe discarded audio
without new measured evidence.

Unity treats the Thinking dialogue shown after PTT release as an explicit
capture-owned placeholder. A recoverable no-turn return to voice `ready`
restores the previous dialogue (or clears an empty prior state). A valid
transcription keeps Thinking until `turn_started`, which retires restoration
authority so late cleanup cannot roll back newer dialogue.

Ordinary long dialogue is measured using TMP's true inner wrapping width; the
ScrollRect content height and completed-response bottom restoration use actual
rendered geometry. Preserve natural completion, skip-to-end, and persisted
restore coverage whenever changing reveal/layout code.

## Validation

```bash
.venv-aifren/bin/python -m unittest discover -s tests -v
.venv-aifren/bin/python -m unittest tests.test_tts_providers tests.test_assistant_service -v
.venv-aifren/bin/python test_tts.py
git diff --check
```

For Unity work, run relevant EditMode tests and build the target player. Let a
`-runTests` batch invocation exit through the Test Runner; adding an immediate
`-quit` can end the process before it publishes `-testResults`. Require result
XML or record the exact runner failure, and never treat compiler errors as
validated. Manually test text, PTT while
idle/playing/synthesizing, natural TTS completion then next PTT, reconnect, UI
hide/show, portrait/landscape, and asset-library flows.

For dialogue/presentation changes, include focused tests for
`DialoguePresentationParser`, `SubtitlePagination`/`SubtitleTimingPlan`,
`HiddenSubtitlePresenter`, `ChatInputFieldLayout`, `AvatarGestureMapper`, and
`AvatarAnimationController` as applicable.

Performance acceptance is an ordinary Development-player conversation using a
synthetic/test character through Unity input, loopback transport, real context
assembly, managed model, canonicalization, TTS/playback, presentation, and
persistence. Unit tests are reproducible evidence, not proof that the
daily-driver loop is healthy. Never use protected/private character or voice
data for automated or manual diagnostics.

For Active State, layer validation deliberately. Parser fixtures prove only
proposal behavior. Also exercise production-shaped wording through
`AssistantService`, post-mutation response requirements, transport artifacts,
Unity reducers/EditMode fixtures, accumulated-state restart sessions, direct
queries, constrained responses, and deterministic temporary-store sequences.
A real Development-player smoke remains the product acceptance gate, and every
reproducible manual failure should become a permanent regression. Safe fallback
is not equivalent to immersive success.

## Guardrails

- Unity is the only production user-facing frontend. Keep backend services and
  the loopback protocol frontend-neutral; there is no Tkinter application path.
- Do not put subtitles/timing on the TTS/PTT critical path.
- Keep hidden-subtitle ownership deterministic: page text/ranges must cover
  every spoken word exactly once in global order. `HiddenSubtitlePresenter` is
  the sole production owner of renderability, alpha, page swaps, and
  transitions; one inactive TMP may measure pages but must never become a
  second presentation buffer. Temporary UI peek may suppress rendering but must
  not cancel the subtitle session.
- Keep typed dialogue semantics aligned in Unity and Python: single markers are
  action/emote spans when action-shaped or standalone roleplay segments;
  otherwise inline emphasis remains spoken regardless of word count. Double
  markers are emphasis unless owned by an outer action. Nested formatting does
  not close that action or leak its remainder into TTS. Parentheses remain
  ordinary prose.
- Keep the chat input's masked multiline viewport. It centers fitting content
  and only switches to top-aligned internal scrolling on actual height overflow.
- Gesture calls are semantic `AvatarGestureIntent` requests over Humanoid
  mappings; do not couple character identity or specific VRM hierarchy names
  to gesture behavior.
- Do not reverse direct rendering into UV crop framing.
- Model/background swaps must not mutate character identity or canonical data.
- Never trust managed-library metadata as deletion authority: validate canonical
  containment and exact kind directory first.
- Memory V2 generic retrieval remains shadow-only until a Memory Viewer/Editor
  and explicit promotion decision exist. The only production structured lanes
  are the governed Active State, Open Thread, and closed-schema durable-fact
  paths; do not broaden them by treating arbitrary V2 claims as prompt truth.
- Derived episode compaction is a narrow, non-authoritative working-context
  seam, not Memory V2 promotion. It uses exact source-ranged provenance and
  fails open when its cache is absent, stale, corrupt, or version-incompatible.
  A conservative second derived level may consolidate at least four contiguous
  selected lower episodes only when the configured provider confirms they
  repeat one temporary interaction mode. A separate rebuild-time retention
  verification must pass against the covered lower accounts before the era is
  selectable; missing details, uncertainty, provider/parser errors, and stale
  gate metadata all retain the lower accounts. The prompt admits either a
  verified era account or its covered lower rows, never both.
  Rebuild it explicitly for an existing authorized test character with
  `.venv-aifren/bin/python scripts/rebuild_episode_compaction.py`. The command
  uses the configured provider, writes only derived V2 rows, and prints only
  structural/numeric results. Do not run it against private character data.
  Lower-episode rebuild requests use a deterministic explicit seed derived from
  source/version/compactor provenance. The seed and non-secret compactor
  identity are stored in derived metadata; unchanged local llama rebuilds must
  be byte-identical. This is separate from ordinary assistant requests, which
  continue receiving fresh random seeds. Online-provider rebuilds retain the
  same provenance but are only reproducible if that provider guarantees seeded
  determinism. Switching the chat provider does not invalidate a valid cache;
  an intentional rebuild records the compactor used for that new generation.
  Application startup never performs a series of synchronous compaction calls;
  ordinary-user migration should eventually be a bounded background job with
  atomic publication.
  Lower episodes additionally extract at most six source-grounded continuity
  anchors, verify their preservation, permit one deterministic refinement, and
  use a bounded source-grounded fallback if necessary. Anchor metadata remains
  derived and non-authoritative. Purpose-built temporary-store regressions
  protect prompt selection: prompt savings do not excuse continuity or
  topic-reversion regressions.
- Active State is sparse, mention-driven current-scene data—not a world
  simulator. Preserve unaffected facts; do not infer unseen objects, physics,
  expiry, or time-driven changes. Contextual extractors may only submit bounded
  proposals backed by persisted same-character evidence; backend validation,
  lifecycle, and actor scope remain authoritative.
- Keep user, companion, shared context, and current scene subjects distinct.
  Assistant-generated prose alone is never authoritative state evidence. New
  scene/actor fields require governed attributes, bounded reads, deterministic
  fixtures, and typed relevance-gated prompt admission only.
- Keep sleep policy deterministic at its capability boundary. User sleep
  suppresses proactive output; companion sleep bypasses ordinary awake
  generation but may use the bounded VN-reaction curator. Backend-owned pose,
  gaze, reaction, and speech-mode intents constrain Unity; explicit wake
  evidence is the only transition back. PTT invalidation must cover proactive
  TTS using the same speech-generation boundary as ordinary turns.
- Current scene relations are a closed actor/facet/predicate/cause contract.
  Derive the shared sensory/communication/manual/locomotion/awareness/posture
  envelope from relation semantics, affected region/side, and explicit
  consequence—not object-name flags. Removing one cause must preserve other
  active causes and unrelated capability domains. Preserve truth scope,
  canonical evidence, relation/attribute parity, and bounded exact reads.
- Scene-subject lifecycle is non-destructive. A relation source is current and
  cannot retire; a relation-free subject may become dormant, and the oldest
  eligible dormant rows retire under the closed salience/count budget. Only an
  explicitly unique distinct retired subject may reactivate. Never infer an old
  generic cup is a newly mentioned cup. Detailed Scene Details/admin clears
  are silent, revision guarded corrections. The compact Current Scene overlay
  X is intentionally different: it closes the selected cause first, publishes
  post-mutation state, then records one backend-rendered synthetic USER scene
  event and offers one natural reaction. The frontend supplies only the opaque
  token; generated event prose is never reparsed into authority. Both paths
  close causes—not derived capabilities—and preserve historical evidence.
- Proactive polling is never eligibility. One unresolved governed thread,
  minimum interaction gap, availability policy, setting, cooldown, and ignored
  backoff must pass before generation. Supply exactly one bounded reason and
  persist only displayed assistant prose in canonical conversation. Apply the
  separate startup grace, keep background generation invisible until a draft
  is publishable, and count failed attempts for throttling; never announce a
  turn without a terminal lifecycle.
  The interval setting is a minimum opportunity gate, not a timer; unanswered
  messages increase AFK backoff and a real user turn resets it.
- Durable curation is proposal-only and closed-schema. Accept only canonical
  real-world user evidence; preserve superseded history; abstain on past-only,
  hypothetical, quoted, negated, malformed, or ambiguous claims. V1 dedup may
  remove only a clear duplicate of a V2 fact admitted on that request.
- Keep History/Log hierarchical and bounded: Year -> Month -> Day -> paged
  messages. Canonical/local message state updates immediately, hidden panels
  only become dirty, and visible event bursts coalesce. Never instantiate the
  lifetime archive as one TMP hierarchy. Future transport pagination may
  reduce the initial canonical snapshot without changing viewer semantics.

## Diagnostics

With `AIFREN_PERFORMANCE_TIMING=1`, development logs include PTT release/STT
final, accepted turn, provider request/first raw and canonical deltas, first
complete sentence, the first 160 received characters, provider completion, TTS
submission/synthesis, playback start, and natural completion. A compact summary
reports `STT final -> accepted`, `accepted -> first text`, `accepted -> LLM
final`, `LLM final -> TTS submit`, TTS synthesis, and `accepted -> first audio`.
Normal logging stays quiet. Diagnostics never include conversation content,
credentials, or private paths.

Linux Development players automatically enable a bounded performance flight
recorder after connecting. It keeps roughly 30 seconds in memory, samples
process/system state at 5 Hz and NVIDIA GPU state at 1 Hz, and writes nothing
until an incident. A frame over 100 ms, three frames over 50 ms within two
seconds, a PortAudio-underflow burst, or available RAM below 1 GiB freezes the
pre-incident window; the recorder then collects ten seconds more and writes
`/tmp/aifren-flight-recorder-<timestamp>/`. Type `6666666` while the chat field
is not focused for an immediate manual dump. Captures contain structural IDs,
counts, timings, and resource metrics only—never conversation/prompt/subtitle
text, character data, credentials, or protected paths. Release players do not
compile or start this recorder. The intended 1.0 behavior is likewise
diagnostics off by default, no unbounded persistent logging, and at most an
explicit bounded Diagnostics opt-in using this same central infrastructure.

The recorder identified two distinct former failures: unnecessary all-token
llama logits retention caused the swap storm, while lifetime-scale History TMP
rebuilds caused repeatable stalls adjacent to TTS start. History now uses the
bounded Year/Month/Day/page hierarchy; do not reintroduce lifetime row
construction. PortAudio was not responsible for that former repeatable hitch.
Smaller Console population costs remain a separate presentation concern.

Future context-resource UI may expose Low/Balanced/High/Custom detail, a
working-context token budget, advanced model `n_ctx`, recent prompt statistics,
prompt composition, measured VRAM, and cautious capacity estimates. None of
those controls exists yet. Keep model size separate from context capacity, and
keep the underlying selection provider-neutral so Local and Online differ only
by their configured budget/capacity.
