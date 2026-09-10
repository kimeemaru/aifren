"""Bounded recent-context policies for Memory V2 answer containment.

The selectors operate only on copied prompt messages. Canonical conversation
is never changed. Recent dialogue remains useful continuity, but is explicitly
separated from source-grounded long-term-memory evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from memory.memory import meaningful_words
from memory_query_decision import MemoryQueryDecision, decide_memory_query


RECENT_POLICY_R0 = "balanced_12"
RECENT_POLICY_R1 = "user_led"
RECENT_POLICY_R2 = "balanced_6"
RECENT_POLICY_MEMORY_CONTAINED = "memory_query_contained"
RECENT_POLICIES = frozenset({
    RECENT_POLICY_R0,
    RECENT_POLICY_R1,
    RECENT_POLICY_R2,
    RECENT_POLICY_MEMORY_CONTAINED,
})

RECENT_CONVERSATION_BOUNDARY = (
    "[Recent conversation — continuity only]\n"
    "The dialogue below may clarify the immediate exchange, tone, and references. "
    "It is not independent evidence of a remembered historical or personal fact; "
    "for memory answers, only CURRENT AUTHORITATIVE STATE and GROUNDED LONG-TERM "
    "MEMORY EVIDENCE may establish such details.\n"
    "[End recent conversation boundary]"
)

_FOLLOWUP = re.compile(
    r"\b(?:that|those|them|it|they|what\s+else|another|connected|you\s+just\s+said|"
    r"you\s+mean|go\s+on|continue|why\s+is\s+that|how\s+so)\b",
    re.IGNORECASE,
)
_PROPER_PHRASE = re.compile(
    r"\b[A-Z][A-Za-z0-9-]{2,}(?:\s+[A-Z][A-Za-z0-9-]{2,})+\b"
)
_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b")
_GENERIC = frozenset({
    "aifren", "assistant", "okay", "user", "mrow",
    "remember", "memory", "previously", "historical", "conversation",
    "connected", "mentioned", "specifically",
})


@dataclass(frozen=True)
class RecentOnlyAnchorSource:
    anchor: str
    roles: tuple[str, ...]
    message_offsets: tuple[int, ...]


def _distinctive_context_anchors(text: object) -> tuple[str, ...]:
    value = str(text or "")
    anchors: set[str] = {
        " ".join(match.group(0).casefold().split())
        for match in _PROPER_PHRASE.finditer(value)
    }
    for match in _WORD.finditer(value):
        token = match.group(0)
        normalized = token.casefold()
        if normalized in _GENERIC:
            continue
        before = value[:match.start()].rstrip()
        sentence_initial = not before or before[-1:] in ".!?\n"
        if (
            (token[:1].isupper() and not sentence_initial and len(token) >= 5)
            or "-" in token
            or any(character.isdigit() for character in token)
        ):
            anchors.add(normalized)
    return tuple(sorted(anchors))


def recent_only_anchor_sources(
    query_text: object,
    evidence_texts: Sequence[object],
    recent_messages: Sequence[Mapping[str, object]],
) -> tuple[RecentOnlyAnchorSource, ...]:
    """Describe bounded distinctive details owned only by recent dialogue."""
    allowed_text = "\n".join((
        str(query_text or ""),
        *(str(item or "") for item in evidence_texts),
    )).casefold()
    sources: dict[str, dict[str, set[object]]] = {}
    long_words: dict[str, set[int]] = {}
    messages = tuple(recent_messages)
    for offset, item in enumerate(messages[:-1]):
        role = str(item.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content", ""))
        for anchor in _distinctive_context_anchors(content):
            detail = sources.setdefault(anchor, {"roles": set(), "offsets": set()})
            detail["roles"].add(role)
            detail["offsets"].add(offset)
        for word in meaningful_words(content):
            if len(word) >= 9 and word not in _GENERIC:
                long_words.setdefault(word, set()).add(offset)
    for word, offsets in long_words.items():
        if len(offsets) < 2:
            continue
        detail = sources.setdefault(word, {"roles": set(), "offsets": set()})
        detail["offsets"].update(offsets)
        detail["roles"].update(
            str(messages[index].get("role", "")) for index in offsets
        )
    result = []
    for anchor, detail in sorted(sources.items()):
        if anchor in allowed_text or any(part in allowed_text for part in anchor.split()):
            continue
        result.append(RecentOnlyAnchorSource(
            anchor,
            tuple(sorted(str(item) for item in detail["roles"] if item)),
            tuple(sorted(int(item) for item in detail["offsets"])),
        ))
    return tuple(result[:24])


def select_recent_messages(
    messages: Sequence[Mapping[str, object]],
    policy: str,
    *,
    maximum_messages: int = 12,
    memory_query_decision: MemoryQueryDecision | None = None,
) -> tuple[dict[str, object], ...]:
    """Return one bounded copied recent-message selection.

    R1 preserves user-led continuity. Prior assistant prose is admitted only
    when the current user surface is structurally follow-up shaped, and then
    only the immediately preceding assistant message is retained.
    """
    name = str(policy)
    if name not in RECENT_POLICIES:
        raise ValueError("unknown V2 recent-context policy")
    copied = tuple(dict(item) for item in messages)
    limit = max(1, int(maximum_messages))
    if name == RECENT_POLICY_R0:
        return copied[-limit:]
    if name == RECENT_POLICY_R2:
        return copied[-min(limit, 6):]
    if not copied:
        return ()

    latest_user = next((
        index for index in range(len(copied) - 1, -1, -1)
        if str(copied[index].get("role", "")) == "user"
    ), len(copied) - 1)
    current_text = str(copied[latest_user].get("content", ""))
    decision = memory_query_decision or decide_memory_query(current_text)
    if name == RECENT_POLICY_MEMORY_CONTAINED:
        # A direct memory request already has a typed evidence section.
        # Earlier questions and assistant answers are especially dangerous:
        # they can restate false premises or model inventions as remembered.
        if decision.contain_recent_context:
            return (copied[latest_user],)
        return copied[-limit:]
    selected = {
        index for index, item in enumerate(copied[:latest_user + 1])
        if str(item.get("role", "")) == "user"
    }
    if _FOLLOWUP.search(current_text):
        prior = latest_user - 1
        if prior >= 0 and str(copied[prior].get("role", "")) == "assistant":
            selected.add(prior)
    ordered = sorted(selected)[-limit:]
    return tuple(copied[index] for index in ordered)


__all__ = [
    "RECENT_CONVERSATION_BOUNDARY",
    "RECENT_POLICIES",
    "RECENT_POLICY_R0",
    "RECENT_POLICY_R1",
    "RECENT_POLICY_R2",
    "RECENT_POLICY_MEMORY_CONTAINED",
    "RecentOnlyAnchorSource",
    "recent_only_anchor_sources",
    "select_recent_messages",
]
