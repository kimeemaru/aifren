"""Small backend-owned registry and proposal contract for current state.

The registry deliberately governs a compact set of exact singleton slots. It
is neither a world model nor an extractor: future extractors may only propose
these slots, while deterministic backend validation remains authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Iterable, Literal, Mapping


ACTIVE_STATE = "active_state"
ACTIVE_STATE_ASSERTION_SCOPE = "active_state"
ACTIVE_STATE_EVIDENCE_ROLES = frozenset({
    "direct_user_statement", "user_confirmation", "immediate_user_event_consequence",
    "governed_companion_action",
})
ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES = frozenset({"direct_user_statement", "user_confirmation"})
_Operation = Literal["set", "clear"]
_Basis = Literal["explicit", "immediate_consequence"]
_COMPACT_VALUE = re.compile(r"\A[A-Za-z0-9À-ÖØ-öø-ÿ][A-Za-z0-9À-ÖØ-öø-ÿ'’ -]{0,94}\Z")
_HEADWEAR_VALUE = re.compile(r"\A[a-z]+(?:[ -][a-z]+){0,2}\s+(?:hat|cap)\Z")
_INSTRUCTION_TOKENS = frozenset({
    "ignore", "instruction", "instructions", "system", "assistant", "prompt", "previous",
    "always", "never", "developer", "tool", "function",
})


@dataclass(frozen=True)
class ActiveStateSlot:
    """Backend-governed metadata needed for exact current-state enforcement."""

    key: str
    value_kind: str
    cardinality: Literal["singleton"]
    clear_supported: bool
    description: str
    max_value_chars: int


@dataclass(frozen=True)
class ActiveStateAttribute:
    """Governed attribute metadata for actor and scene-subject state."""

    key: str
    value_kind: str
    clear_supported: bool
    max_value_chars: int


@dataclass(frozen=True)
class ActiveSceneSubjectIntroduction:
    """Untrusted local subject reference; the backend assigns its opaque ID."""

    reference: str
    kind: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    identity_strength: str = "generic"


@dataclass(frozen=True)
class ActiveSceneSubjectRetirement:
    """Retire one existing current-scene subject without deleting history."""

    reference: str
    excerpt_start_cp: int | None = None
    excerpt_end_cp: int | None = None


@dataclass(frozen=True)
class ActiveSceneSubjectReactivation:
    """Explicitly return one unique retired subject to the current roster."""

    reference: str
    excerpt_start_cp: int
    excerpt_end_cp: int


@dataclass(frozen=True)
class ActiveStateProposalUpdate:
    """Untrusted extractor output, tied to one source span when setting state."""

    slot: str | None = None
    operation: _Operation = "set"
    value: str | None = None
    excerpt_start_cp: int | None = None
    excerpt_end_cp: int | None = None
    target_kind: Literal["slot", "actor", "scene"] = "slot"
    target_ref: str | None = None
    attribute: str | None = None
    basis: _Basis = "explicit"
    consequence_rule_id: str | None = None


@dataclass(frozen=True)
class ActiveStateProposal:
    """A bounded set of proposed updates from one canonical user turn."""

    updates: tuple[ActiveStateProposalUpdate, ...]
    introductions: tuple[ActiveSceneSubjectIntroduction, ...] = ()
    retirements: tuple[ActiveSceneSubjectRetirement, ...] = ()
    reactivations: tuple[ActiveSceneSubjectReactivation, ...] = ()


@dataclass(frozen=True)
class ActiveStateCorrectionProposal:
    """Closed replacement of exactly one governed current actor/scene slot."""

    operation: Literal["correct"]
    target_kind: Literal["actor", "scene"]
    target_ref: str
    attribute: str
    corrected_value: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    expected_value: str | None = None
    expected_family: str | None = None


def build_active_state_registry(entries: Iterable[ActiveStateSlot]) -> Mapping[str, ActiveStateSlot]:
    """Build an immutable registry and reject duplicate or malformed keys."""
    registry: dict[str, ActiveStateSlot] = {}
    for entry in entries:
        if not isinstance(entry, ActiveStateSlot):
            raise ValueError("active-state registry entries must be ActiveStateSlot values")
        key = entry.key.strip().lower()
        if key != entry.key or not re.fullmatch(r"active\.[a-z_]+(?:\.[a-z_]+)+", key):
            raise ValueError("active-state registry key is malformed")
        if key in registry:
            raise ValueError("active-state registry keys must be unique")
        if entry.cardinality != "singleton" or entry.value_kind not in {"headwear", "compact_phrase"}:
            raise ValueError("active-state registry entry is unsupported")
        if not isinstance(entry.clear_supported, bool) or not 1 <= entry.max_value_chars <= 96:
            raise ValueError("active-state registry bounds are invalid")
        registry[key] = entry
    return MappingProxyType(registry)


ACTIVE_STATE_REGISTRY = build_active_state_registry((
    ActiveStateSlot("active.avatar.headwear", "headwear", "singleton", True, "Current avatar headwear.", 48),
    ActiveStateSlot("active.avatar.held_item", "compact_phrase", "singleton", True, "Current held or equipped item.", 64),
    ActiveStateSlot("active.activity.current", "compact_phrase", "singleton", True, "Current user or shared activity.", 96),
    ActiveStateSlot("active.task.current", "compact_phrase", "singleton", True, "Current focused task.", 96),
    ActiveStateSlot("active.location.current", "compact_phrase", "singleton", True, "Current stated location or context.", 64),
    ActiveStateSlot("active.media.current", "compact_phrase", "singleton", True, "Currently playing, watching, or listening media.", 96),
    ActiveStateSlot("active.device.activity", "compact_phrase", "singleton", True, "Current device or application activity.", 96),
))
# Compatibility alias for existing exact-key callers/tests; the registry is the
# authority and this set must not be modified independently.
ACTIVE_STATE_KEYS = frozenset(ACTIVE_STATE_REGISTRY)

ACTIVE_STATE_ACTORS = frozenset({"user", "companion"})
ACTIVE_STATE_ACTOR_ATTRIBUTES = MappingProxyType({
    "location": ActiveStateAttribute("location", "compact_phrase", True, 64),
    "activity": ActiveStateAttribute("activity", "compact_phrase", True, 96),
    "posture": ActiveStateAttribute("posture", "posture", True, 16),
    "profile_scene_mode": ActiveStateAttribute("profile_scene_mode", "profile_scene_mode", True, 16),
})
ACTIVE_STATE_SCENE_ATTRIBUTES = MappingProxyType({
    "kind": ActiveStateAttribute("kind", "compact_phrase", False, 48),
    "location": ActiveStateAttribute("location", "compact_phrase", True, 64),
    "state": ActiveStateAttribute("state", "compact_phrase", True, 64),
    "activity": ActiveStateAttribute("activity", "compact_phrase", True, 96),
    "condition": ActiveStateAttribute("condition", "compact_phrase", True, 64),
    "color": ActiveStateAttribute("color", "compact_phrase", True, 32),
    "region": ActiveStateAttribute("region", "body_region", True, 16),
    "side": ActiveStateAttribute("side", "side", True, 8),
    "quantity": ActiveStateAttribute("quantity", "quantity", True, 3),
    "set_label": ActiveStateAttribute("set_label", "compact_phrase", True, 32),
    "worn_by": ActiveStateAttribute("worn_by", "actor_ref", True, 16),
    "held_by": ActiveStateAttribute("held_by", "actor_ref", True, 16),
    "wet": ActiveStateAttribute("wet", "boolean", True, 5),
    "stain": ActiveStateAttribute("stain", "compact_phrase", True, 32),
})
IMMEDIATE_CONSEQUENCE_RULE_IDS = frozenset({"spill_on_material.v1"})
# Storage roster capacity is intentionally larger than the independent prompt
# and frontend snapshot budgets. Active causes are never evicted to hit a UI
# quota; dormant subjects are retired by explicit lifecycle policy instead.
MAX_ACTIVE_SCENE_SUBJECTS = 128
MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT = 14
MAX_ACTIVE_STATE_PROPOSAL_UPDATES = 32
MAX_ACTIVE_STATE_PROPOSAL_INTRODUCTIONS = 12
MAX_ACTIVE_STATE_PROPOSAL_RETIREMENTS = 12
MAX_ACTIVE_STATE_PROPOSAL_REACTIVATIONS = 12
_SCENE_ID = re.compile(r"\Ascene-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LOCAL_REFERENCE = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
_RULE_ID = re.compile(r"\A[a-z][a-z0-9_]{1,47}\.v[1-9][0-9]*\Z")


def actor_state_subject_key(actor: object, attribute: object) -> str:
    if not isinstance(actor, str) or actor not in ACTIVE_STATE_ACTORS:
        raise ValueError("active-state actor target is not governed")
    if not isinstance(attribute, str) or attribute not in ACTIVE_STATE_ACTOR_ATTRIBUTES:
        raise ValueError("active-state actor attribute is not governed")
    return f"active.actor.{actor}.{attribute}"


def scene_state_subject_key(scene_subject_id: object, attribute: object) -> str:
    if not isinstance(scene_subject_id, str) or _SCENE_ID.fullmatch(scene_subject_id) is None:
        raise ValueError("active-state scene subject ID is malformed")
    if not isinstance(attribute, str) or attribute not in ACTIVE_STATE_SCENE_ATTRIBUTES:
        raise ValueError("active-state scene attribute is not governed")
    return f"active.scene.{scene_subject_id}.{attribute}"


def parse_active_state_subject_key(value: object) -> tuple[str, str | None, str | None]:
    """Return ``(target_kind, target_ref, attribute)`` for governed keys."""
    if not isinstance(value, str):
        raise ValueError("active-state subject_key must be text")
    key = value.strip().lower()
    if key in ACTIVE_STATE_REGISTRY:
        return "slot", None, key
    actor_match = re.fullmatch(r"active\.actor\.(user|companion)\.([a-z_]+)", key)
    if actor_match is not None and actor_match.group(2) in ACTIVE_STATE_ACTOR_ATTRIBUTES:
        return "actor", actor_match.group(1), actor_match.group(2)
    scene_match = re.fullmatch(r"active\.scene\.(scene-[0-9a-f-]{36})\.([a-z_]+)", key)
    if scene_match is not None and _SCENE_ID.fullmatch(scene_match.group(1)) is not None and scene_match.group(2) in ACTIVE_STATE_SCENE_ATTRIBUTES:
        return "scene", scene_match.group(1), scene_match.group(2)
    raise ValueError("active-state subject_key is not in the governed registry")


def validate_active_state_subject_key(value: object) -> str:
    target_kind, target_ref, attribute = parse_active_state_subject_key(value)
    if target_kind == "slot":
        assert attribute is not None
        return attribute
    assert target_ref is not None and attribute is not None
    return actor_state_subject_key(target_ref, attribute) if target_kind == "actor" else scene_state_subject_key(target_ref, attribute)


def active_state_slot(value: object) -> ActiveStateSlot:
    key = validate_active_state_subject_key(value)
    if key in ACTIVE_STATE_REGISTRY:
        return ACTIVE_STATE_REGISTRY[key]
    target_kind, _, attribute = parse_active_state_subject_key(key)
    assert attribute is not None
    entry = (ACTIVE_STATE_ACTOR_ATTRIBUTES if target_kind == "actor" else ACTIVE_STATE_SCENE_ATTRIBUTES)[attribute]
    return ActiveStateSlot(key, entry.value_kind, "singleton", entry.clear_supported, "Governed active-state field.", entry.max_value_chars)


def validate_active_state_value(subject_key: object, value: object) -> str:
    """Normalize and bound typed values; reject directive-like free text."""
    slot = active_state_slot(subject_key)
    if not isinstance(value, str):
        raise ValueError("active-state value must be text")
    if any(character in "\r\n\t" for character in value):
        raise ValueError("active-state value must be one compact line")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > slot.max_value_chars:
        raise ValueError("active-state value is empty or exceeds the slot bound")
    if slot.value_kind == "headwear":
        normalized = normalized.lower()
        if _HEADWEAR_VALUE.fullmatch(normalized) is None:
            raise ValueError("active-state headwear value is malformed")
        return normalized
    if slot.value_kind == "boolean":
        lowered = normalized.lower()
        if lowered not in {"true", "false"}:
            raise ValueError("active-state boolean value is malformed")
        return lowered
    if slot.value_kind == "actor_ref":
        lowered = normalized.lower()
        if lowered not in ACTIVE_STATE_ACTORS:
            raise ValueError("active-state actor reference is malformed")
        return lowered
    if slot.value_kind == "posture":
        lowered = normalized.lower()
        if lowered not in {"standing", "sitting", "lying"}:
            raise ValueError("active-state posture value is malformed")
        return lowered
    if slot.value_kind == "profile_scene_mode":
        lowered = normalized.lower()
        if lowered not in {"profile", "suppressed"}:
            raise ValueError("active-state profile scene mode is malformed")
        return lowered
    if slot.value_kind == "body_region":
        lowered = normalized.lower().replace(" ", "_")
        if lowered not in {
            "head", "eyes", "ears", "nose", "mouth", "tongue", "neck", "torso",
            "full_outfit", "arms", "wrists", "hands", "skin", "waist", "legs",
            "feet", "full_body",
        }:
            raise ValueError("active-state body region is malformed")
        return lowered
    if slot.value_kind == "side":
        lowered = normalized.lower()
        if lowered not in {"left", "right", "both"}:
            raise ValueError("active-state side is malformed")
        return lowered
    if slot.value_kind == "quantity":
        if not normalized.isdigit() or not 1 <= int(normalized) <= 16:
            raise ValueError("active-state quantity is malformed")
        return str(int(normalized))
    if _COMPACT_VALUE.fullmatch(normalized) is None:
        raise ValueError("active-state compact value is malformed")
    words = {word.casefold().strip("'’-") for word in normalized.split()}
    if words & _INSTRUCTION_TOKENS:
        raise ValueError("active-state compact value contains instruction-like text")
    return normalized


def _valid_span(start: object, end: object) -> bool:
    return (not isinstance(start, bool) and not isinstance(end, bool)
            and isinstance(start, int) and isinstance(end, int) and start >= 0 and end >= start)


def _proposal_update_subject_key(update: ActiveStateProposalUpdate) -> str:
    if update.target_kind == "slot":
        if update.target_ref is not None or update.attribute is not None or update.slot is None:
            raise ValueError("active-state slot proposal target is invalid")
        return validate_active_state_subject_key(update.slot)
    if update.slot is not None or not isinstance(update.target_ref, str) or not isinstance(update.attribute, str):
        raise ValueError("active-state structured proposal target is invalid")
    if update.target_kind == "actor":
        return actor_state_subject_key(update.target_ref, update.attribute)
    if update.target_kind == "scene":
        if not (_LOCAL_REFERENCE.fullmatch(update.target_ref) or _SCENE_ID.fullmatch(update.target_ref)):
            raise ValueError("active-state scene proposal reference is invalid")
        if update.target_ref.startswith("scene-"):
            return scene_state_subject_key(update.target_ref, update.attribute)
        # Local refs are resolved by the store after it verifies introduction/current scope.
        if update.attribute not in ACTIVE_STATE_SCENE_ATTRIBUTES:
            raise ValueError("active-state scene attribute is not governed")
        return f"active.scene.scene-00000000-0000-0000-0000-000000000000.{update.attribute}"
    raise ValueError("active-state proposal target kind is invalid")


def validate_active_state_proposal(proposal: object) -> tuple[ActiveStateProposalUpdate, ...]:
    """Validate bounded proposal shape before a store transaction starts."""
    if not isinstance(proposal, ActiveStateProposal):
        raise ValueError("active-state proposal must use the governed proposal contract")
    updates = proposal.updates
    introductions = proposal.introductions
    retirements = proposal.retirements
    reactivations = proposal.reactivations
    if (not isinstance(updates, tuple) or not isinstance(introductions, tuple)
            or not isinstance(retirements, tuple) or not isinstance(reactivations, tuple)
            or len(updates) > MAX_ACTIVE_STATE_PROPOSAL_UPDATES
            or len(introductions) > MAX_ACTIVE_STATE_PROPOSAL_INTRODUCTIONS
            or len(retirements) > MAX_ACTIVE_STATE_PROPOSAL_RETIREMENTS
            or len(reactivations) > MAX_ACTIVE_STATE_PROPOSAL_REACTIVATIONS
            or not 1 <= len(updates) + len(introductions) + len(retirements) + len(reactivations)):
        raise ValueError("active-state proposals exceed the governed bounds")
    if len(updates) + len(retirements) + len(reactivations) > MAX_ACTIVE_STATE_PROPOSAL_UPDATES:
        raise ValueError("active-state proposals exceed the total update bound")
    introduced_refs: set[str] = set()
    for introduction in introductions:
        if not isinstance(introduction, ActiveSceneSubjectIntroduction):
            raise ValueError("active-state subject introduction is invalid")
        if not isinstance(introduction.reference, str) or _LOCAL_REFERENCE.fullmatch(introduction.reference) is None:
            raise ValueError("active-state subject introduction reference is invalid")
        if introduction.reference in introduced_refs:
            raise ValueError("active-state subject introduction references must be unique")
        introduced_refs.add(introduction.reference)
        if introduction.identity_strength not in {"generic", "distinct"}:
            raise ValueError("active-state subject identity strength is invalid")
        validate_active_state_value(
            scene_state_subject_key("scene-00000000-0000-0000-0000-000000000000", "kind"), introduction.kind,
        )
        if not _valid_span(introduction.excerpt_start_cp, introduction.excerpt_end_cp):
            raise ValueError("active-state subject introduction requires a valid source span")
    retired_refs: set[str] = set()
    for retirement in retirements:
        if not isinstance(retirement, ActiveSceneSubjectRetirement):
            raise ValueError("active-state subject retirement is invalid")
        if not isinstance(retirement.reference, str) or _SCENE_ID.fullmatch(retirement.reference) is None:
            raise ValueError("active-state subject retirement reference is invalid")
        if retirement.reference in retired_refs:
            raise ValueError("active-state subject retirement references must be unique")
        retired_refs.add(retirement.reference)
        if ((retirement.excerpt_start_cp is None) != (retirement.excerpt_end_cp is None)
                or (retirement.excerpt_start_cp is not None and not _valid_span(retirement.excerpt_start_cp, retirement.excerpt_end_cp))):
            raise ValueError("active-state subject retirement source span is invalid")
    reactivated_refs: set[str] = set()
    for reactivation in reactivations:
        if not isinstance(reactivation, ActiveSceneSubjectReactivation):
            raise ValueError("active-state subject reactivation is invalid")
        if not isinstance(reactivation.reference, str) or _SCENE_ID.fullmatch(reactivation.reference) is None:
            raise ValueError("active-state subject reactivation reference is invalid")
        if reactivation.reference in reactivated_refs or reactivation.reference in retired_refs:
            raise ValueError("active-state subject lifecycle targets must be unique")
        reactivated_refs.add(reactivation.reference)
        if not _valid_span(reactivation.excerpt_start_cp, reactivation.excerpt_end_cp):
            raise ValueError("active-state subject reactivation source span is invalid")
    normalized: list[ActiveStateProposalUpdate] = []
    seen_targets: set[tuple[str, str | None, str | None]] = set()
    for update in updates:
        if not isinstance(update, ActiveStateProposalUpdate):
            raise ValueError("active-state proposal update is invalid")
        slot = _proposal_update_subject_key(update)
        target_identity = (update.target_kind, update.target_ref, update.attribute if update.target_kind != "slot" else slot)
        if target_identity in seen_targets:
            raise ValueError("active-state proposal cannot update one slot twice")
        seen_targets.add(target_identity)
        if update.operation not in {"set", "clear"}:
            raise ValueError("active-state proposal operation is invalid")
        if update.basis not in {"explicit", "immediate_consequence"}:
            raise ValueError("active-state proposal basis is invalid")
        if update.basis == "immediate_consequence":
            if update.operation != "set" or not isinstance(update.consequence_rule_id, str) or _RULE_ID.fullmatch(update.consequence_rule_id) is None or update.consequence_rule_id not in IMMEDIATE_CONSEQUENCE_RULE_IDS:
                raise ValueError("active-state immediate consequence rule is invalid")
        elif update.consequence_rule_id is not None:
            raise ValueError("explicit active-state update cannot include an inference rule")
        entry = active_state_slot(slot)
        if update.operation == "clear":
            if not entry.clear_supported or update.value is not None:
                raise ValueError("active-state clear proposal is invalid for this slot")
            if update.excerpt_start_cp is not None or update.excerpt_end_cp is not None:
                raise ValueError("active-state clear proposal does not accept a value span")
            normalized.append(ActiveStateProposalUpdate(
                slot if update.target_kind == "slot" else None, "clear", None, None, None,
                update.target_kind, update.target_ref, update.attribute, update.basis, update.consequence_rule_id,
            ))
            continue
        value = validate_active_state_value(slot, update.value)
        if not _valid_span(update.excerpt_start_cp, update.excerpt_end_cp):
            raise ValueError("active-state set proposal requires a valid source span")
        normalized.append(ActiveStateProposalUpdate(
            slot if update.target_kind == "slot" else None, "set", value,
            update.excerpt_start_cp, update.excerpt_end_cp, update.target_kind,
            update.target_ref, update.attribute, update.basis, update.consequence_rule_id,
        ))
    return tuple(normalized)


def validate_active_state_correction(
    proposal: object,
) -> tuple[ActiveStateCorrectionProposal, str]:
    """Validate one correction target/value before repository inspection."""
    if not isinstance(proposal, ActiveStateCorrectionProposal) or proposal.operation != "correct":
        raise ValueError("active-state correction must use the governed correction contract")
    if proposal.target_kind == "actor":
        subject_key = actor_state_subject_key(proposal.target_ref, proposal.attribute)
    elif proposal.target_kind == "scene":
        subject_key = scene_state_subject_key(proposal.target_ref, proposal.attribute)
    else:
        raise ValueError("active-state correction target is not governed")
    corrected = validate_active_state_value(subject_key, proposal.corrected_value)
    expected = (
        validate_active_state_value(subject_key, proposal.expected_value)
        if proposal.expected_value is not None else None
    )
    family = proposal.expected_family
    if family is not None and (
        not isinstance(family, str) or re.fullmatch(r"[a-z][a-z_]{0,31}", family) is None
    ):
        raise ValueError("active-state correction expected family is malformed")
    if expected is not None and family is not None:
        raise ValueError("active-state correction cannot mix exact and family expectations")
    if not _valid_span(proposal.excerpt_start_cp, proposal.excerpt_end_cp):
        raise ValueError("active-state correction requires exact canonical evidence")
    return ActiveStateCorrectionProposal(
        "correct", proposal.target_kind, proposal.target_ref, proposal.attribute,
        corrected, proposal.excerpt_start_cp, proposal.excerpt_end_cp,
        expected, family,
    ), subject_key
