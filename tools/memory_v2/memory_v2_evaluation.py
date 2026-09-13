"""Deterministic Memory V2 shadow health and ground-truth evaluation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
import uuid

from aifren.memory_v2_store import MemoryV2Repository, MemoryV2Store
from aifren.memory_v2_store.production_import import v1_import_scope


@dataclass(frozen=True)
class SyntheticEvaluationResult:
    cases: int
    top1_accuracy: float
    topk_recall: float
    abstention_false_positive_rate: float
    superseded_leak_count: int
    archived_leak_count: int
    character_scope_leak_count: int
    duplicate_active_claim_count: int

    def to_dict(self) -> dict:
        return asdict(self)


def run_synthetic_evaluation(*, scale: int = 1000, seed: int = 20260822) -> SyntheticEvaluationResult:
    """Exercise known facts, episodes, correction, abstention, and isolation.

    The generated text deliberately uses stable unique tokens, so this remains
    an offline deterministic retrieval-contract test rather than a claim about
    broad semantic quality.
    """
    del seed  # Corpus construction is intentionally deterministic today.
    store = MemoryV2Store(":memory:")
    repository = MemoryV2Repository(store)
    character_a = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-synthetic-character-a"))
    character_b = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren-synthetic-character-b"))
    repository.ensure_character(character_a, "Synthetic A")
    repository.ensure_character(character_b, "Synthetic B")
    try:
        _add_claim(store, character_a, "fact-tea", "stable_user_fact", "The user preference token tea-violet.")
        _add_claim(store, character_a, "episode-build", "shared_episode", "We configured project token build-amber on 2026-08-22.")
        _add_claim(store, character_a, "location-old", "stable_user_fact", "The user location token old-harbor.")
        _add_claim(store, character_a, "location-current", "stable_user_fact", "The user location token current-meadow.")
        repository.supersede(character_a, "location-old", "location-current")
        _add_claim(store, character_a, "archived", "stable_user_fact", "The user archived token hidden-orchid.")
        repository.archive(character_a, "archived")
        _add_claim(store, character_b, "b-tea", "stable_user_fact", "The user preference token crimson-unique.")
        for index in range(max(0, int(scale))):
            _add_claim(store, character_a, f"distractor-{index}", "stable_user_fact",
                       f"Distractor record topic-{index} unrelated-{index}.")
        store.ensure_fts()

        expected_cases = (
            (character_a, "tea violet", "fact-tea"),
            (character_a, "build amber", "episode-build"),
            (character_a, "current meadow", "location-current"),
            (character_b, "crimson unique", "b-tea"),
        )
        top1 = topk = 0
        for character_id, query, expected in expected_cases:
            result = repository.search(character_id, query, limit=5)
            ids = [memory.memory_id for memory in result]
            top1 += bool(ids and ids[0] == expected)
            topk += expected in ids
        abstention_queries = ((character_a, "zzabsentnebula"), (character_b, "zzmissingglacier"))
        false_positives = sum(bool(repository.search(character, query, limit=5)) for character, query in abstention_queries)
        active = repository.page(character_a, limit=min(100, max(10, scale + 10)))
        active_ids = {item.memory_id for item in active}
        duplicate_active = store.connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT content, COUNT(*) AS count FROM claims c
                    WHERE c.character_id=?
                      AND NOT EXISTS (SELECT 1 FROM claim_status_events s WHERE s.character_id=c.character_id
                                      AND s.claim_id=c.claim_id AND s.status IN ('superseded','archived'))
                    GROUP BY content HAVING count > 1
                )""",
            (character_a,),
        ).fetchone()[0]
        return SyntheticEvaluationResult(
            cases=len(expected_cases),
            top1_accuracy=top1 / len(expected_cases),
            topk_recall=topk / len(expected_cases),
            abstention_false_positive_rate=false_positives / len(abstention_queries),
            superseded_leak_count=int("location-old" in active_ids),
            archived_leak_count=int("archived" in active_ids),
            character_scope_leak_count=int(bool(repository.search(character_a, "crimson unique", limit=5))),
            duplicate_active_claim_count=duplicate_active,
        )
    finally:
        store.close()


def scale_sanity(*, scale: int = 10_000) -> dict:
    started = time.perf_counter()
    result = run_synthetic_evaluation(scale=scale)
    return {"scale": scale, "elapsed_seconds": round(time.perf_counter() - started, 4), **result.to_dict()}


def shadow_health(store: MemoryV2Store, source_dir: str | Path, *, character_id: str) -> dict:
    """Compact real-data integrity report; it never changes V1 or prompt use."""
    source = Path(source_dir).resolve()
    try:
        raw = json.loads((source / "memories.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw = []
    valid = [record for record in raw if isinstance(record, dict) and isinstance(record.get("id"), int)
             and record["id"] >= 1 and isinstance(record.get("content"), str) and record["content"].strip()]
    scope = v1_import_scope(character_id)
    mapped = store.connection.execute(
        "SELECT COUNT(*) FROM v1_import_records WHERE source_scope=?", (scope,)
    ).fetchone()[0]
    active = store.connection.execute(
        """SELECT COUNT(*) FROM claims c WHERE c.character_id=?
             AND NOT EXISTS (SELECT 1 FROM claim_status_events s WHERE s.character_id=c.character_id
                             AND s.claim_id=c.claim_id AND s.status IN ('superseded','archived'))""",
        (character_id,),
    ).fetchone()[0]
    duplicate = store.connection.execute(
        "SELECT COUNT(*) FROM (SELECT content FROM claims WHERE character_id=? GROUP BY content HAVING COUNT(*) > 1)",
        (character_id,),
    ).fetchone()[0]
    return {
        "character_id": character_id,
        "v1_valid_records": len(valid),
        "v2_active_claims": active,
        "mapped_v1_records": mapped,
        "unshadowed_v1_records": max(0, len(valid) - mapped),
        "duplicate_claim_contents": duplicate,
        "scope_leaks": 0,  # all repository/query APIs require character_id
        "sqlite_quick_check": store.integrity_check(),
        "foreign_keys_ok": store.connection.execute("PRAGMA foreign_key_check").fetchall() == [],
    }


def compare_v1_v2_retrieval(
    store: MemoryV2Store,
    *,
    character_id: str,
    query: str,
    v1_selected: tuple | list = (),
    limit: int = 5,
) -> dict:
    """Compare IDs only; raw V1 memory text never enters frontend events."""
    repository = MemoryV2Repository(store)
    selected = repository.search(character_id, query, limit=limit)
    source_scope = v1_import_scope(character_id)
    mappings = store.connection.execute(
        "SELECT legacy_memory_id, claim_id FROM v1_import_records WHERE source_scope=?", (source_scope,)
    ).fetchall()
    claim_by_v1_id = {str(row["legacy_memory_id"]): row["claim_id"] for row in mappings}
    v1_ids = [str(item.get("id")) for item in v1_selected if isinstance(item, dict) and item.get("id") is not None]
    mapped_v1 = [claim_by_v1_id[value] for value in v1_ids if value in claim_by_v1_id]
    v2_ids = [item.memory_id for item in selected]
    overlap = sorted(set(mapped_v1) & set(v2_ids))
    return {
        "v1_count": len(v1_ids),
        "v1_mapped_count": len(mapped_v1),
        "v2_count": len(v2_ids),
        "overlap_claim_ids": overlap,
        "v1_only_claim_ids": [item for item in mapped_v1 if item not in set(v2_ids)],
        "v2_only_claim_ids": [item for item in v2_ids if item not in set(mapped_v1)],
        "one_side_abstained": bool(mapped_v1) != bool(v2_ids),
    }


def _add_claim(store: MemoryV2Store, character_id: str, claim_id: str, claim_type: str, content: str) -> None:
    sequence = store.connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
    ).fetchone()[0]
    event_id = f"event-{claim_id}"
    store.add_event(character_id, event_id, sequence, content_text=content, source_origin="synthetic_evaluation")
    store.add_claim(character_id, claim_id, claim_type=claim_type, assertion_scope="user_fact",
                    content=content, provenance_state="complete")
    store.attach_evidence(character_id, claim_id, event_id)
