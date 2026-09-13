"""Opt-in, validation-only contextual Active State shadow observation.

This module deliberately has no Gemini dependency and never applies a
proposal.  Provider adapters may translate their structured output into the
small candidate contract below; the stable boundary is candidate -> governed
ActiveStateProposal -> deterministic validation -> diagnostic trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Protocol

from aifren.memory_v2_store import (
    ACTIVE_STATE_ACTOR_ATTRIBUTES,
    ACTIVE_STATE_REGISTRY,
    ACTIVE_STATE_SCENE_ATTRIBUTES,
    ActiveSceneSubjectRetirement,
    ActiveStateProposal,
    ActiveStateProposalUpdate,
    MemoryV2Repository,
    MemoryV2Store,
    validate_active_state_proposal,
)


@dataclass(frozen=True)
class ContextualSceneSubject:
    """Provider-visible local scene label; never exposes a database ID."""

    reference: str
    kind: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class ContextualActiveStateExtractorInput:
    """The bounded, provider-neutral context required for one user turn."""

    latest_user_turn: str
    recent_user_turns: tuple[str, ...]
    actor_state: dict[str, dict[str, str]]
    scene_subjects: tuple[ContextualSceneSubject, ...]
    governed_global_slots: tuple[str, ...]
    governed_actor_attributes: tuple[str, ...]
    governed_scene_attributes: tuple[str, ...]


class ContextualActiveStateExtractor(Protocol):
    """Replaceable untrusted extractor; it has no store or write authority."""

    provider_id: str
    model_id: str | None

    def propose(self, extraction_input: ContextualActiveStateExtractorInput) -> ActiveStateProposal | None:
        """Return a candidate with local scene refs, or abstain with ``None``."""


class JsonlActiveStateShadowTraceStore:
    """Small rebuildable diagnostic store; normalized trace data only."""

    def __init__(self, path: str | Path, *, max_records: int = 200) -> None:
        self.path = Path(path)
        self.max_records = max_records

    def append(self, trace: dict[str, object]) -> None:
        if not isinstance(self.max_records, int) or not 1 <= self.max_records <= 1000:
            raise ValueError("active-state shadow trace bound is invalid")
        records: list[str] = []
        try:
            if self.path.exists():
                records = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            records = []
        encoded = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        records = (records + [encoded])[-self.max_records:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text("\n".join(records) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def _scope_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _operation_record(proposal: ActiveStateProposal) -> list[dict[str, object]]:
    operations: list[dict[str, object]] = []
    for introduction in proposal.introductions:
        operations.append({"target": f"new:{introduction.reference}", "attribute": "kind", "operation": "introduce", "value": introduction.kind})
    for update in proposal.updates:
        target = update.slot if update.target_kind == "slot" else f"{update.target_kind}:{update.target_ref}"
        operations.append({
            "target": target, "attribute": update.attribute, "operation": update.operation,
            "value": update.value, "basis": update.basis, "rule": update.consequence_rule_id,
        })
    for retirement in proposal.retirements:
        operations.append({"target": f"scene:{retirement.reference}", "attribute": None, "operation": "retire", "value": None})
    return operations


class ActiveStateContextualShadowObserver:
    """Fail-open observer for one already-persisted canonical user turn.

    It validates candidates but deliberately never imports or calls the store's
    proposal application method.  A future local-model adapter only needs to
    implement ``ContextualActiveStateExtractor.propose``.
    """

    def __init__(
        self,
        store: MemoryV2Store,
        *,
        character_id: str,
        extractor: ContextualActiveStateExtractor,
        trace_store: JsonlActiveStateShadowTraceStore,
        enabled: bool = False,
    ) -> None:
        self.store = store
        self.character_id = str(character_id)
        self.extractor = extractor
        self.trace_store = trace_store
        self.enabled = bool(enabled)

    @staticmethod
    def _canonical_message_is_persisted(
        message: object, *, conversation_index: int, conversation_file: str | Path,
    ) -> bool:
        if not isinstance(message, dict) or message.get("role") != "user":
            return False
        try:
            records = json.loads(Path(conversation_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(records, list) and 0 <= conversation_index < len(records)
            and isinstance(records[conversation_index], dict)
            and records[conversation_index].get("role") == "user"
            and records[conversation_index].get("content") == message.get("content")
            and records[conversation_index].get("timestamp") == message.get("timestamp")
        )

    def _build_input(self) -> tuple[ContextualActiveStateExtractorInput, dict[str, str]]:
        repository = MemoryV2Repository(self.store)
        actor_state: dict[str, dict[str, str]] = {}
        for actor in ("user", "companion"):
            values: dict[str, str] = {}
            for attribute in ACTIVE_STATE_ACTOR_ATTRIBUTES:
                state = repository.lookup_actor_state(self.character_id, actor, attribute).state
                if state is not None:
                    values[attribute] = state.value
            if values:
                actor_state[actor] = values
        subject_map: dict[str, str] = {}
        subjects: list[ContextualSceneSubject] = []
        for index, record in enumerate(repository.list_scene_subjects(self.character_id), 1):
            attributes = {
                item.subject_key.rsplit(".", 1)[1]: item.value
                for item in repository.lookup_scene_attributes(self.character_id, record.scene_subject_id)
            }
            kind = attributes.pop("kind", None)
            if not kind:
                continue
            reference = f"s{index}"
            subject_map[reference] = record.scene_subject_id
            subjects.append(ContextualSceneSubject(reference, kind, attributes))
        return ContextualActiveStateExtractorInput(
            latest_user_turn="", recent_user_turns=(), actor_state=actor_state,
            scene_subjects=tuple(subjects), governed_global_slots=tuple(sorted(ACTIVE_STATE_REGISTRY)),
            governed_actor_attributes=tuple(sorted(ACTIVE_STATE_ACTOR_ATTRIBUTES)),
            governed_scene_attributes=tuple(sorted(ACTIVE_STATE_SCENE_ATTRIBUTES)),
        ), subject_map

    @staticmethod
    def _bind_local_refs(
        candidate: ActiveStateProposal,
        subject_map: dict[str, str],
    ) -> ActiveStateProposal:
        introduced = {item.reference for item in candidate.introductions}
        updates: list[ActiveStateProposalUpdate] = []
        for update in candidate.updates:
            reference = update.target_ref
            if update.target_kind == "scene":
                if reference in subject_map:
                    reference = subject_map[str(reference)]
                elif reference not in introduced:
                    raise ValueError("unknown_scene_reference")
            updates.append(ActiveStateProposalUpdate(
                update.slot, update.operation, update.value, update.excerpt_start_cp, update.excerpt_end_cp,
                update.target_kind, reference, update.attribute, update.basis, update.consequence_rule_id,
            ))
        retirements: list[ActiveSceneSubjectRetirement] = []
        for retirement in candidate.retirements:
            if retirement.reference not in subject_map:
                raise ValueError("unknown_scene_reference")
            retirements.append(ActiveSceneSubjectRetirement(
                subject_map[retirement.reference], retirement.excerpt_start_cp, retirement.excerpt_end_cp,
            ))
        return ActiveStateProposal(tuple(updates), candidate.introductions, tuple(retirements))

    @staticmethod
    def _validate_source_spans(proposal: ActiveStateProposal, content: str) -> None:
        spans = [
            (item.excerpt_start_cp, item.excerpt_end_cp) for item in proposal.introductions
        ] + [
            (item.excerpt_start_cp, item.excerpt_end_cp) for item in proposal.updates if item.operation == "set"
        ] + [
            (item.excerpt_start_cp, item.excerpt_end_cp) for item in proposal.retirements
        ]
        if any(start is None or end is None or start < 0 or end < start or end > len(content) for start, end in spans):
            raise ValueError("evidence_binding_failure")

    @staticmethod
    def _risk_labels(
        proposal: ActiveStateProposal,
        scene_subjects: tuple[ContextualSceneSubject, ...],
    ) -> tuple[str, ...]:
        labels: set[str] = set()
        if proposal.introductions:
            labels.add("introduction")
        if proposal.retirements:
            labels.add("retirement")
        if len(proposal.introductions) + len(proposal.updates) + len(proposal.retirements) > 1:
            labels.add("multi_update")
        # This is only a diagnostic warning, not a rejection: a provider may
        # have been given two current objects of the same broad kind and should
        # abstain rather than guess between their local references.
        kinds = [subject.kind for subject in scene_subjects]
        if len(set(kinds)) != len(kinds):
            labels.add("ambiguous_scene_reference")
        for update in proposal.updates:
            if update.basis == "immediate_consequence":
                labels.add("immediate_consequence")
            if update.target_kind == "actor":
                labels.add(f"actor_target_{update.target_ref}")
        return tuple(sorted(labels))

    def observe_canonical_user_turn(
        self,
        message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, object]:
        """Observe one saved user turn; every outcome is non-authoritative."""
        if not self.enabled:
            return {"state": "disabled"}
        started = time.perf_counter()
        source = f"{Path(conversation_file).name}#{conversation_index}"
        content = message.get("content") if isinstance(message, dict) else None
        trace: dict[str, object] = {
            "format": "active-state-contextual-shadow-v1",
            "scope_hash": _scope_hash(self.character_id),
            "canonical_event_hash": _scope_hash(f"{source}:{hashlib.sha256(str(content).encode('utf-8')).hexdigest()}"),
            "provider": str(getattr(self.extractor, "provider_id", "unknown")),
            "model": getattr(self.extractor, "model_id", None),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "parse_status": "not_run",
            "evidence_binding_status": "not_run",
            "validation_status": "not_run",
            "operations": [],
            "risk_labels": [],
            "latency_ms": None,
            "error_category": None,
        }
        try:
            if not isinstance(content, str) or not self._canonical_message_is_persisted(
                message, conversation_index=conversation_index, conversation_file=conversation_file,
            ):
                trace.update(parse_status="skipped", evidence_binding_status="failed", validation_status="not_run", error_category="canonical_evidence_failure")
                return {"state": "rejected", "reason": "canonical_evidence_failure", "trace": trace}
            extraction_input, subject_map = self._build_input()
            extraction_input = ContextualActiveStateExtractorInput(
                content, extraction_input.recent_user_turns, extraction_input.actor_state,
                extraction_input.scene_subjects, extraction_input.governed_global_slots,
                extraction_input.governed_actor_attributes, extraction_input.governed_scene_attributes,
            )
            candidate = self.extractor.propose(extraction_input)
            if candidate is None:
                trace.update(parse_status="abstained", evidence_binding_status="not_applicable", validation_status="not_applicable")
                return {"state": "abstained", "trace": trace}
            if not isinstance(candidate, ActiveStateProposal):
                trace.update(parse_status="failed", error_category="malformed_output")
                return {"state": "rejected", "reason": "malformed_output", "trace": trace}
            trace["parse_status"] = "parsed"
            trace["operations"] = _operation_record(candidate)
            bound = self._bind_local_refs(candidate, subject_map)
            self._validate_source_spans(bound, content)
            trace["evidence_binding_status"] = "bound"
            validate_active_state_proposal(bound)
            trace["validation_status"] = "accepted"
            trace["risk_labels"] = list(self._risk_labels(candidate, extraction_input.scene_subjects))
            return {"state": "validated", "proposal": bound, "trace": trace}
        except ValueError as error:
            reason = str(error)
            trace.update(
                evidence_binding_status="failed" if "evidence" in reason else trace["evidence_binding_status"],
                validation_status="rejected", error_category=reason if reason in {"unknown_scene_reference", "evidence_binding_failure"} else "contract_rejected",
            )
            return {"state": "rejected", "reason": trace["error_category"], "trace": trace}
        except Exception as error:
            trace.update(parse_status="failed", validation_status="not_run", error_category=f"provider_{type(error).__name__}")
            return {"state": "failed", "reason": trace["error_category"], "trace": trace}
        finally:
            trace["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            try:
                self.trace_store.append(trace)
            except Exception:
                # Trace persistence is itself best-effort and cannot become a
                # user-turn failure or a reason to apply a candidate.
                pass
