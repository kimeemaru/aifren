import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import wave
from unittest.mock import MagicMock, patch

import numpy as np

from assistant_service import AssistantService
from config import AUDIO8_WARMUP_TEXT, TTS_CHUNK_MIN_CHARS
from tts import tts
from tts.chunker import SpeechChunker
from tts.streaming import StreamingSpeechQueue, TtsSynthesisResourceManager
from tts.audio8_runtime import Audio8Runtime


class _HttpResponse:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.payload


class ProviderSelectionTests(unittest.TestCase):
    def test_audio8_disposable_warmup_exercises_a_production_sized_chunk(self):
        self.assertGreaterEqual(len(AUDIO8_WARMUP_TEXT), TTS_CHUNK_MIN_CHARS)

    def test_default_audio8_uses_kokoro_as_runtime_fallback(self):
        primary, fallback = MagicMock(), MagicMock()
        with patch.object(tts, "Audio8TextToSpeech", return_value=primary), patch.object(tts, "KokoroTextToSpeech", return_value=fallback):
            provider = tts.create_tts_provider("audio8")
            self.assertFalse(provider.fallback_loaded)
            tts.KokoroTextToSpeech.assert_not_called()
            self.assertIs(provider.fallback, fallback)
        self.assertIs(provider.primary, primary)

    def test_audio8_failed_chunk_falls_back_once(self):
        primary, fallback = MagicMock(), MagicMock()
        primary.speak.return_value = False
        fallback.speak.return_value = True
        provider = tts.FallbackTextToSpeech(primary, fallback)
        self.assertTrue(provider.speak("One complete sentence."))
        fallback.speak.assert_called_once()

    def test_prepared_audio8_chunk_falls_back_without_reordering(self):
        primary, fallback = MagicMock(), MagicMock()
        primary.prepare_stream_chunk.side_effect = RuntimeError("offline")
        fallback.prepare_stream_chunk.return_value = "prepared fallback"
        fallback.start_prepared_chunk.return_value = True
        provider = tts.FallbackTextToSpeech(primary, fallback)
        prepared = provider.prepare_stream_chunk("One complete sentence.")
        self.assertTrue(provider.start_prepared_chunk(prepared))
        fallback.prepare_stream_chunk.assert_called_once_with("One complete sentence.")
        fallback.start_prepared_chunk.assert_called_once_with("prepared fallback")

    def test_audio8_wrapper_exposes_kokoro_cpu_resource_fallback(self):
        primary, fallback = MagicMock(), MagicMock()
        fallback.fallback_to_cpu_after_resource_failure.return_value = True
        fallback.device = "cpu"
        provider = tts.FallbackTextToSpeech(primary, fallback)

        self.assertTrue(provider.fallback_to_cpu_after_resource_failure())
        fallback.fallback_to_cpu_after_resource_failure.assert_called_once_with()
        self.assertEqual("cpu", provider.device)

    def test_audio8_startup_failure_uses_kokoro_when_available(self):
        fallback = MagicMock()
        with patch.object(tts, "Audio8TextToSpeech", side_effect=RuntimeError("offline")), patch.object(
            tts, "KokoroTextToSpeech", return_value=fallback
        ):
            self.assertIs(fallback, tts.create_tts_provider("audio8"))

    def test_audio8_fallback_exposes_a_safe_reason_without_reference_data(self):
        fallback = MagicMock()
        with patch.object(tts, "Audio8TextToSpeech", side_effect=RuntimeError("private path must not leak")), patch.object(
            tts, "KokoroTextToSpeech", return_value=fallback
        ):
            provider = tts.create_tts_provider("audio8")
        self.assertEqual("audio8", provider.configured_provider)
        self.assertEqual("Audio8 unavailable (RuntimeError)", provider.fallback_reason)
        self.assertNotIn("private", provider.fallback_reason)

    def test_both_chunk_providers_can_fail_without_raising(self):
        primary, fallback = MagicMock(), MagicMock()
        primary.speak.return_value = False
        fallback.speak.return_value = False
        self.assertFalse(tts.FallbackTextToSpeech(primary, fallback).speak("Text still persists."))

    def test_piper_is_not_a_supported_provider(self):
        with self.assertRaisesRegex(ValueError, "audio8.*kokoro"):
            tts.create_tts_provider("piper")


class ChunkerTests(unittest.TestCase):
    def test_sentences_are_complete_and_ordered(self):
        chunker = SpeechChunker(minimum_chars=8, preferred_max_chars=80, hard_max_chars=120)
        self.assertEqual(("Hello there.",), chunker.feed("Hello there. Next"))
        self.assertEqual(("Next sentence!",), chunker.feed(" sentence!"))
        self.assertEqual((), chunker.finish())

    def test_long_sentence_uses_clause_guard_and_never_drops_tail(self):
        chunker = SpeechChunker(minimum_chars=15, preferred_max_chars=25, hard_max_chars=35)
        emitted = chunker.feed("This is a deliberately long sentence, with a useful clause, and a final tail")
        emitted += chunker.finish()
        self.assertEqual("This is a deliberately long sentence, with a useful clause, and a final tail", " ".join(emitted))
        self.assertTrue(all(part.strip() for part in emitted))

    def test_abbreviation_and_decimal_do_not_emit_empty_chunks(self):
        chunker = SpeechChunker(minimum_chars=10, preferred_max_chars=80, hard_max_chars=120)
        pieces = chunker.feed("Version 1.5 is ready. Dr. Lee agreed.") + chunker.finish()
        self.assertTrue(all(piece.strip() for piece in pieces))

    def test_overlong_complete_sentence_still_respects_hard_audio_limit(self):
        chunker = SpeechChunker(minimum_chars=20, preferred_max_chars=40, hard_max_chars=55)
        text = "This deliberately oversized sentence has no punctuation until its ending and must remain safely chunked."
        pieces = chunker.feed(text) + chunker.finish()
        self.assertEqual(text, " ".join(pieces))
        self.assertTrue(all(len(piece) <= 55 for piece in pieces))

class PlaybackTests(unittest.TestCase):
    def test_shared_playback_controls_clamp_volume_and_stop(self):
        provider = object.__new__(tts.LocalPlaybackTTS)
        provider._initialize_playback_state()
        provider.set_volume(5)
        self.assertEqual(provider.get_volume(), 1.0)
        provider.set_volume(-1)
        self.assertEqual(provider.get_volume(), 0.0)
        provider.stop()
        self.assertTrue(provider.playback_finished.is_set())

    def test_audio8_wav_decoder_uses_pcm_data(self):
        payload = io.BytesIO()
        with wave.open(payload, "wb") as wav_file:
            wav_file.setnchannels(1); wav_file.setsampwidth(2); wav_file.setframerate(24000)
            wav_file.writeframes(b"\x00\x00\x10\x00")
        audio, rate = tts.LocalPlaybackTTS.decode_wav_bytes(payload.getvalue())
        self.assertEqual(24000, rate)
        self.assertEqual((2, 1), audio.shape)
        self.assertEqual(np.float32, audio.dtype)

    def test_audio8_readiness_probe_fails_closed_without_network(self):
        provider = object.__new__(tts.Audio8TextToSpeech)
        provider.runtime = MagicMock(); provider.runtime.ensure_ready.return_value = False
        self.assertFalse(provider.is_available())

    def test_kokoro_stream_chunk_can_be_prepared_before_ordered_playback(self):
        provider = object.__new__(tts.KokoroTextToSpeech)
        provider._initialize_playback_state()
        audio = np.zeros((2400, 1), dtype=np.float32)
        provider._generate_audio = MagicMock(return_value=(audio, 24000, [0.0]))

        prepared = provider.prepare_stream_chunk("Synthetic sentence.")

        provider._generate_audio.assert_called_once_with("Synthetic sentence.")
        with patch.object(provider, "_start_playback", return_value=True) as start:
            self.assertTrue(provider.start_prepared_chunk(prepared))
        start.assert_called_once()
        self.assertEqual([0.0], start.call_args.args[3])
        self.assertFalse(provider.playback_finished.is_set())

    def test_kokoro_continuous_player_opens_portaudio_once_for_two_pcm_chunks(self):
        import threading
        provider = object.__new__(tts.KokoroTextToSpeech)
        provider._initialize_playback_state()
        provider._initialize_continuous_state()
        starts, finishes, streams = [], [], []
        provider.set_playback_started_callback(
            lambda duration, envelope, words, playback_id: starts.append(playback_id)
        )
        provider.set_playback_finished_callback(finishes.append)

        class FakeOutputStream:
            def __init__(self, *, samplerate, channels, dtype, callback):
                self.callback = callback
                self.active = False
                self.worker = None
                streams.append(self)
            def start(self):
                self.active = True
                def pump():
                    while self.active:
                        output = np.zeros((64, 1), dtype=np.float32)
                        try:
                            self.callback(output, 64, None, None)
                        except tts.sd.CallbackStop:
                            self.active = False
                        time.sleep(.001)
                self.worker = threading.Thread(target=pump, daemon=True)
                self.worker.start()
            def abort(self): self.active = False
            def stop(self): self.active = False
            def close(self):
                self.active = False
                if self.worker is not None: self.worker.join(.2)

        first = (np.ones((240, 1), dtype=np.float32) * .1, 24000, [0.0])
        second = (np.ones((240, 1), dtype=np.float32) * .1, 24000, [0.0])
        with patch.object(tts.sd, "OutputStream", FakeOutputStream), patch.object(
            tts.sd, "sleep", side_effect=lambda milliseconds: time.sleep(milliseconds / 1000.0)
        ):
            self.assertTrue(provider.begin_prepared_stream(first, on_started=lambda: None))
            self.assertTrue(provider.append_prepared_stream(second, on_started=lambda: None))
            provider.finish_prepared_stream()
            self.assertTrue(provider.playback_finished.wait(1))
            if provider.playback_thread is not None:
                provider.playback_thread.join(1)

        self.assertEqual(1, len(streams))
        self.assertEqual(2, len(starts))
        self.assertEqual(1, len(set(starts)))
        self.assertEqual(starts[-1:], finishes)

    def test_kokoro_serializes_synthesis_across_replacement_turns(self):
        import threading
        provider = object.__new__(tts.KokoroTextToSpeech)
        provider._initialize_playback_state()
        provider._initialize_continuous_state()
        provider.device = "cpu"
        provider.voice = "af_test"
        provider.voice_path = "synthetic.pt"
        provider.speed = 1.0
        active = 0
        maximum_active = 0
        guard = threading.Lock()

        def pipeline(text, **_kwargs):
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(.03)
            yield type("Result", (), {"audio": np.ones(24), "tokens": ()})()
            with guard:
                active -= 1

        provider.pipeline = pipeline
        workers = [threading.Thread(target=provider._generate_audio, args=(text,)) for text in ("First.", "Second.")]
        for worker in workers: worker.start()
        for worker in workers: worker.join(1)

        self.assertEqual(1, maximum_active)

    def test_cancelled_synthesis_result_never_opens_continuous_playback(self):
        import threading
        synthesis_started = threading.Event()
        release = threading.Event()
        class BlockingContinuousFake:
            def __init__(self):
                self.playback_finished = threading.Event()
                self.begin_count = 0
                self.stop_count = 0
            def prepare_stream_chunk(self, text):
                synthesis_started.set()
                release.wait(1)
                return text
            def begin_prepared_stream(self, prepared, *, on_started=None):
                self.begin_count += 1
                return True
            def append_prepared_stream(self, prepared, *, on_started=None):
                return True
            def finish_prepared_stream(self):
                self.playback_finished.set()
            def stop(self):
                self.stop_count += 1
                self.playback_finished.set()

        fake = BlockingContinuousFake()
        queue = StreamingSpeechQueue(fake)
        self.assertTrue(queue.submit("Speech from the cancelled turn."))
        self.assertTrue(synthesis_started.wait(1))
        queue.cancel()
        release.set()
        queue.join(1)

        self.assertEqual(0, fake.begin_count)
        self.assertEqual(1, fake.stop_count)

    def test_audio8_runtime_warms_once_and_reuses_resident_voice(self):
        calls = []
        def fake_open(req, timeout):
            calls.append((req.full_url, req.get_method()))
            if req.full_url.endswith("/api/health"):
                return _HttpResponse({"ok": True})
            if req.full_url.endswith("/api/voices"):
                return _HttpResponse({"voices": [{"name": "approved_dev_voice"}]})
            if req.full_url.endswith("/audio/speech"):
                return _HttpResponse(b"RIFFfake")
            raise AssertionError(req.full_url)
        runtime = Audio8Runtime(
            base_url="http://audio8.test/v1", model="arktts", voice_profile="approved_dev_voice",
            urlopen=fake_open,
        )
        self.assertTrue(runtime.ensure_ready())
        runtime.request_audio("First."); runtime.request_audio("Second.")
        status = runtime.status
        self.assertEqual(1, status["model_load_count"])
        self.assertEqual(0, status["voice_condition_count"])
        self.assertEqual(1, status["warmup_count"])
        self.assertEqual(2, status["synthesis_count"])
        self.assertEqual(1, sum(url.endswith("/api/voices") for url, _ in calls))

    def test_audio8_runtime_registers_reference_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"; reference.write_bytes(b"wav")
            calls = []
            def fake_open(req, timeout):
                calls.append(req.full_url)
                if req.full_url.endswith("/api/health"): return _HttpResponse({"ok": True})
                if req.full_url.endswith("/api/voices"): return _HttpResponse({"voices": []})
                if req.full_url.endswith("/api/voices/register"):
                    return _HttpResponse({"ok": True, "voice": {"name": "aifren_local_voice"}})
                if req.full_url.endswith("/audio/speech"): return _HttpResponse(b"RIFFfake")
                raise AssertionError(req.full_url)
            runtime = Audio8Runtime(
                base_url="http://audio8.test/v1", model="arktts", reference_audio=str(reference),
                reference_text="approved transcript", urlopen=fake_open,
            )
            self.assertTrue(runtime.ensure_ready())
            self.assertTrue(runtime.ensure_ready())
            self.assertEqual(1, calls.count("http://audio8.test/api/voices/register"))
            self.assertEqual(1, runtime.status["voice_condition_count"])

    def test_audio8_runtime_start_failure_stays_failed(self):
        runtime = Audio8Runtime(
            base_url="http://audio8.test/v1", model="arktts", runtime_root="/missing",
            urlopen=lambda req, timeout: (_ for _ in ()).throw(OSError("offline")),
        )
        self.assertFalse(runtime.ensure_ready())
        self.assertEqual("failed", runtime.status["state"])

    def test_audio8_runtime_launches_one_resident_service_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "start_server.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            launches, health = [], {"count": 0}
            class Process:
                def poll(self): return None
            def fake_open(req, timeout):
                if req.full_url.endswith("/api/health"):
                    health["count"] += 1
                    if health["count"] == 1: raise OSError("not yet")
                    return _HttpResponse({"ok": True})
                if req.full_url.endswith("/api/voices"): return _HttpResponse({"voices": [{"name": "safe"}]})
                if req.full_url.endswith("/audio/speech"): return _HttpResponse(b"RIFFfake")
                raise AssertionError(req.full_url)
            runtime = Audio8Runtime(base_url="http://audio8.test/v1", model="arktts", runtime_root=str(root),
                voice_profile="safe", urlopen=fake_open, start_process=lambda *args, **kwargs: launches.append((args, kwargs)) or Process())
            self.assertTrue(runtime.ensure_ready())
            self.assertTrue(runtime.ensure_ready())
            self.assertEqual(1, len(launches))
            self.assertEqual(1, runtime.status["model_load_count"])

    def test_audio8_runtime_stops_only_the_service_it_started(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stop_script = root / "stop_server.sh"
            stop_script.write_text("#!/bin/sh\n", encoding="utf-8")
            stopped = []
            runtime = Audio8Runtime(
                base_url="http://audio8.test/v1", model="arktts", runtime_root=str(root),
                stop_process=lambda *args, **kwargs: stopped.append((args, kwargs)),
            )
            runtime._owns_service = True
            runtime._ready = True

            self.assertTrue(runtime.shutdown_owned())
            self.assertEqual(1, len(stopped))
            self.assertFalse(runtime.owns_service)
            self.assertEqual("stopped", runtime.status["state"])

            external = Audio8Runtime(base_url="http://audio8.test/v1", model="arktts")
            self.assertFalse(external.shutdown_owned())

    def test_owned_audio8_service_is_stopped_before_fallback_loads(self):
        order = []
        primary = MagicMock()
        primary.prepare_stream_chunk.side_effect = RuntimeError("unhealthy")
        primary.deactivate.side_effect = lambda: order.append("audio8_stopped")
        fallback = MagicMock()
        fallback.prepare_stream_chunk.side_effect = lambda text: order.append("kokoro_loaded") or text
        provider = tts.FallbackTextToSpeech(primary, fallback_factory=lambda: fallback)

        provider.prepare_stream_chunk("Fallback sentence.")

        self.assertEqual(["audio8_stopped", "kokoro_loaded"], order)
        self.assertTrue(provider.fallback_loaded)

    def test_queue_keeps_speech_in_order(self):
        class FakeTts:
            def __init__(self):
                import threading
                self.playback_finished = threading.Event(); self.playback_finished.set(); self.calls = []
            def speak(self, text): self.calls.append(text); self.playback_finished.set(); return True
            def stop(self): pass
        fake = FakeTts()
        queue = StreamingSpeechQueue(fake, max_chunks=2)
        self.assertTrue(queue.submit("First.")); self.assertTrue(queue.submit("Second."))
        queue.close(); queue.join(1)
        self.assertEqual(["First.", "Second."], fake.calls)

    def test_queue_preserves_audio_and_subtitle_chunk_identity_at_playback(self):
        class FakeTts:
            def __init__(self):
                import threading
                self.playback_finished = threading.Event(); self.playback_finished.set()
            def speak(self, text): return True
            def stop(self): pass
        starting = []
        fake = FakeTts()
        queue = StreamingSpeechQueue(
            fake,
            on_chunk_starting=lambda spoken, presentation, index: starting.append(
                (spoken, presentation, index)
            ),
        )
        self.assertTrue(queue.submit("Hello there.", presentation_text="**Hello** there."))
        queue.close(); queue.join(1)
        self.assertEqual([("Hello there.", "**Hello** there.", 0)], starting)

    def test_queue_announces_chunk_before_synthesis_and_playback(self):
        import threading
        order = []
        class FakeTts:
            def __init__(self): self.playback_finished = threading.Event(); self.playback_finished.set()
            def prepare_stream_chunk(self, text): order.append("synthesis"); return text
            def start_prepared_chunk(self, text): order.append("playback"); self.playback_finished.set(); return True
            def stop(self): pass
        queue = StreamingSpeechQueue(
            FakeTts(),
            on_chunk_submitted=lambda spoken, presentation, index: order.append("queued"),
        )
        self.assertTrue(queue.submit("Hello there."))
        queue.close(); queue.join(1)
        self.assertEqual(["queued", "synthesis", "playback"], order)

    def test_audio8_capability_prepares_following_chunk_during_playback(self):
        import threading
        class PreparedFake:
            def __init__(self):
                self.playback_finished = threading.Event()
                self.prepared, self.played = [], []
                self.first_played = threading.Event()
                self.second_prepared = threading.Event()
            def prepare_stream_chunk(self, text):
                self.prepared.append(text)
                if text == "Second.": self.second_prepared.set()
                return text
            def start_prepared_chunk(self, text):
                self.played.append(text)
                if text == "First.": self.first_played.set()
                return True
            def stop(self): self.playback_finished.set()
        fake = PreparedFake()
        queue = StreamingSpeechQueue(fake, max_chunks=2)
        self.assertTrue(queue.submit("First.")); self.assertTrue(queue.submit("Second.")); queue.close()
        self.assertTrue(fake.first_played.wait(1))
        self.assertTrue(fake.second_prepared.wait(1), "next Audio8 chunk should synthesize while first waits to play")
        self.assertEqual(["First."], fake.played)
        fake.playback_finished.set()
        queue.join(1)
        self.assertEqual(["First.", "Second."], fake.played)

    def test_kokoro_continuous_capability_opens_one_session_for_all_sentences(self):
        import threading
        class ContinuousFake:
            def __init__(self):
                self.playback_finished = threading.Event()
                self.prepared, self.appended = [], []
                self.begin_count = 0
                self.finish_count = 0
            def prepare_stream_chunk(self, text):
                self.prepared.append(text)
                return text
            def begin_prepared_stream(self, prepared, *, on_started=None):
                self.begin_count += 1
                self.appended.append(prepared)
                if on_started: on_started()
                return True
            def append_prepared_stream(self, prepared, *, on_started=None):
                self.appended.append(prepared)
                if on_started: on_started()
                return True
            def finish_prepared_stream(self):
                self.finish_count += 1
                self.playback_finished.set()
            def stop(self): self.playback_finished.set()

        fake = ContinuousFake()
        started = []
        queue = StreamingSpeechQueue(
            fake, max_chunks=2,
            on_chunk_starting=lambda spoken, presentation, index: started.append((index, spoken)),
        )
        for text in ("First.", "Second!", "Third?"):
            self.assertTrue(queue.submit(text))
        queue.close(); queue.join(1)
        self.assertEqual(1, fake.begin_count)
        self.assertEqual(1, fake.finish_count)
        self.assertEqual(["First.", "Second!", "Third?"], fake.prepared)
        self.assertEqual(fake.prepared, fake.appended)
        self.assertEqual([(0, "First."), (1, "Second!"), (2, "Third?")], started)

    def test_bounded_queue_coalesces_only_not_yet_synthesized_sentences_in_order(self):
        import threading
        gate = threading.Event()
        first_started = threading.Event()
        class SlowFake:
            def __init__(self):
                self.playback_finished = threading.Event(); self.playback_finished.set()
                self.prepared = []
            def prepare_stream_chunk(self, text):
                self.prepared.append(text)
                if text == "First.":
                    first_started.set(); gate.wait(1)
                return text
            def start_prepared_chunk(self, prepared):
                self.playback_finished.set(); return True
            def stop(self): self.playback_finished.set()

        fake = SlowFake()
        queue = StreamingSpeechQueue(fake, max_chunks=1)
        self.assertTrue(queue.submit("First."))
        self.assertTrue(first_started.wait(1))
        self.assertTrue(queue.submit("Second."))
        self.assertTrue(queue.submit("Third."))
        self.assertTrue(queue.submit("Fourth."))
        queue.close(); gate.set(); queue.join(1)
        self.assertEqual(["First.", "Second.", "Third. Fourth."], fake.prepared)

    def test_transient_synthesis_failure_retries_same_chunk_before_later_chunks(self):
        import threading
        class RetryFake:
            def __init__(self):
                self.playback_finished = threading.Event(); self.playback_finished.set()
                self.attempts, self.played = [], []
            def prepare_stream_chunk(self, text):
                self.attempts.append(text)
                if text == "First." and self.attempts.count(text) == 1:
                    raise RuntimeError("CUDA allocation temporarily unavailable")
                return text
            def start_prepared_chunk(self, prepared):
                self.played.append(prepared); self.playback_finished.set(); return True
            def stop(self): self.playback_finished.set()
        fake = RetryFake()
        queue = StreamingSpeechQueue(fake)
        queue.submit("First."); queue.submit("Second."); queue.close(); queue.join(2)
        self.assertEqual(["First.", "First.", "Second."], fake.attempts)
        self.assertEqual(["First.", "Second."], fake.played)

    def test_repeated_cuda_failure_retries_same_chunk_once_on_cpu(self):
        import threading
        class CpuFallbackFake:
            def __init__(self):
                self.playback_finished = threading.Event(); self.playback_finished.set()
                self.attempts, self.played = [], []
                self.device = "cuda"
                self.fallbacks = 0
            def prepare_stream_chunk(self, text):
                self.attempts.append((text, self.device))
                if text == "Middle." and self.device == "cuda":
                    raise RuntimeError("CUDA out of memory")
                return text
            def fallback_to_cpu_after_resource_failure(self):
                self.fallbacks += 1; self.device = "cpu"; return True
            def start_prepared_chunk(self, prepared):
                self.played.append(prepared); self.playback_finished.set(); return True
            def stop(self): self.playback_finished.set()
        failures=[]; fake=CpuFallbackFake()
        queue=StreamingSpeechQueue(fake, on_failure=failures.append)
        for item in ("First.", "Middle.", "Later."): queue.submit(item)
        queue.close(); queue.join(2)
        self.assertEqual([
            ("First.", "cuda"), ("Middle.", "cuda"),
            ("Middle.", "cuda"), ("Middle.", "cpu"), ("Later.", "cpu"),
        ], fake.attempts)
        self.assertEqual(["First.", "Middle.", "Later."], fake.played)
        self.assertEqual(1, fake.fallbacks)
        self.assertEqual([], failures)

    def test_second_synthesis_failure_aborts_sequence_without_skipping_middle(self):
        import threading
        class FailedFake:
            def __init__(self):
                self.playback_finished = threading.Event(); self.playback_finished.set(); self.attempts=[]; self.played=[]
            def prepare_stream_chunk(self, text):
                self.attempts.append(text)
                if text == "Middle.": raise RuntimeError("CUDA out of memory")
                return text
            def start_prepared_chunk(self, prepared):
                self.played.append(prepared); self.playback_finished.set(); return True
            def stop(self): self.playback_finished.set()
        failures=[]; fake=FailedFake(); queue=StreamingSpeechQueue(fake, on_failure=failures.append)
        for item in ("First.", "Middle.", "Later."): queue.submit(item)
        queue.close(); queue.join(2)
        self.assertEqual(2, fake.attempts.count("Middle."))
        self.assertNotIn("Later.", fake.attempts)
        self.assertEqual(["tts_synthesis_sequence_failed"], failures)

    def test_interruption_cancels_provider_idle_retry_wait(self):
        import threading
        attempted = threading.Event()
        class BusyFake:
            def __init__(self): self.playback_finished=threading.Event(); self.playback_finished.set(); self.stop_count=0
            def prepare_stream_chunk(self, text): attempted.set(); raise RuntimeError("CUDA busy")
            def stop(self): self.stop_count += 1; self.playback_finished.set()
        fake=BusyFake(); queue=StreamingSpeechQueue(fake, provider_generation_active=lambda: True)
        queue.submit("Pending."); self.assertTrue(attempted.wait(1)); queue.cancel(); queue.join(1)
        self.assertEqual(1, fake.stop_count)

    def test_interruption_during_direct_cpu_fallback_never_starts_same_utterance(self):
        import threading
        cancelled = threading.Event()
        class DirectFake:
            def __init__(self):
                self.attempts = 0
                self.fallbacks = 0
            def prepare_stream_chunk(self, _text):
                self.attempts += 1
                raise RuntimeError("CUDA out of memory")
            def fallback_to_cpu_after_resource_failure(self):
                self.fallbacks += 1
                cancelled.set()
                return True
        fake = DirectFake()
        manager = TtsSynthesisResourceManager(fake, cancelled=cancelled)
        result = manager.prepare("Exact governed utterance.", direct=True)
        self.assertFalse(result.succeeded)
        self.assertTrue(result.cancelled)
        self.assertEqual(2, fake.attempts)
        self.assertEqual(1, fake.fallbacks)

    def test_cleaning_remains_provider_independent(self):
        self.assertEqual(AssistantService.clean_text_for_tts("*waves* Hello, *smiles* friend!"), "Hello, friend!")

    def test_emoji_are_removed_only_from_spoken_projection(self):
        cases = {
            "Okay! 😊": "Okay!",
            "That's cute ✨✨": "That's cute.",
            "❤️": "",
            "Hello 👩🏽‍💻 friend": "Hello friend",
            "Flag 🇨🇦": "Flag.",
            "Press 1️⃣ now": "Press now",
        }
        for canonical, expected in cases.items():
            with self.subTest(canonical=canonical):
                self.assertEqual(expected, AssistantService.clean_text_for_tts(canonical))

    def test_accelerator_oom_classification_is_cuda_hip_and_rocm_neutral(self):
        import sys
        import threading
        from types import SimpleNamespace

        class OutOfMemoryError(Exception):
            pass
        OutOfMemoryError.__module__ = "torch.cuda"
        self.assertEqual(
            "transient_resource",
            TtsSynthesisResourceManager.failure_category(OutOfMemoryError(), False),
        )
        self.assertEqual(
            "concurrent_provider_resource",
            TtsSynthesisResourceManager.failure_category(
                RuntimeError("HIP/ROCm accelerator out of memory"), True,
            ),
        )
        fake_torch = SimpleNamespace(version=SimpleNamespace(hip="6.2", cuda=None))
        manager = TtsSynthesisResourceManager(
            SimpleNamespace(device="cuda"), cancelled=threading.Event(),
        )
        with patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual("hip_rocm", manager.accelerator_backend())

    def test_kokoro_declares_conservative_complete_sentence_strategy(self):
        provider = object.__new__(tts.KokoroTextToSpeech)
        with patch("model_settings.kokoro_early_speech_status", return_value={"effective": True}):
            self.assertEqual("complete_sentences", provider.synthesis_strategy)
        with patch("model_settings.kokoro_early_speech_status", return_value={"effective": False}):
            self.assertEqual("whole_response", provider.synthesis_strategy)
        self.assertEqual("manual_chunks", tts.Audio8TextToSpeech.synthesis_strategy)

    def test_kokoro_runtime_cpu_fallback_moves_existing_model_without_changing_settings(self):
        import threading
        provider = object.__new__(tts.KokoroTextToSpeech)
        provider._kokoro_synthesis_lock = threading.Lock()
        provider.device = "cuda"
        provider.voice = "af_test"
        provider._repo_id = "synthetic/kokoro"
        provider._torch = MagicMock()
        model = MagicMock()
        model.to.return_value = model
        model.eval.return_value = model
        provider.pipeline = type("Pipeline", (), {"model": model})()
        replacement = object()
        provider._KPipeline = MagicMock(return_value=replacement)

        self.assertTrue(provider.fallback_to_cpu_after_resource_failure())
        self.assertEqual("cpu", provider.device)
        self.assertIs(replacement, provider.pipeline)
        model.to.assert_called_once_with("cpu")
        provider._KPipeline.assert_called_once_with(
            lang_code="a", repo_id="synthetic/kokoro", model=model, device="cpu"
        )
        provider._torch.cuda.empty_cache.assert_called_once_with()
        self.assertFalse(provider.fallback_to_cpu_after_resource_failure())
