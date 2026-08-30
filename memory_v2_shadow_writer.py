"""Best-effort V1 mutation observer for the non-authoritative Memory V2 store.

This deliberately does not participate in context construction. V1 writes
first; only a successful V1 mutation is mirrored, and any SQLite failure is
reported without rolling back or invalidating the canonical JSON record.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any
import uuid

from memory_v2_store.retrieval_models import RetrievalQuery
from memory_v2_store import (EmbeddingLifecycle, MemoryV2Store,
                             MemoryV2Repository,
                             ActiveStateProposal,
                             MiniLMEmbeddingProvider,
                             extract_identity_name_assertion,
                             extract_headwear_state_assertion,
                             import_v1_memories, shadow_v1_mutation)
from memory_v2_store.store import parse_timestamp_us
from memory_v2_telemetry import record_dual_read
from memory_v2_adaptive import AdaptiveShadowRetrieval, WorkingRecallCache
from memory_v2_store.production_import import v1_import_scope


DEFAULT_V2_DIRECTORY = "memory_v2"
DEFAULT_V2_DATABASE = "memory_v2.sqlite3"
_IDENTITY_NAME_NAMESPACE = uuid.UUID("d4ce5410-23e6-4e4d-b8c4-d07c5df823c0")
_ACTIVE_HEADWEAR_NAMESPACE = uuid.UUID("ae7aeac2-a3de-44fb-a3bd-52fa12c8a1a7")
_ACTIVE_STATE_PROPOSAL_NAMESPACE = uuid.UUID("7aa46a4f-b16f-42cc-96e1-78a100070112")
_CURRENT_CONTINUITY_NAMESPACE = uuid.UUID("c52b18b4-34b4-48a5-a59e-35daa5f67631")
_DURABLE_FACT_NAMESPACE = uuid.UUID("f62048a4-a8d1-48f5-b878-608633b126cc")
_COMPANION_ACTION_NAMESPACE = uuid.UUID("290bc5b4-ecdd-4cbd-89e4-f571634b092d")


def default_v2_path(application_dir: str | Path) -> Path:
    return Path(application_dir).resolve() / DEFAULT_V2_DIRECTORY / DEFAULT_V2_DATABASE


class MemoryV2ShadowWriter:
    """Character-scoped, fail-open shadow writer and reconciliation boundary."""

    def __init__(
        self,
        application_dir: str | Path,
        *,
        character_id: str,
        display_name: str,
        memory_file: str | Path,
        database_path: str | Path | None = None,
    ) -> None:
        self.application_dir = Path(application_dir).resolve()
        self.character_id = str(character_id)
        self.display_name = str(display_name)
        self.memory_file = Path(memory_file).resolve()
        self.database_path = Path(database_path).resolve() if database_path else default_v2_path(self.application_dir)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = MemoryV2Store(str(self.database_path))
        self.last_error: str | None = None
        self._embedding_provider = None
        self._working_recall = WorkingRecallCache()

    def reconcile(self) -> dict[str, Any]:
        """Idempotently catch up after a crash, rebuild, or disabled observer."""
        try:
            result = import_v1_memories(
                self.store,
                self.memory_file.parent,
                character_id=self.character_id,
                display_name=self.display_name,
            )
            # Reconciliation is the recovery/index-maintenance boundary. It
            # may rebuild a derived index after a real import, never on every
            # ordinary retrieval.
            if result.imported or result.superseded:
                EmbeddingLifecycle(
                    self.store, self._provider(), include_legacy_unverified=True,
                ).rebuild_stale_or_missing()
            self.last_error = None
            return {"state": "ok", "imported": result.imported, "unchanged": result.unchanged,
                    "superseded": result.superseded, "skipped": result.skipped, "errors": list(result.errors)}
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "error": self.last_error}

    def observe(self, mutation: Any) -> None:
        """Receive a ``MemoryMutation`` only after V1's atomic save succeeded."""
        try:
            shadow_v1_mutation(
                self.store,
                character_id=self.character_id,
                display_name=self.display_name,
                kind=str(getattr(mutation, "kind", "")),
                record=getattr(mutation, "record", None),
                previous=getattr(mutation, "previous", None),
            )
            record = getattr(mutation, "record", None)
            if isinstance(record, dict) and isinstance(record.get("id"), int):
                mapped = self.store.connection.execute(
                    "SELECT claim_id FROM v1_import_records WHERE source_scope=? AND legacy_memory_id=?",
                    (v1_import_scope(self.character_id), record["id"]),
                ).fetchone()
                if mapped is not None:
                    EmbeddingLifecycle(
                        self.store, self._provider(), include_legacy_unverified=True,
                    ).rebuild_claims([mapped["claim_id"]])
            self.last_error = None
        except Exception as error:
            self.last_error = type(error).__name__
            # Memory invokes observers after its own successful save. Raising
            # lets the central observer boundary log one concise diagnostic,
            # while preserving V1 success.
            raise

    def observe_canonical_user_message(
        self,
        message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Mirror one already-persisted canonical user name assertion.

        This intentionally recognizes only the tiny deterministic
        ``identity.name`` grammar in ``memory_v2_store.identity_name``.  It
        never changes V1, prompt construction, or generic V2 retrieval.
        """
        try:
            if not isinstance(message, dict) or message.get("role") != "user":
                return {"state": "ignored", "reason": "not_user_message"}
            if isinstance(conversation_index, bool) or not isinstance(conversation_index, int) or conversation_index < 0:
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            content = message.get("content")
            timestamp = message.get("timestamp")
            assertion = extract_identity_name_assertion(content)
            if assertion is None:
                return {"state": "ignored", "reason": "not_explicit_identity_name"}
            if not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "missing_canonical_timestamp"}
            try:
                recorded_at_us = parse_timestamp_us(timestamp)
            except (TypeError, ValueError):
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _IDENTITY_NAME_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            ))
            repository = MemoryV2Repository(self.store)
            repository.ensure_character(self.character_id, self.display_name, legacy_config_key="characters/default")
            if self.store.active_truth_scope_id(self.character_id) != self.store.default_truth_scope_id(self.character_id):
                return {"state": "ignored", "reason": "truth_scope_not_real_world"}
            existing_event = self.store.connection.execute(
                "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                (self.character_id, event_id),
            ).fetchone()
            if existing_event is None:
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence, event_type="canonical_user_message",
                    actor_kind="user", recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=content, source_origin="canonical_conversation",
                    source_reference=source_reference,
                )
            elif (existing_event["actor_kind"] != "user" or existing_event["content_text"] != content
                  or existing_event["source_reference"] != source_reference):
                return {"state": "ignored", "reason": "canonical_event_identity_conflict"}

            claim_content = f"The user's name is {assertion.value}."
            current = repository.lookup_durable_core(self.character_id, "identity.name")
            if len(current.candidates) > 1:
                return {"state": "ignored", "reason": "multiple_current_identity_names"}
            if current.candidates and current.candidates[0].content == claim_content:
                return {"state": "unchanged", "claim_id": current.candidates[0].claim_id, "event_id": event_id}

            claim_id = str(uuid.uuid5(
                _IDENTITY_NAME_NAMESPACE, f"durable-name:{self.character_id}:{event_id}"
            ))
            self.store.add_durable_claim(
                self.character_id, claim_id, subject_key="identity.name", content=claim_content,
                evidence_event_id=event_id, evidence_role="direct_user_statement",
                evidence_excerpt_start_cp=assertion.excerpt_start_cp,
                evidence_excerpt_end_cp=assertion.excerpt_end_cp,
                valid_from_us=recorded_at_us, created_at_us=recorded_at_us,
                curator_name="identity_name_assertion", curator_version="1",
                curator_policy_version="explicit_v1",
                supersedes_claim_id=current.candidates[0].claim_id if current.candidates else None,
            )
            self.last_error = None
            return {"state": "superseded" if current.candidates else "created", "claim_id": claim_id, "event_id": event_id}
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def observe_governed_companion_action(
        self,
        plan: object,
        assistant_message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Apply one separately validated companion-only action after pair save."""
        try:
            from companion_action import CompanionActionPlan, semantic_action_evidence

            if not isinstance(plan, CompanionActionPlan):
                return {"state": "ignored", "reason": "invalid_action_plan"}
            if (not isinstance(assistant_message, dict)
                    or assistant_message.get("role") != "assistant"):
                return {"state": "ignored", "reason": "not_assistant_message"}
            if (isinstance(conversation_index, bool) or not isinstance(conversation_index, int)
                    or conversation_index < 0):
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            timestamp = assistant_message.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "missing_canonical_timestamp"}
            try:
                recorded_at_us = parse_timestamp_us(timestamp)
            except (TypeError, ValueError):
                recorded_at_us = None
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                assistant_message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            evidence = semantic_action_evidence(plan)
            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _COMPANION_ACTION_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(evidence.encode('utf-8')).hexdigest()}",
            ))
            repository = MemoryV2Repository(self.store)
            repository.ensure_character(
                self.character_id, self.display_name, legacy_config_key="characters/default",
            )
            with self.store.transaction():
                existing = self.store.connection.execute(
                    "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                    (self.character_id, event_id),
                ).fetchone()
                if existing is not None:
                    if (existing["actor_kind"] != "assistant" or existing["content_text"] != evidence
                            or existing["source_reference"] != source_reference):
                        return {"state": "ignored", "reason": "canonical_event_identity_conflict"}
                    return {"state": "unchanged", "event_id": event_id}
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence,
                    event_type="governed_companion_action", actor_kind="assistant",
                    recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=evidence,
                    payload={
                        "family": plan.proposal.family,
                        "operation": plan.proposal.operation,
                        "value": plan.proposal.value,
                    },
                    payload_schema=1, source_origin="governed_companion_action",
                    source_reference=source_reference,
                )
                updates = ()
                relation_updates = ()
                if plan.active_state is not None:
                    updates = self.store.apply_active_state_proposal(
                        self.character_id, plan.active_state, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
                if plan.relations:
                    if any(
                        relation.target_kind != "actor" or relation.target != "companion"
                        or relation.cause_subject_ref is None
                        for relation in plan.relations
                    ):
                        raise ValueError("governed companion relation action is not companion-only")
                    relation_updates = self.store.apply_scene_relation_proposals(
                        self.character_id, plan.relations, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
                    self.store.synchronize_scene_ownership_mirrors(
                        self.character_id, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
            self.last_error = None
            return {
                "state": "applied", "event_id": event_id,
                "active_state_updates": len(updates),
                "scene_relation_updates": len(relation_updates),
            }
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def apply_governed_companion_action(
        self,
        plan: object,
        *,
        decision_reference: str,
        recorded_at_us: int | None = None,
    ) -> dict[str, Any]:
        """Apply one validated backend action without assistant-prose authority.

        The semantic action event is canonical in its own right. The caller
        must apply it before saving or exposing narration that claims success.
        A stable decision reference makes a retry idempotent.
        """
        try:
            from companion_action import CompanionActionPlan, semantic_action_evidence

            if not isinstance(plan, CompanionActionPlan):
                return {"state": "ignored", "reason": "invalid_action_plan"}
            if (not isinstance(decision_reference, str)
                    or re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", decision_reference) is None):
                return {"state": "ignored", "reason": "invalid_decision_reference"}
            if recorded_at_us is None:
                recorded_at_us = int(time.time() * 1_000_000)
            if (isinstance(recorded_at_us, bool) or not isinstance(recorded_at_us, int)
                    or recorded_at_us < 1):
                return {"state": "ignored", "reason": "invalid_action_timestamp"}

            evidence = semantic_action_evidence(plan)
            source_reference = f"governed_companion_action:{decision_reference}"
            event_id = str(uuid.uuid5(
                _COMPANION_ACTION_NAMESPACE,
                f"{self.character_id}:{source_reference}:{hashlib.sha256(evidence.encode('utf-8')).hexdigest()}",
            ))
            repository = MemoryV2Repository(self.store)
            repository.ensure_character(
                self.character_id, self.display_name, legacy_config_key="characters/default",
            )
            with self.store.transaction():
                existing = self.store.connection.execute(
                    "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                    (self.character_id, event_id),
                ).fetchone()
                if existing is not None:
                    if (existing["actor_kind"] != "assistant" or existing["content_text"] != evidence
                            or existing["source_reference"] != source_reference):
                        return {"state": "ignored", "reason": "canonical_event_identity_conflict"}
                    return {"state": "unchanged", "event_id": event_id}
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence,
                    event_type="governed_companion_action", actor_kind="assistant",
                    recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=evidence,
                    payload={
                        "family": plan.proposal.family,
                        "operation": plan.proposal.operation,
                        "value": plan.proposal.value,
                    },
                    payload_schema=1, source_origin="governed_companion_action",
                    source_reference=source_reference,
                )
                updates = ()
                relation_updates = ()
                if plan.active_state is not None:
                    updates = self.store.apply_active_state_proposal(
                        self.character_id, plan.active_state, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
                if plan.relations:
                    if any(
                        relation.target_kind != "actor" or relation.target != "companion"
                        or relation.cause_subject_ref is None
                        for relation in plan.relations
                    ):
                        raise ValueError("governed companion relation action is not companion-only")
                    relation_updates = self.store.apply_scene_relation_proposals(
                        self.character_id, plan.relations, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
                    self.store.synchronize_scene_ownership_mirrors(
                        self.character_id, evidence_event_id=event_id,
                        evidence_role="governed_companion_action",
                    )
            self.last_error = None
            return {
                "state": "applied", "event_id": event_id,
                "active_state_updates": len(updates),
                "scene_relation_updates": len(relation_updates),
            }
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def observe_canonical_user_active_state(
        self,
        message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Mirror one persisted, explicit headwear set/clear assertion only.

        This is deliberately a one-slot deterministic proposal path.  It does
        not inspect history, infer arbitrary actions, affect V1, or construct
        prompt context.
        """
        try:
            if not isinstance(message, dict) or message.get("role") != "user":
                return {"state": "ignored", "reason": "not_user_message"}
            if isinstance(conversation_index, bool) or not isinstance(conversation_index, int) or conversation_index < 0:
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            content = message.get("content")
            timestamp = message.get("timestamp")
            assertion = extract_headwear_state_assertion(content)
            if assertion is None:
                return {"state": "ignored", "reason": "not_explicit_headwear_state"}
            if not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "missing_canonical_timestamp"}
            try:
                recorded_at_us = parse_timestamp_us(timestamp)
            except (TypeError, ValueError):
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _ACTIVE_HEADWEAR_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            ))
            repository = MemoryV2Repository(self.store)
            repository.ensure_character(self.character_id, self.display_name, legacy_config_key="characters/default")
            existing_event = self.store.connection.execute(
                "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                (self.character_id, event_id),
            ).fetchone()
            if existing_event is None:
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence, event_type="canonical_user_message",
                    actor_kind="user", recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=content, source_origin="canonical_conversation",
                    source_reference=source_reference,
                )
            elif (existing_event["actor_kind"] != "user" or existing_event["content_text"] != content
                  or existing_event["source_reference"] != source_reference):
                return {"state": "ignored", "reason": "canonical_event_identity_conflict"}

            current = repository.lookup_active_state(self.character_id, "active.avatar.headwear")
            if assertion.operation == "set":
                if current.state is not None and current.state.value == assertion.value:
                    return {"state": "unchanged", "state_id": current.state.state_id, "event_id": event_id}
                state_id = str(uuid.uuid5(
                    _ACTIVE_HEADWEAR_NAMESPACE, f"headwear:{self.character_id}:{event_id}"
                ))
                self.store.set_active_state(
                    self.character_id, state_id, subject_key="active.avatar.headwear", value=str(assertion.value),
                    evidence_event_id=event_id, evidence_role="direct_user_statement",
                    evidence_excerpt_start_cp=assertion.excerpt_start_cp,
                    evidence_excerpt_end_cp=assertion.excerpt_end_cp,
                )
                self.last_error = None
                return {
                    "state": "replaced" if current.state is not None else "created",
                    "state_id": state_id, "event_id": event_id,
                }
            cleared = self.store.clear_active_state(
                self.character_id, subject_key="active.avatar.headwear", evidence_event_id=event_id,
                evidence_role="direct_user_statement",
            )
            self.last_error = None
            return {"state": "cleared" if cleared else "unchanged", "event_id": event_id}
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def observe_canonical_user_durable_facts(
        self,
        message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Apply one closed-schema durable proposal from persisted user evidence."""
        try:
            if not isinstance(message, dict) or message.get("role") != "user":
                return {"state": "ignored", "reason": "not_user_message"}
            if isinstance(conversation_index, bool) or not isinstance(conversation_index, int) or conversation_index < 0:
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            content, timestamp = message.get("content"), message.get("timestamp")
            if not isinstance(content, str) or not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "invalid_canonical_message"}
            recorded_at_us = parse_timestamp_us(timestamp)
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            from durable_fact_curation import extract_durable_fact_proposal

            repository = MemoryV2Repository(self.store)
            repository.ensure_character(self.character_id, self.display_name, legacy_config_key="characters/default")
            if self.store.active_truth_scope_id(self.character_id) != self.store.default_truth_scope_id(self.character_id):
                return {"state": "ignored", "reason": "truth_scope_not_real_world"}
            proposal = extract_durable_fact_proposal(
                content,
                previous_user_content=self._previous_user_content(conversation_file, conversation_index),
            )
            if proposal is None:
                return {"state": "ignored", "reason": "no_clear_durable_fact"}
            current = repository.lookup_durable_core(self.character_id, proposal.subject_key)
            if len(current.candidates) > 1:
                return {"state": "ignored", "reason": "multiple_current_durable_values"}
            favorite_bridge = None
            if proposal.stance == "correction" and not current.candidates:
                if proposal.subject_key != "preference.color":
                    return {"state": "ignored", "reason": "missing_current_durable_value"}
                favorite_bridge = self._favorite_color_v1_predecessor()
                if favorite_bridge is None:
                    return {"state": "ignored", "reason": "missing_current_durable_value"}

            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _DURABLE_FACT_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            ))
            existing_event = self.store.connection.execute(
                "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                (self.character_id, event_id),
            ).fetchone()
            if existing_event is None:
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence, event_type="canonical_user_message",
                    actor_kind="user", recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=content, source_origin="canonical_conversation",
                    source_reference=source_reference,
                )
            elif (existing_event["actor_kind"] != "user" or existing_event["content_text"] != content
                  or existing_event["source_reference"] != source_reference):
                return {"state": "ignored", "reason": "canonical_event_identity_conflict"}

            if current.candidates and current.candidates[0].content == proposal.content:
                self.store.reconfirm_durable_claim(
                    self.character_id, current.candidates[0].claim_id,
                    evidence_event_id=event_id,
                    evidence_excerpt_start_cp=proposal.excerpt_start_cp,
                    evidence_excerpt_end_cp=proposal.excerpt_end_cp,
                )
                return {"state": "unchanged", "claim_id": current.candidates[0].claim_id, "event_id": event_id}

            claim_id = str(uuid.uuid5(
                _DURABLE_FACT_NAMESPACE,
                f"durable:{self.character_id}:{proposal.subject_key}:{event_id}",
            ))
            predecessor_id = current.candidates[0].claim_id if current.candidates else None
            bridged_historical = False
            with self.store.transaction():
                if (favorite_bridge is not None
                        and favorite_bridge["value"].casefold() != proposal.value.casefold()):
                    predecessor_id = str(uuid.uuid5(
                        _DURABLE_FACT_NAMESPACE,
                        f"favorite-color-v1:{self.character_id}:{favorite_bridge['claim_id']}",
                    ))
                    self.store.add_legacy_favorite_color_predecessor(
                        self.character_id, predecessor_id,
                        content=f"The user's favorite color is {favorite_bridge['value']}.",
                        legacy_evidence_event_id=favorite_bridge["event_id"],
                        valid_from_us=favorite_bridge["recorded_at_us"],
                    )
                    bridged_historical = True
                self.store.add_durable_claim(
                    self.character_id, claim_id, subject_key=proposal.subject_key,
                    content=proposal.content, evidence_event_id=event_id,
                    evidence_role="direct_user_statement",
                    evidence_excerpt_start_cp=proposal.excerpt_start_cp,
                    evidence_excerpt_end_cp=proposal.excerpt_end_cp,
                    valid_from_us=recorded_at_us, created_at_us=recorded_at_us,
                    curator_name="bounded_durable_fact_curator", curator_version="1",
                    curator_policy_version="closed_schema_v1",
                    supersedes_claim_id=predecessor_id,
                )
            self.last_error = None
            return {
                "state": "superseded" if predecessor_id is not None else "created",
                "claim_id": claim_id, "event_id": event_id,
                "subject_key": proposal.subject_key,
                "v1_favorite_bridge": bridged_historical,
            }
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def _favorite_color_v1_predecessor(self) -> dict[str, Any] | None:
        """Return exactly one safe, current, imported V1 favorite-color value."""
        try:
            from durable_fact_curation import extract_v1_favorite_color_memory

            raw = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ImportError):
            return None
        if not isinstance(raw, list):
            return None
        candidates: list[tuple[int, str]] = []
        for row in raw:
            if not isinstance(row, dict) or isinstance(row.get("id"), bool) or not isinstance(row.get("id"), int):
                continue
            if str(row.get("category", "")).strip().casefold() not in {"preference", "preferences"}:
                continue
            value = extract_v1_favorite_color_memory(row.get("content"))
            if value is not None:
                candidates.append((int(row["id"]), value))
        logical_values = {value.casefold() for _, value in candidates}
        if len(logical_values) != 1:
            return None
        legacy_id, value = max(candidates, key=lambda item: item[0])
        mapped = self.store.connection.execute(
            """SELECT vir.claim_id, ce.event_id, e.recorded_at_us
                 FROM v1_import_records vir
                 JOIN claim_evidence ce ON ce.character_id=? AND ce.claim_id=vir.claim_id
                 JOIN events e ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                WHERE vir.source_scope=? AND vir.legacy_memory_id=?
                  AND ce.evidence_role='legacy_memory_record'
                  AND e.event_type='legacy_memory_record' AND e.actor_kind='system'
                  AND e.source_origin='legacy_v1_import' AND e.redaction_state='active'
                ORDER BY ce.created_at_us DESC LIMIT 1""",
            (self.character_id, v1_import_scope(self.character_id), legacy_id),
        ).fetchone()
        if mapped is None:
            return None
        return {
            "value": value,
            "claim_id": str(mapped["claim_id"]),
            "event_id": str(mapped["event_id"]),
            "recorded_at_us": int(mapped["recorded_at_us"]),
        }

    def apply_canonical_user_active_state_proposal(
        self,
        message: object,
        proposal: ActiveStateProposal,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Validate and atomically apply bounded updates from one saved user turn.

        This is the future extractor seam. A proposal is untrusted data: the
        canonical-message proof, registry/value validation, user evidence, and
        lifecycle application all remain backend-owned.
        """
        try:
            if not isinstance(message, dict) or message.get("role") != "user":
                return {"state": "ignored", "reason": "not_user_message"}
            if isinstance(conversation_index, bool) or not isinstance(conversation_index, int) or conversation_index < 0:
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            content = message.get("content")
            timestamp = message.get("timestamp")
            if not isinstance(content, str) or not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "invalid_canonical_message"}
            try:
                recorded_at_us = parse_timestamp_us(timestamp)
            except (TypeError, ValueError):
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _ACTIVE_STATE_PROPOSAL_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            ))
            repository = MemoryV2Repository(self.store)
            repository.ensure_character(self.character_id, self.display_name, legacy_config_key="characters/default")
            existing_event = self.store.connection.execute(
                "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                (self.character_id, event_id),
            ).fetchone()
            if existing_event is None:
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence, event_type="canonical_user_message",
                    actor_kind="user", recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=content, source_origin="canonical_conversation",
                    source_reference=source_reference,
                )
            elif (existing_event["actor_kind"] != "user" or existing_event["content_text"] != content
                  or existing_event["source_reference"] != source_reference):
                return {"state": "ignored", "reason": "canonical_event_identity_conflict"}
            updates = self.store.apply_active_state_proposal(
                self.character_id, proposal, evidence_event_id=event_id,
                evidence_role="direct_user_statement",
            )
            self.last_error = None
            return {"state": "applied", "event_id": event_id, "updates": updates}
        except Exception as error:
            self.last_error = type(error).__name__
            return {"state": "failed", "reason": self.last_error}

    def observe_canonical_user_continuity(
        self,
        message: object,
        *,
        conversation_index: int,
        conversation_file: str | Path,
    ) -> dict[str, Any]:
        """Apply bounded Current Continuity proposals after canonical save.

        Extraction happens before the transaction but has no write authority.
        The event and every proposed lifecycle mutation then commit atomically,
        after proof that the canonical user record is already on disk.
        """
        recognized_continuity = False
        extraction = None
        try:
            if not isinstance(message, dict) or message.get("role") != "user":
                return {"state": "ignored", "reason": "not_user_message"}
            if isinstance(conversation_index, bool) or not isinstance(conversation_index, int) or conversation_index < 0:
                return {"state": "ignored", "reason": "invalid_canonical_index"}
            content, timestamp = message.get("content"), message.get("timestamp")
            if not isinstance(content, str) or not isinstance(timestamp, str) or not timestamp.strip():
                return {"state": "ignored", "reason": "invalid_canonical_message"}
            try:
                recorded_at_us = parse_timestamp_us(timestamp)
            except (TypeError, ValueError):
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if recorded_at_us is None:
                return {"state": "ignored", "reason": "invalid_canonical_timestamp"}
            if not self._matches_persisted_canonical_message(
                message, conversation_file=conversation_file, index=conversation_index,
            ):
                return {"state": "ignored", "reason": "canonical_message_not_persisted"}

            from current_continuity import extract_current_continuity, find_exact_scenario_scope

            repository = MemoryV2Repository(self.store)
            repository.ensure_character(self.character_id, self.display_name, legacy_config_key="characters/default")
            active_scope = repository.active_truth_scope(self.character_id)
            recent_user_turns = self._recent_scoped_user_turns(
                conversation_file, conversation_index,
                scope_id=active_scope.truth_scope_id, scope_kind=active_scope.kind,
            )
            extraction = extract_current_continuity(
                repository, self.character_id, content,
                recent_user_turns=recent_user_turns,
            )
            if not extraction.has_mutation:
                return {
                    "state": "ignored", "reason": extraction.reason,
                    "extraction_method": extraction.method,
                    "extraction_outcome": "abstained",
                    "extraction_intents": extraction.intents,
                    "extraction_confidence": extraction.confidence,
                }
            recognized_continuity = True

            source_reference = self._canonical_source_reference(conversation_file, conversation_index)
            event_id = str(uuid.uuid5(
                _CURRENT_CONTINUITY_NAMESPACE,
                f"{self.character_id}:{source_reference}:{timestamp}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            ))
            with self.store.transaction():
                existing_event = self.store.connection.execute(
                    "SELECT actor_kind, content_text, source_reference FROM events WHERE character_id=? AND event_id=?",
                    (self.character_id, event_id),
                ).fetchone()
                if existing_event is not None:
                    if (existing_event["actor_kind"] != "user" or existing_event["content_text"] != content
                            or existing_event["source_reference"] != source_reference):
                        return {
                            "state": "ignored", "reason": "canonical_event_identity_conflict",
                            "extraction_method": extraction.method,
                            "extraction_outcome": "validation_rejected",
                            "extraction_intents": extraction.intents,
                            "extraction_confidence": extraction.confidence,
                        }
                    return {
                        "state": "unchanged", "memory_v1_allowed": False,
                        "extraction_method": extraction.method,
                        "extraction_outcome": "applied",
                        "extraction_intents": extraction.intents,
                        "extraction_confidence": extraction.confidence,
                    }
                sequence = self.store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()[0]
                self.store.add_event(
                    self.character_id, event_id, sequence, event_type="canonical_user_message",
                    actor_kind="user", recorded_at_us=recorded_at_us, temporal_precision="instant",
                    content_text=content, source_origin="canonical_conversation",
                    source_reference=source_reference,
                )

                scope_before = repository.active_truth_scope(self.character_id)
                if extraction.scenario is not None:
                    transition = extraction.scenario
                    if transition.operation == "exit":
                        self.store.deactivate_to_real_world(
                            self.character_id, evidence_event_id=event_id,
                            evidence_excerpt_start_cp=transition.excerpt_start_cp,
                            evidence_excerpt_end_cp=transition.excerpt_end_cp,
                        )
                    else:
                        assert transition.label is not None
                        existing = find_exact_scenario_scope(
                            repository.list_truth_scopes(self.character_id), transition.label,
                        )
                        scope_id = existing.truth_scope_id if existing is not None else self.store.create_scenario_truth_scope(
                            self.character_id, transition.label, evidence_event_id=event_id,
                            evidence_excerpt_start_cp=transition.excerpt_start_cp,
                            evidence_excerpt_end_cp=transition.excerpt_end_cp,
                        )
                        self.store.activate_truth_scope(
                            self.character_id, scope_id, evidence_event_id=event_id,
                            evidence_excerpt_start_cp=transition.excerpt_start_cp,
                            evidence_excerpt_end_cp=transition.excerpt_end_cp,
                        )
                    scope_after = repository.active_truth_scope(self.character_id)
                    self.last_error = None
                    return {
                        "state": "applied", "memory_v1_allowed": False,
                        "scope_changed": scope_before.truth_scope_id != scope_after.truth_scope_id,
                        "truth_scope": {"kind": scope_after.kind, "label": scope_after.label if scope_after.kind == "scenario" else None},
                        "active_state_updates": 0, "open_thread_updates": 0,
                        "extraction_method": extraction.method,
                        "extraction_outcome": "applied",
                        "extraction_intents": extraction.intents,
                        "extraction_confidence": extraction.confidence,
                    }

                active_updates = ()
                thread_updates = ()
                relation_updates = ()
                local_scene_refs: dict[str, str] = {}
                if extraction.active_state is not None:
                    active_updates = self.store.apply_active_state_proposal(
                        self.character_id, extraction.active_state, evidence_event_id=event_id,
                        evidence_role="direct_user_statement",
                    )
                    local_scene_refs = {
                        str(item["reference"]): str(item["scene_subject_id"])
                        for item in active_updates
                        if item.get("operation") == "introduce"
                        and item.get("reference") and item.get("scene_subject_id")
                    }
                if extraction.correction is not None:
                    correction_update = self.store.apply_active_state_correction(
                        self.character_id, extraction.correction, evidence_event_id=event_id,
                    )
                    active_updates = tuple(active_updates) + (correction_update,)
                if extraction.open_threads is not None:
                    thread_updates = self.store.apply_open_thread_proposal(
                        self.character_id, extraction.open_threads, evidence_event_id=event_id,
                    )
                if extraction.scene_relations:
                    relation_updates = self.store.apply_scene_relation_proposals(
                        self.character_id, extraction.scene_relations, evidence_event_id=event_id,
                        local_scene_refs=local_scene_refs,
                    )
                scene_mutated = bool(extraction.scene_relations)
                if extraction.active_state is not None:
                    scene_mutated = scene_mutated or bool(
                        extraction.active_state.introductions
                        or extraction.active_state.retirements
                        or extraction.active_state.reactivations
                        or any(update.target_kind == "scene" for update in extraction.active_state.updates)
                    )
                parity_updates = ()
                if scene_mutated:
                    parity_updates = self.store.synchronize_scene_ownership_mirrors(
                        self.character_id, evidence_event_id=event_id,
                    )
                retired_by_budget = self.store.sweep_scene_subject_budget(
                    self.character_id, evidence_event_id=event_id,
                ) if scene_mutated else ()
                self.last_error = None
                return {
                    "state": "applied", "memory_v1_allowed": False, "scope_changed": False,
                    "truth_scope": {"kind": scope_before.kind, "label": scope_before.label if scope_before.kind == "scenario" else None},
                    "active_state_updates": len(active_updates), "open_thread_updates": len(thread_updates),
                    "scene_relation_updates": len(relation_updates),
                    "scene_ownership_parity_updates": len(parity_updates),
                    "scene_budget_retirements": len(retired_by_budget),
                    "extraction_method": extraction.method,
                    "extraction_outcome": "applied",
                    "extraction_intents": extraction.intents,
                    "extraction_confidence": extraction.confidence,
                }
        except Exception as error:
            self.last_error = type(error).__name__
            result = {
                "state": "failed", "reason": self.last_error,
                "memory_v1_allowed": not recognized_continuity,
                "extraction_outcome": "validation_rejected" if recognized_continuity else "abstained",
            }
            if extraction is not None:
                result.update({
                    "extraction_method": extraction.method,
                    "extraction_intents": extraction.intents,
                    "extraction_confidence": extraction.confidence,
                })
            return result

    def _canonical_source_reference(self, conversation_file: str | Path, index: int) -> str:
        source = Path(conversation_file).resolve()
        try:
            relative = source.relative_to(self.application_dir)
        except ValueError:
            relative = Path(source.name)
        return f"{relative.as_posix()}#{index}"

    @staticmethod
    def _previous_user_content(conversation_file: str | Path, index: int) -> str | None:
        try:
            records = json.loads(Path(conversation_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(records, list):
            return None
        for record in reversed(records[:index]):
            if isinstance(record, dict) and record.get("role") == "user":
                content = record.get("content")
                return content if isinstance(content, str) and len(content) <= 300 else None
        return None

    @staticmethod
    def _recent_scoped_user_turns(
        conversation_file: str | Path,
        index: int,
        *,
        scope_id: str,
        scope_kind: str,
    ):
        """Load only a bounded contiguous same-scope canonical user window."""
        from continuity_reference import (
            MAX_RECENT_USER_CHARS,
            MAX_RECENT_USER_GAP_US,
            MAX_RECENT_USER_TURNS,
            RecentUserTurn,
        )

        try:
            records = json.loads(Path(conversation_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return ()
        if not isinstance(records, list) or not 0 <= index <= len(records):
            return ()
        current_timestamp = records[index].get("timestamp") if index < len(records) and isinstance(records[index], dict) else None
        newest_us = parse_timestamp_us(current_timestamp)
        if newest_us is None:
            return ()
        selected: list[RecentUserTurn] = []
        total_chars = 0
        for record_index in range(index - 1, -1, -1):
            record = records[record_index]
            if not isinstance(record, dict) or record.get("role") != "user":
                continue
            provenance = record.get("truth_scope")
            if not isinstance(provenance, dict):
                break
            if provenance.get("scope_id") != scope_id or provenance.get("kind") != scope_kind:
                break
            content = record.get("content")
            if not isinstance(content, str) or not content or len(content) > 500:
                break
            recorded_at_us = parse_timestamp_us(record.get("timestamp"))
            if (recorded_at_us is None or recorded_at_us > newest_us
                    or newest_us - recorded_at_us > MAX_RECENT_USER_GAP_US):
                break
            if len(selected) >= MAX_RECENT_USER_TURNS or total_chars + len(content) > MAX_RECENT_USER_CHARS:
                break
            selected.append(RecentUserTurn(record_index, content))
            total_chars += len(content)
            newest_us = recorded_at_us
        return tuple(reversed(selected))

    @staticmethod
    def _matches_persisted_canonical_message(message: dict[str, Any], *, conversation_file: str | Path, index: int) -> bool:
        """Confirm the proposal refers to the exact already-saved V1 record."""
        try:
            source = Path(conversation_file)
            records = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(records, list) or not 0 <= index < len(records):
            return False
        record = records[index]
        role = message.get("role")
        return (
            isinstance(record, dict)
            and role in {"user", "assistant"}
            and record.get("role") == role
            and record.get("content") == message.get("content")
            and record.get("timestamp") == message.get("timestamp")
        )

    def compare(self, query: str, v1_selected=(), messages=(), *, v1_latency_ms: float | None = None) -> dict[str, Any]:
        """Run V2 silently beside V1; returned data is IDs/metrics only."""
        v1_ids = [str(item.get("id")) for item in v1_selected if isinstance(item, dict) and item.get("id") is not None]
        started = time.perf_counter()
        error_kind = None
        strategy = "hybrid_local"
        mapped_v1_ids = self._map_v1_ids(v1_ids)
        try:
            recent = tuple(
                str(message.get("content", "")) for message in list(messages)[-16:]
                if isinstance(message, dict) and message.get("role") == "user" and str(message.get("content", "")).strip()
            )[-8:]
            if recent and recent[-1] == str(query):
                recent = recent[:-1]
            v2_ids, strategy, abstention = AdaptiveShadowRetrieval(
                self.store, self._provider(), self._working_recall,
            ).retrieve(RetrievalQuery(
                self.character_id, str(query), datetime.now(timezone.utc).isoformat(), "ordinary", recent
            ))
            v2_ids = list(v2_ids)
        except Exception as error:
            self.last_error = type(error).__name__
            error_kind = self.last_error
            v2_ids = []
            abstention = "shadow_failure"
        latency = (time.perf_counter() - started) * 1000.0
        record_dual_read(
            self.store, character_id=self.character_id, query=query, v1_ids=v1_ids, v2_ids=v2_ids,
            comparison_v1_ids=mapped_v1_ids,
            v1_latency_ms=v1_latency_ms, v2_latency_ms=latency,
            retrieval_strategy=strategy, error_kind=error_kind,
        )
        overlap = sorted(set(mapped_v1_ids) & set(v2_ids))
        return {
            "v1_count": len(v1_ids), "v1_mapped_count": len(mapped_v1_ids), "v2_count": len(v2_ids), "overlap_claim_ids": overlap,
            "v1_only_count": len(set(mapped_v1_ids) - set(v2_ids)), "v2_only_count": len(set(v2_ids) - set(mapped_v1_ids)),
            "v1_abstained": not bool(v1_ids), "v2_abstained": not bool(v2_ids),
            "v2_abstention_reason": abstention, "v2_latency_ms": latency, "error_kind": error_kind,
            "v2_retrieval_strategy": strategy,
        }

    def _provider(self):
        if self._embedding_provider is None:
            self._embedding_provider = MiniLMEmbeddingProvider()
        return self._embedding_provider

    def _map_v1_ids(self, v1_ids: list[str]) -> list[str]:
        rows = self.store.connection.execute(
            "SELECT legacy_memory_id, claim_id FROM v1_import_records WHERE source_scope=?",
            (v1_import_scope(self.character_id),),
        ).fetchall()
        mapping = {str(row["legacy_memory_id"]): row["claim_id"] for row in rows}
        return [mapping[value] for value in v1_ids if value in mapping]

    def close(self) -> None:
        self.store.close()
