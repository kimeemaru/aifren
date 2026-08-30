"""Optional, validation-only runtime observer for untrusted Open Thread proposals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Protocol

from memory_v2_store import (
    OPEN_THREAD_KINDS, OPEN_THREAD_PARTICIPANT_SCOPES, MemoryV2Repository,
    MemoryV2Store, OpenThreadProposal, OpenThreadProposalOperation,
    validate_open_thread_proposal,
)


@dataclass(frozen=True)
class ContextualOpenThread:
    reference: str
    kind: str
    scope: str
    topic: str
    temporal_anchor: str | None


@dataclass(frozen=True)
class ContextualOpenThreadExtractorInput:
    latest_user_turn: str
    recent_user_turns: tuple[str, ...]
    current_open_threads: tuple[ContextualOpenThread, ...]
    governed_operations: tuple[str, ...]
    governed_kinds: tuple[str, ...]
    governed_scopes: tuple[str, ...]


class ContextualOpenThreadExtractor(Protocol):
    provider_id: str
    model_id: str | None
    def propose(self, extraction_input: ContextualOpenThreadExtractorInput) -> OpenThreadProposal | None: ...


class JsonlOpenThreadShadowTraceStore:
    def __init__(self, path: str | Path, *, max_records: int = 200) -> None:
        self.path, self.max_records = Path(path), max_records

    def append(self, trace: dict[str, object]) -> None:
        if not isinstance(self.max_records, int) or not 1 <= self.max_records <= 1000:
            raise ValueError("open-thread shadow trace bound is invalid")
        try:
            rows = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            rows = []
        rows = (rows + [json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])[-self.max_records:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class OpenThreadContextualShadowObserver:
    """Fail-open observer that deliberately never applies an OpenThreadProposal."""
    def __init__(self, store: MemoryV2Store, *, character_id: str, extractor: ContextualOpenThreadExtractor,
                 trace_store: JsonlOpenThreadShadowTraceStore, enabled: bool = False) -> None:
        self.store, self.character_id, self.extractor, self.trace_store, self.enabled = store, str(character_id), extractor, trace_store, bool(enabled)

    @staticmethod
    def _persisted(message: object, index: int, path: str | Path) -> bool:
        if not isinstance(message, dict) or message.get("role") != "user":
            return False
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(isinstance(rows, list) and 0 <= index < len(rows) and isinstance(rows[index], dict)
                    and rows[index].get("role") == "user" and rows[index].get("content") == message.get("content")
                    and rows[index].get("timestamp") == message.get("timestamp"))

    def _input(self, content: str, conversation_file: str | Path, index: int) -> tuple[ContextualOpenThreadExtractorInput, dict[str, str]]:
        repository = MemoryV2Repository(self.store)
        mapping, threads = {}, []
        for number, thread in enumerate(repository.list_open_threads(self.character_id).threads, 1):
            reference = f"t{number}"
            mapping[reference] = thread.thread_id
            threads.append(ContextualOpenThread(reference, thread.kind, thread.participant_scope, thread.description, thread.temporal_anchor))
        # Read at most four preceding user turns from the already canonical file;
        # never expose an archive or assistant/private prompt data.
        try:
            rows = json.loads(Path(conversation_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            rows = []
        recent = tuple(str(item.get("content")) for item in rows[max(0, index - 8):index]
                       if isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("content"), str))[-4:]
        return ContextualOpenThreadExtractorInput(
            content, recent, tuple(threads), ("open", "reconfirm", "resolve", "cancel"),
            tuple(sorted(OPEN_THREAD_KINDS)), tuple(sorted(OPEN_THREAD_PARTICIPANT_SCOPES)),
        ), mapping

    @staticmethod
    def _bind(candidate: OpenThreadProposal, mapping: dict[str, str]) -> OpenThreadProposal:
        bound = []
        for item in candidate.operations:
            reference = mapping.get(item.reference, item.reference)
            if item.operation != "open" and item.reference not in mapping:
                raise ValueError("unknown_thread_reference")
            bound.append(OpenThreadProposalOperation(item.operation, reference, item.excerpt_start_cp, item.excerpt_end_cp,
                                                      item.kind, item.participant_scope, item.description, item.temporal_anchor))
        return OpenThreadProposal(tuple(bound))

    @staticmethod
    def _spans(proposal: OpenThreadProposal, content: str) -> None:
        if any(not (0 <= item.excerpt_start_cp < item.excerpt_end_cp <= len(content)) for item in proposal.operations):
            raise ValueError("evidence_binding_failure")

    def observe_canonical_user_turn(self, message: object, *, conversation_index: int, conversation_file: str | Path) -> dict[str, object]:
        if not self.enabled:
            return {"state": "disabled"}
        started, content = time.perf_counter(), message.get("content") if isinstance(message, dict) else None
        trace: dict[str, object] = {"format":"open-thread-contextual-shadow-v1", "scope_hash":_hash(self.character_id),
            "canonical_event_hash":_hash(f"{Path(conversation_file).name}:{conversation_index}:{content}"),
            "provider":str(getattr(self.extractor, "provider_id", "unknown")), "model":getattr(self.extractor, "model_id", None),
            "observed_at":datetime.now(timezone.utc).isoformat(), "parse_status":"not_run", "evidence_binding_status":"not_run",
            "validation_status":"not_run", "operations":[], "risk_labels":[], "latency_ms":None, "error_category":None}
        try:
            if not isinstance(content, str) or not self._persisted(message, conversation_index, conversation_file):
                trace.update(parse_status="skipped", evidence_binding_status="failed", error_category="canonical_evidence_failure")
                return {"state":"rejected", "reason":"canonical_evidence_failure", "trace":trace}
            extraction_input, mapping = self._input(content, conversation_file, conversation_index)
            candidate = self.extractor.propose(extraction_input)
            if candidate is None:
                trace.update(parse_status="abstained", evidence_binding_status="not_applicable", validation_status="not_applicable")
                return {"state":"abstained", "trace":trace}
            if not isinstance(candidate, OpenThreadProposal):
                trace.update(parse_status="failed", error_category="malformed_output")
                return {"state":"rejected", "reason":"malformed_output", "trace":trace}
            trace["parse_status"] = "parsed"
            trace["operations"] = [{"operation":item.operation, "ref":item.reference, "kind":item.kind, "scope":item.participant_scope, "topic":item.description} for item in candidate.operations]
            bound = self._bind(candidate, mapping); self._spans(bound, content)
            trace["evidence_binding_status"] = "bound"; validate_open_thread_proposal(bound)
            trace["validation_status"] = "accepted"
            labels = {item.operation for item in candidate.operations}
            trace["risk_labels"] = sorted(labels | ({"multi_update"} if len(candidate.operations) > 1 else set()))
            return {"state":"validated", "proposal":bound, "trace":trace}
        except ValueError as error:
            reason = str(error); trace.update(validation_status="rejected", error_category=reason if reason in {"unknown_thread_reference", "evidence_binding_failure"} else "contract_rejected")
            return {"state":"rejected", "reason":trace["error_category"], "trace":trace}
        except Exception as error:
            trace.update(parse_status="failed", error_category=f"provider_{type(error).__name__}")
            return {"state":"failed", "reason":trace["error_category"], "trace":trace}
        finally:
            trace["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            try: self.trace_store.append(trace)
            except Exception: pass
