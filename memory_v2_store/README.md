# Memory V2 structured continuity store

`memory_v2_store` is AIFren's character-scoped SQLite continuity
infrastructure. The current schema version is **17**. File databases use WAL,
foreign keys, full synchronous writes, and a bounded busy timeout; tests may use
isolated in-memory databases.

## Authority boundary

Canonical conversation remains the permanent source record. Memory V1 remains
the broad/general prompt-facing memory authority. Memory V2 is neither a
replacement for the raw archive nor one monolithic universally authoritative
retriever.

The store currently supports several deliberately separate lanes:

- source-ranged, rebuildable episode accounts and bounded retrieval;
- prospective truth-scope provenance and isolation;
- closed-schema governed durable facts with evidence and supersession;
- Open Threads for unresolved/waiting/planned continuity;
- Active State scene subjects, attributes, relations, lifecycle, and
  capability derivation;
- rebuildable FTS, embeddings, ANN candidates, and privacy-safe structural
  diagnostics.

Only explicitly promoted governed lanes may supply authoritative structured
truth. Generic V2 retrieval remains subordinate/background context and fails
open when absent, stale, corrupt, or incompatible. Relationship State is a
separate deferred subsystem.

## Source and derived data

Events and exact evidence ranges preserve provenance. Claims, lifecycle/status
history, source-ranged summaries, semantic relations, and current-scene rows
are character and truth-scope bound. FTS, embedding, ANN, episode, era, and
capability projections are versioned/rebuildable derived data; deleting or
rebuilding them never licenses editing canonical JSON.

`memory_v2_episode_compaction.py` creates bounded lower episode accounts and
optional conservative contiguous-era accounts. Each lower account carries
source fingerprints and at most six verified continuity anchors. A failed or
uncertain retention check keeps lower/raw context. The explicit
`scripts/rebuild_episode_compaction.py` command rebuilds only this derived lane
for an authorized character and never runs automatically at startup.

`retrieval.py`, `embeddings.py`, and `ann.py` provide bounded candidate
lanes. ANN candidates are always rechecked through SQLite character,
provenance, scope, and lifecycle filters. Zero retrieval is valid.

## Active State

Active State stores sparse interaction-relevant current reality, not an
inventory or simulated world. Governed operations establish subjects, set
attributes/relations, clear, replace, transfer, locate, correct, retire, and
explicitly reactivate strongly identified subjects. Mutations are
evidence-bound, actor-aware, scope-aware, atomic, and non-destructive to
history.

Semantic current relations are the primary authority for wearing, holding,
attachments, obstructions, equipment use, and environmental consequences.
Compatibility subject attributes are projections whose parity is checked in
the mutation transaction. Derived capability effects compose all current
causes across perception, communication, manipulation, locomotion, awareness,
and posture.

Subjects move non-destructively through current, dormant, and retired
lifecycle states. Active relation/capability sources cannot retire. Generic
historical objects reactivate conservatively; a lone old generic noun match is
not sufficient identity.

## Import and maintenance

`production_import.py` is a tolerant, idempotent importer that leaves
`memories.json` untouched. `repository.py` is the bounded runtime boundary,
and `cli.py` exposes explicit `status`, `migrate-v1`, `export`,
`integrity-check`, and privacy-safe `retrieval-report` operations.

Tests use purpose-built neutral data in temporary/generated stores. SQLite,
WAL/SHM, ANN/index, cache, and diagnostic artifacts are never committed.
