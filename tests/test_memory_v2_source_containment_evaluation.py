import unittest

from memory_v2_answer_governance import MemoryAnswerEvidence
from memory_v2_replacement_evaluation import ReplacementEvaluationCase
from memory_v2_replacement_shadow import V2ReplacementContext
from memory_v2_source_containment import RECENT_POLICY_R0, RECENT_POLICY_R1
from memory_v2_source_containment_evaluation import (
    compose_contained_memory_context,
    run_source_containment_evaluation,
)


class _Provider:
    def __init__(self):
        self.calls = []

    def generate(self, context, prompt, seed=None):
        self.calls.append((tuple(context), prompt, seed))
        rendered = "\n".join(str(item.get("content", "")) for item in context)
        if "Harley-Davidson" in rendered:
            return "I remember the boop and your Harley-Davidson."
        return "I remember the boop."

    def generate_bounded(self, _context, _prompt, **_kwargs):
        return "I remember the boop."


def _design():
    from memory_v2_answer_governance import compose_memory_answer_requirement

    requirement = compose_memory_answer_requirement(
        "Do you remember when I booped you?",
        (MemoryAnswerEvidence(
            "boop-1", "historical_conversation_only", "user",
            "unknown_scope", "assertion", "boop",
        ),),
    )
    callback = type("Callback", (), {"triggered": False})()
    # Evaluation only relies on the typed fields below; use the production
    # dataclass so malformed designs fail at the same boundary.
    return V2ReplacementContext(
        context=(
            {"role": "user", "content": "[Long-term memory authority — backend policy]"},
            {"role": "user", "content": "Did I own a motorcycle?", "timestamp": "t1"},
            {"role": "assistant", "content": "You owned a Harley-Davidson.", "timestamp": "t2"},
            {"role": "user", "content": "Do you remember when I booped you?", "timestamp": "t3"},
        ),
        context_without_memory_answer_requirement=(),
        authority_block="authority",
        historical_block="history",
        items=(),
        callback_design=object(),
        callback_contract=callback,
        memory_answer_requirement=requirement,
        excluded=(),
        removed_v1_block_count=1,
        long_term_character_count=10,
        total_context_character_count=100,
        answer_requirement_character_count=10,
        composer_latency_ms=0.1,
    )


class MemoryV2SourceContainmentEvaluationTests(unittest.TestCase):
    def test_composition_separates_static_authority_from_recent_dialogue(self):
        design = _design()
        contained = compose_contained_memory_context(
            design, "Do you remember when I booped you?", RECENT_POLICY_R1,
        )
        rendered = "\n".join(str(item.get("content", "")) for item in contained.context)
        self.assertIn("Long-term memory authority", rendered)
        self.assertIn("continuity only", rendered)
        self.assertNotIn("Harley-Davidson", rendered)
        self.assertEqual("user", contained.recent_messages[-1]["role"])

    def test_matched_policy_run_records_raw_decoration_before_repair(self):
        provider = _Provider()
        source = {
            "case": ReplacementEvaluationCase(
                "boop_interaction", "historical_interaction",
                "Do you remember when I booped you?",
            ),
            "v2_design": _design(),
        }
        report = run_source_containment_evaluation(
            provider, "character", (source,),
            policies=(RECENT_POLICY_R0, RECENT_POLICY_R1), seeds=(7,),
        )
        by_policy = {row["policy"]: row for row in report["rows"]}
        self.assertFalse(by_policy[RECENT_POLICY_R0]["raw_pass"])
        self.assertEqual(
            "harley-davidson",
            by_policy[RECENT_POLICY_R0]["unrelated_recent_additions"][0]["anchor"],
        )
        self.assertTrue(by_policy[RECENT_POLICY_R0]["repair"]["validation"]["accepted"])
        self.assertTrue(by_policy[RECENT_POLICY_R1]["raw_pass"])
        self.assertEqual((), by_policy[RECENT_POLICY_R1]["unrelated_recent_additions"])
        self.assertFalse(report["production_prompt_influenced"])


if __name__ == "__main__":
    unittest.main()
