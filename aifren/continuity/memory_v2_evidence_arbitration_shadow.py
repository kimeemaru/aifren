"""Disconnected V1/V2 evidence arbitration for explicit historical callbacks.

This module changes only copied counterfactual contexts used by Development QA.
It never mutates Memory V1, canonical conversation, Memory V2, or production
context assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Mapping, Sequence

from aifren.memory.memory import meaningful_words, normalize_text
from aifren.continuity.memory_v2_prompt_admission_shadow import SupplementalPromptDesign


MAX_ARBITRATED_V1_ITEMS = 5

_MEMORY_START = "RELEVANT LIFELONG MEMORIES:\n"
_MEMORY_END = "\nEND AUTHORITATIVE LIFELONG MEMORIES."
_QUERYISH_CATEGORY = re.compile(r"(?:query|inquiry|question|request)", re.I)
_QUERYISH_SOURCE = re.compile(r"(?:query|inquiry|question|request)", re.I)
_QUERYISH_CONTENT = re.compile(
    r"\b(?:user|they)\s+(?:asked|asks|is\s+interested\s+in\s+what|"
    r"wants\s+to\s+know|wanted\s+to\s+know)\b",
    re.I,
)
_USER_FACT = re.compile(r"\b(?:the\s+)?user\b|\byou(?:r|'re|\s+are)?\b", re.I)
_NEGATION = re.compile(
    r"\b(?:not|never|no|couldn['’]?t|didn['’]?t|can['’]?t|unable)\b",
    re.I,
)
_UNCERTAINTY = re.compile(
    r"\b(?:might|may|maybe|perhaps|possibly|probably|think|guess|uncertain)\b",
    re.I,
)
_FUTURE = re.compile(
    r"\b(?:plan|planning|intend|intending|tomorrow|later|going\s+to)\b",
    re.I,
)
_HISTORICAL = re.compile(
    r"\b(?:used\s+to|formerly|previously|back\s+then|at\s+the\s+time)\b",
    re.I,
)
_COMPLETION = re.compile(
    r"\b(?:successfully|finish(?:ed)?|complete(?:d)?|accomplished|done)\b",
    re.I,
)
_ACQUISITION_OR_OWNERSHIP = re.compile(
    r"\b(?:bought|purchased|owns?|owned|has|have|collection)\b",
    re.I,
)
_CURRENT_STATE = re.compile(
    r"\b(?:currently|still|now|owns?|has|have|collects?|likes?|loves?|"
    r"enjoys?|favorite|favourite|prefers?)\b",
    re.I,
)
_ASSERTED_USER_OWNERSHIP = re.compile(
    r"\b(?:the\s+)?user\b.{0,55}\b(?:owns?|owned|has|have|collection)\b|"
    r"\byou\b.{0,55}\b(?:owns?|owned|have|collection)\b",
    re.I,
)
_TOPIC_NOISE = frozenset({
    "assistant", "historical", "history", "previously", "remember", "recall",
    "said", "saying", "tell", "telling", "talk", "talked", "thing", "things",
    "before", "today", "tomorrow", "yesterday", "couldn", "didn", "might",
    "probably", "planning", "finished", "finish", "completed", "complete",
})


@dataclass(frozen=True)
class V1EvidenceDecision:
    memory_id: str
    category: str
    classification: str
    reason: str
    retained_c1: bool
    retained_c2: bool


@dataclass(frozen=True)
class EvidenceArbitration:
    applied: bool
    reason: str
    callback_source_kind: str
    decisions: tuple[V1EvidenceDecision, ...]
    c1_items: tuple[dict[str, object], ...]
    c2_items: tuple[dict[str, object], ...]
    priority_block: str
    latency_ms: float


def _record_content(item: Mapping[str, object]) -> str:
    return " ".join(str(item.get("content", "")).split())


def _topic_terms(text: object) -> set[str]:
    return {
        word for word in meaningful_words(str(text or ""))
        if word not in _TOPIC_NOISE
    }


def _source_terms(design: SupplementalPromptDesign) -> set[str]:
    terms: set[str] = set()
    for item in design.items:
        terms.update(_topic_terms(item.source_quote))
    return terms


def _query_echo(
    item: Mapping[str, object], query_text: str,
) -> bool:
    category = str(item.get("category", ""))
    provenance = item.get("provenance")
    source = (
        str(provenance.get("source", ""))
        if isinstance(provenance, Mapping) else ""
    )
    content = _record_content(item)
    if _QUERYISH_CATEGORY.search(category) or _QUERYISH_SOURCE.search(source):
        return True
    if content.endswith("?") or _QUERYISH_CONTENT.search(content):
        return True
    query_terms = _topic_terms(query_text)
    content_terms = _topic_terms(content) | _topic_terms(category)
    return bool(
        query_terms
        and len(query_terms & content_terms) >= max(2, len(query_terms) - 1)
        and re.search(r"\b(?:remember|recall|what|which|asked|question)\b", content, re.I)
    )


def _semantic_conflict(
    content: str,
    design: SupplementalPromptDesign,
) -> tuple[str, str] | None:
    semantics = {item.semantic_class for item in design.items}
    content_negated = bool(_NEGATION.search(content))
    content_uncertain = bool(_UNCERTAINTY.search(content))
    content_future = bool(_FUTURE.search(content))
    content_historical = bool(_HISTORICAL.search(content))

    if "negated_completion" in semantics and _COMPLETION.search(content) and not content_negated:
        return "polarity_conflict", "affirmative completion contradicts negated canonical source"
    if "positive_completion" in semantics and _COMPLETION.search(content) and content_negated:
        return "polarity_conflict", "negated completion contradicts affirmative canonical source"
    if (
        "uncertain_acquisition" in semantics
        and _ACQUISITION_OR_OWNERSHIP.search(content)
        and not content_uncertain
        and not content_negated
    ):
        return "modality_conflict", "certain acquisition or ownership strengthens uncertain source"
    if (
        "future_intent" in semantics
        and _COMPLETION.search(content)
        and not content_future
        and not content_uncertain
    ):
        return "temporal_current_state_conflict", "completed action strengthens future intention"
    if (
        "historical_state" in semantics
        and _CURRENT_STATE.search(content)
        and not content_historical
        and not content_negated
    ):
        return "temporal_current_state_conflict", "current state strengthens historical-only source"
    if (
        "assistant_speculation" in semantics
        and _ASSERTED_USER_OWNERSHIP.search(content)
        and not content_uncertain
    ):
        return "unsupported_strengthening", "derivative ownership strengthens assistant speculation"
    return None


def classify_v1_evidence(
    item: Mapping[str, object],
    query_text: str,
    design: SupplementalPromptDesign,
) -> V1EvidenceDecision:
    """Classify one exact selected V1 item without modifying its authority."""
    memory_id = str(item.get("id", ""))
    category = str(item.get("category", "unknown"))[:64]
    content = _record_content(item)
    if _query_echo(item, query_text):
        return V1EvidenceDecision(
            memory_id, category, "query_inquiry_echo",
            "selected V1 item restates a query or inquiry rather than historical evidence",
            False, False,
        )

    source_terms = _source_terms(design)
    content_terms = _topic_terms(content) | _topic_terms(category)
    if not content_terms:
        return V1EvidenceDecision(
            memory_id, category, "cannot_determine_safely",
            "insufficient structured content for deterministic comparison",
            True, False,
        )
    related = bool(source_terms & content_terms)
    if design.callback_source_kind == "assistant" and related and _USER_FACT.search(content):
        return V1EvidenceDecision(
            memory_id, category, "attribution_conflict",
            "user-fact V1 item cannot establish what the assistant previously said",
            False, False,
        )

    conflict = _semantic_conflict(content, design)
    if related and conflict is not None:
        classification, reason = conflict
        return V1EvidenceDecision(
            memory_id, category, classification, reason, False, False,
        )

    normalized = normalize_text(content)
    source_normalized = {
        normalize_text(item.source_quote) for item in design.items
    }
    if normalized and normalized in source_normalized:
        return V1EvidenceDecision(
            memory_id, category, "redundant", "duplicates exact canonical evidence",
            True, False,
        )
    if not related:
        return V1EvidenceDecision(
            memory_id, category, "unrelated", "no concrete source-topic overlap",
            True, False,
        )
    return V1EvidenceDecision(
        memory_id, category, "compatible_supportive",
        "shares canonical source subject without a deterministic semantic conflict",
        True, True,
    )


def arbitrate_historical_callback_evidence(
    query_text: str,
    v1_items: Sequence[Mapping[str, object]],
    design: SupplementalPromptDesign,
) -> EvidenceArbitration:
    """Build bounded C1/C2 V1 selections for a disconnected callback experiment."""
    started = time.perf_counter()
    if not design.triggered or design.callback_source_kind not in {"user", "assistant", "shared"}:
        return EvidenceArbitration(
            False, "ineligible_callback", design.callback_source_kind, (), (), (), "",
            round((time.perf_counter() - started) * 1000.0, 6),
        )
    bounded = tuple(dict(item) for item in tuple(v1_items)[:MAX_ARBITRATED_V1_ITEMS])
    decisions = tuple(
        classify_v1_evidence(item, query_text, design) for item in bounded
    )
    c1 = tuple(
        item for item, decision in zip(bounded, decisions) if decision.retained_c1
    )
    c2 = tuple(
        item for item, decision in zip(bounded, decisions) if decision.retained_c2
    )
    priority_block = (
        "[Historical callback source precedence]\n"
        "For this explicit callback only, exact canonical SOURCE QUOTE evidence "
        "below overrides derivative lifelong-memory statements about what was "
        "said. Compatible lifelong memories are background only.\n"
        + design.prompt_block
        + "\n[End historical callback source precedence]"
    )
    return EvidenceArbitration(
        True, "explicit_historical_callback", design.callback_source_kind,
        decisions, c1, c2, priority_block,
        round((time.perf_counter() - started) * 1000.0, 6),
    )


def _replace_memory_block(
    context: Sequence[Mapping[str, object]],
    retained: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    copied = [dict(item) for item in context]
    for index, message in enumerate(copied):
        content = str(message.get("content", ""))
        if _MEMORY_START not in content or _MEMORY_END not in content:
            continue
        if not retained:
            del copied[index]
            return copied
        prefix, remainder = content.split(_MEMORY_START, 1)
        _, suffix = remainder.split(_MEMORY_END, 1)
        lines = "".join(
            f"- [{str(item.get('category', 'unknown'))}] "
            f"{_record_content(item)}\n"
            for item in retained
        )
        replacement = dict(message)
        replacement["content"] = prefix + _MEMORY_START + lines + _MEMORY_END + suffix
        copied[index] = replacement
        return copied
    return copied


def _insert_before_latest_user(
    context: Sequence[Mapping[str, object]], block: str,
) -> list[dict[str, object]]:
    copied = [dict(item) for item in context]
    insertion = len(copied)
    for index in range(len(copied) - 1, -1, -1):
        if str(copied[index].get("role", "")) == "user":
            insertion = index
            break
    copied.insert(insertion, {"role": "user", "content": str(block)})
    return copied


def arbitrated_counterfactual_context(
    production_context: Sequence[Mapping[str, object]],
    arbitration: EvidenceArbitration,
    design: SupplementalPromptDesign,
    *,
    variant: str,
) -> list[dict[str, object]]:
    """Return C1 or C2 while leaving the caller's production context untouched."""
    if not arbitration.applied:
        return [dict(item) for item in production_context]
    if variant == "c1_conflicts_removed":
        retained = arbitration.c1_items
        block = design.prompt_block
    elif variant == "c2_canonical_priority":
        retained = arbitration.c2_items
        block = arbitration.priority_block
    else:
        raise ValueError("unknown evidence-arbitration variant")
    filtered = _replace_memory_block(production_context, retained)
    return _insert_before_latest_user(filtered, block)


__all__ = [
    "EvidenceArbitration",
    "MAX_ARBITRATED_V1_ITEMS",
    "V1EvidenceDecision",
    "arbitrate_historical_callback_evidence",
    "arbitrated_counterfactual_context",
    "classify_v1_evidence",
]
