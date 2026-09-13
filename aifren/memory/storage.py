import json
import os


MEMORY_FILE = "memories.json"


def load_memories():

    if not os.path.exists(MEMORY_FILE):
        return []

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except (json.JSONDecodeError, OSError):

        print(
            "Warning: Could not load memories.json."
        )

        return []


def save_memories(memories):

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            memories,
            file,
            ensure_ascii=False,
            indent=2
        )


def next_memory_id(memories):

    if not memories:
        return 1

    valid_ids = [
        memory["id"]
        for memory in memories
        if isinstance(memory, dict)
        and isinstance(memory.get("id"), int)
    ]

    if not valid_ids:
        return 1

    return max(valid_ids) + 1