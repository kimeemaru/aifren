"""Production copy/cleanup contracts on a rich, wholly synthetic storage graph.

These rows exercise preservation, not conversational application/extraction. That
boundary is covered independently by the ordinary service regression suite.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from aifren.memory_v2_store import character_copy as copying
from aifren.memory_v2_store.character_copy import (
    CharacterCopyError, CharacterCopyCancelled, SelectedCharacterInventory,
    inspect_character_state, copy_character_state, cleanup_character_state,
)
from aifren.memory_v2_store.production_import import import_v1_memories
from aifren.memory_v2_store.store import MemoryV2Store


class SelectedCharacterCopyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "shared.sqlite3"
        self.target = self.root / "selected.sqlite3"
        self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4())
        with self.store(self.source) as store:
            self._rich_character(store, self.a, "A", checkpoint="running")
            self._rich_character(store, self.b, "B", checkpoint="complete")
            store.rebuild_fts()

    @staticmethod
    def store(path):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            value = MemoryV2Store(str(path))
            try:
                yield value
            finally:
                value.close()
        return opened()

    @staticmethod
    def connection(path):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            connection = sqlite3.connect(path)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
        return opened()

    def _insert(self, store, table, **values):
        columns = ",".join(values)
        store.connection.execute(
            f"INSERT INTO {table}({columns}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def _rich_character(self, store, cid, name, *, checkpoint):
        store.create_character(cid, f"Synthetic {name}", created_at_us=1,
                               metadata={"retained": name}, legacy_config_key=f"synthetic/{name}")
        scope = store.default_truth_scope_id(cid)
        original = f"My favorite color is green. Synthetic {name}."
        correction = f"My favorite color is blue now. Synthetic {name}."
        for number, content in enumerate((original, correction, "Let's imagine a mountain cabin.",
                                          "I am preparing a model railway."), start=1):
            store.add_event(cid, f"{name}-e{number}", number, content_text=content,
                            recorded_at_us=number * 100, source_origin="canonical_conversation",
                            source_reference=f"characters/{cid}/conversation.json#{number-1}")
        store.add_durable_claim(cid, f"{name}-old", subject_key="preference.color", content="green",
                                evidence_event_id=f"{name}-e1", evidence_role="direct_user_statement",
                                created_at_us=100, updated_at_us=100)
        # Viewer-origin correction is original state, not a QA admission exception.
        store.connection.execute(
            "UPDATE events SET event_type='memory_viewer_correction', source_origin='memory_viewer' "
            "WHERE character_id=? AND event_id=?", (cid, f"{name}-e2"))
        store.add_durable_claim(cid, f"{name}-new", subject_key="preference.color", content="blue",
                                evidence_event_id=f"{name}-e2", evidence_role="user_confirmation",
                                supersedes_claim_id=f"{name}-old", created_at_us=200, updated_at_us=200)
        store.set_active_state(cid, f"{name}-activity", subject_key="active.activity.current",
                               value="preparing a model railway", evidence_event_id=f"{name}-e4")
        scenario = store.create_scenario_truth_scope(cid, "mountain cabin", evidence_event_id=f"{name}-e3",
                                                     evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=30)
        store.activate_truth_scope(cid, scenario, evidence_event_id=f"{name}-e3",
                                   evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=30)
        historical = f"We discussed the synthetic {name} station."
        store.add_historical_evidence(
            cid, f"{name}-history", event_id=f"{name}-historical-event", canonical_index=4,
            canonical_record_id=f"{name}-canonical-record", speaker_role="assistant", speech_act="assertion",
            source_class="canonical_conversation", scope_state="real_world", truth_scope_id=scope,
            source_content_sha256=hashlib.sha256(historical.encode()).hexdigest(), searchable_text=historical,
            recorded_at_us=500, source_reference="conversation.json#4", retrieval_eligible=True)
        store.add_summary(cid, f"{name}-episode", "Derived synthetic episode", source_count=4,
                          provenance_state="complete", generator_name="synthetic", generator_version="1",
                          summary_level="episode", created_at_us=600)
        store.add_summary_source_range(cid, f"{name}-episode", 1, 4)
        self._insert(store, "claim_embeddings", character_id=cid, claim_id=f"{name}-history",
                     provider="synthetic", model="tiny", model_version="pinned-1", dimensions=1,
                     dtype="float32", normalized=1, preprocessing_fingerprint="identity",
                     content_fingerprint="selected-content", source_content_sha256="7"*64,
                     vector_blob=b"\x00\x00\x80?", generated_at_us=600, state="current")
        self._insert(store, "active_scene_subjects", character_id=cid, scene_subject_id=f"{name}-object",
                     introduced_at_us=300, introduced_event_id=f"{name}-e3", truth_scope_id=scenario,
                     last_referenced_at_us=400, identity_strength="distinct")
        self._insert(store, "active_scene_relations", character_id=cid, relation_id=f"{name}-relation",
                     truth_scope_id=scenario, target_kind="actor", target_actor="companion", facet="hands",
                     side="left", predicate="covered_by", cause_kind="scene", cause="green glove",
                     cause_subject_id=f"{name}-object", valid_from_us=400, locus="left hand")
        self._insert(store, "active_scene_relation_events", character_id=cid, relation_id=f"{name}-relation",
                     operation="set", event_id=f"{name}-e3", created_at_us=400)
        self._insert(store, "active_scene_subject_lifecycle_events", character_id=cid,
                     scene_subject_id=f"{name}-object", operation="introduced", event_id=f"{name}-e3",
                     created_at_us=300)
        # Storage-level thread graph includes both current and closed history.
        for label, status in (("open", "open"), ("closed", "resolved")):
            thread = f"{name}-{label}-thread"
            store.add_claim(cid, thread, claim_type="conversation_event", assertion_scope="shared_event",
                            content=f"Synthetic {label} plan", created_at_us=400)
            store.attach_evidence(cid, thread, f"{name}-e4", evidence_role="direct_user_statement")
            self._insert(store, "open_threads", character_id=cid, thread_id=thread, thread_kind="plan",
                         participant_scope="shared", description=f"Synthetic {label} plan", status=status,
                         opened_at_us=400, last_mentioned_at_us=500, closed_at_us=600 if status != "open" else None,
                         opened_event_id=f"{name}-e4", last_event_id=f"{name}-e4",
                         closed_event_id=f"{name}-e4" if status != "open" else None, truth_scope_id=scenario)
        self._insert(store, "proactive_checkins", character_id=cid, checkin_id=f"{name}-checkin",
                     reason_kind="open_thread", thread_id=f"{name}-open-thread", displayed_at_us=700,
                     conversation_index=6, assistant_content_sha256="1"*64)
        self._insert(store, "proactive_attempts", character_id=cid, attempt_id=f"{name}-attempt",
                     reason_kind="open_thread", thread_id=f"{name}-open-thread", attempted_at_us=800,
                     outcome="cancelled")
        for index, state in enumerate(("pending", "complete", "unresolved", "failed")):
            self._insert(store, "canonical_observation_progress", character_id=cid,
                         source_key="logical-canonical-key", consumer=f"consumer-{index}", policy_version="1",
                         next_index=index, prefix_digest="a"*64, state=state,
                         reason="synthetic retained disposition", updated_at_us=900+index)
        self._insert(store, "canonical_observation_dispositions", character_id=cid,
                     source_key="logical-canonical-key", consumer="consumer-2", policy_version="1",
                     source_index=2, source_digest="b"*64, truth_scope_id=scope,
                     reason="non_mutating_question", witness_event_id=f"{name}-e2", decided_at_us=1000)
        self._insert(store, "historical_evidence_checkpoints", character_id=cid, policy_version="1",
                     source_key="conversation.json", next_index=5, source_prefix_digest="c"*64,
                     source_archive_digest="d"*64, source_record_count=8, state=checkpoint, updated_at_us=1000)
        self._insert(store, "retrieval_telemetry", character_id=cid, recorded_at_us=1100,
                     query_sha256="e"*64, v1_ids_json="[]", v2_ids_json='["synthetic"]', overlap_count=0,
                     v1_abstained=1, v2_abstained=0, v2_latency_ms=2.5, retrieval_strategy="hybrid")
        legacy = self.root / f"legacy-{name}"
        legacy.mkdir()
        (legacy / "memories.json").write_text(json.dumps([
            {"id": 1, "content": f"Synthetic imported {name}", "category": "stable_user_fact"},
        ]))
        imported = import_v1_memories(store, legacy, character_id=cid, display_name=f"Synthetic {name}")
        self.assertEqual(1, imported.imported, imported.errors)

    def inventory(self, path=None, cid=None):
        return inspect_character_state(path or self.source, cid or self.a, batch_rows=2)

    def fts_rows(self, cid):
        with self.connection(self.source) as connection:
            return tuple((table, tuple(connection.execute(
                f"SELECT character_id,claim_id,searchable_text FROM {table} WHERE character_id=? ORDER BY claim_id",
                (cid,)))) for table in ("claims_fts", "historical_evidence_fts"))

    def test_copy_preserves_every_selected_table_original_state_and_foreign_source(self):
        before_a, before_b = self.inventory(), self.inventory(cid=self.b)
        self.assertEqual(24, len(before_a.counts))
        self.assertTrue(all(count > 0 for _, count in before_a.counts), before_a.counts)
        source_bytes = self.source.read_bytes()
        report = copy_character_state(self.source, self.target, self.a, batch_rows=1)
        self.assertEqual(str(self.target), json.loads(json.dumps(report.to_dict()))["target_database"])
        self.assertEqual(before_a, report.inventory)
        self.assertEqual(before_a, self.inventory(self.target))
        self.assertEqual(before_a, self.inventory())
        self.assertEqual(before_b, self.inventory(cid=self.b))
        self.assertEqual(source_bytes, self.source.read_bytes())
        with self.store(self.target) as target:
            self.assertTrue(target.fts_is_current())
            self.assertGreater(report.fts_row_count, 0)
            self.assertEqual([self.a], [row[0] for row in target.connection.execute("SELECT character_id FROM characters")])
            self.assertEqual([], target.connection.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual("running", target.connection.execute("SELECT state FROM historical_evidence_checkpoints").fetchone()[0])
            self.assertEqual({"pending", "complete", "unresolved", "failed"},
                             {row[0] for row in target.connection.execute("SELECT state FROM canonical_observation_progress")})
            old = target.connection.execute("SELECT valid_to_us FROM claims WHERE claim_id='A-old'").fetchone()[0]
            self.assertIsNotNone(old)
            self.assertEqual("blue", target.connection.execute("SELECT content FROM claims WHERE claim_id='A-new'").fetchone()[0])
        self.assertFalse(any(path.name.endswith(".json") for path in self.root.iterdir()))

    def test_complete_checkpoint_also_preserved_without_qa_gate(self):
        with self.store(self.source) as store:
            store.connection.execute("UPDATE historical_evidence_checkpoints SET state='complete' WHERE character_id=?", (self.a,))
        report = copy_character_state(self.source, self.target, self.a)
        self.assertEqual(self.inventory(), report.inventory)
        self.assertEqual(self.inventory(self.target), report.inventory)

    def test_cleanup_is_selected_transactional_idempotent_and_does_not_rebuild_foreign_fts(self):
        before_b, foreign_fts = self.inventory(cid=self.b), self.fts_rows(self.b)
        report = copy_character_state(self.source, self.target, self.a)
        result = cleanup_character_state(self.source, self.a, expected_inventory=report.inventory)
        self.assertFalse(result.already_absent)
        self.assertTrue(result.global_fts_digest_invalidated)
        self.assertEqual(report.inventory.counts, result.removed_counts)
        self.assertEqual(self.a, json.loads(json.dumps(result.to_dict()))["character_id"])
        self.assertEqual(0, self.inventory().total_rows)
        self.assertEqual(before_b, self.inventory(cid=self.b))
        self.assertEqual(foreign_fts, self.fts_rows(self.b))
        self.assertEqual(report.inventory, self.inventory(self.target))
        with self.connection(self.source) as connection:
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
            self.assertIsNone(connection.execute("SELECT value FROM database_meta WHERE key='fts_claims_digest'").fetchone())
        repeated = cleanup_character_state(self.source, self.a, expected_inventory=report.inventory)
        self.assertTrue(repeated.already_absent)
        self.assertFalse(repeated.global_fts_digest_invalidated)

    def test_newer_selected_state_refuses_cleanup_even_with_unchanged_row_counts(self):
        before = self.inventory()
        with self.store(self.source) as store:
            store.connection.execute("UPDATE open_threads SET description='New original status' WHERE character_id=? AND status='open'", (self.a,))
        newer, foreign = self.inventory(), self.inventory(cid=self.b)
        self.assertEqual(before.counts, newer.counts)
        with self.assertRaisesRegex(CharacterCopyError, "changed since"):
            cleanup_character_state(self.source, self.a, expected_inventory=before)
        self.assertEqual(newer, self.inventory())
        self.assertEqual(foreign, self.inventory(cid=self.b))

    def test_empty_selection_is_inventory_only_not_an_invented_character(self):
        absent = str(uuid.uuid4())
        result = self.inventory(cid=absent)
        self.assertFalse(result.character_exists)
        self.assertEqual(0, result.total_rows)
        with self.assertRaisesRegex(CharacterCopyError, "absent"):
            copy_character_state(self.source, self.target, absent)
        self.assertFalse(self.target.exists())

    def test_wrong_local_identity_and_orphan_timeline_fail_closed(self):
        for values in ({"storage_character_id": self.b, "timeline_generation": "1"},
                       {"timeline_generation": "1"}):
            with self.subTest(values=values):
                with self.store(self.source) as store:
                    store.connection.executemany("INSERT OR REPLACE INTO database_meta VALUES (?,?)", values.items())
                for action in (lambda: self.inventory(), lambda: copy_character_state(self.source, self.target, self.a)):
                    with self.assertRaises(CharacterCopyError):
                        action()
                with self.store(self.source) as store:
                    store.connection.execute("DELETE FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')")
        with self.store(self.source) as store:
            store.connection.executemany("INSERT OR REPLACE INTO database_meta VALUES (?,?)",
                                         (("storage_character_id", self.a), ("timeline_generation", "3")))
        report = copy_character_state(self.source, self.target, self.a)
        with self.connection(self.target) as connection:
            self.assertEqual([], connection.execute("SELECT key FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')").fetchall())
        self.assertEqual(self.a, report.character_id)

    def test_unknown_table_column_and_trigger_refuse_before_copy_or_cleanup(self):
        inventory = self.inventory()
        variants = (
            ("CREATE TABLE unreviewed(value TEXT)", "DROP TABLE unreviewed"),
            ("ALTER TABLE characters ADD COLUMN unreviewed TEXT", "ALTER TABLE characters DROP COLUMN unreviewed"),
            ("CREATE TRIGGER foreign_delete AFTER DELETE ON characters BEGIN DELETE FROM events; END", "DROP TRIGGER foreign_delete"),
        )
        for create, remove in variants:
            with self.subTest(schema=create):
                with self.connection(self.source) as connection:
                    connection.execute(create)
                with self.assertRaises(CharacterCopyError):
                    copy_character_state(self.source, self.target, self.a)
                with self.assertRaises(CharacterCopyError):
                    cleanup_character_state(self.source, self.a, expected_inventory=inventory)
                self.assertFalse(self.target.exists())
                with self.connection(self.source) as connection:
                    connection.execute(remove)
        self.assertEqual(inventory, self.inventory())

    def test_copy_interruption_discards_only_new_destination(self):
        before_a, before_b = self.inventory(), self.inventory(cid=self.b)
        original, completed = copying._copy_table, []

        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            completed.append(args[2].name)
            if len(completed) == 4:
                raise OSError("synthetic interrupted copy")
            return result

        with patch.object(copying, "_copy_table", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                copy_character_state(self.source, self.target, self.a)
        self.assertEqual(4, len(completed))
        self.assertFalse(any(Path(f"{self.target}{suffix}").exists() for suffix in ("", "-wal", "-shm", "-journal")))
        self.assertEqual(before_a, self.inventory())
        self.assertEqual(before_b, self.inventory(cid=self.b))
        report = copy_character_state(self.source, self.target, self.a)
        self.assertEqual(before_a, report.inventory)

    def test_cancelled_copy_and_cleanup_are_bounded_and_atomic(self):
        original = self.inventory()
        calls = 0

        def cancelled():
            nonlocal calls
            if self.target.exists():
                calls += 1
            return calls >= 6

        with self.assertRaises(CharacterCopyCancelled):
            copy_character_state(self.source, self.target, self.a, batch_rows=1, cancelled=cancelled)
        self.assertEqual(6, calls)
        self.assertFalse(self.target.exists())
        deleted = []
        connect = sqlite3.connect

        def observed(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            if str(database) == self.source.as_uri() + "?mode=rw":
                connection.set_trace_callback(lambda query: deleted.append(query)
                    if query.startswith('DELETE FROM "open_threads"') else None)
            return connection

        with patch.object(copying.sqlite3, "connect", side_effect=observed):
            with self.assertRaises(CharacterCopyCancelled):
                cleanup_character_state(self.source, self.a, expected_inventory=original,
                                        batch_rows=1, cancelled=lambda: bool(deleted))
        self.assertTrue(deleted, "Cancellation must occur after real transactional deletes")
        self.assertEqual(original, self.inventory())

    def test_copy_detects_changed_source_and_target_digest_corruption(self):
        before = self.inventory()
        original = copying._copy_table

        def tampered(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[2].name == "claims":
                args[1].execute("UPDATE claims SET content='corrupted target' WHERE character_id=? AND claim_id='A-new'", (self.a,))
            return result

        with patch.object(copying, "_copy_table", side_effect=tampered):
            with self.assertRaisesRegex(CharacterCopyError, "semantic"):
                copy_character_state(self.source, self.target, self.a)
        self.assertFalse(self.target.exists())
        self.assertEqual(before, self.inventory())

        def source_changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[2].name == "v1_import_records":
                with self.connection(self.source) as connection:
                    connection.execute("UPDATE open_threads SET description='Concurrent original change' WHERE character_id=? AND status='open'", (self.a,))
            return result

        with patch.object(copying, "_copy_table", side_effect=source_changed):
            with self.assertRaisesRegex(CharacterCopyError, "source changed"):
                copy_character_state(self.source, self.target, self.a)
        self.assertFalse(self.target.exists())
        self.assertNotEqual(before, self.inventory(), "A caller's newer source state must not be rolled back")

    def test_other_character_can_commit_during_selected_copy(self):
        before, foreign_before = self.inventory(), self.inventory(cid=self.b)
        original = copying._copy_table

        def foreign_write(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[2].name == "claims":
                with self.connection(self.source) as connection:
                    connection.execute("UPDATE open_threads SET description='B concurrently updated status' "
                                       "WHERE character_id=? AND status='open'", (self.b,))
            return result

        with patch.object(copying, "_copy_table", side_effect=foreign_write):
            report = copy_character_state(self.source, self.target, self.a)
        self.assertEqual(before, report.inventory)
        self.assertEqual(before, self.inventory(self.target))
        self.assertEqual(before, self.inventory())
        self.assertNotEqual(foreign_before, self.inventory(cid=self.b))

    def test_source_path_replacement_refuses_even_identical_selected_state(self):
        original = copying._copy_table
        replacement = self.root / "replacement.sqlite3"
        replacement.write_bytes(self.source.read_bytes())

        def replaced(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[2].name == "v1_import_records":
                replacement.replace(self.source)
            return result

        with patch.object(copying, "_copy_table", side_effect=replaced):
            with self.assertRaisesRegex(CharacterCopyError, "replaced"):
                copy_character_state(self.source, self.target, self.a)
        self.assertFalse(self.target.exists())

    def test_missing_selected_ownership_reference_refuses_copy_and_cleanup(self):
        expected = self.inventory()
        with self.connection(self.source) as connection:
            connection.execute("UPDATE canonical_observation_dispositions SET witness_event_id='missing' WHERE character_id=?", (self.a,))
        with self.assertRaisesRegex(CharacterCopyError, "ownership reference"):
            copy_character_state(self.source, self.target, self.a)
        with self.assertRaises(CharacterCopyError):
            cleanup_character_state(self.source, self.a, expected_inventory=expected)
        self.assertFalse(self.target.exists())

    def test_live_committed_wal_is_read_without_losing_rows(self):
        # Caller retains writer ownership but keeps this connection quiescent.
        with self.store(self.source) as store:
            store.connection.execute("UPDATE open_threads SET description='Committed WAL status' WHERE character_id=? AND status='open'", (self.a,))
            self.assertGreater(Path(f"{self.source}-wal").stat().st_size, 0)
            expected = self.inventory()
            report = copy_character_state(self.source, self.target, self.a)
            self.assertEqual(expected, report.inventory)
            self.assertEqual(expected, self.inventory(self.target))

    def test_work_ceiling_is_explicit_and_never_publishes_a_partial_copy(self):
        with patch.object(copying, "MAX_SELECTED_ROWS", 3):
            with self.assertRaisesRegex(CharacterCopyError, "work ceiling"):
                copy_character_state(self.source, self.target, self.a)
        self.assertFalse(self.target.exists())
        with self.assertRaises(CharacterCopyError):
            copy_character_state(self.source, self.target, self.a, batch_rows=0)

    def test_destination_reservation_identity_and_inventory_serialization(self):
        inventory = self.inventory()
        self.assertEqual(inventory, SelectedCharacterInventory.from_dict(json.loads(json.dumps(inventory.to_dict()))))
        with self.assertRaises(CharacterCopyError):
            cleanup_character_state(self.source, self.b, expected_inventory=inventory)
        self.target.write_text("existing unrelated file")
        with self.assertRaises(CharacterCopyError):
            copy_character_state(self.source, self.target, self.a)
        self.assertEqual("existing unrelated file", self.target.read_text())
        with self.assertRaises(CharacterCopyError):
            copy_character_state(self.source, self.source, self.a)
        linked = self.root / "symlink.sqlite3"
        linked.symlink_to(self.source)
        with self.assertRaises(CharacterCopyError):
            copy_character_state(linked, self.root / "unused.sqlite3", self.a)
        with self.assertRaises(CharacterCopyError):
            self.inventory(cid="not-a-character")

    def test_selected_payload_queries_never_read_the_other_character(self):
        traces = []
        original = sqlite3.connect

        def traced(database, *args, **kwargs):
            result = original(database, *args, **kwargs)
            if str(database).startswith(self.source.as_uri() + "?mode=ro"):
                result.set_trace_callback(traces.append)
            return result

        with patch.object(copying.sqlite3, "connect", side_effect=traced):
            copy_character_state(self.source, self.target, self.a)
        owned_tables = {spec.name for spec in copying._ALL_SPECS}
        selected = [query for query in traces if query.lstrip().upper().startswith("SELECT")
                    and any(f'FROM "{table}"' in query for table in owned_tables)]
        self.assertTrue(selected)
        self.assertTrue(all(" WHERE " in query and self.a in query for query in selected))
        self.assertFalse(any(self.b in query for query in traces))


if __name__ == "__main__":
    unittest.main()
