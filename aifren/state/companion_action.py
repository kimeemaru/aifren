"""Closed model-initiated companion action proposal and validation boundary."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from aifren.memory_v2_store import ActiveStateProposal, ActiveStateProposalUpdate, MemoryV2Repository
from aifren.memory_v2_store.scene_relation_contract import CapabilityEffects, SceneRelationProposal
from aifren.dialogue.presentation_metadata import ParsedAssistantResponse


ACTION_FAMILIES = frozenset({"activity", "posture", "wear", "hold", "equipment"})
ACTION_OPERATIONS = frozenset({"set", "clear"})
POSTURES = frozenset({"standing", "sitting", "lying"})
_ACTIVITY = re.compile(r"[a-z][a-z'’-]*(?:ing)(?: [a-z][a-z'’ -]{0,31}){0,3}", re.I)
_AUTONOMY_TRIGGER = re.compile(
    r"\b(?:what\s+do\s+you\s+want\s+to\s+do|what\s+would\s+you\s+like\s+to\s+do|"
    r"do\s+whatever\s+you(?:'d|\s+would)?\s+like|you\s+(?:can|may)\s+(?:decide|choose)|"
    r"choose\s+what\s+you\s+do|do\s+something\s+you\s+want|"
    r"decide\s+whether\s+you\s+want\s+to\s+(?:sit\s*,?\s+or\s+stand|"
    r"sit\s*,?\s+or\s+lie\s+down))\b",
    re.I,
)
_HAND_ACTIVITY = re.compile(r"\b(?:waving|clapping|juggling|knitting|writing|drawing)\b", re.I)
_VISION_ACTIVITY = re.compile(r"\b(?:looking|watching|reading|observing)\b", re.I)
_WALK_ACTIVITY = re.compile(r"\b(?:walking|strolling|hiking|jogging|running)\b", re.I)
_SLEEP_ACTIVITY = re.compile(r"\b(?:sleeping|waking|awakening)\b", re.I)
_SPEECH_ACTIVITY = re.compile(
    r"\b(?:talking|speaking|chatting|singing|reading\s+aloud|reciting)\b", re.I,
)
_WORLD_MUTATING_ACTIVITY = re.compile(
    r"\b(?:buying|selling|ordering|calling|messaging|emailing|posting|deleting|"
    r"destroying|discarding|throwing\s+away|opening|closing|turning\s+(?:on|off)|"
    r"picking\s+up|putting\s+down|holding|carrying|wearing|removing|giving|taking)\b",
    re.I,
)
_LOCOMOTION_ACTIVITY_MODES = (
    (re.compile(r"\b(?:walking|strolling|hiking|jogging|running)\b", re.I), "walking"),
    (re.compile(r"\b(?:rolling)\b", re.I), "rolling"),
    (re.compile(r"\b(?:skating|skiing)\b", re.I), "skating"),
    (re.compile(r"\b(?:cycling|biking)\b", re.I), "cycling"),
    (re.compile(r"\b(?:driving)\b", re.I), "driving"),
    (re.compile(r"\b(?:riding)\b", re.I), "riding"),
    (re.compile(r"\b(?:swimming)\b", re.I), "swimming"),
)
_WORN_MOBILITY = {
    "rollerblade": "skating", "rollerblades": "skating", "skate": "skating",
    "skates": "skating", "ski": "skating", "skis": "skating",
}
_EQUIPMENT_MODES = {
    "crutches": ("using", "assisted"),
    "wheelchair": ("seated_in", "assisted"),
    "bicycle": ("riding", "cycling"), "bike": ("riding", "cycling"),
    "car": ("driving", "driving"), "horse": ("riding", "riding"),
}


@dataclass(frozen=True)
class CompanionActionProposal:
    family: str
    operation: str
    value: str | None = None


@dataclass(frozen=True)
class CompanionActionPlan:
    proposal: CompanionActionProposal
    active_state: ActiveStateProposal | None
    narration_terms: tuple[str, ...]
    relations: tuple[SceneRelationProposal, ...] = ()

    @property
    def semantic_evidence(self) -> str:
        value = self.proposal.value or "current value"
        return f"companion {self.proposal.family} {self.proposal.operation}: {value}"

    @property
    def context_block(self) -> str:
        return (
            "[Validated companion action — backend policy]\n"
            "The backend accepted this closed companion-only action. Narrate it naturally as completed, "
            "do not propose another action, and keep companion_action null in the response envelope.\n"
            + json.dumps({
                "family": self.proposal.family,
                "operation": self.proposal.operation,
                "value": self.proposal.value,
            }, separators=(",", ":"))
            + "\n[End validated companion action]"
        )


@dataclass(frozen=True)
class CompanionActionDecision:
    relevant: bool
    accepted: bool
    category: str
    plan: CompanionActionPlan | None = None


@dataclass(frozen=True)
class _ActionSubject:
    scene_subject_id: str
    label: str
    kind: str
    region: str | None
    quantity: int


def companion_action_relevant(user_message: object) -> bool:
    text = " ".join(str(user_message or "").strip().split())
    return bool(text and len(text) <= 240 and _AUTONOMY_TRIGGER.search(text))


def companion_action_decision_prompt(
    user_message: object,
    effects: CapabilityEffects,
    *,
    character_context: object = "",
    current_scene_subjects: tuple[str, ...] = (),
) -> str:
    """Use the unified envelope for an optional pre-response decision."""
    payload = {
        "canonical_user_request": str(user_message or "")[:240],
        "capabilities": effects.prompt_payload(),
        "allowed_actions": [
            {"family": "activity", "operation": "set", "value": "one present activity ending in -ing"},
            {"family": "activity", "operation": "clear", "value": None},
            {"family": "posture", "operation": "set", "value": "standing|sitting|lying"},
            {"family": "posture", "operation": "clear", "value": None},
            {"family": "wear", "operation": "set|clear", "value": "one current scene subject label"},
            {"family": "hold", "operation": "set|clear", "value": "one current scene subject label"},
            {"family": "equipment", "operation": "set|clear", "value": "one bounded current equipment label"},
        ],
        "current_scene_subjects": tuple(str(item)[:64] for item in current_scene_subjects[:12]),
    }
    return (
        "GOVERNED COMPANION ACTION DECISION\n"
        "Use the same AUTHORITATIVE RESPONSE FORMAT. Return exactly one JSON object with dialogue=\"\", "
        "response_mode=\"action_decision\", spoken_content=\"\", presentation=null, and "
        "capability_compliance=[]. Set companion_action to exactly one allowed closed action, or null if acting is not "
        "natural. This decision can affect only the companion. It cannot create entities, change user state, "
        "wake/sleep, activate scopes, create a subject, or bypass a capability. Wear/hold/equipment actions may reference only one listed current subject. Do not write ordinary dialogue here.\n"
        "Bounded character context (data):\n"
        + json.dumps(str(character_context or "")[:700], ensure_ascii=False) + "\n"
        "Decision data:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        "Shape reminder (values are examples, not a second contract):\n"
        '{"dialogue":"","response_mode":"action_decision","spoken_content":"",'
        '"presentation":null,"companion_action":{"family":"activity","operation":"set",'
        '"value":"stretching"},"capability_compliance":[]}'
    )


def validate_companion_action_decision(
    parsed: ParsedAssistantResponse,
    repository: MemoryV2Repository,
    character_id: str,
    effects: CapabilityEffects,
) -> CompanionActionDecision:
    """Validate proposal shape, target, capability, and singleton lifecycle."""
    if parsed.contract_status != "valid":
        return CompanionActionDecision(True, False, "action_contract")
    discarded_decision_prose = (
        parsed.response_mode != "action_decision"
        or bool(parsed.dialogue)
        or bool(parsed.spoken_content or "")
    )
    # This is a private pre-response call: none of its dialogue, speech, or
    # presentation can cross the user-visible boundary. Some otherwise capable
    # models populate those ordinary fields while still placing a well-formed
    # closed proposal in companion_action. Discarding that prose is safe; a
    # missing proposal under the wrong mode remains an abstention/rejection.
    if discarded_decision_prose and parsed.companion_action is None:
        return CompanionActionDecision(True, False, "action_decision_mode")
    raw = parsed.companion_action
    if raw is None:
        return CompanionActionDecision(True, False, "action_abstained")
    if set(raw) - {"family", "operation", "value"}:
        return CompanionActionDecision(True, False, "action_unknown_field")
    family = _name(raw.get("family"))
    operation = _name(raw.get("operation"))
    if family not in ACTION_FAMILIES or operation not in ACTION_OPERATIONS:
        return CompanionActionDecision(True, False, "action_family")
    value_raw = raw.get("value")
    value = " ".join(str(value_raw or "").split()).casefold() if value_raw is not None else None
    scene_family = family in {"wear", "hold", "equipment"}
    if scene_family:
        if value is None or not re.fullmatch(r"[a-z0-9][a-z0-9 '’\-]{0,63}", value):
            return CompanionActionDecision(True, False, "action_scene_subject_value")
    elif operation == "clear":
        if value not in {None, ""}:
            return CompanionActionDecision(True, False, "action_clear_value")
        value = None
    elif family == "posture":
        if value not in POSTURES:
            return CompanionActionDecision(True, False, "action_posture_value")
    else:
        if value is None or _ACTIVITY.fullmatch(value) is None:
            return CompanionActionDecision(True, False, "action_activity_value")
        if _SLEEP_ACTIVITY.search(value):
            return CompanionActionDecision(True, False, "action_awareness_transition")
        if _WORLD_MUTATING_ACTIVITY.search(value):
            return CompanionActionDecision(True, False, "action_world_mutation")

    if effects.awareness_mode == "asleep":
        return CompanionActionDecision(True, False, "action_asleep")
    if value and effects.hands_mode in {"occupied", "unavailable", "constrained"} and _HAND_ACTIVITY.search(value):
        return CompanionActionDecision(True, False, "action_hands_conflict")
    if value and not effects.vision_available and _VISION_ACTIVITY.search(value):
        return CompanionActionDecision(True, False, "action_vision_conflict")
    if value and effects.speech_mode != "normal" and _SPEECH_ACTIVITY.search(value):
        return CompanionActionDecision(True, False, "action_speech_conflict")
    if value and effects.locomotion_mode != "walking" and _WALK_ACTIVITY.search(value):
        return CompanionActionDecision(True, False, "action_locomotion_conflict")
    if value:
        proposed_mode = next(
            (mode for pattern, mode in _LOCOMOTION_ACTIVITY_MODES if pattern.search(value)),
            None,
        )
        if proposed_mode is not None and proposed_mode != effects.locomotion_mode:
            return CompanionActionDecision(True, False, "action_locomotion_conflict")
    if family == "posture" and operation == "set" and effects.locomotion_mode in {"cycling", "driving", "riding"}:
        return CompanionActionDecision(True, False, "action_posture_locomotion_conflict")

    proposal = CompanionActionProposal(family, operation, value)
    evidence = _semantic_evidence(family, operation, value)
    if scene_family:
        subjects = _action_subjects(repository, character_id)
        matches = _match_action_subjects(subjects, str(value))
        if len(matches) != 1:
            return CompanionActionDecision(True, False, "action_scene_subject_ambiguous")
        subject = matches[0]
        relations = repository.list_scene_relations(character_id, limit=96)
        family_predicates = {
            "wear": {"wearing", "worn_by"},
            "hold": {"holding", "carrying"},
            "equipment": {"using", "seated_in", "riding", "driving"},
        }[family]
        current_relations = tuple(
            relation for relation in relations
            if relation.target_kind == "actor" and relation.target == "companion"
            and relation.cause_subject_id == subject.scene_subject_id
            and relation.predicate in family_predicates
        )
        if operation == "clear":
            if len(current_relations) != 1:
                return CompanionActionDecision(True, False, "action_no_current_value")
            relation_proposals = (_clear_relation(current_relations[0], len(evidence)),)
        else:
            if current_relations:
                return CompanionActionDecision(True, False, "action_no_change")
            if any(
                relation.target == "user" and relation.cause_subject_id == subject.scene_subject_id
                and relation.predicate in {"wearing", "worn_by", "holding", "carrying", "using",
                                           "seated_in", "riding", "driving"}
                for relation in relations
            ):
                return CompanionActionDecision(True, False, "action_user_authority")
            if family == "hold" and effects.hands_mode in {"occupied", "unavailable", "constrained"}:
                return CompanionActionDecision(True, False, "action_hands_conflict")
            if family == "wear":
                if subject.region is None:
                    return CompanionActionDecision(True, False, "action_wear_region_unknown")
                if any(
                    relation.target == "companion" and relation.cause_subject_id == subject.scene_subject_id
                    and relation.predicate in {"holding", "carrying"}
                    for relation in relations
                ):
                    return CompanionActionDecision(True, False, "action_subject_in_hand")
                predicate, semantic, facet = "wearing", _WORN_MOBILITY.get(subject.kind), subject.region
            elif family == "hold":
                predicate, semantic, facet = "holding", "hand_occupancy", "hands"
            else:
                equipment = _EQUIPMENT_MODES.get(subject.kind)
                if equipment is None:
                    return CompanionActionDecision(True, False, "action_equipment_family")
                predicate, semantic = equipment
                facet = None
            relation_proposals = (SceneRelationProposal(
                "set", "companion", facet, predicate, "scene", subject.label, subject.kind,
                0, len(evidence), cause_subject_ref=subject.scene_subject_id,
                semantic_family=semantic, quantity=subject.quantity,
            ),)
        narration_terms = _narration_terms(proposal, None)
        plan = CompanionActionPlan(proposal, None, narration_terms, relation_proposals)
    else:
        attribute = family
        current = repository.lookup_actor_state(character_id, "companion", attribute).state
        if operation == "clear" and current is None:
            return CompanionActionDecision(True, False, "action_no_current_value")
        if operation == "set" and current is not None and current.value.casefold() == value:
            return CompanionActionDecision(True, False, "action_no_change")
        update = ActiveStateProposalUpdate(
            operation=operation,
            value=value,
            excerpt_start_cp=0 if operation == "set" else None,
            excerpt_end_cp=len(evidence) if operation == "set" else None,
            target_kind="actor", target_ref="companion", attribute=attribute,
        )
        narration_terms = _narration_terms(proposal, current.value if current is not None else None)
        plan = CompanionActionPlan(proposal, ActiveStateProposal((update,)), narration_terms)
    return CompanionActionDecision(
        True, True, "accepted_discarded_prose" if discarded_decision_prose else "accepted",
        plan,
    )


def action_narration_valid(plan: CompanionActionPlan | None, parsed: ParsedAssistantResponse) -> bool:
    if parsed.companion_action is not None:
        # The decision was a separate validated step; the response cannot
        # smuggle in a second single-pass mutation.
        return False
    if plan is None:
        return True
    text = " ".join(parsed.dialogue.casefold().replace("’", "'").split())
    if plan.proposal.family in {"wear", "hold", "equipment"}:
        target = str(plan.proposal.value or "").casefold()
        target_present = bool(target and _asserted_term(text, target)) or bool(
            re.search(r"\b(?:it|them)\b", text)
        )
        verbs = {
            ("wear", "set"): ("put on", "puts on", "puts it on", "puts them on", "wears"),
            ("wear", "clear"): ("take off", "takes off", "takes it off", "takes them off", "removes"),
            ("hold", "set"): ("pick up", "picks up", "picks it up", "picks them up", "holds"),
            ("hold", "clear"): ("put down", "puts down", "puts it down", "puts them down", "sets down", "releases"),
            ("equipment", "set"): ("uses", "mounts", "gets into", "gets into it"),
            ("equipment", "clear"): ("dismounts", "gets out", "gets out of it", "stops using"),
        }[(plan.proposal.family, plan.proposal.operation)]
        return target_present and any(_asserted_term(text, term) for term in verbs)
    if (plan.proposal.family == "activity" and plan.proposal.operation == "set"
            and plan.proposal.value):
        # The closed action value is normalized as a present activity ending
        # in -ing, but natural narration commonly inflects the same verb:
        # "stretching" -> "stretches" / "starts to stretch". Match a
        # conservative verb stem while retaining the existing negation guard;
        # this is narration validation, never state extraction.
        gerund = plan.proposal.value.split()[0]
        stem = gerund[:-3] if gerund.endswith("ing") else gerund
        if (len(stem) >= 3 and stem[-1] == stem[-2]
                and stem[-1] not in {"s"}):
            stem = stem[:-1]
        return bool(len(stem) >= 3 and _asserted_activity_stem(text, stem))
    return any(_asserted_term(text, term) for term in plan.narration_terms)


def unauthorized_action_narrated(parsed: ParsedAssistantResponse) -> bool:
    """Detect a stateful companion action claim when no plan was authorized.

    This is deliberately narrower than general emote parsing. Small reactive
    movements remain valid, while the lifecycle verbs used to claim a new
    activity or posture are rejected whether the model writes first-person
    prose, subject-prefixed prose, or a visual-novel action span.
    """
    if parsed.companion_action is not None:
        return True
    text = " ".join(parsed.dialogue.casefold().replace("’", "'").split())
    if not text:
        return False
    action_spans = " ".join(re.findall(r"\*([^*]+)\*", text))
    lifecycle_terms = (
        "begin", "begins", "start", "starts", "stop", "stops",
        "sit down", "sits down", "stand up", "stands up",
        "lie down", "lies down", "take a seat", "takes a seat",
        "get up", "gets up", "put on", "puts on", "take off", "takes off",
        "pick up", "picks up", "put down", "puts down", "release", "releases",
        "mount", "mounts", "dismount", "dismounts", "get into", "gets into",
        "get out", "gets out", "start using", "starts using", "stop using", "stops using",
    )
    if action_spans and any(_asserted_term(action_spans, term) for term in lifecycle_terms):
        return True
    subject_claim = re.compile(
        r"\b(?:i|she|he|they|the\s+companion)\s+(?:quietly\s+|slowly\s+|suddenly\s+)?"
        r"(?:begins?|starts?|stops?|sits?\s+down|stands?\s+up|lies?\s+down|"
        r"takes?\s+a\s+seat|gets?\s+up|puts?\s+on|takes?\s+off|picks?\s+up|"
        r"puts?\s+down|releases?|mounts?|dismounts?|gets?\s+into|gets?\s+out|"
        r"starts?\s+using|stops?\s+using)\b",
        re.I,
    )
    return any(
        _asserted_term(text, match.group(0))
        for match in subject_claim.finditer(text)
    )


def _action_subjects(
    repository: MemoryV2Repository, character_id: str,
) -> tuple[_ActionSubject, ...]:
    rows: list[_ActionSubject] = []
    for subject in repository.list_scene_subjects(character_id, limit=128):
        attributes = {
            record.subject_key.rsplit(".", 1)[-1]: record.value
            for record in repository.lookup_scene_attributes(character_id, subject.scene_subject_id)
        }
        kind = str(attributes.get("kind") or "").casefold()
        if not kind:
            continue
        color = str(attributes.get("color") or "").casefold()
        label = " ".join(part for part in (color, kind) if part)
        try:
            quantity = max(1, min(16, int(attributes.get("quantity") or 1)))
        except (TypeError, ValueError):
            quantity = 1
        rows.append(_ActionSubject(
            subject.scene_subject_id, label, kind,
            str(attributes.get("region")) if attributes.get("region") else None,
            quantity,
        ))
    return tuple(rows)


def current_companion_action_subject_labels(
    repository: MemoryV2Repository, character_id: str,
) -> tuple[str, ...]:
    """Bounded prompt-safe labels; opaque subject IDs never leave validation."""
    return tuple(subject.label for subject in _action_subjects(repository, character_id)[:12])


def _match_action_subjects(
    subjects: tuple[_ActionSubject, ...], value: str,
) -> tuple[_ActionSubject, ...]:
    query = set(re.findall(r"[a-z0-9]+", value.casefold())) - {"a", "an", "the", "your"}
    exact = tuple(subject for subject in subjects if subject.label == value.casefold())
    if exact:
        return exact
    return tuple(
        subject for subject in subjects
        if query and query <= set(re.findall(r"[a-z0-9]+", subject.label))
    )


def _clear_relation(record: object, evidence_length: int) -> SceneRelationProposal:
    return SceneRelationProposal(
        "clear", str(getattr(record, "target")), getattr(record, "facet", None),
        str(getattr(record, "predicate")), str(getattr(record, "cause_kind")),
        str(getattr(record, "cause")), excerpt_start_cp=0, excerpt_end_cp=evidence_length,
        target_kind=str(getattr(record, "target_kind", "actor")),
        side=getattr(record, "side", None),
        cause_subject_ref=getattr(record, "cause_subject_id", None),
    )


def action_fallback_dialogue(plan: CompanionActionPlan) -> str:
    proposal = plan.proposal
    if proposal.operation == "clear":
        if proposal.family == "wear":
            return f"*Takes off the {proposal.value}.*"
        if proposal.family == "hold":
            return f"*Sets the {proposal.value} down.*"
        if proposal.family == "equipment":
            return f"*Stops using the {proposal.value}.*"
        return (
            "*Lets the activity come to a natural stop.*"
            if proposal.family == "activity" else
            "*Shifts out of the previous posture.*"
        )
    assert proposal.value is not None
    if proposal.family == "posture":
        verb = {"sitting": "Sits down", "standing": "Stands up", "lying": "Lies down"}[proposal.value]
        return f"*{verb} comfortably.*"
    if proposal.family == "wear":
        return f"*Puts on the {proposal.value}.*"
    if proposal.family == "hold":
        return f"*Picks up the {proposal.value}.*"
    if proposal.family == "equipment":
        return f"*Begins using the {proposal.value}.*"
    return f"*Begins {proposal.value}.*"


def _semantic_evidence(family: str, operation: str, value: str | None) -> str:
    return f"companion {family} {operation}: {value or 'current value'}"


def semantic_action_evidence(plan: CompanionActionPlan) -> str:
    return _semantic_evidence(plan.proposal.family, plan.proposal.operation, plan.proposal.value)


def _narration_terms(proposal: CompanionActionProposal, previous: str | None) -> tuple[str, ...]:
    if proposal.family == "wear":
        return (("takes off", "removes") if proposal.operation == "clear"
                else (str(proposal.value), "puts on", "wears"))
    if proposal.family == "hold":
        return (("puts down", "sets down", "releases") if proposal.operation == "clear"
                else (str(proposal.value), "picks up", "holds"))
    if proposal.family == "equipment":
        return (("dismounts", "gets out", "stops using") if proposal.operation == "clear"
                else (str(proposal.value), "uses", "mounts", "gets into"))
    if proposal.operation == "set" and proposal.value:
        terms = [proposal.value]
        if proposal.family == "posture":
            terms.extend({"standing": ("stands", "stand up"), "sitting": ("sits", "sit down"),
                          "lying": ("lies down", "lie down")}[proposal.value])
        return tuple(terms)
    return ("stop", "stops", "settles", "lets the activity", "shifts out")


def _asserted_term(text: str, term: str) -> bool:
    """Reject a matching action word when it is explicitly negated."""
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return False
        prefix = text[max(0, index - 56):index]
        if re.search(
            r"\b(?:not|never|without|won't|wouldn't|can't|cannot|don't|do\s+not|"
            r"doesn't|does\s+not|"
            r"didn't|did\s+not|refuses?\s+to|declines?\s+to)\b(?:\W+\w+){0,3}\W*$",
            prefix,
            re.I,
        ) is None:
            return True
        start = index + max(1, len(term))


def _asserted_activity_stem(text: str, stem: str) -> bool:
    for match in re.finditer(rf"\b{re.escape(stem)}[a-z]*\b", text, re.I):
        if _asserted_term(text, match.group(0)):
            return True
    return False


def _name(value: Any) -> str:
    return str(value or "").strip().casefold()
