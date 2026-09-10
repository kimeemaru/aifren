import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
import uuid

from assistant_service import AssistantService
from conversation.conversation import Conversation
from development_flight_recorder import DevelopmentFlightRecorder
from memory_recall_shadow import RealTurnMemoryShadow
from memory_v2_hybrid_recall import (
    HistoricalRecallAnchor, HybridMemoryV2Recall, HybridRecallCandidate,
    HybridRecallResult, RecallEvidence,
)
from memory_v2_store import MemoryV2Store, SemanticRetrievalV2
from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth, RetrievalQuery
from memory_v2_store.durable_prompt import TypedDurableFact
from memory_v2_store.production_import import v1_import_scope
from scripts.inspect_memory_recall_shadow import build_report


class _Recall:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def retrieve(self, query):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class MemoryRecallShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "memory_v2.sqlite3"
        self.character = str(uuid.uuid4())
        store = MemoryV2Store(str(self.database))
        store.create_character(self.character, "Synthetic")
        store.close()
        self.recorder = DevelopmentFlightRecorder(sample_hz=1)

    def tearDown(self):
        self.recorder.stop()
        self.temp.cleanup()

    def _observer(self, recall, *, prompt_design_observer=None):
        return RealTurnMemoryShadow(
            self.database, self.character, recorder=self.recorder,
            recall_factory=lambda *_args: recall,
            prompt_design_observer=prompt_design_observer,
        )

    def test_disabled_recorder_schedules_no_retrieval(self):
        recall = _Recall(HybridRecallResult(()))
        observed_design_inputs = []
        observer = self._observer(
            recall,
            prompt_design_observer=lambda turn, query, result, scope: (
                observed_design_inputs.append((turn, query, result, scope))
            ),
        )
        accepted = observer.submit(
            turn_id=1, generation=1, canonical_user_index=0, query_text="synthetic query",
            messages=(), v1_selected=(), v1_latency_ms=1.0, accept=lambda _record: True,
        )
        self.assertFalse(accepted)
        self.assertEqual(0, recall.calls)
        self.assertEqual([], observed_design_inputs)
        observer.close()

    def test_structural_trace_is_bounded_and_contains_hybrid_diagnostics(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        mapping_store = MemoryV2Store(str(self.database))
        mapping_store.connection.execute(
            "INSERT INTO v1_import_records VALUES (?, ?, ?, ?, ?)",
            (v1_import_scope(self.character), 9, "claim-1", "0" * 64, 1),
        )
        mapping_store.close()
        candidate = HybridRecallCandidate(
            "claim-1", "semantic_v2", "private synthetic candidate text", 4.25,
            (("semantic", 3.0), ("entity", 1.25)), "scope-private", "active",
            (RecallEvidence(
                "canonical_user_message", "event-1", "/private/synthetic/conversation.json#2",
                sequence=2, recorded_at_us=123,
            ),),
            speaker_role="assistant", speech_act="other",
            source_class="ordinary_conversation", scope_state="unknown_scope",
            attribution_state="canonical_source_only",
            canonical_record_id="canonical-record-2", canonical_index=2,
            episode_id="episode-1", episode_source_start_index=0,
            episode_source_end_index_exclusive=4,
        )
        recall = _Recall(HybridRecallResult((candidate,), generated_counts=(("semantic_v2", 1),),
                                          health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),))))
        observed_design_inputs = []
        observer = self._observer(
            recall,
            prompt_design_observer=lambda turn, query, result, scope: (
                observed_design_inputs.append((turn, query, result, scope))
            ),
        )
        self.assertTrue(observer.submit(
            turn_id=4, generation=7, canonical_user_index=2, query_text="private synthetic query",
            messages=({"role": "user", "content": "private synthetic query"},),
            v1_selected=({"id": "9", "category": "fact", "rank": 1},),
            v1_latency_ms=0.5, accept=lambda _record: True,
        ))
        self.assertTrue(observer.drain())
        observer.close()
        record = list(self.recorder._memory_recall_shadow)[-1]
        serialized = json.dumps(record)
        self.assertEqual("semantic_v2", record["v2"][0]["lane"])
        self.assertEqual(4.25, record["v2"][0]["score"])
        self.assertEqual("canonical_user_message", record["v2"][0]["evidence"][0]["source_type"])
        self.assertEqual("assistant", record["v2"][0]["speaker_role"])
        self.assertEqual(2, record["v2"][0]["canonical_index"])
        self.assertEqual("episode-1", record["v2"][0]["episode_id"])
        self.assertEqual(["claim-1"], record["overlap_claim_ids"])
        self.assertNotIn("private synthetic query", serialized)
        self.assertNotIn("private synthetic candidate text", serialized)
        self.assertNotIn("/private/synthetic", serialized)
        self.assertEqual(1, len(observed_design_inputs))
        self.assertEqual("private synthetic candidate text", (
            observed_design_inputs[0][2].candidates[0].content
        ))

    def test_ephemeral_anchor_is_identity_only_one_turn_scoped_and_not_chained(self):
        store = MemoryV2Store(str(self.database))
        scope_id = store.default_truth_scope_id(self.character)
        store.close()
        candidate = HybridRecallCandidate(
            "historical-claim", "historical_evidence", "private source text",
            4.0, (), "", "historical_unknown_scope", (), "",
            speaker_role="user", speech_act="assertion",
            source_class="ordinary_conversation", scope_state="unknown_scope",
            canonical_record_id="canonical-source-4", canonical_index=4,
            episode_id="episode-1",
        )
        anchor = RealTurnMemoryShadow._anchor_from_result(
            HybridRecallResult((candidate,), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),))), self.character, scope_id, 7,
        )
        self.assertIsInstance(anchor, HistoricalRecallAnchor)
        self.assertEqual((4,), anchor.canonical_indices)
        self.assertNotIn("private source text", repr(anchor))

        observer = self._observer(_Recall(HybridRecallResult(())))
        observer._recall_anchor = anchor
        observer._recall_anchor_expires_at = float("inf")
        self.assertEqual(anchor, observer._anchor_for(8, scope_id))
        self.assertIsNone(observer._anchor_for(9, scope_id))
        observer._recall_anchor = anchor
        observer._recall_anchor_expires_at = float("inf")
        self.assertIsNone(observer._anchor_for(8, "different-scope"))

        chained = HybridRecallCandidate(
            "linked", "historical_recall_anchor_source", "private linked text",
            10.0, (), "", "historical_unknown_scope", (), "",
            speaker_role="assistant", source_class="ordinary_conversation",
            canonical_record_id="canonical-source-5", canonical_index=5,
        )
        self.assertIsNone(RealTurnMemoryShadow._anchor_from_result(
            HybridRecallResult((chained,)), self.character, scope_id, 8,
        ))
        observer.close()
        self.assertIsNone(observer._recall_anchor)

    def test_malformed_retrieval_fails_open_and_stale_acceptance_is_rejected(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        failing = self._observer(_Recall(error=ValueError("private malformed payload")))
        self.assertTrue(failing.submit(
            turn_id=1, generation=1, canonical_user_index=0, query_text="query",
            messages=(), v1_selected=(), v1_latency_ms=None, accept=lambda _record: True,
        ))
        self.assertTrue(failing.drain())
        failing.close()
        record = list(self.recorder._memory_recall_shadow)[-1]
        self.assertEqual("shadow_failure", record["v2_abstention_reason"])
        self.assertEqual("ValueError", record["error_kind"])

        stale = self._observer(_Recall(HybridRecallResult(())))
        self.assertTrue(stale.submit(
            turn_id=2, generation=1, canonical_user_index=1, query_text="query",
            messages=(), v1_selected=(), v1_latency_ms=None, accept=lambda _record: False,
        ))
        self.assertTrue(stale.drain())
        stale.close()
        self.assertEqual(1, len(self.recorder._memory_recall_shadow))

    def test_read_snapshot_precedes_same_turn_v2_observer_writes(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        release = threading.Event()
        observed_counts = []
        character = self.character

        class Recall:
            def __init__(self, store): self.store = store
            def retrieve(self, _query):
                release.wait(2)
                observed_counts.append(self.store.connection.execute(
                    "SELECT COUNT(*) FROM claims WHERE character_id=?", (character,),
                ).fetchone()[0])
                return HybridRecallResult(())

        observer = RealTurnMemoryShadow(
            self.database, self.character, recorder=self.recorder,
            recall_factory=lambda store, *_args: Recall(store),
        )
        self.assertTrue(observer.submit(
            turn_id=3, generation=3, canonical_user_index=0, query_text="current turn",
            messages=(), v1_selected=(), v1_latency_ms=0, accept=lambda _record: True,
        ))
        writer = MemoryV2Store(str(self.database))
        writer.add_claim(
            self.character, "same-turn-claim", claim_type="fact",
            assertion_scope="user_fact", content="same turn", provenance_state="complete",
        )
        writer.close()
        release.set()
        self.assertTrue(observer.drain())
        observer.close()
        self.assertEqual([0], observed_counts)

    def test_worker_busy_does_not_queue_overlapping_shadow_jobs(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        release = threading.Event()

        class SlowRecall:
            def retrieve(self, _query):
                release.wait(2)
                return HybridRecallResult(())

        observer = self._observer(SlowRecall())
        self.assertTrue(observer.submit(
            turn_id=1, generation=1, canonical_user_index=0,
            query_text="first synthetic query", messages=(), v1_selected=(),
            v1_latency_ms=None, accept=lambda _record: True,
        ))
        self.assertFalse(observer.submit(
            turn_id=2, generation=1, canonical_user_index=1,
            query_text="second synthetic query", messages=(), v1_selected=(),
            v1_latency_ms=None, accept=lambda _record: True,
        ))
        release.set()
        self.assertTrue(observer.drain())
        observer.close()
        skipped = [
            event for event in self.recorder._events
            if event.get("event") == "memory_recall_shadow_skipped"
        ]
        self.assertEqual(1, len(skipped))
        self.assertEqual("worker_busy", skipped[0]["reason"])

    def test_hybrid_scope_lookup_stays_read_only_after_same_turn_wal_commit(self):
        """Reproduce the former SQLITE_BUSY_SNAPSHOT production ordering."""
        reader = MemoryV2Store(str(self.database))
        reader.ensure_fts()
        reader.connection.execute("BEGIN")
        reader.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=?", (self.character,),
        ).fetchone()
        changes_before = reader.connection.total_changes

        writer = MemoryV2Store(str(self.database))
        writer.add_event(
            self.character, "same-turn-event", 1, event_type="synthetic_test",
            actor_kind="user", recorded_at_us=1,
            content_text="The user found a violet astrolabe.",
        )
        writer.add_claim(
            self.character, "same-turn-claim", claim_type="fact",
            assertion_scope="user_fact",
            content="The user found a violet astrolabe.", provenance_state="complete",
        )
        writer.attach_evidence(self.character, "same-turn-claim", "same-turn-event")
        writer.close()

        try:
            recall = HybridMemoryV2Recall(
                reader,
                self.character,
                semantic_retriever=SemanticRetrievalV2(reader, embedding_provider=None),
            )
            result = recall.retrieve(RetrievalQuery(
                self.character, "What violet astrolabe did I find?",
                "2026-08-30T00:00:00Z",
            ))
            self.assertEqual((), result.candidates)
            self.assertEqual(changes_before, reader.connection.total_changes)
            self.assertTrue(reader.connection.in_transaction)
        finally:
            reader.connection.rollback()
            reader.close()

    def test_sqlite_failure_trace_records_only_owned_stage_and_error_code(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})

        class BrokenRecall:
            def __init__(self, store):
                self.store = store

            def retrieve(self, _query):
                self.store.connection.execute("SELECT value FROM absent_synthetic_table").fetchone()

        observer = RealTurnMemoryShadow(
            self.database, self.character, recorder=self.recorder,
            recall_factory=lambda store, *_args: BrokenRecall(store),
        )
        self.assertTrue(observer.submit(
            turn_id=5, generation=5, canonical_user_index=0,
            query_text="synthetic query", messages=(), v1_selected=(),
            v1_latency_ms=None, accept=lambda _record: True,
        ))
        self.assertTrue(observer.drain())
        observer.close()
        record = list(self.recorder._memory_recall_shadow)[-1]
        self.assertEqual("shadow_failure", record["v2_abstention_reason"])
        self.assertEqual("claim_query", record["error_stage"])
        self.assertEqual("SQLITE_ERROR", record["sqlite_error_name"])
        self.assertEqual(sqlite3.SQLITE_ERROR, record["sqlite_error_code"])
        self.assertEqual("fail_open", record["error_disposition"])
        self.assertEqual("not_retried", record["retry_disposition"])

    def test_trace_ring_and_dump_are_bounded_and_private(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        base = {
            "version": 1, "turn_id": 1, "generation": 1, "canonical_user_index": 0,
            "character_key": "a" * 64, "query_sha256": "b" * 64,
            "v1_latency_ms": 1.0, "v2_latency_ms": 2.0, "v1": [], "v2": [],
            "v2_abstention_reason": "weak", "generated_counts": [],
            "overlap_claim_ids": [], "error_kind": "", "content": "must disappear",
        }
        for index in range(140):
            record = dict(base, turn_id=index)
            self.assertTrue(self.recorder.record_memory_recall_shadow(record))
        self.assertEqual(128, len(self.recorder._memory_recall_shadow))
        capture_id = "20260830T120000-a1b2c3"
        bundle = Path("/tmp") / f"aifren-flight-recorder-{capture_id}"
        shutil.rmtree(bundle, ignore_errors=True)
        try:
            self.recorder.dump(capture_id)
            payload = (bundle / "memory_recall_shadow.json").read_text(encoding="utf-8")
            self.assertNotIn("must disappear", payload)
            self.assertEqual(128, len(json.loads(payload)["records"]))
        finally:
            shutil.rmtree(bundle, ignore_errors=True)

    def test_v1_category_separator_is_safely_normalized_without_dropping_turn(self):
        self.recorder.start(unity_pid=999999, state_provider=lambda: {})
        record = {
            "version": 1, "turn_id": 3, "generation": 3,
            "canonical_user_index": 2, "character_key": "a" * 64,
            "query_sha256": "b" * 64, "v1_latency_ms": 1.0,
            "v2_latency_ms": 2.0,
            "v1": [{
                "memory_id": "184", "category": "question/inquiry", "rank": 1,
            }],
            "v2": [], "v2_abstention_reason": "weak",
            "generated_counts": [], "overlap_claim_ids": [], "error_kind": "",
        }

        self.assertTrue(self.recorder.record_memory_recall_shadow(record))
        stored = list(self.recorder._memory_recall_shadow)[-1]
        self.assertEqual("question_inquiry", stored["v1"][0]["category"])

    def test_v1_trace_uses_exact_post_dedup_prompt_selection(self):
        class Memory:
            def get_relevant_memories(self, *_args, **_kwargs):
                return [
                    {"id": 1, "category": "profile", "content": "My name is Elena."},
                    {"id": 2, "category": "episode", "content": "We found a paper telescope."},
                ]
        conversation = Conversation(
            object(), conversation_file=Path(self.temp.name) / "conversation.json",
            summary_file=Path(self.temp.name) / "summary.json",
        )
        conversation.add_user_message("What do you remember?")
        conversation._capture_v1_retrieval_diagnostics = True
        conversation.build_context(
            Memory(), "What do you remember?",
            admitted_durable_facts=(TypedDurableFact("identity.name", "Elena"),),
        )
        self.assertEqual(
            ({"id": "2", "category": "episode", "rank": 1},),
            conversation._last_v1_prompt_diagnostics,
        )


class _Conversation:
    def __init__(self):
        self.messages = []
        self._last_v1_prompt_diagnostics = ({"id": "1", "category": "fact", "rank": 1},)
        self._last_v1_retrieval_latency_ms = 1.0
    def add_user_message(self, value): self.messages.append({"role": "user", "content": value})
    def add_assistant_message(self, value): self.messages.append({"role": "assistant", "content": value})
    def save(self): pass
    def update_summary(self): pass


class _Memory:
    memories = []
    def process(self, *_args): pass
    def save(self): pass


class _Tts:
    def __init__(self, order=None): self.order = order
    def stop(self): pass
    def set_volume(self, *_args): pass
    def speak(self, *_args):
        if self.order is not None: self.order.append("tts")


class _Observer:
    def __init__(self, order=None): self.calls = []; self.order = order
    def submit(self, **kwargs):
        self.calls.append(kwargs)
        if self.order is not None: self.order.append("shadow")
        return True
    def close(self): pass


class MemoryRecallServiceIsolationTests(unittest.TestCase):
    def test_shadow_never_enters_response_input_and_stale_generation_is_rejected(self):
        observer = _Observer()
        seen = []
        service = AssistantService(
            object(), _Memory(), _Conversation(), object(), {}, "prompt", _Tts(),
            response_generator=lambda _llm, _conversation, _memory, query, _prompt: seen.append(query) or "production reply",
            memory_recall_shadow=observer,
            memory_authority="v1",
        )
        result = service.process_text_turn("ordinary query", speak=False)
        self.assertEqual("production reply", result.reply)
        self.assertEqual(["ordinary query"], seen)
        self.assertEqual(1, len(observer.calls))
        callback = observer.calls[0]["accept"]
        generation = observer.calls[0]["generation"]
        character_key = __import__("hashlib").sha256(str(service.character_id).encode()).hexdigest()
        self.assertTrue(callback({"generation": generation, "character_key": character_key}))
        with service._turn_state_lock:
            service._turn_generation += 1
        self.assertFalse(callback({"generation": generation, "character_key": character_key}))

    def test_new_shadow_submission_occurs_after_tts_dispatch(self):
        order = []
        observer = _Observer(order)
        service = AssistantService(
            object(), _Memory(), _Conversation(), object(), {}, "prompt", _Tts(order),
            response_generator=lambda *_args: "production reply",
            memory_recall_shadow=observer,
            memory_authority="v1",
        )
        self.assertTrue(service.process_text_turn("ordinary query", speak=True).succeeded)
        self.assertEqual(["tts", "shadow"], order)


class MemoryRecallShadowInspectionTests(unittest.TestCase):
    def test_explicit_inspector_resolves_only_selected_synthetic_character(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "characters/default").mkdir(parents=True)
            (root / "characters/default/character.json").write_text(
                json.dumps({"name": "Synthetic"}), encoding="utf-8",
            )
            (root / "conversation.json").write_text(json.dumps([
                {"role": "user", "content": "What was the synthetic detail?"},
            ]), encoding="utf-8")
            (root / "conversation_summary.json").write_text("{}", encoding="utf-8")
            (root / "memories.json").write_text(json.dumps([
                {"id": 1, "category": "fact", "content": "V1 synthetic detail"},
            ]), encoding="utf-8")
            from character_registry import CharacterRegistry
            character = CharacterRegistry(root).active()
            database = root / "memory_v2/memory_v2.sqlite3"
            database.parent.mkdir()
            store = MemoryV2Store(str(database))
            store.create_character(character.character_id, "Synthetic")
            store.add_claim(
                character.character_id, "claim-1", claim_type="fact",
                assertion_scope="user_fact", content="V2 synthetic detail",
                provenance_state="complete",
            )
            store.close()
            bundle = root / "bundle"
            bundle.mkdir()
            key = __import__("hashlib").sha256(character.character_id.encode()).hexdigest()
            (bundle / "memory_recall_shadow.json").write_text(json.dumps({
                "version": 1,
                "records": [{
                    "character_key": key, "canonical_user_index": 0, "turn_id": 1,
                    "v1": [{"memory_id": "1", "category": "fact", "rank": 1}],
                    "v2": [{"memory_id": "claim-1", "lane": "semantic_v2", "rank": 1}],
                    "overlap_claim_ids": [], "v2_abstention_reason": "",
                    "v1_latency_ms": 1.0, "v2_latency_ms": 2.0, "error_kind": "",
                }],
            }), encoding="utf-8")
            report = build_report(root, character.character_id, bundle)
            self.assertEqual("What was the synthetic detail?", report["turns"][0]["query"])
            self.assertEqual("V1 synthetic detail", report["turns"][0]["v1"][0]["summary"])
            self.assertEqual("V2 synthetic detail", report["turns"][0]["v2"][0]["summary"])

    def test_inspector_does_not_count_shadow_failure_as_abstention_or_v1_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "characters/default").mkdir(parents=True)
            (root / "characters/default/character.json").write_text(
                json.dumps({"name": "Synthetic"}), encoding="utf-8",
            )
            (root / "conversation.json").write_text(json.dumps([
                {"role": "user", "content": "Synthetic question"},
            ]), encoding="utf-8")
            (root / "conversation_summary.json").write_text("{}", encoding="utf-8")
            (root / "memories.json").write_text(json.dumps([
                {"id": 1, "category": "fact", "content": "Synthetic memory"},
            ]), encoding="utf-8")
            from character_registry import CharacterRegistry
            character = CharacterRegistry(root).active()
            database = root / "memory_v2/memory_v2.sqlite3"
            database.parent.mkdir()
            store = MemoryV2Store(str(database))
            store.create_character(character.character_id, "Synthetic")
            store.close()
            bundle = root / "bundle"
            bundle.mkdir()
            key = __import__("hashlib").sha256(character.character_id.encode()).hexdigest()
            (bundle / "memory_recall_shadow.json").write_text(json.dumps({
                "version": 1,
                "records": [{
                    "character_key": key, "canonical_user_index": 0, "turn_id": 1,
                    "v1": [{"memory_id": "1", "category": "fact", "rank": 1}],
                    "v2": [], "overlap_claim_ids": [],
                    "v2_abstention_reason": "shadow_failure",
                    "v2_latency_ms": 2.0, "error_kind": "OperationalError",
                    "error_stage": "scope_query", "sqlite_error_name": "SQLITE_BUSY_SNAPSHOT",
                    "sqlite_error_code": sqlite3.SQLITE_BUSY_SNAPSHOT,
                    "error_disposition": "fail_open", "retry_disposition": "not_retried",
                }],
            }), encoding="utf-8")
            report = build_report(root, character.character_id, bundle)
            self.assertEqual("shadow_failure", report["turns"][0]["classification"])
            self.assertEqual(1, report["classification_counts"]["shadow_failure"])
            self.assertEqual(0, report["classification_counts"]["v1_only"])
            self.assertEqual(0, report["classification_counts"]["both_abstained"])
            self.assertIsNone(report["average_v2_latency_ms"])
            self.assertEqual(2.0, report["average_shadow_execution_latency_ms"])


if __name__ == "__main__":
    unittest.main()
