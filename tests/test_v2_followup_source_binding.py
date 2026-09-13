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
            "We saw sparkly leaves near the fountain last time.", truth_scope=scope)
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

    def test_fresh_occurrence_followup_does_not_wait_for_episode_compaction(self):
        self.event_index = len(self.conversation.messages)
        learned = self.service.process_text_turn(self.event, speak=False)
        self.assertTrue(learned.succeeded, learned.error)
        validation = self.authority.recall.episode_cache.validate_for_context(
            self.conversation.messages, allow_historical_recall=True)
        self.assertFalse(any(row.accepted and row.source_start_index is not None
            and row.source_start_index <= self.event_index < row.source_end_index_exclusive
            for row in validation.lower_records))
        self.recall_event()
        with patch.object(self.authority.recall.semantic, "retrieve",
                          side_effect=AssertionError("No generic retrieval for an exact attribute")), \
                patch.object(self.authority.recall.episode_cache, "validate_for_context",
                             side_effect=AssertionError("Exact canonical source needs no episode")):
            result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded,
            str(self.service._last_memory_authority_diagnostics))
        self.assertIn("beside the maple arch", result.reply)
        items = self.service._last_memory_authority_diagnostics["admitted_items"]
        self.assertEqual(1, len(items))
        self.assertTrue(items[0]["canonical_record_id"].startswith(
            f"conversation-record-{self.event_index}-"))

    def test_fresh_source_missing_attribute_does_not_search_an_earlier_place(self):
        self.service.process_text_turn("We visited a fountain.", speak=False)
        self.service.process_text_turn("We watched a paper glider together.", speak=False)
        self.recall_event()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
        self.assertNotIn("fountain", result.reply)
        self.assertEqual("complete", self.service._last_memory_authority_diagnostics["retrieval_health"])

    def test_fresh_source_with_two_places_remains_ambiguous(self):
        self.service.process_text_turn(
            "We watched a paper glider beside the maple arch. We sat near the lake.", speak=False)
        self.recall_event()
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn("clarify", result.reply)
        self.assertNotIn("maple arch", result.reply)
        self.assertNotIn("near the lake", result.reply)

    def test_fresh_anchor_hash_speaker_scope_and_character_stay_required(self):
        self.service.process_text_turn(self.event, speak=False)
        self.recall_event()
        valid = self.authority._recall_anchor
        for anchor in (replace(valid, speaker_roles=("assistant",)),
                       replace(valid, canonical_record_ids=("different-source-hash",)),
                       replace(valid, active_truth_scope_id="different-scope"),
                       replace(valid, character_id="different-character")):
            with self.subTest(anchor=anchor):
                self.authority._recall_anchor = replace(anchor, originating_generation=self.authority._turn_generation)
                result = self.service.process_text_turn("Which place was that?", speak=False)
                self.assertNotIn("maple arch", result.reply or "")

    def test_fresh_exact_candidate_requires_complete_canonical_provenance(self):
        from benchmarks.memory_v2.models import RetrievalQuery
        from memory_query_decision import decide_memory_query
        from memory_v2_replacement_shadow import _historical_exclusion
        self.service.process_text_turn(self.event, speak=False)
        self.recall_event()
        query = "Which place was that?"
        result = self.authority.recall.retrieve(
            RetrievalQuery(self.h.character_id, query, "2026-09-11T12:00:00+00:00", "ordinary"),
            recall_anchor=self.authority._recall_anchor, memory_query_decision=decide_memory_query(query))
        self.assertEqual(1, len(result.candidates))
        candidate = result.candidates[0]
        scope = self.service.truth_scope_provenance()["scope_id"]
        self.assertEqual("", candidate.episode_id, "No invented episode provenance")
        self.assertEqual("", _historical_exclusion(candidate, scope, allow_recall_anchor=True))
        proof = candidate.evidence[0]
        for altered in (replace(candidate, evidence=()), replace(candidate, source_segments=()),
                        replace(candidate, associated_from="another-record"),
                        replace(candidate, attribution_state="canonical_interaction_source"),
                        replace(candidate, evidence=(replace(proof, source_id="another-record"),)),
                        replace(candidate, evidence=(replace(proof, source_reference="canonical_index:999"),)),
                        replace(candidate, lane="historical_episode_source")):
            with self.subTest(altered=altered):
                self.assertTrue(_historical_exclusion(altered, scope, allow_recall_anchor=True))

    def test_fresh_source_scope_cannot_be_relabelled_by_an_anchor(self):
        from benchmarks.memory_v2.models import RetrievalQuery
        from memory_query_decision import decide_memory_query
        self.service.process_text_turn("Let's roleplay that we're in a synthetic test scene.", speak=False)
        self.assertEqual("scenario", self.service.truth_scope_provenance()["kind"])
        self.service.process_text_turn(self.event, speak=False)
        self.recall_event()
        anchor = self.authority._recall_anchor
        self.service.process_text_turn("Back to real life.", speak=False)
        scope = self.service.truth_scope_provenance()
        self.assertEqual("real_world", scope["kind"])
        query = "Which place was that?"
        result = self.authority.recall.retrieve(
            RetrievalQuery(self.h.character_id, query, "2026-09-11T12:00:00+00:00", "ordinary"),
            recall_anchor=replace(anchor, active_truth_scope_id=scope["scope_id"]),
            memory_query_decision=decide_memory_query(query))
        self.assertFalse(result.candidates)
        self.assertTrue(any(lane.lane == "anchor" and lane.error_code == "owner_mismatch"
                            for lane in result.health.lanes))

    def test_fresh_broader_association_still_requires_a_valid_episode(self):
        from benchmarks.memory_v2.models import RetrievalQuery
        from memory_query_decision import decide_memory_query
        self.service.process_text_turn(self.event, speak=False)
        self.recall_event()
        query = "What else was connected to that?"
        cache = self.authority.recall.episode_cache
        with patch.object(cache, "validate_for_context", wraps=cache.validate_for_context) as validate:
            result = self.authority.recall.retrieve(
                RetrievalQuery(self.h.character_id, query, "2026-09-11T12:00:00+00:00", "ordinary"),
                recall_anchor=self.authority._recall_anchor,
                memory_query_decision=decide_memory_query(query))
        self.assertGreater(validate.call_count, 0)
        self.assertFalse(any(candidate.lane == "historical_recall_anchor_source"
                             for candidate in result.candidates))

    def test_fresh_observation_place_keeps_exact_source_for_spotted(self):
        text = 'We spotted a silver kite together beside the maple arch during our quiet walk.'
        self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)
        self.llm.response = 'I cannot supply that answer.'
        recalled = self.service.process_text_turn('Do you remember the silver kite?', speak=False)
        self.assertTrue(recalled.succeeded, recalled.error)
        self.assertIn('silver kite', recalled.reply)
        result = self.service.process_text_turn('Which place was that?', speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('beside the maple arch', result.reply)

    def test_cedar_bridge_followup_never_selects_earlier_fountain_in_same_episode(self):
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
        for text in ("Maybe we watched a meteor beside the bridge.", "We never sat near the lake.",
                     "We never spotted a kite beside the arch.", "Maybe we spotted a kite beside the arch."):
            self.assertEqual("", relation_value(text, "place", require_unique=True))


if __name__ == "__main__":
    unittest.main()
