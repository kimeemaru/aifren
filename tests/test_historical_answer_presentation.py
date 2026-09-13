"""Historical answer governance through the real service/retrieval owners."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_episode_range_progression as episode_tests
from test_memory_v2_historical_episodes import _DeterministicHistoricalCompactor


class HistoricalAnswerPresentationTests(unittest.TestCase):
    setUp = episode_tests.EpisodeRangeProgressionTests.setUp
    open_service = episode_tests.EpisodeRangeProgressionTests.open_service
    reopen = episode_tests.EpisodeRangeProgressionTests.reopen
    install_rollover = episode_tests.EpisodeRangeProgressionTests.install_rollover
    append_pairs = episode_tests.EpisodeRangeProgressionTests.append_pairs
    prepare_gap = episode_tests.EpisodeRangeProgressionTests.prepare_gap
    maintain = episode_tests.EpisodeRangeProgressionTests.maintain

    def prepare_history(self):
        self.prepare_gap()
        _, rollover = self.install_rollover(_DeterministicHistoricalCompactor())
        self.maintain(rollover)
        self.reopen()

    def test_current_emote_is_not_part_of_the_remembered_proposition(self):
        self.prepare_history()
        answer = '*nods warmly* I remember you saying, "' + self.event + '"'
        self.llm.response = answer
        before = len(self.llm.calls)
        result = self.service.process_text_turn(
            "Do you remember the violet meteor beside the lake?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(answer, result.reply)
        self.assertEqual(before + 1, len(self.llm.calls), "current presentation must not force repair")

    def test_emote_does_not_hide_an_unsupported_retrospective_claim(self):
        self.prepare_history()
        self.llm.response = ('*nods, remembering you told me about your silver telescope* '
                             'You said, "' + self.event + '"')
        result = self.service.process_text_turn(
            "Do you remember the violet meteor beside the lake?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertNotIn("telescope", result.reply)

    def test_place_fallback_is_short_attributed_history_after_failed_repair(self):
        self.prepare_history()
        self.llm.response = "I cannot supply that answer."  # One owned source despite toy background candidates.
        self.assertTrue(self.service.process_text_turn(
            "Do you remember the violet meteor beside the lake?", speak=False).succeeded)
        self.llm.response = "It was underneath the lake."
        before = len(self.llm.calls)
        result = self.service.process_text_turn("Which place was that?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual('You mentioned “beside the lake”.', result.reply)
        self.assertEqual(before + 2, len(self.llm.calls))
        self.assertEqual(result.reply, self.conversation.messages[-1]["content"])

    def test_typed_actions_are_checked_separately_without_losing_spoken_emphasis(self):
        from aifren.continuity.memory_v2_answer_governance import (
            MemoryAnswerEvidence, compose_memory_answer_requirement, validate_memory_answer_response,
        )
        source = MemoryAnswerEvidence("source", "historical_conversation_only", "user",
            "real_world", "assertion", "We watched a violet meteor beside the lake.")
        requirement = compose_memory_answer_requirement("Do you remember the violet meteor?", (source,))
        cases = (
            ('*nods warmly* You said we watched a **violet meteor** beside the lake.', True),
            ('You said *nods warmly* we watched a violet meteor beside the lake.', True),
            ('*nods warmly* You said we watched a violet meteor beside the castle.', False),
            ('*nods; you told me about your telescope* You said we watched a violet meteor beside the lake.', False),
            ('You told me about your **telescope**. *nods warmly*', False),
            ('Your telescope collection, I remember you telling me about it.', False),
        )
        for answer, expected in cases:
            with self.subTest(answer=answer):
                result = validate_memory_answer_response(requirement, answer)
                self.assertEqual(expected, result.accepted, result)

    def test_overlapping_other_speaker_source_cannot_reject_correct_attribution(self):
        from aifren.continuity.memory_v2_answer_governance import (
            MemoryAnswerEvidence, compose_memory_answer_requirement, validate_memory_answer_response,
        )
        user = MemoryAnswerEvidence("user-source", "historical_conversation_only", "user",
            "real_world", "assertion", "We watched a violet meteor beside the lake.")
        assistant = replace(user, evidence_id="assistant-source", speaker_role="assistant",
            source_text="That violet meteor sounds beautiful.")
        requirement = compose_memory_answer_requirement("Do you remember the violet meteor?", (user, assistant))
        result = validate_memory_answer_response(requirement, 'You said we watched a violet meteor beside the lake.')
        self.assertTrue(result.accepted, result)
        # Overlap is not evidence that the assistant made the user's statement.
        wrong = validate_memory_answer_response(requirement, 'I told you we watched a violet meteor beside the lake.')
        self.assertFalse(wrong.accepted)

    def test_real_retrieval_with_both_speakers_retains_the_user_owned_answer(self):
        self.llm.response = "That violet meteor sounds beautiful."
        self.prepare_history()
        answer = '*nods warmly* You said, "' + self.event + '"'
        self.llm.response = answer
        before = len(self.llm.calls)
        result = self.service.process_text_turn(
            "Do you remember the violet meteor beside the lake?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(answer, result.reply)
        self.assertEqual(before + 1, len(self.llm.calls))

    def test_historical_request_wrapper_cannot_make_an_unrelated_source_relevant(self):
        decoy = "Hello. Say one friendly sentence about a quiet afternoon."
        self.assertTrue(self.service.process_text_turn(decoy, speak=False).succeeded)
        self.append_pairs(16)
        self.service.maintain_canonical_observers()
        query = "What do you remember me saying about one of my Python projects?"
        # Broad semantic candidates are legitimate; their weak scores and
        # generic request words cannot establish agreement with the subject.
        def broad(*_a, **_kw):
            return [(row[0], .40) for row in self.h.writer.store.connection.execute(
                "SELECT claim_id FROM claims WHERE character_id=? AND claim_type='historical_evidence'",
                (self.h.character_id,))]
        with patch.object(self.authority.recall.semantic, "_semantic_rows", side_effect=broad):
            calls = len(self.llm.calls)
            result = self.service.process_text_turn(query, speak=False)
            self.assertTrue(result.succeeded, result.error)
            self.assertNotIn("friendly sentence", result.reply)

            self.assertEqual(calls, len(self.llm.calls), "healthy absence is provider-free")
            self.assertTrue(self.service._last_memory_authority_diagnostics["authoritative_no_evidence"])
            source = "I wrote a Python project that sorts my garden seed catalog."
            self.assertTrue(self.service.process_text_turn(source, speak=False).succeeded)
            self.append_pairs(16)
            self.service.maintain_canonical_observers()
            self.llm.response = 'You said, "' + source + '"'
            result = self.service.process_text_turn(query, speak=False)
            self.assertTrue(result.succeeded, result.error)
            self.assertEqual(self.llm.response, result.reply)
            self.assertNotIn("friendly sentence", result.reply)

    def test_historical_named_preference_preserves_the_requested_past_value(self):
        for text in ("My favorite color is teal.", "My favorite color is amber.",
                     "Actually, my favorite color is violet."):
            self.assertTrue(self.service.process_text_turn(text, speak=False).succeeded)
        self.append_pairs(16)
        self.service.maintain_canonical_observers()
        self.reopen()
        self.llm.response = "You said your favorite color was amber."
        result = self.service.process_text_turn(
            "What did I say about my favorite color being amber?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(self.llm.response, result.reply)
        self.llm.response = "Your favorite color is violet."
        current = self.service.process_text_turn("What is my favorite color now?", speak=False)
        self.assertTrue(current.succeeded, current.error)
        self.assertEqual(self.llm.response, current.reply)
