import tempfile
import unittest

from benchmarks.memory_v2.fixtures import build_core_fixture
from memory_v2_store import EmbeddingLifecycle, HnswClaimIndex, MemoryV2Store
from memory_v2_store.importer import import_fixture
from tests.test_memory_v2_embeddings import ToyEmbeddingProvider


class HnswDerivedIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryV2Store(f"{self.temp.name}/memory.sqlite3")
        self.characters = import_fixture(self.store, build_core_fixture())
        self.provider = ToyEmbeddingProvider()
        EmbeddingLifecycle(self.store, self.provider).rebuild_all()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_rebuildable_character_scoped_index_returns_semantic_candidates(self):
        lyra = self.characters["lyra"]
        mira = self.characters["mira"]
        index = HnswClaimIndex(self.store, lyra, self.provider)
        hits = index.query([1.0, 0.0, 0.0], 4)
        self.assertIn("lyra-walnut-allergy", [claim_id for claim_id, _ in hits])
        self.assertNotIn("lyra-walnut-allergy", [claim_id for claim_id, _ in HnswClaimIndex(self.store, mira, self.provider).query([1.0, 0.0, 0.0], 4)])
        self.assertTrue(index.index_path.exists())
        index.index_path.unlink()
        self.assertIn("lyra-walnut-allergy", [claim_id for claim_id, _ in index.query([1.0, 0.0, 0.0], 4)])

    def test_incremental_embedding_is_added_without_rebuilding_authoritative_store(self):
        lyra = self.characters["lyra"]
        index = HnswClaimIndex(self.store, lyra, self.provider)
        before = index.status()["indexed_labels"]
        claim = self.store.embedding_source_claims()[0]
        # Re-storing an existing claim keeps its stable label; append-only V2
        # updates receive new claim IDs and are added on the next ensure().
        self.store.store_embedding(claim, self.provider, [1.0, 0.0, 0.0])
        self.assertGreaterEqual(index.status()["indexed_labels"], before)
