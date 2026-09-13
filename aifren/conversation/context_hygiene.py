"""Conservative, provider-neutral hygiene for transient recent dialogue context.

Canonical conversation records are inputs only.  This module returns a new
list containing references to admitted records and never mutates those records.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Iterable, Mapping, Sequence


_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


@dataclass(frozen=True)
class ContextHygieneStats:
    raw_recent_message_count: int
    candidate_count: int
    assistant_only_suppressed_count: int
    exchange_candidate_count: int
    exchange_pairs_suppressed_count: int
    admitted_message_count: int
    suppressed_count: int
    user_echo_count: int
    self_redundancy_count: int
    repetitive_run_count: int
    removed_characters: int

    @classmethod
    def unchanged(cls, messages: Sequence[Mapping[str, object]]) -> "ContextHygieneStats":
        return cls(
            raw_recent_message_count=len(messages),
            candidate_count=0,
            assistant_only_suppressed_count=0,
            exchange_candidate_count=0,
            exchange_pairs_suppressed_count=0,
            admitted_message_count=len(messages),
            suppressed_count=0,
            user_echo_count=0,
            self_redundancy_count=0,
            repetitive_run_count=0,
            removed_characters=0,
        )


@dataclass(frozen=True)
class ContextHygieneResult:
    messages: tuple[Mapping[str, object], ...]
    stats: ContextHygieneStats


def _role(message: object) -> str:
    return str(message.get("role", "")) if isinstance(message, Mapping) else ""


def _content(message: object) -> str:
    return str(message.get("content", "")) if isinstance(message, Mapping) else ""


def _tokens(message: object) -> tuple[str, ...]:
    text = _content(message).casefold().replace("’", "'")
    return tuple(match.group(0) for match in _WORD_RE.finditer(text))


def _ngrams(tokens: Sequence[str], size: int) -> frozenset[tuple[str, ...]]:
    if len(tokens) < size:
        return frozenset()
    return frozenset(tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1))


@dataclass(frozen=True)
class _Fingerprint:
    tokens: tuple[str, ...]
    trigrams: frozenset[tuple[str, ...]]
    fourgrams: frozenset[tuple[str, ...]]
    vocabulary: frozenset[str]


def _fingerprint(message: object) -> _Fingerprint:
    tokens = _tokens(message)
    return _Fingerprint(tokens, _ngrams(tokens, 3), _ngrams(tokens, 4), frozenset(tokens))


def _symmetric_containment(
    left_grams: frozenset[tuple[str, ...]], right_grams: frozenset[tuple[str, ...]],
) -> float:
    denominator = min(len(left_grams), len(right_grams))
    if denominator == 0:
        return 0.0
    return len(left_grams.intersection(right_grams)) / denominator


def _directional_coverage(
    source_grams: frozenset[tuple[str, ...]], target_grams: frozenset[tuple[str, ...]],
) -> float:
    if not target_grams:
        return 0.0
    return len(source_grams.intersection(target_grams)) / len(target_grams)


def _is_self_redundant(left: _Fingerprint, right: _Fingerprint) -> bool:
    """Return true only for long, strongly overlapping assistant messages."""
    if min(len(left.tokens), len(right.tokens)) < 10:
        return False
    length_ratio = min(len(left.tokens), len(right.tokens)) / max(len(left.tokens), len(right.tokens))
    if length_ratio < 0.68:
        return False
    trigram = _symmetric_containment(left.trigrams, right.trigrams)
    if trigram < 0.65:
        return False
    fourgram = _symmetric_containment(left.fourgrams, right.fourgrams)
    sequence_ratio = SequenceMatcher(None, left.tokens, right.tokens, autojunk=False).ratio()
    return (
        sequence_ratio >= 0.88 and trigram >= 0.65
    ) or (
        sequence_ratio >= 0.72 and trigram >= 0.82 and fourgram >= 0.70
    )


def _is_user_echo(user: _Fingerprint, assistant: _Fingerprint) -> bool:
    """Detect a near-copy that adds little beyond the preceding user text."""
    if min(len(user.tokens), len(assistant.tokens)) < 10:
        return False
    if len(assistant.tokens) > len(user.tokens) * 1.35:
        return False
    if not assistant.vocabulary:
        return False
    novel_ratio = len(assistant.vocabulary.difference(user.vocabulary)) / len(assistant.vocabulary)
    return (
        _directional_coverage(user.trigrams, assistant.trigrams) >= 0.72
        and _directional_coverage(user.fourgrams, assistant.fourgrams) >= 0.60
        and novel_ratio <= 0.30
    )


def _is_exchange_user_redundant(left: _Fingerprint, right: _Fingerprint) -> bool:
    """Match only exact short prompts or very close longer user messages."""
    if not left.tokens or not right.tokens:
        return False
    if left.tokens == right.tokens:
        return True
    if min(len(left.tokens), len(right.tokens)) < 5:
        return False
    length_ratio = min(len(left.tokens), len(right.tokens)) / max(len(left.tokens), len(right.tokens))
    if length_ratio < 0.82:
        return False
    sequence_ratio = SequenceMatcher(None, left.tokens, right.tokens, autojunk=False).ratio()
    return (
        sequence_ratio >= 0.90
        and _symmetric_containment(left.trigrams, right.trigrams) >= 0.85
        and _symmetric_containment(left.fourgrams, right.fourgrams) >= 0.75
    )


class ContextHygiene:
    """Filter high-confidence assistant duplicates and redundant exchanges.

    The candidate window and comparison horizon keep work bounded.  False
    negatives are intentional in V1: recurring vocabulary or a shared subject
    is insufficient without strong ordered phrase overlap.
    """

    candidate_message_limit = 100
    assistant_comparison_horizon = 8
    exchange_comparison_horizon = 8

    def filter(self, messages: Iterable[Mapping[str, object]]) -> ContextHygieneResult:
        raw = tuple(messages)
        if not raw:
            return ContextHygieneResult((), ContextHygieneStats.unchanged(raw))

        candidate_start = max(0, len(raw) - self.candidate_message_limit)
        assistant_indices = [
            index for index in range(candidate_start, len(raw))
            if _role(raw[index]) == "assistant"
        ]
        if not assistant_indices:
            return ContextHygieneResult(raw, ContextHygieneStats.unchanged(raw))

        exchanges = [
            (index, index + 1)
            for index in range(candidate_start, len(raw) - 1)
            if _role(raw[index]) == "user" and _role(raw[index + 1]) == "assistant"
        ]
        fingerprint_indices = set(assistant_indices)
        fingerprint_indices.update(user_index for user_index, _assistant_index in exchanges)
        fingerprint_cache = {index: _fingerprint(raw[index]) for index in fingerprint_indices}

        exchange_suppressed: set[int] = set()
        exchange_representative_assistants: set[int] = set()
        exchange_pairs_suppressed = 0

        # A redundant exchange is removed as a unit.  Compare only with newer
        # representatives that are still admitted so every omitted pair has a
        # verbatim, chronologically newer replacement in the working context.
        for position in range(len(exchanges) - 1, -1, -1):
            user_index, assistant_index = exchanges[position]
            newer_exchanges = exchanges[
                position + 1:position + 1 + self.exchange_comparison_horizon
            ]
            for newer_user, newer_assistant in newer_exchanges:
                if newer_user in exchange_suppressed or newer_assistant in exchange_suppressed:
                    continue
                if not _is_exchange_user_redundant(
                    fingerprint_cache[user_index], fingerprint_cache[newer_user],
                ):
                    continue
                if not _is_self_redundant(
                    fingerprint_cache[assistant_index], fingerprint_cache[newer_assistant],
                ):
                    continue
                exchange_suppressed.update((user_index, assistant_index))
                exchange_representative_assistants.add(newer_assistant)
                exchange_pairs_suppressed += 1
                break

        self_redundant: set[int] = set()
        adjacent_redundancy: list[bool] = []

        # Walk newest-first and suppress only older matches.  This preserves
        # the newest verbatim instance of any repetitive behavior.
        for position in range(len(assistant_indices) - 1, -1, -1):
            older_index = assistant_indices[position]
            newer = assistant_indices[position + 1:position + 1 + self.assistant_comparison_horizon]
            if any(_is_self_redundant(fingerprint_cache[older_index], fingerprint_cache[index]) for index in newer):
                self_redundant.add(older_index)

        for left, right in zip(assistant_indices, assistant_indices[1:]):
            adjacent_redundancy.append(_is_self_redundant(fingerprint_cache[left], fingerprint_cache[right]))

        repetitive_runs = 0
        run_links = 0
        for redundant in adjacent_redundancy:
            if redundant:
                run_links += 1
            else:
                if run_links >= 2:
                    repetitive_runs += 1
                run_links = 0
        if run_links >= 2:
            repetitive_runs += 1

        echo_indices: list[int] = []
        for index in assistant_indices:
            previous = index - 1
            if previous < 0 or _role(raw[previous]) != "user":
                continue
            if _is_user_echo(_fingerprint(raw[previous]), fingerprint_cache[index]):
                echo_indices.append(index)

        assistant_only_suppressed = set(self_redundant)
        # A single high-overlap response can be an intentional quotation.  V1
        # acts on echoing only when it is a repeated behavior, retaining the
        # newest echo while every user record remains present.
        if len(echo_indices) >= 2:
            assistant_only_suppressed.update(echo_indices[:-1])

        # Pair-owned messages and their admitted representatives must not be
        # independently counted or partially removed by assistant-only rules.
        assistant_only_suppressed.difference_update(exchange_suppressed)
        assistant_only_suppressed.difference_update(exchange_representative_assistants)

        # The latest assistant message anchors immediate turn continuity even
        # if it is itself repetitive.  Older redundant demonstrations are the
        # material removed from the transient context.
        newest_assistant = assistant_indices[-1]
        assistant_only_suppressed.discard(newest_assistant)

        suppressed = exchange_suppressed.union(assistant_only_suppressed)

        admitted = tuple(message for index, message in enumerate(raw) if index not in suppressed)
        removed_characters = sum(len(_content(raw[index])) for index in suppressed)
        return ContextHygieneResult(
            admitted,
            ContextHygieneStats(
                raw_recent_message_count=len(raw),
                candidate_count=len(assistant_indices),
                assistant_only_suppressed_count=len(assistant_only_suppressed),
                exchange_candidate_count=len(exchanges),
                exchange_pairs_suppressed_count=exchange_pairs_suppressed,
                admitted_message_count=len(admitted),
                suppressed_count=len(suppressed),
                user_echo_count=len(echo_indices),
                self_redundancy_count=len(self_redundant),
                repetitive_run_count=repetitive_runs,
                removed_characters=removed_characters,
            ),
        )
