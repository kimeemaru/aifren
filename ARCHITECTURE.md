# AIFren architecture

## Ownership and publication

```text
Unity companion -> loopback WebSocket -> backend_host -> AssistantService
    canonical conversation -> V2 evidence / structured continuity
        -> typed admitted memory answer -> CompanionMemoryRealizer
        -> optional non-authoritative present reaction
        -> canonical assistant commit -> dialogue / TTS / subtitles
```

AssistantService owns turns, final validation, canonical persistence, cancellation,
PTT/speech and backend events. backend_host is a frontend-neutral transport adapter.
Unity owns product settings and presentation; it cannot directly mutate memory.
LLM, TTS, STT and embedding providers remain replaceable. Managed llama.cpp uses
`--logits_all false`; the server applies its selected chat template exactly once.

## Memory and current truth

V2 is the normal prompt-facing memory authority. Canonical conversation is permanent
source evidence, not replaced by retrieval. Original facts, Viewer corrections,
scene state, lifecycle records, Open Threads and scopes have dedicated owners.
FTS, MiniLM, ANN and source-ranged episodes are derived/rebuildable. Similarity or a
summary does not create truth. Original administrative state cannot be rebuilt
from conversation alone.

V1 is explicit process-local compatibility rollback. Normal V2 has no V1 prompt,
learned-memory or rolling-summary writes. V2 failure stays visible and never selects
V1 silently. Legacy V1-origin records in V2 are not themselves a V1 authority read.

Source admission preserves character, speaker, scope, polarity, modality and
current/history meaning. Before/after ordering uses canonical sources. Historical
projection stays at most two passages with 220 total source characters. Immediate
attribute follow-ups use the unique admitted source of the published prior answer,
not another record with a matching relation. Cancelled/unpublished turns establish
no anchor; switching, scope changes, unrelated turns and restart retire it.

A named-topic personal past-value callback after restart is a new governed recall
query. Its topic must match an applicable canonical assertion; competing accounts
clarify. It neither resurrects a one-hop anchor nor searches an entire episode as
one proposition. Unrelated ordinary questions retain ordinary routing.

CompanionMemoryRealizer retrieves nothing. It deterministically surfaces an already
admitted proposition with owner/tense/relation-safe variation. Optional commentary
cannot change the factual core or invent remembered circumstances. Rejection drops
only commentary without a new repair inference. Emergency safety responses remain
separate from ordinary successful memory realization.

Active State uses generic subjects, attributes, relations and literal loci.
Accepted application/removal changes the intended object and independent capability
causes; holding is not wearing. Current corrections outrank old supporting dialogue.
State, biography and future Relationship State are distinct.

## Character-local storage

CharacterRegistry centrally resolves UUID, storage layout, readiness, timeline and
paths. New directories use `sanitized-name--UUID`: character.json, personality.md,
conversation.json, memory_v2.sqlite3 and its WAL/SHM, explicit same-character V1
compatibility files and owned derived/attention state. Identity never comes from a
readable directory name. Database UUID/scope checks remain defense in depth.
Existing legacy layouts move only after confirmation. Missing/corrupt/wrong-owner
local storage is unavailable; no shared-store, root V1 or seed fallback is allowed.

CharacterOperationService inventories a selected UUID/revision, retires runtime
writers under a cooperative maintenance lease, journals progress, then publishes
the new pointer last. Selected migration builds a new empty schema from a consistent
SQLite snapshot, copying only owned rows and necessary metadata. It verifies
schema completeness, original IDs/status/provenance, per-table logical digests,
foreign keys and integrity. Unknown schema blocks instead of silently dropping data.
Derived FTS is rebuilt; ANN retains its normal validation. No whole shared-database
backup-and-delete scheme is used. A logical canonical namespace preserves source
keys across a deliberate location change.

Migration reports retained old application copies. Cleanup is separately confirmed
and preserves other characters. Reset removes selected learned original/derived/
recovery/attention continuity, changes the timeline epoch, and preserves profile,
avatar and framing. Delete removes identity/owned preferences, not shared assets.
The last deletion leaves an explicit empty library. Neither operation creates a
hidden archive or revives a seed/V1 import. Independent backups and forensic erasure
are outside these operations. Interrupted operations are explicit and resumable.

Runtime write leases fence old workers. Switch/reset events capture UUID, session
and generation when produced. Scene/Viewer mutations also require revision/action
tokens. A→B→A does not make an old A event current again. Empty snapshots replace
old arrays. History updates mark hidden views dirty; visible updates are coalesced.
The intermittent reported blank-panel issue remains unresolved; these fences must
not be weakened to hide it.

## Responsive context planning

`assistant.build_response_request` is shared by complete/streaming requests.
`conversation/governed_context.py` and `context_governor.py` coordinate existing
owners; they do not retrieve, mutate memory or infer new truth.

Required character/current-user authority, scope, temporal facts, applicable state,
capabilities and machine/memory obligations are preserved whole. Selection then
keeps an immediate whole exchange, admitted current continuity, older coherent
exchanges and optional bookkeeping. Exact source identity plus equivalent proposition
semantics permits deduplication; a newer thread status cannot be discarded merely
because its supporting source is old. Temporal facts have one representation.

Local model capacity defaults to 16,384; the complete-request operating target is
4,608 tokens. This deployment starting point is not a universal optimum or a smaller
model allocation. Required context can exceed the target within the hard ceiling;
hard overflow fails visibly. Output/framing reserves remain separate. Counting uses
bounded planning estimates plus mandatory/final local content verification where
available. Framing is estimated; content counting is not exact chat-template counting.
Unavailable tokenization uses the labelled conservative path. Cancellation stops
additional preflight work before inference.

The newest coherent suffix depends on content cost, not a semantic last-N rule.
V1 summary/count policy remains rollback-only; hygiene comparison horizons are work
ceilings. Canonical messages are never internally clipped or stripped of meaningful
roleplay to fit the budget. `AIFREN_CONTEXT_GOVERNOR=0` is an explicit process-level
composition rollback independent of V1/V2 authority. No fill-the-window policy is
restored. Diagnostics contain costs/counts/owners/reasons, not raw private dialogue.

The bounded CompanionContext seam carries escaped, non-authoritative salience data.
It contributes no explicit memory evidence. Recent Pulse is disabled; transactional
impulses are dormant with no producers. They are not new truth stores.

## Dialogue, speech and presentation

Natural/Roleplay is a delivery preference, not an identity edit. Only eligible
ordinary local requests change contract; constrained/machine/governed-memory
obligations retain their paths. Reviewed template support is fingerprinted; unknown
templates retain compatible role handling. Every added policy is budgeted.

Fresh output normalization and the shared dialogue span owner separate plain text,
emphasis and actions. Inline emphasis stays spoken; an outer action owns nested
formatting. Full/responsive speech project the complete accepted text before
segmentation, so chunk boundaries cannot change meaning. Canonical visible dialogue
may retain roleplay; hidden speech subtitles and synthesis omit nonspoken actions.

Responsive speech starts only after validation and canonical commit. One utterance
has ordered chunk/word/sample offsets, bounded work/audio queues and one CPU Kokoro
worker. Callbacks consume PCM without I/O or synthesis. Cancellation retires queued
and prepared work immediately; in-flight native work drains cooperatively. Failure
does not replay spoken words or pretend normal completion. Subtitle dwell/page state
never controls PTT readiness. HiddenSubtitlePresenter owns hidden visual progression;
CommittedSpeechTimeline accepts continuing timing without resetting the utterance.

ACT preview is opt-in. Prefix-only bounded fresh-output parsing strips actual
control markup before canonical/TTS/subtitle publication, buffers split prefixes,
and accepts plain dialogue immediately. Quoted/history/user/source markers remain
inert data. Invalid separable optional markup loses presentation, not safe dialogue.
No expression dispatch is allowed before accepted current persistence.

Automatic CPU expressions use accepted current prose, excluding factual memory
cores, quotation, reporting/code/control data. One asynchronous item/no backlog and
a deadline prevent publication/first-speech blocking. Manual/restrictions > valid
explicit ACT/structured channel > admitted automatic > eligible legacy/no-change.
RP parsers remain compatibility paths, not competing owners for an explicit channel.

Automatic expressions are temporary overlays: newer replies, interruption, switch,
disable and expiry release only that owned contribution. Lease checks stop old
cleanup clearing new requests. Equivalent proposals extend without restarting blends.
Release is not a neutral truth claim. Blink, gaze, lip-sync and explicit/manual state
remain independent. Unity maps semantic expressions/gestures to concrete VRM behavior.
Direct rendering is normal; RenderTexture is debug rollback. Framing is scoped to
character/asset/orientation, restored after the matching avatar-ready event; drawers
and global UI hide never resize the avatar.

## Source, runtime and validation boundaries

Application code comes from this checkout. `AIFREN_RESOURCE_ROOT` selects existing
model/static resources; `AIFREN_DATA_ROOT` selects mutable application data. Local
launcher `.env` may choose an external `AIFREN_PYTHON`. These paths never authorize
moving data or importing code from a data directory. Seeds are optional first-install
inputs, never automatic reset recovery. Public builds retain reviewed public assets.

Synthetic structural tests, installed-model checks, native tests and player proof
are distinct evidence. Development flight recording is bounded/privacy-safe; release
diagnostics default off. See the [developer guide](docs/DEVELOPER_GUIDE.md) and
[delivery design](docs/NATURAL_COMPANION_DESIGN.md) for commands and limitations.
Historical `*_shadow` and evaluation modules may remain disconnected/opt-in research;
their names do not define normal memory authority.
