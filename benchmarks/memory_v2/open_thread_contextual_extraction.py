"""Frozen provider-neutral evaluation for untrusted Open Thread proposals."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any

from aifren.memory_v2_store import (
    OPEN_THREAD_KINDS, OPEN_THREAD_PARTICIPANT_SCOPES, OpenThreadProposal,
    OpenThreadProposalOperation, validate_open_thread_proposal,
)

_MANIFEST = Path(__file__).with_name("open_thread_contextual_extraction_manifest.json")
_RESULT = Path(__file__).with_name("_results") / "open_thread_contextual_extraction_last_run.json"


@dataclass(frozen=True)
class ThreadSnapshot:
    reference: str
    kind: str
    scope: str
    topic: str


@dataclass(frozen=True)
class ThreadScenario:
    case_id: str
    category: str
    turn: str
    recent: tuple[str, ...]
    threads: tuple[ThreadSnapshot, ...]
    required: tuple[dict[str, str], ...]
    reject: bool


@dataclass(frozen=True)
class ProposalOperation:
    operation: str
    reference: str
    kind: str | None = None
    scope: str | None = None
    description: str | None = None
    anchor: str | None = None


@dataclass(frozen=True)
class ParsedCandidate:
    proposal: OpenThreadProposal | None
    operations: tuple[ProposalOperation, ...]


@dataclass(frozen=True)
class CaseTrace:
    case_id: str
    category: str
    classification: str
    required_count: int
    proposed_count: int
    contract_valid: bool
    missing: int
    forbidden: int
    wrong_reference: bool
    wrong_scope: bool
    malformed: bool
    failure_stage: str | None = None


@dataclass(frozen=True)
class OpenThreadExtractionReport:
    provider_model: str | None
    provider_calls: int
    latency_ms: float | None
    malformed_count: int
    traces: tuple[CaseTrace, ...]


def load_manifest(path: Path = _MANIFEST) -> tuple[ThreadScenario, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != "open-thread-contextual-extraction-v1":
        raise ValueError("open-thread manifest version is invalid")
    rows = raw.get("cases")
    if not isinstance(rows, list) or not 30 <= len(rows) <= 50:
        raise ValueError("open-thread manifest case count is invalid")
    cases = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("case_id", "category", "turn")):
            raise ValueError("open-thread manifest case is malformed")
        threads = []
        for item in row.get("threads", []):
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("ref", "kind", "scope", "topic")):
                raise ValueError("open-thread manifest snapshot is malformed")
            if item["kind"] not in OPEN_THREAD_KINDS or item["scope"] not in OPEN_THREAD_PARTICIPANT_SCOPES:
                raise ValueError("open-thread manifest snapshot is not governed")
            threads.append(ThreadSnapshot(item["ref"], item["kind"], item["scope"], item["topic"]))
        required = tuple(row.get("required", []))
        if not all(isinstance(item, dict) and item.get("operation") in {"open", "reconfirm", "resolve", "cancel"} for item in required):
            raise ValueError("open-thread manifest expected operation is invalid")
        reject = bool(row.get("reject", False))
        if reject and required:
            raise ValueError("open-thread rejection case cannot require a proposal")
        cases.append(ThreadScenario(row["case_id"], row["category"], row["turn"], tuple(row.get("recent", [])), tuple(threads), required, reject))
    if len({item.case_id for item in cases}) != len(cases):
        raise ValueError("open-thread manifest IDs must be unique")
    return tuple(cases)


def provider_input(cases: tuple[ThreadScenario, ...]) -> dict[str, object]:
    return {"cases": [{
        "case_id": case.case_id, "canonical_user_turn": case.turn,
        "recent_turns": list(case.recent[-4:]),
        "current_open_threads": [asdict(item) for item in case.threads],
        "governed_operations": ["open", "reconfirm", "resolve", "cancel"],
        "governed_kinds": sorted(OPEN_THREAD_KINDS),
        "governed_scopes": sorted(OPEN_THREAD_PARTICIPANT_SCOPES),
    } for case in cases]}


INSTRUCTIONS = """You are an untrusted Open Thread proposal generator for a companion app.
Return JSON only: {"cases":[{"case_id":"...","proposal":null|{"operations":[...]}]}.
Each operation has operation, ref, evidence; open also has kind, scope, description and optional anchor.
Existing refs must be supplied current_open_threads refs. Open refs are short proposal-local refs.
Extract only an explicit current unfinished/continued/resolved/cancelled thread grounded in the user turn.
Use recent turns only to resolve a clearly unique reference. Withhold on ambiguity. Never create or close a
thread from past-only, future-only, hypothetical, quoted, fictional, assistant-authored, or question wording.
Do not invent deadlines, topics, kinds, scopes, or instructions."""


def _thread_id(ref: str) -> str:
    return f"thread-{uuid.uuid5(uuid.NAMESPACE_URL, 'aifren:benchmark-thread:' + ref)}"


def _span(text: str, evidence: object) -> tuple[int, int]:
    if not isinstance(evidence, str) or not evidence:
        raise ValueError("evidence is required")
    start = text.find(evidence)
    if start < 0 or text.find(evidence, start + 1) >= 0:
        raise ValueError("evidence must be one exact canonical span")
    return start, start + len(evidence)


def _parse_case(case: ThreadScenario, raw: object) -> ParsedCandidate:
    if raw is None:
        return ParsedCandidate(None, ())
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        raise ValueError("proposal shape is invalid")
    refs = {item.reference: _thread_id(item.reference) for item in case.threads}
    proposed: list[OpenThreadProposalOperation] = []
    comparable: list[ProposalOperation] = []
    for item in raw["operations"]:
        if not isinstance(item, dict):
            raise ValueError("proposal operation is invalid")
        operation, reference = item.get("operation"), item.get("ref")
        start, end = _span(case.turn, item.get("evidence"))
        if operation == "open":
            proposed.append(OpenThreadProposalOperation(
                "open", reference, start, end, item.get("kind"), item.get("scope"),
                item.get("description"), item.get("anchor"),
            ))
            comparable.append(ProposalOperation("open", str(reference), item.get("kind"), item.get("scope"), item.get("description"), item.get("anchor")))
        else:
            if reference not in refs:
                raise ValueError("existing thread reference was not supplied")
            proposed.append(OpenThreadProposalOperation(operation, refs[reference], start, end))
            comparable.append(ProposalOperation(str(operation), str(reference)))
    proposal = OpenThreadProposal(tuple(proposed)) if proposed else None
    if proposal is not None:
        validate_open_thread_proposal(proposal)
    return ParsedCandidate(proposal, tuple(comparable))


def parse_provider_output(cases: tuple[ThreadScenario, ...], response: str) -> tuple[dict[str, ParsedCandidate], dict[str, str]]:
    raw_text = response.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        rows = json.loads(raw_text).get("cases")
    except (ValueError, json.JSONDecodeError):
        return {}, {case.case_id: "transport_json" for case in cases}
    if not isinstance(rows, list):
        return {}, {case.case_id: "transport_shape" for case in cases}
    by_id = {item.get("case_id"): item.get("proposal") for item in rows if isinstance(item, dict) and isinstance(item.get("case_id"), str) and "proposal" in item}
    candidates, failures = {}, {}
    for case in cases:
        if case.case_id not in by_id:
            failures[case.case_id] = "transport_missing_case"
            continue
        try:
            candidates[case.case_id] = _parse_case(case, by_id[case.case_id])
        except ValueError as error:
            failures[case.case_id] = "binding" if "evidence" in str(error) else "proposal_contract"
    return candidates, failures


def _key(operation: ProposalOperation) -> tuple[object, ...]:
    return operation.operation, operation.reference, operation.kind, operation.scope, operation.description, operation.anchor


def evaluate(cases: tuple[ThreadScenario, ...], candidates: dict[str, ParsedCandidate], failures: dict[str, str]) -> OpenThreadExtractionReport:
    traces = []
    for case in cases:
        candidate = candidates.get(case.case_id)
        if candidate is None:
            traces.append(CaseTrace(case.case_id, case.category, "malformed", len(case.required), 0, False, len(case.required), 0, False, False, True, failures.get(case.case_id)))
            continue
        actual = {_key(item) for item in candidate.operations}
        expected = {_key(ProposalOperation(item["operation"], item["ref"], item.get("kind"), item.get("scope"), item.get("description"), item.get("anchor"))) for item in case.required}
        missing, forbidden = len(expected - actual), len(actual - expected)
        refs = {thread.reference for thread in case.threads}
        wrong_ref = any(item.operation != "open" and item.reference not in refs for item in candidate.operations)
        wrong_scope = any(item.operation == "open" and item.scope not in OPEN_THREAD_PARTICIPANT_SCOPES for item in candidate.operations)
        if case.reject:
            classification = "safe_abstention" if not actual else "hard_safety_failure"
        elif forbidden or wrong_ref or wrong_scope:
            classification = "hard_safety_failure"
        elif missing:
            classification = "false_omission"
        else:
            classification = "correct_proposal"
        traces.append(CaseTrace(case.case_id, case.category, classification, len(expected), len(actual), True, missing, forbidden, wrong_ref, wrong_scope, False))
    return OpenThreadExtractionReport(None, 0, None, sum(item.malformed for item in traces), tuple(traces))


def run_provider_evaluation(*, provider: Any | None = None, batch_size: int = 8, result_path: Path = _RESULT) -> tuple[OpenThreadExtractionReport, dict[str, ParsedCandidate], dict[str, str]]:
    from aifren.runtime.config import GEMINI_MODEL
    from aifren.llm.gemini import Gemini
    cases = load_manifest()
    selected = provider or Gemini()
    candidates: dict[str, ParsedCandidate] = {}
    failures: dict[str, str] = {}
    elapsed = 0.0
    records = []
    for index in range(0, len(cases), batch_size):
        batch = cases[index:index + batch_size]
        started = time.perf_counter()
        response = str(selected.generate([{"role":"user", "content":json.dumps(provider_input(batch), ensure_ascii=False)}], INSTRUCTIONS))
        latency = (time.perf_counter() - started) * 1000.0
        elapsed += latency
        parsed, failed = parse_provider_output(batch, response)
        candidates.update(parsed); failures.update(failed)
        # Persist normalized synthetic candidates, rather than raw provider text, so a
        # formatter/reporting failure cannot make an already-completed evaluation
        # impossible to inspect.  This benchmark never sends real conversation data.
        records.append({
            "case_ids":[case.case_id for case in batch],
            "latency_ms":latency,
            "response_sha256":hashlib.sha256(response.encode()).hexdigest(),
            "parse_failures":failed,
            "parsed_candidates": {
                case_id: [asdict(operation) for operation in candidate.operations]
                for case_id, candidate in parsed.items()
            },
        })
    scored = evaluate(cases, candidates, failures)
    report = OpenThreadExtractionReport(GEMINI_MODEL, len(records), elapsed, len(failures), scored.traces)
    payload = {"format":"open-thread-contextual-extraction-run-v1", "provider_model":GEMINI_MODEL, "provider_calls":len(records), "latency_ms":elapsed, "batches":records, "report":asdict(report)}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return report, candidates, failures
