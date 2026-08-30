"""Small neutral fixtures for supported Memory V2 retrieval regressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from memory_v2_store.retrieval_models import RetrievalQuery
from memory_v2_store.store import MemoryV2Store, parse_timestamp_us


@dataclass(frozen=True)
class TestEvent:
    event_id: str
    character_id: str
    recorded_at: str
    content: str


@dataclass(frozen=True)
class TestClaim:
    claim_id: str
    character_id: str
    category: str
    content: str
    source_event_ids: tuple[str, ...]
    status: str = "active"
    superseded_by: Optional[str] = None
    topic: str = "general"
    importance: int = 5


@dataclass(frozen=True)
class TestRetrievalCase:
    case_id: str
    character_id: str
    query: str
    at: str = "2026-08-01T00:00:00Z"
    recently_used_claim_ids: tuple[str, ...] = ()
    recent_visible_claim_ids: tuple[str, ...] = ()
    query_mode: str = "ordinary"
    embedding_state: Optional[str] = None
    final_token_budget: int = 180
    final_injection_cap: int = 3

    @property
    def retrieval_query(self) -> RetrievalQuery:
        return RetrievalQuery(
            self.character_id, self.query, self.at, self.query_mode, (),
        )


@dataclass(frozen=True)
class RetrievalFixture:
    version: str
    events: tuple[TestEvent, ...]
    claims: tuple[TestClaim, ...]
    retrieval_cases: tuple[TestRetrievalCase, ...]


def build_retrieval_fixture() -> RetrievalFixture:
    """Return compact purpose-built data with no historical companion archive."""
    events = (
        TestEvent("alpha-001", "alpha", "2021-01-01T09:00:00Z", "I prefer red tea."),
        TestEvent("alpha-002", "alpha", "2021-02-01T09:00:00Z", "I am allergic to walnuts."),
        TestEvent("alpha-003", "alpha", "2021-03-01T09:00:00Z", "The pizza meteor joke makes me laugh."),
        TestEvent("alpha-004", "alpha", "2024-01-01T09:00:00Z", "I now prefer green tea."),
        TestEvent("alpha-005", "alpha", "2025-02-01T09:00:00Z", "We planned a July hike."),
        TestEvent("alpha-006", "alpha", "2025-03-01T09:00:00Z", "The July hike was cancelled."),
        TestEvent("alpha-007", "alpha", "2020-04-01T09:00:00Z", "I own a Nintendo 64, serial N64-CA-0042."),
        TestEvent("alpha-008", "alpha", "2022-05-01T09:00:00Z", "My friend Rose is a civil engineer."),
        TestEvent("alpha-009", "alpha", "2022-06-01T09:00:00Z", "I planted a rose bush."),
        TestEvent("alpha-010", "alpha", "2019-07-01T09:00:00Z", "My locker code is cobalt-orchid."),
        TestEvent("alpha-011", "alpha", "2019-08-01T09:00:00Z", "I read a passport manual."),
        TestEvent("alpha-012", "alpha", "2023-09-01T09:00:00Z", "We troubleshot Pokemon Stadium together."),
        TestEvent("beta-001", "beta", "2025-10-01T09:00:00Z", "I live in Skyhaven."),
    )
    claims = (
        TestClaim("alpha-tea-red", "alpha", "preference", "The user preferred red tea.", ("alpha-001",), status="superseded", superseded_by="alpha-tea-green", topic="tea"),
        TestClaim("alpha-tea-green", "alpha", "preference", "The user currently prefers green tea.", ("alpha-004",), topic="tea"),
        TestClaim("alpha-walnut-allergy", "alpha", "fact", "The user is allergic to walnuts.", ("alpha-002",), topic="health", importance=10),
        TestClaim("alpha-pizza-joke", "alpha", "running_joke", "The pizza meteor joke is a shared joke.", ("alpha-003",), topic="joke"),
        TestClaim("alpha-hike-planned", "alpha", "future_event", "A July hike was planned.", ("alpha-005",), status="superseded", superseded_by="alpha-hike-cancelled", topic="hike"),
        TestClaim("alpha-hike-cancelled", "alpha", "future_event", "The July hike was cancelled.", ("alpha-006",), topic="hike"),
        TestClaim("alpha-n64", "alpha", "profile_fact", "The user owns a Nintendo 64.", ("alpha-007",), topic="n64"),
        TestClaim("alpha-n64-serial", "alpha", "identifier", "The user's Nintendo 64 serial is N64-CA-0042.", ("alpha-007",), topic="n64"),
        TestClaim("alpha-rose-person", "alpha", "profile_fact", "Rose is the user's civil-engineer friend.", ("alpha-008",), topic="rose-person"),
        TestClaim("alpha-rose-plant", "alpha", "profile_fact", "The user planted a rose bush.", ("alpha-009",), topic="gardening"),
        TestClaim("alpha-cobalt-code", "alpha", "profile_fact", "The user's locker code is cobalt-orchid.", ("alpha-010",), topic="code"),
        TestClaim("alpha-passport-manual", "alpha", "profile_fact", "The user read a passport manual.", ("alpha-011",), topic="passport", importance=10),
        TestClaim("alpha-pokemon-episode", "alpha", "episode", "The user and companion troubleshot Pokemon Stadium together.", ("alpha-012",), topic="pokemon"),
        TestClaim("beta-skyhaven", "beta", "location", "The user currently lives in Skyhaven.", ("beta-001",), topic="location"),
    )
    cases = (
        TestRetrievalCase("paraphrase-semantic", "alpha", "Do walnuts make me sick?"),
        TestRetrievalCase("identifier", "alpha", "Which console has serial N64-CA-0042?"),
        TestRetrievalCase("fts-punctuation-safety", "alpha", 'Find N64-CA-0042: "cobalt-orchid" (exact).'),
        TestRetrievalCase("alias", "alpha", "Do I own an N64?"),
        TestRetrievalCase("case-collision-person", "alpha", "What does Rose do for work?"),
        TestRetrievalCase("beta-location-isolation", "beta", "Where do I live now?"),
        TestRetrievalCase("current-tea", "alpha", "What tea do I prefer now?"),
        TestRetrievalCase("historical-tea", "alpha", "What tea did I prefer in 2021?"),
        TestRetrievalCase("cancelled-plan", "alpha", "Are we still hiking in July?"),
        TestRetrievalCase("unrelated-guitar", "alpha", "What beginner guitar should I try?"),
        TestRetrievalCase("irrelevant-high-importance", "alpha", "What tea do I prefer?"),
        TestRetrievalCase("recent-visible-duplicate", "alpha", "I still prefer green tea.", recent_visible_claim_ids=("alpha-tea-green",)),
        TestRetrievalCase("explicit-repeat-override", "alpha", "Please repeat the pizza meteor joke now.", recently_used_claim_ids=("alpha-pizza-joke",), query_mode="explicit_repeat"),
        TestRetrievalCase("channel-dominance", "alpha", "Tell me about our Pokemon Stadium evening and N64."),
        TestRetrievalCase("stale-embedding", "alpha", "What is the locker code?", embedding_state="stale"),
        TestRetrievalCase("exact-phrase", "alpha", 'Find "cobalt-orchid" exactly.'),
    )
    return RetrievalFixture("memory-v2-regression-v1", events, claims, cases)


def import_retrieval_fixture(
    store: MemoryV2Store, fixture: RetrievalFixture,
) -> dict[str, str]:
    """Import only the neutral in-memory test objects above."""
    namespace = uuid.UUID("2bb9ab3c-183a-4d28-9739-725be7cf2721")
    character_map: dict[str, str] = {}
    for event in fixture.events:
        if event.character_id not in character_map:
            character_id = str(uuid.uuid5(namespace, f"{fixture.version}:{event.character_id}"))
            character_map[event.character_id] = character_id
            store.create_character(character_id, event.character_id)

    events_by_id = {event.event_id: event for event in fixture.events}
    for sequence, event in enumerate(fixture.events, start=1):
        store.add_event(
            character_map[event.character_id], event.event_id, sequence,
            actor_kind="user", recorded_at_us=parse_timestamp_us(event.recorded_at),
            temporal_precision="instant", content_text=event.content,
            source_origin="synthetic_test",
        )

    claims_by_id = {claim.claim_id: claim for claim in fixture.claims}
    for claim in fixture.claims:
        source_events = [events_by_id[event_id] for event_id in claim.source_event_ids]
        created_at = min(parse_timestamp_us(event.recorded_at) for event in source_events)
        store.add_claim(
            character_map[claim.character_id], claim.claim_id,
            claim_type=claim.category,
            assertion_scope=("shared_episode" if claim.category in {"episode", "running_joke"} else "user_fact"),
            content=claim.content, importance=claim.importance, confidence=1.0,
            valid_from_us=created_at, temporal_precision="instant",
            provenance_state="complete", created_at_us=created_at,
        )
        for event in source_events:
            store.attach_evidence(
                character_map[claim.character_id], claim.claim_id, event.event_id,
            )

    for claim in fixture.claims:
        character_id = character_map[claim.character_id]
        source_event = events_by_id[claim.source_event_ids[-1]]
        status_at = parse_timestamp_us(source_event.recorded_at)
        if claim.superseded_by:
            successor = claims_by_id[claim.superseded_by]
            successor_event = events_by_id[successor.source_event_ids[0]]
            status_at = parse_timestamp_us(successor_event.recorded_at)
        store.add_status(
            character_id, claim.claim_id, claim.status,
            source_event_id=source_event.event_id, created_at_us=status_at,
        )
        if claim.superseded_by:
            store.add_relation(
                character_id, claim.claim_id, claim.superseded_by, "supersedes",
                created_at_us=status_at,
            )
            store.connection.execute(
                "UPDATE claims SET valid_to_us=? WHERE character_id=? AND claim_id=?",
                (status_at, character_id, claim.claim_id),
            )
    store.ensure_fts()
    return character_map
