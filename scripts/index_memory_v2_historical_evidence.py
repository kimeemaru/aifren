#!/usr/bin/env python3
"""Index one bounded page of canonical historical evidence in a staged clone."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aifren.character.character_registry import CharacterRegistry  # noqa: E402
from aifren.continuity.memory_v2_historical_evidence import (  # noqa: E402
    MAX_ARCHIVE_RECORDS,
    HistoricalEvidenceError,
    HistoricalEvidenceIndexer,
    open_staged_historical_evidence_writer,
)
from aifren.memory_v2_store import EmbeddingLifecycle, MiniLMEmbeddingProvider  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index one bounded page of canonical occurrences into a staged V2 database.",
    )
    parser.add_argument("--application-dir", required=True)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--maximum-records", type=int, default=128)
    parser.add_argument(
        "--confirm-staged-disposable",
        action="store_true",
        help="Required acknowledgement; live production V2 databases are rejected regardless.",
    )
    parser.add_argument(
        "--rebuild-derived",
        action="store_true",
        help="On completion, rebuild FTS and missing embeddings for shadow evaluation.",
    )
    args = parser.parse_args()
    if not args.confirm_staged_disposable:
        raise SystemExit("Refusing evidence indexing without --confirm-staged-disposable")
    application_dir = Path(args.application_dir).resolve()
    database = Path(args.database)
    try:
        writer = open_staged_historical_evidence_writer(
            application_dir, args.character_id, database,
        )
    except HistoricalEvidenceError as error:
        raise SystemExit(str(error)) from error
    try:
        # The guarded factory is intentionally the first registry access.  It
        # proves the existing clone/marker before CharacterRegistry could ever
        # initialize a missing registry in a rejected application directory.
        registry = CharacterRegistry(application_dir)
        character = registry.get(args.character_id)
        assert character is not None
        paths = registry.runtime_paths(character.character_id)
        page = HistoricalEvidenceIndexer(
            writer, paths["conversation"],
            source_key="conversation.json",
            confirm_staged_disposable=True,
        ).run_page(maximum_records=args.maximum_records)
        result: dict[str, object] = {"page": asdict(page)}
        if page.complete and args.rebuild_derived:
            result["fts_rows"] = writer.store.rebuild_fts()
            rows = writer.store.connection.execute(
                """SELECT claim_id FROM historical_evidence
                     WHERE character_id=? AND retrieval_eligible=1
                     ORDER BY canonical_index LIMIT ?""",
                (args.character_id, MAX_ARCHIVE_RECORDS + 1),
            ).fetchall()
            if len(rows) > MAX_ARCHIVE_RECORDS:
                raise HistoricalEvidenceError(
                    "historical embedding selection exceeds the archive bound",
                )
            result["embedding"] = EmbeddingLifecycle(
                writer.store, MiniLMEmbeddingProvider(),
            ).rebuild_claims(tuple(str(row[0]) for row in rows))
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        writer.close()


if __name__ == "__main__":
    main()
