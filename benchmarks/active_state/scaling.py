"""Deterministic transient-scene lifecycle and storage scaling acceptance.

The data is wholly synthetic. This exercises the production V2 scene store,
not a product world simulator, and emits structural/numeric results only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile
import time
import uuid

from aifren.state.current_continuity import admit_current_continuity_context
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import (
    ActiveSceneSubjectIntroduction,
    ActiveSceneSubjectReactivation,
    ActiveSceneSubjectRetirement,
    ActiveStateProposal,
    MemoryV2Repository,
)


@dataclass(frozen=True)
class ScalingReport:
    version: str
    transient_created: int
    retired_subjects: int
    roster_before_sweep: int
    roster_after_sweep: int
    dormant_after_sweep: int
    reactivation_preserved_identity: bool
    active_relation_orphans: int
    database_bytes: int
    context_chars: int
    internal_ids_present: bool
    lookup_median_ms: float
    scene_read_median_ms: float
    capability_derivation_median_ms: float
    prompt_admission_median_ms: float
    sweep_ms: float
    reactivation_ms: float
    restart_ms: float


def _median(callback, repeats: int) -> float:
    values = []
    for _index in range(repeats):
        started = time.perf_counter()
        callback()
        values.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(values)


def run(*, retired_total: int = 5_000, roster_total: int = 100) -> ScalingReport:
    if not 1_000 <= retired_total <= 10_000 or not 100 <= roster_total <= 128:
        raise ValueError("transient scaling bounds are invalid")
    with tempfile.TemporaryDirectory(prefix="aifren-scene-scaling-") as folder:
        root = Path(folder)
        memory_file = root / "memories.json"
        memory_file.write_text("[]", encoding="utf-8")
        character_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren:scene-scaling"))
        writer = MemoryV2ShadowWriter(
            root, character_id=character_id, display_name="Synthetic",
            memory_file=memory_file,
        )
        repository = MemoryV2Repository(writer.store)
        repository.ensure_character(character_id, "Synthetic", legacy_config_key="characters/default")
        scope_id = repository.active_truth_scope(character_id).truth_scope_id
        sequence = 0
        timestamp_us = int(datetime(2026, 8, 28, tzinfo=timezone.utc).timestamp() * 1_000_000)

        def event(content: str) -> str:
            nonlocal sequence, timestamp_us
            sequence += 1
            timestamp_us += 1_000
            event_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"aifren:scene-scaling:event:{sequence}",
            ))
            writer.store.add_event(
                character_id, event_id, sequence, event_type="canonical_message",
                actor_kind="user", recorded_at_us=timestamp_us,
                content_text=content, payload_schema=1,
                source_origin="synthetic_scaling", source_reference=f"synthetic:{sequence}",
            )
            return event_id

        distinct_id = ""
        batch_size = 10
        for start in range(0, retired_total, batch_size):
            count = min(batch_size, retired_total - start)
            intro_content = "synthetic transient objects"
            intro_event = event(intro_content)
            introductions = tuple(
                ActiveSceneSubjectIntroduction(
                    f"item_{index + 1}", "hat" if start == 0 and index == 0 else "object",
                    0, len(intro_content),
                    identity_strength="distinct" if start == 0 and index == 0 else "generic",
                )
                for index in range(count)
            )
            introduced = writer.store.apply_active_state_proposal(
                character_id, ActiveStateProposal((), introductions=introductions),
                evidence_event_id=intro_event, truth_scope_id=scope_id,
            )
            subject_ids = tuple(
                str(row["scene_subject_id"]) for row in introduced
                if row.get("operation") == "introduce"
            )
            if start == 0:
                distinct_id = subject_ids[0]
            retire_content = "synthetic transient retirement"
            retire_event = event(retire_content)
            writer.store.apply_active_state_proposal(
                character_id,
                ActiveStateProposal((), retirements=tuple(
                    ActiveSceneSubjectRetirement(subject_id, 0, len(retire_content))
                    for subject_id in subject_ids
                )),
                evidence_event_id=retire_event, truth_scope_id=scope_id,
            )

        for start in range(0, roster_total, batch_size):
            count = min(batch_size, roster_total - start)
            content = "synthetic current scene objects"
            writer.store.apply_active_state_proposal(
                character_id,
                ActiveStateProposal((), introductions=tuple(
                    ActiveSceneSubjectIntroduction(
                        f"current_{index + 1}", "object", 0, len(content),
                    ) for index in range(count)
                )),
                evidence_event_id=event(content), truth_scope_id=scope_id,
            )

        roster_before = repository.list_scene_subjects(character_id, limit=128)
        sweep_event = event("synthetic lifecycle budget sweep")
        sweep_started = time.perf_counter()
        writer.store.sweep_scene_subject_budget(
            character_id, evidence_event_id=sweep_event, truth_scope_id=scope_id,
        )
        sweep_ms = (time.perf_counter() - sweep_started) * 1000.0
        roster_after = repository.list_scene_subjects(character_id, limit=128)

        reactivate_content = "reactivate the distinct synthetic hat"
        reactivation_started = time.perf_counter()
        writer.store.apply_active_state_proposal(
            character_id,
            ActiveStateProposal((), reactivations=(ActiveSceneSubjectReactivation(
                distinct_id, 0, len(reactivate_content),
            ),)),
            evidence_event_id=event(reactivate_content), truth_scope_id=scope_id,
        )
        reactivation_ms = (time.perf_counter() - reactivation_started) * 1000.0
        reactivated_ids = {
            row.scene_subject_id for row in repository.list_scene_subjects(character_id, limit=128)
        }

        def context() -> str:
            admission = admit_current_continuity_context(
                repository, character_id, "What is in the current scene?",
                now_us=timestamp_us + 1_000,
            )
            return "\n".join(filter(None, (
                admission.truth_scope_context, admission.active_state_context,
                admission.open_thread_context,
            )))

        current_context = context()
        lookup_ms = _median(
            lambda: repository.list_scene_subjects(character_id, limit=128), 100,
        )
        scene_read_ms = _median(
            lambda: repository.list_scene_relations(character_id, limit=32), 100,
        )
        capability_ms = _median(
            lambda: repository.capability_effects(character_id), 100,
        )
        context_ms = _median(context, 50)
        orphan_count = int(writer.store.connection.execute(
            """SELECT COUNT(*) FROM active_scene_relations r
                 JOIN active_scene_subjects s
                   ON s.character_id=r.character_id AND s.truth_scope_id=r.truth_scope_id
                  AND s.scene_subject_id=r.cause_subject_id
                WHERE r.character_id=? AND r.valid_to_us IS NULL
                  AND s.retired_at_us IS NOT NULL""",
            (character_id,),
        ).fetchone()[0])
        retired_count = int(writer.store.connection.execute(
            "SELECT COUNT(*) FROM active_scene_subjects WHERE character_id=? AND retired_at_us IS NOT NULL",
            (character_id,),
        ).fetchone()[0])
        before_restart = (
            retired_count,
            tuple((row.scene_subject_id, row.lifecycle_state) for row in repository.list_scene_subjects(character_id, limit=128)),
        )
        database_path = writer.database_path
        writer.store.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        database_bytes = database_path.stat().st_size
        restart_started = time.perf_counter()
        writer.close()
        writer = MemoryV2ShadowWriter(
            root, character_id=character_id, display_name="Synthetic", memory_file=memory_file,
        )
        repository = MemoryV2Repository(writer.store)
        after_restart = (
            int(writer.store.connection.execute(
                "SELECT COUNT(*) FROM active_scene_subjects WHERE character_id=? AND retired_at_us IS NOT NULL",
                (character_id,),
            ).fetchone()[0]),
            tuple((row.scene_subject_id, row.lifecycle_state) for row in repository.list_scene_subjects(character_id, limit=128)),
        )
        restart_ms = (time.perf_counter() - restart_started) * 1000.0
        if before_restart != after_restart:
            raise AssertionError("transient scene snapshot changed across restart")
        writer.close()

        return ScalingReport(
            version="active-scene-scaling-v2",
            transient_created=retired_total + roster_total,
            retired_subjects=retired_count,
            roster_before_sweep=len(roster_before),
            roster_after_sweep=len(roster_after),
            dormant_after_sweep=sum(row.lifecycle_state == "dormant" for row in roster_after),
            reactivation_preserved_identity=distinct_id in reactivated_ids,
            active_relation_orphans=orphan_count,
            database_bytes=database_bytes,
            context_chars=len(current_context),
            internal_ids_present=any(token in current_context for token in (
                "scene-", "relation_id", "event_id", "sha256",
            )),
            lookup_median_ms=round(lookup_ms, 4),
            scene_read_median_ms=round(scene_read_ms, 4),
            capability_derivation_median_ms=round(capability_ms, 4),
            prompt_admission_median_ms=round(context_ms, 4),
            sweep_ms=round(sweep_ms, 4),
            reactivation_ms=round(reactivation_ms, 4),
            restart_ms=round(restart_ms, 4),
        )


def main() -> None:
    print(json.dumps(asdict(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
