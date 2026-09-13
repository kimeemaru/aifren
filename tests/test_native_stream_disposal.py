"""One existing playback worker owns each native stream's teardown."""
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from tts import tts as audio_owner
from test_responsive_speech import StreamFactory, SyntheticKokoro


class NativeStreamDisposalTests(unittest.TestCase):
    @staticmethod
    def wait_for(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.001)
        raise AssertionError("Synthetic stream did not reach its expected boundary")

    def exercise_cancel(self, continuous):
        provider, device = SyntheticKokoro(), StreamFactory()
        entered, release = threading.Event(), threading.Event()
        cancelled_returned = threading.Event()
        starts, finishes, calls, overlaps = [], [], [], []
        active_operations = []
        guard = threading.Lock()
        original_factory = device.__call__

        def factory(**kwargs):
            stream = original_factory(**kwargs)
            first = not calls and len(device.streams) == 1
            if first:
                for stage in ("abort", "stop", "close"):
                    operation = getattr(stream, stage)

                    def native_operation(_stage=stage, _operation=operation):
                        with guard:
                            if active_operations:
                                overlaps.append((_stage, tuple(active_operations)))
                            active_operations.append(_stage)
                            calls.append((_stage, threading.get_ident()))
                        try:
                            if _stage == "abort":
                                entered.set()
                                release.wait(3)
                            _operation()
                        finally:
                            with guard:
                                active_operations.remove(_stage)

                    setattr(stream, stage, native_operation)
            return stream

        provider.set_playback_started_callback(lambda *_args, **_kwargs: starts.append(1))
        provider.set_playback_finished_callback(finishes.append)
        pcm = (np.ones((240000, 1), dtype=np.float32), 24000, [])
        with patch.object(audio_owner.sd, "OutputStream", factory):
            if continuous:
                generation = provider.begin_prepared_stream(pcm)
            else:
                provider.start_prepared_chunk(pcm)
                generation = provider.playback_generation
            self.wait_for(lambda: provider.stream is not None and provider.stream.active and starts)
            old_stream, old_worker = provider.stream, provider.playback_thread

            def cancel():
                provider.stop_if_generation(generation)
                cancelled_returned.set()

            stopper = threading.Thread(target=cancel, name="synthetic-stop-caller", daemon=True)
            stopper.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(cancelled_returned.wait(.25), "Stop must not wait for native teardown")
                self.assertFalse(provider._is_current_playback_generation(generation))
                # Even while the native call is blocked, the retired callback
                # cannot copy any further speech into the device buffer.
                previous_samples = len(device.samples)
                old_stream.once()
                self.assertTrue(all(value == 0 for value in device.samples[previous_samples:]))
                replacement = provider.begin_prepared_stream(pcm)
                self.wait_for(lambda: provider.stream is not None and provider.stream is not old_stream)
                replacement_stream = provider.stream
                self.assertNotEqual(generation, replacement)
                self.assertTrue(replacement_stream.active)
            finally:
                release.set()
                stopper.join(2)
                old_worker.join(2)
                provider.stop()
                worker = provider.playback_thread
                if worker is not None:
                    worker.join(2)
            self.assertFalse(stopper.is_alive())
            self.assertFalse(old_worker.is_alive())
            self.assertEqual([], overlaps)
            self.assertEqual(["abort", "close"], [stage for stage, _ in calls])
            self.assertEqual({old_worker.ident}, {identity for _, identity in calls})
            self.assertEqual([], finishes, "Cancellation must not announce natural completion")

    def test_continuous_cancel_has_one_native_owner_and_does_not_wait(self):
        self.exercise_cancel(True)

    def test_full_prepared_cancel_has_one_native_owner_and_does_not_wait(self):
        self.exercise_cancel(False)

    def test_native_close_failure_cannot_announce_successful_completion(self):
        provider, device = SyntheticKokoro(), StreamFactory()
        marks, finishes = [], []
        provider.set_playback_finished_callback(finishes.append)
        original_factory = device.__call__

        def factory(**kwargs):
            stream = original_factory(**kwargs)
            close = stream.close

            def failed_close():
                close()
                raise RuntimeError("Synthetic native close failure")

            stream.close = failed_close
            return stream

        recorder = SimpleNamespace(mark=lambda kind, **data: marks.append((kind, data)))
        with patch.object(audio_owner.sd, "OutputStream", factory), \
                patch.object(audio_owner, "development_flight_recorder", return_value=recorder):
            generation = provider.begin_prepared_stream((np.ones((120, 1)), 24000, []))
            provider.finish_prepared_stream(stream_id=generation)
            self.assertTrue(provider.playback_finished.wait(2))
            self.assertEqual("failed", provider.continuous_stream_outcome(generation))
            self.assertEqual([], finishes)
            errors = [data for kind, data in marks if kind == "playback_stream_teardown_error"]
            self.assertEqual(1, len(errors))
            self.assertEqual("close", errors[0]["stage"])
            self.assertEqual("RuntimeError", errors[0]["exception_class"])
            self.assertNotIn("Synthetic native", str(errors))
            provider.stop()
