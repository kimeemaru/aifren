"""Development-only real-turn V1/V2 recall observation.

The observer runs after production response construction on one background
worker.  It reads the character-scoped V2 store and emits structural records
to the central Development flight recorder; it has no prompt/context output.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from benchmarks.memory_v2.models import RetrievalHealth, RetrievalQuery
from memory_query_decision import MemoryQueryDecision, decide_memory_query
from memory_v2_episode_compaction import EpisodeCompactionCache
from memory_v2_hybrid_recall import (
    HistoricalRecallAnchor,
    HybridMemoryV2Recall,
    historical_recall_anchor_from_candidates,
)
from memory_v2_store import MemoryV2Store, MiniLMEmbeddingProvider, SemanticRetrievalV2
from memory_v2_store.production_import import v1_import_scope


MAX_V1_CANDIDATES = 5
MAX_V2_CANDIDATES = 5
MAX_SIGNALS = 12
MAX_EVIDENCE = 4
RECALL_ANCHOR_TTL_SECONDS = 300.0


class _SnapshotCancelled(RuntimeError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _failure_diagnostics(error: BaseException, stage: str) -> dict[str, object]:
    """Return only bounded structural failure data safe for a flight trace."""
    sqlite_error = isinstance(error, sqlite3.Error)
    return {
        "error_kind": type(error).__name__,
        "error_stage": str(stage),
        "sqlite_error_name": str(getattr(error, "sqlite_errorname", "")) if sqlite_error else "",
        "sqlite_error_code": getattr(error, "sqlite_errorcode", None) if sqlite_error else None,
        "error_disposition": "cancelled" if isinstance(error, _SnapshotCancelled) else "fail_open",
        "retry_disposition": "not_retried",
    }


class _FixedMessageView(Sequence[Mapping[str, object]]):
    """Pin a canonical append-only sequence length without copying history."""

    def __init__(self, source: Sequence[Mapping[str, object]]) -> None:
        self.source = source
        self.length = len(source)

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self.length)
            return [self.source[value] for value in range(start, stop, step)]
        normalized = index + self.length if index < 0 else index
        if not 0 <= normalized < self.length:
            raise IndexError(index)
        return self.source[normalized]


def _query(character_id: str, text: str, messages: Sequence[Mapping[str, object]]) -> RetrievalQuery:
    recent = tuple(
        str(item.get("content", ""))
        for item in messages[-16:]
        if isinstance(item, Mapping)
        and item.get("role") == "user"
        and str(item.get("content", "")).strip()
    )[-8:]
    if recent and recent[-1] == str(text):
        recent = recent[:-1]
    return RetrievalQuery(
        character_id,
        str(text),
        datetime.now(timezone.utc).isoformat(),
        "ordinary",
        recent,
    )


class RealTurnMemoryShadow:
    """Serialize bounded hybrid retrieval away from the live turn path."""

    def __init__(
        self,
        database_path: str | Path,
        character_id: str,
        *,
        recorder: Any,
        provider_factory: Callable[[], object] = MiniLMEmbeddingProvider,
        recall_factory: Callable[..., object] | None = None,
        prompt_design_observer: Callable[[int, str, object, str], None] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.character_id = str(character_id)
        self.recorder = recorder
        self.provider_factory = provider_factory
        self.recall_factory = recall_factory
        self.prompt_design_observer = prompt_design_observer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-v2-recall-shadow")
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False
        self._provider: object | None = None
        self._recall_anchor: HistoricalRecallAnchor | None = None
        self._recall_anchor_expires_at = 0.0

    def submit(
        self,
        *,
        turn_id: int,
        generation: int,
        canonical_user_index: int,
        query_text: str,
        messages: Sequence[Mapping[str, object]],
        v1_selected: Sequence[Mapping[str, object]],
        v1_latency_ms: float | None,
        accept: Callable[[dict[str, Any]], bool],
        memory_query_decision: MemoryQueryDecision | None = None,
    ) -> bool:
        """Queue at most one observation; disabled diagnostics are a no-op."""
        if not bool(getattr(self.recorder, "enabled", False)):
            return False
        with self._lock:
            if self._closed:
                return False
            if self._pending:
                self.recorder.mark(
                    "memory_recall_shadow_skipped", turn_id=int(turn_id), reason="worker_busy",
                )
                return False
            self._pending = True
        snapshot = _FixedMessageView(messages)
        v1 = tuple(dict(item) for item in tuple(v1_selected)[:MAX_V1_CANDIDATES])
        snapshot_ready = threading.Event()
        cancelled = threading.Event()
        decision = memory_query_decision or decide_memory_query(query_text)
        try:
            self._executor.submit(
                self._run,
                int(turn_id), int(generation), int(canonical_user_index), str(query_text),
                snapshot, v1, v1_latency_ms, accept, snapshot_ready, cancelled,
                decision,
            )
            # Pin the SQLite read view before same-turn V2 observers append
            # evidence. Provider/model work happens only after this handshake.
            if snapshot_ready.wait(0.1):
                return True
            cancelled.set()
            self.recorder.mark(
                "memory_recall_shadow_skipped", turn_id=int(turn_id), reason="snapshot_timeout",
            )
            return False
        except Exception:
            with self._lock:
                self._pending = False
            return False

    def _run(self, turn_id, generation, canonical_user_index, query_text, messages,
             v1_selected, v1_latency_ms, accept, snapshot_ready, cancelled,
             memory_query_decision) -> None:
        started = time.perf_counter()
        record: dict[str, Any]
        result = None
        active_scope_id = ""
        next_anchor: HistoricalRecallAnchor | None = None
        stage = "snapshot_open"
        try:
            store = MemoryV2Store(str(self.database_path))
            operation_failed = False
            try:
                if cancelled.is_set():
                    raise _SnapshotCancelled()
                stage = "snapshot_pin"
                store.connection.execute("BEGIN")
                # A read is required to establish the WAL snapshot now rather
                # than on the first later retrieval statement.
                store.connection.execute(
                    "SELECT COUNT(*) FROM claims WHERE character_id=?", (self.character_id,),
                ).fetchone()
                snapshot_ready.set()
                if cancelled.is_set():
                    raise _SnapshotCancelled()
                # Resolve scope through the same pinned view before any
                # retrieval helper is constructed. This remains a read for a
                # healthy initialized character and gives future failures an
                # owned, content-free stage boundary.
                stage = "scope_query"
                active_scope_id = store.active_truth_scope_id(self.character_id)
                recall_anchor = self._anchor_for(generation, active_scope_id)
                stage = "retriever_open"
                if self.recall_factory is None:
                    if self._provider is None:
                        self._provider = self.provider_factory()
                    semantic = SemanticRetrievalV2(
                        store, embedding_provider=self._provider,
                        include_historical_evidence=True,
                    )
                    recall = HybridMemoryV2Recall(
                        store,
                        self.character_id,
                        semantic_retriever=semantic,
                        episode_cache=EpisodeCompactionCache(store, self.character_id),
                        canonical_messages=messages,
                        include_historical_episodes=True,
                    )
                else:
                    recall = self.recall_factory(store, self.character_id, messages)
                stage = "claim_query"
                retrieval_query = _query(self.character_id, query_text, messages)
                if self.recall_factory is None:
                    result = recall.retrieve(
                        retrieval_query, recall_anchor=recall_anchor,
                        memory_query_decision=memory_query_decision,
                    )
                else:
                    result = recall.retrieve(retrieval_query)
                health = getattr(result, "health", RetrievalHealth())
                next_anchor = self._anchor_from_result(
                    result, self.character_id, active_scope_id, generation,
                )
                stage = "v1_map"
                mapped_v1 = self._map_v1_ids(store, v1_selected)
                v2_ids = {str(item.memory_id) for item in tuple(result.candidates)[:MAX_V2_CANDIDATES]}
                record = {
                    "version": 1,
                    "turn_id": turn_id,
                    "generation": generation,
                    "canonical_user_index": canonical_user_index,
                    "character_key": _digest(self.character_id),
                    "query_sha256": _digest(query_text),
                    "v1_latency_ms": v1_latency_ms,
                    "v1": [
                        {
                            "memory_id": str(item.get("id")),
                            "category": str(item.get("category", "unknown"))[:64],
                            "rank": int(item.get("rank", rank)),
                        }
                        for rank, item in enumerate(v1_selected, 1)
                        if item.get("id") is not None
                    ],
                    "v2": [self._candidate(item, rank) for rank, item in enumerate(
                        tuple(result.candidates)[:MAX_V2_CANDIDATES], 1
                    )],
                    "v2_abstention_reason": "shadow_failure" if health.incomplete else str(result.abstention_reason or "")[:80],
                    "generated_counts": [
                        [str(key)[:64], int(value)]
                        for key, value in tuple(result.generated_counts)[:12]
                    ],
                    "overlap_claim_ids": sorted(set(mapped_v1) & v2_ids)[:MAX_V2_CANDIDATES],
                    "error_kind": "RetrievalIncomplete" if health.incomplete else "",
                    "error_stage": health.diagnostics()["retrieval_error_stage"],
                    "sqlite_error_name": "",
                    "sqlite_error_code": None,
                    "error_disposition": "fail_open" if health.incomplete else "none",
                    "retry_disposition": "not_needed",
                    "memory_query_intent": memory_query_decision.intent,
                    "requested_relation": memory_query_decision.requested_relation,
                    "requested_speaker": memory_query_decision.requested_speaker,
                    **health.diagnostics(),
                }
            except Exception:
                operation_failed = True
                raise
            finally:
                if not operation_failed:
                    stage = "snapshot_close"
                if store.connection.in_transaction:
                    store.connection.rollback()
                store.close()
        except Exception as error:
            snapshot_ready.set()
            record = {
                "version": 1,
                "turn_id": turn_id,
                "generation": generation,
                "canonical_user_index": canonical_user_index,
                "character_key": _digest(self.character_id),
                "query_sha256": _digest(query_text),
                "v1_latency_ms": v1_latency_ms,
                "v1": [
                    {"memory_id": str(item.get("id")), "category": str(item.get("category", "unknown"))[:64], "rank": rank}
                    for rank, item in enumerate(v1_selected, 1) if item.get("id") is not None
                ],
                "v2": [],
                "v2_abstention_reason": "shadow_failure",
                "generated_counts": [],
                "overlap_claim_ids": [],
                **_failure_diagnostics(error, stage),
                "memory_query_intent": memory_query_decision.intent,
                "requested_relation": memory_query_decision.requested_relation,
                "requested_speaker": memory_query_decision.requested_speaker,
            }
        record["v2_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        try:
            accepted = bool(accept(record))
            if accepted:
                self.recorder.record_memory_recall_shadow(record)
                if self.prompt_design_observer is not None and result is not None and not record.get("error_kind"):
                    try:
                        self.prompt_design_observer(
                            int(turn_id), str(query_text), result, str(active_scope_id),
                        )
                    except Exception:
                        # The optional Development-only design artifact is
                        # downstream of the accepted structural shadow record.
                        # It can neither fail the observer nor affect anchors.
                        pass
                with self._lock:
                    self._recall_anchor = next_anchor
                    self._recall_anchor_expires_at = (
                        time.monotonic() + RECALL_ANCHOR_TTL_SECONDS
                        if next_anchor is not None else 0.0
                    )
        except Exception:
            pass
        finally:
            snapshot_ready.set()
            with self._lock:
                self._pending = False

    @staticmethod
    def _candidate(item: object, rank: int) -> dict[str, Any]:
        return {
            "memory_id": str(getattr(item, "memory_id", "")),
            "lane": str(getattr(item, "lane", "unknown"))[:64],
            "rank": rank,
            "score": float(getattr(item, "score", 0.0)),
            "signals": [
                [str(key)[:64], float(value)]
                for key, value in tuple(getattr(item, "signals", ()))[:MAX_SIGNALS]
            ],
            "truth_scope_key": _digest(getattr(item, "truth_scope_id", "")),
            "status": str(getattr(item, "status", "unknown"))[:64],
            "associated_from": str(getattr(item, "associated_from", ""))[:80],
            "speaker_role": str(getattr(item, "speaker_role", ""))[:16],
            "speech_act": str(getattr(item, "speech_act", ""))[:16],
            "source_class": str(getattr(item, "source_class", ""))[:64],
            "scope_state": str(getattr(item, "scope_state", ""))[:32],
            "attribution_state": str(getattr(item, "attribution_state", ""))[:64],
            "canonical_record_id": str(
                getattr(item, "canonical_record_id", "")
            )[:160],
            "canonical_index": getattr(item, "canonical_index", None),
            "episode_id": str(getattr(item, "episode_id", ""))[:160],
            "episode_source_start_index": getattr(
                item, "episode_source_start_index", None,
            ),
            "episode_source_end_index_exclusive": getattr(
                item, "episode_source_end_index_exclusive", None,
            ),
            "evidence": [
                {
                    "source_type": str(getattr(evidence, "source_type", "unknown"))[:64],
                    "source_id": str(getattr(evidence, "source_id", ""))[:80],
                    "source_reference_sha256": _digest(getattr(evidence, "source_reference", "")),
                    "sequence": getattr(evidence, "sequence", None),
                    "recorded_at_us": getattr(evidence, "recorded_at_us", None),
                    "source_start_sequence": getattr(evidence, "source_start_sequence", None),
                    "source_end_sequence": getattr(evidence, "source_end_sequence", None),
                }
                for evidence in tuple(getattr(item, "evidence", ()))[:MAX_EVIDENCE]
            ],
        }

    def _anchor_for(
        self, generation: int, active_scope_id: str,
    ) -> HistoricalRecallAnchor | None:
        with self._lock:
            anchor = self._recall_anchor
            if (
                anchor is None
                or time.monotonic() > self._recall_anchor_expires_at
                or anchor.character_id != self.character_id
                or anchor.active_truth_scope_id != active_scope_id
                or anchor.originating_generation + 1 != int(generation)
            ):
                self._recall_anchor = None
                self._recall_anchor_expires_at = 0.0
                return None
            return anchor

    @staticmethod
    def _anchor_from_result(
        result: object,
        character_id: str,
        active_scope_id: str,
        generation: int,
    ) -> HistoricalRecallAnchor | None:
        """Keep only source identity from one successful grounded recall."""
        if getattr(result, "health", RetrievalHealth()).incomplete:
            return None
        return historical_recall_anchor_from_candidates(
            tuple(getattr(result, "candidates", ())),
            character_id, active_scope_id, generation,
        )

    def _map_v1_ids(self, store: MemoryV2Store, selected) -> list[str]:
        ids = [str(item.get("id")) for item in selected if item.get("id") is not None]
        if not ids:
            return []
        rows = store.connection.execute(
            "SELECT legacy_memory_id, claim_id FROM v1_import_records WHERE source_scope=?",
            (v1_import_scope(self.character_id),),
        ).fetchall()
        mapping = {str(row["legacy_memory_id"]): str(row["claim_id"]) for row in rows}
        return [mapping[value] for value in ids if value in mapping]

    def drain(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._recall_anchor = None
            self._recall_anchor_expires_at = 0.0
        self._executor.shutdown(wait=True, cancel_futures=True)
