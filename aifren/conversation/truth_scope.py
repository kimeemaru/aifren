"""Canonical truth-scope provenance and prompt-history compatibility helpers.

The canonical archive may contain three record classes:

* legacy records with no ``truth_scope`` field;
* prospectively tagged records with a governed kind and stable scope ID;
* malformed/unknown tagged records, which remain readable but are not admitted
  to scoped prompt history or derived episodes.

Legacy records are deliberately not guessed into a scenario or real-world
scope.  They remain compatible, non-authoritative historical context in every
scope.  Tagged records are compatible only with their exact active scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from aifren.memory_v2_store.truth_scope_contract import validate_truth_scope_id


LEGACY_UNTAGGED_SCOPE = "legacy_untagged"
INVALID_SCOPE = "invalid"


@dataclass(frozen=True)
class CanonicalTruthScope:
    kind: str
    scope_id: str = ""

    @property
    def is_legacy(self) -> bool:
        return self.kind == LEGACY_UNTAGGED_SCOPE

    @property
    def is_valid(self) -> bool:
        return self.kind in {"real_world", "scenario", LEGACY_UNTAGGED_SCOPE}

    @property
    def identity(self) -> tuple[str, str]:
        return self.kind, self.scope_id


LEGACY_SCOPE = CanonicalTruthScope(LEGACY_UNTAGGED_SCOPE)
INVALID_CANONICAL_SCOPE = CanonicalTruthScope(INVALID_SCOPE)


def canonical_truth_scope(kind: object, scope_id: object) -> dict[str, str]:
    """Build the exact prospective canonical record shape."""
    normalized_kind = str(kind or "")
    if normalized_kind not in {"real_world", "scenario"}:
        raise ValueError("canonical truth-scope kind is invalid")
    normalized_id = validate_truth_scope_id(scope_id)
    return {"kind": normalized_kind, "scope_id": normalized_id}


def parse_canonical_truth_scope(
    message: object,
    *,
    valid_scope_ids: set[str] | frozenset[str] | None = None,
) -> CanonicalTruthScope:
    """Classify one record without modifying it or inferring missing scope."""
    if not isinstance(message, Mapping):
        return INVALID_CANONICAL_SCOPE
    if "truth_scope" not in message:
        return LEGACY_SCOPE
    raw = message.get("truth_scope")
    if not isinstance(raw, Mapping) or set(raw) != {"kind", "scope_id"}:
        return INVALID_CANONICAL_SCOPE
    kind = raw.get("kind")
    if kind not in {"real_world", "scenario"}:
        return INVALID_CANONICAL_SCOPE
    try:
        scope_id = validate_truth_scope_id(raw.get("scope_id"))
    except ValueError:
        return INVALID_CANONICAL_SCOPE
    if valid_scope_ids is not None and scope_id not in valid_scope_ids:
        return INVALID_CANONICAL_SCOPE
    return CanonicalTruthScope(str(kind), scope_id)


def scope_is_compatible(record_scope: CanonicalTruthScope, active_scope: CanonicalTruthScope | None) -> bool:
    """Apply the prospective compatibility policy used by prompts/retrieval."""
    if not record_scope.is_valid:
        return False
    if record_scope.is_legacy or active_scope is None:
        return True
    return active_scope.is_valid and not active_scope.is_legacy and record_scope.identity == active_scope.identity


def active_scope_from_provenance(value: object) -> CanonicalTruthScope | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return INVALID_CANONICAL_SCOPE
    return parse_canonical_truth_scope({"truth_scope": value})


def coherent_exchange_scope(
    user: object,
    assistant: object,
    *,
    valid_scope_ids: set[str] | frozenset[str] | None = None,
) -> CanonicalTruthScope:
    left = parse_canonical_truth_scope(user, valid_scope_ids=valid_scope_ids)
    right = parse_canonical_truth_scope(assistant, valid_scope_ids=valid_scope_ids)
    return left if left.is_valid and left.identity == right.identity else INVALID_CANONICAL_SCOPE


def filter_scope_compatible_history(
    messages: Sequence[Mapping[str, object]],
    active_scope: CanonicalTruthScope | None,
    *,
    valid_scope_ids: set[str] | frozenset[str] | None = None,
) -> list[Mapping[str, object]]:
    """Keep compatible history without producing orphaned exchange halves."""
    if active_scope is None:
        return list(messages)
    selected: list[Mapping[str, object]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if (
            str(message.get("role", "")) == "user"
            and index + 1 < len(messages)
            and str(messages[index + 1].get("role", "")) == "assistant"
        ):
            assistant = messages[index + 1]
            exchange_scope = coherent_exchange_scope(
                message, assistant, valid_scope_ids=valid_scope_ids,
            )
            if scope_is_compatible(exchange_scope, active_scope):
                selected.extend((message, assistant))
            index += 2
            continue
        record_scope = parse_canonical_truth_scope(message, valid_scope_ids=valid_scope_ids)
        if scope_is_compatible(record_scope, active_scope):
            selected.append(message)
        index += 1
    return selected


def prompt_message(message: Mapping[str, object]) -> dict[str, str]:
    """Project canonical records to provider-visible dialogue only."""
    return {
        "role": str(message.get("role", "")),
        "content": str(message.get("content", "")),
    }
