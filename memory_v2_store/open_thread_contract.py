"""Governed, provider-neutral contract for unresolved companion continuity.

Open Threads are compact evidence-backed continuity records.  They are neither
task-manager objects nor model instructions: an extractor may propose this
bounded shape, while the backend remains the only authority that can validate
or apply it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


OPEN_THREAD = "open_thread"
OPEN_THREAD_ASSERTION_SCOPE = "open_thread"
OPEN_THREAD_EVIDENCE_ROLES = frozenset({
    "thread_open", "thread_reconfirm", "thread_resolve", "thread_cancel",
})
OPEN_THREAD_KINDS = frozenset({
    "unresolved_problem", "waiting", "plan_or_intention",
    "decision_or_question", "ongoing_shared_thread",
})
OPEN_THREAD_PARTICIPANT_SCOPES = frozenset({"user", "companion", "shared"})
OPEN_THREAD_STATUSES = frozenset({"open", "resolved", "cancelled"})
MAX_CURRENT_OPEN_THREADS = 12
MAX_OPEN_THREAD_DESCRIPTION_CHARS = 144
MAX_OPEN_THREAD_ANCHOR_CHARS = 96
MAX_OPEN_THREAD_PROPOSAL_OPERATIONS = 4
MAX_OPEN_THREAD_EVIDENCE_RECORDS = 12

_LOCAL_REFERENCE = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
_THREAD_ID = re.compile(r"\Athread-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_COMPACT_VALUE = re.compile(r"\A[A-Za-z0-9À-ÖØ-öø-ÿ][A-Za-z0-9À-ÖØ-öø-ÿ'’,:?()/&+ -]*\Z")
_INSTRUCTION_TOKENS = frozenset({
    "ignore", "instruction", "instructions", "system", "assistant", "prompt",
    "previous", "always", "never", "developer", "tool", "function",
})


@dataclass(frozen=True)
class OpenThreadProposalOperation:
    """One untrusted update grounded in one canonical user-turn span."""

    operation: Literal["open", "reconfirm", "resolve", "cancel"]
    reference: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    kind: str | None = None
    participant_scope: str | None = None
    description: str | None = None
    temporal_anchor: str | None = None


@dataclass(frozen=True)
class OpenThreadProposal:
    """Small candidate set from one canonical user turn; never store authority."""

    operations: tuple[OpenThreadProposalOperation, ...]


def normalize_open_thread_value(value: object, *, maximum: int, field: str) -> str:
    if not isinstance(value, str) or any(character in value for character in "\r\n\t"):
        raise ValueError(f"open-thread {field} must be one compact line")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or _COMPACT_VALUE.fullmatch(normalized) is None:
        raise ValueError(f"open-thread {field} is malformed or exceeds its bound")
    words = {word.casefold().strip("'’,-:?!()/.&+") for word in normalized.split()}
    if words & _INSTRUCTION_TOKENS:
        raise ValueError(f"open-thread {field} contains instruction-like text")
    return normalized


def validate_open_thread_id(value: object) -> str:
    if not isinstance(value, str) or _THREAD_ID.fullmatch(value) is None:
        raise ValueError("open-thread ID is malformed")
    return value


def _valid_span(start: object, end: object) -> bool:
    return (not isinstance(start, bool) and not isinstance(end, bool)
            and isinstance(start, int) and isinstance(end, int) and 0 <= start < end)


def validate_open_thread_proposal(proposal: object) -> tuple[OpenThreadProposalOperation, ...]:
    """Validate only governed syntax and bounds before any write transaction."""
    if not isinstance(proposal, OpenThreadProposal) or not isinstance(proposal.operations, tuple):
        raise ValueError("open-thread proposal must use the governed proposal contract")
    if not 1 <= len(proposal.operations) <= MAX_OPEN_THREAD_PROPOSAL_OPERATIONS:
        raise ValueError("open-thread proposal exceeds the governed operation bound")
    references: set[str] = set()
    normalized: list[OpenThreadProposalOperation] = []
    for item in proposal.operations:
        if not isinstance(item, OpenThreadProposalOperation):
            raise ValueError("open-thread proposal operation is invalid")
        if item.operation not in {"open", "reconfirm", "resolve", "cancel"}:
            raise ValueError("open-thread proposal operation is not governed")
        if not isinstance(item.reference, str) or (
            _LOCAL_REFERENCE.fullmatch(item.reference) is None and _THREAD_ID.fullmatch(item.reference) is None
        ):
            raise ValueError("open-thread proposal reference is malformed")
        if item.reference in references:
            raise ValueError("open-thread proposal cannot update one thread twice")
        references.add(item.reference)
        if not _valid_span(item.excerpt_start_cp, item.excerpt_end_cp):
            raise ValueError("open-thread proposal requires a non-empty source span")
        if item.operation == "open":
            if _LOCAL_REFERENCE.fullmatch(item.reference) is None:
                raise ValueError("open-thread open operation requires a proposal-local reference")
            if item.kind not in OPEN_THREAD_KINDS or item.participant_scope not in OPEN_THREAD_PARTICIPANT_SCOPES:
                raise ValueError("open-thread kind or participant scope is not governed")
            description = normalize_open_thread_value(
                item.description, maximum=MAX_OPEN_THREAD_DESCRIPTION_CHARS, field="description",
            )
            anchor = (normalize_open_thread_value(
                item.temporal_anchor, maximum=MAX_OPEN_THREAD_ANCHOR_CHARS, field="temporal anchor",
            ) if item.temporal_anchor is not None else None)
            normalized.append(OpenThreadProposalOperation(
                "open", item.reference, item.excerpt_start_cp, item.excerpt_end_cp,
                str(item.kind), str(item.participant_scope), description, anchor,
            ))
            continue
        if _THREAD_ID.fullmatch(item.reference) is None:
            raise ValueError("open-thread existing operation requires a resolved current-thread reference")
        if item.kind is not None or item.participant_scope is not None:
            raise ValueError("open-thread existing operation cannot change kind or participant scope")
        if item.operation in {"resolve", "cancel"}:
            if item.description is not None or item.temporal_anchor is not None:
                raise ValueError("open-thread closure operation cannot replace compact thread data")
            normalized.append(OpenThreadProposalOperation(
                item.operation, item.reference, item.excerpt_start_cp, item.excerpt_end_cp,
            ))
            continue
        if item.description is not None:
            raise ValueError("open-thread reconfirm operation cannot replace the original description")
        anchor = (normalize_open_thread_value(
            item.temporal_anchor, maximum=MAX_OPEN_THREAD_ANCHOR_CHARS, field="temporal anchor",
        ) if item.temporal_anchor is not None else None)
        normalized.append(OpenThreadProposalOperation(
            "reconfirm", item.reference, item.excerpt_start_cp, item.excerpt_end_cp,
            temporal_anchor=anchor,
        ))
    return tuple(normalized)
