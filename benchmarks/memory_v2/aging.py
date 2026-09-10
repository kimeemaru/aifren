"""Frozen release-evaluation target for Memory V2 aging and abstention.

This module is benchmark-only.  It never reads character files, application
history, or a live V1/V2 database.  The manifest intentionally labels product
classes outside V1's LLM-assigned 1--10 importance field.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .models import BenchmarkFixture, GoldClaim, RetrievalCase, SyntheticEvent


MANIFEST_VERSION = "memory-v2-aging-v1"
MEMORY_CLASSES = frozenset({
    "durable_identity_core_fact",
    "correctable_stable_fact",
    "explicit_retention_memory",
    "important_shared_episode",
    "recent_session_continuity",
    "ordinary_long_term_episode",
    "low_salience_mundane_history",
    "ambiguous_unknown_query",
})
_STATUSES = frozenset({"active", "superseded", "archived"})
_EVALUATION_KINDS = frozenset({"hard", "fuzzy"})
_ACTORS = frozenset({"user", "assistant"})
_MANIFEST_PATH = Path(__file__).with_name("aging_manifest.json")


class AgingManifestError(ValueError):
    """Raised when a frozen release fixture is malformed or underspecified."""


@dataclass(frozen=True)
class AgingRecord:
    claim_id: str
    event_id: str
    character_id: str
    recorded_at: str
    event_content: str
    claim_category: str
    claim_content: str
    lifecycle_status: str
    source_reference: str
    assertion_actor: str
    v1_importance: int
    superseded_by: str | None = None


@dataclass(frozen=True)
class AgingCase:
    case_id: str
    memory_class: str
    character_id: str
    query: str
    at: str
    canonical_evidence_ids: tuple[str, ...]
    expected_claim_ids: tuple[str, ...]
    forbidden_claim_ids: tuple[str, ...]
    lifecycle_expectation: str
    synthetic_age_years: int
    correction_state: str
    importance_source: str
    allows_abstention: bool
    evaluation_kind: str
    important_recorded: bool
    query_mode: str = "ordinary"
    must_ignore_v1_importance: bool = False

    @property
    def retrieval_case(self) -> RetrievalCase:
        return RetrievalCase(
            self.case_id,
            self.character_id,
            self.query,
            self.at,
            self.expected_claim_ids,
            self.forbidden_claim_ids,
            query_mode=self.query_mode,
            deterministic_only=self.evaluation_kind == "hard",
            contract_tags=("aging-release-target", self.memory_class, self.evaluation_kind),
            notes="Frozen aging/false-abstention release target; no owner threshold is encoded.",
        )


@dataclass(frozen=True)
class AgingManifest:
    version: str
    unrelated_history_count: int
    records: tuple[AgingRecord, ...]
    cases: tuple[AgingCase, ...]


@dataclass(frozen=True)
class AgingFixture:
    manifest: AgingManifest
    benchmark_fixture: BenchmarkFixture


@dataclass(frozen=True)
class AgingCaseResult:
    """Candidate and final selections emitted by a benchmark adapter."""

    candidate_claim_ids: tuple[str, ...] = ()
    selected_claim_ids: tuple[str, ...] = ()

    @classmethod
    def from_ids(cls, ids: Iterable[str]) -> "AgingCaseResult":
        values = tuple(str(value) for value in ids)
        return cls(values, values)


@dataclass(frozen=True)
class AgingCaseAssessment:
    case_id: str
    successful_recall: bool
    top_k_candidate_recall: bool
    incorrect_selection: bool
    true_abstention: bool
    false_abstention: bool
    important_recorded_false_abstention: bool
    wrong_character_leakage: bool
    lifecycle_current_history_violation: bool
    provenance_violation: bool
    assistant_authority_violation: bool
    hard_invariant_failure: bool


@dataclass(frozen=True)
class AgingEvaluationReport:
    total_cases: int
    cases_by_memory_class: dict[str, int]
    successful_recall: int
    top_k_candidate_recall: int
    incorrect_selection: int
    true_abstention: int
    false_abstention: int
    important_recorded_false_abstention: int
    hard_invariant_failures: int
    wrong_character_leakage: int
    lifecycle_current_history_violations: int
    provenance_violations: int
    assistant_authority_violations: int
    fuzzy_cases: int
    hard_cases: int
    owner_thresholds_configured: bool
    case_assessments: dict[str, AgingCaseAssessment]

    def to_dict(self) -> dict:
        return asdict(self)


def load_aging_manifest(path: str | Path | None = None) -> AgingManifest:
    """Load and validate the small, synthetic frozen release manifest."""
    source = Path(path) if path is not None else _MANIFEST_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgingManifestError(f"aging manifest is unreadable: {type(error).__name__}") from error
    return _parse_manifest(payload)


def _parse_manifest(payload: object) -> AgingManifest:
    if not isinstance(payload, dict):
        raise AgingManifestError("manifest root must be an object")
    if payload.get("version") != MANIFEST_VERSION:
        raise AgingManifestError("unexpected aging manifest version")
    history_count = payload.get("unrelated_history_count")
    if isinstance(history_count, bool) or not isinstance(history_count, int) or history_count < 1:
        raise AgingManifestError("unrelated_history_count must be a positive integer")
    records_data = payload.get("records")
    cases_data = payload.get("cases")
    if not isinstance(records_data, list) or not isinstance(cases_data, list):
        raise AgingManifestError("records and cases must be arrays")
    records = tuple(_parse_record(value) for value in records_data)
    cases = tuple(_parse_case(value) for value in cases_data)
    _validate_manifest(records, cases)
    return AgingManifest(MANIFEST_VERSION, history_count, records, cases)


def _parse_record(value: object) -> AgingRecord:
    if not isinstance(value, dict):
        raise AgingManifestError("record must be an object")
    required = ("claim_id", "event_id", "character_id", "recorded_at", "event_content", "claim_category",
                "claim_content", "lifecycle_status", "source_reference", "assertion_actor", "v1_importance")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required[:-1]):
        raise AgingManifestError("record has missing text metadata")
    importance = value.get("v1_importance")
    if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 10:
        raise AgingManifestError("record v1_importance must be an integer from 1 to 10")
    status = value["lifecycle_status"]
    actor = value["assertion_actor"]
    if status not in _STATUSES or actor not in _ACTORS:
        raise AgingManifestError("record status or assertion actor is invalid")
    successor = value.get("superseded_by")
    if successor is not None and (not isinstance(successor, str) or not successor.strip()):
        raise AgingManifestError("record superseded_by is invalid")
    _parse_time(value["recorded_at"], "record timestamp")
    return AgingRecord(*(value[key].strip() if isinstance(value[key], str) else value[key] for key in required), successor)


def _parse_case(value: object) -> AgingCase:
    if not isinstance(value, dict):
        raise AgingManifestError("case must be an object")
    required_text = ("case_id", "memory_class", "character_id", "query", "at", "lifecycle_expectation",
                     "correction_state", "importance_source", "evaluation_kind")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required_text):
        raise AgingManifestError("case has missing text metadata")
    for key in ("canonical_evidence_ids", "expected_claim_ids", "forbidden_claim_ids"):
        if not isinstance(value.get(key), list) or not all(isinstance(item, str) and item.strip() for item in value[key]):
            raise AgingManifestError(f"case {key} must be a list of identifiers")
    age = value.get("synthetic_age_years")
    if isinstance(age, bool) or not isinstance(age, int) or age < 0:
        raise AgingManifestError("synthetic_age_years must be a non-negative integer")
    for key in ("allows_abstention", "important_recorded"):
        if not isinstance(value.get(key), bool):
            raise AgingManifestError(f"case {key} must be boolean")
    query_mode = value.get("query_mode", "ordinary")
    if query_mode not in {"ordinary", "historical"}:
        raise AgingManifestError("case query_mode is invalid")
    _parse_time(value["at"], "case timestamp")
    return AgingCase(
        value["case_id"], value["memory_class"], value["character_id"], value["query"], value["at"],
        tuple(value["canonical_evidence_ids"]), tuple(value["expected_claim_ids"]), tuple(value["forbidden_claim_ids"]),
        value["lifecycle_expectation"], age, value["correction_state"], value["importance_source"],
        value["allows_abstention"], value["evaluation_kind"], value["important_recorded"], query_mode,
        bool(value.get("must_ignore_v1_importance", False)),
    )


def _validate_manifest(records: Sequence[AgingRecord], cases: Sequence[AgingCase]) -> None:
    record_by_claim = {record.claim_id: record for record in records}
    event_ids = {record.event_id for record in records}
    if len(record_by_claim) != len(records) or len(event_ids) != len(records):
        raise AgingManifestError("record claim and event IDs must be unique")
    if {case.case_id for case in cases}.__len__() != len(cases):
        raise AgingManifestError("case IDs must be unique")
    present_classes = {case.memory_class for case in cases}
    if not MEMORY_CLASSES.issubset(present_classes):
        raise AgingManifestError("manifest must cover every required release memory class")
    for record in records:
        if record.lifecycle_status == "superseded" and record.superseded_by not in record_by_claim:
            raise AgingManifestError("superseded record must name an existing successor")
    for case in cases:
        if case.memory_class not in MEMORY_CLASSES or case.evaluation_kind not in _EVALUATION_KINDS:
            raise AgingManifestError("case class or evaluation kind is invalid")
        if any(claim_id not in record_by_claim for claim_id in (*case.expected_claim_ids, *case.forbidden_claim_ids)):
            raise AgingManifestError("case references an unknown claim")
        if any(event_id not in event_ids for event_id in case.canonical_evidence_ids):
            raise AgingManifestError("case references unknown canonical evidence")
        if set(case.expected_claim_ids) & set(case.forbidden_claim_ids):
            raise AgingManifestError("expected and forbidden claims must not overlap")
        if case.expected_claim_ids and not case.canonical_evidence_ids:
            raise AgingManifestError("recorded expected claims require canonical evidence")
        if case.evaluation_kind == "hard" and case.allows_abstention:
            raise AgingManifestError("hard cases cannot permit abstention")
        if case.memory_class == "durable_identity_core_fact" and not case.must_ignore_v1_importance:
            raise AgingManifestError("durable identity cases must ignore V1 importance")
        for claim_id in case.expected_claim_ids:
            record = record_by_claim[claim_id]
            if record.character_id != case.character_id:
                raise AgingManifestError("expected claim must share the case character")


def build_aging_fixture(
    manifest: AgingManifest | None = None,
    *,
    unrelated_history_count: int | None = None,
) -> AgingFixture:
    """Materialize synthetic canonical records plus on-demand unrelated history."""
    manifest = manifest or load_aging_manifest()
    count = manifest.unrelated_history_count if unrelated_history_count is None else unrelated_history_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("unrelated_history_count must be a non-negative integer")
    events = []
    claims = []
    for record in manifest.records:
        events.append(SyntheticEvent(
            record.event_id, record.character_id, record.recorded_at, record.event_content,
            event_type="message" if record.assertion_actor == "user" else "assistant_message",
        ))
        claims.append(GoldClaim(
            record.claim_id, record.character_id, record.claim_category, record.claim_content,
            (record.event_id,), status=record.lifecycle_status, superseded_by=record.superseded_by,
            topic=record.claim_id, importance=record.v1_importance,
        ))
    events.extend(_unrelated_events(count))
    claims.extend(_unrelated_claims(count))
    fixture = BenchmarkFixture(
        manifest.version,
        tuple(events),
        tuple(claims),
        tuple(case.retrieval_case for case in manifest.cases),
    )
    return AgingFixture(manifest, fixture)


def _unrelated_events(count: int) -> tuple[SyntheticEvent, ...]:
    start = datetime(2020, 4, 1, tzinfo=timezone.utc)
    return tuple(
        SyntheticEvent(
            f"aging-filler-event-{index:05d}", "aging-a",
            (start + timedelta(hours=index * 5)).isoformat().replace("+00:00", "Z"),
            f"Synthetic unrelated history topic {index % 31}, detail {index}.",
        )
        for index in range(count)
    )


def _unrelated_claims(count: int) -> tuple[GoldClaim, ...]:
    return tuple(
        GoldClaim(
            f"aging-filler-claim-{index:05d}", "aging-a", "synthetic",
            f"Synthetic unrelated history topic {index % 31}, detail {index}.",
            (f"aging-filler-event-{index:05d}",), topic=f"filler-{index % 31}", importance=5,
        )
        for index in range(count)
    )


def evaluate_aging_results(
    fixture: AgingFixture,
    results_by_case: Mapping[str, AgingCaseResult | Iterable[str]],
) -> AgingEvaluationReport:
    """Score release classes without embedding any owner quality thresholds."""
    records = {record.claim_id: record for record in fixture.manifest.records}
    claims = {claim.claim_id: claim for claim in fixture.benchmark_fixture.claims}
    event_ids = {event.event_id for event in fixture.benchmark_fixture.events}
    assessments: dict[str, AgingCaseAssessment] = {}
    counts = {memory_class: 0 for memory_class in sorted(MEMORY_CLASSES)}
    for case in fixture.manifest.cases:
        counts[case.memory_class] += 1
        raw = results_by_case.get(case.case_id, AgingCaseResult())
        result = raw if isinstance(raw, AgingCaseResult) else AgingCaseResult.from_ids(raw)
        selected = tuple(dict.fromkeys(result.selected_claim_ids))
        candidates = tuple(dict.fromkeys(result.candidate_claim_ids))
        expected = set(case.expected_claim_ids)
        forbidden = set(case.forbidden_claim_ids)
        successful = bool(expected & set(selected))
        top_k = bool(expected & set(candidates))
        abstained = not selected
        false_abstention = bool(expected) and abstained and not case.allows_abstention
        true_abstention = abstained and (not expected or case.allows_abstention)
        wrong_character = any(
            claim_id in claims and claims[claim_id].character_id != case.character_id
            for claim_id in selected
        )
        lifecycle_violation = any(
            case.lifecycle_expectation == "current" and claim_id in claims and claims[claim_id].status != "active"
            for claim_id in selected
        )
        provenance_violation = any(
            claim_id not in claims
            or not claims[claim_id].source_event_ids
            or not set(claims[claim_id].source_event_ids).issubset(event_ids)
            for claim_id in selected
        )
        assistant_authority = any(
            claim_id in records and records[claim_id].assertion_actor != "user"
            for claim_id in selected
        )
        incorrect = (
            bool(set(selected) & forbidden)
            or (bool(expected) and bool(selected) and not successful)
            or (not expected and bool(selected))
        )
        hard_failure = (
            (case.evaluation_kind == "hard" and not successful)
            or wrong_character
            or lifecycle_violation
            or provenance_violation
            or assistant_authority
        )
        assessments[case.case_id] = AgingCaseAssessment(
            case.case_id, successful, top_k, incorrect, true_abstention, false_abstention,
            bool(case.important_recorded and false_abstention), wrong_character, lifecycle_violation,
            provenance_violation, assistant_authority, hard_failure,
        )
    values = tuple(assessments.values())
    return AgingEvaluationReport(
        total_cases=len(values), cases_by_memory_class=counts,
        successful_recall=sum(item.successful_recall for item in values),
        top_k_candidate_recall=sum(item.top_k_candidate_recall for item in values),
        incorrect_selection=sum(item.incorrect_selection for item in values),
        true_abstention=sum(item.true_abstention for item in values),
        false_abstention=sum(item.false_abstention for item in values),
        important_recorded_false_abstention=sum(item.important_recorded_false_abstention for item in values),
        hard_invariant_failures=sum(item.hard_invariant_failure for item in values),
        wrong_character_leakage=sum(item.wrong_character_leakage for item in values),
        lifecycle_current_history_violations=sum(item.lifecycle_current_history_violation for item in values),
        provenance_violations=sum(item.provenance_violation for item in values),
        assistant_authority_violations=sum(item.assistant_authority_violation for item in values),
        fuzzy_cases=sum(case.evaluation_kind == "fuzzy" for case in fixture.manifest.cases),
        hard_cases=sum(case.evaluation_kind == "hard" for case in fixture.manifest.cases),
        owner_thresholds_configured=False, case_assessments=assessments,
    )


def run_aging_evaluation(fixture: AgingFixture, adapter, *, limit: int = 5) -> AgingEvaluationReport:
    """Run any existing benchmark adapter; this does not imply release readiness."""
    try:
        results = {
            case.case_id: AgingCaseResult.from_ids(adapter.retrieve(fixture.benchmark_fixture, case.retrieval_case, limit=limit))
            for case in fixture.manifest.cases
        }
        return evaluate_aging_results(fixture, results)
    finally:
        closer = getattr(adapter, "close", None)
        if closer:
            closer()


def _parse_time(value: str, label: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AgingManifestError(f"{label} is invalid") from error
