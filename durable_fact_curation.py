"""Closed-schema deterministic durable-fact proposal curation.

The conversational model is never the database authority.  This lightweight
curator deliberately accepts only clear present-tense user biography and
preference forms, leaving ambiguous language to Memory V1.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_TRAILING = re.compile(r"[\s.!?]+$")
_VALUE = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ][A-Za-z0-9À-ÖØ-öø-ÿ'’+#.& /-]{0,94}")
_UNSAFE_PREFIX = re.compile(
    r"(?:what\s+if|if\s+|imagine\s+|suppose\s+|hypothetically\b|for\s+example\b)",
    re.IGNORECASE,
)
_PAST = re.compile(
    r"\b(?:used\s+to|when\s+i\s+was|when\s+i\s+were|years?\s+ago|as\s+a\s+child|"
    r"formerly|previously|i\s+liked|i\s+lived|i\s+worked)\b",
    re.IGNORECASE,
)
_TEMPORARY = re.compile(
    r"\b(?:for\s+now|temporarily|this\s+(?:week|month|semester)|at\s+the\s+moment)\b",
    re.IGNORECASE,
)
_INSTRUCTION = re.compile(
    r"\b(?:ignore|override|follow)\b.{0,24}\b(?:instruction|prompt|system|developer)\b",
    re.IGNORECASE,
)
_INSTRUCTION_VALUE_TOKENS = frozenset({
    "ignore", "instruction", "instructions", "prompt", "system", "developer",
    "assistant", "tool", "function",
})
_COLOR_WORDS = frozenset({
    "black", "blue", "brown", "cream", "cyan", "gold", "gray", "green", "grey",
    "orange", "pink", "purple", "red", "silver", "tan", "teal", "violet", "white", "yellow",
})
_COLOR_MODIFIERS = frozenset({"bright", "dark", "deep", "forest", "light", "navy", "pale"})


@dataclass(frozen=True)
class DurableFactProposal:
    subject_key: str
    value: str
    fact_kind: str
    excerpt_start_cp: int
    excerpt_end_cp: int
    stance: str = "current"

    @property
    def content(self) -> str:
        templates = {
            "home": "The user lives in {value}.",
            "occupation": "The user's occupation is {value}.",
            "school": "The user studies at {value}.",
            "gpu": "The user's GPU is {value}.",
            "computer": "The user's computer is {value}.",
            "pet": "The user's pet is {value}.",
            "project": "The user's long-running project is {value}.",
            "likes": "The user likes {value}.",
            "dislikes": "The user dislikes {value}.",
            "interest": "The user has a recurring interest in {value}.",
            "possession": "The user owns {value}.",
            "favorite_beverage": "The user's favorite beverage is {value}.",
            "favorite_color": "The user's favorite color is {value}.",
            "favorite_food": "The user's favorite food is {value}.",
            "favorite_game": "The user's favorite game is {value}.",
            "favorite_media": "The user's favorite media is {value}.",
        }
        return templates[self.fact_kind].format(value=self.value)


def topic_subject_key(namespace: str, value: object) -> str:
    normalized = " ".join(str(value or "").casefold().replace("’", "'").split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{namespace}.topic.{digest}"


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 300:
        return None
    if any(character in value for character in "\r\n\t"):
        return None
    if (_UNSAFE_PREFIX.match(value) or _PAST.search(value) or _TEMPORARY.search(value)
            or _INSTRUCTION.search(value)):
        return None
    # Embedded/wrapped quotations are not first-person durable evidence.
    if any(mark in value for mark in ('"', '“', '”', '`')):
        return None
    return value


def _compact(value: str, maximum: int = 96) -> str | None:
    result = _TRAILING.sub("", " ".join(value.split())).strip(" '’")
    if not result or len(result) > maximum or _VALUE.fullmatch(result) is None or _INSTRUCTION.search(result):
        return None
    words = {word.casefold().strip("'’") for word in result.split()}
    if words & _INSTRUCTION_VALUE_TOKENS:
        return None
    return result


def _proposal(text: str, subject_key: str, value: str, kind: str, *, stance: str = "current") -> DurableFactProposal | None:
    compact = _compact(value)
    if compact is None:
        return None
    start = text.casefold().rfind(compact.casefold())
    if start < 0:
        start, end = 0, len(text)
    else:
        end = start + len(compact)
    return DurableFactProposal(subject_key, compact, kind, start, end, stance)


def _favorite_color_value(value: object) -> str | None:
    compact = _compact(str(value or ""), maximum=32)
    if compact is None:
        return None
    words = compact.casefold().split()
    if not 1 <= len(words) <= 2 or words[-1] not in _COLOR_WORDS:
        return None
    if len(words) == 2 and words[0] not in _COLOR_MODIFIERS:
        return None
    return compact


def extract_v1_favorite_color_memory(content: object) -> str | None:
    """Read only the exact legacy favorite-color fact shape used by the bridge."""
    if not isinstance(content, str):
        return None
    match = re.fullmatch(
        r"The user(?:'s|’s) (?:absolute )?favorite color is (?P<value>[^.]{1,32})\.",
        content.strip(), re.IGNORECASE,
    )
    return _favorite_color_value(match.group("value")) if match is not None else None


def extract_durable_fact_proposal(
    content: object,
    *,
    previous_user_content: str | None = None,
) -> DurableFactProposal | None:
    """Propose at most one clear current durable fact from one user turn."""
    text = _safe_text(content)
    if text is None:
        return None
    normalized = _TRAILING.sub("", text)
    normalized = re.sub(r"^actually[, ]+", "", normalized, flags=re.IGNORECASE)

    patterns = (
        (r"i\s+live\s+in\s+(?P<value>.+)", "home.primary", "home"),
        (r"i\s+moved\s+to\s+(?P<value>.+)", "home.primary", "home"),
        (r"my\s+home\s+is\s+in\s+(?P<value>.+)", "home.primary", "home"),
        (r"i\s+work\s+as\s+(?:an?\s+)?(?P<value>.+)", "bio.occupation", "occupation"),
        (r"my\s+(?:job|occupation)\s+is\s+(?P<value>.+)", "bio.occupation", "occupation"),
        (r"i\s+(?:study|go\s+to\s+school)\s+at\s+(?P<value>.+)", "bio.school", "school"),
        (r"my\s+(?:school|university|college)\s+is\s+(?P<value>.+)", "bio.school", "school"),
        (r"my\s+(?:gpu|graphics\s+card)\s+is\s+(?:an?\s+)?(?P<value>.+)", "device.gpu", "gpu"),
        (r"i\s+(?:have|own|use)\s+(?:an?\s+)?(?P<value>(?:rtx|gtx|radeon|geforce)\s+.+)", "device.gpu", "gpu"),
        (r"my\s+(?:computer|pc|laptop)\s+is\s+(?P<value>.+)", "device.computer", "computer"),
        (r"my\s+(?:long[- ]running\s+)?project\s+is\s+(?P<value>.+)", "project.primary", "project"),
        (r"i(?:'m|\s+am)\s+working\s+on\s+(?:my\s+|a\s+)?long[- ]running\s+project(?:\s+called)?\s+(?P<value>.+)", "project.primary", "project"),
    )
    for pattern, subject_key, kind in patterns:
        match = re.fullmatch(pattern, normalized, re.IGNORECASE)
        if match is not None:
            return _proposal(text, subject_key, match.group("value"), kind)

    pet = re.fullmatch(
        r"my\s+(?P<kind>pet|dog|cat)\s+is\s+named\s+(?P<name>.+)|"
        r"i\s+have\s+(?:an?\s+)?(?P<kind2>dog|cat)\s+named\s+(?P<name2>.+)",
        normalized, re.IGNORECASE,
    )
    if pet is not None:
        kind = (pet.group("kind") or pet.group("kind2")).casefold()
        name = pet.group("name") or pet.group("name2")
        return _proposal(text, "pet.primary", f"{kind} named {name}", "pet")

    replacement = re.fullmatch(r"i\s+replaced\s+(?:it|that)\s+with\s+(?P<value>.+)", normalized, re.IGNORECASE)
    if replacement is not None and previous_user_content and re.search(
        r"\b(?:gpu|graphics\s+card|rtx|gtx|radeon|geforce)\b", previous_user_content, re.IGNORECASE,
    ):
        return _proposal(text, "device.gpu", replacement.group("value"), "gpu")

    favorite = re.fullmatch(
        r"my\s+favorite\s+(?P<domain>beverage|drink|color|food|game|movie|show|media)\s+is\s+(?P<value>.+)",
        normalized, re.IGNORECASE,
    )
    if favorite is not None:
        domain = favorite.group("domain").casefold()
        domain = {"drink": "beverage", "movie": "media", "show": "media"}.get(domain, domain)
        value = favorite.group("value")
        if domain == "color":
            value = _favorite_color_value(value)
            if value is None:
                return None
        return _proposal(text, f"preference.{domain}", value, f"favorite_{domain}")

    color_correction = re.fullmatch(
        r"my\s+favorite\s+is\s+(?P<value>.+?)\s+actually",
        normalized, re.IGNORECASE,
    )
    if color_correction is not None:
        value = _favorite_color_value(color_correction.group("value"))
        if value is not None:
            return _proposal(
                text, "preference.color", value, "favorite_color", stance="correction",
            )

    preference = re.fullmatch(
        r"i\s+(?P<stance>like|love|enjoy|dislike|hate)\s+(?P<value>.+)|"
        r"i\s+do(?:n't|\s+not)\s+(?:really\s+)?like\s+(?P<negative>.+?)(?:\s+anymore)?",
        normalized, re.IGNORECASE,
    )
    if preference is not None:
        value = preference.group("value") or preference.group("negative")
        compact = _compact(value)
        if compact is None:
            return None
        stance = (preference.group("stance") or "dislike").casefold()
        kind = "dislikes" if stance in {"dislike", "hate"} or preference.group("negative") else "likes"
        return _proposal(text, topic_subject_key("preference", compact), compact, kind)

    interest = re.fullmatch(
        r"(?:one\s+of\s+)?my\s+hobb(?:y\s+is|ies\s+include)\s+(?P<value>.+)|"
        r"i(?:'m|\s+am)\s+interested\s+in\s+(?P<value2>.+)|"
        r"i\s+have\s+a\s+long[- ]term\s+interest\s+in\s+(?P<value3>.+)",
        normalized, re.IGNORECASE,
    )
    if interest is not None:
        value = interest.group("value") or interest.group("value2") or interest.group("value3")
        compact = _compact(value)
        if compact:
            return _proposal(text, topic_subject_key("interest", compact), compact, "interest")

    owned = re.fullmatch(r"i\s+own\s+(?P<value>.+)", normalized, re.IGNORECASE)
    if owned is not None:
        compact = _compact(owned.group("value"))
        if compact:
            return _proposal(text, topic_subject_key("possession", compact), compact, "possession")
    return None


_CONTENT_PATTERNS = (
    (re.compile(r"The user lives in (?P<value>.+)\."), "home"),
    (re.compile(r"The user's occupation is (?P<value>.+)\."), "occupation"),
    (re.compile(r"The user studies at (?P<value>.+)\."), "school"),
    (re.compile(r"The user's GPU is (?P<value>.+)\."), "gpu"),
    (re.compile(r"The user's computer is (?P<value>.+)\."), "computer"),
    (re.compile(r"The user's pet is (?P<value>.+)\."), "pet"),
    (re.compile(r"The user's long-running project is (?P<value>.+)\."), "project"),
    (re.compile(r"The user likes (?P<value>.+)\."), "likes"),
    (re.compile(r"The user dislikes (?P<value>.+)\."), "dislikes"),
    (re.compile(r"The user has a recurring interest in (?P<value>.+)\."), "interest"),
    (re.compile(r"The user owns (?P<value>.+)\."), "possession"),
    (re.compile(r"The user's favorite (?P<domain>beverage|color|food|game|media) is (?P<value>.+)\."), "favorite"),
)


def parse_governed_durable_content(content: object) -> tuple[str, str] | None:
    if not isinstance(content, str):
        return None
    for pattern, kind in _CONTENT_PATTERNS:
        match = pattern.fullmatch(content)
        if match is not None and _compact(match.group("value")) == match.group("value"):
            return kind, match.group("value")
    return None
