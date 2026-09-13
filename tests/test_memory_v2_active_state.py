import unittest
import uuid

from aifren.memory_v2_store import ACTIVE_STATE, DURABLE_CORE_FACT, MemoryV2Repository, MemoryV2Store, StoreError


class ActiveStateTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.repository = MemoryV2Repository(self.store)
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.store.create_character(self.character_a, "A")
        self.store.create_character(self.character_b, "B")

    def tearDown(self):
        self.store.close()

    def event(self, character_id, event_id, timestamp, *, actor="user", content="state evidence"):
        sequence = self.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
        ).fetchone()[0]
        self.store.add_event(character_id, event_id, sequence, actor_kind=actor, recorded_at_us=timestamp,
                             content_text=content, source_origin="synthetic-test")

    def lookup(self, *, character=None, at=None):
        return self.repository.lookup_active_state(
            character or self.character_a, "active.avatar.headwear", historical_at_us=at,
        )

    def test_set_replace_clear_and_historical_values_are_append_only(self):
        self.event(self.character_a, "red-event", 100, content="The red hat is equipped.")
        self.store.set_active_state(self.character_a, "red", subject_key="active.avatar.headwear",
                                    value="red hat", evidence_event_id="red-event")
        self.assertEqual("red hat", self.lookup().state.value)
        self.assertEqual("red", self.lookup().state.state_id)

        self.event(self.character_a, "blue-event", 200, content="The blue hat is equipped.")
        self.store.set_active_state(self.character_a, "blue", subject_key="active.avatar.headwear",
                                    value="blue hat", evidence_event_id="blue-event")
        self.assertEqual(("blue", "blue hat"), (self.lookup().state.state_id, self.lookup().state.value))
        self.assertEqual(("red", "red hat"), (self.lookup(at=150).state.state_id, self.lookup(at=150).state.value))
        self.assertEqual(("blue", "blue hat"), (self.lookup(at=250).state.state_id, self.lookup(at=250).state.value))
        self.assertEqual("superseded", self.store.effective_status(self.character_a, "red"))

        self.event(self.character_a, "clear-event", 300, content="The hat was removed.")
        self.assertTrue(self.store.clear_active_state(self.character_a, subject_key="active.avatar.headwear",
                                                       evidence_event_id="clear-event"))
        self.assertIsNone(self.lookup().state)
        self.assertEqual("blue", self.lookup(at=250).state.state_id)
        self.assertIsNone(self.lookup(at=350).state)
        self.assertEqual("cancelled", self.store.effective_status(self.character_a, "blue"))
        self.assertFalse(self.store.clear_active_state(self.character_a, subject_key="active.avatar.headwear",
                                                        evidence_event_id="clear-event"))

    def test_character_evidence_provenance_and_slot_validation_are_hard(self):
        self.event(self.character_a, "assistant-event", 100, actor="assistant")
        self.event(self.character_b, "foreign-event", 100)
        with self.assertRaises(StoreError):
            self.store.set_active_state(self.character_a, "assistant", subject_key="active.avatar.headwear",
                                        value="red hat", evidence_event_id="assistant-event")
        with self.assertRaises(StoreError):
            self.store.set_active_state(self.character_a, "foreign", subject_key="active.avatar.headwear",
                                        value="red hat", evidence_event_id="foreign-event")
        with self.assertRaises(StoreError):
            self.store.set_active_state(self.character_a, "unknown", subject_key="active.unregistered.current",
                                        value="reading", evidence_event_id="assistant-event")
        self.assertIsNone(self.lookup().state)

    def test_lookup_rejects_manual_incomplete_and_assistant_authored_rows(self):
        self.event(self.character_a, "user-event", 100)
        self.event(self.character_a, "assistant-event", 101, actor="assistant")
        with self.store.transaction():
            for claim_id, provenance, event_id in (
                ("incomplete", "incomplete", "user-event"),
                ("assistant", "complete", "assistant-event"),
            ):
                self.store._insert_claim(
                    self.character_a, claim_id, claim_type=ACTIVE_STATE, assertion_scope="active_state",
                    subject_key="active.avatar.headwear", content="unsafe", importance=5, confidence=None,
                    valid_from_us=1, valid_to_us=None, temporal_precision="instant", temporal_expression=None,
                    provenance_state=provenance, curator_name=None, curator_version=None,
                    curator_policy_version=None, legacy_metadata=None, created_at_us=1, updated_at_us=1,
                )
                self.store.connection.execute(
                    "INSERT INTO claim_evidence VALUES (?, ?, ?, 'direct_user_statement', NULL, NULL, NULL, 1.0, NULL, 1)",
                    (self.character_a, claim_id, event_id),
                )
        self.assertIsNone(self.lookup().state)

    def test_active_and_durable_namespaces_do_not_cross_and_exact_lookup_is_indexed(self):
        self.event(self.character_a, "state-event", 100, content="Red hat equipped.")
        self.store.set_active_state(self.character_a, "red", subject_key="active.avatar.headwear",
                                    value="red hat", evidence_event_id="state-event")
        self.event(self.character_a, "name-event", 101, content="My name is Elena.")
        self.store.add_durable_claim(self.character_a, "name", subject_key="identity.name",
                                     content="The user's name is Elena.", evidence_event_id="name-event",
                                     evidence_role="direct_user_statement", valid_from_us=101, created_at_us=101)
        self.assertEqual("red", self.lookup().state.state_id)
        self.assertEqual(["name"], [item.claim_id for item in self.repository.lookup_durable_core(
            self.character_a, "identity.name", historical_at_us=200,
        ).candidates])
        self.assertEqual(ACTIVE_STATE, self.store.connection.execute(
            "SELECT claim_type FROM claims WHERE character_id=? AND claim_id='red'", (self.character_a,)
        ).fetchone()[0])
        self.assertEqual(DURABLE_CORE_FACT, self.store.connection.execute(
            "SELECT claim_type FROM claims WHERE character_id=? AND claim_id='name'", (self.character_a,)
        ).fetchone()[0])
        plan = self.store.connection.execute(
            "EXPLAIN QUERY PLAN SELECT claim_id FROM claims WHERE character_id=? AND claim_type=? AND subject_key=?",
            (self.character_a, ACTIVE_STATE, "active.avatar.headwear"),
        ).fetchall()
        self.assertTrue(any("claims_durable_lookup" in row[3] for row in plan), plan)

    def test_character_scope_and_archived_state_do_not_leak(self):
        self.event(self.character_a, "a-event", 100)
        self.event(self.character_b, "b-event", 100)
        self.store.set_active_state(self.character_a, "a", subject_key="active.avatar.headwear", value="red hat", evidence_event_id="a-event")
        self.store.set_active_state(self.character_b, "b", subject_key="active.avatar.headwear", value="blue hat", evidence_event_id="b-event")
        self.assertEqual("a", self.lookup().state.state_id)
        self.assertEqual("b", self.lookup(character=self.character_b).state.state_id)
        self.store.add_status(self.character_a, "a", "archived", created_at_us=200)
        self.assertIsNone(self.lookup().state)


if __name__ == "__main__":
    unittest.main()
