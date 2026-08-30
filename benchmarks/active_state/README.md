# Active State QA harnesses

These harnesses validate bounded improvisational world-awareness without
turning production code into a simulator. They use synthetic evidence and
structural/numeric output only. Parser/state tests are necessary but are not
product acceptance by themselves.

## Layers

- `harness.py` / `run.py` — curated matrices, deterministic randomized state
  machines, and long-context persistence.
- `adversarial.py` and `breadth_corpus.py` — paired natural-language negatives,
  compound mutations, identity/lifecycle, relation semantics, scope, and
  capability composition.
- `performance.py` / `scaling.py` — bounded prompt/read/retirement/reactivation
  measurements with mostly retired historical subjects.

Unity-specific projection, overlay, paging, acknowledgement, and layout
contracts stay in EditMode tests fed by production transport shapes. TTS and
accelerator resource behavior are tested separately so they do not distort the
main headless model/state runs.

## Validation philosophy

Every important feature should ultimately prove:

```text
natural user text or validated UI command
-> AssistantService
-> authoritative mutation and capability recomputation
-> response requirements and generation
-> persistence/transport snapshot
```

Direct queries require factual correctness, not merely a safe parse.
Constrained responses must retain creative use of remaining channels. Fallback
is classified generation/validation failure, not a normal constrained-state
outcome. Manual Development-player failures become exact production-path
regressions.

The immediately preceding hardening checkpoints exercised the curated matrix,
300 deterministic seeds x 150 operations (45,000 operations and more than 2.4
million invariant checks), long gaps through 1,000 turns, 5,000/10,000-subject
scaling, accumulated-state and exact-smoke replays, bounded real Gemma sessions,
Unity EditMode, and focused TTS/resource tests. These are checkpoint evidence,
not permission to skip targeted tests after relevant code changes or the final
manual Development-player acceptance gate.

## Running

Deterministic suites:

```text
.venv-aifren/bin/python -m benchmarks.active_state.run curated
.venv-aifren/bin/python -m benchmarks.active_state.run randomized --seeds 300 --operations 150
.venv-aifren/bin/python -m benchmarks.active_state.run long_context
```

Real-model evaluators start the configured managed local model and restore
ownership on exit. Use only synthetic/test characters, keep Unity/TTS unloaded
unless the focused test needs them, and do not persist raw generated dialogue
in benchmark output. See each module's `--help` before running a costly suite.

Never commit generated JSON reports, temporary databases, WAL/SHM files,
ANN/index files, model artifacts, caches, or runtime diagnostics.
