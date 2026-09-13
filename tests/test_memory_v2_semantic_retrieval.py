import unittest
import uuid

from benchmarks.memory_v2.fixtures import build_core_fixture
from aifren.memory_v2_store import MemoryV2Store, RetrievalLimits, SemanticRetrievalV2
from aifren.memory_v2_store.importer import import_fixture
from aifren.memory_v2_store.retrieval import (
    _has_asserted_event_mismatch,
    _has_explicit_attribute_mismatch,
    _has_possessive_role_mismatch,
    _tokens,
)


class _FixedSemanticRows(SemanticRetrievalV2):
    def __init__(self, store, rows):
        self._rows = list(rows)
        super().__init__(store)

    def _semantic_rows(self, _query, _text, _truth_scope_id):
        return list(self._rows)


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
        self.store.connection.execute("UPDATE claims SET content = content || ' drift' WHERE claim_id = 'lyra-n64'")
        self.assertFalse(self.store.fts_is_current())

    def test_fts_parser_safety_and_exact_identifier_lane(self):
        outcome = self.retrieve("identifier")
        self.assertEqual(outcome.claim_ids[0], "lyra-n64-serial")
        safe = self.retrieve("fts-punctuation-safety")
        self.assertIsInstance(safe.claim_ids, tuple)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM claims").fetchone()[0], 24)

    def test_unambiguous_alias_and_case_collision_remain_conservative(self):
        self.assertEqual(self.retrieve("alias").claim_ids[0], "lyra-n64")
        person = self.retrieve("case-collision-person")
        self.assertEqual(person.claim_ids, ("lyra-rose-person",))

    def test_character_status_and_historical_filtering(self):
        self.assertEqual(self.retrieve("mira-location-isolation").claim_ids, ("mira-skyhaven",))
        self.assertEqual(self.retrieve("current-tea").claim_ids, ("lyra-tea-green",))
        self.assertEqual(self.retrieve("historical-tea").claim_ids, ("lyra-tea-red",))
        self.assertEqual(self.retrieve("cancelled-plan").claim_ids, ("lyra-hike-cancelled",))

    def test_abstention_and_irrelevant_high_importance(self):
        unrelated = self.retrieve("unrelated-guitar")
        self.assertEqual(unrelated.claim_ids, ())
        self.assertIsNotNone(unrelated.abstention_reason)
        tea = self.retrieve("irrelevant-high-importance")
        self.assertIn("lyra-tea-green", tea.claim_ids)
        self.assertNotIn("lyra-passport-manual", tea.claim_ids)

    def test_explicit_attribute_mismatch_abstains(self):
        character = self.character_map["lyra"]
        _, template = self.query("alias")
        outcome = self.retriever.retrieve(type(template)(
            character, "What color is my Nintendo 64?", template.at, "ordinary", (),
        ))
        self.assertEqual((), outcome.claim_ids)
        trace = next(value for value in outcome.traces if value.claim_id == "lyra-n64")
        self.assertEqual("explicit_attribute_mismatch", trace.exclusion_reason)

    def test_attribute_relation_agreement_allows_implicit_value_wording(self):
        query = _tokens("What color collar does Pesto wear?")
        content = set(_tokens("Pesto wears a violet collar."))
        self.assertFalse(_has_explicit_attribute_mismatch(query, content))
        self.assertTrue(_has_explicit_attribute_mismatch(
            _tokens("What color is my Nintendo 64?"),
            set(_tokens("The user owns a Nintendo 64 console.")),
        ))

    def test_asserted_action_and_possessive_role_require_relation_support(self):
        self.assertTrue(_has_asserted_event_mismatch(
            "Which telescope did I buy?", "The user discussed a telescope guide.",
        ))
        self.assertFalse(_has_asserted_event_mismatch(
            "Which telescope did I buy?", "The user bought a compact telescope.",
        ))
        self.assertFalse(_has_asserted_event_mismatch(
            "Why did I move from Oslo?", "The user moved from Oslo in spring.",
        ))
        self.assertTrue(_has_possessive_role_mismatch(
            "Who is my dentist?", set(_tokens("The user owns a dental history book.")),
        ))
        self.assertFalse(_has_possessive_role_mismatch(
            "Who was my instructor?", set(_tokens("Jun Aras was the user's instructor.")),
        ))

    def test_rank_one_compacted_episode_has_narrow_paraphrase_floor(self):
        character = str(uuid.uuid4())
        self.store.create_character(character, "Paraphrase synthetic")
        self.store.add_event(character, "episode-source", 1, content_text="A ferry delayed dessert.")
        self.store.add_claim(
            character, "episode", claim_type="shared_episode",
            assertion_scope="shared_episode",
            content="A delayed ferry made the user serve lemon cake on the harbor steps.",
            provenance_state="complete",
        )
        self.store.attach_evidence(character, "episode", "episode-source")
        outcome = _FixedSemanticRows(self.store, (("episode", 0.53),)).retrieve(
            type(self.query("alias")[1])(
                character,
                "Where was dessert finally eaten because the boat was late?",
                "2032-01-01T00:00:00+00:00",
            )
        )
        self.assertEqual(("episode",), outcome.claim_ids)

    def test_visible_and_recent_suppression_with_explicit_repeat_override(self):
        self.assertEqual(self.retrieve("recent-visible-duplicate").claim_ids, ())
        normal_case, normal_query = self.query("explicit-repeat-override")
        normal_query = type(normal_query)(normal_query.character_id, normal_query.current_user_text, normal_query.at, "ordinary", normal_query.recent_user_turns)
        normal = self.retriever.retrieve(normal_query, recently_used_claim_ids=("lyra-pizza-joke",))
        self.assertEqual(normal.claim_ids, ())
        explicit = self.retrieve("explicit-repeat-override")
        self.assertIn("lyra-pizza-joke", explicit.claim_ids)

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

    def test_canonical_shared_episode_uses_structural_route_and_typed_label(self):
        character = str(uuid.uuid4())
        self.store.create_character(character, "Episode routing")
        self.store.add_event(character, "episode-event", 1, content_text="We repaired the microphone.")
        self.store.add_claim(character, "canonical-shared-episode", claim_type="shared_episode",
                             assertion_scope="shared_episode", content="We repaired the microphone together.",
                             provenance_state="complete")
        self.store.attach_evidence(character, "canonical-shared-episode", "episode-event")
        outcome = SemanticRetrievalV2(self.store).retrieve(
            type(self.query("alias")[1])(character, "Do you remember the microphone repair?", "2032-01-01T00:00:00+00:00", "ordinary", ())
        )
        self.assertIn("canonical-shared-episode", outcome.claim_ids)
        selected = next(item for item in outcome.selected_memories if item.claim_id == "canonical-shared-episode")
        self.assertEqual("SHARED EPISODE", selected.label)


if __name__ == "__main__":
    unittest.main()
