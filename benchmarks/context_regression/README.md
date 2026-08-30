# Historical context regression

This benchmark reconstructs past points in an authorized synthetic/test
character archive and compares multiple AIFren working-context architectures
with matched sampling seeds. It never appends to the archive, writes memory,
runs Unity, or invokes speech services.

The checked-in manifest stores only canonical record positions, source-text
digests, categories, and short evaluation expectations. Generated contexts and
responses are written to a caller-selected `/tmp` directory. They are test
artifacts, not character state.

The current arms are:

- `legacy`: the raw recent-history plus rolling-summary path, with current
  Context Hygiene applied to the raw suffix;
- `lower_episodes`: lower-level episode summaries plus the same recent suffix
  and hygiene, with era consolidation disabled;
- `consolidated`: the current episode/era selection plus the same recent suffix
  and hygiene.
- `retrieved_episodes`: lower-level episodes with bounded, current-turn
  retrieval over source-verified continuity anchors and precise local-calendar
  activity cues, plus the unchanged recent suffix and hygiene. Weak activity
  words are considered only inside a resolved temporal range. Entity retrieval
  still promotes at most one episode account. Temporal recall may instead
  replace up to three episode accounts with at most four compact excerpts from
  their exact canonical source records, within a 2,400-character detail bound.
  Direct source evidence supports one result or a coherent bounded set.
  Bounded topic-adjacent evidence is labeled separately when it can clarify an
  unconfirmed activity premise without establishing that the activity happened.
  The selector also distinguishes ambiguity, excessive breadth, no match, and
  an answer already present in recent raw dialogue. The
  total selected episode-unit bound does not grow and source coverage is never
  represented twice.

Lower-episode generation is source-ranged and deterministic for the owned
seeded local provider. It includes bounded continuity-anchor
extraction/verification/refinement; era selection remains optional and falls
back to those validated lower rows when its independent retention gate rejects
the proposal. The benchmark deliberately retains fixtures where broad
compression lost distinctive continuity, rather than tuning the rubric to the
newest architecture.

Preparation and candidate generation use deterministic explicit seeds. The
arms for one fixture/seed are evaluated as a matched set. The harness can
resume an interrupted run from its output directory.

Historical states rebuild disposable episode data in temporary stores. A
counterfactual probe may explicitly target the current derived cache; that
SQLite database is then opened read-only and queried only for the authorized
active test-character ID. This is used to catch loss hidden by regenerating a
different era boundary during evaluation.

Example (requires an already-running configured local provider):

```text
.venv-aifren/bin/python -m benchmarks.context_regression.run \
  --manifest benchmarks/context_regression/serval_historical_v1.json \
  --archive conversation.json \
  --output /tmp/aifren-context-regression-<timestamp>
```

Use `--fixture-limit 12` for a timing tranche. Results include JSONL candidate
records, blinded candidate sets, and an aggregate JSON/Markdown report.
`--fixture-id` and `--arm` may be repeated to run a high-signal matched subset;
omitting them preserves the complete fixture/three-arm comparison and rubric.
Future context-selection/retrieval arms should reuse the same manifest and
matched seeds so prompt savings cannot hide continuity or topic-reversion
regressions.
