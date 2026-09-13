"""Cancelled governed repairs through real turns, temporary JSON, and gated providers."""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from aifren.assistant_service import AssistantService
from aifren.conversation.conversation import Conversation
from test_assistant_service import FakePushToTalk, FakeTTS
from test_assistant_service_v2_authority import _Authority, _Memory


class FocusedPtt(FakePushToTalk):
    """Exercise the service's focused-press path without hooks or a microphone."""

    def press(self, source="frontend"):
        self.on_tts_interrupt()
        self.on_state("listening")

    def release(self, source="frontend"):
        self.on_state("ready")


class BlockingRepairProvider:
    def __init__(self):
        self.repair_entered = threading.Event()
        self.release_repair = threading.Event()
        self.cancel_seen = threading.Event()
        self.fail_repair = False
        self.calls = []
        self.draft = "I think you enjoy quiet projects. You previously described your super pampered cat."
        self.repaired = "I think you enjoy quiet projects."
        self.action = None

    def generate(self, context, prompt):
        self.calls.append("generate")
        if self.calls.count("generate") == 1:
            return self.draft
        return "Fresh synthetic reply."

    def generate_bounded(self, context, prompt, **kwargs):
        if "GOVERNED COMPANION ACTION DECISION" in prompt:
            self.calls.append("action_decision")
            return json.dumps({"dialogue": "", "response_mode": "action_decision",
                               "spoken_content": "", "companion_action": self.action})
        self.calls.append("repair")
        self.repair_entered.set()
        if not self.release_repair.wait(3):
            raise TimeoutError("Synthetic repair gate timed out")
        if self.fail_repair:
            raise RuntimeError("Synthetic repair failure")
        return self.repaired

    def cancel_active_generation(self):
        self.cancel_seen.set()


class PreparedTts(FakeTTS):
    def __init__(self):
        super().__init__()
        self.block_preparation = False
        self.preparation_entered = threading.Event()
        self.release_preparation = threading.Event()

    def prepare_stream_chunk(self, text):
        self.preparation_entered.set()
        if self.block_preparation and not self.release_preparation.wait(3):
            raise TimeoutError("Synthetic synthesis gate timed out")
        return text

    def start_prepared_chunk(self, text):
        self.speak(text)
        return True


class ObservedLock:
    """Signal an interrupt's acquire attempt before delegating to the real lock."""

    def __init__(self, lock):
        self.lock = lock
        self.interrupt_attempted = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == "interrupt-at-commit":
            self.interrupt_attempted.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class CancelledResponseRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aifren-cancelled-repair-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.root)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]
        self.provider = BlockingRepairProvider()
        self.memory = _Memory()
        self.tts = PreparedTts()
        self.path = self.root / "conversation.json"
        self.conversation = Conversation(
            self.provider, conversation_file=self.path,
            summary_file=self.root / "summary.json", memory_authority="v2",
        )
        self.conversation.add_user_message("Synthetic retained evidence.")
        self.conversation.save()
        self.original = self.path.read_bytes()
        self.service = AssistantService(
            self.provider, self.memory, self.conversation, object(),
            {"_character_id": _Authority.character_id}, "Synthetic character.", self.tts,
            character_id=_Authority.character_id, memory_authority="v2",
            memory_v2_authority=_Authority(no_evidence=False), ptt_factory=FocusedPtt,
        )
        self.service.truth_scope_provenance = lambda: {
            "kind": "real_world", "scope_id": _Authority.scope_id,
        }
        self.events = []
        self.service.subscribe(self.events.append)
        self.recorder = Mock(enabled=False)
        recorder = patch('aifren.assistant_service.development_flight_recorder', return_value=self.recorder)
        recorder.start()
        self.addCleanup(recorder.stop)
        self.workers = []
        self.releases = [self.provider.release_repair, self.tts.release_preparation]
        self.addCleanup(self.close)

    def close(self):
        for release in self.releases:
            release.set()
        for worker, _, _ in self.workers:
            worker.join(4)
            self.assertFalse(worker.is_alive(), "Synthetic worker did not retire")
        self.service.close()

    def start(self, callback, *, name="turn"):
        results, errors = [], []

        def run():
            try:
                results.append(callback())
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run, name=name, daemon=True)
        self.workers.append((worker, results, errors))
        worker.start()
        return worker, results, errors

    def finish(self, task):
        worker, results, errors = task
        worker.join(4)
        self.assertFalse(worker.is_alive(), "Synthetic worker did not retire")
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        return results[0]

    def start_repair(self, text="What do you think I like to do?", *, speak=True):
        task = self.start(lambda: self.service.process_text_turn(text, speak=speak))
        self.assertTrue(self.provider.repair_entered.wait(3), "Governed repair did not begin")
        if not self.provider.release_repair.is_set():
            self.assertFalse(any(e.type in {"assistant_delta", "assistant_response"} for e in self.events))
        return task

    def assert_terminal(self, turn_id, outcome):
        terminals = [call.kwargs for call in self.recorder.mark.call_args_list
                     if call.args == ("turn_terminal_outcome",)
                     and call.kwargs.get("turn_id") == turn_id]
        self.assertEqual(1, len(terminals))
        self.assertEqual(outcome, terminals[0]["outcome"])
        self.assertEqual(1, sum(e.type == "turn_started" and e.data.get("turn_id") == turn_id
                                for e in self.events))

    def assert_cancelled(self, result):
        self.assertEqual("interrupted", result.error)
        self.assertFalse(result.succeeded)
        self.assert_terminal(1, "cancelled")
        self.assertEqual(1, sum(e.type == "turn_cancelled" and e.data["turn_id"] == 1 for e in self.events))
        self.assertFalse(any(e.type in {"assistant_response", "conversation_message", "assistant_delta"}
                             and e.data.get("turn_id") == 1 for e in self.events))
        self.assertFalse(any(e.type == "error" for e in self.events))

    def ptt_case(self, *, fail_repair=False):
        self.provider.fail_repair = fail_repair
        task = self.start_repair()
        press = self.start(self.service.push_to_talk_press, name="ptt")
        self.finish(press)  # PTT must complete while the provider is blocked.
        self.assertTrue(self.provider.cancel_seen.is_set())
        self.provider.release_repair.set()
        result = self.finish(task)
        self.assert_cancelled(result)
        self.assertEqual(self.original, self.path.read_bytes())
        self.assertEqual(json.loads(self.original), self.conversation.messages)
        self.assertEqual([], self.tts.spoken)
        self.assertIsNone(self.service._active_turn_cancel)
        self.service.push_to_talk_release()
        following = self.service.process_text_turn("Hello again.")
        self.assertTrue(following.succeeded, following.error)
        self.assertEqual(["Fresh synthetic reply."], self.tts.spoken)
        self.assert_terminal(2, "published")

    def test_ptt_cancels_valid_repair_before_assistant_commit(self):
        self.ptt_case()

    def test_ptt_cancels_failed_repair_before_fallback_commit(self):
        self.ptt_case(fail_repair=True)

    def test_ptt_cancels_rejected_repair_before_projection_fallback(self):
        self.provider.repaired = "You previously described your cat."
        self.ptt_case()

    def replacement_case(self, *, fail_repair=False):
        self.provider.fail_repair = fail_repair
        old = self.start_repair()
        replacement = self.start(lambda: self.service.process_text_turn("Hello again."), name="replacement")
        self.assertTrue(self.provider.cancel_seen.wait(3), "Replacement did not invalidate the old turn")
        replacement_owner = self.service._active_turn_cancel
        self.provider.release_repair.set()
        self.assert_cancelled(self.finish(old))
        result = self.finish(replacement)
        self.assertTrue(result.succeeded, result.error)
        self.assertFalse(replacement_owner.is_set())
        self.assert_terminal(2, "published")
        self.assertEqual(["Fresh synthetic reply."], self.tts.spoken)
        self.assertEqual(3, len(json.loads(self.path.read_bytes())))
        cancelled_index = next(i for i, e in enumerate(self.events) if e.type == "turn_cancelled")
        replacement_index = next(i for i, e in enumerate(self.events)
                                 if e.type == "turn_started" and e.data["turn_id"] == 2)
        self.assertLess(cancelled_index, replacement_index)
        self.assertFalse(any(e.data.get("turn_id") == 1 for e in self.events[replacement_index + 1:]))

    def test_replacement_cancels_valid_repair_without_late_cleanup(self):
        self.replacement_case()

    def test_replacement_cancels_failed_repair_without_fallback_publication(self):
        self.replacement_case(fail_repair=True)

    def test_successful_repair_still_commits_and_publishes_once(self):
        self.provider.release_repair.set()
        result = self.service.process_text_turn("What do you think I like to do?")
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("I think you enjoy quiet projects.", result.reply)
        self.assertEqual(["generate", "repair"], self.provider.calls)
        self.assertEqual(3, len(json.loads(self.path.read_bytes())))
        self.assertEqual([result.reply], self.tts.spoken)
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.assert_terminal(1, "published")

    def test_cancellation_after_repair_check_still_wins_before_commit(self):
        before_commit, release_commit = threading.Event(), threading.Event()
        self.releases.append(release_commit)
        original_boundary = self.service._turn_commit_boundary

        @contextmanager
        def delayed_boundary(*args):
            # After the quick post-repair check, before the protected check.
            before_commit.set()
            if not release_commit.wait(3):
                raise TimeoutError("Synthetic pre-commit gate timed out")
            with original_boundary(*args):
                yield

        self.provider.release_repair.set()
        with patch.object(self.service, "_turn_commit_boundary", delayed_boundary):
            task = self.start_repair()
            self.assertTrue(before_commit.wait(3))
            self.finish(self.start(self.service.push_to_talk_press))
            release_commit.set()
            result = self.finish(task)
        self.assert_cancelled(result)
        self.assertEqual(self.original, self.path.read_bytes())
        self.assertEqual([], self.tts.spoken)

    def assert_commit_won(self, result):
        self.assertTrue(result.succeeded, result.error)
        self.assert_terminal(1, "published")
        self.assertEqual(1, self.provider.calls.count("generate"))
        self.assertEqual(1, self.provider.calls.count("repair"))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.assertFalse(any(e.type in {"turn_cancelled", "error"} for e in self.events))
        self.assertEqual(3, len(json.loads(self.path.read_bytes())))
        self.assertEqual(result.reply, self.conversation.messages[-1]["content"])

    def interrupt_during_commit(self, task, reached, release):
        self.assertTrue(reached.wait(3), "Protected commit boundary was not reached")
        observed = self.service._turn_state_lock
        press = self.start(self.service.push_to_talk_press, name="interrupt-at-commit")
        self.assertTrue(observed.interrupt_attempted.wait(3))
        # The observed acquire attempt removes scheduler timing from this
        # assertion. Cancellation has not won while commit owns the lock.
        self.assertFalse(self.provider.cancel_seen.is_set())
        release.set()
        result = self.finish(task)
        self.finish(press)
        self.assert_commit_won(result)

    def test_commit_wins_when_cancellation_arrives_during_final_replacement(self):
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        self.service._turn_state_lock = ObservedLock(self.service._turn_state_lock)
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.path and json.loads(Path(source).read_bytes())[-1]["role"] == "assistant":
                reached.set()
                if not release.wait(3):
                    raise TimeoutError("Synthetic replacement gate timed out")
            return original_replace(source, destination)

        self.provider.release_repair.set()
        with patch('aifren.conversation.persistence.os.replace', side_effect=replace):
            task = self.start_repair(speak=False)
            self.interrupt_during_commit(task, reached, release)

    def test_postcommit_ptt_preserves_history_and_interrupts_unlocked_synthesis(self):
        self.provider.release_repair.set()
        self.tts.block_preparation = True
        task = self.start_repair()
        self.assertTrue(self.tts.preparation_entered.wait(3))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.finish(self.start(self.service.push_to_talk_press))
        self.assertTrue(self.provider.cancel_seen.is_set())
        after_interrupt = len(self.events)
        self.tts.release_preparation.set()
        self.assert_commit_won(self.finish(task))
        self.assertEqual([], self.tts.spoken)
        self.assertFalse(any(e.type == "tts_state" and e.data.get("state") in {
            "not_started", "failed", "speaking", "playback_started"}
            for e in self.events[after_interrupt:]), "retired speech reopened no-audio presentation")

    def test_successful_repair_with_failed_persistence_retains_failure_contract(self):
        from test_conversation_persistence import ConversationPersistenceTests
        self.provider.release_repair.set()
        with patch('aifren.conversation.persistence.json.dump', side_effect=ConversationPersistenceTests.partial_write):
            result = self.service.process_text_turn("What do you think I like to do?")
        self.assertFalse(result.succeeded)
        self.assert_terminal(1, "persistence_error")
        self.assertFalse(any(e.type in {"assistant_response", "turn_cancelled"} for e in self.events))
        self.assertEqual("conversation_persistence_failed", next(e.data["code"] for e in self.events if e.type == "error"))
        self.assertEqual(self.original, self.path.read_bytes())
        self.assertEqual(json.loads(self.original), self.conversation.messages)
        self.assertEqual([], self.tts.spoken)
        self.assertTrue(self.service.process_text_turn("Hello again.").succeeded)
        self.assert_terminal(2, "published")

    def test_retired_direct_dispatch_failure_cannot_publish_a_no_audio_fallback(self):
        self.provider.release_repair.set()
        for raises in (False, True):
            with self.subTest(raises=raises):
                entered, release = threading.Event(), threading.Event()
                self.releases.append(release)

                def late_failure(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("Synthetic dispatch deadline")
                    if raises:
                        raise RuntimeError("Synthetic private failure sentinel")
                    return False

                with patch.object(self.service, "_dispatch_direct_speech_with_recovery", late_failure):
                    task = self.start(lambda: self.service.process_text_turn("Hello again."))
                    self.assertTrue(entered.wait(3))
                    self.finish(self.start(self.service.push_to_talk_press))
                    after_interrupt = len(self.events)
                    release.set()
                    self.assertTrue(self.finish(task).succeeded)
                self.assertFalse(any(e.type in {"tts_state", "error"}
                                     for e in self.events[after_interrupt:]))
                self.service.push_to_talk_release()
        self.assertTrue(self.service.process_text_turn("Hello again.").succeeded)

    def configure_v1_continuity(self):
        from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
        from aifren.memory_v2_store import MemoryV2Repository
        from test_conversation_persistence import Memory
        from test_memory_v2_embeddings import ToyEmbeddingProvider

        self.service.close()
        memory_path = self.root / "memories.json"
        memory_path.write_text("[]", encoding="utf-8")
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=_Authority.character_id, display_name="Synthetic",
            memory_file=memory_path, database_path=self.root / "synthetic.sqlite3",
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(_Authority.character_id, "Synthetic")
        self.writer.compare = lambda *_a, **_k: {}
        self.writer._embedding_provider = ToyEmbeddingProvider()
        self.memory = Memory()
        self.conversation = Conversation(
            self.provider, conversation_file=self.path, summary_file=self.root / "summary.json",
        )
        self.service = AssistantService(
            self.provider, self.memory, self.conversation, object(),
            {"_character_id": _Authority.character_id}, "Synthetic character.", self.tts,
            character_id=_Authority.character_id, memory_v2_shadow_writer=self.writer,
            ptt_factory=FocusedPtt,
            memory_authority="v1",
        )
        self.events.clear()
        self.service.subscribe(self.events.append)

    def test_cancelled_repair_preserves_user_evidence_committed_before_generation(self):
        self.configure_v1_continuity()
        self.provider.draft = "I can see your red shirt clearly."
        self.provider.repaired = "I can't see through the blindfold."
        task = self.start_repair("*I blindfold you.*")
        committed_user = self.path.read_bytes()
        self.assertEqual(["user", "user"], [m["role"] for m in json.loads(committed_user)])
        self.finish(self.start(self.service.push_to_talk_press))
        self.provider.release_repair.set()
        self.assert_cancelled(self.finish(task))
        self.assertEqual(committed_user, self.path.read_bytes())
        self.assertEqual(json.loads(committed_user), self.conversation.messages)
        self.assertFalse(self.repository.capability_effects(_Authority.character_id).vision_available)
        self.memory.process.assert_not_called()
        self.assertEqual([], self.tts.spoken)
        self.assertTrue(self.service.process_text_turn("Hello again.").succeeded)

    def test_postcommit_observer_failure_is_not_reclassified_as_cancellation(self):
        self.configure_v1_continuity()
        self.conversation.add_user_message("*I blindfold you.*")
        self.conversation.save()
        self.writer.observe_canonical_user_continuity(
            self.conversation.messages[-1], conversation_index=1,
            conversation_file=self.path,
        )
        self.provider.draft = "I can see your red shirt clearly."
        self.provider.repaired = "I can't see through the blindfold."
        self.provider.release_repair.set()
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def fail_after_commit(*_args):
            reached.set()
            if not release.wait(3):
                raise TimeoutError("Synthetic observer gate timed out")
            raise RuntimeError("Synthetic post-commit observer failure")

        self.memory.process.side_effect = fail_after_commit
        task = self.start_repair("Hello.", speak=False)
        self.assertTrue(reached.wait(3))
        committed = self.path.read_bytes()
        self.assertEqual("assistant", json.loads(committed)[-1]["role"])
        self.finish(self.start(self.service.push_to_talk_press))
        release.set()
        result = self.finish(task)
        self.assertEqual("Synthetic post-commit observer failure", result.error)
        self.assert_terminal(1, "error")
        self.assertFalse(any(e.type == "turn_cancelled" for e in self.events))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.assertEqual(committed, self.path.read_bytes())
        self.memory.process.side_effect = None
        self.assertTrue(self.service.process_text_turn("Hello again.", speak=False).succeeded)
        self.assert_terminal(2, "published")

    def configure_action(self):
        self.configure_v1_continuity()
        self.provider.action = {"family": "activity", "operation": "set", "value": "stretching"}
        self.provider.draft = "*Begins cooking.*"
        self.provider.repaired = "*Begins stretching.*"

    def test_cancelled_valid_action_repair_cannot_apply_state_or_publish_gesture(self):
        self.configure_action()
        with patch.object(self.writer, "apply_governed_companion_action", wraps=self.writer.apply_governed_companion_action) as apply:
            task = self.start_repair("Do whatever you'd like.")
            self.assertTrue(any(call.args == ("companion_action_validation",)
                                and call.kwargs.get("accepted_generated")
                                for call in self.recorder.mark.call_args_list))
            self.finish(self.start(self.service.push_to_talk_press))
            self.provider.release_repair.set()
            self.assert_cancelled(self.finish(task))
            apply.assert_not_called()
        self.assertIsNone(self.repository.lookup_actor_state(_Authority.character_id, "companion", "activity").state)
        self.assertEqual(self.original, self.path.read_bytes())
        self.assertEqual([], self.tts.spoken)
        self.memory.process.assert_not_called()

    def test_action_and_assistant_save_share_one_cancellation_boundary(self):
        self.configure_action()
        self.provider.release_repair.set()
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        self.service._turn_state_lock = ObservedLock(self.service._turn_state_lock)
        original_apply = self.writer.apply_governed_companion_action

        def apply(*args, **kwargs):
            result = original_apply(*args, **kwargs)
            reached.set()
            if not release.wait(3):
                raise TimeoutError("Synthetic action gate timed out")
            return result

        with patch.object(self.writer, "apply_governed_companion_action", side_effect=apply):
            task = self.start_repair("Do whatever you'd like.", speak=False)
            self.interrupt_during_commit(task, reached, release)
        state = self.repository.lookup_actor_state(_Authority.character_id, "companion", "activity").state
        self.assertEqual("stretching", state.value)


if __name__ == "__main__":
    unittest.main()
