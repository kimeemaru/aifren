"""Failed committed state updates cannot publish a successful acknowledgement."""
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

import test_v2_runtime_recovery as recovery


class ContinuityMutationFailureTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        case = recovery.V2RuntimeRecoveryTests()
        case.setUp()
        try:
            yield case
        finally:
            case.doCleanups()

    def test_failed_or_unavailable_committed_mutation_stops_before_inference(self):
        for state in ("failed", "unavailable", "ignored", None):
            with self.subTest(state=state), self.fixture() as case:
                events = []
                case.service.subscribe(events.append)
                text = "I put a glove on your left hand."
                self.assertTrue(case.service._response_policy(text).changed_by_current_evidence)
                case.llm.response = "All right."
                with patch.object(case.h.writer, "observe_canonical_user_continuity",
                                  return_value={"state": state, "reason": "PRIVATE raw failure",
                                                "memory_v1_allowed": True}):
                    result = case.service.process_text_turn(text)
                self.assertFalse(result.succeeded)
                self.assertEqual([], case.llm.calls)
                self.assertEqual([], case.service.tts.spoken)
                records = json.loads(case.h.conversation_file.read_text())
                self.assertEqual([("user", text)], [(row["role"], row["content"]) for row in records])
                self.assertEqual(records, case.conversation.messages)
                self.assertFalse(any(event.type in {"assistant_response", "assistant_delta"}
                                     for event in events))
                errors = [event.data for event in events if event.type == "error"]
                self.assertEqual(1, len(errors))
                self.assertEqual("continuity_update_unavailable", errors[0]["code"])
                self.assertTrue(errors[0]["recoverable"])
                self.assertFalse(errors[0]["provider_called"])
                self.assertNotIn("PRIVATE", str(errors))
                self.assertEqual(0, case.service.memory.retrieval_calls)
                self.assertEqual([], case.service.memory.processed)
                self.assertEqual(0, case.service.memory.save_calls)

    def test_real_idempotent_unchanged_observation_still_allows_response(self):
        with self.fixture() as case:
            observe = case.h.writer.observe_canonical_user_continuity
            observed = []

            def replay(message, **kwargs):
                first = observe(message, **kwargs)
                second = observe(message, **kwargs)
                observed.append((first.get("state"), second.get("state")))
                return second

            with patch.object(case.h.writer, "observe_canonical_user_continuity", side_effect=replay):
                result = case.service.process_text_turn("I put a glove on your left hand.", speak=False)
            self.assertTrue(result.succeeded, result.error)
            self.assertIn(("applied", "unchanged"), observed)
            self.assertEqual(1, len(case.llm.calls))
            self.assertEqual(["user", "assistant"], [row["role"] for row in case.conversation.messages])

    def test_cancelled_failed_observation_keeps_saved_user_and_cancel_terminal(self):
        with self.fixture() as case:
            events = []
            case.service.subscribe(events.append)

            def fail(message, **kwargs):
                if case.service._active_turn_cancel is not None:
                    case.service._active_turn_cancel.set()
                return {"state": "failed"}

            with patch.object(case.h.writer, "observe_canonical_user_continuity", side_effect=fail):
                result = case.service.process_text_turn("I put a glove on your left hand.")
            self.assertEqual("interrupted", result.error)
            self.assertEqual([], case.llm.calls)
            self.assertEqual(["user"], [row["role"] for row in json.loads(case.h.conversation_file.read_text())])
            self.assertTrue(any(event.type == "turn_cancelled" for event in events))
            self.assertFalse(any(event.type in {"assistant_response", "error"} for event in events))
