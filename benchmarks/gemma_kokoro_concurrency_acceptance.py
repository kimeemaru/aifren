"""Owned, non-GUI Gemma/Kokoro lossless-order acceptance with synthetic text."""

from __future__ import annotations

import argparse
import atexit
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR  # noqa: E402
from development_flight_recorder import development_flight_recorder  # noqa: E402
from llm.openai_compatible import OpenAICompatibleLLM  # noqa: E402
from local_model_runtime import LocalModelRuntime  # noqa: E402
from model_settings import get_model_settings  # noqa: E402
from tts.streaming import StreamingSpeechQueue  # noqa: E402
from tts.tts import KokoroTextToSpeech  # noqa: E402


def _vram_used_mib() -> int | None:
    try:
        value = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()[0]
        return int(value.strip())
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class _SynthesisOnly:
    """Run real Kokoro inference without opening the desktop audio device."""
    def __init__(self, provider, *, fail_once_at: int | None = None,
                 fail_at: set[int] | None = None):
        self.provider = provider
        self.playback_finished = threading.Event(); self.playback_finished.set()
        self.prepared_indices: list[int] = []
        self.played_indices: list[int] = []
        self.calls = 0
        self.fail_once_at = fail_once_at
        self.fail_at = set(fail_at or ())
        self.index_by_text: dict[str, int] = {}

    def prepare_stream_chunk(self, text):
        call_index = self.calls
        self.calls += 1
        index = self.index_by_text.setdefault(str(text), len(self.index_by_text))
        if self.fail_once_at == call_index:
            self.fail_once_at = None
            raise RuntimeError("synthetic CUDA resource contention")
        if call_index in self.fail_at:
            raise RuntimeError("synthetic CUDA out of memory")
        prepared = self.provider.prepare_stream_chunk(text)
        self.prepared_indices.append(index)
        return (index, prepared)

    def start_prepared_chunk(self, prepared):
        self.played_indices.append(int(prepared[0])); self.playback_finished.set(); return True

    def stop(self):
        self.provider.stop(); self.playback_finished.set()

    def fallback_to_cpu_after_resource_failure(self):
        return self.provider.fallback_to_cpu_after_resource_failure()


def run(model: str) -> dict[str, object]:
    settings = get_model_settings()
    endpoint = str(settings["local_endpoint"])
    runtime = LocalModelRuntime(ROOT, model_directory=LOCAL_LLM_MODEL_DIR,
                                context_size=LOCAL_LLM_CONTEXT_SIZE)
    status = runtime.start(endpoint=endpoint, selected_model=model,
                           api_key=str(settings.get("local_api_key", "")))
    if status.get("state") != "ready":
        raise RuntimeError("managed local model did not become ready")
    cleanup = atexit.register(runtime.stop) if status.get("ownership") == "managed" else None
    generating = threading.Event()
    generation_done = threading.Event()
    recorder = development_flight_recorder()
    recorder.start(unity_pid=0, state_provider=lambda: {
        "qwen_generating": generating.is_set(),
        "kokoro_synthesizing": False,
        "llama_pid": int(status.get("pid", 0) or 0),
    })
    provider = OpenAICompatibleLLM(
        api_key=str(settings.get("local_api_key", "")), base_url=endpoint, model=model,
    )
    generation_error: list[str] = []

    def generate() -> None:
        try:
            generating.set()
            list(provider.stream_generate(
                [{"role": "user", "content": "Write a synthetic six-paragraph story about a clockmaker and a paper bird."}],
                "This is a synthetic performance test. Produce about 300 words.", seed=20260827,
            ))
        except Exception as error:
            generation_error.append(type(error).__name__)
        finally:
            generating.clear(); generation_done.set()

    before = _vram_used_mib()
    thread = threading.Thread(target=generate, name="synthetic-gemma-generation", daemon=True)
    thread.start()
    if not generating.wait(5):
        raise RuntimeError("generation did not start")
    kokoro = KokoroTextToSpeech()
    real = _SynthesisOnly(kokoro)
    failures: list[str] = []
    started = time.monotonic()
    queue = StreamingSpeechQueue(real, on_failure=failures.append,
                                 provider_generation_active=generating.is_set)
    chunks = ("The clockmaker opened the quiet workshop.",
              "A paper bird rustled beside the brass gears.",
              "Together they listened for the final chime.")
    for chunk in chunks: queue.submit(chunk)
    queue.close(); queue.join(90); thread.join(90)
    real_ok = real.played_indices == [0, 1, 2] and not failures and not generation_error

    forced = _SynthesisOnly(kokoro, fail_once_at=1)
    forced_failures: list[str] = []
    retry_queue = StreamingSpeechQueue(forced, on_failure=forced_failures.append,
                                       provider_generation_active=lambda: False)
    for chunk in chunks: retry_queue.submit(chunk)
    retry_queue.close(); retry_queue.join(90)
    retry_ok = forced.played_indices == [0, 1, 2] and forced.calls == 4 and not forced_failures

    cpu_forced = _SynthesisOnly(kokoro, fail_at={1, 2})
    cpu_failures: list[str] = []
    cpu_started = time.monotonic()
    cpu_queue = StreamingSpeechQueue(
        cpu_forced, on_failure=cpu_failures.append,
        provider_generation_active=lambda: False,
    )
    for chunk in chunks: cpu_queue.submit(chunk)
    cpu_queue.close(); cpu_queue.join(120)
    cpu_elapsed = time.monotonic() - cpu_started
    cpu_ok = (
        cpu_forced.played_indices == [0, 1, 2]
        and cpu_forced.calls == 5 and not cpu_failures
        and kokoro.device == "cpu"
    )
    after = _vram_used_mib()
    diagnostic_events = [
        item for item in recorder._events
        if item.get("event") in {
            "tts_synthesis_failure", "tts_synthesis_retry_wait",
            "tts_synthesis_retry_result", "tts_synthesis_sequence_failure",
        }
    ]
    recorder.stop()
    if status.get("ownership") == "managed":
        runtime.stop()
        atexit.unregister(cleanup)
    return {
        "model": model, "runtime_compute": status.get("compute"), "kokoro_device": kokoro.device,
        "real_concurrent_ordered": real_ok, "forced_retry_ordered": retry_ok,
        "forced_cpu_fallback_ordered": cpu_ok,
        "cpu_fallback_elapsed_seconds": round(cpu_elapsed, 3),
        "real_failures": failures, "forced_failures": forced_failures,
        "forced_cpu_failures": cpu_failures,
        "generation_errors": generation_error, "elapsed_seconds": round(time.monotonic() - started, 3),
        "vram_before_mib": before, "vram_peak_sample_mib": after,
        "diagnostics": {
            "failure_events": sum(item.get("event") == "tts_synthesis_failure" for item in diagnostic_events),
            "retry_scheduled": any(item.get("retry_scheduled") is True for item in diagnostic_events),
            "retry_succeeded": any(
                item.get("event") == "tts_synthesis_retry_result" and item.get("succeeded") is True
                for item in diagnostic_events
            ),
            "provider_generation_active_observed": any(
                item.get("provider_generation_active") is True for item in diagnostic_events
            ),
            "coherent_sequence_failure": any(
                item.get("coherent_sequence_failure") is True for item in diagnostic_events
            ),
            "exception_categories": sorted({
                str(item.get("category")) for item in diagnostic_events if item.get("category")
            }),
            "exception_classes": sorted({
                str(item.get("exception_class")) for item in diagnostic_events
                if item.get("exception_class")
            }),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.model), indent=2))


if __name__ == "__main__":
    main()
