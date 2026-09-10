import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from stt.stt import SpeechToText


class SpeechToTextFallbackTests(unittest.TestCase):
    def setUp(self):
        # The decoder is mocked below; model presence is synthetic and local.
        directory = tempfile.TemporaryDirectory(prefix="synthetic-stt-model-")
        self.addCleanup(directory.cleanup)
        model_path = patch("stt.stt.MODEL_DIR", directory.name)
        model_path.start()
        self.addCleanup(model_path.stop)

    def test_missing_model_stays_an_explicit_error_without_download(self):
        with patch("stt.stt.MODEL_DIR", "/synthetic-missing-stt-model"), patch("stt.stt.WhisperModel") as model:
            with self.assertRaises(FileNotFoundError): SpeechToText()
            model.assert_not_called()

    @staticmethod
    def _segments(text):
        return ([SimpleNamespace(text=text)], object())

    def test_cuda_oom_retries_same_wav_once_on_cpu_and_stays_on_cpu(self):
        calls = []
        diagnostics = []

        class Recorder:
            @staticmethod
            def mark(event, **metadata):
                diagnostics.append((event, metadata))

        class FakeModel:
            def __init__(self, _path, *, device, compute_type):
                calls.append(("create", device, compute_type))
                self.device = device

            def transcribe(self, path, **_kwargs):
                calls.append(("transcribe", self.device, path))
                if self.device == "cuda":
                    raise RuntimeError("CUDA failed with error out of memory")
                return SpeechToTextFallbackTests._segments(" recovered ")

        with patch("stt.stt.WhisperModel", FakeModel), patch(
            "stt.stt.development_flight_recorder", return_value=Recorder(),
        ):
            stt = SpeechToText()
            self.assertEqual("recovered", stt.transcribe("same.wav"))
            self.assertEqual("recovered", stt.transcribe("next.wav"))

        self.assertEqual([
            ("create", "cuda", "float16"),
            ("transcribe", "cuda", "same.wav"),
            ("create", "cpu", "int8"),
            ("transcribe", "cpu", "same.wav"),
            ("transcribe", "cpu", "next.wav"),
        ], calls)
        self.assertEqual(
            ["whisper_cuda_cpu_fallback", "whisper_cpu_fallback_ready"],
            [event for event, _metadata in diagnostics],
        )
        self.assertEqual("cuda_out_of_memory", diagnostics[0][1]["reason"])
        self.assertEqual("cpu", diagnostics[0][1]["device"])
        self.assertEqual("int8", diagnostics[0][1]["compute"])

    def test_invalid_device_is_a_recognized_cuda_fallback(self):
        models = []

        class FakeModel:
            def __init__(self, _path, *, device, compute_type):
                self.device = device
                models.append((device, compute_type))

            def transcribe(self, _path, **_kwargs):
                if self.device == "cuda":
                    raise RuntimeError(
                        "parallel_for failed: cudaErrorInvalidDevice: invalid device ordinal"
                    )
                return SpeechToTextFallbackTests._segments("okay")

        with patch("stt.stt.WhisperModel", FakeModel):
            stt = SpeechToText()
            self.assertEqual("okay", stt.transcribe("capture.wav"))
        self.assertEqual([("cuda", "float16"), ("cpu", "int8")], models)

    def test_arbitrary_stt_error_does_not_fallback_or_retry(self):
        calls = []

        class FakeModel:
            def __init__(self, _path, *, device, compute_type):
                calls.append(("create", device, compute_type))

            def transcribe(self, path, **_kwargs):
                calls.append(("transcribe", path))
                raise RuntimeError("decoder failed")

        with patch("stt.stt.WhisperModel", FakeModel):
            stt = SpeechToText()
            with self.assertRaisesRegex(RuntimeError, "decoder failed"):
                stt.transcribe("capture.wav")
        self.assertEqual([
            ("create", "cuda", "float16"),
            ("transcribe", "capture.wav"),
        ], calls)

    def test_cpu_retry_failure_is_not_retried_again(self):
        calls = []

        class FakeModel:
            def __init__(self, _path, *, device, compute_type):
                self.device = device
                calls.append(("create", device, compute_type))

            def transcribe(self, path, **_kwargs):
                calls.append(("transcribe", self.device, path))
                if self.device == "cuda":
                    raise RuntimeError("CUDA failed with error out of memory")
                raise RuntimeError("CPU decoder failed")

        with patch("stt.stt.WhisperModel", FakeModel):
            stt = SpeechToText()
            with self.assertRaisesRegex(RuntimeError, "CPU decoder failed"):
                stt.transcribe("capture.wav")
        self.assertEqual(2, len([item for item in calls if item[0] == "transcribe"]))

    def test_cpu_initialization_failure_retires_cuda_for_session(self):
        calls = []

        class FakeModel:
            def __init__(self, _path, *, device, compute_type):
                calls.append((device, compute_type))
                if device == "cpu":
                    raise RuntimeError("CPU init failed")
                self.device = device

            def transcribe(self, _path, **_kwargs):
                raise RuntimeError("CUDA failed with error out of memory")

        with patch("stt.stt.WhisperModel", FakeModel):
            stt = SpeechToText()
            with self.assertRaisesRegex(RuntimeError, "CPU init failed"):
                stt.transcribe("capture.wav")
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                stt.transcribe("later.wav")
        self.assertEqual([("cuda", "float16"), ("cpu", "int8")], calls)


if __name__ == "__main__":
    unittest.main()
