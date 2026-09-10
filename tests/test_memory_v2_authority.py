from dataclasses import replace
from types import SimpleNamespace
import unittest

from config import configured_memory_authority
from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth
from memory_v2_answer_governance import compose_memory_answer_requirement
from memory_v2_authority import (
    DevelopmentV2MemoryAuthority,
    MemoryV2AuthorityUnavailable,
    render_authoritative_no_evidence,
)


class MemoryV2AuthorityTests(unittest.TestCase):
    @staticmethod
    def _historical_candidate(
        identifier, content, *, speaker="user", speech_act="assertion",
        index=4, lane="historical_evidence", episode_id="",
    ):
        return SimpleNamespace(
            memory_id=identifier, lane=lane, content=content, score=8.0,
            truth_scope_id="scope-real", status="historical_unknown_scope",
            evidence=(), associated_from="", speaker_role=speaker,
            speech_act=speech_act, source_class="ordinary_conversation",
            scope_state="unknown_scope", attribution_state="source_supported",
            canonical_record_id=f"record-{identifier}", canonical_index=index,
            episode_id=episode_id, episode_source_start_index=(0 if episode_id else None),
            episode_source_end_index_exclusive=(10 if episode_id else None),
        )

    def test_default_v2_needs_no_qa_gate_and_v1_is_explicit(self):
        self.assertEqual("v2", configured_memory_authority({}))
        self.assertEqual("v2", configured_memory_authority({"AIFREN_MEMORY_AUTHORITY": "v2"}))
        self.assertEqual("v1", configured_memory_authority({"AIFREN_MEMORY_AUTHORITY": "v1"}))
        with self.assertRaises(RuntimeError):
            configured_memory_authority({"AIFREN_MEMORY_AUTHORITY": "invalid"})

    def test_authoritative_absence_is_a_successful_typed_result(self):
        requirement = compose_memory_answer_requirement(
            "Which motorcycle did I say I owned?", (),
        )
        turn = SimpleNamespace(
            requirement=requirement,
            absence_kind="nothing_relevant",
        )
        rendered = render_authoritative_no_evidence(turn)
        self.assertEqual("I don't remember you saying you owned one.", rendered)
        self.assertNotIn("fallback", rendered.casefold())
        for technical in ("reliable memory", "grounded evidence", "confidently", "retrieval"):
            self.assertNotIn(technical, rendered.casefold())

    def test_speaker_mismatch_preserves_the_distinction(self):
        requirement = compose_memory_answer_requirement(
            "Did I tell you that I owned a collection?", (),
        )
        requirement = replace(requirement, requested_speaker="user")
        turn = SimpleNamespace(
            requirement=requirement,
            absence_kind="speaker_mismatch",
        )
        rendered = render_authoritative_no_evidence(turn)
        self.assertIn(rendered, {
            "I remember mentioning that, but I don't remember you telling me about it.",
            "I brought that up before, but I don't remember you saying it.",
        })

    def test_natural_absence_renderer_varies_by_source_ownership(self):
        cases = (
            ("Do you know if I told you about my cat?", "nothing_relevant", {
                "I don't remember you telling me about your cat.",
                "I can't place you mentioning your cat before.",
            }),
            ("What did you tell me before about Game Boy?", "nothing_relevant", {
                "I don't remember saying anything about Game Boy before.",
                "I can't place telling you about Game Boy.",
            }),
            ("Have we talked about cats before?", "nothing_relevant", {
                "I don't remember us talking about cats.",
                "I can't place a conversation we had about cats.",
            }),
            ("Do you remember anything about my dog?", "nothing_relevant", {
                "I don't remember you telling me about your dog.",
                "I can't place you mentioning your dog before.",
            }),
            ("Do you remember anything unusual?", "nothing_relevant", {
                "I don't remember anything specific about that.",
                "I can't place anything specific about that.",
            }),
        )
        for query, absence_kind, expected in cases:
            with self.subTest(query=query):
                requirement = compose_memory_answer_requirement(query, ())
                turn = SimpleNamespace(
                    requirement=requirement, absence_kind=absence_kind,
                )
                rendered = render_authoritative_no_evidence(turn)
                self.assertIn(rendered, expected)
                self.assertEqual(rendered, render_authoritative_no_evidence(turn))
                self.assertFalse(any(term in rendered.casefold() for term in (
                    "reliable memory", "grounded evidence", "answer confidently",
                    "authority", "retrieval",
                )))

    def test_repeated_absence_rotates_through_a_stable_bounded_phrase_family(self):
        requirement = compose_memory_answer_requirement(
            "What is my favorite color?", (),
        )
        first = render_authoritative_no_evidence(SimpleNamespace(
            requirement=requirement,
            absence_kind="nothing_relevant",
            response_variant=1,
        ))
        second = render_authoritative_no_evidence(SimpleNamespace(
            requirement=requirement,
            absence_kind="nothing_relevant",
            response_variant=2,
        ))
        repeated = render_authoritative_no_evidence(SimpleNamespace(
            requirement=requirement,
            absence_kind="nothing_relevant",
            response_variant=1,
        ))
        self.assertNotEqual(first, second)
        self.assertEqual(first, repeated)

    def test_compound_preference_absence_answers_each_requested_slot(self):
        requirement = compose_memory_answer_requirement(
            "What's my favorite color and what's my favorite food?", (),
        )
        rendered = render_authoritative_no_evidence(SimpleNamespace(
            requirement=requirement,
            absence_kind="nothing_relevant",
            response_variant=1,
        ))
        self.assertIn("color", rendered)
        self.assertIn("food", rendered)

    def test_historical_question_cannot_establish_ownership(self):
        candidate = SimpleNamespace(
            memory_id="question-1", lane="historical_evidence",
            content="Do you think I should buy a motorcycle?", score=2.0,
            truth_scope_id="scope-22222222-2222-4222-8222-222222222222",
            status="historical_occurrence", evidence=(), associated_from="",
            speaker_role="user", speech_act="question",
            source_class="ordinary_conversation", scope_state="unknown_scope",
            attribution_state="source_supported", canonical_record_id="record-1",
            canonical_index=4, episode_id="",
            episode_source_start_index=None,
            episode_source_end_index_exclusive=None,
        )
        recall = SimpleNamespace(retrieve=lambda _query: SimpleNamespace(
            candidates=(candidate,), abstention_reason="", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
        ))
        store = SimpleNamespace(active_truth_scope_id=lambda _character: "scope-ok")
        authority = DevelopmentV2MemoryAuthority(
            store, "11111111-1111-4111-8111-111111111111", (), recall=recall,
        )
        turn = authority.prepare(
            "Which motorcycle did I say I owned?",
            active_truth_scope_id="scope-22222222-2222-4222-8222-222222222222",
        )
        self.assertTrue(turn.authoritative_no_evidence)
        self.assertEqual((), turn.design.items)

    def test_relation_incomplete_favorite_color_candidates_become_no_evidence(self):
        candidates = (
            self._historical_candidate(
                "fragment", "almost like my favorite color",
            ),
            self._historical_candidate(
                "question", "Do you remember my favorite color?",
                speech_act="question", index=5,
            ),
            self._historical_candidate(
                "assistant", "Your favorite color is purple.",
                speaker="assistant", index=6,
            ),
        )
        recall = SimpleNamespace(retrieve=lambda _query, **_kwargs: SimpleNamespace(
            candidates=candidates, abstention_reason="", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
        ))
        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real"),
            "11111111-1111-4111-8111-111111111111", (), recall=recall,
        )
        turn = authority.prepare(
            "What is my favorite color?", active_truth_scope_id="scope-real",
        )
        self.assertTrue(turn.authoritative_no_evidence)
        self.assertEqual(3, turn.insufficient_candidate_count)
        self.assertEqual((), turn.design.items)
        # A topic mention is not enough to license a familiarity claim from
        # this requirement. The backend chooses a fully validated unknown.
        self.assertEqual("I don't remember you telling me your favorite color.",
                         render_authoritative_no_evidence(turn))

    def test_concrete_followup_anchor_is_immediate_bounded_and_cannot_chain(self):
        project = self._historical_candidate(
            "project", "I worked on my Python project today.", index=20,
        )
        python = self._historical_candidate(
            "project", "I worked on my Python project today.",
            speaker="user", index=20,
            lane="historical_recall_anchor_source", episode_id="episode-project",
        )
        seen_anchors = []

        class Recall:
            def retrieve(self, query, **kwargs):
                anchor = kwargs.get("recall_anchor")
                seen_anchors.append(anchor)
                if "project" in query.current_user_text.casefold():
                    return SimpleNamespace(
                        candidates=(project,), abstention_reason="", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
                    )
                return SimpleNamespace(
                    candidates=((python,) if anchor is not None else ()),
                    abstention_reason="no_anchor", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
                )

        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real"),
            "11111111-1111-4111-8111-111111111111", (), recall=Recall(),
        )
        first = authority.prepare(
            "What do you remember me saying about one of my projects?",
            active_truth_scope_id="scope-real",
        )
        self.assertFalse(first.authoritative_no_evidence)
        authority.publish(first)
        second = authority.prepare(
            "Which programming language was connected to it?",
            active_truth_scope_id="scope-real",
        )
        self.assertTrue(second.recall_anchor_used)
        self.assertEqual("grounded_evidence", second.requirement.evidence_state)
        self.assertEqual(
            "The programming language that came up was Python.",
            second.requirement.fallback_dialogue,
        )
        third = authority.prepare(
            "Which programming language was connected to it?",
            active_truth_scope_id="scope-real",
        )
        self.assertTrue(third.authoritative_no_evidence)
        self.assertFalse(third.recall_anchor_used)
        self.assertIsNotNone(seen_anchors[1])
        self.assertIsNone(seen_anchors[2])

    def test_non_memory_retrieval_cannot_seed_a_grounded_followup_anchor(self):
        incidental = self._historical_candidate(
            "incidental", "We discussed Python.", speaker="assistant", index=30,
        )
        seen_anchors = []

        class Recall:
            def retrieve(self, query, **kwargs):
                anchor = kwargs.get("recall_anchor")
                seen_anchors.append(anchor)
                return SimpleNamespace(
                    candidates=(incidental,) if "weather" in query.current_user_text.casefold() else (),
                    abstention_reason="" if anchor is not None else "no_anchor",
                    generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
                )

        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real"),
            "11111111-1111-4111-8111-111111111111", (), recall=Recall(),
        )
        first = authority.prepare(
            "What kind of weather do you like?", active_truth_scope_id="scope-real",
        )
        self.assertFalse(first.memory_query_decision.applicable)
        second = authority.prepare(
            "Which programming language was connected to it?",
            active_truth_scope_id="scope-real",
        )
        self.assertTrue(second.authoritative_no_evidence)
        self.assertFalse(second.recall_anchor_used)
        self.assertEqual([None, None], seen_anchors)

    def test_only_published_unique_source_can_seed_and_duplicate_cannot_resurrect(self):
        source = self._historical_candidate("event", "We watched the paper glider beside the maple arch.")
        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real"), "character", (),
            recall=SimpleNamespace(retrieve=lambda *_a, **_k: SimpleNamespace(
                candidates=(source,), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)))))
        first = authority.prepare("Do you remember the paper glider?", active_truth_scope_id="scope-real")
        self.assertIsNone(authority._recall_anchor, "a prepared/unpublished answer has no antecedent")
        authority.publish(first)
        self.assertIsNotNone(authority._recall_anchor)
        authority.publish(first)  # Duplicate within the same published turn is idempotent.
        second = authority.prepare("Which place was that?", active_truth_scope_id="scope-real")
        self.assertTrue(second.recall_anchor_used)
        authority.publish(first)  # Late publication must not rearm a consumed hop.
        self.assertIsNone(authority._recall_anchor)
        third = authority.prepare("Do you remember the paper glider?", active_truth_scope_id="scope-real")
        authority.invalidate_recall_anchor()
        authority.publish(third)
        self.assertIsNone(authority._recall_anchor)

    def test_multisource_answer_cannot_guess_antecedent_but_exact_fallback_can(self):
        sources = (self._historical_candidate("a", "We watched a meteor beside the lake.", index=4),
                   self._historical_candidate("b", "We watched a meteor beside the bridge.", index=6))
        authority = DevelopmentV2MemoryAuthority(
            SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real"), "character", (),
            recall=SimpleNamespace(retrieve=lambda *_a, **_k: SimpleNamespace(
                candidates=sources, health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)))))
        turn = authority.prepare("Do you remember the meteor?", active_truth_scope_id="scope-real")
        authority.publish(turn)
        self.assertIsNone(authority._recall_anchor)
        self.assertEqual((sources[0].canonical_record_id,), turn.requirement.fallback_evidence_ids)
        authority.publish(turn, source_fallback=True)
        self.assertEqual((sources[0].canonical_record_id,), authority._recall_anchor.canonical_record_ids)

    def test_retrieval_failure_is_fail_closed_not_v1_fallback(self):
        def fail(_query):
            raise RuntimeError("synthetic retrieval failure")

        recall = SimpleNamespace(retrieve=fail)
        store = SimpleNamespace(active_truth_scope_id=lambda _character: "scope-ok")
        authority = DevelopmentV2MemoryAuthority(
            store, "11111111-1111-4111-8111-111111111111", (), recall=recall,
        )
        turn = authority.prepare(
            "What do you remember?",
            active_truth_scope_id="scope-22222222-2222-4222-8222-222222222222",
        )
        self.assertTrue(turn.requirement.lookup_unavailable)
        self.assertFalse(turn.authoritative_no_evidence)
        with self.assertRaises(MemoryV2AuthorityUnavailable) as raised:
            render_authoritative_no_evidence(turn)
        self.assertNotIn("synthetic retrieval failure", str(raised.exception))

    def test_grounded_turn_binds_recent_only_details_to_full_response_validation(self):
        candidate = SimpleNamespace(
            memory_id="boop-1", lane="historical_evidence",
            content="boop", score=2.0,
            truth_scope_id="scope-22222222-2222-4222-8222-222222222222",
            status="historical_occurrence", evidence=(), associated_from="",
            speaker_role="user", speech_act="assertion",
            source_class="ordinary_conversation", scope_state="unknown_scope",
            attribution_state="source_supported", canonical_record_id="record-boop",
            canonical_index=4, episode_id="",
            episode_source_start_index=None,
            episode_source_end_index_exclusive=None,
        )
        recall = SimpleNamespace(retrieve=lambda _query: SimpleNamespace(
            candidates=(candidate,), abstention_reason="", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
        ))
        store = SimpleNamespace(active_truth_scope_id=lambda _character: "scope-ok")
        messages = (
            {"role": "user", "content": "Did I own a motorcycle?"},
            {"role": "assistant", "content": "You owned a Harley-Davidson."},
            {"role": "user", "content": "Do you remember when I booped you?"},
        )
        authority = DevelopmentV2MemoryAuthority(
            store, "11111111-1111-4111-8111-111111111111", messages,
            recall=recall, recent_context_policy="balanced_12",
        )
        turn = authority.prepare(
            "Do you remember when I booped you?",
            active_truth_scope_id="scope-22222222-2222-4222-8222-222222222222",
        )
        self.assertIn(
            "harley-davidson", turn.requirement.recent_context_only_anchors,
        )

    def test_support_ledger_excludes_inactive_truth_scope(self):
        recall = SimpleNamespace(retrieve=lambda _query, **_kwargs: SimpleNamespace(
            candidates=(), abstention_reason="no_candidates", generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
        ))
        store = SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real")
        messages = (
            {
                "role": "user",
                "content": "My scenario cat is pampered.",
                "truth_scope": {
                    "kind": "scenario", "scope_id": "scope-scenario",
                },
            },
            {
                "role": "user",
                "content": "What do you think about cats?",
                "truth_scope": {
                    "kind": "real_world", "scope_id": "scope-real",
                },
            },
        )
        authority = DevelopmentV2MemoryAuthority(
            store, "11111111-1111-4111-8111-111111111111", messages,
            recall=recall,
        )

        turn = authority.prepare(
            "What do you think about cats?",
            active_truth_scope_id="scope-real",
            active_truth_scope={"kind": "real_world", "scope_id": "scope-real"},
        )

        support = "\n".join(
            item.source_text for item in turn.requirement.support_ledger
        )
        self.assertNotIn("scenario cat", support)

    def test_support_ledger_contains_only_prompt_admitted_retrieval_sources(self):
        def candidate(identifier, speaker, content, index):
            return SimpleNamespace(
                memory_id=identifier, lane="historical_evidence",
                content=content, score=8.0 - index,
                truth_scope_id="scope-real", status="historical_real_world",
                evidence=(), associated_from="", speaker_role=speaker,
                speech_act="assertion", source_class="ordinary_conversation",
                scope_state="real_world", attribution_state="source_supported",
                canonical_record_id=f"record-{identifier}",
                canonical_index=index, episode_id="",
                episode_source_start_index=None,
                episode_source_end_index_exclusive=None,
            )

        admitted = candidate("user-cat", "user", "I like cats.", 4)
        wrong_owner = candidate(
            "assistant-cat", "assistant", "You own a pampered cat.", 5,
        )
        recall = SimpleNamespace(retrieve=lambda _query, **_kwargs: SimpleNamespace(
            candidates=(admitted, wrong_owner), abstention_reason="",
            generated_counts=(), health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)),
        ))
        store = SimpleNamespace(active_truth_scope_id=lambda _character: "scope-real")
        authority = DevelopmentV2MemoryAuthority(
            store, "11111111-1111-4111-8111-111111111111",
            ({"role": "user", "content": "What did I say about cats?"},),
            recall=recall,
        )

        turn = authority.prepare(
            "What did I say about cats?",
            active_truth_scope_id="scope-real",
            active_truth_scope={"kind": "real_world", "scope_id": "scope-real"},
        )

        support = tuple(
            item.source_text for item in turn.requirement.support_ledger
            if item.authority_class == "historical_conversation_only"
        )
        self.assertEqual(("I like cats.",), support)
        self.assertNotIn("You own a pampered cat.", support)


if __name__ == "__main__":
    unittest.main()
