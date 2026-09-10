from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tts.device_planning import plan_kokoro_device


class _Cuda:
    def __init__(self, available, total=0):
        self._available = available
        self._total = total

    def is_available(self):
        return self._available

    def get_device_properties(self, _index):
        return SimpleNamespace(total_memory=self._total)


class TTSDevicePlanningTests(unittest.TestCase):
    def test_explicit_override_is_always_respected(self):
        plan = plan_kokoro_device(
            "cuda:1", torch_module=SimpleNamespace(cuda=_Cuda(False)),
            settings={}, model_directory="/unused",
        )
        self.assertEqual(("cuda:1", "configured_device_override"), (
            plan.device, plan.reason,
        ))

    def test_auto_uses_cpu_without_cuda(self):
        plan = plan_kokoro_device(
            "auto", torch_module=SimpleNamespace(cuda=_Cuda(False)),
            settings={}, model_directory="/unused",
        )
        self.assertEqual(("cpu", "accelerator_unavailable"), (
            plan.device, plan.reason,
        ))

    def test_current_eight_gib_managed_model_shape_selects_cpu_before_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "managed.gguf"
            with model.open("wb") as handle:
                handle.truncate(5_335_285_728)
            plan = plan_kokoro_device(
                "auto",
                torch_module=SimpleNamespace(cuda=_Cuda(True, 8_214_085_632)),
                settings={
                    "mode": "local", "local_auto_start": True,
                    "local_model": model.name,
                },
                model_directory=directory,
            )
        self.assertEqual("cpu", plan.device)
        self.assertEqual("managed_local_gpu_headroom_insufficient", plan.reason)
        self.assertGreater(plan.required_total_bytes, plan.gpu_total_bytes)

    def test_larger_gpu_is_not_unnecessarily_forced_to_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "managed.gguf"
            with model.open("wb") as handle:
                handle.truncate(5_335_285_728)
            plan = plan_kokoro_device(
                "auto",
                torch_module=SimpleNamespace(cuda=_Cuda(True, 24 * 1024**3)),
                settings={
                    "mode": "local", "local_auto_start": True,
                    "local_model": model.name,
                },
                model_directory=directory,
            )
        self.assertEqual("cuda", plan.device)
        self.assertEqual("managed_local_gpu_headroom_sufficient", plan.reason)

    def test_external_provider_keeps_cuda_available_for_tts(self):
        plan = plan_kokoro_device(
            "auto", torch_module=SimpleNamespace(cuda=_Cuda(True, 8 * 1024**3)),
            settings={"mode": "online", "local_auto_start": False},
            model_directory="/unused",
        )
        self.assertEqual("cuda", plan.device)
        self.assertEqual(
            "accelerator_available_without_managed_local_llm", plan.reason,
        )


if __name__ == "__main__":
    unittest.main()
