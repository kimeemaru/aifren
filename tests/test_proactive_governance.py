"""Real proactive turns with synthetic evidence and bounded provider/audio gates."""

from datetime import timedelta
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from aifren.assistant_service import AssistantService
from aifren.conversation.conversation import Conversation
from test_assistant_service import FakeTTS
from test_cancelled_response_repair import FocusedPtt
from test_continuity_companion_tranche import _Harness
from test_conversation_persistence import Memory
from test_memory_v2_embeddings import ToyEmbeddingProvider


class GatedProvider:
    def __init__(self):
        self.draft = "Did your GPU arrive?"
        self.repair = "Did your GPU arrive?"
        self.calls = []
        self.block_generation = False
        self.block_repair = False
        self.generation_entered = threading.Event()
        self.repair_entered = threading.Event()
        self.release_generation = threading.Event()
        self.release_repair = threading.Event()
        self.cancel_seen = threading.Event()
        self.fail_repair = False

    def generate(self, context, prompt):
        self.calls.append(("generate", context, prompt))
        self.generation_entered.set()
        if self.block_generation and not self.release_generation.wait(5):
            raise TimeoutError("Synthetic generation gate timed out")
        return self.draft

    def generate_bounded(self, context, prompt, **kwargs):
        self.calls.append(("repair", context, prompt))
        self.repair_entered.set()
        if self.block_repair and not self.release_repair.wait(5):
            raise TimeoutError("Synthetic repair gate timed out")
        if self.fail_repair:
            raise RuntimeError("Synthetic repair failure")
        return self.repair

    def cancel_active_generation(self):
        self.cancel_seen.set()


class GatedAudio(FakeTTS):
    def __init__(self):
        super().__init__()
        self.block_synthesis = False
        self.synthesis_entered = threading.Event()
        self.release_synthesis = threading.Event()
        self.prepared = []
        self.playback_id = 0
        self.fail_synthesis = False

    def prepare_stream_chunk(self, text):
        self.prepared.append(text)
        self.synthesis_entered.set()
        if self.fail_synthesis:
            raise RuntimeError("Synthetic synthesis failure")
        if self.block_synthesis and not self.release_synthesis.wait(5):
            raise TimeoutError("Synthetic synthesis gate timed out")
        return text

    def start_prepared_chunk(self, text):
        self.spoken.append(text)
        self.playback_id += 1
        self.playback_started_callback(1.0, [], [], self.playback_id)
        return True

    def speak(self, text):
        # Mirrors the production synchronous prepare/start shape. The repaired
        # service must use the exposed preparation boundary instead.
        return self.start_prepared_chunk(self.prepare_stream_chunk(text))


class _ProactiveFixture(unittest.TestCase):
    memory_authority = "v1"

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]
        self.h = _Harness()
        self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.h.root)
        self.h.writer.compare = lambda *_a, **_k: {}
        self.h.writer._embedding_provider = ToyEmbeddingProvider()
        self.h.turn("I'm waiting for my GPU to arrive.")
        self.now = self.h.base + timedelta(hours=8)
        self.now_us = int(self.now.timestamp() * 1_000_000)
        self.provider = GatedProvider()
        self.tts = GatedAudio()
        self.memory = Memory()
        self.conversation = Conversation(
            self.provider, conversation_file=self.h.conversation_file,
            summary_file=self.h.root / "summary.json", clock=lambda: self.now,
            memory_authority=self.memory_authority,
        )
        authority = None
        if self.memory_authority == "v2":
            from aifren.continuity.memory_v2_authority import DevelopmentV2MemoryAuthority
            from test_assistant_service_v2_authority import _Memory
            self.memory = _Memory()
            authority = DevelopmentV2MemoryAuthority(
                self.h.writer.store, self.h.character_id, self.conversation.messages,
                embedding_provider=ToyEmbeddingProvider(),
            )
        self.service = AssistantService(
            self.provider, self.memory, self.conversation, object(),
            {"_character_id": self.h.character_id}, "Synthetic character.", self.tts,
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            ptt_factory=FocusedPtt, memory_authority=self.memory_authority,
            memory_v2_authority=authority,
        )
        self.events = []
        self.service.subscribe(self.events.append)
        self.recorder = Mock(enabled=False)
        for target, value in (
            ("aifren.runtime.model_settings.proactive_behavior_status", {"enabled": True, "interval_seconds": 30}),
            ("aifren.assistant_service.development_flight_recorder", self.recorder),
        ):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.workers = []
        self.releases = [self.provider.release_generation, self.provider.release_repair,
                         self.tts.release_synthesis]
        self.addCleanup(self.close)

    def close(self):
        for release in self.releases:
            release.set()
        for worker, _, _, _ in self.workers:
            worker.join(4)
            self.assertFalse(worker.is_alive(), "Synthetic worker did not retire")
        self.service.close()

    def start(self, callback, *, name="proactive"):
        results, errors, done = [], [], threading.Event()

        def run():
            try:
                results.append(callback())
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=run, name=name, daemon=True)
        task = (worker, results, errors, done)
        self.workers.append(task)
        worker.start()
        return task

    def finish(self, task):
        worker, results, errors, done = task
        self.assertTrue(done.wait(3), "Synthetic worker did not complete")
        worker.join(1)
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        return results[0]

    def proactive(self, *, speak=True):
        return self.service.process_proactive_checkin(now_us=self.now_us, speak=speak)

    def evidence(self, text):
        self.h.turn(text)
        self.conversation.reload()

    def assert_published(self, result):
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(result.reply, json.loads(self.h.conversation_file.read_bytes())[-1]["content"])
        starts = [e for e in self.events if e.type == "turn_started"]
        self.assertEqual(1, len(starts))
        turn_id = starts[0].data["turn_id"]
        terminals = [call.kwargs for call in self.recorder.mark.call_args_list
                     if call.args == ("turn_terminal_outcome",) and call.kwargs.get("turn_id") == turn_id]
        self.assertEqual(["published"], [row["outcome"] for row in terminals])
        self.assertEqual(1, sum(e.type == "assistant_response" for e in self.events))
        self.assertFalse(any(e.type == "turn_cancelled" for e in self.events))

    def assert_private_failure(self, result, *, error="interrupted", archive=None):
        self.assertEqual(error, result.error)
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type in {
            "turn_started", "turn_cancelled", "assistant_delta", "assistant_response", "conversation_message",
        } for e in self.events))
        self.assertFalse(any(e.type == "status" and e.data.get("state") == "thinking" for e in self.events))
        if archive is not None:
            self.assertEqual(archive, self.h.conversation_file.read_bytes())
        self.assertEqual([], self.tts.spoken)
        self.assertFalse(self.service._turn_lock.locked())
        self.assertIsNone(self.service._active_turn_cancel)
        self.assertEqual(0, self.h.writer.store.connection.execute("SELECT COUNT(*) FROM proactive_checkins").fetchone()[0])
        self.assertEqual(1, self.h.writer.store.connection.execute("SELECT COUNT(*) FROM proactive_attempts").fetchone()[0])

    def unavailable_speech(self):
        from aifren.memory_v2_store import SceneRelationProposal
        text = "A sealed gag covers the companion's mouth."
        store, cid = self.h.writer.store, self.h.character_id
        sequence = store.connection.execute("SELECT MAX(sequence)+1 FROM events").fetchone()[0]
        store.add_event(
            cid, "synthetic-speech-evidence", sequence, event_type="canonical_user_message",
            actor_kind="user", recorded_at_us=self.now_us - 1_000_000,
            content_text=text, source_origin="synthetic_test",
        )
        store.apply_scene_relation_proposal(
            cid, SceneRelationProposal(
                "set", "companion", "mouth", "obstructed_by", "state", "sealed gag", None,
                0, len(text), semantic_family="speech_obstruction",
            ), evidence_event_id="synthetic-speech-evidence",
        )
        self.assertEqual("unavailable", self.h.repository.capability_effects(cid).speech_mode)


class ProactiveGovernanceTests(_ProactiveFixture):
    def test_published_proactive_face_is_available_to_next_ordinary_request(self):
        self.provider.draft = ('{"dialogue":"Did your GPU arrive?",'
                               '"presentation":{"emotion":"happy","intensity":0.4}}')
        result = self.proactive(speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual("happy", result.presentation.emotion)
        self.provider.draft = "I am listening."
        self.assertTrue(self.service.process_text_turn("Hello there.", speak=False).succeeded)
        self.assertIn("Last published model-metadata facial request: happy (intensity 0.40)", self.provider.calls[-1][2])

    def test_eligible_proactive_reply_still_succeeds(self):
        result = self.proactive()
        self.assert_published(result)
        self.assertEqual([result.reply], self.tts.spoken)

    def test_vision_restriction_repairs_without_memory_requirement(self):
        self.evidence("*I blindfold you.*")
        self.assertFalse(self.h.repository.capability_effects(self.h.character_id).vision_available)
        self.provider.draft = "I can see your red shirt clearly. Did your GPU arrive?"
        result = self.proactive()
        self.assert_published(result)
        self.assertEqual(self.provider.repair, result.reply)
        self.assertEqual([self.provider.repair], self.tts.spoken)
        self.assertEqual(["generate", "repair"], [call[0] for call in self.provider.calls])
        self.assertFalse(any(self.provider.draft in str(e.data) for e in self.events))
        presentation = next(e.data for e in self.events if e.type == "assistant_response")
        self.assertTrue(presentation["has_presentation"])
        self.assertEqual("suppressed", presentation["presentation"]["gaze_mode"])
        self.assertIn("Authoritative capability envelope", str(self.provider.calls[0][1]))
        self.assertIsNone(self.service._response_policy("").memory_answer_requirement)

    def test_speech_unavailable_still_allows_nonspoken_proactive_presentation(self):
        self.unavailable_speech()
        self.provider.draft = '*Tilts their head, ears perked with quiet curiosity.*'
        result = self.proactive()
        self.assert_published(result)
        self.assertEqual(self.provider.draft, result.reply)
        self.assertEqual("", result.spoken_text)
        self.assertEqual([], self.tts.prepared)
        self.assertEqual("nonverbal", result.presentation.speech_mode)
        self.assertEqual(["generate"], [call[0] for call in self.provider.calls])
        self.assertTrue(any(e.type == "tts_state" and e.data.get("state") == "not_started" for e in self.events))

    def test_speech_unavailable_repairs_fluent_draft_to_permitted_reaction(self):
        self.unavailable_speech()
        self.provider.repair = "*Their ears perk with quiet curiosity.*"
        result = self.proactive()
        self.assert_published(result)
        self.assertEqual(self.provider.repair, result.reply)
        self.assertEqual("", result.spoken_text)
        self.assertEqual([], self.tts.prepared)
        self.assertEqual(["generate", "repair"], [call[0] for call in self.provider.calls])

    def test_invalid_draft_and_repair_are_private_and_throttled(self):
        self.evidence("*I blindfold you.*")
        before = self.h.conversation_file.read_bytes()
        self.provider.draft = self.provider.repair = "I can see your red shirt clearly."
        result = self.proactive()
        self.assert_private_failure(result, error="unsafe_response", archive=before)
        calls = len(self.provider.calls)
        self.assertEqual("backed_off_failed_generation", self.proactive().error)
        self.assertEqual(calls, len(self.provider.calls))

    def cancelled_provider_case(self, *, repair=False, fail_repair=False):
        if repair:
            self.evidence("*I blindfold you.*")
            self.provider.draft = "I can see your red shirt clearly."
            self.provider.block_repair = True
            self.provider.fail_repair = fail_repair
            reached, release = self.provider.repair_entered, self.provider.release_repair
        else:
            self.provider.block_generation = True
            reached, release = self.provider.generation_entered, self.provider.release_generation
        before = self.h.conversation_file.read_bytes()
        task = self.start(self.proactive)
        self.assertTrue(reached.wait(3))
        self.assertFalse(any(e.type == "turn_started" for e in self.events))
        self.finish(self.start(self.service.push_to_talk_press, name="ptt"))
        self.assertTrue(self.provider.cancel_seen.is_set())
        self.assertFalse(release.is_set())
        release.set()
        self.assert_private_failure(self.finish(task), archive=before)
        self.assertEqual("interrupted", self.h.writer.store.connection.execute(
            "SELECT outcome FROM proactive_attempts",
        ).fetchone()[0])
        self.service.push_to_talk_release()
        self.provider.draft = "Fresh synthetic reply."
        self.assertTrue(self.service.process_text_turn("Hello again.").succeeded)

    def test_ptt_cancels_private_proactive_generation(self):
        self.cancelled_provider_case()

    def test_ptt_cancels_valid_proactive_repair(self):
        self.cancelled_provider_case(repair=True)

    def test_ptt_cancels_failed_proactive_repair(self):
        self.cancelled_provider_case(repair=True, fail_repair=True)

    def test_replacement_during_repair_owns_the_next_turn_and_playback(self):
        self.evidence("*I blindfold you.*")
        self.provider.draft = "I can see your red shirt clearly."
        self.provider.block_repair = True
        before = json.loads(self.h.conversation_file.read_bytes())
        old = self.start(self.proactive)
        self.assertTrue(self.provider.repair_entered.wait(3))
        self.provider.draft = "Fresh synthetic reply."
        replacement = self.start(lambda: self.service.process_text_turn("Hello again."), name="replacement")
        self.assertTrue(self.provider.cancel_seen.wait(3))
        owner = self.service._active_turn_cancel
        self.provider.release_repair.set()
        self.assertEqual("interrupted", self.finish(old).error)
        self.assert_published(self.finish(replacement))
        self.assertFalse(owner.is_set())
        self.assertEqual([self.provider.draft], self.tts.spoken)
        self.assertEqual(len(before) + 2, len(self.conversation.messages))
        self.assertEqual("user", next(e.data["generation_origin"] for e in self.events if e.type == "turn_started"))
        self.assertFalse(any(e.data.get("generation_origin") == "proactive" for e in self.events))

    def test_changed_capabilities_discard_an_old_generation_snapshot(self):
        self.provider.draft = "I can see your red shirt clearly."
        self.provider.block_generation = True
        task = self.start(self.proactive)
        self.assertTrue(self.provider.generation_entered.wait(3))
        self.evidence("*I blindfold you.*")
        revised = self.h.conversation_file.read_bytes()
        self.provider.release_generation.set()
        self.assert_private_failure(self.finish(task), archive=revised)

    def test_changed_capabilities_discard_an_old_repair_snapshot(self):
        self.evidence("*I blindfold you.*")
        self.provider.draft = "I can see your red shirt clearly."
        self.provider.block_repair = True
        task = self.start(self.proactive)
        self.assertTrue(self.provider.repair_entered.wait(3))
        self.unavailable_speech()
        before = self.h.conversation_file.read_bytes()
        self.provider.release_repair.set()
        self.assert_private_failure(self.finish(task), archive=before)

    def test_scope_change_during_generation_discards_the_old_reason(self):
        self.provider.block_generation = True
        task = self.start(self.proactive)
        self.assertTrue(self.provider.generation_entered.wait(3))
        self.evidence("Let's roleplay that we're in a synthetic test scene.")
        revised = self.h.conversation_file.read_bytes()
        self.provider.release_generation.set()
        self.assert_private_failure(self.finish(task), archive=revised)

    def test_resolved_open_thread_during_generation_is_not_published(self):
        self.provider.block_generation = True
        task = self.start(self.proactive)
        self.assertTrue(self.provider.generation_entered.wait(3))
        self.evidence("My GPU arrived.")
        self.assertEqual((), self.h.repository.list_open_threads(self.h.character_id).threads)
        revised = self.h.conversation_file.read_bytes()
        self.provider.release_generation.set()
        self.assert_private_failure(self.finish(task), archive=revised)

    def test_cancellation_wins_after_validation_before_proactive_commit(self):
        from contextlib import contextmanager
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.service._turn_commit_boundary

        @contextmanager
        def delayed(*args):
            reached.set()
            if not release.wait(3):
                raise TimeoutError("Synthetic commit gate timed out")
            with original(*args):
                yield

        before = self.h.conversation_file.read_bytes()
        with patch.object(self.service, "_turn_commit_boundary", delayed):
            task = self.start(self.proactive)
            self.assertTrue(reached.wait(3))
            self.finish(self.start(self.service.push_to_talk_press))
            release.set()
            self.assert_private_failure(self.finish(task), archive=before)

    def test_commit_wins_before_ptt_and_keeps_one_published_outcome(self):
        from test_cancelled_response_repair import ObservedLock
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        lock = ObservedLock(self.service._turn_state_lock)
        self.service._turn_state_lock = lock
        original = os.replace

        def replace(source, destination):
            if Path(destination) == self.h.conversation_file:
                reached.set()
                if not release.wait(3):
                    raise TimeoutError("Synthetic replacement gate timed out")
            return original(source, destination)

        with patch('aifren.conversation.persistence.os.replace', side_effect=replace):
            task = self.start(lambda: self.proactive(speak=False))
            self.assertTrue(reached.wait(3))
            press = self.start(self.service.push_to_talk_press, name="interrupt-at-commit")
            self.assertTrue(lock.interrupt_attempted.wait(3))
            self.assertFalse(self.provider.cancel_seen.is_set())
            release.set()
            self.assert_published(self.finish(task))
            self.finish(press)
        self.assertEqual(["generate"], [call[0] for call in self.provider.calls])

    def test_postreplace_persistence_failure_retains_saved_proactive_message(self):
        before_count = len(self.conversation.messages)
        with patch('aifren.conversation.persistence._sync_directory', side_effect=OSError("synthetic failure")):
            result = self.proactive()
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type in {"turn_started", "assistant_response", "turn_cancelled"} for e in self.events))
        failure = next(e.data for e in self.events if e.type == "error")
        self.assertEqual("conversation_persistence_failed", failure["code"])
        self.assertTrue(failure["replacement_committed"])
        self.assertTrue(failure["assistant_persisted"])
        self.conversation.reload()
        self.assertEqual(before_count + 1, len(self.conversation.messages))
        self.assertEqual(self.provider.draft, self.conversation.messages[-1]["content"])
        self.assertEqual([], self.tts.spoken)
        self.assertEqual(["generate"], [call[0] for call in self.provider.calls])

    def test_synthesis_failure_has_no_audio_terminal_and_preserves_commit(self):
        self.tts.fail_synthesis = True
        self.assert_published(self.proactive())
        self.assertEqual([], self.tts.spoken)
        self.assertTrue(any(e.type == "tts_state" and e.data.get("state") == "failed" for e in self.events))

    def test_synchronous_speak_only_extension_does_not_block_proactive_ptt(self):
        with patch.object(self.tts, "prepare_stream_chunk", None), \
                patch.object(self.tts, "start_prepared_chunk", None), \
                patch.object(self.tts, "speak", side_effect=AssertionError("Unsafe synchronous dispatch")) as speak:
            self.assert_published(self.proactive())
            speak.assert_not_called()
        self.assertTrue(any(e.type == "tts_state" and e.data.get("state") == "failed" for e in self.events))

    def test_ptt_completes_while_proactive_synthesis_is_still_blocked(self):
        self.tts.block_synthesis = True
        proactive = self.start(self.proactive)
        self.assertTrue(self.tts.synthesis_entered.wait(3))
        committed = self.h.conversation_file.read_bytes()
        press = self.start(self.service.push_to_talk_press, name="ptt")
        self.assertTrue(press[3].wait(1), "PTT waited for synchronous synthesis")
        self.finish(press)
        self.assertFalse(self.tts.release_synthesis.is_set())
        self.assertTrue(any(e.type == "voice_state" and e.data.get("state") == "listening" for e in self.events))
        self.tts.release_synthesis.set()
        self.assert_published(self.finish(proactive))
        self.assertEqual(committed, self.h.conversation_file.read_bytes())
        self.assertEqual([], self.tts.spoken)
        self.assertFalse(any(e.type == "tts_state" and e.data.get("state") == "playback_started" for e in self.events))
        self.service.push_to_talk_release()
        self.provider.draft = "Fresh synthetic reply."
        self.assertTrue(self.service.process_text_turn("Hello again.").succeeded)
        self.assertEqual([self.provider.draft], self.tts.spoken)

    def test_replacement_during_blocked_synthesis_does_not_lose_playback_ownership(self):
        self.tts.block_synthesis = True
        task = self.start(self.proactive)
        self.assertTrue(self.tts.synthesis_entered.wait(3))
        proactive_reply = self.provider.draft
        self.provider.draft = "Fresh synthetic reply."
        replacement = self.start(lambda: self.service.process_text_turn("Hello again."), name="replacement")
        self.assertTrue(self.provider.cancel_seen.wait(3))
        self.tts.release_synthesis.set()
        self.assertTrue(self.finish(task).succeeded)
        self.assertTrue(self.finish(replacement).succeeded)
        self.assertEqual([self.provider.draft], self.tts.spoken)
        self.assertEqual(self.tts.playback_id, self.service._active_tts_playback_id)
        self.assertTrue(any(m["content"] == proactive_reply for m in self.conversation.messages))
        replacement_start = next(i for i, e in enumerate(self.events)
                                 if e.type == "turn_started" and e.data.get("generation_origin") == "user")
        self.assertFalse(any(e.data.get("generation_origin") == "proactive"
                             for e in self.events[replacement_start + 1:]))

    def test_ptt_after_commit_prevents_late_synthesis_start(self):
        from contextlib import contextmanager
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.service._turn_commit_boundary

        @contextmanager
        def after_commit(*args):
            with original(*args):
                yield
            reached.set()  # Commit guard is already released.
            if not release.wait(3):
                raise TimeoutError("Synthetic post-commit gate timed out")

        with patch.object(self.service, "_turn_commit_boundary", after_commit):
            task = self.start(self.proactive)
            self.assertTrue(reached.wait(3))
            self.finish(self.start(self.service.push_to_talk_press))
            event_count = len(self.events)
            release.set()
            self.assert_published(self.finish(task))
        self.assertEqual([], self.tts.prepared)
        self.assertFalse(any(e.type == "status" and e.data.get("state") == "speaking"
                             for e in self.events[event_count:]))

    def test_ptt_after_dispatch_prevents_late_speaking_events(self):
        reached, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.service._dispatch_direct_speech_with_recovery

        def after_dispatch(*args, **kwargs):
            result = original(*args, **kwargs)
            reached.set()  # Dispatch's speech lock is already released.
            if not release.wait(3):
                raise TimeoutError("Synthetic post-dispatch gate timed out")
            return result

        with patch.object(self.service, "_dispatch_direct_speech_with_recovery", after_dispatch):
            task = self.start(self.proactive)
            self.assertTrue(reached.wait(3))
            self.finish(self.start(self.service.push_to_talk_press))
            event_count = len(self.events)
            release.set()
            self.assert_published(self.finish(task))
        self.assertFalse(any(e.type == "tts_state" and e.data.get("state") in {"playback_started", "speaking", "not_started", "failed"}
                             for e in self.events[event_count:]))


class ProactiveV2GovernanceTests(_ProactiveFixture):
    memory_authority = "v2"

    def test_v2_repair_must_satisfy_capability_and_memory_governance(self):
        self.evidence("*I blindfold you.*")
        self.provider.draft = "I can see your red shirt clearly. You previously told me about your pampered cat."
        self.assert_published(result := self.proactive())
        self.assertEqual(self.provider.repair, result.reply)
        self.assertEqual([self.provider.repair], self.tts.spoken)
        self.assertEqual(["generate", "repair"], [call[0] for call in self.provider.calls])
        self.assertFalse(any(self.provider.draft in str(e.data) for e in self.events))
        self.assertEqual(0, self.memory.retrieval_calls)
        self.assertEqual([], self.memory.processed)

    def test_v2_source_projection_cannot_bypass_capability_governance(self):
        self.evidence("*I blindfold you.*")
        before = self.h.conversation_file.read_bytes()
        self.provider.draft = "I can see your red shirt clearly. You previously told me about your pampered cat."
        self.provider.repair = "You previously told me about your pampered cat."
        self.assert_private_failure(self.proactive(), error="unsafe_response", archive=before)
        self.assertEqual(0, self.memory.retrieval_calls)
        self.assertEqual([], self.memory.processed)

    def test_v2_projection_preserves_safe_checkin_after_failed_repair(self):
        self.evidence("*I blindfold you.*")
        self.provider.draft = "You previously told me about your pampered cat. Did your GPU arrive?"
        self.provider.fail_repair = True
        self.assert_published(result := self.proactive())
        self.assertEqual("Did your GPU arrive?", result.reply)
        self.assertEqual([result.reply], self.tts.spoken)
        self.assertFalse(any("pampered cat" in str(e.data) for e in self.events))


if __name__ == "__main__":
    unittest.main()
