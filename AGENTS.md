# AIFren agent guide

Read [PROJECT.md](PROJECT.md), [ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) before changing code.

- Preserve working behavior with small reviewable changes; do not begin an unrelated roadmap stage.
- Canonical conversation and original character/fact/correction/state records are durable. Never casually move, rewrite or migrate them.
- Memory V2 is normal prompt-facing long-term-memory authority. V1 is explicit one-launch compatibility only, with zero normal V2 prompt/learned-memory/summary access. V2 errors never silently select V1.
- Derived episodes, embeddings, FTS/ANN and caches are rebuildable, never replacement truth. Preserve exact source, speaker, scope, polarity and current/historical admission.
- CompanionMemoryRealizer owns only the surface of an admitted answer, never retrieval or memory. Optional present reactions have no historical authority; dropping one retains the grounded core without repair.
- AssistantService owns turns, canonical persistence, memory, TTS/PTT and backend events. aifren/backend_host.py adapts the loopback transport; Unity owns production UI only.
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

## Current delivery and public workflow

Memory V2 and the responsive governor are normal. Required context may exceed the
soft working target but never the hard budget; no fill-the-window regression.
Natural/Roleplay, responsive speech, opt-in ACT and CPU expressions use existing
settings owners. Automatic faces are leased temporary overlays, not durable emotion.
Keep canonical speech projection before chunking and one cancellable utterance owner.

CharacterRegistry owns local/legacy layout, UUID and timeline paths. Management
requires selected UUID/revision confirmation; missing local data never selects
shared/V1/seed truth. Preserve writer leases and character/session/generation fences,
including late A events after A→B→A. All destructive QA uses synthetic stores.
The intermittent blank-panel report remains unresolved; passing synthetic switching
is not proof of its repair.

This public checkout is the single active application codebase. Include code,
synthetic regression and necessary docs in each change. Complete reviewed slices
can land on main; keep unfinished work on a branch. Run targeted development tests
and appropriate final validation once. Review outgoing tree AND commits for privacy
before every public push. Do not maintain another application implementation that
requires periodic export. Historical internal archives are not runtime dependencies.
Never invent private fixtures or require unavailable machine-local assets. Runtime
data, logs, model files, preferences and private QA evidence do not belong in Git.

## Source navigation

Start with [the source map](docs/SOURCE_LAYOUT.md), then read the affected owner:
`aifren/assistant_service.py` (turns), `aifren/backend_host.py` (transport),
`aifren/character/` (identity/storage/management), `aifren/dialogue/` (presentation),
`aifren/state/` (current state/capabilities), `aifren/continuity/` (V2 integration),
and `aifren/context/` (request planning). Providers and stores keep their existing
package names under `aifren`. Developer evaluations belong in `tools`/`benchmarks`;
application modules must not import those tools.

Use explicit `aifren` imports. `aifren.runtime.runtime_layout` owns source/resource/
data roots; moving source never authorizes moving data or changing canonical keys.
Keep the thin root command wrappers for supported launchers. Run package utilities
with `python -m` from the checkout; do not add per-module CWD/path discovery.
