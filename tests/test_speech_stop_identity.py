"""Stop owns only the playback generation observed when service detaches it."""
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from aifren.assistant_service import AssistantService
from aifren.tts import tts as audio_owner
from aifren.tts.tts import KokoroTextToSpeech
from test_responsive_speech import StreamFactory, SyntheticKokoro


class SpeechStopIdentityTests(unittest.TestCase):
    def setUp(self):
        # Production playback methods, with no model construction or real device.
        self.provider = KokoroTextToSpeech.__new__(KokoroTextToSpeech)
        self.provider._initialize_playback_state()
        self.provider._initialize_continuous_state()
        self.device = StreamFactory()
        self.device.pump_gate.clear()
        device_patch = patch.object(audio_owner.sd, "OutputStream", self.device)
        device_patch.start()
        self.addCleanup(device_patch.stop)
        self.addCleanup(self.provider.stop)
        self.service = AssistantService.__new__(AssistantService)
        self.service.tts = self.provider
        self.service._retire_automatic_expression = Mock()
        self.service._speech_generation_lock = threading.RLock()
        self.service._speech_generation = 0
        self.service._tts_state_lock = threading.Lock()
        self.service._streaming_speech_queue = None
        self.service._pending_stream_speech = None
        self.service._active_stream_playback = None
        self.service._active_tts_playback_id = 0
        self.events = []
        self.service._emit = lambda kind, **data: self.events.append((kind, data))
        self.prepared = (np.ones((240, 1), dtype=np.float32), 24000, [])

    @staticmethod
    def wait_for(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.001)
        raise AssertionError("Synthetic playback did not reach its boundary")

    def start_replacement(self, continuous):
        prior_stream = self.provider.stream
        with self.service._speech_generation_lock:
            # The replacement turn has its own speech generation, as normal
            # process_text_turn does before preparing its accepted response.
            self.service._speech_generation += 1
            if continuous:
                generation = self.provider.begin_prepared_stream(self.prepared)
                self.service._streaming_speech_queue = SimpleNamespace(
                    owns_continuous_playback=True, playback_id=generation, cancel=Mock())
            else:
                self.provider.start_prepared_chunk(self.prepared)
                generation = self.provider.playback_generation
            self.service._active_tts_playback_id = generation
        self.wait_for(lambda: self.provider.stream is not None
                      and self.provider.stream is not prior_stream and self.provider.stream.active)
        return generation

    def assert_old_stop_preserves_replacement(self, continuous, retired_service_identity=False):
        entered, release = threading.Event(), threading.Event()
        errors = []
        if retired_service_identity:
            # Natural queue cleanup can precede delivery of the service's
            # completion callback: queue absent, prior presentation ID retained.
            self.service._active_tts_playback_id = 7
            self.service._active_stream_playback = {
                "turn_id": 1, "chunk_index": 0, "committed_stream": True}

        def stop():
            try:
                self.service.stop_speaking(interrupted=True)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=stop, name="old-idle-stop")
        original_snapshot = self.provider.playback_debug_state

        def pause_after_detachment():
            if threading.current_thread() is worker:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("Stop test barrier timed out")
            return original_snapshot()

        with patch.object(self.provider, "playback_debug_state", pause_after_detachment):
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                generation = self.start_replacement(continuous)
                stream = self.provider.stream
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(generation, self.provider.playback_generation)
        self.assertTrue(stream.active)
        self.assertFalse(self.provider.stop_event.is_set())
        self.assertEqual(generation, self.service._active_tts_playback_id)
        # An identity-free late stopped event would cancel newer subtitles too.
        self.assertEqual([], self.events)

    def test_idle_stop_does_not_stop_new_continuous_utterance(self):
        self.assert_old_stop_preserves_replacement(True)

    def test_idle_stop_does_not_stop_new_full_prepared_utterance(self):
        self.assert_old_stop_preserves_replacement(False)

    def test_retired_queue_stop_does_not_stop_new_utterance(self):
        self.assert_old_stop_preserves_replacement(True, retired_service_identity=True)

    def test_old_continuous_queue_cannot_stop_new_full_prepared_utterance(self):
        prior_generation = self.start_replacement(True)
        self.service._streaming_speech_queue.cancel.side_effect = (
            lambda: self.provider.abort_prepared_stream(prior_generation))
        self.assert_old_stop_preserves_replacement(False)

    def test_stop_still_stops_current_full_prepared_playback(self):
        generation = self.start_replacement(False)
        stream = self.provider.stream
        self.service.stop_speaking(interrupted=True)
        self.assertTrue(self.provider.stop_event.is_set())
        self.assertGreater(self.provider.playback_generation, generation)
        self.assertEqual(0, self.service._active_tts_playback_id)
        self.assertEqual(generation, self.events[-1][1]["playback_id"])
        # Native cleanup belongs to the playback worker; logical cancellation
        # and its downstream event never wait for a driver's abort/close call.
        self.wait_for(lambda: not stream.active)

    def test_replacement_during_old_stream_abort_keeps_new_state_and_completion(self):
        previous_generation = self.start_replacement(False)
        old_stream = self.provider.stream
        abort_old = old_stream.abort
        replacement = []

        def replace_before_abort_returns():
            old_stream.abort = abort_old
            replacement.append(self.start_replacement(False))
            abort_old()

        old_stream.abort = replace_before_abort_returns
        self.service.stop_speaking(interrupted=True)
        self.wait_for(lambda: bool(replacement) and not old_stream.active)
        generation = replacement[0]
        self.assertFalse(old_stream.active)
        self.assertTrue(self.provider.stream.active)
        self.assertEqual(generation, self.provider.playback_generation)
        self.assertEqual(generation, self.provider.active_playback_generation)
        self.assertEqual(generation, self.service._active_tts_playback_id)
        self.assertFalse(self.provider.stop_event.is_set())
        self.assertFalse(self.provider.playback_finished.is_set())
        self.assertEqual(1, len(self.events))
        self.assertEqual(previous_generation, self.events[0][1]["playback_id"])

    def test_stop_invalidates_pending_synthesis_without_an_active_queue(self):
        generation = self.provider._next_playback_generation()
        self.provider._mark_synthesis_active(generation)
        self.service.stop_speaking(interrupted=True)
        self.assertIsNone(self.provider.active_synthesis_generation)
        self.assertFalse(self.provider._is_current_playback_generation(generation))
        self.assertTrue(self.provider.stop_event.is_set())
        self.assertTrue(self.provider.playback_finished.is_set())

    def test_inflight_full_preparation_cannot_publish_after_stop(self):
        provider = SyntheticKokoro()
        self.service.tts = provider
        self.service.responsive_speech = False
        self.service.provider_request_active = lambda: False
        self.addCleanup(provider.stop)
        entered, release = threading.Event(), threading.Event()
        result = []

        def native_unit(_index, _text):
            entered.set()
            if not release.wait(2):
                raise AssertionError("Synthetic preparation barrier timed out")

        provider.before_unit = native_unit
        worker = threading.Thread(target=lambda: result.append(
            self.service._dispatch_direct_speech_with_recovery(
                "The pending reply stays unplayed after interruption.",
                cancel_event=threading.Event(), speech_generation=0)),
            name="pending-full-preparation")
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.service.stop_speaking(interrupted=True)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([None], result)
        self.assertEqual([], self.device.streams)

    def test_provider_without_owned_stop_api_keeps_legacy_stop(self):
        provider = SimpleNamespace(stop=Mock(return_value=8))
        self.service.tts = provider
        self.service._active_tts_playback_id = 8
        self.service.stop_speaking(interrupted=True)
        provider.stop.assert_called_once_with()
        self.assertEqual(8, self.events[-1][1]["playback_id"])


if __name__ == "__main__":
    unittest.main()
