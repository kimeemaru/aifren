"""Frozen, benchmark-only admission experiment for current avatar headwear.

The production active-state lookup is exercised for every query.  This module
does not construct a prompt or alter production admission/prompt behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import uuid

from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository


_MANIFEST = Path(__file__).with_name("active_headwear_admission_manifest.json")
_SLOT = "active.avatar.headwear"
# This deliberately recognizes a compact grammatical shape rather than frozen
# sentence strings: an explicit present-tense question about *her* tracked
# avatar's headwear.  It is not a general state intent classifier and does not
# infer referents, hypotheticals, advice, or third-party subjects.
_CURRENT_HEADWEAR_PATTERNS = (
    re.compile(r"^what(?: is|'s) she wearing(?: on)? her head\??$"),
    re.compile(r"^is she wearing (?:a |any )?(?:hat|cap|anything)(?: on her head)?\??$"),
    re.compile(r"^what(?: is|'s) on her head\??$"),
    re.compile(r"^what (?:hat|cap) does she have on\??$"),
    re.compile(r"^what(?: is|'s) she got on her head\??$"),
    re.compile(r"^describe her (?:current )?headwear\.?$"),
    re.compile(r"^what (?:hat|cap) is she wearing\??$"),
)
_FIXTURE_MESSAGES = {
    "set": (("She is wearing a red hat.", "2020-01-01T00:00:00Z"),),
    "replaced": (
        ("She is wearing a red hat.", "2020-01-01T00:00:00Z"),
        ("She is wearing a blue hat.", "2021-01-01T00:00:00Z"),
    ),
    "cleared": (
        ("She is wearing a red hat.", "2020-01-01T00:00:00Z"),
        ("She is wearing a blue hat.", "2021-01-01T00:00:00Z"),
        ("She took the hat off.", "2022-01-01T00:00:00Z"),
    ),
}


@dataclass(frozen=True)
class ActiveHeadwearAdmissionCase:
    case_id: str
    category: str
    state_fixture: str
    query: str
    expected: str


@dataclass(frozen=True)
class ActiveHeadwearAdmissionTrace:
    case_id: str
    category: str
    state_fixture: str
    lookup_executed: bool
    state_found: bool
    candidate_claim_id: str | None
    candidate_value: str | None
    selected_claim_id: str | None
    admitted_claim_id: str | None
    decision_reason: str
    wrong_character_violation: bool
    lifecycle_violation: bool
    provenance_violation: bool


@dataclass(frozen=True)
class ActiveHeadwearAdmissionReport:
    traces: tuple[ActiveHeadwearAdmissionTrace, ...]
    total_prompts: int
    current_state_relevant_prompts: int
    correct_admissions: int
    relevant_false_withholds: int
    unrelated_prompts: int
    correct_withholds: int
    irrelevant_admissions: int
    hypothetical_contamination_failures: int
    historical_state_as_current_failures: int
    clear_resurrection_failures: int
    lookup_count: int
    state_found_count: int
    selection_count: int
    admission_count: int
    wrong_character_violations: int
    lifecycle_violations: int
    provenance_violations: int
    unset_current_queries: int
    correct_unset_withholds: int


def load_active_headwear_admission_manifest(
    path: Path = _MANIFEST,
) -> tuple[ActiveHeadwearAdmissionCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != "active-headwear-admission-v1":
        raise ValueError("invalid active-headwear admission manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or len(rows) != 22:
        raise ValueError("active-headwear admission manifest must contain exactly 22 cases")
    cases = tuple(ActiveHeadwearAdmissionCase(**row) for row in rows)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("active-headwear admission case IDs must be unique")
    expected_categories = {
        "direct_current": 3, "current_paraphrase": 3, "replacement": 1,
        "clear_unset": 2, "historical": 2, "creative": 3, "advice": 3,
        "hypothetical": 3, "third_person": 2,
    }
    counts = {category: sum(case.category == category for case in cases) for category in expected_categories}
    if counts != expected_categories:
        raise ValueError("active-headwear admission category coverage changed")
    if any(case.state_fixture not in _FIXTURE_MESSAGES for case in cases):
        raise ValueError("active-headwear admission fixture is invalid")
    if any(case.expected not in {"admit", "unset", "withhold", "withhold_historical"} for case in cases):
        raise ValueError("active-headwear admission expectation is invalid")
    return cases


def active_headwear_admission_relevant(query: object) -> bool:
    """Narrow, current-avatar headwear grammar; not a general state router."""
    normalized = " ".join(str(query).lower().split())
    return any(pattern.fullmatch(normalized) is not None for pattern in _CURRENT_HEADWEAR_PATTERNS)


def _fixture_lookup(root: Path, state_fixture: str):
    character_id = str(uuid.uuid4())
    messages = [
        {"role": "user", "content": content, "timestamp": timestamp}
        for content, timestamp in _FIXTURE_MESSAGES[state_fixture]
    ]
    conversation = root / "conversation.json"
    conversation.write_text(json.dumps(messages), encoding="utf-8")
    writer = MemoryV2ShadowWriter(
        root, character_id=character_id, display_name="Admission fixture",
        memory_file=root / "memories.json",
    )
    for index, message in enumerate(messages):
        outcome = writer.observe_canonical_user_active_state(
            message, conversation_index=index, conversation_file=conversation,
        )
        if outcome["state"] not in {"created", "replaced", "cleared"}:
            writer.close()
            raise RuntimeError(f"fixture population failed: {outcome}")
    lookup = MemoryV2Repository(writer.store).lookup_active_state(character_id, _SLOT)
    return writer, character_id, lookup


def evaluate_active_headwear_admission(
    cases: tuple[ActiveHeadwearAdmissionCase, ...] | None = None,
) -> ActiveHeadwearAdmissionReport:
    """Exercise exact current-state lookup and benchmark-only relevance policy."""
    values = cases or load_active_headwear_admission_manifest()
    traces = []
    for case in values:
        with tempfile.TemporaryDirectory() as directory:
            writer, character_id, lookup = _fixture_lookup(Path(directory), case.state_fixture)
            try:
                state = lookup.state
                relevant = active_headwear_admission_relevant(case.query)
                selected = state if relevant and state is not None else None
                admitted = selected  # Benchmark-only: selected state would become background context.
                traces.append(ActiveHeadwearAdmissionTrace(
                    case.case_id, case.category, case.state_fixture, True, state is not None,
                    state.state_id if state else None, state.value if state else None,
                    selected.state_id if selected else None, admitted.state_id if admitted else None,
                    "current_headwear_recall" if admitted else (
                        "current_state_unset" if state is None else "conservative_withhold"
                    ),
                    state is not None and state.character_id != character_id,
                    state is not None and state.status != "active",
                    state is not None and not state.evidence_event_ids,
                ))
            finally:
                writer.close()
    relevant = [item for item, case in zip(traces, values) if case.expected == "admit"]
    unrelated = [item for item, case in zip(traces, values) if case.expected == "withhold"]
    unset = [item for item, case in zip(traces, values) if case.expected == "unset"]
    historical = [item for item, case in zip(traces, values) if case.expected == "withhold_historical"]
    return ActiveHeadwearAdmissionReport(
        traces=tuple(traces), total_prompts=len(traces),
        current_state_relevant_prompts=len(relevant),
        correct_admissions=sum(item.admitted_claim_id is not None for item in relevant),
        relevant_false_withholds=sum(item.admitted_claim_id is None for item in relevant),
        unrelated_prompts=len(unrelated),
        correct_withholds=sum(item.admitted_claim_id is None for item in unrelated),
        irrelevant_admissions=sum(item.admitted_claim_id is not None for item in unrelated),
        hypothetical_contamination_failures=sum(
            item.admitted_claim_id is not None for item in traces if item.category == "hypothetical"
        ),
        historical_state_as_current_failures=sum(item.admitted_claim_id is not None for item in historical),
        clear_resurrection_failures=sum(item.state_found or item.admitted_claim_id is not None for item in unset),
        lookup_count=sum(item.lookup_executed for item in traces),
        state_found_count=sum(item.state_found for item in traces),
        selection_count=sum(item.selected_claim_id is not None for item in traces),
        admission_count=sum(item.admitted_claim_id is not None for item in traces),
        wrong_character_violations=sum(item.wrong_character_violation for item in traces),
        lifecycle_violations=sum(item.lifecycle_violation for item in traces),
        provenance_violations=sum(item.provenance_violation for item in traces),
        unset_current_queries=len(unset),
        correct_unset_withholds=sum(
            not item.state_found and item.admitted_claim_id is None for item in unset
        ),
    )
