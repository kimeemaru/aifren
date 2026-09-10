# Memory V2 store

## Authority boundary

Canonical conversation remains permanent source evidence. Memory V2 is normal
prompt-facing long-term-memory authority; only governed source-grounded lanes
establish truth. V1 is explicit compatibility rollback only. Normal V2 has zero
V1 prompt, learned-memory or summary authority and never silently selects V1 on
failure. CompanionMemoryRealizer consumes admitted propositions but owns no memory.

Episodes, FTS, embeddings and ANN are derived/rebuildable. Original SQLite Viewer
corrections, administrative evidence, accepted scene records and lifecycle history
are not disposable caches. Conversation JSON alone cannot reconstruct the whole
store. Preserve original authorities when rebuilding derived representations.

## Schema and ownership

The current schema is **21**. File stores use WAL, foreign keys, full synchronous
writes and bounded busy timeouts. `MemoryV2Store` owns versioned schema upgrades;
`repository.py` exposes bounded character-scoped operations.

- Events and evidence preserve source identity, hashes, speaker and Truth Scope.
- Durable claims retain corrections, supersession and validity/status history.
- Active State subjects retain identity across attribute updates. Relations have
  optional literal loci; independent capability causes compose without collapsing.
- Open Threads and scenario scopes retain their own lifecycle and authority.
- Historical occurrences retain exact canonical index/record/hash; they are not
  automatically current durable facts.

Schema 19 added independent canonical observation cursors. Schema 20 added
nullable relation loci without replacing prior relation/evidence identities.
Schema 21 added auditable source-bound recovery dispositions. A disposition is
not a successfully replayed mutation: it requires exact authoritative proof.
Ambiguous old operations remain unresolved and inspectable. Independent consumers
can progress and unchanged idle passes remain bounded.

## Normal initialization and catch-up

Normal startup does not require a seed or import V1. Existing observation and idle
owners continue bounded canonical pages, pending vectors and episode ranges.
`memory_v2_initialization` supports finite idempotent catch-up through those same
owners. Current authoritative rows are preserved; source-prefix edits fail closed,
and interrupted derived work resumes from durable identities. Subsequent launches
do not rebuild already-current lanes.

Prepared caches are optional derived acceleration/testing only. Strict source,
policy, scope and generation attestation must precede any additive installation.
A stale cache is not relabelled, and it cannot replace live corrections or state.

## Retrieval and answer admission

`retrieval.py`, `embeddings.py` and `ann.py` bound candidate work. ANN hits are
rechecked through SQLite character, provenance, scope and lifecycle filters.
Semantic similarity is not proof of a proposition. Retrieval health remains
separate from genuine absence; failed/budget-limited applicable work is unavailable.

A shared typed query decision carries current/history and anchored before/after
semantics. Answer evidence must prove source ordering relative to the requested
anchor; multiple possible predecessors clarify. Current facts cannot stand in
for historical evidence.

Historical projection selects at most two complete local source passages within
220 total source characters, retaining exact separate offsets. It does not expose
an unrelated prefix or fabricate a continuous quote. An immediate concrete
follow-up is restricted to the previous published answer's unique exact-source
anchor and permitted neighborhood, not a generic topic search. Unrelated input,
scope/character changes and unpublished/cancelled turns invalidate that handle.

The complete answer retains speaker, scope, polarity, modality and current/history
contracts. Local realization uses an immutable backend-owned core plus an optional
separately checked present reaction; unsafe reactions are discarded without repair.

## Episodes and maintenance

`EpisodeCompactionCache` is the shared generation/source-range validator for runtime
and Viewer. Accounts preserve exact source identities and lower dependencies.
Independent eligible ranges may advance beyond excluded gaps without crossing them.
No summary becomes truth merely because it matched a query. Derived generation
publication is atomic and preserves the previous valid generation on failure.

Explicit store CLI maintenance is available through `python -m memory_v2_store.cli
--help`; inspect the required path/character arguments before running it. Import,
export and reconstruction must be deliberate and scoped to data the caller owns.
Never use a whole-database replacement as character-scoped maintenance.

Earlier `*_shadow`, import and evaluation modules document research predating normal
V2 authority. Their opt-in names do not define the current default. In particular,
legacy V1 mirrors stay `legacy_unverified`; normal catch-up never promotes them.
