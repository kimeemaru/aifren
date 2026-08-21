import os
import tempfile
import unittest

from benchmarks.memory_v2.fixtures import build_core_fixture
from memory_v2_store import EmbeddingLifecycle, MemoryV2Store, RetrievalLimits, SemanticRetrievalV2
from memory_v2_store.importer import import_fixture


class ToyEmbeddingProvider:
    provider = "test"
    model = "toy-semantic"
    model_version = "1"
    dimensions = 3
    normalized = True
    dtype = "float32"
    preprocessing_fingerprint = "synthetic-v1"
    device = "cpu"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lower = text.lower()
            if any(word in lower for word in ("walnut", "allerg", "sick", "food sensitivity")):
                vectors.append([1.0, 0.0, 0.0])
            elif "guitar" in lower:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class FailingProvider(ToyEmbeddingProvider):
    model = "failing"

    def embed(self, texts):
        raise RuntimeError("synthetic provider failure")


class EmbeddingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = build_core_fixture()
        self.store = MemoryV2Store()
        self.characters = import_fixture(self.store, self.fixture)
        self.provider = ToyEmbeddingProvider()
        self.lifecycle = EmbeddingLifecycle(self.store, self.provider)

    def tearDown(self):
        self.store.close()

    def test_creation_dimensions_normalization_and_duplicate_rebuild(self):
        result = self.lifecycle.rebuild_all()
        self.assertEqual(result["embedded"], 24)
        self.assertEqual(result["current"], 24)
        row = self.store.connection.execute("SELECT * FROM claim_embeddings LIMIT 1").fetchone()
        self.assertEqual(row["dimensions"], 3)
        self.assertEqual(row["normalized"], 1)
        self.assertEqual(len(row["vector_blob"]), 12)
        second = self.lifecycle.rebuild_stale_or_missing()
        self.assertEqual(second["embedded"], 0)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM claim_embeddings").fetchone()[0], 24)

    def test_content_change_and_model_change_never_use_old_vector(self):
        self.lifecycle.rebuild_all()
        self.store.connection.execute("UPDATE claims SET content='changed synthetic content' WHERE claim_id='serval-walnut-allergy'")
        health = self.lifecycle.health()
        self.assertGreaterEqual(health["stale"], 1)
        character = self.characters["serval"]
        results = self.store.semantic_candidates(character, self.provider, [1.0, 0.0, 0.0], 10)
        self.assertNotIn("serval-walnut-allergy", [claim_id for claim_id, _ in results])
        changed = ToyEmbeddingProvider()
        changed.model_version = "2"
        self.assertGreater(self.store.mark_incompatible_embeddings_stale(changed), 0)
        self.assertEqual(self.store.semantic_candidates(character, changed, [1.0, 0.0, 0.0], 10), [])
        dimensional = ToyEmbeddingProvider()
        dimensional.dimensions = 4
        self.assertEqual(self.store.semantic_candidates(character, dimensional, [1.0, 0.0, 0.0, 0.0], 10), [])

    def test_failed_vectors_are_retryable_and_not_retrieved(self):
        failed = EmbeddingLifecycle(self.store, FailingProvider()).rebuild_all()
        self.assertEqual(failed["failed"], 24)
        self.assertEqual(failed["current"], 0)
        character = self.characters["serval"]
        self.assertEqual(self.store.semantic_candidates(character, FailingProvider(), [1.0, 0.0, 0.0], 5), [])

    def test_semantic_lane_is_character_scoped_and_stale_status_is_not_used(self):
        self.lifecycle.rebuild_all()
        serval = self.characters["serval"]
        mira = self.characters["mira"]
        self.assertIn("serval-walnut-allergy", [item[0] for item in self.store.semantic_candidates(serval, self.provider, [1.0, 0.0, 0.0], 5)])
        self.assertNotIn("serval-walnut-allergy", [item[0] for item in self.store.semantic_candidates(mira, self.provider, [1.0, 0.0, 0.0], 5)])
        self.store.connection.execute("UPDATE claim_embeddings SET state='stale' WHERE claim_id='serval-walnut-allergy'")
        self.assertNotIn("serval-walnut-allergy", [item[0] for item in self.store.semantic_candidates(serval, self.provider, [1.0, 0.0, 0.0], 5)])

    def test_reopen_persistence_and_semantic_paraphrase(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as file:
            path = file.name
        self.store.close()
        try:
            self.store = MemoryV2Store(path)
            self.characters = import_fixture(self.store, self.fixture)
            EmbeddingLifecycle(self.store, self.provider).rebuild_all()
            self.store.close()
            self.store = MemoryV2Store(path)
            character = self.characters["serval"]
            query = next(case for case in self.fixture.retrieval_cases if case.case_id == "paraphrase-semantic").retrieval_query
            query = type(query)(character, query.current_user_text, query.at, query.mode, query.recent_user_turns)
            outcome = SemanticRetrievalV2(self.store, RetrievalLimits(semantic_candidates=4), self.provider).retrieve(query)
            self.assertIn("serval-walnut-allergy", outcome.claim_ids)
            trace = next(item for item in outcome.traces if item.claim_id == "serval-walnut-allergy")
            self.assertIn("semantic", trace.candidate_channels)
        finally:
            self.store.close()
            self.store = MemoryV2Store()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
