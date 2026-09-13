"""Conservative deterministic extraction for one active headwear slot only."""

from __future__ import annotations

from dataclasses import dataclass
import re


_ITEM = r"(?P<value>[a-z]+(?:[ -][a-z]+){0,2}\s+(?:hat|cap))"
_SET_FORMS = (
    ("present_wearing", re.compile(rf"\A(?:she is|she's) wearing (?:a |an )?{_ITEM}[.!]?\Z", re.IGNORECASE)),
    ("imperative_put_on", re.compile(rf"\Aput (?:the )?{_ITEM} on her[.!]?\Z", re.IGNORECASE)),
)
_CLEAR_FORMS = (
    ("present_not_wearing", re.compile(r"\Ashe isn't wearing (?:a |any )?(?:hat|cap) anymore[.!]?\Z", re.IGNORECASE)),
    ("imperative_take_off", re.compile(r"\Atake (?:the )?(?:hat|cap) off[.!]?\Z", re.IGNORECASE)),
    ("past_take_off", re.compile(r"\Ashe took (?:the )?(?:hat|cap) off[.!]?\Z", re.IGNORECASE)),
)


@dataclass(frozen=True)
class HeadwearStateAssertion:
    operation: str
    value: str | None
    excerpt_start_cp: int | None
    excerpt_end_cp: int | None
    form: str


def extract_headwear_state_assertion(text: object) -> HeadwearStateAssertion | None:
    """Recognize only anchored, explicit headwear set/clear statements.

    This deliberately does not recognize past habits, future possibility,
    hypotheticals, questions, quoted text, stories, ownership, or arbitrary
    clothing/actions.
    """
    if not isinstance(text, str):
        return None
    for form, pattern in _SET_FORMS:
        match = pattern.fullmatch(text)
        if match is not None:
            value = " ".join(match.group("value").lower().split())
            return HeadwearStateAssertion(
                "set", value, match.start("value"), match.end("value"), form,
            )
    for form, pattern in _CLEAR_FORMS:
        if pattern.fullmatch(text) is not None:
            return HeadwearStateAssertion("clear", None, None, None, form)
    return None
