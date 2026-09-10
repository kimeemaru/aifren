"""Development-only, read-only Memory V2 shadow comparison.

This module never contributes memories to a prompt or writes V1 data.  Its
database is an explicitly rebuilt, Git-ignored diagnostic snapshot.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from benchmarks.memory_v2.models import RetrievalQuery
from memory_v2_store import EmbeddingLifecycle, MemoryV2Store, MiniLMEmbeddingProvider, SemanticRetrievalV2
from memory_v2_store.v1_import import LEGACY_CHARACTER_NAMESPACE, import_v1_shadow


DEFAULT_SHADOW_DIRECTORY = "memory_v2_shadow"
DEFAULT_SHADOW_DATABASE = "live_shadow.sqlite3"


def _legacy_evidence_id(claim_id: str) -> str:
    return str(uuid.uuid5(LEGACY_CHARACTER_NAMESPACE, f"shadow-evidence:{claim_id}"))


def _source_hashes(source_dir: Path) -> dict[str, str | None]:
    names = ("conversation.json", "conversation_summary.json", "memories.json", "characters/default/character.json")
    result = {}
    for name in names:
        path = source_dir / name
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def default_shadow_path(application_dir: Path | str) -> Path:
    return Path(application_dir).resolve() / DEFAULT_SHADOW_DIRECTORY / DEFAULT_SHADOW_DATABASE


def build_shadow_query(character_id: str, user_message: str, messages, *, at: str | None = None) -> RetrievalQuery:
    """Build the immutable V2 input from user-authored context only."""
    recent_user_turns = [
        str(message.get("content", "")) for message in messages[-16:]
        if isinstance(message, dict) and message.get("role") == "user" and str(message.get("content", "")).strip()
    ]
    # The just-persisted user message is query.current_user_text, not part of
    # its own historical window. Assistant turns are excluded by the filter.
    if recent_user_turns and recent_user_turns[-1] == str(user_message):
        recent_user_turns.pop()
    return RetrievalQuery(character_id, str(user_message), at or datetime.now(timezone.utc).isoformat(), "ordinary", tuple(recent_user_turns[-8:]))


def rebuild_shadow(source_dir: Path | str, destination: Path | str, *, replace: bool = False, provider_factory=MiniLMEmbeddingProvider) -> dict:
    """Explicitly create a disposable diagnostic V2 snapshot from V1 JSON."""
    source_dir, destination = Path(source_dir).resolve(), Path(destination).resolve()
    sidecars = (destination, destination.with_suffix(destination.suffix + ".manifest.json"), destination.with_suffix(destination.suffix + ".report.json"))
    if any(path.exists() for path in sidecars):
        if not replace:
            raise ValueError("Shadow destination already exists; pass replace=True after reviewing the path.")
        for path in sidecars:
            path.unlink(missing_ok=True)
    result = import_v1_shadow(source_dir, destination)
    store = MemoryV2Store(str(destination))
    try:
        # V1 memories have no source event IDs. Add only a diagnostic record
        # pointing back to the legacy memory record; preserve their explicitly
        # unverified provenance and never claim direct-user evidence.
        claims = store.connection.execute("SELECT character_id, claim_id FROM claims ORDER BY claim_id").fetchall()
        for sequence, claim in enumerate(claims, 1_000_001):
            event_id = _legacy_evidence_id(claim["claim_id"])
            store.add_event(
                claim["character_id"], event_id,
                sequence,
                event_type="legacy_memory_record", actor_kind="system",
                recorded_at_us=0, content_text=None, source_origin="legacy_v1_memory_record",
                source_reference=f"memories.json#{claim['claim_id']}",
            )
            store.attach_evidence(claim["character_id"], claim["claim_id"], event_id,
                                  evidence_role="legacy_memory_record", evidence_strength=None)
        store.ensure_fts()
        provider = provider_factory()
        embedding = EmbeddingLifecycle(store, provider, include_legacy_unverified=True).rebuild_all()
        if store.integrity_check() != "ok":
            raise ValueError("Shadow SQLite quick_check failed after derived rebuild.")
    finally:
        store.close()
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["shadow_mode"] = {"v2_legacy_evidence": "legacy_memory_record", "embeddings": embedding, "source_snapshot_sha256": _source_hashes(source_dir)}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {"destination": str(destination), "manifest": str(manifest_path), "character_id": result.character_id, "embedding": embedding}


class MemoryV2ShadowComparator:
    """Per-turn observer. Failure returns diagnostics and never raises to a turn."""

    def __init__(self, application_dir: Path | str, *, shadow_path: Path | str | None = None, console: bool = True, provider_factory=MiniLMEmbeddingProvider):
        self.application_dir = Path(application_dir).resolve()
        self.shadow_path = Path(shadow_path).resolve() if shadow_path else default_shadow_path(self.application_dir)
        self.console = console
        self.provider_factory = provider_factory
        self._store = None
        self._retriever = None
        self._character_id = None

    def close(self):
        if self._store is not None:
            self._store.close()
            self._store = None

    def freshness(self) -> dict:
        manifest_path = self.shadow_path.with_suffix(self.shadow_path.suffix + ".manifest.json")
        if not self.shadow_path.exists() or not manifest_path.exists():
            return {"state": "missing", "path": str(self.shadow_path)}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = {name: data.get("sha256") if data else None for name, data in manifest.get("source_files", {}).items()}
            actual = _source_hashes(self.application_dir)
            if expected != actual:
                return {"state": "stale", "path": str(self.shadow_path), "source_snapshot": manifest.get("aggregate_digest")}
            store = MemoryV2Store(str(self.shadow_path))
            try:
                if store.integrity_check() != "ok" or store.connection.execute("PRAGMA foreign_key_check").fetchall():
                    return {"state": "invalid", "path": str(self.shadow_path), "reason": "sqlite_integrity_failed"}
            finally:
                store.close()
            return {"state": "current", "path": str(self.shadow_path), "source_snapshot": manifest.get("aggregate_digest"), "character_id": manifest["character_mapping"]["characters/default"]}
        except Exception as error:
            return {"state": "invalid", "path": str(self.shadow_path), "reason": type(error).__name__}

    def compare(self, user_message: str, messages, v1_selected=()) -> dict:
        freshness = self.freshness()
        base = {"shadow": freshness, "v1": {"selected": list(v1_selected), "count": len(v1_selected)}}
        if freshness["state"] != "current":
            return {**base, "v2": {"selected": [], "count": 0, "abstention_reason": "shadow_not_current"}, "comparison": {"overlap": [], "v1_only": [item["id"] for item in v1_selected], "v2_only": [], "count_difference": -len(v1_selected)}}
        try:
            if self._store is None:
                self._store = MemoryV2Store(str(self.shadow_path))
                self._character_id = freshness["character_id"]
                provider = self.provider_factory()
                self._retriever = SemanticRetrievalV2(self._store, embedding_provider=provider, allow_legacy_unverified=True)
            query = build_shadow_query(self._character_id, user_message, messages)
            outcome = self._retriever.retrieve(query)
            selected = [{"claim_id": item.claim_id, "label": item.label, "estimated_tokens": item.estimated_tokens} for item in outcome.selected_memories]
            traces = [{"claim_id": trace.claim_id, "channels": trace.candidate_channels, "ranks": trace.channel_ranks, "scores": trace.score_components, "state": trace.selection_state, "reason": trace.exclusion_reason or trace.suppression_reason} for trace in outcome.traces]
            v1_ids = {item["id"] for item in v1_selected}
            # A V1 ID maps through the imported legacy metadata; unsupported
            # mapping simply remains V1-only rather than guessing.
            mapped = self._v1_claim_map()
            v1_claims = {mapped[item] for item in v1_ids if item in mapped}
            v2_ids = {item["claim_id"] for item in selected}
            result = {**base, "v2": {"selected": selected, "count": len(selected), "abstention_reason": outcome.abstention_reason, "traces": traces}, "comparison": {"overlap": sorted(v1_claims & v2_ids), "v1_only": sorted(v1_ids - {key for key, value in mapped.items() if value in v2_ids}), "v2_only": sorted(v2_ids - v1_claims), "count_difference": len(selected) - len(v1_selected)}}
            if self.console:
                suppressed = [{"claim_id": item["claim_id"], "reason": item["reason"]}
                              for item in traces if item["reason"]]
                print("MEMORY SHADOW", json.dumps({
                    "shadow": freshness["state"], "v1_selected": list(v1_selected),
                    "v2_selected": selected, "suppressed": suppressed,
                    "abstention": outcome.abstention_reason,
                }, sort_keys=True))
            return result
        except Exception as error:
            return {**base, "v2": {"selected": [], "count": 0, "abstention_reason": "shadow_failure"}, "comparison": {"overlap": [], "v1_only": [item["id"] for item in v1_selected], "v2_only": [], "count_difference": -len(v1_selected)}, "error": {"source": "memory_v2_shadow", "kind": type(error).__name__}}

    def _v1_claim_map(self):
        rows = self._store.connection.execute("SELECT claim_id, legacy_metadata_json FROM claims").fetchall()
        mapping = {}
        for row in rows:
            try:
                legacy = json.loads(row["legacy_metadata_json"] or "{}")
                mapping[str(legacy["legacy_memory_id"])] = row["claim_id"]
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        return mapping


def main():
    parser = argparse.ArgumentParser(description="Explicit Memory V2 shadow snapshot helper.")
    parser.add_argument("command", choices=("rebuild", "status"))
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--destination")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    source = Path(args.source_dir).resolve()
    destination = Path(args.destination).resolve() if args.destination else default_shadow_path(source)
    if args.command == "rebuild":
        print(json.dumps(rebuild_shadow(source, destination, replace=args.replace), indent=2, sort_keys=True))
    else:
        print(json.dumps(MemoryV2ShadowComparator(source, shadow_path=destination, console=False).freshness(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
