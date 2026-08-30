#!/usr/bin/env python3
"""Explicit, bounded rebuild of derived Memory V2 episode compaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from character_registry import CharacterRegistry
from conversation.conversation import load_json
from llm.llm import create_llm
from memory_v2_episode_compaction import (
    COMPACTION_VERSION,
    ERA_COMPACTION_VERSION,
    ERA_RETENTION_GATE_VERSION,
    EpisodeCompactionCache,
    EpisodeCompactor,
)
from memory_v2_shadow_writer import MemoryV2ShadowWriter


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild non-authoritative episodic context from canonical conversation history.",
    )
    parser.add_argument("--application-dir", default=str(ROOT))
    parser.add_argument("--character-id")
    args = parser.parse_args()

    application_dir = Path(args.application_dir).resolve()
    registry = CharacterRegistry(application_dir)
    character = registry.get(args.character_id) if args.character_id else registry.active()
    if character is None:
        raise SystemExit("character is absent from the registry")
    paths = registry.runtime_paths(character.character_id)
    messages = load_json(str(paths["conversation"]), [])
    if not isinstance(messages, list):
        raise SystemExit("canonical conversation archive is malformed")

    writer = MemoryV2ShadowWriter(
        application_dir,
        character_id=character.character_id,
        display_name=character.display_name,
        memory_file=paths["memory"],
    )
    try:
        reconciliation = writer.reconcile()
        if reconciliation.get("state") != "ok":
            raise SystemExit("Memory V2 reconciliation failed")
        report = EpisodeCompactionCache(writer.store, character.character_id).rebuild(
            messages, EpisodeCompactor(create_llm()),
        )
        # Numeric/structural report only; no conversation or derived prose.
        print(json.dumps({
            "state": "complete",
            "compaction_version": COMPACTION_VERSION,
            "era_compaction_version": ERA_COMPACTION_VERSION,
            "era_retention_gate_version": ERA_RETENTION_GATE_VERSION,
            "episode_count": report.episode_count,
            "episode_source_record_count": report.source_record_count,
            "consolidated_episode_count": report.consolidated_episode_count,
            "consolidated_source_record_count": report.consolidated_source_record_count,
            "lower_level_episodes_replaced": report.lower_level_episodes_replaced,
            "retention_gate_checked_count": report.retention_gate_checked_count,
            "retention_gate_rejected_count": report.retention_gate_rejected_count,
            "retention_verification_duration_ms": round(
                report.retention_verification_duration_ms, 3
            ),
            "continuity_anchor_count": report.continuity_anchor_count,
            "anchor_refined_episode_count": report.anchor_refined_episode_count,
            "anchor_verification_failed_episode_count": (
                report.anchor_verification_failed_episode_count
            ),
            "anchor_extraction_duration_ms": round(
                report.anchor_extraction_duration_ms, 3
            ),
            "summary_generation_duration_ms": round(
                report.summary_generation_duration_ms, 3
            ),
            "anchor_verification_duration_ms": round(
                report.anchor_verification_duration_ms, 3
            ),
            "anchor_refinement_duration_ms": round(
                report.anchor_refinement_duration_ms, 3
            ),
            "coverage_end_index_exclusive": report.coverage_end_index_exclusive,
            "generated_characters": report.generated_characters,
            "episode_rebuild_duration_ms": round(report.duration_ms, 3),
        }, indent=2, sort_keys=True))
    finally:
        writer.close()


if __name__ == "__main__":
    main()
