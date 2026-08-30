import unittest

from memory_v2_adaptive import AdaptiveShadowRetrieval, WorkingRecallCache


class AdaptiveShadowRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.cache = WorkingRecallCache(ttl_seconds=10.0)
        self.policy = AdaptiveShadowRetrieval(None, None, self.cache)

    def test_conservative_skip_and_explicit_recall_decisions(self):
        self.assertEqual("skip", self.policy.decide("a", "yeah").mode)
        self.assertEqual("deep", self.policy.decide("a", "Do you remember my coffee preference?").mode)
        self.assertEqual("normal", self.policy.decide("a", "I bought a new keyboard today").mode)

    def test_cache_is_character_scoped_topic_bound_and_expires(self):
        self.cache.put("a", "coffee preference", ("claim-a",), now=1.0)
        self.assertEqual(("claim-a",), self.cache.get("a", "what coffee do I like", now=2.0))
        self.assertEqual((), self.cache.get("b", "what coffee do I like", now=2.0))
        self.assertEqual((), self.cache.get("a", "tell me about astronomy", now=2.0))
        self.cache.put("a", "coffee preference", ("claim-a",), now=1.0)
        self.assertEqual((), self.cache.get("a", "coffee", now=11.0))

    def test_character_clear_prevents_cross_character_reuse(self):
        self.cache.put("a", "coffee preference", ("claim-a",), now=1.0)
        self.cache.clear("a")
        self.assertEqual((), self.cache.get("a", "coffee", now=2.0))


if __name__ == "__main__":
    unittest.main()
