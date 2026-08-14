import os
import sysconfig


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

        print(
            f"Adding CUDA DLL path: "
            f"{dll_directory}"
        )

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


# ============================================================
# Local Speech-to-Text
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

MODEL_DIR = os.path.join(
    BASE_DIR,
    "models",
    "whisper-small"
)


class SpeechToText:

    def __init__(self):

        print(
            "Loading local STT model on GPU..."
        )

        if not os.path.isdir(
            MODEL_DIR
        ):

            raise FileNotFoundError(
                "\nLocal STT model not found.\n\n"
                "Expected model directory:\n"
                f"{MODEL_DIR}\n"
            )

        self.model = WhisperModel(
            MODEL_DIR,
            device="cuda",
            compute_type="float16"
        )

        print(
            "Local STT model loaded on GPU."
        )

    def transcribe(
        self,
        audio_file
    ):

        segments, info = (
            self.model.transcribe(
                audio_file,
                language="en",
                beam_size=2,
                best_of=1
            )
        )

        text_parts = []

        for segment in segments:

            text = segment.text.strip()

            if text:

                text_parts.append(
                    text
                )

        return " ".join(
            text_parts
        )