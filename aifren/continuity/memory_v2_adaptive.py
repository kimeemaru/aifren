"""Bounded, non-authoritative V2 dual-read retrieval policy.

This module is deliberately used only by shadow telemetry.  It must never
change V1 prompt construction or persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Iterable

from aifren.memory_v2_store.models import RetrievalHealth, RetrievalLaneHealth, RetrievalQuery
from aifren.memory_v2_store import SemanticRetrievalV2


_SKIP = {"ok", "okay", "yeah", "yep", "nope", "lol", "thanks", "thank", "hi", "hello", "hey"}
_RECALL = {"remember", "remind", "prefer", "preference", "used", "before", "when", "where", "what", "did", "ever", "last", "years", "ago"}


def _terms(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]{3,}", str(text).lower()))


@dataclass(frozen=True)
class AdaptiveDecision:
    mode: str
    reason: str


class WorkingRecallCache:
    """Tiny character-scoped ephemeral cache of validated V2 claim IDs."""
    def __init__(self, *, ttl_seconds: float = 90.0, capacity: int = 8) -> None:
        self.ttl_seconds, self.capacity = ttl_seconds, capacity
        self._entries: dict[str, tuple[float, frozenset[str], tuple[str, ...]]] = {}

    def clear(self, character_id: str | None = None) -> None:
        if character_id is None:
            self._entries.clear()
        else:
            self._entries.pop(str(character_id), None)

    def get(self, character_id: str, query: str, now: float | None = None) -> tuple[str, ...]:
        entry = self._entries.get(str(character_id))
        if entry is None:
            return ()
        now = time.monotonic() if now is None else now
        expires, topics, claim_ids = entry
        if now >= expires or not (_terms(query) & topics):
            self._entries.pop(str(character_id), None)
            return ()
        return claim_ids

    def put(self, character_id: str, query: str, claim_ids: Iterable[str], now: float | None = None) -> None:
        ids = tuple(dict.fromkeys(str(value) for value in claim_ids))[: self.capacity]
        if not ids:
            return
        now = time.monotonic() if now is None else now
        self._entries[str(character_id)] = (now + self.ttl_seconds, _terms(query), ids)


class AdaptiveShadowRetrieval:
    """SKIP/CACHED/NORMAL/DEEP policy for V2 diagnostics only."""
    def __init__(self, store, provider, cache: WorkingRecallCache | None = None) -> None:
        self.store, self.provider = store, provider
        self.cache = cache or WorkingRecallCache()
        self.last_health = RetrievalHealth()

    def decide(self, character_id: str, text: str) -> AdaptiveDecision:
        tokens = _terms(text)
        # Never classify an arbitrary short content word ("tea", "Mira")
        # as an acknowledgement: false-negative recall is worse than a small
        # non-authoritative telemetry cost.
        if tokens and tokens <= _SKIP:
            return AdaptiveDecision("skip", "short_acknowledgement")
        if self.cache.get(character_id, text):
            return AdaptiveDecision("cached", "same_character_topic")
        return AdaptiveDecision("deep" if tokens & _RECALL else "normal", "explicit_recall" if tokens & _RECALL else "ordinary_memory_probe")

    def retrieve(self, query: RetrievalQuery):
        decision = self.decide(query.character_id, query.current_user_text)
        if decision.mode == "skip":
            self.last_health = RetrievalHealth((RetrievalLaneHealth("claims", "unused", "routing", "not_applicable"),))
            return (), "adaptive_skip", decision.reason
        cached = self.cache.get(query.character_id, query.current_user_text)
        if cached:
            # Validate cached IDs through the authoritative lifecycle scope.
            rows = self.store.structural_claims(query.character_id, 2**63 - 1, historical=False, claim_ids=cached)
            ids = tuple(row["claim_id"] for row in rows)
            if ids:
                self.last_health = RetrievalHealth((RetrievalLaneHealth("claims", "complete", "cache"),))
                return ids, "adaptive_cached", decision.reason
            self.cache.clear(query.character_id)
        deep = decision.mode == "deep"
        outcome = SemanticRetrievalV2(
            self.store,
            embedding_provider=self.provider,
            allow_legacy_unverified=True,
            ann_ef=4096 if deep else 256,
            ann_candidate_multiplier=16 if deep else 4,
        ).retrieve(query)
        if not outcome.claim_ids and not deep and not outcome.health.incomplete:
            # A conservative normal pass never manufactures a result; only a
            # weak/empty normal result receives the measured deep retry.
            outcome = SemanticRetrievalV2(
                self.store, embedding_provider=self.provider,
                allow_legacy_unverified=True, ann_ef=4096, ann_candidate_multiplier=16,
            ).retrieve(query)
            strategy = "adaptive_normal_then_deep"
        else:
            strategy = "adaptive_deep" if deep else "adaptive_normal"
        self.last_health = outcome.health
        if outcome.claim_ids and not outcome.health.incomplete:
            self.cache.put(query.character_id, query.current_user_text, outcome.claim_ids)
        return outcome.claim_ids, strategy, "shadow_failure" if outcome.health.incomplete else outcome.abstention_reason
