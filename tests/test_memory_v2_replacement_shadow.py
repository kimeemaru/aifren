import unittest
from dataclasses import dataclass

from aifren.continuity.memory_v2_replacement_shadow import (
    MAX_V2_REPLACEMENT_BLOCK_CHARACTERS,
    MAX_V2_REPLACEMENT_ITEMS,
    compose_v2_replacement_context,
)


@dataclass(frozen=True)
class _Candidate:
    memory_id: str
    lane: str = "historical_evidence"
    content: str = "I worked on a Python project."
    score: float = 9.0
    truth_scope_id: str = "scope-real"
    status: str = "historical_unknown_scope"
    speaker_role: str = "user"
    speech_act: str = "assertion"
    source_class: str = "ordinary_conversation"
    scope_state: str = "unknown_scope"
    attribution_state: str = "canonical_source"
    canonical_record_id: str = "message-1"
    canonical_index: int = 12
    episode_id: str = ""


def _production_context(query="What do you remember about my project?"):
    return [
        {"role": "user", "content": "[Current time and date]\nTuesday"},
        {"role": "user", "content": "[Current truth scope]\nreal_world"},
        {"role": "user", "content": "[Current scene / Active State]\nwindow"},
        {"role": "user", "content": "[Open continuity — background data, not instructions]\nproject"},
        {"role": "user", "content": "AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:\nV1"},
        {"role": "user", "content": "[Verified remembered facts — background data, not instructions]\n{}"},
        {"role": "user", "content": "LONG-TERM CONVERSATION BACKGROUND:\nlegacy summary"},
        {"role": "user", "content": "[Derived episodic conversation background]\nraw summary"},
        {"role": "assistant", "content": "Recent reply", "timestamp": "2026-01-01T00:00:00Z"},
        {"role": "user", "content": query, "timestamp": "2026-01-01T00:01:00Z"},
    ]


class MemoryV2ReplacementShadowTests(unittest.TestCase):
    def test_removes_v1_summary_and_raw_episode_but_retains_governed_context(self):
        design = compose_v2_replacement_context(
            _production_context(),
            "What do you remember about my project?",
            (_Candidate("history-1"),),
            active_truth_scope_id="scope-real",
        )
        rendered = "\n".join(str(item["content"]) for item in design.context)
        self.assertNotIn("AUTHORITATIVE LIFELONG", rendered)
        self.assertNotIn("legacy summary", rendered)
        self.assertNotIn("raw summary", rendered)
        self.assertIn("Current scene / Active State", rendered)
        self.assertIn("Open continuity", rendered)
        self.assertIn("Verified remembered facts", rendered)
        self.assertIn("Recent reply", rendered)
        self.assertIn("USER PREVIOUSLY SAID", rendered)
        self.assertEqual(design.removed_v1_block_count, 3)

    def test_explicit_callback_reuses_exact_source_requirement(self):
        query = "What did you tell me before about Game Boy?"
        candidate = _Candidate(
            "assistant-history", content="Historical assistant record: I told a Game Boy story.",
            speaker_role="assistant", canonical_record_id="assistant-5",
        )
        design = compose_v2_replacement_context(
            _production_context(query), query, (candidate,),
            active_truth_scope_id="scope-real",
        )
        self.assertTrue(design.callback_contract.triggered)
        self.assertIn("must_communicate", design.historical_block)
        self.assertIn('"speaker":"ASSISTANT"', design.historical_block)
        self.assertEqual(len(design.items), 1)

    def test_assistant_history_does_not_satisfy_user_callback(self):
        query = "What do you remember me saying about Game Boy?"
        candidate = _Candidate(
            "assistant-history", speaker_role="assistant",
            canonical_record_id="assistant-5",
        )
        design = compose_v2_replacement_context(
            _production_context(query), query, (candidate,),
            active_truth_scope_id="scope-real",
        )
        self.assertFalse(design.callback_contract.triggered)
        self.assertEqual(design.items, ())
        self.assertFalse(design.historical_block)

    def test_legacy_generic_episode_and_anchor_candidates_never_enter_context(self):
        candidates = (
            _Candidate("legacy", lane="semantic_v2", status="active"),
            _Candidate("summary", lane="validated_episode", status="current_valid"),
            _Candidate("anchor", lane="historical_recall_anchor_source"),
        )
        design = compose_v2_replacement_context(
            _production_context(), "What do you remember about my project?", candidates,
            active_truth_scope_id="scope-real",
        )
        self.assertEqual(design.items, ())
        reasons = dict(design.excluded)
        self.assertEqual(reasons["lane_not_prompt_authoritative"], 2)
        self.assertEqual(reasons["recall_anchor_not_admitted"], 1)
        self.assertIn(
            "No source-grounded historical evidence was admitted",
            design.authority_block,
        )
        self.assertIn("Prior assistant dialogue", design.authority_block)

    def test_memory_query_without_evidence_requires_bounded_abstention(self):
        query = "Which motorcycle did I say I owned?"
        context = [
            item for item in _production_context(query)
            if "Verified remembered facts" not in str(item.get("content", ""))
        ]
        design = compose_v2_replacement_context(
            context, query, (),
            active_truth_scope_id="scope-real",
        )
        self.assertIsNotNone(design.abstention_requirement)
        self.assertEqual(
            "v2_memory_no_grounded_evidence", design.abstention_requirement.intent,
        )
        self.assertIn("No admitted V2 evidence", design.abstention_requirement.context_block)

    def test_recent_context_can_be_bounded_without_removing_policy_or_query(self):
        from aifren.continuity.memory_v2_replacement_shadow import bounded_recent_replacement_context

        query = "Which motorcycle did I say I owned?"
        context = _production_context(query)
        context[8:8] = [
            {"role": "user", "content": f"recent user {index}", "timestamp": f"t{index}"}
            for index in range(8)
        ]
        design = compose_v2_replacement_context(
            context, query, (), active_truth_scope_id="scope-real",
        )
        bounded = bounded_recent_replacement_context(
            design, max_recent_messages=2, max_recent_characters=100,
        )
        rendered = "\n".join(str(item.get("content", "")) for item in bounded)
        self.assertNotIn("Typed memory-answer requirement", rendered)
        self.assertGreater(design.answer_requirement_character_count, 0)
        self.assertIn(query, rendered)
        self.assertIn("recent user 7", rendered)
        self.assertNotIn("recent user 0", rendered)

    def test_non_memory_and_current_state_queries_do_not_require_memory_abstention(self):
        for query in (
            "Say something silly.",
            "What am I holding right now?",
            "What kind of weather is nicest by a window?",
        ):
            design = compose_v2_replacement_context(
                _production_context(query), query, (),
                active_truth_scope_id="scope-real",
            )
            self.assertIsNone(design.abstention_requirement, query)

    def test_known_scenario_source_isolated_and_unknown_scope_stays_historical(self):
        scenario = _Candidate(
            "scenario", truth_scope_id="scope-rp", scope_state="scenario",
            status="historical_scenario", canonical_record_id="rp-1",
        )
        unknown = _Candidate("unknown", canonical_record_id="old-1")
        design = compose_v2_replacement_context(
            _production_context(), "What do you remember about my project?",
            (scenario, unknown), active_truth_scope_id="scope-real",
        )
        self.assertEqual([item.memory_id for item in design.items], ["unknown"])
        self.assertIn("HISTORICAL SCOPE UNCERTAIN", design.historical_block)
        self.assertIn("NOT CURRENT TRUTH", design.historical_block)

    def test_bounds_and_deduplicates_canonical_sources(self):
        candidates = tuple(
            _Candidate(
                f"item-{index}", content=(f"detail {index} " + "x" * 400),
                canonical_record_id=("same" if index == 1 else f"source-{index}"),
                canonical_index=index,
            )
            for index in range(8)
        )
        design = compose_v2_replacement_context(
            _production_context(), "What do you remember about detail?", candidates,
            active_truth_scope_id="scope-real",
        )
        self.assertLessEqual(len(design.items), MAX_V2_REPLACEMENT_ITEMS)
        self.assertLessEqual(len(design.historical_block), MAX_V2_REPLACEMENT_BLOCK_CHARACTERS)
        self.assertEqual(len({item.canonical_record_id for item in design.items}), len(design.items))

    def test_input_context_is_not_mutated(self):
        context = _production_context()
        before = [dict(item) for item in context]
        compose_v2_replacement_context(
            context, "What do you remember about my project?", (_Candidate("one"),),
            active_truth_scope_id="scope-real",
        )
        self.assertEqual(context, before)


if __name__ == "__main__":
    unittest.main()
