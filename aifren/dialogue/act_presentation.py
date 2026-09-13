"""Opt-in presentation prefix on fresh, owned provider output only.

Never call this on canonical/history/source text. Non-prefix and quoted syntax
is literal data. This module owns no effects, persistence, memory or repair.
"""
from dataclasses import replace
import re

from aifren.dialogue.presentation_metadata import (EMOTIONS, GESTURES, ResponsePresentationMetadata,
                                   parse_assistant_response, response_contract_prompt)

PREFIX = "<|ACT:"
MAX_MARKER = 256
MAX_DISCARD = 4096


def act_character_prompt(prompt: str) -> str | None:
    contract = response_contract_prompt()
    if prompt.count(contract) != 1 or not prompt.rstrip().endswith(contract):
        return None
    return prompt[:prompt.rfind(contract)] + (
        "OPTIONAL AVATAR CUES:\n"
        "Reply naturally as the character in plain dialogue. Only when a visible CHANGE fits, "
        "you may begin with one marker, for example "
        "<|ACT:emotion=happy;intensity=0.6;gesture=agreement|>That sounds good.\n"
        "No marker means no requested change; do not label every reply's tone. "
        "emotion: neutral, happy, amused, relaxed, sad, angry, surprised. "
        "gesture: greeting, agreement, disagreement, thinking, encouragement, surprise. "
        "intensity: decimal 0 to 1. All fields optional, each at most once; no other fields. "
        "neutral resets the face. Prefix only; no JSON presentation or markers inside dialogue. "
        "These cues grant no action, state or capability permission. "
        "Keep replies proportionate, normally at most 100 words. No emoji. "
        "Nonspoken actions use *action spans*, not parentheses; spoken emphasis stays speech."
    )


class ActPrefixStream:
    """Bounded streaming separation, never a presentation dispatcher.

    At most 256 control characters are buffered. Oversized controls can be
    discarded up to a 4096-character recovery ceiling; no close means no safe
    dialogue boundary. Repeated adjacent prefixes invalidate ALL ACT fields.
    Once prose/JSON/quoted text starts, no later text is treated as control.
    """
    def __init__(self):
        self.mode = "prefix"
        self.buffer = ""
        self.marker_count = 0
        self.control_size = 0
        self.previous = ""
        self.presentation = None
        self.status = "omitted"

    def _fail(self, reason):
        self.presentation = None
        self.status = reason

    def _fields(self, body):
        fields = {}
        for entry in body.split(";"):
            m = re.fullmatch(r"(emotion|intensity|gesture)=([^;=]+)", entry)
            if m is None or m[1] in fields:
                return None, "field"
            key, value = m.groups()
            if key == "emotion" and value not in EMOTIONS or key == "gesture" and value not in GESTURES:
                return None, "value"
            if key == "intensity":
                if not re.fullmatch(r"(?:0(?:\.[0-9]+)?|1(?:\.0+)?)", value):
                    return None, "intensity"
                value = float(value)
            fields[key] = value
        return ResponsePresentationMetadata(**fields, origin="act"), "valid"

    def feed(self, text: str) -> str:
        out = []
        for char in text:
            if self.mode == "blocked":
                continue
            if self.mode == "dialogue":
                out.append(char)
                continue
            if self.mode == "prefix":
                if not self.buffer and char.isspace():
                    continue
                self.buffer += char
                if PREFIX.startswith(self.buffer):
                    if self.buffer == PREFIX:
                        self.marker_count += 1
                        self.control_size = len(PREFIX)
                        self.mode = "marker"
                        if self.marker_count > 1:
                            self._fail("repeated")
                    continue
                self.mode = "dialogue"
                out.append(self.buffer)
                self.buffer = ""
                continue
            self.control_size += 1
            if self.control_size > MAX_DISCARD:
                self._fail("recovery_bound")
                self.mode = "blocked"
                self.buffer = ""
                continue
            if self.control_size <= MAX_MARKER:
                self.buffer += char
            elif self.status != "repeated":
                self._fail("over_bound")
            if self.previous == "|" and char == ">":
                if self.marker_count == 1 and self.control_size <= MAX_MARKER:
                    self.presentation, self.status = self._fields(self.buffer[len(PREFIX):-2])
                self.buffer = ""
                self.mode = "prefix"
            self.previous = char
        return "".join(out)

    def finish(self) -> str:
        if self.mode == "marker":
            self._fail("missing_close")
            self.buffer = ""
            self.mode = "blocked"
        # An incomplete sentinel at EOF is unsafe to expose as control debris.
        if self.mode == "prefix" and self.buffer:
            self._fail("incomplete_prefix")
            self.buffer = ""
        return ""


def parse_fresh_act_response(raw: str, *, diagnostics=None):
    from aifren.llm.output_canonicalization import canonicalize_model_output
    stream = ActPrefixStream()
    dialogue = stream.feed(str(raw or "")) + stream.finish()
    # Validate the control BEFORE emoji/action/Unicode normalization. Otherwise
    # normalizing an invalid field could accidentally make it executable.
    dialogue = canonicalize_model_output(dialogue, diagnostics=diagnostics)
    if stream.status == "omitted" and dialogue.lstrip().startswith(PREFIX):
        # A non-outer prefix exposed by reasoning removal is not a new control
        # opportunity. Its intent/boundary is ambiguous; never publish debris.
        dialogue = ""
        stream._fail("non_outer_prefix")
    parsed = parse_assistant_response(dialogue, normalize_generated_dialogue=True)
    if stream.marker_count:
        # One provider presentation owner. ACT replaces the entire optional
        # legacy presentation object, never its response/action obligations.
        # Capability normalization still runs afterwards under backend policy.
        parsed = replace(parsed, presentation=stream.presentation,
                         has_presentation_contract=stream.presentation is not None)
    return parsed, stream.status
