"""Normal prompt authority owner for Memory V2.

The owner performs one bounded hybrid lookup against the current character and
renders the already-evaluated V2-only context contract. It never reads Memory
V1. An explicit memory question with no grounded evidence is an authoritative
successful result, not a provider error or generation fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import time
from typing import Mapping, Sequence

from aifren.memory_v2_store.models import RetrievalHealth, RetrievalLaneHealth, RetrievalQuery
from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerRequirement,
    bind_memory_answer_source_containment,
)
from aifren.continuity.memory_query_decision import MemoryQueryDecision, decide_memory_query
from aifren.continuity.memory_v2_episode_compaction import EpisodeCompactionCache
from aifren.continuity.memory_v2_evidence_sufficiency import admit_memory_evidence
from aifren.continuity.memory_v2_hybrid_recall import (
    HistoricalRecallAnchor,
    HybridRecallResult,
    HybridMemoryV2Recall,
    historical_recall_anchor_from_candidates,
)
from aifren.continuity.memory_v2_replacement_shadow import V2ReplacementContext, compose_v2_replacement_context
from aifren.memory_v2_store import MiniLMEmbeddingProvider, SemanticRetrievalV2
from aifren.continuity.memory_v2_source_containment import select_recent_messages


class MemoryV2AuthorityUnavailable(RuntimeError):
    """Fail-closed authority initialization/retrieval failure."""


@dataclass(frozen=True)
class V2AuthorityTurn:
    design: V2ReplacementContext
    context_block: str
    requirement: MemoryAnswerRequirement
    retrieval_latency_ms: float
    candidate_count: int
    abstention_reason: str
    absence_kind: str
    memory_query_decision: MemoryQueryDecision
    recent_message_count: int = 0
    recent_character_count: int = 0
    insufficient_candidate_count: int = 0
    recall_anchor_used: bool = False
    response_variant: int = 0
    publication_anchor: HistoricalRecallAnchor | None = None
    fallback_anchor: HistoricalRecallAnchor | None = None

    @property
    def authoritative_no_evidence(self) -> bool:
        return bool(
            self.requirement.triggered
            and self.requirement.evidence_state == "no_grounded_evidence"
            and not self.requirement.lookup_unavailable
        )


def _recent_user_text(messages: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    values = tuple(
        str(message.get("content", ""))
        for message in messages[-24:]
        if isinstance(message, Mapping)
        and message.get("role") == "user"
        and str(message.get("content", "")).strip()
    )[-8:]
    return values[:-1] if values else ()


def _absence_kind(design: V2ReplacementContext, candidates: Sequence[object]) -> str:
    requested = design.callback_design.callback_source_kind
    eligible_history = tuple(
        candidate for candidate in candidates
        if str(getattr(candidate, "lane", "")) in {
            "historical_evidence", "historical_episode_source",
            "historical_recall_anchor_source",
        }
        and str(getattr(candidate, "canonical_record_id", ""))
        and str(getattr(candidate, "speaker_role", "")) in {"user", "assistant"}
    )
    if requested in {"user", "assistant"} and any(
        str(getattr(candidate, "speaker_role", "")) != requested
        for candidate in eligible_history
    ):
        return "speaker_mismatch"
    if eligible_history:
        return "subject_mentioned_proposition_unsupported"
    return "nothing_relevant"


def render_authoritative_no_evidence(turn: V2AuthorityTurn) -> str:
    """Render a natural successful answer from the typed absence result."""
    if turn.requirement.lookup_unavailable:
        raise MemoryV2AuthorityUnavailable("I can't check that memory right now. Please try again in a moment.")
    from aifren.continuity.memory_v2_answer_governance import validate_memory_answer_response

    text = _render_no_evidence_variant(turn)
    if validate_memory_answer_response(turn.requirement, text).accepted:
        return text
    # A family member can imply more subject familiarity than the admitted
    # evidence proves. Stay provider-free and use the existing plain unknown.
    return turn.requirement.missing_slot_dialogue or turn.requirement.fallback_dialogue


def _render_no_evidence_variant(turn: V2AuthorityTurn) -> str:
    requirement = turn.requirement
    decision = requirement.memory_query_decision
    if getattr(turn, "abstention_reason", "") in {"anchor_attribute_ambiguous", "topic_reference_ambiguous"}:
        return "I don't remember which one that was. Could you clarify which one you mean?"
    if decision is not None and decision.source_order is not None:
        direction = decision.source_order.direction
        return (f"I can't reliably recall a single value {direction} that specific change or statement. "
                "Could you clarify which one you mean?")
    subject = decision.subject_reference if decision is not None else ""
    if requirement.slots and (len(requirement.slots) > 1 or requirement.requested_speaker in {"assistant", "shared"}):
        # Compound absence communicates every requested unknown, even when a
        # topical but insufficient row happened to be retrieved.
        return requirement.missing_slot_dialogue
    def choose(*values: str) -> str:
        choices = tuple(value for value in values if value)
        if not choices:
            return "I don't remember anything specific about that."
        key = "|".join((
            str(getattr(decision, "intent", "")),
            str(getattr(decision, "requested_relation", "")),
            str(getattr(decision, "subject_reference", "")),
            str(turn.absence_kind),
        ))
        index = (
            int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")
            + int(getattr(turn, "response_variant", 0) or 0)
        )
        return choices[index % len(choices)]

    if turn.absence_kind == "speaker_mismatch":
        if requirement.requested_speaker == "user":
            return choose(
                "I remember mentioning that, but I don't remember you telling me about it.",
                "I brought that up before, but I don't remember you saying it.",
            )
        return choose(
            "I remember the subject coming up, but I don't remember saying that before.",
            "That came up before, but I can't place it as something I said.",
        )
    if turn.absence_kind == "subject_mentioned_proposition_unsupported":
        if requirement.requested_relation == "programming_language":
            return choose(
                "I can't place which programming language it was.",
                "I remember the project, but I can't place the language.",
            )
        if requirement.requested_relation == "preference":
            return choose(
                "I remember that coming up, but not what your favorite was.",
                "The subject rings a bell, but I can't place that preference.",
            )
        return choose(
            "I remember the subject coming up, but not that detail.",
            "That sounds familiar, but I can't place the specific detail.",
        )
    relation = requirement.requested_relation
    if relation == "ownership":
        return choose(
            "I don't remember you saying you owned one.",
            "I can't place you ever telling me that you owned one.",
        )
    if relation == "identity":
        return choose("I don't remember your name yet.", "I can't place your name yet.")
    if relation == "preference":
        slots = tuple(dict.fromkeys(
            str(value).casefold()
            for value in tuple(getattr(decision, "retrieval_slots", ()))[:2]
            if str(value).isalnum()
        ))
        if slots:
            labelled = (
                f"favorite {slots[0]}"
                if len(slots) == 1 else
                f"favorite {slots[0]} or favorite {slots[1]}"
            )
            compact = slots[0] if len(slots) == 1 else f"{slots[0]} or {slots[1]}"
            return choose(
                f"I don't remember you telling me your {labelled}.",
                f"I can't place what you said your favorite {compact} was.",
            )
        return choose(
            "I don't remember you telling me that preference.",
            "I can't place what you said you preferred.",
        )
    if relation == "plan":
        return choose(
            "I don't remember what you planned to do.",
            "I can't place that plan.",
        )
    if relation == "place":
        return choose(
            "I don't remember which place that was.",
            "I can't place where that was.",
        )
    if relation == "programming_language":
        return choose(
            "I can't place which programming language it was.",
            "I don't remember which language was connected to it.",
        )
    if requirement.requested_speaker == "user":
        return choose(
            f"I don't remember you telling me about {subject}."
            if subject else "I don't remember you telling me that.",
            f"I can't place you mentioning {subject} before."
            if subject else "I can't place you saying that before.",
        )
    if requirement.requested_speaker == "assistant":
        return choose(
            f"I don't remember saying anything about {subject} before."
            if subject else "I don't remember saying that before.",
            f"I can't place telling you about {subject}."
            if subject else "I can't place saying that before.",
        )
    if requirement.requested_speaker == "shared":
        return choose(
            f"I don't remember us talking about {subject}."
            if subject else "I don't remember us talking about that.",
            f"I can't place a conversation we had about {subject}."
            if subject else "I can't place us discussing that.",
        )
    return choose(
        f"I don't remember anything specific about {subject}."
        if subject else "I don't remember anything specific about that.",
        f"I can't place anything specific about {subject}."
        if subject else "I can't place anything specific about that.",
    )


class DevelopmentV2MemoryAuthority:
    """One character-scoped V2 prompt authority; legacy class name retained for compatibility."""

    def __init__(
        self,
        store,
        character_id: str,
        canonical_messages: Sequence[Mapping[str, object]],
        *,
        recall: object | None = None,
        embedding_provider: object | None = None,
        recent_context_policy: str | None = None,
    ) -> None:
        self.store = store
        self.character_id = str(character_id)
        self.canonical_messages = canonical_messages
        if recent_context_policy is None:
            from aifren.runtime.config import V2_AUTHORITY_RECENT_POLICY
            recent_context_policy = V2_AUTHORITY_RECENT_POLICY
        self.recent_context_policy = str(recent_context_policy)
        self._turn_generation = 0
        self._recall_anchor: HistoricalRecallAnchor | None = None
        self.observation_health_provider = None
        if not self.character_id:
            raise MemoryV2AuthorityUnavailable("Memory V2 authority requires a character identity.")
        try:
            if recall is None:
                provider = embedding_provider or MiniLMEmbeddingProvider()
                semantic = SemanticRetrievalV2(
                    store, embedding_provider=provider,
                    include_historical_evidence=True,
                )
                recall = HybridMemoryV2Recall(
                    store, self.character_id,
                    semantic_retriever=semantic,
                    episode_cache=EpisodeCompactionCache(store, self.character_id),
                    canonical_messages=canonical_messages,
                    include_historical_episodes=True,
                )
            self.recall = recall
            active = str(store.active_truth_scope_id(self.character_id))
            if not active:
                raise RuntimeError("active truth scope is unavailable")
        except Exception as error:
            raise MemoryV2AuthorityUnavailable(
                f"Memory V2 authority initialization failed: {type(error).__name__}"
            ) from error

    def prepare(
        self,
        query_text: str,
        *,
        active_truth_scope_id: str,
        active_truth_scope: Mapping[str, object] | None = None,
        governed_facts: Sequence[object] = (),
        governed_lookup_health: RetrievalHealth | None = None,
        memory_query_decision: MemoryQueryDecision | None = None,
        current_authoritative_context: object = "",
    ) -> V2AuthorityTurn:
        started = time.perf_counter()
        try:
            decision = memory_query_decision or decide_memory_query(query_text)
            self._turn_generation += 1
            generation = self._turn_generation
            recall_anchor = None
            if (
                decision.intent == "grounded_followup_attribute"
                and self._recall_anchor is not None
                and self._recall_anchor.originating_generation + 1 == generation
                and self._recall_anchor.active_truth_scope_id == str(active_truth_scope_id)
            ):
                recall_anchor = self._recall_anchor
            self._recall_anchor = None  # Consume even on error or unrelated input.
            observed = (self.observation_health_provider(decision)
                        if self.observation_health_provider is not None else None)
            query = RetrievalQuery(
                self.character_id,
                str(query_text),
                datetime.now(timezone.utc).isoformat(),
                "ordinary",
                _recent_user_text(self.canonical_messages),
            )
            retrieve = self.recall.retrieve
            retrieval_parameters = inspect.signature(retrieve).parameters
            accepts_options = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in retrieval_parameters.values()
            )
            retrieval_options = {}
            if accepts_options or "memory_query_decision" in retrieval_parameters:
                retrieval_options["memory_query_decision"] = decision
            if accepts_options or "recall_anchor" in retrieval_parameters:
                retrieval_options["recall_anchor"] = recall_anchor
            try:
                result = retrieve(query, **retrieval_options)
            except Exception:
                # A failed candidate lookup does not invalidate separately
                # admitted current facts. Preserve them through composition.
                result = HybridRecallResult((), "lookup_incomplete", health=RetrievalHealth((
                    RetrievalLaneHealth("claims", "incomplete", "lookup", "lookup_failed"),
                )))
            health = getattr(result, "health", RetrievalHealth())
            if observed is not None:
                observed_health, current_complete = observed
                base_lanes = health.lanes or (
                    RetrievalLaneHealth("claims", "incomplete", "lookup", "unreported"),
                )
                health = RetrievalHealth((*base_lanes, *observed_health.lanes))
                if not current_complete and not decision.historical:
                    # An unobserved committed correction can invalidate an older
                    # "current" value. Historical evidence remains source-owned.
                    governed_facts = ()
            if governed_lookup_health is not None:
                lanes = health.lanes or (RetrievalLaneHealth("claims", "incomplete", "lookup", "unreported"),)
                # Current governed facts cannot complete an exact historical
                # source request; that optional read is not a dependency.
                durable_lanes = (
                    (RetrievalLaneHealth("durable", "unused", "routing", "not_applicable"),)
                    if decision.historical else governed_lookup_health.lanes
                )
                health = RetrievalHealth((*lanes, *durable_lanes))
            candidates = tuple(getattr(result, "candidates", ()))
            if decision.intent == "grounded_followup_attribute":
                candidates = tuple(item for item in candidates if recall_anchor is not None
                    and (getattr(item, "canonical_record_id", ""),
                         getattr(item, "canonical_index", None),
                         getattr(item, "speaker_role", "")) in zip(
                             recall_anchor.canonical_record_ids, recall_anchor.canonical_indices,
                             recall_anchor.speaker_roles))
            # A historical question proves only that the question was asked.
            # It cannot establish ownership, identity, preference, a plan, or
            # a place proposition merely because it repeats the same nouns.
            admission = admit_memory_evidence(candidates, decision)
            admissible_candidates = admission.candidates
            insufficient_count = admission.insufficient_count
            design = compose_v2_replacement_context(
                ({"role": "user", "content": str(query_text)},),
                str(query_text), admissible_candidates,
                active_truth_scope_id=str(active_truth_scope_id),
                governed_facts=tuple(governed_facts),
                memory_query_decision=decision,
                lookup_health=health,
            )
            from aifren.runtime.config import RECENT_CONTEXT_MAX_MESSAGES
            recent_source = tuple(self.canonical_messages)[-RECENT_CONTEXT_MAX_MESSAGES:]
            if active_truth_scope is not None:
                from aifren.conversation.truth_scope import (
                    active_scope_from_provenance,
                    filter_scope_compatible_history,
                )
                recent_source = tuple(filter_scope_compatible_history(
                    recent_source,
                    active_scope_from_provenance(active_truth_scope),
                ))
            selected_recent = select_recent_messages(
                recent_source,
                self.recent_context_policy,
                maximum_messages=12,
                memory_query_decision=decision,
            )
            requirement = bind_memory_answer_source_containment(
                design.memory_answer_requirement, query_text, selected_recent,
                memory_query_decision=decision,
                current_authoritative_context=current_authoritative_context,
            )
            # The anchor is identity-only, valid for exactly the next turn,
            # and cannot chain from its own expansion.
            publication_anchor = None
            fallback_anchor = None
            if decision.intent != "grounded_followup_attribute" and (
                decision.applicable
                and requirement.evidence_state == "grounded_evidence"
            ):
                publication_anchor = historical_recall_anchor_from_candidates(
                    design.items,
                    self.character_id,
                    str(active_truth_scope_id),
                    generation,
                )
                fallback_anchor = historical_recall_anchor_from_candidates(
                    tuple(item for item in design.items
                          if item.canonical_record_id in requirement.fallback_evidence_ids),
                    self.character_id, str(active_truth_scope_id), generation,
                )
                if requirement.callback_contract is not None and requirement.callback_contract.triggered:
                    # Exact-source callback validation requires this specific
                    # proposition, even if other verification evidence exists.
                    publication_anchor = fallback_anchor
        except Exception as error:
            self._recall_anchor = None
            raise MemoryV2AuthorityUnavailable(
                "I can't check that memory right now. Please try again in a moment."
            ) from error
        context_block = "\n\n".join(
            value for value in (design.authority_block, design.historical_block)
            if value
        )
        return V2AuthorityTurn(
            design, context_block, requirement,
            round((time.perf_counter() - started) * 1000.0, 3),
            len(candidates), (admission.reason if admission.reason == "topic_reference_ambiguous"
                              else str(getattr(result, "abstention_reason", "") or "")),
            (
                "lookup_unavailable" if requirement.lookup_unavailable else _absence_kind(design, candidates)
                if requirement.evidence_state
                == "no_grounded_evidence" else "not_applicable"
            ),
            decision,
            len(selected_recent),
            sum(len(str(item.get("content", ""))) for item in selected_recent),
            insufficient_count,
            recall_anchor is not None,
            generation,
            publication_anchor,
            fallback_anchor,
        )

    def publish(self, turn: V2AuthorityTurn, *, source_fallback: bool = False) -> None:
        """Called only at the service's saved, accepted publication boundary."""
        anchor = turn.fallback_anchor if source_fallback else turn.publication_anchor
        if (turn.response_variant == self._turn_generation
                and anchor is not None
                and anchor.character_id == self.character_id
                and anchor.active_truth_scope_id == str(
                    self.store.active_truth_scope_id(self.character_id))):
            self._recall_anchor = anchor

    def invalidate_recall_anchor(self) -> None:
        self._recall_anchor = None
        self._turn_generation += 1  # Late publication cannot restore it.

    def close(self) -> None:
        self.invalidate_recall_anchor()
        close = getattr(getattr(self.recall, "semantic", None), "embedding_provider", None)
        close = getattr(close, "close", None)
        if callable(close):
            close()


__all__ = [
    "DevelopmentV2MemoryAuthority",
    "MemoryV2AuthorityUnavailable",
    "V2AuthorityTurn",
    "render_authoritative_no_evidence",
]
