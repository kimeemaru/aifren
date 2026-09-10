# AIFren design decisions

## Continuing character, replaceable implementation

Canonical dialogue persists across application and provider changes. Character
identity, authored personality, learned facts, current scene, presentation assets
and voice are distinct. There is no ordinary destructive "new chat" lifecycle.

The product principle is to persist relevant facts and infer the experience.
Sparse, evidence-backed continuity constrains improvisation; it does not attempt
full world simulation. Active State is not biography or future Relationship State.

## Memory authority and realization

V2 is normal long-term-memory authority. V1 remains deliberate one-launch rollback;
errors do not silently switch it on. Raw conversation remains permanent source
truth, and original Viewer/admin corrections have their own durable provenance.
Embeddings, indexes and episode summaries are rebuildable representations.

Exact source/speaker/scope/current-history boundaries outrank fluent model prose.
Historical ordering needs an identified canonical anchor. An immediate attribute
follow-up stays attached to its unique source; absent detail cannot be borrowed
from another event. Retrieval failure is not proof that a memory never existed.

Local models need not freely regenerate a historical answer. CompanionMemoryRealizer
normally renders an already-admitted immutable proposition with bounded natural
surface variation. The same inference may add a present subjective reaction;
unsafe or missing reactions are dropped without losing the factual answer or
invoking repair. This is normal realization, not an emergency fallback experience.
Other providers remain replaceable within the same authority and validation rules.

## Corrections, scopes and current reality

Corrections append/supersede through their owning API and retain evidence history.
They never rewrite canonical dialogue. Attribute changes preserve object identity;
replacement is explicit. Independent relation causes remain distinct even with
identical labels/loci. Removing one cause cannot remove another's consequence.

Scenario time/state and real-world elapsed time remain separate. Genuine human
participation can supply temporal return context; app closure or elapsed hours
cannot prove sleep, travel or off-screen activity. Reconnect/startup is not a new
human interaction and cannot consume a return opportunity.

## Presentation

Unity direct VRM is the normal presentation. Portrait and landscape retain
independent framing/backgrounds; opening UI never moves or resizes the avatar.
Character identity is not the avatar file. Imported originals remain user-owned.

Hidden subtitles are speech-only. Nunito SemiBold, restrained outline/shadow,
180 ms per-word onset and bounded dwell belong to one SubtitleStyle/presenter
boundary. Saved color/reveal/instant choices remain authoritative. Per-word
opacity multiplies base RGB without rebuilding text/layout every frame. Temporary
peek preserves progress; committed Show and interruption cancel immediately.

Expression metadata is optional. Explicit emotion/neutral wins over bounded
current self-facial emotes; absent requests preserve the face. Body gesture,
persistent face, authored blink/mouth/gaze overrides and speech retain separate
owners. No mood, relationship truth or new canonical event is inferred from motion.
The public tree supports external approved clips but ships no new motion trial.

The Current Scene drawer reveals by hover/click/focus with exit grace. Its exact
cause-specific commands retain pending/error/revision semantics. Visibility is
not a mutation. The Memory Viewer/Editor is implemented, bounded and provenance
aware; silent administrative correction remains distinct from in-world removal.

## Cancellation and resources

Only accepted final responses publish. Persistence, cancellation, character/scope,
socket and asset generations are authoritative. PTT must retire obsolete speech
promptly; subtitles cannot delay readiness. Owned local processes are identified
by durable process identity, never by a port alone. Managed llama.cpp disables
unneeded per-token logits with `--logits_all false`.

## Validation and remaining work

Public correctness tests use synthetic fixtures with explicit data/preferences
ownership. Real embedding assertions are separate from structural toy substitutes.
Native XML/build validation is separate from subjective appearance and acoustic
quality. No private fixture, log or installed model is implied by public source.

V2 promotion is implemented; pre-1.0 work remains on companion feel, presentation,
management/backup, cross-platform distribution and hardening. Relationship State
and controlled external capabilities remain later work, not implied authorization.
Historical shadow experiments are retained only where useful as disconnected
research/helpers; their former default/gating statements are superseded.
