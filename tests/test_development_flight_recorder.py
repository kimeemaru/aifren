import json
from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest.mock import patch

from development_flight_recorder import DevelopmentFlightRecorder, valid_capture_id
from tts.streaming import StreamingSpeechQueue


class DevelopmentFlightRecorderTests(unittest.TestCase):
    def setUp(self):
        self.recorder = DevelopmentFlightRecorder(sample_hz=5)
        self.recorder.start(unity_pid=999999, state_provider=lambda: {
            "turn_tasks": 1, "qwen_generating": True, "llama_pid": 0,
        })

    def tearDown(self):
        self.recorder.stop()

    def test_capture_id_is_strict_and_cannot_escape_tmp(self):
        self.assertTrue(valid_capture_id("20260825T123456-abcdef"))
        self.assertFalse(valid_capture_id("../../private"))
        self.assertFalse(valid_capture_id("20260825T123456-ABCDEF"))

    def test_service_content_is_reduced_to_counts(self):
        secret = "private dialogue must never be stored"
        self.recorder.observe_service_event("assistant_response", {
            "turn_id": 7, "content": secret, "unexpected": "/private/path",
        })
        event = list(self.recorder._events)[-1]
        self.assertEqual(event["characters"], len(secret))
        self.assertEqual(event["words"], 6)
        self.assertNotIn("content", event)
        self.assertNotIn("unexpected", event)
        self.assertNotIn(secret, json.dumps(event))

    def test_continuity_event_keeps_only_bounded_snapshot_counts(self):
        self.recorder.observe_service_event("continuity_changed", {
            "continuity": {
                "scene_subjects": [{"private": "object name"}] * 12,
                "scene_relations": [{"private": "relation"}] * 16,
                "capability_effects": [{"private": "effect"}] * 5,
                "profile_baseline": [{"private": "baseline"}],
            },
        })
        event = list(self.recorder._events)[-1]
        self.assertEqual(12, event["scene_subject_count"])
        self.assertEqual(16, event["scene_relation_count"])
        self.assertEqual(5, event["capability_effect_count"])
        self.assertEqual(1, event["baseline_fact_count"])
        self.assertNotIn("continuity", event)
        self.assertNotIn("object name", json.dumps(event))

    def test_token_rate_deltas_are_counted_without_allocating_event_records(self):
        before = len(self.recorder._events)
        for _ in range(100):
            self.recorder.observe_service_event("assistant_delta", {"content": "not retained"})
        self.assertEqual(len(self.recorder._events), before)
        self.assertEqual(self.recorder._assistant_deltas_total, 100)

    def test_explicit_local_seed_is_retained_as_privacy_safe_numeric_metadata(self):
        self.recorder.mark(
            "local_request_seed", turn_id=9, seed=123456, explicit_seed=True,
        )
        event = list(self.recorder._events)[-1]
        self.assertEqual(9, event["turn_id"])
        self.assertEqual(123456, event["seed"])
        self.assertTrue(event["explicit_seed"])

    def test_local_sampling_preset_is_retained_without_request_text(self):
        self.recorder.mark(
            "local_request_sampling", turn_id=4,
            sampling_preset="qwen3.5_non_thinking_general",
            temperature=0.7, top_p=0.8, top_k=20, min_p=0.0,
            presence_penalty=1.5, repeat_penalty=1.0,
            ignored_text="must not be retained",
        )
        event = list(self.recorder._events)[-1]
        self.assertEqual("qwen3.5_non_thinking_general", event["sampling_preset"])
        self.assertEqual(1.5, event["presence_penalty"])
        self.assertNotIn("ignored_text", event)

    def test_continuity_extraction_keeps_only_closed_structural_labels(self):
        secret = "private raw dialogue"
        self.recorder.mark(
            "continuity_extraction", method="semantic", outcome="applied",
            confidence="high", intent="exit_scenario", intent_count=1,
            raw_dialogue=secret,
        )
        event = list(self.recorder._events)[-1]
        self.assertEqual("semantic", event["method"])
        self.assertEqual("applied", event["outcome"])
        self.assertEqual("exit_scenario", event["intent"])
        self.assertEqual(1, event["intent_count"])
        self.assertNotIn("raw_dialogue", event)
        self.assertNotIn(secret, json.dumps(event))

    def test_tts_retry_diagnostics_keep_structural_failure_fields_without_text(self):
        class RetryFake:
            def __init__(self):
                self.playback_finished = threading.Event()
                self.playback_finished.set()
                self.calls = 0

            def prepare_stream_chunk(self, _text):
                self.calls += 1
                if self.calls == 1:
                    provider_active[0] = False
                    raise RuntimeError("synthetic private sentence CUDA busy")
                return b"pcm"

            def start_prepared_chunk(self, _prepared):
                self.playback_finished.set()
                return True

        provider_active = [True]
        fake = RetryFake()
        with patch("tts.streaming.development_flight_recorder", return_value=self.recorder):
            queue = StreamingSpeechQueue(
                fake, provider_generation_active=lambda: provider_active[0],
            )
            queue.submit("synthetic dialogue must not be recorded")
            queue.close()
            queue.join(2)
        failures = [
            event for event in self.recorder._events
            if event.get("event") == "tts_synthesis_failure"
        ]
        outcomes = [
            event for event in self.recorder._events
            if event.get("event") == "tts_synthesis_retry_result"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["exception_class"], "runtimeerror")
        self.assertEqual(failures[0]["category"], "concurrent_provider_resource")
        self.assertTrue(failures[0]["provider_generation_active"])
        self.assertTrue(failures[0]["retry_scheduled"])
        self.assertEqual(failures[0]["attempt"], 1)
        self.assertTrue(outcomes[-1]["succeeded"])
        serialized = json.dumps(failures + outcomes)
        self.assertNotIn("synthetic private sentence", serialized)
        self.assertNotIn("dialogue must not be recorded", serialized)

    def test_context_hygiene_retains_only_numeric_counts(self):
        self.recorder.mark(
            "context_hygiene",
            context_hygiene_candidates=12,
            context_hygiene_assistant_only_suppressed=2,
            context_hygiene_suppressed=3,
            context_hygiene_user_echo_count=2,
            context_hygiene_self_redundancy_count=3,
            context_hygiene_repetitive_run_count=1,
            exchange_candidates=10,
            exchange_pairs_suppressed=1,
            raw_recent_message_count=25,
            admitted_recent_message_count=22,
            context_hygiene_removed_characters=640,
            context_hygiene_approximate_tokens_removed=160,
            final_prompt_characters=32000,
            approximate_final_prompt_tokens=8000,
            compaction_version=2,
            episode_count=15,
            episode_context_count=3,
            episode_source_record_count=1130,
            compacted_context_characters=2437,
            compacted_context_approximate_tokens=610,
            consolidated_episode_count=1,
            consolidated_source_record_count=410,
            lower_level_episodes_replaced=6,
            episode_retrieval_version=1,
            episode_retrieval_candidate_count=2,
            retrieved_episode_count=1,
            retrieved_episode_source_record_count=80,
            retrieved_episode_context_characters=622,
            episode_retrieval_query_term_count=4,
            episode_retrieval_signal_code=3,
            retrieved_episode_source_start_index=480,
            retrieved_episode_source_end_index_exclusive=560,
            temporal_retrieval_version=1,
            temporal_query_present=True,
            temporal_retrieval_candidate_count=1,
            temporal_window_source_record_count=122,
            temporal_activity_match_count=2,
            temporal_raw_match_count=0,
            episode_retrieval_result_state_code=6,
            temporal_source_span_count=3,
            temporal_source_record_count=6,
            temporal_distinct_result_count=4,
            temporal_related_result_count=3,
            temporal_source_episode_count=2,
            temporal_result_truncated=True,
            episode_cache_suffix_message_count=101,
            episode_cache_rollover_trigger_messages=80,
            episode_cache_rollover_hard_limit_messages=100,
            episode_cache_rollover_grace_limit_messages=120,
            episode_cache_rollover_state=3,
            episode_cache_selector_available=True,
            episode_cache_temporary_grace_active=True,
            episode_cache_temporary_fallback=False,
            dialogue="must not be retained",
        )
        event = list(self.recorder._events)[-1]
        self.assertEqual(3, event["context_hygiene_suppressed"])
        self.assertEqual(2, event["context_hygiene_assistant_only_suppressed"])
        self.assertEqual(1, event["exchange_pairs_suppressed"])
        self.assertEqual(8000, event["approximate_final_prompt_tokens"])
        self.assertEqual(15, event["episode_count"])
        self.assertEqual(1130, event["episode_source_record_count"])
        self.assertEqual(1, event["consolidated_episode_count"])
        self.assertEqual(410, event["consolidated_source_record_count"])
        self.assertEqual(6, event["lower_level_episodes_replaced"])
        self.assertEqual(1, event["retrieved_episode_count"])
        self.assertEqual(80, event["retrieved_episode_source_record_count"])
        self.assertEqual(3, event["episode_retrieval_signal_code"])
        self.assertEqual(480, event["retrieved_episode_source_start_index"])
        self.assertEqual(560, event["retrieved_episode_source_end_index_exclusive"])
        self.assertTrue(event["temporal_query_present"])
        self.assertEqual(1, event["temporal_retrieval_candidate_count"])
        self.assertEqual(0, event["temporal_raw_match_count"])
        self.assertEqual(6, event["episode_retrieval_result_state_code"])
        self.assertEqual(3, event["temporal_source_span_count"])
        self.assertEqual(6, event["temporal_source_record_count"])
        self.assertEqual(4, event["temporal_distinct_result_count"])
        self.assertEqual(3, event["temporal_related_result_count"])
        self.assertEqual(2, event["temporal_source_episode_count"])
        self.assertTrue(event["temporal_result_truncated"])
        self.assertEqual(101, event["episode_cache_suffix_message_count"])
        self.assertEqual(80, event["episode_cache_rollover_trigger_messages"])
        self.assertEqual(3, event["episode_cache_rollover_state"])
        self.assertTrue(event["episode_cache_selector_available"])
        self.assertTrue(event["episode_cache_temporary_grace_active"])
        self.assertNotIn("dialogue", event)

    @patch.object(DevelopmentFlightRecorder, "_gpu_sample", return_value={})
    def test_trigger_preserves_pre_window_and_dump_writes_only_bounded_telemetry(self, _gpu):
        capture_id = "20260825T123456-abc123"
        bundle = Path("/tmp") / ("aifren-flight-recorder-" + capture_id)
        shutil.rmtree(bundle, ignore_errors=True)
        try:
            self.recorder.mark("turn_started", turn_id=3)
            self.assertTrue(self.recorder.trigger(capture_id, "manual_hotkey"))
            self.recorder.mark("playback_started", playback_id=4)
            summary = self.recorder.dump(capture_id)
            self.assertEqual(summary["capture_reason"], "manual_hotkey")
            timeline = (bundle / "backend_timeline.jsonl").read_text(encoding="utf-8")
            self.assertIn("turn_started", timeline)
            self.assertIn("playback_started", timeline)
            self.assertNotIn("content", timeline)
        finally:
            shutil.rmtree(bundle, ignore_errors=True)

    def test_event_ring_is_bounded(self):
        for index in range(5000):
            self.recorder.mark("sample", value=index)
        self.assertEqual(len(self.recorder._events), 4096)

    @patch.object(DevelopmentFlightRecorder, "_gpu_sample", return_value={})
    @patch.object(DevelopmentFlightRecorder, "_process_sample", return_value={"alive": False})
    @patch.object(DevelopmentFlightRecorder, "_system_cpu", return_value={})
    @patch.object(DevelopmentFlightRecorder, "_system_memory", return_value={"available_ram_mb": 8192})
    def test_post_release_ptt_worker_triggers_diagnostic_capture(
        self, _memory, _cpu, _process, _gpu
    ):
        reasons = []
        recorder = DevelopmentFlightRecorder(sample_hz=5)
        recorder._state_provider = lambda: {
            "ptt_worker_alive": True,
            "ptt_worker_age_seconds": 12.5,
            "ptt_stage": "whisper_begin",
            "ptt_stage_age_seconds": 11.0,
            "ptt_post_release": True,
            "ptt_post_release_age_seconds": 10.5,
            "ptt_recording": False,
            "ptt_listening": False,
            "ptt_transcribing": True,
        }
        recorder._auto_trigger = reasons.append

        sample = recorder._sample(time.monotonic())

        self.assertEqual(reasons, ["ptt_worker_post_release_over_10s"])
        self.assertEqual(sample["ptt_stage"], "whisper_begin")
        self.assertTrue(sample["ptt_worker_alive"])
        self.assertTrue(sample["ptt_transcribing"])


if __name__ == "__main__":
    unittest.main()
