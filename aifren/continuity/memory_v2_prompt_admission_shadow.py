"""Disconnected design contract for a future supplemental historical prompt slot.

This module renders counterfactual Development-QA artifacts only. Production
context assembly does not import it and Memory V2 remains shadow-only.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Mapping, Sequence

from aifren.continuity.memory_query_decision import MemoryQueryDecision, decide_memory_query
from aifren.memory_v2_store.models import HistoricalOrderWitness, HistoricalSourceSegment
from aifren.continuity.memory_v2_source_projection import expand_source_segments, segment_identity


MAX_PROMPT_ITEMS = 2
MAX_ITEM_CONTENT_CHARACTERS = 220
MAX_PROMPT_BLOCK_CHARACTERS = 900
MAX_APPROXIMATE_PROMPT_TOKENS = 225
MAX_SECONDARY_SCORE_GAP = 1.0

_ALLOWED_LANES = frozenset({"historical_evidence", "historical_episode_source"})
_ALLOWED_SOURCE_CLASSES = frozenset({
    "ordinary_conversation",
    "canonical_conversation_user",
    "canonical_conversation_assistant",
})
_UNSUPPORTED_ATTRIBUTION = frozenset({
    "user_attribution_unsupported",
    "assistant_attribution_unsupported",
})
_HEADER = (
    "[Historical conversation evidence — NOT CURRENT TRUTH]\n"
    "Rules: Each SOURCE QUOTE proves only what was said then. Preserve speaker, "
    "negation, uncertainty/modality, and time. Do not infer completion, "
    "ownership/possession, preference, success/failure, or current state. "
    "ASSISTANT evidence is never USER testimony. If insufficient, say so."
)
_FOOTER = "[End historical conversation evidence]"


@dataclass(frozen=True)
class PromptEvidenceItem:
    memory_id: str
    canonical_record_id: str
    canonical_index: int
    lane: str
    speaker_role: str
    authority_class: str
    scope_class: str
    speech_act: str
    source_quote: str
    semantic_class: str
    episode_id: str
    rank: int
    score: float
    rendered_line: str
    order_witness: HistoricalOrderWitness | None = None
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


@dataclass(frozen=True)
class SupplementalPromptDesign:
    triggered: bool
    reason: str
    callback_source_kind: str
    prompt_block: str
    items: tuple[PromptEvidenceItem, ...]
    excluded: tuple[tuple[str, int], ...]
    character_count: int
    approximate_token_count: int
    composer_latency_ms: float


@dataclass(frozen=True)
class HistoricalResponseValidation:
    accepted: bool
    category: str
    violations: tuple[str, ...]
    communicated_item_count: int


def _approximate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _content(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"^Historical\s+(?:user|assistant)\s+"
        r"(?:record|statement|question|interaction):\s*",
        "", text, count=1, flags=re.IGNORECASE,
    )
    if len(text) > MAX_ITEM_CONTENT_CHARACTERS:
        text = text[: MAX_ITEM_CONTENT_CHARACTERS - 1].rstrip() + "…"
    return text


def _line(
    speaker_role: str,
    scope_state: str,
    speech_act: str,
    content: str,
) -> str:
    owner = "USER" if speaker_role == "user" else "ASSISTANT"
    scope = {
        "unknown_scope": "UNKNOWN SCOPE",
        "real_world": "REAL-WORLD SCOPE",
        "scenario": "SCENARIO SCOPE",
    }.get(scope_state, "UNKNOWN SCOPE")
    act = {
        "assertion": "ASSERTION",
        "question": "QUESTION",
        "other": "OTHER SPEECH",
    }.get(speech_act, "SPEECH ACT UNKNOWN")
    # JSON quoting preserves the canonical text as data and escapes line/control
    # boundaries without inventing a new dialogue-markup grammar.
    return (
        f"- SPEAKER {owner} | HISTORICAL ONLY | {scope} | {act}\n"
        f"  SOURCE QUOTE: {json.dumps(content, ensure_ascii=False)}"
    )


def _block(lines: Sequence[str]) -> str:
    return "\n".join((_HEADER, *lines, _FOOTER))


def _semantic_class(speaker_role: str, content: str) -> str:
    lower = content.casefold()
    if speaker_role == "assistant" and re.search(
        r"\b(?:probably|possibly|maybe|might|may|guess|speculat)", lower,
    ):
        return "assistant_speculation"
    if re.search(
        r"\b(?:couldn['’]?t|didn['’]?t|can['’]?t|not|unable)\b.{0,45}"
        r"\b(?:finish|complete)", lower,
    ):
        return "negated_completion"
    if re.search(r"\b(?:think|might|may|maybe|perhaps|possibly)\b", lower) and re.search(
        r"\b(?:buy|purchase|own|have|get)\b", lower,
    ):
        return "uncertain_acquisition"
    if re.search(r"\b(?:plan|planning|intend|going\s+to|tomorrow|later)\b", lower):
        return "future_intent"
    if re.search(r"\b(?:used\s+to|formerly|previously)\b", lower):
        return "historical_state"
    if re.search(r"\b(?:finished|completed)\b", lower):
        return "positive_completion"
    return "literal_historical_source"


def _candidate_exclusion(
    candidate: object,
    *,
    allowed_speakers: frozenset[str],
    active_truth_scope_id: str,
) -> str:
    lane = str(getattr(candidate, "lane", ""))
    if lane == "historical_recall_anchor_source":
        return "anchor_expansion_excluded"
    if lane not in _ALLOWED_LANES:
        return "candidate_lane_not_admissible"
    speaker = str(getattr(candidate, "speaker_role", ""))
    if speaker not in allowed_speakers:
        return "speaker_ownership_mismatch"
    source_class = str(getattr(candidate, "source_class", ""))
    if source_class not in _ALLOWED_SOURCE_CLASSES:
        return "source_class_not_admissible"
    if str(getattr(candidate, "attribution_state", "")) in _UNSUPPORTED_ATTRIBUTION:
        return "unsupported_summary_attribution"
    if not str(getattr(candidate, "status", "")).startswith("historical_"):
        return "not_historical_only"
    scope_state = str(getattr(candidate, "scope_state", ""))
    if scope_state not in {"unknown_scope", "real_world", "scenario"}:
        return "scope_state_not_admissible"
    if scope_state != "unknown_scope" and (
        not active_truth_scope_id
        or str(getattr(candidate, "truth_scope_id", "")) != active_truth_scope_id
    ):
        return "truth_scope_mismatch"
    if not str(getattr(candidate, "canonical_record_id", "")):
        return "canonical_source_identity_missing"
    if getattr(candidate, "canonical_index", None) is None:
        return "canonical_source_order_missing"
    if lane == "historical_episode_source" and not str(getattr(candidate, "episode_id", "")):
        return "episode_refinement_identity_missing"
    if not _content(getattr(candidate, "content", "")):
        return "source_content_missing"
    return ""


def compose_supplemental_historical_prompt(
    query_text: str,
    candidates: Sequence[object],
    *,
    active_truth_scope_id: str = "",
    enabled: bool = True,
    memory_query_decision: MemoryQueryDecision | None = None,
) -> SupplementalPromptDesign:
    """Render the exact future prompt block without admitting it anywhere."""
    started = time.perf_counter()
    decision = memory_query_decision or decide_memory_query(query_text)
    callback_kind = decision.callback_source_kind
    if not enabled:
        return _result(started, False, "experiment_disabled", callback_kind)
    if not callback_kind:
        return _result(started, False, "ineligible_callback_intent", "")
    allowed_speakers = (
        frozenset({"user", "assistant"})
        if callback_kind == "shared" else frozenset({callback_kind})
    )
    exclusions: dict[str, int] = {}
    eligible: list[tuple[int, object, str]] = []
    seen_ids: set[tuple] = set()
    seen_content: set[str] = set()
    for rank, candidate in enumerate(expand_source_segments(tuple(candidates)), 1):
        reason = _candidate_exclusion(
            candidate,
            allowed_speakers=allowed_speakers,
            active_truth_scope_id=str(active_truth_scope_id),
        )
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            continue
        source_id = segment_identity(candidate)
        content = _content(getattr(candidate, "content", ""))
        content_key = content.casefold()
        if source_id in seen_ids or content_key in seen_content:
            exclusions["duplicate_source"] = exclusions.get("duplicate_source", 0) + 1
            continue
        seen_ids.add(source_id)
        seen_content.add(content_key)
        eligible.append((rank, candidate, content))

    if not eligible:
        reason = (
            "anchor_expansion_excluded"
            if exclusions.get("anchor_expansion_excluded") else
            "no_structurally_eligible_source"
        )
        return _result(started, False, reason, callback_kind, exclusions=exclusions)

    selected: list[PromptEvidenceItem] = []
    lines: list[str] = []
    strongest_score = float(getattr(eligible[0][1], "score", 0.0))
    for rank, candidate, content in eligible:
        score = float(getattr(candidate, "score", 0.0))
        if selected and strongest_score - score > MAX_SECONDARY_SCORE_GAP:
            exclusions["weak_secondary"] = exclusions.get("weak_secondary", 0) + 1
            continue
        speaker = str(getattr(candidate, "speaker_role", ""))
        scope_state = str(getattr(candidate, "scope_state", ""))
        speech_act = str(getattr(candidate, "speech_act", ""))
        semantic_class = _semantic_class(speaker, content)
        rendered = _line(speaker, scope_state, speech_act, content)
        trial = _block((*lines, rendered))
        if (
            len(trial) > MAX_PROMPT_BLOCK_CHARACTERS
            or _approximate_tokens(trial) > MAX_APPROXIMATE_PROMPT_TOKENS
        ):
            exclusions["prompt_budget"] = exclusions.get("prompt_budget", 0) + 1
            continue
        lines.append(rendered)
        selected.append(PromptEvidenceItem(
            str(getattr(candidate, "memory_id", "")),
            str(getattr(candidate, "canonical_record_id", "")),
            int(getattr(candidate, "canonical_index")),
            str(getattr(candidate, "lane", "")),
            speaker,
            "historical_conversation_only",
            scope_state,
            speech_act,
            content,
            semantic_class,
            str(getattr(candidate, "episode_id", "")),
            rank,
            score,
            rendered,
            getattr(candidate, "order_witness", None),
            getattr(candidate, "source_segments", ()),
        ))
        if len(selected) >= MAX_PROMPT_ITEMS:
            break

    if not selected:
        return _result(
            started, False, "prompt_budget_exceeded", callback_kind,
            exclusions=exclusions,
        )
    for _rank, candidate, _content_text in eligible:
        source_id = str(getattr(candidate, "canonical_record_id", ""))
        expected = sum(str(getattr(c, "canonical_record_id", "")) == source_id for _, c, _ in eligible)
        actual = sum(item.canonical_record_id == source_id for item in selected)
        if actual and actual != expected:
            return _result(started, False, "prompt_budget_exceeded", callback_kind,
                           exclusions={**exclusions, "partial_source_projection": 1})
    rendered_block = _block(lines)
    return _result(
        started, True, "eligible_explicit_historical_callback", callback_kind,
        block=rendered_block, items=tuple(selected), exclusions=exclusions,
    )


_NEGATION = re.compile(
    r"\b(?:not|never|no|couldn['’]?t|didn['’]?t|can['’]?t|"
    r"don['’]?t|won['’]?t|wasn['’]?t|weren['’]?t|unable)\b",
    re.I,
)
_UNCERTAINTY = re.compile(r"\b(?:might|may|maybe|perhaps|possibly|probably|think|guess|speculat)\w*\b", re.I)
_HISTORICAL_FRAME = re.compile(
    r"\b(?:previously|before|back\s+then|at\s+the\s+time|used\s+to|i\s+(?:said|told|mentioned|guessed))\b",
    re.I,
)


def _sentences(value: object) -> tuple[str, ...]:
    text = " ".join(str(value or "").split())
    return tuple(item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip())


def _communicates(item: PromptEvidenceItem, response: str) -> bool:
    lower = response.casefold()
    exact = " ".join(item.source_quote.casefold().split())
    if exact and exact in lower:
        return True
    semantic = item.semantic_class
    if semantic == "negated_completion":
        return bool(re.search(
            r"\b(?:couldn['’]?t|didn['’]?t|wasn['’]?t|weren['’]?t|unable|not)\b"
            r".{0,60}\b(?:finish|complete)", lower,
        ))
    if semantic == "uncertain_acquisition":
        return bool(_UNCERTAINTY.search(lower) and re.search(r"\b(?:buy|purchase|get|telescope)\b", lower))
    if semantic == "future_intent":
        return bool(re.search(r"\b(?:plan|planning|intend|tomorrow|going\s+to)\b", lower))
    if semantic == "historical_state":
        return bool(re.search(r"\b(?:used\s+to|formerly|previously)\b", lower))
    if semantic == "assistant_speculation":
        return bool(
            re.search(
                r"(?:\b(?:i|assistant)\b.{0,40}\b"
                r"(?:said|told|telling|mentioned|guessed|speculated)\b|"
                r"\bme\s+who\s+(?:said|mentioned|guessed|speculated)\b)",
                lower,
            )
            and _UNCERTAINTY.search(lower)
        )
    if semantic == "positive_completion":
        return bool(re.search(r"\b(?:finished|completed)\b", lower))
    source_terms = tuple(dict.fromkeys(re.findall(r"[a-z0-9]{4,}", exact)))[:8]
    return bool(source_terms and sum(term in lower for term in source_terms) >= min(3, len(source_terms)))


def _semantic_violations(item: PromptEvidenceItem, response: str) -> tuple[str, ...]:
    violations: list[str] = []
    sentences = _sentences(response)
    semantic = item.semantic_class
    if item.speaker_role == "assistant":
        if any(
            re.search(
                r"\byou\s+(?:said|saying|told\s+me|were\s+(?:saying|telling\s+me)|mention(?:ed|ing))\b",
                sentence, re.I,
            )
            and not _NEGATION.search(sentence)
            for sentence in sentences
        ):
            violations.append("speaker_attribution_error")
    if semantic == "negated_completion":
        for sentence in sentences:
            if _NEGATION.search(sentence):
                continue
            if re.search(
                r"\b(?:successfully|finished|completed|accomplishment|milestone|conquered)\b",
                sentence, re.I,
            ):
                violations.append("negation_or_completion_inversion")
                break
    elif semantic == "uncertain_acquisition":
        for sentence in sentences:
            if _NEGATION.search(sentence) or _UNCERTAINTY.search(sentence):
                continue
            if re.search(
                r"\b(?:bought|purchased|own|owns|owned|have|has)\b.{0,40}\btelescope\b|"
                r"\btelescope\b.{0,40}\b(?:bought|purchased|own|owns|owned)\b",
                sentence, re.I,
            ):
                violations.append("modality_or_ownership_strengthening")
                break
    elif semantic == "future_intent":
        for sentence in sentences:
            if _NEGATION.search(sentence) or _UNCERTAINTY.search(sentence):
                continue
            if re.search(r"\b(?:worked\s+on|finished|completed)\b.{0,50}\bgame\b", sentence, re.I):
                violations.append("future_intent_promoted_to_completed_action")
                break
    elif semantic == "historical_state":
        for sentence in sentences:
            if _NEGATION.search(sentence) or re.search(r"\bused\s+to\b", sentence, re.I):
                continue
            if re.search(
                r"\b(?:currently|still)\b.{0,40}\bcollect|"
                r"\byou\s+(?:collect|have)\b.{0,40}\bcartridge",
                sentence, re.I,
            ):
                violations.append("historical_state_promoted_to_current")
                break
    elif semantic == "assistant_speculation":
        for sentence in sentences:
            if re.search(
                r"\byou\s+(?:enjoy|like|love|prefer|collect)\w*\b|"
                r"\byour\s+(?:absolute\s+)?favorite\b|"
                r"\byou\s+are\s+(?:an?\s+)?(?:dedicated|avid)\b",
                sentence, re.I,
            ) and not _NEGATION.search(sentence):
                violations.append("unsupported_user_inference_from_assistant_history")
                break
            if _NEGATION.search(sentence) or _UNCERTAINTY.search(sentence):
                continue
            if re.search(
                r"\byou\s+(?:have|own)\b.{0,45}\bcollection\b|"
                r"\byour\s+(?:huge\s+)?collection\b",
                sentence, re.I,
            ) and not _HISTORICAL_FRAME.search(sentence):
                violations.append("assistant_speculation_promoted_to_user_truth")
                break
    elif semantic == "positive_completion":
        for sentence in sentences:
            if re.search(r"\b(?:finish|complete|refactor)\b", sentence, re.I) and _NEGATION.search(sentence):
                violations.append("positive_completion_inverted")
                break
    return tuple(dict.fromkeys(violations))


def validate_supplemental_historical_response(
    design: SupplementalPromptDesign,
    response: object,
) -> HistoricalResponseValidation:
    """Validate obvious source-semantic violations for disconnected QA only."""
    if not design.triggered or not design.items:
        return HistoricalResponseValidation(True, "not_required", (), 0)
    text = " ".join(str(response or "").split())
    violations = tuple(dict.fromkeys(
        violation
        for item in design.items
        for violation in _semantic_violations(item, text)
    ))
    communicated = sum(_communicates(item, text) for item in design.items)
    if violations:
        return HistoricalResponseValidation(False, violations[0], violations, communicated)
    if communicated == 0:
        return HistoricalResponseValidation(
            False, "historical_source_omitted", ("historical_source_omitted",), 0,
        )
    # Separate passages of one source are separately required; omission must
    # not turn a disjoint query projection into a fabricated complete answer.
    if any(len([other for other in design.items if other.canonical_record_id == item.canonical_record_id]) > 1
           and not _communicates(item, text) for item in design.items):
        return HistoricalResponseValidation(False, "historical_segment_omitted", ("historical_segment_omitted",), communicated)
    return HistoricalResponseValidation(True, "accepted", (), communicated)


def historical_response_repair_prompt(
    design: SupplementalPromptDesign,
    query_text: str,
    _rejected_draft: object,
) -> str:
    """Build one bounded repair request from exact admitted source semantics."""
    evidence = []
    safe_sentences = []
    for item in design.items:
        evidence.append({
            "speaker": item.speaker_role.upper(),
            "authority": "HISTORICAL CONVERSATION ONLY",
            "scope": item.scope_class.upper(),
            "speech_act": item.speech_act.upper() or "UNKNOWN",
            "source_quote": item.source_quote,
        })
        quote = json.dumps(item.source_quote, ensure_ascii=False)
        if item.speech_act == "question":
            safe_sentences.append(
                f"The {item.speaker_role} previously asked {quote}; that records the question, not its premise."
            )
        elif item.speaker_role == "assistant":
            safe_sentences.append(
                f"I previously said {quote}; that is my prior statement, not user testimony or current truth."
            )
        else:
            safe_sentences.append(
                f"You previously said {quote}; I do not know whether that changed afterward."
            )
    payload = {
        "query": str(query_text)[:220],
        "evidence": evidence,
        "required_factual_core": " ".join(safe_sentences),
    }
    return (
        "HISTORICAL CALLBACK RESPONSE REPAIR\n"
        "Rewrite as one concise natural in-character answer. Use only the SOURCE QUOTE semantics below. "
        "Preserve speaker, negation, uncertainty/modality, and time exactly. Do not infer completion, "
        "ownership/possession, preference, success/failure, or current state. Do not repeat unsupported "
        "related memories. Render required_factual_core exactly once as the factual part; "
        "a short nonfactual character beat is optional. Data:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def historical_response_fallback_dialogue(
    design: SupplementalPromptDesign,
) -> str:
    """Render the exceptional deterministic response from source ownership."""
    sentences = []
    for item in design.items:
        quote = json.dumps(item.source_quote, ensure_ascii=False)
        if item.speech_act == "question":
            sentences.append(
                f"I remember that question: {quote} I don't take the question itself as proof."
            )
        elif item.speaker_role == "assistant":
            # A complete first-person assertion can be reported naturally
            # without changing its subject, tense, qualification or words.
            # Other syntax retains a short literal quote instead of guessing
            # pronoun/tense transformations (especially in user testimony).
            text = item.source_quote.strip()
            from aifren.dialogue.dialogue_semantics import DialogueSpanKind, parse_dialogue
            spoken_only = all(span.kind != DialogueSpanKind.EMOTE for span in parse_dialogue(text))
            if (item.source_segments and re.match(r"^(?:I|We)\s", text)
                    and not any(c in text for c in '"“”') and spoken_only):
                clause = text if text.startswith('I ') else text[0].lower() + text[1:]
                sentences.append(f"I told you that {clause}")
            else:
                sentences.append(f"I said, {quote}")
        else:
            # A quoted report already limits this to what the user said then.
            # Do not imply a current value or add uncertainty about a later
            # change that was not asked about. Keep unresolved lookup/slot
            # statements under their existing distinct owners.
            sentences.append(f"You told me, {quote}")
    return " ".join(sentences)


def counterfactual_context_with_block(
    production_context: Sequence[Mapping[str, object]], prompt_block: str,
) -> list[dict[str, object]]:
    """Copy a production context and insert one proposed block before its query."""
    copied = [dict(item) for item in production_context]
    insertion = len(copied)
    for index in range(len(copied) - 1, -1, -1):
        if str(copied[index].get("role", "")) == "user":
            insertion = index
            break
    copied.insert(insertion, {"role": "user", "content": str(prompt_block)})
    return copied


def _result(
    started: float,
    triggered: bool,
    reason: str,
    callback_kind: str,
    *,
    block: str = "",
    items: tuple[PromptEvidenceItem, ...] = (),
    exclusions: Mapping[str, int] | None = None,
) -> SupplementalPromptDesign:
    exclusions = exclusions or {}
    return SupplementalPromptDesign(
        bool(triggered), str(reason), str(callback_kind), str(block), tuple(items),
        tuple(sorted((str(key), int(value)) for key, value in exclusions.items())),
        len(str(block)), _approximate_tokens(str(block)),
        round((time.perf_counter() - started) * 1000.0, 6),
    )


__all__ = [
    "MAX_APPROXIMATE_PROMPT_TOKENS",
    "MAX_ITEM_CONTENT_CHARACTERS",
    "MAX_PROMPT_BLOCK_CHARACTERS",
    "MAX_PROMPT_ITEMS",
    "PromptEvidenceItem",
    "SupplementalPromptDesign",
    "HistoricalResponseValidation",
    "compose_supplemental_historical_prompt",
    "counterfactual_context_with_block",
    "historical_response_repair_prompt",
    "historical_response_fallback_dialogue",
    "validate_supplemental_historical_response",
]
