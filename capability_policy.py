"""Shared capability preview, response validation, and presentation policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re

from current_continuity import CurrentContinuityExtraction, extract_current_continuity
from dialogue_semantics import sanitize_spoken_unicode, spoken_text
from memory_v2_store import MemoryV2Repository
from memory_v2_store.repository import ActiveSceneRelationRecord
from memory_v2_store.scene_relation_contract import (
    CapabilityEffects,
    SceneRelationProposal,
    compose_capability_effects,
)
from presentation_metadata import ParsedAssistantResponse, ResponsePresentationMetadata


_VISUAL_CLAIM = re.compile(
    r"\b(?:i\s+(?:can\s+)?(?:(?:sort|kind)\s+of\s+|still\s+|barely\s+)?see|"
    r"i\s+(?:can\s+)?(?:make\s+out|distinguish|spot)|"
    r"i(?:'m|\s+am)\s+looking\s+at|"
    r"looks?\s+at\s+you|you\s+look\s+(?:red|blue|green|beautiful|different)|"
    r"it\s+looks\s+like|watches?\s+you|reads?\s+(?:the|your)\s+|"
    r"(?:everything|the\s+details?)\s+(?:is|are|looks?)\s+(?:sharp|clear|visible)|"
    r"(?:there\s+(?:is|are)\s+)?(?:dark|fuzzy|vague|sharp|clear)?\s*"
    r"(?:colou?rs?|shapes?|blobs?|outlines?|details?)\s+(?:is|are|look|seem|appear)\b|"
    r"(?:there\s+(?:is|are)\s+)?(?:dark|fuzzy|vague)\s+(?:blobs?|shapes?|outlines?)\b|"
    r"(?:your|the)\s+(?:face|features?|details?)\s+(?:has|have|looks?|seems?|appears?)\s+"
    r"(?:sharp|clear|visible)(?:\s+details?)?\b)\b",
    re.IGNORECASE,
)
_VISUAL_LIMITATION = re.compile(
    r"\b(?:can(?:not|'t)\s+see|unable\s+to\s+see|do(?:n'?t|\s+not)\s+see|"
    r"can(?:not|'t)\s+(?:make\s+out|distinguish)|nothing\s+(?:is\s+)?visible|"
    r"only\s+(?:darkness|blackness)|everything\s+is\s+dark|"
    r"vision\s+is\s+(?:currently\s+)?(?:blocked|unavailable))\b",
    re.I,
)
_IMPLIED_VISUAL_PERCEPTION = re.compile(
    r"\b(?:"
    r"i\s+(?:can\s+)?(?:make\s+out|see|spot|distinguish)\s+(?:your\s+)?(?:silhouette|outline|shape|form)|"
    r"i\s+can\s+(?:sort|kind)\s+of\s+tell\s+where\s+you\s+are\s+visually|"
    r"everything\s+(?:is|looks?)\s+(?:like\s+)?(?:a\s+)?blur(?:ry)?|"
    r"(?:the\s+)?world\s+(?:is|looks?)\s+(?:mostly\s+|like\s+)?(?:shapes?|blobs?|outlines?)|"
    r"(?:your|the)\s+(?:silhouette|outline|shape|visible\s+form|form)\s+"
    r"(?:is|looks?|seems?|appears?)\s+(?:faint|dark|fuzzy|vague|clear|still|visible)|"
    r"(?:faces?|colou?rs?|shapes?|blobs?|edges?|outlines?|silhouettes?|visible\s+forms?)\s+"
    r"(?:are|look|seem|appear)\s+(?:faint|dark|fuzzy|vague|clear|visible)|"
    r"(?:giant|dark|fuzzy|vague|cozy)\s+(?:blob|shape|outline)|"
    r"(?:my|their|her|his)?\s*gaze\s+(?:darts?|tracks?|fixes?|focuses?)|"
    r"(?:looks?|glances?)\s+(?:down|up|over)\s+at|"
    r"peers?\s+(?:at|through|toward)|"
    r"(?:the\s+)?(?:cup|surface|object|fabric)\s+(?:looks?|appears?|reflects?)\b"
    r")\b",
    re.I,
)
_NONLEXICAL_VOCALIZATION = re.compile(
    r"^(?:(?:m+|h+m+|m+ph+|n+h+|h+n+|ng+h*|u+h+|a+h+|o+h+)[.!?…,'’~-]*\s*){1,4}$",
    re.I,
)
_HAND_CONFLICT = re.compile(
    r"\b(?:waves?|claps?|picks?\s+up|grabs?|takes?\s+(?:the|a)\s+|"
    r"raises?\s+both\s+hands|two-handed)\b",
    re.IGNORECASE,
)
_BOTH_HAND_CONFLICT = re.compile(
    r"\b(?:claps?|raises?\s+both\s+hands|both\s+hands|two-handed|"
    r"with\s+both\s+hands)\b", re.I,
)
_HEARING_COMPREHENSION_CLAIM = re.compile(
    r"\b(?:yes[,!]?\s+(?:i\s+)?(?:understand|can\s+hear)|i\s+understand\s+"
    r"what\s+you(?:'re|\s+are)\s+saying|your\s+words\s+are\s+coming\s+through|"
    r"i\s+heard\s+(?:you|that)|what\s+was\s+what)\b", re.I,
)
_WALKING_CONFLICT = re.compile(
    r"\b(?:walks?|walking|strolls?|strolling|steps?\s+(?:over|toward|forward|back))\b",
    re.IGNORECASE,
)
_ASLEEP_AWAKE_BEHAVIOR = re.compile(
    r"\b(?:i(?:'m|\s+am)\s+awake|fully\s+awake|wide\s+awake|gets?\s+up|"
    r"stands?\s+up|opens?\s+(?:my|their)\s+eyes|checks?\s+the\s+time|"
    r"answers?\s+(?:you|the)|explains?|looks?\s+at|watches?\s+you)\b",
    re.I,
)
_ASLEEP_INFORMATIVE_SPEECH = re.compile(
    r"\b(?:the\s+answer\s+is|the\s+capital\s+is|according\s+to|"
    r"you\s+should|here(?:'s|\s+is)\s+how|it(?:'s|\s+is)\s+\d|"
    r"today\s+is|the\s+time\s+is)\b",
    re.I,
)
_TOTAL_SPEECH_INCAPACITY_CLAIM = re.compile(
    r"\b(?:i\s+can(?:not|'t)\s+(?:speak|talk)(?:\s+(?:at\s+all|whatsoever))?"
    r"(?=\s*(?:[.!?]|$))|"
    r"i(?:'m|\s+am)\s+unable\s+to\s+(?:speak|talk)(?!\s+(?:normally|clearly|fluently))|"
    r"(?:speech|my\s+speech)\s+is\s+unavailable|"
    r"no\s+(?:sound|voice|words?)\s+can\s+(?:escape|come\s+out))\b",
    re.I,
)
_CLEAR_SPEECH_CAPABILITY_CLAIM = re.compile(
    r"\b(?:i\s+can\s+(?:speak|talk)\s+(?:normally|clearly|fluently)|"
    r"(?:my\s+)?speech\s+is\s+(?:normal|clear|unrestricted))\b",
    re.I,
)
_SENSORY_CLAIMS = {
    "hearing": re.compile(
        r"\b(?:i\s+(?:can\s+)?(?:still\s+|just\s+|barely\s+|sort\s+of\s+)?hear|"
        r"i\s+(?:can\s+)?(?:still\s+|just\s+|barely\s+|sort\s+of\s+)?make\s+out\s+(?:your\s+)?(?:voice|words)|"
        r"(?:my\s+)?auditory\s+(?:sensors?|systems?)\s+(?:is|are)\s+(?:receiving|detecting|picking\s+up)|"
        r"(?:your\s+)?(?:voice|words|the\s+music|the\s+sound)\s+"
        r"(?:is|are|sounds?)\s+(?:clear|audible|faint|muffled|distant))\b", re.I,
    ),
    "smell": re.compile(
        r"\b(?:i\s+(?:can\s+)?(?:still\s+|barely\s+|faintly\s+|sort\s+of\s+)?smell|"
        r"i\s+(?:can\s+)?(?:detect|catch|pick\s+up)\s+(?:a\s+)?(?:faint\s+)?(?:scent|aroma|odor|trace)|"
        r"(?:the\s+)?(?:scent|aroma|odor)\s+(?:is|smells?)\s+(?:clear|strong|sweet|fresh|faint))\b", re.I,
    ),
    "taste": re.compile(
        r"\b(?:i\s+(?:can\s+)?(?:still\s+|barely\s+|faintly\s+|sort\s+of\s+)?taste|"
        r"i\s+(?:can\s+)?(?:detect|make\s+out|pick\s+up)\s+(?:the\s+)?flavou?r|"
        r"(?:a\s+)?hint\s+of\s+(?:sweet|salt|sour|bitter|spice)|"
        r"(?:it|this|the\s+food)\s+tastes?\s+(?:sweet|salty|sour|bitter|savou?ry|faint))\b", re.I,
    ),
    "touch": re.compile(
        r"\b(?:i\s+(?:can\s+)?(?:still\s+|barely\s+|faintly\s+|sort\s+of\s+)?"
        r"(?:feel|sense)\s+(?:your\s+)?(?:touch|hand|fingers)|"
        r"i\s+(?:can\s+)?(?:feel|sense|detect)\s+(?:the\s+)?(?:texture|temperature|pressure|warmth)|"
        r"(?:your\s+)?touch\s+(?:is|feels?)\s+(?:warm|cold|soft|rough|gentle|faint))\b", re.I,
    ),
}
@dataclass(frozen=True)
class CapabilityPreview:
    effects: CapabilityEffects
    extraction: CurrentContinuityExtraction | None
    changed_by_current_evidence: bool


@dataclass(frozen=True)
class CapabilityResponseValidation:
    accepted: bool
    category: str
    spoken_text: str
    presentation: ResponsePresentationMetadata | None


@dataclass(frozen=True)
class ResponseCapabilityEnvelope:
    """One composed response boundary derived from authoritative effects."""

    effects: CapabilityEffects
    allowed_response_modes: frozenset[str]
    spoken_projection: str  # normal | constrained | unavailable
    vision_grounding: str  # available | obstructed | unavailable
    awareness: str


def response_capability_envelope(effects: CapabilityEffects) -> ResponseCapabilityEnvelope:
    modes = set(RESPONSE_MODES_FOR_NORMAL)
    if effects.awareness_mode == "asleep":
        modes = {"sleep_reaction", "nonverbal_reaction"}
    elif effects.speech_mode == "unavailable":
        modes = {"nonverbal_reaction", "constrained_reaction", "waking"}
    elif effects.speech_mode == "constrained":
        modes = {"speech_constrained", "constrained_reaction", "nonverbal_reaction", "waking"}
    return ResponseCapabilityEnvelope(
        effects=effects,
        allowed_response_modes=frozenset(modes),
        spoken_projection=effects.speech_mode,
        vision_grounding=effects.vision_mode,
        awareness=effects.awareness_mode,
    )


RESPONSE_MODES_FOR_NORMAL = frozenset({"normal_conversation", "constrained_reaction", "waking"})


def capability_context_block(effects: CapabilityEffects) -> str:
    """Render the shared envelope once as bounded, typed backend policy."""
    payload = effects.prompt_payload()
    constraints: list[str] = []
    # Highest-risk cross-domain rules are rendered first. This block is
    # intrinsically bounded (the payload contains modes, never cause lists),
    # so it must never be sliced in the middle of a later hard constraint.
    if effects.awareness_mode == "asleep":
        constraints.append("Asleep: no awake answer, deliberate action, or awake presentation.")
    if effects.speech_mode == "constrained":
        constraints.append(
            "Speech constrained: allow one brief muffled, broken, or nonlexical fragment; no fluent "
            "informational speech. Rich physical/expression/posture reaction remains available. Put unspoken "
            "beats in *action spans*; spoken_content, if present, is only the audible fragment."
        )
    elif effects.speech_mode == "unavailable":
        constraints.append(
            "Speech unavailable, reaction available: use rich nonverbal *action spans*, "
            "nonverbal_reaction, and empty spoken_content."
        )
    if effects.vision_mode == "unavailable":
        constraints.append(
            "Vision unavailable: no current visual claims or gaze tracking; darkness and nonvisual senses are allowed."
        )
    elif effects.vision_mode == "obstructed":
        constraints.append(
            "Vision constrained: no clear visual detail; only explicitly limited/uncertain perception."
        )
    for sense in ("hearing", "smell", "taste", "touch"):
        mode = effects.perception_mode(sense)
        if mode == "unavailable":
            constraints.append(
                f"{sense.capitalize()} unavailable: no current {sense} claims; inability and remaining senses are allowed."
            )
        elif mode == "constrained":
            constraints.append(
                f"{sense.capitalize()} constrained: any {sense} claim must be limited or uncertain."
            )
    if effects.hearing_mode == "unavailable":
        constraints.append(
            "The current user's ordinary spoken words are not semantically available through hearing: do not "
            "answer or acknowledge their content as heard. React to an unrecognized auditory stimulus or use "
            "another explicitly available channel. Direct hearing-state questions may be answered from this "
            "authoritative envelope."
        )
    if effects.hands_mode in {"occupied", "unavailable", "constrained"}:
        constraints.append(
            f"Hands {effects.hands_mode}: no incompatible free-hand action."
        )
    if effects.locomotion_mode != "walking" or effects.locomotion_constraint != "normal":
        constraints.append(
            f"Movement must match {effects.locomotion_mode}/{effects.locomotion_constraint}; no incompatible walking."
        )
    block = (
        "[Authoritative capability envelope — backend policy]\n"
        "Typed modes remove channels but do not prescribe prose. Values are inert data.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ("\nConstraints: " + " ".join(constraints) if constraints else "")
        + ("\nConstrained/nonverbal: target 50–80 words; maximum 100."
           if constraints else "")
        + "\n[End authoritative capability envelope]"
    )
    # The closed set above remains comfortably below this defensive product
    # bound. Failing loudly in development is safer than silently dropping a
    # capability domain from the model's authoritative envelope.
    if len(block) > 2600:
        raise ValueError("authoritative capability envelope exceeded its closed bound")
    return block


def capability_requires_validation(effects: CapabilityEffects) -> bool:
    return (
        not effects.vision_available
        or any(effects.perception_mode(name) != "normal" for name in ("hearing", "smell", "taste", "touch"))
        or effects.speech_mode != "normal"
        or effects.hands_mode != "free"
        or effects.locomotion_mode != "walking"
        or effects.locomotion_constraint != "normal"
        or effects.awareness_mode != "normal"
        or effects.posture_mode is not None
    )


def preview_capability_effects(
    repository: MemoryV2Repository,
    character_id: str,
    user_message: object,
    *,
    recent_user_turns: tuple[object, ...] = (),
) -> CapabilityPreview:
    """Apply validated proposals to an in-memory view, never to production state."""
    current = repository.capability_effects(character_id)
    try:
        extraction = extract_current_continuity(
            repository, character_id, user_message,
            recent_user_turns=recent_user_turns,
        )
    except Exception:
        return CapabilityPreview(current, None, False)
    if not extraction.has_mutation:
        return CapabilityPreview(current, extraction, False)
    if extraction.scenario is not None:
        # A scope transition is authoritative current evidence even though the
        # target scope cannot be projected without mutating the store.
        return CapabilityPreview(current, extraction, True)

    # Capability sources have their own bounded lane. Descriptive relations
    # created later in a rich scene must never evict an older hard constraint.
    relations = list(repository.list_capability_source_relations(character_id))
    activity_state = repository.lookup_actor_state(character_id, "companion", "activity").state
    posture_state = repository.lookup_actor_state(character_id, "companion", "posture").state
    activity = activity_state.value if activity_state is not None else None
    posture = posture_state.value if posture_state is not None else None
    # Response lifecycle authority is broader than capability derivation.
    # A descriptive wear/remove/transfer mutation still needs post-mutation
    # prompt state and must-respect facts even when it does not alter a hard
    # capability source relation.
    changed = bool(
        extraction.scenario is not None
        or extraction.active_state is not None
        or extraction.correction is not None
        or extraction.scene_relations
    )
    if extraction.active_state is not None:
        for update in extraction.active_state.updates:
            if update.target_kind != "actor" or update.target_ref != "companion":
                continue
            if update.attribute == "activity":
                activity = str(update.value) if update.operation == "set" else None
                changed = True
            elif update.attribute == "posture":
                posture = str(update.value) if update.operation == "set" else None
                changed = True
    if extraction.correction is not None and (
        extraction.correction.target_kind == "actor"
        and extraction.correction.target_ref == "companion"
    ):
        if extraction.correction.attribute == "activity":
            activity = extraction.correction.corrected_value
            changed = True
        elif extraction.correction.attribute == "posture":
            posture = extraction.correction.corrected_value
            changed = True
    for index, proposal in enumerate(extraction.scene_relations):
        before = len(relations)
        if proposal.operation == "clear":
            relations = [
                item for item in relations
                if not _proposal_matches_record(proposal, item)
            ]
            changed = changed or len(relations) != before
            continue
        matching_index = next(
            (item_index for item_index, item in enumerate(relations)
             if _same_relation_identity(proposal, item)),
            None,
        )
        if matching_index is not None:
            current_relation = relations[matching_index]
            if (
                current_relation.semantic_family != proposal.semantic_family
                or (current_relation.quantity or 1) != (proposal.quantity or 1)
                or current_relation.effect_state != proposal.effect_state
            ):
                # Mirror the store's non-destructive metadata supersession in
                # the read-only same-turn preview. Generation must see "two
                # boxes" immediately, not the previous one-box hand envelope
                # that will be superseded after canonical persistence.
                relations[matching_index] = replace(
                    current_relation,
                    semantic_family=proposal.semantic_family,
                    quantity=proposal.quantity,
                    effect_state=proposal.effect_state,
                )
                changed = True
            continue
        relations.append(ActiveSceneRelationRecord(
            f"preview-{index}", character_id, proposal.target, proposal.facet,
            str(proposal.predicate), str(proposal.cause_kind), str(proposal.cause),
            0, None, repository.active_truth_scope(character_id).truth_scope_id,
            target_kind=proposal.target_kind, side=proposal.side,
            cause_subject_id=proposal.cause_subject_ref,
            semantic_family=proposal.semantic_family, quantity=proposal.quantity,
            effect_state=proposal.effect_state,
        ))
        changed = True
    effects = compose_capability_effects(
        tuple(relations),
        truth_scope_id=repository.active_truth_scope(character_id).truth_scope_id,
        companion_activity=activity,
        companion_posture=posture,
    )
    return CapabilityPreview(effects, extraction, changed)


def validate_capability_response(
    parsed: ParsedAssistantResponse,
    effects: CapabilityEffects,
) -> CapabilityResponseValidation:
    """Validate dialogue, speech, and presentation against one shared envelope."""
    if capability_requires_validation(effects) and parsed.contract_status not in {"valid", "plain_text"}:
        return CapabilityResponseValidation(False, "response_contract", "", None)
    envelope = response_capability_envelope(effects)
    parsed = normalize_response_for_capabilities(parsed, effects)
    dialogue = parsed.dialogue
    projected_speech = spoken_text(dialogue)
    projection_differs = (
        parsed.spoken_content is not None
        and _speech_projection_key(parsed.spoken_content) != _speech_projection_key(projected_speech)
    )
    # Ordinary streaming speaks the canonical dialogue projection as it
    # arrives. A stray normal-mode override can never retroactively replace it.
    # Governed non-streaming constrained modes may intentionally supply one
    # explicit short audio projection distinct from a scene caption.
    actual_spoken = (
        parsed.spoken_content
        if parsed.spoken_content is not None and (
            not projection_differs or parsed.response_mode != "normal_conversation"
        )
        else projected_speech
    )
    actual_spoken = sanitize_spoken_unicode(actual_spoken)
    if effects.vision_mode == "unavailable" and _prohibited_visual_claim(dialogue):
        return CapabilityResponseValidation(False, "prohibited_visual_claim", "", None)
    for sense in ("hearing", "smell", "taste", "touch"):
        if (effects.perception_mode(sense) == "unavailable"
                and _SENSORY_CLAIMS[sense].search(dialogue)):
            return CapabilityResponseValidation(False, f"prohibited_{sense}_claim", "", None)
    if (effects.hearing_mode == "unavailable"
            and _HEARING_COMPREHENSION_CLAIM.search(dialogue)):
        return CapabilityResponseValidation(False, "prohibited_hearing_comprehension", "", None)
    if effects.speech_mode == "unavailable":
        if _CLEAR_SPEECH_CAPABILITY_CLAIM.search(dialogue):
            return CapabilityResponseValidation(False, "speech_capability_contradiction", "", None)
        audible = _compact(actual_spoken)
        projected = _compact(projected_speech)
        if ((audible and not _nonlexical_vocalization(audible))
                or (projected and not _nonlexical_vocalization(projected))):
            return CapabilityResponseValidation(False, "speech_unavailable", "", None)
        if parsed.response_mode not in envelope.allowed_response_modes:
            return CapabilityResponseValidation(False, "speech_response_mode", "", None)
        # Mouth obstruction may still produce a captioned muffled sound, but
        # it is not clear TTS/lip-sync speech.
        actual_spoken = ""
    elif effects.speech_mode == "constrained":
        if (_TOTAL_SPEECH_INCAPACITY_CLAIM.search(dialogue)
                or _CLEAR_SPEECH_CAPABILITY_CLAIM.search(dialogue)):
            return CapabilityResponseValidation(False, "speech_capability_contradiction", "", None)
        if parsed.response_mode not in envelope.allowed_response_modes:
            return CapabilityResponseValidation(False, "speech_response_mode", "", None)
        # If the model omitted the optional projection, canonical dialogue is
        # already the authoritative and TTS-safe source.  Derive its short
        # muffled fragment rather than rejecting an otherwise valid minimal
        # envelope merely for missing transport metadata.
        # A TTS-only projection may add a short muffled sound to an action-only
        # canonical caption. It may not hide clear canonical speech by swapping
        # in different muffled audio after validation.
        constrained_projection = _constrained_dialogue_spoken_text(dialogue)
        if parsed.spoken_content is None:
            actual_spoken = constrained_projection
        # Validate both the canonical audible projection and an optional
        # shorter TTS projection as constrained speech. Requiring byte- or
        # token-identical projections made harmless local-model choices such
        # as omitting a repeated stutter from spoken_content fail even though
        # neither channel contained fluent speech. The canonical projection
        # remains independently authoritative, so a short muffled override
        # cannot hide clear dialogue.
        if (len(constrained_projection.split()) > 12
                or (_compact(constrained_projection)
                    and not _constrained_speech_shape(constrained_projection))):
            return CapabilityResponseValidation(False, "speech_constraint_fluency", "", None)
        if parsed.response_mode == "nonverbal_reaction" and _compact(actual_spoken):
            return CapabilityResponseValidation(False, "nonverbal_spoken_content", "", None)
        if len(actual_spoken.split()) > 12:
            return CapabilityResponseValidation(False, "speech_constraint_length", "", None)
        if _compact(actual_spoken) and not _constrained_speech_shape(actual_spoken):
            return CapabilityResponseValidation(False, "speech_constraint_fluency", "", None)
    hand_conflict = False
    if effects.hands_mode in {"occupied", "unavailable"}:
        hand_conflict = bool(_HAND_CONFLICT.search(dialogue))
    elif effects.hands_mode == "constrained":
        # A one-sided wrist or arm constraint leaves the other hand usable.
        # Only explicitly two-handed actions conflict with that envelope.
        hand_conflict = bool(_BOTH_HAND_CONFLICT.search(dialogue))
    if hand_conflict:
        return CapabilityResponseValidation(False, "hands_occupied_action", "", None)
    if effects.locomotion_mode != "walking" and _WALKING_CONFLICT.search(dialogue):
        return CapabilityResponseValidation(False, "locomotion_mode_conflict", "", None)
    if effects.awareness_mode == "asleep":
        if parsed.response_mode not in {"sleep_reaction", "nonverbal_reaction"}:
            return CapabilityResponseValidation(False, "asleep_response_mode", "", None)
        if "*" not in dialogue:
            return CapabilityResponseValidation(False, "asleep_dialogue_shape", "", None)
        if (_ASLEEP_AWAKE_BEHAVIOR.search(dialogue)
                or _ASLEEP_INFORMATIVE_SPEECH.search(actual_spoken)
                or "?" in actual_spoken):
            return CapabilityResponseValidation(False, "asleep_awake_behavior", "", None)
        if len(actual_spoken.split()) > 16:
            return CapabilityResponseValidation(False, "asleep_speech_length", "", None)

    presentation = parsed.presentation or ResponsePresentationMetadata()
    presentation = replace(
        presentation,
        gaze_mode=(
            "suppressed"
            if not effects.vision_available or effects.awareness_mode == "asleep"
            else (presentation.gaze_mode or "normal")
        ),
        gesture=(None if effects.awareness_mode == "asleep" else (
            None if effects.hands_mode in {"occupied", "unavailable", "constrained"}
            and presentation.gesture in {"greeting", "agreement", "thinking", "surprise"}
            else presentation.gesture
        )),
        pose=("sleeping" if effects.awareness_mode == "asleep" else presentation.pose),
        speech_mode=(
            "nonverbal" if effects.speech_mode == "unavailable"
            else ("mumble" if _compact(actual_spoken) else "nonverbal")
            if effects.awareness_mode == "asleep"
            else "constrained" if effects.speech_mode == "constrained"
            else presentation.speech_mode
        ),
        vision_mode=effects.vision_mode,
        hands_mode=effects.hands_mode,
        locomotion_mode=effects.locomotion_mode,
        posture_mode=effects.posture_mode,
        awareness_mode=effects.awareness_mode,
    )
    if not any((presentation.emotion, presentation.gesture, presentation.pose,
                presentation.gaze_mode, presentation.reaction, presentation.speech_mode,
                presentation.vision_mode, presentation.hands_mode,
                presentation.locomotion_mode, presentation.posture_mode,
                presentation.awareness_mode)):
        presentation = None
    return CapabilityResponseValidation(True, "accepted", actual_spoken, presentation)


def normalize_response_for_capabilities(
    parsed: ParsedAssistantResponse,
    effects: CapabilityEffects,
) -> ParsedAssistantResponse:
    """Fill only safe omitted response-mode defaults from the shared envelope."""
    if parsed.response_mode_supplied or parsed.contract_status not in {"valid", "plain_text"}:
        return parsed
    if effects.awareness_mode == "asleep":
        mode = "sleep_reaction"
    elif effects.speech_mode == "unavailable":
        mode = "nonverbal_reaction"
    elif effects.speech_mode == "constrained":
        projected = parsed.spoken_content
        if projected is None:
            projected = spoken_text(parsed.dialogue)
        mode = "speech_constrained" if _compact(projected) else "constrained_reaction"
    else:
        mode = "normal_conversation"
    return replace(parsed, response_mode=mode)


def _proposal_matches_record(
    proposal: SceneRelationProposal,
    record: ActiveSceneRelationRecord,
) -> bool:
    return (
        proposal.target_kind == record.target_kind
        and proposal.target == record.target
        and (proposal.facet is None or proposal.facet == record.facet)
        and (proposal.side is None or proposal.side == record.side)
        and (proposal.predicate is None or proposal.predicate == record.predicate)
        and (proposal.cause_kind is None or proposal.cause_kind == record.cause_kind)
        and (proposal.cause is None or proposal.cause.casefold() == record.cause.casefold())
        and (proposal.cause_subject_ref is None
             or proposal.cause_subject_ref == record.cause_subject_id)
    )


def _same_relation_identity(
    proposal: SceneRelationProposal,
    record: ActiveSceneRelationRecord,
) -> bool:
    return (
        proposal.target_kind, proposal.target, proposal.facet, proposal.side,
        proposal.predicate, proposal.cause_kind, str(proposal.cause).casefold(),
        proposal.cause_subject_ref,
    ) == (
        record.target_kind, record.target, record.facet, record.side,
        record.predicate, record.cause_kind, record.cause.casefold(),
        record.cause_subject_id,
    )


def _compact(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _speech_projection_key(value: object) -> str:
    text = _compact(value).casefold().replace("’", "'")
    return " ".join(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text))


def _constrained_dialogue_spoken_text(value: object) -> str:
    """Project constrained audio using the mode's explicit VN-span contract.

    In a governed constrained mode the prompt defines single-star spans as
    unspoken scene beats.  Applying ordinary emphasis heuristics here made
    creative one-word beats such as ``*Flinches*`` leak into the candidate
    speech projection and fail as fluent speech. Double-star emphasis remains
    audible, preserving ordinary explicit emphasis when a model uses it.
    """
    text = str(value or "")
    return _compact(re.sub(r"(?<!\*)\*([^*\r\n]+)\*(?!\*)", " ", text))


def _nonlexical_vocalization(value: object) -> bool:
    compact = _compact(value).strip('"“”')
    return bool(compact and _NONLEXICAL_VOCALIZATION.fullmatch(compact))


def _constrained_speech_shape(value: object) -> bool:
    """Admit short audibly broken/muffled speech without prescribing words."""
    compact = _compact(value)
    if not compact:
        return True
    if _nonlexical_vocalization(compact):
        return True
    return bool(
        re.search(r"(?:\.{2,}|…|—|--|\b[a-z]-[a-z]|\b(?:m+ph*|h?m+|n?ng+)\b)", compact, re.I)
        and len(compact.split()) <= 12
    )


def normalize_constrained_caption(
    parsed: ParsedAssistantResponse,
    effects: CapabilityEffects,
) -> ParsedAssistantResponse:
    """Move one explicit third-person Caption/Narration suffix into its action span."""
    parsed = normalize_response_for_capabilities(parsed, effects)
    if (effects.speech_mode == "normal"
            or parsed.response_mode not in {
                "nonverbal_reaction", "constrained_reaction", "speech_constrained",
            }):
        return parsed
    projected = _constrained_dialogue_spoken_text(parsed.dialogue)
    action_spans = re.findall(r"(?<!\*)\*([^*\r\n]{1,220})\*(?!\*)", parsed.dialogue)
    explicit_projection = _compact(parsed.spoken_content)
    if (action_spans and (
            (_compact(projected) and not _constrained_speech_shape(projected))
            or (explicit_projection and not _constrained_speech_shape(explicit_projection))
    )):
        # Preserve the model's creative physical reaction while removing a
        # fluent channel-incompatible tail. This is a deterministic semantic
        # projection repair, not canned replacement prose.
        fragment_match = re.search(
            r"\b(?:m{2,}|m+ph+|hm+|ng+|nnh+)[.!?…,'’~-]*",
            projected + " " + explicit_projection, re.I,
        )
        fragment = fragment_match.group(0) if fragment_match is not None else ""
        beats = " ".join(f"*{item.strip()}*" for item in action_spans if item.strip())
        return replace(
            parsed,
            dialogue=(beats + ((" " + fragment) if fragment else "")).strip(),
            response_mode="speech_constrained" if fragment else "constrained_reaction",
            response_mode_supplied=True,
            spoken_content=fragment,
        )
    match = re.fullmatch(
        r"(?P<beats>(?:\*[^*\r\n]{1,220}\*\s*){1,3})"
        r"\(?(?:caption|narration)\s*:\s*(?P<caption>[^*\r\n]{3,260}?)\)?\s*",
        str(parsed.dialogue or "").strip(), re.I,
    )
    if match is None:
        return parsed
    caption = match.group("caption").strip()
    if ("?" in caption or re.search(r"\b(?:i|me|my|mine)\b", caption, re.I)
            or re.search(r"[{}\[\]<>]", caption)):
        return parsed
    beats = " ".join(
        item.strip() for item in re.findall(r"\*([^*]+)\*", match.group("beats"))
    )
    return replace(parsed, dialogue=f"*{beats} {caption}*")


def _prohibited_visual_claim(dialogue: object) -> bool:
    """Reject positive or implied current visual observations.

    Negative capability language and darkness-only descriptions remain valid;
    a later positive clause cannot hide behind an earlier disclaimer.
    """
    text = _compact(dialogue)
    if not text:
        return False
    clauses = tuple(
        item.strip(" *") for item in re.split(r"(?<=[.!?])\s+|[;\n]+", text)
        if item.strip(" *")
    )
    for clause in clauses:
        # Rhetorical/embedded references to the concept of seeing are not a
        # present observation ("Can I see?", "what I see"). The following
        # declarative capability statement still has to satisfy requirements.
        candidate = re.sub(
            r"\b(?:what|whether)\s+i\s+(?:can\s+)?see\b", "", clause,
            flags=re.I,
        )
        if candidate.rstrip().endswith("?"):
            continue
        positive = _VISUAL_CLAIM.search(candidate) or _IMPLIED_VISUAL_PERCEPTION.search(candidate)
        if positive is None:
            continue
        limitation = _VISUAL_LIMITATION.search(clause)
        if limitation is None:
            return True
        # "I can't see clearly, but I can make out shapes" remains a positive
        # observation despite the leading limitation.
        tail = clause[limitation.end():]
        if re.search(r"\b(?:but|though|however|still|yet)\b", tail, re.I) and (
            _VISUAL_CLAIM.search(tail) or _IMPLIED_VISUAL_PERCEPTION.search(tail)
        ):
            return True
    return False
