"""Narrow structural handling for assertions embedded in question-shaped turns."""

from __future__ import annotations

from dataclasses import dataclass
import re


_EMBEDDED_SELF_ASSERTION = re.compile(
    r"^(?:did|do)\s+you\s+know(?:\s+that)?\s+"
    r"(?P<assertion>i\s+.+?)\s*\?$",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:might|may|maybe|perhaps|possibly|probably|i\s+(?:think|guess|wonder))\b",
    re.IGNORECASE,
)
_HISTORICAL_ATTRIBUTION = re.compile(
    r"^(?:did|have)\s+i\s+(?:ever\s+)?(?:tell|told|say|said|mention|mentioned)\b",
    re.IGNORECASE,
)
_OPINION = re.compile(r"^do\s+you\s+think\b", re.IGNORECASE)


@dataclass(frozen=True)
class EmbeddedSelfAssertion:
    text: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    modality: str

    @property
    def authoritative(self) -> bool:
        return self.modality == "asserted"


def extract_embedded_self_assertion(value: object) -> EmbeddedSelfAssertion | None:
    """Extract only ``Did you know that I …?`` first-person complements.

    The wrapper is conversational; the complement retains its own modality.
    Historical-attribution and assistant-opinion questions deliberately do not
    match this grammar.
    """
    if not isinstance(value, str) or value != value.strip() or not value:
        return None
    if _HISTORICAL_ATTRIBUTION.match(value) or _OPINION.match(value):
        return None
    match = _EMBEDDED_SELF_ASSERTION.fullmatch(value)
    if match is None:
        return None
    # Keep the original complement bytes/character positions intact.  The
    # durable-fact curator owns its own whitespace normalization, while the
    # observation evidence span must continue to point into the canonical
    # user message even when natural speech contains repeated whitespace.
    assertion = match.group("assertion").rstrip()
    if not assertion or len(assertion) > 240:
        return None
    start = match.start("assertion")
    return EmbeddedSelfAssertion(
        assertion,
        start,
        start + len(assertion),
        "uncertain" if _UNCERTAIN.search(assertion) else "asserted",
    )


__all__ = ["EmbeddedSelfAssertion", "extract_embedded_self_assertion"]
