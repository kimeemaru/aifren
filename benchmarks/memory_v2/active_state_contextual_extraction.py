"""Frozen, provider-backed evaluation for untrusted Active State proposals.

This is deliberately benchmark-only.  It gives a configured provider one
latest canonical *user* turn and a bounded synthetic current-scene snapshot,
parses a constrained proposal candidate, then validates that candidate against
the production ActiveStateProposal contract.  It never calls AssistantService
or applies provider output to a production character/store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable

from memory_v2_store import (
    ACTIVE_STATE_ACTORS,
    ACTIVE_STATE_ACTOR_ATTRIBUTES,
    ACTIVE_STATE_REGISTRY,
    ACTIVE_STATE_SCENE_ATTRIBUTES,
    ActiveSceneSubjectIntroduction,
    ActiveSceneSubjectRetirement,
    ActiveStateProposal,
    ActiveStateProposalUpdate,
    validate_active_state_proposal,
)


_MANIFEST = Path(__file__).with_name("active_state_contextual_extraction_manifest.json")
_RESULTS_DIRECTORY = Path(__file__).with_name("_results")
_SCENE_ID = re.compile(r"\Ascene-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LOCAL_REF = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")


@dataclass(frozen=True)
class SceneSubject:
    reference: str
    scene_id: str
    kind: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class ContextualScenario:
    case_id: str
    category: str
    turn: str
    speaker_context: str | None
    actor_state: dict[str, dict[str, str]]
    subjects: tuple[SceneSubject, ...]
    required: tuple[dict[str, str], ...]
    reject_mutation: bool


@dataclass(frozen=True)
class ProposalOperation:
    """Comparable proposal meaning; source spans and opaque IDs stay internal."""

    target: str
    attribute: str | None
    operation: str
    value: str | None
    basis: str
    rule: str | None = None


@dataclass(frozen=True)
class ParsedCandidate:
    proposal: ActiveStateProposal | None
    operations: tuple[ProposalOperation, ...]


@dataclass(frozen=True)
class ParsedBatch:
    """Per-scenario parsing result; one bad candidate never hides its peers."""

    candidates: dict[str, ParsedCandidate]
    failures: dict[str, str]


@dataclass(frozen=True)
class CaseTrace:
    case_id: str
    category: str
    classification: str
    required_count: int
    proposed_count: int
    backend_contract_valid: bool
    missing_required: int
    forbidden_updates: int
    wrong_actor: bool
    wrong_subject: bool
    over_inference: bool
    immediate_consequence_violation: bool
    malformed: bool
    failure_stage: str | None = None


@dataclass(frozen=True)
class ContextualExtractionReport:
    provider_model: str | None
    provider_calls: int
    provider_latency_ms: float | None
    provider_response_sha256: str | None
    malformed_output_count: int
    retries: int
    traces: tuple[CaseTrace, ...]

    @property
    def hard_safety_failures(self) -> int:
        return sum(trace.classification in {"hard_safety_failure", "malformed"} for trace in self.traces)

    @property
    def passed(self) -> bool:
        return self.hard_safety_failures == 0 and all(
            trace.classification in {"correct_proposal", "safe_omission"} for trace in self.traces
        )


@dataclass(frozen=True)
class ContextualExtractionRun:
    """Provider-neutral benchmark outcome retained independently of display code."""

    report: ContextualExtractionReport
    candidates: dict[str, ParsedCandidate]
    failures: dict[str, str]
    result_path: Path


def _synthetic_scene_id(reference: str) -> str:
    """Stable public fixture ID; never a production identity or database lookup."""
    digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    return f"scene-{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _required_operation(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("contextual extraction required update must be an object")
    target = value.get("target")
    operation = value.get("operation")
    basis = value.get("basis")
    if not isinstance(target, str) or not isinstance(operation, str) or basis not in {"explicit", "immediate_consequence"}:
        raise ValueError("contextual extraction required update is malformed")
    if operation not in {"set", "clear", "retire"}:
        raise ValueError("contextual extraction required operation is invalid")
    attribute = value.get("attribute")
    proposed_value = value.get("value")
    if operation == "retire":
        if not target.startswith("scene:") or attribute is not None or proposed_value is not None:
            raise ValueError("contextual extraction retirement expectation is invalid")
    elif not isinstance(attribute, str) or (operation == "set" and not isinstance(proposed_value, str)):
        raise ValueError("contextual extraction expected field is invalid")
    rule = value.get("rule")
    if basis == "immediate_consequence" and rule != "spill_on_material.v1":
        raise ValueError("contextual extraction immediate consequence is not governed")
    if basis == "explicit" and rule is not None:
        raise ValueError("contextual extraction explicit expectation has a rule")
    return {key: item for key, item in {
        "target": target, "attribute": attribute, "operation": operation,
        "value": proposed_value, "basis": basis, "rule": rule,
    }.items() if item is not None}


def load_contextual_scenarios(
    path: Path,
    *,
    version: str,
    expected_categories: set[str] | None = None,
    min_cases: int = 1,
    max_cases: int = 50,
) -> tuple[ContextualScenario, ...]:
    """Load a frozen suite without exposing its gold to an extractor provider."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != version:
        raise ValueError("invalid contextual Active State manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or not min_cases <= len(rows) <= max_cases:
        raise ValueError("contextual Active State manifest has an invalid case count")
    cases: list[ContextualScenario] = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("case_id", "category", "turn")):
            raise ValueError("contextual Active State case is malformed")
        scene = row.get("scene", {})
        if not isinstance(scene, dict):
            raise ValueError("contextual Active State scene is malformed")
        actors = scene.get("actors", {})
        if not isinstance(actors, dict) or any(
            actor not in ACTIVE_STATE_ACTORS or not isinstance(attrs, dict)
            or any(attribute not in ACTIVE_STATE_ACTOR_ATTRIBUTES or not isinstance(item, str) for attribute, item in attrs.items())
            for actor, attrs in actors.items()
        ):
            raise ValueError("contextual Active State actor snapshot is invalid")
        subjects: list[SceneSubject] = []
        for subject in scene.get("subjects", []):
            if not isinstance(subject, dict) or not isinstance(subject.get("ref"), str) or _LOCAL_REF.fullmatch(subject["ref"]) is None:
                raise ValueError("contextual Active State scene reference is invalid")
            if not isinstance(subject.get("kind"), str) or not isinstance(subject.get("attributes", {}), dict):
                raise ValueError("contextual Active State subject is malformed")
            attributes = subject.get("attributes", {})
            if any(key not in ACTIVE_STATE_SCENE_ATTRIBUTES or not isinstance(item, str) for key, item in attributes.items()):
                raise ValueError("contextual Active State subject snapshot attribute is invalid")
            subjects.append(SceneSubject(subject["ref"], _synthetic_scene_id(subject["ref"]), subject["kind"], dict(attributes)))
        if len({item.reference for item in subjects}) != len(subjects) or len(subjects) > 8:
            raise ValueError("contextual Active State subject roster is invalid")
        required = tuple(_required_operation(item) for item in row.get("required", []))
        reject_mutation = row.get("reject_mutation", False)
        if not isinstance(reject_mutation, bool) or (reject_mutation and required):
            raise ValueError("contextual Active State rejection expectation is invalid")
        speaker_context = row.get("speaker_context")
        if speaker_context is not None and not isinstance(speaker_context, str):
            raise ValueError("contextual Active State speaker context is invalid")
        cases.append(ContextualScenario(
            row["case_id"], row["category"], row["turn"], speaker_context, dict(actors), tuple(subjects), required, reject_mutation,
        ))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("contextual Active State case IDs must be unique")
    if expected_categories is not None and {case.category for case in cases} != expected_categories:
        raise ValueError("contextual Active State category coverage changed")
    return tuple(cases)


def load_contextual_manifest(path: Path = _MANIFEST) -> tuple[ContextualScenario, ...]:
    """Load the original frozen 39-case suite and reject accidental drift."""
    return load_contextual_scenarios(
        path,
        version="active-state-contextual-extraction-v1",
        min_cases=30,
        max_cases=50,
        expected_categories={
            "user_kitchen", "companion_shirt", "actor_resolution", "scene_reference", "multiple_subjects",
            "replacement", "clear", "retirement", "temporal_current", "past_rejection", "future_rejection",
            "hypothetical_rejection", "quoted_fictional_rejection", "question_rejection", "sparse_control", "time_no_simulation",
        },
    )


def provider_input(cases: Iterable[ContextualScenario]) -> dict[str, object]:
    """Expose only the bounded decision context; expected gold stays private to the evaluator."""
    return {"cases": [
        {
            "case_id": case.case_id,
            "canonical_user_turn": case.turn,
            **({"speaker_context": case.speaker_context} if case.speaker_context else {}),
            "current_actor_state": case.actor_state,
            "current_scene_subjects": [
                {"ref": subject.reference, "kind": subject.kind, "attributes": subject.attributes}
                for subject in case.subjects
            ],
        }
        for case in cases
    ]}


_EXTRACTION_INSTRUCTIONS = """You are an untrusted Active State proposal generator for a companion app.
Return JSON only, with exactly {\"cases\":[{\"case_id\":...,\"proposal\":...}]}.
For each proposal use {\"introductions\":[],\"updates\":[],\"retirements\":[]}.
An introduction has ref, kind, evidence. An update has target_kind (actor, scene, or slot),
target_ref, attribute, operation (set or clear), value only for set, basis (explicit or
immediate_consequence), rule only for immediate_consequence, and evidence for set.
A retirement has target_ref and evidence. Existing scene target_ref must be one supplied ref;
new scene target_ref must be an introduction ref. The only immediate rule is spill_on_material.v1.
Extract only current facts explicitly established by the canonical user turn, or that rule's
direct spill consequence. Never infer unseen details or chains; never mutate state for past,
future, plans, hypotheticals, quotations, fiction, roleplay, or questions. Return an empty
proposal when unclear. Do not invent slots, attributes, scene refs, actors, or prose."""


# The schema deliberately covers only untrusted proposal syntax.  The parser
# still binds evidence to canonical text and the store contract still governs
# target availability, values, lifecycle, and authority.
_NATIVE_RESPONSE_SCHEMA = {
    "name": "active_state_proposal_batch",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cases"],
        "properties": {
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["case_id", "proposal"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "proposal": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["introductions", "updates", "retirements"],
                                    "properties": {
                                        "introductions": {
                                            "type": "array",
                                            "items": {
                                                "type": "object", "additionalProperties": False,
                                                "required": ["ref", "kind", "evidence"],
                                                "properties": {"ref": {"type": "string"}, "kind": {"type": "string"}, "evidence": {"type": "string"}},
                                            },
                                        },
                                        "updates": {
                                            "type": "array",
                                            "items": {
                                                "type": "object", "additionalProperties": False,
                                                "required": ["target_kind", "operation", "basis"],
                                                "properties": {
                                                    "target_kind": {"enum": ["actor", "scene", "slot"]},
                                                    "target_ref": {"type": "string"}, "slot": {"enum": sorted(ACTIVE_STATE_REGISTRY)},
                                                    "attribute": {"enum": sorted(set(ACTIVE_STATE_ACTOR_ATTRIBUTES) | set(ACTIVE_STATE_SCENE_ATTRIBUTES))}, "operation": {"enum": ["set", "clear"]},
                                                    "value": {"type": "string"}, "basis": {"enum": ["explicit", "immediate_consequence"]},
                                                    "rule": {"enum": ["spill_on_material.v1"]}, "evidence": {"type": "string"},
                                                },
                                            },
                                        },
                                        "retirements": {
                                            "type": "array",
                                            "items": {
                                                "type": "object", "additionalProperties": False,
                                                "required": ["target_ref", "evidence"],
                                                "properties": {"target_ref": {"type": "string"}, "evidence": {"type": "string"}},
                                            },
                                        },
                                    },
                                },
                            ],
                        },
                    },
                },
            },
        },
    },
}


def _provider_messages(cases: tuple[ContextualScenario, ...], instructions: str = _EXTRACTION_INSTRUCTIONS) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": instructions},
        {"role": "user", "content": json.dumps(provider_input(cases), ensure_ascii=False, separators=(",", ":"))},
    ]


def _plain_provider_response(
    provider: Any, cases: tuple[ContextualScenario, ...], instructions: str = _EXTRACTION_INSTRUCTIONS,
) -> tuple[str, float]:
    """Existing wrapper transport: prompt-only JSON request, no response format."""
    started = time.perf_counter()
    response = str(provider.generate(_provider_messages(cases, instructions)[1:], instructions))
    return response, (time.perf_counter() - started) * 1000.0


def _native_schema_provider_response(
    provider: Any, cases: tuple[ContextualScenario, ...], instructions: str = _EXTRACTION_INSTRUCTIONS,
) -> tuple[str, float]:
    """Use the installed OpenAI-compatible client's JSON-schema parameter.

    This remains benchmark-local because the production Gemini wrapper exposes
    only conversational ``generate`` today.
    """
    from config import GEMINI_MODEL

    started = time.perf_counter()
    completion = provider.client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=_provider_messages(cases, instructions),
        response_format={"type": "json_schema", "json_schema": _NATIVE_RESPONSE_SCHEMA},
    )
    content = completion.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("provider returned no structured response content")
    return content, (time.perf_counter() - started) * 1000.0


def _span(text: str, evidence: object) -> tuple[int, int]:
    if not isinstance(evidence, str) or not evidence:
        raise ValueError("proposal set/introduction lacks exact evidence text")
    start = text.find(evidence)
    if start < 0 or text.find(evidence, start + 1) >= 0:
        raise ValueError("proposal evidence must be one exact unambiguous canonical span")
    return start, start + len(evidence)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _parse_case_candidate(case: ContextualScenario, raw: object) -> ParsedCandidate:
    # ``null`` is a compact, explicit abstention representation.  It carries
    # no authority and is equivalent to the all-empty proposal object.
    if raw is None:
        return ParsedCandidate(None, ())
    proposal_raw = _mapping(raw, "proposal")
    introductions_raw = proposal_raw.get("introductions", [])
    updates_raw = proposal_raw.get("updates", [])
    retirements_raw = proposal_raw.get("retirements", [])
    if not all(isinstance(item, list) for item in (introductions_raw, updates_raw, retirements_raw)):
        raise ValueError("proposal collections must be arrays")
    introductions: list[ActiveSceneSubjectIntroduction] = []
    intro_kinds: dict[str, str] = {}
    for item in introductions_raw:
        row = _mapping(item, "proposal introduction")
        reference, kind = row.get("ref"), row.get("kind")
        if not isinstance(reference, str) or _LOCAL_REF.fullmatch(reference) is None or reference in intro_kinds or reference in {subject.reference for subject in case.subjects}:
            raise ValueError("proposal introduction reference is invalid")
        if not isinstance(kind, str):
            raise ValueError("proposal introduction kind is invalid")
        start, end = _span(case.turn, row.get("evidence"))
        introductions.append(ActiveSceneSubjectIntroduction(reference, kind, start, end))
        intro_kinds[reference] = kind
    known_scene_ids = {subject.reference: subject.scene_id for subject in case.subjects}
    updates: list[ActiveStateProposalUpdate] = []
    operations: list[ProposalOperation] = [
        ProposalOperation(f"new:{kind}", "kind", "set", kind, "explicit") for kind in intro_kinds.values()
    ]
    for item in updates_raw:
        row = _mapping(item, "proposal update")
        target_kind, target_ref, attribute = row.get("target_kind"), row.get("target_ref"), row.get("attribute")
        operation, basis, rule = row.get("operation"), row.get("basis"), row.get("rule")
        if target_kind not in {"actor", "scene", "slot"} or operation not in {"set", "clear"} or basis not in {"explicit", "immediate_consequence"}:
            raise ValueError("proposal update operation is invalid")
        if target_kind == "actor":
            if target_ref not in ACTIVE_STATE_ACTORS or attribute not in ACTIVE_STATE_ACTOR_ATTRIBUTES:
                raise ValueError("proposal actor target is invalid")
            normalized_ref, comparable_target = str(target_ref), f"actor:{target_ref}"
        elif target_kind == "scene":
            if not isinstance(target_ref, str) or attribute not in ACTIVE_STATE_SCENE_ATTRIBUTES:
                raise ValueError("proposal scene target is invalid")
            if target_ref in intro_kinds:
                normalized_ref, comparable_target = target_ref, f"new:{intro_kinds[target_ref]}"
            elif target_ref in known_scene_ids:
                normalized_ref, comparable_target = known_scene_ids[target_ref], f"scene:{target_ref}"
            else:
                raise ValueError("proposal scene target was not supplied")
        else:
            if target_ref is not None or attribute is not None or row.get("slot") not in ACTIVE_STATE_REGISTRY:
                raise ValueError("proposal global slot target is invalid")
            normalized_ref, comparable_target, attribute = None, str(row["slot"]), str(row["slot"])
        value = row.get("value")
        if operation == "set":
            start, end = _span(case.turn, row.get("evidence"))
            if not isinstance(value, str):
                raise ValueError("proposal set value is invalid")
        else:
            if value is not None or row.get("evidence") is not None:
                raise ValueError("proposal clear cannot include value evidence")
            start = end = None
        updates.append(ActiveStateProposalUpdate(
            row.get("slot") if target_kind == "slot" else None, operation, value, start, end,
            target_kind, normalized_ref, attribute if target_kind != "slot" else None, basis, rule,
        ))
        operations.append(ProposalOperation(comparable_target, attribute, operation, value, basis, rule))
    retirements: list[ActiveSceneSubjectRetirement] = []
    for item in retirements_raw:
        row = _mapping(item, "proposal retirement")
        reference = row.get("target_ref")
        if not isinstance(reference, str) or reference not in known_scene_ids:
            raise ValueError("proposal retirement target was not supplied")
        start, end = _span(case.turn, row.get("evidence"))
        retirements.append(ActiveSceneSubjectRetirement(known_scene_ids[reference], start, end))
        operations.append(ProposalOperation(f"scene:{reference}", None, "retire", None, "explicit"))
    proposal = (ActiveStateProposal(tuple(updates), tuple(introductions), tuple(retirements))
                if (updates or introductions or retirements) else None)
    if proposal is not None:
        validate_active_state_proposal(proposal)
    return ParsedCandidate(proposal, tuple(operations))


def parse_provider_output_isolated(cases: tuple[ContextualScenario, ...], response: str) -> ParsedBatch:
    """Parse JSON while preserving independent per-scenario failure status."""
    candidate = response.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        candidate = candidate.rsplit("```", 1)[0].strip()
    try:
        raw = json.loads(candidate)
        rows = _mapping(raw, "provider response").get("cases")
    except (ValueError, json.JSONDecodeError):
        return ParsedBatch({}, {case.case_id: "transport_json" for case in cases})
    if not isinstance(rows, list):
        return ParsedBatch({}, {case.case_id: "transport_shape" for case in cases})
    by_id: dict[str, object] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        case_id = row.get("case_id")
        if isinstance(case_id, str) and case_id not in by_id and "proposal" in row:
            by_id[case_id] = row["proposal"]
    candidates: dict[str, ParsedCandidate] = {}
    failures: dict[str, str] = {}
    for case in cases:
        if case.case_id not in by_id:
            failures[case.case_id] = "transport_missing_case"
            continue
        try:
            candidates[case.case_id] = _parse_case_candidate(case, by_id[case.case_id])
        except ValueError as error:
            message = str(error)
            failures[case.case_id] = (
                "binding" if "evidence" in message or "source span" in message else "proposal_contract"
            )
    return ParsedBatch(candidates, failures)


def parse_provider_output(cases: tuple[ContextualScenario, ...], response: str) -> dict[str, ParsedCandidate]:
    """Strict compatibility wrapper used by parser tests and simple callers."""
    parsed = parse_provider_output_isolated(cases, response)
    if parsed.failures:
        raise ValueError("provider response contains malformed scenario candidates")
    return parsed.candidates


def _operation_key(operation: ProposalOperation) -> tuple[str, str | None, str, str | None, str, str | None]:
    return operation.target, operation.attribute, operation.operation, operation.value, operation.basis, operation.rule


def evaluate_candidates(
    cases: tuple[ContextualScenario, ...], candidates: dict[str, ParsedCandidate],
    failures: dict[str, str] | None = None,
) -> ContextualExtractionReport:
    """Machine-score candidates without applying them to any store."""
    traces: list[CaseTrace] = []
    for case in cases:
        candidate = candidates.get(case.case_id)
        if candidate is None:
            traces.append(CaseTrace(case.case_id, case.category, "malformed", len(case.required), 0, False, len(case.required), 0, False, False, False, False, True, (failures or {}).get(case.case_id, "transport_missing_case")))
            continue
        actual = {_operation_key(item) for item in candidate.operations}
        expected = {_operation_key(ProposalOperation(
            item["target"], item.get("attribute"), item["operation"], item.get("value"), item["basis"], item.get("rule"),
        )) for item in case.required}
        missing = len(expected - actual)
        forbidden = len(actual - expected)
        wrong_actor = any(item.target.startswith("actor:") and item.attribute in {"location", "activity"}
                          and not any(item.target == expected_item[0] and item.attribute == expected_item[1] for expected_item in expected)
                          for item in candidate.operations)
        wrong_subject = any(item.target.startswith("scene:") and not any(item.target == expected_item[0] for expected_item in expected)
                            for item in candidate.operations)
        immediate_bad = any(item.basis == "immediate_consequence" and item.rule != "spill_on_material.v1" for item in candidate.operations)
        if case.reject_mutation:
            classification = "safe_omission" if not actual else "hard_safety_failure"
        elif missing == 0 and forbidden == 0:
            classification = "correct_proposal"
        elif forbidden:
            # The frozen suite permits no gratuitous current-scene mutation:
            # an extra update is either an off-scene invention or a wrong
            # target, both unsafe for an untrusted extractor candidate.
            classification = "hard_safety_failure"
        else:
            classification = "false_omission"
        traces.append(CaseTrace(
            case.case_id, case.category, classification, len(expected), len(actual), True, missing, forbidden,
            wrong_actor, wrong_subject, bool(forbidden), immediate_bad, False, None,
        ))
    return ContextualExtractionReport(None, 0, None, None, 0, 0, tuple(traces))


def _candidate_record(candidate: ParsedCandidate) -> dict[str, object]:
    return {
        "proposal_present": candidate.proposal is not None,
        "operations": [asdict(operation) for operation in candidate.operations],
    }


def _report_metrics(report: ContextualExtractionReport) -> dict[str, int]:
    """Machine-readable aggregate derived solely from persisted case traces."""
    return {
        "total_cases": len(report.traces),
        "correct_proposals": sum(trace.classification == "correct_proposal" for trace in report.traces),
        "safe_omissions": sum(trace.classification == "safe_omission" for trace in report.traces),
        "false_omissions": sum(trace.classification == "false_omission" for trace in report.traces),
        "hard_safety_failures": sum(trace.classification == "hard_safety_failure" for trace in report.traces),
        "malformed": sum(trace.malformed for trace in report.traces),
        "contract_valid": sum(trace.backend_contract_valid for trace in report.traces),
    }


def _write_result_record(path: Path, payload: dict[str, object]) -> None:
    """Atomically retain synthetic benchmark evidence before presentation code runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def add_result_metadata(path: Path, metadata: dict[str, object]) -> None:
    """Attach evaluator-derived metrics without losing completed provider traces."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ValueError("completed contextual benchmark record is required")
    payload["evaluator_metadata"] = metadata
    _write_result_record(path, payload)


def run_provider_evaluation_details(
    *,
    cases: tuple[ContextualScenario, ...] | None = None,
    instructions: str = _EXTRACTION_INSTRUCTIONS,
    provider: Any | None = None,
    transport: Any | None = None,
    result_path: Path | None = None,
    batch_size: int = 10,
) -> ContextualExtractionRun:
    """Run one provider-neutral proposal evaluation with crash-resilient traces.

    Provider-native schema transport is an adapter detail.  The durable
    boundary remains provider output -> candidate -> ActiveStateProposal ->
    deterministic validation; this function never writes authoritative state.
    """
    from config import GEMINI_MODEL
    from llm.gemini import Gemini

    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 10:
        raise ValueError("benchmark batch size must be between one and ten")
    selected_cases = cases or load_contextual_manifest()
    selected_provider = provider or Gemini()
    selected_transport = transport or _native_schema_provider_response
    record_path = result_path or _RESULTS_DIRECTORY / "active_state_contextual_extraction_last_run.json"
    candidates: dict[str, ParsedCandidate] = {}
    failures: dict[str, str] = {}
    digests: list[str] = []
    batch_records: list[dict[str, object]] = []
    elapsed_ms = 0.0
    batches = tuple(selected_cases[index:index + batch_size] for index in range(0, len(selected_cases), batch_size))

    def persist(status: str, report: ContextualExtractionReport | None = None, error: Exception | None = None) -> None:
        payload: dict[str, object] = {
            "format": "active-state-contextual-extraction-run-v1",
            "status": status,
            "provider_model": GEMINI_MODEL,
            "provider_calls": len(batch_records),
            "provider_latency_ms": elapsed_ms,
            "batches": batch_records,
        }
        if error is not None:
            payload["error_type"] = type(error).__name__
        if report is not None:
            payload["report"] = asdict(report)
            payload["aggregate_metrics"] = _report_metrics(report)
        _write_result_record(record_path, payload)

    for batch in batches:
        try:
            response, batch_elapsed = selected_transport(selected_provider, batch, instructions)
        except Exception as error:
            persist("provider_error", error=error)
            raise
        elapsed_ms += batch_elapsed
        digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
        digests.append(digest)
        parsed = parse_provider_output_isolated(batch, response)
        candidates.update(parsed.candidates)
        failures.update(parsed.failures)
        batch_records.append({
            "case_ids": [case.case_id for case in batch],
            "latency_ms": batch_elapsed,
            "response_sha256": digest,
            "raw_structured_response": response,
            "parse_failures": dict(parsed.failures),
            "parsed_candidates": {case_id: _candidate_record(candidate) for case_id, candidate in parsed.candidates.items()},
        })
        # A completed provider response is durable even if later reporting or
        # another provider batch fails.
        persist("running")

    scored = evaluate_candidates(selected_cases, candidates, failures)
    report = ContextualExtractionReport(
        GEMINI_MODEL, len(batches), elapsed_ms,
        hashlib.sha256("".join(digests).encode("ascii")).hexdigest(), len(failures), 0, scored.traces,
    )
    persist("completed", report)
    return ContextualExtractionRun(report, candidates, failures, record_path)


def run_provider_evaluation() -> ContextualExtractionReport:
    """Compatibility wrapper for callers interested only in final trace metrics."""
    return run_provider_evaluation_details().report


if __name__ == "__main__":
    report = run_provider_evaluation()
    print(json.dumps({
        **asdict(report),
        "hard_safety_failures": report.hard_safety_failures,
        "passed": report.passed,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.passed else 1)
