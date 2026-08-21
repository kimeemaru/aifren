# Memory V2 SQLite shadow store

This is an isolated, built-in-`sqlite3` storage foundation. Its normal
benchmark path never reads or writes `conversation.json`,
`conversation_summary.json`, or `memories.json`. The optional
`memory_v2_shadow.py` development helper explicitly imports a disposable,
Git-ignored snapshot for observation only; it is never authoritative and never
writes V1 data.

Schema version 4 contains canonical character-scoped events and derived claims,
evidence, relations, append-only status history, and source-ranged summaries.
File databases enable WAL,
foreign keys, full synchronous writes, and a five-second busy timeout. In-memory
test databases use SQLite's in-memory journal because WAL is unavailable there.

`importer.py` imports only the versioned synthetic Memory V2 benchmark fixture.

`v1_import.py` is an explicitly invoked, strict reader for a disposable V1
JSON shadow import. It never runs at AIFren startup and never modifies source
JSON. Rollback is deleting the generated SQLite database, manifest, and report.

`retrieval.py` is an isolated, non-production hybrid retrieval engine.
Its FTS index is rebuildable derived state over eligible source-backed claims;
it is never canonical authority and is rebuilt when simple count drift is found.

`embeddings.py` adds an explicit, lazy local MiniLM embedding provider and
lifecycle operations. `claim_embeddings` is derived SQLite state keyed by
character and claim, with model/dimension/normalization/preprocessing and
source-content fingerprints. It is only built on an explicit synthetic-store
operation, rejects stale/failed/incompatible rows, and can be deleted and
rebuilt without changing canonical claims or source JSON.
