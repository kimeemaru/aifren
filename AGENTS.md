# AIFren agent guide

Read [PROJECT.md](PROJECT.md), [ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) before changing code.

- Preserve working behavior with small reviewable changes; do not begin an unrelated roadmap stage.
- Canonical conversation and original character/fact/correction/state records are durable. Never casually move, rewrite or migrate them.
- Memory V2 is normal prompt-facing long-term-memory authority. V1 is explicit one-launch compatibility only, with zero normal V2 prompt/learned-memory/summary access. V2 errors never silently select V1.
- Derived episodes, embeddings, FTS/ANN and caches are rebuildable, never replacement truth. Preserve exact source, speaker, scope, polarity and current/historical admission.
- CompanionMemoryRealizer owns only the surface of an admitted answer, never retrieval or memory. Optional present reactions have no historical authority; dropping one retains the grounded core without repair.
- AssistantService owns turns, canonical persistence, memory, TTS/PTT and backend events. backend_host.py adapts the loopback transport; Unity owns production UI only.
- Keep LLM, TTS, STT and embedding implementations replaceable. Managed llama.cpp launches explicitly use `--logits_all false`; never stop an external process by port alone.
- Character identity/personality/history are separate from visual assets and voice. Direct VRM is normal; RenderTexture is rollback/debug-only. UI visibility never changes avatar framing.
- A subsystem deletes only owned canonical paths. Reject traversal, symlink escapes and kind confusion; imported external originals are never implicit deletion targets.
- PTT invalidates active synthesis/playback immediately. Stale callbacks cannot resume it; subtitles never gate audio or readiness.
- HiddenSubtitlePresenter is the sole hidden-subtitle render/page/fade owner. Keep due/shown/fade state distinct; peek suppresses rendering and committed Show cancels.
- DialoguePresentationParser and Python speech projection must agree on action/emphasis semantics without rewriting canonical dialogue. Semantic gestures use Humanoid intent, separate from persistent face, blink/lips/gaze.
- Only final accepted replies dispatch presentation; retain socket/character/turn/asset generation guards and capability-first eligibility.
- History/Viewer are bounded derived views. Hidden changes mark dirty; do not rebuild invisible TMP hierarchies or scan the archive every frame.
- Tests isolate application preferences/data before first initialization. Never borrow/reset normal keys and restore them later. Preserve concurrent edits.
- Public fixtures must be synthetic and self-contained. Do not invent private fixtures, assume another developer's local assets, or require unavailable prepared seeds. Never commit settings, credentials, conversations, models, builds or generated QA evidence.

For backend changes run the public structural discovery, separately installed
real embedding assertion, offline TTS contract smoke and diff check described in
the developer guide. Run affected Unity EditMode tests and a matching build after
native changes. Public builds accept only the reviewed bundled sample assets;
local imported resources need their own explicit distribution review.

Do not claim model, acoustic or human experience acceptance from synthetic tests.
Keep release diagnostics disabled by default and bounded when enabled.
