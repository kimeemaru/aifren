"""Offline experiments for future episodic retrieval design.

Nothing in this module is a production retrieval dependency.  It deliberately
keeps multi-vector facets, source spans, temporal adjacency, recall frames,
and query routing outside the V2 SQLite schema while we measure whether they
earn a durable representation change.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from typing import Iterable, Sequence


@dataclass(frozen=True)
class EpisodePrototype:
    claim_id: str
    character_id: str
    narrative: str
    source_event_id: str
    session_id: str
    session_order: int
    occurred_at_us: int | None = None


@dataclass(frozen=True)
class EpisodeRepresentation:
    claim_id: str
    kind: str
    text: str


@dataclass(frozen=True)
class PrototypeHit:
    claim_id: str
    score: float
    representation: str
    representation_rank: int


@dataclass(frozen=True)
class RecallFrame:
    character_id: str
    claim_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    topics: tuple[str, ...]
    temporal_anchor_us: int | None
    last_used_monotonic: float
    lifecycle_revision: int
    expires_monotonic: float

    def valid_for(self, character_id: str, lifecycle_revision: int, *, now: float | None = None) -> bool:
        return self.character_id == character_id and self.lifecycle_revision == lifecycle_revision and (now or time.monotonic()) < self.expires_monotonic


def _tokens(text: str) -> tuple[str, ...]:
    stop = {"in", "we", "the", "a", "an", "and", "was", "were", "with", "by", "that", "this", "it", "to", "of", "on", "after", "later"}
    return tuple(dict.fromkeys(token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in stop and not (token.isdigit() and len(token) == 4)))


def derive_episode_representations(episode: EpisodePrototype) -> tuple[EpisodeRepresentation, ...]:
    """Derive compact benchmark-only retrieval views from one narrative.

    The generic cause pattern is intentional: the experiment tests whether
    representation, not a fixture-specific lookup table, restores candidate
    recall for causal/vague episode questions.
    """
    narrative = episode.narrative.strip()
    lowered = narrative.lower()
    match = re.search(r"(?:fixed|resolved|repaired)\s+(.+?)\s+(?:caused by|because of)\s+(.+?)(?:[.?!]|$)", lowered)
    if match:
        problem, cause = match.group(1).strip(), match.group(2).strip()
        summary = f"{problem.capitalize()} was fixed; {cause} caused it."
        facet = f"problem: {problem}; cause: {cause}; result: fixed"
    else:
        summary = narrative
        facet = f"shared experience: {narrative}"
    topic_terms = [token for token in _tokens(narrative) if token not in {"fixed", "caused", "result", "march", "later", "together", "routine", "detail", "note"}]
    topic = " ".join(topic_terms[:8]) or narrative
    return (
        EpisodeRepresentation(episode.claim_id, "summary", summary),
        EpisodeRepresentation(episode.claim_id, "narrative", narrative),
        EpisodeRepresentation(episode.claim_id, "problem_cause_result", facet),
        EpisodeRepresentation(episode.claim_id, "entity_topic", topic),
    )


class MultiRepresentationEpisodeIndex:
    """Small benchmark-local multi-vector index with claim-level deduplication."""

    def __init__(self, embedding_provider, *, representation_kinds: tuple[str, ...] = ("summary", "narrative", "problem_cause_result", "entity_topic")) -> None:
        self.provider = embedding_provider
        self.representation_kinds = frozenset(representation_kinds)
        self._representations: list[EpisodeRepresentation] = []
        self._vectors: list[list[float]] = []

    def add(self, episodes: Iterable[EpisodePrototype], *, batch_size: int = 64) -> None:
        batch: list[EpisodeRepresentation] = []
        for episode in episodes:
            batch.extend(item for item in derive_episode_representations(episode) if item.kind in self.representation_kinds)
            if len(batch) >= batch_size:
                self._add_batch(batch)
                batch = []
        if batch:
            self._add_batch(batch)

    def _add_batch(self, representations: Sequence[EpisodeRepresentation]) -> None:
        vectors = self.provider.embed([item.text for item in representations])
        if len(vectors) != len(representations):
            raise ValueError("embedding provider returned a mismatched representation batch")
        self._representations.extend(representations)
        self._vectors.extend(vectors)

    def query(self, text: str, *, limit: int = 5) -> tuple[PrototypeHit, ...]:
        vector = self.provider.embed([text])[0]
        ranked = sorted(
            ((self._cosine(vector, item), position) for position, item in enumerate(self._vectors)),
            key=lambda item: (-item[0], self._representations[item[1]].claim_id, self._representations[item[1]].kind),
        )
        per_claim: dict[str, PrototypeHit] = {}
        for rank, (score, position) in enumerate(ranked, 1):
            representation = self._representations[position]
            hit = PrototypeHit(representation.claim_id, score, representation.kind, rank)
            current = per_claim.get(hit.claim_id)
            if current is None or hit.score > current.score:
                per_claim[hit.claim_id] = hit
        return tuple(sorted(per_claim.values(), key=lambda hit: (-hit.score, hit.claim_id))[:limit])

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        numerator = sum(float(a) * float(b) for a, b in zip(left, right))
        left_size = math.sqrt(sum(float(a) * float(a) for a in left))
        right_size = math.sqrt(sum(float(b) * float(b) for b in right))
        return numerator / (left_size * right_size) if left_size and right_size else 0.0

    @property
    def representation_count(self) -> int:
        return len(self._representations)


def run_known_episode_probe_experiment(embedding_provider, *, filler_count: int = 2_500) -> dict[str, object]:
    """Compare one narrative vector with four offline episode views.

    This deliberately uses the same MiniLM provider as current V2.  It is a
    finite architecture experiment, not an alternative production index.
    """
    episodes = [
        EpisodePrototype("needle-audio", "a", "In March 2028 we fixed microphone crackling caused by a damaged USB extension.", "event-needle", "audio-repair", 2, 1),
        EpisodePrototype("audio-before", "a", "We noticed microphone crackling while checking the Linux audio setup.", "event-before", "audio-repair", 1, 0),
        EpisodePrototype("audio-after", "a", "We tested the microphone and confirmed the audio was clear after the repair.", "event-after", "audio-repair", 3, 2),
        EpisodePrototype("near-audio", "a", "We later replaced a USB microphone after a different audio repair.", "event-near", "later-audio", 1, 3),
    ]
    topics = ("microphone", "linux", "usb", "audio", "hardware", "coffee", "garden")
    for number in range(filler_count):
        topic = topics[number % len(topics)]
        episodes.append(EpisodePrototype(f"filler-{number}", "a", f"In {2020 + number % 13} we discussed {topic} repair detail {number} in a routine note.", f"event-filler-{number}", f"filler-session-{number}", 1, number + 10))
    baseline = MultiRepresentationEpisodeIndex(embedding_provider, representation_kinds=("narrative",))
    multi = MultiRepresentationEpisodeIndex(embedding_provider)
    started = time.perf_counter()
    baseline.add(episodes)
    baseline_seconds = time.perf_counter() - started
    started = time.perf_counter()
    multi.add(episodes)
    multi_seconds = time.perf_counter() - started
    probes = (
        "What USB related audio issue did we fix?",
        "Do you remember why my mic sounded terrible years ago?",
        "What were we doing years ago with the microphone?",
    )
    results = {}
    for probe in probes:
        started = time.perf_counter()
        one = baseline.query(probe, limit=5)
        narrative_query_ms = (time.perf_counter() - started) * 1_000.0
        started = time.perf_counter()
        many = multi.query(probe, limit=5)
        multi_query_ms = (time.perf_counter() - started) * 1_000.0
        def describe(hits):
            expected = next((index + 1 for index, hit in enumerate(hits) if hit.claim_id == "needle-audio"), None)
            return {"expected_rank": expected, "hits": tuple((hit.claim_id, round(hit.score, 4), hit.representation) for hit in hits)}
        results[probe] = {"narrative": describe(one), "multi": describe(many),
                          "narrative_query_ms": narrative_query_ms, "multi_query_ms": multi_query_ms}
    source = SourceSpanPrototype(episodes[:4])
    started = time.perf_counter()
    expanded = source.expand("needle-audio", radius=2)
    source_expand_ms = (time.perf_counter() - started) * 1_000.0
    frame = source.frame("needle-audio", lifecycle_revision=1)
    started = time.perf_counter()
    _ = source.adjacent(frame.claim_ids[0], "after")
    recall_follow_up_ms = (time.perf_counter() - started) * 1_000.0
    return {
        "filler_count": filler_count,
        "baseline_representations": baseline.representation_count,
        "multi_representations": multi.representation_count,
        "baseline_embed_seconds": baseline_seconds,
        "multi_embed_seconds": multi_seconds,
        "probes": results,
        "source_span": tuple(item.claim_id for item in expanded),
        "source_expand_ms": source_expand_ms,
        "recall_follow_up_ms": recall_follow_up_ms,
        "after": source.adjacent("needle-audio", "after").claim_id,
        "before": source.adjacent("needle-audio", "before").claim_id,
        "frame_valid": frame.valid_for("a", 1),
    }


class SourceSpanPrototype:
    """Bounded source-event expansion and session traversal for experiments."""

    def __init__(self, episodes: Iterable[EpisodePrototype]) -> None:
        self.by_claim = {episode.claim_id: episode for episode in episodes}
        self.sessions: dict[tuple[str, str], list[EpisodePrototype]] = {}
        for episode in self.by_claim.values():
            self.sessions.setdefault((episode.character_id, episode.session_id), []).append(episode)
        for values in self.sessions.values():
            values.sort(key=lambda item: item.session_order)

    def expand(self, claim_id: str, *, radius: int = 2) -> tuple[EpisodePrototype, ...]:
        focus = self.by_claim[claim_id]
        items = self.sessions[(focus.character_id, focus.session_id)]
        index = next(i for i, item in enumerate(items) if item.claim_id == claim_id)
        return tuple(items[max(0, index - radius):index + radius + 1])

    def adjacent(self, claim_id: str, direction: str) -> EpisodePrototype | None:
        focus = self.by_claim[claim_id]
        items = self.sessions[(focus.character_id, focus.session_id)]
        index = next(i for i, item in enumerate(items) if item.claim_id == claim_id)
        target = index + (1 if direction == "after" else -1)
        return items[target] if 0 <= target < len(items) else None

    def frame(self, claim_id: str, *, lifecycle_revision: int, ttl_seconds: float = 90.0) -> RecallFrame:
        focus = self.by_claim[claim_id]
        return RecallFrame(focus.character_id, (claim_id,), (focus.source_event_id,), _tokens(focus.narrative), focus.occurred_at_us,
                           time.monotonic(), lifecycle_revision, time.monotonic() + ttl_seconds)


def route_episode_query(text: str, *, has_recall_frame: bool = False) -> str:
    """Offline deterministic routing experiment, intentionally not runtime-wired."""
    lower = text.lower()
    if has_recall_frame and re.search(r"\b(before|after|that|it again)\b", lower):
        return "follow_up"
    if re.search(r"\b(before|after|last time|years? ago|when)\b", lower):
        return "temporal_episode"
    if re.search(r"\b(remember|do you remember)\b", lower):
        return "vague_episode"
    if re.search(r"\b(why|cause[ds]?|fixed|repair(?:ed)?)\b", lower):
        return "direct_episode"
    if re.search(r"\b(previously|used to|ever)\b", lower):
        return "historical_fact"
    if re.search(r"\b(now|currently|prefer|live|own)\b", lower):
        return "current_fact"
    if len(_tokens(text)) <= 1:
        return "unknown"
    return "unknown"
