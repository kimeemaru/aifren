import unittest

from benchmarks.memory_v2.fixtures import build_core_fixture
from memory_v2_store import MemoryV2Store, RetrievalLimits, SemanticRetrievalV2
from memory_v2_store.importer import import_fixture


class SemanticRetrievalV2Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = build_core_fixture()
        self.store = MemoryV2Store()
        self.character_map = import_fixture(self.store, self.fixture)
        self.retriever = SemanticRetrievalV2(self.store)

    def tearDown(self):
        self.store.close()

    def query(self, case_id):
        case = next(item for item in self.fixture.retrieval_cases if item.case_id == case_id)
        query = case.retrieval_query
        query = type(query)(self.character_map[query.character_id], query.current_user_text, query.at, query.mode, query.recent_user_turns)
        return case, query

    def retrieve(self, case_id, **kwargs):
        case, query = self.query(case_id)
        return self.retriever.retrieve(
            query, recent_visible_claim_ids=case.recent_visible_claim_ids,
            recently_used_claim_ids=case.recently_used_claim_ids,
            embedding_state=case.embedding_state, final_count=case.final_injection_cap,
            token_budget=case.final_token_budget, **kwargs,
        )

    def test_fts_rebuild_is_derived_and_detects_drift(self):
        self.assertTrue(self.store.fts_available())
        self.assertTrue(self.store.fts_is_current())
        self.store.connection.execute("DELETE FROM claims_fts")
        self.assertFalse(self.store.fts_is_current())
        self.assertGreater(self.store.ensure_fts(), 0)
        self.assertTrue(self.store.fts_is_current())
        self.store.connection.execute("UPDATE claims SET content = content || ' drift' WHERE claim_id = 'serval-n64'")
        self.assertFalse(self.store.fts_is_current())

    def test_fts_parser_safety_and_exact_identifier_lane(self):
        outcome = self.retrieve("identifier")
        self.assertEqual(outcome.claim_ids[0], "serval-n64-serial")
        safe = self.retrieve("fts-punctuation-safety")
        self.assertIsInstance(safe.claim_ids, tuple)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM claims").fetchone()[0], 24)

    def test_unambiguous_alias_and_case_collision_remain_conservative(self):
        self.assertEqual(self.retrieve("alias").claim_ids[0], "serval-n64")
        person = self.retrieve("case-collision-person")
        self.assertEqual(person.claim_ids, ("serval-rose-person",))

    def test_character_status_and_historical_filtering(self):
        self.assertEqual(self.retrieve("mira-location-isolation").claim_ids, ("mira-skyhaven",))
        self.assertEqual(self.retrieve("current-tea").claim_ids, ("serval-tea-green",))
        self.assertEqual(self.retrieve("historical-tea").claim_ids, ("serval-tea-red",))
        self.assertEqual(self.retrieve("cancelled-plan").claim_ids, ("serval-hike-cancelled",))

    def test_abstention_and_irrelevant_high_importance(self):
        unrelated = self.retrieve("unrelated-guitar")
        self.assertEqual(unrelated.claim_ids, ())
        self.assertIsNotNone(unrelated.abstention_reason)
        tea = self.retrieve("irrelevant-high-importance")
        self.assertIn("serval-tea-green", tea.claim_ids)
        self.assertNotIn("serval-passport-manual", tea.claim_ids)

    def test_visible_and_recent_suppression_with_explicit_repeat_override(self):
        self.assertEqual(self.retrieve("recent-visible-duplicate").claim_ids, ())
        normal_case, normal_query = self.query("explicit-repeat-override")
        normal_query = type(normal_query)(normal_query.character_id, normal_query.current_user_text, normal_query.at, "ordinary", normal_query.recent_user_turns)
        normal = self.retriever.retrieve(normal_query, recently_used_claim_ids=("serval-pizza-joke",))
        self.assertEqual(normal.claim_ids, ())
        explicit = self.retrieve("explicit-repeat-override")
        self.assertIn("serval-pizza-joke", explicit.claim_ids)

    def test_hard_caps_dedup_typed_output_and_trace_completeness(self):
        case, query = self.query("channel-dominance")
        tiny = SemanticRetrievalV2(self.store, RetrievalLimits(exact_candidates=1, fts_candidates=1, structural_candidates=1, final_count=1, token_budget=20))
        outcome = tiny.retrieve(query)
        self.assertLessEqual(len(outcome.claim_ids), 1)
        self.assertEqual(len(outcome.claim_ids), len(set(outcome.claim_ids)))
        self.assertLessEqual(sum(item.estimated_tokens for item in outcome.selected_memories), 20)
        self.assertTrue(all(item.label in {"CURRENT USER FACT", "HISTORICAL USER FACT", "SHARED EPISODE", "TEMPORARY/PLAN", "LEGACY UNVERIFIED"} for item in outcome.selected_memories))
        selected = [trace for trace in outcome.traces if trace.selection_state == "selected"]
        self.assertTrue(all(trace.candidate_channels and trace.selection_reason and trace.score_components for trace in selected))
        summary = next(trace for trace in outcome.traces if trace.claim_id == "__query__")
        self.assertEqual(summary.query_mode, case.query_mode)
        self.assertIsNotNone(summary.deduplicated_candidate_count)

    def test_stale_embedding_state_abstains_without_using_derived_vector_data(self):
        outcome = self.retrieve("stale-embedding")
        self.assertEqual(outcome.claim_ids, ())
        self.assertEqual(outcome.abstention_reason, "embedding_state_not_current")

    def test_deterministic_repeatability(self):
        first = self.retrieve("exact-phrase")
        second = self.retrieve("exact-phrase")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
