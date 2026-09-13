#!/usr/bin/env python3
"""Rebuild cache-validated historical episodes in an attested disposable clone."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aifren.character.character_registry import CharacterRegistry
from aifren.llm.maintenance import configured_maintenance_provider
from aifren.continuity.memory_v2_episode_compaction import EpisodeCompactor
from aifren.continuity.memory_v2_historical_evidence import open_staged_historical_evidence_writer
from aifren.continuity.memory_v2_historical_episodes import HistoricalEpisodeRebuilder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild validated historical episodes in a disposable V2 clone.",
    )
    parser.add_argument("--application-dir", required=True)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm-staged-disposable", action="store_true")
    args = parser.parse_args()
    if not args.confirm_staged_disposable:
        raise SystemExit("Refusing historical episode rebuild without --confirm-staged-disposable")

    root = Path(args.application_dir).resolve()
    registry = CharacterRegistry(root)
    character = registry.get(args.character_id)
    if character is None:
        raise SystemExit("character is absent from the staged clone registry")
    paths = registry.runtime_paths(character.character_id)
    writer = open_staged_historical_evidence_writer(
        root, character.character_id, args.database,
    )
    try:
        with configured_maintenance_provider(ROOT) as provider:
            report = HistoricalEpisodeRebuilder(
                writer, paths["conversation"], confirm_staged_disposable=True,
            ).rebuild(EpisodeCompactor(provider.provider))
        print(json.dumps({
            "state": "complete",
            "provider_adapter": provider.adapter,
            "provider_model": provider.model,
            "provider_mode": provider.mode,
            "runtime_compute": provider.runtime_compute,
            "runtime_ownership": provider.runtime_ownership,
            **asdict(report),
        }, indent=2, sort_keys=True))
    finally:
        writer.close()


if __name__ == "__main__":
    main()
