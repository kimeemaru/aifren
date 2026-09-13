"""Small governed truth-scope contract for real and persistent scenario continuity."""
from __future__ import annotations

import re
import uuid


REAL_WORLD_SCOPE = "real_world"
SCENARIO_SCOPE = "scenario"
TRUTH_SCOPE_KINDS = frozenset({REAL_WORLD_SCOPE, SCENARIO_SCOPE})
TRUTH_SCOPE_STATUSES = frozenset({"active", "inactive"})
MAX_SCENARIO_SCOPES_PER_CHARACTER = 16
MAX_TRUTH_SCOPE_LABEL_CHARS = 96

_SCOPE_ID = re.compile(r"\Ascope-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LABEL = re.compile(r"\A[A-Za-z0-9À-ÖØ-öø-ÿ][A-Za-z0-9À-ÖØ-öø-ÿ'’,:?()/&+ -]*\Z")
_INSTRUCTION_TOKENS = frozenset({
    "ignore", "instruction", "instructions", "system", "assistant", "prompt",
    "previous", "always", "never", "developer", "tool", "function",
})


def default_real_world_scope_id(character_id: object) -> str:
    """Stable backend ID; a display label is never a scope identity."""
    try:
        normalized = str(uuid.UUID(str(character_id)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("truth-scope character ID is malformed") from error
    return f"scope-{uuid.uuid5(uuid.NAMESPACE_URL, 'aifren:real-world:' + normalized)}"


def validate_truth_scope_id(value: object) -> str:
    if not isinstance(value, str) or _SCOPE_ID.fullmatch(value) is None:
        raise ValueError("truth-scope ID is malformed")
    return value


def normalize_truth_scope_label(value: object) -> str:
    if not isinstance(value, str) or any(character in value for character in "\r\n\t"):
        raise ValueError("truth-scope label must be one compact line")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > MAX_TRUTH_SCOPE_LABEL_CHARS or _LABEL.fullmatch(normalized) is None:
        raise ValueError("truth-scope label is malformed or exceeds its bound")
    words = {word.casefold().strip("'’,:?()/&+.-") for word in normalized.split()}
    if words & _INSTRUCTION_TOKENS:
        raise ValueError("truth-scope label contains instruction-like text")
    return normalized
