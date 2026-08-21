"""Benchmark adapters. They are test harnesses, not production implementations."""

import hashlib
import math
import re
import threading
from datetime import datetime, timezone

from memory.memory import Memory, generate_memory_keywords
from memory_v2_store import MemoryV2Store
from memory_v2_store.retrieval import SemanticRetrievalV2
from memory_v2_store.embeddings import EmbeddingLifecycle, MiniLMEmbeddingProvider
from memory_v2_store.importer import import_fixture
from memory_v2_store.store import parse_timestamp_us


class GoldReferenceAdapter:
    """Fixture oracle used to validate fixture annotations and metric plumbing."""

    name = "gold-reference"

    def retrieve(self, fixture, case, limit=5):
        return list(case.expected_claim_ids[:limit])


class DeterministicEmbeddingModel:
    """Small token-hash embedding for reproducible structural V1 evaluation."""

    dimensions = 384

    def encode(self, text):
        values = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", str(text).lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            values[int.from_bytes(digest[:2], "big") % self.dimensions] += 1.0
        magnitude = math.sqrt(sum(value * value for value in values))
        return values if not magnitude else [value / magnitude for value in values]


class MemoryV1StructuralAdapter:
    """Thin adapter around production V1 ranking with a deterministic embedding.

    It intentionally supplies all characters and all historical claim states:
    V1 has neither character scope nor supersession/temporal filtering. Results
    are therefore a structural baseline, not MiniLM production-quality claims.
    """

    name = "memory-v1-structural"

    def __init__(self, fixture):
        self.claim_id_by_memory_id = {}
        self.memory = Memory.__new__(Memory)
        self.memory._lock = threading.RLock()
        self.memory.embedding_model = DeterministicEmbeddingModel()
        self.memory.llm = None
        self.memory.memories = []
        for index, claim in enumerate(fixture.claims, start=1):
            record = {
                "id": index,
                "category": claim.category,
                "content": claim.content,
                "importance": claim.importance,
                "keywords": [],
                "embedding": self.memory.embedding_model.encode(claim.content),
                "created": "2000-01-01T00:00:00Z",
                "updated": "2000-01-01T00:00:00Z",
            }
            record["keywords"] = generate_memory_keywords(record)
            self.memory.memories.append(record)
            self.claim_id_by_memory_id[index] = claim.claim_id

    def retrieve(self, fixture, case, limit=5):
        records = self.memory.get_relevant_memories(case.query, max_memories=limit)
        return [self.claim_id_by_memory_id[record["id"]] for record in records]


class MemoryV2StructuralAdapter:
    """SQLite structural filtering followed by small deterministic lexical ranking."""

    name = "memory-v2-structural"

    def __init__(self, fixture):
        self.store = MemoryV2Store()
        self.character_map = import_fixture(self.store, fixture)

    def close(self):
        self.store.close()

    @staticmethod
    def _tokens(text):
        aliases = {"allergy": "allerg", "allergic": "allerg", "hiking": "hike", "lives": "live", "liked": "like"}
        ignored = {"can", "you", "me", "about", "what", "do", "i", "we", "our", "the", "a", "an", "is", "are", "did", "in", "now", "still", "again", "tell", "how", "where", "when", "my", "weather", "today"}
        result = set()
        for token in re.findall(r"[a-z0-9]+", str(text).lower()):
            token = aliases.get(token, token)
            if token.endswith("ing") and len(token) > 5:
                token = token[:-3]
            elif token.endswith("ed") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            if token not in ignored:
                result.add(token)
        return result

    @staticmethod
    def _historical_time(case):
        years = re.findall(r"\b(19\d{2}|20\d{2})\b", case.query)
        if not years:
            return False, parse_timestamp_us(case.at)
        year = int(years[-1])
        return True, int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1_000_000)

    def retrieve(self, fixture, case, limit=5):
        historical, at_us = self._historical_time(case)
        candidates = self.store.structural_claims(
            self.character_map[case.character_id], at_us, historical=historical,
            exclude_claim_ids=case.recently_used_claim_ids,
        )
        query_tokens = self._tokens(case.query)
        scored = []
        for row in candidates:
            overlap = query_tokens & self._tokens(row["content"] + " " + row["claim_type"])
            if overlap:
                scored.append((len(overlap), row["importance"], row["claim_id"]))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if not scored:
            return []
        best_score = scored[0][0]
        return [claim_id for score, _, claim_id in scored if score == best_score][:limit]


class SemanticRetrievalV2Adapter:
    """Adapter for the isolated FTS5/lexical V2 retrieval engine, never runtime."""

    name = "semantic-retrieval-v2-isolated"

    def __init__(self, fixture):
        self.store = MemoryV2Store()
        self.character_map = import_fixture(self.store, fixture)
        self.retriever = SemanticRetrievalV2(self.store)

    def close(self):
        self.store.close()

    def retrieve_outcome(self, fixture, case, limit=5):
        query = case.retrieval_query
        # Benchmark fixture character IDs are labels; the store exposes UUIDs.
        query = type(query)(self.character_map[query.character_id], query.current_user_text, query.at, query.mode, query.recent_user_turns)
        return self.retriever.retrieve(
            query,
            recent_visible_claim_ids=case.recent_visible_claim_ids,
            recently_used_claim_ids=case.recently_used_claim_ids,
            embedding_state=case.embedding_state,
            final_count=min(limit, case.final_injection_cap),
            token_budget=case.final_token_budget,
        )

    def retrieve(self, fixture, case, limit=5):
        return list(self.retrieve_outcome(fixture, case, limit).claim_ids)


class HybridSemanticRetrievalV2Adapter(SemanticRetrievalV2Adapter):
    """Synthetic-only V2 hybrid benchmark using the local MiniLM model."""

    name = "semantic-retrieval-v2-hybrid-isolated"

    def __init__(self, fixture):
        self.store = MemoryV2Store()
        self.character_map = import_fixture(self.store, fixture)
        self.provider = MiniLMEmbeddingProvider()
        self.embedding_build = EmbeddingLifecycle(self.store, self.provider).rebuild_all()
        self.retriever = SemanticRetrievalV2(self.store, embedding_provider=self.provider)
