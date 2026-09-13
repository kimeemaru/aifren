# Natural companion delivery

This milestone connects three existing owners: accepted dialogue, speech, and
avatar presentation. It does not change memory authority or introduce a new
conversation store. Memory V2 and the responsive Context Governor remain normal;
CompanionMemoryRealizer continues to own governed memory statements.

## Controls and defaults

| Settings section | Control | Default | Ownership |
| --- | --- | --- | --- |
| Dialogue | Natural conversation / Roleplay | Roleplay, preserving existing delivery | Application delivery preference; saved personality and history are unchanged. |
| Audio | Responsive speech | On | Prepare and play the beginning of an accepted utterance while preparing its remainder. |
| Appearance | Automatic expressions (local CPU) | Off | Optional tentative facial proposal, with readiness status. |
| Appearance | Explicit avatar cues (ACT preview) | Off, unchanged | Optional model presentation control through the existing parser and resolver. |

The controls use the existing settings snapshot, Save/Cancel, and application
preference isolation. Saving one control preserves the other saved choices and
unrelated settings. Automatic expressions require the separately installed local
classifier. Enabling the preference does not download weights during a turn.

## Speech begins after acceptance

`AssistantService` validates and persists one assistant response before deriving
its permitted spoken projection. Responsive speech passes that complete
projection to the existing `StreamingSpeechQueue`. It does not consume unchecked
provider deltas or create a separate chat turn for each chunk.

The existing chunker selects two modest opening units, then bounded subsequent units.
The committed path uses 48/120/160-character opening thresholds and
48/96/120-character later thresholds (minimum/preferred/hard). These are native
work-unit bounds, not dialogue limits; indivisible words remain intact.
The second short unit provides synthesis runway while the first is playing.
Whitespace and indivisible words are preserved; chunk boundaries do not impose a
response-length limit. The service uses two-entry work and prepared-audio queues
with backpressure and one synthesis worker. Kokoro stays on CPU. The shared
synthesis resource manager retains cancellation and recovery ownership.

The first prepared unit starts one continuous playback ID. Later units append in
order through the existing prepared-stream API, followed by one finish. Each
unit carries sequence, word, and sample offsets. Content sample offsets are
distinct from playback offsets, which include any actual starvation silence.
Audio callbacks only consume prepared samples; they do not synthesize, perform
I/O, or wait for producers.

Unity's `CommittedSpeechTimeline` keeps one complete subtitle utterance. Future
words remain unscheduled until their audio is available. Later timing updates
preserve the existing reveal clock, page state, fades, and final dwell. Alignment
uses provider word timings where valid and labels estimated timing honestly.
Lip-sync receives continuing chunks without resetting the mouth between them.

PTT, cancellation, and replacement retire preparing, queued, and playing work.
A later synthesis failure ends the utterance as a failure; already spoken words
are not replayed. Audio authority remains independent of subtitle visibility.
Stop captures both service and provider generation before detaching work.
Kokoro's conditional stop invalidates only that captured generation and aborts
only its captured output stream. An idle Stop or delayed old-queue cleanup cannot
stop a newer direct/continuous utterance or publish an identity-free stale stop.
Disabling responsive speech selects the existing full-preparation path. Providers
without owned continuous playback retain their supported path.

First-playback latency and total completion time are different measurements.
Chunking can reduce the wait for an opening unit while still encountering CPU
contention, cold model loading, synthesis gaps, or prosody changes at boundaries.
PCM counts and lifecycle callbacks alone do not establish acoustic quality.

## Delivery preference and template support

`aifren/dialogue/conversation_style.py` replaces only recognized application-owned roleplay and
output-contract scaffolding. The authored identity/personality substring is
preserved. Natural conversation asks for a proportionate reply, meaningful action
prose, and questions only when useful. Its synthetic delivery illustrations are
explicit examples, never canonical dialogue or claimed shared history.

A completed backend-owned state update alone does not disable Natural delivery.
The shared gate still rejects actual machine obligations; ACT and lean eligibility
remain unchanged. The retained wording is guidance: a subsequent four-pair
wording candidate was mixed and was not adopted.

The preference applies only to eligible ordinary local turns. Governed memory,
constrained speech, exact spoken content, and typed action/capability obligations
retain their existing paths. Unknown custom prompt shapes fail closed to the
established contract. ACT, when selected, remains optional; Natural mode does not
activate the earlier lean experiment or remove structured infrastructure.

Development recording distinguishes selection from application with the bounded
`conversation_delivery` event: `source` is the selected style, `state` reports
`applied` or `not_applied`, and `reason` distinguishes ordinary local delivery,
structured obligations, a nonlocal provider, an unrecognized custom prompt, or
Roleplay selection. It records no personality, request or reply text. A saved
Natural preference alone is not proof that a governed/constrained reply used it.

Nonempty, unfenced quoted prose remains plain dialogue even when JSON decoding
recognizes a string. Quotation and escapes stay literal data; an inner envelope or
ACT example is never executed. This avoids an unnecessary repair inference that
could replace ordinary wording with narrated prose. Explicit JSON fences and
invalid envelopes retain their checks, as do capability and memory validators.

The complete selected policy enters the existing Context Governor before final
budget verification. There is no new context allocation or hidden unbudgeted
style message. Historical roleplay remains unchanged historical data, with no
postprocessor deleting questions or actions from accepted responses.

`aifren/llm/local_template.py` makes a narrower decision than a model-family assumption.
It reads the configured GGUF's embedded chat-template metadata without loading
tensors. Only an explicitly reviewed template fingerprint permits the initial
application policy to use the template's supported system role. Unknown templates
or an unavailable metadata reader retain the existing adapter role. For that
verified template, the one exact application-owned Natural delivery policy is
placed after historical examples and before the intact current user
turn. Its content was already budgeted; the additional message uses the existing
framing reserve. Canonical message roles are untouched; the server still applies
its own embedded template once. This does not claim that every Gemma version
supports the same roles, or change sampling, stop tokens, context capacity, or
quantization.

Style is guidance, not guaranteed acting. A persona and its historical examples
can still strongly influence prose. A shorter prompt or correct role does not by
itself prove a conversational improvement. A further bounded wording comparison
increased narration/questions and introduced unsupported personal detail; that
candidate was rejected. The retained policy still permits excessive elaboration
and questions in genuine use. The quoted-prose repair is a specific admission fix,
not evidence of a general improvement in model acting or factual reliability.

## Governed memory and present commentary

The authoritative factual core remains immutable. The optional existing model
call can select a closed present speech act: `INTEREST`, `APPROVAL`, `CONCERN`,
`SUGGESTION`, or `NONE`. CompanionMemoryRealizer renders that selection as a short
owned comment, such as an invitation to discuss the subject. The selection cannot
supply a new remembered circumstance, reason, trait, or past feeling.

This is a typed presentation choice, not a claim to semantically verify arbitrary
English. The existing narrow plain-reaction compatibility check remains; it has
not become a general paraphrase validator. Invalid or unsuitable commentary is
dropped while the factual core stays intact. No additional model stage or repair
cycle is introduced. Exact quotation remains available where grammatical
reporting cannot preserve the admitted proposition safely.

## Automatic facial proposals

`aifren/dialogue/automatic_expression.py` is an optional local CPU classifier over final accepted
assistant prose. It does not classify the user's feelings or validate dialogue.
For governed memory, only accepted present commentary is eligible; the factual
core is excluded. Quoted/reporting material, code, control-like payloads, and
over-budget text cause conservative abstention. Existing typed action spans are
excluded from classifier prose, without editing canonical dialogue.

The worker permits one in-flight item, no backlog, and a two-second result
deadline. Loading and inference run away from dialogue publication and first
speech. Service-owned turn, character, scope, preference, and cancellation
transitions retire the proposal lease. A late result is discarded; the worker
does not query service-owned memory databases to recover authority.

Only clearly admitted joy, sadness, anger and surprise labels map to restrained
semantic faces. The top label must be one of those classes, score at least 0.75,
and lead other mapped faces by 0.15. Opposite joy/anger/sadness scores of 0.20
veto a proposal. Co-occurring optimism does not itself create or compete as a
face. These are model-specific presentation thresholds, not truth confidence.
No strong mapped emotion means no new inferred request. Intensity remains 0.45.

An automatic face is a temporary overlay over the last explicit/default semantic
expression. A newer accepted reply, interruption, switch or disable retires that
overlay. Natural speech completion allows a 0.35-second dwell; an 8-second maximum
hold prevents indefinite persistence when completion is unavailable. Existing
0.20-second transitions release it gracefully. Exact lease identity prevents old
cleanup from clearing a newer proposal. Equivalent targets renew the lease without
restarting the blend. Retirement emits no neutral emotion claim or history update,
and does not clear manual/ACT ownership, blink, gaze or lip-sync.

The existing resolver keeps one precedence order: manual ownership and backend
restrictions, explicit ACT/structured face requests, admitted automatic face
proposals, then eligible legacy compatibility. A pending automatic face reserves
only that channel; body gesture compatibility stays independent. Publication is
once per current response. Explicit neutral still resets, omission preserves the
face, and equivalent requests do not restart transitions. Blink, gaze, lip-sync,
and avatar-specific mappings retain their existing owners.

The classifier is English text-emotion classification, not an acting model. It can
misread sympathy, negation, sarcasm, mixed tone, or a character's distinctive
voice. Abstention is intentional. Automatic presentation creates no durable mood,
physical action, user diagnosis, memory fact, or prompt authority.

## Connected-interaction repairs

A newly recalled event can resolve a concrete immediate follow-up directly from
its exact admitted canonical record, without waiting for a derived episode.
Hash, character, speaker, scope and unique-anchor checks still apply; broader
related-source searches retain their episode requirement. Projection remains at
most two passages sharing 220 characters. A bounded existing place grammar also
recognizes the ordinary past-observation verb "spotted" with the same uncertainty
and negation exclusions.

Current-activity extraction now checks the existing value contract before
promising a mutation. If a value is not representable, its raw text remains in
canonical dialogue; no punctuation is stripped to fabricate a different state.
This prevents a rejected automatic proposal from suppressing an otherwise-safe
reply. Existing authoritative state is preserved.

Speech-only caption limits no longer apply merely because a hand or sensory
capability is constrained. Capability contradictions remain rejected. A failed
ordinary response cannot accidentally select an empty, untriggered memory-answer
fallback. These are specific delivery/admission repairs, not a broader memory
architecture change.

Consecutive explicit memory questions also service at most one existing
32-record embedding page before retrieval, when canonical observation is already
complete and only that derived lane is behind. This avoids requiring a ten-second
idle gap between short answers. Actual health is read again afterward: remaining
backlog, failed encoding or incomplete observation still means unavailable.
It performs no canonical replay, episode/model summary or V1 access. Ordinary
turns and exact-source attribute follow-ups do not invoke this generic vector
preflight. The query-slot parser also excludes the temporal operators "before"
and "after" from a favorite-property noun capture; existing property selection and
strict canonical ordering remain authoritative.

## Limits and installation

Natural mode remains guidance; narration, unnecessary questions and unsupported
incidental ordinary backstory may still occur. Automatic expression selection is
not guaranteed emotional judgment. Optional memory commentary stays a limited
closed mechanism; unsafe commentary is dropped without losing the factual answer.
CPU native synthesis cannot be forcibly preempted safely; replacement can wait for
the in-flight unit even though queued/playing ownership is retired immediately.

Install optional CPU expression requirements explicitly in the project environment:

```bash
.venv-aifren/bin/python -m pip install -r requirements-expressions.txt
.venv-aifren/bin/python -m aifren.dialogue.automatic_expression --install
```

The installer downloads pinned/hash-checked files and converts locally using standard
libraries. It does not execute remote custom Python. Runtime uses local INT8 ONNX;
weights remain outside Git, under the resource root. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the model revision and licenses.
No automatic installation happens during a conversational turn.

## Source-to-design references

Ordered speech segments and cancellation responsibilities were informed by AIRI;
separation of dialogue and expression responsibilities was informed by SillyTavern.
These are original AIFren implementations, not copied/translated source. The pinned
reviews, licenses and attribution are retained in THIRD_PARTY_NOTICES.md. No external
project's voice, avatar, motion or model is licensed merely by citing its design.
