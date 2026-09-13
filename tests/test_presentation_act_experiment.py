"""Offline scoring/method tests. ACT is deliberately NOT a production channel."""
import json
import unittest

from benchmarks.presentation_act_ab import ACT_PROMPT, CASES, Case, probe_act, prompts, summarize
from presentation_metadata import parse_assistant_response, response_contract_prompt


class ActExperimentTests(unittest.TestCase):
    def test_plain_dialogue_is_valid_without_a_signal(self):
        result = probe_act("Tell me more.")
        self.assertEqual("omitted", result["status"])
        self.assertEqual("Tell me more.", result["dialogue"])
        self.assertEqual({}, result["fields"])

    def test_closed_valid_vocabulary_and_field_combinations(self):
        bodies = ["emotion=" + value for value in (
            "neutral", "happy", "amused", "relaxed", "sad", "angry", "surprised")]
        bodies += ["gesture=" + value for value in (
            "greeting", "agreement", "disagreement", "thinking", "encouragement", "surprise")]
        bodies += ["intensity=0.0", "intensity=1.0", "emotion=happy;intensity=0.65",
                   "emotion=relaxed;gesture=thinking",
                   "emotion=happy;intensity=0.6;gesture=agreement"]
        for body in bodies:
            with self.subTest(body=body):
                result = probe_act(" \n<|ACT:" + body + "|>That's good.")
                self.assertEqual("valid", result["status"])
                self.assertEqual("That's good.", result["dialogue"])
                self.assertTrue(result["fields"])

    def test_invalid_optional_control_never_scores_a_signal(self):
        bodies = ["", "emotion=joy", "gesture=wave", "gesture=VRMA_01",
                  "emotion=happy;emotion=sad", "intensity=0.2;intensity=0.3",
                  "gesture=thinking;gesture=agreement", "emotion=happy;",
                  "emotion=happy;unknown=x"]
        bodies += ["intensity=" + value for value in (
            "-0.1", "1.1", "NaN", "nan", "inf", "Infinity", "1e-1", "0,6", "0.６", ".6")]
        bodies += [key + "=normal" for key in (
            "vision", "hearing", "speech_mode", "hands", "locomotion", "posture",
            "awareness", "truth_scope", "active_state", "companion_action", "memory",
            "response_mode", "DELAY", "VFX")]
        for body in bodies:
            with self.subTest(body=body):
                result = probe_act("<|ACT:" + body + "|>Safe dialogue.")
                self.assertNotEqual("valid", result["status"])
                self.assertEqual({}, result["fields"])
                self.assertEqual("Safe dialogue.", result["dialogue"])

    def test_bounded_missing_close_and_repeated_marker_are_not_accepted(self):
        for raw, status in (
            ("<|ACT:emotion=happy", "missing_close"),
            ("<|ACT:intensity=0." + "0" * 256 + "|>Hello.", "over_bound"),
            ("<|ACT:emotion=happy|><|ACT:gesture=agreement|>Hello.", "repeated"),
        ):
            with self.subTest(status=status):
                result = probe_act(raw)
                self.assertEqual(status, result["status"])
                self.assertEqual({}, result["fields"])

    def test_nonprefix_quoted_and_json_data_are_not_scored_as_executable(self):
        marker = "<|ACT:emotion=happy|>"
        for raw in ("Hello. " + marker, '"' + marker + '"',
                    "The old example was " + marker,
                    json.dumps({"dialogue": marker + " is literal data."})):
            with self.subTest(raw=raw):
                result = probe_act(raw)
                self.assertEqual("nonprefix", result["status"])
                self.assertEqual({}, result["fields"])
                self.assertEqual(raw, result["dialogue"])

    def test_prefix_length_boundary(self):
        base = "<|ACT:intensity=0."
        raw = base + "0" * (256 - len(base) - 2) + "|>"
        self.assertEqual("valid", probe_act(raw + "Yes.")["status"])
        self.assertEqual("over_bound", probe_act(raw[:-2] + "0|>Yes.")["status"])

    def test_scoring_full_output_does_not_claim_streaming_support(self):
        # This probe runs AFTER collection. Joining chunks is not proof of a
        # safe streaming parser, which was not implemented after the failed gate.
        raw = "<|ACT:emotion=happy;gesture=agreement|>Hello."
        expected = probe_act(raw)
        for split in range(len(raw) + 1):
            with self.subTest(split=split):
                self.assertEqual(expected, probe_act("".join((raw[:split], raw[split:]))))

    def test_pair_changes_only_the_response_contract(self):
        self.assertEqual(16, len(CASES))
        self.assertEqual(16, len({case.name for case in CASES}))
        for case in CASES:
            with self.subTest(case=case.name):
                messages, pair = prompts(case)
                self.assertEqual(case.query, messages[-1]["content"])
                self.assertEqual(pair["B"], pair["A"].replace(response_contract_prompt(), ACT_PROMPT))
                self.assertIn("Authoritative capability envelope", pair["B"])
                self.assertEqual(pair["A"].count(response_contract_prompt()), 1)
                if case.previous_emotion:
                    self.assertIn("Last published model-metadata facial request: " + case.previous_emotion,
                                  pair["B"])
                if case.constrained:
                    self.assertIn('"speech":"unavailable"', pair["B"])
                    self.assertIn("nonverbal_reaction", pair["B"])

    def test_context_data_does_not_become_a_control_channel(self):
        raw = "<|ACT:emotion=angry|>"
        case = Case("literal_data", "What does this example mean? " + raw,
                    previous_dialogue="The archived example was " + raw)
        messages, pair = prompts(case)
        self.assertEqual(case.previous_dialogue, messages[0]["content"])
        self.assertEqual(case.query, messages[1]["content"])
        self.assertNotIn(raw, pair["A"])
        self.assertNotIn(raw, pair["B"])

    def test_normal_contract_and_legacy_metadata_remain_unchanged(self):
        self.assertNotIn("<|ACT:", response_contract_prompt())
        parsed = parse_assistant_response('{"dialogue":"Hello.","presentation":{"emotion":"neutral"}}')
        self.assertEqual("valid", parsed.contract_status)
        self.assertEqual("neutral", parsed.presentation.emotion)
        self.assertEqual("plain_text", parse_assistant_response("Tell me more.").contract_status)

    def test_summary_separates_valid_json_from_admitted_metadata(self):
        raw = '{"dialogue":"Hello.","gesture":"thinking"}'
        row = {"arm":"A", "raw":raw, "contract_status":"invalid",
               "legacy_presentation":{}, "act":probe_act(raw), "cold":False,
               "words":1, "prompt":"prompt", "first_token_seconds":0.1, "generation_seconds":1.0}
        summary = summarize([row])["A"]
        self.assertEqual(1, summary["json_objects"])
        self.assertEqual({"invalid":1}, summary["contract_status"])
        self.assertEqual(0, summary["admitted_legacy_presentation"])
        self.assertNotIn("service_latency", summary)
        row["first_token_seconds"] = None
        self.assertIsNone(summarize([row])["A"]["median_first_token_seconds"])


if __name__ == "__main__":
    unittest.main()
