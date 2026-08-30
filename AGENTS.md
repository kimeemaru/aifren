# AIFren agent guide

Read [PROJECT.md](PROJECT.md), [ARCHITECTURE.md](ARCHITECTURE.md), and
[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) before changing code.

## Core rules

- Preserve working behavior with small, reviewable changes. Do not begin an unrelated roadmap stage without approval.
- `conversation.json`, `conversation_summary.json`, `memories.json`, and character data are durable canonical records. Do not casually move, rewrite, or migrate them.
- `AssistantService` owns backend turns, canonical persistence, memory processing, TTS/PTT lifecycle, and backend events. `backend_host.py` is the loopback WebSocket adapter. Unity is the production presentation client.
- Keep LLM, TTS, STT, embedding, and frontend implementations replaceable. Frontends must not mutate `Memory.memories` directly.
- Character identity, personality, conversation, and memory are separate from global visual avatar/background assets. A visual asset swap must not change a character's durable identity.
- Unity is the sole production frontend. Python services and transport remain frontend-neutral. Add product settings, character management, memory UX, and other user-facing flows in Unity only.
- Direct VRM rendering is the normal Unity path. The RenderTexture presentation path is rollback/debug-only; do not reintroduce UV crop framing as a feature.
- Portrait and landscape presentation/background choices are independent. UI show/hide overlays the full viewport and must not move or resize the avatar.
- AIFren-owned `llama_cpp.server` processes must explicitly use
  `--logits_all false`. AIFren chat does not consume per-prompt-token logits,
  and retaining them can allocate multiple GiB on large-vocabulary models.
- Synthetic tests support diagnosis; ordinary Linux Development-player
  use with a synthetic/test character is the final performance acceptance
  gate. Keep the Development flight recorder privacy-safe and available for
  intermittent real-user failures.
- Memory V1 remains authoritative and prompt-facing. Memory V2 episode
  compactions may supply bounded, source-ranged derived context, but they are
  disposable, rebuildable, and subordinate to the canonical raw archive.

## Ownership and deletion

A subsystem may destructively modify only data it owns; a reference never implies ownership. Imported source files outside AIFren are never deletion targets. Managed asset deletion is restricted to canonical files under the owned AssetLibrary kind directories; reject traversal, external paths, similarly prefixed roots, symlink escapes, and directories. Keep deletion kind-scoped: a model and background with the same content hash are not shared ownership. Future character and memory features must follow the same rule.

## Audio/PTT boundary

PTT/audio state is authoritative; subtitles are downstream presentation only. A PTT press must immediately invalidate active synthesis/playback, stop audio, and allow no stale callback to resume it. Natural completion separately retires the active playback ID. Subtitle timing, layout, or playback presentation must never gate PTT readiness, cancellation, audio start, or backend readiness.

## Dialogue presentation and gestures

- `HiddenSubtitlePresenter` is the sole production owner of hidden-subtitle
  renderability, alpha, page swaps, TMP mesh visibility, and transitions. Keep
  `timingDue` separate from `presentationShown`; temporary edge peek suppresses
  rendering only, while committed Show cancels the session.
- `DialoguePresentationParser` produces `PlainText`, `Emphasis`, and `Emote`
  spans. Single `*...*` is an emote when action-shaped or a standalone
  roleplay segment; otherwise inline emphasis remains spoken regardless of
  word count. `**...**` is emphasis unless owned by an outer action. Canonical
  text is unchanged and the backend TTS cleaner must mirror these semantics.
- Semantic gestures use `AvatarGestureIntent`, never model-specific clip names.
  Keep them based on Humanoid mappings and separate from blink/lip-sync.

## Validation

For backend or transport changes:

```text
.venv-aifren/bin/python -m unittest discover -s tests -v
.venv-aifren/bin/python test_tts.py
git diff --check
```

For Unity changes, run relevant Unity EditMode tests and the appropriate build for the target platform. Use the local-asset build mechanism only for a local development build; never package or commit ignored local presentation assets. Commit only after requested validation/review.

The Conversation History/Log is a derived view of canonical messages. Hidden
updates mark it dirty; do not rebuild its TMP hierarchy until it is visible.
Visible event bursts should remain coalesced.

Development flight recording is enabled for Development builds and includes
automatic incident capture plus the manual `6666666` dump. A release/1.0
player must keep diagnostics off by default and avoid unbounded persistent log
growth; do not create a second logging subsystem.

## Map

- `assistant_service.py` — frontend-neutral application and audio/turn boundary.
- `backend_host.py` — one-client, loopback-only WebSocket adapter.
- `conversation/`, `memory/` — canonical history/context and Memory V1.
- `llm/`, `stt/`, `tts/`, `voice/` — replaceable integration boundaries.
- `unity/AIFrenUnityPoc/` — Unity companion presentation client.
