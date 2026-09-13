"""One provider-neutral decision for Memory V2 query authority.

This module owns the grammatical decision that used to be repeated by
retrieval, recent-context containment, prompt admission, and response
governance.  Retrieval lanes may still interpret tokens for ranking, but they
must not independently decide whether a turn is a memory question or who owns
the requested historical source.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


MEMORY_QUERY_DECISION_VERSION = 5

_USER_HISTORY = re.compile(
    r"\bdo\s+you\s+know\s+(?:whether|if)\s+i\s+(?:ever\s+)?"
    r"(?:told\s+you|said|mentioned|asked)\b|"
    r"\b(?:have|did)\s+i\s+(?:ever\s+)?"
    r"(?:tell\s+you|told\s+you|say|said|mention|mentioned|ask|asked|claim|claimed)\b|"
    r"\bdo\s+you\s+(?:remember|recall)\s+me\s+"
    r"(?:saying|telling|talking|mentioning|asking|discussing)\b|"
    r"\bwhat\s+do\s+you\s+(?:remember|recall)\s+(?:about\s+)?me\s+"
    r"(?:saying|telling|talking|mentioning|asking|discussing)\b|"
    r"\bwhat\s+did\s+i\s+(?:say|tell|mention|ask|claim|discuss)\b|"
    r"\b(?:which|what)\b.{0,80}\bdid\s+i\s+(?:say|tell|mention|claim)\b",
    re.IGNORECASE,
)
_ASSISTANT_HISTORY = re.compile(
    r"\b(?:did|have)\s+you\s+(?:ever\s+)?"
    r"(?:tell|told|say|said|mention|mentioned|discuss|discussed)\s+me\b|"
    r"\bwhat\s+did\s+you\s+(?:tell|say|mention|discuss|recall|remember)\b|"
    r"\bdo\s+you\s+remember\s+(?:yourself\s+)?"
    r"(?:saying|telling|mentioning|discussing)\b|"
    r"\bwhat\s+(?:was|is)\s+that\s+thing\s+you\s+(?:said|mentioned)\b|"
    r"\b(?:you|the\s+assistant)\s+(?:previously\s+)?"
    r"(?:said|told|mentioned|recalled|discussed)\b",
    re.IGNORECASE,
)
_SHARED_HISTORY = re.compile(
    r"\bdo\s+you\s+(?:remember|recall)\s+what\s+we\s+(?:talked|discussed|said|mentioned)\b|"
    r"\b(?:have|did)\s+we\s+(?:ever\s+)?(?:talk|talked|discuss|discussed|mention|mentioned)\b|"
    r"\b(?:we|we['’]?ve|we\s+have)\s+(?:talked|discussed|mentioned)\b|"
    r"\bwhat\s+did\s+we\s+(?:talk|discuss|mention|say)\b|"
    r"\bwhat\s+(?:have|did)\s+we\s+(?:talk|discuss)\b|"
    r"\bwhat\s+do\s+you\s+remember\s+(?:about\s+)?(?:us|our\s+conversation)\b|"
    r"\bremember\s+(?:what\s+)?we\s+(?:talked|discussed)\b|"
    r"\b(?:shared\s+conversation|stuff\s+we\s+talked\s+about)\b",
    re.IGNORECASE,
)
_ASSISTANT_OPINION = re.compile(
    r"\b(?:what|how)\s+do\s+you\s+(?:think|feel|like|prefer|want)\b|"
    r"\bwhat\s+(?:do|would)\s+you\s+(?!(?:remember|recall|know)\b)|"
    r"\bwould\s+you\s+(?:like|play|choose|prefer)\b|"
    r"\bdo\s+you\s+(?:think|like|prefer|want|play)\b",
    re.IGNORECASE,
)
_GENERIC_REASONING = re.compile(
    r"\b(?:(?:tell|show)\s+me\s+(?:something|anything)\s+(?:interesting|new)|"
    r"what\s+should|which\s+.+\s+would\s+you\s+recommend|"
    r"recommend|solve|math\s+problem|beginner)\b",
    re.IGNORECASE,
)
_CURRENT_STATE_ONLY = re.compile(
    r"\b(?:right\s+now|currently)\b.*\b(?:holding|wearing|doing|standing|sitting)\b|"
    r"\bwhat\s+am\s+i\s+(?:holding|wearing|doing)\b",
    re.IGNORECASE,
)
_GENERIC_RECOLLECTION = re.compile(
    r"^(?:(?:do|can|could|would|will)\s+you\s+(?:please\s+)?(?:remember|recall)\b(?!\s+to\b)|"
    r"(?:what|which|when|where|who)\b.{0,100}\byou\s+(?:remember|recall)\b|"
    r"(?:what|which)\b.{0,80}\b(?:my|our)\s+(?:memory|memories)\b|"
    r"(?:remember|recall)\s+(?!(?:to|that|before|after|when)\b)\S+\b)",
    re.IGNORECASE,
)
_HISTORICAL_FACT_REQUEST = re.compile(
    r"^(?:what|which|where|who|when)\b.{0,65}\b(?:did|was|were|had)\s+(?:i|we|you|my|our)\b"
    r".*\b(?:before|after|previously|used\s+to|long\s+ago)\b", re.I,
)
# A named topic return followed by a personal past-value question is a recall
# request even without the words "remember" or "said". The topic is data, not
# an immediate-answer anchor, and must be resolved against canonical evidence.
_TOPIC_RETURN = re.compile(
    r"^(?:back\s+to|returning\s+to|regarding|about|as\s+for)\s+"
    r"(?P<topic>[\w'’ -]{1,72})\s*[:,.!?—–]\s*"
    r"(?P<request>(?:what|which|where|who|when|do|did|can|could)\b.+)$", re.I,
)
_PERSONAL_PAST_VALUE = re.compile(
    r"^(?:what|which|where|who|when)\b.{0,65}\bdid\s+(i|you|we)\s+[a-z]+\b", re.I,
)
_CURRENT_FACT = re.compile(
    r"\bwhat(?:\s+(?:is|are)|'s)\s+my\s+(?:current\s+)?(?:name|favou?rite|preference)\b|"
    r"\bdo\s+you\s+know\s+my\s+(?:name|favou?rite|preference)\b|"
    r"\b(?:which|what)\b.{0,50}\bdo\s+i\s+(?:own|have|prefer)\b",
    re.IGNORECASE,
)
_QUESTION_HISTORY = re.compile(
    r"\b(?:question\s+i\s+asked|did\s+i\s+(?:ever\s+)?ask|"
    r"do\s+you\s+know\s+(?:whether|if)\s+i\s+(?:ever\s+)?asked|"
    r"what\s+did\s+i\s+ask|do\s+you\s+remember\s+me\s+asking)\b",
    re.IGNORECASE,
)
_CONCRETE_FOLLOWUP = (
    ("programming_language", re.compile(
        r"\b(?:which|what)\s+(?:programming\s+)?language\b"
        r"(?:(?=\s+(?:was|were|did)\b).{0,80}\b(?:it|that|this)\b|"
        r"\s+(?:was|were)\s+(?:it|that|this)\b)|"
        r"\b(?:programming\s+)?language\b.{0,100}\b(?:do\s+you\s+remember\s+)?"
        r"(?:which\s+one|what\s+(?:one|was\s+it))\b",
        re.IGNORECASE,
    )),
    ("place", re.compile(
        r"\b(?:which|what)\s+place\b.{0,60}\b(?:it|that|this)\b|"
        r"\bwhere\s+(?:was|were|did)\b.{0,50}\b(?:it|that|this)\b",
        re.IGNORECASE,
    )),
    ("person", re.compile(
        r"\b(?:which|what)\s+person\b.{0,60}\b(?:it|that|this)\b|"
        r"\bwho\s+(?:was|were)\b.{0,50}\b(?:that|it)\b",
        re.IGNORECASE,
    )),
    ("date", re.compile(
        r"\b(?:which|what)\s+date\b.{0,60}\b(?:it|that|this)\b|"
        r"\bwhen\s+(?:was|were|did)\b.{0,50}\b(?:it|that|this)\b",
        re.IGNORECASE,
    )),
    ("identity", re.compile(
        r"\b(?:which|what)\s+name\b.{0,60}\b(?:it|that|this|they|them)\b|"
        r"\bwhat\s+was\s+(?:its|their|that)\s+name\b",
        re.IGNORECASE,
    )),
    ("object", re.compile(
        r"\b(?:which|what)\s+(?:object|item)\b.{0,60}\b(?:it|that|this)\b",
        re.IGNORECASE,
    )),
)

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_-]*")
_SUBJECT_STOP = frozenset({
    "about", "again", "anything", "are", "before", "did", "do", "ever",
    "have", "history", "long", "memory", "memories", "most", "one",
    "know", "owned", "previously", "recall", "remember", "said", "say",
    "something", "tell", "thing", "told", "what", "which", "your",
})


@dataclass(frozen=True)
class MemoryQuerySlot:
    """One bounded requested value; consumers never decompose the query again."""

    relation: str
    key: str

    @property
    def label(self) -> str:
        return f"favorite {self.key}"


@dataclass(frozen=True)
class MemorySourceOrder:
    """One source-order constraint, never a calendar/time interpretation.

    An unrecognized anchor is retained as unresolved; it must not degrade into
    an ordinary topic lookup. Values are data and never diagnostic text.
    """

    direction: str
    anchor_kind: str
    anchor_value: str
    anchor_speaker: str
    slot: MemoryQuerySlot | None

    @property
    def supported(self) -> bool:
        return bool(self.slot and self.anchor_value and self.anchor_kind in {"correction", "statement"})


@dataclass(frozen=True)
class MemoryQueryDecision:
    """The immutable per-turn memory authority decision shared by consumers."""

    applicable: bool
    intent: str
    requested_relation: str
    requested_speaker: str
    time_semantics: str
    subject_terms: tuple[str, ...]
    subject_reference: str
    authoritative_no_evidence_allowed: bool
    contain_recent_context: bool
    exact_source_must_communicate: bool
    requested_speech_act: str
    retrieval_intent: str
    retrieval_slots: tuple[str, ...]
    reason: str
    version: int = MEMORY_QUERY_DECISION_VERSION
    requested_slots: tuple[MemoryQuerySlot, ...] = ()
    source_order: MemorySourceOrder | None = None
    topic_return: bool = False

    @property
    def historical(self) -> bool:
        return self.time_semantics == "historical"

    @property
    def callback_source_kind(self) -> str:
        if self.intent == "user_historical_source":
            return "user"
        if self.intent == "assistant_historical_source":
            return "assistant"
        if self.intent == "shared_historical_conversation":
            return "shared"
        return ""

    @property
    def allowed_historical_speakers(self) -> frozenset[str]:
        if self.requested_speaker == "assistant":
            return frozenset({"assistant"})
        if self.requested_speaker == "shared":
            return frozenset({"user", "assistant"})
        return frozenset({"user"})

    def diagnostics(self) -> dict[str, object]:
        """Return content-free bounded fields suitable for Development traces."""
        return {
            "memory_query_applicable": self.applicable,
            "memory_query_intent": self.intent,
            "requested_relation": self.requested_relation,
            "requested_speaker": self.requested_speaker or "not_applicable",
            "memory_time_semantics": self.time_semantics,
            "memory_query_reason": self.reason,
            "memory_query_decision_version": self.version,
            "memory_source_order": self.source_order.direction if self.source_order else "none",
            "memory_order_anchor_kind": self.source_order.anchor_kind if self.source_order else "none",
            "memory_topic_return": self.topic_return,
        }


def _relation(text: str) -> str:
    lower = text.casefold()
    if re.search(r"\b(?:own|owned|ownership|bought|purchased)\b", lower):
        return "ownership"
    if re.search(r"\b(?:favou?rite|preference|prefer)\b", lower):
        return "preference"
    if re.search(r"\bmy\s+name\b|\bwhat\s+should\s+you\s+call\s+me\b", lower):
        return "identity"
    if re.search(r"\b(?:place|where|went|visit|visited|trip|travel|destination)\b", lower):
        return "place"
    if re.search(r"\b(?:plan|planned|planning|intend|intended|later|future|going\s+to)\b", lower):
        return "plan"
    return "historical_recall"


def _subject_reference(text: str) -> str:
    match = re.search(r"\b(?:about|regarding)\s+(.+?)(?:[?.!]|$)", text, re.I)
    value = match.group(1).strip() if match is not None else ""
    value = re.sub(r"^(?:anything|something)\s+(?:at\s+all\s+)?about\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:before|previously|in\s+the\s+past)$", "", value, flags=re.I)
    value = re.sub(r"^my\b", "your", value, flags=re.I)
    if (
        not value
        or value.casefold() in {"it", "that", "this", "anything", "something"}
        or len(value) > 72
        or len(_WORD.findall(value)) > 8
        or re.search(r"[^A-Za-z0-9'’ _-]", value)
    ):
        return ""
    return " ".join(value.split())


def _subject_terms(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        token.casefold() for token in _WORD.findall(text)
        if len(token) >= 3 and token.casefold() not in _SUBJECT_STOP
    ))[:6]


def _retrieval_slots(text: str) -> tuple[str, ...]:
    lower = text.casefold()
    favorite = tuple(dict.fromkeys(
        match for match in re.findall(r"\bfavou?rite\s+([a-z0-9]+)", lower)
        # In "what color was my favorite before/after ...", the temporal
        # operator starts a clause; it is not a preference-property noun.
        # Fall through to the existing named-property lookup, never the value.
        if match not in {"before", "after"}
    ))
    if favorite:
        # One bounded coordinated favorite list, e.g. "favorite color and
        # food". This does not decompose unrelated questions or infer topics.
        coordinated = re.findall(
            r"\bfavou?rite\s+[a-z0-9]+\s+and\s+(color|colour|food|game|drink|beverage|movie|show|media|animal|hobby)\b",
            lower,
        )
        return tuple(dict.fromkeys((*favorite, *coordinated)))
    return tuple(
        token for token in ("color", "animal", "game", "hobby", "food", "location")
        if re.search(rf"\b{token}\b", lower)
    )


def _concrete_followup_relation(text: str) -> str:
    for relation, pattern in _CONCRETE_FOLLOWUP:
        if pattern.search(text):
            return relation
    return ""


def _source_order(query: str, speaker: str, slots: tuple[MemoryQuerySlot, ...]) -> MemorySourceOrder | None:
    match = re.search(r"\b(before|after)\s+(.+)$", query, re.I)
    if match is None:
        return None
    direction, anchor = match[1].lower(), match[2].strip()
    # "What did you tell me before about X?" uses an unanchored recall
    # adverb. Its topic preposition does not introduce an ordering operand.
    if direction == "before" and re.match(r"(?:about|regarding)\b", anchor, re.I):
        return None
    # Bounded grammatical clauses, shared across preference kinds and values.
    # No topical keyword, current fact, embedding or second model resolves this.
    correction = re.fullmatch(
        r"(I|you)\s+(?:changed|corrected|updated)\s+(?:it|that|my\s+favou?rite\s+\w+|your\s+favou?rite\s+\w+)\s+to\s+(.+)", anchor, re.I)
    statement = re.fullmatch(
        r"(I|you)\s+(?:said|told\s+(?:you|me))\s+(?:(?:that\s+)?(?:it|my\s+favou?rite\s+\w+|your\s+favou?rite\s+\w+)\s+(?:is|was)\s+)(.+)", anchor, re.I)
    found = correction or statement
    value = found[2].strip() if found else ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*){0,5}", value) or len(value) > 64:
        value = ""
    owner = ("user" if found[1].lower() == "i" else "assistant") if found else ""
    return MemorySourceOrder(direction, "correction" if correction else "statement" if statement else "unresolved",
                             value, owner, slots[0] if len(slots) == 1 and owner == speaker else None)


def _unquoted_query(text: str) -> str:
    # Quoted examples are data, including questions inside quoted examples.
    # Apostrophes within words remain intact. This is routing only: canonical
    # input and the ordinary continuity context retain the original text.
    return re.sub(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|`[^`\n]*`|(?<!\w)\'[^\'\n]+\'(?!\w)', " ", text)


def _request_clauses(text: str) -> tuple[str, ...]:
    unquoted = _unquoted_query(text)
    clauses = []
    for part in re.split(r"[.!?;,]", unquoted):
        part = re.sub(r"^(?:and\s+)?(?:please\s+)?", "", part.strip(), flags=re.I)
        part = re.sub(
            r"^(?:(?:can|could|would)\s+you\s+)?(?:tell|remind)\s+me\s+"
            r"(?=(?:what|which|when|where|who)\b)", "", part, flags=re.I,
        )
        if re.match(r"^(?:what|which|when|where|who|how|do|did|have|can|could|would|will|remind|recall)\b", part, re.I):
            clauses.append(part)
        elif re.match(r"^remember\s+(?!(?:to|that|before|after|when)\b)", part, re.I) and not re.match(
            r"^remember\s+(?:(?:my|your|our)\b.{0,60}\b(?:is|are)\b|i\s+(?:like|love|am|have|prefer)\b)", part, re.I,
        ):
            clauses.append(part)
    return tuple(clauses)


def decide_memory_query(query_text: object) -> MemoryQueryDecision:
    """Classify a turn once for every V2 authority consumer.

    Rules are deliberately grammatical and topic-neutral.  The output is a
    closed structural decision; raw query text is not retained in diagnostics.
    """
    raw_query = " ".join(str(query_text or "").split())
    topic_return = _TOPIC_RETURN.fullmatch(_unquoted_query(raw_query))
    clauses = _request_clauses(topic_return["request"] if topic_return else raw_query)
    query = " and ".join(clauses)
    relation = _relation(query)
    terms = _subject_terms(query)
    reference = _subject_reference(query)
    slots = _retrieval_slots(query)

    intent = "non_memory"
    speaker = ""
    time_semantics = "not_applicable"
    reason = "no_memory_grammar"
    applicable = False
    exact_source = False
    speech_act = "any"
    # The established one-hop shape may name its attribute in a preceding
    # sentence. It still requires an actual question/request in this turn.
    followup_relation = _concrete_followup_relation(_unquoted_query(raw_query)) if clauses else ""
    matches = lambda pattern: any(pattern.match(clause) for clause in clauses)

    if followup_relation:
        applicable = True
        intent = "grounded_followup_attribute"
        speaker = "shared"
        time_semantics = "historical"
        relation = followup_relation
        reason = "concrete_grounded_followup_attribute"
        exact_source = True
        speech_act = "assertion"
    elif matches(_USER_HISTORY):
        applicable = True
        intent = "user_historical_source"
        speaker = "user"
        time_semantics = "historical"
        reason = "explicit_user_source_grammar"
        exact_source = True
        speech_act = "question" if _QUESTION_HISTORY.search(query) else "assertion"
    elif matches(_ASSISTANT_HISTORY):
        applicable = True
        intent = "assistant_historical_source"
        speaker = "assistant"
        time_semantics = "historical"
        reason = "explicit_assistant_source_grammar"
        exact_source = True
        speech_act = "assertion"
    elif matches(_SHARED_HISTORY):
        applicable = True
        intent = "shared_historical_conversation"
        speaker = "shared"
        time_semantics = "historical"
        reason = "explicit_shared_source_grammar"
        exact_source = True
    elif _ASSISTANT_OPINION.search(query):
        intent = "assistant_opinion"
        reason = "assistant_opinion_grammar"
    elif _CURRENT_STATE_ONLY.search(query):
        intent = "non_memory"
        reason = "current_active_state_query"
    elif _GENERIC_REASONING.search(query or raw_query) and not matches(_GENERIC_RECOLLECTION):
        intent = "non_memory"
        reason = "generic_reasoning_grammar"
    elif matches(_CURRENT_FACT):
        applicable = True
        intent = "current_governed_fact"
        speaker = "user"
        time_semantics = "current"
        reason = "explicit_current_fact_grammar"
    elif topic_return and (past := _PERSONAL_PAST_VALUE.match(query)):
        applicable = True
        speaker = {"i": "user", "you": "assistant", "we": "shared"}[past[1].lower()]
        intent = {"user": "user_historical_source", "assistant": "assistant_historical_source",
                  "shared": "shared_historical_conversation"}[speaker]
        time_semantics = "historical"
        reason = "ordinary_topic_past_value"
        exact_source = True
        speech_act = "assertion"
    elif matches(_GENERIC_RECOLLECTION) or matches(_HISTORICAL_FACT_REQUEST):
        applicable = True
        intent = "generic_recollection"
        speaker = "user" if re.search(r"\b(?:i|me|my)\b", query, re.I) else ""
        time_semantics = "historical"
        reason = "explicit_recollection_grammar"

    if intent == "user_historical_source":
        retrieval_intent = "historical_user_source"
    elif intent == "assistant_historical_source":
        retrieval_intent = "historical_assistant_source"
    elif intent == "shared_historical_conversation":
        retrieval_intent = "historical_shared_source"
    elif intent == "assistant_opinion":
        retrieval_intent = "assistant_opinion"
    elif intent == "grounded_followup_attribute":
        retrieval_intent = "historical_grounded_followup"
    elif reason == "generic_reasoning_grammar":
        retrieval_intent = "generic_reasoning"
    elif (
        applicable and relation == "preference" and not slots
        and re.search(r"\bfavou?rite\b", query, re.IGNORECASE)
    ):
        retrieval_intent = "ambiguous_memory"
    elif applicable and " and " in query.casefold() and len(terms) >= 3:
        retrieval_intent = "multi_user_memory"
    elif applicable:
        retrieval_intent = (
            "historical_user_fact" if time_semantics == "historical" else "user_memory"
        )
    else:
        retrieval_intent = "unspecified"

    requested_slots = tuple(
        MemoryQuerySlot("preference", "color" if slot == "colour" else slot)
        for slot in dict.fromkeys(slots)
    ) if applicable and relation == "preference" else ()
    order = _source_order(query, speaker, requested_slots) if (
        applicable and time_semantics == "historical" and
        (intent in {"user_historical_source", "assistant_historical_source"} or relation == "preference")
    ) else None
    if topic_return and applicable:
        reference = re.sub(r"^(?:the|my|your|our)\s+", "", topic_return["topic"].strip(), flags=re.I)
        # Deictic topic labels cannot revive a prior turn's routing handle.
        if reference.casefold() in {"it", "that", "this", "them", "those", "that topic", "this topic"}:
            reference = ""
        terms = _subject_terms(reference)
    return MemoryQueryDecision(
        applicable=applicable,
        intent=intent,
        requested_relation=relation if applicable else "not_applicable",
        requested_speaker=speaker,
        time_semantics=time_semantics,
        subject_terms=terms if applicable else (),
        subject_reference=reference if applicable else "",
        authoritative_no_evidence_allowed=applicable,
        contain_recent_context=applicable,
        exact_source_must_communicate=exact_source,
        requested_speech_act=speech_act,
        retrieval_intent=retrieval_intent,
        retrieval_slots=slots,
        reason=reason,
        requested_slots=requested_slots,
        source_order=order,
        topic_return=bool(topic_return and applicable),
    )


__all__ = [
    "MEMORY_QUERY_DECISION_VERSION",
    "MemoryQueryDecision",
    "MemoryQuerySlot",
    "MemorySourceOrder",
    "decide_memory_query",
]
