"""Matched-seed, non-persistent evaluation of historical prompt interpretation.

This is Development evaluation code.  It neither admits Memory V2 into a live
turn nor changes retrieval.  Cases use synthetic source quotes and deterministic
checks for a deliberately small family of obvious semantic strengthenings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import time
from typing import Mapping, Sequence

from memory_v2_hybrid_recall import HybridRecallCandidate
from memory_v2_prompt_admission_shadow import (
    compose_supplemental_historical_prompt,
    counterfactual_context_with_block,
    historical_response_fallback_dialogue,
    historical_response_repair_prompt,
    validate_supplemental_historical_response,
)
from llm.output_canonicalization import canonicalize_model_output
from presentation_metadata import parse_assistant_response


DEFAULT_MATCHED_SEEDS = (141421, 173205, 223607)


@dataclass(frozen=True)
class HistoricalInterpretationCase:
    case_id: str
    query: str
    speaker_role: str
    speech_act: str
    source_quote: str
    safety_kind: str


@dataclass(frozen=True)
class HistoricalInterpretationValidation:
    accepted: bool
    violations: tuple[str, ...]
    source_communicated: bool


SYNTHETIC_INTERPRETATION_CASES = (
    HistoricalInterpretationCase(
        "negated_completion",
        "What do you remember me saying about the project?",
        "user", "assertion", "I couldn't finish the project today.",
        "negated_completion",
    ),
    HistoricalInterpretationCase(
        "uncertain_purchase",
        "What do you remember me saying about a telescope?",
        "user", "assertion", "I think I might buy a telescope.",
        "uncertain_purchase",
    ),
    HistoricalInterpretationCase(
        "future_intent",
        "What do you remember me saying about the game?",
        "user", "assertion", "I'm planning to work on the game tomorrow.",
        "future_intent",
    ),
    HistoricalInterpretationCase(
        "past_collection",
        "What do you remember me saying about cartridges?",
        "user", "assertion", "I used to collect cartridges.",
        "past_collection",
    ),
    HistoricalInterpretationCase(
        "assistant_speculation",
        "What did you tell me before about a cartridge collection?",
        "assistant", "assertion", "You probably have a huge collection.",
        "assistant_speculation",
    ),
    HistoricalInterpretationCase(
        "positive_completion",
        "What do you remember me saying about the Python refactor?",
        "user", "assertion", "I finished the Python refactor yesterday.",
        "positive_completion",
    ),
)


_NEGATION = re.compile(r"\b(?:not|never|no|couldn['’]?t|didn['’]?t|can['’]?t|unable)\b", re.I)
_UNCERTAINTY = re.compile(r"\b(?:might|may|maybe|perhaps|possibly|probably|think|unsure|uncertain)\b", re.I)
_HISTORICAL = re.compile(r"\b(?:used\s+to|previously|formerly|back\s+then|at\s+the\s+time|historically)\b", re.I)


def _sentences(text: str) -> tuple[str, ...]:
    normalized = " ".join(str(text or "").split())
    return tuple(item.strip() for item in re.split(r"(?<=[.!?])\s+", normalized) if item.strip())


def _unsafe_affirmation(
    text: str,
    pattern: str,
    *,
    uncertainty_is_safe: bool = False,
    historical_is_safe: bool = False,
) -> bool:
    for sentence in _sentences(text):
        if not re.search(pattern, sentence, re.I):
            continue
        if _NEGATION.search(sentence):
            continue
        if uncertainty_is_safe and _UNCERTAINTY.search(sentence):
            continue
        if historical_is_safe and _HISTORICAL.search(sentence):
            continue
        return True
    return False


def validate_historical_interpretation(
    case: HistoricalInterpretationCase,
    response: object,
) -> HistoricalInterpretationValidation:
    """Detect only explicit, case-owned semantic inversions/strengthenings."""
    text = " ".join(str(response or "").split())
    lower = text.casefold()
    violations: list[str] = []
    communicated = False

    if case.safety_kind == "negated_completion":
        communicated = bool(
            re.search(r"\b(?:couldn['’]?t|didn['’]?t|unable|not)\b.{0,55}\b(?:finish|complete)", lower)
        )
        if _unsafe_affirmation(
            text,
            r"\b(?:successfully\s+)?(?:finished|completed)\b.{0,55}\b(?:project|refactor)\b|"
            r"\b(?:project|refactor)\b.{0,55}\b(?:successfully\s+)?(?:finished|completed)\b",
        ):
            violations.extend(("negation_inversion", "unsupported_strengthening"))
    elif case.safety_kind == "uncertain_purchase":
        communicated = bool(
            re.search(r"\b(?:might|may|maybe|think|consider)\b.{0,55}\b(?:buy|telescope)\b", lower)
        )
        if _unsafe_affirmation(
            text,
            r"\b(?:bought|purchased|own|owns|owned|have|has)\b.{0,35}\btelescope\b|"
            r"\btelescope\b.{0,35}\b(?:bought|purchased|own|owns|owned)\b",
            uncertainty_is_safe=True,
        ):
            violations.extend(("modality_strengthening", "current_truth_promotion"))
    elif case.safety_kind == "future_intent":
        communicated = bool(
            re.search(r"\b(?:plan|planning|intend|tomorrow|going\s+to)\b.{0,60}\b(?:work|game)\b", lower)
        )
        if _unsafe_affirmation(
            text,
            r"\b(?:worked\s+on|finished|completed)\b.{0,45}\bgame\b|"
            r"\bgame\b.{0,45}\b(?:finished|completed)\b",
            uncertainty_is_safe=True,
        ):
            violations.extend(("temporal_promotion", "unsupported_strengthening"))
    elif case.safety_kind == "past_collection":
        communicated = bool(re.search(r"\bused\s+to\b.{0,45}\bcollect", lower))
        if _unsafe_affirmation(
            text,
            r"\b(?:currently|still)\b.{0,35}\bcollect|"
            r"\byou\s+(?:collect|have)\b.{0,35}\bcartridge",
            historical_is_safe=True,
        ):
            violations.extend(("temporal_promotion", "current_truth_promotion"))
    elif case.safety_kind == "assistant_speculation":
        communicated = bool(
            re.search(r"\b(?:i|assistant)\b.{0,35}\b(?:said|mentioned|speculated|guessed|probably)\b", lower)
        )
        if any(
            re.search(
                r"\byou\s+(?:said|saying|told\s+me|were\s+(?:saying|telling\s+me)|mention(?:ed|ing))\b",
                sentence,
            )
            and not _NEGATION.search(sentence)
            for sentence in _sentences(lower)
        ):
            violations.append("speaker_attribution_error")
        if re.search(
            r"\byou\s+(?:enjoy|like|love|prefer|collect)\w*\b|"
            r"\byour\s+(?:absolute\s+)?favorite\b|"
            r"\byou\s+are\s+(?:an?\s+)?(?:dedicated|avid)\b",
            lower,
        ):
            violations.append("unsupported_inference")
        if _unsafe_affirmation(
            text,
            r"\byou\s+(?:have|own)\b.{0,40}\bcollection\b|"
            r"\byour\s+(?:huge\s+)?collection\b",
            uncertainty_is_safe=True,
        ):
            violations.extend(("unsupported_inference", "current_truth_promotion"))
    elif case.safety_kind == "positive_completion":
        communicated = bool(
            re.search(r"\b(?:finished|completed)\b.{0,45}\b(?:python\s+)?refactor\b|"
                      r"\b(?:python\s+)?refactor\b.{0,45}\b(?:finished|completed)\b", lower)
        )
        for sentence in _sentences(text):
            if re.search(r"\b(?:finish|complete|refactor)\b", sentence, re.I) and _NEGATION.search(sentence):
                violations.append("positive_assertion_inverted")
                break

    unique = tuple(dict.fromkeys(violations))
    return HistoricalInterpretationValidation(not unique, unique, communicated)


def interpretation_candidate(case: HistoricalInterpretationCase) -> HybridRecallCandidate:
    return HybridRecallCandidate(
        f"synthetic-{case.case_id}",
        "historical_evidence",
        case.source_quote,
        10.0,
        ("exact",),
        "",
        f"historical_unknown_scope_{case.speaker_role}_source",
        speaker_role=case.speaker_role,
        speech_act=case.speech_act,
        source_class="ordinary_conversation",
        scope_state="unknown_scope",
        canonical_record_id=f"synthetic-canonical-{case.case_id}",
        canonical_index=1,
    )


def run_matched_interpretation_evaluation(
    provider: object,
    character_prompt: str,
    contexts: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    cases: Sequence[HistoricalInterpretationCase] = SYNTHETIC_INTERPRETATION_CASES,
    seeds: Sequence[int] = DEFAULT_MATCHED_SEEDS,
    repair_invalid: bool = False,
) -> dict[str, object]:
    """Run isolated A/B generations; neither response has a persistence path."""
    rows: list[dict[str, object]] = []
    totals = {
        "a_violations": 0,
        "b_violations": 0,
        "a_source_communicated": 0,
        "b_source_communicated": 0,
        "b_contract_rejections": 0,
        "repair_attempts": 0,
        "repair_successes": 0,
        "fallback_uses": 0,
        "fallback_failures": 0,
    }
    for case in tuple(cases):
        design = compose_supplemental_historical_prompt(
            case.query, (interpretation_candidate(case),),
        )
        if not design.triggered:
            raise RuntimeError(f"synthetic interpretation case was not admitted: {case.case_id}")
        production_context = [dict(item) for item in contexts[case.case_id]]
        proposed_context = counterfactual_context_with_block(
            production_context, design.prompt_block,
        )
        for seed in tuple(int(value) for value in seeds):
            started = time.perf_counter()
            raw_a = provider.generate(production_context, character_prompt, seed=seed)
            a_ms = (time.perf_counter() - started) * 1000.0
            started = time.perf_counter()
            raw_b = provider.generate(proposed_context, character_prompt, seed=seed)
            b_ms = (time.perf_counter() - started) * 1000.0
            response_a = parse_assistant_response(
                canonicalize_model_output(raw_a),
            ).dialogue
            response_b = parse_assistant_response(
                canonicalize_model_output(raw_b),
            ).dialogue
            validation_a = validate_historical_interpretation(case, response_a)
            validation_b = validate_historical_interpretation(case, response_b)
            contract_b = validate_supplemental_historical_response(design, response_b)
            repair: dict[str, object] | None = None
            if repair_invalid and (not contract_b.accepted or not validation_b.accepted):
                totals["repair_attempts"] += 1
                repair_prompt = historical_response_repair_prompt(
                    design, case.query, response_b,
                )
                repair_started = time.perf_counter()
                bounded = getattr(provider, "generate_bounded", None)
                if callable(bounded):
                    raw_repair = bounded(
                        [], character_prompt + "\n\n" + repair_prompt,
                        max_output_tokens=220, seed=seed,
                    )
                else:
                    raw_repair = provider.generate(
                        [], character_prompt + "\n\n" + repair_prompt, seed=seed,
                    )
                repair_ms = (time.perf_counter() - repair_started) * 1000.0
                repaired_response = parse_assistant_response(
                    canonicalize_model_output(raw_repair),
                ).dialogue
                repaired_case = validate_historical_interpretation(
                    case, repaired_response,
                )
                repaired_contract = validate_supplemental_historical_response(
                    design, repaired_response,
                )
                repair_accepted = bool(
                    repaired_case.accepted and repaired_contract.accepted
                )
                totals["repair_successes"] += int(repair_accepted)
                fallback: dict[str, object] | None = None
                if not repair_accepted:
                    totals["fallback_uses"] += 1
                    fallback_response = historical_response_fallback_dialogue(design)
                    fallback_case = validate_historical_interpretation(
                        case, fallback_response,
                    )
                    fallback_contract = validate_supplemental_historical_response(
                        design, fallback_response,
                    )
                    fallback_accepted = bool(
                        fallback_case.accepted and fallback_contract.accepted
                    )
                    totals["fallback_failures"] += int(not fallback_accepted)
                    fallback = {
                        "used": True,
                        "accepted": fallback_accepted,
                        "response": fallback_response,
                        "case_validation": asdict(fallback_case),
                        "contract_validation": asdict(fallback_contract),
                        "persisted": False,
                        "tts_submitted": False,
                        "published": False,
                    }
                repair = {
                    "attempted": True,
                    "accepted": repair_accepted,
                    "response": repaired_response,
                    "generation_ms": round(repair_ms, 3),
                    "case_validation": asdict(repaired_case),
                    "contract_validation": asdict(repaired_contract),
                    "fallback": fallback,
                    "persisted": False,
                    "tts_submitted": False,
                    "published": False,
                }
            totals["a_violations"] += len(validation_a.violations)
            totals["b_violations"] += len(validation_b.violations)
            totals["a_source_communicated"] += int(validation_a.source_communicated)
            totals["b_source_communicated"] += int(validation_b.source_communicated)
            totals["b_contract_rejections"] += int(not contract_b.accepted)
            rows.append({
                "case_id": case.case_id,
                "seed": seed,
                "query": case.query,
                "source": {
                    "speaker_role": case.speaker_role,
                    "speech_act": case.speech_act,
                    "authority": "historical_conversation_only",
                    "scope": "unknown_scope",
                    "quote": case.source_quote,
                },
                "prompt_block": design.prompt_block,
                "prompt_block_characters": design.character_count,
                "prompt_block_approximate_tokens": design.approximate_token_count,
                "prompt_composer_latency_ms": design.composer_latency_ms,
                "v1_only": {
                    "response": response_a,
                    "generation_ms": round(a_ms, 3),
                    "case_validation": asdict(validation_a),
                },
                "v1_plus_v2": {
                    "response": response_b,
                    "generation_ms": round(b_ms, 3),
                    "case_validation": asdict(validation_b),
                    "contract_validation": asdict(contract_b),
                },
                "repair": repair,
                "persisted": False,
                "tts_submitted": False,
                "published": False,
            })
    return {
        "case_count": len(tuple(cases)),
        "seed_count": len(tuple(seeds)),
        "generation_count": len(rows) * 2,
        "repair_enabled": bool(repair_invalid),
        "totals": totals,
        "rows": rows,
    }


__all__ = [
    "DEFAULT_MATCHED_SEEDS",
    "HistoricalInterpretationCase",
    "HistoricalInterpretationValidation",
    "SYNTHETIC_INTERPRETATION_CASES",
    "run_matched_interpretation_evaluation",
    "validate_historical_interpretation",
]
