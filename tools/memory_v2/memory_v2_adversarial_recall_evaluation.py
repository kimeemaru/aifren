"""Expanded synthetic adversarial evaluation for frozen V1/V2 recall.

This module is deliberately separate from the curated 15-case diagnostic.
It reads no application persistence and never admits shadow V2 results to a
production prompt. Expected answers are authored from fixture provenance, not
from retrieval output.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import argparse
import json
import time
from typing import Iterable

from aifren.memory_v2_store.models import BenchmarkFixture, GoldClaim, RetrievalQuery, SyntheticEvent
from aifren.continuity.memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor, canonical_record_id
from aifren.continuity.memory_v2_hybrid_recall import HybridMemoryV2Recall
from tools.memory_v2.memory_v2_long_term_recall_evaluation import _V1Recall
from aifren.memory_v2_store import EmbeddingLifecycle, MemoryV2Store, MiniLMEmbeddingProvider
from aifren.memory_v2_store.importer import import_fixture
from aifren.memory_v2_store.retrieval import RetrievalLimits, SemanticRetrievalV2


# Public fixture-policy identity, independent of any development repository history.
FROZEN_RETRIEVAL_CHECKPOINT = "public-synthetic-retrieval-v1"
EVALUATION_VERSION = "memory-v2-adversarial-recall-v1"
DEFAULT_HISTORY_SIZES = (500, 1500, 4000)
K = 5


@dataclass(frozen=True)
class AdversarialCase:
    case_id: str
    category: str
    query: str
    expected_ids: tuple[str, ...] = ()
    relevant_ids: tuple[str, ...] = ()
    forbidden: tuple[tuple[str, str], ...] = ()
    expected_source_ids: tuple[str, ...] = ()
    likely_failure: str = ""

    @property
    def negative(self) -> bool:
        return not self.expected_ids


@dataclass(frozen=True)
class EvaluatedResult:
    candidates: tuple[str, ...]
    lanes: tuple[tuple[str, tuple[str, ...]], ...]
    signals: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]
    recall_at_1: float
    recall_at_5: float
    precision: float
    reciprocal_rank: float
    expected_rank: int | None
    abstention_correct: bool
    false_recall: bool
    forbidden: tuple[tuple[str, str], ...]
    failure_class: str
    latency_ms: float


@dataclass(frozen=True)
class CaseComparison:
    history_size: int
    case_id: str
    category: str
    expected_ids: tuple[str, ...]
    v1: EvaluatedResult
    v2: EvaluatedResult


@dataclass(frozen=True)
class Metrics:
    cases: int
    positive_cases: int
    negative_cases: int
    recall_at_1: float
    recall_at_5: float
    precision: float
    mean_reciprocal_rank: float
    abstention_accuracy: float
    false_recall_rate: float
    forbidden_candidate_rate: float
    cross_character_leaks: int
    cross_scope_leaks: int
    mean_latency_ms: float
    rank_distribution: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ScaleReport:
    history_size: int
    corpus_claims: int
    v1: Metrics
    v2: Metrics
    v1_by_category: tuple[tuple[str, Metrics], ...]
    v2_by_category: tuple[tuple[str, Metrics], ...]
    forbidden_causes_v1: tuple[tuple[str, int], ...]
    forbidden_causes_v2: tuple[tuple[str, int], ...]
    v2_lane_counts: tuple[tuple[str, int], ...]
    cases: tuple[CaseComparison, ...]


@dataclass(frozen=True)
class AdversarialReport:
    version: str
    frozen_checkpoint: str
    history_sizes: tuple[int, ...]
    case_count: int
    scales: tuple[ScaleReport, ...]
    history_pressure_failures: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_adversarial_fixture(noise_claims: int) -> tuple[
    BenchmarkFixture, tuple[AdversarialCase, ...], tuple[tuple[str, str, int], ...],
]:
    """Build a deterministic unseen corpus with authored evidence contracts."""
    if noise_claims < 500:
        raise ValueError("adversarial history must contain at least 500 noise claims")
    events: list[SyntheticEvent] = []
    claims: list[GoldClaim] = []
    cases: list[AdversarialCase] = []

    def add_event(event_id: str, content: str, at: str, *, character: str = "primary") -> None:
        if not any(value.event_id == event_id and value.character_id == character for value in events):
            events.append(SyntheticEvent(event_id, character, at, content))

    def add_claim(
        claim_id: str, content: str, at: str, *, category: str = "fact",
        character: str = "primary", event_id: str | None = None,
        status: str = "active", superseded_by: str | None = None, importance: int = 5,
    ) -> None:
        source = event_id or f"event-{claim_id}"
        add_event(source, content, at, character=character)
        claims.append(GoldClaim(
            claim_id=claim_id, character_id=character, category=category,
            content=content, source_event_ids=(source,), status=status,
            superseded_by=superseded_by, topic=claim_id, importance=importance,
        ))

    exact_values = (
        ("quartz-harbor-41", "drawer label"), ("indigo-wren-73", "storage tag"),
        ("saffron-keel-29", "cabinet mark"), ("umber-lantern-88", "archive phrase"),
    )
    for index, (value, noun) in enumerate(exact_values):
        claim_id = f"exact-{index}"
        add_claim(claim_id, f"The user's old {noun} is {value}.", f"2010-0{index + 1}-03T10:00:00Z")
        cases.append(AdversarialCase(
            claim_id, "exact_old_detail", f'What was my {noun} "{value}"?',
            (claim_id,), (claim_id,), expected_source_ids=(f"event-{claim_id}",),
        ))

    paraphrases = (
        ("pipe-dinner", "A burst pipe forced the user's birthday dinner into the railway waiting room.", "Where did my birthday meal move after the plumbing disaster?"),
        ("wind-kite", "A violent gust stranded the handmade kite in the bell tower.", "Where did the flying craft end up after the weather turned rough?"),
        ("ferry-cake", "A delayed ferry made the user serve the lemon cake on the harbor steps.", "Where was dessert finally eaten because the boat was late?"),
        ("snow-concert", "Heavy snow moved the small concert into a greenhouse.", "Where did the music gathering relocate due to winter weather?"),
    )
    for index, (claim_id, content, query) in enumerate(paraphrases):
        add_claim(claim_id, content, f"2011-0{index + 1}-11T11:00:00Z", category="shared_episode")
        cases.append(AdversarialCase(claim_id, "heavy_paraphrase", query, (claim_id,), (claim_id,), expected_source_ids=(f"event-{claim_id}",), likely_failure="semantic_miss"))

    rare_entities = (
        ("qoro", "Professor Elian Qoro repaired the user's astrolabe.", "Who was Elian Qoro?"),
        ("vesca", "Mira Vesca mapped the hidden footpath near the quarry.", "What did Mira Vesca map?"),
        ("tovik", "The rare Tovik spindle is stored beside the loom.", "Where is the Tovik spindle?"),
        ("nyra", "The user met cartographer Nyra Pel at Mossbridge.", "Where did I meet Nyra Pel?"),
    )
    for index, (suffix, content, query) in enumerate(rare_entities):
        claim_id = f"rare-{suffix}"
        add_claim(claim_id, content, f"2012-0{index + 1}-12T12:00:00Z")
        cases.append(AdversarialCase(claim_id, "rare_name_term", query, (claim_id,), (claim_id,), expected_source_ids=(f"event-{claim_id}",)))

    entity_rows = (
        ("entity-person", "Jun Aras is the user's glassblowing instructor.", "What does Jun Aras teach?"),
        ("entity-place", "The user stores winter tools at Rowan Depot.", "What do I keep at Rowan Depot?"),
        ("entity-object", "The brass tide clock hangs in the study.", "Where is the brass tide clock?"),
        ("entity-pet", "The user's neighbor's dog Pesto wears a violet collar.", "What color collar does Pesto wear?"),
    )
    for index, (claim_id, content, query) in enumerate(entity_rows):
        add_claim(claim_id, content, f"2013-0{index + 1}-13T13:00:00Z")
        cases.append(AdversarialCase(claim_id, "entity_reference", query, (claim_id,), (claim_id,), expected_source_ids=(f"event-{claim_id}",)))

    similar_clusters = (
        ("notebook", "blue", "train schedules", "red", "bird sketches"),
        ("thermos", "silver", "mint tea", "white", "coffee"),
        ("keycase", "green", "studio keys", "black", "garage keys"),
        ("camera", "copper", "macro lens", "bronze", "portrait lens"),
    )
    for index, (noun, wanted_color, wanted_value, wrong_color, wrong_value) in enumerate(similar_clusters):
        wanted = f"similar-{index}-wanted"
        wrong = f"similar-{index}-wrong"
        add_claim(wanted, f"The {wanted_color} {noun} contains {wanted_value}.", f"2014-0{index + 1}-14T14:00:00Z")
        add_claim(wrong, f"The {wrong_color} {noun} contains {wrong_value}.", f"2014-0{index + 1}-15T14:00:00Z")
        cases.append(AdversarialCase(
            f"similar-{index}", "similar_object_attributes",
            f"What does the {wanted_color} {noun} contain?", (wanted,), (wanted,),
            ((wrong, "conflicting_similar_object"),), (f"event-{wanted}",),
            "redundant_unsafe_secondary_candidate",
        ))

    corrections = (
        ("workshop", "Old Mill", "East Foundry"), ("bicycle", "yellow", "navy"),
        ("tea", "smoked oolong", "jasmine tea"), ("printer", "laser printer", "resin printer"),
    )
    for index, (slot, old_value, new_value) in enumerate(corrections):
        old_id, new_id = f"correction-{index}-old", f"correction-{index}-current"
        add_claim(old_id, f"The user's {slot} was {old_value}.", f"2015-0{index + 1}-01T09:00:00Z", status="superseded", superseded_by=new_id)
        add_claim(new_id, f"The user's current {slot} is {new_value}.", f"2024-0{index + 1}-01T09:00:00Z")
        cases.append(AdversarialCase(
            f"correction-{index}", "corrected_superseded", f"What is my current {slot}?",
            (new_id,), (new_id,), ((old_id, "superseded_predecessor"),),
            (f"event-{new_id}",), "scope_lifecycle_filtering",
        ))

    anecdotes = (
        ("otter-map", "An otter carried away the user's folded trail map."),
        ("goat-scarf", "A goat chewed the fringe of the user's orange scarf."),
        ("crow-spoon", "A crow dropped a silver spoon onto the picnic blanket."),
        ("seal-boot", "A seal nudged the user's green boot into the tide pool."),
    )
    for index, (claim_id, content) in enumerate(anecdotes):
        add_claim(claim_id, content, f"2016-0{index + 1}-06T16:00:00Z", category="shared_episode")
        cases.append(AdversarialCase(claim_id, "old_incidental_event", f"What happened in the old {claim_id.replace('-', ' ')} incident?", (claim_id,), (claim_id,), expected_source_ids=(f"event-{claim_id}",)))

    association_rows = (
        ("Sena", "Reed Marsh", "ivory barometer"), ("Oren", "Slate Pier", "folding sextant"),
        ("Lio", "Juniper Pass", "ceramic whistle"), ("Tara", "Gull Cove", "woven compass pouch"),
    )
    for index, (person, place, obj) in enumerate(association_rows):
        event_id = f"assoc-event-{index}"
        add_event(event_id, f"At {place} we met {person}, who lent the user a {obj}.", f"2017-0{index + 1}-17T17:00:00Z")
        anchor, answer = f"assoc-{index}-anchor", f"assoc-{index}-answer"
        add_claim(anchor, f"The user and companion met {person} at {place}.", f"2017-0{index + 1}-17T17:00:00Z", category="shared_episode", event_id=event_id)
        add_claim(answer, f"{person} lent the user a {obj}.", f"2017-0{index + 1}-17T17:00:00Z", event_id=event_id)
        cases.append(AdversarialCase(
            f"source-associated-{index}", "source_associated",
            f"What object was connected to our {place} meeting?", (answer,), (answer, anchor),
            expected_source_ids=(event_id,), likely_failure="associative_expansion",
        ))

    # Independent events with tempting lexical similarity must never become
    # evidence-linked merely because their descriptions sound compatible.
    for index, (person, place, obj) in enumerate(association_rows):
        anchor = f"invalid-assoc-{index}-anchor"
        tempting = f"invalid-assoc-{index}-tempting"
        add_claim(anchor, f"The user met {person} at the {place} archive.", f"2018-0{index + 1}-10T10:00:00Z", category="shared_episode")
        add_claim(tempting, f"A {obj} was displayed in a different museum.", f"2019-0{index + 1}-10T10:00:00Z")
        cases.append(AdversarialCase(
            f"invalid-association-{index}", "tempting_invalid_association",
            f"What object was connected to meeting {person} at the {place} archive?",
            (), (), ((tempting, "plausible_but_unrelated_association"),),
            likely_failure="weak_abstention",
        ))

    activities = ("weaving", "kayaking", "bread class", "bird survey")
    for index, activity in enumerate(activities):
        ids = []
        places = ("North Hall", "Market Annex", "Harbor Room")
        years = (2013, 2018, 2025)
        for occurrence, (place, year) in enumerate(zip(places, years)):
            claim_id = f"occurrence-{index}-{occurrence}"
            ids.append(claim_id)
            qualifier = "most recent " if occurrence == 2 else ""
            add_claim(claim_id, f"The user's {qualifier}{activity} session was at {place}.", f"{year}-06-0{occurrence + 1}T12:00:00Z", category="shared_episode")
        cases.extend((
            AdversarialCase(f"first-{index}", "first_occurrence", f"Where was my first {activity} session?", (ids[0],), tuple(ids), expected_source_ids=(f"event-{ids[0]}",), likely_failure="temporal_reasoning"),
            AdversarialCase(f"latest-{index}", "latest_occurrence", f"Where was my latest {activity} session?", (ids[2],), tuple(ids), expected_source_ids=(f"event-{ids[2]}",), likely_failure="temporal_reasoning"),
            AdversarialCase(f"repeated-{index}", "repeated_occurrences", f"Which {activity} sessions do you remember?", tuple(ids), tuple(ids), expected_source_ids=tuple(f"event-{value}" for value in ids), likely_failure="temporal_reasoning"),
        ))

    relation_rows = (
        ("Maple Archive", "Copper Cafe", "Stone Atrium"),
        ("West Pier", "Clock Museum", "Fern House"),
        ("Lime Station", "Paper Theater", "North Garden"),
        ("Glass Library", "Cedar Bakery", "Blue Workshop"),
    )
    for index, (before, anchor, after) in enumerate(relation_rows):
        ids = (f"sequence-{index}-before", f"sequence-{index}-anchor", f"sequence-{index}-after")
        for offset, (claim_id, place) in enumerate(zip(ids, (before, anchor, after))):
            add_claim(claim_id, f"The user and companion visited {place}.", f"2020-0{index + 1}-0{offset + 1}T12:00:00Z", category="shared_episode")
        cases.extend((
            AdversarialCase(f"before-{index}", "before_after", f"What did we visit immediately before {anchor}?", (ids[0],), (ids[0],), ((ids[1], "temporal_anchor"), (ids[2], "neighboring_wrong_episode")), (f"event-{ids[0]}",), "temporal_reasoning"),
            AdversarialCase(f"after-{index}", "before_after", f"What did we visit immediately after {anchor}?", (ids[2],), (ids[2],), ((ids[1], "temporal_anchor"), (ids[0], "neighboring_wrong_episode")), (f"event-{ids[2]}",), "temporal_reasoning"),
        ))

    periods = (
        ("winter 2017", "frozen canal market", "2017-01-20T12:00:00Z"),
        ("spring 2019", "orchard lantern walk", "2019-04-20T12:00:00Z"),
        ("October 2021", "night ferry exhibit", "2021-10-20T12:00:00Z"),
        ("summer 2023", "cliffside pottery fair", "2023-07-20T12:00:00Z"),
    )
    for index, (period, event, occurred_at) in enumerate(periods):
        claim_id = f"period-{index}"
        add_claim(claim_id, f"The user attended the {event}.", occurred_at, category="shared_episode")
        distractor = f"period-{index}-misleading"
        add_claim(distractor, f"The user later read an article titled '{period} travel notes'.", f"2025-0{index + 1}-20T12:00:00Z")
        cases.append(AdversarialCase(
            f"named-period-{index}", "named_time_period", f"What did we do during {period}?",
            (claim_id,), (claim_id,), ((distractor, "misleading_lexical_temporal_anchor"),),
            (f"event-{claim_id}",), "temporal_reasoning",
        ))

    retired_rows = (
        ("pager", "weather receiver"), ("red satchel", "gray backpack"),
        ("old studio", "river workshop"), ("dial-up modem", "fiber adapter"),
    )
    for index, (retired_value, current_value) in enumerate(retired_rows):
        retired, current = f"retired-{index}", f"retired-{index}-current"
        add_claim(retired, f"The user once used {retired_value}.", f"2012-0{index + 1}-01T00:00:00Z", status="archived")
        add_claim(current, f"The user currently uses {current_value}.", f"2025-0{index + 1}-01T00:00:00Z")
        cases.append(AdversarialCase(
            f"retired-case-{index}", "retired_distractor",
            f"Which do I currently use: {retired_value} or {current_value}?",
            (current,), (current,), ((retired, "retired_record"),), (f"event-{current}",),
            "scope_lifecycle_filtering",
        ))

    # Close other-character facts exercise store isolation at every size.
    other_rows = (
        ("favorite mug", "ochre", "teal"), ("home city", "Cloudrest", "Birchport"),
        ("pet bird", "Kiri", "Momo"), ("workbench", "attic", "garage"),
    )
    for index, (slot, wanted, other_value) in enumerate(other_rows):
        wanted_id, other_id = f"isolation-{index}-primary", f"other-{index}"
        add_claim(wanted_id, f"The primary user's {slot} is {wanted}.", f"2022-0{index + 1}-01T00:00:00Z")
        add_claim(other_id, f"The other user's {slot} is {other_value}.", f"2022-0{index + 1}-01T00:00:00Z", character="other")
        cases.append(AdversarialCase(
            f"character-isolation-{index}", "other_character", f"What is my {slot}?",
            (wanted_id,), (wanted_id,), ((other_id, "other_character"),),
            (f"event-{wanted_id}",), "scope_lifecycle_filtering",
        ))

    negatives = (
        ("never-0", "What is my passport number?", "passport-case", "The user's passport case is amber."),
        ("never-1", "Which telescope did I buy?", "astronomy-book", "The user read an astronomy telescope guide."),
        ("never-2", "What is my bank routing number?", "bank-map", "The user keeps a map of the river bank."),
        ("never-3", "Who is my dentist?", "dental-book", "The user owns a dental history book."),
    )
    for index, (case_id, query, close_id, close_content) in enumerate(negatives):
        add_claim(close_id, close_content, f"2021-0{index + 1}-01T00:00:00Z")
        cases.append(AdversarialCase(case_id, "never_stated", query, (), (), ((close_id, "superficially_similar_unstated"),), likely_failure="weak_abstention"))

    false_premises = (
        ("sold bicycle", "The user rents a bicycle."),
        ("lost violin", "The user attended a violin concert."),
        ("moved from Oslo", "The user watched a documentary about Oslo."),
        ("adopted a rabbit", "The user bought a rabbit-shaped lamp."),
    )
    for index, (premise, close_content) in enumerate(false_premises):
        close_id = f"false-premise-close-{index}"
        add_claim(close_id, close_content, f"2021-0{index + 5}-01T00:00:00Z")
        cases.append(AdversarialCase(
            f"false-premise-{index}", "false_premise", f"Why did I {premise}?",
            (), (), ((close_id, "false_premise_neighbor"),), likely_failure="weak_abstention",
        ))

    weak_queries = (
        ("What was the number?", "weak-code", "The equipment code is delta-19."),
        ("Where was that place?", "weak-place", "The user visited Sable Point."),
        ("Who was that person?", "weak-person", "The user met Cora Bell."),
        ("What color was it?", "weak-color", "The user's travel case is violet."),
    )
    for index, (query, close_id, content) in enumerate(weak_queries):
        add_claim(close_id, content, f"2022-0{index + 5}-01T00:00:00Z")
        cases.append(AdversarialCase(
            f"insufficient-{index}", "insufficient_evidence", query, (), (),
            ((close_id, "underspecified_query"),), likely_failure="weak_abstention",
        ))

    # Even the largest scale remains historical relative to the fixed query
    # time. Future-dated noise would invalidate the temporal comparison.
    start = datetime(1980, 1, 1, tzinfo=timezone.utc)
    topics = (
        "garden gate", "camera battery", "museum ticket", "bread timer",
        "train platform", "blue notebook", "silver compass", "winter market",
        "harbor cafe", "ceramics class", "archive label", "telescope guide",
        "river bank", "clock workshop", "violet travel case", "ferry schedule",
    )
    modifiers = ("quiet", "striped", "folded", "small", "ordinary", "distant", "spare", "old")
    for index in range(noise_claims):
        claim_id = f"noise-{index:05d}"
        at = (start + timedelta(days=index * 3 + 1)).isoformat().replace("+00:00", "Z")
        content = (
            f"Synthetic archive item {index}: the user discussed a "
            f"{modifiers[(index * 3) % len(modifiers)]} {topics[index % len(topics)]} "
            f"detail with value {index % 97}."
        )
        add_claim(claim_id, content, at, category="shared_episode" if index % 9 == 0 else "fact", importance=3 + index % 4)

    # Put the authoritative real-world fact in the shared V1/V2 corpus. Only
    # its inactive scenario competitor requires post-import truth-scope setup.
    for index in range(4):
        add_claim(
            f"scope-real-{index}",
            f"The user's real-world scenario test item {index} is cedar-{index}.",
            f"2024-0{index + 1}-20T12:00:00Z",
            event_id=f"scope-real-event-{index}",
        )

    fixture = BenchmarkFixture(EVALUATION_VERSION, tuple(events), tuple(claims), ())
    extra_v1 = tuple(
        (f"episode-only-{index}", content, 6)
        for index, content in enumerate(_episode_only_contents())
    )
    episode_sources = _episode_only_source_ids()
    for index, content in enumerate(_episode_only_contents()):
        cases.append(AdversarialCase(
            f"episode-only-{index}", "episode_only_detail",
            _episode_only_queries()[index], (f"episode-only-{index}",),
            (f"episode-only-{index}",), expected_source_ids=(episode_sources[index],),
            likely_failure="episode_participation",
        ))
    # Scenario competitors are inserted after import so they retain a genuine
    # inactive truth-scope identity instead of pretending scenario text is
    # real-world.
    for index in range(4):
        cases.append(AdversarialCase(
            f"cross-scope-{index}", "cross_scope_distractor",
            f"What is my real-world scenario test item {index}?",
            (f"scope-real-{index}",), (f"scope-real-{index}",),
            ((f"scope-scenario-{index}", "inactive_truth_scope"),),
            (f"scope-real-event-{index}",), "scope_lifecycle_filtering",
        ))
    return fixture, tuple(cases), extra_v1


def _episode_only_contents() -> tuple[str, ...]:
    return (
        "The user and companion built a paper observatory whose Saturn ring tilted sideways.",
        "They cooked violet dumplings in a borrowed lighthouse kitchen.",
        "They found a brass acorn inside the old theater prop box.",
        "They painted a tiny moon on the underside of a cedar canoe.",
    )


def _episode_only_queries() -> tuple[str, ...]:
    return (
        "What did we build whose Saturn ring tilted sideways?",
        "What color dumplings did we cook in the lighthouse kitchen?",
        "What was inside the theater prop box?",
        "What did we paint beneath the cedar canoe?",
    )


def _episode_messages() -> tuple[dict[str, object], ...]:
    targets = {0: _episode_only_contents()[0], 40: _episode_only_contents()[1],
               80: _episode_only_contents()[2], 120: _episode_only_contents()[3]}
    rows: list[dict[str, object]] = []
    for index in range(180):
        content = targets.get(index, f"Routine bounded historical exchange {index}.")
        rows.extend((
            {"role": "user", "content": content, "timestamp": f"2020-01-{1 + index // 24:02d}T{index % 24:02d}:00:00Z"},
            {"role": "assistant", "content": f"Acknowledged historical exchange {index}.", "timestamp": f"2020-01-{1 + index // 24:02d}T{index % 24:02d}:00:01Z"},
        ))
    return tuple(rows)


def _episode_only_source_ids() -> tuple[str, ...]:
    messages = _episode_messages()
    return tuple(canonical_record_id(index * 80, messages[index * 80]) for index in range(4))


class _ExtractiveProvider:
    def generate(self, _context, prompt, *, seed=None):
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        if "Return one strict JSON object" in prompt:
            return '{"consolidate":false,"first_episode":0,"last_episode":0,"account":""}'
        lines = [line.split("USER: ", 1)[1] for line in prompt.splitlines() if "] USER: " in line]
        return "During this period, " + " ".join(lines)[:2800]


def run_adversarial_evaluation(
    *, history_sizes: Iterable[int] = DEFAULT_HISTORY_SIZES,
) -> AdversarialReport:
    sizes = tuple(int(value) for value in history_sizes)
    if not sizes or any(value < 500 for value in sizes):
        raise ValueError("history sizes must all be at least 500")
    provider = MiniLMEmbeddingProvider()
    scales = tuple(_run_scale(size, provider) for size in sizes)
    outcomes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for scale in scales:
        for case in scale.cases:
            outcomes[("v1", case.case_id)].append(case.v1.recall_at_5 == 1.0)
            outcomes[("v2", case.case_id)].append(case.v2.recall_at_5 == 1.0)
    pressure = tuple(sorted(
        f"{system}:{case_id}" for (system, case_id), values in outcomes.items()
        if any(values) and not all(values) and values[-1] is False
    ))
    return AdversarialReport(
        EVALUATION_VERSION, FROZEN_RETRIEVAL_CHECKPOINT, sizes,
        len(scales[0].cases), scales, pressure,
    )


def _run_scale(history_size: int, provider: MiniLMEmbeddingProvider) -> ScaleReport:
    fixture, cases, extra_v1 = build_adversarial_fixture(history_size)
    v1 = _V1Recall(fixture, provider, extra_memories=extra_v1)
    store = MemoryV2Store()
    try:
        characters = import_fixture(store, fixture)
        primary = characters["primary"]
        _add_scoped_distractors(store, primary)
        EmbeddingLifecycle(store, provider).rebuild_all()
        semantic = SemanticRetrievalV2(
            store, RetrievalLimits(final_count=K, token_budget=320), embedding_provider=provider,
        )
        messages = _episode_messages()
        cache = EpisodeCompactionCache(store, primary)
        cache.rebuild(messages, EpisodeCompactor(_ExtractiveProvider()))
        validation = cache.validate_for_context(messages)
        episode_sources = {
            record.record_id: frozenset(str(value) for value in (record.metadata or {}).get("source_record_ids", ()))
            for record in validation.lower_records if record.accepted
        }
        hybrid = HybridMemoryV2Recall(
            store, primary, semantic_retriever=semantic,
            episode_cache=cache, canonical_messages=messages,
        )
        comparisons: list[CaseComparison] = []
        for case in cases:
            started = time.perf_counter()
            v1_ids = v1.retrieve(case.query, K)
            v1_ms = (time.perf_counter() - started) * 1000.0
            started = time.perf_counter()
            v2_outcome = hybrid.retrieve(
                RetrievalQuery(primary, case.query, "2026-08-30T12:00:00Z"), limit=K,
            )
            v2_ms = (time.perf_counter() - started) * 1000.0
            v2_ids = tuple(value.memory_id for value in v2_outcome.candidates)
            v2_support = {
                value.memory_id: episode_sources.get(value.memory_id, frozenset())
                for value in v2_outcome.candidates
            }
            comparisons.append(CaseComparison(
                history_size, case.case_id, case.category, case.expected_ids,
                _evaluate(case, v1_ids, (), {}, {}, v1_ms),
                _evaluate(
                    case, v2_ids,
                    tuple((value.memory_id, (value.lane,)) for value in v2_outcome.candidates),
                    {value.memory_id: value.signals for value in v2_outcome.candidates},
                    v2_support, v2_ms,
                ),
            ))
        values = tuple(comparisons)
        return ScaleReport(
            history_size, len(fixture.claims) + 4,
            _metrics(tuple(value.v1 for value in values), cases),
            _metrics(tuple(value.v2 for value in values), cases),
            _by_category(values, cases, "v1"), _by_category(values, cases, "v2"),
            _forbidden_causes(tuple(value.v1 for value in values)),
            _forbidden_causes(tuple(value.v2 for value in values)),
            _lane_counts(tuple(value.v2 for value in values)), values,
        )
    finally:
        store.close()


def _add_scoped_distractors(store: MemoryV2Store, character_id: str) -> None:
    sequence = int(store.connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,),
    ).fetchone()[0])
    scope_event = "scenario-scope-open"
    scope_text = "Begin the synthetic lantern scenario."
    store.add_event(character_id, scope_event, sequence, actor_kind="user", content_text=scope_text)
    sequence += 1
    scope = store.create_scenario_truth_scope(
        character_id, "Synthetic lantern scenario", evidence_event_id=scope_event,
        evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5,
    )
    enter_event = "scenario-scope-enter"
    store.add_event(character_id, enter_event, sequence, actor_kind="user", content_text="Enter the lantern scenario.")
    sequence += 1
    store.activate_truth_scope(character_id, scope, evidence_event_id=enter_event,
                               evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)
    scoped_slots = (
        "active.activity.current",
        "active.task.current",
        "active.location.current",
        "active.media.current",
    )
    for index, subject_key in enumerate(scoped_slots):
        event_id = f"scope-scenario-event-{index}"
        content = f"The user's scenario test item {index} is ember-{index}."
        store.add_event(character_id, event_id, sequence, actor_kind="user", content_text=content)
        sequence += 1
        store.set_active_state(
            character_id,
            f"scope-scenario-{index}",
            subject_key=subject_key,
            value=f"scenario test item {index} ember-{index}",
            evidence_event_id=event_id,
            truth_scope_id=scope,
        )
    leave_event = "scenario-scope-leave"
    store.add_event(character_id, leave_event, sequence, actor_kind="user", content_text="Leave the lantern scenario.")
    store.deactivate_to_real_world(character_id, evidence_event_id=leave_event,
                                   evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)


def _evaluate(case, candidates, lanes, signals, support, latency_ms) -> EvaluatedResult:
    candidates = tuple(candidates[:K])
    expected, relevant = set(case.expected_ids), set(case.relevant_ids)
    expected_sources = set(case.expected_source_ids)

    def supports(candidate_id: str) -> bool:
        return candidate_id in expected or bool(expected_sources & set(support.get(candidate_id, ())))

    hit_positions = [index for index, value in enumerate(candidates) if supports(value)]
    relevant_positions = [
        index for index, value in enumerate(candidates)
        if value in relevant or bool(expected_sources & set(support.get(value, ())))
    ]
    expected_hit_ids = {value for value in candidates if value in expected}
    if expected_sources and any(expected_sources & set(support.get(value, ())) for value in candidates):
        expected_hit_ids.add("__source_supported__")
    if case.negative:
        recall1 = recall5 = float(not candidates)
    else:
        recall1 = sum(supports(value) for value in candidates[:1]) / len(expected)
        direct_hits = len(expected_hit_ids & expected)
        if "__source_supported__" in expected_hit_ids:
            direct_hits = max(direct_hits, 1)
        recall5 = min(1.0, direct_hits / len(expected))
    rank = hit_positions[0] + 1 if hit_positions else None
    forbidden = tuple((claim_id, cause) for claim_id, cause in case.forbidden if claim_id in candidates)
    failure = _failure_class(case, recall5, forbidden, bool(candidates))
    return EvaluatedResult(
        candidates, tuple(lanes), tuple((key, tuple(value)) for key, value in signals.items()),
        recall1, recall5,
        len(relevant_positions) / len(candidates) if candidates else float(case.negative),
        1.0 / rank if rank else 0.0, rank,
        (not candidates) if case.negative else recall5 == 1.0,
        case.negative and bool(candidates), forbidden, failure, round(latency_ms, 3),
    )


def _failure_class(case: AdversarialCase, recall5: float, forbidden, has_candidates: bool) -> str:
    if forbidden:
        return "redundant_unsafe_secondary_candidate" if recall5 == 1.0 else forbidden[0][1]
    if case.negative and has_candidates:
        return "weak_abstention"
    if recall5 < 1.0:
        return case.likely_failure or ("candidate_generation_or_ranking" if has_candidates else "candidate_generation_failure")
    return ""


def _metrics(results: tuple[EvaluatedResult, ...], cases: tuple[AdversarialCase, ...]) -> Metrics:
    positive = [(case, result) for case, result in zip(cases, results) if not case.negative]
    negative = [(case, result) for case, result in zip(cases, results) if case.negative]
    rank_counts = Counter("miss" if result.expected_rank is None else str(result.expected_rank) for _case, result in positive)
    return Metrics(
        len(results), len(positive), len(negative),
        (sum(result.recall_at_1 for _case, result in positive) / len(positive)
         if positive else 0.0),
        (sum(result.recall_at_5 for _case, result in positive) / len(positive)
         if positive else 0.0),
        sum(result.precision for result in results) / len(results),
        (sum(result.reciprocal_rank for _case, result in positive) / len(positive)
         if positive else 0.0),
        (sum(result.abstention_correct for _case, result in negative) / len(negative)
         if negative else 0.0),
        (sum(result.false_recall for _case, result in negative) / len(negative)
         if negative else 0.0),
        sum(bool(result.forbidden) for result in results) / len(results),
        sum(any(cause == "other_character" for _claim, cause in result.forbidden) for result in results),
        sum(any(cause == "inactive_truth_scope" for _claim, cause in result.forbidden) for result in results),
        sum(result.latency_ms for result in results) / len(results),
        tuple(sorted(rank_counts.items(), key=lambda value: (value[0] == "miss", value[0]))),
    )


def _by_category(values, cases, system):
    grouped: dict[str, list[tuple[AdversarialCase, EvaluatedResult]]] = defaultdict(list)
    for case, comparison in zip(cases, values):
        grouped[case.category].append((case, getattr(comparison, system)))
    return tuple((category, _metrics(
        tuple(result for _case, result in rows), tuple(case for case, _result in rows),
    )) for category, rows in sorted(grouped.items()))


def _forbidden_causes(results):
    return tuple(sorted(Counter(cause for result in results for _claim, cause in result.forbidden).items()))


def _lane_counts(results):
    return tuple(sorted(Counter(lane for result in results for _claim, lanes in result.lanes for lane in lanes).items()))


def _print_summary(report: AdversarialReport) -> None:
    print(f"frozen_checkpoint={report.frozen_checkpoint} cases={report.case_count}")
    for scale in report.scales:
        print(f"\nHISTORY {scale.history_size} claims={scale.corpus_claims}")
        print("V1", scale.v1)
        print("V2", scale.v2)
        print("V2 forbidden causes", scale.forbidden_causes_v2)
        print("V2 lanes", scale.v2_lane_counts)
        print("V1 failure classes", _failure_counts(scale.cases, "v1"))
        print("V2 failure classes", _failure_counts(scale.cases, "v2"))
        v1_categories = dict(scale.v1_by_category)
        for category, v2_metric in scale.v2_by_category:
            v1_metric = v1_categories[category]
            print(
                f"  {category}: "
                f"V1[R1={v1_metric.recall_at_1:.3f} R5={v1_metric.recall_at_5:.3f} "
                f"P={v1_metric.precision:.3f} abstain={v1_metric.abstention_accuracy:.3f} "
                f"forbidden={v1_metric.forbidden_candidate_rate:.3f}] "
                f"V2[R1={v2_metric.recall_at_1:.3f} R5={v2_metric.recall_at_5:.3f} "
                f"P={v2_metric.precision:.3f} abstain={v2_metric.abstention_accuracy:.3f} "
                f"forbidden={v2_metric.forbidden_candidate_rate:.3f}]"
            )
    print("history_pressure_failures", report.history_pressure_failures)


def _failure_counts(cases: tuple[CaseComparison, ...], system: str) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(
        result.failure_class
        for case in cases
        if (result := getattr(case, system)).failure_class
    ).items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-sizes", default=",".join(str(value) for value in DEFAULT_HISTORY_SIZES))
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    sizes = tuple(int(value.strip()) for value in arguments.history_sizes.split(",") if value.strip())
    report = run_adversarial_evaluation(history_sizes=sizes)
    if arguments.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
