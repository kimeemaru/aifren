import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository
from memory_v2_store.identity_name import extract_identity_name_assertion
from memory_v2_store.store import parse_timestamp_us


class IdentityNameExtractionTests(unittest.TestCase):
    def test_only_anchored_direct_assertions_are_supported(self):
        first = extract_identity_name_assertion("My name is Elena.")
        second = extract_identity_name_assertion("You can call me Elena!")
        self.assertEqual(("Elena", 11, 16, "my_name_is"),
                         (first.value, first.excerpt_start_cp, first.excerpt_end_cp, first.form))
        self.assertEqual(("Elena", "call_me"), (second.value, second.form))

        for text in (
            "What's my name?", "If my name were Elena...", "My character's name is Elena.",
            "Elena is my sister.", 'She said "My name is Elena."', '"My name is Elena."',
            "I'm Elena.", "I am Elena.", "My name is Elena, by the way.", "My name is...",
            "You can call me later.", "Something unrelated.",
        ):
            self.assertIsNone(extract_identity_name_assertion(text), text)


class IdentityNamePopulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conversation = self.root / "conversation.json"
        self.conversation.write_text("[]", encoding="utf-8")
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_a, display_name="A",
            memory_file=self.root / "memories.json",
        )

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def observe(self, content, *, index, timestamp, writer=None, role="user"):
        message = {"role": role, "content": content, "timestamp": timestamp}
        records = json.loads(self.conversation.read_text(encoding="utf-8"))
        while len(records) <= index:
            records.append({"role": "assistant", "content": "filler", "timestamp": timestamp})
        records[index] = message
        self.conversation.write_text(json.dumps(records), encoding="utf-8")
        return (writer or self.writer).observe_canonical_user_message(
            message, conversation_index=index, conversation_file=self.conversation,
        )

    def lookup(self, *, at=None, character=None, writer=None):
        return MemoryV2Repository((writer or self.writer).store).lookup_durable_core(
            character or self.character_a, "identity.name", historical_at_us=at,
        )

    def test_positive_creation_binds_exact_user_event_and_span(self):
        result = self.observe("My name is Elena.", index=0, timestamp="2020-01-01T00:00:00Z")
        self.assertEqual("created", result["state"])
        candidate = self.lookup().candidates[0]
        self.assertEqual("The user's name is Elena.", candidate.content)
        self.assertEqual((result["event_id"],), candidate.evidence_event_ids)

        claim = self.writer.store.connection.execute(
            "SELECT claim_type, assertion_scope, subject_key, provenance_state FROM claims WHERE claim_id=?",
            (result["claim_id"],),
        ).fetchone()
        self.assertEqual(
            ("durable_core_fact", "user_fact", "identity.name", "complete"), tuple(claim),
        )
        evidence = self.writer.store.connection.execute(
            """SELECT ce.evidence_role, ce.excerpt_start_cp, ce.excerpt_end_cp, ce.excerpt_hash,
                      e.actor_kind, e.content_text, e.source_reference
                   FROM claim_evidence ce JOIN events e
                     ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=?""",
            (self.character_a, result["claim_id"]),
        ).fetchone()
        self.assertEqual(("direct_user_statement", 11, 16, "user", "My name is Elena.", "conversation.json#0"),
                         (evidence["evidence_role"], evidence["excerpt_start_cp"], evidence["excerpt_end_cp"],
                          evidence["actor_kind"], evidence["content_text"], evidence["source_reference"]))
        self.assertEqual(hashlib.sha256(b"Elena").hexdigest(), evidence["excerpt_hash"])

    def test_unpersisted_message_cannot_be_promoted(self):
        result = self.writer.observe_canonical_user_message(
            {"role": "user", "content": "My name is Elena.", "timestamp": "2020-01-01T00:00:00Z"},
            conversation_index=0, conversation_file=self.conversation,
        )
        self.assertEqual(("ignored", "canonical_message_not_persisted"), (result["state"], result["reason"]))
        self.assertEqual(0, self.writer.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

    def test_supported_call_me_form_and_reconfirmation_are_idempotent(self):
        created = self.observe("You can call me Elena.", index=0, timestamp="2020-01-01T00:00:00Z")
        repeated = self.observe("My name is Elena.", index=1, timestamp="2020-02-01T00:00:00Z")
        self.assertEqual("created", created["state"])
        self.assertEqual(("unchanged", created["claim_id"]), (repeated["state"], repeated["claim_id"]))
        self.assertEqual(1, self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
            (self.character_a,),
        ).fetchone()[0])

    def test_negative_and_assistant_messages_create_no_durable_claim_or_event(self):
        negatives = (
            "What's my name?", "If my name were Elena...", "My character's name is Elena.",
            "Elena is my sister.", 'She said "My name is Elena."', '"My name is Elena."',
            "I'm Elena.", "An unrelated sentence.",
        )
        for index, content in enumerate(negatives):
            result = self.observe(content, index=index, timestamp="2020-01-01T00:00:00Z")
            self.assertEqual("ignored", result["state"], content)
        assistant = self.observe("Your name is Elena.", index=99, timestamp="2020-01-01T00:00:00Z", role="assistant")
        self.assertEqual(("ignored", "not_user_message"), (assistant["state"], assistant["reason"]))
        self.assertEqual(0, self.writer.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        self.assertEqual(0, self.writer.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def test_correction_history_age_and_character_scope(self):
        first = self.observe("My name is Elena.", index=0, timestamp="2020-01-01T00:00:00Z")
        second = self.observe("My name is Mara.", index=1, timestamp="2021-01-01T00:00:00Z")
        self.assertEqual(("created", "superseded"), (first["state"], second["state"]))
        self.assertEqual([second["claim_id"]], [item.claim_id for item in self.lookup().candidates])
        before_correction = parse_timestamp_us("2020-06-01T00:00:00Z")
        self.assertEqual([first["claim_id"]], [item.claim_id for item in self.lookup(at=before_correction).candidates])
        seven_years = parse_timestamp_us("2027-01-01T00:00:00Z")
        self.assertEqual([second["claim_id"]], [item.claim_id for item in self.lookup(at=seven_years).candidates])

        second_writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_b, display_name="B", memory_file=self.root / "b-memories.json",
            database_path=self.writer.database_path,
        )
        try:
            other = self.observe("My name is Blair.", index=0, timestamp="2020-01-01T00:00:00Z", writer=second_writer)
            self.assertEqual("created", other["state"])
            self.assertEqual([second["claim_id"]], [item.claim_id for item in self.lookup().candidates])
            self.assertEqual([other["claim_id"]], [item.claim_id for item in self.lookup(character=self.character_b, writer=second_writer).candidates])
        finally:
            second_writer.close()


if __name__ == "__main__":
    unittest.main()
