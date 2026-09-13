"""New synthetic learning through ordinary service turns, without staged seeds."""
import unittest
import threading
from unittest.mock import patch

import test_v2_runtime_recovery as recovery_tests


class V2IncrementalLearningTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen

    def install_rollover(self, provider):
        from memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor, EpisodeCompactionRollover
        cache = EpisodeCompactionCache(self.h.writer.store, self.h.character_id)
        conversation = self.conversation
        rollover = EpisodeCompactionRollover(cache, lambda: EpisodeCompactor(provider),
            historical_runtime=True, retry_seconds=0,
            canonical_source_provider=lambda: conversation.messages[:conversation._persisted_message_count])
        conversation.episode_compaction_rollover = rollover
        self.addCleanup(rollover.close)
        return cache, rollover

    def learn_old_period(self, count=68):
        for index in range(count):
            text = ("We watched the meteor shower by the lake." if index == 0
                    else f"Synthetic ordinary greeting number {index}.")
            self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)

    def test_shared_event_ages_out_and_is_recalled_after_restart_without_rebuild(self):
        event = "We watched the meteor shower by the lake."
        self.assertTrue(self.service.process_text_turn(event, speak=False).succeeded)
        for index in range(16):
            self.assertTrue(self.service.process_text_turn(
                f"Synthetic ordinary greeting number {index}.", speak=False).succeeded)
        self.assertFalse(any("meteor" in row["content"] for row in self.conversation.messages[-12:]))
        self.reopen()
        self.llm.response = 'I remember you saying, “We watched the meteor shower by the lake.”'
        result = self.service.process_text_turn("Do you remember the meteor shower?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("by the lake", result.reply)
        self.assertFalse(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])

    def test_failed_new_semantic_vectors_remain_unavailable_until_retry_recovers(self):
        from test_memory_retrieval_health import ControlledEmbedding
        embedding = ControlledEmbedding()
        self.authority.recall.semantic.embedding_provider = embedding
        self.assertTrue(self.service.process_text_turn(
            "I spent the afternoon refactoring my Python project.", speak=False).succeeded)
        query = "Do you recall software coding?"
        calls = len(self.llm.calls)
        # A due page is now serviced at explicit lookup as well as idle. A real
        # provider failure still cannot be relabelled healthy absence.
        embedding.failed = True
        missing_vectors = self.service.process_text_turn(query, speak=False)
        self.assertFalse(missing_vectors.succeeded)
        self.assertEqual(calls, len(self.llm.calls))
        self.assertEqual("lookup_unavailable", self.service._last_memory_authority_diagnostics["absence_kind"])
        self.service.maintain_canonical_observers()
        self.assertGreater(self.service._canonical_observation_recovery.last_embedding_work["failed"], 0)
        reasons = [row[0] for row in self.h.writer.store.connection.execute(
            "SELECT failure_reason FROM claim_embeddings WHERE failure_reason IS NOT NULL")]
        self.assertTrue(reasons)
        self.assertEqual({"embedding_failed"}, set(reasons))
        embedding.failed = False
        self.service.maintain_canonical_observers()
        self.llm.response = 'I remember you saying, “I spent the afternoon refactoring my Python project.”'
        healthy = self.service.process_text_turn(query, speak=False)
        self.assertTrue(healthy.succeeded, healthy.error)
        self.assertIn("Python project", healthy.reply)

    def test_derived_maintenance_fault_does_not_retire_service_or_force_rebuild(self):
        self.assertTrue(self.service.process_text_turn("We visited the lighthouse.", speak=False).succeeded)
        with patch.object(self.h.writer.store, "rebuild_fts", side_effect=AssertionError("full rebuild")), \
                patch("memory_v2_runtime_observation.CanonicalObservationRecovery.maintain_embeddings",
                      side_effect=RuntimeError("synthetic maintenance interruption")):
            self.service.maintain_canonical_observers()
            self.assertTrue(self.service.process_text_turn("Hello.", speak=False).succeeded)
        self.service.maintain_canonical_observers()
        self.llm.response = 'I remember you saying, “We visited the lighthouse.”'
        self.assertIn("lighthouse", self.service.process_text_turn(
            "Do you remember the lighthouse?", speak=False).reply)

    def test_append_index_failure_is_unavailable_and_retains_originals_until_retry(self):
        with patch("memory_v2_runtime_observation.CanonicalObservationRecovery._observe",
                   side_effect=RuntimeError("synthetic crash after canonical save")):
            self.assertTrue(self.service.process_text_turn("We visited the lighthouse.", speak=False).succeeded)
        count = len(self.conversation.messages)
        with patch("memory_v2_historical_evidence.persist_historical_occurrence",
                   side_effect=RuntimeError("synthetic indexing failure")):
            result = self.service.process_text_turn("Do you remember the lighthouse?", speak=False)
        self.assertFalse(result.succeeded)
        self.assertEqual(count, len(self.conversation.messages))
        self.assertEqual("lookup_unavailable", self.service._last_memory_authority_diagnostics["absence_kind"])
        self.reopen()
        self.llm.response = 'I remember you saying, “We visited the lighthouse.”'
        result = self.service.process_text_turn("Do you remember the lighthouse?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("visited the lighthouse", result.reply)

    def test_idle_episode_pages_are_bounded_valid_and_append_aware(self):
        from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor
        from memory_v2_episode_compaction import EPISODE_PURPOSE_HISTORICAL
        class Provider(_DeterministicHistoricalCompactor):
            episode_calls = 0
            def generate(inner, history, prompt, **kwargs):
                if "Create a compact, neutral third-person account" in prompt:
                    inner.episode_calls += 1
                return super().generate(history, prompt, **kwargs)
        provider = Provider()
        cache, rollover = self.install_rollover(provider)
        self.learn_old_period()
        self.assertEqual(0, provider.episode_calls, "turns must not compact in the foreground")
        self.service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10), "bounded synthetic compactor did not finish")
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted, validation)
        self.assertEqual(EPISODE_PURPOSE_HISTORICAL, validation.generation_purpose)
        self.assertEqual(1, provider.episode_calls)
        old_end = validation.raw_start_index
        old_generation = cache._current_generation_id()
        self.service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10))
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted, validation)
        self.assertGreater(validation.raw_start_index, old_end)
        self.assertNotEqual(old_generation, cache._current_generation_id())
        self.assertEqual(2, provider.episode_calls, "validated prefix must be reused")
        self.reopen()
        self.llm.response = 'I remember you saying, “We watched the meteor shower by the lake.”'
        result = self.service.process_text_turn("Do you remember the meteor shower?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("by the lake", result.reply)

    def test_invalid_episode_generation_preserves_valid_prefix_and_raw_sources(self):
        from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor
        class Provider(_DeterministicHistoricalCompactor):
            broken = False
            def generate(inner, *args, **kwargs):
                if inner.broken:
                    raise RuntimeError("synthetic failure with PRIVATE sentinel")
                return super().generate(*args, **kwargs)
        provider = Provider()
        cache, rollover = self.install_rollover(provider)
        self.learn_old_period()
        self.service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10))
        generation = cache._current_generation_id()
        self.assertIsNotNone(generation)
        provider.broken = True
        self.service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10))
        self.assertEqual(generation, cache._current_generation_id())
        self.assertTrue(cache.validate_for_context(self.conversation.messages, allow_historical_recall=True).accepted)
        self.llm.response = 'I remember you saying, “We watched the meteor shower by the lake.”'
        result = self.service.process_text_turn("Do you remember the meteor shower?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("by the lake", result.reply)
        self.assertTrue(rollover.wait(10))

    def test_retirement_rejects_blocked_compaction_without_blocking_new_turn(self):
        from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor
        entered, release = threading.Event(), threading.Event()
        class Provider(_DeterministicHistoricalCompactor):
            def generate(inner, *args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("synthetic barrier timeout")
                return super().generate(*args, **kwargs)
        cache, rollover = self.install_rollover(Provider())
        self.learn_old_period()
        self.service.maintain_canonical_observers()
        try:
            self.assertTrue(entered.wait(5))
            result = self.service.process_text_turn("Hello again.", speak=False)
            self.assertTrue(result.succeeded, result.error)
            self.assertFalse(release.is_set())
            rollover.close()
        finally:
            release.set()
            self.assertTrue(rollover.wait(10))
        self.assertIsNone(cache._current_generation_id())

    def test_current_correction_and_old_speech_remain_distinct_after_restart(self):
        self.assertTrue(self.service.process_text_turn("My favorite color is red.", speak=False).succeeded)
        self.assertTrue(self.service.process_text_turn("Actually, my favorite color is blue.", speak=False).succeeded)
        for index in range(16):
            self.service.process_text_turn(f"Ordinary greeting {index}.", speak=False)
        self.reopen()
        self.llm.response = "Your favorite color is blue."
        current = self.service.process_text_turn("What is my favorite color?", speak=False)
        self.assertTrue(current.succeeded, current.error)
        self.assertIn("blue", current.reply)
        # The production idle owner fills bounded vector pages, including the
        # newly committed recall turn, before this semantic lookup.
        for _ in range(3):
            self.service.maintain_canonical_observers()
        for query in ("Did I ever say my favorite color is red?",
                      "Do you remember my favorite color being red?"):
            self.llm.response = 'You said, “My favorite color is red.”'
            calls = len(self.llm.calls)
            historical = self.service.process_text_turn(query, speak=False)
            self.assertTrue(historical.succeeded, str(self.service._last_memory_authority_diagnostics))
            self.assertIn("red", historical.reply)
            self.assertGreater(len(self.llm.calls), calls)
            self.assertFalse(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
            self.service.maintain_canonical_observers()

    def test_recorded_speech_does_not_establish_an_unsupported_purchase(self):
        for text in ("I read a guide about a silver telescope.",
                     "Would I enjoy buying a silver telescope?"):
            self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)
        self.reopen()
        self.service.maintain_canonical_observers()
        calls = len(self.llm.calls)
        result = self.service.process_text_turn("Did I ever say I bought a silver telescope?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(calls, len(self.llm.calls))
        self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])


if __name__ == "__main__":
    unittest.main()
