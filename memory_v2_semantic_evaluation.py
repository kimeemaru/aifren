"""Offline ground-truth evaluation for the non-authoritative Memory V2 index.

This is deliberately a diagnostic harness, not a source of prompt context.  It
uses seeded natural-language fixtures, explicit expected claim IDs, and local
embedding providers so a V1/V2 ranking disagreement is never treated as the
definition of correctness.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import random
import time
import uuid

from benchmarks.memory_v2.models import RetrievalQuery
from memory_v2_store import (
    EmbeddingLifecycle,
    MemoryV2Repository,
    MemoryV2Store,
    MiniLMEmbeddingProvider,
    SemanticRetrievalV2,
)


SEED = 20260822


@dataclass(frozen=True)
class SemanticCase:
    category: str
    character_id: str
    query: str
    expected_claim_id: str | None = None
    forbidden_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Metric:
    cases: int
    top1_hit_rate: float
    top3_recall: float
    top5_recall: float
    mean_reciprocal_rank: float
    abstention_false_positive_rate: float
    forbidden_leak_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SemanticEvaluationReport:
    scale: int
    embed_seconds: float
    query_seconds: float
    baseline_fts: dict[str, Metric]
    semantic_v2: dict[str, Metric]
    lifecycle: dict[str, int]
    character_scope_leaks: int
    duplicate_active_claims: int

    def to_dict(self) -> dict:
        return {
            "scale": self.scale,
            "embed_seconds": round(self.embed_seconds, 4),
            "query_seconds": round(self.query_seconds, 4),
            "baseline_fts": {key: value.to_dict() for key, value in self.baseline_fts.items()},
            "semantic_v2": {key: value.to_dict() for key, value in self.semantic_v2.items()},
            "lifecycle": self.lifecycle,
            "character_scope_leaks": self.character_scope_leaks,
            "duplicate_active_claims": self.duplicate_active_claims,
        }


def run_semantic_evaluation(*, scale: int = 1_000, seed: int = SEED) -> SemanticEvaluationReport:
    """Evaluate FTS-only baseline and V2 hybrid retrieval with known truth.

    ``scale`` adds unrelated history records.  The tested queries retain
    natural-language paraphrases and are therefore intentionally more
    demanding than an exact-token fixture.
    """
    store = MemoryV2Store(":memory:")
    repository = MemoryV2Repository(store)
    character_a = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-semantic-eval-a"))
    character_b = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-semantic-eval-b"))
    repository.ensure_character(character_a, "Evaluation A")
    repository.ensure_character(character_b, "Evaluation B")
    try:
        _seed_core_claims(store, repository, character_a, character_b)
        _seed_long_history(store, character_a, scale=scale, seed=seed)
        store.ensure_fts()
        provider = MiniLMEmbeddingProvider()
        embedding_started = time.perf_counter()
        lifecycle = EmbeddingLifecycle(store, provider).rebuild_all()
        embed_seconds = time.perf_counter() - embedding_started
        cases = _cases(character_a, character_b)
        query_started = time.perf_counter()
        baseline = _evaluate_cases(
            cases,
            lambda case: [item.memory_id for item in repository.search(case.character_id, case.query, limit=5)],
        )
        retriever = SemanticRetrievalV2(store, embedding_provider=provider)
        semantic = _evaluate_cases(
            cases,
            lambda case: list(retriever.retrieve(_query(case)).claim_ids),
        )
        query_seconds = time.perf_counter() - query_started
        active_ids = {
            row["claim_id"]
            for row in store.structural_claims(character_a, _now_us(), historical=False)
        }
        lifecycle_leaks = {
            "superseded": int(bool({"a-location-old", "a-location-middle"} & active_ids)),
            "archived": int("a-archived" in active_ids),
        }
        duplicate_active = store.connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT content FROM claims c WHERE c.character_id=?
                    AND NOT EXISTS (SELECT 1 FROM claim_status_events s
                                    WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                                      AND s.status IN ('superseded', 'archived'))
                   GROUP BY content HAVING COUNT(*) > 1
                )""",
            (character_a,),
        ).fetchone()[0]
        character_scope_leaks = sum(
            int(any(claim_id.startswith("b-") for claim_id in retriever.retrieve(_query(case)).claim_ids))
            for case in cases if case.character_id == character_a
        )
        return SemanticEvaluationReport(
            scale=int(scale),
            embed_seconds=embed_seconds,
            query_seconds=query_seconds,
            baseline_fts=baseline,
            semantic_v2=semantic,
            lifecycle=lifecycle_leaks | {
                "embedding_embedded": int(lifecycle["embedded"]),
                "embedding_failed": int(lifecycle["failed"]),
            },
            character_scope_leaks=character_scope_leaks,
            duplicate_active_claims=int(duplicate_active),
        )
    finally:
        store.close()


def _evaluate_cases(cases: tuple[SemanticCase, ...], retrieve) -> dict[str, Metric]:
    buckets: dict[str, list[tuple[SemanticCase, list[str]]]] = defaultdict(list)
    for case in cases:
        buckets[case.category].append((case, list(retrieve(case))))
    return {category: _metric(items) for category, items in sorted(buckets.items())}


def _metric(items: list[tuple[SemanticCase, list[str]]]) -> Metric:
    expected = [(case, ids) for case, ids in items if case.expected_claim_id]
    abstentions = [(case, ids) for case, ids in items if not case.expected_claim_id]
    top1 = sum(bool(ids and ids[0] == case.expected_claim_id) for case, ids in expected)
    top3 = sum(case.expected_claim_id in ids[:3] for case, ids in expected)
    top5 = sum(case.expected_claim_id in ids[:5] for case, ids in expected)
    reciprocal = sum(1.0 / (ids.index(case.expected_claim_id) + 1)
                     for case, ids in expected if case.expected_claim_id in ids)
    false_positives = sum(bool(ids) for _, ids in abstentions)
    forbidden = sum(
        1 for case, ids in items if set(case.forbidden_claim_ids) & set(ids)
    )
    denominator = len(expected)
    return Metric(
        cases=len(items),
        top1_hit_rate=top1 / denominator if denominator else 0.0,
        top3_recall=top3 / denominator if denominator else 0.0,
        top5_recall=top5 / denominator if denominator else 0.0,
        mean_reciprocal_rank=reciprocal / denominator if denominator else 0.0,
        abstention_false_positive_rate=false_positives / len(abstentions) if abstentions else 0.0,
        forbidden_leak_count=forbidden,
    )


def _query(case: SemanticCase) -> RetrievalQuery:
    return RetrievalQuery(
        case.character_id,
        case.query,
        "2026-08-22T12:00:00+00:00",
        "ordinary",
        (),
    )


def _seed_core_claims(store, repository, character_a: str, character_b: str) -> None:
    _add_claim(store, character_a, "a-coffee", "preference", "The user prefers dark roast coffee.")
    _add_claim(store, character_a, "a-tea", "preference", "The user prefers jasmine green tea in the afternoon.")
    _add_claim(store, character_a, "a-machine", "fact", "The user bought an espresso machine for the kitchen.")
    _add_claim(store, character_a, "a-cafe", "shared_episode", "We visited a small coffee cafe together last spring.")
    _add_claim(store, character_a, "a-audio", "shared_episode", "We configured the user's Linux audio setup together on August 20.")
    _add_claim(store, character_a, "a-speakers", "shared_episode", "We compared replacement speakers after a sound problem.")
    _add_claim(store, character_a, "a-location-old", "location", "The user lived in Harbor City.")
    _add_claim(store, character_a, "a-location-middle", "location", "The user later lived in Meadow Town.")
    _add_claim(store, character_a, "a-location-current", "location", "The user now lives in Cedar Ridge.")
    repository.supersede(character_a, "a-location-old", "a-location-middle")
    repository.supersede(character_a, "a-location-middle", "a-location-current")
    _add_claim(store, character_a, "a-archived", "fact", "The user once owned an obsolete pager.")
    repository.archive(character_a, "a-archived")
    _add_claim(store, character_b, "b-coffee", "preference", "The user prefers light roast coffee.")
    _add_claim(store, character_b, "b-audio", "shared_episode", "We repaired the user's Windows microphone on August 20.")


def _seed_long_history(store, character_id: str, *, scale: int, seed: int) -> None:
    randomizer = random.Random(seed)
    topics = ("gardening", "astronomy", "recipes", "cycling", "books", "weather", "movies")
    for index in range(max(0, int(scale))):
        topic = topics[index % len(topics)]
        adjective = ("quiet", "curious", "ordinary", "distant", "small")[randomizer.randrange(5)]
        _add_claim(
            store,
            character_id,
            f"filler-{index}",
            "shared_episode" if index % 5 == 0 else "fact",
            f"On a previous day we discussed {adjective} {topic} detail number {index}.",
        )


def _cases(character_a: str, character_b: str) -> tuple[SemanticCase, ...]:
    return (
        SemanticCase("facts", character_a, "What kind of coffee do I like?", "a-coffee"),
        SemanticCase("paraphrases", character_a, "Do you remember my coffee preference?", "a-coffee"),
        SemanticCase("paraphrases", character_a, "Would I probably choose a light roast?", "a-coffee", ("b-coffee",)),
        SemanticCase("episodes", character_a, "What did we work on with my audio?", "a-audio"),
        SemanticCase("episodes", character_a, "Did we ever troubleshoot sound together?", "a-audio"),
        SemanticCase("temporal", character_a, "What were we doing on August 20?", "a-audio"),
        SemanticCase("corrections", character_a, "Where do I live now?", "a-location-current", ("a-location-old", "a-location-middle")),
        SemanticCase("distractors", character_a, "Which roast did I say I preferred?", "a-coffee", ("a-tea", "a-machine", "a-cafe")),
        SemanticCase("abstention", character_a, "What is my favourite constellation?"),
        SemanticCase("abstention", character_a, "Do you remember my passport number?"),
        SemanticCase("character_isolation", character_a, "What coffee roast do I prefer?", "a-coffee", ("b-coffee",)),
        SemanticCase("character_isolation", character_b, "What coffee roast do I prefer?", "b-coffee", ("a-coffee",)),
    )


def _add_claim(store, character_id: str, claim_id: str, claim_type: str, content: str) -> None:
    sequence = store.connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
    ).fetchone()[0]
    event_id = f"event-{claim_id}"
    store.add_event(character_id, event_id, sequence, content_text=content, source_origin="semantic_evaluation")
    store.add_claim(character_id, claim_id, claim_type=claim_type, assertion_scope="user_fact",
                    content=content, provenance_state="complete")
    store.attach_evidence(character_id, claim_id, event_id)


def _now_us() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000)
