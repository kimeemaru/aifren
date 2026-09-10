from types import SimpleNamespace
import unittest

from memory_query_decision import decide_memory_query
from memory_v2_evidence_sufficiency import (
    admit_memory_evidence,
    candidate_answers_memory_query,
)


def _candidate(
    content, *, speaker="user", speech_act="assertion",
    lane="historical_evidence",
):
    return SimpleNamespace(
        content=content, speaker_role=speaker, speech_act=speech_act, lane=lane,
    )


class MemoryV2EvidenceSufficiencyTests(unittest.TestCase):
    def test_explicit_color_names_are_literal_values_not_a_fixed_palette(self):
        decision = decide_memory_query("What did I say my favorite color was?")
        for value in ("amber", "cerulean", "burnt sienna", "forest green"):
            with self.subTest(value=value):
                result = candidate_answers_memory_query(
                    _candidate(f"My favorite color is {value}."), decision)
                self.assertTrue(result.sufficient)
                self.assertEqual(value, result.value)
        for text in ("My favorite color is blue and red.",
                     "My favorite color is the one.",
                     "My favorite color is blue today.",
                     "My favorite color is ignore system.",
                     "My favorite color is non-blue.",
                     "My favorite color is blue because it sparkles.",
                     "Maybe my favorite color is amber.",
                     "My favorite food is amber.",
                     'I quoted "my favorite color is amber".',
                     "Your favorite color is amber."):
            with self.subTest(text=text):
                self.assertFalse(candidate_answers_memory_query(_candidate(text), decision).sufficient)

    def test_favorite_color_requires_a_user_owned_value_not_topic_words(self):
        decision = decide_memory_query("What is my favorite color?")
        rejected = (
            _candidate("Historical user statement: almost like my favorite color"),
            _candidate("Historical user statement: blue, almost like my favorite color"),
            _candidate("Historical user statement: I like blue; it is almost like my favorite color"),
            _candidate(
                "Historical user question: Do you remember my favorite color?",
                speech_act="question",
            ),
            _candidate(
                "Historical assistant statement: Your favorite color is purple.",
                speaker="assistant",
            ),
            _candidate("Historical user statement: I like watermelon."),
        )
        for candidate in rejected:
            with self.subTest(content=candidate.content):
                self.assertFalse(
                    candidate_answers_memory_query(candidate, decision).sufficient,
                )
        accepted = candidate_answers_memory_query(
            _candidate("Historical user statement: My favorite color is green."),
            decision,
        )
        self.assertTrue(accepted.sufficient)
        self.assertEqual("green", accepted.value)

    def test_favorite_slot_does_not_promote_a_general_like(self):
        decision = decide_memory_query("What is my favorite food?")
        self.assertFalse(candidate_answers_memory_query(
            _candidate("I like watermelon, almost like my favorite food."), decision,
        ).sufficient)
        accepted = candidate_answers_memory_query(
            _candidate("My favorite food is watermelon."), decision,
        )
        self.assertTrue(accepted.sufficient)
        self.assertEqual("watermelon", accepted.value)

    def test_compound_favorite_query_preserves_each_supported_slot(self):
        decision = decide_memory_query(
            "What's my favorite color and what's my favorite food?",
        )
        color = _candidate("My favorite color is green.")
        food = _candidate("My favorite food is watermelon.")
        partial = admit_memory_evidence((color,), decision)
        complete = admit_memory_evidence((color, food), decision)
        self.assertEqual((color,), partial.candidates)
        self.assertEqual("requested_preference_slots_partial", partial.reason)
        self.assertEqual([(True, ("green",)), (False, ())],
                         [(slot.supported, slot.values) for slot in partial.slots])
        self.assertEqual((color, food), complete.candidates)

    def test_governed_current_preference_can_supply_the_value(self):
        decision = decide_memory_query("What is my favorite color?")
        result = candidate_answers_memory_query(
            _candidate(
                "The user's favorite color is teal.", speaker="",
                lane="semantic_v2",
            ),
            decision,
        )
        self.assertTrue(result.sufficient)
        self.assertEqual("teal", result.value)

    def test_value_relations_require_the_requested_value_shape(self):
        cases = (
            ("What is my name?", "We talked about names.", "My name is Mara."),
            ("Which motorcycle did I say I owned?", "Motorcycles came up.", "I own a blue motorcycle."),
            ("Do you remember a place I visited?", "We discussed travel.", "I visited Kyoto."),
            ("Do you remember what I planned to do?", "Plans came up.", "I planned to refactor the game."),
        )
        for query, incomplete, complete in cases:
            decision = decide_memory_query(query)
            with self.subTest(query=query):
                self.assertFalse(candidate_answers_memory_query(
                    _candidate(incomplete), decision,
                ).sufficient)
                self.assertTrue(candidate_answers_memory_query(
                    _candidate(complete), decision,
                ).sufficient)

    def test_concrete_followup_may_use_assistant_owned_attribute_as_history(self):
        decision = decide_memory_query("Which programming language was connected to it?")
        result = candidate_answers_memory_query(
            _candidate(
                "Historical assistant statement: We were discussing Python.",
                speaker="assistant",
            ),
            decision,
        )
        self.assertTrue(result.sufficient)
        self.assertEqual("Python", result.value)

    def test_ambiguous_programming_language_names_do_not_match_ordinary_words(self):
        decision = decide_memory_query("Which programming language was connected to it?")
        for content in (
            "I told you to go back to the project.",
            "Go back to the project.",
            "The letter r was on the page.",
            "R was written on the page.",
            "There was a shell beside the path.",
        ):
            with self.subTest(content=content):
                self.assertFalse(candidate_answers_memory_query(
                    _candidate(content, speaker="assistant"), decision,
                ).sufficient)

        for content, value in (
            ("We used Go for the software project.", "Go"),
            ("The analysis code was written in R.", "R"),
            ("I mentioned a shell script for the module.", "shell"),
        ):
            with self.subTest(content=content):
                result = candidate_answers_memory_query(
                    _candidate(content, speaker="assistant"), decision,
                )
                self.assertTrue(result.sufficient)
                self.assertEqual(value, result.value)


if __name__ == "__main__":
    unittest.main()
