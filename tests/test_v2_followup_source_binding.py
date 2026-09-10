"""One-hop attributes through canonical indexing, episodes and service publication."""
import unittest
from dataclasses import replace
import threading
from unittest.mock import patch

import test_episode_range_progression as ranges
import test_v2_incremental_learning as learning
from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor


class FollowupSourceBindingTests(unittest.TestCase):
    setUp = learning.V2IncrementalLearningTests.setUp
    open_service = learning.V2IncrementalLearningTests.open_service
    reopen = learning.V2IncrementalLearningTests.reopen
    install_rollover = learning.V2IncrementalLearningTests.install_rollover
    append_pairs = ranges.EpisodeRangeProgressionTests.append_pairs
    maintain = ranges.EpisodeRangeProgressionTests.maintain

    event = "We watched a paper glider together beside the maple arch during our quiet picnic."

    def prepare_history(self, event=None, *, speaker="user", duplicate=None, reply="Understood."):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message("My favorite color is green.", truth_scope=scope)
        self.conversation.add_assistant_message(
            "We saw painted pebbles near the fountain last time.", truth_scope=scope)
        self.append_pairs(3)
        self.event_index = len(self.conversation.messages)
        if speaker == "assistant":
            self.conversation.add_user_message("Tell me about your project.", truth_scope=scope)
            self.event_index += 1
            self.conversation.add_assistant_message(event or self.event, truth_scope=scope)
        else:
            self.conversation.add_user_message(event or self.event, truth_scope=scope)
            self.conversation.add_assistant_message(reply, truth_scope=scope)
        if duplicate:
            self.conversation.add_user_message(duplicate, truth_scope=scope)
            self.conversation.add_assistant_message("Understood.", truth_scope=scope)
        self.append_pairs(32)
        cache, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover)
        validation = cache.validate_for_context(self.conversation.messages, allow_historical_recall=True)
        self.assertTrue(any(row.accepted and row.source_start_index <= 1
                            and row.source_end_index_exclusive > self.event_index
                            for row in validation.lower_records), "distractor must share the validated episode")
        self.reopen()

    def recall_event(self):
        # An invalid provider draft exercises the real safe source fallback.
        self.llm.response = "I cannot supply that answer."
        with patch.object(self.authority, "publish", wraps=self.authority.publish) as published:
            result = self.service.process_text_turn("Do you remember the paper glider?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("paper glider", result.reply)
        self.assertEqual(1, published.call_count)
        self.assertIsNotNone(self.authority._recall_anchor)
        return result

    def test_maple_arch_followup_never_selects_earlier_fountain_in_same_episode(self):
        self.prepare_history()
        self.recall_event()
        prepared = []
        original = self.authority.prepare
        def capture(*args, **kwargs):
            turn = original(*args, **kwargs)
            prepared.append(turn)
            return turn
        with patch.object(self.authority, "prepare", side_effect=capture):
            result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        # Assert before inference as well as publication: a provider/renderer
        # patch cannot mask an authoritative source-binding failure.
        self.assertIn("beside the maple arch", prepared[0].requirement.fallback_dialogue)
        self.assertNotIn("fountain", prepared[0].requirement.fallback_dialogue)
        self.assertEqual({self.event_index}, {item.canonical_index for item in prepared[0].design.items})
        self.assertIn("beside the maple arch", result.reply)
        self.assertNotIn("fountain", result.reply)

    def test_missing_attribute_never_uses_another_interaction(self):
        self.prepare_history("We watched a paper glider together.")
        self.recall_event()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
        self.assertNotIn("fountain", result.reply)

    def test_pair_membership_alone_does_not_prove_same_event(self):
        self.prepare_history("We watched a paper glider together.", reply="I visited a fountain.")
        self.recall_event()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
        self.assertNotIn("fountain", result.reply)

    def test_two_similar_events_fallback_keeps_its_published_source(self):
        self.prepare_history(duplicate="We watched a paper glider near the willow pond.")
        self.llm.response = "I cannot supply that answer."
        recalled = self.service.process_text_turn("Do you remember the paper glider?", speak=False)
        self.assertTrue(recalled.succeeded)
        anchor = self.authority._recall_anchor
        self.assertIsNotNone(anchor)
        from memory_v2_evidence_sufficiency import relation_value
        expected = relation_value(self.conversation.messages[anchor.canonical_indices[0]]["content"], "place")
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertIn(expected, result.reply)
        self.assertNotIn("fountain", result.reply)

    def test_healthy_absence_does_not_establish_source(self):
        self.prepare_history()
        self.recall_event()
        self.service.maintain_canonical_observers()  # Complete new vectors before testing healthy absence.
        result = self.service.process_text_turn("Which motorcycle did I say I owned?", speak=False)
        self.assertTrue(result.succeeded, str(self.service._last_memory_authority_diagnostics))
        self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
        self.assertIsNone(self.authority._recall_anchor)
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])

    def test_user_owned_language_attribute(self):
        self.prepare_history("I wrote the paper glider simulation in Python.")
        self.recall_event()
        result = self.service.process_text_turn("Which programming language was it?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertIn("Python", result.reply)
        self.assertEqual({"user"}, {item["speaker_role"] for item in
                                   self.service._last_memory_authority_diagnostics["admitted_items"]})

    def test_ambiguous_locations_inside_exact_source_clarify(self):
        self.prepare_history("We watched a paper glider beside the maple arch. We sat near the lake.")
        self.recall_event()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("clarify", result.reply)
        self.assertNotIn("fountain", result.reply)
        self.assertNotIn("maple arch", result.reply)

    def test_user_owned_and_assistant_owned_language_stays_on_exact_source(self):
        # Run ownership variants on independent canonical/service fixtures.
        self.prepare_history("I wrote the paper glider simulation in Python.", speaker="assistant")
        self.llm.response = "I cannot supply that answer."
        recalled = self.service.process_text_turn("What did you tell me about the paper glider simulation?", speak=False)
        self.assertTrue(recalled.succeeded, recalled.error)
        self.assertIn("Python", recalled.reply)
        self.assertEqual(("assistant",), self.authority._recall_anchor.speaker_roles)
        result = self.service.process_text_turn("Which programming language was it?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("Python", result.reply)
        items = self.service._last_memory_authority_diagnostics["admitted_items"]
        self.assertEqual(1, len(items))
        self.assertTrue(items[0]["canonical_record_id"].startswith(f"conversation-record-{self.event_index}-"))
        self.assertEqual({"assistant"}, {item["speaker_role"] for item in items})

    def test_unrelated_turn_consumes_and_followup_cannot_chain(self):
        self.prepare_history()
        self.recall_event()
        self.assertTrue(self.service.process_text_turn("Hello.", speak=False).succeeded)
        self.assertIsNone(self.authority._recall_anchor)
        self.assertNotIn("fountain", self.service.process_text_turn("Which place was that?", speak=False).reply)
        self.recall_event()
        self.assertIn("maple arch", self.service.process_text_turn("Which place was that?", speak=False).reply)
        again = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(again.succeeded)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])

    def test_new_explicit_subject_uses_ordinary_retrieval(self):
        self.prepare_history()
        self.recall_event()
        result = self.service.process_text_turn("What did you tell me about the fountain?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])
        self.assertIn("fountain", result.reply)

    def test_restart_does_not_resurrect_ephemeral_anchor(self):
        self.prepare_history()
        self.recall_event()
        self.reopen()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])
        self.assertNotIn("fountain", result.reply)

    def test_wrong_source_role_hash_and_scope_cannot_enter_followup(self):
        self.prepare_history()
        self.recall_event()
        valid = self.authority._recall_anchor
        for anchor in (replace(valid, speaker_roles=("assistant",)),
                       replace(valid, canonical_record_ids=("changed-source-hash",)),
                       replace(valid, active_truth_scope_id="different-scope"),
                       replace(valid, character_id="different-character")):
            with self.subTest(anchor=anchor):
                self.authority._recall_anchor = replace(anchor, originating_generation=self.authority._turn_generation)
                result = self.service.process_text_turn("Which place was that?", speak=False)
                self.assertNotIn("fountain", result.reply or "")
                self.assertNotIn("maple arch", result.reply or "")

    def test_source_edit_is_not_an_identity_preserving_followup(self):
        self.prepare_history()
        self.recall_event()
        self.conversation.messages[self.event_index]["content"] = "We watched a paper glider near the fountain."
        self.conversation.save()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertFalse(result.succeeded)
        self.assertNotIn("fountain", result.reply or "")

    def test_failed_assistant_persistence_cannot_publish_anchor(self):
        from conversation.persistence import ConversationPersistenceError
        self.prepare_history()
        save = self.conversation.save
        def fail_assistant():
            if self.conversation.messages[-1]["role"] == "assistant":
                raise ConversationPersistenceError(record_kind="conversation", stage="replace", committed=False)
            return save()
        self.llm.response = "I cannot supply that answer."
        with patch.object(self.conversation, "save", side_effect=fail_assistant):
            result = self.service.process_text_turn("Do you remember the paper glider?", speak=False)
        self.assertFalse(result.succeeded)
        self.assertIsNone(self.authority._recall_anchor)
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])

    def test_cancelled_provider_and_replacement_cannot_publish_anchor(self):
        self.prepare_history()
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("finite provider barrier")
            return 'I remember you saying, “' + self.event + '”'
        results = []
        with patch.object(self.llm, "generate_bounded", side_effect=blocked), patch.object(self.llm, "generate", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(self.service.process_text_turn(
                "Do you remember the paper glider?", speak=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.authority._recall_anchor)
                self.service._cancel_active_turn()
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertFalse(results[0].succeeded)
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded)
        self.assertFalse(self.service._last_memory_authority_diagnostics["recall_anchor_used"])


class FollowupAttributeUniquenessTests(unittest.TestCase):
    def test_unique_attribute_uses_existing_grammar_without_cross_value_guessing(self):
        from memory_v2_evidence_sufficiency import AmbiguousHistoricalAttribute, relation_value
        supported = (("We watched the meteor beside the maple arch.", "place", "beside the maple arch"),
                     ("I wrote the parser in Python.", "programming_language", "Python"),
                     ("I met Mira.", "person", "Mira"), ("My name is Mira.", "identity", "Mira"))
        for text, relation, expected in supported:
            with self.subTest(text=text):
                self.assertEqual(expected, relation_value(text, relation, require_unique=True))
        for text, relation in (("I wrote the project in Python and Rust.", "programming_language"),
                               ("We sat beside the bridge and near the pond.", "place"),
                               ("I visited the lake. I visited the pond.", "place"),
                               ("I met Mira. I met Ada.", "person"),
                               ("My name is Mira. My name is Ada.", "identity"),
                               ("We met on 2025-01-01 and 2025-02-01.", "date")):
            with self.subTest(text=text):
                with self.assertRaises(AmbiguousHistoricalAttribute):
                    relation_value(text, relation, require_unique=True)
                self.assertTrue(relation_value(text, relation), "ordinary existing extraction is unchanged")
        for text in ("Maybe we watched a meteor beside the bridge.", "We never sat near the lake."):
            self.assertEqual("", relation_value(text, "place", require_unique=True))


if __name__ == "__main__":
    unittest.main()
