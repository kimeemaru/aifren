"""Temporal operators are not the head noun of a favorite-property slot."""
import unittest

from aifren.continuity.memory_query_decision import decide_memory_query


class TemporalPreferenceSlotTests(unittest.TestCase):
    def test_predicative_favorite_before_and_after_keep_the_requested_property(self):
        for direction in ("before", "after"):
            with self.subTest(direction=direction):
                query = f"What color did I say was my favorite {direction} I changed it to blue?"
                decision = decide_memory_query(query)
                self.assertEqual(("color",), decision.retrieval_slots)
                self.assertEqual("color", decision.source_order.slot.key)
                self.assertEqual(direction, decision.source_order.direction)
                self.assertEqual("blue", decision.source_order.anchor_value)
                self.assertEqual("user", decision.source_order.anchor_speaker)

    def test_missing_property_stays_unresolved_instead_of_guessing_from_anchor(self):
        for direction in ("before", "after"):
            with self.subTest(direction=direction):
                decision = decide_memory_query(
                    f"What did I say was my favorite {direction} I changed it to blue?")
                self.assertEqual((), decision.retrieval_slots)
                self.assertIsNone(decision.source_order.slot)

    def test_named_property_and_wrong_speaker_keep_existing_typed_semantics(self):
        decision = decide_memory_query(
            "What favorite instrument did I say before I changed it to cello?")
        self.assertEqual(("instrument",), decision.retrieval_slots)
        self.assertEqual("instrument", decision.source_order.slot.key)
        wrong_owner = decide_memory_query(
            "What color did I say was my favorite before you changed it to blue?")
        self.assertEqual("user", wrong_owner.requested_speaker)
        self.assertEqual("assistant", wrong_owner.source_order.anchor_speaker)
        self.assertIsNone(wrong_owner.source_order.slot)


if __name__ == "__main__":
    unittest.main()
