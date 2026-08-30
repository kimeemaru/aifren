import os
import tempfile
import threading
import wave
import time

import numpy as np
import sounddevice as sd

from stt.stt import SpeechToText


# ============================================================
# Voice Input
# ============================================================

SAMPLE_RATE = 16000
CHANNELS = 1

# Maximum amount of time to listen.
MAX_RECORD_SECONDS = 15

# Time of silence required before recording ends.
SILENCE_SECONDS = 1.0

# Time spent measuring microphone background noise.
CALIBRATION_SECONDS = 0.75

# How much louder than the background noise speech must be.
# Higher = less sensitive.
SPEECH_MULTIPLIER = 2.5

# Minimum threshold so very quiet environments don't
# become excessively sensitive.
MIN_SPEECH_THRESHOLD = 150


CHUNK_MS = 100

CHUNK_SAMPLES = int(
    SAMPLE_RATE * CHUNK_MS / 1000
)

# A normal PortAudio input-stream stop completes within a callback interval or
# two.  The real failing capture remained inside InputStream.__exit__ for
# seconds, so 250 ms leaves ample scheduling margin without making PTT appear
# permanently wedged.
PTT_MIC_CLOSE_TIMEOUT_SECONDS = 0.25
PTT_MIC_ABORT_TIMEOUT_SECONDS = 0.25


class MicrophoneShutdownError(RuntimeError):
    """The owned PTT capture stream could not be shut down safely."""

    discard_capture = True


class VoiceInput:

    def __init__(self):

        self._ptt_stage_observer = None

        print(
            "Initializing VoiceInput..."
        )

        self.stt = SpeechToText()

        print(
            "VoiceInput ready."
        )

    def set_ptt_stage_observer(self, observer):
        self._ptt_stage_observer = observer

    def _observe_ptt_stage(self, stage, **metadata):
        observer = getattr(self, "_ptt_stage_observer", None)
        if not callable(observer):
            return
        try:
            observer(stage, **metadata)
        except Exception:
            # Development diagnostics must never affect capture or STT.
            pass

    def record_ptt(
        self,
        is_pressed,
        capture_id=None,
    ):
    
        print()
        print(
            "PTT recording..."
        )
    
        audio_chunks = []

        def observe(stage, **metadata):
            if capture_id is not None:
                metadata["capture_id"] = int(capture_id)
            self._observe_ptt_stage(stage, **metadata)
    
        def callback(
            indata,
            frames,
            time_info,
            status
        ):
    
            if status:
    
                print(
                    f"\nMicrophone status: {status}"
                )
    
            if is_pressed():
    
                audio_chunks.append(
                    indata.copy()
                )
    
        # --------------------------------------------------------
        # Open microphone stream.
        # --------------------------------------------------------
    
        observe("mic_open_begin")
        mic_phase = "open"
        capture_error = None
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=callback
            )
            stream.start()
            observe("mic_open_end")
            observe("mic_capture_started")
            mic_phase = "capture"
            try:
                while is_pressed():
                    sd.sleep(20)
            except Exception as error:
                capture_error = error
                raise
            finally:
                mic_phase = "close"
                observe("mic_close_begin")
                self._close_ptt_stream(stream, observe)
        except Exception as error:
            if capture_error is not None:
                error_stage = "mic_capture_error"
            elif mic_phase == "open":
                error_stage = "mic_open_error"
            else:
                error_stage = "mic_close_error"
            observe(error_stage, error_type=type(error).__name__)
            raise
        else:
            observe("mic_close_end")
    
        print(
            "PTT recording finished."
        )
    
        # --------------------------------------------------------
        # Nothing recorded.
        # --------------------------------------------------------
        observe("captured_audio_prepare_begin")
        try:
            if not audio_chunks:
                observe(
                    "captured_audio_ready", audio_samples=0, byte_count=0, duration_seconds=0.0
                )
                observe("transcription_ready", characters=0, words=0)
                return ""

            # --------------------------------------------------------
            # Combine audio chunks.
            # --------------------------------------------------------

            import numpy as np

            audio = np.concatenate(
                audio_chunks,
                axis=0
            )
        except Exception as error:
            observe("captured_audio_prepare_error", error_type=type(error).__name__)
            raise
        audio_samples = int(audio.shape[0])
        observe(
            "captured_audio_ready",
            audio_samples=audio_samples,
            byte_count=int(audio.nbytes),
            duration_seconds=audio_samples / float(SAMPLE_RATE),
        )
    
        temp_path = None
    
        try:
            observe("wav_prepare_begin")
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".wav",
                    delete=False
                ) as temp_file:
                    temp_path = temp_file.name

                with wave.open(
                    temp_path,
                    "wb"
                ) as wav_file:

                    wav_file.setnchannels(
                        CHANNELS
                    )

                    wav_file.setsampwidth(
                        2
                    )

                    wav_file.setframerate(
                        SAMPLE_RATE
                    )

                    wav_file.writeframes(
                        audio.tobytes()
                    )
            except Exception as error:
                observe("wav_prepare_error", error_type=type(error).__name__)
                raise
            observe("wav_prepare_end")
    
            print(
                "Transcribing..."
            )
    
            whisper_started = time.monotonic()
            observe("whisper_begin")
            try:
                text = self.stt.transcribe(
                    temp_path
                )
            except Exception as error:
                observe("whisper_error", error_type=type(error).__name__)
                raise
            observe(
                "whisper_end", duration_ms=(time.monotonic() - whisper_started) * 1000.0
            )

            result = text.strip()
            observe(
                "transcription_ready",
                characters=len(result),
                words=len(result.split()),
            )
            return result
    
        finally:
    
            if (
                temp_path
                and os.path.exists(
                    temp_path
                )
            ):
    
                try:
    
                    os.remove(
                        temp_path
                    )
    
                except OSError:
    
                    pass

    def _close_ptt_stream(self, stream, observe):
        """Bound PortAudio's synchronous stop/close and abort on a stall.

        ``sounddevice.InputStream.__exit__`` calls ``stop()`` followed by
        ``close()`` on the caller.  Either native call can block that PTT
        worker indefinitely.  Keep that graceful sequence as the normal path,
        but run it under an owned cleanup thread so the recorder worker can
        recover if PortAudio does not return.
        """
        graceful_done = threading.Event()
        graceful_error = []
        phase = {"value": "stop"}

        def graceful_close():
            try:
                observe("mic_stop_begin")
                stream.stop()
                observe("mic_stop_end")
                phase["value"] = "close"
                observe("mic_stream_close_begin")
                stream.close()
                observe("mic_stream_close_end")
            except Exception as error:
                graceful_error.append(error)
            finally:
                graceful_done.set()

        cleanup_thread = threading.Thread(
            target=graceful_close,
            name="AIFren PTT microphone cleanup",
            daemon=True,
        )
        cleanup_thread.start()
        if graceful_done.wait(PTT_MIC_CLOSE_TIMEOUT_SECONDS):
            if graceful_error:
                raise MicrophoneShutdownError("Microphone shutdown failed.") from graceful_error[0]
            return

        observe(
            "mic_close_timeout",
            source=phase["value"],
            duration_ms=PTT_MIC_CLOSE_TIMEOUT_SECONDS * 1000.0,
        )
        observe("mic_abort_begin", source=phase["value"])
        abort_done = threading.Event()
        abort_error = []

        def abort_stream():
            try:
                # PortAudio defines abort as immediate: it discards pending
                # input and is the safe way to release a wedged owned stream.
                stream.abort()
            except Exception as error:
                abort_error.append(error)
            finally:
                abort_done.set()

        abort_thread = threading.Thread(
            target=abort_stream,
            name="AIFren PTT microphone abort",
            daemon=True,
        )
        abort_thread.start()
        if not abort_done.wait(PTT_MIC_ABORT_TIMEOUT_SECONDS):
            observe("mic_abort_error", source=phase["value"], error_type="TimeoutError")
        elif abort_error:
            observe(
                "mic_abort_error",
                source=phase["value"],
                error_type=type(abort_error[0]).__name__,
            )
        else:
            observe("mic_abort_end", source=phase["value"])

        # A successful abort normally releases a blocked stop immediately and
        # lets the same cleanup owner close the stream.  Bound that settle too;
        # no PTT lifecycle state is owned by the daemon cleanup thread.
        graceful_done.wait(PTT_MIC_ABORT_TIMEOUT_SECONDS)
        raise MicrophoneShutdownError("Microphone shutdown timed out; capture discarded.")

    # ========================================================
    # Audio Level
    # ========================================================

    def audio_level(
        self,
        audio
    ):

        if len(audio) == 0:

            return 0

        audio_float = (
            audio.astype(
                np.float32
            )
        )

        return float(
            np.sqrt(
                np.mean(
                    audio_float ** 2
                )
            )
        )

    # ========================================================
    # Microphone Calibration
    # ========================================================

    def calibrate_microphone(
        self,
        stream
    ):

        print(
            "Calibrating microphone..."
        )

        levels = []

        calibration_chunks = int(
            CALIBRATION_SECONDS
            * 1000
            / CHUNK_MS
        )

        for _ in range(
            calibration_chunks
        ):

            audio, overflowed = (
                stream.read(
                    CHUNK_SAMPLES
                )
            )

            audio = (
                audio[:, 0]
            )

            level = (
                self.audio_level(
                    audio
                )
            )

            levels.append(
                level
            )

        if not levels:

            return MIN_SPEECH_THRESHOLD

        # ----------------------------------------------------
        # Use the average background level.
        # ----------------------------------------------------

        background_level = (
            float(
                np.mean(
                    levels
                )
            )
        )

        # ----------------------------------------------------
        # Speech must be significantly louder than
        # the measured background.
        # ----------------------------------------------------

        threshold = max(
            MIN_SPEECH_THRESHOLD,
            background_level
            * SPEECH_MULTIPLIER
        )

        print(
            f"Background level: "
            f"{background_level:.0f}"
        )

        print(
            f"Speech threshold: "
            f"{threshold:.0f}"
        )

        return threshold

    # ========================================================
    # Record
    # ========================================================

    def record(self):

        print()

        print(
            "Listening..."
        )

        recorded_chunks = []

        speech_started = False

        silence_start = None

        start_time = time.time()

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SAMPLES
        ) as stream:

            # ------------------------------------------------
            # Calibrate before listening for speech.
            # ------------------------------------------------

            speech_threshold = (
                self.calibrate_microphone(
                    stream
                )
            )

            print(
                "Speak now."
            )

            # ------------------------------------------------
            # Main recording loop.
            # ------------------------------------------------

            while True:

                elapsed = (
                    time.time()
                    - start_time
                )

                if (
                    elapsed
                    >= MAX_RECORD_SECONDS
                ):

                    print(
                        "Maximum recording time reached."
                    )

                    break

                # ------------------------------------------------
                # Read microphone chunk.
                # ------------------------------------------------

                audio, overflowed = (
                    stream.read(
                        CHUNK_SAMPLES
                    )
                )

                audio = (
                    audio[:, 0]
                )

                level = (
                    self.audio_level(
                        audio
                    )
                )

                # ------------------------------------------------
                # Speech detected.
                # ------------------------------------------------

                if (
                    level
                    >= speech_threshold
                ):

                    if not speech_started:

                        speech_started = True

                        print(
                            "Speech detected."
                        )

                    silence_start = None

                    recorded_chunks.append(
                        audio.copy()
                    )

                # ------------------------------------------------
                # Waiting for speech.
                # ------------------------------------------------

                elif not speech_started:

                    # Don't record the calibration/background
                    # audio before speech begins.
                    continue

                # ------------------------------------------------
                # Speech has started, so monitor silence.
                # ------------------------------------------------

                else:

                    recorded_chunks.append(
                        audio.copy()
                    )

                    if (
                        silence_start
                        is None
                    ):

                        silence_start = (
                            time.time()
                        )

                    silence_duration = (
                        time.time()
                        - silence_start
                    )

                    if (
                        silence_duration
                        >= SILENCE_SECONDS
                    ):

                        print(
                            "Speech finished."
                        )

                        break

        # ========================================================
        # Validate recording
        # ========================================================

        if not recorded_chunks:

            print(
                "No speech detected."
            )

            return ""

        audio = np.concatenate(
            recorded_chunks
        )

        temp_path = None

        try:

            # ----------------------------------------------------
            # Create temporary WAV.
            # ----------------------------------------------------

            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False
            ) as temp_file:

                temp_path = (
                    temp_file.name
                )

            with wave.open(
                temp_path,
                "wb"
            ) as wav_file:

                wav_file.setnchannels(
                    CHANNELS
                )

                wav_file.setsampwidth(
                    2
                )

                wav_file.setframerate(
                    SAMPLE_RATE
                )

                wav_file.writeframes(
                    audio.tobytes()
                )

            print(
                "Transcribing..."
            )

            # ----------------------------------------------------
            # Whisper
            # ----------------------------------------------------

            text = (
                self.stt.transcribe(
                    temp_path
                )
            )

            return text.strip()

        finally:

            if (
                temp_path
                and os.path.exists(
                    temp_path
                )
            ):

                try:

                    os.remove(
                        temp_path
                    )

                except OSError:

                    pass

    # ========================================================
    # Listen
    # ========================================================

    def listen(self):

        try:

            return self.record()

        except Exception as e:

            print(
                f"\nVoice input error: {e}"
            )

            return ""
