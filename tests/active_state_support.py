"""Neutral temporary runtime used by supported Active State regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

from current_continuity import admit_current_continuity_context
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository


BASE_TIME = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
SYNTHETIC_ASSISTANT = "Synthetic response."


class SyntheticSession:
    """One isolated synthetic character using the canonical writer boundary."""

    def __init__(self, case_id: str) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="aifren-active-state-regression-")
        self.root = Path(self._temp.name)
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.conversation_file.write_text("[]", encoding="utf-8")
        self.character_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, "aifren:active-state-regression:" + case_id,
        ))
        self.rows: list[dict[str, object]] = []
        self.turn_count = 0
        self._action_count = 0
        self.turn_latencies_ms: list[float] = []
        self.max_context_chars = 0
        self._open_writer()

    def _open_writer(self) -> None:
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id,
            display_name="Synthetic", memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(
            self.character_id, "Synthetic", legacy_config_key="characters/default",
        )

    def close(self) -> None:
        self.writer.close()
        self._temp.cleanup()

    def restart(self) -> None:
        self.writer.close()
        self._open_writer()

    def turn(self, text: str, *, assistant: str = SYNTHETIC_ASSISTANT) -> dict[str, Any]:
        scope = self.repository.active_truth_scope(self.character_id)
        timestamp = BASE_TIME + timedelta(seconds=2 * self.turn_count)
        provenance = {"kind": scope.kind, "scope_id": scope.truth_scope_id}
        user = {
            "role": "user", "content": text, "timestamp": timestamp.isoformat(),
            "truth_scope": provenance,
        }
        assistant_row = {
            "role": "assistant", "content": assistant,
            "timestamp": (timestamp + timedelta(seconds=1)).isoformat(),
            "truth_scope": provenance,
        }
        index = len(self.rows)
        self.rows.extend((user, assistant_row))
        self.conversation_file.write_text(
            json.dumps(self.rows, ensure_ascii=False), encoding="utf-8",
        )
        started = time.perf_counter()
        result = self.writer.observe_canonical_user_continuity(
            user, conversation_index=index, conversation_file=self.conversation_file,
        )
        self.turn_latencies_ms.append((time.perf_counter() - started) * 1000.0)
        self.turn_count += 1
        return result

    def apply_action(self, plan: object) -> dict[str, Any]:
        self._action_count += 1
        recorded_at_us = int(
            (BASE_TIME + timedelta(
                microseconds=max(1, self.turn_count * 2_000_000 - 500_000),
            )).timestamp() * 1_000_000
        )
        return self.writer.apply_governed_companion_action(
            plan,
            decision_reference=f"synthetic-{self.turn_count}-{self._action_count}",
            recorded_at_us=recorded_at_us,
        )

    def effects(self, *, target: str = "companion"):
        return self.repository.capability_effects(self.character_id, target=target)

    def relations(self):
        return self.repository.list_scene_relations(self.character_id, limit=96)

    def scene(self) -> tuple[dict[str, str], ...]:
        rendered = []
        for subject in self.repository.list_scene_subjects(self.character_id, limit=24):
            rendered.append({
                row.subject_key.rsplit(".", 1)[-1]: row.value
                for row in self.repository.lookup_scene_attributes(
                    self.character_id, subject.scene_subject_id,
                )
            })
        return tuple(rendered)

    def activity(self, actor: str) -> str | None:
        state = self.repository.lookup_actor_state(
            self.character_id, actor, "activity",
        ).state
        return state.value if state is not None else None

    def posture(self, actor: str = "companion") -> str | None:
        state = self.repository.lookup_actor_state(
            self.character_id, actor, "posture",
        ).state
        return state.value if state is not None else None

    def context(self, query: str) -> str:
        admission = admit_current_continuity_context(
            self.repository, self.character_id, query,
            now_us=int((BASE_TIME + timedelta(days=1)).timestamp() * 1_000_000),
        )
        block = "\n".join(filter(None, (
            admission.truth_scope_context,
            admission.active_state_context,
            admission.open_thread_context,
        )))
        self.max_context_chars = max(self.max_context_chars, len(block))
        return block

    def structural_snapshot(self) -> tuple[object, ...]:
        scope = self.repository.active_truth_scope(self.character_id)
        relations = tuple(sorted(
            (item.target_kind, item.target, item.facet or "", item.side or "",
             item.predicate, item.cause_kind, item.cause,
             item.semantic_family or "", item.quantity or 0)
            for item in self.relations()
        ))
        scene = tuple(sorted(tuple(sorted(item.items())) for item in self.scene()))
        effects = self.effects().prompt_payload()
        return (
            scope.kind, scope.label, self.activity("user"),
            self.activity("companion"), self.posture(), relations, scene,
            json.dumps(effects, sort_keys=True, separators=(",", ":")),
        )
