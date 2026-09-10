import unittest
import uuid

from benchmarks.memory_v2.models import RetrievalOutcome, RetrievalQuery
from memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor
from memory_v2_hybrid_recall import HybridMemoryV2Recall
from memory_v2_store import MemoryV2Store


class _Semantic:
    def __init__(self, *claim_ids):
        self.claim_ids = tuple(claim_ids)
        self.calls = 0

    def retrieve(self, _query, **_kwargs):
        self.calls += 1
        return RetrievalOutcome(claim_ids=self.claim_ids)


class _EpisodeProvider:
    def generate(self, _context, prompt, *, seed=None):
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        lines = [
            line.split("USER: ", 1)[1]
            for line in prompt.splitlines() if "] USER: " in line
        ]
        return " ".join(lines) or "Synthetic compacted episode."


def _messages():
    rows = []
    for index in range(45):
        detail = (
            "We built a cardboard planetarium that projected Orion crookedly."
            if index == 0 else f"Routine synthetic exchange {index}."
        )
        rows.extend((
            {"role": "user", "content": detail, "timestamp": f"2026-01-01T00:{index:02d}:00Z"},
            {"role": "assistant", "content": "Acknowledged.", "timestamp": f"2026-01-01T00:{index:02d}:01Z"},
        ))
    return rows


class HybridMemoryV2RecallTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.character = str(uuid.uuid4())
        self.store.create_character(self.character, "Hybrid synthetic")

    def tearDown(self):
        self.store.close()

    def _add(self, claim_id, content, sequence, *, event_id=None, claim_type="shared_episode"):
        event_id = event_id or f"event-{claim_id}"
        if self.store.connection.execute(
            "SELECT 1 FROM events WHERE character_id=? AND event_id=?",
            (self.character, event_id),
        ).fetchone() is None:
            self.store.add_event(
                self.character, event_id, sequence, content_text=content,
                recorded_at_us=1_700_000_000_000_000 + sequence,
            )
        self.store.add_claim(
            self.character, claim_id, claim_type=claim_type,
            assertion_scope="shared_episode" if claim_type == "shared_episode" else "user_fact",
            content=content, provenance_state="complete",
        )
        self.store.attach_evidence(self.character, claim_id, event_id)

    def _query(self, text):
        return RetrievalQuery(self.character, text, "2026-08-30T00:00:00Z")

    def test_one_source_evidence_hop_is_bounded_and_projected(self):
        self._add("meeting", "We met Imani at Cedar Lake.", 1, event_id="shared")
        self._add("object", "Imani lent the user a brass compass.", 1, event_id="shared", claim_type="fact")
        semantic = _Semantic("meeting")
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=semantic,
        ).retrieve(self._query("What object was connected to the Cedar Lake meeting?"), limit=3)
        self.assertEqual(1, semantic.calls)
        self.assertEqual("object", result.candidates[0].memory_id)
        self.assertEqual("source_association", result.candidates[0].lane)
        self.assertEqual("meeting", result.candidates[0].associated_from)
        self.assertEqual(("shared",), tuple(value.source_id for value in result.candidates[0].evidence))

        direct = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("meeting"),
        ).retrieve(self._query("Tell me about the Cedar Lake meeting."), limit=3)
        self.assertEqual(("meeting",), tuple(value.memory_id for value in direct.candidates))

    def test_association_uses_only_strongest_anchor_and_abstains_on_unlinked_anchor(self):
        self._add("unlinked", "We met Imani at the Cedar Lake archive.", 1)
        self._add("linked", "We met Imani at Cedar Lake.", 2, event_id="shared")
        self._add(
            "object", "Imani lent the user a brass compass.", 2,
            event_id="shared", claim_type="fact",
        )
        result = HybridMemoryV2Recall(
            self.store, self.character,
            semantic_retriever=_Semantic("unlinked", "linked"),
        ).retrieve(self._query("What object was connected to meeting Imani at the Cedar Lake archive?"))
        self.assertEqual((), result.candidates)

    def test_relation_answer_suppresses_navigation_anchor(self):
        self._add("library", "We visited the Ash Library.", 10)
        self._add("cafe", "We visited the Lantern Cafe.", 11)
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("cafe"),
        ).retrieve(self._query("What did we do immediately before Lantern Cafe?"))
        self.assertEqual(("library",), tuple(value.memory_id for value in result.candidates))

    def test_causal_after_does_not_invoke_source_order_navigation(self):
        self._add("answer", "A burst pipe moved dinner into the railway waiting room.", 10)
        self._add("next-event", "A kite became stuck in a bell tower.", 11)
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("answer"),
        ).retrieve(self._query(
            "Where did my birthday meal move after the plumbing disaster?"
        ))
        self.assertEqual(("answer",), tuple(value.memory_id for value in result.candidates))

    def test_noun_only_false_premise_abstains(self):
        self._add("neighbor", "The user attended a violin concert.", 1, claim_type="fact")
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("neighbor"),
        ).retrieve(self._query("Why did I lose my violin?"))
        self.assertEqual((), result.candidates)

        self._add("event", "The user lost a violin during the train trip.", 2, claim_type="fact")
        supported = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("event"),
        ).retrieve(self._query("Why did I lose my violin?"))
        self.assertEqual(("event",), tuple(value.memory_id for value in supported.candidates))

    def test_weak_same_object_secondary_is_suppressed(self):
        self._add("wanted", "The silver thermos contains mint tea.", 1, claim_type="fact")
        self._add("wrong", "The white thermos contains coffee.", 2, claim_type="fact")
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("wanted", "wrong"),
        ).retrieve(self._query("What does the silver thermos contain?"))
        self.assertEqual(("wanted",), tuple(value.memory_id for value in result.candidates))

    def test_association_never_crosses_character_boundary(self):
        self._add("meeting", "We met Imani at Cedar Lake.", 1, event_id="shared")
        other = str(uuid.uuid4())
        self.store.create_character(other, "Other synthetic")
        self.store.add_event(other, "shared", 1, content_text="Another user owns a gold compass.")
        self.store.add_claim(
            other, "other-object", claim_type="fact", assertion_scope="user_fact",
            content="The other user owns a gold compass.", provenance_state="complete",
        )
        self.store.attach_evidence(other, "other-object", "shared")
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("meeting"),
        ).retrieve(self._query("What object was connected to the Cedar Lake meeting?"))
        self.assertNotIn("other-object", tuple(value.memory_id for value in result.candidates))

    def test_before_relation_uses_adjacent_authoritative_source_order(self):
        self._add("library", "We visited the Ash Library.", 10)
        self._add("cafe", "We visited the Lantern Cafe.", 11)
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("cafe"),
        ).retrieve(self._query("What did we do immediately before Lantern Cafe?"))
        self.assertEqual("library", result.candidates[0].memory_id)
        self.assertEqual("temporal_source_order", result.candidates[0].lane)

    def test_temporal_navigation_does_not_expand_a_second_semantic_anchor(self):
        self._add("library", "We visited the Ash Library.", 10)
        self._add("cafe", "We visited the Lantern Cafe.", 11)
        self._add("noise-before", "A clock workshop note.", 20)
        self._add("noise-anchor", "A clock museum guide.", 21)
        result = HybridMemoryV2Recall(
            self.store, self.character,
            semantic_retriever=_Semantic("cafe", "noise-anchor"),
        ).retrieve(self._query("What did we visit immediately before Lantern Cafe?"))
        self.assertEqual(("library",), tuple(value.memory_id for value in result.candidates))

    def test_first_uses_evidence_timestamp_without_recency_override(self):
        self._add("first", "The user attended a ceramics class.", 1)
        self._add("latest", "The user attended a ceramics class.", 3)
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic("latest", "first"),
        ).retrieve(self._query("When was my first ceramics class?"))
        self.assertEqual("first", result.candidates[0].memory_id)
        self.assertGreater(dict(result.candidates[0].signals)["temporal_extreme"], 0)

    def test_only_shared_validator_accepted_episode_participates(self):
        messages = _messages()
        cache = EpisodeCompactionCache(self.store, self.character)
        cache.rebuild(messages, EpisodeCompactor(_EpisodeProvider()))
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic(),
            episode_cache=cache, canonical_messages=messages,
        ).retrieve(self._query("What projected Orion crookedly?"))
        self.assertEqual(1, len(result.candidates))
        self.assertEqual("validated_episode", result.candidates[0].lane)
        self.assertEqual("validated_canonical_source_range", result.candidates[0].evidence[0].source_type)
        typed = cache.retrieve_candidates(messages, "What projected Orion crookedly?")
        self.assertEqual((result.candidates[0].memory_id,), tuple(
            value.record_id for value in typed.candidates
        ))

        changed = list(messages)
        changed[0] = {**changed[0], "content": "Source changed."}
        stale = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic(),
            episode_cache=cache, canonical_messages=changed,
        ).retrieve(self._query("What projected Orion crookedly?"))
        self.assertEqual((), stale.candidates)

    def test_unresolved_before_after_query_does_not_fall_back_to_episode_similarity(self):
        messages = _messages()
        cache = EpisodeCompactionCache(self.store, self.character)
        cache.rebuild(messages, EpisodeCompactor(_EpisodeProvider()))
        result = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=_Semantic(),
            episode_cache=cache, canonical_messages=messages,
        ).retrieve(self._query(
            "What happened immediately before the crooked Orion projection?"
        ))
        self.assertEqual((), result.candidates)

    def test_character_mismatch_and_malformed_episode_cache_fail_open(self):
        class BrokenCache:
            def validate_for_context(self, *_args, **_kwargs):
                raise ValueError("synthetic malformed cache")

        semantic = _Semantic()
        recall = HybridMemoryV2Recall(
            self.store, self.character, semantic_retriever=semantic,
            episode_cache=BrokenCache(), canonical_messages=(),
        )
        mismatch = recall.retrieve(RetrievalQuery(str(uuid.uuid4()), "Remember this", "2026-01-01T00:00:00Z"))
        self.assertEqual("character_mismatch", mismatch.abstention_reason)
        self.assertEqual(0, semantic.calls)
        self.assertEqual((), recall.retrieve(self._query("Remember malformed provenance")).candidates)

    def test_query_and_evidence_bounds_are_enforced(self):
        self._add("seed", "The user keeps a brass compass.", 1, claim_type="fact")
        recall = HybridMemoryV2Recall(self.store, self.character, semantic_retriever=_Semantic("seed"))
        with self.assertRaises(ValueError):
            recall.retrieve(self._query("compass"), limit=6)
        result = recall.retrieve(self._query("brass compass"), evidence_limit=1)
        self.assertLessEqual(len(result.candidates), 3)
        self.assertLessEqual(len(result.candidates[0].evidence), 1)


if __name__ == "__main__":
    unittest.main()
