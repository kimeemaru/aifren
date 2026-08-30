import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from stt.voice import VoiceInput
from voice.ptt import PushToTalk


class PushToTalkLifecycleTests(unittest.TestCase):
    def test_normal_ptt_reports_ordered_worker_stages(self):
        captured = threading.Event()
        transcribed = threading.Event()
        marked = []

        class Recorder:
            @staticmethod
            def mark(event, **metadata):
                marked.append((event, metadata))

        class Voice:
            def set_ptt_stage_observer(self, observer):
                self.observer = observer

            def record_ptt(self, is_pressed, capture_id=None):
                metadata = {"capture_id": capture_id}
                self.observer("mic_open_begin", **metadata)
                self.observer("mic_open_end", **metadata)
                self.observer("mic_capture_started", **metadata)
                captured.set()
                while is_pressed():
                    time.sleep(0.001)
                self.observer("mic_close_begin", **metadata)
                self.observer("mic_close_end", **metadata)
                self.observer("captured_audio_ready", audio_samples=1600, byte_count=3200, **metadata)
                self.observer("wav_prepare_begin", **metadata)
                self.observer("wav_prepare_end", **metadata)
                self.observer("whisper_begin", **metadata)
                self.observer("whisper_end", **metadata)
                self.observer("transcription_ready", characters=5, words=1, **metadata)
                return "hello"

        class Tts:
            @staticmethod
            def stop():
                return None

        with patch("voice.ptt.development_flight_recorder", return_value=Recorder()):
            ptt = PushToTalk(
                Voice(), Tts(), lambda _text: transcribed.set(),
                on_tts_interrupt=lambda: None, listen_globally=False,
            )
            try:
                ptt.press()
                self.assertTrue(captured.wait(1))
                ptt.release()
                self.assertTrue(transcribed.wait(1))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not any(
                    event == "ptt_record_thread_exit" for event, _metadata in marked
                ):
                    time.sleep(0.001)
            finally:
                ptt.stop()

        stages = [event for event, _metadata in marked]
        expected = [
            "ptt_record_thread_started", "mic_open_begin", "mic_open_end",
            "mic_capture_started", "ptt_release_seen", "mic_close_begin", "mic_close_end",
            "captured_audio_ready", "wav_prepare_begin", "wav_prepare_end", "whisper_begin",
            "whisper_end", "transcription_ready", "ptt_record_thread_exit",
        ]
        positions = [stages.index(stage) for stage in expected]
        self.assertEqual(positions, sorted(positions))

    def test_real_voice_input_reports_numeric_audio_and_whisper_stages(self):
        stages = []
        pressed = {"value": True}

        class FakeStt:
            @staticmethod
            def transcribe(_path):
                return " hello "

        class FakeInputStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def start(self):
                self.callback(np.ones((1600, 1), dtype=np.int16), 1600, None, None)
                pressed["value"] = False

            @staticmethod
            def stop():
                return None

            @staticmethod
            def close():
                return None

            @staticmethod
            def abort():
                return None

        voice = object.__new__(VoiceInput)
        voice.stt = FakeStt()
        voice._ptt_stage_observer = lambda stage, **metadata: stages.append((stage, metadata))
        with patch("stt.voice.sd.InputStream", FakeInputStream):
            result = voice.record_ptt(lambda: pressed["value"])

        self.assertEqual(result, "hello")
        names = [stage for stage, _metadata in stages]
        expected = [
            "mic_open_begin", "mic_open_end", "mic_capture_started", "mic_close_begin",
            "mic_stop_begin", "mic_stop_end", "mic_stream_close_begin", "mic_stream_close_end",
            "mic_close_end", "captured_audio_prepare_begin", "captured_audio_ready",
            "wav_prepare_begin", "wav_prepare_end", "whisper_begin", "whisper_end",
            "transcription_ready",
        ]
        self.assertEqual([names.index(stage) for stage in expected], sorted(names.index(stage) for stage in expected))
        audio_metadata = dict(stages[names.index("captured_audio_ready")][1])
        self.assertEqual(audio_metadata["audio_samples"], 1600)
        self.assertEqual(audio_metadata["byte_count"], 3200)
        self.assertEqual(audio_metadata["duration_seconds"], 0.1)

    def test_transcription_callback_no_longer_owns_the_recorder_slot(self):
        callback_entered = threading.Event()
        release_callback = threading.Event()

        class Voice:
            @staticmethod
            def record_ptt(_is_pressed, capture_id=None):
                return "hello"

        ptt = object.__new__(PushToTalk)
        ptt.voice_input = Voice()
        ptt.on_transcription = lambda _text: (callback_entered.set(), release_callback.wait(1))
        ptt.on_state = None
        ptt.on_error = None
        ptt._state_lock = threading.Lock()
        recorder = threading.Thread(target=ptt._record)
        ptt.record_thread = recorder

        recorder.start()
        self.assertTrue(callback_entered.wait(1))
        self.assertIsNone(
            ptt.record_thread,
            "a completed STT capture must not block PTT for the whole assistant turn",
        )

        newer_recorder = object()
        ptt.record_thread = newer_recorder
        release_callback.set()
        recorder.join(1)
        self.assertIs(ptt.record_thread, newer_recorder)

    def test_blocked_close_is_aborted_discarded_and_next_press_succeeds(self):
        marked = []
        states = []
        transcriptions = []
        interrupt_count = []
        streams = []

        class Recorder:
            @staticmethod
            def mark(event, **metadata):
                marked.append((event, metadata))

        class FakeStt:
            @staticmethod
            def transcribe(_path):
                return "hello"

        class Stream:
            def __init__(self, callback, blocked):
                self.callback = callback
                self.blocked = blocked
                self.started = threading.Event()
                self.stop_release = threading.Event()
                self.aborted = False
                self.closed = False

            def start(self):
                self.callback(np.ones((1600, 1), dtype=np.int16), 1600, None, None)
                self.started.set()

            def stop(self):
                if self.blocked:
                    self.stop_release.wait(1)

            def abort(self):
                self.aborted = True
                self.stop_release.set()

            def close(self):
                self.closed = True

        def input_stream_factory(**kwargs):
            stream = Stream(kwargs["callback"], blocked=not streams)
            streams.append(stream)
            return stream

        voice = object.__new__(VoiceInput)
        voice.stt = FakeStt()
        voice._ptt_stage_observer = None

        class Tts:
            stop_calls = 0

            @classmethod
            def stop(cls):
                cls.stop_calls += 1

        with (
            patch("voice.ptt.development_flight_recorder", return_value=Recorder()),
            patch("stt.voice.sd.InputStream", side_effect=input_stream_factory),
            patch("stt.voice.PTT_MIC_CLOSE_TIMEOUT_SECONDS", 0.02),
            patch("stt.voice.PTT_MIC_ABORT_TIMEOUT_SECONDS", 0.02),
        ):
            ptt = PushToTalk(
                voice,
                Tts(),
                transcriptions.append,
                on_state=states.append,
                on_tts_interrupt=lambda: interrupt_count.append(True),
                listen_globally=False,
            )
            try:
                ptt.press()
                self.assertTrue(self._wait_until(lambda: streams and streams[0].started.is_set()))
                ptt.release()
                self.assertTrue(self._wait_until(lambda: ptt.record_thread is None))

                self.assertTrue(streams[0].aborted)
                self.assertTrue(streams[0].closed)
                self.assertEqual(transcriptions, [])
                self.assertEqual(states[-1], "ready")
                recovered_state = ptt.flight_recorder_state()
                self.assertFalse(recovered_state["ptt_worker_alive"])
                self.assertFalse(recovered_state["ptt_listening"])
                self.assertFalse(recovered_state["ptt_recording"])
                self.assertFalse(recovered_state["ptt_transcribing"])
                self.assertIn("mic_close_timeout", [event for event, _ in marked])
                self.assertIn("ptt_capture_discarded", [event for event, _ in marked])
                self.assertIn("ptt_worker_recovered", [event for event, _ in marked])
                self.assertNotIn("whisper_begin", [event for event, _ in marked])

                ptt.press()
                self.assertTrue(self._wait_until(lambda: len(streams) == 2 and streams[1].started.is_set()))
                ptt.release()
                self.assertTrue(self._wait_until(lambda: transcriptions == ["hello"]))
                self.assertTrue(self._wait_until(lambda: ptt.record_thread is None))
            finally:
                ptt.stop()

        self.assertEqual(len(interrupt_count), 2)
        self.assertEqual(Tts.stop_calls, 1, "PTT must retain the authoritative streamed-TTS interrupt callback")

    def test_late_old_close_completion_cannot_overwrite_new_capture_state(self):
        streams = []

        class FakeStt:
            @staticmethod
            def transcribe(_path):
                return "fresh"

        class Stream:
            def __init__(self, callback, blocked):
                self.callback = callback
                self.blocked = blocked
                self.started = threading.Event()
                self.stop_release = threading.Event()

            def start(self):
                self.callback(np.ones((800, 1), dtype=np.int16), 800, None, None)
                self.started.set()

            def stop(self):
                if self.blocked:
                    self.stop_release.wait(1)

            @staticmethod
            def abort():
                # Simulate an abort API that returns but whose old stop call
                # unwinds later. The PTT worker must still recover now.
                return None

            @staticmethod
            def close():
                return None

        def input_stream_factory(**kwargs):
            stream = Stream(kwargs["callback"], blocked=not streams)
            streams.append(stream)
            return stream

        voice = object.__new__(VoiceInput)
        voice.stt = FakeStt()
        voice._ptt_stage_observer = None
        transcriptions = []
        with (
            patch("stt.voice.sd.InputStream", side_effect=input_stream_factory),
            patch("stt.voice.PTT_MIC_CLOSE_TIMEOUT_SECONDS", 0.01),
            patch("stt.voice.PTT_MIC_ABORT_TIMEOUT_SECONDS", 0.01),
        ):
            ptt = PushToTalk(
                voice, object(), transcriptions.append,
                on_tts_interrupt=lambda: None, listen_globally=False,
            )
            try:
                ptt.press()
                self.assertTrue(self._wait_until(lambda: streams and streams[0].started.is_set()))
                first_capture_id = ptt._active_capture_id
                ptt.release()
                self.assertTrue(self._wait_until(lambda: ptt.record_thread is None))

                ptt.press()
                self.assertTrue(self._wait_until(lambda: len(streams) == 2 and streams[1].started.is_set()))
                second_capture_id = ptt._active_capture_id
                self.assertGreater(second_capture_id, first_capture_id)
                self.assertEqual(ptt.flight_recorder_state()["ptt_stage"], "mic_capture_started")

                streams[0].stop_release.set()
                time.sleep(0.03)
                self.assertEqual(
                    ptt.flight_recorder_state()["ptt_stage"],
                    "mic_capture_started",
                    "late cleanup from the old capture must not own new PTT state",
                )

                ptt.release()
                self.assertTrue(self._wait_until(lambda: transcriptions == ["fresh"]))
            finally:
                streams[0].stop_release.set()
                ptt.stop()

    @staticmethod
    def _wait_until(predicate, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.002)
        return bool(predicate())


if __name__ == "__main__":
    unittest.main()
