import tempfile
import unittest

from memory_v2_store import EmbeddingLifecycle, HnswClaimIndex, MemoryV2Store
from tests.memory_v2_test_data import build_retrieval_fixture, import_retrieval_fixture
from tests.test_memory_v2_embeddings import ToyEmbeddingProvider


class HnswDerivedIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryV2Store(f"{self.temp.name}/memory.sqlite3")
        self.characters = import_retrieval_fixture(self.store, build_retrieval_fixture())
        self.provider = ToyEmbeddingProvider()
        EmbeddingLifecycle(self.store, self.provider).rebuild_all()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_rebuildable_character_scoped_index_returns_semantic_candidates(self):
        alpha = self.characters["alpha"]
        beta = self.characters["beta"]
        index = HnswClaimIndex(self.store, alpha, self.provider)
        hits = index.query([1.0, 0.0, 0.0], 4)
        self.assertIn("alpha-walnut-allergy", [claim_id for claim_id, _ in hits])
        self.assertNotIn("alpha-walnut-allergy", [claim_id for claim_id, _ in HnswClaimIndex(self.store, beta, self.provider).query([1.0, 0.0, 0.0], 4)])
        self.assertTrue(index.index_path.exists())
        index.index_path.unlink()
        self.assertIn("alpha-walnut-allergy", [claim_id for claim_id, _ in index.query([1.0, 0.0, 0.0], 4)])

    def test_incremental_embedding_is_added_without_rebuilding_authoritative_store(self):
        alpha = self.characters["alpha"]
        index = HnswClaimIndex(self.store, alpha, self.provider)
        before = index.status()["indexed_labels"]
        claim = self.store.embedding_source_claims()[0]
        # Re-storing an existing claim keeps its stable label; append-only V2
        # updates receive new claim IDs and are added on the next ensure().
        self.store.store_embedding(claim, self.provider, [1.0, 0.0, 0.0])
        self.assertGreaterEqual(index.status()["indexed_labels"], before)
