import io
import json
import os
import re
import threading
import time
import wave
from collections import deque
from queue import Empty, SimpleQueue
from urllib import request

import numpy as np
import sounddevice as sd
from development_flight_recorder import development_flight_recorder

from config import (
    AUDIO8_BASE_URL,
    AUDIO8_MODEL,
    AUDIO8_REFERENCE_AUDIO,
    AUDIO8_REFERENCE_TEXT,
    AUDIO8_RUNTIME_ROOT,
    AUDIO8_STARTUP_TIMEOUT_SECONDS,
    AUDIO8_TIMEOUT_SECONDS,
    AUDIO8_VOICE_PROFILE,
    AUDIO8_WARMUP_TEXT,
    KOKORO_DEVICE,
    KOKORO_MODEL_DIR,
    KOKORO_SPEED,
    KOKORO_VOICE,
    TTS_PROVIDER,
)
from tts.audio8_runtime import Audio8Runtime


# ============================================================
# Local Text-to-Speech
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

class LocalPlaybackTTS:
    """Provider-independent local PCM/WAV playback and PTT cancellation path."""

    def __init__(self):

        self._initialize_playback_state()

    def _initialize_playback_state(self):
        """Set up the provider-independent sounddevice playback controls."""

        self.playback_thread = None
        self.playback_generation = 0
        self.playback_generation_lock = threading.Lock()

        self.stop_event = (
            threading.Event()
        )

        self.playback_finished = (
            threading.Event()
        )

        self.playback_finished.set()
        self.playback_started_callback = None
        self.playback_finished_callback = None
        # A generation is both a playback id for presentation clients and the
        # cancellation token for the local audio pipeline.  Keep the active
        # synthesis and active stream separate: neither subtitle timing nor a
        # natural-completion callback may decide whether PTT can interrupt.
        self.playback_state_lock = threading.Lock()
        self.active_synthesis_generation = None
        self.active_playback_generation = None

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        self.volume = 1.0

        self.volume_lock = (
            threading.Lock()
        )

        # ----------------------------------------------------
        # Active stream
        # ----------------------------------------------------

        self.stream = None

        self.stream_lock = (
            threading.Lock()
        )

    def set_playback_started_callback(self, callback):
        """Notify a frontend-neutral owner once local audio output starts."""
        self.playback_started_callback = callback if callable(callback) else None

    def set_playback_finished_callback(self, callback):
        """Notify a frontend-neutral owner when local playback naturally ends."""
        self.playback_finished_callback = callback if callable(callback) else None

    def _notify_playback_started(self, duration_seconds, lip_sync_envelope=None, word_start_seconds=None, playback_id=None):
        callback = self.playback_started_callback
        if callback is None:
            return
        try:
            callback(float(duration_seconds), lip_sync_envelope, list(word_start_seconds or ()), playback_id)
        except TypeError:
            # Existing integrations may still accept the historic two or one
            # callback arguments.
            try:
                callback(float(duration_seconds), lip_sync_envelope)
            except TypeError:
                try:
                    callback(float(duration_seconds))
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            # Presentation notification must never interrupt local playback.
            pass

    def _notify_playback_finished(self, playback_id):
        callback = self.playback_finished_callback
        if callback is None:
            return
        try:
            callback(playback_id)
        except Exception:
            pass

    def _next_playback_generation(self):
        with self.playback_generation_lock:
            self.playback_generation += 1
            return self.playback_generation

    def _is_current_playback_generation(self, generation):
        with self.playback_generation_lock:
            return generation == self.playback_generation

    def playback_debug_state(self):
        """Return a small, non-control diagnostic snapshot for dev logging."""
        with self.playback_state_lock:
            synthesis_generation = self.active_synthesis_generation
            playback_generation = self.active_playback_generation
        with self.playback_generation_lock:
            current_generation = self.playback_generation
        with self.stream_lock:
            stream = self.stream
        thread = self.playback_thread
        return {
            "generation": current_generation,
            "synthesizing": synthesis_generation,
            "playing": playback_generation,
            "stream": stream is not None,
            "thread": bool(thread and thread.is_alive()),
            "cancelled": self.stop_event.is_set(),
        }

    def _mark_synthesis_active(self, generation):
        with self.playback_state_lock:
            self.active_synthesis_generation = generation

    def _retire_synthesis(self, generation):
        with self.playback_state_lock:
            if self.active_synthesis_generation == generation:
                self.active_synthesis_generation = None

    def _mark_playback_active(self, generation):
        with self.playback_state_lock:
            if self.active_synthesis_generation == generation:
                self.active_synthesis_generation = None
            self.active_playback_generation = generation

    def _retire_playback(self, generation):
        with self.playback_state_lock:
            if self.active_playback_generation == generation:
                self.active_playback_generation = None

    @staticmethod
    def build_lip_sync_envelope(audio, sample_rate, samples_per_second=24):
        """Derive a compact RMS envelope from the exact local playback audio."""
        audio = np.asarray(audio, dtype=np.float32)
        if audio.size == 0 or sample_rate <= 0:
            return []
        mono = audio.reshape(-1) if audio.ndim == 1 else np.mean(audio, axis=1)
        frame_size = max(1, int(sample_rate / max(1, samples_per_second)))
        count = int(np.ceil(len(mono) / frame_size))
        envelope = np.empty(count, dtype=np.float32)
        for index in range(count):
            frame = mono[index * frame_size:(index + 1) * frame_size]
            envelope[index] = np.sqrt(np.mean(np.square(frame))) if frame.size else 0.0
        reference = float(np.percentile(envelope, 92)) if envelope.size else 0.0
        if reference <= 1e-5:
            return [0.0] * count
        # Gate low-level noise and cap peaks to avoid jittery mouth movement.
        return np.clip((envelope / reference - .08) / .92, 0.0, 1.0).astype(float).tolist()

    @staticmethod
    def decode_wav_bytes(payload: bytes):
        with wave.open(io.BytesIO(payload), "rb") as wav_file:
            sample_rate, channels, sample_width = wav_file.getframerate(), wav_file.getnchannels(), wav_file.getsampwidth()
            frames = wav_file.readframes(wav_file.getnframes())
        if sample_width != 2:
            raise ValueError("unsupported TTS WAV sample width")
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio.reshape(-1, channels if channels > 1 else 1), sample_rate

    # ========================================================
    # Set Volume
    # ========================================================

    def set_volume(
        self,
        volume
    ):

        try:

            volume = float(
                volume
            )

        except (
            TypeError,
            ValueError
        ):

            return

        volume = max(
            0.0,
            min(
                1.0,
                volume
            )
        )

        with self.volume_lock:

            self.volume = volume

    # ========================================================
    # Get Volume
    # ========================================================

    def get_volume(self):

        with self.volume_lock:

            return self.volume

    # ========================================================
    # Synthesize
    # ========================================================

    def synthesize(
        self,
        text,
        output_file
    ):

        if not text:

            return False

        raise NotImplementedError("TTS provider must implement offline synthesis explicitly.")

    # ========================================================
    # Speak
    # ========================================================

    def speak(
        self,
        text
    ):
        """Speak through a concrete provider; the base owns playback only."""
        raise NotImplementedError("TTS provider must implement speech synthesis.")

    # ========================================================
    # Audio Playback
    # ========================================================

    def _start_playback(self, audio, sample_rate, generation=None, word_start_seconds=None):
        """Start already-synthesized float audio through the shared player."""
        if generation is None:
            generation = self._next_playback_generation()
        elif not self._is_current_playback_generation(generation):
            # PTT or a newer request stopped this synthesis before it reached
            # the audio device. Never resurrect stale speech after an interrupt.
            self.playback_finished.set()
            development_flight_recorder().mark(
                "tts_stale_result_discarded", playback_id=int(generation or 0)
            )
            return False
        if self.stop_event.is_set():
            self._retire_synthesis(generation)
            self.playback_finished.set()
            return False
        recorder = development_flight_recorder()
        buffer_started_at = time.monotonic()
        duration_seconds = len(audio) / float(sample_rate) if sample_rate else 0.0
        lip_sync_envelope = self.build_lip_sync_envelope(audio, sample_rate)
        recorder.mark(
            "audio_buffer_ready", playback_id=int(generation or 0), audio_samples=len(audio),
            sample_rate=int(sample_rate or 0), duration_ms=(time.monotonic() - buffer_started_at) * 1000.0,
        )
        self._mark_playback_active(generation)
        self.playback_thread = (
            threading.Thread(
                target=self._play_audio,
                args=(audio, sample_rate, duration_seconds, lip_sync_envelope, word_start_seconds, generation),
                daemon=True,
            )
        )
        self.playback_thread.start()
        return True

    def _play_audio(
        self,
        audio,
        sample_rate,
        duration_seconds,
        lip_sync_envelope,
        word_start_seconds,
        generation,
    ):

        recorder = development_flight_recorder()
        position = 0
        first_non_silent_reported = False

        # ----------------------------------------------------
        # Keep a reference to the current stream locally.
        # ----------------------------------------------------

        stream = None

        try:

            def callback(
                outdata,
                frames,
                time_info,
                status
            ):

                nonlocal position, first_non_silent_reported

                if status:

                    if getattr(status, "output_underflow", False):
                        recorder.mark("portaudio_underflow", playback_id=int(generation), underflows=1)

                    print(
                        f"\nTTS audio status: {status}"
                    )

                # --------------------------------------------
                # Stop immediately when requested.
                # --------------------------------------------

                if self.stop_event.is_set() or not self._is_current_playback_generation(generation):

                    outdata.fill(
                        0
                    )

                    raise sd.CallbackStop()

                remaining = (
                    len(audio)
                    - position
                )

                count = min(
                    frames,
                    remaining
                )

                if count > 0:

                    # ----------------------------------------
                    # Read the CURRENT volume.
                    #
                    # This is what makes the slider real-time.
                    # ----------------------------------------

                    volume = (
                        self.get_volume()
                    )

                    outdata[
                        :count
                    ] = (
                        audio[
                            position:
                            position + count
                        ]
                        * volume
                    )

                    position += count
                    if not first_non_silent_reported and np.any(np.abs(audio[position - count:position]) > 1e-7):
                        first_non_silent_reported = True
                        recorder.mark("playback_first_non_silent_buffer", playback_id=int(generation))

                # --------------------------------------------
                # Fill any remaining frames with silence.
                # --------------------------------------------

                if count < frames:

                    outdata[
                        count:
                    ].fill(
                        0
                    )

                    raise sd.CallbackStop()

            # ------------------------------------------------
            # Create output stream.
            # ------------------------------------------------

            stream_open_at = time.monotonic()
            recorder.mark("playback_stream_open_begin", playback_id=int(generation))
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=audio.shape[1],
                dtype="float32",
                callback=callback
            )

            recorder.mark("playback_stream_open_end", playback_id=int(generation), duration_ms=(time.monotonic() - stream_open_at) * 1000.0)
            with self.stream_lock:

                self.stream = stream

            stream_start_at = time.monotonic()
            recorder.mark("playback_stream_start_begin", playback_id=int(generation))
            stream.start()
            recorder.mark("playback_stream_start_end", playback_id=int(generation), duration_ms=(time.monotonic() - stream_start_at) * 1000.0)
            # A PTT press can invalidate playback between stream creation and
            # start. Do not emit a false playback_started event or let that
            # stale stream produce another audible callback.
            if self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                try:
                    stream.abort()
                except Exception:
                    pass
                return
            print(f"[AIFren Timing] audio playback started; id={generation}; duration={duration_seconds:.3f}s")
            self._notify_playback_started(duration_seconds, lip_sync_envelope, word_start_seconds, generation)
            recorder.mark("playback_started_callback_sent", playback_id=int(generation), duration_seconds=duration_seconds)

            # ------------------------------------------------
            # Wait until playback finishes or is stopped.
            # ------------------------------------------------

            while stream.active:

                if self.stop_event.is_set() or not self._is_current_playback_generation(generation):

                    break

                sd.sleep(
                    20
                )

        except Exception as e:

            if not self.stop_event.is_set() and self._is_current_playback_generation(generation):

                print(
                    f"\nTTS playback error: {e}"
                )

        finally:

            # ------------------------------------------------
            # Close stream.
            # ------------------------------------------------

            if stream:

                try:

                    stream.stop()

                except Exception:
                    pass

                try:

                    stream.close()

                except Exception:
                    pass

            with self.stream_lock:

                if self.stream is stream:

                    self.stream = None

            naturally_completed = (
                not self.stop_event.is_set()
                and self._is_current_playback_generation(generation)
            )
            recorder.mark("playback_stream_stopped", playback_id=int(generation), cancelled=not naturally_completed)
            self._retire_playback(generation)
            if self.playback_thread is threading.current_thread():
                self.playback_thread = None
            if naturally_completed:
                self.playback_finished.set()
                print(f"[AIFren TTS] natural completion; id={generation}; state={self.playback_debug_state()}")
                self._notify_playback_finished(generation)

    # ========================================================
    # Stop Speaking
    # ========================================================

    def stop(self):

        # Invalidate both active playback and any in-progress synthesis before
        # touching the shared audio stream. A later speak() gets a new token.
        interrupted_generation = self._next_playback_generation()
        development_flight_recorder().mark(
            "tts_cancellation", playback_id=int(interrupted_generation or 0), cancelled=True
        )
        self.stop_event.set()
        with self.playback_state_lock:
            interrupted_synthesis = self.active_synthesis_generation
            interrupted_playback = self.active_playback_generation
            self.active_synthesis_generation = None
            self.active_playback_generation = None
        started_at = time.monotonic()
        print(
            "[AIFren TTS] cancellation requested; "
            f"new_generation={interrupted_generation}; synthesis={interrupted_synthesis}; "
            f"playback={interrupted_playback}"
        )

        # ----------------------------------------------------
        # Stop active stream.
        # ----------------------------------------------------

        with self.stream_lock:

            stream = self.stream

        if stream:

            try:

                stream.abort()

            except Exception:
                pass

        # Do not join the audio worker here. PTT must begin microphone capture
        # immediately; the invalidated worker cleans its own stream up and is
        # forbidden from publishing a natural-completion event.
        thread = self.playback_thread
        if thread is not None and not thread.is_alive():
            self.playback_thread = None

        # ----------------------------------------------------
        # Reset state for next speech.
        # ----------------------------------------------------

        self.playback_finished.set()
        print(f"[AIFren TTS] cancellation dispatched in {(time.monotonic() - started_at) * 1000:.1f}ms")
        return interrupted_playback


class KokoroTextToSpeech(LocalPlaybackTTS):
    """Optional hexgrad Kokoro-82M provider using the shared local player."""

    supports_early_speech = True

    @property
    def synthesis_strategy(self):
        from model_settings import kokoro_early_speech_status
        return "complete_sentences" if kokoro_early_speech_status()["effective"] else "whole_response"

    def __init__(self, voice=KOKORO_VOICE, speed=KOKORO_SPEED, device=KOKORO_DEVICE):
        try:
            import torch
            from kokoro import KPipeline
            from kokoro.model import KModel
            from tts.kokoro_assets import REPOSITORY_ID, require_local_assets
        except ImportError as error:
            raise RuntimeError(
                "Kokoro is not installed. Create an isolated environment with "
                "requirements-kokoro.txt before selecting it."
            ) from error

        self._torch = torch
        self._KPipeline = KPipeline
        self._repo_id = REPOSITORY_ID
        self.voice = str(voice).strip()
        self.speed = float(speed)
        if not self.voice or self.speed <= 0:
            raise ValueError("Kokoro voice must be non-empty and speed must be positive.")
        from tts.device_planning import plan_kokoro_device
        device_plan = plan_kokoro_device(device, torch_module=torch)
        self.device = device_plan.device
        self.device_selection_reason = device_plan.reason
        development_flight_recorder().mark(
            "kokoro_device_selected", device=self.device,
            reason=self.device_selection_reason,
            gpu_total_bytes=device_plan.gpu_total_bytes,
            managed_model_bytes=device_plan.managed_model_bytes,
            device_required_bytes=device_plan.required_total_bytes,
        )
        print("Loading Kokoro TTS...")
        config_path, model_path, self.voice_path = require_local_assets(KOKORO_MODEL_DIR, self.voice)
        model = KModel(repo_id=REPOSITORY_ID, config=str(config_path), model=str(model_path))
        model = model.to(self.device).eval()
        self.pipeline = KPipeline(
            lang_code=self.voice[:1], repo_id=REPOSITORY_ID, model=model, device=self.device
        )
        self._initialize_playback_state()
        self._initialize_continuous_state()
        print("Kokoro TTS loaded.")

    def fallback_to_cpu_after_resource_failure(self) -> bool:
        """Move the loaded model to CPU after repeated CUDA resource pressure."""
        with self._kokoro_synthesis_lock:
            if str(self.device).casefold() == "cpu":
                return False
            pipeline = getattr(self, "pipeline", None)
            model = getattr(pipeline, "model", None)
            if model is None:
                return False
            started_at = time.monotonic()
            model = model.to("cpu").eval()
            self.pipeline = self._KPipeline(
                lang_code=self.voice[:1], repo_id=self._repo_id,
                model=model, device="cpu",
            )
            self.device = "cpu"
            self.device_selection_reason = "runtime_resource_failure_cpu_sticky"
            try:
                self._torch.cuda.empty_cache()
            except Exception:
                pass
            development_flight_recorder().mark(
                "kokoro_runtime_device_fallback", device="cpu",
                reason=self.device_selection_reason,
                duration_ms=(time.monotonic() - started_at) * 1000.0,
            )
            return True

    def _initialize_continuous_state(self):
        self._kokoro_synthesis_lock = threading.Lock()
        self._continuous_condition = threading.Condition()
        self._continuous_chunks = deque()
        self._continuous_closed = False
        self._continuous_generation = None
        self._continuous_sample_rate = None
        self._continuous_max_chunks = 4

    def _generate_audio(self, text, generation=None, *, cancelled=None):
        # Cancellation can retire a turn while a CUDA kernel is still winding
        # down. Serialize at the provider boundary so the replacement turn can
        # never start a second Kokoro inference concurrently.
        while not self._kokoro_synthesis_lock.acquire(timeout=0.05):
            if cancelled is not None and cancelled.is_set():
                return None
        try:
            if cancelled is not None and cancelled.is_set():
                return None
            return self._generate_audio_serial(text, generation, cancelled=cancelled)
        finally:
            self._kokoro_synthesis_lock.release()

    def _generate_audio_serial(self, text, generation=None, *, cancelled=None):
        synthesis_started_at = time.monotonic()
        recorder = development_flight_recorder()
        recorder.mark(
            "kokoro_synthesis_start", playback_id=int(generation or 0), characters=len(str(text or "")),
            words=len(re.findall(r"\S+", str(text or ""))), device=self.device, active_jobs=1,
        )
        chunks = []
        word_starts = []
        offset_seconds = 0.0
        def retired():
            return ((cancelled is not None and cancelled.is_set()) or
                    (generation is not None and (self.stop_event.is_set() or
                     not self._is_current_playback_generation(generation))))

        def discard():
            if generation is not None:
                self._retire_synthesis(generation)
            recorder.mark("tts_stale_result_discarded", playback_id=int(generation or 0))
            recorder.mark("kokoro_synthesis_end", playback_id=int(generation or 0),
                          duration_ms=(time.monotonic()-synthesis_started_at)*1000,
                          cancelled=True, audio_samples=0, active_jobs=0)

        units = (text,)
        if cancelled is not None:
            # Already projected/validated speech only. Reuse the existing
            # sentence/clause policy, with substantial units (not word-sized
            # playback chunks). Concatenate PCM before normal dispatch so no
            # new queue starvation gaps, playback IDs or subtitle clocks arise.
            from tts.chunker import SpeechChunker
            chunker = SpeechChunker(minimum=80, preferred_maximum=220,
                                    hard_maximum=260, preserve_whitespace=True)
            units = chunker.feed(text) + chunker.finish()
        for index, unit in enumerate(units):
            if retired():
                discard()
                return None
            unit_start = time.monotonic()
            unit_samples = 0
            recorder.mark("kokoro_synthesis_unit_start", chunk_index=index, characters=len(unit))
            for result in self.pipeline(unit, voice=str(self.voice_path), speed=self.speed):
                # An in-flight native call is irreducible here. Never request
                # its next yield/unit after cancellation, even in prepare mode.
                if retired():
                    discard()
                    return None
                audio = result.audio
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                chunk = np.asarray(audio, dtype=np.float32).reshape(-1)
                chunks.append(chunk)
                unit_samples += len(chunk)
                word_starts.extend(self._result_word_starts(getattr(result, "tokens", None), offset_seconds))
                offset_seconds += len(chunk) / 24000.0
            recorder.mark("kokoro_synthesis_unit_end", chunk_index=index,
                          duration_ms=(time.monotonic()-unit_start)*1000,
                          audio_samples=unit_samples, sample_rate=24000)
        if retired():
            discard()
            return None
        if not chunks:
            raise ValueError("Kokoro produced no audio.")
        expected_words = re.findall(r"\S+", text or "")
        # Kokoro tokenization can expand or normalize text. Only expose
        # alignment when it still maps one-to-one to the exact spoken words;
        # otherwise callers retain their deterministic fallback schedule.
        if len(word_starts) != len(expected_words):
            word_starts = []
        print(
            "[AIFren Timing] Kokoro synthesis/audio ready "
            f"t={time.monotonic() - synthesis_started_at:.3f}s; "
            f"aligned_words={len(word_starts)}/{len(expected_words)}"
        )
        recorder.mark(
            "kokoro_synthesis_end", playback_id=int(generation or 0),
            duration_ms=(time.monotonic() - synthesis_started_at) * 1000.0,
            audio_samples=sum(len(chunk) for chunk in chunks), sample_rate=24000, active_jobs=0,
        )
        return np.concatenate(chunks).reshape(-1, 1), 24000, word_starts

    @staticmethod
    def _result_word_starts(tokens, offset_seconds):
        starts = []
        for token in tokens or ():
            text = str(getattr(token, "text", "") or "").strip()
            start = getattr(token, "start_ts", None)
            if start is None or not text or not any(character.isalnum() for character in text):
                continue
            # MToken text is lexical for the English Kokoro pipeline; retain
            # one timestamp for every whitespace-delimited spoken word.
            starts.extend(offset_seconds + float(start) for _ in re.findall(r"\S+", text))
        return starts

    def synthesize(self, text, output_file):
        if not text:
            return False
        audio, sample_rate, _ = self._generate_audio(text)
        with wave.open(output_file, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        return True

    def prepare_stream_chunk(self, text: str):
        """Synthesize the next chunk without disturbing current playback."""
        return self._generate_audio(str(text))

    def prepare_cancellable_stream_chunk(self, text: str, *, cancelled):
        """Prepare exact speech with cancellation between bounded native units."""
        return self._generate_audio(str(text), cancelled=cancelled)

    @staticmethod
    def _continuous_chunk(prepared, on_started=None):
        audio, sample_rate, word_starts = prepared
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        if audio.ndim != 2 or audio.shape[1] != 1 or len(audio) == 0 or int(sample_rate) <= 0:
            raise ValueError("Continuous Kokoro playback requires non-empty mono PCM.")
        duration = len(audio) / float(sample_rate) if sample_rate else 0.0
        return {
            "audio": audio,
            "sample_rate": int(sample_rate),
            "word_starts": list(word_starts or ()),
            "duration": duration,
            "envelope": LocalPlaybackTTS.build_lip_sync_envelope(audio, sample_rate),
            "on_started": on_started,
            "position": 0,
            "announced": False,
        }

    def begin_prepared_stream(self, prepared, *, on_started=None) -> bool:
        """Open one queued PCM playback session for this assistant turn."""
        self.stop()
        chunk = self._continuous_chunk(prepared, on_started)
        generation = self._next_playback_generation()
        self.stop_event.clear()
        self.playback_finished.clear()
        with self._continuous_condition:
            self._continuous_chunks.clear()
            self._continuous_chunks.append(chunk)
            self._continuous_closed = False
            self._continuous_generation = generation
            self._continuous_sample_rate = chunk["sample_rate"]
            self._continuous_condition.notify_all()
        self._mark_playback_active(generation)
        self.playback_thread = threading.Thread(
            target=self._play_continuous_audio,
            args=(chunk["sample_rate"], generation),
            name="aifren-tts-continuous-playback",
            daemon=True,
        )
        self.playback_thread.start()
        return True

    def append_prepared_stream(self, prepared, *, on_started=None) -> bool:
        """Append ordered PCM without opening or restarting the audio device."""
        chunk = self._continuous_chunk(prepared, on_started)
        with self._continuous_condition:
            generation = self._continuous_generation
            if (
                generation is None or self._continuous_closed or self.stop_event.is_set()
                or not self._is_current_playback_generation(generation)
                or chunk["sample_rate"] != self._continuous_sample_rate
            ):
                development_flight_recorder().mark(
                    "tts_stale_result_discarded", playback_id=int(generation or 0)
                )
                return False
            while len(self._continuous_chunks) >= self._continuous_max_chunks:
                self._continuous_condition.wait(timeout=0.10)
                if (
                    self._continuous_closed or self.stop_event.is_set()
                    or not self._is_current_playback_generation(generation)
                ):
                    return False
            self._continuous_chunks.append(chunk)
            depth = len(self._continuous_chunks)
            self._continuous_condition.notify_all()
        development_flight_recorder().mark(
            "tts_pcm_queued", playback_id=int(generation), queue_depth=depth,
            audio_samples=len(chunk["audio"]), sample_rate=chunk["sample_rate"],
        )
        return True

    def finish_prepared_stream(self) -> None:
        with self._continuous_condition:
            self._continuous_closed = True
            self._continuous_condition.notify_all()

    def _cancel_continuous_stream(self) -> None:
        with self._continuous_condition:
            self._continuous_closed = True
            self._continuous_chunks.clear()
            self._continuous_generation = None
            self._continuous_sample_rate = None
            self._continuous_condition.notify_all()

    def _play_continuous_audio(self, sample_rate, generation):
        recorder = development_flight_recorder()
        transitions = SimpleQueue()
        current = None
        stream = None

        try:
            def callback(outdata, frames, time_info, status):
                nonlocal current
                if status:
                    if getattr(status, "output_underflow", False):
                        recorder.mark("portaudio_underflow", playback_id=int(generation), underflows=1)
                    print(f"\nTTS audio status: {status}")
                outdata.fill(0)
                if self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                    raise sd.CallbackStop()

                written = 0
                while written < frames:
                    if current is None:
                        with self._continuous_condition:
                            if self._continuous_chunks:
                                current = self._continuous_chunks.popleft()
                                self._continuous_condition.notify_all()
                            elif self._continuous_closed:
                                raise sd.CallbackStop()
                            else:
                                return
                    if not current["announced"]:
                        current["announced"] = True
                        transitions.put(current)
                    available = len(current["audio"]) - current["position"]
                    count = min(frames - written, available)
                    if count > 0:
                        with self.volume_lock:
                            volume = self.volume
                        start = current["position"]
                        outdata[written:written + count] = current["audio"][start:start + count] * volume
                        current["position"] += count
                        written += count
                    if current["position"] >= len(current["audio"]):
                        current = None

            opened_at = time.monotonic()
            recorder.mark("playback_stream_open_begin", playback_id=int(generation))
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            recorder.mark(
                "playback_stream_open_end", playback_id=int(generation),
                duration_ms=(time.monotonic() - opened_at) * 1000.0,
            )
            with self.stream_lock:
                self.stream = stream
            started_at = time.monotonic()
            recorder.mark("playback_stream_start_begin", playback_id=int(generation))
            stream.start()
            recorder.mark(
                "playback_stream_start_end", playback_id=int(generation),
                duration_ms=(time.monotonic() - started_at) * 1000.0,
            )

            first_transition = True
            while stream.active:
                while True:
                    try:
                        chunk = transitions.get_nowait()
                    except Empty:
                        break
                    callback_started = chunk.get("on_started")
                    if callable(callback_started):
                        callback_started()
                    recorder.mark(
                        "playback_chunk_transition", playback_id=int(generation),
                        duration_seconds=chunk["duration"],
                    )
                    if first_transition:
                        first_transition = False
                        recorder.mark("playback_first_non_silent_buffer", playback_id=int(generation))
                    self._notify_playback_started(
                        chunk["duration"], chunk["envelope"], chunk["word_starts"], generation
                    )
                if self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                    break
                sd.sleep(2)

            # A short final buffer can stop the stream before the management
            # loop observes its transition marker.
            while True:
                try:
                    chunk = transitions.get_nowait()
                except Empty:
                    break
                callback_started = chunk.get("on_started")
                if callable(callback_started):
                    callback_started()
                recorder.mark("playback_chunk_transition", playback_id=int(generation))
                self._notify_playback_started(
                    chunk["duration"], chunk["envelope"], chunk["word_starts"], generation
                )
        except Exception as error:
            if not self.stop_event.is_set() and self._is_current_playback_generation(generation):
                print('[AIFren TTS] audio operation failed.')
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            with self.stream_lock:
                if self.stream is stream:
                    self.stream = None
            naturally_completed = (
                not self.stop_event.is_set()
                and self._is_current_playback_generation(generation)
            )
            recorder.mark(
                "playback_stream_stopped", playback_id=int(generation),
                cancelled=not naturally_completed,
            )
            self._retire_playback(generation)
            with self._continuous_condition:
                if self._continuous_generation == generation:
                    self._continuous_generation = None
                    self._continuous_sample_rate = None
                    self._continuous_chunks.clear()
                    self._continuous_closed = True
                    self._continuous_condition.notify_all()
            if self.playback_thread is threading.current_thread():
                self.playback_thread = None
            if naturally_completed:
                self.playback_finished.set()
                self._notify_playback_finished(generation)

    def stop(self):
        interrupted = super().stop()
        self._cancel_continuous_stream()
        return interrupted

    def start_prepared_chunk(self, prepared) -> bool:
        """Start an already-synthesized Kokoro chunk in source order."""
        audio, sample_rate, word_starts = prepared
        generation = self._next_playback_generation()
        self.stop_event.clear()
        self.playback_finished.clear()
        return self._start_playback(audio, sample_rate, generation, word_starts)

    def speak(self, text):
        if not text:
            return False
        self.stop()
        generation = self._next_playback_generation()
        self._mark_synthesis_active(generation)
        self.stop_event.clear()
        self.playback_finished.clear()
        try:
            generated = self._generate_audio(text, generation)
            if generated is None or self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                self._retire_synthesis(generation)
                self.playback_finished.set()
                return False
            audio, sample_rate, word_starts = generated
            return self._start_playback(audio, sample_rate, generation, word_starts)
        except Exception as error:
            self._retire_synthesis(generation)
            self.playback_finished.set()
            print('[AIFren TTS] audio operation failed.')
            return False


class Audio8TextToSpeech(LocalPlaybackTTS):
    """Warm external Audio8 service adapter using its local speech endpoint."""

    synthesis_strategy = "manual_chunks"

    def __init__(self, *, base_url=AUDIO8_BASE_URL, model=AUDIO8_MODEL,
                 reference_audio=AUDIO8_REFERENCE_AUDIO, reference_text=AUDIO8_REFERENCE_TEXT,
                 runtime_root=AUDIO8_RUNTIME_ROOT, voice_profile=AUDIO8_VOICE_PROFILE,
                 timeout_seconds=AUDIO8_TIMEOUT_SECONDS,
                 startup_timeout_seconds=AUDIO8_STARTUP_TIMEOUT_SECONDS,
                 runtime=None):
        super().__init__()
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.voice = str(voice_profile or "default")
        self.reference_audio = str(reference_audio or "")
        self.reference_text = str(reference_text or "")
        self.timeout_seconds = float(timeout_seconds)
        if not self.base_url or not self.model:
            raise ValueError("Audio8 endpoint and model are required")
        self.runtime = runtime or Audio8Runtime(
            base_url=self.base_url,
            model=self.model,
            runtime_root=runtime_root,
            voice_profile=voice_profile,
            reference_audio=self.reference_audio,
            reference_text=self.reference_text,
            timeout_seconds=self.timeout_seconds,
            startup_timeout_seconds=startup_timeout_seconds,
            warmup_text=AUDIO8_WARMUP_TEXT,
        )

    def is_available(self) -> bool:
        """Start/reuse the configured warm service; fail closed into Kokoro."""
        return bool(self.runtime.ensure_ready())

    def _request_wav(self, text: str) -> bytes:
        return self.runtime.request_audio(text)

    def prepare_stream_chunk(self, text: str):
        """Synthesize without touching playback so the next chunk can overlap.

        ``StreamingSpeechQueue`` uses this optional provider capability.  It is
        deliberately Audio8-specific transport optimization, not a change to
        canonical assistant-message or Memory V2 semantics.
        """
        started = time.monotonic()
        audio, sample_rate = self.decode_wav_bytes(self._request_wav(str(text)))
        print(f"[AIFren Timing] Audio8 prepared chunk t={time.monotonic() - started:.3f}s")
        return audio, sample_rate

    def start_prepared_chunk(self, prepared) -> bool:
        """Play a previously synthesized chunk in source order."""
        audio, sample_rate = prepared
        generation = self._next_playback_generation()
        self.stop_event.clear()
        self.playback_finished.clear()
        return self._start_playback(audio, sample_rate, generation)

    def speak(self, text):
        if not text:
            return False
        self.stop()
        generation = self._next_playback_generation()
        self._mark_synthesis_active(generation)
        self.stop_event.clear()
        self.playback_finished.clear()
        started = time.monotonic()
        try:
            audio, sample_rate = self.prepare_stream_chunk(str(text))
            if self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                return False
            print(f"[AIFren Timing] Audio8 synthesis/audio ready t={time.monotonic() - started:.3f}s")
            return self._start_playback(audio, sample_rate, generation)
        except Exception as error:
            self._retire_synthesis(generation)
            self.playback_finished.set()
            print(f"[AIFren TTS] Audio8 synthesis error: {type(error).__name__}")
            return False

    def close(self):
        self.stop()
        self.runtime.shutdown_owned()

    def deactivate(self):
        """Release an owned unhealthy service before loading fallback TTS."""
        self.close()


class FallbackTextToSpeech:
    """Keep one response ordered and instantiate Kokoro only after failure."""
    def __init__(self, primary, fallback=None, *, fallback_factory=None):
        self.primary = primary
        if fallback_factory is None:
            self._fallback_factory = lambda: fallback
        else:
            self._fallback_factory = fallback_factory
        self._fallback = None
        self._fallback_lock = threading.Lock()
        self._active = primary
        self.voice = getattr(primary, "voice", "default")
        self.device = getattr(primary, "device", "local")
        self._playback_started_callback = None
        self._playback_finished_callback = None
        self._volume = primary.get_volume() if callable(getattr(primary, "get_volume", None)) else 1.0

    @property
    def fallback(self):
        return self._ensure_fallback()

    @property
    def fallback_loaded(self):
        return self._fallback is not None

    @property
    def synthesis_strategy(self):
        return getattr(self.primary, "synthesis_strategy", "manual_chunks")

    def _ensure_fallback(self):
        with self._fallback_lock:
            if self._fallback is not None:
                return self._fallback
            deactivate = getattr(self.primary, "deactivate", None)
            if callable(deactivate):
                deactivate()
            fallback = self._fallback_factory()
            if self._playback_started_callback is not None:
                fallback.set_playback_started_callback(self._playback_started_callback)
            if self._playback_finished_callback is not None:
                fallback.set_playback_finished_callback(self._playback_finished_callback)
            setter = getattr(fallback, "set_volume", None)
            if callable(setter):
                setter(self._volume)
            self._fallback = fallback
            return fallback

    @property
    def playback_finished(self):
        return getattr(self._active, "playback_finished", None)

    def set_playback_started_callback(self, callback):
        self._playback_started_callback = callback
        self.primary.set_playback_started_callback(callback)
        if self._fallback is not None:
            self._fallback.set_playback_started_callback(callback)

    def set_playback_finished_callback(self, callback):
        self._playback_finished_callback = callback
        self.primary.set_playback_finished_callback(callback)
        if self._fallback is not None:
            self._fallback.set_playback_finished_callback(callback)

    def set_volume(self, value):
        self._volume = value
        self.primary.set_volume(value)
        if self._fallback is not None:
            self._fallback.set_volume(value)

    def get_volume(self):
        return self.primary.get_volume()

    def stop(self):
        self.primary.stop()
        if self._fallback is not None:
            self._fallback.stop()

    def close(self):
        close = getattr(self.primary, "close", None)
        if callable(close):
            close()
        else:
            self.primary.stop()
        if self._fallback is not None:
            close = getattr(self._fallback, "close", None)
            if callable(close):
                close()
            else:
                self._fallback.stop()

    def prepare_stream_chunk(self, text):
        """Preserve Audio8 overlap while retaining per-chunk Kokoro fallback."""
        prepare = getattr(self.primary, "prepare_stream_chunk", None)
        if not callable(prepare):
            return ("normal", str(text), str(text))
        try:
            return ("primary", prepare(str(text)), str(text))
        except Exception:
            fallback = self._ensure_fallback()
            fallback_prepare = getattr(fallback, "prepare_stream_chunk", None)
            payload = fallback_prepare(str(text)) if callable(fallback_prepare) else str(text)
            return ("fallback", payload, str(text))

    def start_prepared_chunk(self, prepared):
        source, payload, original_text = prepared
        if source == "primary":
            start = getattr(self.primary, "start_prepared_chunk", None)
            if callable(start) and start(payload):
                self._active = self.primary
                return True
        if source == "normal":
            return self.speak(original_text)
        fallback = self._ensure_fallback()
        self._active = fallback
        start = getattr(fallback, "start_prepared_chunk", None)
        return start(payload) if callable(start) else fallback.speak(original_text)

    def fallback_to_cpu_after_resource_failure(self):
        """Expose the same runtime failover when Kokoro is the lazy fallback."""
        fallback = self._ensure_fallback()
        switch = getattr(fallback, "fallback_to_cpu_after_resource_failure", None)
        if not callable(switch):
            return False
        switched = bool(switch())
        if switched:
            self.device = getattr(fallback, "device", "cpu")
        return switched

    def speak(self, text):
        started = self.primary.speak(text)
        if started:
            self._active = self.primary
            return True
        fallback = self._ensure_fallback()
        self._active = fallback
        return fallback.speak(text)


def create_tts_provider(provider=None, fallback=True):
    """Audio8 is primary; Kokoro is the only deterministic fallback."""
    selected = str(provider or TTS_PROVIDER).strip().lower()

    if selected == "audio8":
        try:
            primary = Audio8TextToSpeech()
            if not primary.is_available():
                raise RuntimeError(getattr(primary.runtime, "failure_reason", "Audio8 runtime is unavailable"))
            return FallbackTextToSpeech(primary, fallback_factory=KokoroTextToSpeech)
        except Exception as error:
            if not fallback:
                raise
            message = str(error)
            if "runtime root" in message:
                reason = "Audio8 runtime root is not configured"
            elif "voice profile" in message:
                reason = "Audio8 voice profile is not configured"
            else:
                reason = f"Audio8 unavailable ({type(error).__name__})"
            print(reason + "; falling back to Kokoro.")
            fallback_provider = KokoroTextToSpeech()
            # Status-only diagnostic; no reference paths, prompts, or voice
            # material is ever sent to Unity or canonical records.
            fallback_provider.fallback_reason = reason
            fallback_provider.configured_provider = "audio8"
            return fallback_provider

    if selected == "kokoro":
        try:
            return KokoroTextToSpeech()
        except Exception as error:
            if not fallback:
                raise
            raise RuntimeError(f"Kokoro unavailable: {type(error).__name__}") from error

    raise ValueError(
        f"Unsupported TTS_PROVIDER {selected!r}. Use 'audio8' or 'kokoro'."
    )


class TextToSpeech:
    """Compatibility factory for the longstanding AIFren TTS API."""

    def __new__(cls, provider=None, fallback=True):
        return create_tts_provider(provider=provider, fallback=fallback)
