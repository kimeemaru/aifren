"""Benchmark-only bounded durable-core lane, independent of intent routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import time
from typing import Iterable

from aifren.memory_v2_store.retrieval import _infer_intent, _tokens

from .aging import AgingFixture
from aifren.memory_v2_store.models import BenchmarkFixture, RetrievalCase
from .router_intent import RouterIntentFixture
from .router_intent_counterfactual import GenericHoldoutCase


CORE_CLASSES = frozenset({
    "user_name", "preferred_address", "current_home_location",
    "important_person_relationship_fact", "durable_preference",
    "durable_biographical_fact",
})
_RELATION_TERMS = frozenset({"sister", "brother", "partner", "parent", "mother", "father", "friend", "coworker", "colleague"})
_PREFERENCE_CATEGORY_TERMS = frozenset({"drink", "food", "game", "tea", "coffee", "communication"})
_HYPOTHETICAL = re.compile(r"\b(?:if\s+i\s+were|fictional|character\s+sheet|role[- ]playing|game\s+character)\b", re.IGNORECASE)
_CALL_ME = re.compile(r"\bcall\s+me\b", re.IGNORECASE)


@dataclass(frozen=True)
class DurableCoreRecord:
    claim_id: str
    character_id: str
    core_class: str
    content: str
    recorded_at: str
    status: str
    assertion_actor: str
    source_event_ids: tuple[str, ...]
    superseded_by: str | None = None


@dataclass(frozen=True)
class DurableCoreIndex:
    """Small benchmark-only core-class index; never an episodic archive index."""
    records: tuple[DurableCoreRecord, ...]
    by_character_class: dict[tuple[str, str], tuple[DurableCoreRecord, ...]]
    by_claim_id: dict[str, DurableCoreRecord]


@dataclass(frozen=True)
class DurableCandidate:
    claim_id: str
    core_class: str
    rank: int
    score: int
    selection_eligible: bool
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class DurableLaneOutcome:
    current_router_intent: str
    lookup_ran: bool
    candidates: tuple[DurableCandidate, ...]
    selected_claim_ids: tuple[str, ...]
    admitted_claim_ids: tuple[str, ...]
    inspected_record_count: int
    lookup_latency_ms: float


@dataclass(frozen=True)
class DurableLaneAssessment:
    case_id: str
    expected_claim_recovered: bool
    irrelevant_durable_selected: bool
    wrong_character_leakage: bool
    lifecycle_violation: bool
    provenance_violation: bool
    assistant_authority_violation: bool
    outcome: DurableLaneOutcome


def router_core_records(fixture: RouterIntentFixture) -> tuple[DurableCoreRecord, ...]:
    """Use frozen fixture labels, never query answers, to define the small pool."""
    classes_by_claim = {}
    for case in fixture.suite.cases:
        if case.fact_type == "generic_control":
            continue
        for claim_id in case.expected_claim_ids:
            classes_by_claim.setdefault(claim_id, _router_core_class(case.fact_type))
    records = {record.claim_id: record for record in fixture.suite.records}
    claims = {claim.claim_id: claim for claim in fixture.benchmark_fixture.claims}
    return tuple(
        DurableCoreRecord(
            claim_id, records[claim_id].character_id, core_class, claims[claim_id].content,
            records[claim_id].recorded_at, records[claim_id].status, records[claim_id].assertion_actor,
            claims[claim_id].source_event_ids,
        )
        for claim_id, core_class in sorted(classes_by_claim.items())
    )


def aging_core_records(fixture: AgingFixture) -> tuple[DurableCoreRecord, ...]:
    """Classify only aging identity/location records as a synthetic core pool."""
    class_by_category = {"identity": "user_name", "location": "current_home_location"}
    claims = {claim.claim_id: claim for claim in fixture.benchmark_fixture.claims}
    return tuple(
        DurableCoreRecord(
            record.claim_id, record.character_id, class_by_category[record.claim_category],
            claims[record.claim_id].content, record.recorded_at, record.lifecycle_status,
            record.assertion_actor, claims[record.claim_id].source_event_ids, record.superseded_by,
        )
        for record in fixture.manifest.records if record.claim_category in class_by_category
    )


def build_durable_core_index(records: Iterable[DurableCoreRecord]) -> DurableCoreIndex:
    values = tuple(records)
    grouped: dict[tuple[str, str], list[DurableCoreRecord]] = {}
    for record in values:
        grouped.setdefault((record.character_id, record.core_class), []).append(record)
    return DurableCoreIndex(
        values, {key: tuple(value) for key, value in grouped.items()},
        {record.claim_id: record for record in values},
    )


def lookup_durable_core(
    records: Iterable[DurableCoreRecord] | DurableCoreIndex,
    case: RetrievalCase,
) -> DurableLaneOutcome:
    """Run a bounded fixture-classified lookup regardless of router result."""
    started = time.perf_counter()
    if isinstance(records, DurableCoreIndex):
        all_records = records.records
        index = records
    else:
        all_records = tuple(records)
        index = None
    current_intent = _infer_intent(case.query, _tokens(case.query)).kind
    query_tokens = set(_tokens(case.query))
    slots = _query_slots(case.query, query_tokens)
    historical_years = re.findall(r"\b(19\d{2}|20\d{2})\b", case.query)
    historical = case.query_mode == "historical" or bool(historical_years)
    at = (
        datetime(int(historical_years[-1]), 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        if historical_years else _parse_time(case.at)
    )
    scoped_records = (
        tuple(record for slot in slots for record in index.by_character_class.get((case.character_id, slot), ()))
        if index is not None else all_records
    )
    matching = []
    for record in scoped_records:
        lifecycle_records = index if index is not None else all_records
        if record.character_id != case.character_id or not _lifecycle_eligible(lifecycle_records, record, at, historical):
            continue
        score = _score(record, case.query, query_tokens, slots)
        if score:
            hypothetical = bool(_HYPOTHETICAL.search(case.query))
            eligible = not hypothetical and _selection_eligible(record, score, query_tokens, slots)
            matching.append((score, record, eligible, "hypothetical_or_fictional_context" if hypothetical else None))
    matching.sort(key=lambda item: (-item[0], item[1].claim_id))
    candidates = tuple(
        DurableCandidate(record.claim_id, record.core_class, rank, score, eligible, reason if not eligible else None)
        for rank, (score, record, eligible, reason) in enumerate(matching, 1)
    )
    eligible = [candidate for candidate in candidates if candidate.selection_eligible]
    # A bounded core lane should abstain rather than inject an ambiguous tie.
    selected = (
        eligible[0].claim_id
        if eligible and (len(eligible) == 1 or eligible[0].score > eligible[1].score)
        else None
    )
    return DurableLaneOutcome(
        current_intent, True, candidates, (selected,) if selected else (),
        # This benchmark admits only a selected, relevant user fact as
        # background evidence.  Lookup and candidate presence never inject.
        (selected,) if selected else (),
        len(scoped_records),
        round((time.perf_counter() - started) * 1000, 3),
    )


def assess_durable_lane(
    fixture: BenchmarkFixture,
    records: Iterable[DurableCoreRecord] | DurableCoreIndex,
    case: RetrievalCase,
    *,
    expected_claim_ids: tuple[str, ...] = (),
    forbidden_claim_ids: tuple[str, ...] = (),
) -> DurableLaneAssessment:
    all_records = records.records if isinstance(records, DurableCoreIndex) else tuple(records)
    outcome = lookup_durable_core(records, case)
    selected = set(outcome.selected_claim_ids)
    expected = set(expected_claim_ids)
    claims = {claim.claim_id: claim for claim in fixture.claims}
    record_by_id = {record.claim_id: record for record in all_records}
    event_ids = {event.event_id for event in fixture.events}
    return DurableLaneAssessment(
        case.case_id,
        bool(expected & selected),
        bool(selected - expected) or bool(selected & set(forbidden_claim_ids)),
        any(claim_id in claims and claims[claim_id].character_id != case.character_id for claim_id in selected),
        any(claim_id in record_by_id and record_by_id[claim_id].status != "active" and case.query_mode != "historical" for claim_id in selected),
        any(claim_id not in claims or not set(claims[claim_id].source_event_ids).issubset(event_ids) for claim_id in selected),
        any(claim_id in record_by_id and record_by_id[claim_id].assertion_actor != "user" for claim_id in selected),
        outcome,
    )


def _router_core_class(fact_type: str) -> str:
    return {
        "user_name": "user_name",
        "preferred_address": "preferred_address",
        "stable_home_location": "current_home_location",
        "important_person_relationship_fact": "important_person_relationship_fact",
        "durable_preference": "durable_preference",
        "durable_biographical_fact": "durable_biographical_fact",
    }[fact_type]


def _query_slots(query: str, tokens: set[str]) -> set[str]:
    lower = query.lower()
    slots = set()
    self_name_phrase = bool(re.search(r"\bmy\s+(?:(?:legal|given|full)\s+)?name\b|\bname\b.*\bfor\s+me\b", lower))
    if self_name_phrase or _CALL_ME.search(lower):
        slots.add("user_name" if "again" not in tokens else "preferred_address")
    if "nickname" in tokens or "address" in tokens and re.search(r"\b(my|me)\b", lower):
        slots.add("preferred_address")
    if {"live", "home", "city", "based"} & tokens:
        slots.add("current_home_location")
    if _RELATION_TERMS & tokens:
        slots.add("important_person_relationship_fact")
    if {"prefer", "favorite", "usual"} & tokens or _PREFERENCE_CATEGORY_TERMS & set(re.findall(r"[a-z]+", lower)):
        slots.add("durable_preference")
    if {"job", "occupation", "career"} & tokens:
        slots.add("durable_biographical_fact")
    return slots


def _score(record: DurableCoreRecord, query: str, query_tokens: set[str], slots: set[str]) -> int:
    if record.core_class not in slots:
        return 0
    content_tokens = set(_tokens(record.content))
    overlap = len(query_tokens & content_tokens)
    if record.core_class == "user_name" and _CALL_ME.search(query):
        return 3
    if record.core_class == "preferred_address" and "nickname" in query_tokens:
        return 3
    if record.core_class == "preferred_address" and _CALL_ME.search(query) and "again" in query_tokens:
        return 3
    if record.core_class == "important_person_relationship_fact" and _RELATION_TERMS & query_tokens:
        return 2 + overlap if overlap else 0
    if record.core_class == "durable_preference":
        raw_query_terms = set(re.findall(r"[a-z]+", query.lower()))
        raw_content_terms = set(re.findall(r"[a-z]+", record.content.lower()))
        specific_overlap = ((query_tokens & content_tokens) - {"prefer", "favorite", "usual", "like", "preference"}) | (raw_query_terms & raw_content_terms & _PREFERENCE_CATEGORY_TERMS)
        return 2 + len(specific_overlap) if specific_overlap else 0
    return 2 + overlap


def _selection_eligible(record: DurableCoreRecord, score: int, query_tokens: set[str], slots: set[str]) -> bool:
    if record.assertion_actor != "user" or not record.source_event_ids:
        return False
    if record.core_class == "durable_preference":
        return bool(query_tokens & set(_tokens(record.content)))
    return score >= 2 and record.core_class in slots


def _lifecycle_eligible(records: Iterable[DurableCoreRecord] | DurableCoreIndex, record: DurableCoreRecord, at: datetime, historical: bool) -> bool:
    recorded_at = _parse_time(record.recorded_at)
    if recorded_at > at:
        return False
    if record.status == "archived":
        return False
    if not historical:
        return record.status == "active"
    if record.status == "active":
        return True
    if record.status != "superseded" or not record.superseded_by:
        return False
    successor = (
        records.by_claim_id.get(record.superseded_by)
        if isinstance(records, DurableCoreIndex)
        else next((item for item in records if item.claim_id == record.superseded_by), None)
    )
    return successor is not None and at < _parse_time(successor.recorded_at)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
