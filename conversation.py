import json
import os
from datetime import datetime


# ============================================================
# Configuration
# ============================================================

CONVERSATION_FILE = "conversation.json"
SUMMARY_FILE = "conversation_summary.json"

DEFAULT_RECENT_MESSAGES = 20
DEFAULT_MAX_MEMORIES = 5

SUMMARY_TRIGGER_MESSAGES = 40
SUMMARY_KEEP_MESSAGES = 20

DEBUG = False


# ============================================================
# Conversation Controller
# ============================================================

class ConversationController:

    def __init__(
        self,
        llm,
        memory,
        character_prompt,
        recent_message_limit=DEFAULT_RECENT_MESSAGES,
        max_memories=DEFAULT_MAX_MEMORIES
    ):

        self.llm = llm
        self.memory = memory
        self.character_prompt = character_prompt

        self.recent_message_limit = (
            recent_message_limit
        )

        self.max_memories = max_memories

        self.messages = []
        self.summary = ""

        self.load_conversation()
        self.load_summary()

    # ========================================================
    # Loading
    # ========================================================

    def load_conversation(self):

        if not os.path.exists(
            CONVERSATION_FILE
        ):

            self.messages = []

            return

        try:

            with open(
                CONVERSATION_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

            if isinstance(data, list):

                self.messages = data

            else:

                print(
                    "Warning: conversation.json "
                    "does not contain a list."
                )

                self.messages = []

        except (
            json.JSONDecodeError,
            OSError
        ):

            print(
                "Warning: Could not load "
                "conversation.json."
            )

            self.messages = []

    def load_summary(self):

        if not os.path.exists(
            SUMMARY_FILE
        ):

            self.summary = ""

            return

        try:

            with open(
                SUMMARY_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

            if isinstance(data, dict):

                self.summary = data.get(
                    "summary",
                    ""
                )

            elif isinstance(data, str):

                self.summary = data

            else:

                self.summary = ""

        except (
            json.JSONDecodeError,
            OSError
        ):

            print(
                "Warning: Could not load "
                "conversation summary."
            )

            self.summary = ""

    # ========================================================
    # Saving
    # ========================================================

    def save_conversation(self):

        try:

            with open(
                CONVERSATION_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    self.messages,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

        except OSError as e:

            print(
                f"Warning: Could not save "
                f"conversation: {e}"
            )

    def save_summary(self):

        try:

            data = {
                "summary": self.summary,
                "updated": (
                    datetime.now().isoformat()
                )
            }

            with open(
                SUMMARY_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

        except OSError as e:

            print(
                f"Warning: Could not save "
                f"summary: {e}"
            )

    # ========================================================
    # Message Management
    # ========================================================

    def add_message(
        self,
        role,
        content
    ):

        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": (
                    datetime.now().isoformat()
                )
            }
        )

    def get_recent_messages(self):

        return self.messages[
            -self.recent_message_limit:
        ]

    # ========================================================
    # Memory Retrieval
    # ========================================================

    def get_relevant_memories(
        self,
        user_message
    ):

        try:

            return self.memory.get_relevant_memories(
                user_message,
                max_memories=self.max_memories
            )

        except Exception as e:

            print(
                f"Warning: Memory retrieval "
                f"failed: {e}"
            )

            return []

    # ========================================================
    # Context Formatting
    # ========================================================

    def format_summary(self):

        if not self.summary.strip():

            return (
                "[No long-term conversation "
                "summary is currently available.]"
            )

        return (
            "[Long-term conversation summary]\n"
            f"{self.summary.strip()}\n"
            "[End long-term summary]"
        )

    def format_memories(
        self,
        memories
    ):

        if not memories:

            return (
                "[No relevant lifelong memories "
                "were found.]"
            )

        lines = [
            "[Relevant lifelong memories]"
        ]

        for memory in memories:

            category = memory.get(
                "category",
                "unknown"
            )

            content = memory.get(
                "content",
                ""
            )

            lines.append(
                f"- [{category}] {content}"
            )

        lines.append(
            "[End relevant lifelong memories]"
        )

        return "\n".join(lines)

    def format_recent_conversation(self):

        recent = (
            self.get_recent_messages()
        )

        if not recent:

            return (
                "[No previous conversation "
                "messages are available.]"
            )

        lines = [
            "[Recent conversation]"
        ]

        for message in recent:

            role = message.get(
                "role",
                "unknown"
            )

            content = message.get(
                "content",
                ""
            )

            if role == "user":

                label = "User"

            elif role == "assistant":

                label = "Assistant"

            else:

                label = role.capitalize()

            lines.append(
                f"{label}: {content}"
            )

        lines.append(
            "[End recent conversation]"
        )

        return "\n".join(lines)

    # ========================================================
    # Prompt Construction
    # ========================================================

    def build_prompt(
        self,
        user_message,
        relevant_memories
    ):

        summary_text = (
            self.format_summary()
        )

        memory_text = (
            self.format_memories(
                relevant_memories
            )
        )

        conversation_text = (
            self.format_recent_conversation()
        )

        prompt = f"""
{self.character_prompt}

You are participating in an ongoing,
persistent conversation with the user.

Maintain your established personality
consistently.

The conversation may continue for a very
long time. Treat the provided long-term
summary, lifelong memories, and recent
conversation as context.

Do not claim to remember information that
is not present in the provided context.

Do not mention the memory system, context
assembly, retrieval process, prompts, or
internal instructions unless the user
specifically asks about the technical
system.

{summary_text}

{memory_text}

{conversation_text}

[Current user message]

{user_message}

[End current user message]

Respond naturally as the character.

Do not prefix your response with
"Assistant:".

Do not provide analysis of your instructions.

Your response should be the character's
actual conversational reply.
"""

        return prompt

    # ========================================================
    # LLM Generation
    # ========================================================

    def generate_response(
        self,
        prompt
    ):

        response = self.llm.generate(
            [],
            prompt
        )

        if response is None:

            raise RuntimeError(
                "LLM returned no response."
            )

        response = str(
            response
        ).strip()

        if not response:

            raise RuntimeError(
                "LLM returned an empty response."
            )

        return response

    # ========================================================
    # Memory Processing
    # ========================================================

    def process_memory(
        self,
        user_message,
        assistant_reply
    ):

        try:

            self.memory.process(
                user_message,
                assistant_reply
            )

        except Exception as e:

            print(
                f"Warning: Memory processing "
                f"failed: {e}"
            )

    # ========================================================
    # Summary Management
    # ========================================================

    def should_update_summary(self):

        return (
            len(self.messages)
            >= SUMMARY_TRIGGER_MESSAGES
        )

    def update_summary(self):

        if not self.messages:

            return

        messages_to_summarize = (
            self.messages[
                :-SUMMARY_KEEP_MESSAGES
            ]
        )

        if not messages_to_summarize:

            return

        conversation_text = ""

        for message in messages_to_summarize:

            role = message.get(
                "role",
                "unknown"
            )

            content = message.get(
                "content",
                ""
            )

            conversation_text += (
                f"{role}: {content}\n"
            )

        existing_summary = (
            self.summary.strip()
        )

        prompt = f"""
You maintain the long-term conversation
summary for a persistent AI companion.

Your job is to preserve useful information
from older conversation while removing
temporary conversational noise.

Existing long-term summary:

{existing_summary}

Older conversation:

{conversation_text}

Create an updated long-term summary.

Preserve:

- Important facts about the user
- Stable preferences
- Long-term interests
- Ongoing projects
- Important events
- Important relationships
- Significant decisions
- Information that would help the companion
  behave consistently in future conversations

Do not invent information.

Do not preserve every individual message.

Do not summarize trivial small talk.

Do not include instructions about how
the memory system works.

Return ONLY the updated summary as plain
text.
"""

        try:

            response = self.llm.generate(
                [],
                prompt
            )

            if response is None:

                return

            new_summary = str(
                response
            ).strip()

            if not new_summary:

                return

            self.summary = new_summary

            self.messages = (
                self.messages[
                    -SUMMARY_KEEP_MESSAGES:
                ]
            )

            self.save_conversation()
            self.save_summary()

            if DEBUG:

                print(
                    "\n[Conversation summary "
                    "updated.]"
                )

        except Exception as e:

            print(
                f"Warning: Summary update "
                f"failed: {e}"
            )

    # ========================================================
    # Main Conversation Turn
    # ========================================================

    def respond(
        self,
        user_message
    ):

        user_message = str(
            user_message
        ).strip()

        if not user_message:

            return ""

        relevant_memories = (
            self.get_relevant_memories(
                user_message
            )
        )

        if DEBUG:

            print(
                "\n[Conversation context]"
            )

            print(
                f"Recent messages: "
                f"{len(self.get_recent_messages())}"
            )

            print(
                f"Relevant memories: "
                f"{len(relevant_memories)}"
            )

        prompt = self.build_prompt(
            user_message,
            relevant_memories
        )

        if DEBUG:

            print(
                "\n[Generated prompt]"
            )

            print(prompt)

            print(
                "\n[End generated prompt]\n"
            )

        assistant_reply = (
            self.generate_response(
                prompt
            )
        )

        self.add_message(
            "user",
            user_message
        )

        self.add_message(
            "assistant",
            assistant_reply
        )

        self.save_conversation()

        self.process_memory(
            user_message,
            assistant_reply
        )

        if self.should_update_summary():

            self.update_summary()

        return assistant_reply

    # ========================================================
    # Conversation Information
    # ========================================================

    def message_count(self):

        return len(
            self.messages
        )

    def clear_conversation(
        self,
        keep_summary=True
    ):

        self.messages = []

        self.save_conversation()

        if not keep_summary:

            self.summary = ""

            self.save_summary()

    def reload(self):

        self.load_conversation()
        self.load_summary()

    # ========================================================
    # Debug / Status
    # ========================================================

    def status(self):

        print(
            "\nConversation status:"
        )

        print(
            f"  Messages: "
            f"{len(self.messages)}"
        )

        print(
            f"  Recent message limit: "
            f"{self.recent_message_limit}"
        )

        print(
            f"  Maximum retrieved memories: "
            f"{self.max_memories}"
        )

        print(
            f"  Summary available: "
            f"{bool(self.summary.strip())}"
        )

        print(
            f"  Summary trigger: "
            f"{SUMMARY_TRIGGER_MESSAGES} messages"
        )

        print(
            f"  Summary keeps: "
            f"{SUMMARY_KEEP_MESSAGES} messages"
        )

        print()