"""Typed backend authority for immersive Current Scene overlay actions.

Unity supplies only an opaque snapshot-relative command token. The backend
validates and applies that command before constructing this bounded event;
model-facing prose is therefore narration of accepted state, never evidence
that is reparsed into Active State.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from collections.abc import Mapping
import re
import uuid

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

    def control_record(self, *, command_id, control_event_id, character_id, truth_scope, timestamp):
        """Capture accepted wording/provenance in the mutation's SQLite transaction."""
        return {
            "version": 1,
            "event": asdict(self),
            "message": {
                "role": "user", "content": self.model_text, "timestamp": timestamp,
                "truth_scope": dict(truth_scope),
                "origin": {
                    **self.canonical_origin(), "command_id": command_id,
                    "control_event_id": control_event_id, "character_id": character_id,
                },
            },
        }


def load_scene_ui_control_record(record, *, command_id, control_event_id, character_id):
    """Validate a captured record; never reconstruct it from today's scene state."""
    try:
        if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
            raise ValueError()
        event = SceneUiEvent(**record["event"])
        message = record["message"]
        scope = message["truth_scope"]
        if (
            event.event_origin != "scene_ui" or event.actor != "user"
            or event.operation_family != "clear_relation"
            or event.target_actor not in {"user", "companion", "scene"}
            or event.scope_kind not in {"real_world", "scenario"}
            or not isinstance(event.reaction_opportunity, bool)
            or any(not isinstance(value, str) or len(value) > 512
                   for key, value in asdict(event).items() if key != "reaction_opportunity")
            or not event.model_text
            or set(scope) != {"kind", "scope_id"}
            or scope.get("kind") != event.scope_kind
            or not isinstance(scope.get("scope_id"), str) or not scope["scope_id"]
        ):
            raise ValueError()
        datetime.fromisoformat(message["timestamp"])
        expected = event.control_record(
            command_id=command_id, control_event_id=control_event_id,
            character_id=character_id, truth_scope=scope, timestamp=message["timestamp"],
        )
        if record != expected or not valid_scene_ui_origin(message["origin"]):
            raise ValueError()
        return event, message
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("Stored scene event is unavailable or invalid; its data was preserved.") from error


def valid_scene_ui_origin(origin):
    """Recognize the legacy origin or its exact, linked command extension."""
    if not isinstance(origin, Mapping) or origin.get("generated_event") is not True:
        return False
    base = {"kind": "scene_ui", "generated_event": True, "operation": "clear_relation"}
    if origin == base:
        return True
    if set(origin) != set(base) | {"command_id", "control_event_id", "character_id"}:
        return False
    if any(origin[key] != value for key, value in base.items()):
        return False
    try:
        character_id = str(uuid.UUID(origin["character_id"]))
        command_id = str(uuid.UUID(origin["command_id"]))
    except (ValueError, TypeError, AttributeError):
        return False
    return (
        character_id == origin["character_id"] and command_id == origin["command_id"]
        and origin["control_event_id"] == str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"aifren:continuity-control:{character_id}:{command_id}",
        ))
    )


def scene_ui_clear_event(relation: object, scope: object, *, target_label: str | None = None) -> SceneUiEvent:
    """Render one already-accepted relation close without leaking internals."""
    target = _actor(getattr(relation, "target", None))
    facet = _safe_label(getattr(relation, "facet", None), maximum=24)
    side = _safe_label(getattr(relation, "side", None), maximum=12)
    predicate = str(getattr(relation, "predicate", "") or "").casefold()
    cause_kind = str(getattr(relation, "cause_kind", "") or "").casefold()
    family = str(getattr(relation, "semantic_family", "") or "").casefold()
    cause = _safe_label(getattr(relation, "cause", None), maximum=64) or "item"
    locus = _safe_label(getattr(relation, "locus", None), maximum=64)

    # Predicate/family own the semantic operation. A decorative worn item on
    # a wrist must never inherit restraint wording from its body facet.
    if locus and getattr(relation, "target_kind", "actor") == "scene":
        target = "scene"
        label_target = _safe_label(target_label, maximum=64)
        if not label_target:
            raise ValueError("scene event target label is unavailable")
        text = f"*I remove the {cause} from the {label_target}'s {locus}.*"
        label = f"{cause} on {label_target} {locus}"
    elif locus:
        pronoun = "your" if target == "companion" else "my"
        text, label = f"*I remove the {cause} from {pronoun} {locus}.*", f"{cause} on {locus}"
    elif family == "restraint" or predicate in {"tethered_to", "restrained_by"}:
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
