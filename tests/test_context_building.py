from pathlib import Path
import tempfile
import unittest
import uuid

from aifren.assistant import generate_response
from aifren.runtime.config import RECENT_CONTEXT_MAX_CHARS, RECENT_CONTEXT_MAX_MESSAGES
from aifren.conversation.conversation import ContextManager, Conversation
from aifren.llm.openai_compatible import OpenAICompatibleLLM


class FakeMemory:
    def __init__(self):
        self.calls = []

    def get_relevant_memories(self, user_message, max_memories):
        self.calls.append((user_message, max_memories))
        return [{"category": "preference", "content": "Likes pineapples."}]


class FakeLLM:
    def __init__(self):
        self.context = None
        self.character_prompt = None

    def generate(self, context, character_prompt):
        self.context = context
        self.character_prompt = character_prompt
        return "Hello!"


class ContextBuildingTests(unittest.TestCase):
    def make_conversation(self):
        conversation = Conversation.__new__(Conversation)
        conversation.summary_data = {"summary": "The user likes games."}
        conversation.messages = [
            {"role": "user", "content": "Earlier message", "timestamp": "t1"},
            {"role": "assistant", "content": "Earlier reply", "timestamp": "t2"},
        ]
        conversation.context_manager = ContextManager()
        return conversation

    def test_conversation_preserves_active_context_payload_and_order(self):
        conversation = self.make_conversation()
        memory = FakeMemory()

        context = conversation.build_context(
            memory, "What do I like?",
            admitted_active_state_context="[typed active state]",
            admitted_durable_context="[typed durable fact]",
        )

        self.assertEqual(memory.calls, [("What do I like?", 5)])
        self.assertEqual(len(context), 7)
        self.assertIn("[Current turn temporal facts]", context[0]["content"])
        self.assertEqual("[typed active state]", context[1]["content"])
        self.assertIn("AUTHORITATIVE LIFELONG MEMORIES", context[2]["content"])
        self.assertEqual("[typed durable fact]", context[3]["content"])
        self.assertIn("LONG-TERM CONVERSATION BACKGROUND", context[4]["content"])
        self.assertEqual(context[5], conversation.messages[0])
        self.assertEqual(context[6], conversation.messages[1])
        self.assertNotIn("CURRENT AUTHORITATIVE", "\n".join(
            item["content"] for item in context
        ))

    def test_assistant_delegates_context_building_to_conversation(self):
        conversation = self.make_conversation()
        memory = FakeMemory()
        llm = FakeLLM()

        reply = generate_response(
            llm,
            conversation,
            memory,
            "What do I like?",
            "character prompt",
        )

        self.assertEqual(reply, "Hello!")
        self.assertEqual(llm.character_prompt, "character prompt")
        self.assertEqual(memory.calls, [("What do I like?", 5)])
        self.assertIn("[Current turn temporal facts]", llm.context[0]["content"])
        self.assertTrue(any("AUTHORITATIVE LIFELONG MEMORIES" in item["content"] for item in llm.context))

    def test_generous_recent_window_keeps_one_hundred_messages_in_order(self):
        conversation = self.make_conversation()
        conversation.messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index:03d}", "timestamp": str(index)}
            for index in range(RECENT_CONTEXT_MAX_MESSAGES)
        ]
        recent = conversation.get_recent_messages()
        self.assertEqual(RECENT_CONTEXT_MAX_MESSAGES, len(recent))
        self.assertEqual([f"message-{index:03d}" for index in range(RECENT_CONTEXT_MAX_MESSAGES)],
                         [item["content"] for item in recent])
        self.assertEqual("message-099", recent[-1]["content"])
        self.assertLess(sum(len(item["content"]) for item in recent), RECENT_CONTEXT_MAX_CHARS)

    def test_character_budget_trims_a_contiguous_oldest_first_suffix_without_truncating_messages(self):
        conversation = self.make_conversation()
        conversation.context_manager = ContextManager(max_recent_chars=25)
        conversation.messages = [
            {"role": "user", "content": f"full-message-{index}", "timestamp": str(index)}
            for index in range(5)
        ]
        recent = conversation.get_recent_messages()
        self.assertEqual(["full-message-4"], [item["content"] for item in recent])
        self.assertEqual("full-message-4", recent[-1]["content"])

        conversation.messages[-1]["content"] = "x" * 30
        recent = conversation.get_recent_messages()
        self.assertEqual(["x" * 30], [item["content"] for item in recent])

    def test_restart_preserves_recent_suffix_and_rolling_summary(self):
        with tempfile.TemporaryDirectory() as root:
            conversation_file = Path(root) / "conversation.json"
            summary_file = Path(root) / "conversation_summary.json"
            original = Conversation(FakeLLM(), conversation_file=conversation_file, summary_file=summary_file)
            original.messages = [
                {"role": "user", "content": f"persisted-{index}", "timestamp": str(index)}
                for index in range(RECENT_CONTEXT_MAX_MESSAGES + 3)
            ]
            original.summary_data = {"summary": "Older derived continuity.", "summarized_messages": 3}
            original.save()
            original.save_summary()
            restored = Conversation(FakeLLM(), conversation_file=conversation_file, summary_file=summary_file)
            self.assertEqual("Older derived continuity.", restored.summary_data["summary"])
            self.assertEqual(RECENT_CONTEXT_MAX_MESSAGES, len(restored.get_recent_messages()))
            self.assertEqual("persisted-3", restored.get_recent_messages()[0]["content"])

    def test_summary_boundary_stops_before_the_actual_character_bounded_raw_suffix(self):
        with tempfile.TemporaryDirectory() as root:
            llm = FakeLLM()
            conversation = Conversation(
                llm, conversation_file=Path(root) / "conversation.json", summary_file=Path(root) / "summary.json",
            )
            conversation.context_manager = ContextManager(max_recent_chars=25)
            conversation.messages = [
                {"role": "user", "content": f"summary-source-{index}", "timestamp": str(index)}
                for index in range(21)
            ]
            conversation.update_summary()
            self.assertEqual(20, conversation.summary_data["summarized_messages"])
            self.assertEqual(["summary-source-20"], [item["content"] for item in conversation.get_recent_messages()])
            self.assertNotIn("summary-source-20", llm.character_prompt)

    def test_provider_capacity_trims_only_oldest_raw_messages_and_keeps_admitted_state_and_latest_turn(self):
        conversation = self.make_conversation()
        conversation.messages = [
            {"role": "user", "content": f"old-{index}-" + "x" * 30, "timestamp": str(index)}
            for index in range(4)
        ]
        context = conversation.build_context(
            FakeMemory(), "latest",
            admitted_active_state_context="[current governed state]",
            admitted_durable_context="[durable fact]",
            max_context_chars=230,
        )
        joined = "\n".join(item["content"] for item in context)
        self.assertIn("[current governed state]", joined)
        self.assertIn("[durable fact]", joined)
        self.assertIn("old-3-", joined)
        self.assertNotIn("old-0-", joined)

    def test_recent_raw_history_keeps_legacy_and_exact_scope_without_orphaning_pairs(self):
        conversation = self.make_conversation()
        real_id = "scope-" + str(uuid.uuid4())
        scenario_id = "scope-" + str(uuid.uuid4())
        other_id = "scope-" + str(uuid.uuid4())

        def pair(prefix, scope=None):
            values = [
                {"role": "user", "content": prefix + " user", "timestamp": prefix + "-u"},
                {"role": "assistant", "content": prefix + " assistant", "timestamp": prefix + "-a"},
            ]
            if scope is not None:
                for value in values:
                    value["truth_scope"] = dict(scope)
            return values

        conversation.messages = (
            pair("legacy")
            + pair("scenario-a", {"kind": "scenario", "scope_id": scenario_id})
            + pair("scenario-b", {"kind": "scenario", "scope_id": other_id})
            + pair("real", {"kind": "real_world", "scope_id": real_id})
        )
        conversation.summary_data = {
            "summary": "scenario-a must not survive through the unscoped rolling summary",
            "summarized_messages": 0,
        }
        local_context = conversation.build_context(
            FakeMemory(), "real user",
            active_truth_scope={"kind": "real_world", "scope_id": real_id},
        )
        online_context = conversation.build_context(
            FakeMemory(), "real user",
            active_truth_scope={"kind": "real_world", "scope_id": real_id},
        )
        self.assertEqual(local_context, online_context)
        contents = [item["content"] for item in local_context]
        self.assertIn("legacy user", contents)
        self.assertIn("legacy assistant", contents)
        self.assertIn("real user", contents)
        self.assertIn("real assistant", contents)
        self.assertFalse(any("scenario-a" in item or "scenario-b" in item for item in contents))

        provider = OpenAICompatibleLLM(api_key="synthetic", base_url="http://127.0.0.1:9/v1", model="synthetic")
        self.addCleanup(provider.client.close)
        provider_messages = provider._messages(local_context, "character")
        self.assertTrue(all(set(item) == {"role", "content"} for item in provider_messages))
        self.assertNotIn("scope-", str(provider_messages))


if __name__ == "__main__":
    unittest.main()
