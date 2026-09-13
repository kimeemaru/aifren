"""Synthetic normal-service lookup between idle index maintenance ticks."""
import unittest
import threading
from unittest.mock import patch

import test_v2_runtime_recovery as recovery_tests
from memory_query_decision import decide_memory_query


class V2LookupPreflightTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen

    def current_answer_after_restart(self):
        for text in ("My favorite color is green.",
                     "Actually, my favorite color is blue."):
            result = self.service.process_text_turn(text, speak=False)
            self.assertTrue(result.succeeded, result.error)
        self.service.maintain_canonical_observers()
        self.reopen()
        result = self.service.process_text_turn("What is my favorite color now?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("blue", result.reply)
        recovery = self.service._canonical_observation_recovery
        provider = self.authority.recall.semantic.embedding_provider
        self.assertTrue(recovery._missing_embedding_ids(provider, limit=1))
        return recovery, provider

    def test_restarted_current_then_before_question_needs_no_idle_gap(self):
        recovery, _ = self.current_answer_after_restart()
        original = list(self.conversation.messages)
        with patch.object(recovery, "maintain_embeddings", wraps=recovery.maintain_embeddings) as maintain:
            result = self.service.process_text_turn(
                "What color did I say was my favorite before I changed it to blue?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("green", result.reply)
        self.assertEqual(original, self.conversation.messages[:len(original)])
        maintain.assert_called_once()
        self.assertEqual("complete", self.service._last_memory_authority_diagnostics["retrieval_health"])
        self.assertEqual(0, self.service.memory.retrieval_calls)
        self.assertEqual([], self.service.memory.processed)
        self.assertEqual(0, self.service.memory.save_calls)

    def test_restarted_current_then_after_question_keeps_corrected_value(self):
        recovery, _ = self.current_answer_after_restart()
        with patch.object(recovery, "maintain_embeddings", wraps=recovery.maintain_embeddings) as maintain:
            result = self.service.process_text_turn(
                "What color did I say was my favorite after I said it was green?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("blue", result.reply)
        self.assertNotIn("green", result.reply)
        maintain.assert_called_once()

    def test_newly_answered_question_does_not_make_absence_temporarily_unavailable(self):
        recovery, _ = self.current_answer_after_restart()
        with patch.object(recovery, "maintain_embeddings", wraps=recovery.maintain_embeddings) as maintain:
            result = self.service.process_text_turn("What is my favorite musical instrument?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        maintain.assert_called_once()
        diagnostic = self.service._last_memory_authority_diagnostics
        self.assertNotEqual("lookup_unavailable", diagnostic["absence_kind"])
        self.assertEqual("no_grounded_evidence", diagnostic["evidence_state"])
        self.assertEqual("complete", diagnostic["retrieval_health"])

    def test_clean_lookup_does_not_repeat_embedding_work_or_mutate_canonical(self):
        recovery, _ = self.current_answer_after_restart()
        original = list(self.conversation.messages)
        decision = decide_memory_query("What is my favorite musical instrument?")
        with patch.object(recovery, "maintain_embeddings", wraps=recovery.maintain_embeddings) as maintain:
            health, current = recovery.prepare_lookup(decision)
            self.assertFalse(health.incomplete)
            self.assertTrue(current)
            self.assertEqual(1, recovery.last_lookup_embedding_work["embedded"])
            self.assertGreaterEqual(recovery.last_lookup_embedding_work["duration_ms"], 0)
            health, current = recovery.prepare_lookup(decision)
        maintain.assert_called_once()
        self.assertFalse(recovery.last_lookup_embedding_work["attempted"])
        self.assertFalse(health.incomplete)
        self.assertEqual(original, self.conversation.messages)

    def test_failed_page_cannot_establish_absence_and_does_not_call_dialogue_model(self):
        recovery, provider = self.current_answer_after_restart()
        original = list(self.conversation.messages)
        before_calls = len(self.llm.calls)
        with patch.object(provider, "embed", side_effect=RuntimeError("synthetic encoder failure")), \
                patch.object(recovery, "maintain_embeddings", wraps=recovery.maintain_embeddings) as maintain:
            result = self.service.process_text_turn("What is my favorite musical instrument?", speak=False)
        self.assertFalse(result.succeeded)
        maintain.assert_called_once()
        self.assertEqual("lookup_unavailable", self.service._last_memory_authority_diagnostics["absence_kind"])
        self.assertGreater(recovery.last_lookup_embedding_work["failed"], 0)
        self.assertEqual(original, self.conversation.messages)
        self.assertEqual(before_calls, len(self.llm.calls))

    def test_backlog_gets_only_one_existing_bounded_page_per_lookup(self):
        from memory_v2_runtime_observation import PAGE_RECORDS
        scope = self.service.truth_scope_provenance()
        for index in range(PAGE_RECORDS + 3):
            self.conversation.add_user_message(f"Synthetic ordinary greeting {index}.", truth_scope=scope)
            self.conversation.add_assistant_message("Understood.", truth_scope=scope)
        self.conversation.save()
        recovery = self.service._canonical_observation_recovery
        for _ in range(3):
            self.service._recover_canonical_observers()
        self.assertTrue(all(value["state"] == "complete"
                            for value in recovery.last_status["consumers"].values()))
        original = list(self.conversation.messages)
        provider = self.authority.recall.semantic.embedding_provider
        with patch.object(provider, "embed", wraps=provider.embed) as embed:
            health, current = recovery.prepare_lookup(decide_memory_query(
                "What is my favorite musical instrument?"))
        embed.assert_called_once()
        self.assertEqual(PAGE_RECORDS, len(embed.call_args.args[0]))
        self.assertEqual(PAGE_RECORDS, recovery.last_lookup_embedding_work["embedded"])
        self.assertTrue(current)
        self.assertTrue(health.incomplete)
        self.assertEqual("embedding_not_current", health.diagnostics()["retrieval_error_code"])
        self.assertEqual(original, self.conversation.messages)

    def test_unresolved_observation_does_not_replay_or_advance_during_preflight(self):
        recovery, _ = self.current_answer_after_restart()
        recovery.last_status = {"state": "unresolved", "reason": "source_scope_unresolved"}
        with patch.object(recovery, "maintain_embeddings", side_effect=AssertionError("source unresolved")), \
                patch.object(recovery, "run_page", side_effect=AssertionError("no source replay")):
            health, _ = recovery.prepare_lookup(decide_memory_query(
                "What is my favorite musical instrument?"))
        self.assertTrue(health.incomplete)
        self.assertFalse(recovery.last_lookup_embedding_work["attempted"])

    def test_ordinary_and_exact_anchor_lookup_do_not_trigger_generic_vector_preflight(self):
        recovery, _ = self.current_answer_after_restart()
        with patch.object(recovery, "maintain_embeddings", side_effect=AssertionError("no generic page")):
            for query in ("Hello.", "Which place was that?"):
                health, _ = recovery.prepare_lookup(decide_memory_query(query))
                self.assertEqual("unused", health.state)
                self.assertFalse(recovery.last_lookup_embedding_work["attempted"])

    def test_cancelled_preflight_cannot_publish_or_call_dialogue_model(self):
        recovery, provider = self.current_answer_after_restart()
        entered, release = threading.Event(), threading.Event()
        results, events = [], []
        self.service.subscribe(events.append)
        original = list(self.conversation.messages)
        before_calls = len(self.llm.calls)
        real_embed = provider.embed

        def blocked(texts):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("synthetic barrier timeout")
            return real_embed(texts)

        with patch.object(provider, "embed", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(self.service.process_text_turn(
                "What is my favorite musical instrument?", speak=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.service._handle_ptt_tts_interrupt()
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual("interrupted", results[0].error)
        self.assertEqual(before_calls, len(self.llm.calls))
        self.assertEqual(original, self.conversation.messages)
        self.assertFalse(any(event.type == "assistant_response" for event in events))


if __name__ == "__main__":
    unittest.main()
