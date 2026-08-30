"""Provider-neutral current-turn temporal facts from canonical conversation data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import os
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence
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
class TemporalContextFacts:
    current_local_datetime: datetime
    previous_user_interaction_at: datetime | None
    elapsed_since_previous_user_interaction: timedelta | None
    user_interaction_before_previous_at: datetime | None = None
    elapsed_gap_ending_at_previous_user_interaction: timedelta | None = None


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


def _current_user_index(
    messages: Sequence[Mapping[str, object]],
    current_user_message: object,
) -> int | None:
    expected_content = str(current_user_message)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "user" and str(message.get("content", "")) == expected_content:
            return index
    return None


def derive_temporal_context_facts(
    messages: Sequence[Mapping[str, object]],
    current_user_message: object,
    *,
    clock: Clock | None = None,
) -> TemporalContextFacts:
    """Derive current time and the wall-clock gap before this user turn."""
    current = clock_local_datetime(clock)
    current_index = _current_user_index(messages, current_user_message)
    if current_index is None:
        return TemporalContextFacts(current, None, None)

    previous_index = next(
        (
            index for index in range(current_index - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        None,
    )
    if previous_index is None:
        return TemporalContextFacts(current, None, None)
    previous_message = messages[previous_index]

    previous = parse_conversation_timestamp(
        previous_message.get("timestamp"), current.tzinfo or timezone.utc,
        reject_ambiguous_legacy=True,
    )
    if previous is None:
        return TemporalContextFacts(current, None, None)

    elapsed = _safe_elapsed(current, previous)

    interaction_before_previous = None
    elapsed_ending_at_previous = None
    if (
        elapsed is not None
        and _is_explicit_return_utterance(previous_message.get("content"))
        and _is_explicit_absence_duration_followup(current_user_message)
    ):
        interaction_before_previous_message = next(
            (
                messages[index]
                for index in range(previous_index - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if interaction_before_previous_message is not None:
            interaction_before_previous = parse_conversation_timestamp(
                interaction_before_previous_message.get("timestamp"),
                current.tzinfo or timezone.utc,
                reject_ambiguous_legacy=True,
            )
            if interaction_before_previous is not None:
                elapsed_ending_at_previous = _safe_elapsed(
                    previous, interaction_before_previous,
                )

    return TemporalContextFacts(
        current,
        previous,
        elapsed,
        interaction_before_previous,
        elapsed_ending_at_previous,
    )


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
    lines.extend([
        "Use these facts only when naturally relevant; do not mention time or the gap mechanically.",
        "[End current turn temporal facts]",
    ])
    return "\n".join(lines)
