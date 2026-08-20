# AIFren Project

## Vision

AIFren is personal, local-first "forever software": a long-lived AI companion
for daily use over five or more years. Its central differentiator is
continuity. A character should feel like one persistent individual whose
relationship and shared history accumulate over months and years.

This is not a disposable chatbot-session product or an imitation of a
constantly autonomous streamer. Intelligence, contextual responsiveness,
personality consistency, and recalling the right things matter more than
constant chatter. A polished application suitable for the owner's own daily
use is the 1.0 priority; broad distribution concerns are secondary.

## Durable design principles

1. Character identity and accumulated canonical history are durable assets.
2. LLMs, TTS/STT, embedding models, retrieval indexes, and frontends are
   replaceable where practical; canonical records are not.
3. Raw conversation history is retained indefinitely unless explicitly deleted.
   Summaries, embeddings, indexes, and derived memories never replace it.
4. Data durability, provenance, backup/recovery, portability, and migration
   matter more than minimizing storage size.
5. An existing character has no normal "New Chat" lifecycle. Reopening AIFren
   returns to that continuing character; a new independent history belongs to
   a genuinely new character identity.
6. Avoid mandatory cloud dependencies, giant rewrites, and speculative
   complexity. Work toward stable, usable milestones.

## Current implementation

Today AIFren has a Tkinter desktop frontend and a Unity companion-client
prototype, both backed by the same frontend-independent Python service. It
provides Gemini LLM integration; persistent conversation history/summaries and
long-term memory; an editable file-backed personality; local embedding
retrieval; local-capable Whisper STT; Kokoro TTS (`af_heart`, speed `1.0`) with
Piper fallback; global F8 push-to-talk in Tkinter; and window-focused,
rebindable PTT in the Unity development client.
Unity currently owns presentation-only idle/reactive animation and
envelope-driven mouth motion; the Python backend remains authoritative for
speech audio and all canonical data.

The reproducible Python 3.10 AIFren runtime includes the selected Kokoro
provider. The current character configuration is Serval, while character data
is separate from conversation history so explicit personality can be edited
without discarding continuity.

See [ARCHITECTURE.md](ARCHITECTURE.md) for current boundaries and persistence,
and [AGENTS.md](AGENTS.md) for concise working instructions.

## Current development focus

Friend-test packaging is deferred. The current priority is a visual
presentation and avatar-viewer remake.

- Target VRoid Hub-level avatar sharpness at comparable on-screen face and
  upper-body size. The current UV/crop framing is transitional; do not keep
  patching it as final architecture.
- Preferred direction: stable, high-resolution full-avatar RenderTexture ->
  presentation container -> X/Y translation plus transform scale, with
  independent portrait and landscape presentation state.
- The eventual viewer should accept arbitrary standards-compliant VRM assets
  where possible, with graceful capability fallback when a model lacks an
  optional feature.
- **Background policy:** Light/Dark affects UI only; there is no automatic
  day/night cycle. Background is independent from theme, portrait and
  landscape remember separate selections, the intended portrait default is a
  solid white/light neutral, and the intended landscape default is the bundled
  bedroom. Later work may allow user PNG backgrounds, but not a user
  crop/framing editor.
- **Dialogue presentation:** improve typography/font, spacing, and reveal/fade
  while preserving the backend-owned complete response and speech flow.

Deferred unless separately approved: portable friend-test packaging, Memory V2
authority, further UV-framing patches, multi-character work, deeper animation,
and Linux packaging.

## Current non-goals

- Unity microphone/audio streaming, lip sync, locomotion, VR, and world
  simulation are not implemented. Unity’s current window-focused PTT only
  routes its input through the existing backend voice boundary.
- Semantic memory deduplication, contradiction resolution, supersession, and
  archival are not yet implemented.
- A game-style main menu, new-chat workflow, or a large 3D game are not goals.

## Product requirements and future direction

The following are requirements and architectural guidance, not claims of
current implementation.

### Continuity and character identity

- Normal startup should immediately restore the last-used character, or show
  character selection only when necessary.
- Different characters must eventually be wholly separate identities: raw
  history, memories, summaries, relationship state, personality/configuration,
  avatar/presentation, voice, and other character-specific state.
- Development remains focused on one believable character first, but backend
  character swapping must not mix identities.
- Base personality, morality, and identity are highly persistent and explicitly
  user-editable. Learned memories, experiences, or self-generated claims must
  not silently rewrite them.

### Memory and relationship development

- Memory is AIFren's highest-priority differentiator. The eventual system needs
  complementary layers: canonical raw events, working context, short- and
  long-term memories, important facts/events, episodic shared experiences,
  temporal memory, relationship state, summaries, and possibly recurring
  topics/running jokes.
- Retrieval should eventually balance relevance, recency, importance, diversity,
  and repetition suppression. A remembered phrase or joke must not recur in
  every conversation.
- Corrected/outdated memories should eventually be superseded, deprecated, or
  archived rather than automatically permanently deleted. In-app inspection and
  correction should live in Settings/Advanced or another secondary interface.
- Learned relationship/experience state may influence behavior but remains
  corrigible and separate from authoritative base personality.

### Input, proactivity, and settings

- Voice should become the primary interaction mode; keyboard remains a critical
  fallback. PTT should eventually be globally/configurably bindable where
  practical, including mouse thumb buttons. Interrupting speech on PTT is
  acceptable; elaborate interruption reactions are not a 1.0 requirement.
- Proactivity must be conservative and configurable: approximate check-in
  interval, quiet/sleep periods, and an explicit user preference. A companion
  left running overnight should normally remain quiet.
- Settings should become comprehensive but understandable, with Advanced for
  technical/debug controls. Expected domains include audio/TTS, STT/PTT,
  dialogue, avatar/background/animation, characters, proactivity, memory
  inspection, and model/backend configuration.

### UI and presentation

- Normal startup/navigation should not resemble a game menu. Most useful UI may
  remain visible, with a Hide UI mode that makes the character/background the
  focus and temporarily reveals input when typing begins.
- Support polished windowed/fullscreen landscape layouts from 1280x720 through
  1920x1080, plus deliberate portrait/TATE compositions around 900x1600 and
  1080x1920 rather than squeezing landscape UI.
- Near-term visual priority is a polished viewport: 2D background, expressive
  VRM character, dialogue/UI, voice, animation, and lip sync. Detailed physical
  interaction and 3D environments are lower priority.
- Future presentation may add gestures, expressions, multiple idles, and
  explicitly permitted outfit changes. Do not infer an outfit implementation
  from this requirement.
- As visual expression improves, reduce needless asterisk action narration and
  eventually distinguish dialogue, emphasis/prosody, and presentation metadata.
  Not every textual action needs an animation.
- Dialogue reveal speed should remain configurable. Lip sync should eventually
  activate automatically for normal spoken output, with synchronization tuning
  configurable when implemented.

## Additional long-term companion requirements

These are product requirements and future architectural guidance, not current
runtime behavior.

- Canonical storage and conscious recall are distinct: the archive is never
  deleted to simulate forgetting. Any future imperfect recall must be
  conservative and not frustrating.
- User/profile facts and character episodic/shared memories are conceptually
  distinct. Stable facts may be retrieved beside shared experiences, promises,
  repairs, running jokes, and milestones, but neither should be mistaken for
  the other.
- Explicit, plausible user corrections are high-authority evidence. Clear
  conflicts preserve competing evidence or a disputed state rather than
  rewriting history. The companion remains non-argumentative.
- Negative experiences may be remembered, but relationship change requires
  clear/repeated evidence or explicit events. Ambiguous sentiment, one comment,
  and model interpretation cannot create lasting injury, resentment, mood, or
  personality drift.
- A future memory browser uses bounded queries, search, filters, and
  pagination/virtualization; it never renders a lifetime archive at once.
  Correction/hide/delete operations retain provenance/history semantics.
- Editing base personality normally preserves identity, relationship, and
  history. A new identity/history requires an explicit new-character or fork
  operation. Authored interests anchor identity; learned interests cannot make
  the character dismissive of a topic the user still cares about.
- The default experience strongly favours immersion (approximately 90% kayfabe,
  10% software awareness as philosophy, not a setting). Explicit character
  configuration remains authoritative; accidental fourth-wall breaks are not
  reinforced memories.
- Avoid quests, relationship meters, and overt game mechanics. Natural but
  bounded follow-ups are desirable; ignored proactivity is only a weak back-off
  signal, never relationship rejection.
- Availability/proactivity must be conservative and not rely on a fixed sleep
  schedule. Good-night language should pause interruptions until later activity
  suggests availability. Any user-facing proactivity level explains its effect.
- New characters can gradually get acquainted without interrogation, a tutorial,
  or relationship levels. They should have conversational initiative, then move
  on if a suggested topic is ignored.
- Future presentation separates roleplay actions from spoken dialogue: actions
  are not spoken, may drive recognised animation/expression, and otherwise are
  safely ignored. Emotion-aware TTS should prefer structured state over raw
  punctuation.
- The end state is local/offline for core use. Cloud providers are optional;
  hardware-aware setup and straightforward Windows/Linux distribution are later
  goals. Canonical data must remain portable across checkout paths, OSes,
  hardware, models, and external SSD archives. Replacing an LLM never resets
  the character's identity, history, or relationship.

## Development workflow

```text
Plan -> small implementation -> automated validation -> user testing
     -> stable Git commit -> next stage
```

Preserve working behavior and persistent data formats unless a planned migration
is approved.

## Project license and future packages

AIFren source is licensed under **AIFren Public Source License v1.0**,
Copyright © 2026 kimeemaru. The root [LICENSE.md](LICENSE.md) is the
authoritative text. It applies only to rights controlled by AIFren; third-party
components and assets retain their own licenses. Any future portable
friend-test package must include `LICENSE.md` and the applicable separate
third-party notices.

### Deferred portable friend-test package

This is a packaging requirement, not a current deliverable. The intended
Windows experience is: extract a `.7z`, double-click `AIFren.exe`, silently
start the bundled backend/runtime, and open AIFren without Python, pip, model
downloads, installers, terminal windows, AppData, or a pre-existing local
checkout.

The package must contain the Unity player, portable Python/runtime
dependencies, approved Kokoro and STT assets, required native libraries,
VRoidPreset_A under its official sample-model conditions, AIFren-generated
default background assets, a fresh user-data scaffold,
`LICENSE.md`, third-party notices, `VERSION.txt`, and `README-FIRST.txt`. It
must never include a Gemini API key, conversation or Memory V1 data, developer
logs, generated audio, or development secrets. User data must stay inside the
extracted AIFren directory, and the in-app Gemini key setting is the supported
setup path. Reuse the existing AIFren heart logo for the eventual executable
icon and simple startup presentation; do not replace it merely for packaging.

## Completed architecture stages

1. Frontend-independent `AssistantService`.
2. `Conversation.build_context()` as the authoritative context
   builder.
3. Memory integrity: atomic persistence, validation, provenance,
   and user-only memory evidence.
4. Tkinter text turns through the service.
5. PTT/STT through the same service boundary.
6. Loopback-only local WebSocket transport.
7. Unity proof-of-concept and VRM support — separate local presentation client.
8. Kokoro TTS provider with Piper fallback.
9. Unity companion presentation/runtime: reproducible real Kokoro
   runtime, compact/scrolled dialogue, history/settings, VRM framing/lighting,
   and presentation validation.

## Near-term roadmap

1. Remake the Unity avatar viewer and dialogue presentation around the current
   visual focus before deeper animation or world work.
2. Improve memory quality, backup/recovery, and character-scoped persistence
   as separately approved data-focused stages.
3. Evolve input toward configurable voice/PTT and support a deliberate
   multi-character lifecycle without disrupting the one-character daily-use
   experience.
