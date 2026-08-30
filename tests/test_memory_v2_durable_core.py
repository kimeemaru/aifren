import unittest
import uuid

from memory_v2_store import DURABLE_CORE_FACT, MemoryV2Repository, MemoryV2Store, StoreError


class DurableCoreStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryV2Store()
        self.repository = MemoryV2Repository(self.store)
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.store.create_character(self.character_a, "A")
        self.store.create_character(self.character_b, "B")

    def tearDown(self):
        self.store.close()

    def event(self, character_id, event_id, *, actor="user", recorded_at=1_000, content="Synthetic user statement."):
        sequence = self.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?", (character_id,)
        ).fetchone()[0]
        self.store.add_event(character_id, event_id, sequence, actor_kind=actor,
                             recorded_at_us=recorded_at, content_text=content, source_origin="test")

    def durable(self, claim_id, subject_key, content, event_id, *, character=None, created_at=1_000,
                supersedes=None):
        self.store.add_durable_claim(
            character or self.character_a, claim_id, subject_key=subject_key, content=content,
            evidence_event_id=event_id, evidence_role="direct_user_statement",
            created_at_us=created_at, valid_from_us=created_at, supersedes_claim_id=supersedes,
        )

    def lookup(self, subject_key, *, character=None, at=None):
        return self.repository.lookup_durable_core(character or self.character_a, subject_key,
                                                   historical_at_us=at)

    def test_valid_name_is_exact_slot_lookup_and_age_independent(self):
        self.event(self.character_a, "name-event", recorded_at=1_000, content="My name is Avery.")
        self.durable("name-avery", "identity.name", "The user's name is Avery.", "name-event")

        seven_years_us = 7 * 365 * 24 * 60 * 60 * 1_000_000
        result = self.lookup("identity.name", at=1_000 + seven_years_us)

        self.assertEqual(["name-avery"], [item.claim_id for item in result.candidates])
        self.assertEqual(("name-event",), result.candidates[0].evidence_event_ids)

    def test_durable_creation_rejects_invalid_contracts(self):
        self.event(self.character_a, "user-event")
        self.event(self.character_a, "assistant-event", actor="assistant")
        self.event(self.character_b, "foreign-event")

        with self.assertRaises(StoreError):
            self.durable("invalid-key", "identity.favorite_color", "The user likes amber.", "user-event")
        with self.assertRaises(StoreError):
            self.store.add_claim(self.character_a, "wrong-scope", claim_type=DURABLE_CORE_FACT,
                                 assertion_scope="assistant_fact", content="Bad", provenance_state="incomplete")
        with self.assertRaises(StoreError):
            self.durable("assistant-only", "identity.name", "The user's name is River.", "assistant-event")
        with self.assertRaises(StoreError):
            self.durable("foreign", "identity.name", "The user's name is River.", "foreign-event")
        with self.assertRaises(StoreError):
            self.durable("missing-evidence", "identity.name", "The user's name is River.", "absent")

    def test_current_correction_hides_predecessor_and_history_preserves_it(self):
        self.event(self.character_a, "old-home-event", recorded_at=1_000, content="I live in Cedar Terrace.")
        self.durable("home-cedar", "home.primary", "The user lives in Cedar Terrace.", "old-home-event", created_at=1_000)
        self.event(self.character_a, "new-home-event", recorded_at=2_000, content="I now live in Port Meridian.")
        self.durable("home-port", "home.primary", "The user lives in Port Meridian.", "new-home-event",
                     created_at=2_000, supersedes="home-cedar")

        self.assertEqual(["home-port"], [item.claim_id for item in self.lookup("home.primary").candidates])
        self.assertEqual(["home-cedar"], [item.claim_id for item in self.lookup("home.primary", at=1_500).candidates])
        self.assertEqual(["home-port"], [item.claim_id for item in self.lookup("home.primary", at=2_500).candidates])
        with self.assertRaises(StoreError):
            self.repository.supersede(self.character_a, "home-cedar", "home-port")
        with self.assertRaises(StoreError):
            self.store.add_status(self.character_a, "home-port", "superseded")

    def test_archive_character_and_legacy_rows_do_not_qualify(self):
        self.event(self.character_a, "name-event", content="My name is Avery.")
        self.durable("name-avery", "identity.name", "The user's name is Avery.", "name-event")
        self.store.add_status(self.character_a, "name-avery", "archived", created_at_us=2_000)
        self.assertEqual((), self.lookup("identity.name").candidates)

        self.event(self.character_b, "b-name-event", content="My name is Blair.")
        self.durable("name-blair", "identity.name", "The user's name is Blair.", "b-name-event", character=self.character_b)
        self.assertEqual((), self.lookup("identity.name").candidates)
        self.assertEqual(["name-blair"], [item.claim_id for item in self.lookup("identity.name", character=self.character_b).candidates])

        self.store.add_claim(self.character_a, "legacy-name", claim_type="stable_user_fact", assertion_scope="user_fact",
                             subject_key="identity.name", content="The user's name is Legacy.",
                             provenance_state="legacy_unverified")
        self.store.attach_evidence(self.character_a, "legacy-name", "name-event", evidence_role="direct_user_statement")
        self.assertEqual((), self.lookup("identity.name").candidates)

    def test_query_rejects_adversarial_incomplete_or_assistant_durable_rows(self):
        self.event(self.character_a, "user-event", content="My name is Avery.")
        self.event(self.character_a, "assistant-event", actor="assistant", content="Your name is River.")
        with self.store.transaction():
            self.store._insert_claim(
                self.character_a, "incomplete", claim_type=DURABLE_CORE_FACT, assertion_scope="user_fact",
                subject_key="identity.name", content="The user's name is Incomplete.", importance=5,
                confidence=None, valid_from_us=1_000, valid_to_us=None, temporal_precision="unknown",
                temporal_expression=None, provenance_state="incomplete", curator_name=None,
                curator_version=None, curator_policy_version=None, legacy_metadata=None,
                created_at_us=1_000, updated_at_us=1_000,
            )
            self.store.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, 'direct_user_statement', NULL, NULL, NULL, 1.0, NULL, 1)",
                (self.character_a, "incomplete", "user-event"),
            )
            self.store._insert_claim(
                self.character_a, "assistant-durable", claim_type=DURABLE_CORE_FACT, assertion_scope="user_fact",
                subject_key="identity.name", content="The user's name is River.", importance=5,
                confidence=None, valid_from_us=1_000, valid_to_us=None, temporal_precision="unknown",
                temporal_expression=None, provenance_state="complete", curator_name=None,
                curator_version=None, curator_policy_version=None, legacy_metadata=None,
                created_at_us=1_000, updated_at_us=1_000,
            )
            self.store.connection.execute(
                "INSERT INTO claim_evidence VALUES (?, ?, ?, 'direct_user_statement', NULL, NULL, NULL, 1.0, NULL, 1)",
                (self.character_a, "assistant-durable", "assistant-event"),
            )
        self.assertEqual((), self.lookup("identity.name").candidates)

    def test_preference_is_singleton_per_governed_domain_and_relation_ids_are_opaque(self):
        self.event(self.character_a, "tea-event", content="I prefer jasmine tea.")
        self.durable("tea", "preference.beverage", "The user prefers jasmine tea.", "tea-event")
        self.event(self.character_a, "coffee-event", recorded_at=2_000, content="I prefer coffee now.")
        with self.assertRaises(StoreError):
            self.durable("coffee", "preference.beverage", "The user prefers coffee.", "coffee-event", created_at=2_000)
        self.durable("coffee", "preference.beverage", "The user prefers coffee.", "coffee-event",
                     created_at=2_000, supersedes="tea")
        self.assertEqual(["coffee"], [item.claim_id for item in self.lookup("preference.beverage").candidates])

        self.event(self.character_a, "sibling-event", recorded_at=3_000, content="Mara is my sister.")
        self.durable("sibling", "relation.sibling.p-0123456789abcdef", "Mara is the user's sister.",
                     "sibling-event", created_at=3_000)
        self.assertEqual(["sibling"], [item.claim_id for item in self.lookup("relation.sibling.p-0123456789abcdef").candidates])
        with self.assertRaises(StoreError):
            self.lookup("relation.sister.mara")

    def test_generic_stable_fact_is_unchanged_and_durable_lookup_is_indexed_and_empty_on_no_match(self):
        self.event(self.character_a, "generic-event", content="The user likes astronomy.")
        self.store.add_claim(self.character_a, "generic", claim_type="stable_user_fact", assertion_scope="user_fact",
                             content="The user likes astronomy.", subject_key="profile.hobby")
        self.store.attach_evidence(self.character_a, "generic", "generic-event")
        self.assertEqual((), self.lookup("bio.occupation").candidates)

        indexes = {row[1] for row in self.store.connection.execute("PRAGMA index_list('claims')")}
        self.assertIn("claims_durable_lookup", indexes)
        plan = self.store.connection.execute(
            "EXPLAIN QUERY PLAN SELECT claim_id FROM claims WHERE character_id=? AND claim_type=? AND subject_key=?",
            (self.character_a, DURABLE_CORE_FACT, "bio.occupation"),
        ).fetchall()
        self.assertTrue(any("claims_durable_lookup" in row[3] for row in plan), plan)


if __name__ == "__main__":
    unittest.main()
