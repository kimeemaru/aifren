from pathlib import Path
import unittest

from aifren.continuity.memory_v2_hybrid_recall import HybridRecallCandidate
from aifren.continuity.memory_v2_prompt_admission_shadow import (
    MAX_APPROXIMATE_PROMPT_TOKENS,
    MAX_PROMPT_BLOCK_CHARACTERS,
    MAX_PROMPT_ITEMS,
    compose_supplemental_historical_prompt,
    counterfactual_context_with_block,
    historical_response_fallback_dialogue,
    validate_supplemental_historical_response,
)


class SupplementalPromptAdmissionShadowTests(unittest.TestCase):
    @staticmethod
    def _candidate(
        memory_id="history-1",
        *,
        lane="historical_evidence",
        content="I worked on a small Python parser project.",
        score=8.0,
        speaker="user",
        source_class="ordinary_conversation",
        scope_state="unknown_scope",
        truth_scope_id="",
        status="historical_unknown_scope",
        attribution_state="",
        canonical_index=10,
        episode_id="",
    ):
        return HybridRecallCandidate(
            memory_id, lane, content, score, (), truth_scope_id, status,
            speaker_role=speaker, speech_act="assertion",
            source_class=source_class, scope_state=scope_state,
            attribution_state=attribution_state,
            canonical_record_id=f"canonical-{canonical_index}",
            canonical_index=canonical_index, episode_id=episode_id,
        )

    def test_only_explicit_callback_intent_can_render_and_scope_warning_is_explicit(self):
        candidate = self._candidate(
            content="Historical user statement: I worked on a small Python parser project.",
        )
        for query in (
            "Tell me an old memory.",
            "What else was connected to that?",
            "What do you think about my project?",
            "Which motorcycle do I own?",
            "Say something silly.",
        ):
            design = compose_supplemental_historical_prompt(query, (candidate,))
            self.assertFalse(design.triggered, query)
            self.assertEqual("ineligible_callback_intent", design.reason)

        design = compose_supplemental_historical_prompt(
            "What do you remember me saying about one of my projects?",
            (candidate,),
        )
        self.assertTrue(design.triggered)
        self.assertIn("SPEAKER USER", design.prompt_block)
        self.assertIn("HISTORICAL ONLY", design.prompt_block)
        self.assertIn("UNKNOWN SCOPE", design.prompt_block)
        self.assertIn("ASSERTION", design.prompt_block)
        self.assertIn("NOT CURRENT TRUTH", design.prompt_block)
        self.assertIn("Preserve speaker, negation, uncertainty/modality, and time", design.prompt_block)
        self.assertIn("Do not infer completion", design.prompt_block)
        self.assertIn("ASSISTANT evidence is never USER testimony", design.prompt_block)
        self.assertNotIn("Historical user statement:", design.prompt_block)

    def test_speaker_ownership_and_false_attribution_are_hard_filters(self):
        assistant = self._candidate(
            speaker="assistant", content="You own a huge cartridge collection.",
        )
        user_query = compose_supplemental_historical_prompt(
            "Did I tell you that I owned a huge Game Boy cartridge collection?",
            (assistant,),
        )
        self.assertFalse(user_query.triggered)
        self.assertIn(("speaker_ownership_mismatch", 1), user_query.excluded)

        assistant_query = compose_supplemental_historical_prompt(
            "What did you tell me before about Game Boy?", (assistant,),
        )
        self.assertTrue(assistant_query.triggered)
        self.assertIn("SPEAKER ASSISTANT", assistant_query.prompt_block)
        self.assertNotIn("SPEAKER USER", assistant_query.prompt_block)

    def test_response_contract_rejects_obvious_source_semantic_strengthening(self):
        cases = (
            (
                "What do you remember me saying about the project?",
                self._candidate(content="I couldn't finish the project today."),
                "You successfully finished the project.",
                "You said you couldn't finish the project that day.",
            ),
            (
                "What do you remember me saying about a telescope?",
                self._candidate(content="I think I might buy a telescope."),
                "You bought and now own a telescope.",
                "You said you thought you might buy a telescope.",
            ),
            (
                "What do you remember me saying about the game?",
                self._candidate(content="I'm planning to work on the game tomorrow."),
                "You worked on and finished the game.",
                "You were planning to work on the game tomorrow.",
            ),
            (
                "What do you remember me saying about cartridges?",
                self._candidate(content="I used to collect cartridges."),
                "You still collect cartridges.",
                "You said you used to collect cartridges.",
            ),
            (
                "What did you tell me before about a cartridge collection?",
                self._candidate(
                    speaker="assistant", content="You probably have a huge collection.",
                ),
                "I remember you saying you have a huge collection.",
                "I previously guessed that you probably had a huge collection.",
            ),
            (
                "What do you remember me saying about the Python refactor?",
                self._candidate(content="I finished the Python refactor yesterday."),
                "You couldn't finish the Python refactor.",
                "You said you finished the Python refactor yesterday.",
            ),
        )
        for query, candidate, unsafe, safe in cases:
            with self.subTest(query=query):
                design = compose_supplemental_historical_prompt(query, (candidate,))
                self.assertTrue(design.triggered)
                self.assertFalse(
                    validate_supplemental_historical_response(
                        design, unsafe,
                    ).accepted,
                )
                self.assertTrue(
                    validate_supplemental_historical_response(
                        design, safe,
                    ).accepted,
                )

    def test_assistant_fallback_negates_user_attribution_without_false_positive(self):
        design = compose_supplemental_historical_prompt(
            "What did you tell me before about a cartridge collection?",
            (self._candidate(
                speaker="assistant", content="You probably have a huge collection.",
            ),),
        )
        fallback = historical_response_fallback_dialogue(design)
        validation = validate_supplemental_historical_response(design, fallback)
        self.assertTrue(validation.accepted, validation.violations)
        unsafe = validate_supplemental_historical_response(
            design, "You told me that you probably have a huge collection.",
        )
        self.assertIn("speaker_attribution_error", unsafe.violations)

    def test_raw_episode_legacy_generated_unsupported_and_anchor_candidates_never_enter(self):
        rejected = (
            self._candidate(lane="historical_episode", content="raw episode summary"),
            self._candidate(
                "legacy", lane="semantic_v2", content="legacy text",
                status="legacy_unverified", canonical_index=11,
            ),
            self._candidate(
                "generated", source_class="generated_system", canonical_index=12,
            ),
            self._candidate(
                "unsupported", lane="historical_episode_source",
                source_class="canonical_conversation_user",
                attribution_state="user_attribution_unsupported",
                canonical_index=13, episode_id="episode-1",
            ),
            self._candidate(
                "anchor", lane="historical_recall_anchor_source",
                canonical_index=14, episode_id="episode-1",
            ),
        )
        design = compose_supplemental_historical_prompt(
            "What do you remember me saying about a project?", rejected,
        )
        self.assertFalse(design.triggered)
        self.assertNotIn("raw episode summary", design.prompt_block)
        self.assertIn(("anchor_expansion_excluded", 1), design.excluded)
        self.assertIn(("unsupported_summary_attribution", 1), design.excluded)

    def test_canonical_episode_refinement_is_allowed_but_scope_mismatch_is_not(self):
        refined = self._candidate(
            lane="historical_episode_source",
            content="Historical assistant record: We discussed an old handheld.",
            speaker="assistant",
            source_class="canonical_conversation_assistant",
            scope_state="scenario", truth_scope_id="scenario-1",
            status="historical_scenario_assistant_source",
            episode_id="episode-1",
        )
        mismatch = compose_supplemental_historical_prompt(
            "What did you tell me before about the old handheld?", (refined,),
            active_truth_scope_id="real-world",
        )
        self.assertFalse(mismatch.triggered)
        self.assertIn(("truth_scope_mismatch", 1), mismatch.excluded)

        matching = compose_supplemental_historical_prompt(
            "What did you tell me before about the old handheld?", (refined,),
            active_truth_scope_id="scenario-1",
        )
        self.assertTrue(matching.triggered)
        self.assertNotIn("Historical assistant record:", matching.prompt_block)

    def test_item_character_token_dedup_and_weak_secondary_bounds(self):
        candidates = (
            self._candidate(content="A" * 500, score=10.0, canonical_index=1),
            self._candidate("duplicate", content="A" * 500, score=9.9, canonical_index=2),
            self._candidate("second", content="Another precise project detail.", score=9.5, canonical_index=3),
            self._candidate("third", content="A weak secondary.", score=7.0, canonical_index=4),
        )
        design = compose_supplemental_historical_prompt(
            "What do you remember me saying about my projects?", candidates,
        )
        self.assertTrue(design.triggered)
        self.assertLessEqual(len(design.items), MAX_PROMPT_ITEMS)
        self.assertEqual(2, len(design.items))
        self.assertLessEqual(design.character_count, MAX_PROMPT_BLOCK_CHARACTERS)
        self.assertLessEqual(
            design.approximate_token_count, MAX_APPROXIMATE_PROMPT_TOKENS,
        )
        self.assertIn(("duplicate_source", 1), design.excluded)

    def test_disabled_design_has_no_behavior_and_counterfactual_copy_is_isolated(self):
        candidate = self._candidate()
        disabled = compose_supplemental_historical_prompt(
            "What do you remember me saying about my project?",
            (candidate,), enabled=False,
        )
        self.assertFalse(disabled.triggered)
        self.assertEqual("experiment_disabled", disabled.reason)

        enabled = compose_supplemental_historical_prompt(
            "What do you remember me saying about my project?", (candidate,),
        )
        production = [
            {"role": "user", "content": "V1 memory context"},
            {"role": "user", "content": "What do you remember me saying about my project?"},
        ]
        proposed = counterfactual_context_with_block(production, enabled.prompt_block)
        self.assertEqual(2, len(production))
        self.assertNotIn(enabled.prompt_block, [item["content"] for item in production])
        self.assertEqual(3, len(proposed))
        self.assertEqual(enabled.prompt_block, proposed[-2]["content"])

        # The design module remains absent from every production prompt owner.
        root = Path(__file__).resolve().parents[1]
        for relative in (
            'aifren/assistant_service.py', 'aifren/assistant.py', 'aifren/conversation/conversation.py',
        ):
            self.assertNotIn(
                "memory_v2_prompt_admission_shadow",
                (root / relative).read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
