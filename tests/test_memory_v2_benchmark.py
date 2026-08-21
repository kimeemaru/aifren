import unittest

from benchmarks.memory_v2.adapters import GoldReferenceAdapter, MemoryV1StructuralAdapter, MemoryV2StructuralAdapter
from benchmarks.memory_v2.fixtures import build_core_fixture, generate_scale_fixture, structural_baseline_cases
from benchmarks.memory_v2.harness import run_retrieval_benchmark
from benchmarks.memory_v2.metrics import evaluate_consolidation, evaluate_retrieval
from benchmarks.memory_v2.models import ClaimProposal, EmbeddingIdentity, RetrievalOutcome, RetrievalQuery, RetrievalTrace


class MemoryV2BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = build_core_fixture()

    def test_core_fixture_has_source_traceability_and_negative_cases(self):
        event_ids = {event.event_id for event in self.fixture.events}
        self.assertGreaterEqual(len({event.character_id for event in self.fixture.events}), 2)
        self.assertTrue(any(case.is_negative for case in self.fixture.retrieval_cases))
        for claim in self.fixture.claims:
            self.assertTrue(claim.source_event_ids)
            self.assertTrue(set(claim.source_event_ids).issubset(event_ids))

    def test_core_fixture_covers_non_factual_and_temporal_source_cases(self):
        contents = " ".join(event.content.lower() for event in self.fixture.events)
        self.assertIn("if i moved to mars", contents)
        self.assertIn("castle on the moon", contents)
        self.assertIn("might visit quebec", contents)
        self.assertIn("mara and marina", contents)
        self.assertIn("completed our museum trip", contents)
        self.assertEqual(
            next(claim for claim in self.fixture.claims if claim.claim_id == "serval-tea-green").source_event_ids,
            ("serval-008", "serval-013"),
        )

    def test_reference_adapter_validates_gold_retrieval_contract(self):
        report, retrieved = run_retrieval_benchmark(self.fixture, GoldReferenceAdapter())
        self.assertEqual(report.recall_at_k, 1.0)
        self.assertEqual(report.mean_reciprocal_rank, 1.0)
        self.assertEqual(report.negative_false_positive_rate, 0.0)
        self.assertEqual(report.character_isolation_violation_rate, 0.0)
        self.assertEqual(report.provenance_completeness, 1.0)
        self.assertEqual(retrieved["allergy-needle"], ["serval-walnut-allergy"])
        self.assertEqual(retrieved["unrelated-guitar"], [])

    def test_consolidation_metrics_detect_unsupported_and_bad_supersession(self):
        valid = [
            ClaimProposal(claim.claim_id, claim.character_id, claim.source_event_ids, claim.status, claim.superseded_by)
            for claim in self.fixture.claims
        ]
        valid_report = evaluate_consolidation(self.fixture, valid)
        self.assertEqual(valid_report.unsupported_claim_rate, 0.0)
        self.assertEqual(valid_report.supersession_correctness, 1.0)
        self.assertEqual(valid_report.temporal_correctness, 1.0)

        invalid = valid + [ClaimProposal("unsupported", "serval", ("not-an-event",))]
        invalid_report = evaluate_consolidation(self.fixture, invalid)
        self.assertGreater(invalid_report.unsupported_claim_rate, 0.0)
        self.assertLess(invalid_report.provenance_completeness, 1.0)

    def test_memory_v1_structural_adapter_runs_without_runtime_changes(self):
        adapter = MemoryV1StructuralAdapter(self.fixture)
        report, retrieved = run_retrieval_benchmark(self.fixture, adapter)
        self.assertEqual(report.query_count, len(self.fixture.retrieval_cases))
        self.assertIn("allergy-needle", retrieved)
        self.assertGreater(report.character_isolation_violation_rate, 0.0)
        self.assertGreaterEqual(report.latency_seconds, 0.0)
        self.assertGreaterEqual(report.peak_memory_bytes, 0)

    def test_memory_v2_structural_adapter_meets_the_core_gates(self):
        report, _ = run_retrieval_benchmark(
            self.fixture, MemoryV2StructuralAdapter(self.fixture),
            cases=structural_baseline_cases(self.fixture),
        )
        self.assertEqual(report.character_isolation_violation_rate, 0.0)
        self.assertEqual(report.provenance_completeness, 1.0)
        self.assertEqual(report.temporal_correctness, 1.0)
        self.assertEqual(report.forbidden_retrieval_rate, 0.0)
        self.assertEqual(report.negative_false_positive_rate, 0.0)

    def test_large_history_is_generated_on_demand(self):
        fixture = generate_scale_fixture(1000)
        self.assertEqual(len(fixture.events), 1000)
        self.assertEqual(len(fixture.claims), 1000)
        self.assertEqual(fixture.claims[0].source_event_ids, ("scale-needle-event",))

    def test_expanded_fixture_has_future_retrieval_contract_coverage(self):
        case_ids = {case.case_id for case in self.fixture.retrieval_cases}
        self.assertGreaterEqual(len(self.fixture.events), 29)
        self.assertGreaterEqual(len(self.fixture.claims), 24)
        self.assertGreaterEqual(len(self.fixture.retrieval_cases), 40)
        self.assertTrue({
            "paraphrase-semantic", "exact-phrase", "identifier", "alias",
            "assistant-echo-trap", "recent-visible-duplicate",
            "channel-dominance", "embedding-fingerprint-mismatch",
            "one-hop-relationship", "fts-punctuation-safety", "trace-abstention",
        }.issubset(case_ids))
        self.assertFalse(next(case for case in self.fixture.retrieval_cases if case.case_id == "paraphrase-semantic").deterministic_only)
        self.assertEqual(len(structural_baseline_cases(self.fixture)), 12)

    def test_retrieval_query_excludes_assistant_turns_and_is_bounded(self):
        query = RetrievalQuery("serval", "What tea do I prefer?", "2026-08-01T00:00:00Z", recent_user_turns=("I had tea yesterday.",))
        self.assertEqual(query.recent_user_turns, ("I had tea yesterday.",))
        with self.assertRaises(ValueError):
            RetrievalQuery("serval", "x", "2026-08-01T00:00:00Z", recent_user_turns=("x",) * 9)

    def test_embedding_identity_rejects_untrustworthy_contracts(self):
        current = EmbeddingIdentity("local", "model-a", 384, "pre-v1", "content-v1")
        self.assertEqual(current.status, "current")
        with self.assertRaises(ValueError):
            EmbeddingIdentity("local", "model-a", 0, "pre-v1", "content-v1")
        with self.assertRaises(ValueError):
            EmbeddingIdentity("local", "model-a", 384, "pre-v1", "content-v1", "unknown")

    def test_trace_metrics_detect_missing_trace_and_stale_embedding_use(self):
        cases = tuple(case for case in self.fixture.retrieval_cases if case.case_id in {"trace-abstention", "stale-embedding"})
        retrieved = {"trace-abstention": [], "stale-embedding": ["serval-cobalt-needle"]}
        outcomes = {
            "trace-abstention": RetrievalOutcome((), (), "no_relevant_candidate"),
            "stale-embedding": RetrievalOutcome(("serval-cobalt-needle",), (RetrievalTrace("serval-cobalt-needle", ("semantic",), "semantic_score"),)),
        }
        fixture = type(self.fixture)(self.fixture.version, self.fixture.events, self.fixture.claims, cases)
        report = evaluate_retrieval(fixture, retrieved, outcomes_by_case=outcomes)
        self.assertEqual(report.trace_completeness, 1.0)
        self.assertGreater(report.stale_embedding_use_violation_rate, 0.0)

    def test_trace_metrics_require_channel_and_reason_for_selected_claims(self):
        cases = tuple(case for case in self.fixture.retrieval_cases if case.case_id == "duplicate-multi-channel")
        fixture = type(self.fixture)(self.fixture.version, self.fixture.events, self.fixture.claims, cases)
        report = evaluate_retrieval(
            fixture, {"duplicate-multi-channel": ["serval-pokemon-episode"]},
            outcomes_by_case={"duplicate-multi-channel": RetrievalOutcome(("serval-pokemon-episode",), (RetrievalTrace("serval-pokemon-episode", ("fts",)),))},
        )
        self.assertEqual(report.trace_completeness, 0.0)


if __name__ == "__main__":
    unittest.main()
