"""Provider-neutral canonicalization for raw model output."""

from __future__ import annotations

from dialogue_semantics import (
    AssistantEmojiStreamSanitizer,
    AssistantParentheticalActionNormalizer,
)


class ModelOutputCanonicalizer:
    """Remove internal reasoning blocks while preserving streamable dialogue.

    Qwen-family compatible endpoints may place private reasoning in
    ``<think>...</think>`` before their user-facing answer.  The small state
    machine retains possible marker prefixes between provider chunks, so no
    partial marker or reasoning text can escape downstream.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_reasoning = False
        self._removed_reasoning = False
        self._visible_started = False
        self._emoji = AssistantEmojiStreamSanitizer()
        self._parenthetical_actions = AssistantParentheticalActionNormalizer()

    def feed(self, value: object) -> str:
        self._buffer += str(value or "")
        visible: list[str] = []
        while self._buffer:
            marker = self._CLOSE if self._inside_reasoning else self._OPEN
            lowered = self._buffer.lower()
            index = lowered.find(marker)
            if index >= 0:
                if not self._inside_reasoning:
                    visible.append(self._visible(self._buffer[:index]))
                    self._inside_reasoning = True
                    self._removed_reasoning = True
                else:
                    self._inside_reasoning = False
                self._buffer = self._buffer[index + len(marker):]
                continue

            retained = self._marker_prefix_length(lowered, marker)
            if self._inside_reasoning:
                self._buffer = self._buffer[-retained:] if retained else ""
            else:
                emit_to = len(self._buffer) - retained
                visible.append(self._visible(self._buffer[:emit_to]))
                self._buffer = self._buffer[emit_to:]
            break
        return "".join(visible)

    def finish(self) -> str:
        if self._inside_reasoning:
            self._buffer = ""
            return self._finish_visible()
        remaining, self._buffer = self._buffer, ""
        return self._visible(remaining) + self._finish_visible()

    def _visible(self, text: str) -> str:
        if self._removed_reasoning and not self._visible_started:
            text = text.lstrip()
        text = self._parenthetical_actions.feed(self._emoji.feed(text))
        if text:
            self._visible_started = True
        return text

    def _finish_visible(self) -> str:
        emoji_tail = self._emoji.finish()
        return self._parenthetical_actions.feed(emoji_tail) + self._parenthetical_actions.finish()

    @staticmethod
    def _marker_prefix_length(value: str, marker: str) -> int:
        maximum = min(len(value), len(marker) - 1)
        for length in range(maximum, 0, -1):
            if value.endswith(marker[:length]):
                return length
        return 0


def canonicalize_model_output(value: object) -> str:
    canonicalizer = ModelOutputCanonicalizer()
    return canonicalizer.feed(value) + canonicalizer.finish()
