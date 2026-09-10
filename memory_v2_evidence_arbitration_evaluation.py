"""Matched A/B/C evaluation for disconnected historical evidence arbitration."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Mapping, Sequence

from llm.output_canonicalization import canonicalize_model_output
from memory_v2_evidence_arbitration_shadow import (
    arbitrate_historical_callback_evidence,
    arbitrated_counterfactual_context,
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


ARBITRATION_VARIANTS = (
    "v1_only",
    "v1_plus_v2",
    "c1_conflicts_removed",
    "c2_canonical_priority",
)


def _dialogue(value: object) -> str:
    return parse_assistant_response(canonicalize_model_output(value)).dialogue


def _generate(provider, context, character_prompt, seed) -> tuple[str, float]:
    started = time.perf_counter()
    response = provider.generate(context, character_prompt, seed=seed)
    return _dialogue(response), (time.perf_counter() - started) * 1000.0


def _raw_validation(case, design, response, *, requires_source):
    case_validation = validate_historical_interpretation(case, response)
    contract_validation = (
        validate_supplemental_historical_response(design, response)
        if requires_source else None
    )
    accepted = bool(
        case_validation.accepted
        and (contract_validation is None or contract_validation.accepted)
    )
    return accepted, case_validation, contract_validation


def _repair(provider, character_prompt, case, design, rejected, seed):
    prompt = historical_response_repair_prompt(design, case.query, rejected)
    started = time.perf_counter()
    bounded = getattr(provider, "generate_bounded", None)
    if callable(bounded):
        raw = bounded(
            [], character_prompt + "\n\n" + prompt,
            max_output_tokens=220, seed=seed,
        )
    else:
        raw = provider.generate([], character_prompt + "\n\n" + prompt, seed=seed)
    elapsed = (time.perf_counter() - started) * 1000.0
    response = _dialogue(raw)
    accepted, case_validation, contract_validation = _raw_validation(
        case, design, response, requires_source=True,
    )
    fallback = None
    if not accepted:
        fallback_response = historical_response_fallback_dialogue(design)
        fallback_accepted, fallback_case, fallback_contract = _raw_validation(
            case, design, fallback_response, requires_source=True,
        )
        fallback = {
            "response": fallback_response,
            "accepted": fallback_accepted,
            "case_validation": asdict(fallback_case),
            "contract_validation": asdict(fallback_contract),
            "persisted": False,
            "tts_submitted": False,
            "published": False,
        }
    return {
        "response": response,
        "generation_ms": round(elapsed, 3),
        "accepted": accepted,
        "case_validation": asdict(case_validation),
        "contract_validation": asdict(contract_validation),
        "fallback": fallback,
        "persisted": False,
        "tts_submitted": False,
        "published": False,
    }


def _empty_metrics() -> dict[str, object]:
    return {
        "raw_acceptable": 0,
        "raw_rejected": 0,
        "source_communicated": 0,
        "attribution_errors": 0,
        "polarity_inversions": 0,
        "modality_strengthening": 0,
        "temporal_current_truth_promotion": 0,
        "unsupported_inference": 0,
        "source_omission": 0,
        "repair_required": 0,
        "repair_successes": 0,
        "fallback_required": 0,
        "fallback_failures": 0,
        "generation_ms": [],
        "repair_ms": [],
    }


def _accumulate(metrics, row, *, requires_source):
    metrics["raw_acceptable"] += int(row["raw_acceptable"])
    metrics["raw_rejected"] += int(not row["raw_acceptable"])
    case = row["case_validation"]
    metrics["source_communicated"] += int(case["source_communicated"])
    violations = set(case["violations"])
    contract = row.get("contract_validation")
    if contract:
        violations.update(contract["violations"])
    metrics["attribution_errors"] += sum("attribution" in item for item in violations)
    metrics["polarity_inversions"] += sum(
        "negation" in item or "positive_assertion_inverted" in item
        for item in violations
    )
    metrics["modality_strengthening"] += sum("modality" in item for item in violations)
    metrics["temporal_current_truth_promotion"] += sum(
        "temporal" in item or "current_truth" in item or "current_state" in item
        for item in violations
    )
    metrics["unsupported_inference"] += sum(
        "unsupported" in item or "strengthening" in item for item in violations
    )
    metrics["source_omission"] += sum("omitted" in item for item in violations)
    metrics["generation_ms"].append(row["generation_ms"])
    repair = row.get("repair")
    if repair is not None:
        metrics["repair_required"] += 1
        metrics["repair_successes"] += int(repair["accepted"])
        metrics["repair_ms"].append(repair["generation_ms"])
        fallback = repair.get("fallback")
        if fallback is not None:
            metrics["fallback_required"] += 1
            metrics["fallback_failures"] += int(not fallback["accepted"])


def _finish_metrics(metrics):
    result = dict(metrics)
    for key in ("generation_ms", "repair_ms"):
        values = tuple(float(value) for value in result.pop(key))
        result[key + "_mean"] = round(sum(values) / len(values), 3) if values else None
        result[key + "_max"] = round(max(values), 3) if values else None
    total = int(result["raw_acceptable"]) + int(result["raw_rejected"])
    result["raw_acceptance_rate"] = round(
        int(result["raw_acceptable"]) / total, 6,
    ) if total else None
    return result


def run_arbitrated_interpretation_evaluation(
    provider: object,
    character_prompt: str,
    contexts: Mapping[str, Sequence[Mapping[str, object]]],
    v1_selected: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    cases: Sequence[HistoricalInterpretationCase] = SYNTHETIC_INTERPRETATION_CASES,
    seeds: Sequence[int] = DEFAULT_MATCHED_SEEDS,
    govern_rejected: bool = True,
) -> dict[str, object]:
    """Run immutable A/B/C1/C2 generations and then bounded governance."""
    rows: list[dict[str, object]] = []
    arbitration_latency: list[float] = []
    case_by_id = {case.case_id: case for case in tuple(cases)}
    design_by_id = {}
    for case in case_by_id.values():
        design = compose_supplemental_historical_prompt(
            case.query, (interpretation_candidate(case),),
        )
        if not design.triggered:
            raise RuntimeError(f"case was not structurally admitted: {case.case_id}")
        design_by_id[case.case_id] = design
        production = [dict(item) for item in contexts[case.case_id]]
        selected = tuple(dict(item) for item in v1_selected.get(case.case_id, ()))
        arbitration = arbitrate_historical_callback_evidence(
            case.query, selected, design,
        )
        arbitration_latency.append(arbitration.latency_ms)
        variant_contexts = {
            "v1_only": production,
            "v1_plus_v2": counterfactual_context_with_block(
                production, design.prompt_block,
            ),
            "c1_conflicts_removed": arbitrated_counterfactual_context(
                production, arbitration, design, variant="c1_conflicts_removed",
            ),
            "c2_canonical_priority": arbitrated_counterfactual_context(
                production, arbitration, design, variant="c2_canonical_priority",
            ),
        }
        for seed in tuple(int(value) for value in seeds):
            variants: dict[str, object] = {}
            # Freeze every raw A/B/C result. Governance is a separate pass only
            # after the complete raw suite exists.
            for name in ARBITRATION_VARIANTS:
                response, elapsed = _generate(
                    provider, variant_contexts[name], character_prompt, seed,
                )
                requires_source = name != "v1_only"
                accepted, case_validation, contract_validation = _raw_validation(
                    case, design, response, requires_source=requires_source,
                )
                row = {
                    "response": response,
                    "generation_ms": round(elapsed, 3),
                    "raw_acceptable": accepted,
                    "case_validation": asdict(case_validation),
                    "contract_validation": (
                        asdict(contract_validation)
                        if contract_validation is not None else None
                    ),
                    "context_characters": sum(
                        len(str(item.get("content", "")))
                        for item in variant_contexts[name]
                    ),
                    "repair": None,
                    "persisted": False,
                    "tts_submitted": False,
                    "published": False,
                }
                variants[name] = row
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
                "priority_block": arbitration.priority_block,
                "arbitration": {
                    "latency_ms": arbitration.latency_ms,
                    "decisions": [asdict(item) for item in arbitration.decisions],
                    "c1_retained_ids": [str(item.get("id")) for item in arbitration.c1_items],
                    "c2_retained_ids": [str(item.get("id")) for item in arbitration.c2_items],
                },
                "variants": variants,
            })
    if govern_rejected:
        for evaluation_row in rows:
            case = case_by_id[str(evaluation_row["case_id"])]
            design = design_by_id[case.case_id]
            seed = int(evaluation_row["seed"])
            variants = evaluation_row["variants"]
            for name in ARBITRATION_VARIANTS[1:]:
                row = variants[name]
                if not bool(row["raw_acceptable"]):
                    row["repair"] = _repair(
                        provider, character_prompt, case, design,
                        str(row["response"]), seed,
                    )
    metrics = {variant: _empty_metrics() for variant in ARBITRATION_VARIANTS}
    for evaluation_row in rows:
        variants = evaluation_row["variants"]
        for name in ARBITRATION_VARIANTS:
            _accumulate(
                metrics[name], variants[name], requires_source=name != "v1_only",
            )
    return {
        "case_count": len(case_by_id),
        "seed_count": len(tuple(seeds)),
        "row_count": len(rows),
        "variant_count": len(ARBITRATION_VARIANTS),
        "governance_enabled": bool(govern_rejected),
        "arbitration_latency_ms_mean": round(
            sum(arbitration_latency) / len(arbitration_latency), 6,
        ) if arbitration_latency else None,
        "arbitration_latency_ms_max": round(max(arbitration_latency), 6) if arbitration_latency else None,
        "metrics": {name: _finish_metrics(value) for name, value in metrics.items()},
        "rows": rows,
    }


__all__ = [
    "ARBITRATION_VARIANTS",
    "run_arbitrated_interpretation_evaluation",
]
