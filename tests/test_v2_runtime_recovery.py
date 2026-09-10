"""Synthetic canonical crash gaps through real Development V2 service owners."""
import json
import os
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch

from assistant_service import AssistantService
from conversation.conversation import Conversation
from memory_v2_authority import DevelopmentV2MemoryAuthority
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository
from test_assistant_service_v2_authority import _LLM, _Memory, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_v2_embeddings import ToyEmbeddingProvider


class V2RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()
        self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.h.root)
        self.llm = _LLM("Understood.")
        self.service = self.open_service()
        self.addCleanup(lambda: self.service.close())

    def open_service(self):
        self.h.writer.compare = lambda *_a, **_k: {}
        self.h.writer._embedding_provider = ToyEmbeddingProvider()
        self.conversation = Conversation(self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root / "summary.json", memory_authority="v2")
        self.authority = DevelopmentV2MemoryAuthority(self.h.writer.store, self.h.character_id,
            self.conversation.messages, embedding_provider=ToyEmbeddingProvider())
        return AssistantService(self.llm, _Memory(), self.conversation, object(),
            {"_character_id": self.h.character_id}, "Synthetic companion.", _TTS(),
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            memory_authority="v2", memory_v2_authority=self.authority)

    def reopen(self):
        self.service.close()
        self.h.writer = MemoryV2ShadowWriter(self.h.root, character_id=self.h.character_id,
            display_name="Synthetic", memory_file=self.h.memory_file)
        self.h.repository = MemoryV2Repository(self.h.writer.store)
        self.service = self.open_service()

    def progress(self):
        return {row["consumer"]: dict(row) for row in self.h.writer.store.connection.execute(
            "SELECT * FROM canonical_observation_progress WHERE character_id=?", (self.h.character_id,))}

    def facts(self):
        return [row[0] for row in self.h.writer.store.connection.execute(
            "SELECT content FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
            (self.h.character_id,))]

    def test_failed_durable_observer_recovers_from_canonical_commit_on_reopen(self):
        with patch.object(self.h.writer, "observe_canonical_user_durable_facts",
                          side_effect=RuntimeError("PRIVATE synthetic failure")):
            result = self.service.process_text_turn("My favorite color is green.")
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(2, len(json.loads(self.h.conversation_file.read_text())))
        self.assertEqual([], self.facts())
        self.assertEqual("failed", self.progress()["durable"]["state"])
        self.assertEqual(2, self.progress()["identity"]["next_index"])
        calls = len(self.llm.calls)
        self.reopen()
        self.assertEqual(calls, len(self.llm.calls), "recovery must not generate")
        self.assertTrue(any("green" in value for value in self.facts()))
        counts = self.h.writer.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.reopen()
        self.assertEqual(counts, self.h.writer.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.llm.response = "Your favorite color is green."
        result = self.service.process_text_turn("What is my favorite color?")
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("green", result.reply)

    def test_failure_after_observer_mutation_rolls_back_only_that_consumer(self):
        original = self.h.writer.observe_canonical_user_durable_facts
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic crash after SQLite mutation")
        with patch.object(self.h.writer, "observe_canonical_user_durable_facts", side_effect=crash):
            self.assertTrue(self.service.process_text_turn("My favorite color is blue.").succeeded)
        self.assertEqual([], self.facts())
        self.reopen()
        self.assertTrue(any("blue" in value for value in self.facts()))

    def test_prefix_change_is_not_silently_replayed(self):
        self.assertTrue(self.service.process_text_turn("My favorite color is blue.").succeeded)
        self.conversation.messages[0]["content"] = "My favorite color is red."
        self.conversation.save()  # Explicit synthetic archive edit, no live data.
        self.reopen()
        outcomes = self.service._canonical_observation_recovery.last_status["consumers"]
        self.assertTrue(all(v["reason"] == "canonical_prefix_changed" for v in outcomes.values()))
        self.assertFalse(any("red" in value for value in self.facts()))

    def test_each_deterministic_consumer_has_independent_failed_progress(self):
        from memory_v2_runtime_observation import CONSUMERS
        for consumer, method in CONSUMERS.items():
            with self.subTest(consumer=consumer):
                with patch.object(self.h.writer, method, side_effect=RuntimeError("synthetic crash")):
                    # This ordinary assertion reaches all non-headwear consumers;
                    # a headwear assertion also exercises its guarded state path.
                    text = "She is wearing a red hat." if consumer == "headwear" else "My name is Mira."
                    self.assertTrue(self.service.process_text_turn(text).succeeded)
                before = self.progress()[consumer]
                self.assertEqual("failed", before["state"])
                self.reopen()
                after = self.progress()[consumer]
                self.assertGreater(after["next_index"], before["next_index"])
                self.assertEqual("complete", after["state"])

    def test_viewer_correction_survives_projection_rebuild_and_recovered_old_correction(self):
        self.assertTrue(self.service.process_text_turn("My favorite color is green.").succeeded)
        with patch.object(self.h.writer, "observe_canonical_user_durable_facts",
                          side_effect=RuntimeError("synthetic crash")):
            self.assertTrue(self.service.process_text_turn("Actually, my favorite color is blue.").succeeded)
        claim = self.h.writer.store.connection.execute(
            "SELECT claim_id FROM claims WHERE character_id=? AND subject_key='preference.color'",
            (self.h.character_id,)).fetchone()[0]
        correction = self.service.apply_memory_view_mutation(
            character_id=self.h.character_id, action="correct_v2_durable", record_id=claim,
            content="The user's favorite color is purple.", command_id=str(uuid.uuid4()))
        store = self.h.writer.store
        original_events = [tuple(row) for row in store.connection.execute(
            "SELECT * FROM events WHERE source_origin='memory_viewer'")]
        self.assertTrue(original_events)
        store.rebuild_fts()
        self.reopen()
        store = self.h.writer.store
        self.assertEqual(original_events, [tuple(row) for row in store.connection.execute(
            "SELECT * FROM events WHERE source_origin='memory_viewer'")])
        current = self.h.repository.lookup_durable_core(self.h.character_id, "preference.color")
        self.assertIn("purple", str(current))
        self.assertFalse(any("blue" in value for value in self.facts()))
        self.assertEqual("complete", self.progress()["durable"]["state"])

    def test_idle_recovery_is_bounded_and_rejects_pending_uncommitted_input(self):
        scope = self.service.truth_scope_provenance()
        for index in range(100):
            self.conversation.add_user_message(f"Synthetic ordinary record {index}.", truth_scope=scope)
            self.conversation.add_assistant_message("Understood.", truth_scope=scope)
        self.conversation.save()
        self.service.maintain_canonical_observers()
        outcomes = self.service._canonical_observation_recovery.last_status
        self.assertEqual(1, outcomes["canonical_reads"])
        self.assertTrue(all(v["processed"] <= 32 for v in outcomes["consumers"].values()))
        before = self.progress()["durable"]["next_index"]
        self.conversation.add_user_message("My favorite color is orange.", truth_scope=scope)
        self.service.maintain_canonical_observers()
        self.assertEqual(before + 32, self.progress()["durable"]["next_index"])
        self.assertFalse(any("orange" in value for value in self.facts()))
        self.assertEqual(201, len(self.conversation.messages))

    def test_recovery_never_replays_generated_or_inaccessible_records(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message("My favorite color is orange.", truth_scope=scope)
        self.conversation.messages[-1]["semantic_admission"] = {
            "channel": "hearing", "state": "unavailable", "understood": False}
        self.conversation.add_assistant_message("My favorite color is orange.", truth_scope=scope)
        self.conversation.add_user_message("My name is Invented.", truth_scope=scope)
        self.conversation.messages[-1]["origin"] = {"kind": "synthetic_generated"}
        self.conversation.add_assistant_message("Understood.", truth_scope=scope)
        self.conversation.save()
        self.reopen()
        self.assertEqual([], self.facts())
        self.assertEqual(0, self.h.writer.store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='canonical_user_message'").fetchone()[0])

    def test_missed_old_activity_is_disposed_without_overwriting_later_state(self):
        original = self.h.writer.observe_canonical_user_continuity
        def missed(message, **kwargs):
            if message["content"] == "I'm cooking dinner.":
                raise RuntimeError("synthetic missed observation")
            return original(message, **kwargs)
        with patch.object(self.h.writer, "observe_canonical_user_continuity",
                          side_effect=missed):
            self.assertTrue(self.service.process_text_turn("I'm cooking dinner.").succeeded)
            self.assertTrue(self.service.process_text_turn("I'm reading Dune.").succeeded)
        self.reopen()
        self.assertIn("reading Dune", str(self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity").state))
        self.assertEqual("complete", self.progress()["continuity"]["state"])
        disposition = self.h.writer.store.connection.execute(
            "SELECT reason FROM canonical_observation_dispositions WHERE character_id=?",
            (self.h.character_id,)).fetchone()
        self.assertEqual("superseded_actor_activity", disposition[0])

        # Disposed old activity remains canonical without replaying/erasing it or
        # freezing independently committed later state and personal learning.
        original_records = json.loads(self.h.conversation_file.read_text())
        self.assertTrue(self.service.process_text_turn("I'm painting a picture.").succeeded)
        self.assertTrue(self.service.process_text_turn("My favorite color is blue.").succeeded)
        self.reopen()
        self.assertEqual(original_records, json.loads(self.h.conversation_file.read_text())[:len(original_records)])
        self.assertIn("painting a picture", str(self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity").state))
        self.assertEqual("complete", self.progress()["durable"]["state"])
        page = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertNotEqual("recovery_unresolved", self.service._canonical_observation_recovery.inspection_status())
        self.llm.response = "Your favorite color is blue."
        self.assertIn("blue", self.service.process_text_turn("What is my favorite color?", speak=False).reply)


class RecoveryHostOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_host_poll_joins_cancelled_page_before_retiring_owner(self):
        import asyncio
        import threading
        from backend_host import AIFrenWebSocketHost
        from test_websocket_transport import FakeService
        started, release = threading.Event(), threading.Event()
        service = FakeService()
        def maintenance():
            started.set()
            if not release.wait(3):
                raise AssertionError("synthetic maintenance barrier timed out")
        service.maintain_canonical_observers = maintenance
        host = AIFrenWebSocketHost(service=service)
        async def tick(_seconds):
            return
        with patch("backend_host.asyncio.sleep", side_effect=tick):
            task = asyncio.create_task(host._proactive_loop())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertEqual(1, len(host._turn_tasks))
                task.cancel()
                # An event-loop barrier proves cancellation has been delivered;
                # the synchronous owned page is still blocked, not stopped.
                barrier = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(barrier.set_result, None)
                await barrier
                self.assertFalse(task.done())
                self.assertEqual(1, len(host._turn_tasks))
            finally:
                release.set()
                await asyncio.wait_for(task, 3)
            self.assertEqual(set(), host._turn_tasks)


if __name__ == "__main__":
    unittest.main()
