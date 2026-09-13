"""Large synthetic QA harness for the production Active State pipeline.

This module is deliberately a test simulator, not a production world model.
It feeds synthetic canonical user messages through the same extraction,
reference/correction, validation, persistence, capability, context, response,
action, and presentation seams used by AssistantService. Reports contain only
structural case IDs, categories, numeric counts, and timings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import statistics
import tempfile
import time
from typing import Any, Callable, Iterable
import uuid

from aifren.state.capability_policy import capability_context_block, validate_capability_response
from aifren.character.character_scene_profile import (
    cache_character_scene_profile,
    derive_character_scene_profile,
    effective_profile_worn_items,
)
from aifren.state.companion_action import validate_companion_action_decision
from aifren.state.current_continuity import admit_current_continuity_context
from aifren.state.interaction_policy import (
    classify_interaction_policy,
    render_sleep_reaction,
)
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository
from aifren.memory_v2_store.scene_relation_contract import compose_capability_effects
from aifren.dialogue.presentation_metadata import parse_assistant_response
from aifren.dialogue.response_requirements import (
    derive_response_requirement,
    validate_response_requirement,
)


BASE_TIME = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
SYNTHETIC_ASSISTANT = "Synthetic response."


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    family: str
    passed: bool
    checks: int
    turns: int
    duration_ms: float
    failure_category: str | None = None


@dataclass(frozen=True)
class HarnessReport:
    version: str
    case_count: int
    passed: int
    failed: int
    checks: int
    turns: int
    duration_ms: float
    median_case_ms: float
    p95_case_ms: float
    max_context_chars: int
    results: tuple[CaseResult, ...]


@dataclass(frozen=True)
class CuratedCase:
    case_id: str
    family: str
    runner: Callable[["SyntheticSession", "Checks", int], None]
    variant: int


class Checks:
    def __init__(self) -> None:
        self.count = 0

    def expect(self, condition: object, category: str) -> None:
        self.count += 1
        if not condition:
            raise AssertionError(category)

    def equal(self, actual: object, expected: object, category: str) -> None:
        self.expect(actual == expected, f"{category}:{actual!r}!={expected!r}")


class SyntheticSession:
    """One isolated synthetic character using the canonical writer boundary."""

    def __init__(self, case_id: str) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="aifren-active-state-")
        self.root = Path(self._temp.name)
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.conversation_file.write_text("[]", encoding="utf-8")
        self.character_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, "aifren:active-state-qa:" + case_id,
        ))
        self.rows: list[dict[str, object]] = []
        self.turn_count = 0
        self._action_count = 0
        self.turn_latencies_ms: list[float] = []
        self.max_context_chars = 0
        self._open_writer()

    def _open_writer(self) -> None:
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id,
            display_name="Synthetic", memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(
            self.character_id, "Synthetic", legacy_config_key="characters/default",
        )

    def close(self) -> None:
        self.writer.close()
        self._temp.cleanup()

    def restart(self) -> None:
        self.writer.close()
        self._open_writer()

    def turn(self, text: str, *, assistant: str = SYNTHETIC_ASSISTANT) -> dict[str, Any]:
        scope = self.repository.active_truth_scope(self.character_id)
        timestamp = BASE_TIME + timedelta(seconds=2 * self.turn_count)
        provenance = {"kind": scope.kind, "scope_id": scope.truth_scope_id}
        user = {
            "role": "user", "content": text, "timestamp": timestamp.isoformat(),
            "truth_scope": provenance,
        }
        assistant_row = {
            "role": "assistant", "content": assistant,
            "timestamp": (timestamp + timedelta(seconds=1)).isoformat(),
            "truth_scope": provenance,
        }
        index = len(self.rows)
        self.rows.extend((user, assistant_row))
        self.conversation_file.write_text(
            json.dumps(self.rows, ensure_ascii=False), encoding="utf-8",
        )
        started = time.perf_counter()
        result = self.writer.observe_canonical_user_continuity(
            user, conversation_index=index, conversation_file=self.conversation_file,
        )
        self.turn_latencies_ms.append((time.perf_counter() - started) * 1000.0)
        self.turn_count += 1
        return result

    def apply_action(self, plan: object) -> dict[str, Any]:
        self._action_count += 1
        recorded_at_us = int(
            (BASE_TIME + timedelta(
                microseconds=max(1, self.turn_count * 2_000_000 - 500_000),
            )).timestamp()
            * 1_000_000
        )
        return self.writer.apply_governed_companion_action(
            plan,
            decision_reference=f"synthetic-{self.turn_count}-{self._action_count}",
            recorded_at_us=recorded_at_us,
        )

    def effects(self, *, target: str = "companion"):
        return self.repository.capability_effects(self.character_id, target=target)

    def relations(self):
        return self.repository.list_scene_relations(self.character_id, limit=96)

    def scene(self) -> tuple[dict[str, str], ...]:
        rendered = []
        for subject in self.repository.list_scene_subjects(self.character_id, limit=24):
            rendered.append({
                row.subject_key.rsplit(".", 1)[-1]: row.value
                for row in self.repository.lookup_scene_attributes(
                    self.character_id, subject.scene_subject_id,
                )
            })
        return tuple(rendered)

    def activity(self, actor: str) -> str | None:
        state = self.repository.lookup_actor_state(
            self.character_id, actor, "activity",
        ).state
        return state.value if state is not None else None

    def posture(self, actor: str = "companion") -> str | None:
        state = self.repository.lookup_actor_state(
            self.character_id, actor, "posture",
        ).state
        return state.value if state is not None else None

    def context(self, query: str) -> str:
        admission = admit_current_continuity_context(
            self.repository, self.character_id, query,
            now_us=int((BASE_TIME + timedelta(days=1)).timestamp() * 1_000_000),
        )
        block = "\n".join(filter(None, (
            admission.truth_scope_context,
            admission.active_state_context,
            admission.open_thread_context,
        )))
        self.max_context_chars = max(self.max_context_chars, len(block))
        return block

    def structural_snapshot(self) -> tuple[object, ...]:
        scope = self.repository.active_truth_scope(self.character_id)
        relations = tuple(sorted(
            (item.target_kind, item.target, item.facet or "", item.side or "",
             item.predicate, item.cause_kind, item.cause,
             item.semantic_family or "", item.quantity or 0)
            for item in self.relations()
        ))
        scene = tuple(sorted(tuple(sorted(item.items())) for item in self.scene()))
        effects = self.effects().prompt_payload()
        return (
            scope.kind, scope.label, self.activity("user"),
            self.activity("companion"), self.posture(), relations, scene,
            json.dumps(effects, sort_keys=True, separators=(",", ":")),
        )


def _relation_causes(session: SyntheticSession, target: str, predicate: str) -> set[str]:
    return {
        item.cause for item in session.relations()
        if item.target_kind == "actor" and item.target == target
        and item.predicate == predicate
    }


def _assert_invariants(session: SyntheticSession, checks: Checks) -> None:
    store = session.writer.store
    current_duplicates = store.connection.execute(
        """SELECT truth_scope_id, subject_key, COUNT(*) AS n FROM claims
             WHERE character_id=? AND claim_type='active_state' AND valid_to_us IS NULL
             GROUP BY truth_scope_id, subject_key HAVING COUNT(*) > 1""",
        (session.character_id,),
    ).fetchall()
    checks.expect(not current_duplicates, "singleton_current_value")

    scope = session.repository.active_truth_scope(session.character_id)
    relations = session.relations()
    checks.expect(
        all(item.truth_scope_id == scope.truth_scope_id for item in relations),
        "cross_scope_relation_leak",
    )
    identities = [
        (item.target_kind, item.target, item.facet, item.side, item.predicate,
         item.cause_kind, item.cause, item.cause_subject_id)
        for item in relations
    ]
    checks.equal(len(identities), len(set(identities)), "relation_identity_unique")

    worn_slots = [
        (item.target, item.facet, item.side or "unsided") for item in relations
        if item.target_kind == "actor" and item.predicate in {"wearing", "worn_by"}
        and item.facet in {"head", "eyes", "hands", "feet", "full_outfit"}
    ]
    checks.equal(len(worn_slots), len(set(worn_slots)), "exclusive_equipment_slot")
    for target, facet in {(target, facet) for target, facet, _side in worn_slots}:
        sides = [side for owner, region, side in worn_slots if owner == target and region == facet]
        checks.expect(
            len(sides) == 1 or set(sides) == {"left", "right"},
            "exclusive_equipment_side_composition",
        )

    effects = session.effects()
    activity = session.activity("companion")
    expected = compose_capability_effects(
        relations, truth_scope_id=scope.truth_scope_id,
        companion_activity=activity, companion_posture=session.posture(),
    )
    checks.equal(effects, expected, "capability_rederivation")
    source_ids = {item.relation_id for item in relations}
    checks.expect(
        all(set(effect.source_relation_ids) <= source_ids for effect in effects.effects),
        "capability_source_current",
    )

    # Semantic ownership relations are authoritative; scene attributes are a
    # compatibility projection and must agree after every governed mutation.
    expected_ownership: dict[tuple[str, str], str] = {}
    for relation in relations:
        attribute = (
            "held_by" if relation.predicate in {"holding", "carrying"}
            else "worn_by" if relation.predicate in {"wearing", "worn_by"}
            else None
        )
        if attribute is None or relation.cause_subject_id is None:
            continue
        key = (relation.cause_subject_id, attribute)
        checks.expect(
            key not in expected_ownership or expected_ownership[key] == relation.target,
            "relation_ownership_single_actor",
        )
        expected_ownership[key] = relation.target
    for subject in session.repository.list_scene_subjects(session.character_id):
        attributes = {
            record.subject_key.rsplit(".", 1)[-1]: record.value
            for record in session.repository.lookup_scene_attributes(
                session.character_id, subject.scene_subject_id,
            )
        }
        checks.equal(
            attributes.get("held_by"),
            expected_ownership.get((subject.scene_subject_id, "held_by")),
            "held_relation_attribute_parity",
        )
        checks.equal(
            attributes.get("worn_by"),
            expected_ownership.get((subject.scene_subject_id, "worn_by")),
            "worn_relation_attribute_parity",
        )

    context = session.context("What is relevant right now?")
    checks.expect(len(context) <= 2100, "context_bound")
    checks.expect(
        all(token not in context for token in (
            "relation_id", "event_id", "excerpt", "sha256", "scene-",
        )),
        "context_internal_identifier",
    )
    checks.expect(
        not store.connection.execute("PRAGMA foreign_key_check").fetchall(),
        "foreign_key_integrity",
    )
    orphaned_sources = store.connection.execute(
        """SELECT 1 FROM active_scene_relations r
             JOIN active_scene_subjects s
               ON s.character_id=r.character_id AND s.truth_scope_id=r.truth_scope_id
              AND s.scene_subject_id=r.cause_subject_id
            WHERE r.character_id=? AND r.valid_to_us IS NULL
              AND s.retired_at_us IS NOT NULL LIMIT 1""",
        (session.character_id,),
    ).fetchone()
    checks.expect(orphaned_sources is None, "active_relation_source_not_retired")
    orphaned_targets = store.connection.execute(
        """SELECT 1 FROM active_scene_relations r
             JOIN active_scene_subjects s
               ON s.character_id=r.character_id AND s.truth_scope_id=r.truth_scope_id
              AND s.scene_subject_id=r.target_actor
            WHERE r.character_id=? AND r.valid_to_us IS NULL
              AND r.target_kind='scene' AND s.retired_at_us IS NOT NULL LIMIT 1""",
        (session.character_id,),
    ).fetchone()
    checks.expect(orphaned_targets is None, "active_relation_target_not_retired")
    subjects = session.repository.list_scene_subjects(session.character_id, limit=128)
    checks.expect(len(subjects) <= 128, "scene_roster_bound")
    checks.expect(
        sum(item.lifecycle_state == "dormant" for item in subjects) <= 32,
        "dormant_roster_bound",
    )
    canonical = json.loads(session.conversation_file.read_text(encoding="utf-8"))
    checks.equal(canonical, session.rows, "canonical_evidence_retained")


def _validated_response(
    session: SyntheticSession,
    dialogue: str,
    *,
    response_mode: str = "normal_conversation",
    spoken_content: str | None = None,
    presentation: dict[str, object] | None = None,
):
    raw = json.dumps({
        "dialogue": dialogue,
        "response_mode": response_mode,
        "spoken_content": spoken_content,
        "presentation": presentation,
        "companion_action": None,
        "capability_compliance": [],
    })
    return validate_capability_response(
        parse_assistant_response(raw), session.effects(),
    )


def _requirement(session: SyntheticSession, query: str):
    return derive_response_requirement(
        session.repository, session.character_id, query,
        local_datetime=BASE_TIME,
        companion_effects=session.effects(),
        user_effects=session.effects(target="user"),
    )


_ACTIVITIES = (
    ("cooking dinner", "Go cook dinner.", "cooking dinner"),
    ("reading a book", "Go read a book.", "reading a book"),
    ("writing a letter", "Go write a letter.", "writing a letter"),
    ("cleaning the kitchen", "Go clean the kitchen.", "cleaning the kitchen"),
    ("drawing a fox", "Go draw a fox.", "drawing a fox"),
    ("building a model", "Go build a model.", "building a model"),
    ("repairing a keyboard", "Go repair a keyboard.", "repairing a keyboard"),
    ("baking bread", "Go bake bread.", "baking bread"),
    ("gardening", "Go garden.", "gardening"),
    ("resting", "Go rest.", "resting"),
)


def _run_activity(session: SyntheticSession, checks: Checks, variant: int) -> None:
    activity, command, companion_value = _ACTIVITIES[variant // 4]
    phase = variant % 4
    if phase == 0:
        session.turn(f"I'm {activity}.")
        checks.equal(session.activity("user"), activity, "user_activity")
        checks.equal(session.activity("companion"), None, "wrong_actor")
    elif phase == 1:
        session.turn("I'm working.")
        session.turn(f"No, I meant {activity}.")
        checks.equal(session.activity("user"), activity, "corrected_user_activity")
        historical = session.writer.store.connection.execute(
            """SELECT COUNT(*) FROM claims WHERE character_id=?
                 AND subject_key='active.actor.user.activity'""",
            (session.character_id,),
        ).fetchone()[0]
        checks.expect(historical >= 2, "correction_history_retained")
    elif phase == 2:
        session.turn(command)
        checks.equal(session.activity("companion"), companion_value, "companion_activity")
        checks.equal(session.activity("user"), None, "companion_wrong_actor")
    else:
        session.turn("Go work.")
        session.turn("Go back.")
        checks.equal(session.activity("companion"), "working", "direction_particle_abstain")
        session.turn(f"Now start {activity}.")
        checks.equal(session.activity("companion"), activity, "immediate_replacement")
    _assert_invariants(session, checks)


_OUTFITS = (
    ("black dress", "red scarf", "boots"),
    ("blue dress", "white scarf", "shoes"),
    ("green coat", "black scarf", "sneakers"),
    ("red dress", "blue scarf", "rollerblades"),
    ("white hoodie", "purple scarf", "boots"),
    ("brown jacket", "green scarf", "shoes"),
    ("pink dress", "black necklace", "sneakers"),
    ("gray coat", "red scarf", "skates"),
    ("orange shirt", "blue scarf", "boots"),
    ("teal dress", "white necklace", "shoes"),
)


def _run_clothing(session: SyntheticSession, checks: Checks, variant: int) -> None:
    first, middle, feet = _OUTFITS[variant // 4]
    phase = variant % 4
    if phase == 0:
        session.turn(f"You're wearing a {first}, a {middle}, and {feet}.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.equal(causes, {first, middle, feet}, "multi_item_outfit")
        checks.equal(sum(item.get("worn_by") == "companion" for item in session.scene()), 3,
                     "outfit_mirrors")
    elif phase == 1:
        session.turn(f"You're wearing a {first}, a {middle}, and {feet}.")
        session.turn(f"Take off the {middle}.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.equal(causes, {first, feet}, "partial_clothing_clear")
    elif phase == 2:
        session.turn(f"You're wearing a {first}, a {middle}, and {feet}.")
        replacement = "rollerblades" if feet == "sneakers" else "sneakers"
        session.turn(f"Take off the {feet} and put on {replacement}.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.expect(feet not in causes and replacement in causes, "footwear_replacement")
        checks.expect(first in causes and middle in causes, "replacement_preserves_other_slots")
        feet_rows = [item for item in session.relations() if item.target == "companion"
                     and item.predicate == "wearing" and item.facet == "feet"]
        checks.equal(len(feet_rows), 1, "exclusive_footwear")
    else:
        session.turn(f"I'm wearing a {first} and {feet}.")
        checks.equal(_relation_causes(session, "user", "wearing"), {first, feet},
                     "user_outfit")
        checks.equal(_relation_causes(session, "companion", "wearing"), set(),
                     "user_outfit_wrong_actor")
    _assert_invariants(session, checks)


def _run_vision(session: SyntheticSession, checks: Checks, variant: int) -> None:
    activity = _ACTIVITIES[variant // 4][0]
    session.turn(f"I'm {activity}.")
    phase = variant % 4
    if phase == 0:
        session.turn("I blindfold you.")
        expected = {"blindfold"}
    elif phase == 1:
        session.turn("I cover your eyes with my hands.")
        expected = {"user hands"}
    else:
        session.turn("You're blindfolded.")
        session.turn("I cover your eyes with my hands.")
        if phase == 3:
            session.turn("I take my hands away from your eyes.")
            expected = {"blindfold"}
        else:
            expected = {"blindfold", "user hands"}
    effects = session.effects()
    checks.equal(effects.vision_mode, "unavailable", "vision_unavailable")
    checks.equal(set(effects.vision_causes), expected, "vision_cause_composition")
    visual = _validated_response(session, "I can see a red light.")
    checks.expect(not visual.accepted and visual.category == "prohibited_visual_claim",
                  "visual_claim_rejected")
    checks.expect('"vision":"unavailable"' in session.context("What do you see?"),
                  "vision_context")
    _assert_invariants(session, checks)


def _run_speech(session: SyntheticSession, checks: Checks, variant: int) -> None:
    session.turn(f"I'm {_ACTIVITIES[variant // 4][0]}.")
    phase = variant % 4
    if phase == 0:
        session.turn("Your mouth is full.")
        expected_mode, expected_causes = "constrained", {"mouth contents"}
    elif phase == 1:
        session.turn("I cover your mouth with my hand.")
        expected_mode, expected_causes = "constrained", {"user hand"}
    else:
        session.turn("Your mouth is full.")
        session.turn("I cover your mouth with my hand.")
        if phase == 3:
            session.turn("I take my hand away from your mouth.")
            expected_mode, expected_causes = "constrained", {"mouth contents"}
        else:
            expected_mode, expected_causes = "constrained", {"mouth contents", "user hand"}
    effects = session.effects()
    checks.equal(effects.speech_mode, expected_mode, "speech_mode")
    checks.equal(set(effects.speech_causes), expected_causes, "speech_causes")
    ordinary = _validated_response(session, "I can answer normally.")
    checks.expect(not ordinary.accepted, "normal_speech_rejected")
    if expected_mode == "constrained":
        constrained = _validated_response(
            session, "*Tilts their head without speaking.* Mm.",
            response_mode="speech_constrained", spoken_content="Mm.",
            presentation={"speech_mode": "constrained"},
        )
        checks.expect(constrained.accepted, "constrained_speech_accepted")
    else:
        nonverbal = _validated_response(
            session, "*Tilts their head without speaking.*",
            response_mode="nonverbal_reaction", spoken_content="",
            presentation={"speech_mode": "nonverbal"},
        )
        checks.expect(nonverbal.accepted and not nonverbal.spoken_text,
                      "nonverbal_response_accepted")
    _assert_invariants(session, checks)


def _run_hands(session: SyntheticSession, checks: Checks, variant: int) -> None:
    session.turn(f"I'm {_ACTIVITIES[variant // 4][0]}.")
    phase = variant % 4
    if phase == 0:
        session.turn("You're carrying a box in one hand.")
        checks.equal(session.effects().hands_mode, "partially_occupied", "one_hand_occupied")
    elif phase == 1:
        session.turn("You're holding a cup and a book.")
        checks.equal(session.effects().hands_mode, "occupied", "two_objects_occupied")
    elif phase == 2:
        session.turn("You're holding a cup in your left hand and a book in your right hand.")
        effects = session.effects()
        checks.equal((effects.left_hand_mode, effects.right_hand_mode),
                     ("occupied", "occupied"), "side_aware_hands")
    else:
        session.turn("I hand you the umbrella.")
        checks.equal(session.effects().hands_mode, "partially_occupied", "transfer_to_companion")
        session.turn("Give me the umbrella.")
        checks.equal(session.effects().hands_mode, "free", "transfer_clears_companion")
        checks.equal(session.effects(target="user").hands_mode, "partially_occupied",
                     "transfer_sets_user")
    if session.effects().hands_mode == "occupied":
        conflict = _validated_response(session, "*Claps both hands brightly.*")
        checks.expect(not conflict.accepted and conflict.category == "hands_occupied_action",
                      "occupied_hand_action_rejected")
    _assert_invariants(session, checks)


_MOBILITY_CASES = (
    ("You're wearing rollerblades.", "Take off the rollerblades.", "skating", "companion"),
    ("You're wearing skates.", "Take off the skates.", "skating", "companion"),
    ("You're wearing skis.", "Take off the skis.", "skating", "companion"),
    ("You get on a bicycle.", "You get off the bicycle.", "cycling", "companion"),
    ("You mount a horse.", "You dismount the horse.", "riding", "companion"),
    ("You get into the car and drive.", "You get out of the car.", "driving", "companion"),
    ("You're in a wheelchair.", "You get out of the wheelchair.", "assisted", "companion"),
    ("You're using crutches.", "You stop using crutches.", "assisted", "companion"),
    ("I'm riding a bicycle.", "I get off the bicycle.", "cycling", "user"),
    ("I'm driving a car.", "I get out of the car.", "driving", "user"),
)


def _run_mobility(session: SyntheticSession, checks: Checks, variant: int) -> None:
    establish, clear, mode, target = _MOBILITY_CASES[variant % 10]
    phase = variant // 10
    if phase == 4:
        session.turn(establish)
        session.turn(f"Let's roleplay that we're in synthetic mobility scene {variant}.")
        checks.equal(session.effects(target=target).locomotion_mode, "walking",
                     "mobility_scope_isolation")
        session.turn("Back to real life.")
    else:
        session.turn(establish)
    effects = session.effects(target=target)
    checks.equal(effects.locomotion_mode, mode, "locomotion_mode")
    checks.expect(all("=true" not in item for item in effects.locomotion_causes),
                  "no_item_flag")
    if phase == 1:
        requirement = _requirement(
            session, "How are you moving?" if target == "companion" else "How am I moving?",
        )
        checks.expect(requirement is not None, "movement_requirement")
        checks.expect(validate_response_requirement(
            requirement, requirement.fallback_dialogue,
        ).accepted, "movement_requirement_fallback")
    elif phase == 2:
        before = session.structural_snapshot()
        session.restart()
        checks.equal(session.structural_snapshot(), before, "mobility_restart")
    elif phase == 3:
        session.turn(clear)
        checks.equal(session.effects(target=target).locomotion_mode, "walking",
                     "mobility_clear")
    elif phase == 5:
        session.turn(establish)
        checks.equal(session.effects(target=target).locomotion_mode, mode,
                     "mobility_reconfirmation")
    if target == "companion" and session.effects().locomotion_mode != "walking":
        conflict = _validated_response(session, "*Walks toward you.*")
        checks.expect(not conflict.accepted and conflict.category == "locomotion_mode_conflict",
                      "walking_conflict")
    _assert_invariants(session, checks)


_SLEEP_ACTIONS = (
    "*Their ears twitch once before their shoulders relax.*",
    "*They curl closer around the blanket and settle.*",
    "*A soft flinch passes through them before stillness returns.*",
    "*They shift onto their side with a sleepy sigh.*",
    "*Their tail flicks once, then rests against the pillow.*",
)


def _run_sleep(session: SyntheticSession, checks: Checks, variant: int) -> None:
    session.turn("Go to sleep.")
    checks.equal(session.activity("companion"), "sleeping", "sleep_state")
    checks.equal(session.effects().awareness_mode, "asleep", "sleep_awareness")
    phase = variant % 4
    if phase == 0:
        decision = classify_interaction_policy(
            session.repository, session.character_id, "Please stay asleep.",
        )
        raw = json.dumps({
            "dialogue": _SLEEP_ACTIONS[variant % len(_SLEEP_ACTIONS)],
            "response_mode": "sleep_reaction", "spoken_content": "",
            "presentation": {"pose": "sleeping", "gaze_mode": "suppressed", "reaction": "settle", "speech_mode": "nonverbal"},
            "companion_action": None, "capability_compliance": ["awareness"],
        })
        reaction = render_sleep_reaction(decision, raw, user_message="Please stay asleep.")
        checks.expect(not reaction.used_fallback and reaction.kind == "nonverbal",
                      "generated_sleep_reaction")
    elif phase == 1:
        decision = classify_interaction_policy(
            session.repository, session.character_id, "*I stroke your hair.*",
        )
        reaction = render_sleep_reaction(
            decision, "malformed", user_message="*I stroke your hair.*", variant=variant,
        )
        checks.expect(reaction.used_fallback and reaction.signature is not None,
                      "sleep_fallback_safe")
    elif phase == 2:
        decision = classify_interaction_policy(
            session.repository, session.character_id, "What is the capital of France?",
        )
        raw = json.dumps({
            "dialogue": "The capital is Paris.", "response_mode": "sleep_reaction",
            "spoken_content": "The capital is Paris.", "presentation": None,
            "companion_action": None, "capability_compliance": ["awareness"],
        })
        reaction = render_sleep_reaction(decision, raw, user_message="What is the capital of France?")
        checks.expect(reaction.used_fallback and reaction.fallback_category in {
            "dialogue_shape", "informative_speech", "awake_behavior",
        }, "awake_answer_rejected")
    else:
        session.turn("Wake up.")
        checks.equal(session.activity("companion"), None, "governed_wake")
        checks.equal(session.effects().awareness_mode, "normal", "wake_restoration")
    _assert_invariants(session, checks)


def _run_compound(session: SyntheticSession, checks: Checks, variant: int) -> None:
    phase = variant % 4
    color = ("black", "blue", "red", "green", "white")[variant % 5]
    if phase == 0:
        session.turn(f"You're wearing a {color} dress, a red scarf, and boots.")
        checks.equal(len(_relation_causes(session, "companion", "wearing")), 3,
                     "compound_outfit")
    elif phase == 1:
        session.turn("You're blindfolded and carrying two boxes.")
        effects = session.effects()
        checks.equal((effects.vision_mode, effects.hands_mode),
                     ("unavailable", "occupied"), "compound_independent_effects")
    elif phase == 2:
        session.turn("You're wearing a red scarf and boots.")
        session.turn("Take off the scarf and put on the blue coat.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.expect("red scarf" not in causes and "blue coat" in causes and "boots" in causes,
                      "atomic_compound_replacement")
    else:
        session.turn(f"You're wearing a {color} dress, maybe a scarf, and boots.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.expect(f"{color} dress" in causes and "boots" in causes,
                      "clear_list_fragments_survive")
        checks.expect(all("maybe" not in item for item in causes), "ambiguous_fragment_abstains")
    _assert_invariants(session, checks)


def _run_scope(session: SyntheticSession, checks: Checks, variant: int) -> None:
    label = f"synthetic scope {variant}"
    session.turn("You're blindfolded.")
    real_snapshot = session.structural_snapshot()
    session.turn(f"Let's roleplay that we're in {label}.")
    checks.equal(session.effects().vision_mode, "available", "rp_does_not_inherit_vision")
    session.turn("I cover your eyes with my hands.")
    checks.equal(set(session.effects().vision_causes), {"user hands"}, "rp_local_vision")
    session.turn("Back to real life.")
    checks.equal(set(session.effects().vision_causes), {"blindfold"}, "real_scope_restore")
    checks.equal(session.structural_snapshot(), real_snapshot, "real_scope_snapshot_restore")
    if variant % 2:
        session.turn(f"Let's roleplay that we're in {label}.")
        checks.equal(set(session.effects().vision_causes), {"user hands"}, "rp_reentry_restore")
    if variant % 3 == 0:
        before = session.structural_snapshot()
        session.restart()
        checks.equal(session.structural_snapshot(), before, "scope_restart")
    _assert_invariants(session, checks)


def _action_envelope(family: str, operation: str, value: str | None) -> str:
    return json.dumps({
        "dialogue": "", "response_mode": "action_decision", "spoken_content": "",
        "presentation": None,
        "companion_action": {"family": family, "operation": operation, "value": value},
        "capability_compliance": [],
    })


def _run_action(session: SyntheticSession, checks: Checks, variant: int) -> None:
    phase = variant % 5
    session.turn("Do whatever you'd like.", assistant="*Makes a synthetic choice.*")
    before = session.structural_snapshot()
    if phase == 0:
        parsed = parse_assistant_response(_action_envelope("activity", "set", "stretching"))
        decision = validate_companion_action_decision(
            parsed, session.repository, session.character_id, session.effects(),
        )
        checks.expect(decision.accepted and decision.plan is not None, "activity_action_accepted")
        checks.equal(session.activity("companion"), None, "assistant_text_not_authority")
        result = session.apply_action(decision.plan)
        checks.equal(result.get("state"), "applied", "action_applied")
        checks.equal(session.activity("companion"), "stretching", "action_state")
    elif phase == 1:
        parsed = parse_assistant_response(_action_envelope("posture", "set", "sitting"))
        decision = validate_companion_action_decision(
            parsed, session.repository, session.character_id, session.effects(),
        )
        checks.expect(decision.accepted and decision.plan is not None, "posture_action_accepted")
        session.apply_action(decision.plan)
        checks.equal(session.posture(), "sitting", "posture_action_state")
    elif phase == 2:
        session.turn("I blindfold you.")
        snapshot = session.structural_snapshot()
        parsed = parse_assistant_response(_action_envelope("activity", "set", "reading"))
        decision = validate_companion_action_decision(
            parsed, session.repository, session.character_id, session.effects(),
        )
        checks.expect(not decision.accepted and decision.category == "action_vision_conflict",
                      "vision_action_rejected")
        checks.equal(session.structural_snapshot(), snapshot, "rejected_action_no_mutation")
    elif phase == 3:
        session.turn("You're holding a cup and a book.")
        snapshot = session.structural_snapshot()
        parsed = parse_assistant_response(_action_envelope("activity", "set", "waving"))
        decision = validate_companion_action_decision(
            parsed, session.repository, session.character_id, session.effects(),
        )
        checks.expect(not decision.accepted and decision.category == "action_hands_conflict",
                      "hands_action_rejected")
        checks.equal(session.structural_snapshot(), snapshot, "hands_rejection_no_mutation")
    else:
        malformed = json.loads(_action_envelope("activity", "set", "stretching"))
        malformed["companion_action"]["target"] = "user"
        decision = validate_companion_action_decision(
            parse_assistant_response(json.dumps(malformed)), session.repository,
            session.character_id, session.effects(),
        )
        checks.expect(not decision.accepted and decision.category == "action_unknown_field",
                      "user_target_action_rejected")
        checks.equal(session.structural_snapshot(), before, "invalid_action_no_mutation")
    _assert_invariants(session, checks)


_BASELINE_ITEMS = (
    "black dress", "blue coat", "white hoodie", "red dress", "green jacket", "gray coat",
)


def _run_baseline(session: SyntheticSession, checks: Checks, variant: int) -> None:
    item = _BASELINE_ITEMS[variant // 5]
    phase = variant % 5
    if phase == 4:
        profile = derive_character_scene_profile(
            {"name": "Synthetic"},
            "CHARACTER PERSONALITY:\nSynthetic normally wears " + item + ".\nCHARACTER CONSISTENCY:",
        )
    else:
        profile = derive_character_scene_profile({
            "name": "Synthetic", "default_scene": {"worn": [item]},
        })
    checks.expect(profile is not None, "profile_derived")
    cache_character_scene_profile(session.repository, session.character_id, profile)
    checks.equal(tuple(value.label for value in effective_profile_worn_items(
        session.repository, session.character_id,
    )), (item,), "baseline_effective")
    if phase == 1:
        session.turn("You're wearing a blue coat.")
        checks.equal(effective_profile_worn_items(session.repository, session.character_id), (),
                     "baseline_override")
    elif phase == 2:
        session.turn("You're wearing a blue coat.")
        session.turn("Change back into your usual clothes.")
        checks.equal(tuple(value.label for value in effective_profile_worn_items(
            session.repository, session.character_id,
        )), (item,), "baseline_restore")
        checks.equal(_relation_causes(session, "companion", "wearing"), set(),
                     "usual_clothes_clear_override")
    elif phase == 3:
        session.turn("You're wearing a blue coat.")
        before = session.structural_snapshot()
        session.restart()
        checks.equal(session.structural_snapshot(), before, "baseline_override_restart")
        checks.equal(effective_profile_worn_items(session.repository, session.character_id), (),
                     "baseline_does_not_reassert")
    requirement = _requirement(session, "What are you wearing?")
    checks.expect(requirement is not None, "baseline_direct_requirement")
    checks.expect(validate_response_requirement(
        requirement, requirement.fallback_dialogue,
    ).accepted, "baseline_requirement_valid")
    _assert_invariants(session, checks)


_DIRECT_CASES = (
    ((), "What time is it?", "It's 1:01 PM."),
    ((), "What day is it?", "It's Friday, August 28, 2026."),
    (("You're wearing a black dress.",), "What are you wearing?", "I'm wearing nothing."),
    (("You're holding two boxes.",), "What are you holding?", "I'm holding nothing."),
    (("Go cook dinner.",), "What are you doing?", "I don't have a current activity."),
    (("I blindfold you.",), "Can you see?", "Yes, I can see."),
    (("I cover your mouth with my hand.",), "Why can't you talk normally?", "I can speak normally."),
    (("You're holding a cup and a book.",), "Are your hands free?", "Both hands are free."),
    (("You're wearing rollerblades.",), "How are you moving?", "I'm walking."),
    (("I'm riding a bicycle.",), "How am I moving?", "You're walking."),
)


def _run_direct(session: SyntheticSession, checks: Checks, variant: int) -> None:
    setup, query, contradiction = _DIRECT_CASES[variant // 4]
    for turn in setup:
        session.turn(turn)
    requirement = _requirement(session, query)
    checks.expect(requirement is not None, "direct_requirement_recognized")
    checks.expect(validate_response_requirement(
        requirement, requirement.fallback_dialogue,
    ).accepted, "deterministic_fallback_valid")
    phase = variant % 4
    if phase == 0:
        checks.expect(not validate_response_requirement(requirement, contradiction).accepted,
                      "contradiction_rejected")
    elif phase == 1:
        before = session.structural_snapshot()
        session.turn(query)
        checks.equal(session.structural_snapshot(), before, "query_does_not_rewrite_state")
    elif phase == 2:
        checks.expect(len(requirement.context_block) <= 1000, "requirement_context_bound")
        checks.expect(all(token not in requirement.context_block for token in (
            "scene-", "event_id", "relation_id", "sha256",
        )), "requirement_has_no_ids")
    else:
        second = _requirement(session, query)
        checks.equal(second, requirement, "provider_neutral_requirement")
    _assert_invariants(session, checks)


def curated_cases() -> tuple[CuratedCase, ...]:
    specs = (
        ("activity", 40, _run_activity),
        ("clothing_equipment", 40, _run_clothing),
        ("vision", 40, _run_vision),
        ("speech", 40, _run_speech),
        ("hands", 40, _run_hands),
        ("mobility", 60, _run_mobility),
        ("sleep_awareness", 40, _run_sleep),
        ("multi_item_compound", 40, _run_compound),
        ("truth_scope", 40, _run_scope),
        ("model_actions", 30, _run_action),
        ("profile_baseline", 30, _run_baseline),
        ("direct_queries", 40, _run_direct),
    )
    cases = []
    for family, count, runner in specs:
        cases.extend(
            CuratedCase(f"{family}-{index:03d}", family, runner, index)
            for index in range(count)
        )
    if len(cases) != 480:
        raise AssertionError("curated Active State matrix must contain exactly 480 sequences")
    return tuple(cases)


def _safe_failure(error: Exception) -> str:
    if isinstance(error, AssertionError) and error.args:
        return str(error.args[0]).split(":", 1)[0][:80]
    return type(error).__name__[:80]


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _report(version: str, results: list[CaseResult], started: float, max_context: int) -> HarnessReport:
    durations = [item.duration_ms for item in results]
    passed = sum(item.passed for item in results)
    return HarnessReport(
        version=version,
        case_count=len(results), passed=passed, failed=len(results) - passed,
        checks=sum(item.checks for item in results),
        turns=sum(item.turns for item in results),
        duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
        median_case_ms=round(statistics.median(durations), 3) if durations else 0.0,
        p95_case_ms=round(_percentile(durations, .95), 3),
        max_context_chars=max_context,
        results=tuple(results),
    )


def run_curated_matrix() -> HarnessReport:
    started = time.perf_counter()
    results: list[CaseResult] = []
    max_context = 0
    for case in curated_cases():
        session = SyntheticSession(case.case_id)
        checks = Checks()
        case_started = time.perf_counter()
        failure = None
        try:
            case.runner(session, checks, case.variant)
        except Exception as error:
            failure = _safe_failure(error)
        duration = (time.perf_counter() - case_started) * 1000.0
        max_context = max(max_context, session.max_context_chars)
        results.append(CaseResult(
            case.case_id, case.family, failure is None, checks.count,
            session.turn_count, round(duration, 3), failure,
        ))
        session.close()
    return _report("active-state-curated-v1", results, started, max_context)


_RANDOM_ESTABLISH = (
    "You're blindfolded.",
    "I cover your eyes with my hands.",
    "Your mouth is full.",
    "I cover your mouth with my hand.",
    "You're holding a cup and a book.",
    "You're carrying a box in one hand.",
    "You're wearing a black dress, a red scarf, and boots.",
    "You're wearing rollerblades.",
    "You get on a bicycle.",
    "You're using crutches.",
    "Go cook dinner.",
    "Sit down.",
)

_RANDOM_CLEAR = (
    "I take my hands away from your eyes.",
    "I take the blindfold off you.",
    "Your mouth is clear now.",
    "I take my hand away from your mouth.",
    "Put the cup down.",
    "Put the book down.",
    "Put the box down.",
    "Take off everything you're wearing.",
    "You get off the bicycle.",
    "You stop using crutches.",
    "Stop cooking dinner.",
    "Stand up.",
)


def _random_operation(
    session: SyntheticSession,
    checks: Checks,
    rng: random.Random,
    seed: int,
    step: int,
) -> None:
    operation = rng.randrange(27)
    if operation == 0:
        session.turn(rng.choice(_RANDOM_ESTABLISH))
    elif operation == 1:
        session.turn(rng.choice(_RANDOM_CLEAR))
    elif operation == 2:
        scope = session.repository.active_truth_scope(session.character_id)
        if scope.kind == "scenario":
            session.turn("Back to real life.")
        else:
            session.turn(
                f"Let's roleplay that we're in synthetic random scene {seed % 3}."
            )
    elif operation == 3:
        before = session.structural_snapshot()
        session.restart()
        checks.equal(session.structural_snapshot(), before, "random_restart_snapshot")
    elif operation == 4:
        session.turn("Go work.")
        session.turn("That was a typo. I meant sleeping.")
        checks.equal(session.activity("companion"), "sleeping", "random_correction")
    elif operation == 5:
        session.turn("I hand you the umbrella.")
        if _relation_causes(session, "companion", "holding") == {"umbrella"}:
            session.turn("Give me the umbrella.")
            checks.expect("umbrella" in _relation_causes(session, "user", "holding"),
                          "random_transfer")
            session.turn("I put the umbrella down.")
            checks.expect("umbrella" not in _relation_causes(session, "user", "holding"),
                          "random_release_new_holder")
            checks.equal(session.effects(target="user").hands_mode, "free",
                         "random_release_restores_user_hands")
    elif operation == 6:
        session.turn("You're blindfolded and carrying two boxes.")
        checks.equal((session.effects().vision_mode, session.effects().hands_mode),
                     ("unavailable", "occupied"), "random_compound")
    elif operation == 7:
        queries = (
            "Can you see?", "What are you wearing?", "Are your hands free?",
            "How are you moving?", "What are you doing?",
        )
        query = rng.choice(queries)
        before = session.structural_snapshot()
        session.turn(query)
        checks.equal(session.structural_snapshot(), before, "random_query_no_mutation")
        requirement = _requirement(session, query)
        checks.expect(requirement is not None, "random_query_requirement")
        checks.expect(validate_response_requirement(
            requirement, requirement.fallback_dialogue,
        ).accepted, "random_query_fallback")
    elif operation == 8:
        parsed = parse_assistant_response(_action_envelope("activity", "set", "stretching"))
        before = session.structural_snapshot()
        decision = validate_companion_action_decision(
            parsed, session.repository, session.character_id, session.effects(),
        )
        if decision.accepted and decision.plan is not None:
            # Bind the governed action to a persisted synthetic assistant turn.
            session.turn("Do whatever you'd like.", assistant="*Begins stretching.*")
            result = session.apply_action(decision.plan)
            checks.expect(result.get("state") in {"applied", "unchanged"},
                          "random_action_apply")
        else:
            checks.equal(session.structural_snapshot(), before, "random_action_rejection")
    elif operation == 9:
        session.turn("You're blindfolded.")
        session.turn("I cover your eyes with my hands.")
        session.turn("I take my hands away from your eyes.")
        causes = set(session.effects().vision_causes)
        checks.expect("blindfold" in causes and "user hands" not in causes,
                      "random_remove_one_cause")
    elif operation == 10:
        session.turn(rng.choice(("Sit down.", "Stand up.", "Lie down.")))
        checks.expect(session.posture() in {"sitting", "standing", "lying"},
                      "random_posture")
    elif operation == 11:
        before = session.structural_snapshot()
        session.turn(
            f"Tell me synthetic joke {seed}-{step}.",
            assistant="I am now flying and changing every state.",
        )
        checks.equal(session.structural_snapshot(), before, "assistant_prose_no_authority")
    elif operation == 12:
        session.turn(rng.choice((
            "The room is pitch black.", "The lights come on.",
            "The music is so loud you can't hear me.", "The music stops.",
            "The smoke makes it hard to see.", "The smoke clears.",
        )))
    elif operation == 13:
        session.turn("You're wearing a blue hat.")
        session.turn(rng.choice((
            "Your blue hat is red now.", "Set the blue hat color to green.",
            "Your blue hat is muddy.",
        )))
    elif operation == 14:
        if rng.randrange(2):
            session.turn("You're wearing a blue hat.")
            session.turn("I replace your blue hat with a red one.")
            worn_hats = [
                relation for relation in session.relations()
                if relation.target == "companion" and relation.predicate == "wearing"
                and relation.cause.endswith("hat")
            ]
            checks.equal(len(worn_hats), 1, "random_atomic_replacement")
        else:
            session.turn("You're wearing boots, a red scarf, and a black dress.")
            session.turn(
                "Take off the boots and the red scarf and put on sneakers and a necklace."
            )
            causes = _relation_causes(session, "companion", "wearing")
            checks.expect(
                {"sneakers", "necklace", "black dress"}.issubset(causes),
                "random_atomic_multi_replacement",
            )
    elif operation == 15:
        session.turn(rng.choice((
            "I cover your left ear.", "I uncover your left ear.",
            "Your left wrist is handcuffed to a pole.", "Release the left handcuff.",
            "Your left arm is missing.",
        )))
    elif operation == 16:
        session.turn("I hand you a red lantern.")
        session.turn("Put the red lantern down.")
        if rng.randrange(2):
            session.turn("Throw the red lantern away.")
            session.turn("Pick up the red lantern.")
    elif operation == 17:
        session.turn(rng.choice((
            "You're using crutches.", "You stop using crutches.",
            "You're in a wheelchair.", "You get out of the wheelchair.",
            "You're wearing rollerblades.", "Take off the rollerblades.",
        )))
    elif operation == 18:
        before = session.structural_snapshot()
        session.turn(rng.choice((
            "What if your left arm were missing?", "Imagine that I blindfold you.",
            "Are you awake?", "Could the music be too loud?",
        )))
        checks.equal(session.structural_snapshot(), before, "random_non_authoritative_abstention")
    elif operation == 19:
        session.turn("You're wearing a green hat.")
        session.turn("Your hat is blue. Sorry, red.")
        causes = _relation_causes(session, "companion", "wearing")
        checks.expect("red hat" in causes and "blue hat" not in causes,
                      "random_ordered_correction")
    elif operation == 20:
        session.turn("You're wearing a left glove and a right glove.")
        session.turn("Your left glove is wet.")
        # Lifecycle history may legitimately contain earlier dormant gloves.
        # The invariant concerns the two authoritative current wearing
        # relations, not every subject in the bounded scene roster.
        gloves = []
        for relation in session.relations():
            if (relation.target != "companion" or relation.predicate != "wearing"
                    or relation.cause_subject_id is None):
                continue
            attributes = {
                row.subject_key.rsplit(".", 1)[-1]: row.value
                for row in session.repository.lookup_scene_attributes(
                    session.character_id, relation.cause_subject_id,
                )
            }
            if attributes.get("kind") == "glove":
                gloves.append(attributes)
        checks.equal(len(gloves), 2, "random_side_identity_count")
        checks.equal({item.get("side") for item in gloves}, {"left", "right"},
                     "random_side_identity_sides")
        checks.equal(sum(item.get("wet") == "true" for item in gloves), 1,
                     "random_side_identity_update")
    elif operation == 21:
        session.turn("The room is pitch black.")
        session.turn("I blindfold you.")
        session.turn("The lights come on.")
        checks.equal(session.effects().vision_mode, "unavailable",
                     "random_environment_attached_composition")
    elif operation == 22:
        session.turn("You're holding a cup.")
        session.turn("Give me the cup.")
        session.turn("I put the cup on the table.")
        checks.equal(session.effects(target="user").hands_mode, "free",
                     "random_user_release")
    elif operation == 23:
        before = session.structural_snapshot()
        session.turn(rng.choice((
            "I'm holding a lantern?",
            "I wonder what would happen if the smoke makes it hard to see.",
            "The wheelchair might be nearby.",
            "You're wearing a hat, or maybe not.",
        )))
        checks.equal(session.structural_snapshot(), before,
                     "random_authority_surface_abstention")
    elif operation == 24:
        session.turn("I hand you a red lantern.")
        session.turn("Put the red lantern down.")
        session.turn("Throw the red lantern away.")
        before = session.structural_snapshot()
        session.restart()
        checks.equal(session.structural_snapshot(), before, "random_retirement_restart")
    elif operation == 25:
        session.turn("You're carrying two boxes.")
        session.turn("Your both arms are missing.")
        checks.equal(session.effects().hands_mode, "unavailable",
                     "random_strongest_manual_restriction")
        session.turn("Your both arms are available again.")
    else:
        session.turn("You're wearing a red hat and a red scarf.")
        before = session.structural_snapshot()
        session.turn("Your red thing is wet.")
        checks.equal(session.structural_snapshot(), before,
                     "random_lexical_overlap_abstention")


def run_randomized_state_machine(
    *, seed_count: int = 300, operations_per_seed: int = 150,
    seed_start: int = 0,
) -> HarnessReport:
    if (not 1 <= seed_count <= 500 or not 1 <= operations_per_seed <= 200
            or isinstance(seed_start, bool) or not isinstance(seed_start, int)
            or seed_start < 0 or seed_start + seed_count > 10_000):
        raise ValueError("randomized Active State QA bounds are invalid")
    started = time.perf_counter()
    results: list[CaseResult] = []
    max_context = 0
    for seed in range(seed_start, seed_start + seed_count):
        case_id = f"random-seed-{seed:03d}"
        session = SyntheticSession(case_id)
        checks = Checks()
        rng = random.Random(0xA1F2E000 + seed)
        case_started = time.perf_counter()
        failure = None
        try:
            for step in range(operations_per_seed):
                _random_operation(session, checks, rng, seed, step)
                _assert_invariants(session, checks)
        except Exception as error:
            failure = _safe_failure(error)
        duration = (time.perf_counter() - case_started) * 1000.0
        max_context = max(max_context, session.max_context_chars)
        results.append(CaseResult(
            case_id, "randomized_state_machine", failure is None, checks.count,
            session.turn_count, round(duration, 3), failure,
        ))
        session.close()
    return _report("active-state-randomized-v4", results, started, max_context)


@dataclass(frozen=True)
class LongContextCase:
    case_id: str
    establish: tuple[str, ...]
    gap_turns: int
    query: str
    expected: Callable[[SyntheticSession, Checks], None]
    clear: tuple[str, ...]
    restored: Callable[[SyntheticSession, Checks], None]


def _expect_vision(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().vision_mode, "unavailable", "long_vision")


def _restore_vision(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().vision_mode, "available", "long_vision_restore")


def _expect_skating(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().locomotion_mode, "skating", "long_skating")


def _expect_cycling(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().locomotion_mode, "cycling", "long_cycling")


def _restore_walking(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().locomotion_mode, "walking", "long_locomotion_restore")


def _expect_speech(session: SyntheticSession, checks: Checks) -> None:
    checks.expect(session.effects().speech_mode != "normal", "long_speech")


def _restore_speech(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().speech_mode, "normal", "long_speech_restore")


def _expect_hands(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().hands_mode, "occupied", "long_hands")


def _restore_hands(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().hands_mode, "free", "long_hands_restore")


def _expect_sleep(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().awareness_mode, "asleep", "long_sleep")


def _restore_sleep(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().awareness_mode, "normal", "long_sleep_restore")


def _expect_two_vision_causes(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(set(session.effects().vision_causes), {"blindfold", "user hands"},
                 "long_multiple_causes")


def _expect_environment_and_attached_vision_causes(
    session: SyntheticSession, checks: Checks,
) -> None:
    checks.equal(session.effects().vision_mode, "unavailable", "long_vision")
    checks.equal(set(session.effects().vision_causes), {"darkness", "blindfold"},
                 "long_environment_attached_causes")


def _restore_one_vision_cause(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(set(session.effects().vision_causes), {"blindfold"},
                 "long_remove_one_cause")


def _expect_hearing(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().hearing_mode, "unavailable", "long_hearing")


def _restore_hearing(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().hearing_mode, "normal", "long_hearing_restore")


def _expect_touch(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().touch_mode, "unavailable", "long_touch")


def _restore_touch(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().touch_mode, "normal", "long_touch_restore")


def _expect_restraint(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().left_arm_mode, "constrained", "long_restraint")


def _restore_restraint(session: SyntheticSession, checks: Checks) -> None:
    checks.equal(session.effects().left_arm_mode, "normal", "long_restraint_restore")


def long_context_cases() -> tuple[LongContextCase, ...]:
    return (
        LongContextCase(
            "touch-environment-turn-50",
            ("The numbness makes it impossible to feel.",), 50,
            "Can you feel anything?", _expect_touch,
            ("The numbness clears.",), _restore_touch,
        ),
        LongContextCase(
            "blindfold-turn-101", ("You're blindfolded.",), 101, "Can you see?",
            _expect_vision, ("I take the blindfold off you.",), _restore_vision,
        ),
        LongContextCase(
            "rollerblades-turn-100", ("You're wearing rollerblades.",), 100,
            "How are you moving?", _expect_skating,
            ("Take off the rollerblades.",), _restore_walking,
        ),
        LongContextCase(
            "mouth-turn-80", ("Your mouth is full.",), 80,
            "Why can't you talk normally?", _expect_speech,
            ("Your mouth is clear now.",), _restore_speech,
        ),
        LongContextCase(
            "hands-turn-75", ("You're holding a cup and a book.",), 75,
            "Are your hands free?", _expect_hands,
            ("Put the cup down.", "Put the book down."), _restore_hands,
        ),
        LongContextCase(
            "sleep-turn-120", ("Go to sleep.",), 120,
            "What are you doing?", _expect_sleep,
            ("Wake up.",), _restore_sleep,
        ),
        LongContextCase(
            "multiple-causes-turn-130",
            ("You're blindfolded.", "I cover your eyes with my hands."), 130,
            "What is covering your eyes?", _expect_two_vision_causes,
            ("I take my hands away from your eyes.",), _restore_one_vision_cause,
        ),
        LongContextCase(
            "bicycle-turn-90", ("You get on a bicycle.",), 90,
            "How are you moving?", _expect_cycling,
            ("You get off the bicycle.",), _restore_walking,
        ),
        LongContextCase(
            "vision-restart-turn-150", ("I cover your eyes with my hands.",), 150,
            "What do you see?", _expect_vision,
            ("I take my hands away from your eyes.",), _restore_vision,
        ),
        LongContextCase(
            "hearing-environment-turn-250",
            ("The music is so loud you can't hear me.",), 250,
            "Can you hear me?", _expect_hearing,
            ("The music stops.",), _restore_hearing,
        ),
        LongContextCase(
            "wrist-restraint-restart-turn-500",
            ("Your left wrist is handcuffed to a pole.",), 500,
            "Are your hands free?", _expect_restraint,
            ("Release the left handcuff.",), _restore_restraint,
        ),
        LongContextCase(
            "multiple-vision-causes-restart-turn-1000",
            ("The room is pitch black.", "You're blindfolded."), 1000,
            "Why can't you see?", _expect_environment_and_attached_vision_causes,
            ("The lights come on.", "Remove the blindfold."), _restore_vision,
        ),
    )


def run_long_context_matrix() -> HarnessReport:
    started = time.perf_counter()
    results: list[CaseResult] = []
    max_context = 0
    for case in long_context_cases():
        session = SyntheticSession(case.case_id)
        checks = Checks()
        case_started = time.perf_counter()
        failure = None
        try:
            for turn in case.establish:
                session.turn(turn)
            for index in range(case.gap_turns):
                session.turn(f"Let's discuss synthetic astronomy topic {index}.")
                if index == case.gap_turns // 2 and "restart" in case.case_id:
                    before = session.structural_snapshot()
                    session.restart()
                    checks.equal(session.structural_snapshot(), before, "long_restart_snapshot")
            checks.expect(
                all(str(row.get("content")) not in case.establish for row in session.rows[-20:]),
                "original_turn_outside_recent_window",
            )
            case.expected(session, checks)
            session.turn(case.query)
            requirement = _requirement(session, case.query)
            if session.effects().awareness_mode != "asleep":
                checks.expect(requirement is not None, "long_direct_requirement")
                checks.expect(validate_response_requirement(
                    requirement, requirement.fallback_dialogue,
                ).accepted, "long_required_fact")
            block = session.context(case.query)
            checks.expect(len(block) <= 2100, "long_context_bound")
            checks.expect(capability_context_block(session.effects()) in block
                          or "companion_capabilities" in block,
                          "long_capability_admitted")
            for turn in case.clear:
                session.turn(turn)
            case.restored(session, checks)
            _assert_invariants(session, checks)
        except Exception as error:
            failure = _safe_failure(error)
        duration = (time.perf_counter() - case_started) * 1000.0
        max_context = max(max_context, session.max_context_chars)
        results.append(CaseResult(
            case.case_id, "long_context", failure is None, checks.count,
            session.turn_count, round(duration, 3), failure,
        ))
        session.close()
    return _report("active-state-long-context-v1", results, started, max_context)


def report_json(report: HarnessReport) -> str:
    """Serialize only structural/numeric synthetic results."""
    return json.dumps(asdict(report), indent=2, sort_keys=True)
