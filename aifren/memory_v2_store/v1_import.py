"""Explicit, strict V1 JSON -> disposable V2 SQLite shadow import tool."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .store import MemoryV2Store, SCHEMA_VERSION, StoreError


IMPORTER_VERSION = "1"
LEGACY_CHARACTER_NAMESPACE = uuid.UUID("f4da4317-6e5f-43ad-9f2b-a17f4d2b0c58")
SOURCE_NAMES = ("conversation.json", "conversation_summary.json", "memories.json", "characters/default/character.json")


class V1ImportError(ValueError):
    pass


@dataclass
class ImportResult:
    passed: bool
    destination: str
    manifest_path: str
    report_path: str
    event_count: int
    claim_count: int
    summary_count: int
    character_id: str
    aggregate_digest: str
    unknown_fields: dict


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, *, required: bool):
    if not path.exists():
        if required:
            raise V1ImportError(f"Required source file is missing: {path}")
        return None, None
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), {"path": str(path.resolve()), "size": len(raw), "sha256": _digest_bytes(raw)}
    except UnicodeDecodeError as error:
        raise V1ImportError(f"Source file is not UTF-8: {path}") from error
    except json.JSONDecodeError as error:
        raise V1ImportError(f"Source file contains malformed JSON: {path}") from error
    except OSError as error:
        raise V1ImportError(f"Could not read source file: {path}") from error


def _timestamp(value, field: str):
    if value is None:
        return 0, "legacy_unknown", None
    if not isinstance(value, str) or not value.strip():
        raise V1ImportError(f"{field} must be a non-empty timestamp string when present.")
    raw = value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise V1ImportError(f"{field} is not an ISO-8601 timestamp.") from error
    precision = "instant" if parsed.tzinfo else "legacy_naive_assumed_utc"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000), precision, raw


def _stable_id(kind: str, legacy_key: str) -> str:
    return str(uuid.uuid5(LEGACY_CHARACTER_NAMESPACE, f"{kind}:{legacy_key}"))


def _validate_sources(source_dir: Path):
    conversation, conversation_meta = _read_json(source_dir / "conversation.json", required=True)
    summary, summary_meta = _read_json(source_dir / "conversation_summary.json", required=False)
    memories, memories_meta = _read_json(source_dir / "memories.json", required=True)
    character, character_meta = _read_json(source_dir / "characters/default/character.json", required=True)
    if not isinstance(conversation, list): raise V1ImportError("conversation.json must contain a JSON array.")
    if not isinstance(memories, list): raise V1ImportError("memories.json must contain a JSON array.")
    if not isinstance(character, dict) or not isinstance(character.get("name"), str) or not character["name"].strip(): raise V1ImportError("character.json must be an object with non-empty name.")
    if summary is not None:
        if not isinstance(summary, dict) or not isinstance(summary.get("summary"), str) or isinstance(summary.get("summarized_messages"), bool) or not isinstance(summary.get("summarized_messages"), int): raise V1ImportError("conversation_summary.json has invalid structure.")
        if not 0 <= summary["summarized_messages"] <= len(conversation): raise V1ImportError("Summary range is inconsistent with conversation count.")
    memory_ids = set()
    for index, message in enumerate(conversation):
        if not isinstance(message, dict): raise V1ImportError(f"Conversation record {index} must be an object.")
        if set(("role", "content")) - set(message): raise V1ImportError(f"Conversation record {index} is missing role/content.")
        if message["role"] not in {"user", "assistant", "system"} or not isinstance(message["content"], str): raise V1ImportError(f"Conversation record {index} has invalid role/content.")
        _timestamp(message.get("timestamp"), f"Conversation record {index} timestamp")
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict): raise V1ImportError(f"Memory record {index} must be an object.")
        required = {"id", "category", "content", "importance"}
        if required - set(memory): raise V1ImportError(f"Memory record {index} is missing required fields.")
        if isinstance(memory["id"], bool) or not isinstance(memory["id"], int) or memory["id"] < 1 or memory["id"] in memory_ids: raise V1ImportError(f"Memory record {index} has duplicate/invalid id.")
        if not isinstance(memory["category"], str) or not memory["category"].strip() or not isinstance(memory["content"], str) or not memory["content"].strip(): raise V1ImportError(f"Memory record {index} has invalid category/content.")
        if isinstance(memory["importance"], bool) or not isinstance(memory["importance"], int) or not 1 <= memory["importance"] <= 10: raise V1ImportError(f"Memory record {index} has invalid importance.")
        if "keywords" in memory and (not isinstance(memory["keywords"], list) or not all(isinstance(item, str) for item in memory["keywords"])): raise V1ImportError(f"Memory record {index} has invalid keywords.")
        if "embedding" in memory and (not isinstance(memory["embedding"], list) or len(memory["embedding"]) != 384 or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in memory["embedding"])): raise V1ImportError(f"Memory record {index} has invalid embedding.")
        if "provenance" in memory and (not isinstance(memory["provenance"], dict) or not isinstance(memory["provenance"].get("source"), str)): raise V1ImportError(f"Memory record {index} has invalid provenance.")
        for timestamp_key in ("created", "updated"):
            _timestamp(memory.get(timestamp_key), f"Memory record {index} {timestamp_key}")
        memory_ids.add(memory["id"])
    return conversation, summary, memories, character, {"conversation.json": conversation_meta, "conversation_summary.json": summary_meta, "memories.json": memories_meta, "characters/default/character.json": character_meta}


def import_v1_shadow(source_dir: str | Path, destination: str | Path, *, report_path: str | Path | None = None) -> ImportResult:
    """Strictly import V1 JSON into a new disposable database; never writes V1."""
    source_dir, destination = Path(source_dir).resolve(), Path(destination).resolve()
    if destination.exists(): raise V1ImportError("Destination database already exists; refusing to overwrite it.")
    conversation, summary, memories, character, source_meta = _validate_sources(source_dir)
    before = {name: (meta or {}).get("sha256") for name, meta in source_meta.items()}
    legacy_key = "characters/default"
    character_id = _stable_id("character", legacy_key)
    unknown = {"conversation": sorted(set().union(*(set(item) for item in conversation)) - {"role", "content", "timestamp"}) if conversation else [], "memories": sorted(set().union(*(set(item) for item in memories)) - {"id", "category", "content", "importance", "created", "updated", "keywords", "embedding", "provenance"}) if memories else [], "character": sorted(set(character) - {"name", "description", "version", "voice", "avatar"}), "summary": sorted(set(summary or {}) - {"summary", "summarized_messages"})}
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    store = None
    try:
        store = MemoryV2Store(str(temp_path))
        store.create_character(character_id, character["name"], legacy_config_key=legacy_key, metadata={"legacy_config_path": "characters/default/character.json", "legacy_config_sha256": source_meta["characters/default/character.json"]["sha256"], "legacy_config": character})
        for sequence, message in enumerate(conversation, 1):
            recorded_at, precision, raw_timestamp = _timestamp(message.get("timestamp"), f"Conversation record {sequence - 1} timestamp")
            event_id = _stable_id("event", f"{legacy_key}:{sequence - 1}")
            payload = {"legacy_source_file": "conversation.json", "legacy_index": sequence - 1, "legacy_timestamp": raw_timestamp, "legacy_record": {key: value for key, value in message.items() if key not in {"role", "content", "timestamp"}}}
            store.add_event(character_id, event_id, sequence, event_type="message", actor_kind=message["role"], recorded_at_us=recorded_at, temporal_precision=precision, content_text=message["content"], payload=payload, source_origin="legacy_v1_import", source_reference=f"conversation.json#{sequence - 1}")
        for memory in memories:
            created_at, precision, raw_created = _timestamp(memory.get("created"), f"Memory {memory['id']} created")
            _, _, raw_updated = _timestamp(memory.get("updated"), f"Memory {memory['id']} updated")
            claim_id = _stable_id("claim", f"{legacy_key}:memory:{memory['id']}")
            metadata = dict(memory)
            metadata.update({"legacy_memory_id": memory["id"], "legacy_created": raw_created, "legacy_updated": raw_updated})
            store.add_claim(character_id, claim_id, claim_type=memory["category"], assertion_scope="user_fact", content=memory["content"], importance=memory["importance"], valid_from_us=created_at or None, temporal_precision=precision, provenance_state="legacy_unverified", curator_name="legacy_v1_import", curator_version=IMPORTER_VERSION, curator_policy_version="legacy_unverified", legacy_metadata=metadata, created_at_us=created_at)
        summary_count = 0
        if summary is not None and summary["summary"]:
            summary_id = _stable_id("summary", legacy_key)
            store.add_summary(character_id, summary_id, summary["summary"], source_count=summary["summarized_messages"], provenance_state="legacy_unverified", generator_name="legacy_v1_unknown", generator_version=None, legacy_metadata=summary, created_at_us=0)
            if summary["summarized_messages"]:
                store.add_summary_source_range(character_id, summary_id, 1, summary["summarized_messages"])
            summary_count = 1
        if store.integrity_check() != "ok": raise V1ImportError("SQLite quick_check failed.")
        store.close()
        store = None
        os.replace(temp_path, destination)
    except Exception:
        if store is not None:
            store.close()
        if temp_path.exists(): temp_path.unlink()
        raise
    after_data = _validate_sources(source_dir)[4]
    after = {name: (meta or {}).get("sha256") for name, meta in after_data.items()}
    if before != after:
        destination.unlink(missing_ok=True)
        raise V1ImportError("Source files changed during import; shadow database removed.")
    verify = verify_v1_shadow(source_dir, destination, expected_character_id=character_id)
    if not verify["passed"]:
        destination.unlink(missing_ok=True)
        raise V1ImportError("Shadow verification failed; shadow database removed.")
    aggregate = hashlib.sha256(json.dumps(verify["identity_digest"], sort_keys=True).encode()).hexdigest()
    manifest = {"tool_version": IMPORTER_VERSION, "schema_version": SCHEMA_VERSION, "imported_at_utc": datetime.now(timezone.utc).isoformat(), "source_files": source_meta, "character_mapping": {legacy_key: character_id}, "source_counts": {"conversation": len(conversation), "memories": len(memories), "summary": summary_count}, "destination": str(destination), "status": "PASS", "aggregate_digest": aggregate, "unknown_fields": unknown}
    report = {"passed": True, "verification": verify, "manifest": manifest}
    report_path = Path(report_path).resolve() if report_path else destination.with_suffix(destination.suffix + ".report.json")
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return ImportResult(True, str(destination), str(manifest_path), str(report_path), len(conversation), len(memories), summary_count, character_id, aggregate, unknown)


def verify_v1_shadow(source_dir: str | Path, destination: str | Path, *, expected_character_id: str | None = None) -> dict:
    source_dir, destination = Path(source_dir).resolve(), Path(destination).resolve()
    conversation, summary, memories, _, source_meta = _validate_sources(source_dir)
    store = MemoryV2Store(str(destination))
    try:
        character = store.connection.execute("SELECT character_id FROM characters").fetchall()
        character_id = character[0][0] if len(character) == 1 else None
        event_rows = store.connection.execute("SELECT sequence, event_id, content_sha256 FROM events WHERE character_id = ? ORDER BY sequence", (character_id,)).fetchall() if character_id else []
        claim_rows = store.connection.execute("SELECT claim_id, content, provenance_state FROM claims WHERE character_id = ? ORDER BY claim_id", (character_id,)).fetchall() if character_id else []
        sequences_ok = [row["sequence"] for row in event_rows] == list(range(1, len(conversation) + 1))
        hashes_ok = len(event_rows) == len(conversation) and all(row["content_sha256"] == _digest_bytes(message["content"].encode("utf-8")) for row, message in zip(event_rows, conversation))
        content_ok = len(claim_rows) == len(memories) and sorted(row["content"] for row in claim_rows) == sorted(memory["content"] for memory in memories)
        provenance_ok = all(row["provenance_state"] == "legacy_unverified" for row in claim_rows) and store.connection.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 0
        summaries_ok = store.connection.execute("SELECT COUNT(*) FROM summaries WHERE character_id = ?", (character_id,)).fetchone()[0] == (1 if summary and summary["summary"] else 0)
        identity = {"character_id": character_id, "event_ids": [row["event_id"] for row in event_rows], "claim_ids": [row["claim_id"] for row in claim_rows], "event_hashes": [row["content_sha256"] for row in event_rows], "source_hashes": {name: (meta or {}).get("sha256") for name, meta in source_meta.items()}}
        checks = {"character_mapping": expected_character_id is None or character_id == expected_character_id, "event_count": len(event_rows) == len(conversation), "claim_count": len(claim_rows) == len(memories), "sequence": sequences_ok, "event_hashes": hashes_ok, "claim_content": content_ok, "no_fabricated_provenance": provenance_ok, "summary": summaries_ok, "quick_check": store.integrity_check() == "ok", "foreign_keys": store.connection.execute("PRAGMA foreign_key_check").fetchall() == []}
        return {"passed": all(checks.values()), "checks": checks, "identity_digest": identity}
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a disposable, read-only V1 -> V2 SQLite shadow import.")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report-path")
    args = parser.parse_args()
    try:
        print(json.dumps(asdict(import_v1_shadow(args.source_dir, args.destination, report_path=args.report_path)), indent=2, sort_keys=True))
    except V1ImportError as error:
        raise SystemExit(f"IMPORT FAILED: {error}")


if __name__ == "__main__":
    main()
