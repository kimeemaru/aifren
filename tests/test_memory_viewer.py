from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from memory.memory import EMBEDDING_DIMENSIONS, Memory
from memory_viewer import MemoryViewer
from memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor
from memory_v2_store import (
    MemoryV2Repository, MemoryV2Store, OpenThreadProposal,
    OpenThreadProposalOperation,
)


class _Embedding:
    def encode(self, _content):
        return [0.0] * EMBEDDING_DIMENSIONS


class _Llm:
    pass


class _EpisodeProvider:
    def generate(self, _context, prompt, *, seed=None):
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        return "The synthetic participants discussed a bounded test topic."


def _episode_messages(exchange_count=30):
    rows = []
    for index in range(exchange_count):
        rows.extend((
            {"role": "user", "content": f"Synthetic user topic {index}.",
             "timestamp": f"2026-01-01T00:{index:02d}:00Z"},
            {"role": "assistant", "content": f"Synthetic assistant detail {index}.",
             "timestamp": f"2026-01-01T00:{index:02d}:01Z"},
        ))
    return rows


class MemoryViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.memory_path = self.root / "memories.json"
        self.memory = Memory(_Llm(), memory_file=str(self.memory_path), embedding_model=_Embedding())
        self.database_path = self.root / "memory-v2.sqlite3"
        self.store = MemoryV2Store(str(self.database_path))
        self.repository = MemoryV2Repository(self.store)
        self.repository.ensure_character(self.character_a, "Synthetic A")
        self.repository.ensure_character(self.character_b, "Synthetic B")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp.cleanup()

    def add_durable(self, character_id, claim_id, subject_key, content, sequence, at_us):
        event_id = f"event-{claim_id}"
        self.store.add_event(
            character_id, event_id, sequence, event_type="canonical_user_message",
            actor_kind="user", recorded_at_us=at_us, content_text=content,
            source_origin="canonical_conversation",
            source_reference=f"conversation.json#{sequence - 1}",
        )
        self.store.add_durable_claim(
            character_id, claim_id, subject_key=subject_key, content=content,
            evidence_event_id=event_id, evidence_role="direct_user_statement",
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=len(content),
            valid_from_us=at_us, created_at_us=at_us,
            curator_name="synthetic-test", curator_version="1",
            curator_policy_version="test",
        )

    def viewer(self, character_id=None, store=True, *, episode_cache=None, messages=()):
        return MemoryViewer(
            self.memory, self.store if store else None,
            character_id or self.character_a,
            episode_cache=episode_cache, canonical_messages=messages,
        )

    def test_v1_and_v2_authority_labels_are_explicit_and_distinct(self):
        self.memory.add_memory("preference", "The user likes tea.", source="synthetic_test")
        self.add_durable(
            self.character_a, "durable-name", "identity.name",
            "The user's name is River.", 1, 1_000,
        )

        v1 = self.viewer().page(lane="v1")
        v2 = self.viewer().page(lane="v2_claims")

        self.assertIn("canonical prompt-facing", v1["authority_label"])
        self.assertIn("canonical prompt-facing authority", v1["items"][0]["authority"])
        self.assertIn("V2 current and historical records", v2["authority_label"])
        self.assertIn("governed structured authority", v2["items"][0]["authority"])
        self.assertNotIn("canonical Memory V1", v2["items"][0]["authority"])

    def test_character_isolation_bounded_pages_and_search(self):
        for index in range(27):
            content = f"Synthetic memory {index}"
            if index == 17:
                content += " telescope"
            self.memory.add_memory("test", content, source="synthetic_test")
        self.add_durable(
            self.character_a, "a-name", "identity.name",
            "The user's name is Alpha.", 1, 1_000,
        )
        self.add_durable(
            self.character_b, "b-name", "identity.name",
            "The user's name is Beta.", 1, 1_000,
        )

        first = self.viewer().page(lane="v1", limit=10)
        second = self.viewer().page(lane="v1", limit=10, offset=10)
        searched = self.viewer().page(lane="v1", query="telescope", limit=10)
        a = self.viewer(self.character_a).page(lane="v2_claims")
        b = self.viewer(self.character_b).page(lane="v2_claims")

        self.assertEqual(10, len(first["items"]))
        self.assertTrue(first["has_more"])
        self.assertEqual(10, len(second["items"]))
        self.assertEqual(1, len(searched["items"]))
        self.assertIn("telescope", searched["items"][0]["content"])
        self.assertEqual(["The user's name is Alpha."], [row["content"] for row in a["items"]])
        self.assertEqual(["The user's name is Beta."], [row["content"] for row in b["items"]])

    def test_v1_edit_and_v2_correction_supersession_persist_across_reload(self):
        original = self.memory.add_memory(
            "preference", "The user likes tea.", importance=4, source="synthetic_test",
        )
        self.add_durable(
            self.character_a, "durable-name", "identity.name",
            "The user's name is River.", 1, 1_000,
        )
        viewer = self.viewer()
        viewer.mutate(
            action="edit_v1", record_id=f"v1:{original['id']}",
            content="The user likes coffee.", category="preference", importance=7,
        )
        correction = viewer.mutate(
            action="correct_v2_durable", record_id="durable-name",
            content="The user's name is Rowan.", command_id=str(uuid.uuid4()),
        )

        current = viewer.page(lane="v2_claims", status_filter="current")
        historical = viewer.page(lane="v2_claims", status_filter="historical")
        self.assertEqual(["The user's name is Rowan."], [row["content"] for row in current["items"]])
        self.assertEqual("superseded", historical["items"][0]["status"])
        self.assertEqual(correction["replacement_record_id"], historical["items"][0]["corrected_by"])

        self.store.close()
        self.store = MemoryV2Store(str(self.database_path))
        reloaded_memory = Memory(_Llm(), memory_file=str(self.memory_path), embedding_model=_Embedding())
        reloaded = MemoryViewer(reloaded_memory, self.store, self.character_a)
        self.assertEqual("The user likes coffee.", reloaded.page(lane="v1")["items"][0]["content"])
        self.assertEqual("The user's name is Rowan.", reloaded.page(lane="v2_claims")["items"][0]["content"])

        detail = reloaded.detail(
            lane="v2_claims", record_id=correction["replacement_record_id"],
        )["detail"]
        self.assertEqual("explicit_viewer_correction", detail["evidence"][0]["source_class"])
        self.assertEqual("memory_viewer_correction", detail["evidence"][0]["source_type"])
        self.assertEqual("memory_viewer", detail["evidence"][0]["source_origin"])
        self.assertEqual("supersedes", detail["relations"][0]["direction"])

        predecessor = reloaded.detail(lane="v2_claims", record_id="durable-name")["detail"]
        self.assertEqual("superseded_by", predecessor["relations"][0]["direction"])
        self.assertEqual("superseded", predecessor["status"])

    def test_provenance_is_bounded_and_unresolved_references_remain_inspectable(self):
        self.add_durable(
            self.character_a, "durable-audit", "bio.occupation",
            "The user has synthetic audit evidence.", 1, 1_000,
        )
        for index in range(1, 15):
            event_id = f"audit-{index}"
            content = f"Synthetic evidence {index}."
            self.store.add_event(
                self.character_a, event_id, index + 1,
                event_type="synthetic_observation", actor_kind="system",
                recorded_at_us=1_000 + index, content_text=content,
                source_origin="synthetic_test",
                source_reference=f"unavailable-source:{index}",
            )
            self.store.attach_evidence(
                self.character_a, "durable-audit", event_id,
                evidence_role="direct_user_statement",
                excerpt_start_cp=0, excerpt_end_cp=len(content),
            )

        first = self.viewer().detail(
            lane="v2_claims", record_id="durable-audit", limit=5,
        )
        second = self.viewer().detail(
            lane="v2_claims", record_id="durable-audit", limit=5, offset=5,
        )

        self.assertEqual(5, len(first["detail"]["evidence"]))
        self.assertTrue(first["has_more"])
        self.assertEqual(5, len(second["detail"]["evidence"]))
        synthetic = second["detail"]["evidence"][0]
        self.assertEqual("synthetic_or_system_derived", synthetic["source_class"])
        self.assertEqual("reference_retained_not_expanded", synthetic["source_status"])
        self.assertTrue(synthetic["source_reference"].startswith("unavailable-source:"))
        missing = self.viewer().detail(lane="v2_claims", record_id="missing-claim")
        self.assertEqual("degraded", missing["availability"])
        self.assertEqual({}, missing["detail"])
        with self.assertRaises(ValueError):
            self.viewer().detail(lane="v2_claims", record_id="durable-audit", limit=13)

    def test_status_filter_and_retirement_preserve_history(self):
        self.add_durable(
            self.character_a, "durable-job", "bio.occupation",
            "The user is a synthetic tester.", 1, 1_000,
        )
        self.viewer().mutate(
            action="retire_v2_durable", record_id="durable-job",
            command_id=str(uuid.uuid4()),
        )
        self.assertEqual([], self.viewer().page(lane="v2_claims", status_filter="current")["items"])
        historical = self.viewer().page(lane="v2_claims", status_filter="historical")
        self.assertEqual("archived", historical["items"][0]["status"])
        self.assertEqual("The user is a synthetic tester.", historical["items"][0]["content"])

    def test_open_threads_are_searchable_scoped_and_lifecycle_labeled(self):
        content = "I am waiting for the synthetic parcel."
        self.store.add_event(
            self.character_a, "thread-event", 1, event_type="canonical_user_message",
            actor_kind="user", recorded_at_us=1_000, content_text=content,
            source_origin="canonical_conversation", source_reference="conversation.json#0",
        )
        scope = self.repository.active_truth_scope(self.character_a)
        self.store.apply_open_thread_proposal(
            self.character_a,
            OpenThreadProposal((OpenThreadProposalOperation(
                "open", "parcel", 0, len(content), "waiting", "user",
                "Waiting for the synthetic parcel", "soon",
            ),)),
            evidence_event_id="thread-event", truth_scope_id=scope.truth_scope_id,
        )

        page = self.viewer().page(lane="open_threads", query="parcel")

        self.assertEqual(1, len(page["items"]))
        self.assertEqual("open", page["items"][0]["status"])
        self.assertEqual("Real world", page["items"][0]["scope"])
        self.assertIn("not a V1 fact", page["items"][0]["authority"])
        self.assertIn("conversation.json#0", page["items"][0]["source_reference"])

    def test_episode_valid_stale_missing_and_malformed_states_fail_open(self):
        messages = _episode_messages()
        cache = EpisodeCompactionCache(self.store, self.character_a)
        cache.rebuild(messages, EpisodeCompactor(_EpisodeProvider()))
        viewer = self.viewer(episode_cache=cache, messages=messages)

        page = viewer.page(lane="episodes")
        self.assertEqual("current_valid", page["diagnostic_state"])
        self.assertTrue(page["items"])
        self.assertTrue(all(row["status"] == "current_valid" for row in page["items"]))
        detail = viewer.detail(lane="episodes", record_id=page["items"][0]["record_id"])
        self.assertEqual("current_valid", detail["detail"]["diagnostic_state"])
        self.assertIn("shared episode-cache validator", detail["detail"]["diagnostic_reason"])

        original = self.store.connection.execute(
            """SELECT s.*, r.start_sequence, r.end_sequence FROM summaries s
                 JOIN summary_source_ranges r ON r.character_id=s.character_id
                  AND r.summary_id=s.summary_id
                WHERE s.character_id=? AND s.summary_id=?""",
            (self.character_a, page["items"][0]["record_id"]),
        ).fetchone()
        old_metadata = json.loads(original["legacy_metadata_json"])
        old_metadata["generation_id"] = "superseded-synthetic-generation"
        self.store.add_summary(
            self.character_a, "old-synthetic-episode", "Older diagnostic record.",
            source_count=original["source_count"],
            provenance_state="derived_rebuildable",
            generator_name=original["generator_name"],
            generator_version=original["generator_version"],
            legacy_metadata=old_metadata, created_at_us=1,
            summary_level=original["summary_level"],
        )
        self.store.add_summary_source_range(
            self.character_a, "old-synthetic-episode",
            original["start_sequence"], original["end_sequence"],
        )
        all_rows = viewer.page(lane="episodes", status_filter="all", scope_filter="all")
        old = next(row for row in all_rows["items"] if row["record_id"] == "old-synthetic-episode")
        self.assertEqual("superseded", old["status"])

        changed = list(messages)
        changed[0] = {**changed[0], "content": "Changed canonical synthetic source."}
        stale = self.viewer(episode_cache=cache, messages=changed).page(lane="episodes")
        self.assertTrue(any(row["status"] == "stale" for row in stale["items"]))

        summary_id = page["items"][0]["record_id"]
        self.store.connection.execute(
            "DELETE FROM summary_source_ranges WHERE character_id=? AND summary_id=?",
            (self.character_a, summary_id),
        )
        missing = viewer.detail(lane="episodes", record_id=summary_id)
        self.assertEqual("missing_source_coverage", missing["detail"]["diagnostic_state"])

        row = self.store.connection.execute(
            "SELECT legacy_metadata_json FROM summaries WHERE character_id=? AND summary_id=?",
            (self.character_a, summary_id),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata.pop("source_start_index")
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE character_id=? AND summary_id=?",
            (json.dumps(metadata), self.character_a, summary_id),
        )
        malformed = viewer.detail(lane="episodes", record_id=summary_id)
        self.assertEqual("ready", malformed["availability"])
        self.assertEqual("invalid", malformed["detail"]["diagnostic_state"])

        unavailable = self.viewer(store=False).page(lane="episodes")
        self.assertEqual("unavailable", unavailable["availability"])
        self.assertEqual([], unavailable["items"])
        self.assertIn("Memory V1 remains authoritative", unavailable["warning"])

    def test_shared_episode_validation_drives_runtime_and_viewer_rejection_classes(self):
        messages = _episode_messages()
        cache = EpisodeCompactionCache(self.store, self.character_a)

        def rebuild():
            cache.rebuild(messages, EpisodeCompactor(_EpisodeProvider()))
            row = self.store.connection.execute(
                """SELECT s.*, r.start_sequence, r.end_sequence FROM summaries s
                     JOIN summary_source_ranges r ON r.character_id=s.character_id
                      AND r.summary_id=s.summary_id
                    WHERE s.character_id=? AND s.summary_level='episode_compaction'
                    ORDER BY r.start_sequence LIMIT 1""",
                (self.character_a,),
            ).fetchone()
            return row

        def assert_shared(
            expected_state, source_messages, record_id, *, record_state=None,
        ):
            with patch.object(
                cache, "validate_for_context", wraps=cache.validate_for_context,
            ) as shared_validator:
                validation = cache.validate_for_context(source_messages)
                selection = cache.select_for_context(source_messages)
                page = self.viewer(episode_cache=cache, messages=source_messages).page(
                    lane="episodes", status_filter="all", scope_filter="all",
                )
            self.assertEqual(3, shared_validator.call_count)
            self.assertEqual("ready", page["availability"])
            self.assertEqual(validation.accepted, selection is not None)
            self.assertEqual(expected_state, validation.state)
            self.assertEqual(validation.state, page["diagnostic_state"])
            self.assertEqual(not validation.accepted, bool(validation.rejection_reasons))
            record = validation.record(record_id)
            self.assertIsNotNone(record)
            item = next(value for value in page["items"] if value["record_id"] == record_id)
            self.assertEqual(record_state or expected_state, record.state)
            self.assertEqual(record.state, item["status"])

        row = rebuild()
        assert_shared("current_valid", messages, str(row["summary_id"]))
        validation = cache.validate_for_context(messages)
        self.assertTrue(validation.generation_id)
        self.assertTrue(validation.generation_identity_digest)
        self.assertEqual(0, validation.lower_records[0].source_start_index)
        self.assertGreater(validation.lower_records[0].source_end_index_exclusive, 0)

        other_cache = EpisodeCompactionCache(self.store, self.character_b)
        self.assertFalse(other_cache.validate_for_context(messages).accepted)
        self.assertIsNone(other_cache.select_for_context(messages))
        isolated = self.viewer(
            self.character_b, episode_cache=other_cache, messages=messages,
        ).page(lane="episodes", status_filter="all", scope_filter="all")
        self.assertEqual([], isolated["items"])

        self.store.add_summary(
            self.character_a, "synthetic-malformed-orphan", "Malformed orphan.",
            source_count=0, provenance_state="derived_rebuildable",
            generator_name="synthetic", generator_version="1",
            legacy_metadata="{", created_at_us=1,
            summary_level="episode_compaction",
        )
        self.assertTrue(cache.validate_for_context(messages).accepted)
        self.assertIsNotNone(cache.select_for_context(messages))
        orphan_page = self.viewer(episode_cache=cache, messages=messages).page(
            lane="episodes", status_filter="all", scope_filter="all",
        )
        orphan = next(
            value for value in orphan_page["items"]
            if value["record_id"] == "synthetic-malformed-orphan"
        )
        self.assertEqual("invalid", orphan["status"])

        changed = list(messages)
        changed[0] = {**changed[0], "content": "Synthetic canonical source changed."}
        assert_shared("stale", changed, str(row["summary_id"]))

        row = rebuild()
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["compaction_version"] = -1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE character_id=? AND summary_id=?",
            (json.dumps(metadata), self.character_a, row["summary_id"]),
        )
        assert_shared("invalid", messages, str(row["summary_id"]))

        row = rebuild()
        self.store.connection.execute(
            "DELETE FROM summary_source_ranges WHERE character_id=? AND summary_id=?",
            (self.character_a, row["summary_id"]),
        )
        assert_shared("missing_source_coverage", messages, str(row["summary_id"]))

        row = rebuild()
        old_metadata = json.loads(row["legacy_metadata_json"])
        old_metadata["generation_id"] = "synthetic-superseded-generation"
        self.store.add_summary(
            self.character_a, "synthetic-superseded-record", "Superseded synthetic record.",
            source_count=row["source_count"], provenance_state="derived_rebuildable",
            generator_name=row["generator_name"], generator_version=row["generator_version"],
            legacy_metadata=old_metadata, created_at_us=1,
            summary_level="episode_compaction",
        )
        self.store.add_summary_source_range(
            self.character_a, "synthetic-superseded-record",
            row["start_sequence"], row["end_sequence"],
        )
        # Preserved older generations remain inspectable as superseded without
        # invalidating the current generation selected by the shared validator.
        assert_shared(
            "current_valid", messages, "synthetic-superseded-record",
            record_state="superseded",
        )

        row = rebuild()
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json='{' WHERE character_id=? AND summary_id=?",
            (self.character_a, row["summary_id"]),
        )
        assert_shared("invalid", messages, str(row["summary_id"]))

    def test_episode_query_pagination_is_bounded_and_character_scoped(self):
        cache = EpisodeCompactionCache(self.store, self.character_a)
        for index in range(9):
            self.store.add_summary(
                self.character_a, f"diagnostic-{index}",
                f"Bounded diagnostic topic {index}.", source_count=2,
                provenance_state="derived_rebuildable", generator_name="synthetic",
                generator_version="1", legacy_metadata={"malformed": True},
                created_at_us=index + 1, summary_level="episode_compaction",
            )
            self.store.add_summary_source_range(
                self.character_a, f"diagnostic-{index}", index * 2 + 1, index * 2 + 2,
            )
        self.store.add_summary(
            self.character_b, "other-character", "Bounded diagnostic topic leak.",
            source_count=2, provenance_state="derived_rebuildable",
            generator_name="synthetic", generator_version="1",
            legacy_metadata={"malformed": True}, created_at_us=20,
            summary_level="episode_compaction",
        )
        self.store.add_summary_source_range(self.character_b, "other-character", 1, 2)

        page = self.viewer(episode_cache=cache).page(
            lane="episodes", query="diagnostic topic", scope_filter="all", limit=3,
        )
        self.assertEqual(3, len(page["items"]))
        self.assertTrue(page["has_more"])
        self.assertFalse(any(row["record_id"] == "other-character" for row in page["items"]))
        second = self.viewer(episode_cache=cache).page(
            lane="episodes", query="diagnostic topic", scope_filter="all", limit=3, offset=3,
        )
        self.assertEqual(3, len(second["items"]))

    def test_viewer_is_read_only_when_unused_and_page_reads_do_not_change_context_data(self):
        self.memory.add_memory("fact", "A stable synthetic fact.", source="synthetic_test")
        before_v1 = self.memory_path.read_bytes()
        before_v2 = json.dumps(self.repository.export(character_id=self.character_a), sort_keys=True)
        prompt_memories_before = self.memory.get_relevant_memories("stable synthetic fact")

        self.viewer().page(lane="v1")
        self.viewer().page(lane="v2_claims")
        self.viewer().page(lane="open_threads")

        self.assertEqual(before_v1, self.memory_path.read_bytes())
        self.assertEqual(before_v2, json.dumps(self.repository.export(character_id=self.character_a), sort_keys=True))
        self.assertEqual(
            prompt_memories_before,
            self.memory.get_relevant_memories("stable synthetic fact"),
        )


if __name__ == "__main__":
    unittest.main()
