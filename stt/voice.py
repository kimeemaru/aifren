import os
import tempfile
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


class VoiceInput:

    def __init__(self):

        print(
            "Initializing VoiceInput..."
        )

        self.stt = SpeechToText()

        print(
            "VoiceInput ready."
        )

    def record_ptt(
        self,
        is_pressed
    ):
    
        print()
        print(
            "PTT recording..."
        )
    
        audio_chunks = []
    
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
    
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=callback
        ):
    
            while is_pressed():
    
                sd.sleep(20)
    
        print(
            "PTT recording finished."
        )
    
        # --------------------------------------------------------
        # Nothing recorded.
        # --------------------------------------------------------
    
        if not audio_chunks:
    
            return ""
    
        # --------------------------------------------------------
        # Combine audio chunks.
        # --------------------------------------------------------
    
        import numpy as np
    
        audio = np.concatenate(
            audio_chunks,
            axis=0
        )
    
        temp_path = None
    
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
    
            print(
                "Transcribing..."
            )
    
            text = self.stt.transcribe(
                temp_path
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