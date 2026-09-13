"""Typed relation sufficiency for Memory V2 prompt authority.

Retrieval relevance and answer authority are deliberately different.  A row
may be topically related to a question without supplying the value or relation
that the question asks for.  This module is the small deterministic boundary
between those two states; it does not rank candidates or infer new facts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from aifren.continuity.memory_query_decision import MemoryQueryDecision, MemoryQuerySlot


_HISTORICAL_PREFIX = re.compile(
    r"^Historical\s+(?:user|assistant)\s+"
    r"(?:record|statement|question|interaction):\s*",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:might|may|maybe|perhaps|possibly|probably|i\s+(?:think|guess))\b",
    re.IGNORECASE,
)
_PREFERENCE_NONASSERTIVE = re.compile(
    r"\b(?:if|unless|while|when|imagine|suppose|hypothetically|assuming|"
    r"presumably|apparently|supposedly|allegedly|reportedly|rumou?red|"
    r"would|could|will|tomorrow|not|never|no|isn't|wasn't|don't|didn't)\b|\bfor\s+example\b|"
    r"\b(?:never|didn't|did\s+not)\s+(?:say|said|claim)\b", re.I,
)
_PROGRAMMING_LANGUAGES = (
    "assembly", "bash", "c#", "c++", "clojure", "cobol", "dart", "elixir",
    "erlang", "fortran", "go", "haskell", "java", "javascript", "julia",
    "kotlin", "lua", "matlab", "objective-c", "perl", "php", "python", "r",
    "ruby", "rust", "scala", "shell", "sql", "swift", "typescript", "visual basic",
)
_AMBIGUOUS_LANGUAGE_CASING = {"go": "Go", "r": "R"}
_AMBIGUOUS_LANGUAGE_SYNTAX = {
    "go": re.compile(
        r"\b(?:(?:using|used|written|coded|programmed|implemented|built)\s+"
        r"(?:(?:in|with)\s+)?go|(?:language|codebase|module|app|application)\s+"
        r"(?:is|was|uses|used)\s+go|go\s+(?:programming\s+language|code|"
        r"codebase|module))\b",
        re.IGNORECASE,
    ),
    "r": re.compile(
        r"\b(?:(?:using|used|written|coded|programmed|implemented|built)\s+"
        r"(?:(?:in|with)\s+)?r|(?:language|codebase|module|app|application)\s+"
        r"(?:is|was|uses|used)\s+r|r\s+(?:programming\s+language|code|"
        r"codebase|module))\b",
        re.IGNORECASE,
    ),
}
_PROGRAMMING_CONTEXT = re.compile(
    r"\b(?:code|coded|coding|language|programming|script|scripting|software|"
    r"compiler|interpreter|project|module|parser|application|app)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceSufficiency:
    sufficient: bool
    relation: str
    value: str = ""
    reason: str = ""


@dataclass(frozen=True)
class EvidenceAdmission:
    candidates: tuple[object, ...]
    insufficient_count: int
    reason: str
    slots: tuple[EvidenceSlotAdmission, ...] = ()


@dataclass(frozen=True)
class EvidenceSlotAdmission:
    slot: MemoryQuerySlot
    candidates: tuple[object, ...]
    values: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return bool(self.values)


def source_text(value: object) -> str:
    return _HISTORICAL_PREFIX.sub(
        "", " ".join(str(value or "").split()), count=1,
    )


class AmbiguousHistoricalAttribute(ValueError):
    """The bounded source does not identify a unique concrete attribute."""


def _unique_attribute(values: list[str]) -> str:
    distinct = {value.casefold() for value in values if value}
    if len(distinct) > 1 or any(re.search(r"\b(?:and|or)\b", value, re.I) for value in values):
        raise AmbiguousHistoricalAttribute()
    return values[0] if values else ""


def _word_value(patterns: tuple[str, ...], text: str, *, unique: bool = False) -> str:
    values = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = " ".join(match.group("value").strip(" .,!?:;\"'“”").split())[:96]
            if not unique:
                return value
            values.append(value)
    return _unique_attribute(values)


def relation_value(text: object, relation: str, *, require_unique: bool = False) -> str:
    """Return only an explicitly expressed value for one supported relation."""
    value = source_text(text)
    lower = value.casefold()
    def word_value(patterns, text):
        return _word_value(patterns, text, unique=require_unique)
    if require_unique and (_UNCERTAIN.search(value) or re.search(
            r"\b(?:not|never|didn't|wasn't|if|unless|would|could)\b", value, re.I)):
        return ""
    if relation == "programming_language":
        values = []
        for language in sorted(_PROGRAMMING_LANGUAGES, key=len, reverse=True):
            match = re.search(
                rf"(?<![a-z0-9+#]){re.escape(language)}(?![a-z0-9+#])",
                value, re.IGNORECASE,
            )
            if match is None:
                continue
            matched = match.group(0)
            nearby = value[max(0, match.start() - 56):match.end() + 56]
            required_case = _AMBIGUOUS_LANGUAGE_CASING.get(language)
            if required_case is not None and (
                matched != required_case
                or _AMBIGUOUS_LANGUAGE_SYNTAX[language].search(value) is None
            ):
                continue
            if language == "shell" and _PROGRAMMING_CONTEXT.search(nearby) is None:
                continue
            if not require_unique:
                return matched
            values.append(matched)
        return _unique_attribute(values)
    if relation == "preference":
        direct = word_value((
            r"\bmy\s+favou?rite\s+(?:color|colour|food|game|drink|beverage|"
            r"movie|show|media|animal|hobby)\s+is\s+(?P<value>[^.!?]{1,80})",
            r"\bthe\s+user(?:'s|’s)\s+favou?rite\s+(?:color|colour|food|game|"
            r"drink|beverage|movie|show|media|animal|hobby)\s+is\s+"
            r"(?P<value>[^.!?]{1,80})",
            r"\b(?P<value>[^.!?]{1,60})\s+is\s+my\s+favou?rite\b",
            r"\bi\s+(?:really\s+)?(?:like|love|enjoy|prefer)\s+(?P<value>[^.!?]{1,80})",
            r"\bthe\s+user\s+(?:likes|loves|enjoys|prefers)\s+(?P<value>[^.!?]{1,80})",
        ), value)
        return direct
    if relation == "identity":
        return word_value((
            r"\bmy\s+name\s+is\s+(?P<value>[A-Za-z][A-Za-z'’-]{0,48})",
            r"\b(?:call|you\s+can\s+call)\s+me\s+(?P<value>[A-Za-z][A-Za-z'’-]{0,48})",
            r"\bthe\s+user(?:'s|’s)\s+name\s+is\s+(?P<value>[A-Za-z][A-Za-z'’-]{0,48})",
        ), value)
    if relation == "ownership":
        if re.search(r"\b(?:do\s+not|don't|never)\s+(?:own|have)\b", lower):
            return ""
        return word_value((
            r"\bi\s+(?:own|owned|bought|purchased)\s+(?P<value>[^.!?]{1,96})",
            r"\bmy\s+(?P<value>[^.!?]{1,70})\s+(?:is|was)\b",
            r"\bthe\s+user\s+(?:owns|owned|bought|purchased)\s+(?P<value>[^.!?]{1,96})",
        ), value)
    if relation == "place":
        if require_unique and len(re.findall(r"\b(?:beside|by|near|at|in)\s+", value, re.I)) > 1:
            # Coordinated/nested locatives need an explicit choice. Do not
            # silently select the first even when only it matches the grammar.
            raise AmbiguousHistoricalAttribute()
        return word_value((
            # A bounded locative attached to an explicit shared past event.
            # Keep the user's preposition; no geocoding or general decomposition.
            r"\b(?:we|i)\s+(?:watched|saw|spotted|walked|sat|camped|picnicked|met|played)\b"
            r"[^.!?]{0,96}?\s+(?P<value>(?:beside|by|near|at|in)\s+(?:the\s+)?[^.!?;,]{1,60}?)"
            r"(?=\s+(?:during|while|and|before|after)\b|[.!?;,]|$)",
            r"\bi\s+(?:went|travelled|traveled|moved)\s+to\s+(?P<value>[^.!?]{1,80})",
            r"\bi\s+(?:visited|live\s+in|stayed\s+in|stayed\s+at)\s+(?P<value>[^.!?]{1,80})",
            r"\b(?:the\s+)?place\s+(?:was|is)\s+(?P<value>[^.!?]{1,80})",
            r"\bthe\s+user\s+(?:lives|went|visited|travelled|traveled)\s+(?:in|to)?\s*"
            r"(?P<value>[^.!?]{1,80})",
        ), value)
    if relation == "plan":
        return word_value((
            r"\bi\s+(?:plan|planned|intend|intended)\s+to\s+(?P<value>[^.!?]{1,96})",
            r"\bi(?:'m|\s+am)\s+going\s+to\s+(?P<value>[^.!?]{1,96})",
            r"\bi\s+will\s+(?P<value>[^.!?]{1,96})",
            r"\bthe\s+user\s+(?:plans|planned|intends|intended)\s+to\s+(?P<value>[^.!?]{1,96})",
        ), value)
    if relation == "person":
        return word_value((
            r"\b(?:met|with|person\s+(?:was|is)|their\s+name\s+was)\s+"
            r"(?P<value>[A-Z][A-Za-z'’-]{1,48})",
        ), value)
    if relation == "date":
        matches = list(re.finditer(
            r"\b(?:\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|"
            r"July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?"
            r"(?:,\s*\d{4})?)\b",
            value, re.IGNORECASE,
        ))
        values = [match.group(0) for match in matches]
        return _unique_attribute(values) if require_unique else (values[0] if values else "")
    if relation == "object":
        return word_value((
            r"\b(?:the\s+)?(?:object|item)\s+(?:was|is)\s+(?P<value>[^.!?]{1,80})",
            r"\bi\s+(?:used|held|carried|bought)\s+(?P<value>[^.!?]{1,80})",
        ), value)
    return value if value else ""


def _favorite_slot_value(text: object, slot: str) -> str:
    """Return only a value grammatically owned by the requested favorite slot."""
    value = source_text(text)
    slot_pattern = r"colou?r" if slot in {"color", "colour"} else re.escape(slot)
    end = r"(?=\s+(?:and|but)\s+(?:my|the\s+user)|[.!?;,]|$)"
    direct = _word_value((
        rf"\bmy\s+favou?rite\s+{slot_pattern}\s+is\s+(?P<value>[^.!?;,]{{1,80}}?){end}",
        rf"\bthe\s+user(?:'s|’s)\s+favou?rite\s+{slot_pattern}\s+is\s+"
        rf"(?P<value>[^.!?;,]{{1,80}}?){end}",
        rf"\b(?P<value>[^.!?]{{1,60}})\s+is\s+my\s+favou?rite\s+{slot_pattern}\b",
    ), value)
    if not direct or re.search(r"\b(?:not|never|no|maybe|perhaps|possibly)\b", direct, re.I):
        return ""
    if slot in {"color", "colour"}:
        from aifren.continuity.durable_fact_curation import (
            _RELATIVE_VALUE_TIME, _TEMPORARY, _validated_kind_value,
        )

        # An explicit favorite-color assertion supplies a literal preference
        # value, not a color inferred from unrelated prose. Do not require a
        # fixed palette here: otherwise a valid past value can disappear and
        # an older, differently named color can become the answer. Keep the
        # existing small phrase bound; clause polarity/ownership is checked by
        # the caller and the final response must preserve the full value.
        if (len(direct) > 48
                or not re.fullmatch(r"[A-Za-z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*)?", direct)
                or _validated_kind_value(direct, "favorite_color") != direct
                or _TEMPORARY.search(direct) or _RELATIVE_VALUE_TIME.search(direct)
                or set(direct.casefold().replace("-", " ").split()) & {
                    "and", "or", "but", "because", "is", "was", "non",
                }):
            return ""
        return direct
    return direct


def _slot_value(candidate: object, decision: MemoryQueryDecision, slot: MemoryQuerySlot) -> str:
    """The same value/owner gate for search candidates and final admitted evidence."""
    authority = str(getattr(candidate, "authority_class", ""))
    if decision.source_order is not None and not _source_order_proven(candidate, decision):
        return ""
    if authority == "governed_current_fact":
        if decision.historical:
            return ""  # Current facts cannot establish a historical source.
        key = str(getattr(candidate, "subject_key", ""))
        domain = {"drink": "beverage", "movie": "media", "show": "media"}.get(slot.key, slot.key)
        if key not in {f"preference.{domain}", f"identity.favorite_{domain}"}:
            return ""
        # This value has already passed the durable owner. Preserve it exactly,
        # including modifiers; a generic color token is not a replacement fact.
        return str(getattr(candidate, "value", "") or "")
    speaker = str(getattr(candidate, "speaker_role", ""))
    governed_candidate = getattr(candidate, "lane", "") == "semantic_v2" and not speaker
    if not governed_candidate and speaker not in decision.allowed_historical_speakers:
        return ""
    speech_act = str(getattr(candidate, "speech_act", ""))
    if speech_act not in {"assertion", "statement"} and not (speaker == "assistant" and speech_act == "other"):
        return ""
    text = source_text(getattr(candidate, "content", getattr(candidate, "source_text", "")))
    values = []
    # A qualified food clause must not invalidate an independent color
    # assertion in the same source record. Never strip qualifications from the
    # clause that actually supplies a value.
    for clause in re.split(r"(?<=[.!?])\s+|\s+(?:and|but)\s+(?=(?:my|the\s+user(?:'s|’s))\s+favou?rite)", text, flags=re.I):
        if (_UNCERTAIN.search(clause) or _PREFERENCE_NONASSERTIVE.search(clause)
                or any(mark in clause for mark in ('?', '"', '“', '”', '`', '*', '…', '...'))):
            continue
        value = _favorite_slot_value(clause, slot.key)
        if value and value not in values:
            values.append(value)
    return values[0] if len(values) == 1 else ""


def _source_order_proven(candidate: object, decision: MemoryQueryDecision) -> bool:
    order = decision.source_order
    witness = getattr(candidate, "order_witness", None)
    identity = str(getattr(candidate, "canonical_record_id", getattr(candidate, "evidence_id", "")))
    if not order or not order.supported or witness is None:
        return False
    return bool(witness.direction == order.direction and witness.source_id == identity and
        witness.anchor_id and witness.anchor_id != witness.source_id and
        witness.anchor_value.casefold() == order.anchor_value.casefold() and
        witness.speaker_role == order.anchor_speaker == getattr(candidate, "speaker_role", "") and
        witness.truth_scope_id and
        witness.truth_scope_id == getattr(candidate, "truth_scope_id", witness.truth_scope_id) and
        (witness.source_index < witness.anchor_index if order.direction == "before"
         else witness.source_index > witness.anchor_index))


def admit_requested_slots(candidates: tuple[object, ...], decision: MemoryQueryDecision) -> tuple[EvidenceSlotAdmission, ...]:
    """Preserve independent support; run after structural selection as well.

    Selection owns source/scope/lifecycle checks. This owner proves only the
    relation, and never lets an excluded or budget-trimmed source authorize a
    final slot. Governed current evidence wins over historical evidence for a
    current question; historical questions retain their source-only authority.
    """
    slots = []
    for slot in decision.requested_slots:
        checks = [(candidate, _slot_value(candidate, decision, slot)) for candidate in candidates]
        supplied = [(candidate, value) for candidate, value in checks if value]
        current = [(candidate, value) for candidate, value in supplied
                   if getattr(candidate, "authority_class", "") == "governed_current_fact"]
        if current:
            supplied = current
        # Conflicting sources cannot choose a current value by retrieval rank.
        # Historical values remain independently attributed in the requirement.
        slots.append(EvidenceSlotAdmission(
            slot, tuple(candidate for candidate, _ in supplied),
            tuple(value for _, value in supplied),
        ))
    return tuple(slots)


def candidate_answers_memory_query(
    candidate: object,
    decision: MemoryQueryDecision,
) -> EvidenceSufficiency:
    """Prove that a retrieved candidate supplies the requested answer shape."""
    relation = decision.requested_relation
    if decision.reason == "ordinary_topic_past_value":
        # The explicit topic identifies the source to look up, not a new fact
        # or a revived one-turn anchor. A partially matching topic, a question,
        # or the other speaker's account cannot answer this personal callback.
        topic = set(re.findall(r"\w+", decision.subject_reference.casefold()))
        content = set(re.findall(r"\w+", source_text(getattr(candidate, "content", "")).casefold()))
        speaker = getattr(candidate, "speaker_role", "")
        # Canonical historical indexing labels assistant prose "other"; its
        # exact quoted account retains that label, never a fabricated user act.
        acts = {"assertion", "other"} if speaker == "assistant" else {"assertion"}
        if (not topic or not topic.issubset(content)
                or not getattr(candidate, "canonical_record_id", "")
                or speaker not in decision.allowed_historical_speakers
                or getattr(candidate, "speech_act", "") not in acts):
            return EvidenceSufficiency(False, relation, reason="topic_source_unresolved")
    if decision.source_order is not None and not _source_order_proven(candidate, decision):
        return EvidenceSufficiency(False, relation, reason="source_order_unproven")
    if decision.requested_slots:
        slots = admit_requested_slots((candidate,), decision)
        supplied = next((slot for slot in slots if slot.supported), None)
        return EvidenceSufficiency(
            supplied is not None, relation,
            supplied.values[0] if supplied else "",
            "requested_relation_supplied" if supplied else "requested_preference_value_missing",
        )
    if not decision.applicable or relation in {"not_applicable", "historical_recall"}:
        return EvidenceSufficiency(True, relation, reason="relation_not_value_bearing")
    speech_act = str(getattr(candidate, "speech_act", "") or "")
    speaker = str(getattr(candidate, "speaker_role", "") or "")
    lane = str(getattr(candidate, "lane", "") or "")
    authority_is_governed = lane == "semantic_v2" and not speaker
    if speech_act == "question":
        return EvidenceSufficiency(False, relation, reason="question_does_not_supply_value")
    if relation in {"preference", "identity", "ownership", "place", "plan"}:
        allowed_speakers = decision.allowed_historical_speakers
        if (
            not authority_is_governed
            and speaker not in allowed_speakers
        ):
            return EvidenceSufficiency(False, relation, reason="source_owner_cannot_establish_relation")
    text = getattr(candidate, "content", "")
    if relation == "preference" and decision.retrieval_slots:
        requested_slot = decision.retrieval_slots[0].casefold()
        slot_value = _favorite_slot_value(text, requested_slot)
        if not slot_value:
            return EvidenceSufficiency(
                False, relation, reason="requested_preference_value_missing",
            )
        value = slot_value
    else:
        value = relation_value(text, relation)
    if not value:
        return EvidenceSufficiency(False, relation, reason="requested_relation_value_missing")
    if relation in {"preference", "ownership"} and _UNCERTAIN.search(source_text(text)):
        return EvidenceSufficiency(False, relation, reason="uncertain_source_cannot_establish_relation")
    return EvidenceSufficiency(True, relation, value=value, reason="requested_relation_supplied")


def admit_memory_evidence(
    candidates: tuple[object, ...], decision: MemoryQueryDecision,
) -> EvidenceAdmission:
    """Admit each supported requested slot without inventing its missing peers."""
    values = tuple(candidates)
    if decision.requested_slots:
        slots = admit_requested_slots(values, decision)
        admitted = tuple(candidate for candidate in values
                         if any(candidate in slot.candidates for slot in slots))
        return EvidenceAdmission(
            admitted, len(values) - len(admitted),
            "requested_preference_slots_partial" if admitted and any(not slot.supported for slot in slots)
            else "requested_relation_supplied" if admitted else "requested_relation_incomplete",
            slots,
        )
    checks = tuple(
        (candidate, candidate_answers_memory_query(candidate, decision))
        for candidate in values
    )
    admitted = tuple(candidate for candidate, check in checks if check.sufficient)
    if decision.reason == "ordinary_topic_past_value":
        # Ranking is navigation, not disambiguation. Without an existing typed
        # ordering/value constraint, distinct accounts of the named topic must
        # not silently become a single answer via the secondary-score cutoff.
        sources = {source_text(getattr(candidate, "content", "")).casefold() for candidate in admitted}
        identities = {getattr(candidate, "canonical_record_id", "") for candidate in admitted}
        if not decision.subject_reference or (len(identities) > 1 and len(sources) > 1):
            return EvidenceAdmission((), len(values), "topic_reference_ambiguous")
    return EvidenceAdmission(
        admitted,
        sum(not check.sufficient for _, check in checks),
        "requested_relation_supplied" if admitted else "requested_relation_incomplete",
    )


__all__ = [
    "EvidenceAdmission",
    "EvidenceSufficiency",
    "EvidenceSlotAdmission",
    "admit_memory_evidence",
    "admit_requested_slots",
    "candidate_answers_memory_query",
    "relation_value",
    "source_text",
]
