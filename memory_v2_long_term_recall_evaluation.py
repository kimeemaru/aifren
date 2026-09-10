"""Privacy-safe V1/V2 long-term recall evaluation.

The harness invokes production V1 ranking and the existing
``SemanticRetrievalV2`` authority side by side.  It never reads application
persistence and never inserts either result into a production prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import argparse
import json
import re
import threading
from typing import Iterable

from benchmarks.memory_v2.models import (
    BenchmarkFixture,
    GoldClaim,
    RetrievalQuery,
    SyntheticEvent,
)
from memory.memory import Memory, generate_memory_keywords
from memory_v2_store import EmbeddingLifecycle, MemoryV2Store, MiniLMEmbeddingProvider
from memory_v2_store.importer import import_fixture
from memory_v2_store.retrieval import RetrievalLimits, SemanticRetrievalV2
from memory_v2_episode_compaction import (
    EpisodeCompactionCache,
    EpisodeCompactor,
    canonical_record_id,
)


EVALUATION_VERSION = "long-term-recall-v1"
DEFAULT_K = 5


@dataclass(frozen=True)
class LongTermRecallCase:
    case_id: str
    category: str
    query: str
    expected_claim_ids: tuple[str, ...]
    relevant_claim_ids: tuple[str, ...]
    forbidden_claim_ids: tuple[str, ...] = ()
    expected_source_ids: tuple[str, ...] = ()
    diagnostic_hint: str = ""

    @property
    def is_negative(self) -> bool:
        return not self.expected_claim_ids


@dataclass(frozen=True)
class SystemCaseResult:
    candidate_ids: tuple[str, ...]
    candidate_lanes: tuple[tuple[str, tuple[str, ...]], ...]
    correct_evidence_appeared: bool
    expected_rank: int | None
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float
    false_recall: bool
    abstention_correct: bool
    abstention_reason: str = ""


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    query: str
    expected_claim_ids: tuple[str, ...]
    expected_source_ids: tuple[str, ...]
    v1: SystemCaseResult
    v2: SystemCaseResult
    v2_miss_classification: str = ""


@dataclass(frozen=True)
class AggregateMetrics:
    cases: int
    positive_cases: int
    negative_cases: int
    evidence_hit_rate: float
    recall_at_k: float
    precision_at_k: float
    mean_reciprocal_rank: float
    false_recall_rate: float
    abstention_accuracy: float
    forbidden_result_rate: float


@dataclass(frozen=True)
class BaselineReport:
    version: str
    noise_claims: int
    k: int
    v1: AggregateMetrics
    v2: AggregateMetrics
    cases: tuple[CaseResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PostChangeCaseResult:
    case_id: str
    result: SystemCaseResult


@dataclass(frozen=True)
class ComparisonReport:
    baseline: BaselineReport
    post_v2: AggregateMetrics
    post_cases: tuple[PostChangeCaseResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _V1EmbeddingAdapter:
    """Expose the shared MiniLM provider through Memory V1's small boundary."""

    def __init__(self, provider: MiniLMEmbeddingProvider) -> None:
        self.provider = provider

    def encode(self, text: str) -> list[float]:
        return self.provider.embed([str(text)])[0]


class _V1Recall:
    def __init__(
        self,
        fixture: BenchmarkFixture,
        provider: MiniLMEmbeddingProvider,
        *,
        extra_memories: tuple[tuple[str, str, int], ...] = (),
    ) -> None:
        memory = Memory.__new__(Memory)
        memory._lock = threading.RLock()
        memory._mutation_observers = []
        memory.llm = None
        memory.embedding_model = _V1EmbeddingAdapter(provider)
        source = [
            (claim.claim_id, claim.category, claim.content, claim.importance)
            for claim in fixture.claims if claim.character_id == "primary"
        ]
        source.extend(
            (claim_id, "episode", content, importance)
            for claim_id, content, importance in extra_memories
        )
        vectors = provider.embed([content for _claim_id, _category, content, _importance in source])
        memory.memories = []
        self.claim_id_by_memory_id: dict[int, str] = {}
        for memory_id, ((claim_id, category, content, importance), vector) in enumerate(
            zip(source, vectors), start=1,
        ):
            record = {
                "id": memory_id,
                "category": category,
                "content": content,
                "importance": importance,
                "created": "2010-01-01T00:00:00+00:00",
                "updated": "2026-01-01T00:00:00+00:00",
                "embedding": vector,
            }
            record["keywords"] = generate_memory_keywords(record)
            memory.memories.append(record)
            self.claim_id_by_memory_id[memory_id] = claim_id
        self.memory = memory

    def retrieve(self, text: str, limit: int) -> tuple[str, ...]:
        rows = self.memory.get_relevant_memories(text, max_memories=limit)
        return tuple(self.claim_id_by_memory_id[int(row["id"])] for row in rows)


def build_long_history_fixture(*, noise_claims: int = 240) -> tuple[
    BenchmarkFixture,
    tuple[LongTermRecallCase, ...],
    tuple[tuple[str, str, int], ...],
]:
    """Build one fixed, noisy, wholly synthetic recall corpus."""
    if noise_claims < 100:
        raise ValueError("long-term evaluation requires at least 100 noise claims")
    events: list[SyntheticEvent] = []
    claims: list[GoldClaim] = []

    def add(
        claim_id: str,
        content: str,
        *,
        category: str = "fact",
        at: str,
        status: str = "active",
        superseded_by: str | None = None,
        importance: int = 5,
        event_id: str | None = None,
    ) -> None:
        source_id = event_id or f"event-{claim_id}"
        if not any(value.event_id == source_id for value in events):
            events.append(SyntheticEvent(source_id, "primary", at, content))
        claims.append(GoldClaim(
            claim_id, "primary", category, content, (source_id,), status,
            superseded_by, None, None, claim_id, importance,
        ))

    add("locker-code", "The archive locker code is cobalt-orchid.", at="2011-01-04T10:00:00Z", importance=7)
    add("storm-picnic", "A sudden storm moved our riverside picnic beneath the old station canopy.", category="episode", at="2011-06-18T12:00:00Z")
    add("rare-object", "The user keeps a zaffre-blue orrery key in the desk drawer.", at="2012-03-09T09:00:00Z")
    add("named-person", "Dr. Veda Nkomo taught the user to restore mechanical clocks.", at="2012-11-20T18:00:00Z")
    add("goose-anecdote", "A goose stole the user's yellow mitten during an old canal walk.", category="episode", at="2013-02-02T16:00:00Z")
    add("ceramics-first", "The user attended a ceramics class at North Studio.", category="episode", at="2014-01-10T19:00:00Z")
    add("ceramics-middle", "The user attended another ceramics class at Market Studio.", category="episode", at="2017-04-08T19:00:00Z")
    add("ceramics-latest", "The user's most recent ceramics class was at Harbor Studio.", category="episode", at="2025-05-16T19:00:00Z")
    add("visit-library", "The user and companion visited the Ash Library.", category="episode", at="2016-03-01T12:00:00Z")
    add("visit-cafe", "The user and companion visited the Lantern Cafe.", category="episode", at="2016-03-02T12:00:00Z")
    add("visit-garden", "The user and companion visited the Glass Garden.", category="episode", at="2016-03-03T12:00:00Z")
    add("location-old", "The user lived in Alder Bay.", category="location", at="2018-01-01T09:00:00Z", status="superseded", superseded_by="location-current")
    add("location-current", "The user now lives in Copper Ridge.", category="location", at="2023-09-12T09:00:00Z", importance=7)
    shared_event = "event-cedar-lake-imani"
    events.append(SyntheticEvent(
        shared_event, "primary", "2019-08-21T14:00:00Z",
        "At Cedar Lake we met Imani, who lent me a brass compass.",
    ))
    add("cedar-lake-meeting", "The user and companion met Imani at Cedar Lake.", category="episode", at="2019-08-21T14:00:00Z", event_id=shared_event)
    add("imani-compass", "Imani lent the user a brass compass.", category="fact", at="2019-08-21T14:00:00Z", event_id=shared_event)
    add("pine-lake-compass", "At Pine Lake, Rowan showed the user a silver compass.", category="episode", at="2020-07-10T14:00:00Z")
    add("passport-case", "The user's passport case is maroon.", at="2022-05-03T08:00:00Z")

    start = datetime(2010, 1, 1, tzinfo=timezone.utc)
    topics = (
        "garden soil", "train timetable", "bread recipe", "camera strap",
        "rain jacket", "museum map", "coffee grinder", "astronomy book",
        "bicycle light", "wooden puzzle", "canal bridge", "clock battery",
    )
    adjectives = ("amber", "quiet", "folded", "ordinary", "striped", "small", "winter")
    for index in range(noise_claims):
        at = (start + timedelta(days=index * 19 + 3)).isoformat().replace("+00:00", "Z")
        topic = topics[index % len(topics)]
        adjective = adjectives[(index * 5) % len(adjectives)]
        add(
            f"noise-{index:04d}",
            f"Synthetic archive note {index}: the user discussed a {adjective} {topic} detail numbered {index % 31}.",
            category="episode" if index % 7 == 0 else "fact",
            at=at,
            importance=3 + (index % 4),
        )

    # A second character deliberately contains close lexical distractors.
    events.extend((
        SyntheticEvent("other-locker", "other", "2015-01-01T00:00:00Z", "The archive locker code is cobalt-iris."),
        SyntheticEvent("other-cafe", "other", "2016-03-02T00:00:00Z", "The companion visited Lantern Cafe with the user."),
    ))
    claims.extend((
        GoldClaim("other-locker", "other", "fact", "The archive locker code is cobalt-iris.", ("other-locker",)),
        GoldClaim("other-cafe", "other", "episode", "The user visited Lantern Cafe with another companion.", ("other-cafe",)),
    ))

    cases = (
        LongTermRecallCase("exact-old-detail", "exact_old_detail", 'What was my archive locker code "cobalt-orchid"?', ("locker-code",), ("locker-code",), expected_source_ids=("event-locker-code",)),
        LongTermRecallCase("paraphrased-old-detail", "paraphrased_old_detail", "Where did we shelter when bad weather ruined that old outdoor lunch?", ("storm-picnic",), ("storm-picnic",), expected_source_ids=("event-storm-picnic",)),
        LongTermRecallCase("rare-exact-term", "rare_exact_term", "Where is my zaffre-blue orrery key?", ("rare-object",), ("rare-object",), expected_source_ids=("event-rare-object",)),
        LongTermRecallCase("named-entity", "named_entity", "Who was Dr. Veda Nkomo?", ("named-person",), ("named-person",), expected_source_ids=("event-named-person",), diagnostic_hint="missing_entity_signal"),
        LongTermRecallCase("corrected-fact", "correction", "Where do I live now?", ("location-current",), ("location-current",), ("location-old",), ("event-location-current",)),
        LongTermRecallCase("incidental-anecdote", "old_incidental_anecdote", "What happened to my yellow mitten on that canal walk?", ("goose-anecdote",), ("goose-anecdote",), expected_source_ids=("event-goose-anecdote",)),
        LongTermRecallCase("historical-episode", "historical_episode", "What old outing involved a station canopy?", ("storm-picnic",), ("storm-picnic",), expected_source_ids=("event-storm-picnic",)),
        LongTermRecallCase("first-occurrence", "temporal_first", "Where was my first ceramics class?", ("ceramics-first",), ("ceramics-first", "ceramics-middle", "ceramics-latest"), expected_source_ids=("event-ceramics-first",), diagnostic_hint="missing_temporal_reasoning"),
        LongTermRecallCase("latest-occurrence", "temporal_latest", "Where was my most recent ceramics class?", ("ceramics-latest",), ("ceramics-first", "ceramics-middle", "ceramics-latest"), expected_source_ids=("event-ceramics-latest",), diagnostic_hint="missing_temporal_reasoning"),
        LongTermRecallCase("before-relation", "temporal_relation", "What did we do immediately before visiting the Lantern Cafe?", ("visit-library",), ("visit-library",), ("visit-cafe", "visit-garden"), ("event-visit-library",), "missing_temporal_reasoning"),
        LongTermRecallCase("associative-source", "associative", "What unusual object was connected to our Cedar Lake meeting?", ("imani-compass",), ("imani-compass", "cedar-lake-meeting"), ("pine-lake-compass",), (shared_event,), "missing_associative_expansion"),
        LongTermRecallCase("similar-wrong-distractor", "distractor", "Which compass did I borrow from the person we met at Cedar Lake?", ("imani-compass",), ("imani-compass", "cedar-lake-meeting"), ("pine-lake-compass",), (shared_event,)),
        LongTermRecallCase("never-stated-passport", "negative", "What is my passport number?", (), (), ("passport-case",)),
        LongTermRecallCase("never-stated-telescope", "negative", "Which telescope did I buy?", (), ()),
        LongTermRecallCase("compacted-episode-only", "compacted_episode", "What did we build that projected Orion crookedly?", ("raw-planetarium",), ("raw-planetarium",), expected_source_ids=(_planetarium_source_id(),), diagnostic_hint="missing_episode_participation"),
    )
    fixture = BenchmarkFixture(EVALUATION_VERSION, tuple(events), tuple(claims), ())
    extra_v1 = (("raw-planetarium", "The user and companion built a cardboard planetarium that projected Orion crookedly.", 6),)
    return fixture, cases, extra_v1


def _episode_messages() -> tuple[dict[str, object], ...]:
    messages: list[dict[str, object]] = []
    for index in range(45):
        user_content = (
            "We built a cardboard planetarium that projected Orion crookedly."
            if index == 0 else f"Routine synthetic exchange {index}."
        )
        messages.extend((
            {"role": "user", "content": user_content,
             "timestamp": f"2020-01-01T00:{index:02d}:00Z"},
            {"role": "assistant", "content": f"Acknowledged synthetic exchange {index}.",
             "timestamp": f"2020-01-01T00:{index:02d}:01Z"},
        ))
    return tuple(messages)


def _planetarium_source_id() -> str:
    messages = _episode_messages()
    return canonical_record_id(0, messages[0])


class _ExtractiveEpisodeProvider:
    """Deterministic, generic extractive provider for synthetic compaction."""

    def generate(self, _context, prompt, *, seed=None):
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        if "Return one strict JSON object" in prompt:
            return '{"consolidate":false,"first_episode":0,"last_episode":0,"account":""}'
        user_lines = re.findall(r"\[RECORD \d+\] USER: ([^\n]+)", prompt)
        if user_lines:
            return "During this period, " + " ".join(user_lines)[:2800]
        return "The synthetic participants continued their bounded conversation period."


def run_baseline(*, noise_claims: int = 240, k: int = DEFAULT_K) -> BaselineReport:
    """Run the unchanged V1 and SemanticRetrievalV2 baseline."""
    if not 1 <= k <= 5:
        raise ValueError("k must be from 1 to 5")
    fixture, cases, extra_v1 = build_long_history_fixture(noise_claims=noise_claims)
    provider = MiniLMEmbeddingProvider()
    v1 = _V1Recall(fixture, provider, extra_memories=extra_v1)
    store = MemoryV2Store()
    try:
        characters = import_fixture(store, fixture)
        EmbeddingLifecycle(store, provider).rebuild_all()
        retriever = SemanticRetrievalV2(
            store,
            RetrievalLimits(final_count=k, token_budget=300),
            embedding_provider=provider,
            # Preserve the pre-tranche admission policy so the baseline
            # remains reproducible after the justified post-baseline fix.
            enable_explicit_attribute_gate=False,
        )
        primary = characters["primary"]
        per_case: list[CaseResult] = []
        for case in cases:
            v1_ids = v1.retrieve(case.query, k)
            outcome = retriever.retrieve(RetrievalQuery(
                primary, case.query, "2026-08-30T12:00:00Z", "ordinary", (),
            ), final_count=k, token_budget=300)
            v2_ids = tuple(outcome.claim_ids)
            channels = {
                trace.claim_id: trace.candidate_channels
                for trace in outcome.traces if trace.claim_id != "__query__"
            }
            v1_result = _score(case, v1_ids, ((claim_id, ("production_v1",)) for claim_id in v1_ids), k)
            v2_result = _score(case, v2_ids, ((claim_id, channels.get(claim_id, ())) for claim_id in v2_ids), k, outcome.abstention_reason or "")
            v2_failure = (
                case.is_negative and v2_result.false_recall
                or (not case.is_negative and v2_result.expected_rank != 1)
            )
            per_case.append(CaseResult(
                case.case_id, case.category, case.query, case.expected_claim_ids,
                case.expected_source_ids, v1_result, v2_result,
                _classify_v2_miss(case, outcome) if v2_failure else "",
            ))
        results = tuple(per_case)
        return BaselineReport(
            EVALUATION_VERSION, noise_claims, k,
            _aggregate(cases, tuple(value.v1 for value in results)),
            _aggregate(cases, tuple(value.v2 for value in results)),
            results,
        )
    finally:
        store.close()


def run_comparison(*, noise_claims: int = 240, k: int = DEFAULT_K) -> ComparisonReport:
    """Run the fixed baseline, then the same cases through the thin seam."""
    baseline = run_baseline(noise_claims=noise_claims, k=k)
    fixture, cases, _extra_v1 = build_long_history_fixture(noise_claims=noise_claims)
    provider = MiniLMEmbeddingProvider()
    store = MemoryV2Store()
    try:
        characters = import_fixture(store, fixture)
        EmbeddingLifecycle(store, provider).rebuild_all()
        primary = characters["primary"]
        semantic = SemanticRetrievalV2(
            store, RetrievalLimits(final_count=k, token_budget=300), embedding_provider=provider,
        )
        messages = _episode_messages()
        cache = EpisodeCompactionCache(store, primary)
        cache.rebuild(messages, EpisodeCompactor(_ExtractiveEpisodeProvider()))
        validation = cache.validate_for_context(messages)
        episode_sources = {
            record.record_id: frozenset(str(value) for value in (record.metadata or {}).get("source_record_ids", ()))
            for record in validation.lower_records if record.accepted
        }
        from memory_v2_hybrid_recall import HybridMemoryV2Recall
        hybrid = HybridMemoryV2Recall(
            store, primary, semantic_retriever=semantic,
            episode_cache=cache, canonical_messages=messages,
        )
        results: list[PostChangeCaseResult] = []
        scored: list[SystemCaseResult] = []
        for case in cases:
            outcome = hybrid.retrieve(
                RetrievalQuery(primary, case.query, "2026-08-30T12:00:00Z", "ordinary", ()),
                limit=k,
            )
            candidate_ids = tuple(value.memory_id for value in outcome.candidates)
            support = {
                value.memory_id: episode_sources.get(value.memory_id, frozenset())
                for value in outcome.candidates
            }
            result = _score_hybrid(case, outcome, candidate_ids, support, k)
            scored.append(result)
            results.append(PostChangeCaseResult(case.case_id, result))
        return ComparisonReport(
            baseline, _aggregate(cases, tuple(scored)), tuple(results),
        )
    finally:
        store.close()


def _score_hybrid(case, outcome, candidate_ids, support, k) -> SystemCaseResult:
    expected = set(case.expected_claim_ids)
    relevant = set(case.relevant_claim_ids)
    expected_sources = set(case.expected_source_ids)
    matched_positions = [
        index for index, candidate_id in enumerate(candidate_ids[:k])
        if candidate_id in expected or bool(expected_sources & set(support.get(candidate_id, ())))
    ]
    relevant_positions = [
        index for index, candidate_id in enumerate(candidate_ids[:k])
        if candidate_id in relevant or bool(expected_sources & set(support.get(candidate_id, ())))
    ]
    rank = matched_positions[0] + 1 if matched_positions else None
    if case.is_negative:
        recall = float(not candidate_ids)
    else:
        recall = 1.0 if rank is not None else 0.0
    return SystemCaseResult(
        candidate_ids,
        tuple((value.memory_id, (value.lane,)) for value in outcome.candidates),
        (not candidate_ids) if case.is_negative else rank is not None,
        rank,
        recall,
        len(relevant_positions) / len(candidate_ids[:k]) if candidate_ids[:k] else float(case.is_negative),
        1.0 / rank if rank else 0.0,
        case.is_negative and bool(candidate_ids),
        (not candidate_ids) if case.is_negative else rank is not None,
        outcome.abstention_reason,
    )


def _score(
    case: LongTermRecallCase,
    candidate_ids: tuple[str, ...],
    lanes: Iterable[tuple[str, tuple[str, ...]]],
    k: int,
    abstention_reason: str = "",
) -> SystemCaseResult:
    expected = set(case.expected_claim_ids)
    relevant = set(case.relevant_claim_ids)
    hits = expected & set(candidate_ids[:k])
    ranks = [candidate_ids.index(value) + 1 for value in case.expected_claim_ids if value in candidate_ids[:k]]
    rank = min(ranks) if ranks else None
    false_recall = case.is_negative and bool(candidate_ids)
    return SystemCaseResult(
        candidate_ids,
        tuple(lanes),
        bool(hits) if expected else not candidate_ids,
        rank,
        len(hits) / len(expected) if expected else float(not candidate_ids),
        len(relevant & set(candidate_ids[:k])) / len(candidate_ids[:k]) if candidate_ids[:k] else float(case.is_negative),
        1.0 / rank if rank else 0.0,
        false_recall,
        (not candidate_ids) if case.is_negative else bool(hits),
        abstention_reason,
    )


def _aggregate(
    cases: tuple[LongTermRecallCase, ...],
    results: tuple[SystemCaseResult, ...],
) -> AggregateMetrics:
    positives = [(case, result) for case, result in zip(cases, results) if not case.is_negative]
    negatives = [(case, result) for case, result in zip(cases, results) if case.is_negative]
    expected_count = sum(len(case.expected_claim_ids) for case, _ in positives)
    hits = sum(round(result.recall_at_k * len(case.expected_claim_ids)) for case, result in positives)
    forbidden = sum(bool(set(case.forbidden_claim_ids) & set(result.candidate_ids)) for case, result in zip(cases, results))
    return AggregateMetrics(
        len(cases), len(positives), len(negatives),
        sum(result.correct_evidence_appeared for _case, result in positives) / len(positives),
        hits / expected_count if expected_count else 0.0,
        sum(result.precision_at_k for result in results) / len(results),
        sum(result.reciprocal_rank for _case, result in positives) / len(positives),
        sum(result.false_recall for _case, result in negatives) / len(negatives),
        sum(result.abstention_correct for result in results) / len(results),
        forbidden / len(cases),
    )


def _classify_v2_miss(case: LongTermRecallCase, outcome) -> str:
    if case.is_negative:
        return "abstention_too_weak"
    if case.diagnostic_hint:
        return case.diagnostic_hint
    expected_traces = [
        trace for trace in outcome.traces if trace.claim_id in set(case.expected_claim_ids)
    ]
    if not expected_traces:
        return "candidate_never_generated"
    if any(trace.exclusion_reason == "below_final_relevance_gate" for trace in expected_traces):
        return "abstention_too_aggressive"
    if any(trace.exclusion_reason in {"final_count_cap", "token_budget_cap"} for trace in expected_traces):
        return "ranking_or_fusion_error"
    if any(trace.suppression_reason for trace in expected_traces):
        return "scope_or_lifecycle_rejection"
    return "ranking_or_fusion_error"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noise", type=int, default=240)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--post", action="store_true", help="also run the thin hybrid seam")
    arguments = parser.parse_args()
    report = (
        run_comparison(noise_claims=arguments.noise, k=arguments.k)
        if arguments.post else run_baseline(noise_claims=arguments.noise, k=arguments.k)
    )
    if arguments.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        baseline = report.baseline if isinstance(report, ComparisonReport) else report
        print("V1", baseline.v1)
        print("V2", baseline.v2)
        if isinstance(report, ComparisonReport):
            print("POST V2", report.post_v2)
        for case in baseline.cases:
            print(case.case_id, "V1", case.v1.candidate_ids, "V2", case.v2.candidate_ids, case.v2_miss_classification)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
