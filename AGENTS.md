# AIFren agent guide

Read [PROJECT.md](PROJECT.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) before changing code.

## Rules

- Preserve working behavior with small, reviewable changes; do not begin an unrelated roadmap stage without approval.
- Treat conversation, summary, memory, and character records as durable canonical data.
- `AssistantService` owns turns, persistence, memory processing, TTS/PTT, and events. `backend_host.py` is a loopback adapter; Unity and Tkinter are presentation clients.
- Keep providers/frontends replaceable. Character identity/personality/memory are separate from reusable visual assets.
- Direct VRM rendering is the normal Unity path; RenderTexture avatar rendering is rollback/debug-only. UI show/hide must not alter the full avatar viewport.

## Ownership and audio

A subsystem may destructively modify only data it owns. References never imply ownership. Imported source files outside AIFren are never deletion targets. Managed deletion must use canonical exact kind directories and refuse traversal, external paths, similarly prefixed roots, symlink escapes, and directories. PTT/audio state is authoritative: interruption immediately invalidates playback/synthesis and never waits for subtitles. Natural completion separately retires the playback ID.

## Validation

```text
python -m unittest discover -s tests -v
python test_tts.py
git diff --check
```

For Unity changes, run relevant EditMode tests and build the target. Never commit credentials, user data, locally imported assets, logs, generated builds, or machine-specific configuration.
