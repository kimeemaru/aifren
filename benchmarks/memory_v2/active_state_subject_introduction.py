"""Frozen semantic precision experiment for sparse Active State subjects.

This is intentionally a prompt-contract comparison only.  It neither changes
the governed proposal contract nor writes provider output to production state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from aifren.memory_v2_store import ACTIVE_STATE_ACTORS, ACTIVE_STATE_ACTOR_ATTRIBUTES, ACTIVE_STATE_SCENE_ATTRIBUTES

from .active_state_contextual_extraction import (
    ContextualScenario, ParsedCandidate, SceneSubject, _LOCAL_REF, _native_schema_provider_response,
    _required_operation, _synthetic_scene_id, evaluate_candidates, parse_provider_output_isolated,
)


_MANIFEST = Path(__file__).with_name("active_state_subject_introduction_manifest.json")
INTRODUCTION_GUIDANCE = """
Sparse subject-introduction rule: before introducing a new scene subject, ask whether the
mention is adequately represented as an actor/global attribute value or an existing subject's
attribute. If yes, use that value/update and do not introduce a subject. Introduce only a
concrete currently relevant entity that needs independent evolving attributes or later local
reference continuity. Locations, furniture, and incidental scenery are normally values, not
subjects. When identity continuity is unclear, withhold the introduction. This rule does not
authorize any unmentioned fact or consequence.
""".strip()


@dataclass(frozen=True)
class IntroductionMetrics:
    required_introductions: int
    correct_introductions: int
    missed_introductions: int
    unnecessary_introductions: int
    existing_subject_updates_required: int
    existing_subject_updates_correct: int
    actor_updates_required: int
    actor_updates_correct: int
    false_omissions: int
    hard_safety_failures: int
    contract_invalid: int


@dataclass(frozen=True)
class IntroductionPolicyReport:
    name: str
    provider_calls: int
    latency_ms: float
    response_sha256: str
    candidates: dict[str, ParsedCandidate]
    failures: dict[str, str]
    traces: tuple[Any, ...]
    metrics: IntroductionMetrics


def load_subject_introduction_manifest(path: Path = _MANIFEST) -> tuple[ContextualScenario, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != "active-state-subject-introduction-v1":
        raise ValueError("invalid subject-introduction manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or not 20 <= len(rows) <= 30:
        raise ValueError("subject-introduction manifest must contain 20-30 cases")
    cases: list[ContextualScenario] = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("case_id", "category", "turn")):
            raise ValueError("subject-introduction case is malformed")
        scene = row.get("scene", {})
        if not isinstance(scene, dict):
            raise ValueError("subject-introduction scene is malformed")
        actors = scene.get("actors", {})
        if not isinstance(actors, dict) or any(
            actor not in ACTIVE_STATE_ACTORS or not isinstance(attributes, dict)
            or any(attribute not in ACTIVE_STATE_ACTOR_ATTRIBUTES or not isinstance(value, str) for attribute, value in attributes.items())
            for actor, attributes in actors.items()
        ):
            raise ValueError("subject-introduction actor snapshot is invalid")
        subjects: list[SceneSubject] = []
        for item in scene.get("subjects", []):
            if not isinstance(item, dict) or not isinstance(item.get("ref"), str) or _LOCAL_REF.fullmatch(item["ref"]) is None:
                raise ValueError("subject-introduction scene reference is invalid")
            if not isinstance(item.get("kind"), str) or not isinstance(item.get("attributes", {}), dict):
                raise ValueError("subject-introduction subject is malformed")
            attributes = item.get("attributes", {})
            if any(attribute not in ACTIVE_STATE_SCENE_ATTRIBUTES or not isinstance(value, str) for attribute, value in attributes.items()):
                raise ValueError("subject-introduction attribute snapshot is invalid")
            subjects.append(SceneSubject(item["ref"], _synthetic_scene_id(item["ref"]), item["kind"], dict(attributes)))
        if len({subject.reference for subject in subjects}) != len(subjects) or len(subjects) > 8:
            raise ValueError("subject-introduction scene roster is invalid")
        required = tuple(_required_operation(item) for item in row.get("required", []))
        reject = row.get("reject_mutation", False)
        if not isinstance(reject, bool) or (reject and required):
            raise ValueError("subject-introduction rejection expectation is invalid")
        cases.append(ContextualScenario(row["case_id"], row["category"], row["turn"], None, dict(actors), tuple(subjects), required, reject))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("subject-introduction IDs must be unique")
    expected_categories = {"context_value", "existing_subject", "actor_preservation", "required_subject", "required_subject_contract_gap", "incidental", "ambiguous", "environment_gap"}
    if {case.category for case in cases} != expected_categories:
        raise ValueError("subject-introduction category coverage changed")
    return tuple(cases)


def _operation_key(item: Any) -> tuple[str, str | None, str, str | None, str, str | None]:
    return item.target, item.attribute, item.operation, item.value, item.basis, item.rule


def _expected_operations(case: ContextualScenario) -> set[tuple[str, str | None, str, str | None, str, str | None]]:
    return {(
        item["target"], item.get("attribute"), item["operation"], item.get("value"), item["basis"], item.get("rule"),
    ) for item in case.required}


def _metrics(cases: tuple[ContextualScenario, ...], candidates: dict[str, ParsedCandidate], traces: tuple[Any, ...]) -> IntroductionMetrics:
    required_introductions = correct_introductions = missed_introductions = unnecessary_introductions = 0
    existing_required = existing_correct = actor_required = actor_correct = 0
    for case in cases:
        expected = _expected_operations(case)
        actual = {_operation_key(item) for item in candidates.get(case.case_id, ParsedCandidate(None, ())).operations}
        expected_new = {item for item in expected if item[0].startswith("new:") and item[1] == "kind"}
        actual_new = {item for item in actual if item[0].startswith("new:") and item[1] == "kind"}
        required_introductions += len(expected_new)
        correct_introductions += len(expected_new & actual_new)
        missed_introductions += len(expected_new - actual_new)
        unnecessary_introductions += len(actual_new - expected_new)
        expected_existing = {item for item in expected if item[0].startswith("scene:")}
        expected_actor = {item for item in expected if item[0].startswith("actor:")}
        existing_required += len(expected_existing)
        existing_correct += len(expected_existing & actual)
        actor_required += len(expected_actor)
        actor_correct += len(expected_actor & actual)
    return IntroductionMetrics(
        required_introductions, correct_introductions, missed_introductions, unnecessary_introductions,
        existing_required, existing_correct, actor_required, actor_correct,
        sum(trace.classification == "false_omission" for trace in traces),
        sum(trace.classification == "hard_safety_failure" for trace in traces),
        sum(trace.classification == "malformed" for trace in traces),
    )


def run_subject_introduction_policy(
    name: str, instructions: str, *, provider: Any | None = None,
) -> IntroductionPolicyReport:
    """One fixed no-retry run of the focused frozen suite."""
    from aifren.llm.gemini import Gemini

    cases = load_subject_introduction_manifest()
    provider = provider or Gemini()
    candidates: dict[str, ParsedCandidate] = {}
    failures: dict[str, str] = {}
    digests: list[str] = []
    latency = 0.0
    for start in range(0, len(cases), 10):
        batch = cases[start:start + 10]
        response, elapsed = _native_schema_provider_response(provider, batch, instructions)
        latency += elapsed
        digests.append(hashlib.sha256(response.encode("utf-8")).hexdigest())
        parsed = parse_provider_output_isolated(batch, response)
        candidates.update(parsed.candidates)
        failures.update(parsed.failures)
    evaluated = evaluate_candidates(cases, candidates, failures)
    return IntroductionPolicyReport(
        name, 3, latency, hashlib.sha256("".join(digests).encode("ascii")).hexdigest(),
        candidates, failures, evaluated.traces, _metrics(cases, candidates, evaluated.traces),
    )


def revised_instructions(base: str) -> str:
    return f"{base}\n\n{INTRODUCTION_GUIDANCE}"


if __name__ == "__main__":
    from .active_state_contextual_extraction import _EXTRACTION_INSTRUCTIONS

    baseline = run_subject_introduction_policy("baseline", _EXTRACTION_INSTRUCTIONS)
    revised = run_subject_introduction_policy("revised", revised_instructions(_EXTRACTION_INSTRUCTIONS))
    print(json.dumps({"baseline": baseline.metrics.__dict__, "revised": revised.metrics.__dict__}, indent=2))
