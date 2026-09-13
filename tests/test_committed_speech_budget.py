"""Committed speech work bounds and cooperative replacement; no model/device I/O."""

import threading
import unittest
from unittest.mock import patch

from aifren.tts import tts as audio_owner
from aifren.tts.streaming import (
    CommittedSpeechUnitPolicy, StreamingSpeechQueue, TtsSynthesisResourceManager,
)
from test_responsive_speech import SyntheticKokoro


LONG_TEXT = (
    "Start with two or three herbs that you actually enjoy using. "
    "Basil, mint, and chives are manageable choices, although mint is best kept in its own pot. "
    "Choose containers with drainage holes and set them where the plants receive suitable light. "
    "Check the soil before watering instead of following a rigid calendar. "
    "If the surface is dry, water slowly until a little runs out below, then empty the saucer. "
    "Harvest small amounts regularly, leaving enough healthy growth for the plant to recover. "
    "Watch for yellow leaves, crowded roots, and pests, but change one thing at a time so you can tell what helped. "
    "A simple note about watering and growth can make patterns easier to notice. "
    "The goal is steady care, not a complicated routine."
)


class CommittedSpeechBudgetTests(unittest.TestCase):
    def test_later_units_do_not_grow_into_long_native_work_after_short_opening(self):
        units = StreamingSpeechQueue.committed_units(LONG_TEXT)
        self.assertEqual([11, 16], [len(unit.split()) for unit in units[:2]])
        self.assertTrue(all(len(unit) <= 120 for unit in units[2:]))
        self.assertEqual(LONG_TEXT, "".join(units))
        self.assertEqual(LONG_TEXT.split(), [word for unit in units for word in unit.split()])

    def test_previous_policy_is_reproducible_without_changing_provider_or_opening(self):
        prior = CommittedSpeechUnitPolicy(80, 220, 260)
        before = StreamingSpeechQueue.committed_units(LONG_TEXT, policy=prior)
        after = StreamingSpeechQueue.committed_units(LONG_TEXT)
        self.assertEqual([11, 16, 25, 30, 34, 9], [len(unit.split()) for unit in before])
        self.assertEqual([11, 16, 14, 11, 17, 13, 21, 13, 9],
                         [len(unit.split()) for unit in after])
        self.assertEqual(before[:2], after[:2])
        self.assertEqual("".join(before), "".join(after))
        self.assertTrue(all(unit.rstrip().endswith(".") for unit in after))

    def test_work_bounds_keep_indivisible_words_and_whitespace_exact(self):
        for text in (
            "Hi.", "  Fine.  ",
            "word " * 100, "unbroken" * 60,
            "\tContinue naturally.\n\n" * 18,
            LONG_TEXT + " " + "x" * 400 + " end.",
        ):
            with self.subTest(characters=len(text)):
                units = StreamingSpeechQueue.committed_units(text)
                self.assertEqual(text, "".join(units))
                self.assertEqual(text.split(), [word for unit in units for word in unit.split()])
                self.assertTrue(all(unit.strip() for unit in units))
        self.assertEqual((), StreamingSpeechQueue.committed_units(" \t\n"))

    def test_invalid_work_policy_fails_before_queue_workers_start(self):
        for values in ((0, 96, 120), (80, 40, 120), (48, 160, 120)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                CommittedSpeechUnitPolicy(*values)


class CommittedSynthesisCancellationTests(unittest.TestCase):
    def setUp(self):
        self.provider = SyntheticKokoro()
        self.addCleanup(self.provider.stop)
        self.events = []

        class Recorder:
            def mark(_self, event, **data):
                self.events.append((event, data))

        recorder_patch = patch.object(audio_owner, 'development_flight_recorder', return_value=Recorder())
        recorder_patch.start()
        self.addCleanup(recorder_patch.stop)

    def test_cancelled_native_work_drains_once_then_replacement_owns_single_resource(self):
        entered, release, replacement_started, replacement_finished = (
            threading.Event() for _ in range(4))
        old_cancel = threading.Event()
        old_manager = TtsSynthesisResourceManager(self.provider, cancelled=old_cancel)
        replacement_cancel = threading.Event()
        replacement_manager = TtsSynthesisResourceManager(
            self.provider, cancelled=replacement_cancel)
        old_results, new_results = [], []
        active = 0
        maximum_active = 0
        original_pipeline = self.provider.pipeline

        def pipeline(text, **kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                yield from original_pipeline(text, **kwargs)
            finally:
                active -= 1

        self.provider.pipeline = pipeline

        def before(index, _text):
            if index == 2:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("Synthetic native unit barrier timed out")

        self.provider.before_unit = before

        def obsolete():
            for index, unit in enumerate(StreamingSpeechQueue.committed_units(LONG_TEXT)):
                result = old_manager.prepare(unit, unit_index=index)
                old_results.append(result)
                if not result.succeeded:
                    return

        def replacement():
            replacement_started.set()
            new_results.append(replacement_manager.prepare("The replacement stays intact."))
            replacement_finished.set()

        old = threading.Thread(target=obsolete, name="test-obsolete-native-unit")
        new = threading.Thread(target=replacement, name="test-replacement-native-unit")
        old.start()
        try:
            self.assertTrue(entered.wait(1))
            old_cancel.set()
            new.start()
            self.assertTrue(replacement_started.wait(1))
            self.assertFalse(replacement_finished.wait(.02))
            self.assertEqual(3, len(self.provider.units))
        finally:
            release.set()
            old.join(2)
            if new.ident is not None:
                new.join(2)
        self.assertFalse(old.is_alive())
        self.assertFalse(new.is_alive())
        self.assertEqual(1, maximum_active)
        self.assertEqual([True, True, False], [r.succeeded for r in old_results])
        self.assertTrue(old_results[-1].cancelled)
        self.assertIsNone(old_results[-1].prepared)
        self.assertTrue(new_results[0].succeeded)
        self.assertEqual(4, len(self.provider.units))
        self.assertEqual("The replacement stays intact.", self.provider.units[-1])
        waits = [data for event, data in self.events if event == "kokoro_synthesis_resource_wait"]
        self.assertEqual(4, len(waits))
        self.assertTrue(all("duration_ms" in data and "text" not in data for data in waits))

    def test_cancelled_waiter_does_not_wait_for_obsolete_native_unit_or_run_inference(self):
        cancelled = threading.Event()
        results = []
        ready = threading.Event()

        def waiter():
            ready.set()
            results.append(self.provider.prepare_cancellable_stream_chunk(
                "Do not synthesize a cancelled waiter.", cancelled=cancelled))

        worker = threading.Thread(target=waiter, name="test-cancelled-synthesis-waiter")
        self.provider._kokoro_synthesis_lock.acquire()
        try:
            worker.start()
            self.assertTrue(ready.wait(1))
            cancelled.set()
            worker.join(.5)
            self.assertFalse(worker.is_alive())
        finally:
            self.provider._kokoro_synthesis_lock.release()
            worker.join(1)
        self.assertEqual([None], results)
        self.assertEqual([], self.provider.units)
        waits = [data for event, data in self.events if event == "kokoro_synthesis_resource_wait"]
        self.assertEqual(1, len(waits))
        self.assertTrue(waits[0]["cancelled"])

    def test_full_preparation_keeps_its_existing_native_unit_policy(self):
        self.provider.prepare_cancellable_stream_chunk(LONG_TEXT, cancelled=threading.Event())
        self.assertEqual(LONG_TEXT, "".join(self.provider.units))
        self.assertTrue(any(len(unit) > 120 for unit in self.provider.units))


if __name__ == "__main__":
    unittest.main()
