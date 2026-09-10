"""Bounded, character-scoped Memory Viewer/Editor application boundary.

Memory V1 remains the broad prompt-facing authority.  This module projects V1
and the deliberately separate V2 lanes into a frontend-neutral inspection
shape and delegates every mutation to the owning semantic API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from memory_v2_store import DURABLE_CORE_FACT, MemoryV2Repository


MEMORY_VIEW_LANES = frozenset({"v1", "v2_claims", "episodes", "open_threads"})
MEMORY_VIEW_STATUS_FILTERS = frozenset({"current", "historical", "all"})
MEMORY_VIEW_SCOPE_FILTERS = frozenset({"applicable", "all"})
MEMORY_VIEW_PAGE_LIMIT = 20
MEMORY_VIEW_MAX_LIMIT = 50
MEMORY_VIEW_DETAIL_MAX_LIMIT = 12


def _iso_timestamp(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return ""
    try:
        return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _scope_label(kind: str, label: str) -> str:
    if kind == "legacy_untagged":
        return "Legacy / untagged"
    if kind == "real_world":
        return "Real world"
    if kind == "scenario":
        return f"Scenario: {label}" if label else "Scenario"
    return "Unknown scope"


def _authority_for_claim(claim_type: str) -> tuple[str, bool]:
    if claim_type == DURABLE_CORE_FACT:
        return "Memory V2 durable fact · governed structured authority", True
    return "Memory V2 claim · non-authoritative shadow record", False


class MemoryViewer:
    """One short-lived projection over the service's current memory owners."""

    def __init__(
        self, memory: Any, v2_store: Any, character_id: str, *,
        episode_cache: Any = None, canonical_messages: Any = (),
        active_truth_scope: dict[str, object] | None = None,
        development_v2_authority: bool = False,
        recovery_status: str = "current",
    ) -> None:
        self.memory = memory
        self.v2_store = v2_store
        self.character_id = str(character_id or "")
        self.episode_cache = episode_cache
        self.canonical_messages = tuple(canonical_messages or ())
        self.active_truth_scope = active_truth_scope
        self.development_v2_authority = development_v2_authority
        self.recovery_warning = {
            "recovery_pending": "Memory recovery is still catching up with saved conversation.",
            "recovery_failed": "Memory recovery could not complete; saved conversation is retained.",
            "recovery_unresolved": "Memory recovery needs review; saved conversation is retained.",
            "recovery_unavailable": "Memory recovery status is temporarily unavailable.",
            "index_pending": "Some saved memories are still being prepared for search.",
            "index_failed": "Some saved memories could not be prepared for search; their sources are retained.",
        }.get(recovery_status, "") if development_v2_authority else ""

    @staticmethod
    def _request(
        *, lane: object, query: object, status_filter: object,
        scope_filter: object, limit: object, offset: object,
    ) -> tuple[str, str, str, str, int, int]:
        lane = str(lane or "v1")
        query = " ".join(str(query or "").split())
        status_filter = str(status_filter or "current")
        scope_filter = str(scope_filter or "applicable")
        if lane not in MEMORY_VIEW_LANES:
            raise ValueError("Unsupported memory viewer lane.")
        if len(query) > 160:
            raise ValueError("Memory search exceeds 160 characters.")
        if status_filter not in MEMORY_VIEW_STATUS_FILTERS:
            raise ValueError("Unsupported memory status filter.")
        if scope_filter not in MEMORY_VIEW_SCOPE_FILTERS:
            raise ValueError("Unsupported memory scope filter.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MEMORY_VIEW_MAX_LIMIT:
            raise ValueError("Memory page limit must be from 1 to 50.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Memory page offset must be non-negative.")
        return lane, query, status_filter, scope_filter, limit, offset

    def page(
        self,
        *,
        lane: object = "v1",
        query: object = "",
        status_filter: object = "current",
        scope_filter: object = "applicable",
        limit: object = MEMORY_VIEW_PAGE_LIMIT,
        offset: object = 0,
    ) -> dict[str, object]:
        lane, query, status_filter, scope_filter, limit, offset = self._request(
            lane=lane, query=query, status_filter=status_filter,
            scope_filter=scope_filter, limit=limit, offset=offset,
        )
        base: dict[str, object] = {
            "character_id": self.character_id,
            "lane": lane,
            "query": query,
            "status_filter": status_filter,
            "scope_filter": scope_filter,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "items": [],
            "availability": "ready",
            "warning": "",
        }
        if lane == "v1":
            result = self.memory.query_page(query=query, limit=limit, offset=offset)
            base["authority_label"] = ("Memory V1 · retained archive, inactive in V2 authority"
                if self.development_v2_authority else "Memory V1 · canonical prompt-facing memory authority")
            base["has_more"] = bool(result["has_more"])
            base["items"] = [self._v1_item(item) for item in result["items"]]
            return base

        if self.v2_store is None:
            base.update(
                authority_label=self._unavailable_authority(),
                availability="unavailable",
                warning=("Memory V2 inspection is unavailable. Saved conversation is retained."
                         if self.development_v2_authority else
                         "Memory V2 is unavailable. Memory V1 remains authoritative and conversation is unaffected."),
            )
            return base

        repository = MemoryV2Repository(self.v2_store)
        try:
            if lane == "v2_claims":
                result = repository.inspect_claims(
                    self.character_id, query=query, status_filter=status_filter,
                    scope_filter=scope_filter, limit=limit, offset=offset,
                )
                items = [self._claim_item(item) for item in result["items"]]
                authority = "Memory V2 facts/claims · separate from canonical Memory V1"
            elif lane == "episodes":
                if self.episode_cache is None:
                    raise RuntimeError("Episode cache diagnostics are unavailable")
                result = self.episode_cache.inspect_records(
                    self.canonical_messages, query=query,
                    status_filter=status_filter, scope_filter=scope_filter,
                    limit=limit, offset=offset,
                    active_truth_scope=self.active_truth_scope,
                    allow_historical_recall=self.development_v2_authority,
                )
                items = [self._episode_item(item) for item in result["items"]]
                authority = "Memory V2 episodes · derived, rebuildable, non-authoritative"
                base["diagnostic_state"] = str(result.get("cache_state") or "unusable")
                base["diagnostic_reason"] = str(result.get("cache_reason") or "")
            else:
                result = repository.inspect_open_threads(
                    self.character_id, query=query, status_filter=status_filter,
                    scope_filter=scope_filter, limit=limit, offset=offset,
                )
                items = [self._thread_item(item) for item in result["items"]]
                authority = "Memory V2 Open Threads · scoped unresolved continuity, not V1 facts"
            base.update(
                authority_label=(f"V2 authority · {authority}" if self.development_v2_authority else authority),
                has_more=bool(result["has_more"]),
                items=items,
                warning=self.recovery_warning,
            )
            return base
        except Exception as error:
            # Inspection failure is local to this page; it never changes the
            # selected prompt authority or starts a conversational fallback.
            base.update(
                authority_label=self._unavailable_authority(),
                availability="degraded",
                warning=f"Memory V2 viewer data is unavailable ({type(error).__name__}).",
            )
            return base

    def _unavailable_authority(self):
        return ("V2 authority · inspection unavailable; no V1 fallback"
                if self.development_v2_authority else
                "Memory V2 · unavailable (Memory V1 remains authoritative)")

    def detail(
        self,
        *,
        lane: object,
        record_id: object,
        limit: object = 8,
        offset: object = 0,
    ) -> dict[str, object]:
        """Return bounded V2 audit detail without expanding source archives."""
        lane = str(lane or "")
        record_id = str(record_id or "")
        if lane not in {"v2_claims", "episodes"} or not record_id:
            raise ValueError("Memory detail is available for V2 claims and episodes only.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MEMORY_VIEW_DETAIL_MAX_LIMIT:
            raise ValueError("Memory detail limit must be from 1 to 12.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Memory detail offset must be non-negative.")
        base: dict[str, object] = {
            "character_id": self.character_id, "lane": lane,
            "record_id": record_id, "limit": limit, "offset": offset,
            "availability": "ready", "has_more": False,
        }
        if self.v2_store is None:
            base.update(
                availability="unavailable", detail={},
                warning="Memory V2 detail is unavailable. Saved conversation is retained.",
            )
            return base
        try:
            if lane == "v2_claims":
                raw = MemoryV2Repository(self.v2_store).inspect_claim_provenance(
                    self.character_id, record_id, limit=limit, offset=offset,
                )
                detail = self._claim_detail(raw)
                has_more = bool(raw.get("has_more"))
            else:
                if self.episode_cache is None:
                    raise RuntimeError("Episode cache diagnostics are unavailable")
                result = self.episode_cache.inspect_records(
                    self.canonical_messages, status_filter="all", scope_filter="all",
                    limit=1, offset=0, record_id=record_id,
                    active_truth_scope=self.active_truth_scope,
                    allow_historical_recall=self.development_v2_authority,
                )
                if not result["items"]:
                    raise ValueError("The selected episode is no longer available.")
                detail = self._episode_detail(result["items"][0], result)
                has_more = False
            base.update(detail=detail, has_more=has_more, warning=self.recovery_warning)
            return base
        except Exception as error:
            base.update(
                availability="degraded", detail={}, has_more=False,
                warning=f"Memory V2 detail failed safely ({type(error).__name__}).",
            )
            return base

    def mutate(
        self,
        *,
        action: object,
        record_id: object,
        content: object = "",
        category: object = "",
        importance: object = 5,
        command_id: object = "",
    ) -> dict[str, object]:
        action = str(action or "")
        record_id = str(record_id or "")
        if action == "edit_v1":
            if not record_id.startswith("v1:"):
                raise ValueError("V1 edit target is malformed.")
            try:
                memory_id = int(record_id[3:])
            except ValueError as error:
                raise ValueError("V1 edit target is malformed.") from error
            updated = self.memory.edit_memory(memory_id, str(category), str(content), importance)
            if updated is None:
                raise ValueError("The selected V1 memory no longer exists.")
            return {"accepted": True, "action": action, "record_id": record_id}
        if action == "remove_v1":
            if not record_id.startswith("v1:"):
                raise ValueError("V1 removal target is malformed.")
            try:
                memory_id = int(record_id[3:])
            except ValueError as error:
                raise ValueError("V1 removal target is malformed.") from error
            if not self.memory.delete_memory(memory_id):
                raise ValueError("The selected V1 memory no longer exists.")
            return {"accepted": True, "action": action, "record_id": record_id}
        if self.v2_store is None:
            raise RuntimeError("Memory V2 is unavailable; Memory V1 is unaffected.")
        repository = MemoryV2Repository(self.v2_store)
        if action == "correct_v2_durable":
            new_id = repository.correct_durable_from_viewer(
                self.character_id, record_id, str(content), command_id=str(command_id),
            )
            return {
                "accepted": True, "action": action, "record_id": record_id,
                "replacement_record_id": new_id,
            }
        if action == "retire_v2_durable":
            repository.retire_durable_from_viewer(
                self.character_id, record_id, command_id=str(command_id),
            )
            return {"accepted": True, "action": action, "record_id": record_id}
        raise ValueError("Unsupported memory viewer mutation.")

    @staticmethod
    def _v1_item(item: dict[str, object]) -> dict[str, object]:
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        return {
            "record_id": f"v1:{item['id']}",
            "lane": "v1",
            "authority": "Memory V1 · canonical prompt-facing authority",
            "content": str(item.get("content") or ""),
            "category": str(item.get("category") or ""),
            "importance": int(item.get("importance") or 5),
            "status": "current",
            "scope": "Character-owned / general",
            "provenance": str(provenance.get("source") or "legacy / unspecified"),
            "source_reference": str(provenance.get("source_message") or ""),
            "created_at": str(item.get("created") or ""),
            "updated_at": str(item.get("updated") or ""),
            "editable": True,
            "retirable": True,
            "derived": False,
        }

    @staticmethod
    def _claim_item(item: dict[str, object]) -> dict[str, object]:
        authority, editable = _authority_for_claim(str(item.get("claim_type") or ""))
        evidence_ids = item.get("evidence_event_ids") or ()
        provenance = str(item.get("provenance_state") or "unknown")
        if evidence_ids:
            provenance += f" · {len(evidence_ids)} retained evidence reference(s)"
        return {
            "record_id": str(item.get("record_id") or ""),
            "lane": "v2_claims",
            "authority": authority,
            "content": str(item.get("content") or ""),
            "category": str(item.get("subject_key") or item.get("claim_type") or ""),
            "importance": int(item.get("importance") or 5),
            "status": str(item.get("status") or "unknown"),
            "scope": _scope_label(
                str(item.get("truth_scope_kind") or ""),
                str(item.get("truth_scope_label") or ""),
            ),
            "provenance": provenance,
            "source_reference": str(item.get("source_reference") or ""),
            "created_at": _iso_timestamp(item.get("created_at_us")),
            "updated_at": _iso_timestamp(item.get("updated_at_us")),
            "valid_to": _iso_timestamp(item.get("valid_to_us")),
            "corrected_by": str(item.get("corrected_by") or ""),
            "supersedes": str(item.get("supersedes") or ""),
            "editable": editable and item.get("status") == "active" and item.get("valid_to_us") is None,
            "retirable": editable and item.get("status") == "active" and item.get("valid_to_us") is None,
            "derived": False,
        }

    @staticmethod
    def _episode_item(item: dict[str, object]) -> dict[str, object]:
        level = str(item.get("summary_level") or "")
        category = "Consolidated era" if level == "episode_era_compaction" else "Episode"
        return {
            "record_id": str(item.get("record_id") or ""),
            "lane": "episodes",
            "authority": "Memory V2 derived episode · non-authoritative / rebuildable",
            "content": str(item.get("content") or ""),
            "category": category,
            "importance": 0,
            "status": str(item.get("diagnostic_state") or "invalid"),
            "scope": _scope_label(
                str(item.get("truth_scope_kind") or ""),
                str(item.get("truth_scope_label") or ""),
            ),
            "provenance": (
                f"Canonical conversation records {item.get('source_start_sequence')}–"
                f"{item.get('source_end_sequence')} · {item.get('provenance_state')} · "
                f"{item.get('diagnostic_reason')}"
            ),
            "source_reference": "",
            "created_at": _iso_timestamp(item.get("created_at_us")),
            "updated_at": "",
            "editable": False,
            "retirable": False,
            "derived": True,
        }

    @staticmethod
    def _claim_detail(item: dict[str, object]) -> dict[str, object]:
        evidence = []
        for value in item.get("evidence", ()):
            evidence.append({
                **dict(value),
                "recorded_at": _iso_timestamp(value.get("recorded_at_us")),
                "occurred_from": _iso_timestamp(value.get("occurred_from_us")),
                "occurred_to": _iso_timestamp(value.get("occurred_to_us")),
                "linked_at": _iso_timestamp(value.get("linked_at_us")),
            })
        statuses = [{
            **dict(value), "created_at": _iso_timestamp(value.get("created_at_us")),
        } for value in item.get("status_history", ())]
        relations = [{
            **dict(value), "created_at": _iso_timestamp(value.get("created_at_us")),
        } for value in item.get("relations", ())]
        return {
            "kind": "claim_provenance",
            "claim_id": str(item.get("record_id") or ""),
            "claim_type": str(item.get("claim_type") or ""),
            "subject_key": str(item.get("subject_key") or ""),
            "status": str(item.get("status") or "unknown"),
            "scope": _scope_label(
                str(item.get("truth_scope_kind") or ""),
                str(item.get("truth_scope_label") or ""),
            ),
            "truth_scope_id": str(item.get("truth_scope_id") or ""),
            "provenance_state": str(item.get("provenance_state") or ""),
            "created_at": _iso_timestamp(item.get("created_at_us")),
            "valid_from": _iso_timestamp(item.get("valid_from_us")),
            "valid_to": _iso_timestamp(item.get("valid_to_us")),
            "evidence": evidence,
            "status_history": statuses,
            "relations": relations,
        }

    @staticmethod
    def _episode_detail(item: dict[str, object], result: dict[str, object]) -> dict[str, object]:
        return {
            "kind": "episode_diagnostic",
            "episode_id": str(item.get("record_id") or ""),
            "summary_level": str(item.get("summary_level") or ""),
            "diagnostic_state": str(item.get("diagnostic_state") or "invalid"),
            "diagnostic_reason": str(item.get("diagnostic_reason") or ""),
            "cache_state": str(result.get("cache_state") or "unusable"),
            "cache_reason": str(result.get("cache_reason") or ""),
            "generation_id": str(item.get("generation_id") or ""),
            "scope": _scope_label(
                str(item.get("truth_scope_kind") or ""), "",
            ),
            "truth_scope_id": str(item.get("truth_scope_id") or ""),
            "source_start_sequence": item.get("source_start_sequence"),
            "source_end_sequence": item.get("source_end_sequence"),
            "source_count": int(item.get("source_count") or 0),
            "lower_episode_ids": list(item.get("lower_episode_ids") or ()),
            "created_at": _iso_timestamp(item.get("created_at_us")),
        }

    @staticmethod
    def _thread_item(item: dict[str, object]) -> dict[str, object]:
        anchor = str(item.get("temporal_anchor") or "")
        provenance = f"{int(item.get('evidence_count') or 0)} retained evidence reference(s)"
        if anchor:
            provenance += f" · {anchor}"
        return {
            "record_id": str(item.get("record_id") or ""),
            "lane": "open_threads",
            "authority": "Memory V2 Open Thread · scoped continuity, not a V1 fact",
            "content": str(item.get("content") or ""),
            "category": f"{item.get('kind')} · {item.get('participant_scope')}",
            "importance": 0,
            "status": str(item.get("status") or "unknown"),
            "scope": _scope_label(
                str(item.get("truth_scope_kind") or ""),
                str(item.get("truth_scope_label") or ""),
            ),
            "provenance": provenance,
            "source_reference": str(item.get("source_reference") or ""),
            "created_at": _iso_timestamp(item.get("opened_at_us")),
            "updated_at": _iso_timestamp(item.get("updated_at_us")),
            "editable": False,
            "retirable": False,
            "derived": False,
        }
