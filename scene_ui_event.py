"""Typed backend authority for immersive Current Scene overlay actions.

Unity supplies only an opaque snapshot-relative command token. The backend
validates and applies that command before constructing this bounded event;
model-facing prose is therefore narration of accepted state, never evidence
that is reparsed into Active State.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from response_requirements import RequiredFact, ResponseRequirement


@dataclass(frozen=True)
class SceneUiEvent:
    event_origin: str
    actor: str
    operation_family: str
    target_actor: str
    semantic_label: str
    scope_kind: str
    scope_label: str
    model_text: str
    accepted_mutation_summary: str
    reaction_opportunity: bool = True

    def canonical_origin(self) -> dict[str, object]:
        return {
            "kind": self.event_origin,
            "generated_event": True,
            "operation": self.operation_family,
        }


def scene_ui_clear_event(relation: object, scope: object) -> SceneUiEvent:
    """Render one already-accepted relation close without leaking internals."""
    target = _actor(getattr(relation, "target", None))
    facet = _safe_label(getattr(relation, "facet", None), maximum=24)
    side = _safe_label(getattr(relation, "side", None), maximum=12)
    predicate = str(getattr(relation, "predicate", "") or "").casefold()
    cause_kind = str(getattr(relation, "cause_kind", "") or "").casefold()
    family = str(getattr(relation, "semantic_family", "") or "").casefold()
    cause = _safe_label(getattr(relation, "cause", None), maximum=64) or "item"

    # Predicate/family own the semantic operation. A decorative worn item on
    # a wrist must never inherit restraint wording from its body facet.
    if family == "restraint" or predicate in {"tethered_to", "restrained_by"}:
        qualified = (side + " wrist").strip() if side else "wrist"
        text, label = f"*I release your {qualified} from the restraint.*", f"{qualified} restraint"
    elif cause_kind == "environment" and family == "hearing_obstruction":
        text, label = "*The loud music stops.*", cause
    elif cause_kind == "environment" and family == "vision_obstruction":
        text, label = "*The obstructing environmental condition clears.*", cause
    elif predicate in {"wearing", "worn_by"} and facet == "ears" and "earplug" in cause.casefold():
        text, label = "*I remove the earplugs from your ears.*", "earplugs"
    elif predicate in {"wearing", "worn_by"}:
        pronoun = "your" if target == "companion" else "my"
        text, label = f"*I take off {pronoun} {cause}.*", cause
    elif predicate in {"holding", "carrying"}:
        text = (
            f"*I take the {cause} from you and set it down.*"
            if target == "companion" else f"*I set the {cause} down.*"
        )
        label = cause
    elif facet == "eyes" and "blindfold" in cause.casefold():
        text, label = "*I take off your blindfold.*", "blindfold"
    elif facet == "eyes" and cause_kind == "actor_part":
        plural = "hands" if "hands" in cause.casefold() else "hand"
        text, label = f"*I move my {plural} away from your eyes.*", f"my {plural} over your eyes"
    elif facet == "mouth" and cause_kind == "actor_part":
        text, label = "*I move my hand away from your mouth.*", "my hand over your mouth"
    elif facet == "mouth":
        text, label = f"*I remove the {cause} from your mouth.*", cause
    elif predicate in {"riding", "driving", "seated_in", "using", "supported_by"}:
        text, label = f"*I help you stop using the {cause}.*", cause
    elif predicate in {"covered_by", "obstructed_by", "occupied_by", "tethered_to"}:
        text, label = f"*I remove the {cause} from you.*", cause
    else:
        text, label = f"*I remove the {cause} from the current scene.*", cause

    scope_kind = str(getattr(scope, "kind", "real_world") or "real_world")
    scope_label = _safe_label(getattr(scope, "label", None), maximum=48)
    return SceneUiEvent(
        event_origin="scene_ui", actor="user", operation_family="clear_relation",
        target_actor=target, semantic_label=label,
        scope_kind=scope_kind, scope_label=scope_label,
        model_text=text,
        accepted_mutation_summary=f"cleared {label} for {target}",
    )


def scene_ui_response_requirement(event: SceneUiEvent) -> ResponseRequirement:
    label = event.semantic_label
    context = (
        "[Accepted scene interaction — backend fact]\n"
        "The following mutation already succeeded before generation. React naturally; "
        "do not claim that the cleared cause is still current. You do not need to restate it.\n"
        f"{{\"operation\":\"clear_relation\",\"target\":\"{event.target_actor}\","
        f"\"label\":\"{label}\"}}\n"
        "[End accepted scene interaction]"
    )
    return ResponseRequirement(
        intent="scene_ui_clear",
        facts=(RequiredFact("cleared_scene_cause", label, (label,)),),
        fallback_dialogue="*Adjusts naturally to the change.*",
        context_block=context,
        forbidden_terms=(
            f"still wearing {label}", f"still holding {label}",
            f"{label} is still", f"still blocked by {label}",
        ),
        mode="must_respect",
    )


def _actor(value: object) -> str:
    return "user" if str(value or "").casefold() == "user" else "companion"


def _safe_label(value: object, *, maximum: int) -> str:
    compact = " ".join(str(value or "").strip().split())[:maximum]
    if not compact or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]{0,63}", compact):
        return ""
    return compact.casefold()
