# Application source map

`aifren` is the Python application package. Location groups existing owners; it
adds no new state, authority, provider or dependency framework. Large service and
transport implementations remain intact.

| Owner / previous location | Current location |
| --- | --- |
| `assistant.py`, `assistant_service.py`, `backend_host.py` implementations | `aifren/` |
| Character identity, registry, operations, storage runtime and profile | `aifren/character/` |
| Active Scene, capability/action policy, continuity intent/reference and scene events | `aifren/state/` |
| Dialogue spans, response requirements, Natural/Roleplay, ACT and expressions | `aifren/dialogue/` |
| Governor, companion context and dormant salience owners | `aifren/context/` |
| V2 authority, recall, admission, realizer, observation and Viewer integration | `aifren/continuity/` |
| Existing `conversation`, `memory`, `memory_v2_store` packages | Same names under `aifren/` |
| Existing `llm`, `tts`, `stt`, `voice` packages | Same names under `aifren/` |
| Configuration, local settings/model runtime, root resolution and recorder | `aifren/runtime/` |
| Root memory evaluations/prototypes and store maintenance CLI | `tools/memory_v2/` |
| Root Kokoro diagnostic and offline TTS smoke | `tools/test_kokoro.py`, `tools/tts_smoke.py` |
| Shared retrieval types previously in `benchmarks/memory_v2/models.py` | `aifren/memory_v2_store/models.py` |
| Previously shadowed root `conversation.py` | `aifren/conversation/legacy.py` |
| Credential configuration example | `docs/examples/config_secret_example.py` |

The legacy conversation module is retained explicitly, not substituted for the
active `aifren.conversation.conversation` owner. V1/legacy retirement remains a
separate release gate. `benchmarks/` retains existing synthetic evaluation suites;
normal runtime imports neither it nor `tools/`.

## Commands

Use the selected project interpreter; the examples below assume `.venv-aifren`.
Run module commands from the checkout. Script commands also support an absolute
script path from another working directory.

```bash
.venv-aifren/bin/python backend_host.py
.venv-aifren/bin/python -m aifren.backend_host --help
.venv-aifren/bin/python scripts/run_structural_tests.py
.venv-aifren/bin/python test_tts.py
.venv-aifren/bin/python scripts/check_offline_embeddings.py --help
.venv-aifren/bin/python -m tools.memory_v2.cli --help
.venv-aifren/bin/python -m aifren.dialogue.automatic_expression --help
scripts/aifren_dev_linux.sh current development
scripts/aifren_dev_linux.sh rebuild development
```

Root `backend_host.py`, `assistant.py` and `test_tts.py` are the only Python command
wrappers. They preserve actual launcher/console/smoke entry contracts. Import the
implementation through `aifren`, not those wrappers. Existing launch and maintenance
scripts remain in `scripts`; no new launcher actions or environment installation
steps are required.

## Roots and identities

`aifren.runtime.runtime_layout.source_root()` identifies the code checkout or
packaged application root. Its existing `resolve_runtime_roots` / `resource_path`
owners keep `AIFREN_RESOURCE_ROOT` and `AIFREN_DATA_ROOT` separate. Do not infer a
character or model directory from an individual module's new parent directory.
CharacterRegistry still resolves character-owned paths and timeline identity.

The source move does not change database schema, canonical namespaces, source keys,
content hashes, import receipts, observer cursors or stored policy versions. No data
migration/cache rewrite accompanies it. Explicit future episode rebuilds record
the provider class's new Python module path in their provenance. Existing episode
records still validate their stored identity/digest; relocation does not trigger
regeneration or rewrite that metadata. Source-code inventories and CLI/module
references name the new locations. Linux packaging continues using the explicit
reviewed `scripts/package_runtime_files.txt`, including package initializers and the
supported backend command wrapper. Windows composition retains the same exclusions.

Validation checks request roles/content, dialogue and speech projections, governed
recall/source indices, and character paths against the previous implementation.
Source-root/entry tests also run outside the checkout with unrelated legacy module
names on the search path. Native inputs are unaffected by Python-only relocation;
normal Development runs the external backend from this source checkout.
