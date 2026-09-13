"""Data contracts shared by deterministic memory benchmark components."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RetrievalLaneHealth:
    """Execution, not evidence. Codes are owned constants, never exceptions.

    ``affected_slots=None`` means the whole request. A bounded provider which
    executes a narrower slot contract may name its keys; consumers never infer
    that scope from candidates or reclassify the query.
    """

    lane: str
    state: str
    stage: str = "lookup"
    error_code: str = ""
    affected_slots: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.lane not in {"claims", "exact", "lexical", "structural", "semantic", "episodes", "anchor", "durable", "observation"}:
            raise ValueError("invalid retrieval lane")
        if self.state not in {"complete", "incomplete", "unused", "recovered"}:
            raise ValueError("invalid retrieval execution state")
        if self.stage not in {"lookup", "routing", "embedding", "ann", "cosine", "cache", "source"}:
            raise ValueError("invalid retrieval stage")
        if self.error_code not in {
            "", "lookup_failed", "embedding_failed", "embedding_invalid", "embedding_not_current",
            "ann_failed", "cosine_failed", "fallback_bound", "cache_invalid", "cache_missing",
            "not_configured", "not_applicable", "candidate_bound", "unreported", "owner_mismatch",
            "observation_pending", "observation_failed", "source_changed",
        }:
            raise ValueError("invalid retrieval error code")
        if self.affected_slots is not None and (
            not isinstance(self.affected_slots, tuple) or len(self.affected_slots) > 12
            or any(not isinstance(key, str) or not key.isalnum() or len(key) > 32 for key in self.affected_slots)
        ):
            raise ValueError("invalid retrieval slot scope")


@dataclass(frozen=True)
class RetrievalHealth:
    """Bounded execution ledger shared by retrieval, authority and diagnostics.

    No declaration is unknown, never proof of a healthy empty lookup. A
    recovered lane fulfilled the same bounded search via its supported fallback.
    """

    lanes: tuple[RetrievalLaneHealth, ...] = ()

    def __post_init__(self):
        if not isinstance(self.lanes, tuple) or len(self.lanes) > 12 or any(
            not isinstance(lane, RetrievalLaneHealth) for lane in self.lanes
        ):
            raise ValueError("invalid retrieval health ledger")

    @property
    def incomplete(self) -> bool:
        return not self.lanes or any(lane.state == "incomplete" for lane in self.lanes)

    @property
    def state(self) -> str:
        if self.incomplete:
            return "incomplete" if self.lanes else "unreported"
        if any(lane.state == "recovered" for lane in self.lanes):
            return "recovered"
        return "complete" if any(lane.state == "complete" for lane in self.lanes) else "unused"

    def affects(self, slot_key: str) -> bool:
        return not self.lanes or any(
            lane.state == "incomplete" and (lane.affected_slots is None or slot_key in lane.affected_slots)
            for lane in self.lanes
        )

    def diagnostics(self) -> dict[str, object]:
        failed = next((lane for lane in self.lanes if lane.state == "incomplete"), None)
        recovered = next((lane for lane in self.lanes if lane.state == "recovered"), None)
        detail = failed or recovered
        return {
            "retrieval_health": self.state,
            "retrieval_failed_lanes": sum(lane.state == "incomplete" for lane in self.lanes),
            "retrieval_recovered_lanes": sum(lane.state == "recovered" for lane in self.lanes),
            "retrieval_error_stage": detail.stage if detail else "",
            "retrieval_error_code": detail.error_code if detail else ("unreported" if not self.lanes else ""),
        }


@dataclass(frozen=True)
class RetrievalQuery:
    """Future retrieval input, deliberately independent of runtime classes.

    Ordinary factual retrieval is built from user-authored text only.  Recent
    assistant text is intentionally absent: it must not become indirect
    evidence of a user fact or steer normal user-memory recall.
    """

    character_id: str
    current_user_text: str
    at: str
    mode: str = "ordinary"
    recent_user_turns: tuple[str, ...] = ()

    def __post_init__(self):
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
    """Derived-data identity required before a vector is considered current."""

    provider: str
    model: str
    dimensions: int
    preprocessing_fingerprint: str
    content_fingerprint: str
    status: str = "current"

    def __post_init__(self):
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
    """Privacy-safe future retrieval trace; IDs/reasons, never raw archive text."""

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
class HistoricalOrderWitness:
    """Source-bound proof supplied by the existing historical retrieval owner."""

    direction: str
    source_id: str
    source_index: int
    anchor_id: str
    anchor_index: int
    anchor_value: str
    truth_scope_id: str
    speaker_role: str


@dataclass(frozen=True)
class HistoricalSourceSegment:
    """An exact, independently quoted passage of one canonical source."""

    start: int
    end: int
    text: str
    source_length: int

    def __post_init__(self):
        if not 0 <= self.start < self.end <= self.source_length or self.end - self.start != len(self.text):
            raise ValueError("invalid historical source segment")


@dataclass(frozen=True)
class TypedMemory:
    """Structured final selection; prompt construction remains a later concern."""

    claim_id: str
    label: str
    content: str
    estimated_tokens: int
    order_witness: HistoricalOrderWitness | None = None
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


@dataclass(frozen=True)
class RetrievalOutcome:
    """Shared runtime retrieval result, also used by isolated benchmarks."""

    claim_ids: tuple[str, ...] = ()
    traces: tuple[RetrievalTrace, ...] = ()
    abstention_reason: Optional[str] = None
    selected_memories: tuple[TypedMemory, ...] = ()
    health: RetrievalHealth = RetrievalHealth()


@dataclass(frozen=True)
class SyntheticEvent:
    event_id: str
    character_id: str
    recorded_at: str
    content: str
    event_type: str = "message"
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclass(frozen=True)
class GoldClaim:
    claim_id: str
    character_id: str
    category: str
    content: str
    source_event_ids: tuple[str, ...]
    status: str = "active"
    superseded_by: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    topic: str = "general"
    importance: int = 5


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    character_id: str
    query: str
    at: str
    expected_claim_ids: tuple[str, ...] = ()
    forbidden_claim_ids: tuple[str, ...] = ()
    recently_used_claim_ids: tuple[str, ...] = ()
    recent_visible_claim_ids: tuple[str, ...] = ()
    query_mode: str = "ordinary"
    recent_user_turns: tuple[str, ...] = ()
    expected_channels: tuple[str, ...] = ()
    requires_trace: bool = False
    embedding_state: Optional[str] = None
    relationship_hop_limit: int = 0
    final_token_budget: int = 180
    final_injection_cap: int = 3
    contract_tags: tuple[str, ...] = ()
    deterministic_only: bool = True
    baseline_compatible: bool = False
    notes: str = ""

    @property
    def is_negative(self) -> bool:
        return not self.expected_claim_ids

    @property
    def retrieval_query(self) -> RetrievalQuery:
        return RetrievalQuery(
            character_id=self.character_id,
            current_user_text=self.query,
            at=self.at,
            mode=self.query_mode,
            recent_user_turns=self.recent_user_turns,
        )


@dataclass(frozen=True)
class ClaimProposal:
    """Future extractor/consolidator output evaluated without a live model."""

    claim_id: str
    character_id: str
    source_event_ids: tuple[str, ...]
    status: str = "active"
    superseded_by: Optional[str] = None


@dataclass(frozen=True)
class BenchmarkFixture:
    version: str
    events: tuple[SyntheticEvent, ...]
    claims: tuple[GoldClaim, ...]
    retrieval_cases: tuple[RetrievalCase, ...]


@dataclass
class RetrievalReport:
    query_count: int = 0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mean_reciprocal_rank: float = 0.0
    ndcg_at_k: float = 0.0
    negative_false_positive_rate: float = 0.0
    character_isolation_violation_rate: float = 0.0
    provenance_completeness: float = 0.0
    temporal_correctness: float = 0.0
    forbidden_retrieval_rate: float = 0.0
    recent_use_violation_rate: float = 0.0
    category_diversity: float = 0.0
    topic_diversity: float = 0.0
    near_duplicate_rate: float = 0.0
    abstention_accuracy: float = 0.0
    recent_visible_duplication_violation_rate: float = 0.0
    assistant_contamination_violation_rate: float = 0.0
    candidate_channel_dominance_rate: Optional[float] = None
    candidate_deduplication_violation_rate: float = 0.0
    explicit_repeat_override_correctness: float = 0.0
    repeated_memory_injection_rate: float = 0.0
    reinforcement_source_correctness: float = 0.0
    self_reinforcement_violation_rate: float = 0.0
    context_budget_violation_rate: float = 0.0
    claim_type_starvation_rate: float = 0.0
    stale_embedding_use_violation_rate: float = 0.0
    relationship_expansion_bound_violation_rate: float = 0.0
    trace_completeness: Optional[float] = None
    paraphrase_success: Optional[float] = None
    weak_fts_false_positive_rate: Optional[float] = None
    ambiguity_abstention: Optional[float] = None
    assistant_opinion_contamination: Optional[float] = None
    multi_intent_coverage: Optional[float] = None
    legacy_strong_recall: Optional[float] = None
    legacy_weak_abstention: Optional[float] = None
    latency_seconds: float = 0.0
    peak_memory_bytes: int = 0


@dataclass
class ConsolidationReport:
    proposal_count: int = 0
    unsupported_claim_rate: float = 0.0
    provenance_completeness: float = 0.0
    supersession_correctness: float = 0.0
    temporal_correctness: float = 0.0
