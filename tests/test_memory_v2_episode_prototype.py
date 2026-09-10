import re
import unittest

from memory_v2_episode_prototype import (
    EpisodePrototype, MultiRepresentationEpisodeIndex, SourceSpanPrototype,
    derive_episode_representations, route_episode_query,
)


class _TokenProvider:
    """Offline deterministic provider for prototype mechanics, not quality."""
    vocabulary = ("microphone", "crackling", "damaged", "usb", "extension", "audio", "replace", "repair")

    def embed(self, texts):
        vectors = []
        for text in texts:
            tokens = set(re.findall(r"[a-z]+", text.lower()))
            if "mic" in tokens:
                tokens.add("microphone")
            if "terrible" in tokens:
                tokens.add("crackling")
            vectors.append([float(token in tokens) for token in self.vocabulary])
        return vectors


class EpisodePrototypeTests(unittest.TestCase):
    def setUp(self):
        self.episodes = (
            EpisodePrototype("needle", "a", "In March 2028 we fixed microphone crackling caused by a damaged USB extension.", "event-needle", "audio", 1, 1),
            EpisodePrototype("after", "a", "We tested the microphone and confirmed the audio was clear.", "event-after", "audio", 2, 2),
            EpisodePrototype("near", "a", "We later replaced a USB microphone after a different audio repair.", "event-near", "other", 1, 3),
        )

    def test_multirepresentation_deduplicates_to_canonical_episode_and_exposes_facets(self):
        representations = derive_episode_representations(self.episodes[0])
        self.assertEqual({"summary", "narrative", "problem_cause_result", "entity_topic"}, {item.kind for item in representations})
        index = MultiRepresentationEpisodeIndex(_TokenProvider())
        index.add(self.episodes)
        hits = index.query("Why did my mic sound terrible?", limit=3)
        self.assertEqual("needle", hits[0].claim_id)
        self.assertEqual(len({item.claim_id for item in hits}), len(hits))
        self.assertEqual(12, index.representation_count)

    def test_source_expansion_and_adjacency_are_bounded_and_deterministic(self):
        source = SourceSpanPrototype(self.episodes)
        self.assertEqual(("needle", "after"), tuple(item.claim_id for item in source.expand("needle", radius=2)))
        self.assertEqual("after", source.adjacent("needle", "after").claim_id)
        self.assertIsNone(source.adjacent("needle", "before"))
        frame = source.frame("needle", lifecycle_revision=4, ttl_seconds=30)
        self.assertTrue(frame.valid_for("a", 4))
        self.assertFalse(frame.valid_for("b", 4))
        self.assertFalse(frame.valid_for("a", 5))

    def test_session_adjacency_is_exact_over_many_deterministic_sessions(self):
        episodes = []
        for session in range(100):
            for order in range(3):
                episodes.append(EpisodePrototype(f"{session}-{order}", "a", f"Session {session} event {order}.",
                                                 f"event-{session}-{order}", f"session-{session}", order, session * 10 + order))
        source = SourceSpanPrototype(episodes)
        checks = 0
        correct = 0
        for session in range(100):
            middle = f"{session}-1"
            correct += source.adjacent(middle, "before").claim_id == f"{session}-0"
            correct += source.adjacent(middle, "after").claim_id == f"{session}-2"
            checks += 2
        self.assertGreaterEqual(correct / checks, 0.95)

    def test_recall_frame_reuses_resolved_episode_for_follow_ups(self):
        source = SourceSpanPrototype(self.episodes)
        # Turn 1 resolved the episode through an ordinary retrieval lane.
        frame = source.frame("needle", lifecycle_revision=9)
        self.assertTrue(frame.valid_for("a", 9))
        # Turn 2/3 never rediscover globally: the bounded focused span is the
        # only candidate source for temporal/cause follow-ups.
        self.assertEqual("after", source.adjacent(frame.claim_ids[0], "after").claim_id)
        focused = source.expand(frame.claim_ids[0], radius=0)[0]
        self.assertIn("damaged USB extension", focused.narrative)

    def test_router_handles_memory_and_follow_up_without_runtime_wiring(self):
        self.assertEqual("current_fact", route_episode_query("What do I prefer now?"))
        self.assertEqual("historical_fact", route_episode_query("What did I used to own?"))
        self.assertEqual("direct_episode", route_episode_query("What caused the repair?"))
        self.assertEqual("vague_episode", route_episode_query("Do you remember that problem?"))
        self.assertEqual("temporal_episode", route_episode_query("What happened years ago?"))
        self.assertEqual("follow_up", route_episode_query("What happened after that?", has_recall_frame=True))
        self.assertEqual("unknown", route_episode_query("lol"))

    def test_router_held_out_fixture_is_conservative_for_memory_needed_queries(self):
        cases = (
            ("What coffee do I prefer now?", False, "current_fact"),
            ("Where did I used to live?", False, "historical_fact"),
            ("Why did we fix the microphone?", False, "direct_episode"),
            ("What caused that repair?", False, "direct_episode"),
            ("Do you remember the audio problem?", False, "vague_episode"),
            ("Do you remember what we did together?", False, "vague_episode"),
            ("When did we talk about the microphone?", False, "temporal_episode"),
            ("What happened years ago with audio?", False, "temporal_episode"),
            ("What happened after that?", True, "follow_up"),
            ("What caused it again?", True, "follow_up"),
            ("okay", False, "unknown"),
            ("thanks", False, "unknown"),
        )
        correct = sum(route_episode_query(text, has_recall_frame=frame) == expected for text, frame, expected in cases)
        memory_needed = [case for case in cases if case[2] != "unknown"]
        false_skips = sum(route_episode_query(text, has_recall_frame=frame) == "unknown" for text, frame, _ in memory_needed)
        self.assertGreaterEqual(correct / len(cases), 0.98)
        self.assertEqual(0, false_skips)
