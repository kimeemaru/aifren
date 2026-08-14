import json
import math
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime

from memory.embeddings import EmbeddingModel


# ============================================================
# Configuration
# ============================================================

MEMORY_FILE = "memories.json"

MAX_RELEVANT_MEMORIES = 5

SEMANTIC_THRESHOLD = 0.20
RELEVANCE_THRESHOLD = 0.30

SEMANTIC_WEIGHT = 0.55
KEYWORD_WEIGHT = 0.30
CATEGORY_WEIGHT = 0.15

IMPORTANCE_WEIGHT = 0.02

EMBEDDING_DIMENSIONS = 384


class MemoryDataError(ValueError):
    """Raised when persisted memory data is malformed or unsafe to use."""


def _normalize_importance(value):

    if isinstance(value, bool):
        raise MemoryDataError("Memory importance must be an integer.")

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    if not isinstance(value, int):
        raise MemoryDataError("Memory importance must be an integer.")

    if not 1 <= value <= 10:
        raise MemoryDataError("Memory importance must be between 1 and 10.")

    return value


def validate_memory_record(record, allow_missing_derived=False):
    """Validate and normalize one compatible memory record in place."""
    if not isinstance(record, dict):
        raise MemoryDataError("Each memory record must be an object.")

    memory_id = record.get("id")
    if isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id < 1:
        raise MemoryDataError("Memory id must be a positive integer.")

    category = record.get("category")
    if not isinstance(category, str) or not category.strip():
        raise MemoryDataError("Memory category must be non-empty text.")
    record["category"] = category.strip()

    content = record.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MemoryDataError("Memory content must be non-empty text.")
    record["content"] = content.strip()

    record["importance"] = _normalize_importance(record.get("importance"))

    keywords = record.get("keywords")
    if keywords is None and allow_missing_derived:
        pass
    elif not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        raise MemoryDataError("Memory keywords must be a list of strings.")

    embedding = record.get("embedding")
    if embedding is None and allow_missing_derived:
        pass
    else:
        if not isinstance(embedding, list) or len(embedding) != EMBEDDING_DIMENSIONS:
            raise MemoryDataError(
                f"Memory embedding must contain {EMBEDDING_DIMENSIONS} values."
            )

        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in embedding
        ):
            raise MemoryDataError("Memory embedding contains an invalid value.")

    for field in ("created", "updated"):
        value = record.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise MemoryDataError(f"Memory {field} timestamp is invalid.")

    provenance = record.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise MemoryDataError("Memory provenance must be an object.")
        if not isinstance(provenance.get("source"), str):
            raise MemoryDataError("Memory provenance source is invalid.")

    return record


# ============================================================
# JSON
# ============================================================

def load_memories():

    if not os.path.exists(
        MEMORY_FILE
    ):

        return []

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(data, list):
            raise MemoryDataError("memories.json does not contain a list.")

        seen_ids = set()

        for record in data:
            validate_memory_record(
                record,
                allow_missing_derived=True
            )

            if record["id"] in seen_ids:
                raise MemoryDataError("memories.json contains duplicate ids.")

            seen_ids.add(record["id"])

        return data

    except json.JSONDecodeError as error:
        raise MemoryDataError(
            "memories.json is malformed. It has not been replaced."
        ) from error

    except OSError as error:
        raise MemoryDataError(
            f"Could not read memories.json: {error}"
        ) from error


def save_memories(
    memories
):

    if not isinstance(memories, list):
        raise MemoryDataError("Memories must be stored as a list.")

    seen_ids = set()

    for record in memories:
        validate_memory_record(record)

        if record["id"] in seen_ids:
            raise MemoryDataError("Cannot save duplicate memory ids.")

        seen_ids.add(record["id"])

    directory = os.path.dirname(os.path.abspath(MEMORY_FILE))
    basename = os.path.basename(MEMORY_FILE)
    temp_path = None

    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{basename}.",
            suffix=".tmp",
            dir=directory
        )

        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(memories, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())

        if os.path.exists(MEMORY_FILE):
            shutil.copy2(MEMORY_FILE, MEMORY_FILE + ".bak")

        os.replace(temp_path, MEMORY_FILE)
        temp_path = None

    except OSError as error:
        raise MemoryDataError(
            f"Could not safely save memories.json: {error}"
        ) from error

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ============================================================
# IDs
# ============================================================

def next_memory_id(
    memories
):

    if not memories:

        return 1

    valid_ids = [
        memory["id"]
        for memory in memories
        if (
            isinstance(
                memory,
                dict
            )
            and
            isinstance(
                memory.get("id"),
                int
            )
        )
    ]

    if not valid_ids:

        return 1

    return max(
        valid_ids
    ) + 1


# ============================================================
# Text Processing
# ============================================================

def tokenize(
    text
):

    if not text:

        return []

    return re.findall(
        r"[a-z0-9]+(?:'[a-z0-9]+)?",
        str(text).lower()
    )


def normalize_text(
    text
):

    return " ".join(
        tokenize(text)
    )


# ============================================================
# Stop Words
# ============================================================

STOP_WORDS = {
    "the",
    "and",
    "that",
    "this",
    "these",
    "those",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "how",
    "why",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
    "with",
    "from",
    "about",
    "your",
    "you",
    "yourself",
    "my",
    "mine",
    "our",
    "ours",
    "for",
    "but",
    "not",
    "think",
    "tell",
    "know",
    "into",
    "than",
    "then",
    "they",
    "them",
    "their",
    "there",
    "here",
    "just",
    "really",
    "very",
    "like",
    "want",
    "some",
    "something",
    "anything",
    "someone",
    "thing",
    "things",
    "much",
    "many",
    "does",
    "did",
    "get",
    "got",
    "use",
    "using",
}


def meaningful_words(
    text
):

    words = tokenize(
        text
    )

    result = []

    for word in words:

        if len(word) < 3:

            continue

        if word in STOP_WORDS:

            continue

        result.append(
            word
        )

    return result


# ============================================================
# Category Concepts
# ============================================================

CATEGORY_CONCEPTS = {

    "preference": {
        "favorite",
        "prefer",
        "preference",
        "like",
        "likes",
        "love",
        "loves",
        "enjoy",
        "enjoys",
        "dislike",
        "dislikes",
        "hate",
        "hates",
        "favorite",
    },

    "interest": {
        "game",
        "games",
        "gaming",
        "rpg",
        "jrpg",
        "video",
        "console",
        "nintendo",
        "playstation",
        "xbox",
        "pc",
        "computer",
        "book",
        "books",
        "movie",
        "movies",
        "music",
        "collecting",
        "collection",
        "hobby",
        "hobbies",
    },

    "project": {
        "project",
        "python",
        "programming",
        "program",
        "script",
        "code",
        "coding",
        "software",
        "development",
        "develop",
        "build",
        "building",
    },

    "event": {
        "event",
        "happened",
        "bought",
        "bought",
        "sold",
        "sold",
        "visited",
        "went",
        "started",
        "finished",
        "completed",
    },

    "relationship": {
        "friend",
        "family",
        "parent",
        "brother",
        "sister",
        "partner",
        "relationship",
        "person",
    },

    "fact": {
        "fact",
        "name",
        "age",
        "location",
        "lives",
        "owns",
        "has",
        "uses",
    },
}


# ============================================================
# Query Concept Detection
# ============================================================

QUERY_CONCEPTS = {

    "color": {
        "color",
        "colour",
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "purple",
        "violet",
        "pink",
        "black",
        "white",
        "brown",
        "gray",
        "grey",
    },

    "animal": {
        "animal",
        "animals",
        "dog",
        "dogs",
        "cat",
        "cats",
        "cow",
        "cows",
        "horse",
        "horses",
        "bird",
        "birds",
        "penguin",
        "penguins",
    },

    "game": {
        "game",
        "games",
        "gaming",
        "rpg",
        "jrpg",
        "video",
        "videogame",
        "console",
        "nintendo",
        "playstation",
        "xbox",
        "pc",
    },

    "classic_game": {
        "classic",
        "retro",
        "old",
        "oldschool",
        "rpg",
        "jrpg",
    },

    "computer": {
        "computer",
        "pc",
        "python",
        "program",
        "programming",
        "code",
        "coding",
        "script",
        "software",
    },

    "collecting": {
        "collect",
        "collecting",
        "collection",
        "collector",
        "games",
        "cards",
        "cartridges",
        "carts",
    },

    "music": {
        "music",
        "song",
        "songs",
        "band",
        "bands",
        "artist",
        "artists",
    },

    "book": {
        "book",
        "books",
        "novel",
        "novels",
        "reading",
    },

    "movie": {
        "movie",
        "movies",
        "film",
        "films",
    },

    "project": {
        "project",
        "projects",
        "working",
        "build",
        "building",
        "making",
        "coding",
        "programming",
    },

    "favorite": {
        "favorite",
        "favourite",
        "best",
        "love",
        "loves",
        "like",
        "likes",
        "prefer",
        "preference",
    },

    "word": {
        "word",
        "words",
        "term",
        "phrase",
    },

    "animal_word": {
        "word",
        "term",
        "bovine",
        "cow",
    },
}


# ============================================================
# Concept Expansion
# ============================================================

CONCEPT_RELATIONSHIPS = {

    "rpg": {
        "game",
        "games",
        "jrpg",
        "classic_game",
    },

    "jrpg": {
        "game",
        "games",
        "rpg",
        "classic_game",
    },

    "classic": {
        "classic_game",
        "game",
        "games",
        "retro",
    },

    "retro": {
        "classic_game",
        "classic",
        "game",
        "games",
    },

    "color": {
        "favorite",
    },

    "animal": {
        "favorite",
    },

    "game": {
        "interest",
        "classic_game",
    },
}


# ============================================================
# Concept Detection Helpers
# ============================================================

def detect_query_concepts(
    text
):

    words = set(
        meaningful_words(text)
    )

    concepts = set()

    for concept, terms in (
        QUERY_CONCEPTS.items()
    ):

        if words & terms:

            concepts.add(
                concept
            )

    # Add related concepts.
    expanded = set(
        concepts
    )

    for concept in concepts:

        related = (
            CONCEPT_RELATIONSHIPS.get(
                concept,
                set()
            )
        )

        expanded.update(
            related
        )

    return expanded


def detect_memory_concepts(
    memory
):

    content = memory.get(
        "content",
        ""
    )

    category = memory.get(
        "category",
        ""
    )

    words = set(
        meaningful_words(
            content
        )
    )

    concepts = set()

    # --------------------------------------------------------
    # Category itself is a concept.
    # --------------------------------------------------------

    if category:

        concepts.add(
            category.lower()
        )

    # --------------------------------------------------------
    # Match query concepts against memory text.
    # --------------------------------------------------------

    for concept, terms in (
        QUERY_CONCEPTS.items()
    ):

        if words & terms:

            concepts.add(
                concept
            )

    # --------------------------------------------------------
    # Category concepts.
    # --------------------------------------------------------

    category_terms = (
        CATEGORY_CONCEPTS.get(
            category.lower(),
            set()
        )
    )

    if words & category_terms:

        concepts.add(
            category.lower()
        )

    # --------------------------------------------------------
    # Expand concepts.
    # --------------------------------------------------------

    expanded = set(
        concepts
    )

    for concept in concepts:

        expanded.update(
            CONCEPT_RELATIONSHIPS.get(
                concept,
                set()
            )
        )

    return expanded


# ============================================================
# Keyword Metadata
# ============================================================

def generate_memory_keywords(
    memory
):

    content = memory.get(
        "content",
        ""
    )

    category = memory.get(
        "category",
        ""
    )

    words = set(
        meaningful_words(
            content
        )
    )

    concepts = detect_memory_concepts(
        memory
    )

    keywords = set(
        words
    )

    keywords.update(
        concepts
    )

    if category:

        keywords.add(
            category.lower()
        )

    return sorted(
        keywords
    )


# ============================================================
# Keyword Matching
# ============================================================

def calculate_keyword_score(
    query_words,
    memory_keywords
):

    if not query_words:

        return 0.0

    if not memory_keywords:

        return 0.0

    memory_keywords = set(
        memory_keywords
    )

    exact_matches = (
        query_words
        &
        memory_keywords
    )

    if not exact_matches:

        return 0.0

    score = (
        len(exact_matches)
        /
        len(query_words)
    )

    return min(
        score,
        1.0
    )


# ============================================================
# Memory
# ============================================================

class Memory:

    def __init__(
        self,
        llm
    ):

        self.llm = llm

        self._lock = threading.RLock()

        self.memories = (
            load_memories()
        )

        self.embedding_model = (
            EmbeddingModel()
        )

    # ========================================================
    # Metadata
    # ========================================================

    def generate_missing_metadata(
        self
    ):

        changed = False

        for memory in self.memories:

            if (
                "keywords"
                not in memory
            ):

                memory["keywords"] = (
                    generate_memory_keywords(
                        memory
                    )
                )

                changed = True

            else:

                if not isinstance(
                    memory["keywords"],
                    list
                ):

                    memory["keywords"] = (
                        generate_memory_keywords(
                            memory
                        )
                    )

                    changed = True

        if changed:

            self.save()

            print(
                "Memory metadata updated."
            )

    # ========================================================
    # Embeddings
    # ========================================================

    def generate_missing_embeddings(
        self
    ):

        changed = False

        for memory in self.memories:

            if (
                "embedding"
                in memory
            ):

                continue

            content = memory.get(
                "content",
                ""
            )

            if not content:

                continue

            print(
                f"Generating embedding for memory "
                f"{memory.get('id', '?')}..."
            )

            embedding = (
                self.embedding_model.encode(
                    content
                )
            )

            if hasattr(
                embedding,
                "tolist"
            ):

                embedding = (
                    embedding.tolist()
                )

            memory["embedding"] = (
                embedding
            )

            changed = True

        if changed:

            self.save()

            print(
                "Memory embeddings saved."
            )

    # ========================================================
    # Save
    # ========================================================

    def save(
        self
    ):

        with self._lock:
            save_memories(
                self.memories
            )

    # ========================================================
    # Centralized Mutations
    # ========================================================

    def _create_embedding(
        self,
        content
    ):

        embedding = self.embedding_model.encode(
            content
        )

        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        return embedding

    def _provenance(
        self,
        source,
        source_message=None
    ):

        provenance = {
            "source": source,
            "recorded_at": datetime.now().isoformat()
        }

        if source_message:
            provenance["source_message"] = source_message

        return provenance

    def add_memory(
        self,
        category,
        content,
        importance=5,
        source="user_message",
        source_message=None
    ):

        with self._lock:
            if not isinstance(category, str) or not category.strip():
                raise MemoryDataError("Memory category must be non-empty text.")

            if not isinstance(content, str) or not content.strip():
                raise MemoryDataError("Memory content must be non-empty text.")

            candidate = {
                "id": next_memory_id(self.memories),
                "category": category.strip(),
                "content": content.strip(),
                "importance": _normalize_importance(importance),
                "keywords": [],
                "created": datetime.now().isoformat(),
                "updated": datetime.now().isoformat(),
                "provenance": self._provenance(
                    source,
                    source_message
                )
            }

            validate_memory_record(
                candidate,
                allow_missing_derived=True
            )

            normalized_content = normalize_text(candidate["content"])

            for existing in self.memories:
                if normalize_text(existing.get("content", "")) == normalized_content:
                    return None

            candidate["keywords"] = generate_memory_keywords(candidate)
            candidate["embedding"] = self._create_embedding(
                candidate["content"]
            )
            validate_memory_record(candidate)

            self.memories.append(candidate)
            self.save()

            return candidate

    def update_memory(
        self,
        memory_id,
        category=None,
        content=None,
        importance=None,
        source="user_message",
        source_message=None
    ):

        with self._lock:
            for memory in self.memories:
                if memory.get("id") != memory_id:
                    continue

                if category is not None and (
                    not isinstance(category, str)
                    or not category.strip()
                ):
                    raise MemoryDataError(
                        "Memory category must be non-empty text."
                    )

                if content is not None and (
                    not isinstance(content, str)
                    or not content.strip()
                ):
                    raise MemoryDataError(
                        "Memory content must be non-empty text."
                    )

                new_category = (
                    memory.get("category")
                    if category is None
                    else category.strip()
                )
                new_content = (
                    memory.get("content")
                    if content is None
                    else content.strip()
                )
                new_importance = (
                    memory.get("importance")
                    if importance is None
                    else importance
                )

                candidate = dict(memory)
                candidate["category"] = new_category
                candidate["content"] = new_content
                candidate["importance"] = _normalize_importance(new_importance)
                candidate["keywords"] = generate_memory_keywords(candidate)
                candidate["updated"] = datetime.now().isoformat()
                candidate["provenance"] = self._provenance(
                    source,
                    source_message
                )

                if new_content != memory.get("content"):
                    candidate["embedding"] = self._create_embedding(new_content)

                validate_memory_record(candidate)
                memory.clear()
                memory.update(candidate)
                self.save()

                return memory

        return None

    def edit_memory(
        self,
        memory_id,
        category,
        content,
        importance
    ):

        return self.update_memory(
            memory_id,
            category=category,
            content=content,
            importance=importance,
            source="manual_edit"
        )

    def delete_memory(
        self,
        memory_id
    ):

        with self._lock:
            for memory in self.memories:
                if memory.get("id") == memory_id:
                    self.memories.remove(memory)
                    self.save()
                    return True

        return False

    # ========================================================
    # List
    # ========================================================

    def list(
        self
    ):

        if not self.memories:

            print(
                "\nNo memories stored."
            )

            return

        print(
            "\nLifelong memories:\n"
        )

        for memory in self.memories:

            print(
                f"[{memory.get('id', '?')}] "
                f"[{memory.get('category', 'unknown')}] "
                f"(importance "
                f"{memory.get('importance', '?')})"
            )

            print(
                f"    {memory.get('content', '')}"
            )

            keywords = memory.get(
                "keywords",
                []
            )

            if keywords:

                print(
                    f"    Keywords: "
                    f"{', '.join(keywords)}"
                )

            print()

    # ========================================================
    # Search
    # ========================================================

    def search(
        self,
        search_term
    ):

        search_term = (
            search_term.lower()
        )

        found = []

        for memory in self.memories:

            content = memory.get(
                "content",
                ""
            )

            if (
                search_term
                in content.lower()
            ):

                found.append(
                    memory
                )

        if not found:

            print(
                "\nNo matching memories found."
            )

            return

        print(
            "\nMatching memories:\n"
        )

        for memory in found:

            print(
                f"[{memory.get('id', '?')}] "
                f"[{memory.get('category', 'unknown')}] "
                f"(importance "
                f"{memory.get('importance', '?')})"
            )

            print(
                f"    {memory.get('content', '')}"
            )

            print()

    # ========================================================
    # Delete
    # ========================================================

    def delete(
        self,
        memory_id
    ):

        try:

            memory_id = int(
                memory_id
            )

        except ValueError:

            print(
                "\nInvalid memory ID."
            )

            return

        for memory in self.memories:

            if (
                memory.get("id")
                == memory_id
            ):

                print(
                    f"\nMemory to delete:\n"
                    f"{memory.get('content', '')}"
                )

                confirmation = input(
                    "\nDelete this memory? (yes/no): "
                )

                if (
                    confirmation.lower()
                    == "yes"
                ):

                    if self.delete_memory(memory_id):

                        print(
                            "Memory deleted."
                        )

                else:

                    print(
                        "Deletion cancelled."
                    )

                return

        print(
            "\nMemory ID not found."
        )

    # ========================================================
    # Wipe
    # ========================================================

    def wipe(
        self
    ):

        if not self.memories:

            print(
                "\nThere are no memories to wipe."
            )

            return

        print(
            f"\nWARNING: This will delete all "
            f"{len(self.memories)} lifelong memories."
        )

        confirmation = input(
            "Type WIPE to confirm: "
        )

        if confirmation == "WIPE":

            with self._lock:
                self.memories.clear()
                self.save()

            print(
                "All lifelong memories have "
                "been deleted."
            )

        else:

            print(
                "Memory wipe cancelled."
            )

    # ========================================================
    # Similarity
    # ========================================================

    def calculate_similarity(
        self,
        query_embedding,
        memory_embedding
    ):

        if not query_embedding:

            return 0.0

        if not memory_embedding:

            return 0.0

        length = min(
            len(query_embedding),
            len(memory_embedding)
        )

        if length == 0:

            return 0.0

        similarity = sum(
            query_embedding[index]
            *
            memory_embedding[index]
            for index in range(length)
        )

        return float(
            similarity
        )

    # ========================================================
    # Relevance
    # ========================================================

    def calculate_relevance(
        self,
        user_message,
        memory,
        query_embedding
    ):

        content = memory.get(
            "content",
            ""
        )

        if not content:

            return {
                "score": 0.0,
                "semantic": 0.0,
                "keyword": 0.0,
                "concept": 0.0
            }

        # ----------------------------------------------------
        # Semantic
        # ----------------------------------------------------

        semantic = 0.0

        memory_embedding = (
            memory.get(
                "embedding"
            )
        )

        if memory_embedding:

            semantic = (
                self.calculate_similarity(
                    query_embedding,
                    memory_embedding
                )
            )

        semantic = max(
            -1.0,
            min(
                semantic,
                1.0
            )
        )

        semantic_positive = max(
            semantic,
            0.0
        )

        # ----------------------------------------------------
        # Keywords
        # ----------------------------------------------------

        query_words = set(
            meaningful_words(
                user_message
            )
        )

        memory_keywords = memory.get(
            "keywords",
            []
        )

        keyword = (
            calculate_keyword_score(
                query_words,
                memory_keywords
            )
        )

        # ----------------------------------------------------
        # Concepts
        # ----------------------------------------------------

        query_concepts = (
            detect_query_concepts(
                user_message
            )
        )

        memory_concepts = (
            detect_memory_concepts(
                memory
            )
        )

        concept_matches = (
            query_concepts
            &
            memory_concepts
        )

        concept_score = 0.0

        if query_concepts:

            concept_score = (
                len(concept_matches)
                /
                len(query_concepts)
            )

        # ----------------------------------------------------
        # Combined score
        # ----------------------------------------------------

        score = (
            semantic_positive
            * SEMANTIC_WEIGHT
        )

        score += (
            keyword
            * KEYWORD_WEIGHT
        )

        score += (
            concept_score
            * CATEGORY_WEIGHT
        )

        # ----------------------------------------------------
        # Importance
        # ----------------------------------------------------

        importance = memory.get(
            "importance",
            5
        )

        try:

            importance = float(
                importance
            )

        except (
            TypeError,
            ValueError
        ):

            importance = 5.0

        score += (
            importance
            / 10
            * IMPORTANCE_WEIGHT
        )

        # ----------------------------------------------------
        # Strong concept match bonus
        # ----------------------------------------------------

        if concept_score >= 0.50:

            score += 0.12

        # ----------------------------------------------------
        # Strong direct keyword match bonus
        # ----------------------------------------------------

        if keyword >= 0.75:

            score += 0.10

        return {
            "score": score,
            "semantic": semantic,
            "keyword": keyword,
            "concept": concept_score
        }

    # ========================================================
    # Relevant Memories
    # ========================================================

    def get_relevant_memories(
        self,
        user_message,
        max_memories=MAX_RELEVANT_MEMORIES
    ):

        if not self.memories:

            return []

        user_message = (
            str(user_message)
            .strip()
        )

        if not user_message:

            return []

        query_embedding = (
            self.embedding_model.encode(
                user_message
            )
        )

        if hasattr(
            query_embedding,
            "tolist"
        ):

            query_embedding = (
                query_embedding.tolist()
            )

        scored = []

        for memory in self.memories:

            if (
                "embedding"
                not in memory
            ):

                continue

            relevance = (
                self.calculate_relevance(
                    user_message,
                    memory,
                    query_embedding
                )
            )

            score = relevance[
                "score"
            ]

            semantic = relevance[
                "semantic"
            ]

            keyword = relevance[
                "keyword"
            ]

            concept = relevance[
                "concept"
            ]

            # ------------------------------------------------
            # Relevance decision
            # ------------------------------------------------

            qualifies = (
                score
                >= RELEVANCE_THRESHOLD
            )

            if not qualifies:

                continue

            scored.append(
                (
                    score,
                    semantic,
                    keyword,
                    concept,
                    memory
                )
            )

        # ----------------------------------------------------
        # Sort
        # ----------------------------------------------------

        scored.sort(
            key=lambda item: item[0],
            reverse=True
        )

        # ----------------------------------------------------
        # Return
        # ----------------------------------------------------

        return [
            memory
            for (
                score,
                semantic,
                keyword,
                concept,
                memory
            )
            in scored[
                :max_memories
            ]
        ]

    # ========================================================
    # Memory Processing
    # ========================================================

    def _process_legacy(
        self,
        user_message,
        assistant_reply
    ):

        existing_memory_text = ""

        for memory in self.memories:

            existing_memory_text += (
                f"ID {memory.get('id', '?')} | "
                f"[{memory.get('category', 'unknown')}] "
                f"{memory.get('content', '')}\n"
            )

        prompt = f"""
You are the memory manager for a lifelong
AI companion.

Your job is to maintain useful, durable
information about the user.

EXISTING MEMORIES:

{existing_memory_text}

CURRENT USER MESSAGE:

{user_message}

CURRENT ASSISTANT RESPONSE:

{assistant_reply}

Identify genuinely NEW information about
the user.

Do NOT create a memory because the assistant
merely recalled an existing memory.

Do NOT create a duplicate memory even if it
is worded differently.

If new information expands, corrects, or
changes an existing memory, UPDATE the
existing memory instead.

Only remember information likely to remain
useful in future conversations.

Prefer:

- stable preferences
- interests
- hobbies
- projects
- relationships
- important facts
- meaningful events
- durable likes and dislikes

Do not remember trivial small talk.

Do not invent information.

Do not infer personal facts that were never
actually stated.

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

            response = self.llm.generate(
                [],
                prompt
            )

            result = (
                response
                .strip()
            )

            if result.startswith(
                "```"
            ):

                result = result.replace(
                    "```json",
                    ""
                )

                result = result.replace(
                    "```",
                    ""
                )

                result = result.strip()

            actions = json.loads(
                result
            )

            if not isinstance(
                actions,
                list
            ):

                return

            for action in actions:

                if not isinstance(
                    action,
                    dict
                ):

                    continue

                action_type = action.get(
                    "action",
                    ""
                ).upper()

                # ====================================================
                # ADD
                # ====================================================

                if action_type == "ADD":

                    content = action.get(
                        "content",
                        ""
                    ).strip()

                    category = action.get(
                        "category"
                    )

                    if (
                        not content
                        or not category
                    ):

                        continue

                    normalized_content = (
                        normalize_text(
                            content
                        )
                    )

                    duplicate = False

                    for existing in (
                        self.memories
                    ):

                        existing_content = (
                            existing.get(
                                "content",
                                ""
                            )
                        )

                        normalized_existing = (
                            normalize_text(
                                existing_content
                            )
                        )

                        if (
                            normalized_existing
                            ==
                            normalized_content
                        ):

                            duplicate = True
                            break

                    if duplicate:

                        continue

                    importance = action.get(
                        "importance",
                        5
                    )

                    try:

                        importance = int(
                            importance
                        )

                    except (
                        TypeError,
                        ValueError
                    ):

                        importance = 5

                    importance = max(
                        1,
                        min(
                            importance,
                            10
                        )
                    )

                    memory = {
                        "id": next_memory_id(
                            self.memories
                        ),
                        "category": category,
                        "content": content,
                        "importance": importance,
                        "keywords": [],
                        "created": (
                            datetime.now()
                            .isoformat()
                        ),
                        "updated": (
                            datetime.now()
                            .isoformat()
                        )
                    }

                    memory["keywords"] = (
                        generate_memory_keywords(
                            memory
                        )
                    )

                    try:

                        embedding = (
                            self.embedding_model.encode(
                                content
                            )
                        )

                        if hasattr(
                            embedding,
                            "tolist"
                        ):

                            embedding = (
                                embedding.tolist()
                            )

                        memory["embedding"] = (
                            embedding
                        )

                    except Exception as e:

                        print(
                            f"\nWarning: Could not "
                            f"generate embedding "
                            f"for new memory: {e}"
                        )

                    self.memories.append(
                        memory
                    )

                    print(
                        f"\n[Memory saved: "
                        f"{content}]"
                    )

                # ====================================================
                # UPDATE
                # ====================================================

                elif action_type == "UPDATE":

                    memory_id = action.get(
                        "id"
                    )

                    for memory in (
                        self.memories
                    ):

                        if (
                            memory.get("id")
                            != memory_id
                        ):

                            continue

                        old_content = (
                            memory.get(
                                "content",
                                ""
                            )
                        )

                        new_content = (
                            action.get(
                                "content",
                                old_content
                            )
                        ).strip()

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
                            new_content
                        )

                        importance = action.get(
                            "importance",
                            memory.get(
                                "importance",
                                5
                            )
                        )

                        try:

                            importance = int(
                                importance
                            )

                        except (
                            TypeError,
                            ValueError
                        ):

                            importance = 5

                        memory["importance"] = (
                            max(
                                1,
                                min(
                                    importance,
                                    10
                                )
                            )
                        )

                        memory["keywords"] = (
                            generate_memory_keywords(
                                memory
                            )
                        )

                        memory["updated"] = (
                            datetime.now()
                            .isoformat()
                        )

                        if (
                            new_content
                            != old_content
                        ):

                            try:

                                embedding = (
                                    self.embedding_model.encode(
                                        new_content
                                    )
                                )

                                if hasattr(
                                    embedding,
                                    "tolist"
                                ):

                                    embedding = (
                                        embedding.tolist()
                                    )

                                memory["embedding"] = (
                                    embedding
                                )

                            except Exception as e:

                                print(
                                    f"\nWarning: Could "
                                    f"not update "
                                    f"memory embedding: "
                                    f"{e}"
                                )

                        print(
                            f"\n[Memory updated: "
                            f"{memory['content']}]"
                        )

                        break

            self.save()

        except Exception as e:

            print(
                f"\nMemory check failed: {e}"
            )

    # ========================================================
    # Memory Processing
    # ========================================================

    def _is_attributable_to_user(
        self,
        content,
        user_message
    ):

        content_words = set(meaningful_words(content))
        user_words = set(meaningful_words(user_message))

        # Retained memories must contain at least one meaningful fact term
        # explicitly present in the user's message. This blocks assistant-only
        # claims while allowing normal summaries such as "The user likes X".
        return bool(content_words & user_words)

    def process(
        self,
        user_message,
        assistant_reply
    ):

        user_message = str(user_message).strip()

        if not user_message:
            return

        with self._lock:
            existing_memory_text = ""

            for memory in self.memories:
                existing_memory_text += (
                    f"ID {memory.get('id', '?')} | "
                    f"[{memory.get('category', 'unknown')}] "
                    f"{memory.get('content', '')}\n"
                )

            prompt = f"""
You are the memory manager for a lifelong AI companion.

Your job is to maintain useful, durable information explicitly stated by the user.

EXISTING MEMORIES:

{existing_memory_text}

USER MESSAGE (the only evidence source for this decision):

{user_message}

Do not treat an assistant response, implication, guess, or inference as evidence.
Only retain information that is explicitly attributable to the user's message.

Identify genuinely NEW durable information about the user.

Do NOT create duplicate memories even if they are worded differently.
If the user explicitly corrects or expands an existing memory, UPDATE it.

Prefer stable preferences, interests, hobbies, projects, relationships,
important facts, and meaningful events. Do not remember trivial small talk,
hypothetical statements, jokes, tests, or speculation.

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
"""

            try:
                response = self.llm.generate([], prompt)
                result = str(response or "").strip()

                if result.startswith("```"):
                    result = result.replace("```json", "").replace("```", "").strip()

                actions = json.loads(result)

                if not isinstance(actions, list):
                    return

                for action in actions:
                    if not isinstance(action, dict):
                        continue

                    action_type = str(action.get("action", "")).upper()
                    content = action.get("content")

                    if action_type not in {"ADD", "UPDATE"}:
                        continue

                    if not isinstance(content, str) or not content.strip():
                        continue

                    content = content.strip()

                    if not self._is_attributable_to_user(
                        content,
                        user_message
                    ):
                        continue

                    try:
                        if action_type == "ADD":
                            memory = self.add_memory(
                                action.get("category", ""),
                                content,
                                action.get("importance", 5),
                                source="user_message",
                                source_message=user_message
                            )

                            if memory:
                                print(f"\n[Memory saved: {content}]")

                        else:
                            memory = self.update_memory(
                                action.get("id"),
                                category=action.get("category"),
                                content=content,
                                importance=action.get("importance"),
                                source="user_message",
                                source_message=user_message
                            )

                            if memory:
                                print(
                                    f"\n[Memory updated: "
                                    f"{memory['content']}]"
                                )

                    except (MemoryDataError, ValueError, TypeError) as error:
                        print(f"\nMemory action skipped: {error}")

            except Exception as error:
                print(f"\nMemory check failed: {error}")
