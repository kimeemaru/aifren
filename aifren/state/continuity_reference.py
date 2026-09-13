"""Bounded, rebuildable reference identity for Current Continuity proposals.

Canonical user evidence and the governed Open Thread lifecycle remain the only
authority.  This module derives a small lexical identity on demand so natural
aliases and pronouns can select an existing governed record without inventing
state or replacing its original description.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from aifren.memory_v2_store.repository import OpenThreadRecord


MAX_RECENT_USER_TURNS = 6
MAX_RECENT_USER_CHARS = 900
MAX_RECENT_USER_GAP_US = 30 * 60 * 1_000_000
MAX_LOGICAL_DUPLICATE_TARGETS = 4

_WORD = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ]+(?:['’][A-Za-z0-9À-ÖØ-öø-ÿ]+)?")
_QUOTED = re.compile(r"[\"“”`]")
_FILLERS = frozenset({
    "actually", "again", "alright", "finally", "hey", "just", "now", "oh",
    "okay", "ok", "please", "so", "still", "uh", "um", "well", "yeah", "yep",
})
_SUBJECT_STOP = frozenset({
    "a", "an", "and", "are", "at", "be", "been", "being", "for", "from", "has",
    "anymore", "have", "i", "im", "in", "is", "it", "my", "no", "not", "of", "on",
    "our", "that", "the", "this", "to", "was", "we", "were", "with", "thing", "stuff", "yet",
}) | _FILLERS
_EVENT_WORDS = frozenset({
    "arrive", "await", "cancel", "come", "complete", "configure", "deliver", "delivery",
    "do", "doing", "drop", "finish", "forget", "get", "got", "install", "plan", "receive",
    "resolve", "set", "setup", "ship", "shipment", "shipping", "show", "up", "wait",
})
_TRAILING_EVENT_START = frozenset({
    "arrive", "come", "complete", "deliver", "finish", "get", "receive", "ship", "show",
})
_RECEIPT_WORDS = frozenset({
    "arrive", "come", "deliver", "delivery", "get", "got", "receive", "ship", "shipment",
    "shipping", "show",
})
_TASK_WORDS = frozenset({"complete", "configure", "finish", "install", "set", "setup"})
_RESPONSE_WORDS = frozenset({"answer", "reply", "response"})
_ACTION_FAMILIES = {
    "configure": "setup", "install": "setup", "set": "setup", "setup": "setup",
    "replace": "replace", "repair": "repair", "upgrade": "upgrade",
}
_ROOTS = {
    "i'm": "im", "we're": "we", "let's": "lets", "gonna": "going",
    "didn't": "not", "doesn't": "not", "don't": "not", "dont": "not", "hasn't": "not", "haven't": "not",
    "isn't": "not", "wasn't": "not", "won't": "not",
    "arrived": "arrive", "arrives": "arrive", "awaiting": "await",
    "bought": "buy", "came": "come", "cancelled": "cancel", "canceled": "cancel",
    "completed": "complete", "completing": "complete", "configured": "configure",
    "configuring": "configure", "delivered": "deliver", "finished": "finish", "finishing": "finish",
    "getting": "get", "got": "get", "installing": "install", "meant": "mean", "ordered": "order",
    "planning": "plan", "received": "receive", "receiving": "receive",
    "setting": "set", "shipped": "ship", "showed": "show", "shown": "show", "waiting": "wait",
}
_PHRASE_ALIASES = {
    ("graphics", "card"): "gpu",
    ("video", "card"): "gpu",
}
_CANONICAL_ALIASES = {
    "gpu": frozenset({"gpu", "graphics card", "video card", "card"}),
}


@dataclass(frozen=True)
class RecentUserTurn:
    conversation_index: int
    content: str


@dataclass(frozen=True)
class SubjectIdentity:
    key: str
    aliases: frozenset[str]


@dataclass(frozen=True)
class DerivedThreadIdentity:
    record: OpenThreadRecord
    subject: SubjectIdentity
    event_family: str
    aliases: frozenset[str]


@dataclass(frozen=True)
class LogicalThreadGroup:
    identities: tuple[DerivedThreadIdentity, ...]
    subject_key: str
    event_family: str
    aliases: frozenset[str]

    @property
    def representative(self) -> OpenThreadRecord:
        return min(
            (item.record for item in self.identities),
            key=lambda item: (item.opened_at_us, item.thread_id),
        )

    @property
    def thread_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.record.thread_id for item in self.identities))


@dataclass(frozen=True)
class ThreadReferenceDecision:
    group: LogicalThreadGroup | None
    reason: str


def _root(value: str) -> str:
    word = value.casefold().replace("’", "'")
    return _ROOTS.get(word, word)


def _words(text: str) -> tuple[str, ...]:
    raw = tuple(_root(match.group(0)) for match in _WORD.finditer(str(text or "")))
    normalized: list[str] = []
    index = 0
    aliases = sorted(_PHRASE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
    while index < len(raw):
        replacement = None
        consumed = 0
        for phrase, canonical in aliases:
            if raw[index:index + len(phrase)] == phrase:
                replacement = canonical
                consumed = len(phrase)
                break
        if replacement is not None:
            normalized.append(replacement)
            index += consumed
        else:
            normalized.append(raw[index])
            index += 1
    return tuple(normalized)


def _event_family(kind: str, text: str) -> str:
    words = _words(text)
    word_set = set(words)
    if kind == "waiting":
        if word_set & _TASK_WORDS:
            return "task_completion"
        if word_set & _RESPONSE_WORDS:
            return "response"
        if word_set & _RECEIPT_WORDS:
            return "receipt"
        return "waiting_unspecified"
    for word in words:
        if word in _ACTION_FAMILIES:
            return _ACTION_FAMILIES[word]
    return {
        "plan_or_intention": "planned_action",
        "unresolved_problem": "problem_resolution",
        "decision_or_question": "decision",
        "ongoing_shared_thread": "shared_continuation",
    }.get(kind, kind)


def _subject_tail(words: tuple[str, ...], start: int) -> tuple[str, ...]:
    tail = list(words[start:])
    while tail and tail[0] in _SUBJECT_STOP | {"await", "wait"}:
        tail.pop(0)
    for index, word in enumerate(tail):
        if word in _TRAILING_EVENT_START:
            tail = tail[:index]
            break
    return tuple(tail)


def _subject_words(text: str, kind: str) -> tuple[str, ...]:
    words = _words(text)
    if not words:
        return ()

    if "mean" in words:
        index = len(words) - 1 - tuple(reversed(words)).index("mean")
        candidate = _subject_tail(words, index + 1)
    elif any(word in {"wait", "await"} for word in words):
        index = next(i for i, word in enumerate(words) if word in {"wait", "await"})
        if index + 1 < len(words) and words[index + 1] == "up":
            return ()
        candidate = _subject_tail(words, index + 1)
    elif any(word in {"get", "receive"} for word in words):
        index = next(i for i, word in enumerate(words) if word in {"get", "receive"})
        candidate = _subject_tail(words, index + 1)
    elif any(word in {"arrive", "come", "deliver"} for word in words) or (
        "show" in words and "up" in words
    ):
        indexes = [i for i, word in enumerate(words) if word in {"arrive", "come", "deliver", "show"}]
        candidate = tuple(words[:min(indexes)]) if indexes else ()
    elif kind != "waiting":
        action = next((i for i, word in enumerate(words) if word in _ACTION_FAMILIES), None)
        candidate = _subject_tail(words, action + 1) if action is not None else words
    else:
        candidate = words

    cleaned = tuple(
        word for word in candidate
        if word not in _SUBJECT_STOP and word not in _EVENT_WORDS and len(word) > 1
    )
    # A subject is a compact entity phrase, not every word in a compound
    # conversational clause. Limit it deterministically rather than allowing
    # trailing prose to become identity.
    return cleaned[:5]


def subject_identity(text: str, kind: str) -> SubjectIdentity:
    words = _subject_words(text, kind)
    key = " ".join(words)
    aliases = {key} if key else set()
    aliases.update(_CANONICAL_ALIASES.get(key, ()))
    return SubjectIdentity(key, frozenset(item for item in aliases if item))


def _is_correction(text: str) -> bool:
    words = _words(text)
    return "mean" in words and any(word in {"no", "actually"} for word in words)


def derive_thread_identity(
    record: OpenThreadRecord,
    evidence_texts: Iterable[str] = (),
) -> DerivedThreadIdentity:
    description_subject = subject_identity(record.description, record.kind)
    aliases = set(description_subject.aliases)
    corrected: SubjectIdentity | None = None
    for text in evidence_texts:
        derived = subject_identity(text, record.kind)
        aliases.update(derived.aliases)
        if _is_correction(text) and derived.key:
            corrected = derived
    primary = corrected or description_subject
    aliases.update(primary.aliases)
    return DerivedThreadIdentity(
        record=record,
        subject=primary,
        event_family=_event_family(record.kind, record.description),
        aliases=frozenset(aliases),
    )


def _event_compatible(left: str, right: str) -> bool:
    if "any" in {left, right}:
        return True
    if left == right:
        return True
    return "waiting_unspecified" in {left, right} and {left, right} <= {
        "waiting_unspecified", "receipt", "response",
    }


def _same_logical_identity(left: DerivedThreadIdentity, right: DerivedThreadIdentity) -> bool:
    return (
        left.record.truth_scope_id == right.record.truth_scope_id
        and left.record.kind == right.record.kind
        and left.record.participant_scope == right.record.participant_scope
        and bool(left.subject.key)
        and left.subject.key == right.subject.key
        and _event_compatible(left.event_family, right.event_family)
    )


def build_logical_thread_groups(
    threads: Iterable[OpenThreadRecord],
    evidence_by_thread: Mapping[str, Iterable[str]] | None = None,
) -> tuple[LogicalThreadGroup, ...]:
    evidence_by_thread = evidence_by_thread or {}
    identities = [
        derive_thread_identity(record, evidence_by_thread.get(record.thread_id, ()))
        for record in threads if record.status == "open"
    ]
    pending = list(identities)
    groups: list[LogicalThreadGroup] = []
    while pending:
        seed = pending.pop(0)
        members = [seed]
        changed = True
        while changed:
            changed = False
            remaining: list[DerivedThreadIdentity] = []
            for candidate in pending:
                if any(_same_logical_identity(candidate, member) for member in members):
                    members.append(candidate)
                    changed = True
                else:
                    remaining.append(candidate)
            pending = remaining
        aliases = frozenset(alias for item in members for alias in item.aliases)
        family = next(
            (item.event_family for item in members if item.event_family != "waiting_unspecified"),
            seed.event_family,
        )
        groups.append(LogicalThreadGroup(tuple(members), seed.subject.key, family, aliases))
    return tuple(sorted(groups, key=lambda group: (
        -group.representative.last_mentioned_at_us, group.representative.thread_id,
    )))


def _group_compatible(
    group: LogicalThreadGroup,
    subject: SubjectIdentity,
    *,
    kind: str,
    participant_scope: str | None,
    event_family: str,
) -> bool:
    representative = group.representative
    if representative.kind != kind:
        return False
    if participant_scope is not None and representative.participant_scope != participant_scope:
        return False
    if not _event_compatible(group.event_family, event_family):
        return False
    if not subject.key:
        return False
    if subject.key == group.subject_key:
        return True
    # Only an explicit bounded alias may bridge different surface identities;
    # arbitrary noun overlap is deliberately insufficient.
    return subject.key in group.aliases


def _mentions_group(text: str, group: LogicalThreadGroup) -> bool:
    if _QUOTED.search(text):
        return False
    words = _words(text)
    if words[:2] == ("what", "if") or (words and words[0] in {"if", "hypothetically"}):
        return False
    normalized = " ".join(words)
    aliases = sorted(group.aliases | {group.subject_key}, key=len, reverse=True)
    return any(alias and re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", normalized) for alias in aliases)


def _salient_group(
    candidates: tuple[LogicalThreadGroup, ...],
    recent_user_turns: tuple[RecentUserTurn, ...],
) -> LogicalThreadGroup | None:
    if len(candidates) == 1:
        return candidates[0]
    # Multiple live logical candidates remain genuinely plausible for an
    # unqualified pronoun. Recent discourse may supply aliases, but it cannot
    # erase another governed pending item; require an explicit subject.
    return None


def match_open_thread(
    description: str,
    *,
    kind: str,
    participant_scope: str,
    groups: tuple[LogicalThreadGroup, ...],
) -> ThreadReferenceDecision:
    subject = subject_identity(description, kind)
    family = _event_family(kind, description)
    matches = tuple(
        group for group in groups
        if _group_compatible(
            group, subject, kind=kind, participant_scope=participant_scope,
            event_family=family,
        )
    )
    if len(matches) == 1:
        return ThreadReferenceDecision(matches[0], "one_logical_identity")
    if len(matches) > 1:
        return ThreadReferenceDecision(None, "multiple_logical_identities")
    return ThreadReferenceDecision(None, "no_logical_identity")


def resolve_thread_reference(
    *,
    groups: tuple[LogicalThreadGroup, ...],
    recent_user_turns: tuple[RecentUserTurn, ...],
    kinds: set[str] | None = None,
    participant_scope: str | None = None,
    subject_text: str | None = None,
    event_family: str = "waiting_unspecified",
) -> ThreadReferenceDecision:
    candidates = tuple(
        group for group in groups
        if (kinds is None or group.representative.kind in kinds)
        and (participant_scope is None or group.representative.participant_scope == participant_scope)
        and _event_compatible(group.event_family, event_family)
    )
    subject = subject_identity(subject_text or "", next(iter(kinds)) if kinds and len(kinds) == 1 else "waiting")
    if subject.key:
        matches = tuple(
            group for group in candidates
            if _group_compatible(
                group, subject, kind=group.representative.kind,
                participant_scope=participant_scope, event_family=event_family,
            )
        )
        if len(matches) == 1:
            return ThreadReferenceDecision(matches[0], "explicit_subject")
        if len(matches) > 1:
            return ThreadReferenceDecision(None, "ambiguous_subject")
        return ThreadReferenceDecision(None, "subject_not_current")
    selected = _salient_group(candidates, recent_user_turns)
    if selected is not None:
        return ThreadReferenceDecision(selected, "unique_salient_reference")
    return ThreadReferenceDecision(None, "ambiguous_or_missing_reference")


def lifecycle_target_ids(group: LogicalThreadGroup, operation: str) -> tuple[str, ...]:
    if operation == "reconfirm":
        return (group.representative.thread_id,)
    identifiers = group.thread_ids
    return identifiers if len(identifiers) <= MAX_LOGICAL_DUPLICATE_TARGETS else ()
