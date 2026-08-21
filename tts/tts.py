import io
import os
import re
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from piper import PiperVoice

from config import (
    KOKORO_DEVICE,
    KOKORO_MODEL_DIR,
    KOKORO_SPEED,
    KOKORO_VOICE,
    TTS_PROVIDER,
    TTS_VOICE,
)


# ============================================================
# Local Text-to-Speech
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

VOICE_FILE = os.path.join(
    BASE_DIR,
    TTS_VOICE
)


class PiperTextToSpeech:
    """The established Piper implementation and shared local playback path."""

    def __init__(self):

        print(
            "Loading local TTS voice..."
        )

        if not os.path.isfile(
            VOICE_FILE
        ):

            raise FileNotFoundError(
                "\nTTS voice model not found.\n\n"
                "Expected:\n"
                f"{VOICE_FILE}\n"
            )

        self.voice = PiperVoice.load(
            VOICE_FILE
        )

        self._initialize_playback_state()

        print(
            "Local TTS voice loaded."
        )

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

        with wave.open(
            output_file,
            "wb"
        ) as wav_file:

            self.voice.synthesize_wav(
                text,
                wav_file
            )

        return True

    # ========================================================
    # Speak
    # ========================================================

    def speak(
        self,
        text
    ):

        if not text:

            return False

        # ----------------------------------------------------
        # Stop anything currently playing.
        # ----------------------------------------------------

        self.stop()
        generation = self._next_playback_generation()
        self._mark_synthesis_active(generation)

        self.stop_event.clear()

        self.playback_finished.clear()

        # ----------------------------------------------------
        # Generate WAV in memory.
        # ----------------------------------------------------

        try:

            wav_buffer = (
                io.BytesIO()
            )

            with wave.open(
                wav_buffer,
                "wb"
            ) as wav_file:

                self.voice.synthesize_wav(
                    text,
                    wav_file
                )

            wav_buffer.seek(0)

            with wave.open(
                wav_buffer,
                "rb"
            ) as wav_file:

                sample_rate = (
                    wav_file.getframerate()
                )

                channels = (
                    wav_file.getnchannels()
                )

                sample_width = (
                    wav_file.getsampwidth()
                )

                frames = (
                    wav_file.readframes(
                        wav_file.getnframes()
                    )
                )

            if sample_width != 2:

                raise ValueError(
                    "Unsupported Piper audio format."
                )

            # ------------------------------------------------
            # Convert to float32.
            #
            # We keep the original audio at full scale and
            # apply volume dynamically during playback.
            # ------------------------------------------------

            audio = np.frombuffer(
                frames,
                dtype=np.int16
            ).astype(
                np.float32
            )

            audio /= 32768.0

            if channels > 1:

                audio = audio.reshape(
                    -1,
                    channels
                )

            else:

                audio = audio.reshape(
                    -1,
                    1
                )

            if self.stop_event.is_set() or not self._is_current_playback_generation(generation):
                self._retire_synthesis(generation)
                self.playback_finished.set()
                return False
            return self._start_playback(audio, sample_rate, generation)

        except Exception as e:

            self._retire_synthesis(generation)
            self.playback_finished.set()

            print(
                f"\nTTS synthesis error: {e}"
            )

            return False

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
            return False
        if self.stop_event.is_set():
            self._retire_synthesis(generation)
            self.playback_finished.set()
            return False
        duration_seconds = len(audio) / float(sample_rate) if sample_rate else 0.0
        lip_sync_envelope = self.build_lip_sync_envelope(audio, sample_rate)
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

        position = 0

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

                nonlocal position

                if status:

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

            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=audio.shape[1],
                dtype="float32",
                callback=callback
            )

            with self.stream_lock:

                self.stream = stream

            stream.start()
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


class KokoroTextToSpeech(PiperTextToSpeech):
    """Optional hexgrad Kokoro-82M provider using the shared local player."""

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
        self.voice = str(voice).strip()
        self.speed = float(speed)
        if not self.voice or self.speed <= 0:
            raise ValueError("Kokoro voice must be non-empty and speed must be positive.")
        self.device = (
            "cuda" if str(device).lower() == "auto" and torch.cuda.is_available()
            else "cpu" if str(device).lower() == "auto" else str(device)
        )
        print(f"Loading Kokoro TTS on {self.device} with voice {self.voice}...")
        config_path, model_path, self.voice_path = require_local_assets(KOKORO_MODEL_DIR, self.voice)
        model = KModel(repo_id=REPOSITORY_ID, config=str(config_path), model=str(model_path))
        model = model.to(self.device).eval()
        self.pipeline = KPipeline(
            lang_code=self.voice[:1], repo_id=REPOSITORY_ID, model=model, device=self.device
        )
        self._initialize_playback_state()
        print("Kokoro TTS loaded.")

    def _generate_audio(self, text, generation=None):
        synthesis_started_at = time.monotonic()
        chunks = []
        word_starts = []
        offset_seconds = 0.0
        for result in self.pipeline(text, voice=str(self.voice_path), speed=self.speed):
            # Kokoro yields incrementally. A PTT interruption cannot always
            # preempt work already inside a model kernel, but it must prevent
            # all later chunks and any stale audio from reaching the player.
            if generation is not None and (
                self.stop_event.is_set()
                or not self._is_current_playback_generation(generation)
            ):
                self._retire_synthesis(generation)
                print(f"[AIFren TTS] synthesis cancelled; id={generation}")
                return None
            audio = result.audio
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunk = np.asarray(audio, dtype=np.float32).reshape(-1)
            chunks.append(chunk)
            word_starts.extend(self._result_word_starts(getattr(result, "tokens", None), offset_seconds))
            offset_seconds += len(chunk) / 24000.0
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
            print(f"\nKokoro TTS synthesis error: {error}")
            return False


def create_tts_provider(provider=None, fallback=True):
    """Create a configured provider while preserving Piper as a safe fallback."""
    selected = str(provider or TTS_PROVIDER).strip().lower()

    if selected == "piper":
        return PiperTextToSpeech()

    if selected == "kokoro":
        try:
            return KokoroTextToSpeech()
        except Exception as error:
            if not fallback:
                raise
            print(f"Kokoro unavailable ({error}); falling back to Piper.")
            return PiperTextToSpeech()

    raise ValueError(
        f"Unsupported TTS_PROVIDER {selected!r}. Use 'piper' or 'kokoro'."
    )


class TextToSpeech:
    """Compatibility factory for the longstanding AIFren TTS API."""

    def __new__(cls, provider=None, fallback=True):
        return create_tts_provider(provider=provider, fallback=fallback)
