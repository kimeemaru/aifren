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


class ProcessOutputCapture:
    """Content-free child-output accounting owned by Development diagnostics.

    Never parse, decode, queue or persist child text: even a familiar prefix may
    contain private provider output. Fixed-size draining continues when memory
    or disk retention is full/unavailable. Release retains nothing. Existing
    status/console transport carries software errors independently of stdout.
    """
    READ_BYTES = 8192
    MEMORY_RECORDS = 64
    FILE_BYTES = 65536
    FILE_COUNT = 2

    def __init__(self, *, development: bool, directory: Path | None = None):
        self.development = bool(development)
        self.directory = directory if development else None
        self.records = deque(maxlen=self.MEMORY_RECORDS)
        self.byte_count = 0
        self._lock = threading.Lock()
        self._disk_disabled = False

    def _record(self, count: int) -> None:
        with self._lock:
            self.byte_count = min(2**63 - 1, self.byte_count + count)
            if not self.development:
                return
            line = f"child_output_discarded bytes={count} total={self.byte_count}\n"
            self.records.append(line)
        if self.directory is None or self._disk_disabled:
            return
        try:
            directory = self.directory.absolute()
            if any(path.is_symlink() for path in (directory, *directory.parents)):
                raise OSError("output directory must not be a symlink")
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "process-output-counts.log"
            previous = directory / "process-output-counts.1.log"
            if path.is_symlink() or previous.is_symlink():
                raise OSError("output counters must not be symlinks")
            encoded = line.encode("ascii")
            if path.exists() and path.stat().st_size + len(encoded) > self.FILE_BYTES:
                os.replace(path, previous)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "ab") as handle:
                handle.write(encoded)
        except OSError:
            # Never stop draining because the optional counter sink failed.
            self._disk_disabled = True

    def drain(self, stream) -> None:
        try:
            read = getattr(stream, "read1", stream.read)
            while True:
                chunk = read(self.READ_BYTES)
                if not chunk:
                    break
                self._record(len(chunk))
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def take_records(self) -> tuple[str, ...]:
        with self._lock:
            records = tuple(self.records)
            self.records.clear()
            return records


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
    "memory_authority", "memory_query_intent", "requested_relation",
    "requested_speaker", "memory_answer_state", "recent_context_policy",
    "memory_time_semantics", "memory_query_reason", "absence_kind",
    "repair_disposition", "code", "generation_stage", "generation_output_state",
    "retrieval_health", "retrieval_error_stage", "retrieval_error_code",
    "memory_realization", "reaction_status",
    "capacity_source", "counting_method", "episode_compression", "context_owner", "context_kind",
    "performance_profile", "candidate_cost_method",
}
_SAFE_NUMBER_KEYS = {
    "view_alias", "view_count",
    "turn_id", "playback_id", "chunk_index", "pid", "unity_pid", "frame",
    "characters", "words", "prompt_tokens", "generated_tokens", "duration_ms",
    "duration_seconds", "tok_s", "active_jobs", "pending_jobs", "queue_depth",
    "underflows", "audio_samples", "sample_rate", "threads", "turn_tasks",
    "event_tasks", "provider_streams", "stale_tts_results", "timestamp", "capture_id",
    "byte_count", "ptt_worker_age_seconds", "ptt_stage_age_seconds",
    "ptt_post_release_age_seconds", "seed", "temperature", "top_p", "top_k",
    "min_p", "presence_penalty", "repeat_penalty",
    "context_hygiene_candidates", "context_hygiene_suppressed",
    "capacity_tokens", "output_reserve_tokens", "framing_reserve_tokens", "input_budget_tokens",
    "mandatory_tokens", "continuity_budget_tokens", "final_tokens", "headroom_tokens", "tokenizer_calls",
    "recent_messages", "recent_exchanges", "recent_characters", "dropped_count", "duplicate_items",
    "temporal_duplicate_removed", "temporal_duplicate_characters", "hygiene_suppressed", "hygiene_removed_characters",
    "history_assistant_count", "history_leading_action_count", "history_closing_question_count", "history_long_reply_count",
    "estimated_tokens", "source_count",
    "final_request_characters",
    "operating_target_tokens", "effective_target_tokens", "target_headroom_tokens",
    "verification_corrections", "tokenizer_ms", "planning_ms", "context_preparation_ms", "episode_selection_ms",
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
    "v1_memory_count", "v2_memory_count", "overlap_count",
    "canonical_action_span_count", "spoken_emphasis_span_count",
    "normalized_parenthesized_star_action_count",
    "normalized_starred_parenthetical_action_count",
    "spoken_projection_action_count",
    "memory_query_decision_version", "memory_context_characters",
    "recent_context_characters", "recent_context_approximate_tokens",
    "retrieval_ms", "retrospective_claim_count",
    "unsupported_retrospective_claim_count", "memory_candidate_count",
    "gpu_total_bytes", "managed_model_bytes", "device_required_bytes",
    "insufficient_memory_candidate_count",
    "retrieval_failed_lanes", "retrieval_recovered_lanes",
    "memory_supported_slots", "memory_missing_slots", "memory_unavailable_slots",
}
_SAFE_BOOL_KEYS = {
    "memory_contained", "history_work_ceiling_reached",
    "mandatory_above_target",
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
    "shadow_failed", "abstained",
    "provider_called", "provider_bypassed", "memory_query_applicable",
    "authoritative_no_evidence", "v1_prompt_retrieval_entered",
    "v1_write_path_enabled", "spontaneous_retrospective_claims_detected",
    "unsupported_retrospective_claims_rejected",
    "recall_anchor_used",
    "reasoning_content_present", "visible_content_present",
}


def valid_capture_id(value: Any) -> bool:
    return isinstance(value, str) and _CAPTURE_ID.fullmatch(value) is not None


def _shadow_label(value: Any, maximum: int = 80) -> str:
    text = str(value or "")
    if len(text) > maximum or re.fullmatch(r"[A-Za-z0-9_.:+-]*", text) is None:
        raise ValueError("memory recall shadow field is not structural")
    return text


def _shadow_category(value: Any) -> str:
    """Reduce a bounded V1 category without dropping its whole turn.

    Legacy V1 categories are user-extensible and existing canonical records
    include separators such as ``question/inquiry``.  The recorder keeps only
    its established structural alphabet; it never needs the exact category
    spelling to resolve the selected memory by ID during authorized review.
    """
    text = re.sub(r"[^A-Za-z0-9_.:+-]+", "_", str(value or "unknown"))
    return _shadow_label(text.strip("_") or "unknown", 64)


def _shadow_number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("memory recall shadow number is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("memory recall shadow number is not finite")
    return int(value) if integer else number


def _sanitize_memory_recall_shadow(record: dict[str, Any]) -> dict[str, Any]:
    """Copy the closed structural schema; unknown/content fields disappear."""
    v1 = []
    for row in list(record.get("v1", ()))[:5]:
        if not isinstance(row, dict):
            raise TypeError("invalid V1 shadow row")
        v1.append({
            "memory_id": _shadow_label(row.get("memory_id")),
            "category": _shadow_category(row.get("category")),
            "rank": _shadow_number(row.get("rank"), integer=True),
        })
    v2 = []
    for row in list(record.get("v2", ()))[:5]:
        if not isinstance(row, dict):
            raise TypeError("invalid V2 shadow row")
        signals = []
        for signal in list(row.get("signals", ()))[:12]:
            if not isinstance(signal, (list, tuple)) or len(signal) != 2:
                raise TypeError("invalid V2 shadow signal")
            signals.append([_shadow_label(signal[0], 64), _shadow_number(signal[1])])
        evidence = []
        for source in list(row.get("evidence", ()))[:4]:
            if not isinstance(source, dict):
                raise TypeError("invalid V2 shadow evidence")
            evidence.append({
                "source_type": _shadow_label(source.get("source_type"), 64),
                "source_id": _shadow_label(source.get("source_id")),
                "source_reference_sha256": _shadow_label(source.get("source_reference_sha256"), 64),
                "sequence": _shadow_number(source.get("sequence"), integer=True),
                "recorded_at_us": _shadow_number(source.get("recorded_at_us"), integer=True),
                "source_start_sequence": _shadow_number(source.get("source_start_sequence"), integer=True),
                "source_end_sequence": _shadow_number(source.get("source_end_sequence"), integer=True),
            })
        v2.append({
            "memory_id": _shadow_label(row.get("memory_id")),
            "lane": _shadow_label(row.get("lane"), 64),
            "rank": _shadow_number(row.get("rank"), integer=True),
            "score": _shadow_number(row.get("score")),
            "signals": signals,
            "truth_scope_key": _shadow_label(row.get("truth_scope_key"), 64),
            "status": _shadow_label(row.get("status"), 64),
            "associated_from": _shadow_label(row.get("associated_from")),
            "speaker_role": _shadow_label(row.get("speaker_role"), 16),
            "speech_act": _shadow_label(row.get("speech_act"), 16),
            "source_class": _shadow_label(row.get("source_class"), 64),
            "scope_state": _shadow_label(row.get("scope_state"), 32),
            "attribution_state": _shadow_label(row.get("attribution_state"), 64),
            "canonical_record_id": _shadow_label(
                row.get("canonical_record_id"), 160,
            ),
            "canonical_index": _shadow_number(
                row.get("canonical_index"), integer=True,
            ),
            "episode_id": _shadow_label(row.get("episode_id"), 160),
            "episode_source_start_index": _shadow_number(
                row.get("episode_source_start_index"), integer=True,
            ),
            "episode_source_end_index_exclusive": _shadow_number(
                row.get("episode_source_end_index_exclusive"), integer=True,
            ),
            "evidence": evidence,
        })
    generated = []
    for item in list(record.get("generated_counts", ()))[:12]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TypeError("invalid generated count")
        generated.append([_shadow_label(item[0], 64), _shadow_number(item[1], integer=True)])
    return {
        "version": 1,
        "recorded_at": time.time(),
        "monotonic": time.monotonic(),
        "turn_id": _shadow_number(record.get("turn_id"), integer=True),
        "generation": _shadow_number(record.get("generation"), integer=True),
        "canonical_user_index": _shadow_number(record.get("canonical_user_index"), integer=True),
        "character_key": _shadow_label(record.get("character_key"), 64),
        "query_sha256": _shadow_label(record.get("query_sha256"), 64),
        "v1_latency_ms": _shadow_number(record.get("v1_latency_ms")),
        "v2_latency_ms": _shadow_number(record.get("v2_latency_ms")),
        "v1": v1,
        "v2": v2,
        "v2_abstention_reason": _shadow_label(record.get("v2_abstention_reason")),
        "generated_counts": generated,
        "overlap_claim_ids": [
            _shadow_label(value) for value in list(record.get("overlap_claim_ids", ()))[:5]
        ],
        "error_kind": _shadow_label(record.get("error_kind")),
        "error_stage": _shadow_label(record.get("error_stage"), 64),
        "sqlite_error_name": _shadow_label(record.get("sqlite_error_name"), 64),
        "sqlite_error_code": _shadow_number(record.get("sqlite_error_code"), integer=True),
        "error_disposition": _shadow_label(record.get("error_disposition"), 64),
        "retry_disposition": _shadow_label(record.get("retry_disposition"), 64),
        "memory_query_intent": _shadow_label(record.get("memory_query_intent"), 64),
        "requested_relation": _shadow_label(record.get("requested_relation"), 32),
        "requested_speaker": _shadow_label(record.get("requested_speaker"), 16),
        "retrieval_health": _shadow_label(record.get("retrieval_health"), 16),
        "retrieval_error_stage": _shadow_label(record.get("retrieval_error_stage"), 16),
        "retrieval_error_code": _shadow_label(record.get("retrieval_error_code"), 32),
        "retrieval_failed_lanes": _shadow_number(record.get("retrieval_failed_lanes"), integer=True),
        "retrieval_recovered_lanes": _shadow_number(record.get("retrieval_recovered_lanes"), integer=True),
    }


class DevelopmentFlightRecorder:
    """Low-rate process sampler plus bounded lifecycle marker ring."""

    def __init__(self, *, sample_hz: float = 5.0) -> None:
        self.sample_hz = max(1.0, min(10.0, float(sample_hz)))
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=4096)
        self._samples: deque[dict[str, Any]] = deque(maxlen=int(self.sample_hz * 35) + 8)
        self._memory_recall_shadow: deque[dict[str, Any]] = deque(maxlen=128)
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

    def mark_view(self, stage: str, request_id: str | None, count: int) -> None:
        # Bounded recorder-local aliases only: no identity/query/payload is serialized.
        with self._lock:
            if not self._enabled:
                return
            if not hasattr(self, "_view_aliases"):
                self._view_aliases = {}
                self._view_alias_sequence = 0
            alias = 0
            if request_id:
                if request_id not in self._view_aliases:
                    if len(self._view_aliases) >= 128:
                        self._view_aliases.pop(next(iter(self._view_aliases)))
                    self._view_alias_sequence += 1
                    self._view_aliases[request_id] = self._view_alias_sequence
                alias = self._view_aliases[request_id]
        self.mark("view_" + stage, view_alias=alias, view_count=count)

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
        if isinstance(values.get("code"), str):
            metadata["code"] = values["code"]
        if isinstance(values.get("generation_stage"), str):
            metadata["generation_stage"] = values["generation_stage"]
        if isinstance(values.get("provider_called"), bool):
            metadata["provider_called"] = values["provider_called"]
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
            self.mark("qwen_prompt_eval_end", duration_ms=float(prompt.group(1)), prompt_tokens=int(prompt.group(2)))
        generated = re.search(r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+) runs.*?([\d.]+) tokens per second", text, re.I)
        if generated:
            self.mark("qwen_generation_end", duration_ms=float(generated.group(1)), generated_tokens=int(generated.group(2)), tok_s=float(generated.group(3)))

    def record_memory_recall_shadow(self, record: dict[str, Any]) -> bool:
        """Attach one already-sanitized structural recall comparison.

        This separate bounded ring deliberately rejects unknown fields and all
        text-bearing values. Human-readable content is resolved only by the
        explicit local inspection tool against an authorized character.
        """
        if not self.enabled or not isinstance(record, dict):
            return False
        try:
            safe = _sanitize_memory_recall_shadow(record)
        except (TypeError, ValueError):
            return False
        with self._lock:
            self._memory_recall_shadow.append(safe)
        self.mark(
            "memory_recall_shadow_complete",
            turn_id=safe["turn_id"],
            duration_ms=safe["v2_latency_ms"],
            v1_memory_count=len(safe["v1"]),
            v2_memory_count=len(safe["v2"]),
            overlap_count=len(safe["overlap_claim_ids"]),
            abstained=not bool(safe["error_kind"]) and not bool(safe["v2"]),
            shadow_failed=bool(safe["error_kind"]),
            succeeded=not bool(safe["error_kind"]),
        )
        return True

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
                "memory_recall_shadow": list(self._memory_recall_shadow),
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
                    "memory_recall_shadow": list(self._memory_recall_shadow),
                }
            trigger_mono = max((item.get("monotonic", 0.0) for item in frozen["events"]), default=0.0)
            post_events = [item for item in self._events if item.get("monotonic", 0.0) > trigger_mono]
            post_samples = [item for item in self._samples if item.get("monotonic", 0.0) > trigger_mono]
            post_shadow = [
                item for item in self._memory_recall_shadow
                if item.get("recorded_at", 0.0) > float(frozen["triggered_at"])
            ]
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
        shadow = list(frozen.get("memory_recall_shadow", ())) + post_shadow
        if shadow:
            with (bundle / "memory_recall_shadow.json").open("w", encoding="utf-8") as handle:
                json.dump({"version": 1, "records": shadow}, handle, indent=2, sort_keys=True)
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
            "audio_queue_depth", "stale_tts_results", "qwen_generating", "kokoro_synthesizing",
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
