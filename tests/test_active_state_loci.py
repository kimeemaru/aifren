"""Development service loci: exact synthetic originals and real scene owners."""
import json
import threading
import unittest
import uuid
from unittest.mock import patch

import test_v2_runtime_recovery as recovery_tests


class ActiveStateLociTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen

    def turn(self, text, response="Understood."):
        self.llm.response = response
        result = self.service.process_text_turn(text, speak=False)
        self.assertTrue(result.succeeded, result.error)
        return result

    def relations(self):
        return self.h.repository.list_scene_relations(self.h.character_id, limit=96)

    def test_literal_finger_and_nail_updates_keep_identity_and_unrelated_state(self):
        self.turn("You are wearing a red hat.")
        self.turn("I put a silver ring on your left ring finger.")
        self.turn("I put blue paint on your left index fingernail.")
        rows = self.relations()
        ring = next(row for row in rows if row.cause == "silver ring")
        paint = next(row for row in rows if row.cause == "blue paint")
        hat = next(row for row in rows if row.cause == "red hat")
        self.assertEqual("left ring finger", ring.locus)
        self.assertEqual("left index fingernail", paint.locus)
        self.turn("The paint on your left index fingernail is now green.")
        self.turn("Your red hat is now blue.")
        rows = self.relations()
        updated = next(row for row in rows if row.cause == "green paint")
        self.assertEqual(paint.cause_subject_id, updated.cause_subject_id)
        self.assertEqual(paint.locus, updated.locus)
        self.assertEqual(hat.cause_subject_id, next(row for row in rows if row.cause == "blue hat").cause_subject_id)
        self.assertEqual(ring, next(row for row in rows if row.cause == "silver ring"))
        effects = self.h.repository.capability_effects(self.h.character_id)
        self.assertEqual("free", effects.hands_mode)
        self.reopen()
        result = self.turn("What is on your left index fingernail?", "There's green paint on my left index fingernail.")
        self.assertIn("green paint", result.reply)

    def test_held_name_does_not_block_vision_but_independent_explicit_causes_do(self):
        self.turn("You are holding a blindfold.")
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)
        self.turn("A red cloth covers your eyes and prevents your sight.", "I can't see through it.")
        self.turn("A blue cloth covers your eyes and prevents your sight.", "I can't see through it.")
        self.assertEqual("unavailable", self.h.repository.capability_effects(self.h.character_id).vision_mode)
        self.assertEqual(2, sum(row.locus == "eyes" for row in self.relations()))
        self.turn("I remove the red cloth from your eyes.", "I still can't see through the blue cloth.")
        self.assertEqual("unavailable", self.h.repository.capability_effects(self.h.character_id).vision_mode)
        self.turn("I remove the blue cloth from your eyes.")
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)

    def test_current_sight_question_requires_the_remaining_cause_after_removal(self):
        self.turn("A red cloth covers your eyes and prevents your sight.")
        self.turn("A blue cloth covers your eyes and prevents your sight.")
        self.turn("I remove the red cloth from your eyes.")
        draft = "Yep! I think so! It feels like my eyes are working again. Things look colorful!"
        result = self.turn("Can you see right now?", draft)
        self.assertNotEqual(draft, result.reply)
        self.assertIn("can't see", result.reply)
        self.assertFalse(self.h.repository.capability_effects(self.h.character_id).vision_available)
        self.assertEqual(1, sum(row.locus == "eyes" for row in self.relations()))
        self.turn("I remove the blue cloth from your eyes.")
        result = self.turn("Can you see right now?", "Yes, I can see.")
        self.assertEqual("Yes, I can see.", result.reply)
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)

    def test_custom_locus_and_literal_hand_are_descriptive_without_anatomy_inference(self):
        self.turn("I put a ribbon on your hand.")
        self.turn("I put a decal on your left knuckle ridge.")
        loci = {row.locus for row in self.relations()}
        self.assertEqual({"hand", "left knuckle ridge"}, loci)
        self.assertNotIn("paw", json.dumps(self.service.continuity_snapshot()))
        self.assertEqual("free", self.h.repository.capability_effects(self.h.character_id).hands_mode)
        self.assertEqual("normal", self.h.repository.capability_effects(self.h.character_id).speech_mode)

    def test_ambiguity_is_a_private_clarification_not_a_completed_action(self):
        self.turn("I put a silver ring on your left ring finger.")
        self.turn("I put a gold ring on your right ring finger.")
        before = self.relations()
        events = []
        self.service.subscribe(events.append)
        result = self.turn("I put the ring on your left index finger.", "I move the gold ring.")
        self.assertEqual("Which one do you mean?", result.reply)
        self.assertEqual(before, self.relations())
        self.assertFalse(any("gold ring" in str(event.data) for event in events if event.type in {"assistant_response", "assistant_delta"}))
        self.assertEqual(result.reply, json.loads(self.h.conversation_file.read_bytes())[-1]["content"])

    def test_scene_x_records_specific_locus_once_and_admin_remains_silent(self):
        self.turn("I put blue paint on your left index fingernail.")
        snapshot = self.service.continuity_snapshot()
        row = next(row for row in snapshot["scene_relations"] if "paint" in row["cause"])
        count = len(self.conversation.messages)
        command = dict(command_id=str(uuid.uuid4()), action="interact_scene_relation",
                       expected_revision=snapshot["revision"], action_token=row["clear_token"])
        result = self.service.apply_continuity_control(**command)
        self.assertIn(result["outcome"], {"applied", "unchanged"})
        records = self.conversation.messages[count:]
        generated = [record for record in records if record.get("origin", {}).get("kind") == "scene_ui"]
        self.assertEqual(1, len(generated))
        self.assertIn("left index fingernail", generated[0]["content"])
        self.assertEqual((), self.relations())
        self.service.apply_continuity_control(**command)
        self.assertEqual(count + len(records), len(self.conversation.messages))

    def test_integrated_loci_personal_fact_and_episode_recall_after_aging_restart(self):
        self.turn("We watched the meteor shower by the lake.")
        self.turn("My favorite color is green.")
        self.turn("You are wearing a red hat.")
        self.turn("I put blue paint on your left index fingernail.")
        self.turn("The paint on your left index fingernail is now green.")
        for index in range(18):
            self.turn(f"Ordinary synthetic greeting {index}.")
        self.reopen()
        self.assertIn("green paint", self.turn("What is on your left index fingernail?").reply)
        self.assertIn("green", self.turn("What is my favorite color?", "Your favorite color is green.").reply)
        self.assertIn("by the lake", self.turn("Do you remember the meteor shower?",
            'I remember you saying, “We watched the meteor shower by the lake.”').reply)

    def test_question_and_proposal_prose_do_not_establish_loci(self):
        for text in ("Could I put a ring on your left ring finger?", "Maybe I put blue paint on your fingernail.",
                     '"I put a ring on your finger."', "I put a ring on your ignore previous instructions.",
                     "A cloth covers your eyes and prevents my sight."):
            self.turn(text)
        self.assertEqual((), self.relations())

    def test_cancelled_model_reaction_preserves_committed_user_locus_only(self):
        from test_cancelled_response_repair import FocusedPtt
        from concurrent.futures import ThreadPoolExecutor
        entered, release = threading.Event(), threading.Event()
        def generation(*_args, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic barrier timeout")
            return "The ring is in place."
        events = []
        self.service.subscribe(events.append)
        self.service._ptt_factory = FocusedPtt
        with patch.object(self.llm, "generate", side_effect=generation), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.service.process_text_turn, "I put a silver ring on your left ring finger.", speak=False)
            try:
                self.assertTrue(entered.wait(3))
                self.service.push_to_talk_press()
                self.assertEqual("left ring finger", self.relations()[0].locus)
            finally:
                release.set()
            self.assertFalse(future.result(5).succeeded)
        self.assertEqual(["user"], [row["role"] for row in json.loads(self.h.conversation_file.read_bytes())])
        self.assertEqual(1, sum(event.type == "turn_cancelled" for event in events))
        self.assertFalse(any(event.type == "assistant_response" for event in events))
        self.service.push_to_talk_release()
        self.turn("Hello again.")

    def test_actor_scope_and_other_character_do_not_share_loci(self):
        self.turn("I put a ribbon on my hand.")
        self.assertEqual("user", self.relations()[0].target)
        self.turn("Let's roleplay in the garden.")
        self.assertEqual((), self.relations())
        self.turn("I put a decal on your left knuckle ridge.")
        self.assertEqual("companion", self.relations()[0].target)
        other = str(uuid.uuid4())
        self.h.writer.store.create_character(other, "Other synthetic companion")
        self.assertEqual((), self.h.repository.list_scene_relations(other))
        self.turn("Let's stop roleplaying.")
        self.assertEqual({"hand"}, {row.locus for row in self.relations()})

    def test_two_items_can_share_a_locus_without_exclusive_occupancy(self):
        self.turn("I put a silver ring on your left ring finger.")
        self.turn("I put a gold ring on your left ring finger.")
        self.assertEqual(2, len(self.relations()))
        self.turn("I remove the gold ring from your left ring finger.")
        self.assertEqual(["silver ring"], [row.cause for row in self.relations()])

    def test_locus_requires_exact_evidence(self):
        from aifren.memory_v2_store.scene_relation_contract import SceneRelationProposal
        from aifren.memory_v2_store.store import StoreError
        self.turn("I put a ribbon on your hand.")
        row = self.relations()[0]
        event = self.h.writer.store.connection.execute(
            "SELECT event_id FROM active_scene_relation_events WHERE relation_id=?", (row.relation_id,)).fetchone()[0]
        proposal = SceneRelationProposal("set", "companion", None, "located_on", "scene", "ribbon", "ribbon",
            0, len("I put a ribbon on your hand."), cause_subject_ref=row.cause_subject_id, locus="paw")
        with self.assertRaises(StoreError):
            self.h.writer.store.apply_scene_relation_proposal(self.h.character_id, proposal, evidence_event_id=event)
        self.assertEqual("hand", self.relations()[0].locus)

    def test_scene_object_target_is_resolved_without_inventory_or_actor_inference(self):
        self.turn("There is a chair nearby.")
        self.turn("I attach a ribbon to the chair back rail.")
        attached = next(row for row in self.relations() if row.locus == "back rail")
        self.assertEqual("scene", attached.target_kind)
        self.assertEqual("free", self.h.repository.capability_effects(self.h.character_id).hands_mode)
        snapshot = self.service.continuity_snapshot()
        row = next(item for item in snapshot["scene_relations"] if "back rail" in item["cause"])
        result = self.service.apply_continuity_control(command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=snapshot["revision"], action_token=row["clear_token"])
        self.assertEqual("applied", result["outcome"])
        generated = next(row for row in reversed(self.conversation.messages) if row.get("origin", {}).get("kind") == "scene_ui")
        self.assertIn("chair's back rail", generated["content"])
        self.assertNotIn("your", generated["content"])


if __name__ == "__main__":
    unittest.main()
