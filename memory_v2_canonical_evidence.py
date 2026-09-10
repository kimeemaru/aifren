"""Strict canonical user-evidence resolution for governed live observers.

The canonical conversation archive is the source authority.  This module is
deliberately a resolver, not a second fact parser: after proving ownership,
scope, completion, and semantic availability it delegates to the existing
identity and durable curators.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from conversation.truth_scope import coherent_exchange_scope, parse_canonical_truth_scope
from durable_fact_curation import DurableFactProposal, extract_durable_fact_proposal
from memory_v2_episode_compaction import canonical_record_id
from memory_v2_store.identity_name import IdentityNameAssertion, extract_identity_name_assertion
from memory_v2_store.store import parse_timestamp_us


MAX_PREVIOUS_USER_CHARACTERS = 300
CANONICAL_EVIDENCE_POLICY_VERSION = "strict_completed_scoped_pair_v1"
ORDINARY_USER_KEYS = frozenset({"role", "content", "timestamp", "truth_scope"})
ORDINARY_ASSISTANT_KEYS = frozenset({"role", "content", "timestamp", "truth_scope"})
LEGACY_MESSAGE_KEYS = frozenset({"role", "content", "timestamp"})


@dataclass(frozen=True)
class LoadedCanonicalArchive:
    """Internal recovery snapshot loaded by the canonical persistence owner.

    Reuse this one validated read within a bounded observer page. This is never
    accepted from transport or a model proposal.
    """
    path: Path
    records: list

    @classmethod
    def load(cls, path):
        from conversation.persistence import load_json
        records, _ = load_json(path, [], record_kind="conversation")
        return cls(Path(path).resolve(), records)

    def for_path(self, path):
        if self.path != Path(path).resolve():
            raise ValueError("canonical snapshot owner differs")
        return self.records


@dataclass(frozen=True)
class CanonicalUserEvidence:
    canonical_index: int
    recorded_at_us: int
    truth_scope_id: str
    canonical_record_id: str
    content: str
    timestamp: str
    previous_user_content: str | None
    identity_assertion: IdentityNameAssertion | None
    durable_proposal: DurableFactProposal | None


@dataclass(frozen=True)
class CanonicalEvidenceDecision:
    evidence: CanonicalUserEvidence | None
    reason: str

    @property
    def accepted(self) -> bool:
        return self.evidence is not None


def _ordinary_user_record(message: Mapping[str, object]) -> bool:
    # Canonical scene/UI actions are valuable continuity evidence, but they
    # are generated control records rather than user-authored durable truth.
    # Unknown future origins remain non-authoritative until explicitly
    # governed instead of being silently treated as ordinary input.
    # Production ordinary understood turns have exactly the scoped shape. The
    # exact older untagged shape is recognized only so the caller can classify
    # it as unresolved legacy scope; it is never admitted. Scene/UI,
    # hearing-unavailable, and future record classes add an authority-bearing
    # field and fail closed until they receive an explicit contract.
    return set(message) in {ORDINARY_USER_KEYS, LEGACY_MESSAGE_KEYS}


def _ordinary_assistant_record(message: Mapping[str, object]) -> bool:
    # Assistant completion is part of the observation proof, so unknown
    # metadata cannot silently certify a historical user assertion either.
    return set(message) in {ORDINARY_ASSISTANT_KEYS, LEGACY_MESSAGE_KEYS}


def _completion_times(
    user: Mapping[str, object],
    assistant: Mapping[str, object],
) -> tuple[int, int] | None:
    user_content, assistant_content = user.get("content"), assistant.get("content")
    user_timestamp, assistant_timestamp = user.get("timestamp"), assistant.get("timestamp")
    if (not isinstance(user_content, str) or not isinstance(assistant_content, str)
            or not isinstance(user_timestamp, str) or not user_timestamp.strip()
            or not isinstance(assistant_timestamp, str) or not assistant_timestamp.strip()):
        return None
    try:
        user_at = parse_timestamp_us(user_timestamp)
        assistant_at = parse_timestamp_us(assistant_timestamp)
    except (TypeError, ValueError, OverflowError):
        return None
    if user_at is None or assistant_at is None or assistant_at < user_at:
        return None
    return user_at, assistant_at


def _previous_same_scope_user_content(
    records: Sequence[Mapping[str, object]],
    index: int,
    *,
    scope_id: str,
    valid_scope_ids: set[str] | frozenset[str],
) -> str | None:
    # The only contextual durable grammar is ``I replaced it/that with ...``.
    # Pronoun ownership is safe only across the immediately adjacent completed
    # exchange; an intervening assistant/system/proactive record can introduce
    # a different referent and must force abstention.
    candidate_index = index - 2
    if candidate_index < 0:
        return None
    candidate = records[candidate_index]
    candidate_assistant = records[index - 1]
    if (not isinstance(candidate, Mapping) or candidate.get("role") != "user"
            or not _ordinary_user_record(candidate)
            or not isinstance(candidate_assistant, Mapping)
            or candidate_assistant.get("role") != "assistant"
            or not _ordinary_assistant_record(candidate_assistant)
            or _completion_times(candidate, candidate_assistant) is None):
        return None
    scope = parse_canonical_truth_scope(candidate, valid_scope_ids=valid_scope_ids)
    if scope.kind != "real_world" or scope.scope_id != scope_id:
        return None
    exchange_scope = coherent_exchange_scope(
        candidate, candidate_assistant, valid_scope_ids=valid_scope_ids,
    )
    if exchange_scope.identity != scope.identity:
        return None
    content = candidate.get("content")
    return (
        content
        if isinstance(content, str) and len(content) <= MAX_PREVIOUS_USER_CHARACTERS
        else None
    )


def resolve_canonical_user_evidence(
    records: Sequence[Mapping[str, object]],
    index: int,
    *,
    expected_real_world_scope_id: str,
    valid_scope_ids: set[str] | frozenset[str],
) -> CanonicalEvidenceDecision:
    """Resolve one exact completed, ordinary, real-world user exchange.

    Untagged legacy records remain canonical archive material but cannot be
    promoted to governed durable truth because their historical scope is
    unknown.  A completed same-scope assistant record is required so an
    interrupted or orphaned user record cannot be accepted as an observed
    production turn.
    """
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(records):
        return CanonicalEvidenceDecision(None, "invalid_canonical_index")
    user = records[index]
    if not isinstance(user, Mapping) or user.get("role") != "user":
        return CanonicalEvidenceDecision(None, "not_user_message")
    if not _ordinary_user_record(user):
        return CanonicalEvidenceDecision(None, "generated_or_unavailable_source")
    if index + 1 >= len(records):
        return CanonicalEvidenceDecision(None, "incomplete_exchange")
    assistant = records[index + 1]
    if not isinstance(assistant, Mapping) or assistant.get("role") != "assistant":
        return CanonicalEvidenceDecision(None, "incoherent_exchange")
    if not _ordinary_assistant_record(assistant):
        return CanonicalEvidenceDecision(None, "unknown_completion_record_class")

    user_scope = parse_canonical_truth_scope(user, valid_scope_ids=valid_scope_ids)
    if user_scope.kind == "legacy_untagged":
        return CanonicalEvidenceDecision(None, "legacy_scope_unresolved")
    if not user_scope.is_valid:
        return CanonicalEvidenceDecision(None, "invalid_scope")
    if user_scope.kind != "real_world" or user_scope.scope_id != expected_real_world_scope_id:
        return CanonicalEvidenceDecision(None, "truth_scope_not_real_world")
    exchange_scope = coherent_exchange_scope(
        user, assistant, valid_scope_ids=valid_scope_ids,
    )
    if exchange_scope.identity != user_scope.identity:
        return CanonicalEvidenceDecision(None, "incoherent_exchange_scope")

    content, timestamp = user.get("content"), user.get("timestamp")
    completion_times = _completion_times(user, assistant)
    if completion_times is None:
        return CanonicalEvidenceDecision(None, "invalid_canonical_message")
    assert isinstance(content, str) and isinstance(timestamp, str)
    recorded_at_us, _assistant_at_us = completion_times
    previous = _previous_same_scope_user_content(
        records, index, scope_id=user_scope.scope_id, valid_scope_ids=valid_scope_ids,
    )
    return CanonicalEvidenceDecision(
        CanonicalUserEvidence(
            canonical_index=index,
            recorded_at_us=recorded_at_us,
            truth_scope_id=user_scope.scope_id,
            canonical_record_id=canonical_record_id(index, user),
            content=content,
            timestamp=timestamp,
            previous_user_content=previous,
            identity_assertion=extract_identity_name_assertion(content),
            durable_proposal=extract_durable_fact_proposal(
                content, previous_user_content=previous,
            ),
        ),
        "accepted",
    )
