import io
import os
import threading
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

        self.stop_event = (
            threading.Event()
        )

        self.playback_finished = (
            threading.Event()
        )

        self.playback_finished.set()
        self.playback_started_callback = None

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

    def _notify_playback_started(self, duration_seconds, lip_sync_envelope=None):
        callback = self.playback_started_callback
        if callback is None:
            return
        try:
            callback(float(duration_seconds), lip_sync_envelope)
        except TypeError:
            # Existing integrations may still accept only a duration value.
            try:
                callback(float(duration_seconds))
            except Exception:
                pass
        except Exception:
            # Presentation notification must never interrupt local playback.
            pass

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

            return self._start_playback(audio, sample_rate)

        except Exception as e:

            self.playback_finished.set()

            print(
                f"\nTTS synthesis error: {e}"
            )

            return False

    # ========================================================
    # Audio Playback
    # ========================================================

    def _start_playback(self, audio, sample_rate):
        """Start already-synthesized float audio through the shared player."""
        duration_seconds = len(audio) / float(sample_rate) if sample_rate else 0.0
        lip_sync_envelope = self.build_lip_sync_envelope(audio, sample_rate)
        self.playback_thread = (
            threading.Thread(
                target=self._play_audio,
                args=(audio, sample_rate, duration_seconds, lip_sync_envelope),
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

                if self.stop_event.is_set():

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
            self._notify_playback_started(duration_seconds, lip_sync_envelope)

            # ------------------------------------------------
            # Wait until playback finishes or is stopped.
            # ------------------------------------------------

            while stream.active:

                if self.stop_event.is_set():

                    break

                sd.sleep(
                    20
                )

        except Exception as e:

            if not self.stop_event.is_set():

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

            self.playback_finished.set()

    # ========================================================
    # Stop Speaking
    # ========================================================

    def stop(self):

        self.stop_event.set()

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

        # ----------------------------------------------------
        # Wait briefly for playback thread.
        # ----------------------------------------------------

        thread = (
            self.playback_thread
        )

        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):

            thread.join(
                timeout=0.25
            )

        self.playback_thread = None

        # ----------------------------------------------------
        # Reset state for next speech.
        # ----------------------------------------------------

        self.stop_event.clear()

        self.playback_finished.set()


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

    def _generate_audio(self, text):
        chunks = []
        for result in self.pipeline(text, voice=str(self.voice_path), speed=self.speed):
            audio = result.audio
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        if not chunks:
            raise ValueError("Kokoro produced no audio.")
        return np.concatenate(chunks).reshape(-1, 1), 24000

    def synthesize(self, text, output_file):
        if not text:
            return False
        audio, sample_rate = self._generate_audio(text)
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
        self.stop_event.clear()
        self.playback_finished.clear()
        try:
            audio, sample_rate = self._generate_audio(text)
            if self.stop_event.is_set():
                self.playback_finished.set()
                return False
            return self._start_playback(audio, sample_rate)
        except Exception as error:
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
