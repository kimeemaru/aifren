"""Conservative deterministic extraction for one durable identity slot only."""

from __future__ import annotations

from dataclasses import dataclass
import re


_NAME = r"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,39})"
_ASSERTIONS = (
    ("my_name_is", re.compile(rf"\AMy name is {_NAME}[.!]?\Z")),
    ("call_me", re.compile(rf"\AYou can call me {_NAME}[.!]?\Z")),
)


@dataclass(frozen=True)
class IdentityNameAssertion:
    value: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    form: str


def extract_identity_name_assertion(text: object) -> IdentityNameAssertion | None:
    """Recognize only two anchored, direct self-name assertion forms.

    Deliberately unsupported: contractions (``I'm …``), quoted text,
    questions, hypotheticals, roleplay, third-person facts, multi-token names,
    and any sentence with additional material.
    """
    if not isinstance(text, str):
        return None
    for form, pattern in _ASSERTIONS:
        match = pattern.fullmatch(text)
        if match is not None:
            return IdentityNameAssertion(
                value=match.group("name"), excerpt_start_cp=match.start("name"),
                excerpt_end_cp=match.end("name"), form=form,
            )
    return None
