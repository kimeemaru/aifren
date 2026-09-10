import os
import wave
import threading
import queue

import numpy as np
import sounddevice as sd


# ============================================================
# UI Typewriter Sound
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

SOUND_FILE = os.path.join(
    BASE_DIR,
    "sounds",
    "text_blip.wav"
)


class UISound:

    def __init__(self):

        print(
            "Loading UI blip sound..."
        )

        if not os.path.isfile(
            SOUND_FILE
        ):

            raise FileNotFoundError(
                "\nUI blip sound not found.\n\n"
                "Expected:\n"
                f"{SOUND_FILE}\n"
            )

        with wave.open(
            SOUND_FILE,
            "rb"
        ) as wav_file:

            self.sample_rate = (
                wav_file.getframerate()
            )

            self.channels = (
                wav_file.getnchannels()
            )

            self.sample_width = (
                wav_file.getsampwidth()
            )

            frames = (
                wav_file.readframes(
                    wav_file.getnframes()
                )
            )

        if self.sample_width != 2:

            raise ValueError(
                "text_blip.wav must be "
                "16-bit PCM."
            )

        audio = np.frombuffer(
            frames,
            dtype=np.int16
        )

        if self.channels > 1:

            audio = audio.reshape(
                -1,
                self.channels
            )

        else:

            audio = audio.reshape(
                -1,
                1
            )

        self.audio = audio

        # ----------------------------------------------------
        # Queue of sounds waiting to play.
        # ----------------------------------------------------

        self.queue = queue.Queue(
            maxsize=32
        )

        self.running = True

        # ----------------------------------------------------
        # Persistent audio stream.
        # ----------------------------------------------------

        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16"
        )

        self.stream.start()

        # ----------------------------------------------------
        # Background playback thread.
        # ----------------------------------------------------

        self.thread = threading.Thread(
            target=self._playback_loop,
            daemon=True
        )

        self.thread.start()

        print(
            "UI blip sound loaded."
        )

    # ========================================================
    # Playback Thread
    # ========================================================

    def _playback_loop(self):

        while self.running:

            try:

                sound = (
                    self.queue.get(
                        timeout=0.1
                    )
                )

            except queue.Empty:

                continue

            try:

                self.stream.write(
                    sound
                )

            except Exception as e:

                if self.running:

                    print(
                        f"\nUI sound playback error: {e}"
                    )

            finally:

                self.queue.task_done()

    # ========================================================
    # Play
    # ========================================================

    def play(self):

        if not self.running:

            return

        try:

            # ------------------------------------------------
            # Don't let a long response build an enormous
            # queue of sounds.
            # ------------------------------------------------

            self.queue.put_nowait(
                self.audio.copy()
            )

        except queue.Full:

            # If the queue is full, simply skip this blip.
            # The text should never be delayed because of audio.
            pass

    # ========================================================
    # Shutdown
    # ========================================================

    def close(self):

        self.running = False

        try:

            self.thread.join(
                timeout=0.5
            )

        except Exception:

            pass

        try:

            self.stream.stop()
            self.stream.close()

        except Exception:

            pass