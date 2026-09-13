"""Independent post-fix challenge for shadow Memory V2 long-term recall.

This synthetic suite is separate from the 88-case adversarial corpus that
guided the tranche. It must not change production prompt authority: V1 remains
prompt-facing and hybrid V2 remains evaluation/shadow-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from aifren.memory_v2_store.models import BenchmarkFixture, GoldClaim, RetrievalQuery, SyntheticEvent
from tools.memory_v2.memory_v2_adversarial_recall_evaluation import (
    AdversarialCase,
    CaseComparison,
    Metrics,
    _by_category,
    _evaluate,
    _metrics,
)
from aifren.continuity.memory_v2_hybrid_recall import HybridMemoryV2Recall
from tools.memory_v2.memory_v2_long_term_recall_evaluation import _V1Recall
from aifren.memory_v2_store import EmbeddingLifecycle, MemoryV2Store, MiniLMEmbeddingProvider
from aifren.memory_v2_store.importer import import_fixture
from aifren.memory_v2_store.retrieval import RetrievalLimits, SemanticRetrievalV2


CHALLENGE_VERSION = "memory-v2-recall-challenge-v1"
DEFAULT_HISTORY_SIZES = (500, 4000)
K = 5


@dataclass(frozen=True)
class ChallengeScale:
    history_size: int
    corpus_claims: int
    v1: Metrics
    v2: Metrics
    v1_by_category: tuple[tuple[str, Metrics], ...]
    v2_by_category: tuple[tuple[str, Metrics], ...]
    cases: tuple[CaseComparison, ...]


@dataclass(frozen=True)
class ChallengeReport:
    version: str
    history_sizes: tuple[int, ...]
    case_count: int
    scales: tuple[ChallengeScale, ...]


def build_challenge_fixture(noise_claims: int) -> tuple[BenchmarkFixture, tuple[AdversarialCase, ...]]:
    """Build authored holdout evidence independently of retrieval output."""
    if noise_claims < 500:
        raise ValueError("challenge histories require at least 500 noise claims")
    events: list[SyntheticEvent] = []
    claims: list[GoldClaim] = []
    cases: list[AdversarialCase] = []

    def add(claim_id: str, content: str, at: str, *, category: str = "fact") -> None:
        event_id = f"event-{claim_id}"
        events.append(SyntheticEvent(event_id, "primary", at, content))
        claims.append(GoldClaim(
            claim_id, "primary", category, content, (event_id,),
            topic=claim_id, importance=5,
        ))

    paraphrases = (
        ("power-supper", "A power failure moved the anniversary supper into the subway concourse.",
         "Where did the celebratory meal end up when the electricity failed?"),
        ("river-pottery", "A rising river forced the pottery lesson into the library basement.",
         "Where was the clay workshop relocated when the water climbed?"),
        ("heater-choir", "A broken heater sent the choir rehearsal to the bakery loft.",
         "Where did the singing practice move after the room lost its heat?"),
        ("fog-picnic", "Dense fog shifted the boat picnic into the harbor conservatory.",
         "Where was the meal gathering held when visibility stopped the voyage?"),
        ("hail-market", "A hailstorm moved the craft market beneath the railway viaduct.",
         "Where did the handmade-goods fair shelter from the ice storm?"),
        ("lift-dance", "A failed lift moved the dance lesson to the museum foyer.",
         "Where was the movement class held when the elevator broke?"),
    )
    for index, (claim_id, content, query) in enumerate(paraphrases):
        add(claim_id, content, f"2010-0{index + 1}-10T10:00:00Z", category="shared_episode")
        cases.append(AdversarialCase(
            claim_id, "challenge_paraphrase", query, (claim_id,), (claim_id,),
            expected_source_ids=(f"event-{claim_id}",), likely_failure="semantic_miss",
        ))

    entities = (
        ("entity-roe", "Tamsin Roe restores the user's harpsichord.", "What does Tamsin Roe restore?"),
        ("entity-shed", "The user stores sled wax at Kestrel Shed.", "What do I keep at Kestrel Shed?"),
        ("entity-gauge", "The indigo pressure gauge hangs beside the kiln.", "Where is the indigo pressure gauge?"),
        ("entity-miso", "The user's friend Miso wears a copper name tag.", "What color name tag does Miso wear?"),
        ("entity-venn", "Dr. Saira Venn calibrates the user's weather instruments.", "What does Saira Venn calibrate?"),
        ("entity-orin", "Guide Orin Hale keeps the spare trail compass at Flint Lodge.", "Where does Orin Hale keep the trail compass?"),
    )
    for index, (claim_id, content, query) in enumerate(entities):
        add(claim_id, content, f"2011-0{index + 1}-11T11:00:00Z")
        cases.append(AdversarialCase(
            claim_id, "challenge_entity", query, (claim_id,), (claim_id,),
            expected_source_ids=(f"event-{claim_id}",),
        ))

    supported_actions = (
        ("action-buy", "The user bought a compact telescope at the observatory sale.", "Which telescope did I buy?"),
        ("action-meet", "The user met Leda Mor at the glass arcade.", "Where did I meet Leda Mor?"),
        ("action-build", "The user and companion built a cedar model bridge.", "What did we build from cedar?"),
        ("action-sell", "The user sold the striped canoe at North Wharf.", "Where did I sell the striped canoe?"),
    )
    for index, (claim_id, content, query) in enumerate(supported_actions):
        add(claim_id, content, f"2012-0{index + 1}-12T12:00:00Z", category="shared_episode")
        cases.append(AdversarialCase(
            claim_id, "challenge_supported_action", query, (claim_id,), (claim_id,),
            expected_source_ids=(f"event-{claim_id}",),
        ))

    negatives = (
        ("negative-dentist", "Who is my dentist?", "near-dental", "The user owns a dental anatomy atlas."),
        ("negative-microscope", "Which microscope did I buy?", "near-microscope", "The user discussed a microscope buyer's guide."),
        ("negative-kayak", "Why did I sell my kayak?", "near-kayak", "The user rents a cedar kayak each summer."),
        ("negative-sculpture", "What sculpture did I make?", "near-sculpture", "The user viewed a bronze sculpture exhibit."),
        ("negative-architect", "Who was my architect?", "near-architect", "The user read an architecture history book."),
        ("negative-scarf", "Which scarf did I wear?", "near-scarf", "The user owns an emerald scarf pattern."),
    )
    for index, (case_id, query, claim_id, content) in enumerate(negatives):
        add(claim_id, content, f"2013-0{index + 1}-13T13:00:00Z")
        cases.append(AdversarialCase(
            case_id, "challenge_negative", query, (), (),
            ((claim_id, "unsupported_relation"),), likely_failure="weak_abstention",
        ))

    temporal = (
        ("Amber Gallery", "Brass Theater", "Cedar Courtyard"),
        ("Moss Station", "Ivory Workshop", "Slate Garden"),
    )
    for index, (before, anchor, after) in enumerate(temporal):
        ids = (f"challenge-sequence-{index}-before", f"challenge-sequence-{index}-anchor",
               f"challenge-sequence-{index}-after")
        for offset, (claim_id, place) in enumerate(zip(ids, (before, anchor, after))):
            add(claim_id, f"The user and companion visited {place}.",
                f"2014-0{index + 1}-0{offset + 1}T14:00:00Z", category="shared_episode")
        cases.extend((
            AdversarialCase(
                f"challenge-before-{index}", "challenge_temporal_rank",
                f"What did we visit immediately before {anchor}?", (ids[0],), (ids[0],),
                ((ids[1], "temporal_anchor"), (ids[2], "wrong_neighbor")),
                (f"event-{ids[0]}",), "ranking_fusion",
            ),
            AdversarialCase(
                f"challenge-after-{index}", "challenge_temporal_rank",
                f"What did we visit immediately after {anchor}?", (ids[2],), (ids[2],),
                ((ids[1], "temporal_anchor"), (ids[0], "wrong_neighbor")),
                (f"event-{ids[2]}",), "ranking_fusion",
            ),
        ))

    start = datetime(1985, 1, 1, tzinfo=timezone.utc)
    topics = (
        "harbor telescope", "copper collar", "clock theater", "cedar canoe",
        "pottery basement", "museum elevator", "dental archive", "railway supper",
        "weather instrument", "choir heater", "trail compass", "sculpture catalog",
    )
    modifiers = ("ordinary", "folded", "spare", "old", "striped", "quiet")
    for index in range(noise_claims):
        claim_id = f"challenge-noise-{index:05d}"
        content = (
            f"Synthetic challenge note {index}: the user discussed a "
            f"{modifiers[index % len(modifiers)]} {topics[(index * 5) % len(topics)]} "
            f"reference numbered {index % 113}."
        )
        occurred = (start + timedelta(days=index * 4 + 1)).isoformat().replace("+00:00", "Z")
        add(claim_id, content, occurred, category="shared_episode" if index % 11 == 0 else "fact")

    return BenchmarkFixture(CHALLENGE_VERSION, tuple(events), tuple(claims), ()), tuple(cases)


def run_challenge_evaluation(
    *, history_sizes: Iterable[int] = DEFAULT_HISTORY_SIZES,
) -> ChallengeReport:
    sizes = tuple(int(value) for value in history_sizes)
    if not sizes or any(value < 500 for value in sizes):
        raise ValueError("challenge history sizes must all be at least 500")
    provider = MiniLMEmbeddingProvider()
    scales = tuple(_run_scale(size, provider) for size in sizes)
    return ChallengeReport(CHALLENGE_VERSION, sizes, len(scales[0].cases), scales)


def _run_scale(history_size: int, provider: MiniLMEmbeddingProvider) -> ChallengeScale:
    fixture, cases = build_challenge_fixture(history_size)
    v1 = _V1Recall(fixture, provider)
    store = MemoryV2Store()
    try:
        character = import_fixture(store, fixture)["primary"]
        EmbeddingLifecycle(store, provider).rebuild_all()
        semantic = SemanticRetrievalV2(
            store, RetrievalLimits(final_count=K, token_budget=320),
            embedding_provider=provider,
        )
        hybrid = HybridMemoryV2Recall(store, character, semantic_retriever=semantic)
        comparisons: list[CaseComparison] = []
        for case in cases:
            v1_ids = v1.retrieve(case.query, K)
            outcome = hybrid.retrieve(
                RetrievalQuery(character, case.query, "2026-08-30T12:00:00Z"), limit=K,
            )
            v2_ids = tuple(value.memory_id for value in outcome.candidates)
            comparisons.append(CaseComparison(
                history_size, case.case_id, case.category, case.expected_ids,
                _evaluate(case, v1_ids, (), {}, {}, 0.0),
                _evaluate(
                    case, v2_ids,
                    tuple((value.memory_id, (value.lane,)) for value in outcome.candidates),
                    {value.memory_id: value.signals for value in outcome.candidates},
                    {}, 0.0,
                ),
            ))
        values = tuple(comparisons)
        return ChallengeScale(
            history_size, len(fixture.claims),
            _metrics(tuple(value.v1 for value in values), cases),
            _metrics(tuple(value.v2 for value in values), cases),
            _by_category(values, cases, "v1"), _by_category(values, cases, "v2"),
            values,
        )
    finally:
        store.close()


if __name__ == "__main__":
    report = run_challenge_evaluation()
    print(f"challenge={report.version} cases={report.case_count}")
    for scale in report.scales:
        print(scale.history_size, "V1", scale.v1)
        print(scale.history_size, "V2", scale.v2)
        print("V2 failures", tuple(
            (case.case_id, case.v2.failure_class, case.v2.candidates)
            for case in scale.cases
            if case.v2.recall_at_5 < 1.0 or case.v2.false_recall or case.v2.forbidden
        ))
