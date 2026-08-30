"""Frontend-neutral semantic presentation metadata for one assistant turn."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

from dialogue_semantics import sanitize_assistant_output_unicode


EMOTIONS = frozenset({"neutral", "happy", "amused", "relaxed", "sad", "angry", "surprised"})
GESTURES = frozenset({"greeting", "agreement", "disagreement", "thinking", "encouragement", "surprise"})
POSES = frozenset({"sleeping", "awake"})
GAZE_MODES = frozenset({"normal", "suppressed"})
REACTIONS = frozenset({"settle", "shift", "stir", "startle", "wake"})
SPEECH_MODES = frozenset({"normal", "constrained", "unavailable", "nonverbal", "mumble"})
RESPONSE_MODES = frozenset({
    "normal_conversation", "constrained_reaction", "sleep_reaction", "waking",
    "speech_constrained", "nonverbal_reaction", "action_decision",
})
CAPABILITY_COMPLIANCE = frozenset({
    # Closed domain names from the payload plus the first-slice capability
    # names. This optional diagnostic metadata cannot authorize behavior.
    "perception", "communication", "manipulation", "awareness",
    "vision", "hearing", "smell", "taste", "touch", "speech", "hands",
    "arms", "locomotion", "posture",
})
DEFAULT_INTENSITY = 0.70


@dataclass(frozen=True)
class ResponsePresentationMetadata:
    """Optional semantic presentation chosen alongside one dialogue response."""

    emotion: str | None = None
    intensity: float | None = None
    gesture: str | None = None
    pose: str | None = None
    gaze_mode: str | None = None
    reaction: str | None = None
    speech_mode: str | None = None
    vision_mode: str | None = None
    hands_mode: str | None = None
    locomotion_mode: str | None = None
    posture_mode: str | None = None
    awareness_mode: str | None = None

    def to_event_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.emotion is not None:
            data["emotion"] = self.emotion
        if self.intensity is not None:
            data["intensity"] = self.intensity
            data["has_intensity"] = True
        if self.gesture is not None:
            data["gesture"] = self.gesture
        if self.pose is not None:
            data["pose"] = self.pose
        if self.gaze_mode is not None:
            data["gaze_mode"] = self.gaze_mode
        if self.reaction is not None:
            data["reaction"] = self.reaction
        if self.speech_mode is not None:
            data["speech_mode"] = self.speech_mode
        if self.vision_mode is not None:
            data["vision_mode"] = self.vision_mode
        if self.hands_mode is not None:
            data["hands_mode"] = self.hands_mode
        if self.locomotion_mode is not None:
            data["locomotion_mode"] = self.locomotion_mode
        if self.posture_mode is not None:
            data["posture_mode"] = self.posture_mode
        if self.awareness_mode is not None:
            data["awareness_mode"] = self.awareness_mode
        return data


@dataclass(frozen=True)
class ParsedAssistantResponse:
    dialogue: str
    presentation: ResponsePresentationMetadata | None = None
    has_presentation_contract: bool = False
    response_mode: str = "normal_conversation"
    response_mode_supplied: bool = False
    spoken_content: str | None = None
    companion_action: dict[str, Any] | None = None
    capability_compliance: tuple[str, ...] = ()
    contract_status: str = "plain_text"
    failure_category: str | None = None

    @property
    def has_response_contract(self) -> bool:
        return self.contract_status in {"valid", "legacy"}


class StreamingResponseDialogue:
    """Expose only a complete JSON envelope's decoded dialogue string.

    The raw provider stream remains the canonical response. This projection is
    presentation-only and deliberately ignores every field after ``dialogue``.
    Leading and trailing JSON-string whitespace is held so its output matches
    the final parser's established ``strip()`` behavior.
    """

    _DIALOGUE_START = re.compile(r'"dialogue"\s*:\s*"')

    def __init__(self) -> None:
        self._prefix = ""
        self._in_dialogue = False
        self._finished = False
        self._escape = ""
        self._pending_whitespace = ""
        self._started_text = False

    def feed(self, value: object) -> str:
        if self._finished:
            return ""
        incoming = str(value or "")
        if not self._in_dialogue:
            self._prefix += incoming
            match = self._DIALOGUE_START.search(self._prefix)
            if match is None:
                # The response envelope header is tiny; retain a bounded tail
                # if malformed output never supplies the expected field.
                self._prefix = self._prefix[-256:]
                return ""
            incoming = self._prefix[match.end():]
            self._prefix = ""
            self._in_dialogue = True

        emitted: list[str] = []
        for character in incoming:
            if self._escape:
                self._escape += character
                if self._escape.startswith("\\u") and len(self._escape) < 6:
                    continue
                decoded = self._decode_escape(self._escape)
                self._escape = ""
                self._append_text(decoded, emitted)
                continue
            if character == "\\":
                self._escape = "\\"
                continue
            if character == '"':
                self._pending_whitespace = ""
                self._finished = True
                break
            self._append_text(character, emitted)
        return "".join(emitted)

    def finish(self) -> str:
        self._pending_whitespace = ""
        self._escape = ""
        return ""

    def _append_text(self, value: str, emitted: list[str]) -> None:
        if not value:
            return
        if value.isspace():
            if self._started_text:
                self._pending_whitespace += value
            return
        if self._pending_whitespace:
            emitted.append(self._pending_whitespace)
            self._pending_whitespace = ""
        emitted.append(value)
        self._started_text = True

    @staticmethod
    def _decode_escape(value: str) -> str:
        try:
            return str(json.loads('"' + value + '"'))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""


def response_contract_prompt() -> str:
    """Authoritative same-call contract for every assistant response mode."""
    return """

AUTHORITATIVE RESPONSE FORMAT:
The canonical dialogue is the only required content. Prefer one compact JSON object with "dialogue" first:
{"dialogue":"your canonical natural reply"}
For local-model robustness, plain canonical dialogue with no JSON is also the complete minimal dialogue-only form. Never emit partial JSON or mix JSON with surrounding prose. AIFren deterministically supplies omitted response mode and presentation defaults from authoritative capabilities.
The canonical dialogue is what AIFren displays and persists. Omit every optional field that is irrelevant; do not manufacture nulls, empty lists, or placeholder presentation metadata.
Do not use emoji or pictographic reaction symbols. Express reactions with ordinary Unicode text and action prose.
Keep an ordinary reply concise, normally no more than about 100 words. Physical actions and emotes MUST use
*action spans* and MUST NOT use (parentheses). Avoid parenthetical asides where possible; rewrite them as ordinary
prose instead. Ordinary dialogue is plain text. Parentheses remain spoken prose, and supported *spoken emphasis*
may still be used where its meaning is unambiguous.
When backend policy requests a constrained mode, add only the fields it names, for example:
{"dialogue":"*brief physical reaction* Mmph...","response_mode":"speech_constrained","spoken_content":"Mmph..."}
"spoken_content" is omitted for normal speech so AIFren derives it from dialogue. In constrained modes it is the exact short text actually spoken, or an empty string for a nonverbal response. "response_mode" may be normal_conversation, constrained_reaction, sleep_reaction, waking, speech_constrained, nonverbal_reaction, or action_decision. action_decision is reserved for a backend-requested pre-response decision.
Optional "presentation" metadata is semantic only. Emotion may be neutral, happy, amused, relaxed, sad, angry, surprised; intensity is 0 to 1. Gesture may be greeting, agreement, disagreement, thinking, encouragement, surprise. Closed optional fields are pose (sleeping/awake), gaze_mode (normal/suppressed), reaction (settle/shift/stir/startle/wake), and speech_mode (normal/constrained/unavailable/nonverbal/mumble). Optional "companion_action" is a closed proposal object only when backend policy requests it. Optional "capability_compliance" is diagnostic only. Never put animation filenames, hidden IDs, or instructions in these fields.
""".strip()


def parse_assistant_response(raw_response: Any) -> ParsedAssistantResponse:
    """Extract the one response envelope without letting bad metadata fail a turn."""
    raw = str(raw_response or "")
    candidate = _strip_json_fence(raw)
    try:
        envelope = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        recovered = sanitize_assistant_output_unicode(_fallback_dialogue(raw))
        return ParsedAssistantResponse(
            dialogue=recovered,
            contract_status="malformed" if raw.lstrip().startswith("{") else "plain_text",
            failure_category="json_parse" if raw.lstrip().startswith("{") else None,
        )
    if not isinstance(envelope, dict) or not isinstance(envelope.get("dialogue"), str):
        return ParsedAssistantResponse(
            dialogue=raw, contract_status="invalid", failure_category="missing_dialogue",
        )

    dialogue = sanitize_assistant_output_unicode(envelope["dialogue"].strip())
    allowed_fields = {
        "dialogue", "response_mode", "spoken_content", "presentation",
        "companion_action", "capability_compliance",
    }
    if set(envelope) - allowed_fields:
        return ParsedAssistantResponse(
            dialogue=dialogue,
            has_presentation_contract="presentation" in envelope,
            contract_status="invalid", failure_category="unknown_top_level_field",
        )
    mode_value = envelope.get("response_mode")
    mode_supplied = "response_mode" in envelope
    if not mode_supplied:
        response_mode = "normal_conversation"
    elif not isinstance(mode_value, str) or mode_value.strip().lower() not in RESPONSE_MODES:
        return ParsedAssistantResponse(
            dialogue=dialogue, has_presentation_contract="presentation" in envelope,
            contract_status="invalid", failure_category="response_mode",
        )
    else:
        response_mode = mode_value.strip().lower()

    spoken_value = envelope.get("spoken_content")
    spoken_content: str | None = None
    if spoken_value is not None:
        if not isinstance(spoken_value, str) or any(mark in spoken_value for mark in "\r\n\t"):
            return ParsedAssistantResponse(
                dialogue=dialogue, response_mode=response_mode,
                has_presentation_contract="presentation" in envelope,
                contract_status="invalid", failure_category="spoken_content",
            )
        spoken_content = sanitize_assistant_output_unicode(" ".join(spoken_value.split()))
        if len(spoken_content) > 320:
            return ParsedAssistantResponse(
                dialogue=dialogue, response_mode=response_mode,
                has_presentation_contract="presentation" in envelope,
                contract_status="invalid", failure_category="spoken_content_bound",
            )

    action_value = envelope.get("companion_action")
    companion_action = None
    if action_value is not None:
        if not isinstance(action_value, dict) or len(action_value) > 8:
            return ParsedAssistantResponse(
                dialogue=dialogue, response_mode=response_mode, spoken_content=spoken_content,
                has_presentation_contract="presentation" in envelope,
                contract_status="invalid", failure_category="companion_action_shape",
            )
        # This is only bounded shape admission. The backend validator owns the
        # closed action family, current-subject resolution, capabilities, and writes.
        companion_action = dict(action_value)

    compliance_value = envelope.get("capability_compliance")
    compliance: tuple[str, ...] = ()
    if compliance_value is not None:
        # This optional field is non-authoritative diagnostics. Malformed or
        # provider-specific shapes cannot grant capability, so discard them
        # rather than sacrificing safe canonical dialogue to a placeholder.
        if isinstance(compliance_value, list) and len(compliance_value) <= 18:
            normalized_compliance = []
            for item in compliance_value:
                if not isinstance(item, str):
                    normalized_compliance = []
                    break
                name = item.strip().lower()
                if name in CAPABILITY_COMPLIANCE and name not in normalized_compliance \
                        and len(normalized_compliance) < 6:
                    normalized_compliance.append(name)
            compliance = tuple(normalized_compliance)

    presentation_value = envelope.get("presentation")
    if presentation_value is None:
        return ParsedAssistantResponse(
            dialogue=dialogue, has_presentation_contract="presentation" in envelope,
            response_mode=response_mode, response_mode_supplied=mode_supplied,
            spoken_content=spoken_content,
            companion_action=companion_action, capability_compliance=compliance,
            contract_status="valid",
        )
    if not isinstance(presentation_value, dict):
        return ParsedAssistantResponse(
            dialogue=dialogue, has_presentation_contract=True,
            response_mode=response_mode, spoken_content=spoken_content,
            companion_action=companion_action, capability_compliance=compliance,
            contract_status="invalid", failure_category="presentation_shape",
        )
    return ParsedAssistantResponse(
        dialogue=dialogue,
        presentation=_parse_metadata(presentation_value),
        has_presentation_contract=True,
        response_mode=response_mode,
        response_mode_supplied=mode_supplied,
        spoken_content=spoken_content,
        companion_action=companion_action,
        capability_compliance=compliance,
        contract_status="valid",
    )


def _strip_json_fence(value: str) -> str:
    """Accept a harmless markdown fence without extracting JSON from prose."""
    stripped = value.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return value
    first_newline = stripped.find("\n")
    if first_newline < 0:
        return value
    return stripped[first_newline + 1:-3].strip()


def _fallback_dialogue(raw: str) -> str:
    """Recover a quoted dialogue field from a malformed envelope when safe."""
    if not raw.lstrip().startswith("{"):
        return raw
    match = re.search(r'"dialogue"\s*:\s*("(?:\\.|[^"\\])*")', raw, re.DOTALL)
    if match is None:
        return raw
    try:
        return str(json.loads(match.group(1))).strip()
    except (TypeError, ValueError):
        return raw


def _parse_metadata(value: dict[str, Any]) -> ResponsePresentationMetadata | None:
    emotion = _known_name(value.get("emotion"), EMOTIONS)
    gesture = _known_name(value.get("gesture"), GESTURES)
    pose = _known_name(value.get("pose"), POSES)
    gaze_mode = _known_name(value.get("gaze_mode"), GAZE_MODES)
    reaction = _known_name(value.get("reaction"), REACTIONS)
    speech_mode = _known_name(value.get("speech_mode"), SPEECH_MODES)
    intensity = _normalized_intensity(value.get("intensity")) if emotion is not None else None
    metadata = ResponsePresentationMetadata(
        emotion=emotion, intensity=intensity, gesture=gesture, pose=pose,
        gaze_mode=gaze_mode, reaction=reaction, speech_mode=speech_mode,
    )
    return metadata if any((metadata.emotion, metadata.gesture, metadata.pose,
                            metadata.gaze_mode, metadata.reaction, metadata.speech_mode)) else None


def _known_name(value: Any, allowed: frozenset[str]) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip().lower() in allowed else None


def _normalized_intensity(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))
