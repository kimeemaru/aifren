# AIFren Unity Companion

This Unity 2022.3.62f3 project is a presentation client for AIFren's local loopback Python backend. It does not own canonical history, memory, personality, STT, or TTS playback.

## Presentation

```text
direct viewer background -> direct VRM rendering -> Screen Space Overlay UI
```

Direct rendering is default for sharp close Avatar View compositions. The old RenderTexture path is rollback/debug support only. The camera has a stable full-body baseline; Avatar View applies independent portrait/landscape X/Y/scale through direct camera controls. UI overlays the full viewport, and hide/show must never change avatar position, framing, or size.

UniVRM supports VRM 1.0 and VRM 0.x through the shared loader. Generic GLB is not implemented. Runtime model swaps leave character identity, personality, memory, voice, and history untouched.

## Appearance libraries

Avatar models and backgrounds are global reusable managed assets. Imports copy files into managed persistent storage; original source files are never runtime dependencies or deletion targets. Model identity is content-hash based while visible names prefer metadata then filename. Background selection is independent per orientation. Built-ins are non-deletable. Normal click applies/selects one delete target; Ctrl-click supports temporary bulk selection.

Deletion may remove only individual canonical files in exact managed kind and thumbnail directories. External paths, traversal, symlink escapes, directories, and cross-kind records are metadata-repair cases, not deletion targets.

## Companion behavior and dialogue

The player runs while unfocused. Always on Top is persisted and uses X11 EWMH where supported. Linux rotated-display fullscreen uses `FullScreenWindow` plus EWMH rather than exclusive fullscreen and preserves selected display physical orientation. PTT routes to the authoritative Python boundary and must interrupt TTS before microphone capture; Unity subtitle/timing work must never delay it. The backend-disconnect warning includes recovery controls.

Visible dialogue keeps canonical assistant text unchanged, renders paired `*emotes*` blue, and uses bold normal text. Optional hidden-UI subtitles are a separate lower-screen overlay: emotes are omitted, pages reveal progressively, and long text paginates without a scroll view. Complete page geometry is fixed before reveal using TMP mesh visibility rather than inline reveal markup.

## Run

Start `backend_host.py` through the repository launcher or directly, open `Assets/Scenes/AIFrenPoc.unity`, and press Play. The client connects to `ws://127.0.0.1:8765`. Use the root developer guide for build and validation.
