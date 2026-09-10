"""Provider-neutral current-turn temporal facts from canonical conversation data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import os
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence

from conversation.truth_scope import parse_canonical_truth_scope, active_scope_from_provenance
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Clock = Callable[[], datetime]


def conversation_local_timezone() -> tzinfo:
    """Return the rule-aware process-local timezone when it can be identified."""
    candidates: list[str] = []
    configured = os.environ.get("TZ", "").strip().removeprefix(":")
    if configured and not configured.startswith("/"):
        candidates.append(configured)

    try:
        resolved = str(Path("/etc/localtime").resolve())
        marker = "/zoneinfo/"
        if marker in resolved:
            candidates.append(resolved.split(marker, 1)[1])
    except OSError:
        pass

    try:
        configured_file = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if configured_file:
            candidates.append(configured_file)
    except OSError:
        pass

    for candidate in dict.fromkeys(candidates):
        try:
            return ZoneInfo(candidate)
        except (ValueError, ZoneInfoNotFoundError):
            continue

    try:
        with Path("/etc/localtime").open("rb") as localtime_file:
            return ZoneInfo.from_file(localtime_file, key="local")
    except (OSError, ValueError):
        return datetime.now().astimezone().tzinfo or timezone.utc


def current_local_datetime() -> datetime:
    """Read an aware local datetime from the system clock."""
    return datetime.now(timezone.utc).astimezone(conversation_local_timezone())


def clock_local_datetime(clock: Clock | None = None) -> datetime:
    """Read and normalize an injectable clock, falling back to the real clock."""
    selected_clock = clock or current_local_datetime
    try:
        value = selected_clock()
    except Exception:
        value = current_local_datetime()
    if not isinstance(value, datetime):
        value = current_local_datetime()
    if value.tzinfo is None:
        return value.replace(tzinfo=conversation_local_timezone())
    return value


def parse_conversation_timestamp(
    value: object,
    local_timezone: tzinfo,
    *,
    reject_ambiguous_legacy: bool = False,
) -> datetime | None:
    """Parse canonical ISO timestamps with optional legacy-DST abstention."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is not None:
        try:
            return parsed.astimezone(local_timezone)
        except (OverflowError, ValueError):
            return None

    # Old canonical records used naive local timestamps. Most are safe to
    # interpret in the conversation timezone, but a DST fold or gap has two
    # possible offsets. Abstain instead of inventing an elapsed duration.
    first = parsed.replace(tzinfo=local_timezone, fold=0)
    second = parsed.replace(tzinfo=local_timezone, fold=1)
    if reject_ambiguous_legacy and first.utcoffset() != second.utcoffset():
        return None
    return first


@dataclass(frozen=True)
class ReturnOpportunity:
    departure_index: int
    return_index: int
    elapsed: timedelta
    departure_kind: str = ""


@dataclass(frozen=True)
class TemporalContextFacts:
    current_local_datetime: datetime
    previous_user_interaction_at: datetime | None
    elapsed_since_previous_user_interaction: timedelta | None
    user_interaction_before_previous_at: datetime | None = None
    elapsed_gap_ending_at_previous_user_interaction: timedelta | None = None
    return_opportunity: ReturnOpportunity | None = None
    reported_activity_durations: tuple[str, ...] = ()


_RETURN_UTTERANCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^(?:i(?:'m| am)|we(?:'re| are)) (?:finally |just )?back(?: home| now| again)?$",
        r"^(?:i|we) (?:just )?(?:got|came|made it) back(?: home| now| again)?$",
        r"^back(?: home| now| again)?$",
        r"^i(?:'m| am) home(?: now| again)?$",
    )
)

_EXPLICIT_ABSENCE_DURATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        (
            r"^(?:(?:can|could) you tell me |do you know )?(?:about )?"
            r"how long (?:(?:have|had) i|i (?:have|had)) been (?:gone|away|absent)$"
        ),
        (
            r"^(?:(?:can|could) you tell me |do you know )?(?:about )?"
            r"how long was i (?:gone|away|absent)$"
        ),
        (
            r"^(?:(?:can|could) you tell me |do you know )?(?:about )?"
            r"how long (?:has|had) it been since i (?:was|have been) (?:last )?here$"
        ),
        r"^(?:about )?how long since i was (?:last )?here$",
        (
            r"^(?:(?:can|could) you tell me |do you know )?(?:about )?"
            r"how much time (?:was i|have i been) (?:gone|away|absent)$"
        ),
    )
)


def _normalized_intent_text(value: object) -> str:
    text = str(value or "").casefold().replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"[^a-z0-9']+", " ", text).strip()


def _matches_any(value: object, patterns: Sequence[re.Pattern[str]]) -> bool:
    normalized = _normalized_intent_text(value)
    return bool(normalized) and any(pattern.fullmatch(normalized) for pattern in patterns)


def _is_explicit_return_utterance(value: object) -> bool:
    return _matches_any(value, _RETURN_UTTERANCE_PATTERNS)


def _is_explicit_absence_duration_followup(value: object) -> bool:
    return _matches_any(value, _EXPLICIT_ABSENCE_DURATION_PATTERNS)


def _safe_elapsed(later: datetime, earlier: datetime) -> timedelta | None:
    try:
        elapsed = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None
    return elapsed if elapsed >= timedelta(0) else None


def _ordinary_record(message: Mapping[str, object], role: str) -> bool:
    # Canonical ordinary input has no generated origin or accessibility marker.
    # Unknown metadata fails closed; role/content matching alone grants nothing.
    return bool(isinstance(message, Mapping) and message.get("role") == role
        and isinstance(message.get("content"), str)
        and set(message) <= {"role", "content", "timestamp", "truth_scope"}
        and parse_canonical_truth_scope(message).is_valid)


def _current_user_index(messages, current_user_message):
    # Only the actual appended turn, never an older equal string.
    index = len(messages) - 1
    if index >= 0 and _ordinary_record(messages[index], "user") and messages[index].get("content") == current_user_message:
        return index
    return None


def _departure_kind(message):
    # Reuse the existing source-evidenced activity interpreter, not a new intent grammar.
    from current_continuity import extract_active_state_proposal
    content = str(message.get("content", ""))
    if len(content) > 240:
        return ""
    proposal = extract_active_state_proposal(content, current_activity=None)
    updates = getattr(proposal, "updates", ())
    if len(updates) == 1 and updates[0].operation == "set":
        return {"sleeping": "bedtime", "away": "departure", "working": "work departure"}.get(updates[0].value, "")
    return ""


def derive_temporal_context_facts(
    messages: Sequence[Mapping[str, object]], current_user_message: object, *,
    clock: Clock | None = None,
    reply_is_human_owned: Callable[[int, Mapping[str, object]], bool] | None = None,
    active_truth_scope: object = None,
) -> TemporalContextFacts:
    """One source-derived projection; no clock/absence state is persisted.

    A two-hour gap offers a return beat. Up to eight adjacent uncompleted human
    attempts may carry it forward; committed ordinary replies consume it. Scene
    events, inaccessible input and foreign scopes cannot supply either endpoint.
    """
    current = clock_local_datetime(clock)
    current_index = _current_user_index(messages, current_user_message)
    if current_index is None:
        return TemporalContextFacts(current, None, None)
    scope = parse_canonical_truth_scope(messages[current_index])
    active_scope = active_scope_from_provenance(active_truth_scope)
    if active_scope is not None and active_scope.identity != scope.identity:
        return TemporalContextFacts(current, None, None)
    def same_scope(message):
        return parse_canonical_truth_scope(message).identity == scope.identity
    def previous_human(before):
        for index in range(before - 1, -1, -1):
            item = messages[index]
            if not _ordinary_record(item, "user"):
                if isinstance(item, Mapping) and item.get("role") == "user":
                    from scene_ui_event import valid_scene_ui_origin
                    if (not valid_scene_ui_origin(item.get("origin")) and item.get("semantic_admission") !=
                            {"channel": "hearing", "state": "unavailable", "understood": False}):
                        return None  # Unknown user provenance is an interval boundary.
                continue
            # Crossing a scope boundary is not absence within this scene.
            return index if same_scope(item) else None
        return None
    def stamp(index):
        return parse_conversation_timestamp(messages[index].get("timestamp"), current.tzinfo or timezone.utc,
                                            reject_ambiguous_legacy=True)
    previous_index = previous_human(current_index)
    if previous_index is None or (previous := stamp(previous_index)) is None:
        return TemporalContextFacts(current, None, None)
    elapsed = _safe_elapsed(current, previous)
    before_previous = None
    adjacent_gap = None
    if elapsed is not None and _is_explicit_return_utterance(messages[previous_index].get("content")) and _is_explicit_absence_duration_followup(current_user_message):
        index = previous_human(previous_index)
        if index is not None and (before_previous := stamp(index)) is not None:
            adjacent_gap = _safe_elapsed(previous, before_previous)

    opportunity = None
    candidate, earlier, end = current_index, previous_index, current
    for _ in range(8):
        start = stamp(earlier)
        gap = _safe_elapsed(end, start) if start is not None else None
        if gap is None: break
        if gap >= timedelta(hours=2):
            opportunity = ReturnOpportunity(earlier, candidate, gap, _departure_kind(messages[earlier]))
            break
        # A completed scoped ordinary exchange, not a draft or a standalone
        # assistant/proactive event, consumes that earlier return opportunity.
        reply_index = earlier + 1
        if reply_index < candidate:
            reply = messages[reply_index]
            if (_ordinary_record(reply, "assistant") and same_scope(reply)
                    and (reply_is_human_owned is None or reply_is_human_owned(reply_index, reply))):
                break
        candidate, end = earlier, start
        earlier = previous_human(candidate)
        if earlier is None: break
    reported = ()
    text = str(current_user_message)
    if not any(mark in text for mark in ('?', '"', '“', '”', '`')):
        reported = tuple(_activity_duration_claims(text, speaker="i"))
    return TemporalContextFacts(current, previous, elapsed, before_previous, adjacent_gap, opportunity, reported)


def _elapsed_text(elapsed: timedelta) -> str:
    seconds = max(0, int(elapsed.total_seconds()))
    if seconds < 60:
        return "less than 1 minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        suffix = f" {remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}" if remaining_minutes else ""
        return f"{hours} hour{'s' if hours != 1 else ''}{suffix}"
    days, remaining_hours = divmod(hours, 24)
    suffix = f" {remaining_hours} hour{'s' if remaining_hours != 1 else ''}" if remaining_hours else ""
    return f"{days} day{'s' if days != 1 else ''}{suffix}"


def temporal_response_requirement(facts: TemporalContextFacts, query: object):
    """Compose with the existing direct-answer owner; no new time classifier."""
    from response_requirements import ResponseRequirement, RequiredFact
    if _is_explicit_absence_duration_followup(query):
        elapsed = (facts.elapsed_gap_ending_at_previous_user_interaction
                   if facts.elapsed_gap_ending_at_previous_user_interaction is not None
                   else facts.elapsed_since_previous_user_interaction)
        if elapsed is None:
            dialogue = "I can't reliably tell how long it's been since our last interaction."
            return ResponseRequirement("interaction_elapsed_unknown", (), dialogue,
                "The canonical interaction interval is unavailable. Say that you cannot reliably tell; do not invent a duration.")
        value = _elapsed_text(elapsed)
        # Allow natural small-number spellings as well as backend digits.
        words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
        verbal = re.sub(r"\b\d+\b", lambda m: words[int(m[0])] if int(m[0]) < len(words) else m[0], value)
        dialogue = f"It's been about {value} since we last interacted. That interval doesn't tell me what you were doing."
        return ResponseRequirement("interaction_elapsed", (RequiredFact("interaction_gap", value, (value, verbal)),), dialogue,
            f"Communicate the supported interaction interval: {value}. This measures time between interactions, not time asleep, physically absent, working or travelling.")
    if facts.return_opportunity is not None:
        return ResponseRequirement("return_continuity", (), "Welcome back.",
            "Acknowledge the resumed interaction naturally, without inventing off-screen activity or its duration.", mode="must_respect")
    return None


def _activity_duration_claims(dialogue: object, *, speaker="you"):
    # Bounded assertion check: an interaction interval never licenses an activity
    # duration. Questions such as 'How did you sleep?' remain ordinary prose.
    pattern = (
        r"\b" + speaker + r"\s+(?P<activity>slept|(?:were|was)\s+(?:asleep|sleeping|working|travelling|traveling|away|gone)|worked|travelled|traveled)\s+"
        r"(?:(?:for|about|around|approximately|a\s+full|the\s+(?:whole|entire))\s+)*"
        r"(?P<amount>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(?P<unit>minutes?|hours?|days?)\b")
    for match in re.finditer(pattern, str(dialogue), re.IGNORECASE):
        activity = match['activity'].casefold()
        family = ('work' if 'work' in activity else 'travel' if 'travel' in activity
                  else 'away' if 'away' in activity or 'gone' in activity else 'sleep')
        words = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty').split()
        amount = match['amount'].casefold()
        if amount in words: amount = str(words.index(amount))
        yield family + ':' + amount + ':' + match['unit'].casefold().rstrip('s')


def temporal_activity_duration_invented(facts: TemporalContextFacts, dialogue: object) -> bool:
    return any(claim not in facts.reported_activity_durations for claim in _activity_duration_claims(dialogue))


def build_temporal_context_block(facts: TemporalContextFacts) -> str:
    """Render compact factual model context without prescribing dialogue."""
    current = facts.current_local_datetime
    timezone_name = getattr(current.tzinfo, "key", None) or current.tzname() or "local"
    lines = [
        "[Current turn temporal facts]",
        (
            "Current local datetime: "
            f"{current.isoformat(timespec='seconds')} "
            f"({current.strftime('%A')}; {timezone_name})."
        ),
    ]
    if facts.elapsed_since_previous_user_interaction is not None:
        lines.append(
            (
                "Elapsed since previous canonical user interaction: "
                f"{_elapsed_text(facts.elapsed_since_previous_user_interaction)}."
            )
        )
    if facts.elapsed_gap_ending_at_previous_user_interaction is not None:
        lines.append(
            (
                "Return/absence interaction gap ending at the previous canonical "
                "user interaction: "
                f"{_elapsed_text(facts.elapsed_gap_ending_at_previous_user_interaction)}."
            )
        )
    gap_count = sum((
        facts.elapsed_since_previous_user_interaction is not None,
        facts.elapsed_gap_ending_at_previous_user_interaction is not None,
    ))
    if gap_count:
        lines.append(
            (
                f"{'These elapsed interaction gaps do' if gap_count > 1 else 'This elapsed interaction gap does'} "
                "not establish sleep, work, travel, or any other activity or its duration."
            )
        )
    opportunity = facts.return_opportunity
    if opportunity is not None:
        lines.extend([
            "Return opportunity — first uncompleted human reply after an appreciable interaction gap.",
            f"Supported interval ending at the resumed human message: {_elapsed_text(opportunity.elapsed)}.",
            "Acknowledge their return naturally and briefly in your character's voice. This also applies to a generic greeting after days away; no magic return phrase is needed.",
            "Answer their current request first in importance. For a substantive or urgent request, keep any acknowledgment short and do not add a distracting preamble. Do not recite numeric timing unless useful or asked.",
        ])
        if opportunity.elapsed >= timedelta(days=2):
            lines.append("For this multi-day interval, make the long pause in conversation evident in a brief, natural acknowledgment.")
        if opportunity.departure_kind:
            lines.append(f"The preceding same-scope human message recorded {opportunity.departure_kind} intent, not proof of the activity or its duration.")
        if opportunity.departure_kind == "bedtime":
            lines.append("A brief question about how they slept is appropriate; do not assert that they slept, slept well, or slept for the interaction gap.")
        lines.append("No observation occurred while the app was closed. Current participation does not prove every earlier activity ended. Do not wake the companion, advance scenario time, or clear unrelated scene state.")
    lines.extend([
        "Use these facts only when naturally relevant; do not mention time or the gap mechanically.",
        "[End current turn temporal facts]",
    ])
    return "\n".join(lines)
