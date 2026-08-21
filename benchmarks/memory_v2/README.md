# Memory V2 deterministic benchmark

This package evaluates candidate memory systems without modifying AIFren's
runtime memory implementation or reading personal persistence files.

## Components

- `fixtures.py` — versioned synthetic canonical events, gold claims, retrieval
  cases, and deterministic large-history generation.
- `models.py` — source/provenance-safe benchmark contracts.
- `metrics.py` — retrieval and consolidation metrics.
- `adapters.py` — reference oracle and a thin structural adapter around current
  production Memory V1 ranking.
- `harness.py` — lightweight latency and peak-memory measurement.
- `run.py` — optional command-line runner.

The core fixture covers one-time and repeated facts, corrections/supersession,
historical/current contradictions, temporary and expired states, shared
experiences, relationship milestones, planned/completed/cancelled/unknown
events, jokes and sarcasm, hypothetical/ambiguous statements, similar names,
negative retrieval, long-past needles, and two isolated characters. It contains
no user data. Hypothetical, sarcastic, and unresolved statements deliberately
have no gold claim: future extractors must not treat them as established facts.

The current V2 fixture also defines retrieval contracts for exact identifiers,
aliases/collisions, assistant-query contamination, visible-history duplication,
explicit repeats, channel/deduplication limits, abstention, high-importance
irrelevance, temporal plans, profile-versus-episodic types, reinforcement,
embedding lifecycle states, bounded relationship expansion, FTS punctuation,
and privacy-safe traces. These are deterministic safety contracts, not claims
about semantic quality. Cases marked `deterministic_only=False` require a
declared real embedding-model evaluation before a semantic-quality conclusion.

Future retrievers must cap every candidate channel and final injection, honour
the explicit token budget/protected recent window, deduplicate canonical claim
IDs before reranking, and treat zero retrieval as valid. Exact matching never
bypasses character, provenance, temporal, supersession, or abstention gates.
Traces contain claim IDs, channels, and reasons rather than private archive text.
Trace- and channel-dependent metrics remain `null` until a future adapter returns
test-only `RetrievalOutcome` trace data; a baseline cannot pass them merely by
omitting traces.

## Run

```text
python -m benchmarks.memory_v2.run --adapter reference
python -m benchmarks.memory_v2.run --adapter v1
python -m benchmarks.memory_v2.run --adapter v2
python -m benchmarks.memory_v2.run --adapter semantic-v2
python -m benchmarks.memory_v2.run --adapter hybrid-v2
python -m benchmarks.memory_v2.run --adapter reference --scale 1000
```

`reference` is a fixture oracle and should score perfectly; it validates gold
annotations and metric plumbing. `v1` invokes production V1 ranking methods
against synthetic records and a deterministic token-hash embedding model. It is
a structural baseline only, not a MiniLM or live-LLM quality measurement.

`v2` imports only the synthetic fixture into an isolated SQLite store. It
applies character, provenance, state, temporal, and explicit recent-use filters
before deterministic lexical ranking; it is not wired into AIFren or its JSON
persistence.

`semantic-v2` uses the first isolated FTS5/exact/structural deterministic
retriever. It emits typed selections and privacy-safe traces. It is expected to
leave `deterministic_only=False` semantic/paraphrase cases unresolved until a
real, separately evaluated embedding candidate lane is added.

`hybrid-v2` explicitly builds derived embeddings from the synthetic fixture
with the local MiniLM model, then combines exact, FTS, structured, and semantic
candidates. It never reads the local conversation or memory JSON data.

## Resource model

The benchmark is intentionally model-independent and lightweight. It supports
on-demand 1k/10k/100k/1M-event generation rather than committing huge fixtures.
It records per-query adapter latency and Python peak tracked memory (excluding
adapter construction). Future curator work
must fit a single-GPU environment: queue/batch work, allow same-model curation
while inference is idle, support CPU/deterministic easy cases, and pause during
games or heavy GPU use. A curator is a logical job, not a permanently resident
second large model.

## Scope limits

The lexical adapters invoke no live model. `hybrid-v2` is the deliberate
exception: it uses the existing local MiniLM model against synthetic claims
only. Neither path can establish semantic extraction quality or conversational
usefulness beyond this bounded retrieval fixture.
