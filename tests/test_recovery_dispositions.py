"""Source-bound recovery dispositions, not replay of obsolete state."""
import json
import unittest
from unittest.mock import patch
import test_v2_runtime_recovery as recovery_tests


class RecoveryDispositionTests(unittest.TestCase):
    setUp = recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service = recovery_tests.V2RuntimeRecoveryTests.open_service
    reopen = recovery_tests.V2RuntimeRecoveryTests.reopen
    progress = recovery_tests.V2RuntimeRecoveryTests.progress

    def miss_then_replace(self, text, replacement="I'm reading Dune.", *, expect_failure=True):
        original = self.h.writer.observe_canonical_user_continuity
        def missed(message, **kwargs):
            if message["content"] == text:
                raise RuntimeError("Synthetic missed observation")
            return original(message, **kwargs)
        with patch.object(self.h.writer, "observe_canonical_user_continuity", side_effect=missed):
            calls = len(self.llm.calls)
            result = self.service.process_text_turn(text, speak=False)
            if expect_failure:
                self.assertFalse(result.succeeded)
                self.assertIn("current-state update could not be completed", result.error)
                self.assertEqual(calls, len(self.llm.calls))
                self.assertEqual("user", self.conversation.messages[-1]["role"])
                self.assertEqual(text, self.conversation.messages[-1]["content"])
                self.assertEqual(self.conversation.messages, json.loads(self.h.conversation_file.read_text()))
            else:
                self.assertTrue(result.succeeded, result.error)
            self.assertTrue(self.service.process_text_turn(replacement, speak=False).succeeded)
        self.reopen()

    def dispositions(self):
        return [dict(row) for row in self.h.writer.store.connection.execute(
            "SELECT * FROM canonical_observation_dispositions WHERE character_id=?", (self.h.character_id,))]

    def test_exact_later_actor_activity_supersedes_missed_activity_idempotently(self):
        self.miss_then_replace("I'm cooking dinner.")
        self.assertEqual("complete", self.progress()["continuity"]["state"])
        self.assertIn("reading Dune", str(self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity").state))
        rows = self.dispositions()
        self.assertEqual("superseded_actor_activity", rows[0]["reason"])
        self.assertTrue(rows[0]["witness_event_id"])
        originals = self.h.conversation_file.read_bytes()
        self.reopen()
        self.assertEqual(rows, self.dispositions())
        self.assertEqual(originals, self.h.conversation_file.read_bytes())

    def test_missed_read_only_question_does_not_need_current_state_replay(self):
        self.miss_then_replace("Did you enjoy the picnic?", expect_failure=False)
        self.assertEqual("complete", self.progress()["continuity"]["state"])
        self.assertEqual("non_mutating_question", self.dispositions()[0]["reason"])

    def test_independent_missing_cause_stays_unresolved_without_idle_spin(self):
        self.miss_then_replace("I cover your eyes with my hands.")
        self.assertEqual("unresolved", self.progress()["continuity"]["state"])
        self.assertEqual([], self.dispositions())
        recovery = self.service._canonical_observation_recovery
        with patch('aifren.continuity.memory_v2_runtime_observation.LoadedCanonicalArchive.load',
                   side_effect=AssertionError("unchanged unresolved archive reread")):
            for _ in range(4):
                self.service.maintain_canonical_observers()
                self.assertEqual(0, recovery.last_status["canonical_reads"])
        self.assertTrue(self.service.process_text_turn("My favorite color is blue.", speak=False).succeeded)
        self.assertEqual("complete", self.progress()["durable"]["state"])
        self.assertEqual("recovery_unresolved", recovery.inspection_status())

    def test_different_actor_and_compound_effect_cannot_be_disposed_by_timestamp(self):
        self.miss_then_replace("I'm cooking dinner and I cover your eyes with my hands.")
        self.assertEqual("unresolved", self.progress()["continuity"]["state"])
        self.assertEqual([], self.dispositions())

    def test_later_companion_clothing_cannot_dispose_user_activity(self):
        self.miss_then_replace("I'm cooking dinner.", "She is wearing a red hat.")
        self.assertEqual("unresolved", self.progress()["continuity"]["state"])
        self.assertEqual([], self.dispositions())

    def test_disposition_failure_rolls_back_with_its_cursor_then_recovers(self):
        from aifren.continuity.memory_v2_runtime_observation import CanonicalObservationRecovery
        original = CanonicalObservationRecovery._record_disposition
        def crash(owner, *args, **kwargs):
            original(owner, *args, **kwargs)
            raise RuntimeError("Synthetic crash before consumer commit")
        with patch.object(CanonicalObservationRecovery, "_record_disposition", crash):
            self.assertTrue(self.service.process_text_turn("Did you enjoy the picnic?", speak=False).succeeded)
        self.assertEqual([], self.dispositions())
        self.assertEqual("failed", self.progress()["continuity"]["state"])
        originals = self.h.conversation_file.read_bytes()
        self.reopen()
        self.assertEqual("complete", self.progress()["continuity"]["state"])
        rows = self.dispositions()
        self.assertEqual(1, len(rows))
        self.h.writer.store.rebuild_fts()
        self.reopen()
        self.assertEqual(rows, self.dispositions())
        self.assertEqual(originals, self.h.conversation_file.read_bytes())

    def test_committed_entry_with_missed_cursor_does_not_reenter_after_explicit_exit(self):
        from aifren.continuity.memory_v2_runtime_observation import CanonicalObservationRecovery
        with patch.object(CanonicalObservationRecovery, "run_page", return_value={}):
            self.assertTrue(self.service.process_text_turn(
                "Let's roleplay that we're in the orchard.", speak=False).succeeded)
            entered = self.h.repository.active_truth_scope(self.h.character_id)
            self.assertEqual("scenario", entered.kind)
            self.assertTrue(self.service.process_text_turn("Back to real life.", speak=False).succeeded)
        original = self.h.conversation_file.read_bytes()
        store = self.h.writer.store
        scope_rows = [tuple(r) for r in store.connection.execute(
            "SELECT * FROM truth_scope_events WHERE character_id=?", (self.h.character_id,))]
        self.reopen()
        self.assertEqual("complete", self.progress()["continuity"]["state"])
        self.assertEqual("real_world", self.h.repository.active_truth_scope(self.h.character_id).kind)
        self.assertEqual(scope_rows, [tuple(r) for r in self.h.writer.store.connection.execute(
            "SELECT * FROM truth_scope_events WHERE character_id=?", (self.h.character_id,))])
        self.assertEqual(original, self.h.conversation_file.read_bytes())

    def test_missing_entry_cannot_be_invented_from_later_different_scope_controls(self):
        entry = "Let's roleplay that we're in the apple orchard."
        original = self.h.writer.observe_canonical_user_continuity
        def missed(message, **kwargs):
            if message["content"] == entry:
                raise RuntimeError("Synthetic missed scope application")
            return original(message, **kwargs)
        with patch.object(self.h.writer, "observe_canonical_user_continuity", side_effect=missed):
            calls = len(self.llm.calls)
            result = self.service.process_text_turn(entry, speak=False)
            self.assertFalse(result.succeeded)
            self.assertIn("current-state update could not be completed", result.error)
            self.assertEqual(calls, len(self.llm.calls))
            self.assertEqual("user", self.conversation.messages[-1]["role"])
            self.assertEqual(entry, self.conversation.messages[-1]["content"])
            self.assertEqual(self.conversation.messages, json.loads(self.h.conversation_file.read_text()))
            self.assertTrue(self.service.process_text_turn(
                "Let's roleplay that we're in the pear orchard.", speak=False).succeeded)
            self.assertTrue(self.service.process_text_turn("Back to real life.", speak=False).succeeded)
        self.reopen()
        self.assertEqual("unresolved", self.progress()["continuity"]["state"])
        self.assertEqual([], self.dispositions())
        self.assertNotIn("apple orchard", [s.label for s in self.h.repository.list_truth_scopes(self.h.character_id)])
        original = self.h.conversation_file.read_bytes()
        self.reopen()
        self.assertEqual(original, self.h.conversation_file.read_bytes())
        with patch('aifren.continuity.memory_v2_runtime_observation.LoadedCanonicalArchive.load',
                   side_effect=AssertionError("unchanged unresolved source reread")):
            for _ in range(5): self.service.maintain_canonical_observers()
        self.assertTrue(self.service.process_text_turn("My favorite color is blue.", speak=False).succeeded)
        self.llm.response = "Your favorite color is blue."
        result = self.service.process_text_turn("What is my favorite color?", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("Your favorite color is blue.", result.reply)
        self.assertEqual("complete", self.progress()["durable"]["state"])
        self.assertEqual("complete", self.progress()["history"]["state"])
        self.assertEqual("recovery_unresolved", self.service._canonical_observation_recovery.inspection_status())
