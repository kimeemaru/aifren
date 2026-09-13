"""Synthetic, non-personal histories and on-demand scale generation."""

from dataclasses import replace
from datetime import datetime, timedelta

from aifren.memory_v2_store.models import BenchmarkFixture, GoldClaim, RetrievalCase, SyntheticEvent


CORE_FIXTURE_VERSION = "memory-v2-core-v2"


def _event(event_id, character_id, recorded_at, content, **kwargs):
    return SyntheticEvent(event_id, character_id, recorded_at, content, **kwargs)


def _claim(claim_id, character_id, category, content, sources, **kwargs):
    return GoldClaim(claim_id, character_id, category, content, tuple(sources), **kwargs)


def build_core_fixture():
    """Return a small, readable fixture representing several synthetic years."""
    events = (
        _event("lyra-001", "lyra", "2021-02-01T09:00:00Z", "I prefer red tea."),
        _event("lyra-002", "lyra", "2021-04-03T10:00:00Z", "I am allergic to walnuts."),
        _event("lyra-003", "lyra", "2021-05-10T18:00:00Z", "The pizza meteor joke still makes me laugh."),
        _event("lyra-004", "lyra", "2022-08-12T22:00:00Z", "We watched the Perseid meteor shower together."),
        _event("lyra-005", "lyra", "2022-12-04T08:00:00Z", "I have the flu this week.", valid_to="2022-12-11T23:59:00Z"),
        _event("lyra-006", "lyra", "2022-12-20T09:00:00Z", "I live in Toronto."),
        _event("lyra-007", "lyra", "2023-03-01T12:00:00Z", "I bought a blue notebook."),
        _event("lyra-008", "lyra", "2024-06-15T10:00:00Z", "I switched to green tea; it is my preference now."),
        _event("lyra-009", "lyra", "2025-04-01T11:00:00Z", "We plan to hike on July 20."),
        _event("lyra-010", "lyra", "2025-07-10T11:00:00Z", "The July hike is cancelled because of weather."),
        _event("lyra-011", "lyra", "2025-09-01T09:00:00Z", "I moved to Ottawa."),
        _event("lyra-012", "lyra", "2025-11-01T20:00:00Z", "We celebrated our first year of weekly stargazing."),
        _event("lyra-013", "lyra", "2026-01-05T08:00:00Z", "I still enjoy green tea every morning."),
        _event("lyra-014", "lyra", "2026-03-08T15:00:00Z", "We completed our museum trip and liked the astronomy exhibit."),
        _event("lyra-015", "lyra", "2026-04-12T12:00:00Z", "If I moved to Mars, I would grow cactus tea."),
        _event("lyra-016", "lyra", "2026-05-01T09:00:00Z", "Sure, I own a castle on the moon, obviously."),
        _event("lyra-017", "lyra", "2026-05-15T18:00:00Z", "Mara and Marina are different friends; Mara likes astronomy and Marina likes baking."),
        _event("lyra-018", "lyra", "2026-06-01T11:00:00Z", "I might visit Quebec someday, but I have not decided."),
        _event("lyra-019", "lyra", "2020-01-04T10:00:00Z", "I own a Nintendo 64, serial N64-CA-0042."),
        _event("lyra-020", "lyra", "2023-02-07T20:00:00Z", "We spent an evening troubleshooting Pokemon Stadium together."),
        _event("lyra-021", "lyra", "2021-01-10T09:00:00Z", "My friend Rose is a civil engineer."),
        _event("lyra-022", "lyra", "2021-01-11T09:00:00Z", "I planted a rose bush on the balcony."),
        _event("lyra-023", "lyra", "2019-04-01T12:00:00Z", "My archive locker code is cobalt-orchid."),
        _event("lyra-024", "lyra", "2019-04-02T12:00:00Z", "I once read an irrelevant 500-page passport manual."),
        _event("lyra-025", "lyra", "2026-07-01T18:00:00Z", "I have been baking sourdough every weekend lately."),
        _event("lyra-026", "lyra", "2026-07-02T18:00:00Z", "We repaired a disagreement by talking it through calmly."),
        _event("lyra-027", "lyra", "2026-07-03T18:00:00Z", "The old pizza meteor joke came up again; please do not repeat it unless I ask."),
        _event("mira-001", "mira", "2024-06-15T10:00:00Z", "I prefer coffee, not tea."),
        _event("mira-002", "mira", "2025-09-01T09:00:00Z", "I live in Skyhaven."),
        _event("mira-003", "mira", "2025-11-01T20:00:00Z", "We baked cinnamon rolls together."),
    )
    claims = (
        _claim("lyra-tea-red", "lyra", "preference", "The user preferred red tea.", ["lyra-001"], status="superseded", superseded_by="lyra-tea-green", topic="tea", importance=6),
        _claim("lyra-tea-green", "lyra", "preference", "The user currently prefers green tea.", ["lyra-008", "lyra-013"], topic="tea", importance=6),
        _claim("lyra-walnut-allergy", "lyra", "fact", "The user is allergic to walnuts.", ["lyra-002"], topic="health", importance=10),
        _claim("lyra-pizza-joke", "lyra", "running_joke", "The pizza meteor joke is a shared joke.", ["lyra-003"], topic="pizza-joke", importance=3),
        _claim("lyra-perseid", "lyra", "episode", "The user and Lyra watched the Perseid meteor shower together.", ["lyra-004"], topic="stargazing", importance=7),
        _claim("lyra-flu", "lyra", "temporary_state", "The user had the flu in December 2022.", ["lyra-005"], status="expired", valid_to="2022-12-11T23:59:00Z", topic="health", importance=3),
        _claim("lyra-toronto", "lyra", "location", "The user lived in Toronto in 2022.", ["lyra-006"], status="superseded", superseded_by="lyra-ottawa", valid_to="2025-09-01T09:00:00Z", topic="location", importance=7),
        _claim("lyra-hike-planned", "lyra", "future_event", "A July 2025 hike was planned.", ["lyra-009"], status="superseded", superseded_by="lyra-hike-cancelled", topic="hike", importance=5),
        _claim("lyra-hike-cancelled", "lyra", "future_event", "The July 2025 hike was cancelled because of weather.", ["lyra-010"], topic="hike", importance=5),
        _claim("lyra-ottawa", "lyra", "location", "The user currently lives in Ottawa.", ["lyra-011"], topic="location", importance=7),
        _claim("lyra-stargazing-milestone", "lyra", "relationship", "The user and Lyra marked a year of weekly stargazing.", ["lyra-012"], topic="stargazing", importance=7),
        _claim("lyra-museum-completed", "lyra", "episode", "The user and Lyra completed their museum trip.", ["lyra-014"], topic="museum", importance=6),
        _claim("lyra-n64", "lyra", "profile_fact", "The user owns a Nintendo 64.", ["lyra-019"], topic="n64", importance=5),
        _claim("lyra-n64-serial", "lyra", "identifier", "The user's Nintendo 64 serial is N64-CA-0042.", ["lyra-019"], topic="n64", importance=4),
        _claim("lyra-pokemon-episode", "lyra", "episode", "The user and Lyra spent an evening troubleshooting Pokemon Stadium.", ["lyra-020"], topic="pokemon-stadium", importance=6),
        _claim("lyra-rose-person", "lyra", "profile_fact", "Rose is the user's civil-engineer friend.", ["lyra-021"], topic="rose-person", importance=4),
        _claim("lyra-rose-plant", "lyra", "profile_fact", "The user planted a rose bush on the balcony.", ["lyra-022"], topic="gardening", importance=3),
        _claim("lyra-cobalt-needle", "lyra", "profile_fact", "The user's archive locker code is cobalt-orchid.", ["lyra-023"], topic="archive-needle", importance=5),
        _claim("lyra-passport-manual", "lyra", "profile_fact", "The user read a passport manual.", ["lyra-024"], topic="passport", importance=10),
        _claim("lyra-sourdough", "lyra", "profile_fact", "The user has recently been baking sourdough every weekend.", ["lyra-025"], topic="sourdough", importance=5),
        _claim("lyra-repair-episode", "lyra", "episode", "The user and Lyra repaired a disagreement by talking calmly.", ["lyra-026"], topic="relationship-repair", importance=8),
        _claim("mira-coffee", "mira", "preference", "The user currently prefers coffee.", ["mira-001"], topic="coffee", importance=6),
        _claim("mira-skyhaven", "mira", "location", "The user currently lives in Skyhaven.", ["mira-002"], topic="location", importance=7),
        _claim("mira-cinnamon-rolls", "mira", "episode", "The user and Mira baked cinnamon rolls together.", ["mira-003"], topic="baking", importance=6),
    )
    cases = (
        RetrievalCase("allergy-needle", "lyra", "Can you remind me about my food allergy?", "2026-08-01T12:00:00Z", ("lyra-walnut-allergy",), ("lyra-pizza-joke",)),
        RetrievalCase("unrelated-guitar", "lyra", "What beginner guitar should I try?", "2026-08-01T12:01:00Z", (), ("lyra-walnut-allergy", "lyra-pizza-joke", "lyra-tea-green")),
        RetrievalCase("current-tea", "lyra", "What tea do I prefer now?", "2026-08-01T12:02:00Z", ("lyra-tea-green",), ("lyra-tea-red",)),
        RetrievalCase("historical-tea", "lyra", "What tea did I prefer in 2021?", "2026-08-01T12:03:00Z", ("lyra-tea-red",), ("lyra-tea-green",)),
        RetrievalCase("cancelled-plan", "lyra", "Are we still hiking in July?", "2026-08-01T12:04:00Z", ("lyra-hike-cancelled",), ("lyra-hike-planned",)),
        RetrievalCase("historical-location", "lyra", "Where did I live in 2022?", "2026-08-01T12:05:00Z", ("lyra-toronto",), ("lyra-ottawa",)),
        RetrievalCase("completed-plan", "lyra", "Did we finish our museum trip?", "2026-08-01T12:05:30Z", ("lyra-museum-completed",), ()),
        RetrievalCase("shared-episode", "lyra", "Remember the meteor shower we watched?", "2026-08-01T12:06:00Z", ("lyra-perseid",), ()),
        RetrievalCase("explicit-joke", "lyra", "Tell me our pizza meteor joke again.", "2026-08-01T12:07:00Z", ("lyra-pizza-joke",), ()),
        RetrievalCase("joke-after-use", "lyra", "How is the weather today?", "2026-08-01T12:08:00Z", (), ("lyra-pizza-joke",), ("lyra-pizza-joke",)),
        RetrievalCase("mira-isolation", "mira", "What drink do I prefer, coffee or green tea?", "2026-08-01T12:09:00Z", ("mira-coffee",), ("lyra-tea-green",)),
        RetrievalCase("mira-location-isolation", "mira", "Where do I live now?", "2026-08-01T12:10:00Z", ("mira-skyhaven",), ("lyra-ottawa",)),
        # The cases below are contracts for a future hybrid retriever.  Cases
        # marked deterministic_only=False require a declared real embedding
        # evaluation; the fixture never treats token overlap as semantic proof.
        RetrievalCase("paraphrase-semantic", "lyra", "Do walnuts make me sick?", "2026-08-01T12:11:00Z", ("lyra-walnut-allergy",), deterministic_only=False, contract_tags=("paraphrase",), notes="Real-model semantic evaluation."),
        RetrievalCase("exact-phrase", "lyra", 'Find "cobalt-orchid" exactly.', "2026-08-01T12:12:00Z", ("lyra-cobalt-needle",), expected_channels=("exact", "fts")),
        RetrievalCase("identifier", "lyra", "Which console has serial N64-CA-0042?", "2026-08-01T12:13:00Z", ("lyra-n64-serial",), expected_channels=("exact",)),
        RetrievalCase("alias", "lyra", "Do I own an N64?", "2026-08-01T12:14:00Z", ("lyra-n64",), deterministic_only=False, notes="Alias resolution needs curated/model evaluation."),
        RetrievalCase("case-collision-person", "lyra", "What does Rose do for work?", "2026-08-01T12:15:00Z", ("lyra-rose-person",), ("lyra-rose-plant",)),
        RetrievalCase("same-word-different-entity", "lyra", "How is the rose bush doing?", "2026-08-01T12:16:00Z", ("lyra-rose-plant",), ("lyra-rose-person",)),
        RetrievalCase("assistant-echo-trap", "lyra", "What did I actually tell you about guitars?", "2026-08-01T12:17:00Z", (), contract_tags=("assistant-contamination",), requires_trace=True, notes="Assistant-only echo must not form a user-memory query source."),
        RetrievalCase("recent-visible-duplicate", "lyra", "I still prefer green tea.", "2026-08-01T12:18:00Z", (), recent_visible_claim_ids=("lyra-tea-green",), notes="Visible active turn normally wins over duplicate injection."),
        RetrievalCase("explicit-repeat-override", "lyra", "Please repeat the pizza meteor joke now.", "2026-08-01T12:19:00Z", ("lyra-pizza-joke",), recently_used_claim_ids=("lyra-pizza-joke",), query_mode="explicit_repeat"),
        RetrievalCase("channel-dominance", "lyra", "Tell me about our Pokemon Stadium evening and N64.", "2026-08-01T12:20:00Z", ("lyra-pokemon-episode", "lyra-n64"), expected_channels=("fts", "semantic"), contract_tags=("channel-dominance",), requires_trace=True),
        RetrievalCase("duplicate-multi-channel", "lyra", "Pokemon Stadium troubleshooting", "2026-08-01T12:21:00Z", ("lyra-pokemon-episode",), expected_channels=("fts", "semantic"), requires_trace=True, notes="One canonical claim ID despite multiple candidate lanes."),
        RetrievalCase("irrelevant-high-importance", "lyra", "What tea do I prefer?", "2026-08-01T12:22:00Z", ("lyra-tea-green",), ("lyra-passport-manual",)),
        RetrievalCase("old-relevant-needle", "lyra", "What is my archive locker code?", "2026-08-01T12:23:00Z", ("lyra-cobalt-needle",)),
        RetrievalCase("old-irrelevant-needle", "lyra", "What book should I read about gardening?", "2026-08-01T12:24:00Z", (), ("lyra-cobalt-needle",)),
        RetrievalCase("threshold-near-negative", "lyra", "Do I have any food sensitivity to almonds?", "2026-08-01T12:25:00Z", (), ("lyra-walnut-allergy",), requires_trace=True),
        RetrievalCase("current-vs-historical-intent", "lyra", "Where do I live these days, not in 2022?", "2026-08-01T12:26:00Z", ("lyra-ottawa",), ("lyra-toronto",)),
        RetrievalCase("expired-plan", "lyra", "Should we get ready for our July 2025 hike?", "2026-08-01T12:27:00Z", ("lyra-hike-cancelled",), ("lyra-hike-planned",)),
        RetrievalCase("profile-vs-episode", "lyra", "Do you remember our Pokemon Stadium night?", "2026-08-01T12:28:00Z", ("lyra-pokemon-episode",), ("lyra-n64",)),
        RetrievalCase("irrelevant-relationship", "lyra", "What is a good beginner guitar?", "2026-08-01T12:29:00Z", (), ("lyra-repair-episode",)),
        RetrievalCase("joke-suppression", "lyra", "How was your day?", "2026-08-01T12:30:00Z", (), ("lyra-pizza-joke",), ("lyra-pizza-joke",), contract_tags=("topic-cooldown",)),
        RetrievalCase("reinforced-memory", "lyra", "What have I been baking recently?", "2026-08-01T12:31:00Z", ("lyra-sourdough",), contract_tags=("reinforced",), notes="Reinforcement must be source-backed, not assistant repetition."),
        RetrievalCase("self-reinforcement-trap", "lyra", "What hobby did you just claim I had?", "2026-08-01T12:32:00Z", (), contract_tags=("self-reinforcement-trap",), requires_trace=True),
        RetrievalCase("topic-cooldown", "lyra", "What would you like to talk about?", "2026-08-01T12:33:00Z", (), ("lyra-pizza-joke",), ("lyra-pizza-joke",), contract_tags=("topic-cooldown",)),
        RetrievalCase("budget-starvation", "lyra", "What do I prefer and what did we watch together?", "2026-08-01T12:34:00Z", ("lyra-tea-green", "lyra-perseid"), final_injection_cap=2, final_token_budget=80, contract_tags=("budget", "claim-type-allocation", "multi-intent")),
        RetrievalCase("embedding-fingerprint-mismatch", "lyra", "What tea do I prefer?", "2026-08-01T12:35:00Z", (), embedding_state="stale", requires_trace=True),
        RetrievalCase("stale-embedding", "lyra", "What is the archive locker code?", "2026-08-01T12:36:00Z", (), embedding_state="stale", requires_trace=True),
        RetrievalCase("failed-embedding-job", "lyra", "What tea do I prefer?", "2026-08-01T12:37:00Z", (), embedding_state="retryable", requires_trace=True),
        RetrievalCase("one-hop-relationship", "lyra", "What shared stargazing memory relates to the meteor shower?", "2026-08-01T12:38:00Z", ("lyra-perseid", "lyra-stargazing-milestone"), relationship_hop_limit=1, final_injection_cap=2, notes="Future relation traversal is one source-backed hop."),
        RetrievalCase("relationship-amplification-trap", "lyra", "Do I like green tea?", "2026-08-01T12:39:00Z", ("lyra-tea-green",), ("lyra-perseid", "lyra-stargazing-milestone", "lyra-repair-episode"), relationship_hop_limit=1, final_injection_cap=2),
        RetrievalCase("fts-punctuation-safety", "lyra", 'Find N64-CA-0042: "cobalt-orchid" (exact).', "2026-08-01T12:40:00Z", (), requires_trace=True, notes="Must be safely escaped; no parser broadening or exception."),
        RetrievalCase("trace-abstention", "lyra", "Do I own a submarine?", "2026-08-01T12:41:00Z", (), requires_trace=True),
        RetrievalCase("policy-weak-fts", "lyra", "Tell me something interesting about astronomy.", "2026-08-01T12:42:00Z", (), contract_tags=("weak-fts",), requires_trace=True),
        RetrievalCase("policy-ambiguous-favorite", "lyra", "What's my favorite?", "2026-08-01T12:43:00Z", (), contract_tags=("ambiguity",), requires_trace=True),
        RetrievalCase("policy-assistant-opinion", "lyra", "What color do you like?", "2026-08-01T12:44:00Z", (), contract_tags=("assistant-opinion",), requires_trace=True),
    )
    structural_baseline_ids = {
        "allergy-needle", "unrelated-guitar", "current-tea", "historical-tea",
        "cancelled-plan", "historical-location", "completed-plan",
        "shared-episode", "explicit-joke", "joke-after-use", "mira-isolation",
        "mira-location-isolation",
    }
    cases = tuple(replace(case, baseline_compatible=case.case_id in structural_baseline_ids) for case in cases)
    return BenchmarkFixture(CORE_FIXTURE_VERSION, events, claims, cases)


def structural_baseline_cases(fixture):
    """Cases the existing lexical-only V2 structural baseline is expected to gate."""
    return tuple(case for case in fixture.retrieval_cases if case.baseline_compatible)


def generate_scale_fixture(event_count, character_id="scale-character"):
    """Generate deterministic history on demand; do not commit huge fixtures."""
    if event_count < 1:
        raise ValueError("event_count must be positive")
    start = datetime(2010, 1, 1)
    events = []
    for index in range(event_count):
        when = start + timedelta(days=index)
        topic = index % 25
        content = f"Synthetic event {index}: topic {topic}, detail {index % 7}."
        events.append(_event(f"scale-{index:07d}", character_id, when.isoformat() + "Z", content))
    needle_event = _event("scale-needle-event", character_id, start.isoformat() + "Z", "The archive needle is cobalt-orchid.")
    events[0] = needle_event
    needle = _claim("scale-needle", character_id, "fact", "The synthetic archive needle is cobalt-orchid.", (needle_event.event_id,), topic="needle", importance=10)
    filler_claims = tuple(
        _claim(
            f"scale-claim-{index:07d}",
            character_id,
            "synthetic",
            f"Synthetic archived detail {index}: topic {index % 25}, value {index % 7}.",
            (events[index].event_id,),
            topic=f"topic-{index % 25}",
        )
        for index in range(1, event_count)
    )
    case = RetrievalCase("scale-needle-query", character_id, "What is the archive needle?", (start + timedelta(days=event_count)).isoformat() + "Z", (needle.claim_id,))
    return BenchmarkFixture(f"memory-v2-scale-{event_count}", tuple(events), (needle,) + filler_claims, (case,))
