"""A delivery preference survives an already-owned, non-format state update."""
import json
from dataclasses import replace
import unittest
from unittest.mock import patch

from assistant import build_character_prompt
from conversation_style import NATURAL_POLICY
import test_v2_runtime_recovery as recovery


class NaturalDeliveryRoutingTests(unittest.TestCase):
    setUp = recovery.V2RuntimeRecoveryTests.setUp
    open_service = recovery.V2RuntimeRecoveryTests.open_service

    def test_applied_user_activity_keeps_natural_delivery_for_ordinary_explanation(self):
        self.llm.local_presentation = True
        self.llm.local_ordinary_dialogue = False
        self.service.conversation_style = "natural"
        self.service.explicit_avatar_cues = False
        personality = "A thoughtful companion with dry wit who can explain practical ideas."
        self.service.character_prompt = build_character_prompt({"name": "Mira"}, personality)
        original_prompt = self.service.character_prompt
        query = ("I am growing basil and thyme in two balcony pots. "
                 "Explain a practical watering routine and why drainage matters.")
        self.llm.response = ("Check whether the top of the soil is dry before watering. "
                             "Drainage lets excess water escape.")
        captured = []
        original_parts = self.service._context_prompt_parts

        def parts(policy, companion_context):
            captured.append(policy)
            return original_parts(policy, companion_context)

        with patch.object(self.service, "_context_prompt_parts", side_effect=parts):
            result = self.service.process_text_turn(query, speak=False)

        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(self.llm.response, result.reply)
        self.assertEqual(self.llm.response, result.spoken_text)
        self.assertEqual(1, len(captured))
        policy = captured[0]
        self.assertTrue(policy.changed_by_current_evidence)
        self.assertTrue(policy.enforce_before_presentation)
        self.assertIsNone(policy.requirement)
        self.assertIsNone(policy.action_plan)
        self.assertIsNone(policy.action_decision_category)
        self.assertFalse(policy.memory_query_decision.applicable)
        self.assertFalse(policy.memory_answer_requirement.triggered)
        self.assertEqual("normal", policy.effects.speech_mode)
        self.assertEqual("normal", policy.effects.awareness_mode)
        self.assertEqual("free", policy.effects.hands_mode)
        self.assertEqual("walking", policy.effects.locomotion_mode)
        self.assertTrue(all(policy.effects.perception_mode(name) == "normal"
                            for name in ("vision", "hearing", "smell", "taste", "touch")))
        activity = self.h.repository.lookup_actor_state(self.h.character_id, "user", "activity")
        self.assertIn("growing basil and thyme in two balcony pots", str(activity.state))
        self.assertEqual(original_prompt, self.service.character_prompt)
        canonical = json.loads(self.h.conversation_file.read_text())
        self.assertEqual(query, canonical[-2]["content"])
        self.assertEqual(self.llm.response, canonical[-1]["content"])
        self.assertEqual(0, self.service.memory.retrieval_calls)
        self.assertEqual([], self.service.memory.processed)
        self.assertEqual(1, len(self.llm.calls), "No style-repair inference")
        prompt = self.llm.calls[0][1]
        self.assertIn(personality, prompt)
        self.assertEqual(1, prompt.count(NATURAL_POLICY),
                         "A completed user-activity update is not a machine-format obligation")
        self.assertNotIn("AUTHORITATIVE RESPONSE FORMAT:", prompt)
        self.assertNotIn(NATURAL_POLICY, str(canonical))
        self.assertFalse(self.service._ordinary_text_eligible(policy))
        self.assertTrue(self.service._ordinary_text_eligible(
            policy, allow_completed_state_update=True))
        for blocked in (
            replace(policy, requirement=object()),
            replace(policy, action_plan=object()),
            replace(policy, authoritative_memory_response="owned memory reply"),
            replace(policy, effects=replace(policy.effects, speech_mode="constrained")),
            replace(policy, effects=replace(policy.effects, awareness_mode="asleep")),
        ):
            with self.subTest(blocked=blocked):
                self.assertFalse(self.service._ordinary_text_eligible(
                    blocked, allow_completed_state_update=True))
                self.assertNotIn(NATURAL_POLICY, self.service._response_character_prompt(
                    ordinary_policy=blocked))
        self.service.explicit_avatar_cues = True
        self.llm.local_ordinary_dialogue = True
        self.assertFalse(self.service._act_eligible(policy))
        self.assertFalse(self.service._lean_ordinary_eligible(policy))


if __name__ == "__main__":
    unittest.main()
