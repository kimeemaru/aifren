# AIFren developer guide

Read [PROJECT.md](../PROJECT.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and [AGENTS.md](../AGENTS.md) first.

## Prerequisites and run

- Use the repository Python environment (commonly `.venv-aifren`).
- Use Unity 2022.3.62f3 LTS for the companion client.
- Configure any online response provider locally; never commit keys or credentials.
- Locally imported models/backgrounds, logs, builds, and user data are not public source assets.

Start the loopback backend from the repository root:

```bash
python backend_host.py
```

The endpoint is `ws://127.0.0.1:8765`. Use repository launcher/build scripts where supplied. They must manage only a backend process owned by that checkout and must not kill arbitrary listeners.

## Validation

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_tts_providers tests.test_assistant_service -v
python test_tts.py
git diff --check
```

For Unity changes, run relevant EditMode tests and build the affected target. A batch test run may lack an XML result artifact; record that limitation, but never call compiler errors validated. Manually cover text, PTT idle/playing/synthesizing, natural TTS completion then next PTT, reconnect, UI hide/show, portrait/landscape, and asset-library flows.

## Guardrails

- Do not put subtitle/timing work on the TTS/PTT critical path.
- Do not reverse direct rendering into UV crop framing.
- Model/background swaps must not mutate character identity or canonical data.
- Never trust asset metadata as deletion authority; validate canonical containment and exact kind directory first.
- Memory V2 remains shadow-only until a Memory Viewer/Editor and explicit promotion decision exist.
