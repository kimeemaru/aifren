import unittest

from conversation.conversation import ContextManager, Conversation
from memory_v2_answer_governance import (
    MemoryAnswerEvidence,
    bind_memory_answer_source_containment,
    compose_memory_answer_requirement,
    validate_memory_answer_response,
)
from memory_v2_source_containment import (
    RECENT_CONVERSATION_BOUNDARY,
    RECENT_POLICY_MEMORY_CONTAINED,
    RECENT_POLICY_R0,
    RECENT_POLICY_R1,
    RECENT_POLICY_R2,
    select_recent_messages,
)


class MemoryV2SourceContainmentTests(unittest.TestCase):
    def setUp(self):
        self.messages = (
            {"role": "user", "content": "Did I own a motorcycle?", "timestamp": "t1"},
            {"role": "assistant", "content": "You owned a Harley-Davidson.", "timestamp": "t2"},
            {"role": "user", "content": "What did I say about projects?", "timestamp": "t3"},
            {"role": "assistant", "content": "You finished the Python refactor.", "timestamp": "t4"},
            {"role": "user", "content": "Do you remember when I booped you?", "timestamp": "t5"},
        )

    def test_r0_is_bounded_role_balanced_tail_without_mutation(self):
        before = tuple(dict(item) for item in self.messages)
        selected = select_recent_messages(
            self.messages, RECENT_POLICY_R0, maximum_messages=4,
        )
        self.assertEqual(("assistant", "user", "assistant", "user"), tuple(
            item["role"] for item in selected
        ))
        selected[0]["content"] = "changed copy"
        self.assertEqual(before, self.messages)

    def test_r1_is_user_led_and_keeps_only_needed_immediate_assistant(self):
        ordinary = (*self.messages[:-1], {
            "role": "user", "content": "Tell me something cheerful.", "timestamp": "t5",
        })
        selected = select_recent_messages(
            ordinary, RECENT_POLICY_R1, maximum_messages=12,
        )
        self.assertTrue(all(item["role"] == "user" for item in selected))
        followup = (*self.messages[:-1], {
            "role": "user", "content": "What else was connected to that?", "timestamp": "t5",
        })
        selected = select_recent_messages(
            followup, RECENT_POLICY_R1, maximum_messages=12,
        )
        assistants = tuple(item for item in selected if item["role"] == "assistant")
        self.assertEqual(1, len(assistants))
        self.assertEqual("t4", assistants[0]["timestamp"])
        self.assertEqual("t5", selected[-1]["timestamp"])

    def test_memory_contained_policy_keeps_only_current_memory_query(self):
        selected = select_recent_messages(
            self.messages, RECENT_POLICY_MEMORY_CONTAINED, maximum_messages=12,
        )
        self.assertEqual(1, len(selected))
        self.assertEqual("t5", selected[0]["timestamp"])

    def test_memory_contained_policy_preserves_balanced_ordinary_continuity(self):
        ordinary = (*self.messages, {
            "role": "assistant", "content": "I remember the boop.", "timestamp": "t6",
        }, {
            "role": "user", "content": "Tell me something cheerful.", "timestamp": "t7",
        })
        selected = select_recent_messages(
            ordinary, RECENT_POLICY_MEMORY_CONTAINED, maximum_messages=12,
        )
        self.assertIn("assistant", {item["role"] for item in selected})
        self.assertEqual("t7", selected[-1]["timestamp"])

    def test_r2_preserves_only_six_role_balanced_messages(self):
        messages = tuple({
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message {index}", "timestamp": f"t{index}",
        } for index in range(20))
        selected = select_recent_messages(
            messages, RECENT_POLICY_R2, maximum_messages=12,
        )
        self.assertEqual(6, len(selected))
        self.assertEqual("t14", selected[0]["timestamp"])
        self.assertEqual("t19", selected[-1]["timestamp"])

    def test_unknown_policy_fails_closed(self):
        with self.assertRaises(ValueError):
            select_recent_messages(self.messages, "unbounded")

    def test_grounded_requirement_rejects_recent_only_named_detail(self):
        requirement = compose_memory_answer_requirement(
            "Do you remember when I booped you?",
            (MemoryAnswerEvidence(
                "record-boop", "historical_conversation_only", "user",
                "unknown_scope", "assertion", "boop",
            ),),
        )
        bound = bind_memory_answer_source_containment(
            requirement, "Do you remember when I booped you?", self.messages,
        )
        self.assertIn("harley-davidson", bound.recent_context_only_anchors)
        self.assertIn("python", bound.recent_context_only_anchors)
        clean = validate_memory_answer_response(bound, "Yes, I remember that boop.")
        contaminated = validate_memory_answer_response(
            bound, "Yes, I remember that boop and your Harley-Davidson.",
        )
        self.assertTrue(clean.accepted, clean)
        self.assertIn("unsupported_recent_memory_detail", contaminated.violations)

    def test_admitted_and_current_query_details_are_not_withheld(self):
        requirement = compose_memory_answer_requirement(
            "What did you tell me about Game Boy?",
            (MemoryAnswerEvidence(
                "record-game", "historical_conversation_only", "assistant",
                "unknown_scope", "assertion", "I mentioned a Game Boy story.",
            ),),
        )
        recent = (
            {"role": "assistant", "content": "I mentioned a Game Boy story."},
            {"role": "user", "content": "What did you tell me about Game Boy?"},
        )
        bound = bind_memory_answer_source_containment(requirement, recent[-1]["content"], recent)
        self.assertNotIn("game boy", bound.recent_context_only_anchors)

    def test_no_evidence_requirement_gains_only_bounded_turn_local_support(self):
        requirement = compose_memory_answer_requirement(
            "Which motorcycle did I say I owned?", (),
        )
        bound = bind_memory_answer_source_containment(
            requirement, "Which motorcycle did I say I owned?", self.messages,
        )
        self.assertEqual(requirement.evidence_state, bound.evidence_state)
        self.assertEqual(requirement.requested_relation, bound.requested_relation)
        self.assertEqual(requirement.fallback_dialogue, bound.fallback_dialogue)
        self.assertLessEqual(len(bound.support_ledger), 24)

    def test_v2_context_labels_recent_dialogue_and_keeps_current_turn(self):
        conversation = Conversation.__new__(Conversation)
        conversation.messages = [dict(item) for item in self.messages]
        conversation.summary_data = {"summary": "", "summarized_messages": 0}
        conversation.context_manager = ContextManager(max_recent_chars=50000)
        conversation.context_hygiene = None
        conversation.episode_compaction_cache = None
        conversation.episode_compaction_rollover = None
        conversation._clock = None
        context = conversation.build_context(
            type("Memory", (), {"retrieve_memories": lambda *_args, **_kwargs: []})(),
            self.messages[-1]["content"],
            long_term_memory_authority="v2",
            recent_context_policy=RECENT_POLICY_R1,
            recent_message_limit=12,
            recent_character_limit=12000,
        )
        rendered = "\n".join(str(item.get("content", "")) for item in context)
        self.assertIn(RECENT_CONVERSATION_BOUNDARY, rendered)
        self.assertIn(self.messages[-1]["content"], rendered)
        self.assertNotIn("Harley-Davidson", rendered)
        self.assertNotIn("Python refactor", rendered)


if __name__ == "__main__":
    unittest.main()
