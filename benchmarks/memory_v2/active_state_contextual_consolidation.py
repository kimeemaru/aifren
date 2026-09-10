"""One provider-neutral consolidation evaluation for contextual Active State proposals.

The extractor is untrusted and benchmark-only.  Native JSON schema, when a
provider offers it, is transport plumbing; all output still becomes a bounded
candidate and passes the real ActiveStateProposal validator before scoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .active_state_contextual_extraction import (
    ContextualExtractionRun,
    ContextualScenario,
    ParsedCandidate,
    ProposalOperation,
    _EXTRACTION_INSTRUCTIONS,
    add_result_metadata,
    evaluate_candidates,
    load_contextual_manifest,
    load_contextual_scenarios,
    run_provider_evaluation_details,
)


_HELDOUT_MANIFEST = Path(__file__).with_name("active_state_contextual_extraction_heldout_manifest.json")

CONSOLIDATED_EXTRACTION_GUIDANCE = """
Use the bounded current-scene roster as temporary local reference context, not as a permanent
entity graph. The supplied refs, kind, and current attributes are the only scene identities
available for this decision. When a current turn clearly describes exactly one supplied current
subject, update that supplied ref rather than introducing a duplicate. If several supplied
subjects could fit a pronoun or description, withhold rather than guess.

Introduce a new scene subject only for a concrete current object that needs independent evolving
state or later local reference continuity. Introduction by itself is valid and establishes only
the governed kind; do not invent an attribute just to justify it. Places, furniture, and scenery
normally belong as actor or object location/context values, not scene subjects.

Resolve canonical-user I/me/my as user and you/your as companion. Use explicit speaker_context
only when supplied for she/her. Emit every independent current change established by this turn,
but do not restate unchanged scene facts. For clear or retirement, emit only the governed change.
Current reconfirmations may repeat the same current value; do not calculate or reset timestamps.
""".strip()

CONSOLIDATED_INSTRUCTIONS = f"{_EXTRACTION_INSTRUCTIONS}\n\n{CONSOLIDATED_EXTRACTION_GUIDANCE}"


@dataclass(frozen=True)
class SuiteMetrics:
    total_cases: int
    explicit_required: int
    explicit_correct: int
    required_introductions: int
    correct_introductions: int
    missed_introductions: int
    unnecessary_introductions: int
    existing_subject_updates_required: int
    existing_subject_updates_correct: int
    actor_updates_required: int
    actor_updates_correct: int
    multi_update_cases: int
    multi_update_correct: int
    clear_retirement_required: int
    clear_retirement_correct: int
    immediate_required: int
    immediate_correct: int
    false_omissions: int
    over_inference: int
    hard_safety_failures: int
    malformed_contract_invalid: int
    safe_rejections: int


@dataclass(frozen=True)
class ConsolidatedExtractionReport:
    run: ContextualExtractionRun
    original: SuiteMetrics
    heldout: SuiteMetrics


def load_heldout_manifest() -> tuple[ContextualScenario, ...]:
    return load_contextual_scenarios(
        _HELDOUT_MANIFEST,
        version="active-state-contextual-extraction-heldout-v1",
        min_cases=20,
        max_cases=30,
        expected_categories={
            "heldout_subject_introduction", "heldout_existing_reference", "heldout_clear",
            "heldout_multi_update", "heldout_actor", "heldout_slot", "heldout_immediate",
            "heldout_rejection", "heldout_ambiguous", "heldout_time",
        },
    )


def _operation_key(operation: ProposalOperation) -> tuple[str, str | None, str, str | None, str, str | None]:
    return operation.target, operation.attribute, operation.operation, operation.value, operation.basis, operation.rule


def _expected(case: ContextualScenario) -> set[tuple[str, str | None, str, str | None, str, str | None]]:
    return {
        (item["target"], item.get("attribute"), item["operation"], item.get("value"), item["basis"], item.get("rule"))
        for item in case.required
    }


def suite_metrics(
    cases: tuple[ContextualScenario, ...],
    candidates: dict[str, ParsedCandidate],
    failures: dict[str, str],
) -> SuiteMetrics:
    report = evaluate_candidates(cases, candidates, failures)
    traces = {trace.case_id: trace for trace in report.traces}
    counters = {
        "explicit_required": 0, "explicit_correct": 0,
        "required_introductions": 0, "correct_introductions": 0, "missed_introductions": 0,
        "unnecessary_introductions": 0, "existing_subject_updates_required": 0,
        "existing_subject_updates_correct": 0, "actor_updates_required": 0,
        "actor_updates_correct": 0, "multi_update_cases": 0, "multi_update_correct": 0,
        "clear_retirement_required": 0, "clear_retirement_correct": 0,
        "immediate_required": 0, "immediate_correct": 0,
    }
    for case in cases:
        expected = _expected(case)
        actual = {_operation_key(item) for item in candidates.get(case.case_id, ParsedCandidate(None, ())).operations}
        explicit = {item for item in expected if item[4] == "explicit"}
        expected_new = {item for item in expected if item[0].startswith("new:") and item[1] == "kind"}
        actual_new = {item for item in actual if item[0].startswith("new:") and item[1] == "kind"}
        expected_existing = {item for item in expected if item[0].startswith("scene:")}
        expected_actor = {item for item in expected if item[0].startswith("actor:")}
        expected_clear_retire = {item for item in expected if item[2] in {"clear", "retire"}}
        expected_immediate = {item for item in expected if item[4] == "immediate_consequence"}
        counters["explicit_required"] += len(explicit)
        counters["explicit_correct"] += len(explicit & actual)
        counters["required_introductions"] += len(expected_new)
        counters["correct_introductions"] += len(expected_new & actual_new)
        counters["missed_introductions"] += len(expected_new - actual_new)
        counters["unnecessary_introductions"] += len(actual_new - expected_new)
        counters["existing_subject_updates_required"] += len(expected_existing)
        counters["existing_subject_updates_correct"] += len(expected_existing & actual)
        counters["actor_updates_required"] += len(expected_actor)
        counters["actor_updates_correct"] += len(expected_actor & actual)
        counters["clear_retirement_required"] += len(expected_clear_retire)
        counters["clear_retirement_correct"] += len(expected_clear_retire & actual)
        counters["immediate_required"] += len(expected_immediate)
        counters["immediate_correct"] += len(expected_immediate & actual)
        if not case.reject_mutation and len(expected) >= 2:
            counters["multi_update_cases"] += 1
            counters["multi_update_correct"] += int(expected == actual)
    return SuiteMetrics(
        total_cases=len(cases),
        false_omissions=sum(trace.classification == "false_omission" for trace in traces.values()),
        over_inference=sum(trace.forbidden_updates for trace in traces.values()),
        hard_safety_failures=sum(trace.classification == "hard_safety_failure" for trace in traces.values()),
        malformed_contract_invalid=sum(trace.malformed for trace in traces.values()),
        safe_rejections=sum(trace.classification == "safe_omission" for trace in traces.values()),
        **counters,
    )


def run_consolidated_evaluation(
    *, provider: Any | None = None, transport: Any | None = None, result_path: Path | None = None,
) -> ConsolidatedExtractionReport:
    """Run the original and held-out suites together once, without state writes."""
    original_cases = load_contextual_manifest()
    heldout_cases = load_heldout_manifest()
    run = run_provider_evaluation_details(
        cases=original_cases + heldout_cases,
        instructions=CONSOLIDATED_INSTRUCTIONS,
        provider=provider,
        transport=transport,
        result_path=result_path,
    )
    original = suite_metrics(original_cases, run.candidates, run.failures)
    heldout = suite_metrics(heldout_cases, run.candidates, run.failures)
    # The raw provider rows and parse/contract traces were already written by
    # the runner. Persist these split metrics before any caller formats them.
    add_result_metadata(run.result_path, {
        "original_suite": asdict(original),
        "heldout_suite": asdict(heldout),
    })
    return ConsolidatedExtractionReport(run, original, heldout)
