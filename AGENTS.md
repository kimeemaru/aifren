# AIFren agent guide

Read [PROJECT.md](PROJECT.md), [ARCHITECTURE.md](ARCHITECTURE.md), and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) before changing code.

## Rules

- Preserve working behavior with small, reviewable changes. Do not start an
  unrelated stage without approval.
- Treat `conversation.json`, `conversation_summary.json`, `memories.json`, and
  character files as durable canonical data. Do not casually move, rewrite, or
  migrate them.
- `AssistantService` owns backend turns, persistence, memory processing, and
  backend events. `backend_host.py` is the loopback WebSocket adapter; Unity
  and Tkinter are presentation clients, not alternate backends.
- Keep LLM, TTS, STT, embedding, and frontend implementations replaceable.
  Frontends must not mutate `Memory.memories` directly.
- Preserve the continuing-character model: no normal New Chat flow, and future
  character identities must keep their durable records separate.

## Validation

For backend or transport changes, run:

```text
.venv-aifren\Scripts\python.exe -m unittest discover -s tests -v
.venv-aifren\Scripts\python.exe test_tts.py
git diff --check
```

For Unity changes, also run relevant Unity EditMode tests and a Windows build.
When startup changes, run the appropriate frontend smoke test. Commit only
after validation and requested review.

## Map

- `assistant_service.py` — frontend-neutral application boundary.
- `backend_host.py` — loopback-only WebSocket adapter.
- `conversation/` and `memory/` — canonical history/context and Memory V1.
- `llm/`, `stt/`, `tts/`, `voice/` — replaceable integration boundaries.
- `gui.py` — existing Tkinter frontend.
- `unity/AIFrenUnityPoc/` — Unity companion presentation client.
