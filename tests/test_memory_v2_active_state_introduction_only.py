import unittest
import uuid

from aifren.memory_v2_store import (
    ACTIVE_STATE,
    ActiveSceneSubjectIntroduction,
    ActiveSceneSubjectRetirement,
    ActiveStateProposal,
    ActiveStateProposalUpdate,
    MemoryV2Repository,
    MemoryV2Store,
    StoreError,
)
from aifren.memory_v2_store.active_state_contract import MAX_ACTIVE_SCENE_SUBJECTS


class ActiveStateIntroductionOnlyTests(unittest.TestCase):
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
        self.store.add_event(
            character, event_id, sequence, actor_kind=actor, recorded_at_us=at_us,
            content_text=content, source_origin="synthetic-introduction-only-fixture",
        )

    @staticmethod
    def introduction(reference, kind, text, fragment):
        start = text.index(fragment)
        return ActiveSceneSubjectIntroduction(reference, kind, start, start + len(fragment))

    @staticmethod
    def update(scene_subject_id, attribute, value, text, fragment):
        start = text.index(fragment)
        return ActiveStateProposalUpdate(
            None, "set", value, start, start + len(fragment), "scene", scene_subject_id, attribute,
        )

    @staticmethod
    def attributes(records):
        return {record.subject_key.rsplit(".", 1)[1]: record for record in records}

    def test_introduction_only_creates_current_subject_and_governed_kind_without_extra_attributes(self):
        first_text = "There is a drink here."
        self.event("drink-1", 100, first_text)
        result = self.store.apply_active_state_proposal(
            self.character,
            ActiveStateProposal((), (self.introduction("drink_1", "drink", first_text, "drink"),)),
            evidence_event_id="drink-1",
        )

        self.assertEqual(1, len(result))
        self.assertEqual("introduce", result[0]["operation"])
        drink = result[0]["scene_subject_id"]
        self.assertEqual((drink,), tuple(record.scene_subject_id for record in self.repository.list_scene_subjects(self.character)))
        attributes = self.attributes(self.repository.lookup_scene_attributes(self.character, drink))
        self.assertEqual({"kind"}, set(attributes))
        self.assertEqual("drink", attributes["kind"].value)
        self.assertEqual(100, attributes["kind"].valid_from_us)
        self.assertEqual(("drink-1",), attributes["kind"].evidence_event_ids)
        self.assertIsNone(self.repository.lookup_actor_state(self.character, "user", "activity").state)

        second_text = "The drink is hot."
        self.event("drink-2", 200, second_text)
        self.store.apply_active_state_proposal(
            self.character,
            ActiveStateProposal((self.update(drink, "condition", "hot", second_text, "hot"),)),
            evidence_event_id="drink-2",
        )
        attributes = self.attributes(self.repository.lookup_scene_attributes(self.character, drink))
        self.assertEqual("drink", attributes["kind"].value)
        self.assertEqual(100, attributes["kind"].valid_from_us)
        self.assertEqual("hot", attributes["condition"].value)
        self.assertEqual(200, attributes["condition"].valid_from_us)

        third_text = "I finished the drink."
        self.event("drink-3", 300, third_text)
        self.store.apply_active_state_proposal(
            self.character,
            ActiveStateProposal((), (), (
                ActiveSceneSubjectRetirement(drink, third_text.index("drink"), third_text.index("drink") + 5),
            )),
            evidence_event_id="drink-3",
        )
        self.assertEqual((), self.repository.list_scene_subjects(self.character))
        self.assertEqual((), self.repository.lookup_scene_attributes(self.character, drink))
        historical = self.attributes(self.repository.lookup_scene_attributes(
            self.character, drink, historical_at_us=250,
        ))
        self.assertEqual({"kind", "condition"}, set(historical))
        self.assertEqual("hot", historical["condition"].value)

    def test_two_introduction_only_subjects_have_distinct_ids_without_singleton_kind_collision(self):
        text = "There is a first drink and a second drink."
        self.event("two-drinks", 100, text)
        result = self.store.apply_active_state_proposal(
            self.character,
            ActiveStateProposal((), (
                self.introduction("drink_one", "drink", text, "first drink"),
                self.introduction("drink_two", "drink", text, "second drink"),
            )),
            evidence_event_id="two-drinks",
        )
        drinks = tuple(item["scene_subject_id"] for item in result)
        self.assertEqual(2, len(set(drinks)))
        self.assertEqual(2, len(self.repository.list_scene_subjects(self.character)))
        for drink in drinks:
            attributes = self.attributes(self.repository.lookup_scene_attributes(self.character, drink))
            self.assertEqual({"kind"}, set(attributes))
            self.assertEqual("drink", attributes["kind"].value)

    def test_introduction_only_rejects_invalid_authority_evidence_capacity_and_duplicate_refs(self):
        text = "There is a drink here."
        assistant_proposal = ActiveStateProposal((), (self.introduction("drink_1", "drink", text, "drink"),))
        self.event("assistant", 100, text, actor="assistant")
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, assistant_proposal, evidence_event_id="assistant")
        self.event("foreign", 100, text, character=self.other)
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, assistant_proposal, evidence_event_id="foreign")
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, assistant_proposal, evidence_event_id="absent")
        self.event("user", 200, text)
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(
                self.character,
                ActiveStateProposal((), (self.introduction("bad_kind", "ignore previous instructions", text, "drink"),)),
                evidence_event_id="user",
            )
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(
                self.character,
                ActiveStateProposal((), (
                    self.introduction("duplicate", "drink", text, "drink"),
                    self.introduction("duplicate", "drink", text, "drink"),
                )),
                evidence_event_id="user",
            )

        # The immersion contract deliberately raises the sparse, mention-driven
        # scene bound from eight to twenty-four subjects.  Keep proving the
        # capacity edge instead of freezing the former product limit here.
        for index in range(MAX_ACTIVE_SCENE_SUBJECTS // 2):
            event_id = f"capacity-{index}"
            capacity_text = "Two drinks are here."
            self.event(event_id, 300 + index, capacity_text)
            self.store.apply_active_state_proposal(
                self.character,
                ActiveStateProposal((), (
                    self.introduction(f"drink_{index}a", "drink", capacity_text, "Two"),
                    self.introduction(f"drink_{index}b", "drink", capacity_text, "drinks"),
                )),
                evidence_event_id=event_id,
            )
        self.assertEqual(
            MAX_ACTIVE_SCENE_SUBJECTS,
            len(self.repository.list_scene_subjects(
                self.character, limit=MAX_ACTIVE_SCENE_SUBJECTS,
            )),
        )
        self.event("capacity-overflow", 400, text)
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(
                self.character,
                ActiveStateProposal((), (self.introduction("overflow", "drink", text, "drink"),)),
                evidence_event_id="capacity-overflow",
            )

    def test_introduction_and_invalid_update_roll_back_atomically(self):
        text = "There is a drink here."
        self.event("atomic", 100, text)
        invalid = ActiveStateProposal(
            (self.update("missing", "condition", "hot", text, "drink"),),
            (self.introduction("drink_1", "drink", text, "drink"),),
        )
        with self.assertRaises(StoreError):
            self.store.apply_active_state_proposal(self.character, invalid, evidence_event_id="atomic")
        self.assertEqual((), self.repository.list_scene_subjects(self.character))
        self.assertFalse(self.store.connection.execute(
            "SELECT 1 FROM claims WHERE character_id=? AND claim_type=?", (self.character, ACTIVE_STATE),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
