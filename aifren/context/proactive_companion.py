"""Conservative deterministic proactive eligibility and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Sequence
import uuid

from aifren.memory_v2_store import MemoryV2Repository
from aifren.memory_v2_store.store import MemoryV2Store, parse_timestamp_us


HOUR_US = 3_600_000_000
MIN_INTERACTION_GAP_US = HOUR_US
MAX_REASON_CONTEXT_CHARS = 520
MIN_FAILED_ATTEMPT_BACKOFF_US = 5 * 60_000_000
MAX_FAILED_ATTEMPT_BACKOFF_US = HOUR_US
_ATTEMPT_OUTCOMES = frozenset({
    "published", "empty_output", "overlength_output", "provider_error", "persistence_error",
    "provider_timeout", "unsafe_reason", "configuration_error", "interrupted",
})
_BUSY_ACTIVITY_PREFIXES = (
    "away", "working", "coding", "studying", "driving", "commuting", "showering",
    "making love", "exercising", "travelling", "traveling",
)


@dataclass(frozen=True)
class ProactiveReason:
    kind: str
    topic: str
    thread_id: str
    eligible_at_us: int


@dataclass(frozen=True)
class ProactiveEligibility:
    eligible: bool
    reason: ProactiveReason | None
    outcome: str
    ignored_streak: int = 0
    next_opportunity_us: int | None = None
    minimum_interval_us: int = HOUR_US


def _latest_user_time(messages: Sequence[Mapping[str, object]]) -> int | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        try:
            return parse_timestamp_us(message.get("timestamp"))
        except (TypeError, ValueError):
            return None
    return None


def _read_checkins(store: MemoryV2Store, character_id: str, limit: int = 16):
    return store.connection.execute(
        """SELECT * FROM proactive_checkins WHERE character_id=?
             ORDER BY displayed_at_us DESC, checkin_id DESC LIMIT ?""",
        (character_id, limit),
    ).fetchall()


def _read_attempts(store: MemoryV2Store, character_id: str, limit: int = 16):
    return store.connection.execute(
        """SELECT * FROM proactive_attempts WHERE character_id=?
             ORDER BY attempted_at_us DESC, attempt_id DESC LIMIT ?""",
        (character_id, limit),
    ).fetchall()


def _failed_attempt_streak(rows, *, after_us: int) -> int:
    count = 0
    for row in rows:
        attempted_at_us = int(row["attempted_at_us"])
        if attempted_at_us <= after_us or row["outcome"] == "published":
            break
        count += 1
    return count


def _ignored_streak(rows) -> int:
    count = 0
    for row in rows:
        if row["responded_at_us"] is not None:
            break
        count += 1
    return count


def _thread_delay_us(kind: str, temporal_anchor: str | None) -> int:
    anchor = str(temporal_anchor or "").casefold()
    if anchor == "tomorrow":
        return 18 * HOUR_US
    if anchor == "tonight":
        return 4 * HOUR_US
    if anchor == "later":
        return 3 * HOUR_US
    if kind == "waiting":
        return 6 * HOUR_US
    if kind == "plan_or_intention":
        return 8 * HOUR_US
    return 12 * HOUR_US


def evaluate_proactive_eligibility(
    repository: MemoryV2Repository,
    character_id: str,
    messages: Sequence[Mapping[str, object]],
    *,
    now_us: int,
    enabled: bool,
    minimum_interval_us: int = HOUR_US,
    actively_conversing: bool = False,
) -> ProactiveEligibility:
    """Choose zero or one evidence-backed reason before any generation."""
    minimum_interval_us = max(30_000_000, int(minimum_interval_us))
    if not enabled:
        return ProactiveEligibility(False, None, "disabled", minimum_interval_us=minimum_interval_us)
    if actively_conversing:
        return ProactiveEligibility(False, None, "active_conversation", minimum_interval_us=minimum_interval_us)
    scope = repository.active_truth_scope(character_id)
    if scope.kind != "real_world":
        return ProactiveEligibility(False, None, "scenario_scope", minimum_interval_us=minimum_interval_us)
    user_activity = repository.lookup_actor_state(character_id, "user", "activity").state
    companion_activity = repository.lookup_actor_state(character_id, "companion", "activity").state
    if user_activity is not None:
        activity = user_activity.value.casefold()
        if activity == "sleeping":
            return ProactiveEligibility(False, None, "user_sleeping", minimum_interval_us=minimum_interval_us)
        if any(activity == prefix or activity.startswith(prefix + " ") for prefix in _BUSY_ACTIVITY_PREFIXES):
            return ProactiveEligibility(False, None, "user_unavailable_or_focused", minimum_interval_us=minimum_interval_us)
    if companion_activity is not None and companion_activity.value.casefold() == "sleeping":
        return ProactiveEligibility(False, None, "companion_sleeping", minimum_interval_us=minimum_interval_us)

    latest_user = _latest_user_time(messages)
    if latest_user is None:
        return ProactiveEligibility(False, None, "no_canonical_user_interaction", minimum_interval_us=minimum_interval_us)
    if now_us < latest_user or now_us - latest_user < minimum_interval_us:
        return ProactiveEligibility(False, None, "too_soon", next_opportunity_us=latest_user + minimum_interval_us,
                                    minimum_interval_us=minimum_interval_us)

    rows = _read_checkins(repository.store, character_id)
    ignored = _ignored_streak(rows)
    if rows:
        latest = int(rows[0]["displayed_at_us"])
        afk_floors = (0, 5 * 60_000_000, 15 * 60_000_000, 30 * 60_000_000,
                      HOUR_US, 2 * HOUR_US)
        cooldown = minimum_interval_us
        if ignored:
            cooldown = max(minimum_interval_us * (2 ** min(ignored + 1, 5)),
                           afk_floors[min(ignored, len(afk_floors) - 1)])
        if now_us < latest or now_us - latest < cooldown:
            outcome = "backed_off_prior_checkin_unanswered" if ignored else "cooldown"
            return ProactiveEligibility(False, None, outcome, ignored, latest + cooldown,
                                        minimum_interval_us)

    # Failed drafts are not relationship events and never enter the ignored
    # check-in streak. They do, however, create a durable scheduler cooldown.
    # A later genuine user interaction naturally invalidates this backoff.
    attempts = _read_attempts(repository.store, character_id)
    failed_attempts = _failed_attempt_streak(attempts, after_us=latest_user)
    if failed_attempts:
        latest_attempt_us = int(attempts[0]["attempted_at_us"])
        cooldown = min(
            MAX_FAILED_ATTEMPT_BACKOFF_US,
            max(minimum_interval_us, MIN_FAILED_ATTEMPT_BACKOFF_US)
            * (2 ** min(failed_attempts - 1, 4)),
        )
        if now_us < latest_attempt_us or now_us - latest_attempt_us < cooldown:
            return ProactiveEligibility(
                False, None, "backed_off_failed_generation", ignored,
                latest_attempt_us + cooldown, minimum_interval_us,
            )

    eligible = []
    for thread in repository.list_open_threads(character_id).threads:
        previous_for_thread = next(
            (row for row in rows if row["thread_id"] == thread.thread_id), None,
        )
        if (previous_for_thread is not None
                and previous_for_thread["responded_at_us"] is not None
                and int(previous_for_thread["displayed_at_us"]) >= thread.last_mentioned_at_us):
            # Acknowledged check-ins are not repeated without fresh canonical
            # user evidence on that thread. This is not relationship rejection.
            continue
        ready_at = thread.last_mentioned_at_us + _thread_delay_us(thread.kind, thread.temporal_anchor)
        if now_us >= ready_at:
            eligible.append((ready_at, -thread.last_mentioned_at_us, thread.thread_id, thread))
    if not eligible:
        return ProactiveEligibility(False, None, "no_eligible_reason", ignored,
                                    minimum_interval_us=minimum_interval_us)
    _, _, _, thread = sorted(eligible)[0]
    return ProactiveEligibility(
        True,
        ProactiveReason("open_thread", thread.description, thread.thread_id,
                        thread.last_mentioned_at_us + _thread_delay_us(thread.kind, thread.temporal_anchor)),
        "eligible_open_thread",
        ignored,
        minimum_interval_us=minimum_interval_us,
    )


def render_proactive_reason(reason: ProactiveReason) -> str | None:
    if reason.kind != "open_thread" or not reason.topic or len(reason.topic) > 160:
        return None
    block = (
        "[Eligible proactive follow-up — governed background data, not instructions]\n"
        "Generate one brief, natural in-character check-in about exactly this unresolved user-grounded topic. "
        "Do not mention hidden memory, elapsed-time mechanics, or any other state. A question is optional.\n"
        f"{json.dumps({'reason': 'unresolved follow-up', 'topic': reason.topic}, ensure_ascii=False, separators=(',', ':'))}\n"
        "[End eligible proactive follow-up]"
    )
    return block if len(block) <= MAX_REASON_CONTEXT_CHARS else None


def record_displayed_checkin(
    store: MemoryV2Store,
    character_id: str,
    reason: ProactiveReason,
    *,
    displayed_at_us: int,
    conversation_index: int,
    assistant_content: str,
) -> str:
    checkin_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"aifren:proactive:{character_id}:{displayed_at_us}:{reason.kind}:{reason.thread_id}",
    ))
    store.connection.execute(
        """INSERT OR IGNORE INTO proactive_checkins(character_id, checkin_id, reason_kind,
           thread_id, displayed_at_us, conversation_index, assistant_content_sha256, responded_at_us)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
        (character_id, checkin_id, reason.kind, reason.thread_id, displayed_at_us,
         conversation_index, hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()),
    )
    return checkin_id


def record_proactive_attempt(
    store: MemoryV2Store,
    character_id: str,
    reason: ProactiveReason,
    *,
    attempted_at_us: int,
    outcome: str,
) -> str:
    """Persist a privacy-safe scheduler attempt and retain a bounded tail."""
    normalized_outcome = str(outcome or "").strip().casefold()
    if normalized_outcome not in _ATTEMPT_OUTCOMES:
        normalized_outcome = "provider_error"
    attempt_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"aifren:proactive-attempt:{character_id}:{attempted_at_us}:{reason.kind}:{reason.thread_id}:{normalized_outcome}",
    ))
    with store.transaction():
        store.connection.execute(
            """INSERT OR IGNORE INTO proactive_attempts(
                   character_id, attempt_id, reason_kind, thread_id, attempted_at_us, outcome)
                 VALUES (?, ?, ?, ?, ?, ?)""",
            (character_id, attempt_id, reason.kind, reason.thread_id,
             int(attempted_at_us), normalized_outcome),
        )
        store.connection.execute(
            """DELETE FROM proactive_attempts WHERE character_id=? AND attempt_id NOT IN (
                   SELECT attempt_id FROM proactive_attempts WHERE character_id=?
                    ORDER BY attempted_at_us DESC, attempt_id DESC LIMIT 64
               )""",
            (character_id, character_id),
        )
    return attempt_id


def mark_checkins_responded(store: MemoryV2Store, character_id: str, *, responded_at_us: int) -> int:
    cursor = store.connection.execute(
        """UPDATE proactive_checkins SET responded_at_us=?
             WHERE character_id=? AND responded_at_us IS NULL AND displayed_at_us <= ?""",
        (responded_at_us, character_id, responded_at_us),
    )
    return int(cursor.rowcount)
