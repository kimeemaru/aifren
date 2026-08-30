"""Bounded, opt-in telemetry for Development-player incident capture.

The recorder is inert until a Development Unity client explicitly enables it.
It accepts structural metadata only and never stores model/user text or paths.
"""

from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable


_CAPTURE_ID = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{6}$")
_SAFE_STRING_KEYS = {
    "event", "state", "source", "provider", "model", "device", "reason",
    "compute", "role", "error_type", "ptt_stage", "sampling_preset",
    "method", "outcome", "intent", "confidence", "response_mode",
    "contract_status", "category", "fallback_category", "reaction",
    "speech_mode", "movement_family", "exception_class",
    "generation_origin", "parse_failure", "semantic_rejection",
    "primary_parse_failure", "primary_semantic_rejection",
    "contract_admission", "primary_contract_admission",
    "application_outcome", "accelerator_backend",
}
_SAFE_NUMBER_KEYS = {
    "turn_id", "playback_id", "chunk_index", "pid", "unity_pid", "frame",
    "characters", "words", "prompt_tokens", "generated_tokens", "duration_ms",
    "duration_seconds", "tok_s", "active_jobs", "pending_jobs", "queue_depth",
    "underflows", "audio_samples", "sample_rate", "threads", "turn_tasks",
    "event_tasks", "provider_streams", "stale_tts_results", "timestamp", "capture_id",
    "byte_count", "ptt_worker_age_seconds", "ptt_stage_age_seconds",
    "ptt_post_release_age_seconds", "seed", "temperature", "top_p", "top_k",
    "min_p", "presence_penalty", "repeat_penalty",
    "context_hygiene_candidates", "context_hygiene_suppressed",
    "context_hygiene_assistant_only_suppressed", "exchange_candidates",
    "exchange_pairs_suppressed",
    "context_hygiene_user_echo_count", "context_hygiene_self_redundancy_count",
    "context_hygiene_repetitive_run_count", "raw_recent_message_count",
    "admitted_recent_message_count", "context_hygiene_removed_characters",
    "context_hygiene_approximate_tokens_removed", "final_context_characters",
    "approximate_final_context_tokens", "final_prompt_characters",
    "approximate_final_prompt_tokens",
    "compaction_version", "episode_count", "episode_context_count",
    "episode_source_record_count", "compacted_context_characters",
    "compacted_context_approximate_tokens", "episode_rebuild_duration_ms",
    "episode_retrieval_version", "episode_retrieval_candidate_count",
    "retrieved_episode_count", "retrieved_episode_source_record_count",
    "retrieved_episode_context_characters", "episode_retrieval_query_term_count",
    "episode_retrieval_signal_code", "retrieved_episode_source_start_index",
    "retrieved_episode_source_end_index_exclusive", "temporal_retrieval_version",
    "temporal_retrieval_candidate_count", "temporal_window_source_record_count",
    "temporal_activity_match_count", "temporal_raw_match_count",
    "episode_retrieval_result_state_code", "temporal_source_span_count",
    "temporal_source_record_count", "temporal_distinct_result_count",
    "temporal_related_result_count",
    "temporal_source_episode_count",
    "episode_cache_suffix_message_count", "episode_cache_source_end_before",
    "episode_cache_source_end_after", "episode_cache_rollover_trigger_messages",
    "episode_cache_rollover_hard_limit_messages",
    "episode_cache_rollover_grace_limit_messages", "episode_cache_rollover_state",
    "episode_cache_reused_episode_count", "episode_cache_generated_episode_count",
    "episode_cache_rebuild_duration_ms", "episode_cache_rollover_error_code",
    "consolidated_episode_count", "consolidated_source_record_count",
    "lower_level_episodes_replaced",
    "intent_count", "attempt", "scene_subject_count", "scene_relation_count",
    "capability_effect_count", "baseline_fact_count",
}
_SAFE_BOOL_KEYS = {
    "active", "generating", "synthesizing", "playing", "loaded", "listening",
    "transcribing", "cancelled", "streamed",
    "ptt_worker_alive", "ptt_recording", "ptt_listening", "ptt_transcribing",
    "ptt_post_release",
    "explicit_seed", "temporal_query_present", "episode_cache_selector_available",
    "episode_cache_temporary_grace_active", "episode_cache_temporary_fallback",
    "episode_cache_rollover_published",
    "temporal_result_truncated", "parse_success", "accepted_generated",
    "fallback_used", "provider_generating", "provider_generation_active",
    "retry_planned", "retry_scheduled",
    "succeeded", "coherent_sequence_failure",
    "proactive", "cpu_fallback_used", "repaired",
    "accepted_direct", "repair_attempted", "repair_succeeded",
    "minimal_plain_text", "applied_before_generation",
    "mutation_expected", "mutation_applied", "response_suppressed",
}


def valid_capture_id(value: Any) -> bool:
    return isinstance(value, str) and _CAPTURE_ID.fullmatch(value) is not None


class DevelopmentFlightRecorder:
    """Low-rate process sampler plus bounded lifecycle marker ring."""

    def __init__(self, *, sample_hz: float = 5.0) -> None:
        self.sample_hz = max(1.0, min(10.0, float(sample_hz)))
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=4096)
        self._samples: deque[dict[str, Any]] = deque(maxlen=int(self.sample_hz * 35) + 8)
        self._enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_provider: Callable[[], dict[str, Any]] | None = None
        self._unity_pid = 0
        self._captures: dict[str, dict[str, Any]] = {}
        self._last_cpu: dict[int, tuple[float, int]] = {}
        self._last_system_cpu: tuple[int, int] | None = None
        self._last_vmstat: tuple[int, int] | None = None
        self._last_gpu: dict[str, float] = {}
        self._last_gpu_at = 0.0
        self._auto_trigger: Callable[[str], None] | None = None
        self._last_auto_trigger_at = -1000.0
        self._underflow_times: deque[float] = deque(maxlen=8)
        self._service_events_total = 0
        self._assistant_deltas_total = 0
        self._stale_tts_results = 0
        self._tts_cancellations = 0

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def start(
        self, *, unity_pid: int, state_provider: Callable[[], dict[str, Any]],
        auto_trigger: Callable[[str], None] | None = None,
    ) -> None:
        with self._lock:
            self._unity_pid = max(0, int(unity_pid or 0))
            self._state_provider = state_provider
            self._auto_trigger = auto_trigger
            if self._enabled:
                return
            self._enabled = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._sample_loop, name="aifren-flight-recorder", daemon=True)
            self._thread.start()
        self.mark("recorder_started", unity_pid=self._unity_pid)

    def stop(self) -> None:
        with self._lock:
            self._enabled = False
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def mark(self, event: str, **metadata: Any) -> None:
        with self._lock:
            if not self._enabled:
                return
        record: dict[str, Any] = {
            "record_type": "backend_event",
            "timestamp": time.time(),
            "monotonic": time.monotonic(),
            "event": self._safe_label(event),
        }
        for key, value in metadata.items():
            if key in _SAFE_STRING_KEYS:
                record[key] = self._safe_label(value)
            elif key in _SAFE_BOOL_KEYS:
                record[key] = bool(value)
            elif key in _SAFE_NUMBER_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if math.isfinite(number):
                    record[key] = int(value) if isinstance(value, int) else number
        with self._lock:
            self._events.append(record)
            if event == "tts_stale_result_discarded":
                self._stale_tts_results += 1
            elif event == "tts_cancellation":
                self._tts_cancellations += 1
        if event == "portaudio_underflow":
            now = time.monotonic()
            self._underflow_times.append(now)
            while self._underflow_times and now - self._underflow_times[0] > 2.0:
                self._underflow_times.popleft()
            if len(self._underflow_times) >= 2:
                self._request_auto_trigger("portaudio_underflow_burst")

    def observe_service_event(self, event_type: str, data: dict[str, Any] | None) -> None:
        """Reduce a service event to counts/IDs; content is never retained."""
        if not self.enabled:
            return
        with self._lock:
            self._service_events_total += 1
            if event_type == "assistant_delta":
                self._assistant_deltas_total += 1
                return
        values = data if isinstance(data, dict) else {}
        metadata: dict[str, Any] = {}
        for key in ("turn_id", "playback_id", "chunk_index"):
            if isinstance(values.get(key), int):
                metadata[key] = values[key]
        if isinstance(values.get("state"), str):
            metadata["state"] = values["state"]
        if isinstance(values.get("source"), str):
            metadata["source"] = values["source"]
        if isinstance(values.get("streamed"), bool):
            metadata["streamed"] = values["streamed"]
        if isinstance(values.get("proactive"), bool):
            metadata["proactive"] = values["proactive"]
        if isinstance(values.get("generation_origin"), str):
            metadata["generation_origin"] = values["generation_origin"]
        content = values.get("content")
        if isinstance(content, str):
            metadata["characters"] = len(content)
            metadata["words"] = len(re.findall(r"\S+", content))
        continuity = values.get("continuity")
        if isinstance(continuity, dict):
            for field, output in (
                ("scene_subjects", "scene_subject_count"),
                ("scene_relations", "scene_relation_count"),
                ("capability_effects", "capability_effect_count"),
                ("profile_baseline", "baseline_fact_count"),
            ):
                rows = continuity.get(field)
                if isinstance(rows, list):
                    metadata[output] = len(rows)
        self.mark(str(event_type), **metadata)

    def observe_model_log(self, message: str) -> None:
        """Extract llama counters without retaining the source log line."""
        if not self.enabled:
            return
        text = str(message)
        prompt = re.search(r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+) tokens", text, re.I)
        if prompt:
            self.mark("model_prompt_eval_end", duration_ms=float(prompt.group(1)), prompt_tokens=int(prompt.group(2)))
        generated = re.search(r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+) runs.*?([\d.]+) tokens per second", text, re.I)
        if generated:
            self.mark("model_generation_end", duration_ms=float(generated.group(1)), generated_tokens=int(generated.group(2)), tok_s=float(generated.group(3)))

    def trigger(self, capture_id: str, reason: str) -> bool:
        if not self.enabled or not valid_capture_id(capture_id):
            return False
        with self._lock:
            if capture_id in self._captures:
                return True
            self._captures[capture_id] = {
                "reason": self._safe_label(reason),
                "triggered_at": time.time(),
                "events": list(self._events),
                "samples": list(self._samples),
            }
        self.mark("capture_triggered", reason=reason)
        return True

    def dump(self, capture_id: str) -> dict[str, Any] | None:
        if not valid_capture_id(capture_id):
            return None
        with self._lock:
            frozen = self._captures.pop(capture_id, None)
            if frozen is None:
                frozen = {
                    "reason": "manual",
                    "triggered_at": time.time(),
                    "events": list(self._events),
                    "samples": list(self._samples),
                }
            trigger_mono = max((item.get("monotonic", 0.0) for item in frozen["events"]), default=0.0)
            post_events = [item for item in self._events if item.get("monotonic", 0.0) > trigger_mono]
            post_samples = [item for item in self._samples if item.get("monotonic", 0.0) > trigger_mono]
        events = frozen["events"] + post_events
        samples = frozen["samples"] + post_samples
        bundle = Path("/tmp") / f"aifren-flight-recorder-{capture_id}"
        bundle.mkdir(mode=0o700, parents=False, exist_ok=True)
        timeline = sorted(events + samples, key=lambda item: (item.get("timestamp", 0.0), item.get("record_type", "")))
        with (bundle / "backend_timeline.jsonl").open("w", encoding="utf-8") as handle:
            for item in timeline:
                handle.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
        summary = self._summarize(samples, frozen["reason"], frozen["triggered_at"])
        with (bundle / "backend_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return summary

    @staticmethod
    def _safe_label(value: Any) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"[^a-z0-9_.:+-]+", "_", text)[:80]

    def _sample_loop(self) -> None:
        interval = 1.0 / self.sample_hz
        while not self._stop.wait(interval):
            started = time.monotonic()
            try:
                sample = self._sample(started)
                sample["sampler_duration_ms"] = (time.monotonic() - started) * 1000.0
                with self._lock:
                    if self._enabled:
                        self._samples.append(sample)
            except Exception:
                # Diagnostics must never affect product behavior.
                pass

    def _sample(self, now: float) -> dict[str, Any]:
        state: dict[str, Any] = {}
        provider = self._state_provider
        if provider is not None:
            try:
                state = provider() or {}
            except Exception:
                state = {}
        llama_pid = int(state.get("llama_pid", 0) or 0)
        sample: dict[str, Any] = {
            "record_type": "backend_sample", "timestamp": time.time(), "monotonic": now,
            **self._system_memory(), **self._system_cpu(),
            "backend": self._process_sample(os.getpid(), now),
            "unity": self._process_sample(self._unity_pid, now),
            "llama": self._process_sample(llama_pid, now),
        }
        with self._lock:
            sample["service_events_total"] = self._service_events_total
            sample["assistant_deltas_total"] = self._assistant_deltas_total
            sample["stale_tts_results"] = self._stale_tts_results
            sample["tts_cancellations_total"] = self._tts_cancellations
        for key in (
            "turn_tasks", "event_tasks", "provider_streams", "tts_active_jobs", "tts_pending_jobs",
            "audio_queue_depth", "stale_tts_results", "model_generating", "kokoro_synthesizing",
            "portaudio_playing", "whisper_loaded", "whisper_active", "ptt_worker_alive",
            "ptt_worker_age_seconds", "ptt_stage_age_seconds", "ptt_post_release_age_seconds",
            "ptt_recording", "ptt_listening", "ptt_transcribing", "ptt_post_release",
        ):
            value = state.get(key)
            if isinstance(value, (bool, int, float)):
                sample[key] = value
        if isinstance(state.get("ptt_stage"), str):
            sample["ptt_stage"] = self._safe_label(state["ptt_stage"])
        if now - self._last_gpu_at >= 1.0:
            self._last_gpu = self._gpu_sample()
            self._last_gpu_at = now
        sample.update(self._last_gpu)
        if sample.get("available_ram_mb", float("inf")) < 1024.0:
            self._request_auto_trigger("available_ram_below_1gb")
        if (
            sample.get("ptt_worker_alive") is True
            and sample.get("ptt_post_release") is True
            and float(sample.get("ptt_post_release_age_seconds", 0.0)) > 10.0
        ):
            self._request_auto_trigger("ptt_worker_post_release_over_10s")
        return sample

    def _request_auto_trigger(self, reason: str) -> None:
        now = time.monotonic()
        callback = self._auto_trigger
        if callback is None or now - self._last_auto_trigger_at < 60.0:
            return
        self._last_auto_trigger_at = now
        try:
            callback(reason)
        except Exception:
            pass

    def _system_memory(self) -> dict[str, float]:
        values: dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as handle:
                for line in handle:
                    name, rest = line.split(":", 1)
                    values[name] = int(rest.strip().split()[0])
        except (OSError, ValueError):
            return {}
        swap_total = values.get("SwapTotal", 0)
        swap_free = values.get("SwapFree", 0)
        vm_in = vm_out = 0
        try:
            with open("/proc/vmstat", "r", encoding="ascii") as handle:
                vm = dict(line.split() for line in handle if line.startswith(("pswpin ", "pswpout ")))
            current = (int(vm.get("pswpin", 0)), int(vm.get("pswpout", 0)))
            if self._last_vmstat is not None:
                vm_in = max(0, current[0] - self._last_vmstat[0])
                vm_out = max(0, current[1] - self._last_vmstat[1])
            self._last_vmstat = current
        except (OSError, ValueError):
            pass
        return {
            "available_ram_mb": values.get("MemAvailable", 0) / 1024.0,
            "swap_used_mb": max(0, swap_total - swap_free) / 1024.0,
            "swap_in_pages_delta": vm_in,
            "swap_out_pages_delta": vm_out,
        }

    def _system_cpu(self) -> dict[str, float]:
        try:
            fields = [int(value) for value in Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            total = sum(fields)
            previous = self._last_system_cpu
            self._last_system_cpu = (idle, total)
            if previous is None or total <= previous[1]:
                return {}
            busy = 1.0 - (idle - previous[0]) / float(total - previous[1])
            return {"system_cpu_percent": max(0.0, min(100.0, busy * 100.0))}
        except (OSError, ValueError, IndexError):
            return {}

    def _process_sample(self, pid: int, now: float) -> dict[str, Any]:
        if pid <= 0:
            return {"pid": 0, "alive": False}
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
            ticks = int(stat[13]) + int(stat[14])
            rss_mb = int(stat[23]) * os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)
            threads = int(stat[19])
            previous = self._last_cpu.get(pid)
            self._last_cpu[pid] = (now, ticks)
            cpu = 0.0
            if previous is not None and now > previous[0]:
                cpu = (ticks - previous[1]) / float(os.sysconf("SC_CLK_TCK")) / (now - previous[0]) * 100.0
            return {"pid": pid, "alive": True, "rss_mb": rss_mb, "cpu_percent": max(0.0, cpu), "threads": threads}
        except (OSError, ValueError, IndexError):
            return {"pid": pid, "alive": False}

    @staticmethod
    def _gpu_sample() -> dict[str, float]:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=0.8, check=False,
            )
            first = result.stdout.strip().splitlines()[0]
            utilization, memory = (float(part.strip()) for part in first.split(",")[:2])
            return {"gpu_utilization_percent": utilization, "vram_used_mb": memory}
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return {}

    @staticmethod
    def _summarize(samples: list[dict[str, Any]], reason: str, timestamp: float) -> dict[str, Any]:
        def values(key: str) -> list[float]:
            return [float(item[key]) for item in samples if isinstance(item.get(key), (int, float))]
        return {
            "capture_reason": reason,
            "capture_timestamp": timestamp,
            "minimum_available_ram_mb": min(values("available_ram_mb"), default=None),
            "swap_in_pages_total": sum(values("swap_in_pages_delta")),
            "swap_out_pages_total": sum(values("swap_out_pages_delta")),
            "peak_gpu_utilization_percent": max(values("gpu_utilization_percent"), default=None),
            "peak_vram_mb": max(values("vram_used_mb"), default=None),
            "peak_backend_rss_mb": max((float(item.get("backend", {}).get("rss_mb", 0)) for item in samples), default=0),
            "peak_unity_rss_mb": max((float(item.get("unity", {}).get("rss_mb", 0)) for item in samples), default=0),
            "peak_llama_rss_mb": max((float(item.get("llama", {}).get("rss_mb", 0)) for item in samples), default=0),
        }


_RECORDER = DevelopmentFlightRecorder()


def development_flight_recorder() -> DevelopmentFlightRecorder:
    return _RECORDER
