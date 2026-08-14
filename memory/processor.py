import json
from datetime import datetime


def process_memory(
    llm,
    memories,
    embedding_model,
    user_message,
    assistant_reply,
    next_memory_id
):

    existing_memory_text = ""

    for memory in memories:

        existing_memory_text += (
            f"ID {memory.get('id', '?')} | "
            f"[{memory.get('category', 'unknown')}] "
            f"{memory.get('content', '')}\n"
        )

    prompt = f"""
You are the memory manager for a lifelong AI companion.

EXISTING MEMORIES:

{existing_memory_text}

CURRENT USER MESSAGE:

{user_message}

CURRENT ASSISTANT RESPONSE:

{assistant_reply}

Identify genuinely NEW information about the user.

Do NOT create a memory because the assistant merely recalled
an existing memory.

Do NOT create a duplicate memory even if it is worded differently.

If new information expands or changes an existing memory,
UPDATE the existing memory.

Only remember information likely to remain useful in future
conversations.

Do not invent information.

Return ONLY valid JSON.

For a new memory:

[
{{
"action": "ADD",
"category": "interest",
"content": "The user enjoys retro Nintendo games.",
"importance": 7
}}
]

For an existing memory that should be updated:

[
{{
"action": "UPDATE",
"id": 3,
"category": "interest",
"content": "Updated memory...",
"importance": 8
}}
]

If nothing should change:

[]

Allowed categories:

fact
preference
event
interest
project
relationship

Importance must be 1-10.
"""

    try:

        response = llm.generate(
            [],
            prompt
        )

        result = response.strip()

        if result.startswith("```"):

            result = result.replace(
                "```json",
                ""
            )

            result = result.replace(
                "```",
                ""
            )

            result = result.strip()

        actions = json.loads(result)

        if not isinstance(actions, list):
            return

        for action in actions:

            if not isinstance(action, dict):
                continue

            action_type = action.get(
                "action",
                ""
            ).upper()

            if action_type == "ADD":

                content = action.get(
                    "content",
                    ""
                ).strip()

                category = action.get(
                    "category"
                )

                if not content or not category:
                    continue

                duplicate = False

                for existing in memories:

                    existing_content = (
                        existing.get(
                            "content",
                            ""
                        ).strip().lower()
                    )

                    if (
                        existing_content
                        == content.lower()
                    ):

                        duplicate = True
                        break

                if duplicate:
                    continue

                embedding = (
                    embedding_model.encode(
                        content
                    )
                )

                memory = {
                    "id": next_memory_id(
                        memories
                    ),
                    "category": category,
                    "content": content,
                    "importance": action.get(
                        "importance",
                        5
                    ),
                    "embedding": (
                        embedding.tolist()
                    ),
                    "created": (
                        datetime.now().isoformat()
                    ),
                    "updated": (
                        datetime.now().isoformat()
                    )
                }

                memories.append(
                    memory
                )

                print(
                    f"\n[Memory saved: "
                    f"{content}]"
                )

            elif action_type == "UPDATE":

                memory_id = action.get(
                    "id"
                )

                for memory in memories:

                    if memory.get(
                        "id"
                    ) == memory_id:

                        memory["category"] = (
                            action.get(
                                "category",
                                memory.get(
                                    "category",
                                    "fact"
                                )
                            )
                        )

                        memory["content"] = (
                            action.get(
                                "content",
                                memory.get(
                                    "content",
                                    ""
                                )
                            ).strip()
                        )

                        new_embedding = (
                            embedding_model.encode(
                                memory["content"]
                            )
                        )

                        memory["embedding"] = (
                            new_embedding.tolist()
                        )

                        memory["importance"] = (
                            action.get(
                                "importance",
                                memory.get(
                                    "importance",
                                    5
                                )
                            )
                        )

                        memory["updated"] = (
                            datetime.now().isoformat()
                        )

                        print(
                            f"\n[Memory updated: "
                            f"{memory['content']}]"
                        )

                        break

    except Exception as e:

        print(
            f"\nMemory check failed: {e}"
        )