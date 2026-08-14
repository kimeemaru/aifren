def get_relevant_memories(
    memories,
    embedding_model,
    user_message,
    max_memories=10
):

    if not memories:
        return []

    query_embedding = (
        embedding_model.encode(
            user_message
        )
    )

    scored = []

    for memory in memories:

        if "embedding" not in memory:
            continue

        memory_embedding = (
            memory["embedding"]
        )

        similarity = sum(
            a * b
            for a, b in zip(
                query_embedding,
                memory_embedding
            )
        )

        if similarity < 0.20:
            continue

        importance = memory.get(
            "importance",
            5
        )

        score = (
            similarity * 100
            + importance * 0.1
        )

        scored.append(
            (
                score,
                similarity,
                memory
            )
        )

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        memory
        for score, similarity, memory
        in scored[:max_memories]
    ]