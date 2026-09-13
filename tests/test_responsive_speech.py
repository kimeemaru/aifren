"""Committed speech through production queue/player owners; no model or device I/O."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from aifren.tts.streaming import StreamingSpeechQueue
from aifren.tts.tts import KokoroTextToSpeech
from aifren.tts import tts as audio_owner


TEXT = (
    "This opening is a complete, comfortably spoken sentence. "
    "The next sentence explains a little more about the same subject without dropping any words. "
    "A final sentence closes the thought with a clear and useful ending. "
) * 5


class SyntheticKokoro(KokoroTextToSpeech):
    def __init__(self):
        self._initialize_playback_state()
        self._initialize_continuous_state()
        self.device, self.voice, self.voice_path, self.speed = "cpu", "af_test", "not-opened", 1.0
        self.units, self.synthesis_threads = [], set()
        self.before_unit = None
        self.pipeline = self._pipeline

    def _pipeline(self, text, **_kwargs):
        index = len(self.units)
        self.units.append(text)
        self.synthesis_threads.add(threading.get_ident())
        if self.before_unit is not None:
            self.before_unit(index, text)
        tokens = []
        cursor = 0
        for word in text.split():
            cursor = text.index(word, cursor)
            tokens.append(SimpleNamespace(text=word, start_ts=cursor / 24000))
            cursor += len(word)
        yield SimpleNamespace(audio=np.array([ord(c) for c in text], dtype=np.float32), tokens=tokens)


class StreamFactory:
    """Finite fake device records every copied PCM sample and callback thread."""
    def __init__(self):
        self.streams, self.samples = [], []
        self.callback_threads = set()
        self.pump_gate = threading.Event()
        self.pump_gate.set()
        self.on_start = None
        self.fail_start = False

    def __call__(self, **kwargs):
        owner = self

        class Stream:
            def __init__(self):
                self.active = False
                self.worker = None
                self.callbacks = 0
                self.callback = kwargs["callback"]

            def once(self):
                owner.callback_threads.add(threading.get_ident())
                data = np.zeros((64, 1), dtype=np.float32)
                self.callbacks += 1
                try:
                    self.callback(data, 64, None, None)
                except audio_owner.sd.CallbackStop:
                    self.active = False
                finally:
                    owner.samples.extend(data.reshape(-1).tolist())

            def start(self):
                if owner.fail_start:
                    raise RuntimeError("Synthetic device start failure")
                self.active = True
                if owner.on_start is not None:
                    owner.on_start(self)
                if not self.active:
                    return

                def pump():
                    while self.active:
                        if owner.pump_gate.wait(.01):
                            self.once()
                        time.sleep(.001)
                self.worker = threading.Thread(target=pump, name="synthetic-portaudio", daemon=True)
                self.worker.start()

            def abort(self): self.active = False
            def stop(self): self.active = False
            def close(self):
                self.active = False
                if self.worker is not None:
                    self.worker.join(1)

        stream = Stream()
        self.streams.append(stream)
        return stream


class ResponsiveSpeechTests(unittest.TestCase):
    def setUp(self):
        self.provider = SyntheticKokoro()
        self.device = StreamFactory()
        self.patch = patch.object(audio_owner.sd, "OutputStream", self.device)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.queues, self.gates = [], []
        self.addCleanup(self.cleanup_audio)
        self.starts, self.finishes, self.failures = [], [], []
        self.provider.set_playback_started_callback(
            lambda duration, envelope, words, pid, **kwargs:
                self.starts.append((pid, kwargs.get("chunk_metadata"), words)))
        self.provider.set_playback_finished_callback(self.finishes.append)

    def cleanup_audio(self):
        for gate in self.gates: gate.set()
        self.device.pump_gate.set()
        for queue in self.queues: queue.cancel()
        self.provider.stop()
        for queue in self.queues:
            queue.join(3)
            for worker in (queue._synthesis_thread, queue._playback_thread, queue._feeder_thread):
                if worker is not None:
                    self.assertFalse(worker.is_alive(), "Owned speech worker leaked")

    def queue(self, text=TEXT, **kwargs):
        queue = StreamingSpeechQueue(self.provider, committed_text=text,
                                     on_failure=self.failures.append, **kwargs)
        self.queues.append(queue)
        return queue

    def gate(self):
        event = threading.Event()
        self.gates.append(event)
        return event

    def wait(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.005)
        self.assertTrue(predicate(), "Synthetic condition timed out")

    def test_units_preserve_exact_text_and_whitespace_word_boundaries(self):
        for text in (TEXT, "Hello.", "  Fine.  ", "Word " + "x" * 400 + " end.",
                     "Keep *literal* operator text.\n\nThen preserve these\twords too. " * 8):
            with self.subTest(text_length=len(text)):
                units = StreamingSpeechQueue.committed_units(text)
                self.assertEqual(text, "".join(units))
                self.assertEqual(text.split(), [word for unit in units for word in unit.split()])
                self.assertTrue(all(unit.strip() for unit in units))
        self.assertEqual((), StreamingSpeechQueue.committed_units("  "))

    def test_short_opening_does_not_queue_a_large_second_synthesis_unit(self):
        opening = "Start with two or three herbs that you actually enjoy using. "
        second = "Basil, mint, and chives are manageable choices, although mint is best kept in its own pot. "
        later = "Choose containers with drainage holes and set them where the plants receive suitable light. "
        text = opening + second + later * 4
        units = StreamingSpeechQueue.committed_units(text)
        # This exact shape exposed a CPU gap: the old second unit combined
        # two sentences (30 words) whose synthesis outlasted the opening.
        self.assertEqual(opening.strip(), units[0].strip())
        self.assertEqual(second.strip(), units[1].strip())
        self.assertEqual(text, ''.join(units))

    def test_first_unit_plays_while_remaining_synthesis_is_still_blocked(self):
        entered, release = self.gate(), self.gate()
        def before(index, _text):
            if index == 1:
                entered.set()
                if not release.wait(3): raise TimeoutError("Synthetic gate")
        self.provider.before_unit = before
        queue = self.queue()
        self.assertTrue(entered.wait(2))
        self.wait(lambda: bool(self.starts))
        self.assertFalse(queue._closed.is_set() and not queue._synthesis_thread.is_alive())
        self.assertEqual(1, len(self.starts))
        self.assertEqual([], self.finishes)
        release.set()
        queue.join(3)
        self.assertEqual("completed", queue.outcome)

    def test_one_utterance_exact_pcm_and_global_word_sample_offsets(self):
        queue = self.queue()
        queue.join(3)
        self.assertEqual("completed", queue.outcome)
        self.assertEqual([], self.failures)
        self.assertEqual(TEXT, "".join(chr(int(v)) for v in self.device.samples if v))
        self.assertEqual(1, len(self.device.streams))
        self.assertEqual(1, len(self.provider.synthesis_threads))
        self.assertEqual(1, len(set(pid for pid, _, _ in self.starts)))
        self.assertEqual([queue.playback_id], self.finishes)
        word_offset, sample_offset = 0, 0
        for index, (pid, metadata, words) in enumerate(self.starts):
            self.assertEqual(index, metadata["sequence"])
            self.assertEqual(word_offset, metadata["word_offset"])
            self.assertEqual(sample_offset, metadata["sample_offset"])
            self.assertEqual(TEXT, metadata["complete_text"])
            self.assertEqual(len(metadata["chunk_text"].split()), metadata["word_count"])
            self.assertEqual("provider_token_count", metadata["alignment_kind"])
            self.assertEqual(metadata["word_count"], len(words))
            self.assertEqual(index == len(self.starts) - 1, metadata["final_chunk"])
            self.assertGreaterEqual(metadata["playback_sample_offset"], sample_offset)
            word_offset += metadata["word_count"]
            sample_offset += metadata["sample_count"]
        self.assertEqual(len(TEXT.split()), word_offset)
        self.assertEqual(len(TEXT), sample_offset)

    def test_committed_feeder_is_bounded_and_never_coalesces_a_large_tail(self):
        self.device.pump_gate.clear()
        text = TEXT * 10
        queue = self.queue(text, max_chunks=2)
        self.wait(lambda: queue._prepared.qsize() == 2)
        time.sleep(.03)
        self.assertIsNone(queue._overflow_item)
        self.assertLessEqual(queue.pending_jobs, 4)
        self.assertLess(len(self.provider.units), len(queue.committed_units(text)))
        self.assertFalse(queue.submit("Cannot append another reply."))
        queue.cancel()
        queue.join(2)
        self.assertEqual(0, queue.pending_jobs)

    def test_manual_close_does_not_truncate_a_committed_feeder(self):
        queue = self.queue()
        queue.close()
        queue.join(3)
        self.assertEqual(TEXT, "".join(chr(int(v)) for v in self.device.samples if v))
        self.assertEqual("completed", queue.outcome)

    def test_cancelled_first_synthesis_does_not_begin_audio(self):
        entered, release = self.gate(), self.gate()
        def before(index, _text):
            if index == 0:
                entered.set()
                if not release.wait(3): raise TimeoutError("Synthetic gate")
        self.provider.before_unit = before
        queue = self.queue()
        self.assertTrue(entered.wait(2))
        queue.cancel(); release.set(); queue.join(2)
        self.assertEqual("cancelled", queue.outcome)
        self.assertEqual([], self.device.streams)
        self.assertEqual([], self.starts)

    def test_cancellation_inside_begin_preparation_cannot_resurrect_audio(self):
        entered, release = self.gate(), self.gate()
        original = self.provider._continuous_chunk
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3): raise TimeoutError("Synthetic gate")
            return original(*args, **kwargs)
        with patch.object(self.provider, "_continuous_chunk", side_effect=blocked):
            queue = self.queue("A safe opening sentence.")
            self.assertTrue(entered.wait(2))
            queue.cancel(); release.set(); queue.join(2)
        self.assertEqual([], self.device.streams)
        self.assertEqual([], self.starts)
        self.assertEqual("cancelled", queue.outcome)

    def test_cancelled_transition_is_not_announced_after_stream_start_returns(self):
        def start(stream):
            stream.once()
            self.provider.stop()
        self.device.on_start = start
        queue = self.queue("One short sentence.")
        queue.join(2)
        self.assertEqual([], self.starts)
        self.assertEqual([], self.finishes)
        self.assertEqual("cancelled", queue.outcome)
        self.assertNotEqual("completed", self.provider.continuous_stream_outcome(queue.playback_id))

    def test_old_append_finish_and_abort_cannot_touch_replacement_session(self):
        self.device.pump_gate.clear()
        prepared = (np.ones((120, 1), dtype=np.float32), 24000, [0.0])
        first = self.provider.begin_prepared_stream(prepared)
        self.provider.stop()
        replacement = self.provider.begin_prepared_stream(prepared)
        self.assertNotEqual(first, replacement)
        self.assertFalse(self.provider.append_prepared_stream(prepared, stream_id=first))
        self.assertFalse(self.provider.finish_prepared_stream(stream_id=first))
        self.assertFalse(self.provider.abort_prepared_stream(first))
        self.assertEqual(replacement, self.provider._continuous_generation)
        self.assertFalse(self.provider.stop_event.is_set())
        self.provider.finish_prepared_stream(stream_id=replacement)
        self.device.pump_gate.set()
        self.assertTrue(self.provider.playback_finished.wait(2))

    def test_failed_middle_unit_has_no_normal_finish_or_later_audio(self):
        def before(index, _text):
            if index == 1:
                self.wait(lambda: bool(self.starts))
                raise ValueError("Synthetic permanent unit failure")
        self.provider.before_unit = before
        queue = self.queue()
        queue.join(3)
        self.assertEqual("failed", queue.outcome)
        self.assertEqual(["tts_synthesis_sequence_failed"], self.failures)
        self.assertEqual([], self.finishes)
        self.assertEqual(1, len(self.starts))
        self.assertEqual(2, len(self.provider.units))

    def test_device_failure_is_failed_not_natural_completion(self):
        self.device.fail_start = True
        queue = self.queue("One complete sentence.")
        queue.join(2)
        self.assertEqual("failed", queue.outcome)
        self.assertEqual([], self.finishes)
        self.assertEqual([], self.starts)
        self.assertEqual(["tts_playback_failed"], self.failures)

    def test_owned_failure_retains_exact_queue_after_service_cleanup(self):
        self._assert_owned_failure_after_cleanup(None)

    def test_owned_failure_never_borrows_replacement_queue_identity(self):
        self._assert_owned_failure_after_cleanup(object())

    def _assert_owned_failure_after_cleanup(self, replacement):
        first_unit, cleaned = self.gate(), self.gate()
        service = {"queue": None}
        failures, completed = [], []

        def before(index, _text):
            if index == 0 and not first_unit.wait(3):
                raise TimeoutError("Synthetic owner setup")
            if index == 1:
                self.wait(lambda: bool(self.starts))
                raise ValueError("Synthetic permanent unit failure")

        def complete(queue):
            completed.append(queue)
            if service["queue"] is queue:
                service["queue"] = replacement
            cleaned.set()

        class CleanupFirstQueue(StreamingSpeechQueue):
            def _report_failure(self, reason):
                if not cleaned.wait(3):
                    raise TimeoutError("Synthetic cleanup boundary")
                super()._report_failure(reason)

        self.provider.before_unit = before
        queue = CleanupFirstQueue(self.provider, committed_text=TEXT,
            on_failure=self.failures.append,
            on_owned_failure=lambda owner, reason: failures.append(
                (owner, reason, owner.playback_id, service["queue"])),
            on_complete=complete)
        self.queues.append(queue)
        service["queue"] = queue
        first_unit.set()
        queue.join(3)

        self.assertEqual("failed", queue.outcome)
        self.assertEqual([queue], completed)
        self.assertEqual(1, len(failures))
        owner, reason, playback_id, global_queue = failures[0]
        self.assertIs(queue, owner)
        self.assertEqual(TEXT, owner._committed_text)
        self.assertEqual("tts_synthesis_sequence_failed", reason)
        self.assertEqual(self.starts[0][0], playback_id)
        self.assertIs(replacement, global_queue)
        self.assertEqual([], self.failures, "Owned and legacy callbacks must not dispatch twice")
        self.assertEqual([], self.finishes)

    def test_starvation_has_real_offsets_and_no_callback_logging(self):
        entered, release = self.gate(), self.gate()
        marks = []
        def before(index, _text):
            if index == 1:
                entered.set()
                if not release.wait(3): raise TimeoutError("Synthetic gate")
        self.provider.before_unit = before
        recorder = SimpleNamespace(mark=lambda event, **data:
                                   marks.append((threading.get_ident(), event, data)))
        with patch.object(audio_owner, 'development_flight_recorder', return_value=recorder):
            queue = self.queue()
            self.assertTrue(entered.wait(2))
            self.wait(lambda: self.device.streams and self.device.streams[0].callbacks > 10)
            release.set(); queue.join(3)
        self.assertEqual("completed", queue.outcome)
        metadata = self.starts[1][1]
        self.assertGreater(metadata["playback_sample_offset"], metadata["sample_offset"])
        self.assertTrue(any(event == "tts_pcm_starvation" for _, event, _ in marks))
        self.assertFalse(any(thread in self.device.callback_threads for thread, _, _ in marks))

    def test_legacy_four_argument_callback_remains_compatible(self):
        calls = []
        self.provider.set_playback_started_callback(lambda seconds, envelope, words, pid: calls.append(pid))
        queue = self.queue("A small complete answer.")
        queue.join(2)
        self.assertEqual([queue.playback_id], calls)
        self.assertEqual("completed", queue.outcome)


if __name__ == "__main__":
    unittest.main()
