"""Viewer integration with actual incremental Development V2 owners."""
import unittest
import uuid
from unittest.mock import patch

import test_v2_runtime_recovery as recovery_tests
import test_v2_incremental_learning as learning_tests
from test_memory_v2_embeddings import ToyEmbeddingProvider


class MemoryViewerRuntimeLearningTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen
    install_rollover = learning_tests.V2IncrementalLearningTests.install_rollover
    learn_old_period = learning_tests.V2IncrementalLearningTests.learn_old_period

    def test_historical_runtime_episode_page_and_detail_share_recall_validation(self):
        from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor
        cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.conversation.episode_compaction_cache = cache
        self.learn_old_period()
        self.service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10))
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted)
        page = self.service.memory_view_page(character_id=self.h.character_id, lane="episodes")
        self.assertEqual("current_valid", page["diagnostic_state"])
        self.assertTrue(page["items"])
        detail = self.service.memory_view_detail(character_id=self.h.character_id, lane="episodes",
                                               record_id=page["items"][0]["record_id"])
        self.assertEqual("current_valid", detail["detail"]["diagnostic_state"])
        self.assertIn("V2 authority", page["authority_label"])
        self.assertNotIn("Development", page["authority_label"])
        self.conversation.messages[0]["content"] = "A different synthetic source."
        self.conversation.save()
        rejected = self.service.memory_view_detail(character_id=self.h.character_id, lane="episodes",
                                                  record_id=page["items"][0]["record_id"])
        self.assertNotEqual("current_valid", rejected["detail"]["diagnostic_state"])

    def test_existing_warning_surface_reports_pending_failed_and_recovered_work(self):
        with patch.object(self.h.writer, "observe_canonical_user_durable_facts",
                          side_effect=RuntimeError("PRIVATE dialogue/path/credential sentinel")):
            self.assertTrue(self.service.process_text_turn("My favorite color is green.", speak=False).succeeded)
        failed = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertIn("recovery", failed["warning"].casefold())
        self.assertNotIn("PRIVATE", str(failed))
        self.service.maintain_canonical_observers()
        recovered = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertEqual("", recovered["warning"])
        with self.assertRaises(RuntimeError):
            self.service.memory_view_page(character_id=str(uuid.uuid4()), lane="v2_claims")

    def test_search_preparation_warning_is_read_only_and_clears_after_recovery(self):
        self.assertTrue(self.service.process_text_turn("We watched a meteor shower together.", speak=False).succeeded)
        before = self.h.writer.store.connection.total_changes
        calls = len(self.llm.calls)
        pending = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertIn("still being prepared", pending["warning"])
        self.assertEqual(before, self.h.writer.store.connection.total_changes)
        self.assertEqual(calls, len(self.llm.calls))
        with patch.object(ToyEmbeddingProvider, "embed", side_effect=RuntimeError("PRIVATE source sentinel")):
            self.service.maintain_canonical_observers()
        failed = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertIn("could not be prepared", failed["warning"])
        self.assertNotIn("PRIVATE", str(failed))
        self.service.maintain_canonical_observers()
        current = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertEqual("", current["warning"])

    def test_development_viewer_never_labels_failure_as_v1_authority(self):
        with patch.object(self.h.writer.store, "connection", None):
            page = self.service.memory_view_page(character_id=self.h.character_id, lane="v2_claims")
        self.assertEqual("degraded", page["availability"])
        self.assertIn("no V1 fallback", page["authority_label"])
        self.assertNotIn("V1 remains authoritative", str(page))


if __name__ == "__main__":
    unittest.main()
