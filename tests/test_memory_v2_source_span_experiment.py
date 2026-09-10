import re
import unittest

from memory_v2_source_span_experiment import (
    FROZEN_MICROPHONE_PROBES,
    MPNetEmbeddingProvider,
    run_source_span_experiment,
    run_temporal_signal_audit,
)


class _TokenProvider:
    """Small deterministic provider exercising source-to-claim mapping only."""

    dimensions = 7

    def embed(self, texts):
        vectors = []
        for text in texts:
            tokens = set(re.findall(r"[a-z]+", text.lower()))
            microphone = "microphone" in tokens or "mic" in tokens
            temporal = "years" in tokens
            vague_symptom = "terrible" in tokens or "horrible" in tokens
            direct_repair = "usb" in tokens or "extension" in tokens
            audio = "audio" in tokens
            coffee = "coffee" in tokens or "roast" in tokens
            location = "cedar" in tokens or "live" in tokens
            vectors.append([
                float(microphone), 3.0 if temporal else 0.0,
                float(vague_symptom), float(direct_repair),
                float(audio), float(coffee), float(location),
            ])
        return vectors


class SourceSpanExperimentTests(unittest.TestCase):
    def test_mpnet_challenger_is_explicitly_cpu_only_and_benchmark_scoped(self):
        self.assertEqual("sentence-transformers/all-mpnet-base-v2", MPNetEmbeddingProvider.model_name)
        self.assertEqual("cpu", MPNetEmbeddingProvider.device)

    def test_richer_canonical_spans_improve_frozen_vague_and_temporal_probes_without_leaks(self):
        report = run_source_span_experiment(scale=32, provider=_TokenProvider(), candidate_limit=16, ef=32)
        self.assertEqual(3, len(FROZEN_MICROPHONE_PROBES))
        self.assertFalse(report.baseline["vague_microphone"].top5)
        self.assertTrue(report.source_span["vague_microphone"].top5)
        self.assertTrue(report.source_span["temporal_microphone"].top5)
        self.assertEqual(0, report.hard_invariant_failures)
        self.assertEqual(0, report.factual_regressions)
        self.assertTrue(report.passed)
        self.assertLessEqual(report.candidate_bound, 32)
        self.assertNotIn("archived-camera", report.source_span["archive"].selected_claim_ids)
        self.assertNotIn("fact-coffee-current", report.source_span["isolation"].selected_claim_ids)

    def test_relative_age_lookup_is_bounded_and_does_not_scan_into_an_unidentified_session(self):
        report = run_temporal_signal_audit(
            scale=64,
            provider=_TokenProvider(),
            candidate_limits=(16,),
            ef=32,
        )
        for measurement in report.temporal_candidates.values():
            self.assertLessEqual(measurement.candidate_count, measurement.candidate_limit)
            self.assertGreater(measurement.matching_historical_records, measurement.candidate_count)
            self.assertNotIn("needle-audio", measurement.candidate_claim_ids)
            self.assertIsNone(measurement.final.expected_rank)
        self.assertEqual(0, report.hard_invariant_failures)
        self.assertFalse(report.passed)


if __name__ == "__main__":
    unittest.main()
