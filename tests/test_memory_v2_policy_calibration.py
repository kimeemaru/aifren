"""Synthetic regressions for V2 retrieval policy calibration.

These cases intentionally contain no local AIFren history or memory text.
"""

import unittest

from benchmarks.memory_v2.fixtures import build_core_fixture
from benchmarks.memory_v2.models import RetrievalQuery
from memory_v2_store import EmbeddingLifecycle, MemoryV2Store, SemanticRetrievalV2
from memory_v2_store.retrieval import _has_specific_medical_conflict, _tokens
from memory_v2_store.importer import import_fixture


class _Vectors:
    provider = "test"
    model = "policy"
    model_version = "1"
    dimensions = 2
    normalized = True
    dtype = "float32"
    preprocessing_fingerprint = "policy-v1"
    device = "cpu"

    def embed(self, texts):
        result = []
        for text in texts:
            lower = text.lower()
            if "warm sunset hue" in lower or "amber" in lower:
                result.append([1.0, 0.0])
            elif "nearby" in lower:
                result.append([0.28, 0.96])
            else:
                result.append([0.0, 1.0])
        return result


class PolicyCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.mapping = import_fixture(self.store, build_core_fixture())
        self.character = self.mapping["serval"]
        self.provider = _Vectors()
        self._add("policy-color", "preference", "The user's favorite color is amber.")
        self._add("policy-game", "preference", "The user's favorite game is Harbor Chess.")
        self._add("policy-neighbor", "profile_fact", "The user keeps a nearby comedy notebook.")
        self._add("policy-legacy", "profile_fact", "The user maintains the zephyr archive.", legacy=True)
        EmbeddingLifecycle(self.store, self.provider, include_legacy_unverified=True).rebuild_all()

    def tearDown(self):
        self.store.close()

    def _add(self, claim_id, claim_type, content, *, legacy=False):
        event_id = claim_id + "-event"
        self.store.add_event(self.character, event_id, 9_000_000 + len(claim_id), event_type="message", actor_kind="user", recorded_at_us=1, content_text=content)
        self.store.add_claim(self.character, claim_id, claim_type=claim_type, assertion_scope="user_fact", content=content, importance=5, provenance_state="legacy_unverified" if legacy else "complete")
        self.store.attach_evidence(self.character, claim_id, event_id, evidence_role="user_statement")

    def _retrieve(self, text, *, legacy=False):
        return SemanticRetrievalV2(self.store, embedding_provider=self.provider, allow_legacy_unverified=legacy).retrieve(
            RetrievalQuery(self.character, text, "2026-08-01T00:00:00Z")
        )

    def test_weak_fts_and_generic_questions_abstain(self):
        outcome = self._retrieve("Tell me something interesting about astronomy.")
        self.assertEqual(outcome.claim_ids, ())
        self.assertEqual(outcome.abstention_reason, "intent_generic_reasoning")

    def test_specific_medical_entities_do_not_cross_match(self):
        self.assertTrue(_has_specific_medical_conflict(
            _tokens("Do I have any food sensitivity to almonds?"),
            set(_tokens("The user is allergic to walnuts.")),
        ))
        self.assertFalse(_has_specific_medical_conflict(
            _tokens("Do walnuts make me sick?"),
            set(_tokens("The user is allergic to walnuts.")),
        ))

    def test_exact_identifier_remains_a_strong_exception(self):
        outcome = self._retrieve("Find N64-CA-0042 exactly.")
        self.assertIn("serval-n64-serial", outcome.claim_ids)

    def test_user_memory_paraphrase_recovers_but_near_neighbor_does_not(self):
        outcome = self._retrieve("What is my favorite warm sunset hue?")
        self.assertIn("policy-color", outcome.claim_ids)
        self.assertNotIn("policy-neighbor", outcome.claim_ids)
        selected = next(trace for trace in outcome.traces if trace.claim_id == "policy-color")
        self.assertEqual(selected.retrieval_intent, "user_memory")
        self.assertEqual(selected.relevance_gate, "passed")

    def test_ambiguity_and_assistant_opinion_abstain(self):
        ambiguous = self._retrieve("What's my favorite?")
        opinion = self._retrieve("What color do you like?")
        self.assertEqual(ambiguous.abstention_reason, "intent_ambiguous_memory")
        self.assertEqual(opinion.abstention_reason, "intent_assistant_opinion")

    def test_two_requested_slots_are_selected_within_existing_cap(self):
        outcome = self._retrieve("What is my favorite color and favorite game?")
        self.assertEqual(set(outcome.claim_ids), {"policy-color", "policy-game"})
        summary = next(trace for trace in outcome.traces if trace.claim_id == "__query__")
        self.assertEqual(summary.retrieval_intent, "multi_user_memory")
        self.assertEqual(set(summary.covered_slots), {"color", "game"})

    def test_legacy_claims_need_real_relevance_but_direct_match_survives(self):
        weak = self._retrieve("What fruit should I snack on?", legacy=True)
        direct = self._retrieve("What is my zephyr archive?", legacy=True)
        self.assertNotIn("policy-legacy", weak.claim_ids)
        self.assertIn("policy-legacy", direct.claim_ids)
        selected = next(trace for trace in direct.traces if trace.claim_id == "policy-legacy")
        self.assertEqual(selected.legacy_uncertainty_penalty, 0.20)


if __name__ == "__main__":
    unittest.main()
