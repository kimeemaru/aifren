"""Frozen, benchmark-only admission experiment for ``identity.name``.

This does not construct prompts or call an LLM.  It tests only the boundary
between a cheap durable lookup and a future, separate prompt-admission choice.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from memory_v2_store import MemoryV2Repository, identity_name_admission_relevant


_MANIFEST = Path(__file__).with_name("identity_name_admission_manifest.json")
@dataclass(frozen=True)
class AdmissionCase:
    case_id: str
    category: str
    query: str
    expected: str


@dataclass(frozen=True)
class AdmissionTrace:
    case_id: str
    category: str
    lookup_executed: bool
    candidate_claim_ids: tuple[str, ...]
    selected_claim_id: str | None
    admitted_claim_id: str | None
    decision_reason: str
    wrong_character_violation: bool
    lifecycle_violation: bool
    provenance_violation: bool


@dataclass(frozen=True)
class AdmissionReport:
    traces: tuple[AdmissionTrace, ...]
    total_prompts: int
    relevant_identity_prompts: int
    correct_admissions: int
    relevant_false_withholds: int
    unrelated_prompts: int
    correct_withholds: int
    irrelevant_admissions: int
    hypothetical_contamination_failures: int
    third_person_contamination_failures: int
    lookup_count: int
    candidate_found_count: int
    selection_count: int
    admission_count: int
    wrong_character_violations: int
    lifecycle_violations: int
    provenance_violations: int
    self_reference_admissions: int


def load_admission_manifest(path: Path = _MANIFEST) -> tuple[AdmissionCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != "identity-name-admission-v1":
        raise ValueError("invalid identity-name admission manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or len(rows) != 21:
        raise ValueError("identity-name admission manifest must contain exactly 21 cases")
    cases = tuple(AdmissionCase(**row) for row in rows)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("identity-name admission case IDs must be unique")
    expected_categories = {
        "direct_identity": 3, "identity_paraphrase": 3, "self_reference": 2,
        "creative": 4, "advice": 4, "hypothetical": 3, "third_person": 2,
    }
    counts = {category: sum(case.category == category for case in cases) for category in expected_categories}
    if counts != expected_categories:
        raise ValueError("identity-name admission category coverage changed")
    if any(case.expected not in {"admit", "withhold", "withhold_product_dependent"} for case in cases):
        raise ValueError("identity-name admission expectation is invalid")
    return cases


def evaluate_identity_name_admission(
    repository: MemoryV2Repository,
    character_id: str,
    cases: tuple[AdmissionCase, ...] | None = None,
) -> AdmissionReport:
    """Run the lookup for every prompt and record admission separately."""
    values = cases or load_admission_manifest()
    traces = []
    for case in values:
        lookup = repository.lookup_durable_core(character_id, "identity.name")
        candidates = lookup.candidates
        relevant = identity_name_admission_relevant(case.query)
        selected = candidates[0] if relevant and len(candidates) == 1 else None
        admitted = selected  # Benchmark-only: selected background evidence would be admitted.
        traces.append(AdmissionTrace(
            case.case_id, case.category, True, tuple(item.claim_id for item in candidates),
            selected.claim_id if selected else None, admitted.claim_id if admitted else None,
            "explicit_identity_recall" if admitted else "conservative_withhold",
            any(item.character_id != character_id for item in candidates),
            any(item.status != "active" for item in candidates),
            any(not item.evidence_event_ids for item in candidates),
        ))
    relevant = [item for item, case in zip(traces, values) if case.expected == "admit"]
    unrelated = [item for item, case in zip(traces, values) if case.expected == "withhold"]
    self_reference = [item for item, case in zip(traces, values) if case.expected == "withhold_product_dependent"]
    return AdmissionReport(
        traces=tuple(traces), total_prompts=len(traces), relevant_identity_prompts=len(relevant),
        correct_admissions=sum(item.admitted_claim_id is not None for item in relevant),
        relevant_false_withholds=sum(item.admitted_claim_id is None for item in relevant),
        unrelated_prompts=len(unrelated), correct_withholds=sum(item.admitted_claim_id is None for item in unrelated),
        irrelevant_admissions=sum(item.admitted_claim_id is not None for item in unrelated),
        hypothetical_contamination_failures=sum(item.admitted_claim_id is not None for item in traces if item.category == "hypothetical"),
        third_person_contamination_failures=sum(item.admitted_claim_id is not None for item in traces if item.category == "third_person"),
        lookup_count=sum(item.lookup_executed for item in traces),
        candidate_found_count=sum(bool(item.candidate_claim_ids) for item in traces),
        selection_count=sum(item.selected_claim_id is not None for item in traces),
        admission_count=sum(item.admitted_claim_id is not None for item in traces),
        wrong_character_violations=sum(item.wrong_character_violation for item in traces),
        lifecycle_violations=sum(item.lifecycle_violation for item in traces),
        provenance_violations=sum(item.provenance_violation for item in traces),
        self_reference_admissions=sum(item.admitted_claim_id is not None for item in self_reference),
    )
