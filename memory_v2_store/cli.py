"""Small maintenance CLI backed by the real V2 repository."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .production_import import export_v2_json, import_v1_memories
from .store import MemoryV2Store
from memory_v2_telemetry import retrieval_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory V2 maintenance helper.")
    parser.add_argument(
        "command",
        choices=("status", "migrate-v1", "export", "integrity-check", "retrieval-report"),
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--destination")
    parser.add_argument("--character-id")
    args = parser.parse_args()
    store = MemoryV2Store(str(Path(args.database).resolve()))
    try:
        if args.command == "migrate-v1":
            print(json.dumps(asdict(import_v1_memories(store, args.source_dir, character_id=args.character_id)), indent=2, sort_keys=True))
        elif args.command == "export":
            if not args.destination:
                raise SystemExit("--destination is required for export")
            print(export_v2_json(store, args.destination, character_id=args.character_id))
        elif args.command == "integrity-check":
            print(json.dumps({"quick_check": store.integrity_check(), "foreign_keys": store.connection.execute("PRAGMA foreign_key_check").fetchall() == []}, sort_keys=True))
        elif args.command == "retrieval-report":
            print(json.dumps(retrieval_report(store), indent=2, sort_keys=True))
        else:
            print(json.dumps({"schema_version": store.schema_version(), "quick_check": store.integrity_check(), "characters": store.connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0], "claims": store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]}, sort_keys=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
