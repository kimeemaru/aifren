from datetime import datetime, timezone
import os
import tempfile
import unittest
import uuid

from aifren.memory_v2_store.models import RetrievalQuery
from aifren.memory_v2_store import (
    ActiveSceneSubjectIntroduction, ActiveStateProposal, ActiveStateProposalUpdate,
    EmbeddingLifecycle, HnswClaimIndex, MemoryV2Repository, MemoryV2Store,
    OpenThreadProposal, OpenThreadProposalOperation, RetrievalLimits,
    SemanticRetrievalV2, StoreError, actor_state_subject_key,
    scene_state_subject_key,
)
from aifren.memory_v2_store.active_state_prompt import admit_active_headwear_context


class _ScopeEmbeddingProvider:
    provider = "scope-test"
    model = "scope-test-v1"
    model_version = "1"
    dimensions = 3
    normalized = True
    dtype = "float32"
    preprocessing_fingerprint = "scope-test-v1"
    device = "cpu"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = str(text).casefold()
            if "dagger" in lowered or "equipped" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "moon bridge" in lowered or "unfinished crossing" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class TruthScopeTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.repo = MemoryV2Repository(self.store)
        self.character = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.store.create_character(self.character, "Scope fixture")
        self.store.create_character(self.other, "Other fixture")

    def tearDown(self):
        self.store.close()

    def event(self, event_id, at_us, text, *, character=None, actor="user", sequence=None):
        character = character or self.character
        if sequence is None:
            sequence = self.store.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character,),
            ).fetchone()[0]
        self.store.add_event(character, event_id, sequence, actor_kind=actor, recorded_at_us=at_us, content_text=text)

    def scenario(self):
        self.event("scope-open", 10, "Let us continue our campaign.")
        scope = self.store.create_scenario_truth_scope(
            self.character, "Campaign", evidence_event_id="scope-open", evidence_excerpt_start_cp=0,
            evidence_excerpt_end_cp=7,
        )
        self.event("scope-enter", 11, "Enter the campaign.")
        self.store.activate_truth_scope(
            self.character, scope, evidence_event_id="scope-enter", evidence_excerpt_start_cp=0,
            evidence_excerpt_end_cp=5,
        )
        return scope

    def leave(self):
        self.event("scope-leave", 40, "Leave the campaign.")
        self.store.deactivate_to_real_world(
            self.character, evidence_event_id="scope-leave", evidence_excerpt_start_cp=0,
            evidence_excerpt_end_cp=5,
        )

    def retrieval_query(self, text):
        return RetrievalQuery(
            self.character, text, datetime.now(timezone.utc).isoformat(),
        )

    def scenario_b(self):
        self.event("scope-b-open", 60, "Start another scenario.")
        scope = self.store.create_scenario_truth_scope(
            self.character, "Other scenario", evidence_event_id="scope-b-open",
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5,
        )
        self.event("scope-b-enter", 61, "Enter another scenario.")
        self.store.activate_truth_scope(
            self.character, scope, evidence_event_id="scope-b-enter",
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5,
        )
        return scope

    def test_default_real_world_scope_and_scenario_active_lifecycle_are_evidence_backed(self):
        default = self.store.default_truth_scope_id(self.character)
        self.assertEqual(default, self.store.active_truth_scope_id(self.character))
        self.assertEqual("real_world", self.repo.active_truth_scope(self.character).kind)
        scenario = self.scenario()
        self.assertEqual(scenario, self.store.active_truth_scope_id(self.character))
        self.assertEqual(("real_world", "scenario"), tuple(item.kind for item in self.repo.list_truth_scopes(self.character)))
        self.leave()
        self.assertEqual(default, self.store.active_truth_scope_id(self.character))
        with self.assertRaises(StoreError):
            self.store.activate_truth_scope(self.character, scenario, evidence_event_id="scope-leave",
                                            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=99)

    def test_active_state_threads_and_prompt_admission_are_scope_filtered_and_resume(self):
        scenario = self.scenario()
        self.event("dagger", 12, "I am holding a dagger.")
        self.store.set_active_state(
            self.character, "scenario-dagger", subject_key="active.avatar.held_item", value="dagger",
            evidence_event_id="dagger",
        )
        self.event("hat", 13, "She is wearing a red hat.")
        self.store.set_active_state(
            self.character, "scenario-hat", subject_key="active.avatar.headwear", value="red hat",
            evidence_event_id="hat",
        )
        self.event("thread", 14, "We still need to defeat the demon.")
        self.store.apply_open_thread_proposal(self.character, OpenThreadProposal(operations=(
            OpenThreadProposalOperation("open", "demon", 0, 34, "unresolved_problem", "shared", "defeat the demon"),
        )), evidence_event_id="thread")
        self.assertEqual("dagger", self.repo.lookup_active_state(self.character, "active.avatar.held_item").state.value)
        self.assertEqual(1, len(self.repo.list_open_threads(self.character).threads))
        self.assertIsNotNone(admit_active_headwear_context(self.repo, self.character, "What hat is she wearing?").context_block)
        self.leave()
        self.assertIsNone(self.repo.lookup_active_state(self.character, "active.avatar.held_item").state)
        self.assertEqual((), self.repo.list_open_threads(self.character).threads)
        self.assertIsNone(admit_active_headwear_context(self.repo, self.character, "What hat is she wearing?").context_block)
        self.assertEqual("dagger", self.repo.lookup_active_state(
            self.character, "active.avatar.held_item", truth_scope_id=scenario,
        ).state.value)
        self.event("scope-resume", 50, "Resume the campaign.")
        self.store.activate_truth_scope(self.character, scenario, evidence_event_id="scope-resume",
                                        evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=6)
        self.assertEqual("dagger", self.repo.lookup_active_state(self.character, "active.avatar.held_item").state.value)
        self.assertEqual(1, len(self.repo.list_open_threads(self.character).threads))

    def test_generic_retrieval_reproduced_leak_is_fixed_and_scope_resume_restores_state(self):
        scenario_a = self.scenario()
        self.event("generic-state", 12, "I hold the moonlit scenario dagger.")
        self.store.set_active_state(
            self.character, "generic-dagger", subject_key="active.avatar.held_item",
            value="moonlit scenario dagger", evidence_event_id="generic-state",
        )
        self.event(
            "other-generic-state", 12, "I hold the moonlit scenario dagger.",
            character=self.other,
        )
        self.store.set_active_state(
            self.other, "other-generic-dagger", subject_key="active.avatar.held_item",
            value="moonlit scenario dagger", evidence_event_id="other-generic-state",
        )
        repository_query = "moonlit scenario dagger"
        hybrid_query = self.retrieval_query(
            'Do I still have the "moonlit scenario dagger"?',
        )
        retriever = SemanticRetrievalV2(self.store)

        self.assertEqual(
            ["generic-dagger"],
            [item.memory_id for item in self.repo.search(self.character, repository_query)],
        )
        scenario_result = retriever.retrieve(hybrid_query)
        self.assertEqual(("generic-dagger",), scenario_result.claim_ids)
        self.assertEqual("CURRENT USER FACT", scenario_result.selected_memories[0].label)

        self.leave()
        self.assertEqual([], self.repo.search(self.character, repository_query))
        self.assertNotIn("generic-dagger", retriever.retrieve(hybrid_query).claim_ids)

        scenario_b = self.scenario_b()
        self.assertNotEqual(scenario_a, scenario_b)
        self.assertEqual([], self.repo.search(self.character, repository_query))
        self.assertNotIn("generic-dagger", retriever.retrieve(hybrid_query).claim_ids)
        self.assertEqual(
            ["generic-dagger"],
            [
                item.memory_id for item in self.repo.search(
                    self.character, repository_query, truth_scope_id=scenario_a,
                )
            ],
        )

        self.event("scope-a-resume", 70, "Resume the campaign.")
        self.store.activate_truth_scope(
            self.character, scenario_a, evidence_event_id="scope-a-resume",
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=6,
        )
        self.assertEqual(
            ["generic-dagger"],
            [item.memory_id for item in self.repo.search(self.character, repository_query)],
        )
        self.assertEqual(("generic-dagger",), retriever.retrieve(hybrid_query).claim_ids)

    def test_generic_structural_exact_fts_semantic_and_ann_lanes_share_scope_boundary(self):
        scenario_a = self.scenario()
        self.event("lane-state", 12, "I hold the moonlit scenario dagger.")
        self.store.set_active_state(
            self.character, "lane-dagger", subject_key="active.avatar.held_item",
            value="moonlit scenario dagger", evidence_event_id="lane-state",
        )
        provider = _ScopeEmbeddingProvider()
        self.store.ensure_fts()
        EmbeddingLifecycle(self.store, provider).rebuild_all()
        at_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)

        def lane_ids(scope_id):
            structural = {
                row["claim_id"] for row in self.store.structural_claims(
                    self.character, at_us, truth_scope_id=scope_id,
                )
            }
            exact = set(self.store.exact_claim_ids(
                self.character, ("moonlit scenario dagger",), 10,
                truth_scope_id=scope_id,
            ))
            fts = {
                row["claim_id"] for row in self.store.search_fts(
                    self.character, '"moonlit" OR "dagger"', 10,
                    truth_scope_id=scope_id,
                )
            }
            semantic = {
                claim_id for claim_id, _score in self.store.semantic_candidates(
                    self.character, provider, [1.0, 0.0, 0.0], 10,
                    truth_scope_id=scope_id,
                )
            }
            ann = {
                claim_id for claim_id, _score in HnswClaimIndex(
                    self.store, self.character, provider,
                ).query([1.0, 0.0, 0.0], 10, truth_scope_id=scope_id)
            }
            return structural, exact, fts, semantic, ann

        self.assertTrue(all("lane-dagger" in lane for lane in lane_ids(scenario_a)))
        real_scope = self.store.default_truth_scope_id(self.character)
        self.assertTrue(all("lane-dagger" not in lane for lane in lane_ids(real_scope)))
        scenario_b = self.scenario_b()
        self.assertTrue(all("lane-dagger" not in lane for lane in lane_ids(scenario_b)))

        bounded = SemanticRetrievalV2(
            self.store,
            RetrievalLimits(
                exact_candidates=2, fts_candidates=2, structural_candidates=2,
                semantic_candidates=2, final_count=1, token_budget=8,
            ),
            provider,
        ).retrieve(
            self.retrieval_query("What relic do I have equipped?"),
            truth_scope_id=scenario_a,
        )
        self.assertEqual(("lane-dagger",), bounded.claim_ids)
        self.assertLessEqual(len(bounded.selected_memories), 1)
        self.assertLessEqual(sum(item.estimated_tokens for item in bounded.selected_memories), 8)

    def test_generic_open_thread_retrieval_is_scope_isolated(self):
        scenario_a = self.scenario()
        text = "We still need to repair the moon bridge."
        self.event("generic-thread", 14, text)
        applied = self.store.apply_open_thread_proposal(
            self.character,
            OpenThreadProposal((
                OpenThreadProposalOperation(
                    "open", "moon_bridge", 17, 39, "unresolved_problem", "shared",
                    "repair the moon bridge",
                ),
            )),
            evidence_event_id="generic-thread",
        )
        thread_id = applied[0]["thread_id"]
        query = self.retrieval_query('Do we still need to "repair the moon bridge"?')
        retriever = SemanticRetrievalV2(self.store)

        self.assertIn(
            thread_id,
            [item.memory_id for item in self.repo.search(self.character, "repair moon bridge")],
        )
        self.assertIn(thread_id, retriever.retrieve(query).claim_ids)
        self.leave()
        self.assertNotIn(
            thread_id,
            [item.memory_id for item in self.repo.search(self.character, "repair moon bridge")],
        )
        self.assertNotIn(thread_id, retriever.retrieve(query).claim_ids)
        self.event("thread-scope-resume", 50, "Resume the campaign.")
        self.store.activate_truth_scope(
            self.character, scenario_a, evidence_event_id="thread-scope-resume",
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=6,
        )
        self.assertIn(thread_id, retriever.retrieve(query).claim_ids)

    def test_generic_real_world_and_durable_retrieval_preserve_contract(self):
        self.event("real-fact", 5, "The user prefers cedar tea.")
        self.store.add_claim(
            self.character, "real-cedar-tea", claim_type="stable_user_fact",
            assertion_scope="user_fact", content="The user prefers cedar tea.",
            provenance_state="complete",
        )
        self.store.attach_evidence(self.character, "real-cedar-tea", "real-fact")
        self.event("real-name", 6, "My name is Elena.")
        self.store.add_durable_claim(
            self.character, "real-name", subject_key="identity.name",
            content="The user's name is Elena.", evidence_event_id="real-name",
            evidence_role="direct_user_statement", evidence_excerpt_start_cp=11,
            evidence_excerpt_end_cp=16,
        )
        real_scope = self.store.default_truth_scope_id(self.character)
        self.assertEqual(
            ["real-cedar-tea"],
            [item.memory_id for item in self.repo.search(self.character, "cedar tea")],
        )
        scenario_a = self.scenario()
        self.assertEqual(
            [],
            self.repo.search(
                self.character, "cedar tea", truth_scope_id=scenario_a,
            ),
        )
        self.assertEqual(
            ["real-name"],
            [
                item.memory_id for item in self.repo.search(
                    self.character, "Elena", truth_scope_id=scenario_a,
                )
            ],
        )
        stored_scope = self.store.connection.execute(
            "SELECT truth_scope_id FROM claims WHERE character_id=? AND claim_id='real-name'",
            (self.character,),
        ).fetchone()[0]
        self.assertEqual(real_scope, stored_scope)
        with self.assertRaises(StoreError):
            self.repo.search(
                self.character, "Elena",
                truth_scope_id=self.store.default_truth_scope_id(self.other),
            )
        with self.assertRaises(StoreError):
            self.repo.search(self.character, "Elena", truth_scope_id="")

    def test_scene_attributes_stay_compositional_and_scenario_history_survives_long_horizon(self):
        scenario = self.scenario()
        self.event("introduce", 20, "The dagger is silver and damaged.")
        result = self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            introductions=(ActiveSceneSubjectIntroduction("dagger", "object", 4, 10),),
            updates=(
                ActiveStateProposalUpdate(None, "set", "silver", 14, 20, "scene", "dagger", "color"),
                ActiveStateProposalUpdate(None, "set", "damaged", 25, 32, "scene", "dagger", "condition"),
            ),
        ), evidence_event_id="introduce")
        scene_id = result[0]["scene_subject_id"]
        self.event("condition", 30, "The dagger is scratched.")
        self.store.set_active_state(self.character, "dagger-condition", subject_key=scene_state_subject_key(scene_id, "condition"),
                                    value="scratched", evidence_event_id="condition")
        attributes = {row.subject_key.rsplit(".", 1)[-1]: row.value for row in self.repo.lookup_scene_attributes(self.character, scene_id)}
        self.assertEqual({"kind": "object", "color": "silver", "condition": "scratched"}, attributes)
        for number in range(1_000):
            self.event(f"horizon-{number}", 100 + number, "ordinary intervening turn")
        self.leave()
        self.assertEqual((), self.repo.list_scene_subjects(self.character))
        self.assertEqual("silver", {row.subject_key.rsplit(".", 1)[-1]: row.value for row in self.repo.lookup_scene_attributes(
            self.character, scene_id, truth_scope_id=scenario,
        )}["color"])
        self.event("scope-resume", 2_000, "Resume the campaign.")
        self.store.activate_truth_scope(self.character, scenario, evidence_event_id="scope-resume", evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=6)
        self.assertEqual(1, len(self.repo.list_scene_subjects(self.character)))

    def test_real_durable_claim_stays_real_and_scope_rows_survive_restart_and_time(self):
        self.event("name", 5, "My name is Elena.")
        self.store.add_durable_claim(
            self.character, "name", subject_key="identity.name", content="The user's name is Elena.",
            evidence_event_id="name", evidence_role="direct_user_statement", evidence_excerpt_start_cp=11,
            evidence_excerpt_end_cp=16, valid_from_us=5,
        )
        scenario = self.scenario()
        self.event("roleplay-name", 15, "My name is Arannis.")
        # Durable writer is deliberately real-world-only even while scenario scope is active.
        with self.assertRaises(StoreError):
            self.store.add_durable_claim(
                self.character, "roleplay-name", subject_key="identity.name", content="The user's name is Arannis.",
                evidence_event_id="roleplay-name", evidence_role="direct_user_statement", evidence_excerpt_start_cp=11,
                evidence_excerpt_end_cp=18,
            )
        self.assertEqual("The user's name is Elena.", self.repo.lookup_durable_core(self.character, "identity.name").candidates[0].content)
        self.leave()
        self.assertEqual(self.store.default_truth_scope_id(self.character), self.store.active_truth_scope_id(self.character))
        self.assertEqual(scenario, self.store._require_truth_scope(self.character, scenario))

    def test_scope_isolation_by_character_and_invalid_evidence_is_hard(self):
        self.event("other-event", 10, "Other enters roleplay.", character=self.other)
        with self.assertRaises(StoreError):
            self.store.create_scenario_truth_scope(
                self.character, "Foreign", evidence_event_id="other-event", evidence_excerpt_start_cp=0,
                evidence_excerpt_end_cp=5,
            )
        scenario = self.scenario()
        self.event("other-state", 20, "Other holds a dagger.", character=self.other)
        self.store.set_active_state(self.other, "other-dagger", subject_key="active.avatar.held_item", value="dagger",
                                    evidence_event_id="other-state")
        self.assertIsNone(self.repo.lookup_active_state(self.character, "active.avatar.held_item").state)
        self.assertNotEqual(self.store.default_truth_scope_id(self.other), scenario)

    def test_scope_and_inactive_scenario_state_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "scopes.sqlite3")
            store = MemoryV2Store(path)
            try:
                character = str(uuid.uuid4())
                store.create_character(character, "Restart fixture")
                store.add_event(character, "open", 1, recorded_at_us=10, content_text="Start campaign.")
                scenario = store.create_scenario_truth_scope(character, "Campaign", evidence_event_id="open",
                                                            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)
                store.add_event(character, "enter", 2, recorded_at_us=11, content_text="Enter campaign.")
                store.activate_truth_scope(character, scenario, evidence_event_id="enter",
                                           evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=5)
                store.add_event(character, "state", 3, recorded_at_us=12, content_text="I hold a dagger.")
                store.set_active_state(character, "dagger", subject_key="active.avatar.held_item", value="dagger",
                                       evidence_event_id="state")
                store.add_event(character, "leave", 4, recorded_at_us=13, content_text="Leave campaign.")
                store.deactivate_to_real_world(character, evidence_event_id="leave", evidence_excerpt_start_cp=0,
                                               evidence_excerpt_end_cp=5)
            finally:
                store.close()
            reopened = MemoryV2Store(path)
            try:
                repo = MemoryV2Repository(reopened)
                self.assertEqual(reopened.default_truth_scope_id(character), reopened.active_truth_scope_id(character))
                self.assertIsNone(repo.lookup_active_state(character, "active.avatar.held_item").state)
                self.assertEqual("dagger", repo.lookup_active_state(
                    character, "active.avatar.held_item", truth_scope_id=scenario,
                ).state.value)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
