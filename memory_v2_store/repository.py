"""Production-oriented repository boundary over the non-authoritative V2 store.

This module deliberately does not wire V2 into AssistantService.  It provides
bounded, character-scoped operations for the later controlled cutover.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable, Optional
import uuid

from .durable_contract import (
    DURABLE_ASSERTION_SCOPE, DURABLE_CORE_FACT, DURABLE_EVIDENCE_ROLES,
    DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE, DURABLE_USER_EVIDENCE_ROLES,
    validate_durable_subject_key,
)
from .active_state_contract import (ACTIVE_STATE, ACTIVE_STATE_ASSERTION_SCOPE,
                                    ACTIVE_STATE_EVIDENCE_ROLES, ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES,
                                    MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT,
                                    MAX_ACTIVE_SCENE_SUBJECTS, actor_state_subject_key,
                                    parse_active_state_subject_key, scene_state_subject_key,
                                    validate_active_state_subject_key)
from .open_thread_contract import (
    MAX_CURRENT_OPEN_THREADS, OPEN_THREAD, OPEN_THREAD_ASSERTION_SCOPE,
    OPEN_THREAD_EVIDENCE_ROLES, OPEN_THREAD_PARTICIPANT_SCOPES,
    OPEN_THREAD_STATUSES, validate_open_thread_id,
)
from .store import (CURRENT_EXCLUDED_STATUSES, HISTORICAL_EXCLUDED_STATUSES,
                    MemoryV2Store, StoreError, _require_uuid, utc_now_us)
from .scene_relation_contract import (
    MAX_CURRENT_SCENE_RELATIONS,
    RELATION_PREDICATES,
    CapabilityEffects,
    compose_capability_effects,
)


STABLE_USER_FACT = "stable_user_fact"
SHARED_EPISODE = "shared_episode"
MEMORY_TYPES = frozenset({STABLE_USER_FACT, SHARED_EPISODE, DURABLE_CORE_FACT})


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    character_id: str
    memory_type: str
    content: str
    importance: int
    status: str
    created_at_us: int
    updated_at_us: int
    valid_from_us: int | None
    valid_to_us: int | None
    provenance_state: str
    source_reference: str | None


@dataclass(frozen=True)
class DurableCoreRecord:
    """A verified durable candidate, not a prompt-admission decision."""

    claim_id: str
    character_id: str
    subject_key: str
    content: str
    status: str
    created_at_us: int
    valid_from_us: int | None
    valid_to_us: int | None
    evidence_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class DurableCoreLookup:
    """Candidate-only durable lookup result.

    Selection/relevance and prompt admission are intentionally outside this
    storage API.
    """

    character_id: str
    subject_key: str
    historical_at_us: int | None
    candidates: tuple[DurableCoreRecord, ...]


@dataclass(frozen=True)
class ActiveStateRecord:
    """One exact, verified active-state value."""

    state_id: str
    character_id: str
    subject_key: str
    value: str
    status: str
    valid_from_us: int | None
    valid_to_us: int | None
    last_confirmed_at_us: int | None
    evidence_event_ids: tuple[str, ...]
    truth_scope_id: str | None = None

    def elapsed_us(self, at_us: int) -> int | None:
        """Derived elapsed duration; reading time never mutates state."""
        if self.valid_from_us is None or isinstance(at_us, bool) or not isinstance(at_us, int):
            return None
        end = self.valid_to_us if self.valid_to_us is not None else at_us
        return max(0, min(at_us, end) - self.valid_from_us)


@dataclass(frozen=True)
class ActiveStateLookup:
    """Exact-slot state result: one value or an explicit unset state."""

    character_id: str
    subject_key: str
    historical_at_us: int | None
    state: ActiveStateRecord | None


@dataclass(frozen=True)
class ActiveSceneSubjectRecord:
    """Bounded character-local scene subject metadata, not entity identity."""

    character_id: str
    scene_subject_id: str
    introduced_at_us: int
    retired_at_us: int | None
    truth_scope_id: str | None = None
    last_referenced_at_us: int | None = None
    identity_strength: str = "generic"
    lifecycle_state: str = "current"


@dataclass(frozen=True)
class ActiveSceneRelationRecord:
    relation_id: str
    character_id: str
    target: str
    facet: str | None
    predicate: str
    cause_kind: str
    cause: str
    valid_from_us: int
    valid_to_us: int | None
    truth_scope_id: str
    target_kind: str = "actor"
    side: str | None = None
    cause_subject_id: str | None = None
    semantic_family: str | None = None
    quantity: int | None = None
    effect_state: str | None = None

    @property
    def target_ref(self) -> str:
        return self.target

    @property
    def body_region(self) -> str | None:
        return self.facet

    @property
    def object_ref(self) -> str | None:
        return self.cause_subject_id


@dataclass(frozen=True)
class OpenThreadRecord:
    """One verified unresolved-continuity record; never a prompt decision."""

    thread_id: str
    character_id: str
    kind: str
    participant_scope: str
    description: str
    temporal_anchor: str | None
    status: str
    opened_at_us: int
    last_mentioned_at_us: int
    closed_at_us: int | None
    evidence_event_ids: tuple[str, ...]
    truth_scope_id: str | None = None

    def elapsed_open_us(self, at_us: int) -> int | None:
        if isinstance(at_us, bool) or not isinstance(at_us, int):
            return None
        end = self.closed_at_us if self.closed_at_us is not None else at_us
        return max(0, min(at_us, end) - self.opened_at_us)


@dataclass(frozen=True)
class OpenThreadLookup:
    """Bounded current-thread result used before future selection/admission."""

    character_id: str
    threads: tuple[OpenThreadRecord, ...]


@dataclass(frozen=True)
class OpenThreadEvidenceRecord:
    """One bounded canonical user-evidence row for rebuildable thread identity."""

    event_id: str
    evidence_role: str
    content: str
    recorded_at_us: int


@dataclass(frozen=True)
class TruthScopeRecord:
    """A compact governed continuity boundary, not a model-defined world."""

    truth_scope_id: str
    character_id: str
    kind: str
    label: str
    status: str
    created_at_us: int
    last_active_at_us: int


def normalize_memory_type(legacy_category: object) -> str:
    """Map V1 categories conservatively into the first two V2 memory kinds."""
    category = str(legacy_category or "").strip().lower()
    return SHARED_EPISODE if category in {"event", "episode", "experience"} else STABLE_USER_FACT


class MemoryV2Repository:
    """Character-scoped lifecycle, paging, and retrieval operations.

    Raw SQL remains private to this repository so future UI and service code do
    not need SQLite knowledge or implicit "current character" state.
    """

    def __init__(self, store: MemoryV2Store) -> None:
        self.store = store

    @staticmethod
    def _status_sql() -> str:
        return """
            COALESCE((SELECT status FROM claim_status_events s
              WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
              ORDER BY s.status_event_id DESC LIMIT 1), 'active')
        """

    @staticmethod
    def _source_reference_sql() -> str:
        return """(SELECT source_reference FROM events e JOIN claim_evidence ce
                    ON ce.character_id=e.character_id AND ce.event_id=e.event_id
                   WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                   ORDER BY ce.created_at_us LIMIT 1)"""

    def ensure_character(self, character_id: str, display_name: str, *, legacy_config_key: str | None = None) -> str:
        character_id = _require_uuid(character_id)
        row = self.store.connection.execute(
            "SELECT character_id FROM characters WHERE character_id=?", (character_id,)
        ).fetchone()
        if row is None:
            self.store.create_character(character_id, display_name, legacy_config_key=legacy_config_key)
        return character_id

    def active_truth_scope(self, character_id: str) -> TruthScopeRecord:
        character_id = _require_uuid(character_id)
        scope_id = self.store.active_truth_scope_id(character_id)
        row = self.store.connection.execute(
            "SELECT * FROM truth_scopes WHERE character_id=? AND truth_scope_id=?", (character_id, scope_id),
        ).fetchone()
        if row is None:
            raise StoreError("active truth scope is absent for this character.")
        return self._truth_scope_record(row)

    def list_truth_scopes(self, character_id: str, *, limit: int = 17) -> tuple[TruthScopeRecord, ...]:
        character_id = _require_uuid(character_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 17:
            raise StoreError("truth-scope limit exceeds the governed bound.")
        rows = self.store.connection.execute(
            """SELECT * FROM truth_scopes WHERE character_id=?
                 ORDER BY CASE WHEN scope_kind='real_world' THEN 0 ELSE 1 END, created_at_us, truth_scope_id LIMIT ?""",
            (character_id, limit),
        ).fetchall()
        return tuple(self._truth_scope_record(row) for row in rows)

    @staticmethod
    def _truth_scope_record(row) -> TruthScopeRecord:
        return TruthScopeRecord(
            str(row["truth_scope_id"]), str(row["character_id"]), str(row["scope_kind"]),
            str(row["label"]), str(row["status"]), int(row["created_at_us"]), int(row["last_active_at_us"]),
        )

    def get_memory(self, character_id: str, memory_id: str) -> MemoryRecord | None:
        character_id = _require_uuid(character_id)
        row = self.store.connection.execute(
            f"""SELECT c.*, {self._status_sql()} AS effective_status,
                    {self._source_reference_sql()} AS source_reference
                FROM claims c WHERE c.character_id=? AND c.claim_id=?""",
            (character_id, str(memory_id)),
        ).fetchone()
        return self._record(row) if row else None

    def page(
        self,
        character_id: str,
        *,
        statuses: Iterable[str] = ("active",),
        memory_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        character_id = _require_uuid(character_id)
        limit = self._bounded_limit(limit)
        if offset < 0:
            raise StoreError("offset must not be negative")
        status_values = tuple(statuses)
        if not status_values or any(status not in {"active", "superseded", "archived", "disputed", "retracted"} for status in status_values):
            raise StoreError("invalid memory status filter")
        if memory_type is not None and memory_type not in MEMORY_TYPES:
            raise StoreError("invalid memory type")
        statement = f"SELECT c.*, {self._status_sql()} AS effective_status, {self._source_reference_sql()} AS source_reference FROM claims c WHERE c.character_id=?"
        arguments: list[object] = [character_id]
        if memory_type is not None:
            statement += " AND c.claim_type=?"
            arguments.append(memory_type)
        statement += " AND " + self._status_sql() + " IN (" + ",".join("?" for _ in status_values) + ")"
        arguments.extend(status_values)
        statement += " ORDER BY c.created_at_us DESC, c.claim_id LIMIT ? OFFSET ?"
        arguments.extend((limit, offset))
        return [self._record(row) for row in self.store.connection.execute(statement, arguments).fetchall()]

    def search(
        self,
        character_id: str,
        query: str,
        *,
        limit: int = 10,
        memory_type: str | None = None,
        truth_scope_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Bounded FTS retrieval; no Python-side scan of the memory corpus."""
        character_id = _require_uuid(character_id)
        if memory_type is not None and memory_type not in MEMORY_TYPES:
            raise StoreError("invalid memory type")
        terms = tuple(dict.fromkeys(re.findall(r"[A-Za-z0-9]{2,}", str(query).lower())))
        if not terms:
            return []
        self.store.ensure_fts()
        safe_query = " OR ".join(f'"{term}"' for term in terms)
        scope_sql, scope_arguments = self.store._retrieval_scope_sql(
            character_id, truth_scope_id,
        )
        statement = f"""
            SELECT c.*, {self._status_sql()} AS effective_status, {self._source_reference_sql()} AS source_reference
              FROM claims_fts f JOIN claims c ON c.character_id=f.character_id AND c.claim_id=f.claim_id
             WHERE f.character_id=? AND f.searchable_text MATCH ?
               AND {scope_sql}
               AND {self._status_sql()} NOT IN ({','.join('?' for _ in CURRENT_EXCLUDED_STATUSES)})
        """
        arguments: list[object] = [
            character_id, safe_query, *scope_arguments, *sorted(CURRENT_EXCLUDED_STATUSES),
        ]
        if memory_type is not None:
            statement += " AND c.claim_type=?"
            arguments.append(memory_type)
        statement += " ORDER BY bm25(claims_fts) LIMIT ?"
        arguments.append(self._bounded_limit(limit))
        return [self._record(row) for row in self.store.connection.execute(statement, arguments).fetchall()]

    def lookup_durable_core(
        self,
        character_id: str,
        subject_key: str,
        *,
        historical_at_us: int | None = None,
        limit: int = 16,
    ) -> DurableCoreLookup:
        """Return strict, exact-slot durable candidates without prompt admission.

        The indexed character/type/subject-key predicate narrows first.  All
        lifecycle, validity, provenance, and user-evidence predicates are in
        SQL before ``LIMIT`` so an ineligible row cannot hide an eligible one.
        """
        character_id = _require_uuid(character_id)
        try:
            subject_key = validate_durable_subject_key(subject_key)
        except ValueError as error:
            raise StoreError(str(error)) from error
        if historical_at_us is not None and (isinstance(historical_at_us, bool) or not isinstance(historical_at_us, int)):
            raise StoreError("historical_at_us must be an integer timestamp.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise StoreError("durable lookup limit must be an integer from 1 to 16.")
        at_us = historical_at_us if historical_at_us is not None else utc_now_us()
        real_scope_id = self.store.default_truth_scope_id(character_id)
        excluded = HISTORICAL_EXCLUDED_STATUSES if historical_at_us is not None else CURRENT_EXCLUDED_STATUSES
        status_placeholders = ",".join("?" for _ in excluded)
        evidence_placeholders = ",".join("?" for _ in DURABLE_USER_EVIDENCE_ROLES)
        statement = f"""
            SELECT c.*, COALESCE((SELECT status FROM claim_status_events s
                WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                  AND s.created_at_us <= ?
                ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status
              FROM claims c
             WHERE c.character_id=? AND c.truth_scope_id=?
               AND c.claim_type=?
               AND c.subject_key=?
               AND c.assertion_scope=?
               AND c.provenance_state='complete'
               AND (c.valid_from_us IS NULL OR c.valid_from_us <= ?)
               AND (c.valid_to_us IS NULL OR c.valid_to_us > ?)
               AND COALESCE((SELECT status FROM claim_status_events s
                    WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                      AND s.created_at_us <= ?
                    ORDER BY s.status_event_id DESC LIMIT 1), 'active') NOT IN ({status_placeholders})
               AND EXISTS (
                    SELECT 1 FROM claim_evidence ce
                    JOIN events e ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                     WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                       AND e.redaction_state='active'
                       AND ((ce.evidence_role IN ({evidence_placeholders})
                             AND e.actor_kind='user' AND e.content_text IS NOT NULL)
                            OR (ce.evidence_role=? AND e.actor_kind='system'
                                AND e.event_type='legacy_memory_record'
                                AND e.source_origin='legacy_v1_import'
                                AND c.subject_key='preference.color'
                                AND c.curator_policy_version='favorite_color_v1_bridge_v1'))
               )
             ORDER BY c.created_at_us DESC, c.claim_id
             LIMIT ?
        """
        arguments: list[object] = [
            at_us, character_id, real_scope_id, DURABLE_CORE_FACT, subject_key, DURABLE_ASSERTION_SCOPE,
            at_us, at_us, at_us, *sorted(excluded), *sorted(DURABLE_USER_EVIDENCE_ROLES),
            DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE, limit,
        ]
        rows = self.store.connection.execute(statement, arguments).fetchall()
        candidates = tuple(self._durable_record(row) for row in rows)
        return DurableCoreLookup(character_id, subject_key, historical_at_us, candidates)

    def list_current_durable_core(
        self,
        character_id: str,
        *,
        limit: int = 16,
    ) -> tuple[DurableCoreRecord, ...]:
        """List a bounded governed current-fact set through exact-slot lookups."""
        character_id = _require_uuid(character_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise StoreError("durable current list limit must be an integer from 1 to 16.")
        rows = self.store.connection.execute(
            """SELECT DISTINCT subject_key FROM claims
                 WHERE character_id=? AND truth_scope_id=? AND claim_type=?
                   AND assertion_scope=? AND subject_key IS NOT NULL
                 ORDER BY updated_at_us DESC, subject_key LIMIT 32""",
            (character_id, self.store.default_truth_scope_id(character_id),
             DURABLE_CORE_FACT, DURABLE_ASSERTION_SCOPE),
        ).fetchall()
        selected: list[DurableCoreRecord] = []
        for row in rows:
            try:
                lookup = self.lookup_durable_core(character_id, row["subject_key"], limit=2)
            except StoreError:
                continue
            if len(lookup.candidates) == 1:
                selected.append(lookup.candidates[0])
            if len(selected) >= limit:
                break
        return tuple(selected)

    def lookup_active_state(
        self,
        character_id: str,
        subject_key: str,
        *,
        historical_at_us: int | None = None,
        truth_scope_id: str | None = None,
    ) -> ActiveStateLookup:
        """Read one governed active-state slot without semantic retrieval.

        Lifecycle, validity, character, provenance, and user-evidence filters
        all run before the bounded two-row corruption check.
        """
        character_id = _require_uuid(character_id)
        try:
            subject_key = validate_active_state_subject_key(subject_key)
        except ValueError as error:
            raise StoreError(str(error)) from error
        if historical_at_us is not None and (isinstance(historical_at_us, bool) or not isinstance(historical_at_us, int)):
            raise StoreError("historical_at_us must be an integer timestamp.")
        at_us = historical_at_us if historical_at_us is not None else utc_now_us()
        if self.store.connection.execute(
            "SELECT 1 FROM characters WHERE character_id=?", (character_id,),
        ).fetchone() is None:
            return ActiveStateLookup(character_id, subject_key, historical_at_us, None)
        truth_scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id), require_active=False,
        )
        target_kind, target_ref, _ = parse_active_state_subject_key(subject_key)
        if target_kind == "scene" and not self._scene_subject_exists_at(character_id, str(target_ref), at_us, truth_scope_id):
            return ActiveStateLookup(character_id, subject_key, historical_at_us, None)
        excluded = HISTORICAL_EXCLUDED_STATUSES if historical_at_us is not None else CURRENT_EXCLUDED_STATUSES
        status_placeholders = ",".join("?" for _ in excluded)
        evidence_placeholders = ",".join("?" for _ in ACTIVE_STATE_EVIDENCE_ROLES)
        explicit_evidence_placeholders = ",".join("?" for _ in ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES)
        rows = self.store.connection.execute(
            f"""SELECT c.*, COALESCE((SELECT status FROM claim_status_events s
                   WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                     AND s.created_at_us <= ?
                   ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status,
                   (SELECT MAX(e.recorded_at_us) FROM claim_evidence ce
                       JOIN events e ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                       WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                         AND ce.evidence_role IN ({explicit_evidence_placeholders})
                         AND e.actor_kind='user' AND e.redaction_state='active'
                         AND e.content_text IS NOT NULL AND e.recorded_at_us <= ?)
                     AS last_confirmed_at_us
                  FROM claims c
                 WHERE c.character_id=? AND c.truth_scope_id=? AND c.claim_type=? AND c.subject_key=?
                   AND c.assertion_scope=? AND c.provenance_state='complete'
                   AND (c.valid_from_us IS NULL OR c.valid_from_us <= ?)
                   AND (c.valid_to_us IS NULL OR c.valid_to_us > ?)
                   AND COALESCE((SELECT status FROM claim_status_events s
                         WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id
                           AND s.created_at_us <= ?
                         ORDER BY s.status_event_id DESC LIMIT 1), 'active') NOT IN ({status_placeholders})
                   AND EXISTS (SELECT 1 FROM claim_evidence ce JOIN events e
                         ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                        WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                          AND ce.evidence_role IN ({evidence_placeholders})
                          AND ((ce.evidence_role='governed_companion_action' AND e.actor_kind='assistant')
                               OR (ce.evidence_role!='governed_companion_action' AND e.actor_kind='user'))
                          AND e.redaction_state='active' AND e.content_text IS NOT NULL)
                 ORDER BY c.created_at_us DESC, c.claim_id LIMIT 2""",
            [at_us, *sorted(ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES), at_us,
             character_id, truth_scope_id, ACTIVE_STATE, subject_key, ACTIVE_STATE_ASSERTION_SCOPE,
             at_us, at_us, at_us, *sorted(excluded), *sorted(ACTIVE_STATE_EVIDENCE_ROLES)],
        ).fetchall()
        if len(rows) > 1:
            raise StoreError("multiple eligible active-state values exist for this singleton slot.")
        return ActiveStateLookup(
            character_id, subject_key, historical_at_us,
            self._active_state_record(rows[0]) if rows else None,
        )

    def lookup_actor_state(
        self,
        character_id: str,
        actor: str,
        attribute: str,
        *,
        historical_at_us: int | None = None,
        truth_scope_id: str | None = None,
    ) -> ActiveStateLookup:
        """Exact bounded state lookup for the stable user/companion targets."""
        return self.lookup_active_state(
            character_id, actor_state_subject_key(actor, attribute), historical_at_us=historical_at_us,
            truth_scope_id=truth_scope_id,
        )

    def _scene_subject_exists_at(self, character_id: str, scene_subject_id: str, at_us: int, truth_scope_id: str) -> bool:
        return self.store.connection.execute(
            """SELECT 1 FROM active_scene_subjects WHERE character_id=? AND truth_scope_id=? AND scene_subject_id=?
                 AND introduced_at_us <= ? AND (retired_at_us IS NULL OR retired_at_us > ?)""",
            (character_id, truth_scope_id, scene_subject_id, at_us, at_us),
        ).fetchone() is not None

    def list_scene_subjects(
        self,
        character_id: str,
        *,
        historical_at_us: int | None = None,
        limit: int = MAX_ACTIVE_SCENE_SUBJECTS,
        truth_scope_id: str | None = None,
    ) -> tuple[ActiveSceneSubjectRecord, ...]:
        """List at most the governed current-scene roster; never archive-scan."""
        character_id = _require_uuid(character_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ACTIVE_SCENE_SUBJECTS:
            raise StoreError("scene-subject limit exceeds the governed bound.")
        at_us = historical_at_us if historical_at_us is not None else utc_now_us()
        truth_scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id), require_active=False,
        )
        if isinstance(at_us, bool) or not isinstance(at_us, int):
            raise StoreError("historical_at_us must be an integer timestamp.")
        rows = self.store.connection.execute(
            """SELECT s.character_id, s.scene_subject_id, s.introduced_at_us, s.retired_at_us,
                      s.last_referenced_at_us, s.identity_strength,
                      CASE WHEN EXISTS(
                          SELECT 1 FROM active_scene_relations r
                           WHERE r.character_id=s.character_id AND r.truth_scope_id=s.truth_scope_id
                             AND r.valid_from_us<=? AND (r.valid_to_us IS NULL OR r.valid_to_us>?)
                             AND ((r.target_kind='scene' AND r.target_actor=s.scene_subject_id)
                                  OR r.cause_subject_id=s.scene_subject_id)
                      ) THEN 'current' ELSE 'dormant' END AS lifecycle_state
                 FROM active_scene_subjects s WHERE s.character_id=? AND s.truth_scope_id=? AND s.introduced_at_us <= ?
                   AND (retired_at_us IS NULL OR retired_at_us > ?)
                 ORDER BY CASE lifecycle_state WHEN 'current' THEN 0 ELSE 1 END,
                          last_referenced_at_us DESC, scene_subject_id LIMIT ?""",
            (at_us, at_us, character_id, truth_scope_id, at_us, at_us, limit),
        ).fetchall()
        return tuple(ActiveSceneSubjectRecord(
            row["character_id"], row["scene_subject_id"], int(row["introduced_at_us"]),
            int(row["retired_at_us"]) if row["retired_at_us"] is not None else None, truth_scope_id,
            int(row["last_referenced_at_us"]) if row["last_referenced_at_us"] is not None else None,
            str(row["identity_strength"]), str(row["lifecycle_state"]),
        ) for row in rows)

    def list_retired_scene_subjects(
        self,
        character_id: str,
        *,
        truth_scope_id: str | None = None,
        limit: int = 32,
    ) -> tuple[ActiveSceneSubjectRecord, ...]:
        """Return a bounded recent retired roster for explicit reactivation only."""
        character_id = _require_uuid(character_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise StoreError("retired scene-subject limit exceeds the governed bound")
        truth_scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id),
            require_active=False,
        )
        rows = self.store.connection.execute(
            """SELECT character_id, scene_subject_id, introduced_at_us, retired_at_us,
                      last_referenced_at_us, identity_strength
                 FROM active_scene_subjects
                WHERE character_id=? AND truth_scope_id=? AND retired_at_us IS NOT NULL
                ORDER BY retired_at_us DESC, scene_subject_id LIMIT ?""",
            (character_id, truth_scope_id, limit),
        ).fetchall()
        return tuple(ActiveSceneSubjectRecord(
            str(row["character_id"]), str(row["scene_subject_id"]), int(row["introduced_at_us"]),
            int(row["retired_at_us"]), truth_scope_id,
            int(row["last_referenced_at_us"]) if row["last_referenced_at_us"] is not None else None,
            str(row["identity_strength"]), "retired",
        ) for row in rows)

    def lookup_scene_attributes(
        self,
        character_id: str,
        scene_subject_id: str,
        *,
        historical_at_us: int | None = None,
        truth_scope_id: str | None = None,
    ) -> tuple[ActiveStateRecord, ...]:
        """Read a bounded attribute bundle for one current/historical subject."""
        character_id = _require_uuid(character_id)
        # Reuse subject-key validation for the opaque ID and a governed field.
        scene_state_subject_key(scene_subject_id, "kind")
        at_us = historical_at_us if historical_at_us is not None else utc_now_us()
        truth_scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id), require_active=False,
        )
        if isinstance(at_us, bool) or not isinstance(at_us, int):
            raise StoreError("historical_at_us must be an integer timestamp.")
        if not self._scene_subject_exists_at(character_id, scene_subject_id, at_us, truth_scope_id):
            return ()
        excluded = HISTORICAL_EXCLUDED_STATUSES if historical_at_us is not None else CURRENT_EXCLUDED_STATUSES
        status_placeholders = ",".join("?" for _ in excluded)
        evidence_placeholders = ",".join("?" for _ in ACTIVE_STATE_EVIDENCE_ROLES)
        explicit_evidence_placeholders = ",".join("?" for _ in ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES)
        prefix = f"active.scene.{scene_subject_id}."
        rows = self.store.connection.execute(
            f"""SELECT c.*, COALESCE((SELECT status FROM claim_status_events s
                   WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id AND s.created_at_us <= ?
                   ORDER BY s.status_event_id DESC LIMIT 1), 'active') AS effective_status,
                   (SELECT MAX(e.recorded_at_us) FROM claim_evidence ce
                       JOIN events e ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                       WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                         AND ce.evidence_role IN ({explicit_evidence_placeholders}) AND e.actor_kind='user'
                         AND e.redaction_state='active' AND e.content_text IS NOT NULL
                         AND e.recorded_at_us <= ?) AS last_confirmed_at_us
                 FROM claims c WHERE c.character_id=? AND c.truth_scope_id=? AND c.claim_type=?
                   AND c.subject_key >= ? AND c.subject_key < ? AND c.assertion_scope=?
                   AND c.provenance_state='complete' AND (c.valid_from_us IS NULL OR c.valid_from_us <= ?)
                   AND (c.valid_to_us IS NULL OR c.valid_to_us > ?)
                   AND COALESCE((SELECT status FROM claim_status_events s
                       WHERE s.character_id=c.character_id AND s.claim_id=c.claim_id AND s.created_at_us <= ?
                       ORDER BY s.status_event_id DESC LIMIT 1), 'active') NOT IN ({status_placeholders})
                   AND EXISTS (SELECT 1 FROM claim_evidence ce JOIN events e
                       ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                       WHERE ce.character_id=c.character_id AND ce.claim_id=c.claim_id
                         AND ce.evidence_role IN ({evidence_placeholders})
                         AND ((ce.evidence_role='governed_companion_action' AND e.actor_kind='assistant')
                              OR (ce.evidence_role!='governed_companion_action' AND e.actor_kind='user'))
                         AND e.redaction_state='active' AND e.content_text IS NOT NULL)
                 ORDER BY c.subject_key, c.created_at_us DESC, c.claim_id LIMIT ?""",
            [at_us, *sorted(ACTIVE_STATE_EXPLICIT_EVIDENCE_ROLES), at_us, character_id, truth_scope_id, ACTIVE_STATE,
             prefix, prefix + "\uffff", ACTIVE_STATE_ASSERTION_SCOPE, at_us, at_us, at_us,
             *sorted(excluded), *sorted(ACTIVE_STATE_EVIDENCE_ROLES), MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT + 1],
        ).fetchall()
        if len(rows) > MAX_ACTIVE_SCENE_ATTRIBUTES_PER_SUBJECT:
            raise StoreError("scene subject exceeds its governed current attribute bound.")
        return tuple(self._active_state_record(row) for row in rows)

    def list_scene_relations(
        self, character_id: str, *, historical_at_us: int | None = None,
        truth_scope_id: str | None = None, limit: int = 32,
    ) -> tuple[ActiveSceneRelationRecord, ...]:
        character_id = _require_uuid(character_id)
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or not 1 <= limit <= MAX_CURRENT_SCENE_RELATIONS):
            raise StoreError("scene relation limit exceeds the governed bound")
        at_us = historical_at_us if historical_at_us is not None else utc_now_us()
        scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id), require_active=False,
        )
        rows = self.store.connection.execute(
            """SELECT * FROM active_scene_relations WHERE character_id=? AND truth_scope_id=?
                 AND valid_from_us <= ? AND (valid_to_us IS NULL OR valid_to_us > ?)
                 ORDER BY valid_from_us DESC, relation_id LIMIT ?""",
            (character_id, scope_id, at_us, at_us, limit),
        ).fetchall()
        return tuple(self._scene_relation_record(row, character_id, scope_id) for row in rows)

    @staticmethod
    def _scene_relation_record(row, character_id: str, scope_id: str) -> ActiveSceneRelationRecord:
        return ActiveSceneRelationRecord(
            str(row["relation_id"]), character_id, str(row["target_actor"]),
            str(row["facet"]) if row["facet"] is not None else None,
            str(row["predicate"]), str(row["cause_kind"]), str(row["cause"]),
            int(row["valid_from_us"]),
            int(row["valid_to_us"]) if row["valid_to_us"] is not None else None,
            scope_id, target_kind=str(row["target_kind"]),
            side=str(row["side"]) if row["side"] is not None else None,
            cause_subject_id=(
                str(row["cause_subject_id"]) if row["cause_subject_id"] is not None else None
            ),
            semantic_family=(
                str(row["semantic_family"]) if row["semantic_family"] is not None else None
            ),
            quantity=int(row["quantity"]) if row["quantity"] is not None else None,
            effect_state=str(row["effect_state"]) if row["effect_state"] is not None else None,
        )

    def list_actor_relations(
        self,
        character_id: str,
        target: str,
        predicates: Iterable[str],
        *,
        limit: int = 32,
    ) -> tuple[ActiveSceneRelationRecord, ...]:
        """Read one bounded actor/predicate lane independent of scene clutter."""
        character_id = _require_uuid(character_id)
        if target not in {"user", "companion"}:
            raise StoreError("scene relation actor target is not governed")
        names = tuple(sorted(set(predicates)))
        if not names or any(item not in RELATION_PREDICATES for item in names):
            raise StoreError("scene relation predicate filter is not governed")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise StoreError("actor relation limit exceeds the governed bound")
        scope_id = self.active_truth_scope(character_id).truth_scope_id
        at_us = utc_now_us()
        placeholders = ",".join("?" for _ in names)
        rows = self.store.connection.execute(
            f"""SELECT * FROM active_scene_relations
                  WHERE character_id=? AND truth_scope_id=? AND target_kind='actor'
                    AND target_actor=? AND predicate IN ({placeholders})
                    AND valid_from_us <= ? AND (valid_to_us IS NULL OR valid_to_us > ?)
                  ORDER BY valid_from_us DESC, relation_id LIMIT ?""",
            (character_id, scope_id, target, *names, at_us, at_us, limit),
        ).fetchall()
        return tuple(self._scene_relation_record(row, character_id, scope_id) for row in rows)

    def list_capability_source_relations(
        self,
        character_id: str,
        *,
        target: str = "companion",
    ) -> tuple[ActiveSceneRelationRecord, ...]:
        """Read hard capability sources before lower-value descriptive rows."""
        character_id = _require_uuid(character_id)
        if target not in {"user", "companion"}:
            raise StoreError("capability target must be a governed actor")
        scope_id = self.active_truth_scope(character_id).truth_scope_id
        at_us = utc_now_us()
        rows = self.store.connection.execute(
            """SELECT * FROM active_scene_relations
                 WHERE character_id=? AND truth_scope_id=? AND target_kind='actor'
                   AND target_actor=? AND valid_from_us <= ?
                   AND (valid_to_us IS NULL OR valid_to_us > ?)
                   AND (semantic_family IS NOT NULL OR facet IN ('eyes','mouth','hands')
                        OR predicate IN ('holding','carrying','driving','riding','seated_in',
                                         'using','supported_by'))
                 ORDER BY valid_from_us DESC, relation_id LIMIT ?""",
            (character_id, scope_id, target, at_us, at_us, MAX_CURRENT_SCENE_RELATIONS + 1),
        ).fetchall()
        if len(rows) > MAX_CURRENT_SCENE_RELATIONS:
            raise StoreError("capability source relation set exceeds the governed bound")
        return tuple(self._scene_relation_record(row, character_id, scope_id) for row in rows)

    def capability_effects(self, character_id: str, *, target: str = "companion") -> CapabilityEffects:
        """Derive the one shared envelope; never persist duplicate effects."""
        if target not in {"user", "companion"}:
            raise StoreError("capability target must be a governed actor")
        scope_id = self.active_truth_scope(character_id).truth_scope_id
        activity = self.lookup_actor_state(character_id, target, "activity").state
        posture = self.lookup_actor_state(character_id, target, "posture").state
        return compose_capability_effects(
            self.list_capability_source_relations(character_id, target=target),
            truth_scope_id=scope_id,
            companion_activity=activity.value if activity is not None else None,
            companion_posture=posture.value if posture is not None else None,
            target=target,
        )

    @staticmethod
    def _open_thread_record(store: MemoryV2Store, row) -> OpenThreadRecord:
        evidence_rows = store.connection.execute(
            """SELECT ce.event_id FROM claim_evidence ce JOIN events e
                 ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=? AND ce.evidence_role IN ({})
                   AND e.actor_kind='user' AND e.redaction_state='active' AND e.content_text IS NOT NULL
                 ORDER BY ce.created_at_us DESC, ce.event_id DESC LIMIT 12""".format(
                ",".join("?" for _ in OPEN_THREAD_EVIDENCE_ROLES),
            ),
            [row["character_id"], row["thread_id"], *sorted(OPEN_THREAD_EVIDENCE_ROLES)],
        ).fetchall()
        return OpenThreadRecord(
            str(row["thread_id"]), str(row["character_id"]), str(row["thread_kind"]),
            str(row["participant_scope"]), str(row["description"]), row["temporal_anchor"],
            str(row["status"]), int(row["opened_at_us"]), int(row["last_mentioned_at_us"]),
            int(row["closed_at_us"]) if row["closed_at_us"] is not None else None,
            tuple(str(item["event_id"]) for item in evidence_rows), row["truth_scope_id"],
        )

    def list_open_threads(
        self,
        character_id: str,
        *,
        limit: int = MAX_CURRENT_OPEN_THREADS,
        truth_scope_id: str | None = None,
    ) -> OpenThreadLookup:
        """List only bounded current unresolved continuity records."""
        character_id = _require_uuid(character_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CURRENT_OPEN_THREADS:
            raise StoreError("open-thread limit exceeds the governed bound.")
        truth_scope_id = self.store._require_truth_scope(
            character_id, truth_scope_id or self.store.active_truth_scope_id(character_id), require_active=False,
        )
        role_placeholders = ",".join("?" for _ in OPEN_THREAD_EVIDENCE_ROLES)
        rows = self.store.connection.execute(
            f"""SELECT t.* FROM open_threads t JOIN claims c
                   ON c.character_id=t.character_id AND c.claim_id=t.thread_id
                 WHERE t.character_id=? AND t.truth_scope_id=? AND t.status='open' AND c.claim_type=?
                   AND c.assertion_scope=? AND c.provenance_state='complete'
                   AND c.valid_to_us IS NULL
                   AND EXISTS (SELECT 1 FROM claim_evidence ce JOIN events e
                         ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                        WHERE ce.character_id=t.character_id AND ce.claim_id=t.thread_id
                          AND ce.evidence_role IN ({role_placeholders})
                          AND e.actor_kind='user' AND e.redaction_state='active'
                          AND e.content_text IS NOT NULL)
                 ORDER BY t.last_mentioned_at_us DESC, t.thread_id LIMIT ?""",
            [character_id, truth_scope_id, OPEN_THREAD, OPEN_THREAD_ASSERTION_SCOPE,
             *sorted(OPEN_THREAD_EVIDENCE_ROLES), limit],
        ).fetchall()
        return OpenThreadLookup(character_id, tuple(self._open_thread_record(self.store, row) for row in rows))

    def list_open_thread_evidence(
        self,
        character_id: str,
        thread_id: str,
    ) -> tuple[OpenThreadEvidenceRecord, ...]:
        """Return bounded canonical user evidence used to rebuild lexical identity."""
        character_id = _require_uuid(character_id)
        try:
            thread_id = validate_open_thread_id(thread_id)
        except ValueError as error:
            raise StoreError(str(error)) from error
        role_placeholders = ",".join("?" for _ in OPEN_THREAD_EVIDENCE_ROLES)
        rows = self.store.connection.execute(
            f"""SELECT ce.event_id, ce.evidence_role, e.content_text, e.recorded_at_us
                  FROM claim_evidence ce JOIN events e
                    ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=?
                   AND ce.evidence_role IN ({role_placeholders})
                   AND e.actor_kind='user' AND e.redaction_state='active'
                   AND e.content_text IS NOT NULL
                 ORDER BY ce.created_at_us DESC, ce.event_id DESC LIMIT 12""",
            [character_id, thread_id, *sorted(OPEN_THREAD_EVIDENCE_ROLES)],
        ).fetchall()
        return tuple(reversed(tuple(
            OpenThreadEvidenceRecord(
                str(row["event_id"]), str(row["evidence_role"]),
                str(row["content_text"]), int(row["recorded_at_us"]),
            ) for row in rows
        )))

    def get_open_thread(self, character_id: str, thread_id: str) -> OpenThreadRecord | None:
        """Read one same-character thread history record without archive scans."""
        character_id = _require_uuid(character_id)
        try:
            thread_id = validate_open_thread_id(thread_id)
        except ValueError as error:
            raise StoreError(str(error)) from error
        row = self.store.connection.execute(
            """SELECT t.* FROM open_threads t JOIN claims c
                   ON c.character_id=t.character_id AND c.claim_id=t.thread_id
                 WHERE t.character_id=? AND t.thread_id=? AND c.claim_type=?
                   AND c.assertion_scope=? AND c.provenance_state='complete'""",
            (character_id, thread_id, OPEN_THREAD, OPEN_THREAD_ASSERTION_SCOPE),
        ).fetchone()
        return self._open_thread_record(self.store, row) if row is not None else None

    def supersede(self, character_id: str, old_memory_id: str, new_memory_id: str, *, reason: str = "user_correction") -> None:
        character_id = _require_uuid(character_id)
        if old_memory_id == new_memory_id:
            raise StoreError("a memory cannot supersede itself")
        for memory_id in (old_memory_id, new_memory_id):
            if self.get_memory(character_id, memory_id) is None:
                raise StoreError("memory is absent for this character")
        durable = self.store.connection.execute(
            "SELECT 1 FROM claims WHERE character_id=? AND claim_id IN (?, ?) AND claim_type=? LIMIT 1",
            (character_id, str(old_memory_id), str(new_memory_id), DURABLE_CORE_FACT),
        ).fetchone()
        if durable is not None:
            raise StoreError("durable corrections must use add_durable_claim with supersedes_claim_id.")
        with self.store.transaction():
            self.store.connection.execute(
                "INSERT INTO claim_relations(character_id, from_claim_id, to_claim_id, relation_type, created_at_us) VALUES (?, ?, ?, 'supersedes', ?)",
                (character_id, new_memory_id, old_memory_id, utc_now_us()),
            )
            self.store.connection.execute(
                "INSERT INTO claim_status_events(character_id, claim_id, status, reason, source_event_id, actor_kind, created_at_us) VALUES (?, ?, 'superseded', ?, NULL, 'user', ?)",
                (character_id, old_memory_id, reason, utc_now_us()),
            )

    def archive(self, character_id: str, memory_id: str, *, reason: str = "user_archive") -> None:
        character_id = _require_uuid(character_id)
        if self.get_memory(character_id, memory_id) is None:
            raise StoreError("memory is absent for this character")
        self.store.add_status(character_id, memory_id, "archived", reason=reason, actor_kind="user")

    def export(self, *, character_id: str | None = None) -> dict:
        """Deterministic portable backup/export; SQLite remains the live store."""
        arguments: tuple[object, ...] = ()
        statement = "SELECT * FROM characters"
        if character_id is not None:
            statement += " WHERE character_id=?"
            arguments = (_require_uuid(character_id),)
        characters = [dict(row) for row in self.store.connection.execute(statement + " ORDER BY character_id", arguments).fetchall()]
        scope = tuple(row["character_id"] for row in characters)
        if not scope:
            return {"format": "aifren-memory-v2-export", "schema_version": self.store.schema_version(), "characters": [], "memories": [], "status_events": [], "relations": [], "active_scene_subjects": [], "active_scene_subject_lifecycle_events": [], "active_scene_relations": [], "active_scene_relation_events": []}
        placeholders = ",".join("?" for _ in scope)
        def rows(table: str, order: str) -> list[dict]:
            return [dict(row) for row in self.store.connection.execute(
                f"SELECT * FROM {table} WHERE character_id IN ({placeholders}) ORDER BY {order}", scope
            ).fetchall()]
        return {
            "format": "aifren-memory-v2-export",
            "schema_version": self.store.schema_version(),
            "characters": characters,
            "memories": rows("claims", "character_id, claim_id"),
            "status_events": rows("claim_status_events", "character_id, status_event_id"),
            "relations": rows("claim_relations", "character_id, relation_id"),
            "evidence": rows("claim_evidence", "character_id, claim_id, event_id, evidence_role"),
            "active_scene_subjects": rows("active_scene_subjects", "character_id, scene_subject_id"),
            "active_scene_subject_lifecycle_events": rows(
                "active_scene_subject_lifecycle_events",
                "character_id, lifecycle_event_id",
            ),
            "active_scene_relations": rows("active_scene_relations", "character_id, relation_id"),
            "active_scene_relation_events": rows("active_scene_relation_events", "character_id, relation_event_id"),
        }

    @staticmethod
    def _bounded_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise StoreError("limit must be an integer from 1 to 100")
        return limit

    @staticmethod
    def _record(row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["claim_id"], character_id=row["character_id"], memory_type=row["claim_type"],
            content=row["content"], importance=row["importance"], status=row["effective_status"],
            created_at_us=row["created_at_us"], valid_from_us=row["valid_from_us"], valid_to_us=row["valid_to_us"],
            updated_at_us=row["updated_at_us"],
            provenance_state=row["provenance_state"], source_reference=row["source_reference"],
        )

    def _durable_record(self, row) -> DurableCoreRecord:
        evidence_placeholders = ",".join("?" for _ in DURABLE_EVIDENCE_ROLES)
        evidence_rows = self.store.connection.execute(
            f"""SELECT ce.event_id FROM claim_evidence ce JOIN events e
                   ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=?
                   AND ce.evidence_role IN ({evidence_placeholders})
                   AND ((ce.evidence_role=? AND e.actor_kind='system')
                        OR (ce.evidence_role<>? AND e.actor_kind='user'))
                   AND e.redaction_state='active'
                 ORDER BY ce.event_id""",
            (row["character_id"], row["claim_id"], *sorted(DURABLE_EVIDENCE_ROLES),
             DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE, DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE),
        ).fetchall()
        return DurableCoreRecord(
            claim_id=row["claim_id"], character_id=row["character_id"], subject_key=row["subject_key"],
            content=row["content"], status=row["effective_status"], created_at_us=row["created_at_us"],
            valid_from_us=row["valid_from_us"], valid_to_us=row["valid_to_us"],
            evidence_event_ids=tuple(item["event_id"] for item in evidence_rows),
        )

    def _active_state_record(self, row) -> ActiveStateRecord:
        evidence_placeholders = ",".join("?" for _ in ACTIVE_STATE_EVIDENCE_ROLES)
        evidence_rows = self.store.connection.execute(
            f"""SELECT ce.event_id FROM claim_evidence ce JOIN events e
                   ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=?
                   AND ce.evidence_role IN ({evidence_placeholders})
                   AND ((ce.evidence_role='governed_companion_action' AND e.actor_kind='assistant')
                        OR (ce.evidence_role!='governed_companion_action' AND e.actor_kind='user'))
                   AND e.redaction_state='active'
                 ORDER BY ce.event_id""",
            (row["character_id"], row["claim_id"], *sorted(ACTIVE_STATE_EVIDENCE_ROLES)),
        ).fetchall()
        return ActiveStateRecord(
            state_id=row["claim_id"], character_id=row["character_id"], subject_key=row["subject_key"],
            value=row["content"], status=row["effective_status"], valid_from_us=row["valid_from_us"],
            valid_to_us=row["valid_to_us"], last_confirmed_at_us=row["last_confirmed_at_us"],
            evidence_event_ids=tuple(item["event_id"] for item in evidence_rows),
            truth_scope_id=row["truth_scope_id"],
        )
