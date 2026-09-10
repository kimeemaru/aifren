"""Sparse current-scene relations and their derived typed capability envelope."""

from __future__ import annotations

from dataclasses import dataclass
import re


RELATION_FACETS = frozenset({
    "head", "eyes", "ears", "nose", "mouth", "tongue", "neck", "torso",
    "full_outfit", "arms", "wrists", "hands", "skin", "waist", "legs", "feet",
    "full_body",
})
RELATION_SIDES = frozenset({"left", "right", "both"})
RELATION_PREDICATES = frozenset({
    # Compatibility predicates retained from schema v14.
    "covered_by", "obstructed_by", "occupied_by", "worn_by",
    # General actor/subject relation contract.
    "wearing", "holding", "carrying", "covering", "obstructing", "occupying",
    "riding", "driving", "seated_in", "using", "supported_by",
    "located_on", "located_in", "near", "attached_to", "tethered_to",
    "restrained_by", "unavailable_due_to",
})
RELATION_TARGETS = frozenset({"user", "companion"})
RELATION_TARGET_KINDS = frozenset({"actor", "scene"})
RELATION_CAUSE_KINDS = frozenset({
    "scene", "actor", "actor_part", "state", "location", "environment", "body_state",
})
RELATION_SEMANTIC_FAMILIES = frozenset({
    "vision_obstruction", "hearing_obstruction", "smell_obstruction",
    "taste_obstruction", "touch_obstruction", "speech_obstruction", "hand_occupancy",
    "body_unavailable", "body_assistance", "restraint", "mobility_constraint",
    "walking", "rolling", "skating", "cycling", "driving", "riding",
    "assisted", "swimming", "other",
})
RELATION_EFFECT_STATES = frozenset({"constrained", "unavailable"})
MAX_CURRENT_SCENE_RELATIONS = 96
CAPABILITY_DOMAINS = frozenset({
    "perception", "communication", "manipulation", "locomotion", "awareness", "posture",
})
_SAFE_CAUSE = re.compile(r"[a-z0-9][a-z0-9 _'’-]{0,95}", re.IGNORECASE)
_SCENE_REF = re.compile(
    r"(?:scene-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
    r"[a-z][a-z0-9_]{0,31})"
)


@dataclass(frozen=True)
class SceneRelationProposal:
    """One untrusted relation mutation tied to exact canonical user evidence.

    The first nine fields retain schema-v14 positional compatibility. New
    extraction should use named fields for target kind, side, stable subject
    reference, semantic family, and quantity.
    """

    operation: str  # set | clear
    target: str
    facet: str | None
    predicate: str | None = None
    cause_kind: str | None = None
    cause: str | None = None
    cause_subject_kind: str | None = None
    excerpt_start_cp: int | None = None
    excerpt_end_cp: int | None = None
    target_kind: str = "actor"
    side: str | None = None
    cause_subject_ref: str | None = None
    semantic_family: str | None = None
    quantity: int | None = None
    effect_state: str | None = None
    locus: str | None = None


def validate_scene_relation_proposal(value: object) -> SceneRelationProposal:
    if not isinstance(value, SceneRelationProposal) or value.operation not in {"set", "clear"}:
        raise ValueError("invalid scene relation proposal")
    if value.target_kind not in RELATION_TARGET_KINDS:
        raise ValueError("unsupported relation target kind")
    if value.target_kind == "actor":
        if value.target not in RELATION_TARGETS:
            raise ValueError("unsupported relation actor target")
    elif not isinstance(value.target, str) or _SCENE_REF.fullmatch(value.target) is None:
        raise ValueError("unsupported relation scene target")
    if value.facet is not None and value.facet not in RELATION_FACETS:
        raise ValueError("unsupported relation body region")
    if value.locus is not None and (
        not isinstance(value.locus, str) or _SAFE_CAUSE.fullmatch(value.locus) is None
        or value.locus != value.locus.strip()
    ):
        raise ValueError("invalid literal relation locus")
    if value.side is not None and value.side not in RELATION_SIDES:
        raise ValueError("unsupported relation side")
    if value.excerpt_start_cp is not None or value.excerpt_end_cp is not None:
        if (not isinstance(value.excerpt_start_cp, int) or not isinstance(value.excerpt_end_cp, int)
                or value.excerpt_start_cp < 0 or value.excerpt_end_cp < value.excerpt_start_cp):
            raise ValueError("invalid scene relation evidence span")
    if value.cause is not None and (
        not isinstance(value.cause, str) or _SAFE_CAUSE.fullmatch(value.cause) is None
    ):
        raise ValueError("invalid relation object label")
    if value.cause_subject_ref is not None and (
        not isinstance(value.cause_subject_ref, str) or _SCENE_REF.fullmatch(value.cause_subject_ref) is None
    ):
        raise ValueError("invalid relation scene subject reference")
    if value.semantic_family is not None and value.semantic_family not in RELATION_SEMANTIC_FAMILIES:
        raise ValueError("unsupported relation semantic family")
    if value.effect_state is not None and value.effect_state not in RELATION_EFFECT_STATES:
        raise ValueError("unsupported relation effect state")
    if value.quantity is not None and (
        isinstance(value.quantity, bool) or not isinstance(value.quantity, int)
        or not 1 <= value.quantity <= 16
    ):
        raise ValueError("invalid relation quantity")
    if value.operation == "clear":
        if (value.cause_subject_kind is not None or value.semantic_family is not None
                or value.quantity is not None or value.effect_state is not None):
            raise ValueError("clear relation has unexpected establishment fields")
        if value.predicate is not None and value.predicate not in RELATION_PREDICATES:
            raise ValueError("unsupported clear relation predicate")
        if value.cause_kind is not None and value.cause_kind not in RELATION_CAUSE_KINDS:
            raise ValueError("unsupported clear relation object kind")
        return value
    if value.predicate not in RELATION_PREDICATES or value.cause_kind not in RELATION_CAUSE_KINDS:
        raise ValueError("unsupported scene relation")
    if not isinstance(value.cause, str):
        raise ValueError("scene relation requires a bounded object label")
    if value.cause_kind == "scene":
        if (not isinstance(value.cause_subject_kind, str)
                or _SAFE_CAUSE.fullmatch(value.cause_subject_kind) is None):
            raise ValueError("scene relation requires a bounded subject kind")
    elif value.cause_subject_kind is not None or value.cause_subject_ref is not None:
        raise ValueError("non-scene relation cannot reference a scene subject")
    return value


@dataclass(frozen=True)
class CapabilityEffect:
    """One composed typed capability result with inspectable source relations."""

    target: str
    domain: str
    capability: str
    state: str
    severity: int
    source_relation_ids: tuple[str, ...]
    truth_scope_id: str


@dataclass(frozen=True)
class CapabilityEffects:
    """Shared backend capability envelope consumed across behavior seams."""

    effects: tuple[CapabilityEffect, ...] = ()
    vision_mode: str = "available"
    hearing_mode: str = "normal"
    smell_mode: str = "normal"
    taste_mode: str = "normal"
    touch_mode: str = "normal"
    speech_mode: str = "normal"
    hands_mode: str = "free"
    left_hand_mode: str = "free"
    right_hand_mode: str = "free"
    left_arm_mode: str = "normal"
    right_arm_mode: str = "normal"
    locomotion_mode: str = "walking"
    locomotion_constraint: str = "normal"
    awareness_mode: str = "normal"
    posture_mode: str | None = None
    vision_causes: tuple[str, ...] = ()
    hearing_causes: tuple[str, ...] = ()
    smell_causes: tuple[str, ...] = ()
    taste_causes: tuple[str, ...] = ()
    touch_causes: tuple[str, ...] = ()
    speech_causes: tuple[str, ...] = ()
    hand_causes: tuple[str, ...] = ()
    locomotion_causes: tuple[str, ...] = ()

    @property
    def vision_available(self) -> bool:
        return self.vision_mode == "available"

    def perception_mode(self, capability: str) -> str:
        """Return the normalized normal/constrained/unavailable sensory mode."""
        value = {
            "vision": self.vision_mode,
            "hearing": self.hearing_mode,
            "smell": self.smell_mode,
            "taste": self.taste_mode,
            "touch": self.touch_mode,
        }.get(str(capability), "normal")
        return {"available": "normal", "obstructed": "constrained"}.get(value, value)

    @property
    def speech_available(self) -> bool:
        return self.speech_mode != "unavailable"

    @property
    def manual_available(self) -> bool:
        return self.hands_mode not in {"occupied", "unavailable"}

    @property
    def vision_cause(self) -> str | None:
        return self.vision_causes[0] if self.vision_causes else None

    def prompt_payload(self) -> dict[str, object]:
        """Render only semantic modes and labels; internal relation IDs stay out."""
        return {
            "perception": {
                name: self.perception_mode(name)
                for name in ("vision", "hearing", "smell", "taste", "touch")
            },
            "communication": {"speech": self.speech_mode},
            "manipulation": {
                "hands": self.hands_mode,
                "left": self.left_hand_mode,
                "right": self.right_hand_mode,
                "left_arm": self.left_arm_mode,
                "right_arm": self.right_arm_mode,
            },
            "locomotion": {
                "mode": self.locomotion_mode,
                "constraint": self.locomotion_constraint,
            },
            "awareness": self.awareness_mode,
            **({"posture": self.posture_mode} if self.posture_mode is not None else {}),
        }


def compose_capability_effects(
    relations: tuple[object, ...],
    *,
    truth_scope_id: str,
    companion_activity: str | None = None,
    companion_posture: str | None = None,
    target: str = "companion",
) -> CapabilityEffects:
    """Compose all domains once from current sources; callers do not persist results."""
    if target not in RELATION_TARGETS:
        raise ValueError("unsupported capability target")

    def selected(region: str, predicates: set[str], family: str | None = None) -> tuple[object, ...]:
        return tuple(
            item for item in relations
            if getattr(item, "target_kind", "actor") == "actor"
            and getattr(item, "target", None) == target
            and getattr(item, "facet", None) == region
            and (getattr(item, "predicate", None) in predicates
                 or (family is not None and getattr(item, "semantic_family", None) == family))
        )

    vision = selected("eyes", {"covered_by", "obstructed_by"}, "vision_obstruction")
    vision_unavailable = tuple(
        item for item in vision
        if (getattr(item, "effect_state", None) == "unavailable"
            or (getattr(item, "predicate", None) != "obstructed_by"
                and getattr(item, "effect_state", None) != "constrained"))
    )
    vision_mode = (
        "unavailable" if vision_unavailable else "obstructed" if vision else "available"
    )

    def sensory_sources(family: str) -> tuple[object, ...]:
        return tuple(
            item for item in relations
            if getattr(item, "target_kind", "actor") == "actor"
            and getattr(item, "target", None) == target
            and getattr(item, "semantic_family", None) == family
        )

    def composed_effect_state(items: tuple[object, ...], *, default: str = "constrained") -> str:
        states = tuple(
            str(getattr(item, "effect_state", None) or default).casefold()
            for item in items
        )
        return "unavailable" if "unavailable" in states else "constrained" if states else "normal"

    hearing_relations = sensory_sources("hearing_obstruction")
    smell_relations = sensory_sources("smell_obstruction")
    taste_relations = sensory_sources("taste_obstruction")
    touch_relations = sensory_sources("touch_obstruction")
    hearing_mode = composed_effect_state(hearing_relations)
    smell_mode = composed_effect_state(smell_relations)
    taste_mode = composed_effect_state(taste_relations)
    touch_mode = composed_effect_state(touch_relations)
    speech_relations = selected(
        "mouth", {"covered_by", "obstructed_by", "occupied_by", "occupying"},
        "speech_obstruction",
    )

    def speech_severity(item: object) -> str:
        """Map relation meaning to capacity without hard-coding equipment flags."""
        explicit = getattr(item, "effect_state", None)
        if explicit in {"constrained", "unavailable"}:
            return str(explicit)
        predicate = str(getattr(item, "predicate", "") or "").casefold()
        cause_kind = str(getattr(item, "cause_kind", "") or "").casefold()
        cause = str(getattr(item, "cause", "") or "").casefold()
        if predicate in {"occupied_by", "occupying"}:
            return "constrained"
        # Ordinary body-part coverage limits articulation but does not erase
        # every possible vocal channel. Total incapacity requires explicit,
        # materially stronger evidence.
        if cause_kind == "actor_part":
            return "constrained"
        if re.search(r"\b(?:sealed|gagged|gag|airtight|completely\s+sealed)\b", cause):
            return "unavailable"
        return "constrained"

    speech_unavailable = tuple(
        item for item in speech_relations if speech_severity(item) == "unavailable"
    )
    speech_constrained = tuple(
        item for item in speech_relations if speech_severity(item) == "constrained"
    )
    hand_relations = tuple(
        item for item in relations
        if getattr(item, "target_kind", "actor") == "actor"
        and getattr(item, "target", None) == target
        and (getattr(item, "predicate", None) in {"holding", "carrying"}
             or (getattr(item, "facet", None) == "hands"
                 and getattr(item, "semantic_family", None) == "hand_occupancy"))
    )
    body_unavailable = tuple(
        item for item in relations
        if getattr(item, "target_kind", "actor") == "actor"
        and getattr(item, "target", None) == target
        and getattr(item, "semantic_family", None) == "body_unavailable"
        and getattr(item, "facet", None) in {"arms", "wrists", "hands"}
    )
    body_assistance = tuple(
        item for item in relations
        if getattr(item, "target_kind", "actor") == "actor"
        and getattr(item, "target", None) == target
        and getattr(item, "semantic_family", None) == "body_assistance"
        and getattr(item, "facet", None) in {"arms", "wrists", "hands"}
    )
    restraints = tuple(
        item for item in relations
        if getattr(item, "target_kind", "actor") == "actor"
        and getattr(item, "target", None) == target
        and getattr(item, "semantic_family", None) == "restraint"
        and getattr(item, "facet", None) in {"arms", "wrists", "hands"}
    )
    left_units = sum(
        (getattr(item, "quantity", None) or 1)
        for item in hand_relations if getattr(item, "side", None) in {"left", "both"}
    )
    right_units = sum(
        (getattr(item, "quantity", None) or 1)
        for item in hand_relations if getattr(item, "side", None) in {"right", "both"}
    )
    unsided_units = sum(
        (getattr(item, "quantity", None) or 1)
        for item in hand_relations if getattr(item, "side", None) is None
    )
    def side_has(items: tuple[object, ...], side: str) -> bool:
        return any(getattr(item, "side", None) in {side, "both"} for item in items)

    left_arm_mode = (
        "constrained" if side_has(body_unavailable, "left")
        and side_has(body_assistance, "left") else
        "unavailable" if side_has(body_unavailable, "left") else (
        "constrained" if side_has(restraints, "left") else "normal"
    ))
    right_arm_mode = (
        "constrained" if side_has(body_unavailable, "right")
        and side_has(body_assistance, "right") else
        "unavailable" if side_has(body_unavailable, "right") else (
        "constrained" if side_has(restraints, "right") else "normal"
    ))
    left_mode = (
        "unavailable" if left_arm_mode == "unavailable" else
        "constrained" if left_arm_mode == "constrained" else
        "occupied" if left_units else ("partially_occupied" if unsided_units else "free")
    )
    right_mode = (
        "unavailable" if right_arm_mode == "unavailable" else
        "constrained" if right_arm_mode == "constrained" else
        "occupied" if right_units else ("partially_occupied" if unsided_units else "free")
    )
    if left_mode == "unavailable" and right_mode == "unavailable":
        hands_mode = "unavailable"
    elif ((left_units and right_units) or unsided_units >= 2
            or any(getattr(item, "side", None) == "both" for item in hand_relations)):
        hands_mode = "occupied"
    elif "constrained" in {left_mode, right_mode} or "unavailable" in {left_mode, right_mode}:
        hands_mode = "constrained"
    elif restraints or body_unavailable:
        # Evidence can establish one affected limb without licensing a
        # fabricated left/right assignment. Preserve the aggregate constraint.
        hands_mode = "constrained"
    elif hand_relations:
        hands_mode = "partially_occupied"
    else:
        hands_mode = "free"

    priority = {
        "driving": 90, "cycling": 80, "riding": 70, "assisted": 60,
        "skating": 50, "rolling": 40, "swimming": 30, "walking": 10, "other": 1,
    }
    locomotion_candidates: list[tuple[int, str, object]] = []
    for item in relations:
        if (getattr(item, "target_kind", "actor") != "actor"
                or getattr(item, "target", None) != target):
            continue
        predicate = getattr(item, "predicate", None)
        mode = getattr(item, "semantic_family", None)
        if predicate == "driving":
            mode = "driving"
        elif predicate == "riding" and mode not in {"cycling", "riding"}:
            mode = "riding"
        elif predicate in {"seated_in", "supported_by", "using"} and mode is None:
            mode = "assisted"
        if mode in priority:
            locomotion_candidates.append((priority[mode], str(mode), item))
    locomotion_candidates.sort(
        key=lambda row: (-row[0], str(getattr(row[2], "relation_id", ""))),
    )
    locomotion_mode = locomotion_candidates[0][1] if locomotion_candidates else "walking"
    locomotion_sources = tuple(
        row[2] for row in locomotion_candidates if row[1] == locomotion_mode
    )
    mobility_constraints = tuple(
        item for item in relations
        if getattr(item, "target_kind", "actor") == "actor"
        and getattr(item, "target", None) == target
        and getattr(item, "semantic_family", None) in {"restraint", "mobility_constraint"}
    )
    locomotion_constraint = (
        "unavailable" if any(
            getattr(item, "effect_state", None) == "unavailable"
            for item in mobility_constraints
        ) else "constrained" if mobility_constraints or locomotion_mode != "walking" else "normal"
    )
    awareness_mode = "asleep" if str(companion_activity or "").casefold() == "sleeping" else "normal"
    speech_mode = "unavailable" if speech_unavailable else (
        "constrained" if speech_constrained else "normal"
    )
    effects: list[CapabilityEffect] = []
    if vision:
        effects.append(CapabilityEffect(
            target, "perception", "vision",
            "unavailable" if vision_mode == "unavailable" else "constrained",
            3 if vision_mode == "unavailable" else 2,
            tuple(str(getattr(item, "relation_id", "")) for item in vision),
            truth_scope_id,
        ))
    for capability, mode, sources in (
        ("hearing", hearing_mode, hearing_relations),
        ("smell", smell_mode, smell_relations),
        ("taste", taste_mode, taste_relations),
        ("touch", touch_mode, touch_relations),
    ):
        if sources:
            effects.append(CapabilityEffect(
                target, "perception", capability, mode,
                3 if mode == "unavailable" else 2,
                tuple(str(getattr(item, "relation_id", "")) for item in sources),
                truth_scope_id,
            ))
    if speech_unavailable or speech_constrained:
        sources = speech_unavailable + speech_constrained
        effects.append(CapabilityEffect(
            target, "communication", "speech", speech_mode,
            3 if speech_mode == "unavailable" else 2,
            tuple(str(getattr(item, "relation_id", "")) for item in sources),
            truth_scope_id,
        ))
    manual_sources = hand_relations + body_unavailable + body_assistance + restraints
    if manual_sources:
        effects.append(CapabilityEffect(
            target, "manipulation", "hands", hands_mode,
            3 if hands_mode in {"occupied", "unavailable"} else 2,
            tuple(dict.fromkeys(str(getattr(item, "relation_id", "")) for item in manual_sources)),
            truth_scope_id,
        ))
    for side, mode in (("left", left_arm_mode), ("right", right_arm_mode)):
        if mode != "normal":
            sources = tuple(
                item for item in body_unavailable + body_assistance + restraints
                if getattr(item, "side", None) in {side, "both"}
            )
            effects.append(CapabilityEffect(
                target, "manipulation", f"{side}_arm", mode,
                3 if mode == "unavailable" else 2,
                tuple(str(getattr(item, "relation_id", "")) for item in sources),
                truth_scope_id,
            ))
    if locomotion_sources:
        all_locomotion_sources = tuple(row[2] for row in locomotion_candidates)
        effects.append(CapabilityEffect(
            target, "locomotion", "mode", locomotion_mode, 2,
            tuple(str(getattr(item, "relation_id", "")) for item in all_locomotion_sources),
            truth_scope_id,
        ))
    if mobility_constraints:
        effects.append(CapabilityEffect(
            target, "locomotion", "constraint", locomotion_constraint,
            3 if locomotion_constraint == "unavailable" else 2,
            tuple(str(getattr(item, "relation_id", "")) for item in mobility_constraints),
            truth_scope_id,
        ))
    if awareness_mode == "asleep":
        effects.append(CapabilityEffect(
            target, "awareness", "awareness", "asleep", 3, (),
            truth_scope_id,
        ))
    if companion_posture is not None:
        effects.append(CapabilityEffect(
            target, "posture", "posture", companion_posture, 1, (),
            truth_scope_id,
        ))

    def causes(items: tuple[object, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(getattr(item, "cause", "")) for item in items))

    return CapabilityEffects(
        effects=tuple(effects),
        vision_mode=vision_mode,
        hearing_mode=hearing_mode,
        smell_mode=smell_mode,
        taste_mode=taste_mode,
        touch_mode=touch_mode,
        speech_mode=speech_mode,
        hands_mode=hands_mode,
        left_hand_mode=left_mode,
        right_hand_mode=right_mode,
        left_arm_mode=left_arm_mode,
        right_arm_mode=right_arm_mode,
        locomotion_mode=locomotion_mode,
        locomotion_constraint=locomotion_constraint,
        awareness_mode=awareness_mode,
        posture_mode=companion_posture,
        vision_causes=causes(vision),
        hearing_causes=causes(hearing_relations),
        smell_causes=causes(smell_relations),
        taste_causes=causes(taste_relations),
        touch_causes=causes(touch_relations),
        speech_causes=causes(speech_unavailable + speech_constrained),
        hand_causes=causes(manual_sources),
        locomotion_causes=causes(tuple(row[2] for row in locomotion_candidates) + mobility_constraints),
    )
