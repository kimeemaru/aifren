"""Frozen multiplicity/distractor evaluation for the benchmark durable-core lane."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Mapping

from .durable_core_lane import (
    CORE_CLASSES,
    DurableCoreRecord,
    DurableLaneAssessment,
    assess_durable_lane,
    build_durable_core_index,
)
from .models import BenchmarkFixture, GoldClaim, RetrievalCase, SyntheticEvent


MANIFEST_VERSION = "memory-v2-durable-core-multiplicity-v1"
_MANIFEST_PATH = Path(__file__).with_name("durable_core_multiplicity_manifest.json")
_STATUSES = frozenset({"active", "superseded", "archived"})
_ACTORS = frozenset({"user", "assistant"})


class MultiplicityManifestError(ValueError):
    """Raised when the frozen durable-core multiplicity corpus is malformed."""


@dataclass(frozen=True)
class MultiplicityRecord:
    claim_id: str
    character_id: str
    core_class: str
    content: str
    recorded_at: str
    status: str
    assertion_actor: str
    source_valid: bool
    superseded_by: str | None = None


@dataclass(frozen=True)
class MultiplicityCase:
    case_id: str
    category: str
    character_id: str
    query: str
    at: str
    expected_claim_ids: tuple[str, ...]
    query_mode: str = "ordinary"

    @property
    def retrieval_case(self) -> RetrievalCase:
        return RetrievalCase(
            self.case_id, self.character_id, self.query, self.at,
            self.expected_claim_ids, query_mode=self.query_mode,
            deterministic_only=True,
            contract_tags=("durable-core-multiplicity", self.category),
        )


@dataclass(frozen=True)
class MultiplicityFixture:
    records: tuple[MultiplicityRecord, ...]
    cases: tuple[MultiplicityCase, ...]
    benchmark_fixture: BenchmarkFixture
    core_records: tuple[DurableCoreRecord, ...]


@dataclass(frozen=True)
class MultiplicityCaseResult:
    case_id: str
    category: str
    expected_claim_ids: tuple[str, ...]
    assessment: DurableLaneAssessment


@dataclass(frozen=True)
class MultiplicityReport:
    total_records: int
    records_by_core_class: dict[str, int]
    total_queries: int
    queries_by_category: dict[str, int]
    lookup_executed: int
    selected: int
    admitted: int
    expected_recovered: int
    incorrect_or_irrelevant_selections: int
    incorrect_or_irrelevant_admissions: int
    wrong_character_leakage: int
    lifecycle_violations: int
    provenance_violations: int
    assistant_authority_violations: int
    max_inspected_record_count: int
    max_candidate_count: int
    results: dict[str, MultiplicityCaseResult]

    def to_dict(self) -> dict:
        return asdict(self)


def load_multiplicity_fixture(path: str | Path | None = None) -> MultiplicityFixture:
    source = Path(path) if path is not None else _MANIFEST_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MultiplicityManifestError("multiplicity manifest is unreadable") from error
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise MultiplicityManifestError("unexpected multiplicity manifest version")
    records_data, cases_data = payload.get("records"), payload.get("cases")
    if not isinstance(records_data, list) or not isinstance(cases_data, list):
        raise MultiplicityManifestError("records and cases must be arrays")
    records = tuple(_parse_record(value) for value in records_data)
    cases = tuple(_parse_case(value) for value in cases_data)
    _validate(records, cases)
    events = tuple(
        SyntheticEvent(f"event-{record.claim_id}", record.character_id, record.recorded_at, record.content)
        for record in records if record.source_valid
    )
    claims = tuple(GoldClaim(
        record.claim_id, record.character_id, record.core_class, record.content,
        (f"event-{record.claim_id}",) if record.source_valid else (), status=record.status,
        superseded_by=record.superseded_by, topic=record.core_class,
    ) for record in records)
    core_records = tuple(DurableCoreRecord(
        record.claim_id, record.character_id, record.core_class, record.content, record.recorded_at,
        record.status, record.assertion_actor,
        (f"event-{record.claim_id}",) if record.source_valid else (), record.superseded_by,
    ) for record in records)
    return MultiplicityFixture(
        records, cases,
        BenchmarkFixture(MANIFEST_VERSION, events, claims, tuple(case.retrieval_case for case in cases)),
        core_records,
    )


def run_multiplicity_evaluation(fixture: MultiplicityFixture) -> MultiplicityReport:
    index = build_durable_core_index(fixture.core_records)
    results = {}
    for case in fixture.cases:
        assessment = assess_durable_lane(
            fixture.benchmark_fixture, index, case.retrieval_case,
            expected_claim_ids=case.expected_claim_ids,
        )
        results[case.case_id] = MultiplicityCaseResult(case.case_id, case.category, case.expected_claim_ids, assessment)
    values = tuple(results.values())
    return MultiplicityReport(
        total_records=len(fixture.records),
        records_by_core_class={core: sum(record.core_class == core for record in fixture.records) for core in sorted(CORE_CLASSES)},
        total_queries=len(values),
        queries_by_category={category: sum(case.category == category for case in fixture.cases) for category in sorted({case.category for case in fixture.cases})},
        lookup_executed=sum(value.assessment.outcome.lookup_ran for value in values),
        selected=sum(bool(value.assessment.outcome.selected_claim_ids) for value in values),
        admitted=sum(bool(value.assessment.outcome.admitted_claim_ids) for value in values),
        expected_recovered=sum(value.assessment.expected_claim_recovered for value in values if value.expected_claim_ids),
        incorrect_or_irrelevant_selections=sum(value.assessment.irrelevant_durable_selected for value in values),
        incorrect_or_irrelevant_admissions=sum(
            bool(set(value.assessment.outcome.admitted_claim_ids) - set(value.expected_claim_ids))
            for value in values
        ),
        wrong_character_leakage=sum(value.assessment.wrong_character_leakage for value in values),
        lifecycle_violations=sum(value.assessment.lifecycle_violation for value in values),
        provenance_violations=sum(value.assessment.provenance_violation for value in values),
        assistant_authority_violations=sum(value.assessment.assistant_authority_violation for value in values),
        max_inspected_record_count=max(value.assessment.outcome.inspected_record_count for value in values),
        max_candidate_count=max(len(value.assessment.outcome.candidates) for value in values),
        results=results,
    )


def _parse_record(value: object) -> MultiplicityRecord:
    text = ("claim_id", "character_id", "core_class", "content", "recorded_at", "status", "assertion_actor")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in text):
        raise MultiplicityManifestError("record has missing text")
    if value["core_class"] not in CORE_CLASSES or value["status"] not in _STATUSES or value["assertion_actor"] not in _ACTORS:
        raise MultiplicityManifestError("record class, status, or actor is invalid")
    if not isinstance(value.get("source_valid"), bool):
        raise MultiplicityManifestError("record source_valid must be boolean")
    successor = value.get("superseded_by")
    if successor is not None and (not isinstance(successor, str) or not successor):
        raise MultiplicityManifestError("record superseded_by is invalid")
    return MultiplicityRecord(*(value[key] for key in text[:7]), value["source_valid"], successor)


def _parse_case(value: object) -> MultiplicityCase:
    text = ("case_id", "category", "character_id", "query", "at")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in text):
        raise MultiplicityManifestError("case has missing text")
    expected = value.get("expected_claim_ids")
    if not isinstance(expected, list) or not all(isinstance(item, str) and item for item in expected):
        raise MultiplicityManifestError("expected claims must be an identifier array")
    mode = value.get("query_mode", "ordinary")
    if mode not in {"ordinary", "historical"}:
        raise MultiplicityManifestError("invalid query mode")
    return MultiplicityCase(*(value[key] for key in text), tuple(expected), mode)


def _validate(records: tuple[MultiplicityRecord, ...], cases: tuple[MultiplicityCase, ...]) -> None:
    ids = {record.claim_id for record in records}
    if len(ids) != len(records) or len({case.case_id for case in cases}) != len(cases):
        raise MultiplicityManifestError("record and case IDs must be unique")
    if len(records) != 28 or len(cases) != 30:
        raise MultiplicityManifestError("frozen multiplicity corpus must contain 28 records and 30 queries")
    if any(record.status == "superseded" and record.superseded_by not in ids for record in records):
        raise MultiplicityManifestError("superseded record requires known successor")
    for case in cases:
        if any(claim_id not in ids for claim_id in case.expected_claim_ids):
            raise MultiplicityManifestError("case references unknown expected claim")
