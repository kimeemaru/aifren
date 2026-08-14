import unittest

from assistant import generate_response
from conversation.conversation import ContextManager, Conversation


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

        context = conversation.build_context(memory, "What do I like?")

        self.assertEqual(memory.calls, [("What do I like?", 5)])
        self.assertEqual(len(context), 4)
        self.assertIn("LONG-TERM CONVERSATION BACKGROUND", context[0]["content"])
        self.assertIn("AUTHORITATIVE LIFELONG MEMORIES", context[1]["content"])
        self.assertEqual(context[2], conversation.messages[0])
        self.assertEqual(context[3], conversation.messages[1])
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
        self.assertIn("AUTHORITATIVE LIFELONG MEMORIES", llm.context[1]["content"])


if __name__ == "__main__":
    unittest.main()
