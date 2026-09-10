"""Synthetic large-state performance and context-budget measurement."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import statistics
import time

from benchmarks.active_state.harness import BASE_TIME, SyntheticSession
from capability_policy import capability_context_block, validate_capability_response
from companion_action import validate_companion_action_decision
from presentation_metadata import parse_assistant_response
from response_requirements import derive_response_requirement, validate_response_requirement


@dataclass(frozen=True)
class PerformanceReport:
    version: str
    scene_subject_count: int
    relation_count: int
    database_bytes: int
    context_chars: int
    hard_capability_chars: int
    query_requirement_chars: int
    internal_ids_present: bool
    hard_capabilities_preserved: bool
    write_median_ms: float
    write_p95_ms: float
    scene_read_median_ms: float
    capability_read_median_ms: float
    context_admission_median_ms: float
    context_admission_p95_ms: float
    context_admission_max_ms: float
    response_validation_median_ms: float
    action_validation_median_ms: float
    restart_ms: float
    response_validation_accepted: bool
    action_validation_accepted: bool


_OBJECTS = tuple(
    f"{color} {kind}"
    for color in (
        "black", "blue", "brown", "gold", "green", "orange",
        "pink", "purple", "red", "silver", "white", "yellow",
    )
    for kind in ("umbrella", "lantern", "notebook", "camera", "thermos", "pillow")
)


def _median_call(callback, repeats: int = 200) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else 0.0


def _timed_samples(callback, repeats: int) -> list[float]:
    samples = []
    for _index in range(repeats):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def run() -> PerformanceReport:
    session = SyntheticSession("active-state-performance")
    try:
        # Mention-driven object lifecycle through the real extractor: each
        # object remains in the current scene, but no inventory model exists.
        for item in _OBJECTS:
            session.turn(f"I hand you the {item}.")
            session.turn(f"Put the {item} on the table.")
        session.turn("You're wearing a black dress, a red scarf, and rollerblades.")
        session.turn("You're blindfolded and carrying two boxes.")
        session.turn("Your mouth is full.")

        effects = session.effects()
        hard = capability_context_block(effects)
        query = "How are you moving?"
        requirement = derive_response_requirement(
            session.repository, session.character_id, query,
            local_datetime=BASE_TIME, companion_effects=effects,
            user_effects=session.effects(target="user"),
        )
        if requirement is None:
            raise AssertionError("synthetic performance query requirement was not recognized")
        context = session.context(query)

        response = parse_assistant_response(json.dumps({
            "dialogue": "*Tilts their head toward the sound.*",
            "response_mode": "speech_constrained", "spoken_content": "Mmph.",
            "presentation": None, "companion_action": None,
            "capability_compliance": ["perception", "communication", "manipulation", "locomotion"],
        }))
        action = parse_assistant_response(json.dumps({
            "dialogue": "", "response_mode": "action_decision", "spoken_content": "",
            "presentation": None,
            "companion_action": {"family": "activity", "operation": "set", "value": "stretching"},
            "capability_compliance": [],
        }))

        response_result = validate_capability_response(response, effects)
        action_result = validate_companion_action_decision(
            action, session.repository, session.character_id, effects,
        )
        scene_read = _median_call(
            lambda: session.repository.list_scene_relations(session.character_id, limit=32),
        )
        capability_read = _median_call(
            lambda: session.repository.capability_effects(session.character_id),
        )
        context_samples = _timed_samples(lambda: session.context(query), repeats=100)
        context_read = statistics.median(context_samples)
        response_read = _median_call(
            lambda: validate_capability_response(response, effects), repeats=1_000,
        )
        action_read = _median_call(
            lambda: validate_companion_action_decision(
                action, session.repository, session.character_id, effects,
            ),
            repeats=500,
        )
        before = session.structural_snapshot()
        restart_started = time.perf_counter()
        session.restart()
        restart_ms = (time.perf_counter() - restart_started) * 1000.0
        if session.structural_snapshot() != before:
            raise AssertionError("large-state restart changed authoritative snapshot")

        subjects = session.repository.list_scene_subjects(session.character_id, limit=128)
        relations = session.repository.list_scene_relations(session.character_id, limit=96)
        db_path = session.writer.database_path
        return PerformanceReport(
            "active-state-performance-v2", len(subjects), len(relations),
            db_path.stat().st_size if db_path.exists() else 0,
            len(context), len(hard), len(requirement.context_block),
            any(token in context for token in ("scene-", "relation_id", "event_id", "sha256")),
            all(token in context for token in (
                '"vision":"unavailable"', '"speech":"constrained"',
                '"hands":"occupied"', '"mode":"skating"',
            )),
            round(statistics.median(session.turn_latencies_ms), 4),
            round(_p95(session.turn_latencies_ms), 4),
            round(scene_read, 4), round(capability_read, 4), round(context_read, 4),
            round(_p95(context_samples), 4), round(max(context_samples), 4),
            round(response_read, 4), round(action_read, 4), round(restart_ms, 4),
            bool(response_result.accepted and validate_response_requirement(
                requirement, requirement.fallback_dialogue,
            ).accepted),
            bool(action_result.accepted),
        )
    finally:
        session.close()


def main() -> None:
    print(json.dumps(asdict(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
