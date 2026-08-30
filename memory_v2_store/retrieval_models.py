"""Runtime contracts for bounded Memory V2 retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RetrievalQuery:
    """Character-scoped retrieval input built only from user-authored text."""

    character_id: str
    current_user_text: str
    at: str
    mode: str = "ordinary"
    recent_user_turns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.character_id:
            raise ValueError("character_id is required")
        if not self.current_user_text.strip():
            raise ValueError("current_user_text is required")
        if len(self.recent_user_turns) > 8:
            raise ValueError("recent_user_turns must be bounded (maximum 8)")
        if any(not turn.strip() for turn in self.recent_user_turns):
            raise ValueError("recent_user_turns cannot contain empty text")


@dataclass(frozen=True)
class EmbeddingIdentity:
    """Derived-data identity required before a vector is current."""

    provider: str
    model: str
    dimensions: int
    preprocessing_fingerprint: str
    content_fingerprint: str
    status: str = "current"

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("embedding provider and model are required")
        if isinstance(self.dimensions, bool) or self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if not self.preprocessing_fingerprint or not self.content_fingerprint:
            raise ValueError("embedding fingerprints are required")
        if self.status not in {"current", "stale", "failed", "queued", "retryable"}:
            raise ValueError("invalid embedding status")


@dataclass(frozen=True)
class RetrievalTrace:
    """Privacy-safe retrieval trace containing IDs and reasons, never queries."""

    claim_id: str
    candidate_channels: tuple[str, ...] = ()
    selection_reason: Optional[str] = None
    exclusion_reason: Optional[str] = None
    suppression_reason: Optional[str] = None
    channel_ranks: tuple[tuple[str, int], ...] = ()
    score_components: tuple[tuple[str, float], ...] = ()
    structural_eligible: Optional[bool] = None
    selection_state: Optional[str] = None
    final_selected_rank: Optional[int] = None
    query_mode: Optional[str] = None
    candidate_counts: tuple[tuple[str, int], ...] = ()
    deduplicated_candidate_count: Optional[int] = None
    final_count: Optional[int] = None
    estimated_token_count: Optional[int] = None
    abstention_reason: Optional[str] = None
    retrieval_intent: Optional[str] = None
    requested_slots: tuple[str, ...] = ()
    relevance_gate: Optional[str] = None
    legacy_uncertainty_penalty: Optional[float] = None
    covered_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class TypedMemory:
    """Structured final selection passed to prompt assembly."""

    claim_id: str
    label: str
    content: str
    estimated_tokens: int


@dataclass(frozen=True)
class RetrievalOutcome:
    """Bounded retrieval result with optional privacy-safe traces."""

    claim_ids: tuple[str, ...] = ()
    traces: tuple[RetrievalTrace, ...] = ()
    abstention_reason: Optional[str] = None
    selected_memories: tuple[TypedMemory, ...] = ()
