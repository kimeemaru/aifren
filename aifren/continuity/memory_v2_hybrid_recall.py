"""Thin, bounded orchestration for V2 recall capabilities outside its retriever.

``SemanticRetrievalV2`` remains the general claim candidate-generation,
ranking, lifecycle, scope, and abstention authority. This shadow/evaluation
layer adds only validated compacted episodes, authoritative source ordering,
one source-backed association hop, and bounded evidence projection. It never
constructs production prompt context or changes Memory V1 authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from aifren.memory_v2_store.models import HistoricalOrderWitness, HistoricalSourceSegment, RetrievalHealth, RetrievalLaneHealth, RetrievalOutcome, RetrievalQuery
from aifren.continuity.memory_query_decision import MemoryQueryDecision, decide_memory_query
from aifren.continuity.memory_v2_evidence_sufficiency import relation_value
from aifren.continuity.memory_v2_episode_compaction import (
    EPISODE_PURPOSE_HISTORICAL,
    EpisodeCompactionCache,
    canonical_episode_source_groups,
    historical_episode_source_groups,
)
from aifren.continuity.memory_v2_historical_evidence import resolve_historical_evidence
from aifren.memory_v2_store import MemoryV2Store, SemanticRetrievalV2
from aifren.memory_v2_store.store import utc_now_us
from aifren.continuity.memory_v2_source_projection import project_source
from aifren.memory_v2_store.retrieval import _tokens, _historical_distinctive_terms


_STOP = frozenset({
    "a", "an", "and", "are", "did", "do", "for", "how", "i", "in", "is",
    "it", "me", "my", "of", "on", "our", "the", "to", "was", "we", "what",
    "when", "where", "which", "who", "you", "your", "that", "this",
})
_TEMPORAL_WORDS = frozenset({
    "before", "after", "first", "earliest", "last", "latest", "most", "recent",
    "immediately", "previous", "next",
})
_ASSOCIATIVE_QUERY_PATTERNS = (
    r"\bconnected to\b", r"\brelated to\b", r"\bperson we\b",
    r"\bwho we\b", r"\bsame (event|episode|meeting|source)\b",
)
_CAUSAL_IRREGULAR = {
    "bought": "buy", "felt": "feel", "found": "find", "left": "leave",
    "lost": "lose", "made": "make", "met": "meet", "sold": "sell",
    "took": "take", "went": "go",
}
MAX_RECALL_ANCHOR_IDENTITIES = 4
MAX_RECALL_ANCHOR_ASSOCIATIONS = 2
MAX_RECALL_ANCHOR_SOURCE_RECORDS = 96


def _terms(value: object) -> frozenset[str]:
    return frozenset(
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]+", str(value).casefold())
        if token not in _STOP
    )


def _causal_terms(value: object) -> frozenset[str]:
    result: set[str] = set()
    for term in _terms(value):
        result.add(_CAUSAL_IRREGULAR.get(term, term))
        if term.endswith("ing") and len(term) > 5:
            result.update((term[:-3], term[:-3] + "e"))
        elif term.endswith("ed") and len(term) > 4:
            result.update((term[:-2], term[:-2] + "e"))
        elif term.endswith("s") and len(term) > 4:
            result.add(term[:-1])
    return frozenset(result)


@dataclass(frozen=True)
class RecallEvidence:
    source_type: str
    source_id: str
    source_reference: str = ""
    sequence: int | None = None
    recorded_at_us: int | None = None
    source_start_sequence: int | None = None
    source_end_sequence: int | None = None


@dataclass(frozen=True)
class HybridRecallCandidate:
    memory_id: str
    lane: str
    content: str
    score: float
    signals: tuple[tuple[str, float], ...]
    truth_scope_id: str
    status: str
    evidence: tuple[RecallEvidence, ...] = ()
    associated_from: str = ""
    speaker_role: str = ""
    speech_act: str = ""
    source_class: str = ""
    scope_state: str = ""
    attribution_state: str = ""
    canonical_record_id: str = ""
    canonical_index: int | None = None
    episode_id: str = ""
    episode_source_start_index: int | None = None
    episode_source_end_index_exclusive: int | None = None
    order_witness: HistoricalOrderWitness | None = None
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


@dataclass(frozen=True)
class HistoricalRecallAnchor:
    """Ephemeral source identity for one immediately following shadow turn."""

    character_id: str
    active_truth_scope_id: str
    originating_generation: int
    canonical_record_ids: tuple[str, ...] = ()
    canonical_indices: tuple[int, ...] = ()
    episode_ids: tuple[str, ...] = ()
    speaker_roles: tuple[str, ...] = ()


def historical_recall_anchor_from_candidates(
    candidates: Sequence[object],
    character_id: str,
    active_truth_scope_id: str,
    generation: int,
) -> HistoricalRecallAnchor | None:
    """Project only canonical identity from one grounded retrieval result."""
    grounded = [
        value for value in tuple(candidates)
        if str(getattr(value, "lane", "")) in {
            "historical_evidence", "historical_episode_source",
        }
        and getattr(value, "canonical_index", None) is not None
        and str(getattr(value, "canonical_record_id", ""))
    ]
    # Several ranked sources do not establish one antecedent. Do not guess
    # which proposition the next "that/it" refers to, even within one episode.
    identities = {str(value.canonical_record_id) for value in grounded}
    if len(identities) != 1:
        return None
    grounded = grounded[:1]
    return HistoricalRecallAnchor(
        str(character_id), str(active_truth_scope_id), int(generation),
        tuple(dict.fromkeys(
            str(value.canonical_record_id) for value in grounded
        )),
        tuple(dict.fromkeys(int(value.canonical_index) for value in grounded)),
        tuple(dict.fromkeys(
            str(value.episode_id) for value in grounded
            if str(getattr(value, "episode_id", ""))
        )),
        (str(grounded[0].speaker_role),),
    )


@dataclass(frozen=True)
class HybridRecallResult:
    candidates: tuple[HybridRecallCandidate, ...]
    abstention_reason: str = ""
    generated_counts: tuple[tuple[str, int], ...] = ()
    health: RetrievalHealth = RetrievalHealth()


@dataclass
class _Candidate:
    memory_id: str
    lane: str
    content: str
    scope_id: str
    status: str = "active"
    base_rank: int | None = None
    associated_from: str = ""
    episode_record: object | None = None
    recall_evidence: tuple[RecallEvidence, ...] = ()
    scope_state: str = ""
    speech_act: str = ""
    speaker_role: str = ""
    source_class: str = ""
    attribution_state: str = ""
    canonical_record_id: str = ""
    canonical_index: int | None = None
    episode_id: str = ""
    episode_source_start_index: int | None = None
    episode_source_end_index_exclusive: int | None = None
    score: float = 0.0
    signals: dict[str, float] | None = None
    order_witness: HistoricalOrderWitness | None = None
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


class HybridMemoryV2Recall:
    """Orchestrate trustworthy lanes around one SemanticRetrievalV2 result."""

    def __init__(
        self,
        store: MemoryV2Store,
        character_id: str,
        *,
        semantic_retriever: SemanticRetrievalV2 | None = None,
        episode_cache: EpisodeCompactionCache | None = None,
        canonical_messages: Sequence[Mapping[str, object]] = (),
        include_historical_episodes: bool = False,
    ) -> None:
        self.store = store
        self.character_id = str(character_id)
        self.semantic = semantic_retriever or SemanticRetrievalV2(store)
        self.episode_cache = episode_cache
        self.include_historical_episodes = bool(include_historical_episodes)
        # Real-turn shadow evaluation may supply a fixed-length view over the
        # append-only canonical archive. Preserve that view so observation
        # does not copy an unbounded lifetime history merely to validate the
        # cache's already-bounded episode candidates.
        self.messages = canonical_messages

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        limit: int = 3,
        candidate_limit: int = 24,
        evidence_limit: int = 3,
        recall_anchor: HistoricalRecallAnchor | None = None,
        memory_query_decision: MemoryQueryDecision | None = None,
    ) -> HybridRecallResult:
        if query.character_id != self.character_id:
            return HybridRecallResult((), "character_mismatch", health=RetrievalHealth((
                RetrievalLaneHealth("claims", "incomplete", "routing", "owner_mismatch"),
            )))
        if not 1 <= limit <= 5 or not 4 <= candidate_limit <= 50 or not 1 <= evidence_limit <= 4:
            raise ValueError("hybrid recall bounds are invalid")
        decision = memory_query_decision or decide_memory_query(query.current_user_text)
        query_terms = _terms(query.current_user_text)
        if not query_terms:
            return HybridRecallResult((), "no_searchable_terms", health=RetrievalHealth((
                RetrievalLaneHealth("claims", "unused", "routing", "not_applicable"),
            )))
        scope_id = self.store.active_truth_scope_id(self.character_id)

        # This is the only general claim retrieval/ranking pass.
        if decision.intent == "grounded_followup_attribute":
            # Only the exact prior source range can answer this contract;
            # unrelated semantic search is intentionally unused.
            outcome = RetrievalOutcome(health=RetrievalHealth((
                RetrievalLaneHealth("claims", "unused", "routing", "not_applicable"),
            )))
        else:
            try:
                outcome = self.semantic.retrieve(
                    query, final_count=limit, truth_scope_id=scope_id,
                    memory_query_decision=decision,
                    **({"canonical_messages": self.messages} if self.messages else {}),
                )
            except Exception:
                outcome = RetrievalOutcome(health=RetrievalHealth((
                    RetrievalLaneHealth("claims", "incomplete", "lookup", "lookup_failed"),
                )))
        health = getattr(outcome, "health", RetrievalHealth())
        lanes = list(health.lanes or (RetrievalLaneHealth("claims", "incomplete", "lookup", "unreported"),))
        rows = self._eligible_rows(tuple(outcome.claim_ids), scope_id)
        historical = self.store.historical_evidence_metadata(
            self.character_id, outcome.claim_ids,
            limit=min(50, max(1, len(outcome.claim_ids))),
        ) if outcome.claim_ids else {}
        candidates: dict[str, _Candidate] = {}
        memories = {m.claim_id:m for m in outcome.selected_memories}
        for rank, claim_id in enumerate(outcome.claim_ids, start=1):
            row = rows.get(claim_id)
            if row is not None:
                historical_row = historical.get(claim_id)
                lane = "historical_evidence" if historical_row is not None else "semantic_v2"
                scope_state = str(historical_row["scope_state"]) if historical_row is not None else ""
                speech_act = str(historical_row["speech_act"]) if historical_row is not None else ""
                speaker_role = str(historical_row["speaker_role"]) if historical_row is not None else ""
                source_class = str(historical_row["source_class"]) if historical_row is not None else ""
                candidates[claim_id] = _Candidate(
                    claim_id, lane, str(getattr(memories.get(claim_id), "content", row["content"])),
                    str(row["truth_scope_id"] or ""),
                    f"historical_{scope_state}" if historical_row is not None else str(row["effective_status"]),
                    base_rank=rank, scope_state=scope_state, speech_act=speech_act,
                    speaker_role=speaker_role, source_class=source_class,
                    canonical_record_id=(
                        str(historical_row["canonical_record_id"])
                        if historical_row is not None else ""
                    ),
                    canonical_index=(
                        int(historical_row["canonical_index"])
                        if historical_row is not None else None
                    ),
                    order_witness=getattr(memories.get(claim_id), "order_witness", None),
                    source_segments=getattr(memories.get(claim_id), "source_segments", ()),
                )

        counts = {
                  "semantic_v2": sum(item.lane == "semantic_v2" for item in candidates.values()),
                  "historical_evidence": sum(item.lane == "historical_evidence" for item in candidates.values()),
                  "episode": 0,
                  "historical_episode": 0,
                  "historical_episode_source": 0,
                  "recall_anchor_source": 0,
                  "source_association": 0, "temporal_source_order": 0}
        temporal_relation = decision.source_order is None and bool(re.search(
            r"\b(?:immediately\s+(?:before|after)|previous|next)\b",
            query.current_user_text.casefold(),
        ))
        if temporal_relation:
            self._add_temporal_neighbors(candidates, query, scope_id, candidate_limit, counts)
            # For an explicit source-order question, the matching anchor is
            # navigation evidence rather than an answer. If authoritative
            # adjacency cannot resolve the relation, abstain instead of
            # returning the anchor as superficially relevant context.
            candidates = {
                key: value for key, value in candidates.items()
                if value.lane == "temporal_source_order"
            }
        associative_query = any(
            re.search(pattern, query.current_user_text.casefold())
            for pattern in _ASSOCIATIVE_QUERY_PATTERNS
        )
        concrete_followup = decision.intent == "grounded_followup_attribute"
        if concrete_followup:
            self._add_recall_anchor_associations(
                candidates, recall_anchor, scope_id, candidate_limit, counts,
                decision=decision,
                health_lanes=lanes,
            )
            # A concrete follow-up has no independent source authority.  It is
            # answered only by an exact one-hop projection from the preceding
            # admitted source identity.
            candidates = {
                key: value for key, value in candidates.items()
                if value.lane == "historical_recall_anchor_source"
            }
        elif associative_query:
            self._add_source_associations(candidates, scope_id, candidate_limit, counts)
            self._add_recall_anchor_associations(
                candidates, recall_anchor, scope_id, candidate_limit, counts,
                decision=decision,
                health_lanes=lanes,
            )
            # A source-association question is answered only by a represented
            # evidence relationship. Semantic anchors remain useful for the
            # bounded hop but are not themselves answers to that relation.
            candidates = {
                key: value for key, value in candidates.items()
                if value.lane in {"source_association", "historical_recall_anchor_source"}
            }
        # Episode similarity cannot establish an associative edge. When the
        # query explicitly asks for a connected detail, only the exact
        # source-evidence hop above may answer; an absent antecedent remains an
        # abstention instead of admitting a plausible neighboring summary.
        if not associative_query and not temporal_relation and not concrete_followup and decision.source_order is None:
            # Do not retain a partially projected episode if source resolution
            # failed; independently selected claims still survive.
            staged, staged_counts = dict(candidates), dict(counts)
            try:
                episode_health = self._add_validated_episodes(
                    staged, query.current_user_text, scope_id, candidate_limit, staged_counts,
                    decision,
                )
            except Exception:
                episode_health = RetrievalHealth((RetrievalLaneHealth("episodes", "incomplete", "source", "lookup_failed"),))
            else:
                candidates, counts = staged, staged_counts
            lanes.extend(episode_health.lanes or (RetrievalLaneHealth("episodes", "incomplete", "lookup", "unreported"),))
        else:
            lanes.append(RetrievalLaneHealth("episodes", "unused", "routing", "not_applicable"))
        # Episode summaries locate an exact source; use the same bounded
        # projection as occurrence retrieval, never their shortened prefix.
        if self.messages and decision.source_order is None and not concrete_followup:
            projection_error = ""
            for key, item in tuple(candidates.items()):
                if item.lane != "historical_episode_source" or item.source_segments:
                    continue
                projection = project_source(self.messages, item.canonical_index,
                    item.canonical_record_id, item.speaker_role,
                    _historical_distinctive_terms(_tokens(query.current_user_text)), _tokens,
                    requested_speech_act=decision.requested_speech_act, allow_whole_source=True)
                if not projection.segments:
                    candidates.pop(key)
                    if projection.reason != "irrelevant":
                        projection_error = projection.reason
                else:
                    item.source_segments = projection.segments
                    item.content = f"Historical {item.speaker_role} record: {projection.segments[0].text}"
            if projection_error:
                lanes.append(RetrievalLaneHealth("episodes", "incomplete", "source", projection_error))
        health = RetrievalHealth(tuple(lanes))
        self._score(candidates, query, query_terms)

        selected = [
            item for item in sorted(candidates.values(), key=lambda value: (-value.score, value.memory_id))
            if item.score >= 1.0
        ]
        selected = self._suppress_conflicting_entity_secondaries(
            selected, query.current_user_text,
        )[:limit]
        if not selected:
            return HybridRecallResult(
                (), "lookup_incomplete" if health.incomplete else "anchor_attribute_ambiguous"
                if counts.get("recall_anchor_ambiguous") else outcome.abstention_reason or "all_augmented_candidates_weak",
                tuple(sorted(counts.items())), health,
            )

        evidence = self._project_claim_evidence(
            [item.memory_id for item in selected if item.episode_record is None], evidence_limit,
        )
        result: list[HybridRecallCandidate] = []
        for item in selected:
            item_evidence = evidence.get(item.memory_id, ())
            if item.recall_evidence:
                item_evidence = item.recall_evidence
            elif item.episode_record is not None:
                record = item.episode_record
                item_evidence = (RecallEvidence(
                    "validated_canonical_source_range", record.record_id,
                    source_start_sequence=record.source_start_sequence,
                    source_end_sequence=record.source_end_sequence,
                ),)
            result.append(HybridRecallCandidate(
                item.memory_id, item.lane, item.content, round(item.score, 6),
                tuple(sorted((item.signals or {}).items())), item.scope_id,
                item.status, item_evidence, item.associated_from,
                item.speaker_role, item.speech_act, item.source_class,
                item.scope_state, item.attribution_state,
                item.canonical_record_id, item.canonical_index,
                item.episode_id, item.episode_source_start_index,
                item.episode_source_end_index_exclusive,
                item.order_witness,
                item.source_segments,
            ))
        return HybridRecallResult(tuple(result), generated_counts=tuple(sorted(counts.items())), health=health)

    def _eligible_rows(self, claim_ids: tuple[str, ...], scope_id: str) -> dict[str, object]:
        if not claim_ids:
            return {}
        rows = self.store.structural_claims(
            self.character_id, utc_now_us(), historical=False,
            claim_ids=tuple(dict.fromkeys(claim_ids)), truth_scope_id=scope_id,
            limit=min(50, len(claim_ids)),
            include_historical_evidence=bool(
                getattr(self.semantic, "include_historical_evidence", False)
            ),
        )
        return {str(row["claim_id"]): row for row in rows}

    def _add_source_associations(self, candidates, scope_id, candidate_limit, counts) -> None:
        # Expanding a second-best semantic anchor manufactured convincing but
        # false relationships in adversarial histories. One step from the
        # strongest admitted anchor is the complete bounded contract.
        seeds = [item.memory_id for item in candidates.values() if item.base_rank == 1]
        if not seeds or len(candidates) >= candidate_limit:
            return
        placeholders = ",".join("?" for _ in seeds)
        rows = self.store.connection.execute(
            f"""SELECT DISTINCT seed.claim_id AS seed_id, linked.claim_id AS linked_id
                  FROM claim_evidence seed JOIN claim_evidence linked
                    ON linked.character_id=seed.character_id AND linked.event_id=seed.event_id
                  JOIN claims c ON c.character_id=linked.character_id AND c.claim_id=linked.claim_id
                 WHERE seed.character_id=? AND seed.claim_id IN ({placeholders})
                   AND linked.claim_id<>seed.claim_id AND c.truth_scope_id=?
                 ORDER BY seed.claim_id, linked.claim_id LIMIT 8""",
            (self.character_id, *seeds, scope_id),
        ).fetchall()
        eligible = self._eligible_rows(tuple(str(row["linked_id"]) for row in rows), scope_id)
        for row in rows:
            linked_id = str(row["linked_id"])
            value = eligible.get(linked_id)
            if value is None or linked_id in candidates or len(candidates) >= candidate_limit:
                continue
            candidates[linked_id] = _Candidate(
                linked_id, "source_association", str(value["content"]), scope_id,
                str(value["effective_status"]), associated_from=str(row["seed_id"]),
            )
            counts["source_association"] += 1

    def _add_recall_anchor_associations(
        self, candidates, anchor, scope_id, candidate_limit, counts, *, decision, health_lanes,
    ) -> None:
        """Expand one exact prior source through its validated interaction.

        The anchor contains identities only. This method asks the existing
        cache validator which historical episode generation is current. A
        vague association projects at most two records from the same
        indivisible interaction group. A concrete attribute belongs ONLY to
        the anchored canonical source: episode membership, adjacency and a
        matching relation are not proof of event identity.
        """
        if decision.intent == "grounded_followup_attribute":
            self._add_exact_anchor_attribute(
                candidates, anchor, scope_id, candidate_limit, counts,
                decision=decision, health_lanes=health_lanes,
            )
            return
        if (
            anchor is None or self.episode_cache is None
            or anchor.character_id != self.character_id
            or anchor.active_truth_scope_id != scope_id
            or not anchor.canonical_indices
            or len(candidates) >= candidate_limit
        ):
            health_lanes.append(RetrievalLaneHealth("anchor", "unused", "routing", "not_applicable"))
            return
        scope = self.store.connection.execute(
            "SELECT scope_kind FROM truth_scopes WHERE character_id=? AND truth_scope_id=?",
            (self.character_id, scope_id),
        ).fetchone()
        if scope is None:
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "owner_mismatch"))
            return
        active_scope = {"kind": str(scope[0]), "scope_id": scope_id}
        try:
            validation = self.episode_cache.validate_for_context(
                self.messages,
                active_truth_scope=active_scope,
                allow_historical_recall=True,
            )
        except Exception:
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "cache", "lookup_failed"))
            return
        health_lanes.append(RetrievalLaneHealth(
            "anchor", "complete" if validation.accepted else "incomplete", "cache",
            "" if validation.accepted else "cache_invalid",
        ))
        if not validation.accepted:
            return
        anchor_indices = set(anchor.canonical_indices[:MAX_RECALL_ANCHOR_IDENTITIES])
        anchor_episode_ids = set(anchor.episode_ids[:MAX_RECALL_ANCHOR_IDENTITIES])
        anchor_identity_by_index = {
            index: record_id for index, record_id in zip(
                anchor.canonical_indices[:MAX_RECALL_ANCHOR_IDENTITIES],
                anchor.canonical_record_ids[:MAX_RECALL_ANCHOR_IDENTITIES],
            )
        }
        records = [
            value for value in validation.lower_records
            if value.accepted
            and value.generation_purpose == EPISODE_PURPOSE_HISTORICAL
            and value.source_start_index is not None
            and value.source_end_index_exclusive is not None
            and (
                value.record_id in anchor_episode_ids
                or any(
                    int(value.source_start_index) <= index < int(value.source_end_index_exclusive)
                    for index in anchor_indices
                )
            )
        ]
        if any(not any(int(row.source_start_index) <= index < int(row.source_end_index_exclusive)
                       for row in records) for index in anchor_indices):
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "cache", "cache_missing"))
        if not records:
            return
        valid_scope_ids = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?",
                (self.character_id,),
            ).fetchall()
        }
        # A recorded hash includes the entire canonical record (role, scope,
        # provenance and content). Revalidate before using even an exact index;
        # a freshly valid cache must not make an edited old anchor valid again.
        for index, record_id in anchor_identity_by_index.items():
            resolved = resolve_historical_evidence(
                self.messages, index, valid_scope_ids=valid_scope_ids,
            ).evidence
            if resolved is None or resolved.canonical_record_id != record_id:
                health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "owner_mismatch"))
                return
        groups = historical_episode_source_groups(
            self.messages, valid_scope_ids=valid_scope_ids,
        )
        group_by_index = {
            index: group
            for group in groups
            for index in range(group.start_index, group.end_index_exclusive)
        }
        related: list[tuple[int, int, object, object]] = []
        for record in records:
            start = int(record.source_start_index)
            end = int(record.source_end_index_exclusive)
            if end - start > MAX_RECALL_ANCHOR_SOURCE_RECORDS:
                continue
            for anchor_index in sorted(anchor_indices):
                if not start <= anchor_index < end:
                    continue
                group = group_by_index.get(anchor_index)
                if group is None or not (
                    start <= group.start_index
                    and group.end_index_exclusive <= end
                ):
                    continue
                source_start, source_end = group.start_index, group.end_index_exclusive
                for index in range(source_start, source_end):
                    if index in anchor_indices:
                        continue
                    source_decision = resolve_historical_evidence(
                        self.messages, index, valid_scope_ids=valid_scope_ids,
                    )
                    evidence = source_decision.evidence
                    if (
                        evidence is None
                        or evidence.source_class != "ordinary_conversation"
                        or evidence.speaker_role not in {"user", "assistant"}
                    ):
                        continue
                    related.append((abs(index - anchor_index), anchor_index, record, evidence))
        for _distance, anchor_index, record, evidence in sorted(
            related,
            key=lambda value: (
                value[0], value[3].canonical_index,
                value[3].canonical_record_id,
            ),
        )[:MAX_RECALL_ANCHOR_ASSOCIATIONS]:
            if len(candidates) >= candidate_limit:
                break
            memory_id = (
                f"{record.record_id}:anchor-source:{evidence.canonical_index}"
            )
            if memory_id in candidates:
                continue
            candidates[memory_id] = _Candidate(
                memory_id,
                "historical_recall_anchor_source",
                evidence.searchable_text,
                str(record.truth_scope_id or ""),
                f"historical_{record.scope_state}_{evidence.speaker_role}_source",
                associated_from=(
                    anchor_identity_by_index.get(anchor_index, record.record_id)
                ),
                episode_record=record,
                recall_evidence=(
                    RecallEvidence(
                        evidence.source_class, evidence.canonical_record_id,
                        source_reference=f"canonical_index:{evidence.canonical_index}",
                        sequence=evidence.canonical_index + 1,
                        recorded_at_us=evidence.recorded_at_us,
                    ),
                    RecallEvidence(
                        "validated_canonical_source_range", record.record_id,
                        source_start_sequence=record.source_start_sequence,
                        source_end_sequence=record.source_end_sequence,
                    ),
                ),
                scope_state=str(record.scope_state),
                speech_act=evidence.speech_act,
                speaker_role=evidence.speaker_role,
                source_class=evidence.source_class,
                attribution_state="canonical_interaction_source",
                canonical_record_id=evidence.canonical_record_id,
                canonical_index=evidence.canonical_index,
                episode_id=record.record_id,
                episode_source_start_index=record.source_start_index,
                episode_source_end_index_exclusive=record.source_end_index_exclusive,
            )
            counts["recall_anchor_source"] += 1

    def _add_exact_anchor_attribute(
        self, candidates, anchor, scope_id, candidate_limit, counts, *, decision, health_lanes,
    ) -> None:
        """Project one published canonical identity without waiting for episodes.

        A new occurrence can be authoritative before its derived episode exists.
        This route resolves only that exact record; broader interaction hops
        continue to require the separately validated episode range above.
        """
        if anchor is None:
            health_lanes.append(RetrievalLaneHealth("anchor", "unused", "routing", "not_applicable"))
            return
        if (anchor.character_id != self.character_id
                or anchor.active_truth_scope_id != scope_id
                or len(anchor.canonical_indices) != 1
                or len(anchor.canonical_record_ids) != 1
                or len(anchor.speaker_roles) != 1):
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "owner_mismatch"))
            return
        if len(candidates) >= candidate_limit:
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "candidate_bound"))
            return
        valid_scope_ids = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?",
                (self.character_id,),
            ).fetchall()
        }
        index, record_id, speaker = (anchor.canonical_indices[0],
            anchor.canonical_record_ids[0], anchor.speaker_roles[0])
        evidence = resolve_historical_evidence(
            self.messages, index, valid_scope_ids=valid_scope_ids,
        ).evidence
        if (scope_id not in valid_scope_ids or evidence is None
                or evidence.canonical_record_id != record_id
                or evidence.speaker_role != speaker
                or evidence.source_class != "ordinary_conversation"
                or (evidence.scope_state != "unknown_scope" and evidence.truth_scope_id != scope_id)):
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "owner_mismatch"))
            return
        from aifren.continuity.memory_v2_historical_evidence import MAX_SEARCHABLE_CONTENT_CHARACTERS
        from aifren.continuity.memory_v2_evidence_sufficiency import AmbiguousHistoricalAttribute
        if len(str(self.messages[index].get("content", ""))) > MAX_SEARCHABLE_CONTENT_CHARACTERS:
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "candidate_bound"))
            return
        try:
            value = relation_value(evidence.searchable_text, decision.requested_relation, require_unique=True)
        except AmbiguousHistoricalAttribute:
            counts["recall_anchor_ambiguous"] = 1
            health_lanes.append(RetrievalLaneHealth("anchor", "complete", "source"))
            return
        if not value:
            health_lanes.append(RetrievalLaneHealth("anchor", "complete", "source"))
            return
        projection = project_source(self.messages, index, record_id, speaker,
            _historical_distinctive_terms(_tokens(value)), _tokens,
            requested_speech_act=decision.requested_speech_act, allow_whole_source=True)
        if not projection.segments:
            health_lanes.append(RetrievalLaneHealth("anchor", "incomplete", "source", "candidate_bound"))
            return
        memory_id = f"{record_id}:anchor-source:{index}"
        candidates[memory_id] = _Candidate(
            memory_id, "historical_recall_anchor_source",
            f"Historical {speaker} record: {projection.segments[0].text}",
            str(evidence.truth_scope_id or ""),
            f"historical_{evidence.scope_state}_{speaker}_source",
            associated_from=record_id,
            recall_evidence=(RecallEvidence(
                evidence.source_class, record_id,
                source_reference=f"canonical_index:{index}", sequence=index + 1,
                recorded_at_us=evidence.recorded_at_us,
            ),),
            scope_state=evidence.scope_state, speech_act=evidence.speech_act,
            speaker_role=speaker, source_class=evidence.source_class,
            attribution_state="canonical_exact_source_anchor",
            canonical_record_id=record_id, canonical_index=index,
            source_segments=projection.segments,
        )
        counts["recall_anchor_source"] += 1
        health_lanes.append(RetrievalLaneHealth("anchor", "complete", "source"))

    def _add_temporal_neighbors(self, candidates, query, scope_id, candidate_limit, counts) -> None:
        direction = -1 if re.search(r"\b(before|previous)\b", query.current_user_text.casefold()) else (
            1 if re.search(r"\b(after|next)\b", query.current_user_text.casefold()) else 0
        )
        if not direction or not candidates or len(candidates) >= candidate_limit:
            return
        # A second semantic anchor becomes increasingly likely to be a
        # same-topic distractor as history grows. Source-order navigation is a
        # single bounded hop from the strongest admitted anchor, matching the
        # source-association contract.
        seed_ids = [item.memory_id for item in candidates.values() if item.base_rank == 1]
        placeholders = ",".join("?" for _ in seed_ids)
        anchors = self.store.connection.execute(
            f"""SELECT ce.claim_id, e.sequence
                  FROM claim_evidence ce JOIN events e
                    ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id IN ({placeholders})
                   AND e.redaction_state='active'
                 ORDER BY ce.claim_id, e.sequence LIMIT 8""",
            (self.character_id, *seed_ids),
        ).fetchall()
        related: list[tuple[str, str]] = []
        for anchor in anchors:
            linked = self.store.connection.execute(
                """SELECT ce.claim_id FROM events e JOIN claim_evidence ce
                       ON ce.character_id=e.character_id AND ce.event_id=e.event_id
                     WHERE e.character_id=? AND e.sequence=? ORDER BY ce.claim_id LIMIT 4""",
                (self.character_id, int(anchor["sequence"]) + direction),
            ).fetchall()
            related.extend((str(anchor["claim_id"]), str(value["claim_id"])) for value in linked)
        eligible = self._eligible_rows(tuple(value for _seed, value in related), scope_id)
        for seed_id, linked_id in related:
            value = eligible.get(linked_id)
            if value is None or linked_id in candidates or len(candidates) >= candidate_limit:
                continue
            candidates[linked_id] = _Candidate(
                linked_id, "temporal_source_order", str(value["content"]), scope_id,
                str(value["effective_status"]), associated_from=seed_id,
            )
            counts["temporal_source_order"] += 1

    def _add_validated_episodes(
        self, candidates, query_text, scope_id, candidate_limit, counts,
        memory_query_decision: MemoryQueryDecision,
    ) -> RetrievalHealth:
        if self.episode_cache is None or len(candidates) >= candidate_limit:
            return RetrievalHealth((RetrievalLaneHealth("episodes", "unused", "routing",
                "not_configured" if self.episode_cache is None else "candidate_bound"),))
        scope = self.store.connection.execute(
            "SELECT scope_kind FROM truth_scopes WHERE character_id=? AND truth_scope_id=?",
            (self.character_id, scope_id),
        ).fetchone()
        try:
            retrieval = self.episode_cache.retrieve_candidates(
                self.messages, query_text,
                active_truth_scope={"kind": str(scope[0]), "scope_id": scope_id} if scope else None,
                allow_historical_recall=self.include_historical_episodes,
                memory_query_decision=memory_query_decision,
            )
        except Exception:
            return RetrievalHealth((RetrievalLaneHealth("episodes", "incomplete", "lookup", "lookup_failed"),))
        valid_scope_ids = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?",
                (self.character_id,),
            ).fetchall()
        }
        for record in retrieval.candidates:
            if len(candidates) >= candidate_limit:
                break
            historical = record.generation_purpose == EPISODE_PURPOSE_HISTORICAL
            if historical:
                # The derived account is only a broad locator. Final
                # historical candidates must project exact role-labelled
                # canonical records so summary attribution cannot overwrite
                # source ownership.
                counts["historical_episode"] += 1
                for source in record.source_refinements:
                    if len(candidates) >= candidate_limit:
                        break
                    source_id = f"{record.record_id}:source:{source.canonical_index}"
                    source_decision = resolve_historical_evidence(
                        self.messages, source.canonical_index,
                        valid_scope_ids=valid_scope_ids,
                    )
                    source_metadata = source_decision.evidence
                    candidates[source_id] = _Candidate(
                        source_id, "historical_episode_source",
                        f"Historical {source.speaker_role} record: {source.content}",
                        record.truth_scope_id,
                        f"historical_{record.scope_state}_{source.speaker_role}_source",
                        associated_from=record.record_id,
                        episode_record=record, scope_state=record.scope_state,
                        speech_act=(
                            source_metadata.speech_act
                            if source_metadata is not None else ""
                        ),
                        speaker_role=source.speaker_role,
                        source_class=source.source_class,
                        attribution_state=source.attribution_state,
                        canonical_record_id=source.canonical_record_id,
                        canonical_index=source.canonical_index,
                        episode_id=record.record_id,
                        episode_source_start_index=record.source_start_index,
                        episode_source_end_index_exclusive=record.source_end_index_exclusive,
                        recall_evidence=(RecallEvidence(
                            source.source_class, source.canonical_record_id,
                            source_reference=f"canonical_index:{source.canonical_index}",
                            sequence=source.source_sequence,
                        ), RecallEvidence(
                            "validated_canonical_source_range", record.record_id,
                            source_start_sequence=record.source_start_sequence,
                            source_end_sequence=record.source_end_sequence,
                        )),
                    )
                    counts["historical_episode_source"] += 1
                continue
            lane = "historical_episode" if historical else "validated_episode"
            if len(candidates) < candidate_limit:
                candidates[record.record_id] = _Candidate(
                    record.record_id, lane, record.content,
                    record.truth_scope_id,
                    f"historical_{record.scope_state}" if historical else "current_valid",
                    episode_record=record, scope_state=record.scope_state,
                )
                counts["historical_episode" if historical else "episode"] += 1
        return getattr(retrieval, "health", RetrievalHealth())

    def _score(self, candidates, query, query_terms) -> None:
        lower_query = query.current_user_text.casefold()
        temporal_mode = "first" if re.search(r"\b(first|earliest)\b", lower_query) else (
            "latest" if re.search(r"\b(last|latest|most recent)\b", lower_query) else ""
        )
        times = self._claim_times(tuple(
            item.memory_id for item in candidates.values() if item.episode_record is None
        ))
        temporal_pool = [
            times[item.memory_id] for item in candidates.values()
            if item.memory_id in times and ((_terms(item.content) & query_terms) - _TEMPORAL_WORDS)
        ]
        earliest = min(temporal_pool) if temporal_pool else 0
        latest = max(temporal_pool) if temporal_pool else 0
        for item in candidates.values():
            overlap = len((query_terms - _TEMPORAL_WORDS) & _terms(item.content))
            signals = {
                "semantic_v2_rank": (6.0 / item.base_rank) if item.base_rank else 0.0,
                "lexical_overlap": min(4.0, overlap * 1.25),
                "historical_evidence": 1.0 if item.lane == "historical_evidence" else 0.0,
                "unknown_scope": 1.0 if item.scope_state == "unknown_scope" else 0.0,
                "historical_question": 1.0 if item.speech_act == "question" else 0.0,
                "validated_episode": 1.0 if item.episode_record is not None else 0.0,
                # These candidates exist only after one explicit, bounded,
                # source-backed hop from a retriever-selected anchor. When a
                # query asks for that relationship, the answer must lead the
                # anchor that made the hop possible.
                "source_association": 10.0 if item.lane == "source_association" else 0.0,
                "recall_anchor_association": (
                    10.0 if item.lane == "historical_recall_anchor_source" else 0.0
                ),
                "temporal_source_order": 12.0 if item.lane == "temporal_source_order" else 0.0,
                "temporal_extreme": 0.0,
                "causal_support_gate": 0.0,
            }
            timestamp = times.get(item.memory_id)
            if temporal_mode and timestamp is not None and temporal_pool:
                if (temporal_mode == "first" and timestamp == earliest) or (
                    temporal_mode == "latest" and timestamp == latest
                ):
                    signals["temporal_extreme"] = 6.0
            causal_overlap = len(_causal_terms(query.current_user_text) & _causal_terms(item.content))
            if re.search(r"\bwhy\s+did\s+(?:i|we)\b", lower_query) and causal_overlap < 2:
                # A causal question asserts an event premise. Noun-only
                # similarity (rabbit/lamp, violin/concert) is not evidence
                # that the asserted action occurred.
                signals["causal_support_gate"] = -20.0
            item.signals = signals
            item.score = sum(signals.values())

    @staticmethod
    def _suppress_conflicting_entity_secondaries(
        selected: list[_Candidate], query_text: str,
    ) -> list[_Candidate]:
        """Suppress same-noun rows that contradict an explicit modifier."""
        if not selected or selected[0].lane != "semantic_v2":
            return selected
        match = re.search(
            r"\bthe\s+([a-z0-9][a-z0-9_-]+)\s+([a-z0-9][a-z0-9_-]+)\b",
            query_text.casefold(),
        )
        if match is None:
            return selected
        modifier, noun = match.groups()
        strongest_terms = _terms(selected[0].content)
        if modifier not in strongest_terms or noun not in strongest_terms:
            return selected
        return [
            item for index, item in enumerate(selected)
            if index == 0
            or item.lane != "semantic_v2"
            or noun not in _terms(item.content)
            or modifier in _terms(item.content)
        ]

    def _claim_times(self, claim_ids: tuple[str, ...]) -> dict[str, int]:
        if not claim_ids:
            return {}
        placeholders = ",".join("?" for _ in claim_ids)
        rows = self.store.connection.execute(
            f"""SELECT ce.claim_id, MIN(e.recorded_at_us) AS first_at
                  FROM claim_evidence ce JOIN events e
                    ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id IN ({placeholders})
                   AND e.redaction_state='active' GROUP BY ce.claim_id""",
            (self.character_id, *claim_ids),
        ).fetchall()
        return {str(row["claim_id"]): int(row["first_at"]) for row in rows}

    def _project_claim_evidence(self, claim_ids, limit) -> dict[str, tuple[RecallEvidence, ...]]:
        result: dict[str, tuple[RecallEvidence, ...]] = {}
        for claim_id in claim_ids:
            rows = self.store.connection.execute(
                """SELECT ce.event_id, e.event_type, e.source_reference,
                          e.sequence, e.recorded_at_us
                     FROM claim_evidence ce LEFT JOIN events e
                       ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                    WHERE ce.character_id=? AND ce.claim_id=?
                    ORDER BY COALESCE(e.sequence, 2147483647), ce.created_at_us LIMIT ?""",
                (self.character_id, claim_id, limit),
            ).fetchall()
            result[claim_id] = tuple(RecallEvidence(
                str(row["event_type"] or "unresolved_event"), str(row["event_id"]),
                str(row["source_reference"] or "")[:240],
                int(row["sequence"]) if row["sequence"] is not None else None,
                int(row["recorded_at_us"]) if row["recorded_at_us"] is not None else None,
            ) for row in rows)
        return result
