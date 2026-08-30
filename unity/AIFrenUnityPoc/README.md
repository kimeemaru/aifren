# AIFren Unity Companion

This Unity 2022.3.62f3 project is a presentation client for AIFren's local
loopback Python backend. It does not own canonical history, memory, personality,
STT, or TTS playback.

## Presentation

```text
direct viewer background -> direct VRM rendering -> Screen Space Overlay UI
```

Direct rendering is default for sharp close Avatar View compositions. The old
RenderTexture path is rollback/debug support only. The camera has a stable
full-body baseline; Avatar View applies independent portrait/landscape X/Y/scale
through direct camera controls. UI overlays the full viewport, and hide/show
must never change avatar position, framing, or size.

The optional compact Current Scene overlay is a lightweight translucent,
content-height view of important current items, relations, conditions, and
capability causes. Its height is bounded, scrolling does not leave a permanent
track/thumb visible, and it participates in global Hide. The persistent RP
indicator is anchored to the root-canvas safe area's true bottom-left in both
portrait and landscape, independent of chat/input layout and Current Scene.

UniVRM supports VRM 1.0 and VRM 0.x through the shared loader when a `.vrm` or
`.glb` container embeds VRM metadata. Plain generic GLB is not implemented.
Runtime model swaps leave character identity, personality, memory, voice, and
history untouched.

The direct avatar camera uses shared neutral presentation lighting. It is not
stored in an imported VRM/GLB or character record, and should preserve material
detail consistently over the selected 2D viewer background.

## Appearance libraries

Avatar models and backgrounds are global reusable managed assets. Imports copy
files into managed persistent storage; original source files are never runtime
dependencies or deletion targets. Model identity is content-hash based while
visible names prefer metadata then filename. Background selection is independent
per orientation. Built-ins are non-deletable. Normal click applies/selects one
delete target; Ctrl-click supports temporary bulk selection.

Deletion may remove only individual canonical files in exact managed kind and
thumbnail directories. External paths, traversal, symlink escapes, directories,
and cross-kind records are metadata-repair cases, not deletion targets.

## Companion behavior

The player runs while unfocused. Always on Top is persisted and uses X11 EWMH
where supported. Linux rotated-display fullscreen uses `FullScreenWindow` plus
EWMH rather than exclusive fullscreen and preserves the selected display's
physical orientation.

PTT routes to the authoritative Python boundary. It must interrupt TTS before
microphone capture; Unity subtitle/timing work must never delay it. The root
backend-disconnect warning includes recovery controls and forwards diagnostics
to the development launcher output. PortAudio stream shutdown is bounded in
the backend: a failed capture is discarded, capture IDs reject stale cleanup,
and later PTT remains usable. If such a no-turn recovery returns to `ready`,
Unity removes the transient Thinking presentation and restores the previous
dialogue; a real `turn_started` retires that restoration authority.

## Dialogue

Visible dialogue keeps canonical assistant text unchanged, renders paired
single-asterisk emotes blue, and uses bold normal text. Typed spans are
`PlainText`, `Emphasis`, and `Emote`: action-shaped or standalone roleplay
single-star segments are emotes, while emphasis embedded in spoken dialogue
remains spoken regardless of length. Double-star emphasis remains spoken unless
owned by an outer action. Optional hidden-UI subtitles are a separate
lower-screen overlay: emotes are omitted, pages reveal progressively, and long
text paginates without a scroll view. `HiddenSubtitlePresenter` is the
single owner of renderability, alpha, page text, TMP word visibility, page
transitions, and timing-due versus presentation-shown state. Complete page
layout is measured before reveal by one inactive measurement-only TMP; one
visible TMP presents the page with `maxVisibleWords`. Temporary edge peek
suppresses rendering without cancelling; committed Show cancels.

An outer roleplay action remains an `Emote` when it contains nested single- or
double-asterisk emphasis; inner formatting cannot close the action early and
leak the remainder into speech. Ordinary spoken emphasis remains spoken.
Generated physical actions are requested in `*...*`; parentheses remain
ordinary prose. Configured WPM/WPS is the maximum normal reveal rate. Audio may
make reveal slower, but short or absent speech cannot accelerate a long
action-heavy response; explicit instant-text mode still wins.
Generated assistant emoji are removed before canonical assistant persistence
and presentation, while user-authored emoji and ordinary Unicode remain intact.

The chat field is fixed-height multiline TMP input with a `RectMask2D` Text
Area. Fitting text is vertically centered; only actual preferred-height
overflow switches to top-aligned internal scrolling. Enter submits and
Shift+Enter adds a newline.

Ordinary long dialogue measures TMP using its true inner wrapping width and
sizes the ScrollRect content from the rendered geometry. Natural streamed
completion, skip-to-end, and persisted-response restoration all leave the final
line reachable and restore completed dialogue at the bottom; the prior
undersized content/RectMask clipping failure is fixed.

The Conversation History/Log is a derived UI view, not another canonical
store. It navigates Year -> Month -> Day -> bounded/paged messages and never
instantiates the lifetime archive as one TMP hierarchy. Incoming
`conversation_message` events update the local index/page projection and mark
hidden views dirty; visible bursts are coalesced. Non-renderable legacy empty
assistant records are omitted from the derived view without changing canonical
history. Future backend transport pagination may reduce initial snapshot size;
smaller Console-population stalls remain separate presentation debt.

## Current Scene interactions

The compact overlay and detailed Scene Details surface have intentionally
different authority semantics:

- Overlay X is an in-world interaction. Unity sends only the opaque current
  cause token, revision, and scope. The backend validates and applies the
  mutation, recomputes capabilities, publishes state, creates one natural
  synthetic user scene event, and offers one reaction from post-mutation state.
  Unity never supplies authoritative event prose.
- Scene Details/admin correction is silent. It changes authoritative current
  state without a canonical roleplay event or companion reaction.

Both paths wait for backend acknowledgement, are revision guarded, preserve
history, and clear causes rather than directly editing derived capabilities.
Synthetic event wording is selected from the accepted predicate/semantic family
before body facet, so decorative wristwear cannot be rendered as a restraint.
Snapshots and delayed conversation echoes share stable canonical message IDs;
Unity deduplicates by identity rather than text.

## Semantic gestures

`AvatarGestureMapper` converts the first supported emote in a response into an
`AvatarGestureIntent`; `AvatarAnimationController` maps it through standard
Humanoid bones. Current procedural intents are Nod, HeadShake, Wave, Shrug,
HeadTilt, and Thinking. Blink/lip-sync remain separate. Procedural motion
quality varies by avatar; future work may use appropriately licensed authored
Humanoid clips without changing the semantic API.

Backend posture authority currently supports standing, sitting, and lying via
explicit evidence or bounded companion actions. That state is distinct from an
actor's location/support relation. Strong authored sitting/lying presentation
is not yet available for every posture.

## Run

Start `backend_host.py` through the repository launcher or directly, open
`Assets/Scenes/AIFrenPoc.unity`, then press Play. The client connects to
`ws://127.0.0.1:8765`. Use the root developer guide for build/validation.

The ordinary Linux Development build is:

```text
Builds/LinuxDevelopment/AIFrenPoc.x86_64
```

Development players include a bounded privacy-safe flight recorder. It writes
nothing during normal rolling capture, automatically dumps serious incidents
under `/tmp/aifren-flight-recorder-<timestamp>/`, and accepts `6666666` as a
manual dump shortcut when chat is not focused. Captures contain structural
timings/counts/resources, never dialogue, prompt, subtitle, memory, credentials,
or private paths. Automated tests provide reproducible coverage, while ordinary
synthetic/test-character player interaction remains the performance acceptance
gate.

Audio Settings includes **Speak while response is generating**. It is enabled
by default and persisted by the backend settings protocol. With Kokoro it
selects bounded complete-sentence synthesis feeding one continuous playback
stream; disabling it selects the whole-response fallback. The normal graphical
Development launcher needs no environment variable or special build.

Known presentation debt includes smaller Console population costs, subtitle
fallback/wall-clock limitations, deferred non-final page dwell, and subtitle
outline/style polish. These are not audio/PTT lifecycle authority.
