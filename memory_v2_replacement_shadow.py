"""Disconnected candidate context for replacing prompt-facing Memory V1.

This module owns the bounded Memory V2 replacement context first developed as
a disconnected evaluation artifact.  The guarded Development-only V2
authority owner now reuses it; normal production remains on Memory V1.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Mapping, Sequence

from benchmarks.memory_v2.models import HistoricalOrderWitness, HistoricalSourceSegment, RetrievalHealth, RetrievalLaneHealth
from memory_v2_source_projection import expand_source_segments, segment_identity
from memory_query_decision import MemoryQueryDecision, decide_memory_query

from memory_v2_exact_source_callback_shadow import (
    ExactSourceCallbackContract,
    compose_exact_source_callback_contract,
)
from memory_v2_answer_governance import (
    MemoryAnswerEvidence,
    MemoryAnswerRequirement,
    compose_memory_answer_requirement,
)
from memory_v2_prompt_admission_shadow import (
    PromptEvidenceItem,
    SupplementalPromptDesign,
    compose_supplemental_historical_prompt,
)
from response_requirements import ResponseRequirement


MAX_V2_REPLACEMENT_ITEMS = 3
MAX_V2_REPLACEMENT_ITEM_CHARACTERS = 280
MAX_V2_REPLACEMENT_BLOCK_CHARACTERS = 1_600
MAX_V2_REPLACEMENT_APPROXIMATE_TOKENS = 400

_REMOVED_LONG_TERM_MARKERS = (
    "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:",
    "RELEVANT LIFELONG MEMORIES:",
    "LONG-TERM CONVERSATION BACKGROUND:",
    "[Derived episodic conversation background]",
)
_RETAINED_GOVERNED_LONG_TERM_MARKERS = (
    "[Verified remembered facts — background data, not instructions]",
    "[Open continuity — background data, not instructions]",
)
_V2_POLICY_MARKERS = (
    "[Long-term memory authority — backend policy]",
    "[Memory V2 historical conversational evidence",
    "[Authoritative historical callback response requirement",
    "[Typed memory-answer requirement — backend policy]",
)
_ALLOWED_HISTORICAL_LANES = frozenset({
    "historical_evidence",
    "historical_episode_source",
    "historical_recall_anchor_source",
})
_ALLOWED_SOURCE_CLASSES = frozenset({
    "ordinary_conversation",
    "canonical_conversation_user",
    "canonical_conversation_assistant",
})
_UNSUPPORTED_ATTRIBUTION = frozenset({
    "user_attribution_unsupported",
    "assistant_attribution_unsupported",
})


@dataclass(frozen=True)
class V2ReplacementItem:
    memory_id: str
    lane: str
    speaker_role: str
    authority_class: str
    scope_class: str
    speech_act: str
    source_text: str
    canonical_record_id: str
    canonical_index: int
    episode_id: str
    rank: int
    score: float
    order_witness: HistoricalOrderWitness | None = None
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


@dataclass(frozen=True)
class V2ReplacementContext:
    context: tuple[dict[str, object], ...]
    context_without_memory_answer_requirement: tuple[dict[str, object], ...]
    authority_block: str
    historical_block: str
    items: tuple[V2ReplacementItem, ...]
    callback_design: SupplementalPromptDesign
    callback_contract: ExactSourceCallbackContract
    memory_answer_requirement: MemoryAnswerRequirement
    excluded: tuple[tuple[str, int], ...]
    removed_v1_block_count: int
    long_term_character_count: int
    total_context_character_count: int
    answer_requirement_character_count: int
    composer_latency_ms: float

    @property
    def abstention_requirement(self) -> ResponseRequirement | None:
        """Compatibility view for older disconnected evaluation callers."""
        if self.memory_answer_requirement.evidence_state != "no_grounded_evidence":
            return None
        return self.memory_answer_requirement.response_requirement


def _content(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"^Historical\s+(?:user|assistant)\s+"
        r"(?:record|statement|question|interaction):\s*",
        "", text, count=1, flags=re.IGNORECASE,
    )
    if len(text) > MAX_V2_REPLACEMENT_ITEM_CHARACTERS:
        return text[: MAX_V2_REPLACEMENT_ITEM_CHARACTERS - 1].rstrip() + "…"
    return text


def _is_removed_long_term(content: str) -> bool:
    return any(marker in content for marker in _REMOVED_LONG_TERM_MARKERS)


def _exact_anchor_source_provenance(candidate: object) -> bool:
    """Recognize the exact-source resolver's bounded canonical proof shape.

    The upstream owner rechecks the canonical hash, speaker and scope. This
    boundary requires its matching source reference and passage projection;
    an arbitrary anchor label cannot substitute for an episode identity.
    """
    from memory_v2_source_projection import MAX_PASSAGES, MAX_PASSAGE_CHARACTERS
    record_id = str(getattr(candidate, "canonical_record_id", ""))
    index = getattr(candidate, "canonical_index", None)
    evidence = getattr(candidate, "evidence", ())
    segments = getattr(candidate, "source_segments", ())
    if (getattr(candidate, "attribution_state", "") != "canonical_exact_source_anchor"
            or not record_id or isinstance(index, bool) or not isinstance(index, int) or index < 0
            or getattr(candidate, "associated_from", "") != record_id
            or not isinstance(evidence, tuple) or not isinstance(segments, tuple)
            or len(evidence) != 1 or not 1 <= len(segments) <= MAX_PASSAGES
            or not all(isinstance(segment, HistoricalSourceSegment) for segment in segments)
            or sum(len(segment.text) for segment in segments) > MAX_PASSAGE_CHARACTERS):
        return False
    proof = evidence[0]
    return (getattr(proof, "source_type", "") == "ordinary_conversation"
            and getattr(proof, "source_id", "") == record_id
            and getattr(proof, "source_reference", "") == f"canonical_index:{index}"
            and getattr(proof, "sequence", None) == index + 1)


def _historical_exclusion(
    candidate: object, active_scope_id: str, *, allow_recall_anchor: bool,
) -> str:
    lane = str(getattr(candidate, "lane", ""))
    if lane == "historical_recall_anchor_source" and not allow_recall_anchor:
        return "recall_anchor_not_admitted"
    if lane not in _ALLOWED_HISTORICAL_LANES:
        return "lane_not_prompt_authoritative"
    if str(getattr(candidate, "source_class", "")) not in _ALLOWED_SOURCE_CLASSES:
        return "source_class_not_admissible"
    if str(getattr(candidate, "speaker_role", "")) not in {"user", "assistant"}:
        return "speaker_ownership_missing"
    if str(getattr(candidate, "attribution_state", "")) in _UNSUPPORTED_ATTRIBUTION:
        return "unsupported_summary_attribution"
    if not str(getattr(candidate, "status", "")).startswith("historical_"):
        return "not_historical_only"
    scope = str(getattr(candidate, "scope_state", ""))
    if scope not in {"unknown_scope", "real_world", "scenario"}:
        return "scope_state_not_admissible"
    if scope != "unknown_scope" and (
        not active_scope_id
        or str(getattr(candidate, "truth_scope_id", "")) != active_scope_id
    ):
        return "truth_scope_mismatch"
    if not str(getattr(candidate, "canonical_record_id", "")):
        return "canonical_source_identity_missing"
    if getattr(candidate, "canonical_index", None) is None:
        return "canonical_source_order_missing"
    if not str(getattr(candidate, "episode_id", "")):
        if lane == "historical_episode_source":
            return "validated_episode_identity_missing"
        if lane == "historical_recall_anchor_source" and not _exact_anchor_source_provenance(candidate):
            return "validated_anchor_source_identity_missing"
    if not _content(getattr(candidate, "content", "")):
        return "source_content_missing"
    return ""


def _item(candidate: object, rank: int) -> V2ReplacementItem:
    return V2ReplacementItem(
        memory_id=str(getattr(candidate, "memory_id", "")),
        lane=str(getattr(candidate, "lane", "")),
        speaker_role=str(getattr(candidate, "speaker_role", "")),
        authority_class="historical_conversation_only",
        scope_class=str(getattr(candidate, "scope_state", "")),
        speech_act=str(getattr(candidate, "speech_act", "")),
        source_text=_content(getattr(candidate, "content", "")),
        canonical_record_id=str(getattr(candidate, "canonical_record_id", "")),
        canonical_index=int(getattr(candidate, "canonical_index")),
        episode_id=str(getattr(candidate, "episode_id", "")),
        rank=int(rank),
        score=float(getattr(candidate, "score", 0.0)),
        order_witness=getattr(candidate, "order_witness", None),
        source_segments=getattr(candidate, "source_segments", ()),
    )


def _historical_line(item: V2ReplacementItem) -> str:
    owner = "USER" if item.speaker_role == "user" else "ASSISTANT"
    action = "ASKED" if item.speech_act == "question" else "SAID"
    scope = {
        "real_world": "KNOWN REAL-WORLD SCOPE",
        "scenario": "KNOWN SCENARIO SCOPE",
        "unknown_scope": "HISTORICAL SCOPE UNCERTAIN",
    }.get(item.scope_class, "HISTORICAL SCOPE UNCERTAIN")
    source_kind = (
        "CANONICAL SOURCE REFINED FROM VALIDATED EPISODE"
        if item.lane == "historical_episode_source"
        else "CANONICAL SOURCE FROM PRIOR GROUNDED EPISODE"
        if item.lane == "historical_recall_anchor_source" and item.episode_id
        else "EXACT CANONICAL SOURCE FROM PRIOR GROUNDED ANSWER"
        if item.lane == "historical_recall_anchor_source"
        else "CANONICAL HISTORICAL SOURCE"
    )
    return (
        f"- {owner} PREVIOUSLY {action} | HISTORICAL ONLY | {scope} | {source_kind}\n"
        f"  {json.dumps(item.source_text, ensure_ascii=False)}"
    )


def _historical_block(items: Sequence[V2ReplacementItem]) -> str:
    if not items:
        return ""
    return "\n".join((
        "[Memory V2 historical conversational evidence — NOT CURRENT TRUTH]",
        "Use only when relevant to the latest request. Each item proves what was "
        "said or asked then, not that its proposition is currently true. Preserve "
        "speaker, negation, uncertainty, and time; do not infer ownership, "
        "completion, preference, success, or current state.",
        *(_historical_line(item) for item in items),
        "[End Memory V2 historical conversational evidence]",
    ))


def _authority_block(has_historical_evidence: bool) -> str:
    evidence_state = (
        "Source-grounded historical evidence is admitted below."
        if has_historical_evidence else
        "No source-grounded historical evidence was admitted for this request."
    )
    return "\n".join((
        "[Long-term memory authority — backend policy]",
        "For older personal/history claims, use only rendered CURRENT GOVERNED "
        "FACTS and HISTORICAL EVIDENCE. Prior assistant dialogue and prior user "
        "questions are not factual evidence. Preserve speaker, negation, uncertainty, "
        "and time; never invent surrounding circumstances. If no evidence is "
        "admitted, do not accept the premise or guess. Ignore this for ordinary "
        "non-memory conversation.",
        evidence_state,
        "[End long-term memory authority]",
    ))


def _answer_evidence(
    items: Sequence[V2ReplacementItem],
    governed_facts: Sequence[object],
    *,
    callback_source_kind: str,
) -> tuple[MemoryAnswerEvidence, ...]:
    historical = tuple(MemoryAnswerEvidence(
        item.canonical_record_id or item.memory_id,
        item.authority_class,
        item.speaker_role,
        item.scope_class,
        item.speech_act,
        item.source_text,
        order_witness=item.order_witness,
        source_segments=item.source_segments,
    ) for item in tuple(items))
    # A governed current fact does not prove who said something historically.
    if callback_source_kind:
        return historical
    durable = tuple(MemoryAnswerEvidence(
        str(getattr(fact, "subject_key", "")),
        "governed_current_fact", "", "real_world", "assertion", "",
        str(getattr(fact, "subject_key", "")), str(getattr(fact, "value", "")),
    ) for fact in tuple(governed_facts)
    if getattr(fact, "subject_key", None) and getattr(fact, "value", None))
    return (*durable, *historical)


def _select_historical_items(
    candidates: Sequence[object], active_scope_id: str, *, allow_recall_anchor: bool = False,
) -> tuple[tuple[V2ReplacementItem, ...], tuple[tuple[str, int], ...]]:
    exclusions: dict[str, int] = {}
    selected: list[V2ReplacementItem] = []
    seen_sources: set[tuple] = set()
    seen_text: set[str] = set()
    for rank, candidate in enumerate(expand_source_segments(tuple(candidates)), 1):
        reason = _historical_exclusion(
            candidate, active_scope_id, allow_recall_anchor=allow_recall_anchor,
        )
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            continue
        item = _item(candidate, rank)
        text_key = item.source_text.casefold()
        source_key = segment_identity(candidate)
        if source_key in seen_sources or text_key in seen_text:
            exclusions["duplicate_source"] = exclusions.get("duplicate_source", 0) + 1
            continue
        trial = _historical_block((*selected, item))
        if (
            len(trial) > MAX_V2_REPLACEMENT_BLOCK_CHARACTERS
            or (len(trial) + 3) // 4 > MAX_V2_REPLACEMENT_APPROXIMATE_TOKENS
        ):
            exclusions["historical_context_budget"] = (
                exclusions.get("historical_context_budget", 0) + 1
            )
            continue
        selected.append(item)
        seen_sources.add(source_key)
        seen_text.add(text_key)
        if len(selected) >= MAX_V2_REPLACEMENT_ITEMS:
            break
    for candidate in candidates:
        segments = getattr(candidate, "source_segments", ())
        if len(segments) < 2:
            continue
        source_id = str(getattr(candidate, "canonical_record_id", ""))
        admitted = sum(item.canonical_record_id == source_id for item in selected)
        if admitted and admitted != len(segments):
            return (), (("partial_source_projection", 1),)
    return tuple(selected), tuple(sorted(exclusions.items()))


def _copied_without_v1(
    production_context: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], int, int]:
    copied = [dict(item) for item in tuple(production_context)]
    latest_user = next(
        (
            index for index in range(len(copied) - 1, -1, -1)
            if str(copied[index].get("role", "")) == "user"
        ),
        len(copied),
    )
    retained: list[dict[str, object]] = []
    removed = 0
    query_index = -1
    for index, message in enumerate(copied):
        if _is_removed_long_term(str(message.get("content", ""))):
            removed += 1
            continue
        retained.append(message)
        if index == latest_user:
            query_index = len(retained) - 1
    return retained, removed, query_index


def compose_v2_replacement_context(
    production_context: Sequence[Mapping[str, object]],
    query_text: str,
    candidates: Sequence[object],
    *,
    active_truth_scope_id: str = "",
    governed_facts: Sequence[object] = (),
    memory_query_decision: MemoryQueryDecision | None = None,
    lookup_health: RetrievalHealth | None = None,
) -> V2ReplacementContext:
    """Build a copied V2-only long-term context for disconnected QA."""
    started = time.perf_counter()
    decision = memory_query_decision or decide_memory_query(query_text)
    callback_design = compose_supplemental_historical_prompt(
        str(query_text), tuple(candidates),
        active_truth_scope_id=str(active_truth_scope_id),
        memory_query_decision=decision,
    )
    callback_contract = compose_exact_source_callback_contract(callback_design)
    retained, removed, query_index = _copied_without_v1(production_context)

    if callback_contract.triggered:
        # Explicit source callbacks retain Variant D's typed must-communicate
        # contract.  Do not also inject a second differently worded copy.
        historical_block = callback_contract.context_block
        items = tuple(
            V2ReplacementItem(
                item.memory_id, item.lane, item.speaker_role,
                item.authority_class, item.scope_class, item.speech_act,
                item.source_quote, item.canonical_record_id,
                item.canonical_index, item.episode_id, item.rank, item.score,
                item.order_witness,
                item.source_segments,
            )
            for item in callback_design.items
        )
        excluded = callback_design.excluded
    elif callback_design.callback_source_kind:
        # An explicit callback whose requested source owner has no eligible
        # canonical evidence must abstain.  It must not fall through to the
        # broader historical renderer and admit the wrong speaker.
        items = ()
        excluded = callback_design.excluded
        historical_block = ""
    else:
        items, excluded = _select_historical_items(
            candidates, str(active_truth_scope_id),
            allow_recall_anchor=decision.intent == "grounded_followup_attribute",
        )
        historical_block = _historical_block(items)

    authority_block = _authority_block(bool(items))
    if callback_design.reason == "prompt_budget_exceeded" or any(
        reason == "partial_source_projection" for reason, _count in excluded
    ):
        lookup_health = RetrievalHealth((*(lookup_health.lanes if lookup_health else ()),
            RetrievalLaneHealth("claims", "incomplete", "source", "candidate_bound")))
    answer_requirement = compose_memory_answer_requirement(
        str(query_text),
        _answer_evidence(
            items, governed_facts,
            callback_source_kind=callback_design.callback_source_kind,
        ),
        callback_contract=callback_contract,
        memory_query_decision=decision,
        lookup_health=lookup_health,
    )
    insertion = query_index if query_index >= 0 else len(retained)
    retained.insert(insertion, {"role": "user", "content": authority_block})
    next_insertion = insertion + 1
    if historical_block:
        retained.insert(
            next_insertion,
            {"role": "user", "content": historical_block},
        )
        next_insertion += 1
    context_without_requirement = tuple(dict(item) for item in retained)

    total_chars = sum(len(str(item.get("content", ""))) for item in retained)
    governed_chars = sum(
        len(str(item.get("content", "")))
        for item in retained
        if any(
            marker in str(item.get("content", ""))
            for marker in _RETAINED_GOVERNED_LONG_TERM_MARKERS
        )
    )
    return V2ReplacementContext(
        tuple(retained), context_without_requirement,
        authority_block, historical_block, items, callback_design,
        callback_contract, answer_requirement, tuple(excluded), removed,
        len(authority_block) + len(historical_block)
        + (len(answer_requirement.context_block) if answer_requirement.triggered else 0)
        + governed_chars,
        total_chars,
        len(answer_requirement.context_block) if answer_requirement.triggered else 0,
        round((time.perf_counter() - started) * 1000.0, 6),
    )


def bounded_recent_replacement_context(
    design: V2ReplacementContext,
    *,
    max_recent_messages: int,
    max_recent_characters: int,
) -> tuple[dict[str, object], ...]:
    """Bound only raw recent dialogue in one copied disconnected context."""
    messages = tuple(dict(item) for item in design.context)
    latest_user = next((
        index for index in range(len(messages) - 1, -1, -1)
        if str(messages[index].get("role", "")) == "user"
        and not any(
            marker in str(messages[index].get("content", ""))
            for marker in (*_RETAINED_GOVERNED_LONG_TERM_MARKERS, *_V2_POLICY_MARKERS)
        )
    ), len(messages) - 1)
    recent_indices = [
        index for index, item in enumerate(messages[:latest_user])
        if any(key in item for key in ("timestamp", "origin", "truth_scope"))
        and not any(
            marker in str(item.get("content", ""))
            for marker in (*_RETAINED_GOVERNED_LONG_TERM_MARKERS, *_V2_POLICY_MARKERS)
        )
    ]
    selected: set[int] = set()
    used = 0
    for index in reversed(recent_indices):
        content = str(messages[index].get("content", ""))
        if len(selected) >= max(0, int(max_recent_messages)):
            break
        if used + len(content) > max(0, int(max_recent_characters)):
            break
        selected.add(index)
        used += len(content)
    return tuple(
        item for index, item in enumerate(messages)
        if index not in recent_indices or index in selected
    )


__all__ = [
    "MAX_V2_REPLACEMENT_APPROXIMATE_TOKENS",
    "MAX_V2_REPLACEMENT_BLOCK_CHARACTERS",
    "MAX_V2_REPLACEMENT_ITEMS",
    "V2ReplacementContext",
    "V2ReplacementItem",
    "bounded_recent_replacement_context",
    "compose_v2_replacement_context",
]
