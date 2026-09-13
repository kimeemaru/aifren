"""Provider-neutral dialogue semantics shared by backend speech consumers.

Canonical dialogue keeps its markers.  This module decides only which complete
spans are spoken and holds an incomplete streamed marker until its closing
marker arrives, so a stage direction can never leak into TTS merely because a
provider split it across deltas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class DialogueSpanKind(str, Enum):
    PLAIN_TEXT = "plain_text"
    EMPHASIS = "emphasis"
    EMOTE = "emote"


@dataclass(frozen=True)
class DialogueSpan:
    kind: DialogueSpanKind
    text: str


_ACTION_VERBS = {
    "smile", "smiles", "smiled", "smiling", "nod", "nods", "nodded", "nodding",
    "shake", "shakes", "shook", "shaking", "wave", "waves", "waved", "waving",
    "shrug", "shrugs", "shrugged", "shrugging", "tilt", "tilts", "tilted", "tilting",
    "cross", "crosses", "crossed", "crossing", "look", "looks", "looked", "looking",
    "sigh", "sighs", "sighed", "sighing", "think", "thinks", "thought", "thinking",
    "ponder", "ponders", "pondered", "pondering", "laugh", "laughs", "laughed", "laughing",
    "grin", "grins", "grinned", "grinning", "frown", "frowns", "frowned", "frowning",
    "turn", "turns", "turned", "turning", "blink", "blinks", "blinked", "blinking",
    "pause", "pauses", "paused", "pausing", "blush", "blushes", "blushed", "blushing",
    "chuckle", "chuckles", "chuckled", "chuckling", "giggle", "giggles", "giggled", "giggling",
    "gasp", "gasps", "gasped", "gasping", "stare", "stares", "stared", "staring",
    "glance", "glances", "glanced", "glancing", "raise", "raises", "raised", "raising",
    "lower", "lowers", "lowered", "lowering", "rub", "rubs", "rubbed", "rubbing",
    "bite", "bites", "bit", "biting", "lean", "leans", "leaned", "leaning",
    "shift", "shifts", "shifted", "shifting", "tap", "taps", "tapped", "tapping",
    "take", "takes", "took", "taking", "breathe", "breathes", "breathed", "breathing",
    "walk", "walks", "walked", "walking",
}

# Parenthetical normalization is deliberately narrower than the general emote
# classifier.  It is a defense for generated stage directions, not a new
# interpretation of parentheses in ordinary prose.  In particular, cognitive
# asides such as ``(I think...)`` remain spoken.
_PHYSICAL_PARENTHETICAL_ACTION_VERBS = _ACTION_VERBS - {
    "think", "thinks", "thought", "thinking",
    "ponder", "ponders", "pondered", "pondering",
} | {
    # Body/animal actions commonly appear after a subject noun (for example
    # ``her ears twitch``), beyond the general classifier's opening words.
    "twitch", "twitches", "twitched", "twitching",
    "purr", "purrs", "purred", "purring",
}


def is_action_emote(text: str) -> bool:
    words = str(text or "").strip().split()
    if not words:
        return False
    normalized = [word.strip("\"'.,!?;:").lower() for word in words[:2]]
    return any(word in _ACTION_VERBS for word in normalized)


def is_high_confidence_parenthetical_action(text: str) -> bool:
    """Return whether generated parenthetical prose is clearly a stage action.

    This intentionally accepts false negatives.  Parentheses remain ordinary
    spoken prose unless a bounded opening phrase contains a physical/action
    predicate.  The limit avoids treating later incidental verbs in a factual
    aside as ownership of the whole span.
    """
    value = str(text or "").strip()
    if not value or len(value) > 800 or "(" in value or ")" in value or "?" in value:
        return False
    words = [
        word.strip("\"'.,!?;:—-[]{}\n\r\t").lower()
        for word in value.split()[:6]
    ]
    return any(word in _PHYSICAL_PARENTHETICAL_ACTION_VERBS for word in words)


class AssistantOuterParentheticalActionNormalizer:
    """Normalize generated ``*(action)*`` without guessing at emphasis.

    A possible outer-star wrapper is retained until its parenthesis, closing
    star, and following boundary are known. This makes provider chunking
    irrelevant and lets the existing action classifiers own the decision.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._history = ""
        self.normalized_starred_parenthetical_actions = 0

    def feed(self, raw: object) -> str:
        self._buffer += str(raw or "")
        return self._drain(final=False)

    def finish(self) -> str:
        rendered = self._drain(final=True)
        if self._buffer:
            rendered += self._buffer
            self._remember(self._buffer)
            self._buffer = ""
        return rendered

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []
        while self._buffer:
            start = self._next_unescaped_star()
            if start < 0:
                self._emit(output, self._buffer)
                self._buffer = ""
                break
            if start > 0:
                self._emit(output, self._buffer[:start])
                self._buffer = self._buffer[start:]
            if len(self._buffer) == 1 and not final:
                break
            if self._buffer.startswith("**"):
                self._emit(output, "**")
                self._buffer = self._buffer[2:]
                continue

            opening = 1
            while opening < len(self._buffer) and self._buffer[opening].isspace():
                opening += 1
            if opening >= len(self._buffer):
                if not final:
                    break
                self._emit(output, self._buffer[0])
                self._buffer = self._buffer[1:]
                continue
            if self._buffer[opening] != "(":
                self._emit(output, self._buffer[0])
                self._buffer = self._buffer[1:]
                continue

            closing, nested = _matching_parenthesis(self._buffer, opening)
            if closing < 0:
                if not final:
                    break
                self._emit(output, self._buffer[0])
                self._buffer = self._buffer[1:]
                continue
            trailing = closing + 1
            while trailing < len(self._buffer) and self._buffer[trailing].isspace():
                trailing += 1
            if trailing >= len(self._buffer):
                if not final:
                    break
                self._emit(output, self._buffer[0])
                self._buffer = self._buffer[1:]
                continue
            if self._buffer[trailing] != "*":
                self._emit(output, self._buffer[0])
                self._buffer = self._buffer[1:]
                continue

            wrapper_end = trailing + 1
            suffix = self._buffer[wrapper_end:]
            if not final and not suffix.strip():
                # The existing standalone-span rule needs the next visible
                # boundary before it can distinguish action from emphasis.
                break
            content = self._buffer[opening + 1:closing].strip()
            if (not nested and content and "*" not in content
                    and any(character.isalpha() for character in content)
                    and self._is_action_owned(content, suffix)):
                self._emit(output, "*" + content + "*")
                self.normalized_starred_parenthetical_actions += 1
            else:
                self._emit(output, self._buffer[:wrapper_end])
            self._buffer = suffix
        return "".join(output)

    def _is_action_owned(self, content: str, suffix: str) -> bool:
        if is_high_confidence_parenthetical_action(content):
            return True
        candidate = self._history + "*" + content + "*" + suffix
        start = len(self._history)
        end = start + len(content) + 1
        return _single_star_is_emote(candidate, start, end, content)

    def _next_unescaped_star(self) -> int:
        value = self._history + self._buffer
        literals = _literal_spans(value, include_unclosed=True)
        for index, character in enumerate(self._buffer):
            if character != "*":
                continue
            absolute = len(self._history) + index
            if any(start <= absolute < end for start, end, _complete in literals):
                continue
            prefix = self._history + self._buffer[:index]
            backslashes = len(prefix) - len(prefix.rstrip("\\"))
            if backslashes % 2 == 0:
                return index
        return -1

    def _emit(self, output: list[str], value: str) -> None:
        output.append(value)
        self._remember(value)

    def _remember(self, value: str) -> None:
        self._history = (self._history + value)[-4096:]


class AssistantParentheticalActionNormalizer:
    """Normalize only high-confidence generated ``(action)`` spans.

    A possible parenthetical is held until its close, making provider-streamed
    output byte-for-byte equivalent to whole-output canonicalization.  User
    content never crosses this assistant-output boundary.
    """

    def __init__(self) -> None:
        self._parenthetical = ""
        self._history = ""
        self._inside_parenthetical = False
        self._nested = False
        self._started_inside_action = False
        self._started_at_action_boundary = False
        self.normalized_parenthesized_star_actions = 0

    def feed(self, raw: object) -> str:
        output: list[str] = []
        for character in str(raw or ""):
            if not self._inside_parenthetical:
                if character == "(" and not _unclosed_literal(self._history):
                    self._inside_parenthetical = True
                    self._parenthetical = character
                    self._nested = False
                    self._started_inside_action = _has_unclosed_single_star(self._history)
                    prefix = self._history.rstrip()
                    self._started_at_action_boundary = not prefix or prefix[-1] in ".!?…\n"
                else:
                    output.append(character)
                    self._remember(character)
                continue

            self._parenthetical += character
            if character == "(":
                self._nested = True
            if character != ")":
                continue
            rendered = self._render_parenthetical()
            output.append(rendered)
            self._remember(rendered)
            self._inside_parenthetical = False
            self._parenthetical = ""
            self._nested = False
            self._started_inside_action = False
            self._started_at_action_boundary = False
        return "".join(output)

    def finish(self) -> str:
        pending = self._parenthetical
        self._parenthetical = ""
        self._inside_parenthetical = False
        self._nested = False
        self._started_inside_action = False
        self._started_at_action_boundary = False
        self._remember(pending)
        return pending

    def _render_parenthetical(self) -> str:
        content = self._parenthetical[1:-1]
        if not self._nested and not self._started_inside_action:
            explicit_action = _canonical_parenthesized_star_action(
                content,
                at_action_boundary=self._started_at_action_boundary,
            )
            if explicit_action is not None:
                self.normalized_parenthesized_star_actions += 1
                return explicit_action
        if (not self._nested and not self._started_inside_action
                and self._started_at_action_boundary
                and "*" not in content
                and is_high_confidence_parenthetical_action(content)):
            return "*" + content.strip() + "*"
        return self._parenthetical

    def _remember(self, value: str) -> None:
        # This is only syntactic context for determining whether a new
        # parenthesis already sits inside an action span.
        self._history = (self._history + value)[-4096:]


def normalize_generated_parenthetical_actions(raw: object) -> str:
    normalizer = AssistantParentheticalActionNormalizer()
    return normalizer.feed(raw) + normalizer.finish()


def normalize_generated_dialogue_actions(raw: object) -> str:
    """Normalize fresh decoded dialogue, never an entire transport envelope.

    The caller must own fresh assistant output. Historical/canonical strings
    and backend-owned memory cores do not pass through this normalization.
    """
    outer = AssistantOuterParentheticalActionNormalizer()
    rendered = outer.feed(raw) + outer.finish()
    return normalize_generated_parenthetical_actions(rendered)


def _canonical_parenthesized_star_action(
    content: object,
    *,
    at_action_boundary: bool,
) -> str | None:
    """Unwrap exactly one explicitly starred physical action.

    Parentheses remain ordinary prose.  This accepts only the malformed model
    shape ``( *physical action* )``: the entire parenthetical must be one
    single-star action span, with no surrounding prose. Formatting inside an
    already-owned outer action retains that owner; adding another pair of
    stars would incorrectly turn the whole action into spoken emphasis.
    """
    value = str(content or "").strip()
    if (len(value) < 3 or not value.startswith("*") or not value.endswith("*")
            or value.startswith("**") or value.endswith("**")):
        return None
    action = value[1:-1].strip()
    if (not action or not any(character.isalpha() for character in action)
            or _find_marker_end(value, 1, 1) != len(value) - 1
            or not (
                is_high_confidence_parenthetical_action(action)
                # The canonical single-star form is itself an RP action when
                # it owns a standalone segment under the existing contract.
                or at_action_boundary
            )):
        return None
    return "*" + action + "*"


def _matching_parenthesis(value: str, opening: int) -> tuple[int, bool]:
    depth = 0
    nested = False
    for index in range(opening, len(value)):
        if value[index] == "(":
            depth += 1
            nested = nested or depth > 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return index, nested
    return -1, nested


def _has_unclosed_single_star(value: str) -> bool:
    singles = 0
    index = 0
    while index < len(value):
        if value[index] != "*" or _is_escaped(value, index):
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "*":
            index += 2
            continue
        singles += 1
        index += 1
    return bool(singles % 2)


def parse_dialogue(raw: object) -> tuple[DialogueSpan, ...]:
    value = str(raw or "")
    spans: list[DialogueSpan] = []
    cursor = 0
    while cursor < len(value):
        start, marker_length = _find_marker_start(value, cursor)
        if start < 0:
            _add(spans, DialogueSpanKind.PLAIN_TEXT, value[cursor:])
            break
        end = _find_marker_end(value, start + marker_length, marker_length)
        if end < 0:
            _add(spans, DialogueSpanKind.PLAIN_TEXT, value[cursor:])
            break
        _add(spans, DialogueSpanKind.PLAIN_TEXT, value[cursor:start])
        content = value[start + marker_length:end].strip()
        if not content or not any(character.isalpha() for character in content):
            _add(spans, DialogueSpanKind.PLAIN_TEXT, value[start:end + marker_length])
        else:
            emote = marker_length == 1 and _single_star_is_emote(
                value, start, end, content,
            )
            _add(spans, DialogueSpanKind.EMOTE if emote else DialogueSpanKind.EMPHASIS, content)
        cursor = end + marker_length
    return tuple(spans)


def spoken_text(raw: object) -> str:
    output = "".join(span.text for span in parse_dialogue(raw) if span.kind != DialogueSpanKind.EMOTE)
    return sanitize_spoken_unicode(_normalize_whitespace(output))


def sanitize_spoken_unicode(raw: object) -> str:
    """Remove Unicode emoji clusters from the speech projection only.

    Canonical dialogue is never changed.  The scanner handles variation
    selectors, skin tones, ZWJ sequences, flags, tags, and keycaps without a
    provider-specific pronunciation table.
    """
    value = str(raw or "")
    output: list[str] = []
    removed_at_end = False
    index = 0
    while index < len(value):
        codepoint = ord(value[index])
        keycap_end = _keycap_cluster_end(value, index)
        if keycap_end is not None:
            index = keycap_end
            removed_at_end = not value[index:].strip()
            continue
        if _is_emoji_base(codepoint):
            index = _emoji_cluster_end(value, index)
            removed_at_end = not value[index:].strip()
            continue
        if _is_emoji_modifier(codepoint) or _is_variation_selector(codepoint) or codepoint == 0x200D:
            # Orphaned cluster controls are never meaningful speech.
            index += 1
            removed_at_end = not value[index:].strip()
            continue
        output.append(value[index])
        index += 1

    spoken = _normalize_whitespace("".join(output))
    spoken = re.sub(r"\s+([,.;:!?])", r"\1", spoken).strip()
    if (removed_at_end and spoken and any(character.isalnum() for character in spoken)
            and spoken[-1] not in ".!?…,:;—-"):
        spoken += "."
    return spoken


class AssistantEmojiStreamSanitizer:
    """Remove generated emoji without damaging ordinary streamed Unicode.

    User content never crosses this boundary. A tiny holdback covers keycap
    sequences whose first ASCII character can arrive in an earlier provider
    delta; other emoji clusters are safe to discard incrementally because
    every base, modifier, selector, and joiner is independently non-rendered.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._pending_whitespace = ""
        self._emoji_removed_after_pending = False

    def feed(self, raw: object) -> str:
        self._buffer += str(raw or "")
        hold = self._incomplete_keycap_prefix_length(self._buffer)
        boundary = len(self._buffer) - hold
        value, self._buffer = self._buffer[:boundary], self._buffer[boundary:]
        return self._sanitize(value)

    def finish(self) -> str:
        value, self._buffer = self._buffer, ""
        rendered = self._sanitize(value)
        if self._pending_whitespace and not self._emoji_removed_after_pending:
            rendered += self._pending_whitespace
        self._pending_whitespace = ""
        self._emoji_removed_after_pending = False
        return rendered

    def _sanitize(self, value: str) -> str:
        output: list[str] = []
        index = 0
        while index < len(value):
            keycap_end = _keycap_cluster_end(value, index)
            codepoint = ord(value[index])
            if keycap_end is not None:
                self._emoji_removed_after_pending = True
                if self._pending_whitespace:
                    self._pending_whitespace = " "
                index = keycap_end
                continue
            if _is_emoji_base(codepoint):
                self._emoji_removed_after_pending = True
                if self._pending_whitespace:
                    self._pending_whitespace = " "
                index = _emoji_cluster_end(value, index)
                continue
            if (_is_emoji_modifier(codepoint) or _is_variation_selector(codepoint)
                    or codepoint == 0x200D):
                self._emoji_removed_after_pending = True
                index += 1
                continue
            character = value[index]
            index += 1
            if character.isspace():
                if not (self._emoji_removed_after_pending and self._pending_whitespace):
                    self._pending_whitespace += character
                continue
            if self._pending_whitespace:
                if character not in ",.;:!?\u2026":
                    output.append(self._pending_whitespace)
                self._pending_whitespace = ""
                self._emoji_removed_after_pending = False
            output.append(character)
        return "".join(output)

    @staticmethod
    def _incomplete_keycap_prefix_length(value: str) -> int:
        if not value:
            return 0
        if value[-1] in "#*0123456789":
            return 1
        if (len(value) >= 2 and value[-2] in "#*0123456789"
                and _is_variation_selector(ord(value[-1]))):
            return 2
        return 0


def sanitize_assistant_output_unicode(raw: object) -> str:
    """Remove generated emoji from canonical assistant presentation text."""
    sanitizer = AssistantEmojiStreamSanitizer()
    return sanitizer.feed(raw) + sanitizer.finish()


def _keycap_cluster_end(value: str, index: int) -> int | None:
    if value[index] not in "#*0123456789":
        return None
    cursor = index + 1
    if cursor < len(value) and _is_variation_selector(ord(value[cursor])):
        cursor += 1
    return cursor + 1 if cursor < len(value) and ord(value[cursor]) == 0x20E3 else None


def _emoji_cluster_end(value: str, index: int) -> int:
    cursor = index + 1
    while cursor < len(value):
        codepoint = ord(value[cursor])
        if (_is_variation_selector(codepoint) or _is_emoji_modifier(codepoint)
                or 0xE0020 <= codepoint <= 0xE007F):
            cursor += 1
            continue
        if codepoint == 0x200D:
            cursor += 1
            if cursor < len(value) and _is_emoji_base(ord(value[cursor])):
                cursor += 1
                continue
            break
        # A pair of regional indicators is one flag cluster.
        if (_is_regional_indicator(ord(value[index]))
                and _is_regional_indicator(codepoint)):
            cursor += 1
        break
    return cursor


def _is_emoji_base(codepoint: int) -> bool:
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x26FF
        or 0x2700 <= codepoint <= 0x27BF
        or _is_regional_indicator(codepoint)
        or codepoint in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299}
    )


def _is_regional_indicator(codepoint: int) -> bool:
    return 0x1F1E6 <= codepoint <= 0x1F1FF


def _is_emoji_modifier(codepoint: int) -> bool:
    return 0x1F3FB <= codepoint <= 0x1F3FF


def _is_variation_selector(codepoint: int) -> bool:
    return codepoint in {0xFE0E, 0xFE0F}


class StableDialogueTextStream:
    """Release only prefixes whose asterisk-span meaning is complete."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: object) -> str:
        self._buffer += str(delta or "")
        boundary = _stable_prefix_length(self._buffer, final=False)
        if boundary <= 0:
            return ""
        stable, self._buffer = self._buffer[:boundary], self._buffer[boundary:]
        return stable

    def finish(self) -> str:
        # End-of-stream does not make an unclosed marker semantically stable.
        # Release any plain prefix, but never guess that an incomplete action
        # should be spoken merely because the provider stopped producing text.
        boundary = _stable_prefix_length(self._buffer, final=True)
        value, self._buffer = self._buffer[:boundary], ""
        return value


class SemanticSentenceAccumulator:
    """Project exact streamed dialogue into complete, safe spoken sentences.

    Canonical fragments are appended byte-for-byte.  Whitespace normalization
    and emote removal happen only after a complete sentence has been isolated,
    never once per provider delta.  This keeps provider token boundaries out of
    both the canonical response and the spoken word boundaries.
    """

    _SENTENCE = re.compile(r"[.!?][\"')\]]*(?=\s)")

    def __init__(self) -> None:
        self._semantics = StableDialogueTextStream()
        self._stable = ""

    def feed(self, delta: object) -> tuple[str, ...]:
        self._stable += self._semantics.feed(delta)
        return self._take_complete(final=False)

    def finish(self) -> tuple[str, ...]:
        self._stable += self._semantics.finish()
        return self._take_complete(final=True)

    def _take_complete(self, *, final: bool) -> tuple[str, ...]:
        output: list[str] = []
        while True:
            boundary = self._next_sentence_boundary(self._stable)
            if boundary is None:
                break
            raw, self._stable = self._stable[:boundary], self._stable[boundary:]
            spoken = spoken_text(raw)
            if spoken:
                output.append(spoken)

        if final and self._stable:
            raw, self._stable = self._stable, ""
            spoken = spoken_text(raw)
            if spoken:
                output.append(spoken)
        return tuple(output)

    @classmethod
    def _next_sentence_boundary(cls, raw: str) -> int | None:
        """Map a spoken punctuation boundary back to the exact raw index."""
        projected: list[str] = []
        raw_ends: list[int] = []
        cursor = 0
        while cursor < len(raw):
            start, marker_length = _find_marker_start(raw, cursor)
            if start < 0:
                cls._append_projection(projected, raw_ends, raw, cursor, len(raw))
                break
            cls._append_projection(projected, raw_ends, raw, cursor, start)
            end = _find_marker_end(raw, start + marker_length, marker_length)
            if end < 0:
                break
            content = raw[start + marker_length:end]
            emote = marker_length == 1 and _single_star_is_emote(
                raw, start, end, content,
            )
            if not emote:
                projected_start = len(projected)
                cls._append_projection(
                    projected, raw_ends, raw, start + marker_length, end
                )
                if len(projected) > projected_start:
                    # Consuming the last spoken character must also consume its
                    # closing marker from the raw stream.
                    raw_ends[-1] = end + marker_length
            cursor = end + marker_length

        match = cls._SENTENCE.search("".join(projected))
        if match is None:
            return None
        return raw_ends[match.end() - 1]

    @staticmethod
    def _append_projection(
        projected: list[str], raw_ends: list[int], raw: str, start: int, end: int
    ) -> None:
        for index in range(start, end):
            projected.append(raw[index])
            raw_ends.append(index + 1)


class SemanticSpeechGrouper:
    """Keep the first sentence immediate, then group small adjacent sentences."""

    def __init__(self, maximum_chars: int = 260) -> None:
        self._maximum_chars = max(80, int(maximum_chars))
        self._first_emitted = False
        self._pending = ""

    def feed(self, sentence: object) -> tuple[str, ...]:
        value = str(sentence or "")
        if not value:
            return ()
        if not self._first_emitted:
            self._first_emitted = True
            return (value,)
        if not self._pending:
            if len(value) > self._maximum_chars:
                return (value,)
            self._pending = value
            return ()

        combined = self._join(self._pending, value)
        if len(combined) <= self._maximum_chars:
            self._pending = ""
            return (combined,)

        ready, self._pending = self._pending, ""
        if len(value) > self._maximum_chars:
            return ready, value
        self._pending = value
        return (ready,)

    def finish(self) -> tuple[str, ...]:
        if not self._pending:
            return ()
        ready, self._pending = self._pending, ""
        return (ready,)

    @staticmethod
    def _join(first: str, second: str) -> str:
        separator = "" if first[-1].isspace() or second[0].isspace() else " "
        return first + separator + second


def _stable_prefix_length(value: str, *, final: bool = False) -> int:
    if not final:
        # A split quote/code delimiter or escape cannot lose its ownership
        # when a plain prefix is released. Retain the literal from its opener,
        # then release it whole once the closing delimiter is available.
        for start, _end, complete in _literal_spans(value, include_unclosed=True):
            if not complete:
                return min(start, _stable_prefix_length(value[:start], final=final))
    cursor = 0
    while cursor < len(value):
        start, marker_length = _find_marker_start(value, cursor)
        if start < 0:
            # A trailing marker is ambiguous until the next delta establishes
            # whether it begins single- or double-marker semantics.
            trailing = len(value) - 1
            if trailing >= cursor and value[trailing] == "*" and not _is_escaped(value, trailing):
                return trailing
            if not final and trailing >= cursor and value[trailing] == "\\":
                return trailing
            return len(value)
        end = _find_marker_end(
            value, start + marker_length, marker_length,
            allow_trailing_single_close=final,
        )
        if end < 0:
            return start
        cursor = end + marker_length
    return len(value)


def _single_star_is_emote(value: str, start: int, end: int, content: str) -> bool:
    """Classify one complete single-star span by semantic ownership.

    Action-shaped spans are nonspoken anywhere. A whole standalone starred
    segment is also an RP action boundary. Inline formatting inside an
    otherwise spoken sentence remains spoken, regardless of word count.
    """
    if is_action_emote(content):
        return True
    before = value[:start].rstrip().rstrip("*").rstrip()
    after = value[end + 1:].lstrip()
    starts_segment = not before or before[-1] in ".!?\n"
    ends_segment = not after or content.rstrip().endswith((".", "!", "?"))
    return starts_segment and ends_segment


def _find_marker_start(value: str, offset: int) -> tuple[int, int]:
    literals = _literal_spans(value)
    for index in range(offset, len(value)):
        if value[index] != "*" or _is_escaped(value, index):
            continue
        if any(start <= index < end for start, end, _complete in literals):
            continue
        double = (
            index + 1 < len(value) and value[index + 1] == "*"
            and (index == 0 or value[index - 1] != "*")
            and (index + 2 >= len(value) or value[index + 2] != "*")
        )
        if double:
            # A delta may end exactly after an opening ``**``.  Treat the
            # complete marker atomically and retain it until the next delta
            # establishes its content; releasing its first star corrupts the
            # state for every following ordinary spoken character.
            if index + 2 < len(value) and value[index + 2].isspace():
                continue
            return index, 2
        if _is_double_star(value, index) or index + 1 >= len(value) or value[index + 1].isspace():
            continue
        return index, 1
    return -1, 0


def _find_marker_end(
    value: str,
    offset: int,
    marker_length: int,
    *,
    allow_trailing_single_close: bool = True,
) -> int:
    literals = _literal_spans(value)
    for index in range(offset, len(value)):
        if value[index] != "*" or _is_escaped(value, index):
            continue
        if any(start <= index < end for start, end, _complete in literals):
            continue
        if marker_length == 2:
            if (
                index + 1 < len(value) and value[index + 1] == "*"
                and (index == 0 or value[index - 1] != "*")
                and (index + 2 >= len(value) or value[index + 2] != "*")
            ):
                return index
        elif not _is_double_star(value, index):
            if index + 1 == len(value) and not allow_trailing_single_close:
                return -1
            # RP action spans are an outer semantic boundary, not ordinary
            # Markdown. A single-star emphasis run inside an action must not
            # terminate the action and leak the remaining stage direction to
            # speech. Treat `` *word* `` as one bounded nested formatting run
            # and continue looking for the outer close. If that nested run is
            # incomplete, retain the whole outer action as incomplete too.
            if _looks_like_nested_single_open(value, index):
                nested_end = _find_nested_single_end(value, index + 1)
                if nested_end < 0:
                    return -1
                # The loop will revisit the nested close unless the scan
                # advances explicitly.
                return _find_marker_end(
                    value, nested_end + 1, marker_length,
                    allow_trailing_single_close=allow_trailing_single_close,
                )
            return index
    return -1


def _looks_like_nested_single_open(value: str, index: int) -> bool:
    return (
        index > 0
        and value[index - 1].isspace()
        and (
            index + 1 >= len(value)
            or (not value[index + 1].isspace() and value[index + 1] != "*")
        )
    )


def _find_nested_single_end(value: str, offset: int) -> int:
    for index in range(offset, len(value)):
        if value[index] != "*" or _is_escaped(value, index) or _is_double_star(value, index):
            continue
        if index > offset and not value[index - 1].isspace():
            return index
    return -1


def _is_escaped(value: str, index: int) -> bool:
    return index > 0 and value[index - 1] == "\\"


def _literal_spans(value: str, *, include_unclosed: bool = False) -> tuple[tuple[int, int, bool], ...]:
    """Closed code/quotation spans are data, never fresh RP instructions.

    This is delimiter ownership only. It does not infer reported speech from
    prose or change the words/markup inside a literal. An apostrophe adjacent
    to a word on its left is not an opening quotation (``don't``/``users'``).
    """
    spans: list[tuple[int, int, bool]] = []
    cursor = 0
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    while cursor < len(value):
        marker = value[cursor]
        if (_is_escaped(value, cursor) or marker not in (*pairs, "`")
                or (marker in {"'", "‘"} and cursor > 0 and value[cursor - 1].isalnum())):
            cursor += 1
            continue
        width = 1
        if marker == "`":
            while cursor + width < len(value) and value[cursor + width] == "`":
                width += 1
        closing = "`" * width if marker == "`" else pairs[marker]
        search = cursor + width
        end = -1
        while search < len(value):
            candidate = value.find(closing, search)
            if candidate < 0:
                break
            after = candidate + len(closing)
            if (_is_escaped(value, candidate)
                    or (marker == "`" and ((candidate > 0 and value[candidate - 1] == "`")
                                           or (after < len(value) and value[after] == "`")))
                    or (marker in {"'", "‘"} and after < len(value) and value[after].isalnum())):
                search = candidate + 1
                continue
            end = after
            break
        if end < 0:
            if include_unclosed:
                spans.append((cursor, len(value), False))
            break
        spans.append((cursor, end, True))
        cursor = end
    return tuple(spans)


def _unclosed_literal(value: str) -> bool:
    return any(not complete for _start, _end, complete in _literal_spans(value, include_unclosed=True))


def _is_double_star(value: str, index: int) -> bool:
    return (index > 0 and value[index - 1] == "*") or (index + 1 < len(value) and value[index + 1] == "*")


def _add(spans: list[DialogueSpan], kind: DialogueSpanKind, text: str) -> None:
    if text:
        spans.append(DialogueSpan(kind, text))


def _normalize_whitespace(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\t ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
