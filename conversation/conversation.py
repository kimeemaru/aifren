import json
import os
from datetime import datetime


# ============================================================
# Configuration
# ============================================================

CONVERSATION_FILE = "conversation.json"
SUMMARY_FILE = "conversation_summary.json"

RECENT_MESSAGES = 20
SUMMARY_INTERVAL = 20

MAX_MEMORIES = 5

# Approximate character budgets.
#
# These are intentionally conservative. They are not exact
# token counts, but they prevent context from growing without
# limit.
MAX_SUMMARY_CHARS = 6000
MAX_MEMORY_CHARS = 5000
MAX_RECENT_CHARS = 14000

# Number of recent messages that should always be preserved
# before older messages are considered for removal.
MIN_RECENT_MESSAGES = 6


# ============================================================
# JSON Helpers
# ============================================================

def load_json(
    filename,
    default
):

    if not os.path.exists(filename):

        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except (
        json.JSONDecodeError,
        OSError
    ):

        print(
            f"Warning: Could not load {filename}."
        )

        return default


def save_json(
    filename,
    data
):

    try:

        with open(
            filename,
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
            f"{filename}: {e}"
        )


# ============================================================
# Context Manager
# ============================================================

class ContextManager:

    def __init__(self):

        self.max_summary_chars = (
            MAX_SUMMARY_CHARS
        )

        self.max_memory_chars = (
            MAX_MEMORY_CHARS
        )

        self.max_recent_chars = (
            MAX_RECENT_CHARS
        )

    # ========================================================
    # Text Limiting
    # ========================================================

    def limit_text(
        self,
        text,
        maximum
    ):

        if not text:

            return ""

        text = str(
            text
        )

        if len(text) <= maximum:

            return text

        return (
            text[:maximum]
            + "\n[Context truncated]"
        )

    # ========================================================
    # Summary
    # ========================================================

    def build_summary_context(
        self,
        summary
    ):

        if not summary:

            return None

        return {
            "role": "user",
            "content": f"""
LONG-TERM CONVERSATION BACKGROUND:

The following is a compressed summary of
previous conversations.

Use this as background context for continuity.

The summary is NOT necessarily authoritative
when it conflicts with a relevant lifelong
memory.

Do not invent information that is not contained
in the summary.

LONG-TERM SUMMARY:

{summary}

END LONG-TERM SUMMARY.
"""
        }

    # ========================================================
    # Memories
    # ========================================================

    def build_memory_context(
        self,
        memories
    ):

        if not memories:

            return None

        memory_text = ""

        for memory in memories:

            category = memory.get(
                "category",
                "unknown"
            )

            content = memory.get(
                "content",
                ""
            )

            memory_text += (
                f"- [{category}] "
                f"{content}\n"
            )

        return {
            "role": "user",
            "content": f"""
AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:

The following information comes from the
user's lifelong memory database.

These memories represent information that has
been intentionally retained because it is
expected to remain useful across conversations.

When answering factual questions about the user,
use these memories as the primary source of truth.

If recent conversation text appears to conflict
with a relevant lifelong memory, do not casually
replace the lifelong memory with the conversation.

A recent conversation may contain:

- temporary statements
- hypothetical statements
- jokes
- misunderstandings
- roleplay
- assistant mistakes
- outdated information

Relevant lifelong memories should therefore be
treated as authoritative unless the user clearly
corrects or changes the information.

Do not invent additional personal information.

Do not assume that an unrelated memory applies
to the current question.

RELEVANT LIFELONG MEMORIES:

{memory_text}

END AUTHORITATIVE LIFELONG MEMORIES.
"""
        }

    # ========================================================
    # Recent Conversation
    # ========================================================

    def build_recent_context(
        self,
        messages
    ):

        if not messages:

            return []

        # Preserve the active application's complete recent-history payload.
        # Conversation.get_recent_messages already applies RECENT_MESSAGES.
        return messages

    # ========================================================
    # Current Personality Authority
    # ========================================================

    def build_personality_authority_context(
        self
    ):

        return {
            "role": "user",
            "content": (
                "[Current character personality authority]\n\n"

                "The character definition supplied by the "
                "application is the CURRENT and AUTHORITATIVE "
                "personality of the character.\n\n"

                "Previous conversation is historical context. "
                "It may contain behavior, attitudes, opinions, "
                "or characterization from an older version of "
                "the character.\n\n"

                "Use previous conversation to preserve useful "
                "continuity, including facts, relationships, "
                "events, preferences, and ongoing topics.\n\n"

                "Do NOT copy an outdated personality from "
                "previous assistant messages.\n\n"

                "If historical assistant behavior conflicts "
                "with the current character definition, "
                "ALWAYS follow the current character definition.\n\n"

                "A change to the character personality applies "
                "immediately. Do not gradually continue the "
                "old personality simply because it appeared "
                "in earlier messages.\n\n"

                "Do not allow historical dialogue to redefine, "
                "override, weaken, or replace the current "
                "character personality.\n\n"

                "[End current character personality authority]"
            )
        }

    # ========================================================
    # Full Context
    # ========================================================

    def build_context(
        self,
        summary,
        memories,
        recent_messages
    ):

        context = []

        # ----------------------------------------------------
        # Priority 1: Long-term summary
        # ----------------------------------------------------

        summary_context = (
            self.build_summary_context(
                summary
            )
        )

        if summary_context:

            context.append(
                summary_context
            )

        # ----------------------------------------------------
        # Priority 2: Relevant memories
        # ----------------------------------------------------

        memory_context = (
            self.build_memory_context(
                memories
            )
        )

        if memory_context:

            context.append(
                memory_context
            )

        # ----------------------------------------------------
        # Priority 3: Recent conversation
        # ----------------------------------------------------

        recent_context = (
            self.build_recent_context(
                recent_messages
            )
        )

        context.extend(
            recent_context
        )

        return context

    # ========================================================
    # Context Statistics
    # ========================================================

    def calculate_characters(
        self,
        context
    ):

        total = 0

        for item in context:

            total += len(
                item.get(
                    "content",
                    ""
                )
            )

        return total

    def print_statistics(
        self,
        context
    ):

        total_chars = (
            self.calculate_characters(
                context
            )
        )

        print(
            "[Context]"
        )

        print(
            f"  Items: {len(context)}"
        )

        print(
            f"  Characters: {total_chars}"
        )


# ============================================================
# Conversation
# ============================================================

class Conversation:

    def __init__(
        self,
        llm
    ):

        self.llm = llm

        self.messages = load_json(
            CONVERSATION_FILE,
            []
        )

        self.summary_data = load_json(
            SUMMARY_FILE,
            {
                "summary": "",
                "summarized_messages": 0
            }
        )

        self.context_manager = (
            ContextManager()
        )

    # ========================================================
    # Saving
    # ========================================================

    def save(self):

        save_json(
            CONVERSATION_FILE,
            self.messages
        )

        save_json(
            SUMMARY_FILE,
            self.summary_data
        )

    # ========================================================
    # Messages
    # ========================================================

    def add_user_message(
        self,
        content
    ):

        self.messages.append(
            {
                "role": "user",
                "content": content,
                "timestamp": (
                    datetime.now().isoformat()
                )
            }
        )

    def add_assistant_message(
        self,
        content
    ):

        self.messages.append(
            {
                "role": "assistant",
                "content": content,
                "timestamp": (
                    datetime.now().isoformat()
                )
            }
        )

    def get_recent_messages(self):

        return self.messages[
            -RECENT_MESSAGES:
        ]

    # ========================================================
    # Memory Retrieval
    # ========================================================

    def get_relevant_memories(
        self,
        memory,
        user_message
    ):

        try:

            memories = memory.get_relevant_memories(
                user_message,
                max_memories=MAX_MEMORIES
            )
            # Shadow-mode only: capture IDs/categories, never text or a new
            # ranking.  This is intentionally inert unless the service enables
            # the private flag for one diagnostic turn.
            if getattr(self, "_capture_v1_retrieval_diagnostics", False):
                self._last_v1_retrieval_diagnostics = tuple(
                    {
                        "id": str(item.get("id")),
                        "category": str(item.get("category", "unknown")),
                        "rank": rank,
                    }
                    for rank, item in enumerate(memories, 1)
                    if isinstance(item, dict) and item.get("id") is not None
                )
            return memories

        except Exception as e:

            print(
                f"Warning: Could not retrieve "
                f"relevant memories: {e}"
            )

            return []

    # ========================================================
    # Context Construction
    # ========================================================

    def build_context(
        self,
        memory,
        user_message
    ):

        # ----------------------------------------------------
        # Retrieve memories
        # ----------------------------------------------------

        relevant_memories = (
            self.get_relevant_memories(
                memory,
                user_message
            )
        )

        # ----------------------------------------------------
        # Display retrieved memories
        # ----------------------------------------------------

        if relevant_memories:

            print(
                "\n[Relevant memories]"
            )

            for item in relevant_memories:

                print(
                    f"- [{item.get('category', 'unknown')}] "
                    f"{item.get('content', '')}"
                )

            print(
                "[End relevant memories]\n"
            )

        else:

            print(
                "\n[No relevant memories found]\n"
            )

        # ----------------------------------------------------
        # Get summary
        # ----------------------------------------------------

        summary = (
            self.summary_data.get(
                "summary",
                ""
            )
        )

        # ----------------------------------------------------
        # Get recent conversation
        # ----------------------------------------------------

        recent_messages = (
            self.get_recent_messages()
        )

        # ----------------------------------------------------
        # Build managed context
        # ----------------------------------------------------

        context = (
            self.context_manager.build_context(
                summary,
                relevant_memories,
                recent_messages
            )
        )

        return context

    # ========================================================
    # Summary
    # ========================================================

    def update_summary(self):

        summarized_count = (
            self.summary_data.get(
                "summarized_messages",
                0
            )
        )

        # Protect against corrupted or outdated
        # summary indexes.
        if summarized_count < 0:

            summarized_count = 0

        if summarized_count > len(
            self.messages
        ):

            summarized_count = 0

        # Keep the newest messages outside
        # the summary.
        summary_end = (
            len(self.messages)
            - RECENT_MESSAGES
        )

        if summary_end <= summarized_count:

            return

        new_messages = self.messages[
            summarized_count:
            summary_end
        ]

        if len(new_messages) < SUMMARY_INTERVAL:

            return

        conversation_text = ""

        for message in new_messages:

            role = message.get(
                "role",
                "unknown"
            )

            content = message.get(
                "content",
                ""
            )

            conversation_text += (
                f"{role.upper()}: "
                f"{content}\n"
            )

        previous_summary = (
            self.summary_data.get(
                "summary",
                ""
            )
        )

        previous_summary = (
            self.context_manager.limit_text(
                previous_summary,
                MAX_SUMMARY_CHARS
            )
        )

        prompt = f"""
You maintain the long-term conversation
summary for an AI companion.

Your job is to preserve useful information
from older conversation so the companion can
continue behaving consistently over time.

Previous summary:

{previous_summary}

New conversation:

{conversation_text}

Create an updated compact summary.

Preserve:

- important facts about the user
- stable preferences
- interests
- important events
- ongoing projects
- decisions
- unresolved topics
- important changes to previous information

Prioritize information that is likely to
remain useful in future conversations.

Do not include trivial small talk.

Do not invent information.

Do not assume information that was not stated.

If newer information contradicts older
information, prefer the newer information.

Write plain text only.
"""

        try:

            response = self.llm.generate(
                [],
                prompt
            )

        except Exception as e:

            print(
                f"\nWarning: Summary generation "
                f"failed: {e}"
            )

            return

        if response is None:

            return

        response = str(
            response
        ).strip()

        if not response:

            return

        self.summary_data = {
            "summary": response,
            "summarized_messages": (
                summary_end
            )
        }

        save_json(
            SUMMARY_FILE,
            self.summary_data
        )

        print(
            "\nConversation summary updated."
        )

    # ========================================================
    # Conversation Turn
    # ========================================================

    def respond(
        self,
        memory,
        character_prompt,
        user_message
    ):

        user_message = str(
            user_message
        ).strip()

        if not user_message:

            return ""

        # ----------------------------------------------------
        # Add current user message
        # ----------------------------------------------------

        self.add_user_message(
            user_message
        )

        # ----------------------------------------------------
        # Build context
        # ----------------------------------------------------

        context = self.build_context(
            memory,
            user_message
        )

        # ----------------------------------------------------
        # Generate response
        # ----------------------------------------------------

        try:

            reply = self.llm.generate(
                context,
                character_prompt
            )

        except Exception:

            # Remove the user message we just
            # added if generation failed.
            if self.messages:

                last_message = (
                    self.messages[-1]
                )

                if (
                    last_message.get("role")
                    == "user"
                    and
                    last_message.get("content")
                    == user_message
                ):

                    self.messages.pop()

            raise

        if reply is None:

            reply = ""

        reply = str(
            reply
        ).strip()

        # ----------------------------------------------------
        # Store assistant response
        # ----------------------------------------------------

        self.add_assistant_message(
            reply
        )

        # ----------------------------------------------------
        # Save conversation
        # ----------------------------------------------------

        self.save()

        # ----------------------------------------------------
        # Process lifelong memory
        # ----------------------------------------------------

        try:

            memory.process(
                user_message,
                reply
            )

        except Exception as e:

            print(
                f"\nWarning: Memory processing "
                f"failed: {e}"
            )

        # ----------------------------------------------------
        # Update summary if necessary
        # ----------------------------------------------------

        self.update_summary()

        # ----------------------------------------------------
        # Final save
        # ----------------------------------------------------

        self.save()

        return reply

    # ========================================================
    # Conversation Management
    # ========================================================

    def clear_conversation(
        self,
        keep_summary=True
    ):

        self.messages = []

        if not keep_summary:

            self.summary_data = {
                "summary": "",
                "summarized_messages": 0
            }

        self.save()

    def reload(self):

        self.messages = load_json(
            CONVERSATION_FILE,
            []
        )

        self.summary_data = load_json(
            SUMMARY_FILE,
            {
                "summary": "",
                "summarized_messages": 0
            }
        )

    # ========================================================
    # Status
    # ========================================================

    def status(self):

        summary = (
            self.summary_data.get(
                "summary",
                ""
            )
        )

        summarized_count = (
            self.summary_data.get(
                "summarized_messages",
                0
            )
        )

        print(
            "\nConversation status:"
        )

        print(
            f"  Total messages: "
            f"{len(self.messages)}"
        )

        print(
            f"  Recent message limit: "
            f"{RECENT_MESSAGES}"
        )

        print(
            f"  Memory limit: "
            f"{MAX_MEMORIES}"
        )

        print(
            f"  Summary interval: "
            f"{SUMMARY_INTERVAL}"
        )

        print(
            f"  Summarized messages: "
            f"{summarized_count}"
        )

        print(
            f"  Summary available: "
            f"{bool(summary.strip())}"
        )

        print(
            f"  Summary characters: "
            f"{len(summary)}"
        )

        print()
