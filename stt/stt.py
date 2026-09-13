import os
import sysconfig
import gc
import threading


# ============================================================
# NVIDIA CUDA DLL Setup
# ============================================================

# Ask Python where its actual site-packages directory is.
SITE_PACKAGES = sysconfig.get_paths()["purelib"]

NVIDIA_DIR = os.path.join(
    SITE_PACKAGES,
    "nvidia"
)


# ============================================================
# CUDA DLL directories
# ============================================================

CUDA_PACKAGES = [
    "cublas",
    "cudnn",
    "cuda_runtime"
]


for package in CUDA_PACKAGES:

    dll_directory = os.path.join(
        NVIDIA_DIR,
        package,
        "bin"
    )

    if os.path.isdir(
        dll_directory
    ):

        print('[AIFren STT] native library path configured.')

        # Windows DLL search path
        if hasattr(
            os,
            "add_dll_directory"
        ):

            os.add_dll_directory(
                dll_directory
            )

        # Also add it to PATH
        os.environ["PATH"] = (
            dll_directory
            + os.pathsep
            + os.environ.get(
                "PATH",
                ""
            )
        )


# ============================================================
# Whisper
# ============================================================

from faster_whisper import WhisperModel

from development_flight_recorder import development_flight_recorder


# ============================================================
# Local Speech-to-Text
# ============================================================

from runtime_layout import resource_path

MODEL_DIR = str(resource_path("models/whisper-small"))


class SpeechToText:

    def __init__(self):

        print(
            "Loading local STT model on GPU..."
        )

        if not os.path.isdir(
            MODEL_DIR
        ):

            raise FileNotFoundError("Local STT model is unavailable.")

        self._model_lock = threading.Lock()
        self._device = "cuda"
        self._compute_type = "float16"
        self.model = self._create_model(self._device, self._compute_type)

        print(
            "Local STT model loaded on GPU."
        )

    @staticmethod
    def _create_model(device, compute_type):
        return WhisperModel(
            MODEL_DIR,
            device=device,
            compute_type=compute_type,
        )

    @staticmethod
    def _cuda_fallback_reason(error):
        """Return a bounded reason code only for observed CUDA resource faults."""
        if not isinstance(error, RuntimeError):
            return None
        message = str(error).casefold()
        if "cuda" not in message:
            return None
        if "out of memory" in message:
            return "cuda_out_of_memory"
        if "invalid device" in message or "cudaerrorinvaliddevice" in message:
            return "cuda_invalid_device"
        return None

    @staticmethod
    def _transcribe_once(model, audio_file):
        segments, _info = model.transcribe(
            audio_file,
            language="en",
            beam_size=2,
            best_of=1,
        )

        text_parts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                text_parts.append(text)
        return " ".join(text_parts)

    def _switch_to_cpu(self, reason):
        """Permanently retire CUDA Whisper and install the session CPU model."""
        with self._model_lock:
            if self._device == "cpu":
                if self.model is None:
                    raise RuntimeError("CPU speech-to-text fallback is unavailable.")
                return self.model

            self._device = "cpu"
            self._compute_type = "int8"
            cuda_model = self.model
            self.model = None
            del cuda_model
            gc.collect()

            development_flight_recorder().mark(
                "whisper_cuda_cpu_fallback",
                source="stt",
                reason=reason,
                device="cpu",
                compute="int8",
            )
            print(
                "CUDA Whisper resource failure; switching this session to "
                f"CPU int8 ({reason})."
            )
            try:
                self.model = self._create_model("cpu", "int8")
            except Exception as error:
                development_flight_recorder().mark(
                    "whisper_cpu_fallback_error",
                    source="stt",
                    reason=reason,
                    device="cpu",
                    compute="int8",
                    error_type=type(error).__name__,
                )
                raise
            development_flight_recorder().mark(
                "whisper_cpu_fallback_ready",
                source="stt",
                reason=reason,
                device="cpu",
                compute="int8",
            )
            return self.model

    def transcribe(
        self,
        audio_file
    ):

        with self._model_lock:
            model = self.model
            device = self._device
        if model is None:
            raise RuntimeError("Speech-to-text model is unavailable.")

        try:
            return self._transcribe_once(model, audio_file)
        except Exception as error:
            reason = self._cuda_fallback_reason(error) if device == "cuda" else None
            if reason is None:
                raise

        # The WAV remains owned by VoiceInput until this call returns. Retry
        # that exact capture once after a one-way session fallback; never
        # reopen the microphone or emit an intermediate transcription.
        cpu_model = self._switch_to_cpu(reason)
        return self._transcribe_once(cpu_model, audio_file)
