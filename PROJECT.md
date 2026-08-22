# AIFren project direction

## Vision

AIFren is local-first, long-lived companion software. Continuity over years is
the product: one character should retain durable history, personality, and a
meaningful relationship without treating every launch as a new chat. The long
term destination is fully local operation, with replaceable local or
network-backed implementations during development.

For the detailed durable product and future-system decisions behind this summary, see [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md).

## Durable principles

1. Raw conversation is canonical. Summaries, embeddings, indexes, and memories
   are aids, never a replacement archive.
2. Character identity, base personality, learned memory, relationship state,
   and visual avatar assets are separate concepts.
3. LLM, TTS, STT, embedding, indexes, and frontends remain replaceable.
4. Data durability, portability, provenance, correction, and explicit scope
   matter more than short-term convenience.
5. A continuing character has no ordinary “New Chat” lifecycle.

## Current implementation

The Python backend owns `AssistantService`, canonical persistence, Memory V1,
provider integration, TTS/PTT, and frontend-neutral events. Unity is the
canonical product frontend and connects through loopback `backend_host.py`;
the older Tkinter client is legacy/debug-only.

Unity's default presentation is direct rendering:

```text
viewer background -> direct-rendered VRM -> Screen Space Overlay UI
```

The previous RenderTexture avatar path is rollback/debug-only. Direct rendering
avoids raster magnification/crop blur at close Avatar View zoom. Avatar View
stores independent portrait and landscape X/Y/scale values; hiding UI does not
resize or reposition the avatar viewport. UniVRM supports VRM 1.0 and VRM 0.x
in `.vrm` or `.glb` containers when embedded VRM metadata is present; plain
generic GLB loading is not implemented. Avatar lighting is global presentation
state rather than character/model identity: a restrained, model-agnostic
baseline should preserve material and white-clothing detail across backgrounds.

## Managed visual assets

Avatar models and backgrounds are global managed visual libraries, not
character identity data. Imports copy into managed storage with a stable content
hash. Model display names prefer embedded VRM metadata then filename stem;
duplicate visible names are disambiguated without changing identity. Imported
models have persisted head/upper-body thumbnails with a generic fallback.
Background previews preserve image aspect and portrait/landscape keep independent
active selection. Built-ins/defaults are permanent and non-deletable.

Normal click applies an imported asset and makes it the sole delete target;
Ctrl-click modifies temporary bulk-delete selection. Active and delete-selected
state remain distinct. Visual asset swaps must not change personality, memory,
history, voice, or relationship state.

## Data Ownership & Destructive Operations

A subsystem may destructively modify only data/files it owns. References do
**not** imply ownership.

- An imported source file outside AIFren is never a deletion target.
- Only individual files in canonical managed `Models/`, `Backgrounds/`, and
  `Thumbnails/` directories may be deleted.
- Traversal, malformed/external paths, symlink/reparse escapes, directories,
  and similarly prefixed roots such as `AssetLibrary2` are refused.
- Tampered metadata may be repaired/removed without deleting a referenced
  external file.
- Deletion is kind-scoped: equal model/background hashes do not imply shared
  ownership or cross-kind deletion.

This is project-wide. Future Character Management may remove only
character-owned data; it dereferences shared avatar/background/voice assets and
must never delete another character's memory/personality/history. Memory and
personality editors should prefer archive/supersede/deactivate, use explicit
scopes for destructive work, and test cross-scope survival.

## Companion, display, and Linux

The player runs in the background so rendering continues unfocused. Always on
Top is persisted; Linux X11 uses native EWMH/X11 where available. Unity
`ExclusiveFullScreen` is unreliable on rotated X11 displays, so Linux uses
`FullScreenWindow` plus EWMH fullscreen and selected physical desktop geometry
to preserve portrait orientation. Windows behavior remains separate and needs a
later compatibility pass.

## PTT/TTS authority

Audio lifecycle is authoritative; subtitle presentation is downstream only.

- PTT interruption immediately invalidates synthesis/playback generation and
  aborts active audio.
- Interrupted/stale synthesis cannot later start playback.
- Interruption never waits for subtitle scheduling, timing extraction, natural
  completion, or subtitle work.
- Natural completion explicitly retires active playback state.
- Playback IDs prevent stale completion from clearing a newer playback.
- Subtitle timing/alignment must never gate PTT readiness, interruption,
  synthesis cancellation, audio startup, or backend readiness.

## Dialogue and hidden subtitles

Canonical assistant content is unchanged by presentation. Normal dialogue keeps
paired `*emote*` spans visible and blue; normal dialogue is bold. Optional
hidden subtitles omit emote/action spans and are a floating lower-screen layer,
not the normal dialogue box or a scroll view. They reveal progressively and
paginate long replies.

`DialoguePresentationParser` produces typed `PlainText`, `Emphasis`, and
`Emote` spans. A paired single `*...*` span is an emote when it is a known
action/stage direction or contains four or more normalized words; otherwise it
is italic spoken emphasis. Paired `**...**` is always emphasis. Emotes are
removed from TTS and hidden subtitles but remain available to gesture mapping.
The Unity parser and backend TTS cleaner intentionally mirror this semantic
interpretation.

`HiddenSubtitlePresenter` is the sole owner of hidden-subtitle page, mesh,
renderability, and alpha state. Pages advance by their actual adjusted word
count, not a requested maximum; page ranges and page text must map every
global spoken word exactly once, in order. `timingDue` and
`presentationShown` are distinct so words due while an incoming page or
temporary UI peek is non-renderable remain pending for visible presentation.
Complete page geometry is computed before reveal and remains stable; TMP
mesh/vertex alpha controls word visibility instead of generated inline alpha
tags. Kokoro timing is forwarded only when finite, monotonic, plausible, and
one-to-one with cleaned spoken words. Invalid/missing timing uses an immutable
fallback plan. Temporary UI peek suppresses rendering without cancelling the
session; committed Show UI cancels it. A short non-final-page readability dwell
remains future polish, not current behavior.

The chat field is a fixed-height multiline TMP input with a masked Text Area.
It stays vertically centered while content fits, then uses top-aligned internal
scrolling only after preferred rendered height exceeds its viewport. Enter
submits; Shift+Enter inserts a newline.

## Gesture backbone

`AvatarGestureIntent`, `AvatarGestureMapper`, and `AvatarAnimationController`
form a semantic, Humanoid-mapped presentation layer. The first supported emote
per response may request Nod, HeadShake, Wave, Shrug, HeadTilt, or Thinking;
gesture cooldown handles repeated requests while blink and lip-sync remain
separate. This is procedural groundwork, not a finished animation system.

Manual QA: Nod is acceptable. HeadShake remains somewhat choppy; HeadTilt and
other gestures can feel robotic, and Wave needs further trigger/visibility QA.
Future work should evaluate appropriately licensed authored Humanoid clips
while retaining the semantic intent abstraction. Expressions and gaze are
separate presentation channels from blink, mouth/lip-sync, and body gesture;
concrete avatar presentation remains Unity-owned.

## Memory status and direction

Memory V1 and `memories.json` remain authoritative and prompt-facing. Memory
V2 is experimental local shadow/evaluation work, not production authority or a
canary. It has character-scoped SQLite events/claims/evidence, append-oriented
lifecycle history, non-destructive supersession/archive, rebuildable FTS and
embedding/ANN support, and fail-open dual-read diagnostics. Raw conversation
remains canonical source material.

The factual/lifecycle foundation is under evaluation and does not yet establish
reliable lifetime episodic or temporal recall. V2 must not be promoted until
that problem, active/current state, relationship state, and a usable
non-destructive memory viewer/editor have explicit evidence-based designs.

## Roadmap

1. Memory correctness: evaluate reliable lifetime episodic/temporal recall
   before any V2 prompt-facing canary.
2. Active/current state, relationship state, and Memory Viewer/Editor design.
3. Friend-build/package validation and Unity frontend QA.
4. Animation/presentation and character-management expansion.
5. Voice and AI settings, including PTT/mic/TTS/STT/model recovery controls.
6. Safe optional web lookup.
7. Windows compatibility pass.
8. 1.0 packaging/productization.

Parked: LAN-first remote/mobile companion, VR experiments, generic GLB support,
and advanced animation/emotion systems. The 1.0 direction includes lower-spec
local models, installer/dependency packaging, import/export/backups, and
possible open-source/low-cost distribution. Built-in background music is not a
priority; functional TTS, microphone, PTT, and justified recovery audio are.
