import json
from pathlib import Path
import tempfile
import unittest
import uuid

from benchmarks.memory_v2.identity_name_admission import (
    evaluate_identity_name_admission,
    identity_name_admission_relevant,
    load_admission_manifest,
)
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository


class IdentityNameAdmissionExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conversation = self.root / "conversation.json"
        self.conversation.write_text(json.dumps([
            {"role": "user", "content": "My name is Elena.", "timestamp": "2020-01-01T00:00:00Z"},
        ]), encoding="utf-8")
        self.character = str(uuid.uuid4())
        self.other_character = str(uuid.uuid4())
        self.writer = MemoryV2ShadowWriter(self.root, character_id=self.character, display_name="A", memory_file=self.root / "memories.json")
        self.created = self.writer.observe_canonical_user_message(
            {"role": "user", "content": "My name is Elena.", "timestamp": "2020-01-01T00:00:00Z"},
            conversation_index=0, conversation_file=self.conversation,
        )
        self.repository = MemoryV2Repository(self.writer.store)

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def test_manifest_is_frozen_and_policy_is_an_identity_allow_list(self):
        cases = load_admission_manifest()
        self.assertEqual(21, len(cases))
        self.assertTrue(identity_name_admission_relevant("What should you call me?"))
        self.assertFalse(identity_name_admission_relevant("Help me name a spaceship."))
        self.assertFalse(identity_name_admission_relevant("If my name were Victor, what nickname would fit?"))
        self.assertFalse(identity_name_admission_relevant("What was my sister's name?"))

    def test_real_durable_lookup_has_clean_admission_precision_and_recall(self):
        self.assertEqual("created", self.created["state"])
        report = evaluate_identity_name_admission(self.repository, self.character)

        self.assertEqual(21, report.total_prompts)
        self.assertEqual((6, 6, 0), (report.relevant_identity_prompts, report.correct_admissions, report.relevant_false_withholds))
        self.assertEqual((13, 13, 0), (report.unrelated_prompts, report.correct_withholds, report.irrelevant_admissions))
        self.assertEqual(0, report.self_reference_admissions)
        self.assertEqual((0, 0, 0), (report.hypothetical_contamination_failures, report.third_person_contamination_failures, report.wrong_character_violations))
        self.assertEqual((0, 0), (report.lifecycle_violations, report.provenance_violations))
        self.assertEqual((21, 21, 6, 6), (report.lookup_count, report.candidate_found_count, report.selection_count, report.admission_count))
        self.assertTrue(all(trace.candidate_claim_ids == (self.created["claim_id"],) for trace in report.traces))

        self.assertTrue(all(
            trace.admitted_claim_id is None
            for trace in report.traces
            if trace.category in {"creative", "advice", "hypothetical", "third_person"}
        ))

    def test_other_character_name_never_enters_primary_admission_candidates(self):
        other_conversation = self.root / "other-conversation.json"
        other_conversation.write_text(json.dumps([
            {"role": "user", "content": "My name is Blair.", "timestamp": "2020-01-01T00:00:00Z"},
        ]), encoding="utf-8")
        other_writer = MemoryV2ShadowWriter(
            self.root, character_id=self.other_character, display_name="B", memory_file=self.root / "other.json",
            database_path=self.writer.database_path,
        )
        try:
            other_writer.observe_canonical_user_message(
                {"role": "user", "content": "My name is Blair.", "timestamp": "2020-01-01T00:00:00Z"},
                conversation_index=0, conversation_file=other_conversation,
            )
            report = evaluate_identity_name_admission(self.repository, self.character)
            self.assertTrue(all(trace.candidate_claim_ids == (self.created["claim_id"],) for trace in report.traces))
            self.assertEqual(0, report.wrong_character_violations)
        finally:
            other_writer.close()


if __name__ == "__main__":
    unittest.main()
