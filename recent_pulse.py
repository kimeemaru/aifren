"""Read-only recent salience from existing V2 owners; never answer evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import re

from companion_context import CompanionContextContribution, CompanionContextRequest
from conversation.temporal_context import parse_conversation_timestamp
from memory_v2_episode_compaction import EpisodeCompactionCache
from memory_v2_historical_evidence import resolve_historical_evidence
from memory_v2_store import MemoryV2Repository


WINDOW_DAYS = {"compact": 1, "balanced": 3, "deep": 7}
# The shared validator is retained unchanged. Above these admission-work bounds
# this optional lane abstains; threads can still contribute. No extra retrieval,
# summary rewrite, extra compactor or replacement memory validator is introduced.
MAX_VALIDATION_RECORDS = 4096
MAX_VALIDATION_CHARACTERS = 1_000_000
MAX_EPISODE_ROWS = 64
MAX_EPISODE_CANDIDATES = 8
MAX_ANCHORS_PER_EPISODE = 2
_TERM = re.compile(r"[\w][\w '’/-]{0,47}\Z", re.UNICODE)


@dataclass(frozen=True)
class RecentPulseResult:
    contributions: tuple[CompanionContextContribution, ...]
    diagnostics: dict[str, object]


class RecentPulseBuilder:
    """Uses current repository threads and the existing episode validity verdict.

    Derived episode *key terms* signal previous discussion only. Summary prose,
    factual propositions and state values are never copied into this channel.
    Instances are turn-local; no persistent attention/cache/store is created.
    """
    def __init__(self, writer, conversation, *, window: str = "balanced"):
        if window not in WINDOW_DAYS:
            raise ValueError("unknown recent continuity window")
        self.writer, self.conversation, self.window = writer, conversation, window

    def build(self, request: CompanionContextRequest) -> RecentPulseResult:
        stats = dict(window_days=WINDOW_DAYS[self.window], thread_candidates=0,
                     episode_candidates=0, stale_excluded=0, scope_excluded=0,
                     date_excluded=0, source_excluded=0, state_excluded=0,
                     episode_budget_excluded=0, item_count=0)
        if request.explicit_memory:
            return RecentPulseResult((), dict(stats, suppressed="explicit_memory"))
        if (request.character_id != self.writer.character_id or request.now.tzinfo is None
                or not request.truth_scope_id):
            return RecentPulseResult((), dict(stats, suppressed="identity_or_clock"))
        store, messages = self.writer.store, self.conversation.messages
        repository = MemoryV2Repository(store)
        scope = repository.active_truth_scope(request.character_id)
        if scope.truth_scope_id != request.truth_scope_id:
            return RecentPulseResult((), dict(stats, suppressed="stale_scope"))
        known = {row[0] for row in store.connection.execute(
            "SELECT truth_scope_id FROM truth_scopes WHERE character_id=? LIMIT 65",
            (request.character_id,))}
        if len(known) > 64:
            return RecentPulseResult((), dict(stats, suppressed="scope_budget"))
        source_prefix = self.writer._canonical_source_reference(self.conversation.conversation_file, 0)[:-1]
        source_key = hashlib.sha256(source_prefix[:-1].encode()).hexdigest()
        unresolved = {int(row[0]) for row in store.connection.execute(
            """SELECT next_index FROM canonical_observation_progress
               WHERE character_id=? AND source_key=? AND state IN ('unresolved','failed') LIMIT 17""",
            (request.character_id, source_key))}
        if len(unresolved) > 16:
            return RecentPulseResult((), dict(stats, suppressed="recovery_budget"))
        first_day = request.now.date() - timedelta(days=WINDOW_DAYS[self.window] - 1)
        items = []

        def source(index):
            if index in unresolved:
                stats["source_excluded"] += 1
                return None
            decision = resolve_historical_evidence(messages, index, valid_scope_ids=known)
            evidence = decision.evidence
            if evidence is None or evidence.source_class != "ordinary_conversation":
                stats["source_excluded"] += 1
                return None
            if evidence.truth_scope_id != request.truth_scope_id:
                stats["scope_excluded"] += 1
                return None
            # Both sides must belong to a completed same-scope ordinary exchange.
            partner = index + 1 if evidence.speaker_role == "user" else index - 1
            other = resolve_historical_evidence(messages, partner, valid_scope_ids=known).evidence
            if (other is None or other.source_class != "ordinary_conversation"
                    or other.truth_scope_id != request.truth_scope_id
                    or other.speaker_role == evidence.speaker_role or partner in unresolved):
                stats["source_excluded"] += 1
                return None
            stamp = parse_conversation_timestamp(evidence.timestamp, request.now.tzinfo,
                                                 reject_ambiguous_legacy=True)
            other_stamp = parse_conversation_timestamp(other.timestamp, request.now.tzinfo,
                                                       reject_ambiguous_legacy=True)
            if (stamp is None or other_stamp is None or other_stamp > request.now
                    or not first_day <= stamp.date() <= request.now.date() or stamp > request.now):
                stats["date_excluded"] += 1
                return None
            return evidence, stamp

        def contribution(payload, priority, refs):
            return CompanionContextContribution("recent_pulse", "continuity_hint", request.character_id,
                request.truth_scope_id, request.turn_key, priority, payload, tuple(refs))

        # Closed/retired threads never reach this governed current-thread lookup.
        for thread in repository.list_open_threads(request.character_id, limit=12,
                                                   truth_scope_id=request.truth_scope_id).threads:
            stats["thread_candidates"] += 1
            latest = None
            for event_id in reversed(thread.evidence_event_ids[-12:]):
                row = store.connection.execute(
                    """SELECT * FROM events WHERE character_id=? AND event_id=?
                       AND source_origin='canonical_conversation' AND redaction_state='active'""",
                    (request.character_id, event_id)).fetchone()
                ref = str(row["source_reference"] or "") if row else ""
                if not ref.startswith(source_prefix) or not ref[len(source_prefix):].isdigit():
                    stats["stale_excluded"] += 1
                    continue
                index = int(ref[len(source_prefix):])
                verified = source(index)
                if (verified and verified[0].speaker_role == "user"
                        and messages[index]["content"] == row["content_text"]
                        and verified[0].source_content_sha256 == row["content_sha256"]):
                    latest = verified
                    break
            if latest is None:
                continue
            # Reuse the thread owner's injection/size checks, never raw source prose.
            from memory_v2_store.open_thread_contract import normalize_open_thread_value
            try:
                description = normalize_open_thread_value(thread.description, maximum=144, field="description")
            except ValueError:
                stats["source_excluded"] += 1
                continue
            payload = f"Open {thread.participant_scope} thread discussed {latest[1].date()}: {description}"
            items.append(contribution(payload, 90, ("thread:" + thread.thread_id, latest[0].canonical_record_id)))

        # Do not accept unvalidated Viewer projections or arbitrary old summaries.
        rows = store.connection.execute(
            "SELECT summary_id FROM summaries WHERE character_id=? AND summary_level IN "
            "('episode_compaction','episode_era_compaction') LIMIT ?",
            (request.character_id, MAX_EPISODE_ROWS + 1)).fetchall()
        within_bound = len(messages) <= MAX_VALIDATION_RECORDS and len(rows) <= MAX_EPISODE_ROWS
        if within_bound:
            size = 0
            for message in messages:
                size += len(str(message.get("content", "")))
                if size > MAX_VALIDATION_CHARACTERS:
                    within_bound = False
                    break
        if not within_bound:
            stats["episode_budget_excluded"] = 1
        elif rows:
            validation = EpisodeCompactionCache(store, request.character_id).validate_for_context(
                messages, allow_historical_recall=True, use_consolidated=False,
                active_truth_scope={"kind":scope.kind, "scope_id":scope.truth_scope_id})
            candidates = sorted(validation.lower_records,
                                key=lambda r: -(r.source_end_index_exclusive or 0))[:MAX_EPISODE_CANDIDATES]
            for episode in candidates:
                stats["episode_candidates"] += 1
                if not episode.accepted:
                    stats["stale_excluded"] += 1
                    continue
                if episode.truth_scope_id != request.truth_scope_id:
                    stats["scope_excluded"] += 1
                    continue
                for anchor in episode.metadata.get("continuity_anchors", ())[:MAX_ANCHORS_PER_EPISODE]:
                    indices = anchor["source_record_indices"]
                    verified = [source(index) for index in indices]
                    if not verified or not all(verified):
                        continue
                    # State/fact evidence keeps its dedicated current owner. Even
                    # topic hints should not revive a corrected/retired property.
                    exchange_indices = set(indices)
                    exchange_indices.update(index + 1 if value[0].speaker_role == "user" else index - 1
                                            for index, value in zip(indices, verified))
                    if any(self._state_source(source_prefix + str(index), request.character_id) for index in exchange_indices):
                        stats["state_excluded"] += 1
                        continue
                    terms = anchor.get("key_terms", ())
                    text = " ".join(str(messages[index]["content"]).casefold() for index in indices)
                    terms = tuple(term for term in terms if isinstance(term, str)
                                  and _TERM.fullmatch(term) and term.casefold() in text)[:4]
                    if not terms:
                        continue
                    day = max(value[1].date() for value in verified)
                    speakers = "/".join(sorted({value[0].speaker_role for value in verified}))
                    payload = f"Past discussion topics ({day}; {speakers}; current status unknown): " + "; ".join(terms)
                    if len(payload) > 240:
                        continue
                    refs = ("episode:" + episode.record_id,) + tuple(value[0].canonical_record_id for value in verified[:3])
                    items.append(contribution(payload, 50, refs))
        stats["item_count"] = len(items)
        return RecentPulseResult(tuple(items[:24]), dict(stats, suppressed="none"))

    def _state_source(self, reference: str, character_id: str) -> bool:
        # Exact selected source only, never a broad scan or a new state validator.
        return self.writer.store.connection.execute(
            """SELECT 1 FROM events e WHERE e.character_id=? AND e.source_reference=? AND (
              EXISTS (SELECT 1 FROM claim_evidence ce JOIN claims c
                ON c.character_id=ce.character_id AND c.claim_id=ce.claim_id
                WHERE ce.character_id=e.character_id AND ce.event_id=e.event_id
                  AND (c.claim_type IN ('durable_core_fact','active_state_slot') OR c.valid_to_us IS NOT NULL))
              OR EXISTS (SELECT 1 FROM active_scene_relation_events r
                WHERE r.character_id=e.character_id AND r.event_id=e.event_id)
              OR EXISTS (SELECT 1 FROM active_scene_subject_lifecycle_events s
                WHERE s.character_id=e.character_id AND s.event_id=e.event_id)
            ) LIMIT 1""", (character_id, reference)).fetchone() is not None
