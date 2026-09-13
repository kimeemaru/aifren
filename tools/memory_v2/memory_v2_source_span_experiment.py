"""Benchmark-only test of bounded canonical source-span retrieval.

This is deliberately not a Memory V2 schema, writer, or runtime change.  It
asks one narrow question: when an episodic claim is a lossy narrative, can a
separate bounded ANN lane over richer canonical source spans recover the
episode without broadening the final candidate set?
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import resource
import time
import uuid
from typing import Iterable, Sequence


FROZEN_MICROPHONE_PROBES = (
    ("direct_microphone", "What USB related audio issue did we fix?", "needle-audio", ("near-audio",)),
    ("vague_microphone", "Do you remember why my mic sounded terrible years ago?", "needle-audio", ("near-audio",)),
    ("temporal_microphone", "What were we doing years ago with the microphone?", "needle-audio", ("near-audio",)),
)

# A fixed benchmark reference time makes relative-age parsing deterministic and
# avoids attaching meaning to the machine clock.  It is benchmark metadata, not
# a production temporal policy.
_BENCHMARK_REFERENCE_US = int(datetime(2035, 3, 1, tzinfo=timezone.utc).timestamp() * 1_000_000)
_HISTORICAL_START_US = int(datetime(2025, 3, 1, tzinfo=timezone.utc).timestamp() * 1_000_000)
_HISTORICAL_END_US = int(datetime(2033, 3, 1, tzinfo=timezone.utc).timestamp() * 1_000_000)


class MPNetEmbeddingProvider:
    """CPU-only challenger adapter for this source-span experiment only."""

    model_name = "sentence-transformers/all-mpnet-base-v2"
    device = "cpu"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name, device=self.device)
        self.dimensions = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]


@dataclass(frozen=True)
class _Record:
    span_id: str
    claim_id: str
    character_id: str
    text: str
    eligible: bool = True


@dataclass(frozen=True)
class _TemporalMetadata:
    """Canonical-style event/session metadata attached only inside this benchmark."""

    span_id: str
    claim_id: str
    character_id: str
    eligible: bool
    timestamp_us: int
    session_order: int


@dataclass(frozen=True)
class ProbeMeasurement:
    expected_rank: int | None
    top1: bool
    top3: bool
    top5: bool
    selected_claim_ids: tuple[str, ...]
    important_distractor_ranks: dict[str, int | None]
    abstained: bool
    warm_query_ms: float
    ineligible_selected: tuple[str, ...]


@dataclass(frozen=True)
class SourceSpanExperimentReport:
    scale: int
    candidate_limit: int
    candidate_bound: int
    baseline: dict[str, ProbeMeasurement]
    source_span: dict[str, ProbeMeasurement]
    build_seconds: dict[str, float]
    rss_mib: dict[str, float]
    source_span_count: int
    hard_invariant_failures: int
    baseline_abstention_false_positives: int
    source_span_abstention_false_positives: int
    factual_regressions: int
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TemporalCandidateMeasurement:
    candidate_limit: int
    matching_historical_records: int
    candidate_count: int
    expected_candidate_rank: int | None
    candidate_claim_ids: tuple[str, ...]
    final: ProbeMeasurement
    metadata_lookup_ms: float
    mpnet_scoring_ms: float
    total_temporal_path_ms: float


@dataclass(frozen=True)
class TemporalSignalAuditReport:
    scale: int
    temporal_candidates: dict[int, TemporalCandidateMeasurement]
    semantic_baseline: dict[str, ProbeMeasurement]
    index_build_ms: float
    hard_invariant_failures: int
    factual_regressions: int
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


class _BoundedAnnLane:
    """In-memory, fixed-depth ANN lane used only by this benchmark.

    It maps a source hit to a canonical claim before ranking.  ``ef`` and
    ``candidate_limit`` are intentionally fixed constants, independent of
    archive size.  The experiment never asks this index for exhaustive rank.
    """

    def __init__(self, provider, records: Sequence[_Record], *, candidate_limit: int, ef: int) -> None:
        import hnswlib  # Benchmark dependency already used by V2's derived ANN.
        import numpy as np

        self.provider = provider
        self.records = tuple(records)
        self.candidate_limit = candidate_limit
        self._np = np
        self._index = hnswlib.Index(space="cosine", dim=provider.dimensions)
        self._index.init_index(max_elements=max(16, len(self.records)), ef_construction=160, M=16)
        self._index.set_ef(ef)
        for start in range(0, len(self.records), 128):
            batch = self.records[start:start + 128]
            vectors = provider.embed([record.text for record in batch])
            self._index.add_items(np.asarray(vectors, dtype=np.float32),
                                  np.asarray(range(start, start + len(batch)), dtype=np.int64))

    def query(self, text: str, character_id: str) -> list[tuple[str, float, bool]]:
        vector = self.provider.embed([text])[0]
        count = min(len(self.records), self.candidate_limit)
        if not count:
            return []
        labels, distances = self._index.knn_query(self._np.asarray([vector], dtype=self._np.float32), k=count)
        scores: dict[str, tuple[float, bool]] = {}
        for label, distance in zip(labels[0], distances[0]):
            record = self.records[int(label)]
            if record.character_id != character_id:
                continue
            score = max(-1.0, min(1.0, 1.0 - float(distance)))
            previous = scores.get(record.claim_id)
            if previous is None or score > previous[0]:
                scores[record.claim_id] = (score, record.eligible)
        return [(claim_id, score, eligible)
                for claim_id, (score, eligible) in sorted(scores.items(), key=lambda item: (-item[1][0], item[0]))]


class _AugmentedSourceSpanRetriever:
    """Merge a lossy claim lane with a richer source lane under a fixed cap."""

    def __init__(self, baseline: _BoundedAnnLane, source: _BoundedAnnLane | None, *, minimum_score: float = 0.35) -> None:
        self.baseline = baseline
        self.source = source
        self.minimum_score = minimum_score

    def retrieve(self, text: str, character_id: str, *, expected: str | None, distractors: Iterable[str]) -> ProbeMeasurement:
        started = time.perf_counter()
        hits = self.baseline.query(text, character_id)
        if self.source is not None:
            hits.extend(self.source.query(text, character_id))
        # Claim-level deduplication keeps a source span from multiplying the
        # final prompt candidate count.  Ineligible candidates are observable
        # in the lane but excluded before final selection.
        merged: dict[str, tuple[float, bool]] = {}
        for claim_id, score, eligible in hits:
            old = merged.get(claim_id)
            if old is None or score > old[0]:
                merged[claim_id] = (score, eligible)
        ranked = sorted(merged.items(), key=lambda item: (-item[1][0], item[0]))
        eligible = [
            claim_id for claim_id, (score, is_eligible) in ranked
            if is_eligible and score >= self.minimum_score
        ]
        selected = tuple(eligible[:5])
        expected_rank = eligible.index(expected) + 1 if expected in eligible else None
        all_ranks = {claim_id: rank for rank, claim_id in enumerate(eligible, 1)}
        ineligible_selected = tuple(claim_id for claim_id in selected if not merged[claim_id][1])
        return ProbeMeasurement(
            expected_rank=expected_rank,
            top1=expected_rank == 1,
            top3=expected_rank is not None and expected_rank <= 3,
            top5=expected_rank is not None and expected_rank <= 5,
            selected_claim_ids=selected,
            important_distractor_ranks={claim_id: all_ranks.get(claim_id) for claim_id in distractors},
            abstained=not selected,
            warm_query_ms=(time.perf_counter() - started) * 1_000.0,
            ineligible_selected=ineligible_selected,
        )


class _TemporalSessionIndex:
    """Indexed, bounded historical-session lookup for this audit only.

    The deliberately small parser recognizes only the general relative phrase
    ``years ago``.  Lookup is a bisected timestamp range and a fixed-size
    recency slice: query work is O(log N + K), not an archive scan.
    """

    def __init__(self, metadata: Sequence[_TemporalMetadata]) -> None:
        by_character: dict[str, list[_TemporalMetadata]] = {}
        for item in metadata:
            if item.eligible:
                by_character.setdefault(item.character_id, []).append(item)
        self._by_character = {
            character_id: tuple(sorted(items, key=lambda item: (item.timestamp_us, item.session_order, item.claim_id)))
            for character_id, items in by_character.items()
        }
        self._timestamps = {
            character_id: tuple(item.timestamp_us for item in items)
            for character_id, items in self._by_character.items()
        }

    @staticmethod
    def _has_years_ago(text: str) -> bool:
        return bool(re.search(r"\byears?\s+ago\b", text, flags=re.IGNORECASE))

    def historical_candidates(self, text: str, character_id: str, *, limit: int) -> tuple[tuple[_TemporalMetadata, ...], int]:
        if not self._has_years_ago(text):
            return (), 0
        records = self._by_character.get(character_id, ())
        timestamps = self._timestamps.get(character_id, ())
        start = bisect_left(timestamps, _HISTORICAL_START_US)
        end = bisect_right(timestamps, _HISTORICAL_END_US)
        # A generic recency preference chooses a bounded tail of the matching
        # relative-age bucket.  It never examines each matching archive row.
        matching = records[start:end]
        return tuple(reversed(matching[-limit:])), len(matching)


def run_source_span_experiment(*, scale: int, provider=None, candidate_limit: int = 48, ef: int = 512) -> SourceSpanExperimentReport:
    """Compare lossy episode narratives to independently searchable source spans.

    ``scale`` controls unrelated archive history.  Each filler gets exactly one
    canonical source span.  Only the microphone session gets several bounded
    source spans, because that is the hypothesis under test: source material
    contains details which are absent from its claim narrative.
    """
    if scale < 1 or candidate_limit < 5 or ef < candidate_limit:
        raise ValueError("scale, candidate_limit, and ef are invalid")
    if provider is None:
        from aifren.memory_v2_store import MiniLMEmbeddingProvider
        provider = MiniLMEmbeddingProvider()

    a = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-source-span-a"))
    b = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-source-span-b"))
    baseline_records, source_records = _build_records(scale, a, b)
    rss = {"before_build": _rss_mib()}
    started = time.perf_counter()
    baseline_lane = _BoundedAnnLane(provider, baseline_records, candidate_limit=candidate_limit, ef=ef)
    baseline_seconds = time.perf_counter() - started
    rss["after_baseline_build"] = _rss_mib()
    started = time.perf_counter()
    source_lane = _BoundedAnnLane(provider, source_records, candidate_limit=candidate_limit, ef=ef)
    source_seconds = time.perf_counter() - started
    rss["after_source_build"] = _rss_mib()

    baseline = _AugmentedSourceSpanRetriever(baseline_lane, None)
    augmented = _AugmentedSourceSpanRetriever(baseline_lane, source_lane)
    cases = _cases(a, b)
    baseline_results = {
        name: baseline.retrieve(query, character, expected=expected, distractors=important)
        for name, character, query, expected, forbidden, important in cases
    }
    source_results = {
        name: augmented.retrieve(query, character, expected=expected, distractors=important)
        for name, character, query, expected, forbidden, important in cases
    }
    rss["after_warm_queries"] = _rss_mib()

    source_microphone_ok = all(source_results[name].top5 for name, *_ in FROZEN_MICROPHONE_PROBES[1:])
    factual_names = ("fact", "paraphrase", "correction", "isolation")
    factual_regressions = sum(
        baseline_results[name].top5 and not source_results[name].top5 for name in factual_names
    )
    hard_failures = sum(
        bool(result.ineligible_selected)
        or any(claim_id in forbidden for claim_id in result.selected_claim_ids)
        for (_, _, _, _, forbidden, _), result in zip(cases, source_results.values())
    )
    negative_cases = ("abstention_passport", "abstention_telescope")
    baseline_abstention_false_positives = sum(not baseline_results[name].abstained for name in negative_cases)
    source_abstention_false_positives = sum(not source_results[name].abstained for name in negative_cases)
    required_recall = ("direct_microphone", "fact", "paraphrase", "correction", "isolation")
    passed = (
        source_microphone_ok
        and all(source_results[name].top5 for name in required_recall)
        and factual_regressions == 0
        and hard_failures == 0
        and source_abstention_false_positives <= baseline_abstention_false_positives
    )
    return SourceSpanExperimentReport(
        scale=scale,
        candidate_limit=candidate_limit,
        candidate_bound=candidate_limit * 2,
        baseline=baseline_results,
        source_span=source_results,
        build_seconds={"baseline_narrative": baseline_seconds, "source_spans": source_seconds},
        rss_mib=rss,
        source_span_count=len(source_records),
        hard_invariant_failures=hard_failures,
        baseline_abstention_false_positives=baseline_abstention_false_positives,
        source_span_abstention_false_positives=source_abstention_false_positives,
        factual_regressions=factual_regressions,
        passed=passed,
    )


def run_temporal_signal_audit(*, scale: int, provider, candidate_limits: Sequence[int] = (16, 32, 48), ef: int = 512) -> TemporalSignalAuditReport:
    """Measure a bounded deterministic temporal candidate lane at fixed scale.

    This deliberately uses the same *lossy* baseline claim records as the
    frozen MPNet comparison.  It does not add source spans, modify embeddings,
    or apply semantic scoring until after metadata has supplied at most K
    eligible records for the queried character.
    """
    if scale < 1 or not candidate_limits or any(limit < 1 or limit > 48 for limit in candidate_limits):
        raise ValueError("scale and candidate_limits are invalid")
    a = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-source-span-a"))
    b = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-source-span-b"))
    baseline_records, _ = _build_records(scale, a, b)
    record_by_span = {record.span_id: record for record in baseline_records}

    started = time.perf_counter()
    temporal_index = _TemporalSessionIndex(_build_temporal_metadata(baseline_records))
    index_build_ms = (time.perf_counter() - started) * 1_000.0

    # These are frozen semantic controls for direct/vague/factual behavior.
    # The temporal lane is only considered for a query with a relative-time
    # cue, so it cannot perturb the other routes.
    semantic_lane = _BoundedAnnLane(provider, baseline_records, candidate_limit=48, ef=ef)
    semantic = _AugmentedSourceSpanRetriever(semantic_lane, None)
    cases = _cases(a, b)
    semantic_baseline = {
        name: semantic.retrieve(query, character, expected=expected, distractors=important)
        for name, character, query, expected, forbidden, important in cases
    }
    temporal_name, temporal_character, temporal_query, temporal_expected, temporal_forbidden, temporal_important = cases[2]
    assert temporal_name == "temporal_microphone"
    measurements: dict[int, TemporalCandidateMeasurement] = {}
    for limit in sorted(set(candidate_limits)):
        total_started = time.perf_counter()
        lookup_started = time.perf_counter()
        metadata_candidates, matching_count = temporal_index.historical_candidates(
            temporal_query, temporal_character, limit=limit
        )
        lookup_ms = (time.perf_counter() - lookup_started) * 1_000.0
        score_started = time.perf_counter()
        final = _score_bounded_temporal_candidates(
            provider,
            temporal_query,
            metadata_candidates,
            record_by_span,
            expected=temporal_expected,
            distractors=temporal_important,
        )
        score_ms = (time.perf_counter() - score_started) * 1_000.0
        candidate_claim_ids = tuple(item.claim_id for item in metadata_candidates)
        expected_candidate_rank = (
            candidate_claim_ids.index(temporal_expected) + 1
            if temporal_expected in candidate_claim_ids else None
        )
        measurements[limit] = TemporalCandidateMeasurement(
            candidate_limit=limit,
            matching_historical_records=matching_count,
            candidate_count=len(metadata_candidates),
            expected_candidate_rank=expected_candidate_rank,
            candidate_claim_ids=candidate_claim_ids,
            final=final,
            metadata_lookup_ms=lookup_ms,
            mpnet_scoring_ms=score_ms,
            total_temporal_path_ms=(time.perf_counter() - total_started) * 1_000.0,
        )

    hard_failures = sum(
        bool(result.ineligible_selected)
        or any(claim_id in forbidden for claim_id in result.selected_claim_ids)
        for (_, _, _, _, forbidden, _), result in zip(cases, semantic_baseline.values())
    )
    hard_failures += sum(
        bool(measurement.final.ineligible_selected)
        or any(claim_id in temporal_forbidden for claim_id in measurement.final.selected_claim_ids)
        or any(claim_id not in measurement.candidate_claim_ids for claim_id in measurement.final.selected_claim_ids)
        for measurement in measurements.values()
    )
    factual_names = ("direct_microphone", "vague_microphone", "fact", "paraphrase", "correction", "isolation")
    factual_regressions = sum(not semantic_baseline[name].top5 for name in factual_names)
    passed = (
        any(measurement.final.top5 for measurement in measurements.values())
        and hard_failures == 0
        and factual_regressions == 0
    )
    return TemporalSignalAuditReport(
        scale=scale,
        temporal_candidates=measurements,
        semantic_baseline=semantic_baseline,
        index_build_ms=index_build_ms,
        hard_invariant_failures=hard_failures,
        factual_regressions=factual_regressions,
        passed=passed,
    )


def _score_bounded_temporal_candidates(
    provider,
    query: str,
    metadata: Sequence[_TemporalMetadata],
    record_by_span: dict[str, _Record],
    *,
    expected: str | None,
    distractors: Iterable[str],
    minimum_score: float = 0.35,
) -> ProbeMeasurement:
    """Apply unchanged cosine scoring only to an already-bounded metadata set."""
    started = time.perf_counter()
    records = [record_by_span[item.span_id] for item in metadata]
    vectors = provider.embed([query, *(record.text for record in records)])
    query_vector = vectors[0]
    merged: dict[str, tuple[float, bool]] = {}
    for record, vector in zip(records, vectors[1:]):
        score = sum(left * right for left, right in zip(query_vector, vector))
        previous = merged.get(record.claim_id)
        if previous is None or score > previous[0]:
            merged[record.claim_id] = (score, record.eligible)
    ranked = sorted(merged.items(), key=lambda item: (-item[1][0], item[0]))
    eligible = [
        claim_id for claim_id, (score, is_eligible) in ranked
        if is_eligible and score >= minimum_score
    ]
    selected = tuple(eligible[:5])
    expected_rank = eligible.index(expected) + 1 if expected in eligible else None
    all_ranks = {claim_id: rank for rank, claim_id in enumerate(eligible, 1)}
    return ProbeMeasurement(
        expected_rank=expected_rank,
        top1=expected_rank == 1,
        top3=expected_rank is not None and expected_rank <= 3,
        top5=expected_rank is not None and expected_rank <= 5,
        selected_claim_ids=selected,
        important_distractor_ranks={claim_id: all_ranks.get(claim_id) for claim_id in distractors},
        abstained=not selected,
        warm_query_ms=(time.perf_counter() - started) * 1_000.0,
        ineligible_selected=tuple(claim_id for claim_id in selected if not merged[claim_id][1]),
    )


def _build_temporal_metadata(records: Sequence[_Record]) -> list[_TemporalMetadata]:
    """Deterministically assign canonical-style event/session timestamps.

    Existing frozen text and source spans remain untouched.  Filler sessions
    retain their already-generated years; the microphone narrative receives
    its existing March 2028 event date.  The audit stores this separately so
    it cannot be mistaken for production schema work.
    """
    metadata = []
    for order, record in enumerate(records):
        if record.span_id == "needle-audio-narrative":
            timestamp = datetime(2028, 3, 15, tzinfo=timezone.utc)
        elif record.span_id == "near-audio":
            timestamp = datetime(2029, 11, 1, tzinfo=timezone.utc)
        else:
            match = re.fullmatch(r"filler-(\d+)", record.span_id)
            if match:
                index = int(match.group(1))
                timestamp = datetime(
                    2020 + index % 13,
                    1 + (index // 13) % 12,
                    1 + (index // 156) % 28,
                    tzinfo=timezone.utc,
                )
            else:
                timestamp = datetime(2034, 1, 1, tzinfo=timezone.utc)
        metadata.append(_TemporalMetadata(
            span_id=record.span_id,
            claim_id=record.claim_id,
            character_id=record.character_id,
            eligible=record.eligible,
            timestamp_us=int(timestamp.timestamp() * 1_000_000),
            session_order=order,
        ))
    return metadata


def _build_records(scale: int, a: str, b: str) -> tuple[list[_Record], list[_Record]]:
    baseline = [
        _Record("fact-coffee-current", "fact-coffee-current", a, "The user prefers dark roast coffee."),
        _Record("fact-coffee-old", "fact-coffee-old", a, "The user previously preferred light roast coffee.", False),
        _Record("fact-location-old", "fact-location-old", a, "The user lived in Harbor City.", False),
        _Record("fact-location-current", "fact-location-current", a, "The user now lives in Cedar Ridge."),
        _Record("archived-camera", "archived-camera", a, "The user once owned an obsolete camera.", False),
        # This is intentionally lossy relative to the canonical spans below.
        _Record("needle-audio-narrative", "needle-audio", a, "In March 2028 we fixed microphone crackling caused by a damaged USB extension."),
        _Record("near-audio", "near-audio", a, "We later replaced a USB microphone after a different audio repair."),
        _Record("b-coffee", "b-coffee", b, "The user prefers light roast coffee."),
    ]
    source = list(baseline)
    source.extend((
        _Record("needle-source-1", "needle-audio", a,
                "Years ago in March 2028, during an audio repair session with the user, their microphone made horrible crackling and popping sounds on the Linux desktop."),
        _Record("needle-source-2", "needle-audio", a,
                "During that shared audio repair session, we unplugged the damaged USB extension cable between the microphone and docking station and connected the microphone directly."),
        _Record("needle-source-3", "needle-audio", a,
                "The damaged USB extension was the cause; after replacing it, the user and AIFren made a clear recording and confirmed the microphone problem was solved."),
    ))
    # Replace the lossy source copy with only canonical session spans.  The
    # narrative remains available solely through the baseline lane.
    source = [record for record in source if record.span_id != "needle-audio-narrative"]
    topics = ("microphone", "linux", "usb", "audio", "hardware", "coffee", "garden")
    for index in range(scale):
        topic = topics[index % len(topics)]
        claim_id = f"filler-{index}"
        text = f"In {2020 + index % 13} the user and AIFren discussed {topic} repair detail {index} in a routine session note."
        record = _Record(claim_id, claim_id, a, text)
        baseline.append(record)
        source.append(record)
    return baseline, source


def _cases(a: str, b: str):
    return (
        *((name, a, query, expected, (), important) for name, query, expected, important in FROZEN_MICROPHONE_PROBES),
        ("fact", a, "What kind of coffee do I like?", "fact-coffee-current", ("fact-coffee-old",), ("fact-coffee-old",)),
        ("paraphrase", a, "Would I probably choose a light roast?", "fact-coffee-current", ("fact-coffee-old", "b-coffee"), ("fact-coffee-old", "b-coffee")),
        ("correction", a, "Where do I live now?", "fact-location-current", ("fact-location-old",), ("fact-location-old",)),
        ("archive", a, "What obsolete camera did I own?", None, ("archived-camera",), ("archived-camera",)),
        ("isolation", b, "What coffee roast do I prefer?", "b-coffee", ("fact-coffee-current",), ("fact-coffee-current",)),
        ("abstention_passport", a, "What is my passport number?", None, (), ()),
        ("abstention_telescope", a, "What telescope did I buy?", None, (), ()),
    )


def _rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
