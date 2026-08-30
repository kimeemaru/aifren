"""Bounded natural-language intent proposals for Current Continuity.

This is deliberately a small compositional interpreter, not a general language
model.  It combines explicit enactment/lifecycle cues with authoritative
current state and returns a closed proposal schema.  It has no persistence or
mutation capability; existing Current Continuity validators remain the only
write boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from continuity_reference import (
    LogicalThreadGroup,
    RecentUserTurn,
    build_logical_thread_groups,
    lifecycle_target_ids,
    match_open_thread,
    resolve_thread_reference,
    subject_identity,
)
from memory_v2_store.repository import ActiveStateRecord, OpenThreadRecord, TruthScopeRecord
from memory_v2_store.truth_scope_contract import normalize_truth_scope_label


_TOKEN = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ]+(?:['’][A-Za-z0-9À-ÖØ-öø-ÿ]+)?")
_QUOTE = re.compile(r"[\"“”`]")
_FILLERS = frozenset({
    "okay", "ok", "yeah", "yep", "alright", "well", "uh", "um", "hey", "so", "please",
    "finally", "oh",
})
_ARTICLES = frozenset({"a", "an", "the", "this", "that"})
_TOPIC_STOP = frozenset({
    "a", "an", "and", "are", "at", "be", "for", "from", "i", "im", "in", "is", "it", "my",
    "of", "on", "our", "that", "the", "this", "to", "we", "with", "thing", "stuff", "finally",
    "still", "actually", "anymore", "now", "again",
})
_TRAILING_TIME = (
    ("for", "a", "while"), ("for", "a", "bit"), ("for", "now"),
    ("for", "awhile"), ("right", "now"), ("now",), ("please",),
)
_ROLE_WORDS = frozenset({"roleplay", "roleplaying", "rp", "scenario", "pretend"})
_FICTION_DISCUSSION = frozenset({"game", "games", "tabletop", "campaign", "movie", "novel", "book"})
_REALITY_LABEL_WORDS = frozenset({"back", "home", "life", "real", "reality", "world"})


@dataclass(frozen=True)
class Token:
    word: str
    raw: str
    start: int
    end: int


@dataclass(frozen=True)
class BoundedContinuityIntent:
    intent: str
    confidence: str = "high"
    actor: str | None = None
    value: str | None = None
    target_id: str | None = None
    target_ids: tuple[str, ...] = ()
    target_label: str | None = None
    kind: str | None = None
    participant_scope: str | None = None
    temporal_anchor: str | None = None


@dataclass(frozen=True)
class IntentDecision:
    intents: tuple[BoundedContinuityIntent, ...]
    reason: str

    @property
    def proposed(self) -> bool:
        return bool(self.intents)


def _root(word: str) -> str:
    word = word.casefold().replace("’", "'")
    fixed = {
        "i'm": "im", "we're": "we", "let's": "lets", "gonna": "going",
        "don't": "not", "dont": "not", "didn't": "not", "doesn't": "not", "isn't": "not",
        "wasn't": "not", "hasn't": "not", "haven't": "not", "won't": "not",
        "playing": "play", "played": "play", "watches": "watch", "watching": "watch",
        "watched": "watch", "installing": "install", "installed": "install",
        "working": "work", "worked": "work", "sleeping": "sleep", "slept": "sleep",
        "stopped": "stop", "stopping": "stop", "finished": "finish", "finishing": "finish",
        "completed": "complete", "completing": "complete", "cancelled": "cancel",
        "canceled": "cancel", "arrived": "arrive", "arrives": "arrive", "came": "come",
        "delivered": "deliver", "got": "get", "received": "receive", "showed": "show", "shown": "show",
        "waiting": "wait", "awaiting": "await", "planning": "plan", "planned": "plan",
        "roleplaying": "roleplay", "role-play": "roleplay", "rping": "roleplay",
        "pretending": "pretend",
        "heading": "head", "meant": "mean", "thinking": "think", "upgrading": "upgrade",
        "adventurers": "adventurer", "movies": "movie", "games": "game",
    }
    return fixed.get(word, word)


def _tokens(text: str) -> tuple[Token, ...]:
    return tuple(Token(_root(match.group(0)), match.group(0), match.start(), match.end()) for match in _TOKEN.finditer(text))


def _trim_fillers(tokens: tuple[Token, ...]) -> tuple[Token, ...]:
    index = 0
    while index < len(tokens) and tokens[index].word in _FILLERS:
        index += 1
    return tokens[index:]


def _words(tokens: Iterable[Token]) -> tuple[str, ...]:
    return tuple(item.word for item in tokens)


def _contains(words: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    return _find(words, sequence) is not None


def _find(words: tuple[str, ...], sequence: tuple[str, ...], start: int = 0) -> int | None:
    if not sequence:
        return None
    for index in range(start, len(words) - len(sequence) + 1):
        if words[index:index + len(sequence)] == sequence:
            return index
    return None


def _phrase(text: str, tokens: tuple[Token, ...], start: int, end: int | None = None) -> str | None:
    end = len(tokens) if end is None else end
    while start < end and tokens[start].word in _ARTICLES:
        start += 1
    while start < end and tokens[end - 1].word in _FILLERS:
        end -= 1
    words = _words(tokens[start:end])
    for suffix in _TRAILING_TIME:
        if len(words) >= len(suffix) and words[-len(suffix):] == suffix:
            end -= len(suffix)
            words = _words(tokens[start:end])
            break
    if start >= end:
        return None
    value = " ".join(item.raw for item in tokens[start:end]).strip()
    return value if 0 < len(value) <= 118 else None


def _topic_words(value: str) -> set[str]:
    return {token.word for token in _tokens(value) if token.word not in _TOPIC_STOP and len(token.word) > 1}


def _is_question_or_report(text: str, words: tuple[str, ...]) -> bool:
    conventional_suggestion = (
        words[:3] == ("why", "not", "we")
        and (any(word in _ROLE_WORDS for word in words) or _contains(words, ("role", "play")))
    )
    explicit_continuity_request = (
        conventional_suggestion
        or (
            words[:2] in {("can", "we"), ("could", "we")}
            and (
                any(word in _ROLE_WORDS for word in words)
                or _contains(words, ("role", "play"))
                or _contains(words, ("real", "life"))
                or _contains(words, ("real", "world"))
                or "reality" in words
            )
        )
    )
    if text.rstrip().endswith("?") and not explicit_continuity_request:
        return True
    if words and words[0] in {"are", "can", "could", "did", "do", "does", "has", "have", "is", "should", "would"}:
        if not explicit_continuity_request:
            return True
    if words[:2] in {("what", "if"), ("how", "about")} or (words and words[0] == "if"):
        return True
    if words and words[0] in {"imagine", "suppose", "hypothetically"}:
        return True
    if any(word in {"maybe", "perhaps", "possibly", "wonder"} for word in words):
        return True
    if _QUOTE.search(text) or any(word in {"said", "says", "quote", "quoted"} for word in words):
        return True
    return False


def _scenario_label_match(words: tuple[str, ...], scope: TruthScopeRecord) -> bool:
    label_words = tuple(token.word for token in _tokens(scope.label) if token.word not in _ARTICLES)
    return bool(label_words and _find(words, label_words) is not None)


def _named_scenario_match(
    words: tuple[str, ...], scopes: tuple[TruthScopeRecord, ...],
) -> TruthScopeRecord | None:
    matches = [scope for scope in scopes if scope.kind == "scenario" and _scenario_label_match(words, scope)]
    return matches[0] if len(matches) == 1 else None


def _scenario_enactment(words: tuple[str, ...]) -> bool:
    role_cue = any(word in _ROLE_WORDS for word in words) or _contains(words, ("role", "play"))
    if role_cue and any(word in (_FICTION_DISCUSSION | {"campaign"}) for word in words):
        return False
    conventional_suggestion = words[:3] == ("why", "not", "we") and role_cue
    first_person_desire = (
        role_cue
        and any(word in {"i", "we"} for word in words[:3])
        and any(
            _contains(words, (desire, "to", "roleplay"))
            or _contains(words, (desire, "to", "role", "play"))
            or _contains(words, (desire, "to", "pretend"))
            for desire in ("want", "wish")
        )
    )
    present_enactment = (
        role_cue
        and "we" in words[:3]
        and any(word in {"are", "roleplay"} for word in words[1:3])
        and (
            _contains(words, ("we", "are", "roleplay"))
            or (len(words) >= 2 and words[0] == "we" and words[1] == "roleplay")
        )
    )
    return (
        ("lets" in words and role_cue)
        or (words[:2] in {("can", "we"), ("could", "we")} and role_cue)
        or (words and words[0] == "pretend" and ("we" in words or "im" in words))
        or ("start" in words and role_cue and ("lets" in words or "we" in words))
        or conventional_suggestion
        or first_person_desire
        or present_enactment
    )


def _scenario_correction(words: tuple[str, ...]) -> bool:
    return (
        "mean" in words
        and any(word in {"i", "we"} for word in words)
        and any(word in {"actually", "no", "sorry", "misheard"} for word in words)
    )


def _standalone_scenario_label_turn(
    tokens: tuple[Token, ...], scenario_scopes: tuple[TruthScopeRecord, ...],
) -> bool:
    """Admit only compact noun-like label replies into the RP discourse frame."""
    words = _words(tokens)
    if not 1 <= len(words) <= 4:
        return False
    if _named_scenario_match(words, scenario_scopes) is not None:
        return True
    grammar_words = {
        "again", "am", "are", "arrive", "came", "come", "did", "do", "does", "get", "go", "got",
        "has", "have", "i", "im", "is", "kill", "made", "mean", "misheard", "my", "said", "so",
        "something", "that", "there", "they", "this", "to", "want", "was", "we", "were", "you",
    } | _ROLE_WORDS | _FICTION_DISCUSSION | _REALITY_LABEL_WORDS
    return not any(word in grammar_words for word in words)


def _scenario_label_from_tokens(
    text: str,
    tokens: tuple[Token, ...],
    scenario_scopes: tuple[TruthScopeRecord, ...],
    *,
    allow_standalone: bool,
) -> str | None:
    words = _words(tokens)
    named = _named_scenario_match(words, scenario_scopes)
    if named is not None:
        return named.label
    if "mean" in words:
        index = len(words) - 1 - tuple(reversed(words)).index("mean")
        end = len(tokens)
        boundary = re.search(r"[.!?]", text[tokens[index].end:])
        if boundary is not None:
            boundary_cp = tokens[index].end + boundary.start()
            end = next((position for position, token in enumerate(tokens) if token.start >= boundary_cp), end)
        while end > index + 1 and tokens[end - 1].word in {"again", "instead"}:
            end -= 1
        label = _phrase(text, tokens, index + 1, end)
        if label:
            try:
                return normalize_truth_scope_label(label)
            except ValueError:
                return None
    start = None
    for marker in ("in", "on", "at", "as"):
        found = None
        for index, word in enumerate(words):
            if word == marker and index + 1 < len(words):
                found = index + 1
        if found is not None:
            start = found
    if start is None:
        for index, word in enumerate(words):
            if word in {"we", "im"} and index + 1 < len(words):
                raw = tokens[index].raw.casefold().replace("’", "'")
                if raw in {"we're", "i'm"}:
                    start = index + 1
                elif index + 1 < len(words) and words[index + 1] in {"are", "am"}:
                    start = index + 2
    if start is not None:
        if tokens[start].word in _ROLE_WORDS:
            start += 1
        elif start + 1 < len(tokens) and tokens[start].word == "role" and tokens[start + 1].word == "play":
            start += 2
        while start < len(tokens) and tokens[start].word in {"as", "at", "in", "on", "that"}:
            start += 1
        label = _phrase(text, tokens, start)
        if label and not (_topic_words(label) & _FICTION_DISCUSSION):
            try:
                return normalize_truth_scope_label(label)
            except ValueError:
                return None
    if not allow_standalone or not 1 <= len(tokens) <= 4:
        return None
    blocked = _ROLE_WORDS | _FICTION_DISCUSSION | {
        "back", "do", "fine", "good", "home", "life", "lets", "mean", "not", "play", "pretend",
        "real", "reality", "role", "scenario", "world",
        "sounds", "sure", "thanks", "what", "why", "yes",
    }
    if any(word in blocked for word in words):
        return None
    label = _phrase(text, tokens, 0)
    if label:
        try:
            return normalize_truth_scope_label(label)
        except ValueError:
            return None
    return None


def _recent_scenario_frame(
    recent_user_turns: tuple[RecentUserTurn, ...],
    scenario_scopes: tuple[TruthScopeRecord, ...],
) -> tuple[bool, str | None, bool]:
    enactment = False
    labels: list[str] = []
    correction = False
    for turn in recent_user_turns:
        tokens = _trim_fillers(_tokens(turn.content))
        words = _words(tokens)
        if _is_question_or_report(turn.content, words):
            continue
        turn_enactment = _scenario_enactment(words)
        turn_correction = _scenario_correction(words)
        enactment = enactment or turn_enactment
        label = None
        if turn_enactment or turn_correction or _standalone_scenario_label_turn(tokens, scenario_scopes):
            label = _scenario_label_from_tokens(
                turn.content, tokens, scenario_scopes,
                allow_standalone=not turn_enactment,
            )
        if label is not None:
            labels.append(label)
        correction = correction or turn_correction
    unique = tuple(dict.fromkeys(item.casefold() for item in labels))
    if correction and labels:
        return enactment, labels[-1], True
    return enactment, labels[-1] if len(unique) == 1 else None, False


def _scenario_intent(
    text: str,
    tokens: tuple[Token, ...],
    active_scope: TruthScopeRecord,
    scenario_scopes: tuple[TruthScopeRecord, ...],
    recent_user_turns: tuple[RecentUserTurn, ...],
) -> BoundedContinuityIntent | None:
    words = _words(tokens)
    if not words:
        return None
    conventional_suggestion = words[:3] == ("why", "not", "we")
    if any(word in {"not", "never"} for word in words) and not conventional_suggestion:
        return None

    reality_target = (
        _contains(words, ("real", "life"))
        or _contains(words, ("real", "world"))
        or "reality" in words
    )
    exit_action = any(word in {"back", "return", "leave", "stop", "end", "quit", "done"} for word in words)
    role_target = any(word in _ROLE_WORDS for word in words)
    current_target = "this" in words and role_target
    label_target = active_scope.kind == "scenario" and _scenario_label_match(words, active_scope)
    if active_scope.kind == "scenario" and exit_action and (reality_target or role_target or current_target or label_target):
        return BoundedContinuityIntent("exit_scenario", target_id=active_scope.truth_scope_id)

    role_cue = any(word in _ROLE_WORDS for word in words) or _contains(words, ("role", "play"))
    resume_action = any(word in {"resume", "continue", "return", "back", "reenter"} for word in words)
    named = _named_scenario_match(words, scenario_scopes)
    if role_cue and resume_action and named is not None:
        return BoundedContinuityIntent(
            "reenter_scenario", target_id=named.truth_scope_id, target_label=named.label,
        )
    if role_cue and resume_action and named is None:
        candidates = tuple(scope for scope in scenario_scopes if scope.kind == "scenario")
        if len(candidates) == 1:
            return BoundedContinuityIntent(
                "reenter_scenario", target_id=candidates[0].truth_scope_id,
                target_label=candidates[0].label,
            )
        return None

    enactment = _scenario_enactment(words)
    if "game" in words and any(word in {"play", "playing", "video", "tabletop"} for word in words):
        return None
    current_correction = _scenario_correction(words)
    standalone_label = _standalone_scenario_label_turn(tokens, scenario_scopes)
    current_label = None
    if enactment or current_correction or standalone_label:
        current_label = _scenario_label_from_tokens(
            text, tokens, scenario_scopes, allow_standalone=standalone_label,
        )
        if current_label is not None and _words(_tokens(current_label)) in {
            ("that",), ("there",), ("this",), ("that", "one"),
        }:
            current_label = None
    recent_enactment, recent_label, _recent_correction = _recent_scenario_frame(
        recent_user_turns, scenario_scopes,
    )
    confirmation_reference = any(word in {"that", "there"} for word in words) and (
        enactment or _contains(words, ("lets", "do", "that")) or _contains(words, ("do", "that"))
    )
    if enactment and current_label is not None:
        label = current_label
    elif enactment and recent_label is not None:
        label = recent_label
    elif current_label is not None and recent_enactment:
        label = current_label
    elif confirmation_reference and recent_label is not None:
        label = recent_label
    else:
        return None
    return BoundedContinuityIntent("enter_scenario", value=label, target_label=label)


def _activity_category(value: str) -> str | None:
    first = next(iter(_tokens(value)), None)
    return first.word if first and first.word in {"play", "watch", "install", "work", "sleep", "away"} else None


def _activity_intent(
    text: str,
    tokens: tuple[Token, ...],
    current: ActiveStateRecord | None,
) -> BoundedContinuityIntent | None:
    words = _words(tokens)
    if not words:
        return None

    current_category = _activity_category(current.value) if current else None
    back_statement = (
        words in {("im", "back"), ("im", "back", "now"), ("back",), ("back", "now")}
        or _contains(words, ("back", "home"))
    )
    if current is not None and current.value.casefold() in {"away", "working", "sleeping"} and back_statement:
        return BoundedContinuityIntent("return_from_activity", actor="user", target_id=current.state_id)

    lifecycle = {"stop", "quit", "done", "finish", "complete"}
    has_lifecycle = any(word in lifecycle for word in words) or _contains(words, ("no", "longer"))
    action_positions = [(index, word) for index, word in enumerate(words) if word in {"play", "watch", "install", "work", "sleep"}]
    if current is not None and has_lifecycle and action_positions:
        action_index, action = action_positions[-1]
        if action == current_category:
            target = _topic_words(_phrase(text, tokens, action_index + 1) or "")
            current_topic = _topic_words(current.value)
            generic = target <= {"it", "movie", "show", "game", "that"}
            if not target or generic or bool(target & current_topic):
                return BoundedContinuityIntent("clear_activity", actor="user", target_id=current.state_id)
    if has_lifecycle:
        return None

    if any(word in {"was", "were", "yesterday", "ago", "used"} for word in words):
        return None
    if any(word in {"not", "never"} for word in words):
        return None
    if any(word in {"later", "tomorrow"} for word in words) or _contains(words, ("next", "week")):
        return None
    if any(word in {"plan", "intend", "might", "probably", "think"} for word in words):
        return None
    if any(word in {"died", "won", "lost", "respawned"} for word in words):
        return None
    immediate_subject = (
        words[0] in {"i", "im", "going"}
        or _contains(words[:4], ("i", "am"))
        or _contains(words[:4], ("i", "will"))
    )
    if not immediate_subject:
        return None
    first_person_sleep = (
        _contains(words[:5], ("i", "go", "to", "sleep"))
        or _contains(words[:6], ("i", "am", "going", "to", "sleep"))
        or _contains(words[:5], ("i", "will", "sleep"))
        or _contains(words[:5], ("i", "am", "sleep"))
        or words[:2] == ("im", "sleep")
        or words[:4] == ("im", "going", "to", "sleep")
        or words[:3] == ("going", "to", "sleep")
    )
    first_person_bed = (
        _contains(words[:6], ("i", "am", "going", "to", "bed"))
        or _contains(words[:6], ("i", "go", "to", "bed"))
        or _contains(words[:6], ("i", "head", "to", "bed"))
        or (words[:4] == ("im", "going", "to", "bed"))
        or (words[:4] == ("im", "head", "to", "bed"))
        or words[:3] == ("going", "to", "bed")
    )
    if first_person_bed or first_person_sleep:
        return BoundedContinuityIntent(
            "change_activity" if current else "start_activity", actor="user", value="sleeping",
        )
    if (_contains(words, ("head", "out")) or _contains(words, ("go", "out"))
            or _contains(words, ("going", "out"))):
        return BoundedContinuityIntent(
            "change_activity" if current else "start_activity", actor="user", value="away",
        )
    for action, progressive in (("play", "playing"), ("watch", "watching"), ("install", "installing")):
        if action not in words:
            continue
        index = words.index(action)
        raw_action = tokens[index].raw.casefold().replace("’", "'")
        immediate_form = (
            raw_action in {"playing", "watching", "installing"}
            or "going" in words
            or "will" in words
            or "off" in words
            or _contains(words, ("about", "to"))
        )
        if not immediate_form:
            continue
        target = _phrase(text, tokens, index + 1)
        if target:
            return BoundedContinuityIntent(
                "change_activity" if current else "start_activity",
                actor="user",
                value=f"{progressive} {target}",
            )
    if any(token.raw.casefold() == "working" for token in tokens):
        return BoundedContinuityIntent(
            "change_activity" if current else "start_activity", actor="user", value="working",
        )
    return None


def _thread_lifecycle_intent(
    intent: str,
    operation: str,
    group: LogicalThreadGroup,
) -> BoundedContinuityIntent | None:
    identifiers = lifecycle_target_ids(group, operation)
    if not identifiers:
        return None
    return BoundedContinuityIntent(
        intent, target_id=group.representative.thread_id, target_ids=identifiers,
    )


def _thread_intent(
    text: str,
    tokens: tuple[Token, ...],
    groups: tuple[LogicalThreadGroup, ...],
    recent_user_turns: tuple[RecentUserTurn, ...],
) -> BoundedContinuityIntent | None:
    words = _words(tokens)
    if not words:
        return None

    correction = "mean" in words and any(word in {"no", "actually"} for word in words)
    if correction:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns,
            subject_text=text, event_family="any",
        )
        if decision.group is None:
            decision = resolve_thread_reference(
                groups=groups, recent_user_turns=recent_user_turns, event_family="any",
            )
        if decision.group is not None:
            return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", decision.group)
        return None

    negated = any(word in {"not", "never"} for word in words)
    explicit_cancellation = (
        "not" in words and "anymore" in words
        and any(word in {"do", "doing", "plan", "install"} for word in words)
    )
    negative_arrival = negated and (
        any(word in {"arrive", "come", "deliver", "receive"} for word in words)
        or _contains(words, ("show", "up"))
    )
    if negative_arrival:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns, kinds={"waiting"},
            subject_text=text, event_family="receipt",
        )
        if decision.group is not None:
            return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", decision.group)
        return None
    if negated and not explicit_cancellation:
        return None

    arrival = (
        any(word in {"arrive", "come", "deliver", "receive"} for word in words)
        or _contains(words, ("show", "up"))
        or ("get" in words and bool(words) and words[0] in {"i", "im", "get"})
    )
    if arrival:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns, kinds={"waiting"},
            subject_text=text, event_family="receipt",
        )
        if decision.group is not None:
            return _thread_lifecycle_intent("resolve_thread", "resolve", decision.group)
        return None

    cancellation = any(word in {"forget", "cancel", "drop"} for word in words) or explicit_cancellation
    if cancellation:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns,
            subject_text=text, event_family="any",
        )
        if decision.group is not None:
            return _thread_lifecycle_intent("cancel_thread", "cancel", decision.group)
        return None

    completion = any(word in {"finish", "complete"} for word in words) or _contains(words, ("set", "up"))
    if completion:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns,
            subject_text=text, event_family="any",
        )
        if decision.group is not None:
            return _thread_lifecycle_intent("resolve_thread", "resolve", decision.group)

    compact = tuple(word for word in words if word not in _FILLERS)
    if compact in {("nothing",), ("nothing", "yet"), ("still", "nothing"), ("no", "sign", "yet")}:
        decision = resolve_thread_reference(
            groups=groups, recent_user_turns=recent_user_turns, kinds={"waiting"},
            event_family="waiting_unspecified",
        )
        if decision.group is not None:
            return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", decision.group)

    wait_index = next((index for index, word in enumerate(words) if word in {"wait", "await"}), None)
    if wait_index is not None and "not" not in words:
        if wait_index + 1 < len(words) and words[wait_index + 1] == "up":
            return None
        target_start = wait_index + 1
        while target_start < len(words) and words[target_start] in {"for", "on", "the", "that", "my"}:
            target_start += 1
        target_value = _phrase(text, tokens, target_start)
        if target_value:
            description = f"waiting for {target_value}"
            decision = match_open_thread(
                description, kind="waiting", participant_scope="user", groups=groups,
            )
            if decision.group is not None:
                return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", decision.group)
            if decision.reason == "multiple_logical_identities":
                return None
            if not subject_identity(description, "waiting").key:
                reference = resolve_thread_reference(
                    groups=groups, recent_user_turns=recent_user_turns, kinds={"waiting"},
                    event_family="waiting_unspecified",
                )
                if reference.group is not None:
                    return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", reference.group)
                return None
            return BoundedContinuityIntent(
                "open_waiting_thread", value=description,
                kind="waiting", participant_scope="user",
            )

    shared_plan = _contains(words, ("we", "should"))
    user_subject = bool(words and words[0] in {"i", "im"}) or _contains(words[:4], ("i", "am"))
    to_index = _find(words, ("to",))
    on_index = _find(words, ("on",))
    about_index = _find(words, ("about",))
    raw_words = {token.raw.casefold().replace("’", "'") for token in tokens}
    plan_to = user_subject and to_index is not None and any(
        word in {"probably", "plan", "intend", "might", "decided"} for word in words
    )
    planning_on = user_subject and on_index is not None and "planning" in raw_words
    thinking_about = user_subject and about_index is not None and "thinking" in raw_words
    if plan_to or planning_on or thinking_about or shared_plan:
        if shared_plan:
            marker = _find(words, ("should",))
        elif thinking_about:
            marker = about_index
        elif planning_on:
            marker = on_index
        else:
            marker = to_index
        start = marker + 1 if marker is not None else None
        value = _phrase(text, tokens, start) if start is not None else None
        if value:
            participant = "shared" if shared_plan else "user"
            decision = match_open_thread(
                value, kind="plan_or_intention", participant_scope=participant, groups=groups,
            )
            if decision.group is not None:
                return _thread_lifecycle_intent("reconfirm_thread", "reconfirm", decision.group)
            if decision.reason == "multiple_logical_identities":
                return None
            return BoundedContinuityIntent(
                "open_plan_thread", value=value, kind="plan_or_intention",
                participant_scope=participant,
            )
    return None


def interpret_continuity_intents(
    content: str,
    *,
    active_scope: TruthScopeRecord,
    scenario_scopes: tuple[TruthScopeRecord, ...],
    current_activity: ActiveStateRecord | None,
    current_threads: tuple[OpenThreadRecord, ...],
    thread_evidence: Mapping[str, Iterable[str]] | None = None,
    recent_user_turns: tuple[RecentUserTurn, ...] = (),
) -> IntentDecision:
    """Return only high-confidence intents from bounded current-state context."""
    tokens = _trim_fillers(_tokens(content))
    words = _words(tokens)
    if not tokens:
        return IntentDecision((), "empty")
    if _is_question_or_report(content, words):
        return IntentDecision((), "question_hypothetical_or_quotation")

    scenario = _scenario_intent(
        content, tokens, active_scope, scenario_scopes, recent_user_turns,
    )
    if scenario is not None:
        return IntentDecision((scenario,), "high_confidence_scenario")

    activity = _activity_intent(content, tokens, current_activity)
    groups = build_logical_thread_groups(current_threads, thread_evidence)
    thread = _thread_intent(content, tokens, groups, recent_user_turns)
    intents = tuple(item for item in (activity, thread) if item is not None)
    if intents:
        return IntentDecision(intents, "high_confidence_current_state")
    return IntentDecision((), "insufficient_intent_or_referent")
