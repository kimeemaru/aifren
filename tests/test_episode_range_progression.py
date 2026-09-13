"""Independent source ranges through service idle work and one-hop authority."""
import unittest
import threading

import test_v2_incremental_learning as learning_tests
from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor
from aifren.continuity.memory_v2_episode_compaction import canonical_record_id


class EpisodeRangeProgressionTests(unittest.TestCase):
    setUp = learning_tests.V2IncrementalLearningTests.setUp
    open_service = learning_tests.V2IncrementalLearningTests.open_service
    reopen = learning_tests.V2IncrementalLearningTests.reopen
    install_rollover = learning_tests.V2IncrementalLearningTests.install_rollover

    def append_pairs(self, count):
        scope = self.service.truth_scope_provenance()
        for index in range(count):
            self.conversation.add_user_message(f"Synthetic greeting {index}.", truth_scope=scope)
            self.conversation.add_assistant_message("Understood.", truth_scope=scope)
        self.conversation.save()

    def prepare_gap(self):
        self.append_pairs(28)
        scope = self.service.truth_scope_provenance()
        gap = len(self.conversation.messages)
        self.conversation.add_user_message("Excluded amber harbor sentinel.", truth_scope=scope)
        self.conversation.messages[-1]["semantic_admission"] = {
            "channel": "hearing", "state": "unavailable", "understood": False}
        self.conversation.add_assistant_message("Excluded orphan response.", truth_scope=scope)
        self.conversation.save()
        self.event_index = len(self.conversation.messages)
        self.event = "We watched a violet meteor together beside the lake during our quiet picnic."
        self.assertTrue(self.service.process_text_turn(self.event, speak=False).succeeded)
        self.append_pairs(30)
        return gap

    def maintain(self, rollover, pages=8):
        for _ in range(pages):
            self.service.maintain_canonical_observers()
            self.assertTrue(rollover.wait(10), "finite synthetic compactor deadline")

    def test_gap_append_idle_restart_recall_and_exact_place_followup(self):
        gap = self.prepare_gap()
        cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover)
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted)
        covered = {i for row in validation.lower_records if row.accepted
                   for i in range(row.source_start_index, row.source_end_index_exclusive)}
        self.assertIn(self.event_index, covered, "later eligible interaction never progressed")
        self.assertNotIn(gap, covered)
        self.assertNotIn(gap + 1, covered, "must not stitch an orphan after the gap")
        for row in validation.lower_records:
            if row.accepted:
                self.assertEqual(tuple(row.metadata["source_record_ids"]), tuple(
                    canonical_record_id(i, self.conversation.messages[i])
                    for i in range(row.source_start_index, row.source_end_index_exclusive)))
        generation = validation.generation_id
        self.maintain(rollover, pages=2)
        self.assertEqual(generation, cache._current_generation_id())
        self.reopen()
        # Toy semantic retrieval also admits unrelated verification rows.
        # The deterministic fallback identifies its one published source;
        # multi-source free prose cannot create an arbitrary antecedent.
        self.llm.response = "I cannot supply that answer."
        recalled = self.service.process_text_turn("Do you remember the violet meteor beside the lake?", speak=False)
        self.assertTrue(recalled.succeeded, recalled.error)
        self.assertIn("lake", recalled.reply)
        self.llm.response = "It was beside the lake."
        followup = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(followup.succeeded, followup.error)
        self.assertIn("lake", followup.reply)
        self.assertFalse(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])

    def test_exact_canonical_anchor_does_not_require_pending_episode(self):
        self.prepare_gap()
        cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover, pages=1)  # Older valid range, later event not prepared yet.
        rollover.close()  # Keep the applicable range pending across the two turns.
        self.llm.response = "I cannot supply that answer."
        self.assertTrue(self.service.process_text_turn(
            "Do you remember the violet meteor beside the lake?", speak=False).succeeded)
        followup = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(followup.succeeded, followup.error)
        self.assertIn("beside the lake", followup.reply)
        diagnostics = self.service._last_memory_authority_diagnostics
        self.assertEqual("not_applicable", diagnostics["absence_kind"])
        self.assertEqual("historical_recall_anchor_source", diagnostics["admitted_items"][0]["lane"])
        self.assertEqual(canonical_record_id(self.event_index, self.conversation.messages[self.event_index]),
                         diagnostics["admitted_items"][0]["canonical_record_id"])

    def test_place_paraphrase_keeps_the_source_relation_without_forcing_fallback(self):
        self.prepare_gap()
        _cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover)
        for answer, accepted in (("It was by the lake.", True),
                                 ("It was underneath the lake.", False)):
            with self.subTest(answer=answer):
                self.llm.response = "I cannot supply that answer."
                self.assertTrue(self.service.process_text_turn(
                    "Do you remember the violet meteor beside the lake?", speak=False).succeeded)
                self.llm.response = answer
                before = len(self.llm.calls)
                result = self.service.process_text_turn("Which place was that?", speak=False)
                self.assertTrue(result.succeeded)
                if accepted:
                    self.assertEqual(answer, result.reply)
                    self.assertEqual(1, len(self.llm.calls) - before)
                else:
                    self.assertNotEqual(answer, result.reply)
                    self.assertIn("beside the lake", result.reply)

    def test_multiple_gaps_and_incomplete_pair_never_enter_compactor(self):
        from aifren.continuity.memory_v2_episode_compaction import historical_episode_source_groups
        gap = self.prepare_gap()
        scope = self.service.truth_scope_provenance()
        malformed = len(self.conversation.messages)
        self.conversation.add_user_message("Malformed provenance sentinel.", truth_scope=scope)
        self.conversation.messages[-1]["origin"] = {"invalid": True}
        self.conversation.add_assistant_message("Orphan sentinel.", truth_scope=scope)
        self.append_pairs(2)
        orphan = len(self.conversation.messages)
        self.conversation.add_user_message("Unanswered sentinel.", truth_scope=scope)
        self.append_pairs(30)
        class Provider(_DeterministicHistoricalCompactor):
            def generate(inner, history, prompt, **kw):
                self.assertNotIn("sentinel", prompt.casefold())
                return super().generate(history, prompt, **kw)
        cache, rollover = self.install_rollover(Provider())
        self.maintain(rollover)
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted)
        covered = {i for a, b in validation.covered_source_ranges for i in range(a, b)}
        self.assertTrue(covered.intersection(range(malformed + 2, orphan)))
        self.assertTrue(covered.isdisjoint({gap, gap + 1, malformed, malformed + 1, orphan}))

    def test_late_failure_cancellation_and_source_edit_preserve_prior_ranges(self):
        self.prepare_gap()
        entered, release = threading.Event(), threading.Event()
        class Provider(_DeterministicHistoricalCompactor):
            blocked = False
            fail = False
            def generate(inner, *args, **kwargs):
                if inner.fail:
                    raise RuntimeError("synthetic private sentinel")
                if inner.blocked:
                    entered.set()
                    if not release.wait(10):
                        raise AssertionError("barrier timeout")
                return super().generate(*args, **kwargs)
        provider = Provider()
        cache, rollover = self.install_rollover(provider)
        self.maintain(rollover, 1)
        original = cache._current_generation_id()
        provider.fail = True
        self.maintain(rollover, 1)
        self.assertEqual(original, cache._current_generation_id())

        provider.fail, provider.blocked = False, True
        self.service.maintain_canonical_observers()
        try:
            self.assertTrue(entered.wait(5))
            self.conversation.messages[self.event_index]["content"] = "Changed exact synthetic source."
            self.conversation.save()
        finally:
            release.set()
            self.assertTrue(rollover.wait(10))
        self.assertEqual(original, cache._current_generation_id())
        self.assertTrue(cache.validate_for_context(self.conversation.messages, allow_historical_recall=True).accepted)
        entered.clear(); release.clear()
        self.service.maintain_canonical_observers()
        try:
            self.assertTrue(entered.wait(5))
            rollover.close()
        finally:
            release.set()
            self.assertTrue(rollover.wait(10))
        self.assertEqual(original, cache._current_generation_id())

    def test_later_scope_boundaries_keep_original_ownership(self):
        import uuid
        self.prepare_gap()
        scenario = "scope-" + str(uuid.uuid4())
        store = self.h.writer.store
        store.connection.execute("INSERT INTO truth_scopes VALUES (?, ?, 'scenario', 'Synthetic scope', 'inactive', 2, 2)",
            (self.h.character_id, scenario))
        start = len(self.conversation.messages)
        # Adjacent roles with different explicit scopes are independent records,
        # as in the existing shared segmentation contract.
        self.conversation.add_user_message("Synthetic scenario event.",
            truth_scope={"kind": "scenario", "scope_id": scenario})
        self.conversation.add_assistant_message("Synthetic real-world reply.",
            truth_scope=self.service.truth_scope_provenance())
        self.append_pairs(30)
        cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover)
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(validation.accepted)
        rows = [r for r in validation.lower_records if r.accepted and r.source_start_index <= start < r.source_end_index_exclusive]
        self.assertEqual(1, len(rows), str(validation.covered_source_ranges))
        self.assertEqual(scenario, rows[0].truth_scope_id)
        self.assertEqual(start + 1, rows[0].source_end_index_exclusive)
