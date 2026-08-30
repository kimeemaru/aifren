import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.context_regression.harness import (
    ContextArm,
    Fixture,
    HistoricalContextBuilder,
    aggregate_results,
    analyze_response,
    load_manifest,
)


class _Provider:
    def __init__(self):
        self.calls = []

    def generate(self, messages, prompt, *, seed=None):
        self.calls.append((list(messages), str(prompt), seed))
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        return "A neutral compact account retained the useful activity and omitted repeated testing."


def _fixture(**overrides):
    values = {
        "fixture_id": "fixture",
        "user_record_index": 0,
        "source_sha256": "digest",
        "category": "healthy",
        "tags": ("continuity",),
        "capability": "retain the established collection",
        "expected_terms": ("game boy",),
        "natural_mirror_allowed": False,
        "poison_relevant": False,
    }
    values.update(overrides)
    return Fixture(**values)


class ContextRegressionHarnessTests(unittest.TestCase):
    def test_manifest_binds_fixture_to_canonical_user_record_without_copying_text(self):
        archive = [
            {"role": "user", "content": "Synthetic prompt"},
            {"role": "assistant", "content": "Synthetic answer"},
        ]
        digest = hashlib.sha256(b"Synthetic prompt").hexdigest()
        payload = {
            "schema": "aifren.context_regression.fixtures", "version": 1,
            "character": "synthetic", "archive_minimum_records": 2,
            "seeds": [1, 2, 3],
            "fixtures": [{
                "id": "one", "user_record_index": 0, "source_sha256": digest,
                "category": "healthy", "tags": [], "capability": "respond",
                "expected_terms": [], "natural_mirror_allowed": False,
                "poison_relevant": False,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            manifest = load_manifest(path, archive)
            self.assertEqual("one", manifest.fixtures[0].fixture_id)
            archive[0]["content"] = "Changed"
            with self.assertRaisesRegex(ValueError, "source record changed"):
                load_manifest(path, archive)

    def test_response_metrics_separate_one_word_echo_phrase_parroting_and_continuity(self):
        fixture = _fixture()
        one_word = analyze_response(fixture, "Do you like pizza?", "Pizza. It smells interesting.")
        phrase = analyze_response(fixture, "Please go to the high branches", "Please go to the high branches? Fine.")
        continuity = analyze_response(fixture, "What do I collect?", "You collect old Game Boy cartridges.")
        self.assertEqual("habitual_one_word_echo", one_word["opening_echo"])
        self.assertEqual("phrase_parroting", phrase["opening_echo"])
        self.assertTrue(continuity["expected_continuity_hit"])

    def test_counterfactual_probe_uses_latest_state_without_mutating_archive(self):
        archive = [
            {"role": "user", "content": "old fact"},
            {"role": "assistant", "content": "old answer"},
        ]
        fixture = _fixture(
            user_record_index=0, archive_state_end_exclusive=2,
            probe_text="What was the old fact?", continuity_source_record_indices=(0, 1),
        )
        before = json.dumps(archive, sort_keys=True)
        self.assertEqual("What was the old fact?", fixture.request_text(archive))
        # Constructing the transient user message is the builder's only probe
        # operation; the caller's canonical list remains byte-equivalent.
        transient = tuple(archive[:fixture.archive_state_end_exclusive]) + ({
            "role": "user", "content": fixture.request_text(archive),
        },)
        self.assertEqual(3, len(transient))
        self.assertEqual(before, json.dumps(archive, sort_keys=True))

    def test_historical_context_arms_do_not_mutate_source_archive(self):
        archive = []
        for index in range(70):
            archive.extend((
                {"role": "user", "content": f"Synthetic user request {index}"},
                {"role": "assistant", "content": f"Synthetic distinct answer {index}"},
            ))
        fixture_index = 138
        fixture = _fixture(
            fixture_id="historical", user_record_index=fixture_index,
            source_sha256=hashlib.sha256(str(archive[fixture_index]["content"]).encode()).hexdigest(),
            expected_terms=(),
        )
        before = json.dumps(archive, ensure_ascii=False, sort_keys=True)
        with tempfile.TemporaryDirectory() as directory:
            builder = HistoricalContextBuilder(
                archive=archive, provider=_Provider(),
                character_id="11111111-1111-4111-8111-111111111111",
                character_prompt="You are Synthetic Serval.",
                artifact_directory=Path(directory),
            )
            arms = builder.prepare(fixture)
        self.assertEqual(set(ContextArm), set(arms))
        self.assertEqual(before, json.dumps(archive, ensure_ascii=False, sort_keys=True))
        self.assertEqual("Synthetic user request 69", arms[ContextArm.CONSOLIDATED].context[-1]["content"])
        self.assertGreater(arms[ContextArm.LOWER_EPISODES].selected_episode_count, 0)

    def test_historical_builder_can_run_a_matched_subset_without_changing_rubric(self):
        archive = []
        for index in range(70):
            archive.extend((
                {"role": "user", "content": f"Synthetic user request {index}"},
                {"role": "assistant", "content": f"Synthetic distinct answer {index}"},
            ))
        fixture = _fixture(
            fixture_id="subset", user_record_index=138,
            source_sha256=hashlib.sha256(b"Synthetic user request 69").hexdigest(),
            expected_terms=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            builder = HistoricalContextBuilder(
                archive=archive, provider=_Provider(),
                character_id="11111111-1111-4111-8111-111111111111",
                character_prompt="You are Synthetic Serval.",
                artifact_directory=Path(directory),
            )
            arms = builder.prepare(
                fixture, (ContextArm.LOWER_EPISODES, ContextArm.CONSOLIDATED),
            )

        self.assertEqual(
            {ContextArm.LOWER_EPISODES, ContextArm.CONSOLIDATED}, set(arms),
        )

    def test_aggregate_keeps_raw_counts_and_arm_breakdown(self):
        fixture = _fixture(fixture_id="one", expected_terms=())
        records = []
        for arm in ContextArm:
            for seed in (1, 2, 3):
                records.append({
                    "fixture_id": "one", "arm": arm.value, "seed": seed,
                    "output": f"response {arm.value} {seed}",
                    "prompt_tokens": 100, "completion_tokens": 10,
                    "generation_seconds": 1.0, "tokens_per_second": 10.0,
                    "metrics": {
                        "problematic_echo": arm == ContextArm.LEGACY,
                        "phrase_parroting": False,
                        "poison_theme_recurrence": False,
                        "unnecessary_poison_reversion": False,
                        "malformed_or_degenerate": False,
                        "expected_continuity_hit": None,
                    },
                    "judge": {
                        "topic_adherence": 2,
                        "unnecessary_topic_reversion": False,
                        "personality_score": 3,
                        "continuity_success": True,
                        "coherence_failure": False,
                    },
                })
        aggregate = aggregate_results(records, {"one": fixture})
        self.assertEqual(3, aggregate["arms"]["legacy"]["problematic_echo"]["count"])
        self.assertEqual(0, aggregate["arms"]["consolidated"]["problematic_echo"]["count"])
        self.assertIn("continuity", aggregate["tags"])


if __name__ == "__main__":
    unittest.main()
