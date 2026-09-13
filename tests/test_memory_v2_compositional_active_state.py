import unittest
import uuid

from aifren.memory_v2_store import (
    ACTIVE_STATE, ActiveSceneSubjectIntroduction, ActiveSceneSubjectRetirement,
    ActiveStateProposal, ActiveStateProposalUpdate, MemoryV2Repository,
    MemoryV2Store, StoreError,
)


class CompositionalActiveStateTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.repository = MemoryV2Repository(self.store)
        self.character = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.store.create_character(self.character, "Scene")
        self.store.create_character(self.other, "Other")

    def tearDown(self):
        self.store.close()

    def event(self, event_id, at_us, content, *, character=None, actor="user"):
        character = character or self.character
        sequence = self.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character,),
        ).fetchone()[0]
        self.store.add_event(character, event_id, sequence, actor_kind=actor, recorded_at_us=at_us,
                             content_text=content, source_origin="synthetic-scene-fixture")

    @staticmethod
    def update(target_kind, target_ref, attribute, value, text, fragment, *, basis="explicit", rule=None):
        start = text.index(fragment)
        return ActiveStateProposalUpdate(
            None, "set", value, start, start + len(fragment), target_kind, target_ref, attribute, basis, rule,
        )

    @staticmethod
    def values(records):
        return {item.subject_key.rsplit(".", 1)[1]: item for item in records}

    def test_kitchen_sequence_preserves_actor_and_subject_attributes_with_time(self):
        t1 = "I am in the kitchen cooking food."
        self.event("kitchen-1", 100, t1)
        first = self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            (
                self.update("actor", "user", "location", "kitchen", t1, "kitchen"),
                self.update("actor", "user", "activity", "cooking", t1, "cooking"),
                self.update("scene", "food_1", "state", "cooking", t1, "cooking"),
            ),
            (ActiveSceneSubjectIntroduction("food_1", "food", t1.index("food"), t1.index("food") + 4),),
        ), evidence_event_id="kitchen-1")
        food = next(item["scene_subject_id"] for item in first if item["operation"] == "introduce")

        t2 = "The food is cooked but burnt."
        self.event("kitchen-2", 200, t2)
        self.store.apply_active_state_proposal(self.character, ActiveStateProposal((
            self.update("scene", food, "state", "cooked", t2, "cooked"),
            self.update("scene", food, "condition", "burnt", t2, "burnt"),
        )), evidence_event_id="kitchen-2")

        t3 = "I am plating food on the counter."
        self.event("kitchen-3", 300, t3)
        self.store.apply_active_state_proposal(self.character, ActiveStateProposal((
            self.update("actor", "user", "activity", "plating food", t3, "plating food"),
            self.update("scene", food, "location", "counter", t3, "counter"),
        )), evidence_event_id="kitchen-3")

        location = self.repository.lookup_actor_state(self.character, "user", "location").state
        activity = self.repository.lookup_actor_state(self.character, "user", "activity").state
        attributes = self.values(self.repository.lookup_scene_attributes(self.character, food))
        self.assertEqual("kitchen", location.value)
        self.assertEqual((100, 100), (location.valid_from_us, location.last_confirmed_at_us))
        self.assertEqual("plating food", activity.value)
        self.assertEqual((300, None), (activity.valid_from_us, activity.valid_to_us))
        self.assertEqual("food", attributes["kind"].value)
        self.assertEqual("cooked", attributes["state"].value)
        self.assertEqual("burnt", attributes["condition"].value)
        self.assertEqual("counter", attributes["location"].value)
        self.assertEqual(200, attributes["condition"].valid_from_us)
        self.assertEqual(300, attributes["location"].valid_from_us)
        self.assertEqual({"kind", "state", "condition", "location"}, set(attributes))
        self.assertEqual("cooking", self.repository.lookup_actor_state(
            self.character, "user", "activity", historical_at_us=250,
        ).state.value)
        self.assertEqual("kitchen", self.repository.lookup_actor_state(
            self.character, "user", "location", historical_at_us=10_000,
        ).state.value)
        self.assertEqual(9_900, location.elapsed_us(10_000))

    def test_companion_shirt_immediate_consequences_reconfirm_without_resetting_start(self):
        t1 = "The companion is wearing a white shirt."
        self.event("shirt-1", 100, t1)
        first = self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            (
                self.update("scene", "shirt_1", "color", "white", t1, "white"),
                self.update("scene", "shirt_1", "worn_by", "companion", t1, "companion"),
            ),
            (ActiveSceneSubjectIntroduction("shirt_1", "shirt", t1.index("shirt"), t1.index("shirt") + 5),),
        ), evidence_event_id="shirt-1")
        shirt = next(item["scene_subject_id"] for item in first if item["operation"] == "introduce")

        t2 = "Wine spilled on the companion's shirt."
        self.event("shirt-2", 200, t2)
        self.store.apply_active_state_proposal(self.character, ActiveStateProposal((
            self.update("scene", shirt, "wet", "true", t2, "spilled", basis="immediate_consequence", rule="spill_on_material.v1"),
            self.update("scene", shirt, "stain", "wine", t2, "Wine", basis="immediate_consequence", rule="spill_on_material.v1"),
        )), evidence_event_id="shirt-2")

        t3 = "The shirt is still white."
        self.event("shirt-3", 300, t3)
        self.store.apply_active_state_proposal(self.character, ActiveStateProposal((
            self.update("scene", shirt, "color", "white", t3, "white"),
        )), evidence_event_id="shirt-3")

        attrs = self.values(self.repository.lookup_scene_attributes(self.character, shirt))
        self.assertEqual("white", attrs["color"].value)
        self.assertEqual((100, 300), (attrs["color"].valid_from_us, attrs["color"].last_confirmed_at_us))
        self.assertEqual("companion", attrs["worn_by"].value)
        self.assertEqual("true", attrs["wet"].value)
        self.assertEqual("wine", attrs["stain"].value)
        self.assertEqual(200, attrs["wet"].valid_from_us)
        self.assertEqual(200, attrs["stain"].valid_from_us)
        self.assertIsNone(attrs["wet"].last_confirmed_at_us)
        self.assertEqual(250, attrs["wet"].elapsed_us(450))
        roles = self.store.connection.execute(
            "SELECT DISTINCT evidence_role FROM claim_evidence WHERE character_id=? AND claim_id IN (?, ?)",
            (self.character, attrs["wet"].state_id, attrs["stain"].state_id),
        ).fetchall()
        self.assertEqual({"immediate_user_event_consequence"}, {row[0] for row in roles})
        self.assertEqual("immediate_consequence_v1", self.store.connection.execute(
            "SELECT curator_policy_version FROM claims WHERE character_id=? AND claim_id=?",
            (self.character, attrs["wet"].state_id),
        ).fetchone()[0])

    def test_user_and_companion_targets_are_symmetric_and_two_subjects_do_not_collide(self):
        text = "User kitchen cooking; companion living room reading; two drinks."
        self.event("actors", 100, text)
        result = self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            (
                self.update("actor", "user", "location", "kitchen", text, "kitchen"),
                self.update("actor", "user", "activity", "cooking", text, "cooking"),
                self.update("actor", "companion", "location", "living room", text, "living room"),
                self.update("actor", "companion", "activity", "reading", text, "reading"),
                self.update("scene", "drink_1", "location", "counter", text, "drinks"),
                self.update("scene", "drink_2", "location", "counter", text, "drinks"),
            ),
            (
                ActiveSceneSubjectIntroduction("drink_1", "drink", text.index("drinks"), text.index("drinks") + 6),
                ActiveSceneSubjectIntroduction("drink_2", "drink", text.index("drinks"), text.index("drinks") + 6),
            ),
        ), evidence_event_id="actors")
        drinks = [item["scene_subject_id"] for item in result if item["operation"] == "introduce"]
        self.assertEqual(2, len(drinks))
        self.assertEqual("kitchen", self.repository.lookup_actor_state(self.character, "user", "location").state.value)
        self.assertEqual("living room", self.repository.lookup_actor_state(self.character, "companion", "location").state.value)
        self.assertEqual("reading", self.repository.lookup_actor_state(self.character, "companion", "activity").state.value)
        self.assertEqual(2, len(self.repository.list_scene_subjects(self.character)))
        self.assertEqual({"drink"}, {self.values(self.repository.lookup_scene_attributes(self.character, item))["kind"].value for item in drinks})
        self.assertEqual((), self.repository.lookup_scene_attributes(self.other, drinks[0]))
        subject_plan = self.store.connection.execute(
            "EXPLAIN QUERY PLAN SELECT scene_subject_id FROM active_scene_subjects "
            "WHERE character_id=? AND retired_at_us IS NULL ORDER BY introduced_at_us DESC LIMIT 8",
            (self.character,),
        ).fetchall()
        self.assertTrue(any("active_scene_subjects_current_lookup" in row[3] for row in subject_plan), subject_plan)
        prefix = f"active.scene.{drinks[0]}."
        attribute_plan = self.store.connection.execute(
            "EXPLAIN QUERY PLAN SELECT claim_id FROM claims WHERE character_id=? AND claim_type=? "
            "AND subject_key >= ? AND subject_key < ?",
            (self.character, ACTIVE_STATE, prefix, prefix + "\uffff"),
        ).fetchall()
        self.assertTrue(any("claims_durable_lookup" in row[3] for row in attribute_plan), attribute_plan)

    def test_retirement_closes_attributes_and_preserves_history_without_resurrection(self):
        t1 = "A food item is on the counter."
        self.event("retire-1", 100, t1)
        result = self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            (self.update("scene", "food_1", "location", "counter", t1, "counter"),),
            (ActiveSceneSubjectIntroduction("food_1", "food", 2, 6),),
        ), evidence_event_id="retire-1")
        food = next(item["scene_subject_id"] for item in result if item["operation"] == "introduce")
        t2 = "I threw the food away."
        self.event("retire-2", 200, t2)
        self.store.apply_active_state_proposal(self.character, ActiveStateProposal(
            (), (), (ActiveSceneSubjectRetirement(food, t2.index("food"), t2.index("food") + 4),),
        ), evidence_event_id="retire-2")
        self.assertEqual((), self.repository.lookup_scene_attributes(self.character, food))
        self.assertEqual((), self.repository.list_scene_subjects(self.character))
        historical = self.values(self.repository.lookup_scene_attributes(self.character, food, historical_at_us=150))
        self.assertEqual("counter", historical["location"].value)
        self.assertEqual("cancelled", self.store.effective_status(self.character, historical["location"].state_id))

    def test_invalid_assistant_foreign_unknown_and_atomic_proposals_are_rejected(self):
        text = "I am in the kitchen."
        self.event("assistant", 100, text, actor="assistant")
        proposal = ActiveStateProposal((self.update("actor", "companion", "location", "kitchen", text, "kitchen"),))
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, proposal, evidence_event_id="assistant")
        self.event("foreign", 100, text, character=self.other)
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, proposal, evidence_event_id="foreign")
        self.event("user", 200, text)
        bad = ActiveStateProposal((
            self.update("actor", "user", "location", "kitchen", text, "kitchen"),
            ActiveStateProposalUpdate(None, "set", "value", 0, 1, "scene", "missing", "unknown"),
        ))
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, bad, evidence_event_id="user")
        self.assertIsNone(self.repository.lookup_actor_state(self.character, "user", "location").state)
        self.assertFalse(self.store.connection.execute(
            "SELECT 1 FROM claims WHERE character_id=? AND claim_type=?", (self.character, ACTIVE_STATE),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
