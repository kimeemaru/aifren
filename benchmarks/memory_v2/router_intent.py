"""Frozen benchmark-only audit for intent-router memory-search false skips."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping

from aifren.memory_v2_store.retrieval import _infer_intent, _tokens

from aifren.memory_v2_store.models import BenchmarkFixture, GoldClaim, RetrievalCase, SyntheticEvent


ROUTER_SUITE_VERSION = "memory-v2-router-intent-v1"
FACT_TYPES = frozenset({
    "user_name", "preferred_address", "stable_home_location",
    "important_person_relationship_fact", "durable_preference",
    "durable_biographical_fact", "generic_control",
})
_QUERY_STYLES = frozenset({"direct", "paraphrase", "generic_pattern_collision", "genuine_generic_control"})
_SEARCH_BEHAVIORS = frozenset({"memory_search", "skip"})
_SKIP_INTENTS = frozenset({"assistant_opinion", "generic_reasoning", "ambiguous_memory"})
_STATUSES = frozenset({"active", "archived", "superseded"})
_ACTORS = frozenset({"user", "assistant"})
_MANIFEST_PATH = Path(__file__).with_name("router_intent_manifest.json")


class RouterIntentManifestError(ValueError):
    """Raised when the frozen router-intent suite is malformed."""


@dataclass(frozen=True)
class RouterRecord:
    claim_id: str
    event_id: str
    character_id: str
    recorded_at: str
    event_content: str
    claim_type: str
    claim_content: str
    status: str
    assertion_actor: str
    source_reference: str


@dataclass(frozen=True)
class RouterCase:
    case_id: str
    fact_type: str
    query_style: str
    character_id: str
    query: str
    at: str
    expected_search_behavior: str
    expected_claim_ids: tuple[str, ...]
    forbidden_claim_ids: tuple[str, ...]
    lifecycle_expectation: str

    @property
    def retrieval_case(self) -> RetrievalCase:
        return RetrievalCase(
            self.case_id, self.character_id, self.query, self.at,
            self.expected_claim_ids, self.forbidden_claim_ids,
            deterministic_only=True,
            contract_tags=("router-intent-held-out", self.fact_type, self.query_style),
            notes="Frozen held-out router audit; classification is observed, never tuned here.",
        )


@dataclass(frozen=True)
class RouterIntentSuite:
    version: str
    records: tuple[RouterRecord, ...]
    cases: tuple[RouterCase, ...]


@dataclass(frozen=True)
class RouterIntentFixture:
    suite: RouterIntentSuite
    benchmark_fixture: BenchmarkFixture


@dataclass(frozen=True)
class RouterCaseResult:
    actual_intent: str
    retrieval_attempted: bool
    candidate_claim_ids: tuple[str, ...] = ()
    selected_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouterCaseAssessment:
    case_id: str
    false_skip: bool
    unnecessary_memory_search: bool
    successful_recall: bool
    expected_claim_entered_candidates: bool
    incorrect_selection: bool
    wrong_character_leakage: bool
    lifecycle_violation: bool
    provenance_violation: bool
    assistant_authority_violation: bool


@dataclass(frozen=True)
class RouterIntentReport:
    total_cases: int
    durable_queries_tested: int
    durable_queries_correctly_routed_to_memory: int
    durable_query_false_skips: int
    direct_queries_tested: int
    direct_query_false_skips: int
    paraphrase_queries_tested: int
    paraphrase_false_skips: int
    generic_pattern_collision_queries: int
    generic_pattern_collision_false_skips: int
    genuine_generic_controls: int
    generic_controls_correctly_skipped: int
    unnecessary_memory_search_activations: int
    wrong_character_leakage: int
    lifecycle_violations: int
    provenance_violations: int
    assistant_authority_violations: int
    case_assessments: dict[str, RouterCaseAssessment]

    def to_dict(self) -> dict:
        return asdict(self)


def load_router_intent_suite(path: str | Path | None = None) -> RouterIntentSuite:
    source = Path(path) if path is not None else _MANIFEST_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RouterIntentManifestError("router intent manifest is unreadable") from error
    if not isinstance(payload, dict) or payload.get("version") != ROUTER_SUITE_VERSION:
        raise RouterIntentManifestError("unexpected router intent manifest version")
    records_data, cases_data = payload.get("records"), payload.get("cases")
    if not isinstance(records_data, list) or not isinstance(cases_data, list):
        raise RouterIntentManifestError("records and cases must be arrays")
    records = tuple(_parse_record(value) for value in records_data)
    cases = tuple(_parse_case(value) for value in cases_data)
    _validate(records, cases)
    return RouterIntentSuite(ROUTER_SUITE_VERSION, records, cases)


def build_router_intent_fixture(suite: RouterIntentSuite | None = None) -> RouterIntentFixture:
    suite = suite or load_router_intent_suite()
    events = tuple(SyntheticEvent(
        record.event_id, record.character_id, record.recorded_at, record.event_content,
        event_type="message" if record.assertion_actor == "user" else "assistant_message",
    ) for record in suite.records)
    claims = tuple(GoldClaim(
        record.claim_id, record.character_id, record.claim_type, record.claim_content,
        (record.event_id,), status=record.status, topic=record.claim_id,
    ) for record in suite.records)
    return RouterIntentFixture(suite, BenchmarkFixture(
        suite.version, events, claims, tuple(case.retrieval_case for case in suite.cases),
    ))


def run_router_intent_audit(fixture: RouterIntentFixture, adapter, *, limit: int = 5) -> RouterIntentReport:
    results = {}
    try:
        for router_case in fixture.suite.cases:
            case = router_case.retrieval_case
            tokens = _tokens(case.query)
            intent = _infer_intent(case.query, tokens).kind
            outcome = adapter.retrieve_outcome(fixture.benchmark_fixture, case, limit=limit)
            candidates = tuple(dict.fromkeys(
                trace.claim_id for trace in outcome.traces if trace.claim_id != "__query__"
            ))
            results[router_case.case_id] = RouterCaseResult(
                intent, intent not in _SKIP_INTENTS, candidates, tuple(outcome.claim_ids),
            )
        return evaluate_router_intent_results(fixture, results)
    finally:
        closer = getattr(adapter, "close", None)
        if closer:
            closer()


def evaluate_router_intent_results(
    fixture: RouterIntentFixture,
    results_by_case: Mapping[str, RouterCaseResult],
) -> RouterIntentReport:
    claims = {claim.claim_id: claim for claim in fixture.benchmark_fixture.claims}
    records = {record.claim_id: record for record in fixture.suite.records}
    event_ids = {event.event_id for event in fixture.benchmark_fixture.events}
    assessments = {}
    for case in fixture.suite.cases:
        result = results_by_case[case.case_id]
        selected = set(result.selected_claim_ids)
        expected = set(case.expected_claim_ids)
        forbidden = set(case.forbidden_claim_ids)
        memory_expected = case.expected_search_behavior == "memory_search"
        wrong_character = any(claim_id in claims and claims[claim_id].character_id != case.character_id for claim_id in selected)
        lifecycle = any(
            claim_id in claims and claims[claim_id].status != "active"
            for claim_id in selected
        )
        provenance = any(
            claim_id not in claims or not claims[claim_id].source_event_ids
            or not set(claims[claim_id].source_event_ids).issubset(event_ids)
            for claim_id in selected
        )
        assistant_authority = any(
            claim_id in records and records[claim_id].assertion_actor != "user"
            for claim_id in selected
        )
        assessments[case.case_id] = RouterCaseAssessment(
            case.case_id,
            memory_expected and not result.retrieval_attempted,
            not memory_expected and result.retrieval_attempted,
            bool(expected & selected),
            bool(expected & set(result.candidate_claim_ids)),
            bool(selected & forbidden) or (bool(expected) and bool(selected) and not bool(expected & selected)) or (not expected and bool(selected)),
            wrong_character, lifecycle, provenance, assistant_authority,
        )
    values = tuple(assessments.values())
    durable_cases = [case for case in fixture.suite.cases if case.expected_search_behavior == "memory_search"]
    generic_cases = [case for case in fixture.suite.cases if case.expected_search_behavior == "skip"]
    def count(style, field):
        return sum(getattr(assessments[case.case_id], field) for case in fixture.suite.cases if case.query_style == style)
    return RouterIntentReport(
        total_cases=len(values),
        durable_queries_tested=len(durable_cases),
        durable_queries_correctly_routed_to_memory=sum(not assessments[case.case_id].false_skip for case in durable_cases),
        durable_query_false_skips=sum(assessments[case.case_id].false_skip for case in durable_cases),
        direct_queries_tested=sum(case.query_style == "direct" for case in durable_cases),
        direct_query_false_skips=count("direct", "false_skip"),
        paraphrase_queries_tested=sum(case.query_style == "paraphrase" for case in durable_cases),
        paraphrase_false_skips=count("paraphrase", "false_skip"),
        generic_pattern_collision_queries=sum(case.query_style == "generic_pattern_collision" for case in durable_cases),
        generic_pattern_collision_false_skips=count("generic_pattern_collision", "false_skip"),
        genuine_generic_controls=len(generic_cases),
        generic_controls_correctly_skipped=sum(not assessments[case.case_id].unnecessary_memory_search for case in generic_cases),
        unnecessary_memory_search_activations=sum(assessments[case.case_id].unnecessary_memory_search for case in generic_cases),
        wrong_character_leakage=sum(item.wrong_character_leakage for item in values),
        lifecycle_violations=sum(item.lifecycle_violation for item in values),
        provenance_violations=sum(item.provenance_violation for item in values),
        assistant_authority_violations=sum(item.assistant_authority_violation for item in values),
        case_assessments=assessments,
    )


def _parse_record(value: object) -> RouterRecord:
    keys = ("claim_id", "event_id", "character_id", "recorded_at", "event_content", "claim_type", "claim_content", "status", "assertion_actor", "source_reference")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in keys):
        raise RouterIntentManifestError("record has missing text metadata")
    if value["status"] not in _STATUSES or value["assertion_actor"] not in _ACTORS:
        raise RouterIntentManifestError("record status or actor is invalid")
    return RouterRecord(*(value[key] for key in keys))


def _parse_case(value: object) -> RouterCase:
    keys = ("case_id", "fact_type", "query_style", "character_id", "query", "at", "expected_search_behavior", "lifecycle_expectation")
    lists = ("expected_claim_ids", "forbidden_claim_ids")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in keys):
        raise RouterIntentManifestError("case has missing text metadata")
    if any(not isinstance(value.get(key), list) or not all(isinstance(item, str) and item.strip() for item in value[key]) for key in lists):
        raise RouterIntentManifestError("case identifiers must be arrays")
    if value["fact_type"] not in FACT_TYPES or value["query_style"] not in _QUERY_STYLES or value["expected_search_behavior"] not in _SEARCH_BEHAVIORS:
        raise RouterIntentManifestError("case category is invalid")
    return RouterCase(*(value[key] for key in keys[:7]), tuple(value["expected_claim_ids"]), tuple(value["forbidden_claim_ids"]), value["lifecycle_expectation"])


def _validate(records: tuple[RouterRecord, ...], cases: tuple[RouterCase, ...]) -> None:
    by_claim = {record.claim_id: record for record in records}
    if len(by_claim) != len(records) or len({record.event_id for record in records}) != len(records):
        raise RouterIntentManifestError("record IDs must be unique")
    if len({case.case_id for case in cases}) != len(cases):
        raise RouterIntentManifestError("case IDs must be unique")
    required_durable = FACT_TYPES - {"generic_control"}
    present = {case.fact_type for case in cases}
    if not required_durable.issubset(present) or "generic_control" not in present:
        raise RouterIntentManifestError("suite must cover durable facts and generic controls")
    if sum(case.expected_search_behavior == "memory_search" for case in cases) != 18 or sum(case.expected_search_behavior == "skip" for case in cases) != 4:
        raise RouterIntentManifestError("frozen suite must contain 18 durable queries and 4 generic controls")
    for case in cases:
        if any(claim_id not in by_claim for claim_id in (*case.expected_claim_ids, *case.forbidden_claim_ids)):
            raise RouterIntentManifestError("case references an unknown claim")
        if case.expected_search_behavior == "memory_search" and not case.expected_claim_ids:
            raise RouterIntentManifestError("durable query requires an expected claim")
        if case.expected_search_behavior == "skip" and case.expected_claim_ids:
            raise RouterIntentManifestError("generic control cannot expect a memory claim")
        if any(by_claim[claim_id].character_id != case.character_id for claim_id in case.expected_claim_ids):
            raise RouterIntentManifestError("expected claim must share case character")
