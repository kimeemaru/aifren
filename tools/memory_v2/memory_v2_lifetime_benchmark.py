"""Disk-backed, streaming lifetime-recall benchmark for shadow Memory V2.

It deliberately persists only SQLite/derived-index state.  Generated batches,
embedding vectors, and probe results are released immediately; compact metrics
and deterministic IDs are the only Python-side retained corpus metadata.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import resource
import hashlib
import json
import sqlite3
import struct
import tempfile
import time
import uuid

from aifren.memory_v2_store.models import RetrievalQuery
from aifren.continuity.memory_v2_adaptive import AdaptiveShadowRetrieval
from aifren.memory_v2_store import EmbeddingLifecycle, HnswClaimIndex, MemoryV2Repository, MemoryV2Store, MiniLMEmbeddingProvider, SemanticRetrievalV2


@dataclass(frozen=True)
class LifetimeMetric:
    cases: int
    top1: float
    top3: float
    top5: float
    abstention_false_positive_rate: float
    forbidden_leaks: int


@dataclass(frozen=True)
class LifetimeReport:
    scale: int
    seed: int
    metrics: dict[str, LifetimeMetric]
    phase_rss_mib: dict[str, float]
    phase_seconds: dict[str, float]
    database_bytes: int
    index_bytes: int
    character_scope_leaks: int
    strategies: dict[str, int]
    max_live_batch_items: int

    def to_dict(self):
        return {**asdict(self), "metrics": {key: asdict(value) for key, value in self.metrics.items()}}


def _rss_mib() -> float:
    # Linux ru_maxrss is KiB; it intentionally records process peak so each
    # phase reports whether a later stage increased the live high-water mark.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_lifetime_benchmark(*, scale: int, seed: int = 17, batch_size: int = 128, root: str | Path | None = None) -> LifetimeReport:
    """Run full hybrid retrieval from a streaming, disk-backed corpus."""
    temporary = None
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="aifren-v2-lifetime-")
        root = temporary.name
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    database = root / "lifetime.sqlite3"
    store = MemoryV2Store(str(database))
    repository = MemoryV2Repository(store)
    a = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aifren-lifetime-a-{seed}"))
    b = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aifren-lifetime-b-{seed}"))
    repository.ensure_character(a, "Lifetime A")
    repository.ensure_character(b, "Lifetime B")
    provider = MiniLMEmbeddingProvider()
    sequence = {a: 0, b: 0}
    phase_rss, phase_seconds = {}, {}
    try:
        started = time.perf_counter()
        core = (
            (a, "fact-coffee-current", "preference", "The user prefers dark roast coffee."),
            (a, "fact-coffee-old", "preference", "The user previously preferred light roast coffee."),
            (a, "fact-location-a", "location", "The user lived in Harbor City."),
            (a, "fact-location-b", "location", "The user later lived in Meadow Town."),
            (a, "fact-location-current", "location", "The user now lives in Cedar Ridge."),
            (a, "needle-audio", "shared_episode", "In March 2028 we fixed microphone crackling caused by a damaged USB extension."),
            (a, "near-audio", "shared_episode", "We later replaced a USB microphone after a different audio repair."),
            (b, "b-coffee", "preference", "The user prefers light roast coffee."),
        )
        core_batch = []
        for character, claim_id, claim_type, content in core:
            sequence[character] += 1
            core_batch.append((character, claim_id, claim_type, content, sequence[character]))
        _bulk_insert(store, provider, core_batch)
        repository.supersede(a, "fact-coffee-old", "fact-coffee-current")
        repository.supersede(a, "fact-location-a", "fact-location-b")
        repository.supersede(a, "fact-location-b", "fact-location-current")
        # Deterministic batches are intentionally not accumulated.
        for start in range(0, int(scale), int(batch_size)):
            batch = []
            for index in range(start, min(start + int(batch_size), int(scale))):
                topic = ("microphone", "linux", "usb", "audio", "hardware", "coffee", "garden")[index % 7]
                claim_id = f"filler-{seed}-{index}"
                sequence[a] += 1
                batch.append((a, claim_id, "shared_episode" if index % 5 == 0 else "fact",
                              f"In {2020 + index % 13} we discussed {topic} repair detail {index} in a routine note.", sequence[a]))
            _bulk_insert(store, provider, batch)
        phase_seconds["generate_and_embed"] = time.perf_counter() - started
        phase_rss["generate_and_embed"] = _rss_mib()
        started = time.perf_counter()
        index = HnswClaimIndex(store, a, provider)
        index.ensure()
        phase_seconds["ann_build"] = time.perf_counter() - started
        phase_rss["ann_build"] = _rss_mib()
        store.ensure_fts()

        cases = (
            ("facts", a, "What kind of coffee do I like?", "fact-coffee-current", ("fact-coffee-old",)),
            ("paraphrases", a, "Would I probably choose a light roast?", "fact-coffee-current", ("fact-coffee-old", "b-coffee")),
            ("old_needle", a, "What caused that old microphone crackling problem?", "needle-audio", ("near-audio",)),
            ("old_needle", a, "Do you remember why my mic sounded terrible years ago?", "needle-audio", ()),
            ("episodes", a, "What USB related audio issue did we fix?", "needle-audio", ()),
            ("temporal", a, "What were we doing years ago with the microphone?", "needle-audio", ()),
            ("corrections", a, "Where do I live now?", "fact-location-current", ("fact-location-a", "fact-location-b")),
            ("character_isolation", b, "What coffee roast do I prefer?", "b-coffee", ("fact-coffee-current",)),
            ("abstention", a, "What is my passport number?", None, ()),
            ("abstention", a, "What telescope did I buy?", None, ()),
        )
        buckets = defaultdict(list)
        strategies = defaultdict(int)
        adaptive = AdaptiveShadowRetrieval(store, provider)
        started = time.perf_counter()
        # A warm direct hybrid retrieval is used for quality; the adaptive call
        # independently records policy behavior without changing the result.
        retriever = SemanticRetrievalV2(store, embedding_provider=provider)
        for category, character, text, expected, forbidden in cases:
            outcome = retriever.retrieve(RetrievalQuery(character, text, datetime.now(timezone.utc).isoformat(), "ordinary", ()))
            ids = outcome.claim_ids
            _, strategy, _ = adaptive.retrieve(RetrievalQuery(character, text, datetime.now(timezone.utc).isoformat(), "ordinary", ()))
            strategies[strategy] += 1
            buckets[category].append((expected, forbidden, ids))
        phase_seconds["warm_hybrid_queries"] = time.perf_counter() - started
        phase_rss["warm_hybrid_queries"] = _rss_mib()
        metrics = {category: _metric(items) for category, items in buckets.items()}
        leaks = sum(int(any(item.startswith("b-") for item in ids)) for expected, forbidden, ids in buckets["facts"] + buckets["old_needle"])
        index_bytes = sum(path.stat().st_size for path in index.directory.glob("*") if path.is_file()) if index.directory else 0
        return LifetimeReport(scale, seed, metrics, phase_rss, phase_seconds, database.stat().st_size, index_bytes, leaks, dict(strategies), int(batch_size))
    finally:
        store.close()
        if temporary is not None:
            temporary.cleanup()


def _metric(items):
    expected = [(wanted, forbidden, ids) for wanted, forbidden, ids in items if wanted]
    absent = [ids for wanted, _, ids in items if not wanted]
    count = len(expected)
    return LifetimeMetric(len(items),
        sum(bool(ids and ids[0] == wanted) for wanted, _, ids in expected) / count if count else 0.0,
        sum(wanted in ids[:3] for wanted, _, ids in expected) / count if count else 0.0,
        sum(wanted in ids[:5] for wanted, _, ids in expected) / count if count else 0.0,
        sum(bool(ids) for ids in absent) / len(absent) if absent else 0.0,
        sum(bool(set(forbidden) & set(ids)) for _, forbidden, ids in expected))


def _add(store, sequence, character, claim_id, claim_type, content):
    sequence[character] += 1
    event_id = f"event-{claim_id}"
    store.add_event(character, event_id, sequence[character], content_text=content, source_origin="lifetime_benchmark")
    store.add_claim(character, claim_id, claim_type=claim_type, assertion_scope="user_fact", content=content, provenance_state="complete")
    store.attach_evidence(character, claim_id, event_id)


def _bulk_insert(store, provider, records):
    """Benchmark-only bounded ingest using the normal V2 table schema.

    It has no production call sites.  Each record has the same event, claim,
    evidence, and current derived embedding state produced by the ordinary
    public APIs, but one batch shares a transaction and embedding call.
    """
    if not records:
        return
    contents = [record[3] for record in records]
    vectors = provider.embed(contents)
    if len(vectors) != len(records):
        raise ValueError("embedding provider returned a mismatched batch")
    timestamp = int(time.time() * 1_000_000)
    events = []
    claims = []
    evidence = []
    embeddings = []
    for (character, claim_id, claim_type, content, sequence), vector in zip(records, vectors):
        event_id = f"event-{claim_id}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        events.append((character, event_id, sequence, "message", "user", timestamp, None, None,
                       "unknown", content, "{}", 1, "lifetime_benchmark", None, digest))
        claims.append((character, claim_id, claim_type, "user_fact", None, content, 5, None,
                       None, None, "unknown", None, "complete", "lifetime-benchmark", "1", "1",
                       timestamp, None, timestamp))
        evidence.append((character, claim_id, event_id, "direct_user_statement", None, None,
                         None, 1.0, None, timestamp))
        blob = sqlite3.Binary(struct.pack(f"<{provider.dimensions}f", *(float(value) for value in vector)))
        embeddings.append((character, claim_id, provider.provider, provider.model, provider.model_version,
                           provider.dimensions, provider.dtype, int(bool(provider.normalized)),
                           provider.preprocessing_fingerprint, digest, digest, blob, timestamp))
    with store.transaction():
        store.connection.executemany(
            """INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)""", events)
        store.connection.executemany(
            """INSERT INTO claims(character_id, claim_id, claim_type, assertion_scope, subject_key, content,
               importance, confidence, valid_from_us, valid_to_us, temporal_precision, temporal_expression,
               provenance_state, curator_name, curator_version, curator_policy_version, created_at_us,
               legacy_metadata_json, updated_at_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", claims)
        store.connection.executemany(
            "INSERT INTO claim_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", evidence)
        store.connection.executemany(
            """INSERT INTO claim_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', NULL)""", embeddings)
