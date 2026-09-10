"""Matched disconnected A/B/C2/D evaluation for exact-source callbacks."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Mapping, Sequence

from llm.output_canonicalization import canonicalize_model_output
from memory_v2_evidence_arbitration_shadow import (
    arbitrate_historical_callback_evidence,
    arbitrated_counterfactual_context,
)
from memory_v2_exact_source_callback_shadow import (
    compose_exact_source_callback_contract,
    source_grounded_callback_context,
    validate_exact_source_callback_response,
)
from memory_v2_historical_interpretation_evaluation import (
    DEFAULT_MATCHED_SEEDS,
    SYNTHETIC_INTERPRETATION_CASES,
    HistoricalInterpretationCase,
    interpretation_candidate,
    validate_historical_interpretation,
)
from memory_v2_prompt_admission_shadow import (
    compose_supplemental_historical_prompt,
    counterfactual_context_with_block,
    historical_response_fallback_dialogue,
    historical_response_repair_prompt,
    validate_supplemental_historical_response,
)
from presentation_metadata import parse_assistant_response
from response_requirements import repair_requirement_prompt


SHARED_HISTORY_CASE = HistoricalInterpretationCase(
    "shared_harbor_map",
    "What did we talk about before regarding the harbor map?",
    "assistant",
    "assertion",
    "I drew a harbor map beside a brass compass.",
    "shared_history",
)
EXACT_SOURCE_CASES = (*SYNTHETIC_INTERPRETATION_CASES, SHARED_HISTORY_CASE)
EXACT_SOURCE_VARIANTS = (
    "v1_only",
    "v1_plus_v2",
    "c2_canonical_priority",
    "d_source_before_query",
    "d_source_after_query",
)


def _dialogue(value: object) -> str:
    return parse_assistant_response(canonicalize_model_output(value)).dialogue


def _generate(provider, context, character_prompt, seed) -> tuple[str, float]:
    started = time.perf_counter()
    response = provider.generate(context, character_prompt, seed=seed)
    return _dialogue(response), (time.perf_counter() - started) * 1000.0


def _validate(case, design, contract, response, *, variant, selected):
    case_validation = validate_historical_interpretation(case, response)
    contract_validation = None
    exact_validation = None
    if variant in {"v1_plus_v2", "c2_canonical_priority"}:
        contract_validation = validate_supplemental_historical_response(
            design, response,
        )
        accepted = case_validation.accepted and contract_validation.accepted
    elif variant.startswith("d_source_"):
        exact_validation = validate_exact_source_callback_response(
            contract, response, competing_v1_items=selected,
        )
        accepted = case_validation.accepted and exact_validation.accepted
    else:
        accepted = case_validation.accepted
    return accepted, case_validation, contract_validation, exact_validation


def _repair(provider, character_prompt, case, design, contract, raw, seed, *, variant, selected):
    if variant.startswith("d_source_"):
        prompt = repair_requirement_prompt(contract.must_communicate, raw)
    else:
        prompt = historical_response_repair_prompt(design, case.query, raw)
    started = time.perf_counter()
    bounded = getattr(provider, "generate_bounded", None)
    if callable(bounded):
        generated = bounded(
            [], character_prompt + "\n\n" + prompt,
            max_output_tokens=220, seed=seed,
        )
    else:
        generated = provider.generate([], character_prompt + "\n\n" + prompt, seed=seed)
    response = _dialogue(generated)
    elapsed = (time.perf_counter() - started) * 1000.0
    accepted, case_validation, supplemental, exact = _validate(
        case, design, contract, response, variant=variant, selected=selected,
    )
    fallback = None
    if not accepted:
        fallback_response = (
            contract.must_communicate.fallback_dialogue
            if variant.startswith("d_source_")
            else historical_response_fallback_dialogue(design)
        )
        fallback_accepted, fallback_case, fallback_supplemental, fallback_exact = _validate(
            case, design, contract, fallback_response,
            variant=variant, selected=selected,
        )
        fallback = {
            "response": fallback_response,
            "accepted": fallback_accepted,
            "case_validation": asdict(fallback_case),
            "contract_validation": asdict(fallback_supplemental) if fallback_supplemental else None,
            "exact_validation": asdict(fallback_exact) if fallback_exact else None,
            "persisted": False,
            "tts_submitted": False,
            "published": False,
        }
    return {
        "response": response,
        "generation_ms": round(elapsed, 3),
        "accepted": accepted,
        "case_validation": asdict(case_validation),
        "contract_validation": asdict(supplemental) if supplemental else None,
        "exact_validation": asdict(exact) if exact else None,
        "fallback": fallback,
        "persisted": False,
        "tts_submitted": False,
        "published": False,
    }


def _metrics():
    return {
        "raw_acceptable": 0,
        "raw_rejected": 0,
        "source_communicated": 0,
        "speaker_errors": 0,
        "polarity_errors": 0,
        "modality_errors": 0,
        "temporal_current_truth_errors": 0,
        "unrelated_substitutions": 0,
        "source_omissions": 0,
        "repair_required": 0,
        "repair_successes": 0,
        "fallback_required": 0,
        "fallback_failures": 0,
        "generation_ms": [],
        "repair_ms": [],
        "context_characters": [],
    }


def _accumulate(metrics, row):
    metrics["raw_acceptable"] += int(row["raw_acceptable"])
    metrics["raw_rejected"] += int(not row["raw_acceptable"])
    violations = set(row["case_validation"]["violations"])
    contract = row.get("contract_validation")
    exact = row.get("exact_validation")
    if contract:
        violations.update(contract["violations"])
        metrics["source_communicated"] += int(contract["communicated_item_count"] > 0)
    elif exact:
        violations.update(exact["violations"])
        metrics["source_communicated"] += int(exact["source_communicated"])
    else:
        metrics["source_communicated"] += int(row["case_validation"]["source_communicated"])
    metrics["speaker_errors"] += sum("speaker" in item or "attribution" in item for item in violations)
    metrics["polarity_errors"] += sum("negation" in item or "positive_assertion" in item for item in violations)
    metrics["modality_errors"] += sum("modality" in item for item in violations)
    metrics["temporal_current_truth_errors"] += sum(
        "temporal" in item or "current_truth" in item or "current_state" in item
        for item in violations
    )
    metrics["unrelated_substitutions"] += int("unrelated_memory_substitution" in violations)
    metrics["source_omissions"] += sum(
        "source_omitted" in item for item in violations
    )
    metrics["generation_ms"].append(row["generation_ms"])
    metrics["context_characters"].append(row["context_characters"])
    repair = row.get("repair")
    if repair:
        metrics["repair_required"] += 1
        metrics["repair_successes"] += int(repair["accepted"])
        metrics["repair_ms"].append(repair["generation_ms"])
        fallback = repair.get("fallback")
        if fallback:
            metrics["fallback_required"] += 1
            metrics["fallback_failures"] += int(not fallback["accepted"])


def _finish(metrics):
    result = dict(metrics)
    for key in ("generation_ms", "repair_ms", "context_characters"):
        values = tuple(float(value) for value in result.pop(key))
        result[key + "_mean"] = round(sum(values) / len(values), 3) if values else None
        result[key + "_max"] = round(max(values), 3) if values else None
    total = result["raw_acceptable"] + result["raw_rejected"]
    result["raw_acceptance_rate"] = round(result["raw_acceptable"] / total, 6) if total else None
    result["raw_source_communication_rate"] = round(result["source_communicated"] / total, 6) if total else None
    result["raw_unrelated_substitution_rate"] = round(result["unrelated_substitutions"] / total, 6) if total else None
    return result


def run_exact_source_callback_evaluation(
    provider: object,
    character_prompt: str,
    contexts: Mapping[str, Sequence[Mapping[str, object]]],
    v1_selected: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    cases: Sequence[HistoricalInterpretationCase] = EXACT_SOURCE_CASES,
    seeds: Sequence[int] = DEFAULT_MATCHED_SEEDS,
    govern_rejected: bool = True,
) -> dict[str, object]:
    """Freeze every raw A/B/C2/D draft before any repair or fallback."""
    rows = []
    contracts = {}
    designs = {}
    selected_by_case = {}
    for case in tuple(cases):
        design = compose_supplemental_historical_prompt(
            case.query, (interpretation_candidate(case),),
        )
        contract = compose_exact_source_callback_contract(design)
        if not contract.triggered:
            raise RuntimeError(f"case was not structurally admitted: {case.case_id}")
        production = [dict(item) for item in contexts[case.case_id]]
        selected = tuple(dict(item) for item in v1_selected.get(case.case_id, ()))
        arbitration = arbitrate_historical_callback_evidence(case.query, selected, design)
        contexts_by_variant = {
            "v1_only": production,
            "v1_plus_v2": counterfactual_context_with_block(production, design.prompt_block),
            "c2_canonical_priority": arbitrated_counterfactual_context(
                production, arbitration, design, variant="c2_canonical_priority",
            ),
            "d_source_before_query": source_grounded_callback_context(
                production, contract, requirement_order="before_query",
            ),
            "d_source_after_query": source_grounded_callback_context(
                production, contract, requirement_order="after_query",
            ),
        }
        contracts[case.case_id] = contract
        designs[case.case_id] = design
        selected_by_case[case.case_id] = selected
        for seed in tuple(int(value) for value in seeds):
            variants = {}
            for variant in EXACT_SOURCE_VARIANTS:
                response, elapsed = _generate(
                    provider, contexts_by_variant[variant], character_prompt, seed,
                )
                accepted, case_validation, supplemental, exact = _validate(
                    case, design, contract, response,
                    variant=variant, selected=selected,
                )
                variants[variant] = {
                    "response": response,
                    "generation_ms": round(elapsed, 3),
                    "raw_acceptable": accepted,
                    "case_validation": asdict(case_validation),
                    "contract_validation": asdict(supplemental) if supplemental else None,
                    "exact_validation": asdict(exact) if exact else None,
                    "context_characters": sum(len(str(item.get("content", ""))) for item in contexts_by_variant[variant]),
                    "repair": None,
                    "persisted": False,
                    "tts_submitted": False,
                    "published": False,
                }
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
                "contract": {
                    "callback_source_kind": contract.callback_source_kind,
                    "context_block": contract.context_block,
                    "composer_latency_ms": contract.composer_latency_ms,
                },
                "arbitration": {
                    "latency_ms": arbitration.latency_ms,
                    "decisions": [asdict(item) for item in arbitration.decisions],
                },
                "variants": variants,
            })
    if govern_rejected:
        for row in rows:
            case_id = str(row["case_id"])
            case = next(item for item in cases if item.case_id == case_id)
            for variant in EXACT_SOURCE_VARIANTS[1:]:
                raw = row["variants"][variant]
                if not raw["raw_acceptable"]:
                    raw["repair"] = _repair(
                        provider, character_prompt, case, designs[case_id],
                        contracts[case_id], raw["response"], int(row["seed"]),
                        variant=variant, selected=selected_by_case[case_id],
                    )
    metrics = {variant: _metrics() for variant in EXACT_SOURCE_VARIANTS}
    for row in rows:
        for variant in EXACT_SOURCE_VARIANTS:
            _accumulate(metrics[variant], row["variants"][variant])
    return {
        "case_count": len(tuple(cases)),
        "seed_count": len(tuple(seeds)),
        "row_count": len(rows),
        "variant_count": len(EXACT_SOURCE_VARIANTS),
        "governance_enabled": bool(govern_rejected),
        "metrics": {name: _finish(value) for name, value in metrics.items()},
        "rows": rows,
    }


__all__ = [
    "EXACT_SOURCE_CASES",
    "EXACT_SOURCE_VARIANTS",
    "SHARED_HISTORY_CASE",
    "run_exact_source_callback_evaluation",
]
