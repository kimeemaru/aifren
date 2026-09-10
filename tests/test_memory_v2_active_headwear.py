import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from assistant_service import AssistantService
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository
from memory_v2_store.active_state_headwear import extract_headwear_state_assertion
from memory_v2_store.store import parse_timestamp_us


class HeadwearExtractionTests(unittest.TestCase):
    def test_only_small_anchored_present_and_clear_grammar_is_supported(self):
        present = extract_headwear_state_assertion("She is wearing a red hat.")
        contraction = extract_headwear_state_assertion("She's wearing a blue hat.")
        imperative = extract_headwear_state_assertion("Put the black cap on her.")
        present_clear = extract_headwear_state_assertion("She isn't wearing a hat anymore.")
        clear = extract_headwear_state_assertion("Take the hat off.")
        past_clear = extract_headwear_state_assertion("She took the hat off.")
        self.assertEqual(("set", "red hat", 17, 24, "present_wearing"),
                         (present.operation, present.value, present.excerpt_start_cp, present.excerpt_end_cp, present.form))
        self.assertEqual(("set", "blue hat", "present_wearing"),
                         (contraction.operation, contraction.value, contraction.form))
        self.assertEqual(("set", "black cap", "imperative_put_on"),
                         (imperative.operation, imperative.value, imperative.form))
        self.assertEqual(("clear", None, "present_not_wearing"),
                         (present_clear.operation, present_clear.value, present_clear.form))
        self.assertEqual(("clear", None, "imperative_take_off"), (clear.operation, clear.value, clear.form))
        self.assertEqual(("clear", None, "past_take_off"), (past_clear.operation, past_clear.value, past_clear.form))

        for text in (
            "I bought a red hat.", "She used to wear a red hat.", "She might wear a red hat later.",
            "If she wore a red hat...", "Her character in the story wears a hat.",
            "I like that blue hat.", "Do you remember when she wore a hat?",
            'She said "She is wearing a red hat."', '"She is wearing a red hat."',
            "She was wearing a red hat.", "She is wearing a scarf.", "Take it off.",
        ):
            self.assertIsNone(extract_headwear_state_assertion(text), text)


class HeadwearPopulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conversation = self.root / "conversation.json"
        self.conversation.write_text("[]", encoding="utf-8")
        self.character_a = str(uuid.uuid4())
        self.character_b = str(uuid.uuid4())
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_a, display_name="A", memory_file=self.root / "memories.json",
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
        return (writer or self.writer).observe_canonical_user_active_state(
            message, conversation_index=index, conversation_file=self.conversation,
        )

    def lookup(self, *, at=None, character=None, writer=None):
        return MemoryV2Repository((writer or self.writer).store).lookup_active_state(
            character or self.character_a, "active.avatar.headwear", historical_at_us=at,
        )

    def test_set_binds_exact_persisted_user_event_and_value_span(self):
        text = "She is wearing a red hat."
        result = self.observe(text, index=0, timestamp="2020-01-01T00:00:00Z")
        self.assertEqual("created", result["state"])
        state = self.lookup().state
        self.assertEqual(("red hat", result["state_id"], (result["event_id"],)),
                         (state.value, state.state_id, state.evidence_event_ids))

        evidence = self.writer.store.connection.execute(
            """SELECT ce.evidence_role, ce.excerpt_start_cp, ce.excerpt_end_cp, ce.excerpt_hash,
                      e.actor_kind, e.content_text, e.source_reference
                   FROM claim_evidence ce JOIN events e
                     ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                 WHERE ce.character_id=? AND ce.claim_id=?""",
            (self.character_a, result["state_id"]),
        ).fetchone()
        start = text.index("red hat")
        self.assertEqual(("direct_user_statement", start, start + len("red hat"), "user", text, "conversation.json#0"),
                         (evidence["evidence_role"], evidence["excerpt_start_cp"], evidence["excerpt_end_cp"],
                          evidence["actor_kind"], evidence["content_text"], evidence["source_reference"]))
        self.assertEqual(hashlib.sha256(b"red hat").hexdigest(), evidence["excerpt_hash"])

    def test_replace_clear_history_and_repetition_are_deterministic(self):
        red = self.observe("She is wearing a red hat.", index=0, timestamp="2020-01-01T00:00:00Z")
        repeated = self.observe("Put the red hat on her.", index=1, timestamp="2020-02-01T00:00:00Z")
        blue = self.observe("She's wearing a blue hat.", index=2, timestamp="2021-01-01T00:00:00Z")
        clear = self.observe("She took the hat off.", index=3, timestamp="2022-01-01T00:00:00Z")
        self.assertEqual(("created", "unchanged", "replaced", "cleared"),
                         (red["state"], repeated["state"], blue["state"], clear["state"]))
        self.assertIsNone(self.lookup().state)
        self.assertEqual("red hat", self.lookup(at=parse_timestamp_us("2020-06-01T00:00:00Z")).state.value)
        self.assertEqual("blue hat", self.lookup(at=parse_timestamp_us("2021-06-01T00:00:00Z")).state.value)
        self.assertIsNone(self.lookup(at=parse_timestamp_us("2022-06-01T00:00:00Z")).state)
        self.assertEqual("superseded", self.writer.store.effective_status(self.character_a, red["state_id"]))
        self.assertEqual("cancelled", self.writer.store.effective_status(self.character_a, blue["state_id"]))
        clear_source = self.writer.store.connection.execute(
            "SELECT source_event_id FROM claim_status_events WHERE character_id=? AND claim_id=? ORDER BY status_event_id DESC LIMIT 1",
            (self.character_a, blue["state_id"]),
        ).fetchone()[0]
        self.assertEqual(clear["event_id"], clear_source)
        self.assertEqual(2, self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='active_state'",
            (self.character_a,),
        ).fetchone()[0])

    def test_unpersisted_assistant_and_negative_text_cannot_mutate_state(self):
        message = {"role": "user", "content": "She is wearing a red hat.", "timestamp": "2020-01-01T00:00:00Z"}
        unpersisted = self.writer.observe_canonical_user_active_state(
            message, conversation_index=0, conversation_file=self.conversation,
        )
        self.assertEqual(("ignored", "canonical_message_not_persisted"), (unpersisted["state"], unpersisted["reason"]))
        negatives = (
            "I bought a red hat.", "She used to wear a red hat.", "If she wore a red hat...",
            "Do you remember when she wore a hat?", '"She is wearing a red hat."',
        )
        for index, text in enumerate(negatives, 1):
            result = self.observe(text, index=index, timestamp="2020-01-01T00:00:00Z")
            self.assertEqual("ignored", result["state"], text)
        assistant = self.observe("She is wearing a blue hat.", index=99, timestamp="2020-01-01T00:00:00Z", role="assistant")
        self.assertEqual(("ignored", "not_user_message"), (assistant["state"], assistant["reason"]))
        self.assertIsNone(self.lookup().state)
        self.assertEqual(0, self.writer.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        self.assertEqual(0, self.writer.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def test_character_scope_keeps_identical_assertions_isolated(self):
        first = self.observe("She is wearing a red hat.", index=0, timestamp="2020-01-01T00:00:00Z")
        other = MemoryV2ShadowWriter(
            self.root, character_id=self.character_b, display_name="B", memory_file=self.root / "b.json",
            database_path=self.writer.database_path,
        )
        try:
            second = self.observe("She is wearing a blue hat.", index=1, timestamp="2020-01-01T00:00:00Z", writer=other)
            self.assertEqual("red hat", self.lookup().state.value)
            self.assertEqual("blue hat", self.lookup(character=self.character_b, writer=other).state.value)
            self.observe("Take the hat off.", index=2, timestamp="2021-01-01T00:00:00Z")
            self.assertIsNone(self.lookup().state)
            self.assertEqual(second["state_id"], self.lookup(character=self.character_b, writer=other).state.state_id)
            self.assertNotEqual(first["state_id"], second["state_id"])
        finally:
            other.close()


class _Conversation:
    def __init__(self):
        self.messages = []
        self.saved = 0

    def add_user_message(self, content):
        self.messages.append(("user", content))

    def add_assistant_message(self, content):
        self.messages.append(("assistant", content))

    def save(self):
        self.saved += 1

    def update_summary(self):
        return None


class _Memory:
    memories = []

    def process(self, user_message, reply):
        return None


class _TTS:
    def speak(self, text):
        return True

    def stop(self):
        return None


class _RecordingStateWriter:
    def __init__(self, conversation):
        self.conversation = conversation
        self.calls = []

    def observe_canonical_user_active_state(self, message, *, conversation_index, conversation_file):
        self.calls.append((message, conversation_index, conversation_file, self.conversation.saved))


class ActiveStateServiceOrderingTests(unittest.TestCase):
    def test_headwear_observer_receives_the_saved_canonical_user_turn(self):
        conversation = _Conversation()
        service = AssistantService(
            llm=object(), memory=_Memory(), conversation=conversation, voice=object(),
            character={"name": "Test"}, character_prompt="test", tts=_TTS(),
            response_generator=lambda *_: "Hello.",
            memory_authority="v1",
        )
        writer = _RecordingStateWriter(conversation)
        service._memory_v2_shadow_writer = writer
        service.process_text_turn("She is wearing a red hat.", speak=False)
        self.assertEqual(1, len(writer.calls))
        message, index, conversation_file, saves = writer.calls[0]
        self.assertEqual(("user", "She is wearing a red hat."), message)
        self.assertEqual(0, index)
        self.assertEqual("conversation.json", conversation_file)
        self.assertEqual(2, saves)


if __name__ == "__main__":
    unittest.main()
