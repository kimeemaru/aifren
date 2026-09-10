"""Bounded, privacy-preserving V1/V2 dual-read telemetry."""

from __future__ import annotations

import hashlib
import json
from statistics import quantiles

from memory_v2_store.store import MemoryV2Store, utc_now_us


MAX_TELEMETRY_ROWS = 500


def record_dual_read(
    store: MemoryV2Store,
    *,
    character_id: str,
    query: str,
    v1_ids: list[str],
    v2_ids: list[str],
    comparison_v1_ids: list[str] | None = None,
    v1_latency_ms: float | None,
    v2_latency_ms: float | None,
    retrieval_strategy: str = "unknown",
    error_kind: str | None = None,
) -> None:
    """Store only IDs and a one-way query digest; rotate oldest diagnostics."""
    overlap = len(set(comparison_v1_ids if comparison_v1_ids is not None else v1_ids) & set(v2_ids))
    with store.transaction():
        store.connection.execute(
            """INSERT INTO retrieval_telemetry(recorded_at_us, character_id, query_sha256, v1_ids_json,
               v2_ids_json, overlap_count, v1_abstained, v2_abstained, v1_latency_ms, v2_latency_ms,
               retrieval_strategy, error_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (utc_now_us(), character_id, hashlib.sha256(str(query).encode("utf-8")).hexdigest(),
             json.dumps(v1_ids), json.dumps(v2_ids), overlap, int(not v1_ids), int(not v2_ids),
             v1_latency_ms, v2_latency_ms, str(retrieval_strategy), error_kind),
        )
        store.connection.execute(
            """DELETE FROM retrieval_telemetry WHERE telemetry_id IN (
                   SELECT telemetry_id FROM retrieval_telemetry
                    ORDER BY telemetry_id DESC LIMIT -1 OFFSET ?
               )""",
            (MAX_TELEMETRY_ROWS,),
        )


def retrieval_report(store: MemoryV2Store) -> dict:
    rows = store.connection.execute(
        "SELECT * FROM retrieval_telemetry ORDER BY telemetry_id"
    ).fetchall()
    total = len(rows)
    if not rows:
        return {"total_compared": 0, "successful_compared": 0, "failed_executions": 0, "retention_limit": MAX_TELEMETRY_ROWS, "characters": {}}
    healthy = [row for row in rows if not row["error_kind"]]
    overlap = sum(1 for row in healthy if row["overlap_count"] > 0)
    v1_only = sum(1 for row in healthy if not row["v1_abstained"] and row["v2_abstained"])
    v2_only = sum(1 for row in healthy if row["v1_abstained"] and not row["v2_abstained"])
    abstention = sum(1 for row in healthy if bool(row["v1_abstained"]) != bool(row["v2_abstained"]))
    errors = sum(1 for row in rows if row["error_kind"])
    by_character = {}
    for row in rows:
        bucket = by_character.setdefault(row["character_id"], {"count": 0, "overlap": 0, "errors": 0, "strategies": {}})
        bucket["count"] += 1
        bucket["overlap"] += int(not row["error_kind"] and row["overlap_count"] > 0)
        bucket["errors"] += int(bool(row["error_kind"]))
        strategy = row["retrieval_strategy"]
        bucket["strategies"][strategy] = bucket["strategies"].get(strategy, 0) + 1
    return {
        "total_compared": total,
        "successful_compared": len(healthy),
        "failed_executions": errors,
        "retention_limit": MAX_TELEMETRY_ROWS,
        "overlap_rate": overlap / len(healthy) if healthy else None,
        "v1_only_abstention_rate": v1_only / len(healthy) if healthy else None,
        "v2_only_abstention_rate": v2_only / len(healthy) if healthy else None,
        "abstention_disagreement_rate": abstention / len(healthy) if healthy else None,
        "v2_error_rate": errors / total,
        "v1_latency_ms": _latency_summary(rows, "v1_latency_ms"),
        "v2_latency_ms": _latency_summary(rows, "v2_latency_ms"),
        "characters": by_character,
    }


def _latency_summary(rows, field: str) -> dict:
    values = sorted(float(row[field]) for row in rows if row[field] is not None)
    if not values:
        return {"average": None, "p95": None}
    p95 = values[-1] if len(values) == 1 else quantiles(values, n=20, method="inclusive")[18]
    return {"average": sum(values) / len(values), "p95": p95}
