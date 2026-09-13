#!/usr/bin/env python3
"""Resolve a structural Development recall trace for one authorized character."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aifren.character.character_registry import CharacterRegistry  # noqa: E402
from aifren.continuity.memory_v2_historical_evidence import validate_staged_disposable_target  # noqa: E402
from aifren.continuity.memory_v2_shadow_writer import default_v2_path  # noqa: E402


def _latest_bundle() -> Path:
    bundles = sorted(Path("/tmp").glob("aifren-flight-recorder-*/memory_recall_shadow.json"))
    if not bundles:
        raise SystemExit("No Development memory-recall shadow bundle was found.")
    return bundles[-1].parent


def _summary(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_report(
    application_dir: Path,
    character_id: str,
    bundle: Path,
    *,
    staged_database: Path | None = None,
) -> dict:
    registry = CharacterRegistry(application_dir)
    character = registry.get(character_id)
    if character is None:
        raise ValueError("character is absent from the registry")
    paths = registry.runtime_paths(character.character_id)
    trace_path = bundle / "memory_recall_shadow.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    records = payload.get("records", ()) if isinstance(payload, dict) else ()
    expected_key = hashlib.sha256(character.character_id.encode("utf-8")).hexdigest()
    conversation = json.loads(paths["conversation"].read_text(encoding="utf-8"))
    memories = json.loads(paths["memory"].read_text(encoding="utf-8"))
    v1 = {str(item.get("id")): item for item in memories if isinstance(item, dict)}
    database = default_v2_path(application_dir)
    if staged_database is not None:
        database = validate_staged_disposable_target(
            application_dir, character.character_id, staged_database,
            paths["conversation"],
        )
    store = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    store.row_factory = sqlite3.Row
    try:
        turns = []
        for record in list(records)[-50:]:
            if not isinstance(record, dict) or record.get("character_key") != expected_key:
                continue
            index = record.get("canonical_user_index")
            query = "[canonical turn unavailable]"
            if isinstance(index, int) and 0 <= index < len(conversation):
                row = conversation[index]
                if isinstance(row, dict) and row.get("role") == "user":
                    query = _summary(row.get("content"), 500)
            v1_rows = []
            for selected in record.get("v1", ()):
                source = v1.get(str(selected.get("memory_id")), {})
                v1_rows.append({**selected, "summary": _summary(source.get("content"))})
            v2_rows = []
            for selected in record.get("v2", ()):
                memory_id = str(selected.get("memory_id", ""))
                claim = store.execute(
                    "SELECT content FROM claims WHERE character_id=? AND claim_id=?",
                    (character.character_id, memory_id),
                ).fetchone()
                episode = None
                if claim is None:
                    episode_id = str(selected.get("episode_id", ""))
                    episode = store.execute(
                        "SELECT content FROM summaries WHERE character_id=? AND summary_id=?",
                        (character.character_id, episode_id or memory_id),
                    ).fetchone()
                canonical_source = None
                canonical_index = selected.get("canonical_index")
                if isinstance(canonical_index, int) and 0 <= canonical_index < len(conversation):
                    source = conversation[canonical_index]
                    if isinstance(source, dict):
                        canonical_source = source.get("content")
                evidence = store.execute(
                    """SELECT e.event_type, e.source_reference, e.sequence, e.recorded_at_us
                         FROM claim_evidence ce LEFT JOIN events e
                           ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                        WHERE ce.character_id=? AND ce.claim_id=?
                        ORDER BY COALESCE(e.sequence, 2147483647) LIMIT 4""",
                    (character.character_id, memory_id),
                ).fetchall()
                v2_rows.append({
                    **selected,
                    "summary": _summary(
                        canonical_source
                        if canonical_source is not None
                        else (claim or episode or {"content": ""})["content"]
                    ),
                    "resolved_evidence": [dict(row) for row in evidence],
                })
            overlap = list(record.get("overlap_claim_ids", ()))
            shadow_failed = bool(record.get("error_kind")) or (
                record.get("v2_abstention_reason") == "shadow_failure"
            ) or (
                record.get("retrieval_health") in {"incomplete", "unreported"}
            )
            if shadow_failed:
                classification = "shadow_failure"
            elif overlap:
                classification = "overlap"
            elif v1_rows and v2_rows:
                classification = "divergent"
            elif v1_rows:
                classification = "v1_only"
            elif v2_rows:
                classification = "v2_only"
            else:
                classification = "both_abstained"
            turns.append({
                "turn_id": record.get("turn_id"),
                "query": query,
                "v1": v1_rows,
                "v2": v2_rows,
                "classification": classification,
                "overlap_claim_ids": overlap,
                "v2_abstention_reason": record.get("v2_abstention_reason", ""),
                "v1_latency_ms": record.get("v1_latency_ms"),
                "v2_latency_ms": record.get("v2_latency_ms"),
                "error_kind": record.get("error_kind", ""),
                "error_stage": record.get("error_stage", ""),
                "sqlite_error_name": record.get("sqlite_error_name", ""),
                "sqlite_error_code": record.get("sqlite_error_code"),
                "error_disposition": record.get("error_disposition", ""),
                "retry_disposition": record.get("retry_disposition", ""),
                "retrieval_health": record.get("retrieval_health", ""),
                "retrieval_error_stage": record.get("retrieval_error_stage", ""),
                "retrieval_error_code": record.get("retrieval_error_code", ""),
            })
        classifications = {
            name: sum(1 for turn in turns if turn["classification"] == name)
            for name in (
                "overlap", "divergent", "v1_only", "v2_only",
                "both_abstained", "shadow_failure",
            )
        }
        all_v2_latencies = [
            float(turn["v2_latency_ms"]) for turn in turns
            if isinstance(turn.get("v2_latency_ms"), (int, float))
        ]
        successful_v2_latencies = [
            float(turn["v2_latency_ms"]) for turn in turns
            if turn["classification"] != "shadow_failure"
            and isinstance(turn.get("v2_latency_ms"), (int, float))
        ]
        return {
            "character_id": character.character_id,
            "display_name": character.display_name,
            "bundle": str(bundle),
            "turn_count": len(turns),
            "classification_counts": classifications,
            "average_v2_latency_ms": (
                sum(successful_v2_latencies) / len(successful_v2_latencies)
                if successful_v2_latencies else None
            ),
            "average_shadow_execution_latency_ms": (
                sum(all_v2_latencies) / len(all_v2_latencies)
                if all_v2_latencies else None
            ),
            "turns": turns,
        }
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a private Development V1/V2 recall shadow capture.",
    )
    parser.add_argument("--application-dir", default=".")
    parser.add_argument(
        "--character-id", required=True,
        help="Authorized disposable/default character UUID (never an implicit active character).",
    )
    parser.add_argument("--bundle", help="Flight-recorder bundle directory; defaults to the latest shadow bundle.")
    parser.add_argument(
        "--staged-disposable-database",
        help=(
            "Marker-bound disposable SQLite path for Development staged QA; "
            "arbitrary databases are rejected."
        ),
    )
    args = parser.parse_args()
    application_dir = Path(args.application_dir).resolve()
    bundle = Path(args.bundle).resolve() if args.bundle else _latest_bundle()
    staged_database = (
        Path(args.staged_disposable_database).resolve()
        if args.staged_disposable_database else None
    )
    print(json.dumps(build_report(
        application_dir, args.character_id, bundle,
        staged_database=staged_database,
    ), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
