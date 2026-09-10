"""Retrieval execution failures through real Development authority/service turns.

All archives, SQLite rows, vectors and providers are synthetic and temporary.
The concept vectors exercise routing/plumbing, not embedding quality.
"""

import hashlib
import asyncio
import json
import os
from pathlib import Path
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from assistant_service import AssistantService
from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth, RetrievalQuery
from conversation.conversation import Conversation
from memory_v2_authority import DevelopmentV2MemoryAuthority
from memory_v2_episode_compaction import canonical_record_id
from memory_v2_store import EmbeddingLifecycle
from test_assistant_service_v2_authority import _LLM, _Memory, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_v2_historical_evidence import _ConceptEmbedding
from test_memory_v2_embeddings import ToyEmbeddingProvider
from test_cancelled_response_repair import FocusedPtt


class ControlledEmbedding(_ConceptEmbedding):
    failed = False

    def embed(self, texts):
        if self.failed:
            raise RuntimeError("synthetic private query /private/test-path token=synthetic-secret")
        return super().embed(texts)


class MemoryRetrievalHealthTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]
        self.h = _Harness()
        self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.h.root)
        self.h.writer.compare = lambda *_a, **_k: {}
        self.h.writer._embedding_provider = ToyEmbeddingProvider()
        self.llm, self.memory, self.tts = _LLM("Hello."), _Memory(), _TTS()
        self.conversation = Conversation(
            self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root / "summary.json", memory_authority="v2",
        )
        self.embedding = ControlledEmbedding()
        self.authority = DevelopmentV2MemoryAuthority(
            self.h.writer.store, self.h.character_id, self.conversation.messages,
            embedding_provider=self.embedding,
        )
        self.service = AssistantService(
            self.llm, self.memory, self.conversation, object(),
            {"_character_id": self.h.character_id}, "Synthetic character.", self.tts,
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            memory_authority="v2", memory_v2_authority=self.authority,
            ptt_factory=FocusedPtt,
        )
        self.addCleanup(self.service.close)
        self.events, self.turns = [], []
        self.service.subscribe(self.events.append)
        self.recorder = Mock(enabled=False)
        recorder = patch("assistant_service.development_flight_recorder", return_value=self.recorder)
        recorder.start()
        self.addCleanup(recorder.stop)
        original = self.authority.prepare

        def prepare(*args, **kwargs):
            turn = original(*args, **kwargs)
            self.turns.append(turn)
            return turn

        self.authority.prepare = prepare
        self.outcomes = []
        retrieve = self.authority.recall.semantic.retrieve

        def semantic(*args, **kwargs):
            outcome = retrieve(*args, **kwargs)
            self.outcomes.append((outcome, kwargs.get("memory_query_decision")))
            return outcome

        self.authority.recall.semantic.retrieve = semantic

    def terminal(self, outcome):
        turn_id = [e.data["turn_id"] for e in self.events if e.type == "turn_started"][-1]
        rows = [c.kwargs for c in self.recorder.mark.call_args_list
                if c.args == ("turn_terminal_outcome",) and c.kwargs.get("turn_id") == turn_id]
        self.assertEqual([outcome], [row["outcome"] for row in rows])
        self.assertFalse(self.service._turn_lock.locked())
        self.assertIsNone(self.service._active_turn_cancel)
        return turn_id

    def unavailable(self, query):
        self.events.clear()
        calls, audio = len(self.llm.calls), len(self.tts.spoken)
        old = list(self.conversation.messages)
        with patch.object(self.service, "_run_memory_v2_shadow") as observer:
            result = self.service.process_text_turn(query, speak=True)
        self.assertFalse(result.succeeded)
        self.assertEqual("I can't check that memory right now. Please try again in a moment.", result.error)
        self.assertEqual(calls, len(self.llm.calls))
        self.assertEqual(audio, len(self.tts.spoken))
        # Pure recall has no pre-generation state mutation requiring a user
        # commit. Preserve the established pending-user rollback contract.
        self.assertEqual(old, self.conversation.messages)
        stored = json.loads(self.h.conversation_file.read_bytes()) if self.h.conversation_file.exists() else []
        self.assertEqual(old, stored)
        observer.assert_not_called()
        self.assertFalse(any(e.type in {"assistant_response", "assistant_delta", "presentation", "playback_started", "turn_cancelled"} for e in self.events))
        turn_id = self.terminal("error")
        errors = [e.data for e in self.events if e.type == "error"]
        self.assertEqual(1, len(errors))
        self.assertEqual("memory_lookup_unavailable", errors[0]["code"])
        self.assertTrue(errors[0]["recoverable"])
        self.assertEqual(turn_id, errors[0]["turn_id"])
        diagnostics = self.service._last_memory_authority_diagnostics
        self.assertTrue(diagnostics["provider_bypassed"])
        self.assertEqual("lookup_unavailable", diagnostics["absence_kind"])
        self.assertEqual(0, self.memory.retrieval_calls)
        self.assertEqual([], self.memory.processed)
        return result

    def source(self, text, *, speaker="user", speech_act="assertion"):
        index = len(self.conversation.messages)
        add = self.conversation.add_user_message if speaker == "user" else self.conversation.add_assistant_message
        add(text)
        self.conversation.save()
        # Healthy synthetic fixtures now use the same source identity/time/
        # projection contract as staged and append-aware production indexing.
        from memory_v2_historical_evidence import resolve_historical_evidence, persist_historical_occurrence
        evidence = resolve_historical_evidence(self.conversation.messages, index, valid_scope_ids=set()).evidence
        self.assertIsNotNone(evidence)
        if speaker == "user":
            self.assertEqual(speech_act, evidence.speech_act)
        claim_id, _ = persist_historical_occurrence(self.h.writer.store, self.h.character_id, evidence)
        self.service.maintain_canonical_observers()
        return claim_id

    def test_semantic_only_source_failure_is_unavailable_and_recovers(self):
        self.source("I spent the afternoon refactoring my Python project.")
        self.llm.response = 'I remember you saying, “I spent the afternoon refactoring my Python project.”'
        query = "Do you recall software coding?"
        healthy = self.service.process_text_turn(query, speak=True)
        self.assertTrue(healthy.succeeded, healthy.error)
        self.assertIn("Python", healthy.reply)
        selected = [t for t in self.outcomes[-1][0].traces if t.selection_state == "selected"]
        self.assertEqual([("semantic",)], [t.candidate_channels for t in selected])
        self.assertIs(self.outcomes[-1][1], self.turns[-1].memory_query_decision)
        self.embedding.failed = True
        self.unavailable(query)
        self.assertFalse(self.turns[-1].authoritative_no_evidence)
        self.assertNotEqual("nothing_relevant", self.turns[-1].absence_kind)
        self.embedding.failed = False
        recovered = self.service.process_text_turn(query, speak=True)
        self.assertTrue(recovered.succeeded, recovered.error)
        self.assertIn("Python", recovered.reply)
        self.terminal("published")

    def test_episode_exception_is_unavailable_not_absence(self):
        with patch.object(self.authority.recall.episode_cache, "retrieve_candidates", side_effect=RuntimeError("synthetic failure")):
            self.unavailable("Do you remember my telescope?")
        self.assertFalse(self.turns[-1].authoritative_no_evidence)
        self.assertEqual([], self.llm.calls)

    def test_healthy_insufficient_lookup_and_absent_optional_cache_still_answer_absence(self):
        self.source("Almost like my favorite color.")
        result = self.service.process_text_turn("Do you remember my favorite color?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)
        self.assertEqual([], self.llm.calls)
        self.assertIn("favorite", result.reply)
        health = self.turns[-1].requirement.lookup_health
        self.assertFalse(health.incomplete)
        self.assertTrue(any(lane.lane == "episodes" and lane.state == "unused" and lane.error_code == "cache_missing" for lane in health.lanes))
        self.terminal("published")

    def test_lexical_evidence_survives_failed_embeddings_without_certifying_missing_food(self):
        self.source("My favorite color is green.")
        self.embedding.failed = True
        self.llm.response = "You said your favorite color was green."
        for query in ("Do you remember my favorite color?", "Do you remember my favorite color and food?", "Do you remember my favorite food and color?"):
            with self.subTest(query=query):
                result = self.service.process_text_turn(query, speak=True)
                self.assertTrue(result.succeeded, result.error)
                self.assertIn("green", result.reply)
                self.assertFalse(self.turns[-1].authoritative_no_evidence)
                self.assertTrue(self.turns[-1].requirement.lookup_health.incomplete)
                if "food" in query:
                    self.assertTrue(result.reply.endswith("I can't check your favorite food right now."))
                    self.assertNotIn("don't remember", result.reply)
                self.terminal("published")

    def test_exact_source_survives_semantic_and_episode_failure(self):
        self.source("My telescope is ZX41.")
        self.embedding.failed = True
        self.llm.response = 'I remember you saying, “My telescope is ZX41.”'
        with patch.object(self.authority.recall.episode_cache, "retrieve_candidates", side_effect=RuntimeError("synthetic")), \
                patch.object(self.h.writer.store, "search_fts", side_effect=RuntimeError("synthetic lexical failure")):
            result = self.service.process_text_turn("Do you recall ZX41?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("ZX41", result.reply)
        self.assertEqual(3, self.service._last_memory_authority_diagnostics["retrieval_failed_lanes"])
        self.terminal("published")

    def test_governed_current_value_survives_claim_lookup_exception(self):
        self.llm.response = "Got it."
        self.assertTrue(self.service.process_text_turn("My favorite color is green.").succeeded)
        self.assertTrue(self.service.process_text_turn("Actually, my favorite color is blue.").succeeded)
        self.llm.response = "Your favorite color is blue."
        with patch.object(self.authority.recall.semantic, "retrieve", side_effect=RuntimeError("synthetic")):
            result = self.service.process_text_turn("What is my favorite color and food?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("Your favorite color is blue. I can't check your favorite food right now.", result.reply)
        self.assertNotIn("green", result.reply)

    def test_unused_embedding_lane_and_source_only_followup_do_not_report_outage(self):
        self.authority.recall.semantic.embedding_provider = None
        result = self.service.process_text_turn("Do you remember my telescope?")
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)
        self.assertTrue(any(lane.lane == "semantic" and lane.state == "unused" for lane in self.turns[-1].requirement.lookup_health.lanes))
        with patch.object(self.authority.recall.semantic, "retrieve", side_effect=AssertionError("unused semantic lane entered")) as unused:
            result = self.service.process_text_turn("Which programming language was it?")
        unused.assert_not_called()
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)

    def test_ann_failure_with_successful_bounded_cosine_fallback_fulfills_contract(self):
        self.source("I spent the afternoon refactoring my Python project.")
        self.llm.response = 'I remember you saying, “I spent the afternoon refactoring my Python project.”'
        with patch("memory_v2_store.ann.HnswClaimIndex.query", side_effect=RuntimeError("synthetic ANN unavailable")):
            grounded = self.service.process_text_turn("Do you recall software coding?")
            self.assertTrue(grounded.succeeded, grounded.error)
            self.assertIn("Python", grounded.reply)
            self.assertEqual("recovered", self.service._last_memory_authority_diagnostics["retrieval_health"])
            # The just-committed question is now incrementally indexed. Finish
            # its healthy idle vector work before asserting genuine absence.
            self.service.maintain_canonical_observers()
            missing = self.service.process_text_turn("Do you remember my telescope?")
        self.assertTrue(missing.succeeded, missing.error)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)
        self.assertEqual("recovered", self.service._last_memory_authority_diagnostics["retrieval_health"])

    def test_ann_failure_without_bounded_fallback_is_unavailable(self):
        with patch("memory_v2_store.ann.HnswClaimIndex.query", side_effect=RuntimeError("synthetic")), \
                patch.object(self.h.writer.store, "ann_embedding_count", return_value=2001), \
                patch.object(self.h.writer.store, "semantic_candidates", side_effect=AssertionError("unbounded fallback")) as fallback:
            self.unavailable("Do you recall software coding?")
        fallback.assert_not_called()
        self.assertEqual("fallback_bound", self.service._last_memory_authority_diagnostics["retrieval_error_code"])

    def test_failed_cosine_fallback_cannot_look_like_healthy_empty_result(self):
        with patch("memory_v2_store.ann.HnswClaimIndex.query", side_effect=RuntimeError("synthetic")), \
                patch.object(self.h.writer.store, "semantic_candidates", side_effect=RuntimeError("synthetic")):
            self.unavailable("Do you recall software coding?")
        self.assertEqual("cosine_failed", self.service._last_memory_authority_diagnostics["retrieval_error_code"])

    def test_invalid_embedding_shape_is_not_a_healthy_empty_lookup(self):
        with patch.object(self.embedding, "embed", return_value=[[1.0]]):
            self.unavailable("Do you recall software coding?")
        self.assertEqual("embedding_invalid", self.service._last_memory_authority_diagnostics["retrieval_error_code"])

    def test_bad_primary_and_failed_repair_keep_supported_value_and_backend_unavailability(self):
        self.source("My favorite color is green.")
        self.embedding.failed = True
        self.llm.response = json.dumps({"dialogue": "Your favorite food is pizza.", "presentation": {"gesture": "greeting"}})
        with patch.object(self.llm, "generate_bounded", side_effect=RuntimeError("synthetic repair failed")):
            result = self.service.process_text_turn("Do you remember my favorite color and food?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("You said your favorite color was green. I can't check your favorite food right now.", result.reply)
        self.assertNotIn("pizza", str(self.events))
        self.assertFalse(any(e.type == "assistant_delta" for e in self.events))
        self.assertIsNone(result.presentation.gesture)
        self.assertTrue(self.service._last_memory_authority_diagnostics["fallback_used"])
        self.assertEqual([result.reply], self.tts.spoken)
        self.terminal("published")

    def test_provider_cannot_replace_unavailability_with_false_absence(self):
        self.source("My favorite color is green.")
        self.embedding.failed = True
        for draft in ("You said your favorite color was green. I don't remember your favorite food.",
                      "You said your favorite food was pizza.", "I don't remember anything."):
            with self.subTest(draft=draft):
                self.llm.response = draft
                result = self.service.process_text_turn("Do you remember my favorite color and food?", speak=True)
                self.assertTrue(result.succeeded, result.error)
                self.assertEqual("You said your favorite color was green. I can't check your favorite food right now.", result.reply)
                self.assertTrue(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_scoped_health_composes_supported_missing_and_unavailable_slots(self):
        # Controlled narrower-lane contract, through real retrieval -> authority
        # -> service. Production's whole-query lane faults conservatively affect
        # every unresolved slot; no downstream consumer infers this scope.
        self.source("My favorite color is green.")
        original = self.authority.recall.retrieve

        def scoped(*args, **kwargs):
            result = original(*args, **kwargs)
            return replace(result, health=RetrievalHealth((
                RetrievalLaneHealth("lexical", "complete"),
                RetrievalLaneHealth("semantic", "incomplete", "embedding", "embedding_failed", ("food",)),
            )))

        self.llm.response = "Your favorite food is pizza."
        with patch.object(self.authority.recall, "retrieve", side_effect=scoped):
            result = self.service.process_text_turn("Do you remember my favorite color and favorite food and favorite game?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("You said your favorite color was green. I can't check your favorite food right now. I don't remember you telling me your favorite game.", result.reply)
        self.assertEqual(["supported", "unavailable", "missing"], [s.state for s in self.turns[-1].requirement.slots])
        self.assertNotIn("pizza", str(self.events))

    def test_failed_retrieval_does_not_disable_ordinary_conversation(self):
        self.embedding.failed = True
        self.llm.response = "I'm doing well."
        result = self.service.process_text_turn("Before we start, how are you?", speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(self.llm.response, result.reply)
        self.assertFalse(self.turns[-1].requirement.triggered)
        self.terminal("published")

    def test_failure_diagnostics_contain_only_structural_health(self):
        self.embedding.failed = True
        self.unavailable("Do you recall software coding?")
        diagnostics = self.service._last_memory_authority_diagnostics
        self.assertEqual("embedding_failed", diagnostics["retrieval_error_code"])
        self.assertEqual("embedding", diagnostics["retrieval_error_stage"])
        structural = json.dumps({"diagnostics": diagnostics, "errors": [e.data for e in self.events if e.type == "error"],
                                 "marks": [c.kwargs for c in self.recorder.mark.call_args_list]})
        for private in ("software coding", "private query", "/private/test-path", "synthetic-secret", str(self.h.root)):
            self.assertNotIn(private, structural)

    def test_unreported_health_is_not_inferred_from_empty_candidates(self):
        with patch.object(self.authority.recall, "retrieve", return_value=SimpleNamespace(candidates=(), abstention_reason="nothing_relevant")):
            self.unavailable("Do you recall software coding?")
        self.assertEqual("unreported", self.service._last_memory_authority_diagnostics["retrieval_error_code"])

    def test_current_owner_read_failure_cannot_certify_absence(self):
        with patch("memory_v2_store.repository.MemoryV2Repository.list_current_durable_core", side_effect=RuntimeError("synthetic private failure")):
            self.unavailable("What is my favorite color?")
        self.assertTrue(self.service.process_text_turn("What is my favorite color?").succeeded)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)

    def test_unreadable_current_owner_cannot_restore_a_superseded_historical_value(self):
        self.source("My favorite color is green.")
        self.llm.response = "Got it."
        self.assertTrue(self.service.process_text_turn("My favorite color is green.").succeeded)
        self.assertTrue(self.service.process_text_turn("Actually, my favorite color is blue.").succeeded)
        historical_query = "What do you remember me saying about my favorite color?"
        self.llm.response = "You said your favorite color was green."
        healthy_history = self.service.process_text_turn(historical_query)
        self.assertTrue(healthy_history.succeeded, healthy_history.error)
        self.assertIn("green", healthy_history.reply)
        with patch("memory_v2_store.repository.MemoryV2Repository.list_current_durable_core", side_effect=RuntimeError("synthetic")):
            self.unavailable("What is my favorite color?")
            # The unavailable current owner does not invalidate exact history.
            history = self.service.process_text_turn(historical_query)
            self.assertTrue(history.succeeded, history.error)
            self.assertEqual(healthy_history.reply, history.reply)
        self.llm.response = "Your favorite color is blue."
        current = self.service.process_text_turn("What is my favorite color?")
        self.assertTrue(current.succeeded, current.error)
        self.assertEqual("Your favorite color is blue.", current.reply)

    def test_degradation_never_promotes_wrong_owner_question_or_uncertain_evidence(self):
        sources = (
            ("Your favorite color is green.", "assistant", "assertion"),
            ("Is my favorite color green?", "user", "question"),
            ("Almost like my favorite color.", "user", "assertion"),
            ("My favorite color might be green.", "user", "assertion"),
        )
        for text, speaker, act in sources:
            with self.subTest(speaker=speaker, act=act):
                self.embedding.failed = False
                self.source(text, speaker=speaker, speech_act=act)
                healthy = self.service.process_text_turn("What is my favorite color?")
                self.assertTrue(healthy.succeeded, healthy.error)
                self.assertTrue(self.turns[-1].authoritative_no_evidence)
                self.embedding.failed = True
                self.unavailable("What is my favorite color?")

    def test_supported_food_and_then_both_supported_survive_degradation(self):
        self.source("My favorite food is pizza.")
        self.llm.response = "You said your favorite food was pizza."
        self.embedding.failed = True
        mixed = self.service.process_text_turn("Do you remember my favorite color and food?", speak=True)
        self.assertTrue(mixed.succeeded, mixed.error)
        self.assertEqual("You said your favorite food was pizza. I can't check your favorite color right now.", mixed.reply)
        self.embedding.failed = False
        self.source("My favorite color is green.")
        self.embedding.failed = True
        self.llm.response = "You said your favorite color was green. You said your favorite food was pizza."
        full = self.service.process_text_turn("Do you remember my favorite color and food?", speak=True)
        self.assertTrue(full.succeeded, full.error)
        self.assertEqual(self.llm.response, full.reply)
        self.assertEqual(["supported", "supported"], [s.state for s in self.turns[-1].requirement.slots])
        self.terminal("published")

    def test_existing_invalid_episode_generation_is_unavailable_then_recovers(self):
        from memory_v2_episode_compaction import EpisodeCacheValidationResult

        cache = self.authority.recall.episode_cache
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        invalid = replace(validation, state="invalid", generation_id="synthetic-invalid-generation")
        self.assertIsInstance(invalid, EpisodeCacheValidationResult)
        with patch.object(cache, "validate_for_context", return_value=invalid):
            self.unavailable("Do you remember my telescope?")
        self.assertEqual("cache_invalid", self.service._last_memory_authority_diagnostics["retrieval_error_code"])
        recovered = self.service.process_text_turn("Do you remember my telescope?")
        self.assertTrue(recovered.succeeded, recovered.error)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)

    def test_real_shadow_worker_records_incomplete_execution_without_quality_or_private_content(self):
        from development_flight_recorder import DevelopmentFlightRecorder
        from memory_recall_shadow import RealTurnMemoryShadow

        self.source("I spent the afternoon refactoring my Python project.")
        recorder = DevelopmentFlightRecorder(sample_hz=1)
        observed = Mock()
        observer = RealTurnMemoryShadow(
            self.h.writer.database_path, self.h.character_id, recorder=recorder,
            provider_factory=lambda: self.embedding, prompt_design_observer=observed,
        )
        try:
            with patch.object(recorder, "_gpu_sample", return_value={}):
                recorder.start(unity_pid=999999, state_provider=lambda: {})
                for turn_id, failed in ((1, True), (2, False)):
                    self.embedding.failed = failed
                    self.assertTrue(observer.submit(
                        turn_id=turn_id, generation=turn_id, canonical_user_index=0,
                        query_text="Do you recall software coding?", messages=self.conversation.messages,
                        v1_selected=(), v1_latency_ms=None, accept=lambda _record: True,
                    ))
                    self.assertTrue(observer.drain(timeout=3))
                    record = list(recorder._memory_recall_shadow)[-1]
                    self.assertEqual("incomplete" if failed else "complete", record["retrieval_health"])
                    self.assertEqual("shadow_failure" if failed else "", record["v2_abstention_reason"])
                    self.assertEqual("embedding_failed" if failed else "", record["retrieval_error_code"])
                    self.assertEqual(0 if failed else 1, observed.call_count)
                    self.assertEqual(0 if failed else 1, len(record["v2"]))
                    for private in ("software coding", "Python project", "synthetic-secret", "/private/test-path", str(self.h.root)):
                        self.assertNotIn(private, json.dumps(record))
        finally:
            observer.close()
            recorder.stop()

    def test_legacy_shadow_failure_is_not_cached_or_counted_as_quality(self):
        from memory_v2_shadow_writer import MemoryV2ShadowWriter
        from memory_v2_telemetry import retrieval_report

        writer = self.h.writer
        writer._embedding_provider = self.embedding
        query = "Do you recall software coding?"
        self.embedding.failed = True
        failed = MemoryV2ShadowWriter.compare(writer, query)
        self.assertEqual("shadow_failure", failed["v2_abstention_reason"])
        self.assertEqual("embedding_failed", failed["error_kind"])
        self.assertEqual((), writer._working_recall.get(self.h.character_id, query))
        report = retrieval_report(writer.store)
        self.assertEqual((1, 0, 1), (report["total_compared"], report["successful_compared"], report["failed_executions"]))
        self.assertIsNone(report["abstention_disagreement_rate"])
        self.assertIsNone(report["overlap_rate"])
        self.embedding.failed = False
        healthy = MemoryV2ShadowWriter.compare(writer, query)
        self.assertIsNone(healthy["error_kind"])
        self.assertTrue(healthy["v2_abstained"])
        report = retrieval_report(writer.store)
        self.assertEqual((2, 1, 1), (report["total_compared"], report["successful_compared"], report["failed_executions"]))
        self.assertEqual(0.0, report["abstention_disagreement_rate"])
        self.assertEqual(0.5, report["v2_error_rate"])
        rows = writer.store.connection.execute("SELECT * FROM retrieval_telemetry").fetchall()
        for private in (query, "synthetic-secret", "/private/test-path"):
            self.assertNotIn(private, json.dumps([dict(row) for row in rows]))

    def test_committed_user_continuity_survives_memory_lookup_failure(self):
        self.embedding.failed = True
        query = "I'm cooking dinner. Do you recall software coding?"
        result = self.service.process_text_turn(query)
        self.assertFalse(result.succeeded)
        self.assertEqual("memory_lookup_unavailable", [e.data["code"] for e in self.events if e.type == "error"][-1])
        self.assertEqual(query, self.conversation.messages[-1]["content"])
        self.assertEqual("user", self.conversation.messages[-1]["role"])
        self.assertEqual(self.conversation.messages, json.loads(self.h.conversation_file.read_bytes()))
        state = self.h.repository.lookup_actor_state(self.h.character_id, "user", "activity").state
        self.assertIsNotNone(state)
        self.terminal("error")

    def test_cancellation_of_degraded_mixed_repair_never_publishes_its_result(self):
        self.source("My favorite color is green.")
        self.embedding.failed = True
        for fail_repair in (False, True):
            with self.subTest(fail_repair=fail_repair):
                entered, release = threading.Event(), threading.Event()
                results = []
                self.events.clear()
                self.recorder.reset_mock()
                old = list(self.conversation.messages)
                # Keep the repair cancellation barrier on the retained format
                # repair path. Known memory-semantic failures now skip the call.
                self.llm.response = '{"dialogue":"Your favorite food is pizza."} trailing prose'

                def repair(*_args, **_kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("Synthetic repair barrier")
                    if fail_repair:
                        raise RuntimeError("Synthetic failed repair")
                    return "You said your favorite color was green."

                with patch.object(self.llm, "generate_bounded", side_effect=repair):
                    worker = threading.Thread(target=lambda: results.append(self.service.process_text_turn(
                        "Do you remember my favorite color and food?", speak=True)))
                    press = threading.Thread(target=self.service.push_to_talk_press)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        press.start()
                        press.join(2)
                        self.assertFalse(press.is_alive())
                        self.assertFalse(release.is_set())
                        release.set()
                        worker.join(3)
                        self.assertFalse(worker.is_alive())
                    finally:
                        release.set()
                        worker.join(3)
                        if press.ident is not None:
                            press.join(3)
                self.assertEqual("interrupted", results[0].error)
                self.assertEqual(old, self.conversation.messages)
                self.assertEqual(old, json.loads(self.h.conversation_file.read_bytes()))
                self.assertFalse(any(e.type in {"assistant_response", "assistant_delta", "presentation", "error"} for e in self.events))
                self.assertEqual(1, sum(e.type == "turn_cancelled" for e in self.events))
                self.terminal("cancelled")
                self.service.push_to_talk_release()
                self.llm.response = "Hello again."
                following = self.service.process_text_turn("Hello again.", speak=True)
                self.assertTrue(following.succeeded, following.error)
                self.assertEqual("Hello again.", following.reply)
                self.terminal("published")

    def test_transport_error_completes_turn_and_next_healthy_lookup_recovers(self):
        import websockets
        from backend_host import AIFrenWebSocketHost, LOOPBACK_HOST

        async def check():
            runtime = SimpleNamespace(stop=Mock(), snapshot=Mock(return_value={
                "state": "off", "ownership": "none", "installed_models": [],
            }))
            host = AIFrenWebSocketHost(service=self.service, application_dir=self.h.root, port=0, local_model_runtime=runtime)
            await host.start()
            try:
                async with websockets.connect(f"ws://{LOOPBACK_HOST}:{host.port}") as client:
                    async def receive():
                        return json.loads(await asyncio.wait_for(client.recv(), 3))

                    await client.send(json.dumps({"command": "get_snapshot"}))
                    self.assertEqual("snapshot", (await receive())["type"])
                    for failed in (True, False):
                        self.embedding.failed = failed
                        await client.send(json.dumps({"command": "submit_text", "text": "Do you remember my telescope?"}))
                        events = []
                        for _ in range(60):
                            message = await receive()
                            event = message["event"]
                            events.append(event)
                            if event["type"] == "status" and event["data"]["state"] in {"error", "ready"}:
                                break
                        else:
                            self.fail("Announced turn did not reach a terminal status")
                        await asyncio.wait_for(asyncio.gather(*list(host._turn_tasks)), 3)
                        await asyncio.sleep(0)
                        await asyncio.wait_for(asyncio.gather(*list(host._event_tasks)), 3)
                        await client.send(json.dumps({"command": "get_snapshot"}))
                        for _ in range(60):
                            message = await receive()
                            if message["type"] == "snapshot":
                                snapshot = message["data"]
                                break
                            events.append(message["event"])
                        else:
                            self.fail("No snapshot barrier")
                        self.assertEqual(1, sum(e["type"] == "turn_started" for e in events))
                        self.assertEqual("error" if failed else "ready", snapshot["status"]["state"])
                        errors = [e["data"] for e in events if e["type"] == "error"]
                        self.assertEqual(1 if failed else 0, len(errors))
                        self.assertEqual(0 if failed else 1, sum(e["type"] == "assistant_response" for e in events))
                        if failed:
                            self.assertEqual("memory_lookup_unavailable", errors[0]["code"])
                            self.assertTrue(errors[0]["recoverable"])
                            for private in ("telescope", "synthetic-secret", str(self.h.root)):
                                self.assertNotIn(private, json.dumps(errors))
                        self.terminal("error" if failed else "published")
            finally:
                await host.stop()

        asyncio.run(check())

    def test_cancelled_embedding_failure_finishes_once_and_replacement_works(self):
        entered, release, replacement_claimed = threading.Event(), threading.Event(), threading.Event()
        results = []

        def blocked(_texts):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("synthetic barrier timeout")
            raise RuntimeError("synthetic embedding failure")

        original_claim = self.service._claim_replacement_turn

        def claim():
            value = original_claim()
            replacement_claimed.set()
            return value

        with patch.object(self.embedding, "embed", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(self.service.process_text_turn("Do you recall software coding?", speak=True)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.service._handle_ptt_tts_interrupt()
                self.assertFalse(release.is_set())
                with patch.object(self.service, "_claim_replacement_turn", side_effect=claim):
                    replacement = threading.Thread(target=lambda: results.append(self.service.process_text_turn("How are you?", speak=True)))
                    replacement.start()
                    self.assertTrue(replacement_claimed.wait(2))
                    release.set()
                    worker.join(3)
                    replacement.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(replacement.is_alive())
            finally:
                release.set()
                worker.join(3)
        self.assertEqual("interrupted", results[0].error)
        self.assertTrue(results[1].succeeded, results[1].error)
        self.assertEqual(1, sum(e.type == "turn_cancelled" for e in self.events))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.assertFalse(any(e.type == "error" for e in self.events))
        terminals = [c.kwargs["outcome"] for c in self.recorder.mark.call_args_list if c.args == ("turn_terminal_outcome",)]
        self.assertEqual(["cancelled", "published"], terminals)
        self.assertEqual(1, sum(row["role"] == "assistant" for row in self.conversation.messages))


if __name__ == "__main__":
    unittest.main()
