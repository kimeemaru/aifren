# AIFren developer guide

Read [PROJECT.md](../PROJECT.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and
[AGENTS.md](../AGENTS.md) before changing code.

## Prerequisites and run

- Use the repository `.venv-aifren` Python environment.
- Use Unity 2022.3.62f3 LTS for the companion client.
- The current response provider needs a user-configured key; do not commit
  credentials. Local models/assets are machine-local and ignored.

Start the loopback backend from the repository root:

```bash
.venv-aifren/bin/python backend_host.py
```

The endpoint is `ws://127.0.0.1:8765`. Linux developer actions are available
through `scripts/aifren_dev_linux.sh current` and `rebuild`; the GUI/desktop
launcher uses the same owned-backend lifecycle and must not kill arbitrary port
8765 listeners.

Local Linux development builds that intentionally include ignored presentation
assets use:

```bash
AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 scripts/build_aifren_linux.sh
```

The ordinary build intentionally rejects those assets. Preserve that boundary.
Windows launcher/build behavior is separate from Linux X11 behavior.

## Validation

```bash
.venv-aifren/bin/python -m unittest discover -s tests -v
.venv-aifren/bin/python -m unittest tests.test_tts_providers tests.test_assistant_service -v
.venv-aifren/bin/python test_tts.py
git diff --check
```

For Unity work, run relevant EditMode tests and build the target player. A
successful Unity batch run may lack an XML result file; record the limitation,
but never treat compiler errors as validated. Manually test text, PTT while
idle/playing/synthesizing, natural TTS completion then next PTT, reconnect, UI
hide/show, portrait/landscape, and asset-library flows.

For dialogue/presentation changes, include focused tests for
`DialoguePresentationParser`, `SubtitlePagination`/`SubtitleTimingPlan`,
`HiddenSubtitlePresenter`, `ChatInputFieldLayout`, `AvatarGestureMapper`, and
`AvatarAnimationController` as applicable.

## Guardrails

- Unity is the canonical/default user-facing frontend. Keep Python services and
  the loopback protocol frontend-neutral; `gui.py` is legacy/debug-only. Do
  not duplicate product settings, character, or memory UX in Tkinter.

- Do not put subtitles/timing on the TTS/PTT critical path.
- Keep hidden-subtitle ownership deterministic: page text/ranges must cover
  every spoken word exactly once in global order. `HiddenSubtitlePresenter` is
  the sole production owner of renderability, alpha, mesh visibility, page
  swaps, and transitions; temporary UI peek may suppress rendering but must
  not cancel the subtitle session.
- Keep typed dialogue semantics aligned in Unity and Python: single markers are
  action emotes for known actions or four-or-more normalized words, otherwise
  spoken emphasis; double markers are always emphasis.
- Keep the chat input's masked multiline viewport. It centers fitting content
  and only switches to top-aligned internal scrolling on actual height overflow.
- Gesture calls are semantic `AvatarGestureIntent` requests over Humanoid
  mappings; do not couple character identity or specific VRM hierarchy names
  to gesture behavior.
- Do not reverse direct rendering into UV crop framing.
- Model/background swaps must not mutate character identity or canonical data.
- Never trust managed-library metadata as deletion authority: validate canonical
  containment and exact kind directory first.
- Memory V1 remains authoritative and prompt-facing. Memory V2 remains
  fail-open shadow/evaluation work until reliable lifetime episodic/temporal
  recall, a Memory Viewer/Editor, and an explicit promotion decision exist.

## Diagnostics

Development logs include concise timing for backend turn start, full response,
TTS request/ready, playback start, PTT cancellation, and natural completion.
Use them to distinguish model latency from TTS/audio/presentation; do not log
conversation content, credentials, or private paths to user-facing diagnostics.
