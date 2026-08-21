"""Synthetic, non-personal histories and on-demand scale generation."""

from dataclasses import replace
from datetime import datetime, timedelta

from .models import BenchmarkFixture, GoldClaim, RetrievalCase, SyntheticEvent


CORE_FIXTURE_VERSION = "memory-v2-core-v2"


def _event(event_id, character_id, recorded_at, content, **kwargs):
    return SyntheticEvent(event_id, character_id, recorded_at, content, **kwargs)


def _claim(claim_id, character_id, category, content, sources, **kwargs):
    return GoldClaim(claim_id, character_id, category, content, tuple(sources), **kwargs)


def build_core_fixture():
    """Return a small, readable fixture representing several synthetic years."""
    events = (
        _event("serval-001", "serval", "2021-02-01T09:00:00Z", "I prefer red tea."),
        _event("serval-002", "serval", "2021-04-03T10:00:00Z", "I am allergic to walnuts."),
        _event("serval-003", "serval", "2021-05-10T18:00:00Z", "The pizza meteor joke still makes me laugh."),
        _event("serval-004", "serval", "2022-08-12T22:00:00Z", "We watched the Perseid meteor shower together."),
        _event("serval-005", "serval", "2022-12-04T08:00:00Z", "I have the flu this week.", valid_to="2022-12-11T23:59:00Z"),
        _event("serval-006", "serval", "2022-12-20T09:00:00Z", "I live in Toronto."),
        _event("serval-007", "serval", "2023-03-01T12:00:00Z", "I bought a blue notebook."),
        _event("serval-008", "serval", "2024-06-15T10:00:00Z", "I switched to green tea; it is my preference now."),
        _event("serval-009", "serval", "2025-04-01T11:00:00Z", "We plan to hike on July 20."),
        _event("serval-010", "serval", "2025-07-10T11:00:00Z", "The July hike is cancelled because of weather."),
        _event("serval-011", "serval", "2025-09-01T09:00:00Z", "I moved to Ottawa."),
        _event("serval-012", "serval", "2025-11-01T20:00:00Z", "We celebrated our first year of weekly stargazing."),
        _event("serval-013", "serval", "2026-01-05T08:00:00Z", "I still enjoy green tea every morning."),
        _event("serval-014", "serval", "2026-03-08T15:00:00Z", "We completed our museum trip and liked the astronomy exhibit."),
        _event("serval-015", "serval", "2026-04-12T12:00:00Z", "If I moved to Mars, I would grow cactus tea."),
        _event("serval-016", "serval", "2026-05-01T09:00:00Z", "Sure, I own a castle on the moon, obviously."),
        _event("serval-017", "serval", "2026-05-15T18:00:00Z", "Mara and Marina are different friends; Mara likes astronomy and Marina likes baking."),
        _event("serval-018", "serval", "2026-06-01T11:00:00Z", "I might visit Quebec someday, but I have not decided."),
        _event("serval-019", "serval", "2020-01-04T10:00:00Z", "I own a Nintendo 64, serial N64-CA-0042."),
        _event("serval-020", "serval", "2023-02-07T20:00:00Z", "We spent an evening troubleshooting Pokemon Stadium together."),
        _event("serval-021", "serval", "2021-01-10T09:00:00Z", "My friend Rose is a civil engineer."),
        _event("serval-022", "serval", "2021-01-11T09:00:00Z", "I planted a rose bush on the balcony."),
        _event("serval-023", "serval", "2019-04-01T12:00:00Z", "My archive locker code is cobalt-orchid."),
        _event("serval-024", "serval", "2019-04-02T12:00:00Z", "I once read an irrelevant 500-page passport manual."),
        _event("serval-025", "serval", "2026-07-01T18:00:00Z", "I have been baking sourdough every weekend lately."),
        _event("serval-026", "serval", "2026-07-02T18:00:00Z", "We repaired a disagreement by talking it through calmly."),
        _event("serval-027", "serval", "2026-07-03T18:00:00Z", "The old pizza meteor joke came up again; please do not repeat it unless I ask."),
        _event("mira-001", "mira", "2024-06-15T10:00:00Z", "I prefer coffee, not tea."),
        _event("mira-002", "mira", "2025-09-01T09:00:00Z", "I live in Skyhaven."),
        _event("mira-003", "mira", "2025-11-01T20:00:00Z", "We baked cinnamon rolls together."),
    )
    claims = (
        _claim("serval-tea-red", "serval", "preference", "The user preferred red tea.", ["serval-001"], status="superseded", superseded_by="serval-tea-green", topic="tea", importance=6),
        _claim("serval-tea-green", "serval", "preference", "The user currently prefers green tea.", ["serval-008", "serval-013"], topic="tea", importance=6),
        _claim("serval-walnut-allergy", "serval", "fact", "The user is allergic to walnuts.", ["serval-002"], topic="health", importance=10),
        _claim("serval-pizza-joke", "serval", "running_joke", "The pizza meteor joke is a shared joke.", ["serval-003"], topic="pizza-joke", importance=3),
        _claim("serval-perseid", "serval", "episode", "The user and Serval watched the Perseid meteor shower together.", ["serval-004"], topic="stargazing", importance=7),
        _claim("serval-flu", "serval", "temporary_state", "The user had the flu in December 2022.", ["serval-005"], status="expired", valid_to="2022-12-11T23:59:00Z", topic="health", importance=3),
        _claim("serval-toronto", "serval", "location", "The user lived in Toronto in 2022.", ["serval-006"], status="superseded", superseded_by="serval-ottawa", valid_to="2025-09-01T09:00:00Z", topic="location", importance=7),
        _claim("serval-hike-planned", "serval", "future_event", "A July 2025 hike was planned.", ["serval-009"], status="superseded", superseded_by="serval-hike-cancelled", topic="hike", importance=5),
        _claim("serval-hike-cancelled", "serval", "future_event", "The July 2025 hike was cancelled because of weather.", ["serval-010"], topic="hike", importance=5),
        _claim("serval-ottawa", "serval", "location", "The user currently lives in Ottawa.", ["serval-011"], topic="location", importance=7),
        _claim("serval-stargazing-milestone", "serval", "relationship", "The user and Serval marked a year of weekly stargazing.", ["serval-012"], topic="stargazing", importance=7),
        _claim("serval-museum-completed", "serval", "episode", "The user and Serval completed their museum trip.", ["serval-014"], topic="museum", importance=6),
        _claim("serval-n64", "serval", "profile_fact", "The user owns a Nintendo 64.", ["serval-019"], topic="n64", importance=5),
        _claim("serval-n64-serial", "serval", "identifier", "The user's Nintendo 64 serial is N64-CA-0042.", ["serval-019"], topic="n64", importance=4),
        _claim("serval-pokemon-episode", "serval", "episode", "The user and Serval spent an evening troubleshooting Pokemon Stadium.", ["serval-020"], topic="pokemon-stadium", importance=6),
        _claim("serval-rose-person", "serval", "profile_fact", "Rose is the user's civil-engineer friend.", ["serval-021"], topic="rose-person", importance=4),
        _claim("serval-rose-plant", "serval", "profile_fact", "The user planted a rose bush on the balcony.", ["serval-022"], topic="gardening", importance=3),
        _claim("serval-cobalt-needle", "serval", "profile_fact", "The user's archive locker code is cobalt-orchid.", ["serval-023"], topic="archive-needle", importance=5),
        _claim("serval-passport-manual", "serval", "profile_fact", "The user read a passport manual.", ["serval-024"], topic="passport", importance=10),
        _claim("serval-sourdough", "serval", "profile_fact", "The user has recently been baking sourdough every weekend.", ["serval-025"], topic="sourdough", importance=5),
        _claim("serval-repair-episode", "serval", "episode", "The user and Serval repaired a disagreement by talking calmly.", ["serval-026"], topic="relationship-repair", importance=8),
        _claim("mira-coffee", "mira", "preference", "The user currently prefers coffee.", ["mira-001"], topic="coffee", importance=6),
        _claim("mira-skyhaven", "mira", "location", "The user currently lives in Skyhaven.", ["mira-002"], topic="location", importance=7),
        _claim("mira-cinnamon-rolls", "mira", "episode", "The user and Mira baked cinnamon rolls together.", ["mira-003"], topic="baking", importance=6),
    )
    cases = (
        RetrievalCase("allergy-needle", "serval", "Can you remind me about my food allergy?", "2026-08-01T12:00:00Z", ("serval-walnut-allergy",), ("serval-pizza-joke",)),
        RetrievalCase("unrelated-guitar", "serval", "What beginner guitar should I try?", "2026-08-01T12:01:00Z", (), ("serval-walnut-allergy", "serval-pizza-joke", "serval-tea-green")),
        RetrievalCase("current-tea", "serval", "What tea do I prefer now?", "2026-08-01T12:02:00Z", ("serval-tea-green",), ("serval-tea-red",)),
        RetrievalCase("historical-tea", "serval", "What tea did I prefer in 2021?", "2026-08-01T12:03:00Z", ("serval-tea-red",), ("serval-tea-green",)),
        RetrievalCase("cancelled-plan", "serval", "Are we still hiking in July?", "2026-08-01T12:04:00Z", ("serval-hike-cancelled",), ("serval-hike-planned",)),
        RetrievalCase("historical-location", "serval", "Where did I live in 2022?", "2026-08-01T12:05:00Z", ("serval-toronto",), ("serval-ottawa",)),
        RetrievalCase("completed-plan", "serval", "Did we finish our museum trip?", "2026-08-01T12:05:30Z", ("serval-museum-completed",), ()),
        RetrievalCase("shared-episode", "serval", "Remember the meteor shower we watched?", "2026-08-01T12:06:00Z", ("serval-perseid",), ()),
        RetrievalCase("explicit-joke", "serval", "Tell me our pizza meteor joke again.", "2026-08-01T12:07:00Z", ("serval-pizza-joke",), ()),
        RetrievalCase("joke-after-use", "serval", "How is the weather today?", "2026-08-01T12:08:00Z", (), ("serval-pizza-joke",), ("serval-pizza-joke",)),
        RetrievalCase("mira-isolation", "mira", "What drink do I prefer, coffee or green tea?", "2026-08-01T12:09:00Z", ("mira-coffee",), ("serval-tea-green",)),
        RetrievalCase("mira-location-isolation", "mira", "Where do I live now?", "2026-08-01T12:10:00Z", ("mira-skyhaven",), ("serval-ottawa",)),
        # The cases below are contracts for a future hybrid retriever.  Cases
        # marked deterministic_only=False require a declared real embedding
        # evaluation; the fixture never treats token overlap as semantic proof.
        RetrievalCase("paraphrase-semantic", "serval", "Do walnuts make me sick?", "2026-08-01T12:11:00Z", ("serval-walnut-allergy",), deterministic_only=False, contract_tags=("paraphrase",), notes="Real-model semantic evaluation."),
        RetrievalCase("exact-phrase", "serval", 'Find "cobalt-orchid" exactly.', "2026-08-01T12:12:00Z", ("serval-cobalt-needle",), expected_channels=("exact", "fts")),
        RetrievalCase("identifier", "serval", "Which console has serial N64-CA-0042?", "2026-08-01T12:13:00Z", ("serval-n64-serial",), expected_channels=("exact",)),
        RetrievalCase("alias", "serval", "Do I own an N64?", "2026-08-01T12:14:00Z", ("serval-n64",), deterministic_only=False, notes="Alias resolution needs curated/model evaluation."),
        RetrievalCase("case-collision-person", "serval", "What does Rose do for work?", "2026-08-01T12:15:00Z", ("serval-rose-person",), ("serval-rose-plant",)),
        RetrievalCase("same-word-different-entity", "serval", "How is the rose bush doing?", "2026-08-01T12:16:00Z", ("serval-rose-plant",), ("serval-rose-person",)),
        RetrievalCase("assistant-echo-trap", "serval", "What did I actually tell you about guitars?", "2026-08-01T12:17:00Z", (), contract_tags=("assistant-contamination",), requires_trace=True, notes="Assistant-only echo must not form a user-memory query source."),
        RetrievalCase("recent-visible-duplicate", "serval", "I still prefer green tea.", "2026-08-01T12:18:00Z", (), recent_visible_claim_ids=("serval-tea-green",), notes="Visible active turn normally wins over duplicate injection."),
        RetrievalCase("explicit-repeat-override", "serval", "Please repeat the pizza meteor joke now.", "2026-08-01T12:19:00Z", ("serval-pizza-joke",), recently_used_claim_ids=("serval-pizza-joke",), query_mode="explicit_repeat"),
        RetrievalCase("channel-dominance", "serval", "Tell me about our Pokemon Stadium evening and N64.", "2026-08-01T12:20:00Z", ("serval-pokemon-episode", "serval-n64"), expected_channels=("fts", "semantic"), contract_tags=("channel-dominance",), requires_trace=True),
        RetrievalCase("duplicate-multi-channel", "serval", "Pokemon Stadium troubleshooting", "2026-08-01T12:21:00Z", ("serval-pokemon-episode",), expected_channels=("fts", "semantic"), requires_trace=True, notes="One canonical claim ID despite multiple candidate lanes."),
        RetrievalCase("irrelevant-high-importance", "serval", "What tea do I prefer?", "2026-08-01T12:22:00Z", ("serval-tea-green",), ("serval-passport-manual",)),
        RetrievalCase("old-relevant-needle", "serval", "What is my archive locker code?", "2026-08-01T12:23:00Z", ("serval-cobalt-needle",)),
        RetrievalCase("old-irrelevant-needle", "serval", "What book should I read about gardening?", "2026-08-01T12:24:00Z", (), ("serval-cobalt-needle",)),
        RetrievalCase("threshold-near-negative", "serval", "Do I have any food sensitivity to almonds?", "2026-08-01T12:25:00Z", (), ("serval-walnut-allergy",), requires_trace=True),
        RetrievalCase("current-vs-historical-intent", "serval", "Where do I live these days, not in 2022?", "2026-08-01T12:26:00Z", ("serval-ottawa",), ("serval-toronto",)),
        RetrievalCase("expired-plan", "serval", "Should we get ready for our July 2025 hike?", "2026-08-01T12:27:00Z", ("serval-hike-cancelled",), ("serval-hike-planned",)),
        RetrievalCase("profile-vs-episode", "serval", "Do you remember our Pokemon Stadium night?", "2026-08-01T12:28:00Z", ("serval-pokemon-episode",), ("serval-n64",)),
        RetrievalCase("irrelevant-relationship", "serval", "What is a good beginner guitar?", "2026-08-01T12:29:00Z", (), ("serval-repair-episode",)),
        RetrievalCase("joke-suppression", "serval", "How was your day?", "2026-08-01T12:30:00Z", (), ("serval-pizza-joke",), ("serval-pizza-joke",), contract_tags=("topic-cooldown",)),
        RetrievalCase("reinforced-memory", "serval", "What have I been baking recently?", "2026-08-01T12:31:00Z", ("serval-sourdough",), contract_tags=("reinforced",), notes="Reinforcement must be source-backed, not assistant repetition."),
        RetrievalCase("self-reinforcement-trap", "serval", "What hobby did you just claim I had?", "2026-08-01T12:32:00Z", (), contract_tags=("self-reinforcement-trap",), requires_trace=True),
        RetrievalCase("topic-cooldown", "serval", "What would you like to talk about?", "2026-08-01T12:33:00Z", (), ("serval-pizza-joke",), ("serval-pizza-joke",), contract_tags=("topic-cooldown",)),
        RetrievalCase("budget-starvation", "serval", "What do I prefer and what did we watch together?", "2026-08-01T12:34:00Z", ("serval-tea-green", "serval-perseid"), final_injection_cap=2, final_token_budget=80, contract_tags=("budget", "claim-type-allocation", "multi-intent")),
        RetrievalCase("embedding-fingerprint-mismatch", "serval", "What tea do I prefer?", "2026-08-01T12:35:00Z", (), embedding_state="stale", requires_trace=True),
        RetrievalCase("stale-embedding", "serval", "What is the archive locker code?", "2026-08-01T12:36:00Z", (), embedding_state="stale", requires_trace=True),
        RetrievalCase("failed-embedding-job", "serval", "What tea do I prefer?", "2026-08-01T12:37:00Z", (), embedding_state="retryable", requires_trace=True),
        RetrievalCase("one-hop-relationship", "serval", "What shared stargazing memory relates to the meteor shower?", "2026-08-01T12:38:00Z", ("serval-perseid", "serval-stargazing-milestone"), relationship_hop_limit=1, final_injection_cap=2, notes="Future relation traversal is one source-backed hop."),
        RetrievalCase("relationship-amplification-trap", "serval", "Do I like green tea?", "2026-08-01T12:39:00Z", ("serval-tea-green",), ("serval-perseid", "serval-stargazing-milestone", "serval-repair-episode"), relationship_hop_limit=1, final_injection_cap=2),
        RetrievalCase("fts-punctuation-safety", "serval", 'Find N64-CA-0042: "cobalt-orchid" (exact).', "2026-08-01T12:40:00Z", (), requires_trace=True, notes="Must be safely escaped; no parser broadening or exception."),
        RetrievalCase("trace-abstention", "serval", "Do I own a submarine?", "2026-08-01T12:41:00Z", (), requires_trace=True),
        RetrievalCase("policy-weak-fts", "serval", "Tell me something interesting about astronomy.", "2026-08-01T12:42:00Z", (), contract_tags=("weak-fts",), requires_trace=True),
        RetrievalCase("policy-ambiguous-favorite", "serval", "What's my favorite?", "2026-08-01T12:43:00Z", (), contract_tags=("ambiguity",), requires_trace=True),
        RetrievalCase("policy-assistant-opinion", "serval", "What color do you like?", "2026-08-01T12:44:00Z", (), contract_tags=("assistant-opinion",), requires_trace=True),
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
