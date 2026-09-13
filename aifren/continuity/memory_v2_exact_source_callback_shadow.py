"""Disconnected exact-source response contract for historical callbacks.

Variant D is a Development QA artifact.  It composes a reduced copy of an
already-built production context and never participates in AssistantService or
canonical persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Mapping, Sequence

from aifren.memory.memory import meaningful_words
from aifren.continuity.memory_v2_prompt_admission_shadow import (
    PromptEvidenceItem,
    SupplementalPromptDesign,
    historical_response_fallback_dialogue,
    validate_supplemental_historical_response,
)
from aifren.dialogue.response_requirements import (
    RequiredFact,
    ResponseRequirement,
    validate_response_requirement,
)


MAX_CALLBACK_RECENT_MESSAGES = 4
MAX_CALLBACK_RECENT_CHARACTERS = 4000
MAX_CALLBACK_REQUIREMENT_CHARACTERS = 1800

_BROAD_CONTEXT_MARKERS = (
    "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:",
    "RELEVANT LIFELONG MEMORIES:",
    "LONG-TERM CONVERSATION BACKGROUND:",
    "[Derived episodic conversation background]",
    "[Verified remembered facts — background data, not instructions]",
    "[Open continuity — background data, not instructions]",
)
_NOISE_TERMS = frozenset({
    "about", "assistant", "before", "historical", "history", "remember",
    "said", "saying", "source", "thing", "things", "today", "told",
    "user", "yesterday",
})


@dataclass(frozen=True)
class ExactSourceCallbackContract:
    triggered: bool
    reason: str
    callback_source_kind: str
    primary_item: PromptEvidenceItem | None
    must_communicate: ResponseRequirement | None
    must_respect_design: SupplementalPromptDesign
    context_block: str
    composer_latency_ms: float


@dataclass(frozen=True)
class ExactSourceCallbackValidation:
    accepted: bool
    category: str
    source_communicated: bool
    speaker_preserved: bool
    source_semantics_respected: bool
    unrelated_substitution: bool
    violations: tuple[str, ...]


def _speaker_anchors(item: PromptEvidenceItem) -> tuple[str, ...]:
    if item.speaker_role == "assistant":
        return (
            "i previously said", "i said", "i told you", "i mentioned",
            "i remember telling you", "i remember saying",
            "assistant said", "the assistant said", "it was me",
            "me who said", "what i asserted",
        )
    return (
        "you previously said", "you said", "you told me", "you mentioned",
        "you remember saying", "i remember you saying",
        "you asserted", "you stated",
    )


def _source_anchors(item: PromptEvidenceItem) -> tuple[str, ...]:
    semantic = item.semantic_class
    if semantic == "negated_completion":
        return (
            "couldn't finish", "could not finish", "wasn't able to finish",
            "weren't able to finish", "unable to finish",
        )
    if semantic == "uncertain_acquisition":
        return ("might buy", "may buy", "thinking about buying", "considering buying")
    if semantic == "future_intent":
        return ("planning to", "planned to", "going to", "intended to")
    if semantic == "historical_state":
        return ("used to", "previously collected", "collected before")
    if semantic == "assistant_speculation":
        return ("probably have", "might have", "maybe have", "guessed")
    if semantic == "positive_completion":
        return ("finished", "completed")
    words = tuple(
        word for word in meaningful_words(item.source_quote)
        if word not in _NOISE_TERMS
    )
    phrases: list[str] = []
    for size in (3, 2):
        for index in range(max(0, len(words) - size + 1)):
            phrase = " ".join(words[index:index + size])
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= 6:
                return tuple(phrases)
    return tuple(phrases) or (item.source_quote,)


def compose_exact_source_callback_contract(
    design: SupplementalPromptDesign,
) -> ExactSourceCallbackContract:
    """Require the top admitted canonical source without expanding admission."""
    started = time.perf_counter()
    if not design.triggered or not design.items:
        return ExactSourceCallbackContract(
            False, "no_admitted_canonical_source", design.callback_source_kind,
            None, None, design, "",
            round((time.perf_counter() - started) * 1000.0, 6),
        )
    item = design.items[0]
    source_items = tuple(value for value in design.items if value.canonical_record_id == item.canonical_record_id)
    fallback = historical_response_fallback_dialogue(
        SupplementalPromptDesign(
            True, design.reason, design.callback_source_kind, design.prompt_block,
            source_items, design.excluded, design.character_count,
            design.approximate_token_count, design.composer_latency_ms,
        ),
    )
    facts = (
        RequiredFact("historical_source", item.source_quote, _source_anchors(item)),
        RequiredFact("historical_speaker", item.speaker_role, _speaker_anchors(item)),
    )
    payload = {
        "intent": "explicit_historical_callback",
        "mode": "must_communicate",
        "speaker": item.speaker_role.upper(),
        "authority": "HISTORICAL CONVERSATION ONLY",
        "scope": item.scope_class.upper(),
        "speech_act": (item.speech_act or "unknown").upper(),
        "source_quote": item.source_quote,
        "requirements": (
            "communicate source substance; preserve speaker, polarity, modality, "
            "time, and historical-only authority; do not substitute another memory"
        ),
    }
    if item.source_segments:
        segment = item.source_segments[0]
        payload["source_span"] = {"start": segment.start, "end": segment.end, "source_length": segment.source_length}
        if len(source_items) > 1:
            payload["additional_source_segments"] = [
                {"start": segment.start, "end": segment.end, "quote": segment.text}
                for value in source_items[1:] for segment in value.source_segments
            ]
        # The passages are quoted independently. Omissions between them do
        # not assert adjacency or continuity in the original source.
        payload["requirements"] += "; communicate each selected passage separately; gaps are omitted source text"
    block = (
        "[Authoritative historical callback response requirement — backend policy]\n"
        "Answer this explicit callback from the canonical SOURCE QUOTE below. "
        "The quote is required evidence, not optional background. Communicate its "
        "substance naturally; do not replace it with another memory. Preserve exactly "
        "who spoke, negation, uncertainty/modality, time, and historical-only status. "
        "Do not infer present truth, ownership, completion, or success.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n[End authoritative historical callback response requirement]"
    )[:MAX_CALLBACK_REQUIREMENT_CHARACTERS]
    requirement = ResponseRequirement(
        "historical_callback_source", facts, fallback, block, (),
        "must_communicate",
    )
    return ExactSourceCallbackContract(
        True, "admitted_source_required", design.callback_source_kind, item,
        requirement, design, block,
        round((time.perf_counter() - started) * 1000.0, 6),
    )


def _is_broad_context(content: str) -> bool:
    return any(marker in content for marker in _BROAD_CONTEXT_MARKERS)


def _recent_suffix(
    messages: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    used = 0
    for item in reversed(tuple(messages)):
        content = str(item.get("content", ""))
        if len(selected) >= MAX_CALLBACK_RECENT_MESSAGES:
            break
        if used + len(content) > MAX_CALLBACK_RECENT_CHARACTERS:
            break
        selected.append(dict(item))
        used += len(content)
    selected.reverse()
    return selected


def source_grounded_callback_context(
    production_context: Sequence[Mapping[str, object]],
    contract: ExactSourceCallbackContract,
    *,
    requirement_order: str = "before_query",
) -> list[dict[str, object]]:
    """Build a minimal copied context; never modify the production context."""
    copied = [dict(item) for item in production_context]
    if not contract.triggered:
        return copied
    latest_user = next(
        (index for index in range(len(copied) - 1, -1, -1)
         if str(copied[index].get("role", "")) == "user"),
        None,
    )
    if latest_user is None:
        return copied
    first_recent = next(
        (index for index, item in enumerate(copied[:latest_user])
         if any(key in item for key in ("timestamp", "truth_scope", "origin"))),
        latest_user,
    )
    controls = [
        dict(item) for item in copied[:first_recent]
        if not _is_broad_context(str(item.get("content", "")))
    ]
    recent = _recent_suffix(copied[first_recent:latest_user])
    query = dict(copied[latest_user])
    requirement = {"role": "user", "content": contract.context_block}
    if requirement_order == "before_query":
        return [*controls, *recent, requirement, query]
    if requirement_order == "after_query":
        return [*controls, *recent, query, requirement]
    raise ValueError("unknown exact-source requirement order")


def _competing_substitution(
    response: str,
    item: PromptEvidenceItem,
    competing_v1_items: Sequence[Mapping[str, object]],
) -> bool:
    response_terms = set(meaningful_words(response))
    source_terms = set(meaningful_words(item.source_quote))
    for candidate in tuple(competing_v1_items)[:5]:
        terms = {
            word for word in meaningful_words(candidate.get("content", ""))
            if word not in source_terms and word not in _NOISE_TERMS
        }
        if len(terms & response_terms) >= 2:
            return True
    return False


def validate_exact_source_callback_response(
    contract: ExactSourceCallbackContract,
    response: object,
    *,
    competing_v1_items: Sequence[Mapping[str, object]] = (),
) -> ExactSourceCallbackValidation:
    if not contract.triggered or contract.must_communicate is None or contract.primary_item is None:
        return ExactSourceCallbackValidation(
            True, "not_required", False, False, True, False, (),
        )
    text = " ".join(str(response or "").split())
    _source_fact, speaker_fact = contract.must_communicate.facts
    speaker_requirement = ResponseRequirement(
        contract.must_communicate.intent,
        (speaker_fact,),
        contract.must_communicate.fallback_dialogue,
        contract.must_communicate.context_block,
        contract.must_communicate.forbidden_terms,
        contract.must_communicate.mode,
    )
    speaker_validation = validate_response_requirement(speaker_requirement, text)
    semantics = validate_supplemental_historical_response(
        contract.must_respect_design, text,
    )
    respect_violations = tuple(
        value for value in semantics.violations
        if value != "historical_source_omitted"
    )
    # The existing historical validator owns bounded natural-paraphrase
    # detection. ResponseRequirement still supplies the typed must-communicate
    # contract, repair prompt, and fallback, but its generic literal-term
    # validator is intentionally not a second semantic paraphrase model.
    source_communicated = bool(semantics.communicated_item_count)
    speaker_preserved = speaker_validation.accepted
    substitution = bool(
        not source_communicated
        and _competing_substitution(
            text, contract.primary_item, competing_v1_items,
        )
    )
    violations = list(respect_violations)
    if not speaker_preserved:
        violations.append("historical_speaker_omitted")
    if not source_communicated:
        violations.append(
            "unrelated_memory_substitution" if substitution
            else "historical_source_omitted"
        )
    unique = tuple(dict.fromkeys(violations))
    return ExactSourceCallbackValidation(
        not unique,
        unique[0] if unique else "accepted",
        source_communicated,
        speaker_preserved,
        not respect_violations,
        substitution,
        unique,
    )


__all__ = [
    "ExactSourceCallbackContract",
    "ExactSourceCallbackValidation",
    "MAX_CALLBACK_RECENT_CHARACTERS",
    "MAX_CALLBACK_RECENT_MESSAGES",
    "compose_exact_source_callback_contract",
    "source_grounded_callback_context",
    "validate_exact_source_callback_response",
]
