"""Development V2 canonical observation progress, not an event/action replay queue.

Original SQLite evidence is never deleted here. Each deterministic consumer owns
its cursor. Failed work remains pending even when another consumer commits.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
import uuid

from conversation.truth_scope import parse_canonical_truth_scope
from memory_v2_canonical_evidence import LoadedCanonicalArchive, ORDINARY_USER_KEYS
from memory_v2_store.store import parse_timestamp_us, utc_now_us


POLICY_VERSION = "canonical_observers_v1"
PAGE_RECORDS = 32
MAX_PAGE_RECORDS = 128
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
CONSUMERS = {
    "identity": "observe_canonical_user_message",
    "durable": "observe_canonical_user_durable_facts",
    "continuity": "observe_canonical_user_continuity",
    "headwear": "observe_canonical_user_active_state",
}


def extend_digest(digest, record):
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(bytes.fromhex(digest) + payload).hexdigest()


class _ObservationIncomplete(Exception):
    def __init__(self, state, reason):
        self.state, self.reason = state, reason


class CanonicalObservationRecovery:
    def __init__(self, writer, conversation):
        self.writer, self.store, self.conversation = writer, writer.store, conversation
        self.character_id = writer.character_id
        path = Path(conversation.conversation_file).resolve()
        relative = path.relative_to(writer.application_dir)
        self.source_key = hashlib.sha256(relative.as_posix().encode()).hexdigest()
        self.last_status = {}
        self._completed_signature = None
        self.embedding_provider_getter = lambda: None
        self.last_embedding_work = {"embedded": 0, "failed": 0}

    def _missing_embedding_ids(self, provider, *, limit):
        if provider is None:
            return ()
        rows = self.store.connection.execute(
            """SELECT h.claim_id FROM historical_evidence h JOIN claims c
                 ON c.character_id=h.character_id AND c.claim_id=h.claim_id
               LEFT JOIN claim_embeddings e ON e.character_id=h.character_id AND e.claim_id=h.claim_id
                 AND e.provider=? AND e.model=? AND e.preprocessing_fingerprint=?
                 AND e.dimensions=? AND e.dtype=? AND e.normalized=?
                 AND COALESCE(e.model_version,'')=?
               WHERE h.character_id=? AND h.retrieval_eligible=1
                 AND c.provenance_state='complete' AND (e.state IS NULL OR e.state<>'current'
                      OR e.vector_blob IS NULL OR length(e.vector_blob)<>e.dimensions*4)
                 AND COALESCE((SELECT status FROM claim_status_events s WHERE s.character_id=c.character_id
                   AND s.claim_id=c.claim_id ORDER BY status_event_id DESC LIMIT 1),'active')
                   NOT IN ('retracted','archived','hidden','redacted')
               ORDER BY h.canonical_index LIMIT ?""",
            (provider.provider, provider.model, provider.preprocessing_fingerprint,
             provider.dimensions, provider.dtype, int(bool(provider.normalized)),
             str(getattr(provider, "model_version", None) or ""), self.character_id, limit),
        )
        return tuple(str(row[0]) for row in rows)

    def maintain_embeddings(self, provider):
        """Idle-only bounded derived work; per-claim vector identity is progress."""
        from memory_v2_store.embeddings import EmbeddingLifecycle
        identifiers = self._missing_embedding_ids(provider, limit=PAGE_RECORDS)
        if not identifiers:
            self.last_embedding_work = {"embedded": 0, "failed": 0}
            return
        self.last_embedding_work = EmbeddingLifecycle(self.store, provider).rebuild_claims(
            identifiers, character_id=self.character_id, report_health=False,
        )

    def lookup_health(self, decision):
        """Compose existing query ownership with source/projection execution state."""
        from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth
        if not decision.applicable or decision.intent == "grounded_followup_attribute":
            return RetrievalHealth((RetrievalLaneHealth("observation", "unused", "routing", "not_applicable"),)), True
        count = getattr(self.conversation, "_persisted_message_count", 0)
        current_count = count
        if count and self.conversation.messages[count - 1].get("role") == "user":
            current_count -= 1  # An orphan/pending pair cannot establish a current fact.
        history_count = (current_count if count == len(self.conversation.messages) else count)
        def complete(consumer, required):
            row = self._progress(consumer)
            outcome = self.last_status.get("consumers", {}).get(consumer, {})
            return (row is not None and int(row["next_index"]) >= required
                    and outcome.get("state") not in {"failed", "unresolved"}
                    and self.last_status.get("state") not in {"failed", "unresolved"})
        history_complete = complete("history", history_count)
        current_complete = all(complete(key, current_count) for key in ("identity", "durable"))
        code = "" if history_complete and (decision.historical or current_complete) else "observation_pending"
        if self.last_status.get("state") in {"failed", "unresolved"} or any(
            value.get("state") in {"failed", "unresolved"}
            for key, value in self.last_status.get("consumers", {}).items()
            if key in ({"history"} if decision.historical else {"history", "identity", "durable"})
        ):
            code = "observation_failed"
        lanes = [RetrievalLaneHealth("observation", "incomplete" if code else "complete", "source", code)]
        provider = self.embedding_provider_getter()
        if provider is not None and self._missing_embedding_ids(provider, limit=1):
            lanes.append(RetrievalLaneHealth("semantic", "incomplete", "embedding", "embedding_not_current"))
        return RetrievalHealth(tuple(lanes)), current_complete

    def inspection_status(self):
        """Bounded read-only status for the existing viewer warning surface."""
        try:
            count = getattr(self.conversation, "_persisted_message_count", 0)
            states = {self.last_status.get("state", "complete")}
            for consumer in (*CONSUMERS, "history"):
                row = self._progress(consumer)
                states.add(self.last_status.get("consumers", {}).get(consumer, {}).get("state", "complete"))
                if row is not None:
                    states.add(str(row["state"]))
                if count and (row is None or int(row["next_index"]) < count):
                    states.add("pending")
            if "unresolved" in states:
                return "recovery_unresolved"
            if "failed" in states:
                return "recovery_failed"
            if "pending" in states:
                return "recovery_pending"
            provider = self.embedding_provider_getter()
            if provider is not None and self._missing_embedding_ids(provider, limit=1):
                return "index_failed" if self.last_embedding_work.get("failed") else "index_pending"
            return "current"
        except Exception:
            return "recovery_unavailable"

    def _key(self, consumer):
        from memory_v2_historical_evidence import HISTORICAL_EVIDENCE_POLICY_VERSION
        policy = (HISTORICAL_EVIDENCE_POLICY_VERSION + ":runtime_append_v1"
                  if consumer == "history" else POLICY_VERSION)
        return (self.character_id, self.source_key, consumer, policy)

    def _progress(self, consumer):
        return self.store.connection.execute(
            """SELECT * FROM canonical_observation_progress WHERE character_id=?
               AND source_key=? AND consumer=? AND policy_version=?""", self._key(consumer),
        ).fetchone()

    def _save_progress(self, consumer, index, digest, state, reason):
        self.store.connection.execute(
            """INSERT INTO canonical_observation_progress VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(character_id,source_key,consumer,policy_version) DO UPDATE SET
               next_index=excluded.next_index, prefix_digest=excluded.prefix_digest,
               state=excluded.state, reason=excluded.reason, updated_at_us=excluded.updated_at_us""",
            (*self._key(consumer), index, digest, state, reason, utc_now_us()),
        )

    @contextmanager
    def _observation_unit(self):
        # The store's nested transaction helper deliberately joins its parent;
        # this local recovery unit needs an actual savepoint when an observer
        # returns failure after making a partial mutation.
        self.store.connection.execute("SAVEPOINT canonical_observation_unit")
        try:
            yield
        except Exception:
            self.store.connection.execute("ROLLBACK TO canonical_observation_unit")
            raise
        finally:
            self.store.connection.execute("RELEASE canonical_observation_unit")

    def _observe(self, consumer, archive, index, scopes):
        message = archive.records[index]
        if consumer == "history":
            from memory_v2_historical_evidence import resolve_historical_evidence, persist_historical_occurrence
            decision = resolve_historical_evidence(archive.records, index, valid_scope_ids=scopes)
            if decision.accepted:
                persist_historical_occurrence(self.store, self.character_id, decision.evidence)
            return
        if set(message) != ORDINARY_USER_KEYS or message.get("role") != "user":
            return  # Never reparse generated/system/inaccessible prose.
        scope = parse_canonical_truth_scope(message, valid_scope_ids=scopes)
        if not scope.is_valid:
            raise _ObservationIncomplete("unresolved", "source_scope_unresolved")
        if consumer in {"identity", "durable"}:
            if scope.kind != "real_world":
                return  # Scenario speech cannot establish real-world truth.
            if index + 1 == len(archive.records):
                raise _ObservationIncomplete("pending", "completion_pending")
            if self.store.active_truth_scope_id(self.character_id) != scope.scope_id:
                raise _ObservationIncomplete("pending", "scope_inactive")
        else:
            if consumer == "headwear":
                from memory_v2_store.active_state_headwear import extract_headwear_state_assertion
                if extract_headwear_state_assertion(message["content"]) is None:
                    return
            self._state_replay_guard(consumer, message, index, archive, scope)

        result = getattr(self.writer, CONSUMERS[consumer])(
            message, conversation_index=index, conversation_file=archive.path, _archive=archive,
        )
        if not isinstance(result, dict) or result.get("state") == "failed":
            raise _ObservationIncomplete("failed", "observer_failed")
        if result.get("reason") in {"canonical_event_identity_conflict", "canonical_identity_conflict"}:
            raise _ObservationIncomplete("unresolved", "source_identity_conflict")

    def _state_replay_guard(self, consumer, message, index, archive, scope):
        """Permit only ordered deterministic state work; never rewind a newer owner.

        A durable current-continuity event commits with all of its mutations.
        Detect it before interpreting old prose against current state. Headwear
        completion additionally requires claim evidence (the legacy writer could
        save its event before applying a state update).
        """
        from memory_v2_shadow_writer import _CURRENT_CONTINUITY_NAMESPACE, _ACTIVE_HEADWEAR_NAMESPACE
        namespace = _CURRENT_CONTINUITY_NAMESPACE if consumer == "continuity" else _ACTIVE_HEADWEAR_NAMESPACE
        reference = self.writer._canonical_source_reference(archive.path, index)
        event_id = str(uuid.uuid5(namespace,
            f"{self.character_id}:{reference}:{message['timestamp']}:"
            f"{hashlib.sha256(message['content'].encode()).hexdigest()}"))
        event = self.store.connection.execute(
            "SELECT actor_kind,content_text,source_reference FROM events WHERE character_id=? AND event_id=?",
            (self.character_id, event_id),
        ).fetchone()
        if event is not None:
            if tuple(event) != ("user", message["content"], reference):
                raise _ObservationIncomplete("unresolved", "source_identity_conflict")
            if consumer == "continuity" or self.store.connection.execute(
                "SELECT 1 FROM claim_evidence WHERE character_id=? AND event_id=? LIMIT 1",
                (self.character_id, event_id),
            ).fetchone():
                raise _ObservationIncomplete("complete", "already_observed")
        if scope.scope_id != self.store.active_truth_scope_id(self.character_id):
            raise _ObservationIncomplete("unresolved", "state_scope_changed")
        if consumer == "continuity":
            disposition = self._continuity_disposition(message, scope)
            if disposition is not None:
                reason, witness = disposition
                self._record_disposition(consumer, message, index, scope, reason, witness)
                raise _ObservationIncomplete("complete", reason)
        # Only actual state/thread/control evidence contributes, not unrelated
        # facts, profile caches, embeddings, summaries or historical projections.
        newer = self.store.connection.execute(
            """SELECT 1 FROM events e WHERE e.character_id=? AND e.recorded_at_us>?
               AND (EXISTS (SELECT 1 FROM claim_evidence ce JOIN claims c
                     ON c.character_id=ce.character_id AND c.claim_id=ce.claim_id
                     WHERE ce.character_id=e.character_id AND ce.event_id=e.event_id
                     AND c.assertion_scope='active_state')
                 OR EXISTS (SELECT 1 FROM active_scene_relation_events r
                     WHERE r.character_id=e.character_id AND r.event_id=e.event_id)
                 OR EXISTS (SELECT 1 FROM truth_scope_events s
                     WHERE s.character_id=e.character_id AND s.event_id=e.event_id)
                 OR EXISTS (SELECT 1 FROM open_threads t WHERE t.character_id=e.character_id
                     AND (t.last_event_id=e.event_id OR t.closed_event_id=e.event_id))) LIMIT 1""",
            (self.character_id, parse_timestamp_us(message["timestamp"])),
        ).fetchone()
        if newer:
            raise _ObservationIncomplete("unresolved", "newer_state_evidence")

    def _continuity_disposition(self, message, scope):
        """Only proven no-effect or a replaced exact singleton, never all old work."""
        from current_continuity import extract_current_continuity, extract_active_state_proposal
        from memory_v2_store import MemoryV2Repository
        repository = MemoryV2Repository(self.store)
        extraction = extract_current_continuity(repository, self.character_id,
            message["content"], allow_loci=bool(getattr(self.writer, "enable_loci", False)))
        if not extraction.has_mutation and extraction.reason == "interrogative_non_authoritative":
            return "non_mutating_question", None
        # Pure explicit actor activity only. Scene attributes, multiple clauses,
        # independent causes, corrections, references and Open Threads do not
        # inherit this disposition from an unrelated newer event.
        if re.search(r"\b(?:and|but|then|while)\b|[;\n]", message["content"], re.I):
            return None
        proposal = extract_active_state_proposal(message["content"], current_activity=None)
        if (proposal is None or proposal != extraction.active_state or len(proposal.updates) != 1
                or extraction.scenario or extraction.correction or extraction.open_threads
                or extraction.scene_relations):
            return None
        update = proposal.updates[0]
        if (update.target_kind, update.target_ref, update.attribute, update.operation) != (
                "actor", "user", "activity", "set"):
            return None
        current = repository.lookup_actor_state(self.character_id, "user", "activity",
            truth_scope_id=scope.scope_id).state
        source_time = parse_timestamp_us(message["timestamp"])
        if current is None or current.valid_from_us is None or current.valid_from_us <= source_time:
            return None
        for event_id in current.evidence_event_ids:
            event = self.store.connection.execute(
                """SELECT recorded_at_us FROM events WHERE character_id=? AND event_id=?
                   AND actor_kind='user' AND redaction_state='active'""",
                (self.character_id, event_id)).fetchone()
            if event is not None and event[0] > source_time:
                return "superseded_actor_activity", event_id
        return None

    def _record_disposition(self, consumer, message, index, scope, reason, witness):
        digest = extend_digest(EMPTY_DIGEST, message)
        key = (*self._key(consumer), index)
        existing = self.store.connection.execute(
            """SELECT source_digest FROM canonical_observation_dispositions WHERE
               character_id=? AND source_key=? AND consumer=? AND policy_version=? AND source_index=?""",
            key).fetchone()
        if existing is not None and existing[0] != digest:
            raise _ObservationIncomplete("unresolved", "source_identity_conflict")
        self.store.connection.execute(
            "INSERT OR IGNORE INTO canonical_observation_dispositions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (*key, digest, scope.scope_id, reason, witness, utc_now_us()))

    def _evidence_revision(self):
        # New canonical/viewer/admin evidence or scope changes permit another
        # bounded attempt. Embedding/cache maintenance does not invalidate this.
        return (self.store.connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM events WHERE character_id=?",
            (self.character_id,)).fetchone()[0], self.store.active_truth_scope_id(self.character_id))

    def run_page(self, *, maximum_records=PAGE_RECORDS):
        if isinstance(maximum_records, bool) or not 1 <= maximum_records <= MAX_PAGE_RECORDS:
            raise ValueError("observation page bound is invalid")
        path = Path(self.conversation.conversation_file)
        try:
            metadata = path.stat()
            signature = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
                         getattr(self.conversation, "_persisted_message_count", -1))
        except FileNotFoundError:
            signature = ("absent", getattr(self.conversation, "_persisted_message_count", -1))
        if (signature, self._evidence_revision()) == self._completed_signature:
            self.last_status = {**self.last_status, "canonical_reads": 0,
                "consumers": {key: {**value, "processed": 0}
                              for key, value in self.last_status["consumers"].items()}}
            return self.last_status
        self._completed_signature = None
        archive = LoadedCanonicalArchive.load(path)
        # A service-owned committed archive, never pending user/assistant input.
        count = getattr(self.conversation, "_persisted_message_count", -1)
        if count != len(archive.records) or archive.records != self.conversation.messages[:count]:
            self.last_status = {"state": "unresolved", "reason": "canonical_snapshot_changed"}
            return self.last_status
        scopes = {str(row[0]) for row in self.store.connection.execute(
            "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?", (self.character_id,),
        )}
        cursors = {consumer: self._progress(consumer) for consumer in (*CONSUMERS, "history")}
        # Validate source identity once per page, independently of observation.
        # JSON still requires a linear read. This does not rebuild any projection.
        boundaries = {int(row["next_index"]) for row in cursors.values() if row is not None}
        digests, digest = {0: EMPTY_DIGEST}, EMPTY_DIGEST
        for index, record in enumerate(archive.records):
            digest = extend_digest(digest, record)
            if index + 1 in boundaries:
                digests[index + 1] = digest
        outcomes = {}
        for consumer, row in cursors.items():
            index = int(row["next_index"]) if row is not None else 0
            digest = str(row["prefix_digest"]) if row is not None else EMPTY_DIGEST
            if digests.get(index) != digest:
                outcomes[consumer] = {"state": "unresolved", "reason": "canonical_prefix_changed", "processed": 0}
                continue
            start = index
            state, reason = "complete", "up_to_date"
            with self.store.transaction():
                while index < min(count, start + maximum_records):
                    try:
                        with self._observation_unit():
                            try:
                                self._observe(consumer, archive, index, scopes)
                            except _ObservationIncomplete as outcome:
                                if outcome.state != "complete":
                                    raise
                            next_digest = extend_digest(digest, archive.records[index])
                            self._save_progress(consumer, index + 1, next_digest, "pending", "page_progress")
                        index, digest = index + 1, next_digest
                    except _ObservationIncomplete as outcome:
                        state, reason = outcome.state, outcome.reason
                        break
                    except Exception:
                        state, reason = "failed", "observer_failed"
                        break
                if state == "complete" and index < count:
                    state, reason = "pending", "page_limit"
                if index > start:
                    references = tuple(reference for value in range(start, index) for reference in (
                        self.writer._canonical_source_reference(archive.path, value),
                        f"conversation.json#{value}",
                    ))
                    self.store.refresh_fts_sources(self.character_id, references)
                self._save_progress(consumer, index, digest, state, reason)
            outcomes[consumer] = {"state": state, "reason": reason, "processed": index - start}
        self.last_status = {"consumers": outcomes, "source_records": count,
                            "page_limit": maximum_records, "canonical_reads": 1}
        if all(value["state"] in {"complete", "unresolved"} for value in outcomes.values()):
            # Unresolved is still visible and its cursor is not advanced. Avoid
            # rereading/reinterpreting an unchanged lifetime archive each poll.
            self._completed_signature = (signature, self._evidence_revision())
        return self.last_status
