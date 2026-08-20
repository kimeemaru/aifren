# AIFren project direction

## Vision

AIFren is local-first, long-lived companion software. Continuity over years is the product: durable conversation, personality, memory, and relationship rather than a session-based chatbot. The eventual goal is fully local operation with replaceable providers and frontends.

## Current presentation

The Unity default is direct rendering: `viewer background -> direct-rendered VRM -> Screen Space Overlay UI`. This avoids magnifying a full-body RenderTexture at close Avatar View framing. The RenderTexture route remains rollback/debug-only. Avatar View stores independent portrait/landscape X/Y/scale; UI visibility is overlay-only and cannot move or resize the avatar. UniVRM supports VRM 1.0 and VRM 0.x. Generic GLB support is not implemented.

## Managed visual assets

Avatar models and backgrounds are global visual libraries, separate from character identity. Imports are copied into AIFren-managed storage with content-hash identity. Model labels prefer embedded VRM metadata then filename stem; duplicate display names are disambiguated only in UI. Imported models use generated thumbnails with a generic fallback. Background previews preserve aspect and portrait/landscape selections are independent. Built-ins/defaults are non-deletable. Regular click applies an imported asset and makes it the one delete target; Ctrl-click modifies temporary bulk selection. Active and delete-selection state remain distinct.

## Data Ownership & Destructive Operations

A subsystem may destructively modify only data/files it owns; references do **not** imply ownership. External imported source files are never deletion targets. Managed cleanup may delete only individual files inside exact managed `Models/`, `Backgrounds/`, and `Thumbnails/` directories. Traversal, absolute external paths, similarly prefixed roots, symlink/reparse escapes, and directories are refused. Tampered metadata is repaired or removed without deleting external files. Deletion is kind-scoped: equal model/background hashes do not grant cross-kind authority.

Future Character Management may delete only character-owned data and must dereference shared/global visual or voice assets. It must never delete another character's history, memory, or personality. Future memory/personality tools should favor archive/supersede/deactivate where practical and prove unrelated data survives destructive work.

## Companion, audio, and subtitles

The player runs while unfocused. Always on Top is persisted; Linux X11 uses EWMH where supported. On rotated X11 displays, Linux uses `FullScreenWindow` plus EWMH fullscreen rather than unreliable exclusive fullscreen, preserving selected physical display orientation. Backend disconnect UI offers warning/reconnect and recovery only starts/reuses a repository-owned backend.

PTT/audio lifecycle is authoritative. Explicit PTT interruption immediately invalidates synthesis/playback, stops audio, and prevents stale synthesis from playing. It never waits for subtitle work or natural playback end. Natural completion retires active playback state; playback IDs prevent stale completion from clearing newer playback. Subtitle timing must never gate PTT readiness, cancellation, audio startup, or backend readiness.

Visible dialogue keeps canonical assistant text unchanged, renders paired `*emotes*` blue, and uses bold normal text. Optional hidden subtitles omit emotes and use a floating lower-screen overlay with progressive reveal/pagination. Page text is sanitized plain text, geometry is precomputed/stable, and TMP mesh/vertex visibility controls reveal rather than inline alpha tags. Valid Kokoro timing may be used; invalid/missing timing uses an immutable fallback plan. Temporary UI peek does not destroy subtitle state; committed Show UI cancels it.

## Memory and roadmap

Memory V1 is authoritative and prompt-facing. Memory V2 is non-authoritative shadow/evaluation work until explicit promotion. Planned V2 concepts include profile facts, shared episodes, deterministic active/current state, and separately handled relationship state; evidence, character scope, temporal claims, supersession/dispute handling, and retrieval diversity remain essential. A Memory Viewer/Editor is required before V2 authority.

1. Hidden-subtitle final QA.
2. Final frontend QA.
3. Reusable expressions/basic animation with model capability fallback.
4. Character Management with ownership-safe deletion.
5. Memory Viewer / Editor.
6. Memory V2/backend work.
7. Voice and AI settings.
8. Safe optional web lookup.
9. Windows compatibility pass.
10. 1.0 packaging/productization.

Parked: remote/mobile companion, VR, generic GLB, and advanced animation/emotion systems. Built-in ambient/background music is not a priority; functional TTS, microphone, PTT, and justified recovery audio are.
