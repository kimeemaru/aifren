import json
from pathlib import Path
import tempfile
import unittest
import uuid

from benchmarks.memory_v2.active_headwear_admission import (
    active_headwear_admission_relevant,
    evaluate_active_headwear_admission,
    load_active_headwear_admission_manifest,
)
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository


class ActiveHeadwearAdmissionExperimentTests(unittest.TestCase):
    def test_manifest_is_frozen_and_policy_requires_current_tracked_avatar_structure(self):
        cases = load_active_headwear_admission_manifest()
        self.assertEqual(22, len(cases))
        self.assertTrue(active_headwear_admission_relevant("What is she wearing on her head?"))
        self.assertTrue(active_headwear_admission_relevant("Describe her current headwear."))
        self.assertFalse(active_headwear_admission_relevant("What hat would look good on her?"))
        self.assertFalse(active_headwear_admission_relevant("What is that NPC wearing?"))
        self.assertFalse(active_headwear_admission_relevant("Help me name a spaceship."))

    def test_exact_slot_lookup_has_clean_current_recall_and_withholding(self):
        report = evaluate_active_headwear_admission()
        self.assertEqual(22, report.total_prompts)
        self.assertEqual((7, 7, 0), (
            report.current_state_relevant_prompts, report.correct_admissions,
            report.relevant_false_withholds,
        ))
        self.assertEqual((11, 11, 0), (
            report.unrelated_prompts, report.correct_withholds, report.irrelevant_admissions,
        ))
        self.assertEqual((2, 2), (report.unset_current_queries, report.correct_unset_withholds))
        self.assertEqual((0, 0, 0), (
            report.hypothetical_contamination_failures,
            report.historical_state_as_current_failures,
            report.clear_resurrection_failures,
        ))
        self.assertEqual((22, 20, 7, 7), (
            report.lookup_count, report.state_found_count, report.selection_count, report.admission_count,
        ))
        self.assertEqual((0, 0, 0), (
            report.wrong_character_violations, report.lifecycle_violations, report.provenance_violations,
        ))
        replacement = next(trace for trace in report.traces if trace.case_id == "replacement-current")
        self.assertEqual("blue hat", replacement.candidate_value)
        self.assertIsNotNone(replacement.admitted_claim_id)
        self.assertTrue(all(
            trace.admitted_claim_id is None
            for trace in report.traces
            if trace.category in {"creative", "advice", "hypothetical", "third_person", "historical", "clear_unset"}
        ))

    def test_foreign_active_state_never_enters_exact_character_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = str(uuid.uuid4())
            other = str(uuid.uuid4())
            primary_conversation = root / "primary.json"
            other_conversation = root / "other.json"
            primary_message = {"role": "user", "content": "She is wearing a red hat.", "timestamp": "2020-01-01T00:00:00Z"}
            other_message = {"role": "user", "content": "She is wearing a blue hat.", "timestamp": "2020-01-01T00:00:00Z"}
            primary_conversation.write_text(json.dumps([primary_message]), encoding="utf-8")
            other_conversation.write_text(json.dumps([other_message]), encoding="utf-8")
            writer = MemoryV2ShadowWriter(root, character_id=primary, display_name="Primary", memory_file=root / "primary-memory.json")
            other_writer = MemoryV2ShadowWriter(
                root, character_id=other, display_name="Other", memory_file=root / "other-memory.json",
                database_path=writer.database_path,
            )
            try:
                writer.observe_canonical_user_active_state(primary_message, conversation_index=0, conversation_file=primary_conversation)
                other_writer.observe_canonical_user_active_state(other_message, conversation_index=0, conversation_file=other_conversation)
                state = MemoryV2Repository(writer.store).lookup_active_state(primary, "active.avatar.headwear").state
                self.assertIsNotNone(state)
                self.assertEqual(primary, state.character_id)
                self.assertEqual("red hat", state.value)
            finally:
                other_writer.close()
                writer.close()


if __name__ == "__main__":
    unittest.main()
