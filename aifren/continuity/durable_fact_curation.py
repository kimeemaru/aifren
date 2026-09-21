"""Closed-schema deterministic durable-fact proposal curation.

The conversational model is never the database authority.  This lightweight
curator deliberately accepts only clear present-tense user biography and
preference forms. Ambiguous language is not promoted to durable authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re


DURABLE_FACT_CURATOR_NAME = "bounded_durable_fact_curator"
DURABLE_FACT_CURATOR_VERSION = "6"
DURABLE_FACT_POLICY_VERSION = "closed_schema_v5"


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
    r"\b(?:for\s+now|temporarily|today|tonight|right\s+now|"
    r"for\s+(?:a|one)\s+(?:day|week|month|semester)|"
    r"this\s+(?:week|month|semester)|at\s+the\s+moment)\b",
    re.IGNORECASE,
)
_NON_ASSERTIVE = re.compile(
    r"\b(?:maybe|perhaps|probably|possibly|presumably|apparently|supposedly|allegedly|reported(?:ly)?|"
    r"rumou?red|"
    r"i\s+(?:think|guess|wonder)|"
    r"may|might|could|would)\b|"
    r"\b(?:is\s+)?(?:said|expected|scheduled)\s+to\s+be\b|"
    r"\b(?:if\s+i|if\s+we|if\s+they|if\s+it|hypothetically|for\s+example)\b|"
    r"\b(?:i\s+(?:plan|intend|hope)\s+to|i(?:'m|\s+am)\s+going\s+to|"
    r"i\s+will|next\s+(?:week|month|year)|tomorrow)\b|"
    r"\b(?:in|for)\s+(?:(?:our|the)\s+(?:roleplay|role-play|rp|scenario|game)|"
    r"a\s+(?:roleplay|role-play|scenario|game|fictional\s+story|hypothetical\s+story)|"
    r"roleplay|role-play|rp|gta)\b",
    re.IGNORECASE,
)
_RELATIVE_VALUE_TIME = re.compile(
    r"\b(?:yesterday|last\s+(?:week|month|year)|\d+\s+(?:days?|weeks?|months?|years?)\s+ago)\b",
    re.IGNORECASE,
)
_NEGATED_POSSESSION = re.compile(
    r"^\s*i\s+own\s+(?:no\b|nothing\b|neither\b|not\b)", re.IGNORECASE,
)
_INSTRUCTION = re.compile(
    r"\b(?:ignore|override|follow)\b.{0,24}\b(?:instruction|prompt|system|developer)\b",
    re.IGNORECASE,
)
_INSTRUCTION_VALUE_TOKENS = frozenset({
    "ignore", "instruction", "instructions", "prompt", "system", "developer",
    "assistant", "tool", "function",
})
_UNSAFE_VALUE = re.compile(
    r"^(?:not|no|none|nothing|neither|never)\b|"
    r"^(?:going\s+to|will)\s+be\b|"
    r"\b(?:hypothetical|fictional|imaginary|roleplay|role-play|scenario|rp)\b|"
    r"\bd\s*&\s*d\b|"
    r"\b(?:starting|beginning|effective|from)\s+(?:in\s+)?(?:\d{4}|next\b|tomorrow\b)",
    re.IGNORECASE,
)
_COLOR_WORDS = frozenset({
    "black", "blue", "brown", "cream", "cyan", "gold", "gray", "green", "grey",
    "orange", "pink", "purple", "red", "silver", "tan", "teal", "violet", "white", "yellow",
})
_COLOR_MODIFIERS = frozenset({"bright", "dark", "deep", "forest", "light", "navy", "pale"})
_NAMED_TOKEN = re.compile(r"(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+|[A-Z]{2,}|[A-Z]+\d+)")
_TOPIC_CLAUSE_PREFIX = re.compile(
    r"(?:to\b|you\b|your\b|yours\b|we\b|our\b|ours\b|they\b|their\b|"
    r"it\b|him\b|her\b|me\b|us\b|this\b|that\b|these\b|those\b|"
    r"the\s+one\b|something\b|anything\b|what\b|who\b|why\b|how\b|"
    r"whether\b|if\b|as\s+if\b)",
    re.IGNORECASE,
)
_UNRESOLVED_TOPIC_REFERENCE = re.compile(
    r"\b(?:whatever|whichever|wherever|whoever)\b|"
    r"\b(?:you|your|yours|we|our|ours|they|their|theirs|him|her|them)\b|"
    r"\b(?:one\s+of\s+(?:these|those|them))\b|"
    r"^(?:it|this|that|these|those|something|anything)\b",
    re.IGNORECASE,
)
_FREEFORM_AUTHORITY_QUALIFIER = re.compile(
    r"\b(?:if|unless|while|when|whenever|until)\b|"
    r"\b(?:roleplaying|role-playing|in[- ]character)\b|"
    r"\b(?:starting|beginning|effective|next|from)\b|"
    # A trailing locus cannot be distinguished from scenario qualification by
    # this non-semantic curator (for example, "pizza in Minecraft"). Prefer a
    # false negative over assigning that value to the real-world singleton.
    r"\b(?:in|inside|during|within)\s+(?:the\s+|an?\s+)?"
    r"[A-Za-z0-9À-ÖØ-öø-ÿ'’.-]+(?:\s+[A-Za-z0-9À-ÖØ-öø-ÿ'’.-]+){0,3}$",
    re.IGNORECASE,
)
_PLACE_NON_ENTITY_PREFIX = re.compile(
    r"(?:using|feeling|being|getting|working|walking|running|living|staying)\b",
    re.IGNORECASE,
)
_GPU_MODEL = re.compile(
    r"(?:an?\s+)?(?:"
    r"(?:nvidia\s+)?(?:geforce\s+)?(?:rtx|gtx)\s*\d{3,4}(?:\s*(?:ti|super|mobile))?|"
    r"(?:amd\s+)?(?:(?:radeon\s+)(?:rx\s*)?|rx\s*)\d{3,4}(?:\s*(?:xt|xtx|m))?|"
    r"(?:intel\s+)?arc\s+[a-z]?\d{3,4}"
    r")",
    re.IGNORECASE,
)
_PLACE_CONNECTORS = frozenset({"the", "of", "de", "del", "la", "le", "upon"})
_SCHOOL_IDENTITY = re.compile(
    r"(?:[A-Z]{2,10}|"
    r"(?:University|College|School|Academy|Institute|Polytechnic)\s+of\s+"
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){0,3}|"
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){0,4}\s+"
    r"(?:University|College|School|Academy|Institute|Polytechnic))"
)
_COMPUTER_MODEL = re.compile(
    r"(?:an?\s+)?(?:"
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'’.-]+){0,3}\s+"
    r"(?:laptop|desktop|workstation|PC|computer)|"
    r"(?:Apple\s+)?MacBook(?:\s+(?:Air|Pro))?(?:\s+M[1-9](?:\s+(?:Pro|Max|Ultra))?)?|"
    r"(?:Lenovo\s+)?ThinkPad(?:\s+(?:X1(?:\s+Carbon)?|[A-Z]\d{1,3}[A-Za-z0-9-]*))?"
    r")"
)


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
    # A question mark is authority-bearing syntax, not punctuation that may be
    # discarded before an assertion grammar runs. Keeping this gate ahead of
    # ``_TRAILING`` also catches mixed endings such as ``I own a bike?!``.
    if "?" in value:
        return None
    if (_NEGATED_POSSESSION.search(value)):
        return None
    if (_UNSAFE_PREFIX.match(value) or _PAST.search(value) or _TEMPORARY.search(value)
            or _NON_ASSERTIVE.search(value) or _RELATIVE_VALUE_TIME.search(value)
            or _INSTRUCTION.search(value)):
        return None
    # Embedded/wrapped quotations are not first-person durable evidence.
    if any(mark in value for mark in ('"', '“', '”', '`')):
        return None
    return value


def _compact(value: str, maximum: int = 96) -> str | None:
    result = _TRAILING.sub("", " ".join(value.split())).strip(" '’")
    if (not result or len(result) > maximum or _VALUE.fullmatch(result) is None
            or _INSTRUCTION.search(result) or _UNSAFE_VALUE.search(result)):
        return None
    words = {word.casefold().strip("'’") for word in result.split()}
    if words & _INSTRUCTION_VALUE_TOKENS:
        return None
    return result


def _validated_kind_value(value: str, kind: str) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    words = compact.split()
    if kind == "home":
        if len(words) > 6 or _PLACE_NON_ENTITY_PREFIX.match(compact):
            return None
        for word in words:
            token = word.strip(".'’-")
            if token.casefold() in _PLACE_CONNECTORS:
                continue
            if not token or _NAMED_TOKEN.fullmatch(word) is None:
                return None
    elif kind == "occupation":
        # Free-form role phrases are too ambiguous for durable authority.
        # Preserve older stored claims, but do not create new ones until a
        # governed occupation taxonomy/admission contract exists.
        return None
    elif kind == "school":
        if len(words) > 10 or _SCHOOL_IDENTITY.fullmatch(compact) is None:
            return None
    elif kind == "gpu":
        if _GPU_MODEL.fullmatch(compact) is None:
            return None
    elif kind == "computer":
        if len(words) > 8 or _COMPUTER_MODEL.fullmatch(compact) is None:
            return None
    elif kind == "pet":
        # ``pet.primary`` cannot represent several concurrently owned pets;
        # abstain until pet identity/multiplicity has a governed lifecycle.
        return None
    elif kind in {"project", "likes", "dislikes", "interest"} or kind.startswith("favorite_"):
        if (len(words) > 12 or _TOPIC_CLAUSE_PREFIX.match(compact)
                or _UNRESOLVED_TOPIC_REFERENCE.search(compact)
                or _FREEFORM_AUTHORITY_QUALIFIER.search(compact)):
            return None
        if kind == "project" and _NAMED_TOKEN.search(compact) is None:
            return None
    return compact


def _proposal(text: str, subject_key: str, value: str, kind: str, *, stance: str = "current") -> DurableFactProposal | None:
    compact = _validated_kind_value(value, kind)
    if compact is None:
        return None
    start = text.casefold().rfind(compact.casefold())
    if start < 0:
        start, end = 0, len(text)
    else:
        end = start + len(compact)
    return DurableFactProposal(subject_key, compact, kind, start, end, stance)


def _legacy_favorite_color_value(value: object) -> str | None:
    compact = _compact(str(value or ""), maximum=32)
    if compact is None:
        return None
    words = compact.casefold().split()
    if not 1 <= len(words) <= 2 or words[-1] not in _COLOR_WORDS:
        return None
    if len(words) == 2 and words[0] not in _COLOR_MODIFIERS:
        return None
    return compact


def _favorite_color_value(value: str) -> str | None:
    """Preserve an explicit preference label, not a physical color inference.

    The full first-person assertion supplies the relation. Its value needs the
    same bounded, non-referential admission as other favorite labels, not an
    exhaustive color vocabulary. Keep the legacy imported-text bridge separate.
    """
    if (_safe_text(value) is None or len(value) > 64
            or not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*){0,5}", value)
            or set(value.casefold().replace("-", " ").split()) & {
                "and", "or", "but", "because", "is", "was", "non",
            }):
        return None
    return _validated_kind_value(value, "favorite_color")


def _retirement_proposal(content: object) -> DurableFactProposal | None:
    """Recognize narrow explicit endings of already-governed preferences.

    A retirement is not a negative fact and does not create a replacement
    claim. The observer applies it only when one open claim has the exact same
    governed subject and value.
    """
    if (not isinstance(content, str) or content != content.strip() or not content
            or len(content) > 300 or any(mark in content for mark in "\r\n\t?\"“”`")):
        return None
    normalized = _TRAILING.sub("", content)
    topic_patterns = (
        r"i\s+used\s+to\s+(?:like|love|enjoy)\s+(?P<value>.+)",
        r"i\s+no\s+longer\s+(?:like|love|enjoy)\s+(?P<value>.+)",
        r"i\s+do(?:n't|\s+not)\s+(?:really\s+)?like\s+(?P<value>.+)\s+anymore",
    )
    for pattern in topic_patterns:
        match = re.fullmatch(pattern, normalized, re.IGNORECASE)
        if match is not None:
            compact = _validated_kind_value(match.group("value"), "likes")
            if compact is not None:
                return _proposal(
                    content, topic_subject_key("preference", compact), compact,
                    "likes", stance="retirement",
                )

    favorite = re.fullmatch(
        r"my\s+favorite\s+(?P<domain>beverage|drink|color|food|game|movie|show|media)\s+"
        r"is\s+(?:not|no\s+longer)\s+(?P<value>.+)",
        normalized, re.IGNORECASE,
    )
    if favorite is None:
        favorite = re.fullmatch(
            r"(?P<value>.+)\s+is\s+no\s+longer\s+my\s+favorite\s+"
            r"(?P<domain>beverage|drink|color|food|game|movie|show|media)",
            normalized, re.IGNORECASE,
        )
    if favorite is not None:
        domain = favorite.group("domain").casefold()
        domain = {"drink": "beverage", "movie": "media", "show": "media"}.get(
            domain, domain,
        )
        value: str | None = favorite.group("value")
        if domain == "color":
            value = _favorite_color_value(value)
        if value is not None:
            return _proposal(
                content, f"preference.{domain}", value, f"favorite_{domain}",
                stance="retirement",
            )
    return None


def extract_v1_favorite_color_memory(content: object) -> str | None:
    """Read only the exact legacy favorite-color fact shape used by the bridge."""
    if not isinstance(content, str):
        return None
    match = re.fullmatch(
        r"The user(?:'s|’s) (?:absolute )?favorite color is (?P<value>[^.]{1,32})\.",
        content.strip(), re.IGNORECASE,
    )
    return _legacy_favorite_color_value(match.group("value")) if match is not None else None


def extract_durable_fact_proposal(
    content: object,
    *,
    previous_user_content: str | None = None,
) -> DurableFactProposal | None:
    """Propose at most one clear current durable fact from one user turn."""
    from aifren.state.user_assertion_semantics import extract_embedded_self_assertion

    embedded = extract_embedded_self_assertion(content)
    if embedded is not None:
        if not embedded.authoritative:
            return None
        proposal = extract_durable_fact_proposal(
            embedded.text,
            previous_user_content=previous_user_content,
        )
        if proposal is None:
            return None
        return replace(
            proposal,
            excerpt_start_cp=embedded.excerpt_start_cp + proposal.excerpt_start_cp,
            excerpt_end_cp=embedded.excerpt_start_cp + proposal.excerpt_end_cp,
        )
    retirement = _retirement_proposal(content)
    if retirement is not None:
        return retirement
    text = _safe_text(content)
    if text is None:
        return None
    normalized = _TRAILING.sub("", text)
    explicit_correction = re.match(r"^actually[, ]+", normalized, re.IGNORECASE) is not None
    normalized = re.sub(r"^actually[, ]+", "", normalized, flags=re.IGNORECASE)

    patterns = (
        (r"i\s+live\s+in\s+(?P<value>.+)", "home.primary", "home"),
        (r"my\s+home\s+is\s+in\s+(?P<value>.+)", "home.primary", "home"),
        (r"my\s+(?:school|university|college)\s+is\s+(?P<value>.+)", "bio.school", "school"),
        (r"my\s+(?:gpu|graphics\s+card)\s+is\s+(?:an?\s+)?(?P<value>.+)", "device.gpu", "gpu"),
        (r"i\s+(?:have|own|use)\s+(?:an?\s+)?(?P<value>(?:rtx|gtx|radeon|geforce)\s+.+)", "device.gpu", "gpu"),
        (r"my\s+(?:computer|pc|laptop)\s+is\s+(?P<value>.+)", "device.computer", "computer"),
        (r"my\s+(?:(?:primary\s+)?long[- ]running|primary)\s+project\s+is\s+(?P<value>.+)", "project.primary", "project"),
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
    if replacement is not None and previous_user_content:
        previous_proposal = extract_durable_fact_proposal(previous_user_content)
        if (previous_proposal is not None
                and previous_proposal.subject_key == "device.gpu"
                and previous_proposal.fact_kind == "gpu"):
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
        return _proposal(
            text, f"preference.{domain}", value, f"favorite_{domain}",
            stance="correction" if explicit_correction and domain == "color" else "current",
        )

    color_correction = re.fullmatch(
        r"my\s+favorite\s+is\s+(?P<value>.+?)\s+actually",
        normalized, re.IGNORECASE,
    )
    if color_correction is not None:
        previous = (
            extract_durable_fact_proposal(previous_user_content)
            if previous_user_content else None
        )
        if (previous is None or previous.subject_key != "preference.color"
                or previous.fact_kind != "favorite_color"):
            return None
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
