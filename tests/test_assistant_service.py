import unittest

from assistant_service import AssistantService


class FakeConversation:
    def __init__(self):
        self.messages = []
        self.saved = 0
        self.summaries_updated = 0

    def add_user_message(self, content):
        self.messages.append(("user", content))

    def add_assistant_message(self, content):
        self.messages.append(("assistant", content))

    def save(self):
        self.saved += 1

    def update_summary(self):
        self.summaries_updated += 1


class FakeMemory:
    def __init__(self):
        self.memories = [{"id": 1}]
        self.processed = []
        self.saved = 0

    def process(self, user_message, reply):
        self.processed.append((user_message, reply))

    def save(self):
        self.saved += 1


class FakeTTS:
    def __init__(self):
        self.spoken = []
        self.stopped = 0
        self.volume = None
        self.playback_started_callback = None
        self.playback_finished_callback = None

    def set_playback_started_callback(self, callback):
        self.playback_started_callback = callback

    def set_playback_finished_callback(self, callback):
        self.playback_finished_callback = callback

    def speak(self, text):
        self.spoken.append(text)
        if self.playback_started_callback is not None:
            self.playback_started_callback(2.5)

    def stop(self):
        self.stopped += 1

    def set_volume(self, volume):
        self.volume = volume


class FailingTTS(FakeTTS):
    def speak(self, text):
        raise RuntimeError("speaker unavailable")


class FakePushToTalk:
    def __init__(
        self,
        voice,
        tts,
        on_transcription,
        on_state=None,
        on_tts_interrupt=None,
        on_error=None,
    ):
        self.voice = voice
        self.tts = tts
        self.on_transcription = on_transcription
        self.on_state = on_state
        self.on_tts_interrupt = on_tts_interrupt
        self.on_error = on_error
        self.stopped = 0
        self.binding = None
        self.global_enabled = False

    def stop(self):
        self.stopped += 1

    def set_binding(self, binding):
        self.binding = binding

    def enable_global_listener(self):
        self.global_enabled = True

    def global_listener_active(self):
        return self.global_enabled


def fake_response_generator(llm, conversation, memory, message, character_prompt):
    return "*AIFren waves.*\n\nHello!"


class AssistantServiceTests(unittest.TestCase):
    def setUp(self):
        self.conversation = FakeConversation()
        self.memory = FakeMemory()
        self.tts = FakeTTS()
        self.service = AssistantService(
            llm=object(),
            memory=self.memory,
            conversation=self.conversation,
            voice=object(),
            character={"name": "AIFren"},
            character_prompt="character prompt",
            tts=self.tts,
            response_generator=fake_response_generator,
            ptt_factory=FakePushToTalk,
        )

    def test_text_turn_preserves_turn_lifecycle_and_emote_filtering(self):
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(result.reply, "*AIFren waves.*\n\nHello!")
        self.assertEqual(result.spoken_text, "Hello!")
        self.assertEqual(self.tts.spoken, ["Hello!"])
        self.assertEqual(
            self.conversation.messages,
            [("user", "Hello"), ("assistant", result.reply)],
        )
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        self.assertEqual(self.conversation.saved, 1)
        self.assertEqual(self.conversation.summaries_updated, 1)
        self.assertEqual(
            [event.type for event in events],
            [
                "turn_started",
                "status",
                "conversation_message",
                "assistant_response",
                "status",
                "tts_state",
                "tts_state",
                "tts_state",
                "conversation_message",
                "memory_updated",
                "status",
            ],
        )
        playback = next(event for event in events if event.data.get("state") == "playback_started")
        self.assertEqual(playback.data["duration_seconds"], 2.5)

    def test_playback_alignment_and_completion_are_forwarded_without_changing_turn_data(self):
        events = []
        self.service.subscribe(events.append)

        self.tts.playback_started_callback(1.5, [0.1], [0.0, 0.7], 42)
        self.tts.playback_finished_callback(42)

        started = events[0]
        self.assertEqual("playback_started", started.data["state"])
        self.assertEqual([0.0, 0.7], started.data["word_start_seconds"])
        self.assertEqual(42, started.data["playback_id"])
        self.assertEqual("stopped", events[1].data["state"])
        self.assertEqual(42, events[1].data["playback_id"])

    def test_stale_natural_completion_cannot_clear_newer_playback_state(self):
        events = []
        self.service.subscribe(events.append)

        self.tts.playback_started_callback(1.0, [], [], 10)
        self.tts.playback_started_callback(1.0, [], [], 11)
        self.tts.playback_finished_callback(10)

        self.assertEqual(11, self.service._active_tts_playback_id)
        self.assertEqual([], [event for event in events if event.data.get("state") == "stopped"])

        self.tts.playback_finished_callback(11)
        self.assertEqual(0, self.service._active_tts_playback_id)
        self.assertEqual(11, events[-1].data["playback_id"])

    def test_explicit_stop_clears_service_playback_state_before_tts_cleanup(self):
        self.tts.playback_started_callback(1.0, [], [], 23)

        self.service.stop_speaking()

        self.assertEqual(0, self.service._active_tts_playback_id)
        self.assertEqual(1, self.tts.stopped)

    def test_turn_can_skip_speech_for_a_frontend_that_owns_playback(self):
        result = self.service.process_text_turn("Hello", speak=False)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.tts.spoken, [])
        self.assertEqual(result.spoken_text, "Hello!")

    def test_controls_and_save_use_existing_dependencies(self):
        self.service.set_tts_volume(0.4)
        self.service.stop_speaking()
        self.service.save()

        self.assertEqual(self.tts.volume, 0.4)
        self.assertEqual(self.tts.stopped, 1)
        self.assertEqual(self.conversation.saved, 1)
        self.assertEqual(self.memory.saved, 1)

    def test_tts_failure_keeps_the_turn_persistence_lifecycle(self):
        self.service.tts = FailingTTS()
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            self.conversation.messages[-1],
            ("assistant", result.reply),
        )
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        self.assertEqual(self.conversation.saved, 1)
        self.assertEqual(self.conversation.summaries_updated, 1)
        self.assertIn(
            ("error", "tts"),
            [
                (event.type, event.data.get("source"))
                for event in events
            ],
        )

    def test_ptt_state_and_tts_interruption_route_through_service_events(self):
        events = []
        self.service.subscribe(events.append)

        ptt = self.service.start_push_to_talk()
        ptt.on_state("listening")
        ptt.on_tts_interrupt()

        self.assertIs(self.service.start_push_to_talk(), ptt)
        self.assertEqual(self.tts.stopped, 1)
        self.assertIn(
            ("voice_state", "listening"),
            [
                (event.type, event.data.get("state"))
                for event in events
            ],
        )
        self.assertIn(
            ("tts_state", "stopped"),
            [
                (event.type, event.data.get("state"))
                for event in events
            ],
        )

    def test_ptt_transcription_uses_the_common_text_turn_path(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        result = ptt.on_transcription("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            self.conversation.messages,
            [("user", "Hello"), ("assistant", result.reply)],
        )
        voice_states = [event.data.get("state") for event in events if event.type == "voice_state"]
        self.assertEqual(voice_states[-1], "ready")

    def test_ptt_review_mode_emits_text_without_persisting_or_starting_a_turn(self):
        events = []
        self.service.subscribe(events.append)
        self.service.set_ptt_auto_submit_transcriptions(False)
        ptt = self.service.start_push_to_talk()

        result = ptt.on_transcription("Please let me review this")

        self.assertIsNone(result)
        self.assertEqual(self.conversation.messages, [])
        self.assertEqual(self.memory.processed, [])
        self.assertIn(
            ("voice_transcription", "Please let me review this"),
            [(event.type, event.data.get("content")) for event in events],
        )

    def test_empty_or_overlapping_ptt_transcription_does_not_start_another_turn(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        self.assertIsNone(ptt.on_transcription("   "))
        self.assertEqual(self.conversation.messages, [])

        self.service._turn_lock.acquire()
        try:
            result = ptt.on_transcription("Hello")
        finally:
            self.service._turn_lock.release()

        self.assertFalse(result.succeeded)
        self.assertEqual(self.conversation.messages, [])
        self.assertIn(
            "voice_transcription",
            [event.type for event in events],
        )

    def test_ptt_errors_are_reported_as_backend_errors(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        ptt.on_error("microphone unavailable")

        self.assertIn(
            ("error", "voice"),
            [
                (event.type, event.data.get("source"))
                for event in events
            ],
        )

    def test_ptt_binding_uses_the_single_global_listener(self):
        events = []
        self.service.subscribe(events.append)

        self.service.set_push_to_talk_binding("Mouse4")
        ptt = self.service.start_push_to_talk()

        self.assertEqual(ptt.binding, "Mouse4")
        self.assertTrue(ptt.global_enabled)
        self.assertIn(
            ("voice_state", "Mouse4"),
            [(event.type, event.data.get("binding")) for event in events],
        )

    def test_ptt_binding_does_not_regress_to_f8_after_reuse(self):
        self.service.set_push_to_talk_binding("Mouse4")
        ptt = self.service.start_push_to_talk()
        self.service.start_push_to_talk()
        self.assertEqual(ptt.binding, "Mouse4")

    def test_ptt_binding_reports_when_the_global_listener_is_unavailable(self):
        class UnavailablePushToTalk(FakePushToTalk):
            def global_listener_active(self):
                return False

        service = AssistantService(
            llm=object(), memory=self.memory, conversation=self.conversation, voice=object(),
            character={"name": "AIFren"}, character_prompt="character prompt", tts=self.tts,
            response_generator=fake_response_generator, ptt_factory=UnavailablePushToTalk,
        )
        events = []
        service.subscribe(events.append)
        service.set_push_to_talk_binding("Mouse4")
        event = [item for item in events if item.type == "voice_state"][-1]
        self.assertFalse(event.data["global_listener"])

    def test_ptt_state_events_preserve_global_listener_availability(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()
        ptt.global_enabled = True

        self.service._handle_ptt_state("listening")

        event = [item for item in events if item.type == "voice_state"][-1]
        self.assertEqual("listening", event.data["state"])
        self.assertTrue(event.data["global_listener"])


if __name__ == "__main__":
    unittest.main()
