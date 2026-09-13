import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from aifren.conversation.conversation import Conversation
from aifren.conversation.truth_scope import parse_canonical_truth_scope
from aifren.continuity.memory_v2_episode_compaction import (
    COMPACTION_VERSION,
    CONTINUITY_ANCHOR_VERSION,
    GENERATOR_VERSION,
    MAX_CONTINUITY_ANCHORS,
    MAX_GENERATED_SCENE_RECORDS_PER_INTERACTION,
    SEGMENTATION_VERSION,
    ERA_COMPACTION_VERSION,
    ERA_RETENTION_GATE_VERSION,
    ERA_SUMMARY_LEVEL,
    EPISODE_PURPOSE_HISTORICAL,
    EpisodeCompactionCache,
    EpisodeCompactionRollover,
    EpisodeCompactor,
    EpisodeRetrievalCandidate,
    EPISODE_RAW_SUFFIX_HARD_LIMIT,
    EPISODE_ROLLOVER_TRIGGER_MESSAGES,
    canonical_record_id,
    canonical_episode_source_groups,
    deterministic_derived_seed,
    deterministic_episode_boundaries,
    deterministic_episode_id,
    _episode_retrieval_score,
    _historical_episode_source_refinements,
    _resolve_temporal_retrieval_query,
    _single_retrieval_key_is_entity_like,
    _source_span_directly_supports_performed_activity,
)
from aifren.memory_v2_store import MemoryV2Store


def message(role, content, index):
    return {"role": role, "content": content, "timestamp": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z"}


def exchanges(count, *, prefix="synthetic"):
    result = []
    for index in range(count):
        result.extend([
            message("user", f"{prefix} user turn {index} asks about topic {index}.", index * 2),
            message("assistant", f"{prefix} assistant turn {index} answers with distinct detail {index}.", index * 2 + 1),
        ])
    return result


def scoped_exchanges(count, scope, *, prefix="synthetic", start_index=0):
    result = exchanges(count, prefix=prefix)
    for offset, record in enumerate(result):
        record["timestamp"] = f"2026-01-01T01:{(start_index + offset) // 60:02d}:{(start_index + offset) % 60:02d}Z"
        record["truth_scope"] = dict(scope)
    return result


def generated_scene_record(index, scope):
    return {
        "role": "user",
        "content": f"Synthetic scene relation {index} cleared.",
        "timestamp": f"2026-01-01T03:00:{index:02d}Z",
        "truth_scope": dict(scope),
        "origin": {
            "kind": "scene_ui", "generated_event": True,
            "operation": "clear_relation",
        },
    }


def temporal_exchanges():
    """Return enough dated exchanges to put one prior-day activity off baseline."""
    result = exchanges(581)
    for exchange_index in range(581):
        episode_index = exchange_index // 40
        if episode_index < 6:
            day = episode_index + 1
        elif exchange_index < 557:
            day = 14
        else:
            day = 15
        for record_index in (exchange_index * 2, exchange_index * 2 + 1):
            result[record_index]["timestamp"] = (
                f"2026-01-{day:02d}T12:00:{record_index % 60:02d}"
            )
    # Episode 6 is outside the positional eight-episode baseline. Episode 7
    # supplies a second broad "talk" candidate for ambiguity coverage.
    result[490]["content"] = "We were playing comet catch together."
    result[492]["content"] = "We talked about the northern lookout."
    result[570]["content"] = "We talked about the observatory repairs."
    return result


class _Provider:
    def __init__(self):
        self.calls = 0

    def generate(self, _context, _prompt, *, seed=None):
        self.calls += 1
        if "Extract a SMALL source-grounded set" in _prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in _prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        return f"The synthetic participants completed episode {self.calls} and moved to its next distinct topic."


class _BlockingProvider(_Provider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, context, prompt, *, seed=None):
        self.started.set()
        if not self.release.wait(5.0):
            raise RuntimeError("synthetic rollover release timed out")
        return super().generate(context, prompt, seed=seed)


class _FailingProvider(_Provider):
    def generate(self, _context, _prompt, *, seed=None):
        raise RuntimeError("synthetic rollover failure")


class _EraProvider(_Provider):
    def __init__(self, *, consolidate=True, retention_status="pass"):
        super().__init__()
        self.consolidate = consolidate
        self.retention_status = retention_status
        self.prompts = []

    def generate(self, _context, prompt, *, seed=None):
        self.calls += 1
        self.prompts.append(prompt)
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        if "Evaluate whether a proposed higher-level" in prompt:
            if self.retention_status == "error":
                raise RuntimeError("synthetic verifier failure")
            if self.retention_status == "malformed":
                return "not JSON"
            missing = [] if self.retention_status == "pass" else [
                "the distinctive observatory visit",
            ]
            return json.dumps({
                "status": self.retention_status,
                "distinctive_items_checked": 1,
                "missing_items": missing,
            })
        if "Extract the neutral historical components" in prompt:
            return json.dumps({
                "period": "a prolonged synthetic testing period repeated one interaction mode",
                "distinct_events": [{
                    "kind": "shared_activity",
                    "description": "a distinctive observatory visit remained relevant",
                }],
                "direction_change": "the participants later moved to a different subject",
            })
        if "Return one strict JSON object" in prompt:
            if not self.consolidate:
                return '{"consolidate": false, "first_episode": 0, "last_episode": 0, "account": ""}'
            episode_count = prompt.count("\nEPISODE ") // 7
            return json.dumps({
                "consolidate": True,
                "first_episode": 1,
                "last_episode": episode_count,
                "account": "A temporary mode recurred across this candidate range.",
            })
        return (
            f"Synthetic testing recurred in episode {self.calls}; the participants also shared "
            "stargazing at the northern lookout."
        )


class _DeterministicLocalProvider:
    fresh_request_seeds = True
    model = "synthetic-local-model"

    def __init__(self, *, fail_after=None):
        self.fail_after = fail_after
        self.calls = []
        self.live_seed_calls = 0

    def request_sampling_metadata(self):
        return {"sampling_preset": "synthetic", "temperature": 0.7}

    def generate(self, _context, prompt, *, seed=None):
        self.calls.append(seed)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("synthetic rebuild failure")
        if seed is None:
            self.live_seed_calls += 1
            seed = 900_000 + self.live_seed_calls
        if "Extract a SMALL source-grounded set" in prompt:
            return '{"anchors":[]}'
        if "Verify whether this compact episode account" in prompt:
            return '{"status":"pass","missing_anchor_ids":[]}'
        fingerprint = hashlib.sha256(f"{seed}:{prompt}".encode("utf-8")).hexdigest()
        return f"Stable derived account {fingerprint}."


class _AnchorProvider:
    fresh_request_seeds = True
    model = "synthetic-anchor-model"

    def __init__(
        self, anchors, *, restore_on_refine=True, always_fail=False,
        malformed_verifier=False,
    ):
        self.anchors = list(anchors)
        self.restore_on_refine = restore_on_refine
        self.always_fail = always_fail
        self.malformed_verifier = malformed_verifier
        self.calls = []
        self.verify_calls = 0
        self.refine_calls = 0

    def request_sampling_metadata(self):
        return {"temperature": 0.7}

    def generate(self, _context, prompt, *, seed=None):
        self.calls.append((prompt, seed))
        if "Extract a SMALL source-grounded set" in prompt:
            return json.dumps({"anchors": self.anchors})
        if "Revise this neutral compact episode account ONCE" in prompt:
            self.refine_calls += 1
            details = "; ".join(str(value["detail"]) for value in self.anchors)
            if self.restore_on_refine:
                return f"The participants shared a varied episode involving {details}."
            return "The participants continued a generic conversation."
        if "Verify whether this compact episode account" in prompt:
            self.verify_calls += 1
            if self.malformed_verifier:
                return '{"status":"pass" "missing_anchor_ids":[]}'
            if self.always_fail or self.verify_calls == 1:
                return json.dumps({
                    "status": "fail",
                    "missing_anchor_ids": [f"A{index}" for index in range(1, len(self.anchors) + 1)],
                })
            return '{"status":"pass","missing_anchor_ids":[]}'
        return "The participants continued a compact, neutral account of the period."


class _Memory:
    def get_relevant_memories(self, _query, max_memories):
        return []


class EpisodeCompactionFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "memory.sqlite3"
        self.store = MemoryV2Store(str(self.database))
        self.character_id = str(uuid.uuid4())
        self.store.create_character(self.character_id, "Synthetic")
        self.cache = EpisodeCompactionCache(self.store, self.character_id)
        self.provider = _Provider()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rebuild(self, messages):
        return self.cache.rebuild(messages, EpisodeCompactor(self.provider))

    def set_episode_anchors(self, source_start_index, anchors):
        row = self.store.connection.execute(
            """SELECT s.summary_id, s.legacy_metadata_json
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                  AND r.start_sequence=?""",
            (self.character_id, source_start_index + 1),
        ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["continuity_anchors"] = anchors
        metadata["continuity_anchor_count"] = len(anchors)
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

    def lower_snapshot(self):
        rows = self.store.connection.execute(
            """SELECT s.summary_id, s.content, s.legacy_metadata_json,
                      r.start_sequence, r.end_sequence
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence""",
            (self.character_id,),
        ).fetchall()
        result = []
        for row in rows:
            metadata = json.loads(row["legacy_metadata_json"])
            result.append({
                "summary_id": row["summary_id"],
                "content_sha256": hashlib.sha256(row["content"].encode("utf-8")).hexdigest(),
                "source_range": [row["start_sequence"], row["end_sequence"]],
                "source_digest": metadata["source_digest"],
                "seed": metadata["derived_generation_seed"],
                "identity_digest": metadata["compactor_identity_digest"],
            })
        return result

    def lower_generation_id(self):
        rows = self.store.connection.execute(
            "SELECT legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchall()
        values = {
            json.loads(row[0])["generation_id"]
            for row in rows
        }
        self.assertEqual(1, len(values))
        return next(iter(values))

    def rollover(self, provider_factory, **kwargs):
        return EpisodeCompactionRollover(
            self.cache,
            lambda: EpisodeCompactor(provider_factory()),
            retry_seconds=0.0,
            **kwargs,
        )

    def create_scenario_scope(self, label, *, sequence):
        event_id = str(uuid.uuid4())
        self.store.add_event(
            self.character_id, event_id, sequence, event_type="synthetic_scope_evidence",
            actor_kind="user", content_text=label,
        )
        return self.store.create_scenario_truth_scope(
            self.character_id, label, evidence_event_id=event_id,
            evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=len(label),
        )

    def test_local_rebuild_is_byte_identical_for_unchanged_inputs(self):
        messages = exchanges(100)
        provider = _DeterministicLocalProvider()
        snapshots = []
        contexts = []
        for _ in range(3):
            self.cache.rebuild(messages, EpisodeCompactor(provider))
            snapshots.append(self.lower_snapshot())
            contexts.append(self.cache.select_for_context(messages).context_block)

        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        self.assertEqual(contexts[0].encode("utf-8"), contexts[1].encode("utf-8"))
        self.assertEqual(contexts[1].encode("utf-8"), contexts[2].encode("utf-8"))
        self.assertTrue(all(seed is not None for seed in provider.calls))

    def test_distinctive_named_callback_survives_one_refinement(self):
        messages = exchanges(30)
        provider = _AnchorProvider([{
            "detail": "Ridley inspired a memorable space-dragon retro-game story",
            "source_record_indices": [0, 1],
        }])
        report = self.cache.rebuild(messages, EpisodeCompactor(provider))
        row = self.store.connection.execute(
            "SELECT content, legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])

        self.assertIn("Ridley", row["content"])
        self.assertEqual("pass", metadata["anchor_verification_status"])
        self.assertTrue(metadata["anchor_refinement_attempted"])
        self.assertEqual(1, report.anchor_refined_episode_count)
        self.assertEqual(1, provider.refine_calls)

    def test_unusual_shared_event_survives_compaction(self):
        messages = exchanges(30)
        provider = _AnchorProvider([{
            "detail": "They stargazed together from the northern observatory",
            "source_record_indices": [2, 3],
        }])
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        content = self.store.connection.execute(
            "SELECT content FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()[0]
        self.assertIn("northern observatory", content)

    def test_concrete_anchor_set_requires_each_source_exact_key_term(self):
        messages = exchanges(30, prefix="wave nod shrug tilt shake think")
        provider = _AnchorProvider([{
            "detail": "They tested wave, nod, shrug, tilt, shake, and think gestures",
            "source_record_indices": [0, 1],
            "key_terms": ["wave", "nod", "shrug", "tilt", "shake", "think"],
        }])
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        row = self.store.connection.execute(
            "SELECT content, legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])

        self.assertEqual("pass", metadata["anchor_verification_status"])
        self.assertEqual(
            ["wave", "nod", "shrug", "tilt", "shake", "think"],
            metadata["continuity_anchors"][0]["key_terms"],
        )
        self.assertTrue(all(
            term in row["content"]
            for term in ("wave", "nod", "shrug", "tilt", "shake", "think")
        ))

    def test_key_term_retention_accepts_clear_grammatical_variants(self):
        self.assertTrue(EpisodeCompactor._key_term_preserved(
            "wave shrug nod", "They were waving, shrugging, and nodding.",
        ))

    def test_repeated_qa_filler_is_deprioritized_by_extraction_contract(self):
        messages = exchanges(30, prefix="repeat this test again")
        provider = _AnchorProvider([{
            "detail": "They visited a distinctive glass observatory",
            "source_record_indices": [4, 5],
        }])
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        extraction_prompt = next(
            prompt for prompt, _seed in provider.calls
            if "Extract a SMALL source-grounded set" in prompt
        )
        summary_prompt = next(
            prompt for prompt, _seed in provider.calls
            if "Create a compact, neutral third-person account" in prompt
        )
        row = self.store.connection.execute(
            "SELECT legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row[0])

        self.assertIn("repeated QA/testing commands", extraction_prompt)
        self.assertIn("include the key members in one anchor", extraction_prompt)
        self.assertIn("a USER question does not establish its", extraction_prompt)
        self.assertIn("An ASSISTANT guess", summary_prompt)
        self.assertEqual(1, metadata["continuity_anchor_count"])
        self.assertIn("glass observatory", metadata["continuity_anchors"][0]["detail"])

    def test_duplicate_anchors_collapse_and_anchor_count_is_bounded(self):
        messages = exchanges(30)
        raw = []
        for index in range(MAX_CONTINUITY_ANCHORS + 5):
            raw.append({
                "detail": (
                    "A unique observatory stargazing event"
                    if index < 2 else f"Distinctive supported event number {index}"
                ),
                "source_record_indices": [index % 12],
            })
        provider = _AnchorProvider(raw, always_fail=True)
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        metadata = json.loads(self.store.connection.execute(
            "SELECT legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()[0])

        self.assertLessEqual(metadata["continuity_anchor_count"], MAX_CONTINUITY_ANCHORS)
        self.assertEqual(1, sum(
            "observatory" in value["detail"] for value in metadata["continuity_anchors"]
        ))

    def test_verifier_detects_missing_anchor_and_refines_at_most_once(self):
        messages = exchanges(30)
        provider = _AnchorProvider([{
            "detail": "A named comet became their shared callback",
            "source_record_indices": [0, 1],
        }], restore_on_refine=False, always_fail=True)
        report = self.cache.rebuild(messages, EpisodeCompactor(provider))
        row = self.store.connection.execute(
            "SELECT content, legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])

        self.assertEqual(2, provider.verify_calls)
        self.assertEqual(1, provider.refine_calls)
        self.assertEqual("fallback", metadata["anchor_verification_status"])
        self.assertEqual(1, metadata["anchor_missing_count"])
        self.assertIn("named comet", row["content"])
        self.assertEqual(1, report.anchor_verification_failed_episode_count)
        self.assertEqual(CONTINUITY_ANCHOR_VERSION, metadata["continuity_anchor_version"])

    def test_malformed_verifier_uses_one_refinement_then_safe_fallback(self):
        messages = exchanges(30)
        provider = _AnchorProvider([{
            "detail": "A named comet became their shared callback",
            "source_record_indices": [0, 1],
        }], malformed_verifier=True)
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        row = self.store.connection.execute(
            "SELECT content, legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])

        self.assertEqual(2, provider.verify_calls)
        self.assertEqual(1, provider.refine_calls)
        self.assertEqual("fallback", metadata["anchor_verification_status"])
        self.assertIn("named comet", row["content"])

    def test_source_change_changes_derived_seed_and_identity(self):
        messages = exchanges(30)
        provider = _DeterministicLocalProvider()
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        before = self.lower_snapshot()[0]
        changed = json.loads(json.dumps(messages))
        changed[0]["content"] += " Material source change."
        self.cache.rebuild(changed, EpisodeCompactor(provider))
        after = self.lower_snapshot()[0]

        self.assertNotEqual(before["source_digest"], after["source_digest"])
        self.assertNotEqual(before["seed"], after["seed"])
        self.assertNotEqual(before["summary_id"], after["summary_id"])

    def test_generator_or_compaction_version_changes_seed_and_identity(self):
        boundary = deterministic_episode_boundaries(exchanges(30))[0]
        identity_digest = "a" * 64
        baseline_seed = deterministic_derived_seed(
            boundary.source_digest, provider_identity_digest=identity_digest,
        )
        self.assertNotEqual(baseline_seed, deterministic_derived_seed(
            boundary.source_digest,
            provider_identity_digest=identity_digest,
            generator_version=str(int(GENERATOR_VERSION) + 1),
        ))
        self.assertNotEqual(baseline_seed, deterministic_derived_seed(
            boundary.source_digest,
            provider_identity_digest=identity_digest,
            compaction_version=COMPACTION_VERSION + 1,
        ))
        baseline_id = deterministic_episode_id(
            self.character_id, boundary, provider_identity_digest=identity_digest,
        )
        self.assertNotEqual(baseline_id, deterministic_episode_id(
            self.character_id,
            boundary,
            provider_identity_digest=identity_digest,
            generator_version=str(int(GENERATOR_VERSION) + 1),
        ))
        self.assertNotEqual(baseline_id, deterministic_episode_id(
            self.character_id,
            boundary,
            provider_identity_digest=identity_digest,
            segmentation_version=SEGMENTATION_VERSION + 1,
        ))

    def test_distinct_source_ranges_receive_distinct_derived_seeds(self):
        boundaries = deterministic_episode_boundaries(exchanges(100))
        identity_digest = "b" * 64
        seeds = [deterministic_derived_seed(
            boundary.source_digest, provider_identity_digest=identity_digest,
        ) for boundary in boundaries]
        self.assertEqual(len(seeds), len(set(seeds)))

    def test_derived_seed_does_not_consume_live_turn_fresh_seed(self):
        messages = exchanges(30)
        provider = _DeterministicLocalProvider()
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        self.assertEqual(0, provider.live_seed_calls)

        provider.generate([], "ordinary assistant turn")
        self.assertEqual(1, provider.live_seed_calls)

    def test_failed_rebuild_leaves_previous_valid_generation_atomic(self):
        messages = exchanges(100)
        self.cache.rebuild(messages, EpisodeCompactor(_DeterministicLocalProvider()))
        before = self.lower_snapshot()
        before_context = self.cache.select_for_context(messages).context_block

        with self.assertRaises(RuntimeError):
            self.cache.rebuild(
                messages,
                EpisodeCompactor(_DeterministicLocalProvider(fail_after=1)),
            )

        self.assertEqual(before, self.lower_snapshot())
        self.assertEqual(before_context, self.cache.select_for_context(messages).context_block)

    def test_rollover_cache_is_valid_at_99_messages_and_schedules_at_80(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(25, prefix="new") + [
            message("user", "latest incomplete rollover query", 999),
        ]
        self.assertEqual(99, len(messages) - 152)
        rollover = self.rollover(_Provider)

        selection = rollover.select_for_context(messages)

        self.assertIsNotNone(selection)
        self.assertEqual(99, rollover.metrics.suffix_message_count)
        self.assertEqual(EPISODE_ROLLOVER_TRIGGER_MESSAGES, rollover.metrics.trigger_message_count)
        self.assertEqual(2, rollover.metrics.state_code)
        self.assertTrue(rollover.metrics.selector_available)

    def test_preexpiry_rollover_reuses_stable_rows_and_publishes_new_suffix(self):
        initial = exchanges(100)
        self.rebuild(initial)
        archive_bytes = json.dumps(initial, sort_keys=True).encode("utf-8")
        messages = initial + exchanges(16, prefix="new")
        events = []
        rollover = EpisodeCompactionRollover(
            self.cache,
            lambda: EpisodeCompactor(_Provider()),
            event_callback=lambda event, data: events.append((event, dict(data))),
            retry_seconds=0.0,
        )

        before = rollover.select_for_context(messages)
        self.assertEqual(80, len(messages) - before.raw_start_index)
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(rollover.wait(5.0))
        after = self.cache.select_for_context(messages)

        self.assertIsNotNone(after)
        self.assertEqual(48, len(messages) - after.raw_start_index)
        self.assertGreater(after.raw_start_index, before.raw_start_index)
        self.assertEqual(archive_bytes, json.dumps(initial, sort_keys=True).encode("utf-8"))
        self.assertIn("episode_cache_rollover_published", [event for event, _ in events])
        published = next(data for event, data in events if event == "episode_cache_rollover_published")
        self.assertGreaterEqual(published["episode_cache_reused_episode_count"], 1)
        self.assertGreaterEqual(published["episode_cache_generated_episode_count"], 1)

    def test_in_progress_rollover_keeps_selector_available_across_99_to_101(self):
        initial = exchanges(100)
        self.rebuild(initial)
        provider = _BlockingProvider()
        rollover = EpisodeCompactionRollover(
            self.cache,
            lambda: EpisodeCompactor(provider),
            retry_seconds=0.0,
        )
        scheduled_messages = initial + exchanges(16, prefix="scheduled")
        self.assertIsNotNone(rollover.select_for_context(scheduled_messages))
        self.assertTrue(rollover.start_pending(scheduled_messages))
        self.assertTrue(provider.started.wait(2.0))

        at_99 = initial + exchanges(25, prefix="progress") + [
            message("user", "distinct callback at ninety nine", 998),
        ]
        at_101 = at_99 + [
            message("assistant", "distinct answer at one hundred", 999),
            message("user", "Ridley yesterday?", 1000),
        ]
        selection_99 = rollover.select_for_context(at_99)
        selection_101 = rollover.select_for_context(at_101)

        self.assertIsNotNone(selection_99)
        self.assertIsNotNone(selection_101)
        self.assertGreater(selection_101.total_episode_count, 0)
        self.assertEqual(COMPACTION_VERSION, selection_101.compaction_version)
        self.assertTrue(rollover.metrics.temporary_grace_active)
        conversation = Conversation(
            object(),
            episode_compaction_cache=self.cache,
            episode_compaction_rollover=rollover,
        )
        conversation.messages = list(at_101)
        conversation.summary_data = {"summary": "legacy must not activate", "summarized_messages": 152}
        context = conversation.build_context(_Memory(), at_101[-1]["content"])
        metrics = conversation._last_context_hygiene_metrics
        self.assertGreater(metrics["episode_count"], 0)
        self.assertGreater(metrics["episode_context_count"], 0)
        self.assertEqual(COMPACTION_VERSION, metrics["compaction_version"])
        self.assertTrue(metrics["episode_cache_selector_available"])
        self.assertTrue(metrics["episode_cache_temporary_grace_active"])
        self.assertFalse(any(
            "legacy must not activate" in str(item.get("content", ""))
            for item in context
        ))
        provider.release.set()
        self.assertTrue(rollover.wait(5.0))
        after = self.cache.select_for_context(at_101)
        self.assertIsNotNone(after)
        self.assertEqual(69, len(at_101) - after.raw_start_index)

    def test_rollover_failure_preserves_cache_and_retries_without_overlap(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(16, prefix="new")
        providers = [_FailingProvider(), _Provider()]
        rollover = EpisodeCompactionRollover(
            self.cache,
            lambda: EpisodeCompactor(providers.pop(0)),
            retry_seconds=0.0,
        )
        before_generation = self.lower_generation_id()
        rollover.select_for_context(messages)
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(rollover.wait(5.0))
        self.assertEqual(before_generation, self.lower_generation_id())
        self.assertIsNotNone(rollover.select_for_context(messages))

        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(rollover.wait(5.0))
        self.assertNotEqual(before_generation, self.lower_generation_id())

    def test_stale_rollover_cannot_replace_newer_explicit_generation(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(16, prefix="new")
        provider = _BlockingProvider()
        events = []
        rollover = EpisodeCompactionRollover(
            self.cache,
            lambda: EpisodeCompactor(provider),
            event_callback=lambda event, data: events.append((event, dict(data))),
            retry_seconds=0.0,
        )
        rollover.select_for_context(messages)
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(provider.started.wait(2.0))

        self.cache.rebuild(messages, EpisodeCompactor(_Provider()))
        newer_generation = self.lower_generation_id()
        provider.release.set()
        self.assertTrue(rollover.wait(5.0))

        self.assertEqual(newer_generation, self.lower_generation_id())
        self.assertIn(
            "episode_cache_rollover_stale_rejected",
            [event for event, _ in events],
        )

    def test_only_one_rollover_worker_runs_for_one_character(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(16, prefix="new")
        provider = _BlockingProvider()
        first = EpisodeCompactionRollover(
            self.cache, lambda: EpisodeCompactor(provider), retry_seconds=0.0,
        )
        second = EpisodeCompactionRollover(
            self.cache, lambda: EpisodeCompactor(_Provider()), retry_seconds=0.0,
        )
        first.select_for_context(messages)
        second.select_for_context(messages)
        self.assertTrue(first.start_pending(messages))
        self.assertTrue(provider.started.wait(2.0))
        self.assertFalse(second.start_pending(messages))
        provider.release.set()
        self.assertTrue(first.wait(5.0))

    def test_rollover_selection_has_no_source_overlap_or_gap(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(16, prefix="new")
        rollover = self.rollover(_Provider)
        rollover.select_for_context(messages)
        rollover.start_pending(messages)
        self.assertTrue(rollover.wait(5.0))

        selected = self.cache.select_for_context(messages)
        rows = self.store.connection.execute(
            """SELECT r.start_sequence, r.end_sequence
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence""",
            (self.character_id,),
        ).fetchall()
        expected = 0
        for row in rows:
            self.assertEqual(expected + 1, row["start_sequence"])
            expected = row["end_sequence"]
        self.assertEqual(selected.raw_start_index, expected)
        self.assertEqual(len(messages), expected + len(messages[selected.raw_start_index:]))

    def test_entity_retrieval_decision_is_unchanged_by_rollover(self):
        initial = exchanges(400)
        self.rebuild(initial)
        row = self.store.connection.execute(
            """SELECT s.summary_id, s.legacy_metadata_json
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence LIMIT 1 OFFSET 1""",
            (self.character_id,),
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])
        start = int(metadata["source_start_index"])
        metadata["continuity_anchors"] = [{
            "anchor_id": "A1",
            "detail": "The participants met Moonwhisker at the northern lookout",
            "source_record_indices": [start],
            "key_terms": ["Moonwhisker"],
        }]
        metadata["continuity_anchor_count"] = 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )
        messages = initial + exchanges(16, prefix="new")
        messages[-2]["content"] = "Do you remember Moonwhisker?"
        rollover = self.rollover(_Provider)

        before = rollover.select_for_context(messages)
        self.assertEqual("entity", before.retrieval_signal)
        before_range = (
            before.retrieved_source_start_index,
            before.retrieved_source_end_index_exclusive,
        )
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(rollover.wait(5.0))
        after = rollover.select_for_context(messages)

        self.assertEqual("entity", after.retrieval_signal)
        self.assertEqual(before_range, (
            after.retrieved_source_start_index,
            after.retrieved_source_end_index_exclusive,
        ))
        self.assertEqual(1, after.retrieved_episode_count)

    def test_temporal_retrieval_decision_is_unchanged_by_rollover(self):
        initial = temporal_exchanges()
        self.rebuild(initial)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The participants played the comet catch game together",
            "source_record_indices": [490],
            "key_terms": ["comet catch", "game"],
        }])
        additions = exchanges(16, prefix="new")
        for index, item in enumerate(additions):
            item["timestamp"] = f"2026-01-15T15:{index // 60:02d}:{index % 60:02d}"
        messages = initial + additions
        messages[-2]["content"] = "What were we playing yesterday?"
        rollover = self.rollover(_Provider)

        before = rollover.select_for_context(messages)
        self.assertEqual("temporal_activity", before.retrieval_signal)
        before_range = (
            before.retrieved_source_start_index,
            before.retrieved_source_end_index_exclusive,
        )
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(rollover.wait(5.0))
        after = rollover.select_for_context(messages)

        self.assertEqual("temporal_activity", after.retrieval_signal)
        self.assertEqual(before_range, (
            after.retrieved_source_start_index,
            after.retrieved_source_end_index_exclusive,
        ))
        self.assertEqual(1, after.retrieved_episode_count)

    def test_close_rejects_late_rollover_publication(self):
        initial = exchanges(100)
        self.rebuild(initial)
        messages = initial + exchanges(16, prefix="new")
        provider = _BlockingProvider()
        rollover = self.rollover(lambda: provider)
        prior_generation = self.lower_generation_id()
        rollover.select_for_context(messages)
        self.assertTrue(rollover.start_pending(messages))
        self.assertTrue(provider.started.wait(2.0))

        rollover.close()
        provider.release.set()
        self.assertTrue(rollover.wait(5.0))

        self.assertEqual(prior_generation, self.lower_generation_id())
        self.assertIsNotNone(self.cache.select_for_context(messages))

    def test_boundaries_are_deterministic_and_keep_complete_exchanges(self):
        messages = exchanges(100)
        first = deterministic_episode_boundaries(messages)
        second = deterministic_episode_boundaries(messages)

        self.assertEqual(first, second)
        self.assertEqual([(0, 80), (80, 152)], [
            (item.start_index, item.end_index_exclusive) for item in first
        ])
        for boundary in first:
            self.assertEqual("user", messages[boundary.start_index]["role"])
            self.assertEqual("assistant", messages[boundary.end_index_exclusive - 1]["role"])
            self.assertEqual(0, boundary.source_record_count % 2)

    def test_generated_scene_ui_run_is_one_scope_coherent_source_group(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scope = {"kind": "real_world", "scope_id": real_id}
        messages = scoped_exchanges(3, scope, prefix="before")
        messages.extend(generated_scene_record(index, scope) for index in range(3))
        assistant = message("assistant", "I reacted once to the complete scene update.", 30)
        assistant["truth_scope"] = dict(scope)
        messages.append(assistant)
        messages.extend(scoped_exchanges(2, scope, prefix="after", start_index=20))

        groups = canonical_episode_source_groups(messages, valid_scope_ids={real_id})
        self.assertEqual(6, len(groups))
        self.assertEqual("generated_scene_ui_run", groups[3].source_kind)
        self.assertEqual((6, 10), (
            groups[3].start_index, groups[3].end_index_exclusive,
        ))
        boundaries = deterministic_episode_boundaries(
            messages, max_exchanges=40, recent_exchanges=0,
            valid_scope_ids={real_id},
        )
        self.assertEqual(1, len(boundaries))
        self.assertEqual(6, boundaries[0].exchange_count)
        self.assertEqual(len(messages), boundaries[0].end_index_exclusive)

    def test_arbitrary_consecutive_users_remain_outside_episode_coverage(self):
        messages = exchanges(3)
        messages.extend([
            message("user", "One ordinary user turn.", 20),
            message("user", "A second ordinary user turn.", 21),
            message("assistant", "One assistant response.", 22),
        ])
        groups = canonical_episode_source_groups(messages)
        self.assertEqual(3, len(groups))
        self.assertEqual(6, groups[-1].end_index_exclusive)

    def test_explicit_scope_transition_preserves_both_records_without_mixing(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scenario_id = self.create_scenario_scope("Transition scenario", sequence=1)
        user = message("user", "A real-world lead-in.", 1)
        user["truth_scope"] = {"kind": "real_world", "scope_id": real_id}
        assistant = message("assistant", "The scenario begins.", 2)
        assistant["truth_scope"] = {"kind": "scenario", "scope_id": scenario_id}

        groups = canonical_episode_source_groups(
            [user, assistant], valid_scope_ids={real_id, scenario_id},
        )
        self.assertEqual(2, len(groups))
        self.assertEqual([
            (0, 1, "real_world", "scope_transition_record"),
            (1, 2, "scenario", "scope_transition_record"),
        ], [
            (value.start_index, value.end_index_exclusive,
             value.scope.kind, value.source_kind)
            for value in groups
        ])

    def test_generated_scene_ui_run_rejects_mixed_scope_and_unbounded_run(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scenario_id = self.create_scenario_scope("Scenario", sequence=1)
        real = {"kind": "real_world", "scope_id": real_id}
        scenario = {"kind": "scenario", "scope_id": scenario_id}
        mixed = [generated_scene_record(0, real), generated_scene_record(1, scenario)]
        assistant = message("assistant", "Synthetic response.", 3)
        assistant["truth_scope"] = dict(real)
        mixed.append(assistant)
        self.assertEqual((), canonical_episode_source_groups(
            mixed, valid_scope_ids={real_id, scenario_id},
        ))

        oversized = [
            generated_scene_record(index, real)
            for index in range(MAX_GENERATED_SCENE_RECORDS_PER_INTERACTION + 1)
        ]
        oversized.append(assistant)
        self.assertEqual((), canonical_episode_source_groups(
            oversized, valid_scope_ids={real_id},
        ))

    def test_generic_recollection_wording_cannot_project_episode_source_records(self):
        messages = [
            message("user", "What do you remember from that time?", 0),
            message("assistant", "I remember a specific time long ago.", 1),
        ]
        candidate = EpisodeRetrievalCandidate(
            "episode", "generic summary", 8, "generation",
            "legacy_untagged", "", 0, 2, 1, 2,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
            scope_state="unknown_scope",
        )
        metadata = {"continuity_anchors": [{
            "detail": "The assistant remembered a specific time",
            "key_terms": ["specific time"],
            "source_record_indices": [1],
        }]}
        self.assertEqual((), _historical_episode_source_refinements(
            messages, candidate, metadata,
            "What's something oddly specific you remember me telling you a long time ago?",
        ))

    def test_source_refinement_exposes_unsupported_summary_speaker_attribution(self):
        messages = [
            message("user", "game.", 0),
            message(
                "assistant",
                "I previously mentioned the unusual Game Boy cartridge collection.",
                1,
            ),
        ]
        candidate = EpisodeRetrievalCandidate(
            "episode", "The user owns a Game Boy collection.", 10, "generation",
            "legacy_untagged", "", 0, 2, 1, 2,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
            scope_state="unknown_scope",
        )
        metadata = {"continuity_anchors": [{
            "detail": "The user owns an unusual Game Boy collection",
            "key_terms": ["Game Boy collection"],
            "source_record_indices": [1],
        }]}
        refinements = _historical_episode_source_refinements(
            messages, candidate, metadata,
            "What did you tell me about the Game Boy collection?",
        )
        self.assertEqual(1, len(refinements))
        self.assertEqual("assistant", refinements[0].speaker_role)
        self.assertEqual(
            "user_attribution_unsupported", refinements[0].attribution_state,
        )

    def test_shared_validator_accepts_rebuilt_generated_scene_ui_source_group(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scope = {"kind": "real_world", "scope_id": real_id}
        messages = scoped_exchanges(30, scope, prefix="scene-validation")
        generated = [generated_scene_record(index, scope) for index in range(3)]
        assistant = message("assistant", "One response to the complete scene update.", 50)
        assistant["truth_scope"] = dict(scope)
        messages[12:12] = [*generated, assistant]

        self.cache.rebuild(messages, EpisodeCompactor(_DeterministicLocalProvider()))
        validation = self.cache.validate_for_context(
            messages, active_truth_scope=scope,
        )
        self.assertTrue(validation.accepted, validation.reason)
        self.assertEqual(1, len([value for value in validation.lower_records if value.accepted]))
        self.assertEqual(16, validation.raw_start_index)

    def test_scope_boundaries_never_mix_real_world_scenarios_or_legacy(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scenario_a_id = self.create_scenario_scope("Scenario A", sequence=1)
        scenario_b_id = self.create_scenario_scope("Scenario B", sequence=2)
        messages = (
            exchanges(4, prefix="legacy")
            + scoped_exchanges(4, {"kind": "real_world", "scope_id": real_id}, prefix="real")
            + scoped_exchanges(4, {"kind": "scenario", "scope_id": scenario_a_id}, prefix="a")
            + scoped_exchanges(4, {"kind": "scenario", "scope_id": scenario_b_id}, prefix="b")
        )
        boundaries = deterministic_episode_boundaries(
            messages, max_exchanges=40, recent_exchanges=0,
            valid_scope_ids={real_id, scenario_a_id, scenario_b_id},
        )
        self.assertEqual([
            ("legacy_untagged", "", 0, 8),
            ("real_world", real_id, 8, 16),
            ("scenario", scenario_a_id, 16, 24),
            ("scenario", scenario_b_id, 24, 32),
        ], [
            (item.truth_scope_kind, item.truth_scope_id,
             item.start_index, item.end_index_exclusive)
            for item in boundaries
        ])

    def test_scoped_episode_selection_excludes_other_scenarios_and_restores_own_scope(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scenario_a_id = self.create_scenario_scope("Scenario A", sequence=1)
        scenario_b_id = self.create_scenario_scope("Scenario B", sequence=2)
        messages = (
            exchanges(30, prefix="legacy")
            + scoped_exchanges(30, {"kind": "real_world", "scope_id": real_id}, prefix="real")
            + scoped_exchanges(30, {"kind": "scenario", "scope_id": scenario_a_id}, prefix="scenario-a")
            + scoped_exchanges(30, {"kind": "scenario", "scope_id": scenario_b_id}, prefix="scenario-b")
        )
        self.rebuild(messages)

        real = self.cache.select_for_context(
            messages, active_truth_scope={"kind": "real_world", "scope_id": real_id},
        )
        scenario_a = self.cache.select_for_context(
            messages, active_truth_scope={"kind": "scenario", "scope_id": scenario_a_id},
        )
        scenario_b = self.cache.select_for_context(
            messages, active_truth_scope={"kind": "scenario", "scope_id": scenario_b_id},
        )
        self.assertIsNotNone(real)
        self.assertIsNotNone(scenario_a)
        self.assertIsNotNone(scenario_b)
        self.assertEqual(120, real.represented_source_record_count)
        self.assertEqual(120, scenario_a.represented_source_record_count)
        self.assertEqual(72, scenario_b.represented_source_record_count)

        rows = self.store.connection.execute(
            "SELECT legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchall()
        for row in rows:
            metadata = json.loads(row[0])
            identities = {
                tuple(messages[index].get("truth_scope", {}).get(key, "") for key in ("kind", "scope_id"))
                if "truth_scope" in messages[index] else ("legacy_untagged", "")
                for index in range(
                    metadata["source_start_index"], metadata["source_end_index_exclusive"], 2,
                )
            }
            self.assertEqual({(
                metadata["truth_scope_kind"], metadata["truth_scope_id"],
            )}, identities)

    def test_entity_and_temporal_retrieval_do_not_cross_scope(self):
        real_id = self.store.default_truth_scope_id(self.character_id)
        scenario_a_id = self.create_scenario_scope("Scenario A", sequence=1)
        scenario_b_id = self.create_scenario_scope("Scenario B", sequence=2)
        scenario_scope = {"kind": "scenario", "scope_id": scenario_a_id}
        real_scope = {"kind": "real_world", "scope_id": real_id}
        messages = (
            scoped_exchanges(40, scenario_scope, prefix="scenario-a")
            + scoped_exchanges(40, real_scope, prefix="real")
        )
        messages[10]["content"] = "We met Moonwhisker while playing comet catch."
        messages[10]["timestamp"] = "2026-01-14T12:00:00Z"
        messages[11]["timestamp"] = "2026-01-14T12:00:01Z"
        messages[-2]["content"] = "Do you remember Moonwhisker from yesterday?"
        messages[-2]["timestamp"] = "2026-01-15T12:00:00Z"
        self.rebuild(messages)
        self.set_episode_anchors(0, [{
            "anchor_id": "A1",
            "detail": "The participants met Moonwhisker while playing comet catch",
            "source_record_indices": [10],
            "key_terms": ["Moonwhisker", "comet catch"],
        }])

        real = self.cache.select_for_context(messages, active_truth_scope=real_scope)
        self.assertIsNotNone(real)
        self.assertEqual("none", real.retrieval_signal)
        self.assertNotIn("Moonwhisker", real.context_block)

        scenario_query = scoped_exchanges(1, scenario_scope, prefix="scenario-return", start_index=500)
        scenario_query[0]["content"] = "What were we playing yesterday, when we met Moonwhisker?"
        scenario_query[0]["timestamp"] = "2026-01-15T12:00:00Z"
        scenario_query[1]["timestamp"] = "2026-01-15T12:00:01Z"
        scenario_messages = messages + scenario_query
        restored = self.cache.select_for_context(
            scenario_messages, active_truth_scope=scenario_scope,
        )
        self.assertIsNotNone(restored)
        self.assertIn(restored.retrieval_signal, {"temporal_entity", "temporal_activity", "entity"})
        self.assertIn("Moonwhisker", restored.context_block)
        self.assertTrue(all(
            parse_canonical_truth_scope(scenario_messages[index]).scope_id == scenario_a_id
            for start, end in restored.temporal_source_ranges
            for index in range(start, end)
        ))

        scenario_b_query = scoped_exchanges(
            1, {"kind": "scenario", "scope_id": scenario_b_id},
            prefix="scenario-b", start_index=520,
        )
        scenario_b_query[0]["content"] = "Do you remember Moonwhisker?"
        isolated_b = self.cache.select_for_context(
            messages + scenario_b_query,
            active_truth_scope={"kind": "scenario", "scope_id": scenario_b_id},
        )
        self.assertIsNone(isolated_b)

    def test_incomplete_latest_user_is_never_compacted(self):
        messages = exchanges(30)
        latest = message("user", "A unique latest user request must remain verbatim.", 99)
        messages.append(latest)
        report = self.rebuild(messages)
        selection = self.cache.select_for_context(messages)

        self.assertEqual(12, report.coverage_end_index_exclusive)
        self.assertIsNotNone(selection)
        self.assertEqual(latest, messages[selection.raw_start_index:][-1])

    def test_backfill_preserves_archive_bytes_and_exact_source_provenance(self):
        messages = exchanges(70)
        archive = Path(self.temp.name) / "conversation.json"
        archive.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        before = hashlib.sha256(archive.read_bytes()).hexdigest()

        report = self.rebuild(messages)
        after = hashlib.sha256(archive.read_bytes()).hexdigest()
        rows = self.store.connection.execute(
            """SELECT s.legacy_metadata_json, r.start_sequence, r.end_sequence
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence""",
            (self.character_id,),
        ).fetchall()

        self.assertEqual(before, after)
        self.assertEqual(2, report.episode_count)
        self.assertEqual(report.episode_count, len(rows))
        for row in rows:
            metadata = json.loads(row["legacy_metadata_json"])
            start = metadata["source_start_index"]
            end = metadata["source_end_index_exclusive"]
            self.assertEqual(start + 1, row["start_sequence"])
            self.assertEqual(end, row["end_sequence"])
            self.assertEqual(
                [canonical_record_id(index, messages[index]) for index in range(start, end)],
                metadata["source_record_ids"],
            )

    def test_version_mismatch_disables_cache_and_explicit_rebuild_restores_it(self):
        messages = exchanges(30)
        self.rebuild(messages)
        row = self.store.connection.execute(
            "SELECT summary_id, legacy_metadata_json FROM summaries WHERE summary_level='episode_compaction'"
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["compaction_version"] = COMPACTION_VERSION + 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

        self.assertIsNone(self.cache.select_for_context(messages))
        self.rebuild(messages)
        self.assertIsNotNone(self.cache.select_for_context(messages))

    def test_missing_or_corrupt_cache_fails_open_and_can_be_rebuilt(self):
        messages = exchanges(30)
        self.assertIsNone(self.cache.select_for_context(messages))
        self.rebuild(messages)
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json='{' WHERE summary_level='episode_compaction'"
        )
        self.assertIsNone(self.cache.select_for_context(messages))
        self.rebuild(messages)
        self.assertIsNotNone(self.cache.select_for_context(messages))

    def test_context_replaces_older_raw_dialogue_and_keeps_recent_suffix_verbatim(self):
        messages = exchanges(30)
        self.rebuild(messages)
        conversation = Conversation(object(), episode_compaction_cache=self.cache)
        conversation.messages = messages
        context = conversation.build_context(_Memory(), messages[-2]["content"])
        contents = [item["content"] for item in context]

        self.assertTrue(any("Derived episodic conversation background" in item for item in contents))
        self.assertFalse(any(messages[0]["content"] == item for item in contents))
        for recent in messages[12:]:
            self.assertIn(recent, context)
        self.assertEqual(messages[-1], context[-1])
        self.assertEqual(12, conversation._last_context_hygiene_metrics["episode_source_record_count"])

    def test_context_hygiene_still_filters_only_the_raw_recent_suffix(self):
        messages = exchanges(30)
        repeated = (
            "I understand this synthetic test became repetitive and will now move the conversation "
            "toward a genuinely fresh distinct topic with a playful response."
        )
        for assistant_index in (55, 57, 59):
            messages[assistant_index]["content"] = repeated
        self.rebuild(messages)
        conversation = Conversation(object(), episode_compaction_cache=self.cache)
        conversation.messages = messages
        context = conversation.build_context(_Memory(), messages[-2]["content"])

        self.assertEqual(2, conversation._last_context_hygiene_metrics["context_hygiene_suppressed"])
        self.assertEqual(messages[-1], context[-1])

    def test_provider_switch_keeps_identical_aifren_selected_context(self):
        messages = exchanges(581)
        self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
        # Compare provider selection at the same application time. A live
        # second boundary must not look like provider-dependent context.
        conversation = Conversation(
            object(), episode_compaction_cache=self.cache,
            clock=lambda: datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        conversation.messages = messages
        local = conversation.build_context(_Memory(), messages[-2]["content"])
        conversation.llm = object()  # Simulate Local -> Online provider replacement.
        online = conversation.build_context(_Memory(), messages[-2]["content"])

        self.assertEqual(local, online)
        self.assertEqual(1, conversation._last_context_hygiene_metrics["consolidated_episode_count"])

    def test_source_edit_invalidates_derived_cache_without_mutating_archive(self):
        messages = exchanges(30)
        self.rebuild(messages)
        changed = json.loads(json.dumps(messages))
        changed[0]["content"] = "A corrected canonical source record."

        self.assertIsNone(self.cache.select_for_context(changed))
        self.assertIsNotNone(self.cache.select_for_context(messages))

    def test_old_character_backfill_is_bounded_and_keeps_latest_user(self):
        messages = exchanges(581)
        report = self.rebuild(messages)
        selection = self.cache.select_for_context(messages)

        self.assertEqual(14, report.episode_count)
        self.assertEqual(1114, report.source_record_count)
        self.assertIsNotNone(selection)
        self.assertEqual(8, selection.selected_episode_count)
        self.assertEqual(messages[-2:], messages[selection.raw_start_index:][-2:])

    def test_current_turn_retrieves_source_grounded_episode_within_existing_bound(self):
        messages = exchanges(581)
        messages[-2]["content"] = "Do you remember the crystal observatory visit?"
        self.rebuild(messages)
        row = self.store.connection.execute(
            """SELECT s.summary_id, s.content, s.legacy_metadata_json
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence LIMIT 1 OFFSET 2""",
            (self.character_id,),
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])
        start = int(metadata["source_start_index"])
        metadata["continuity_anchors"] = [{
            "anchor_id": "A1",
            "detail": "The participants shared a distinctive crystal observatory visit",
            "source_record_indices": [start],
            "key_terms": ["crystal observatory"],
        }]
        metadata["continuity_anchor_count"] = 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        retrieved = self.cache.select_for_context(messages, enable_retrieval=True)
        typed = self.cache.retrieve_candidates(
            messages, messages[-2]["content"],
        )

        self.assertNotIn(row["content"], baseline.context_block)
        self.assertIn(row["content"], retrieved.context_block)
        self.assertEqual((row["summary_id"],), tuple(
            candidate.record_id for candidate in typed.candidates
        ))
        self.assertEqual("current_valid", typed.validation_state)
        self.assertIn("Older episodes relevant to the current turn", retrieved.context_block)
        self.assertEqual(8, retrieved.selected_episode_count)
        self.assertEqual(1, retrieved.retrieved_episode_count)
        self.assertEqual(80, retrieved.retrieved_source_record_count)
        self.assertEqual(634, retrieved.represented_source_record_count)

    def test_unrelated_current_turn_does_not_retrieve_or_expand_episode_context(self):
        messages = exchanges(581)
        self.rebuild(messages)

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(baseline.context_block, selected.context_block)
        self.assertEqual(0, selected.retrieval_candidate_count)
        self.assertEqual(0, selected.retrieved_episode_count)

    def test_retrieval_subject_and_distinctiveness_gates_are_conservative(self):
        assistant_fact = {"continuity_anchors": [{
            "detail": "Assistant claims Japari buns are their favorite food",
            "key_terms": ["Japari buns"],
        }]}
        rare_entity = {"continuity_anchors": [{
            "detail": "User introduced Moonwhisker during a shared observatory visit",
            "key_terms": ["Moonwhisker"],
        }]}
        generic_story = {"continuity_anchors": [{
            "detail": "Assistant told another short story",
            "key_terms": ["Story"],
        }]}

        self.assertEqual(0, _episode_retrieval_score(
            "What's my favorite food?", assistant_fact, {"japari": 1, "buns": 1},
        ))
        self.assertGreaterEqual(_episode_retrieval_score(
            "Do you remember Moonwhisker?", rare_entity, {"moonwhisker": 1},
        ), 8)
        self.assertEqual(0, _episode_retrieval_score(
            "Tell me a story.", generic_story, {"story": 4},
        ))

    def test_single_anchor_rarity_requires_source_cased_entity_or_identifier(self):
        self.assertFalse(_single_retrieval_key_is_entity_like("think"))
        self.assertFalse(_single_retrieval_key_is_entity_like("friend"))
        self.assertTrue(_single_retrieval_key_is_entity_like("Ridley"))
        self.assertTrue(_single_retrieval_key_is_entity_like("Moonwhisker"))
        self.assertTrue(_single_retrieval_key_is_entity_like("VRM"))
        self.assertTrue(_single_retrieval_key_is_entity_like("RTX3070"))

        think_anchor = {"continuity_anchors": [{
            "detail": "User requested animation testing including a think pose",
            "key_terms": ["think"],
        }]}
        ridley_anchor = {"continuity_anchors": [{
            "detail": "User brought up Ridley during a rainy conversation",
            "key_terms": ["Ridley"],
        }]}
        chrono_anchor = {"continuity_anchors": [{
            "detail": "The participants agreed to play Chrono Trigger together",
            "key_terms": ["Chrono Trigger"],
        }]}

        self.assertEqual(0, _episode_retrieval_score(
            "I think you might have misunderstood.", think_anchor, {"think": 1},
        ))
        self.assertGreaterEqual(_episode_retrieval_score(
            "Do you remember Ridley?", ridley_anchor, {"ridley": 1},
        ), 8)
        self.assertGreaterEqual(_episode_retrieval_score(
            "What did you say about Chrono Trigger?",
            chrono_anchor,
            {"chrono": 1, "trigger": 1},
        ), 8)

    def test_think_phrase_does_not_promote_qa_episode_at_records_720_to_799(self):
        messages = exchanges(581)
        messages[-2]["content"] = (
            "I think you might be a little hard of hearing, but it is okay "
            "if you misunderstand sometime."
        )
        self.rebuild(messages)
        row = self.store.connection.execute(
            """SELECT s.summary_id, s.legacy_metadata_json
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                  AND r.start_sequence=721 AND r.end_sequence=800""",
            (self.character_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["continuity_anchors"] = [{
            "anchor_id": "A1",
            "detail": "User requested animation testing including a think pose",
            "source_record_indices": [728, 742],
            "key_terms": ["think"],
        }]
        metadata["continuity_anchor_count"] = 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(baseline.context_block, selected.context_block)
        self.assertEqual(0, selected.retrieval_candidate_count)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual(baseline.represented_source_record_count, selected.represented_source_record_count)

    def test_precise_temporal_activity_promotes_one_unambiguous_episode(self):
        messages = temporal_exchanges()
        messages[-2]["content"] = "What were we playing yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The participants played the comet catch game together",
            "source_record_indices": [490],
            "key_terms": ["comet catch", "game"],
        }])

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(1, selected.temporal_candidate_count)
        self.assertEqual(1, selected.retrieved_episode_count)
        self.assertEqual("temporal_activity", selected.retrieval_signal)
        self.assertEqual(490, selected.retrieved_source_start_index)
        self.assertEqual(492, selected.retrieved_source_end_index_exclusive)
        self.assertEqual(8, selected.selected_episode_count)
        self.assertEqual(
            baseline.represented_source_record_count - 80 + 2,
            selected.represented_source_record_count,
        )
        self.assertNotEqual(baseline.context_block, selected.context_block)

    def test_temporal_activity_abstains_when_multiple_episodes_are_plausible(self):
        messages = temporal_exchanges()
        messages[-2]["content"] = "What did we talk about yesterday?"
        self.rebuild(messages)

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(2, selected.temporal_candidate_count)
        self.assertEqual(0, selected.temporal_raw_match_count)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual("too_broad", selected.retrieval_result_state)
        self.assertIn("Historical recall scope", selected.context_block)
        self.assertNotIn("Source-grounded historical details", selected.context_block)
        self.assertEqual(
            baseline.represented_source_record_count,
            selected.represented_source_record_count,
        )

    def test_temporal_activity_respects_user_and_assistant_source_ownership(self):
        messages = temporal_exchanges()
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The user played the comet catch game",
            "source_record_indices": [490],
            "key_terms": ["comet catch", "game"],
        }])

        messages[-2]["content"] = "What was I playing yesterday?"
        user_selection = self.cache.select_for_context(messages, enable_retrieval=True)
        self.assertEqual(1, user_selection.retrieved_episode_count)

        messages[-2]["content"] = "What were you playing yesterday?"
        assistant_selection = self.cache.select_for_context(messages, enable_retrieval=True)
        self.assertEqual(0, assistant_selection.temporal_candidate_count)
        self.assertEqual(0, assistant_selection.retrieved_episode_count)

    def test_recent_raw_temporal_activity_wins_over_an_older_episode(self):
        messages = temporal_exchanges()
        # Put both an older compacted and a recent verbatim activity on today.
        messages[490]["timestamp"] = "2026-01-15T08:00:00"
        messages[-4]["content"] = "We were playing leaf toss earlier today."
        messages[-2]["content"] = "What were we playing earlier today?"
        self.rebuild(messages)

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(1, selected.temporal_candidate_count)
        self.assertGreater(selected.temporal_raw_match_count, 0)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual("recent_context_sufficient", selected.retrieval_result_state)
        self.assertEqual(baseline.context_block, selected.context_block)

    def test_recent_raw_temporal_entity_detail_suppresses_older_source(self):
        messages = temporal_exchanges()
        messages[490]["content"] = "We discussed Moonwhisker at the northern lookout."
        messages[490]["timestamp"] = "2026-01-15T08:00:00"
        messages[-4]["content"] = "We discussed Moonwhisker earlier today."
        messages[-2]["content"] = "What did we say about Moonwhisker earlier today?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The participants discussed Moonwhisker at the northern lookout",
            "source_record_indices": [490],
            "key_terms": ["Moonwhisker"],
        }])

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual("recent_context_sufficient", selected.retrieval_result_state)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual(0, selected.temporal_source_span_count)
        self.assertEqual(baseline.context_block, selected.context_block)

    def test_temporal_parser_supports_precise_v1_forms_and_rejects_soft_time(self):
        messages = temporal_exchanges()
        expected = {
            "What were we playing today?": "today",
            "What were we playing earlier today?": "earlier_today",
            "What were we playing yesterday?": "yesterday",
            "What were we playing last night?": "last_night",
            "What were we playing two days ago?": "days_ago",
            "What were we playing 12 days ago?": "days_ago",
            "What were we playing last month?": "last_month",
        }
        for query, expression in expected.items():
            with self.subTest(query=query):
                resolved = _resolve_temporal_retrieval_query(query, messages)
                self.assertIsNotNone(resolved)
                self.assertEqual(expression, resolved.expression)
                self.assertEqual("play", resolved.activity)
        self.assertIsNone(_resolve_temporal_retrieval_query(
            "What were we playing recently?", messages,
        ))
        self.assertIsNone(_resolve_temporal_retrieval_query(
            "What were we playing the other day?", messages,
        ))

    def test_one_temporal_result_uses_bounded_canonical_source_detail(self):
        messages = temporal_exchanges()
        messages[-2]["content"] = "What were we playing yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The user and assistant played the comet catch game together",
            "source_record_indices": [490],
            "key_terms": ["comet catch", "game"],
        }])

        baseline = self.cache.select_for_context(messages, enable_retrieval=False)
        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual("single_result", selected.retrieval_result_state)
        self.assertEqual(1, selected.temporal_source_span_count)
        self.assertEqual(2, selected.temporal_source_record_count)
        self.assertIn("Source-grounded historical details", selected.context_block)
        self.assertIn("comet catch", selected.context_block)
        self.assertNotIn("Synthetic participants completed episode 7", selected.context_block)
        self.assertEqual(
            baseline.represented_source_record_count - 80 + 2,
            selected.represented_source_record_count,
        )

    def test_performed_game_query_labels_topic_material_as_related_not_direct(self):
        messages = temporal_exchanges()
        messages[490]["content"] = "Ridley is a video-game character we discussed."
        messages[492]["content"] = "My favorite game is Chrono Trigger."
        messages[494]["content"] = "I collect Game Boy cartridges."
        messages[-2]["content"] = "What games were we playing yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [
            {
                "anchor_id": "A1", "detail": "Ridley came up in a video-game discussion",
                "source_record_indices": [490], "key_terms": ["Ridley", "video game"],
            },
            {
                "anchor_id": "A2", "detail": "Chrono Trigger was named as a favorite game",
                "source_record_indices": [492], "key_terms": ["Chrono Trigger", "favorite game"],
            },
            {
                "anchor_id": "A3", "detail": "Game Boy cartridges were part of a collection",
                "source_record_indices": [494], "key_terms": ["Game Boy", "cartridges"],
            },
        ])

        selected = self.cache.select_for_context(messages)

        self.assertEqual("related_context", selected.retrieval_result_state)
        self.assertEqual(0, selected.temporal_distinct_result_count)
        self.assertEqual(3, selected.temporal_related_result_count)
        self.assertEqual(3, selected.temporal_source_span_count)
        self.assertIn("Related source-grounded historical context", selected.context_block)
        self.assertIn("do not establish that the activity asked about actually occurred", selected.context_block)
        self.assertIn("Ridley", selected.context_block)
        self.assertIn("Chrono Trigger", selected.context_block)
        self.assertIn("Game Boy", selected.context_block)

    def test_game_discussion_query_treats_same_topic_material_as_direct(self):
        messages = temporal_exchanges()
        messages[490]["content"] = "Ridley is a video-game character we discussed."
        messages[492]["content"] = "We talked about Chrono Trigger and Game Boy cartridges."
        messages[-2]["content"] = "What games did we talk about yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [
            {
                "anchor_id": "A1", "detail": "Ridley came up in a video-game discussion",
                "source_record_indices": [490], "key_terms": ["Ridley", "video game"],
            },
            {
                "anchor_id": "A2", "detail": "Chrono Trigger and Game Boy were discussed",
                "source_record_indices": [492], "key_terms": ["Chrono Trigger", "Game Boy"],
            },
        ])

        selected = self.cache.select_for_context(messages)

        self.assertEqual("multi_result", selected.retrieval_result_state)
        self.assertEqual(2, selected.temporal_distinct_result_count)
        self.assertEqual(0, selected.temporal_related_result_count)
        self.assertIn("Source-grounded historical details", selected.context_block)
        self.assertNotIn("do not establish that the activity", selected.context_block)

    def test_figurative_stop_and_go_game_is_not_direct_performed_game_evidence(self):
        messages = [
            {"role": "user", "content": "What was that about?", "timestamp": "2026-01-14T12:00:00"},
            {
                "role": "assistant",
                "content": (
                    "What was what about? The red light thing? I thought we were still "
                    "playing the stop-and-go game! Did I freeze too fast?"
                ),
                "timestamp": "2026-01-14T12:00:01",
            },
        ]
        query = _resolve_temporal_retrieval_query(
            "What games were we playing yesterday?",
            [*messages, {"role": "user", "content": "probe", "timestamp": "2026-01-15T12:00:00"}],
        )

        self.assertIsNotNone(query)
        self.assertFalse(_source_span_directly_supports_performed_activity(
            messages, 0, 2, query,
        ))
        direct_messages = [{
            "role": "user",
            "content": "We spent the evening playing Chrono Trigger.",
            "timestamp": "2026-01-14T13:00:00",
        }]
        self.assertTrue(_source_span_directly_supports_performed_activity(
            direct_messages, 0, 1, query,
        ))

    def test_multiple_same_category_results_are_bounded_and_chronological(self):
        messages = temporal_exchanges()
        messages[570]["content"] = "We were playing star map relay yesterday."
        messages[-2]["content"] = "What games were we playing yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1",
            "detail": "The participants played the comet catch game",
            "source_record_indices": [490],
            "key_terms": ["comet catch", "game"],
        }])
        self.set_episode_anchors(560, [{
            "anchor_id": "A1",
            "detail": "The participants played the star map relay game",
            "source_record_indices": [570],
            "key_terms": ["star map relay", "game"],
        }])

        selected = self.cache.select_for_context(messages)

        self.assertEqual("multi_result", selected.retrieval_result_state)
        self.assertEqual(2, selected.temporal_distinct_result_count)
        self.assertEqual(2, selected.temporal_source_span_count)
        self.assertEqual(2, selected.temporal_source_episode_count)
        self.assertEqual(((490, 492), (570, 572)), selected.temporal_source_ranges)
        self.assertFalse(selected.temporal_result_truncated)
        self.assertLess(
            selected.context_block.index("comet catch"),
            selected.context_block.index("star map relay"),
        )
        self.assertEqual(8, selected.selected_episode_count)

    def test_temporal_multi_result_truncates_at_explicit_span_bound(self):
        messages = temporal_exchanges()
        anchors = []
        for offset, source_index in enumerate((482, 484, 486, 488, 490, 492), start=1):
            messages[source_index]["content"] = f"We played distinct game {offset} yesterday."
            anchors.append({
                "anchor_id": f"A{offset}",
                "detail": f"The participants played distinct game {offset}",
                "source_record_indices": [source_index],
                "key_terms": [f"distinct game {offset}", "game"],
            })
        messages[-2]["content"] = "What games were we playing yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, anchors)

        selected = self.cache.select_for_context(messages)

        self.assertEqual("multi_result", selected.retrieval_result_state)
        self.assertEqual(6, selected.temporal_distinct_result_count)
        self.assertEqual(4, selected.temporal_source_span_count)
        self.assertTrue(selected.temporal_result_truncated)
        self.assertIn("do not present it as exhaustive", selected.context_block)

    def test_repeated_event_mentions_do_not_duplicate_source_spans(self):
        messages = temporal_exchanges()
        messages[570]["content"] = "We played comet catch again yesterday."
        messages[-2]["content"] = "What games were we playing yesterday?"
        self.rebuild(messages)
        anchor = lambda source: [{
            "anchor_id": "A1", "detail": "The participants played comet catch game",
            "source_record_indices": [source], "key_terms": ["comet catch", "game"],
        }]
        self.set_episode_anchors(480, anchor(490))
        self.set_episode_anchors(560, anchor(570))

        selected = self.cache.select_for_context(messages)

        self.assertEqual("single_result", selected.retrieval_result_state)
        self.assertEqual(1, selected.temporal_distinct_result_count)
        self.assertEqual(1, selected.temporal_source_span_count)
        self.assertFalse(selected.temporal_result_truncated)

    def test_singular_temporal_identity_abstains_when_several_results_fit(self):
        messages = temporal_exchanges()
        messages[570]["content"] = "We played the star map relay game yesterday."
        messages[-2]["content"] = "What was that game yesterday?"
        self.rebuild(messages)
        self.set_episode_anchors(480, [{
            "anchor_id": "A1", "detail": "The participants played comet catch game",
            "source_record_indices": [490], "key_terms": ["comet catch", "game"],
        }])
        self.set_episode_anchors(560, [{
            "anchor_id": "A1", "detail": "The participants played star map relay game",
            "source_record_indices": [570], "key_terms": ["star map relay", "game"],
        }])

        selected = self.cache.select_for_context(messages)

        self.assertEqual("ambiguous", selected.retrieval_result_state)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual(0, selected.temporal_source_span_count)
        self.assertIn("Ask a brief natural clarifying question", selected.context_block)

        messages[-2]["content"] = "Which game were we playing yesterday?"
        selected = self.cache.select_for_context(messages)
        self.assertEqual("ambiguous", selected.retrieval_result_state)
        self.assertEqual(0, selected.temporal_source_span_count)

    def test_broad_calendar_query_adds_scope_control_without_bulk_history(self):
        messages = temporal_exchanges()
        for message_value in messages[:160]:
            message_value["timestamp"] = message_value["timestamp"].replace("2026-01", "2025-12")
        messages[-2]["content"] = "What did we talk about last month?"
        self.rebuild(messages)

        selected = self.cache.select_for_context(messages)

        self.assertEqual("too_broad", selected.retrieval_result_state)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual(0, selected.temporal_source_span_count)
        self.assertIn("Historical recall scope", selected.context_block)

    def test_temporal_query_does_not_weaken_strong_entity_retrieval(self):
        messages = temporal_exchanges()
        messages[490]["content"] = "We met Moonwhisker at the northern lookout."
        messages[-2]["content"] = "Did we mention Moonwhisker yesterday?"
        self.rebuild(messages)
        row = self.store.connection.execute(
            """SELECT s.summary_id, s.legacy_metadata_json
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level='episode_compaction'
                ORDER BY r.start_sequence LIMIT 1 OFFSET 6""",
            (self.character_id,),
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["continuity_anchors"] = [{
            "anchor_id": "A1",
            "detail": "The participants met Moonwhisker at the northern lookout",
            "source_record_indices": [490],
            "key_terms": ["Moonwhisker"],
        }]
        metadata["continuity_anchor_count"] = 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

        selected = self.cache.select_for_context(messages, enable_retrieval=True)

        self.assertEqual(1, selected.retrieved_episode_count)
        self.assertEqual("temporal_entity", selected.retrieval_signal)
        self.assertEqual("single_result", selected.retrieval_result_state)

        messages[-2]["content"] = "Did we mention Moonwhisker today?"
        selected = self.cache.select_for_context(messages, enable_retrieval=True)
        self.assertEqual(0, selected.retrieved_episode_count)
        self.assertEqual("no_match", selected.retrieval_result_state)

    def test_contiguous_selected_run_consolidates_with_exact_provenance(self):
        messages = exchanges(581)
        archive = Path(self.temp.name) / "conversation.json"
        archive.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        before = hashlib.sha256(archive.read_bytes()).hexdigest()

        report = self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
        selection = self.cache.select_for_context(messages)
        era = self.store.connection.execute(
            """SELECT s.*, r.start_sequence, r.end_sequence
                 FROM summaries s JOIN summary_source_ranges r USING(character_id, summary_id)
                WHERE s.character_id=? AND s.summary_level=?""",
            (self.character_id, ERA_SUMMARY_LEVEL),
        ).fetchone()

        self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(1, report.consolidated_episode_count)
        self.assertEqual(7, report.lower_level_episodes_replaced)
        self.assertIsNotNone(selection)
        self.assertEqual(1, selection.consolidated_episode_count)
        self.assertEqual(7, selection.lower_level_episodes_replaced)
        self.assertEqual(2, selection.selected_episode_count)
        self.assertEqual(634, selection.represented_source_record_count)
        metadata = json.loads(era["legacy_metadata_json"])
        self.assertTrue(metadata["retention_gate_passed"])
        self.assertEqual(ERA_RETENTION_GATE_VERSION, metadata["retention_gate_version"])
        self.assertEqual(1, metadata["retention_distinctive_items_checked"])
        self.assertEqual(0, metadata["retention_missing_item_count"])
        self.assertEqual(560, metadata["source_start_index"])
        self.assertEqual(1114, metadata["source_end_index_exclusive"])
        self.assertEqual(561, era["start_sequence"])
        self.assertEqual(1114, era["end_sequence"])
        self.assertEqual(
            [canonical_record_id(index, messages[index]) for index in range(560, 1114)],
            metadata["source_record_ids"],
        )
        self.assertIn("distinctive observatory visit", selection.context_block)

    def test_retention_gate_accepts_repetitive_run_without_distinctive_loss(self):
        messages = exchanges(581)
        provider = _EraProvider(retention_status="pass")
        report = self.cache.rebuild(messages, EpisodeCompactor(provider))
        selection = self.cache.select_for_context(messages)

        self.assertEqual(1, report.retention_gate_checked_count)
        self.assertEqual(0, report.retention_gate_rejected_count)
        self.assertEqual(1, selection.consolidated_episode_count)

    def test_retention_gate_rejects_omitted_distinctive_event_and_selects_lower(self):
        messages = exchanges(581)
        provider = _EraProvider(retention_status="fail")
        report = self.cache.rebuild(messages, EpisodeCompactor(provider))
        selection = self.cache.select_for_context(messages)

        self.assertEqual(1, report.retention_gate_checked_count)
        self.assertEqual(1, report.retention_gate_rejected_count)
        self.assertEqual(0, report.consolidated_episode_count)
        self.assertEqual(0, selection.consolidated_episode_count)
        self.assertEqual(8, selection.selected_episode_count)

    def test_retention_gate_accepts_provider_judged_paraphrase(self):
        messages = exchanges(581)
        provider = _EraProvider(retention_status="pass")
        self.cache.rebuild(messages, EpisodeCompactor(provider))
        verifier_prompt = next(
            prompt for prompt in provider.prompts
            if "Evaluate whether a proposed higher-level" in prompt
        )

        self.assertIn("distinctive observatory visit", verifier_prompt)
        self.assertIn("stargazing at the northern lookout", verifier_prompt)
        self.assertIsNotNone(self.cache.select_for_context(messages))
        self.assertEqual(1, self.cache.select_for_context(messages).consolidated_episode_count)

    def test_retention_verifier_error_parse_or_uncertain_rejects_safely(self):
        messages = exchanges(581)
        for status in ("error", "malformed", "uncertain"):
            with self.subTest(status=status):
                report = self.cache.rebuild(
                    messages, EpisodeCompactor(_EraProvider(retention_status=status))
                )
                selection = self.cache.select_for_context(messages)
                self.assertEqual(1, report.retention_gate_rejected_count)
                self.assertEqual(0, selection.consolidated_episode_count)
                self.assertEqual(8, selection.selected_episode_count)

    def test_rejected_run_keeps_unrelated_lower_episodes_distinct(self):
        messages = exchanges(581)
        report = self.cache.rebuild(messages, EpisodeCompactor(_EraProvider(consolidate=False)))
        selection = self.cache.select_for_context(messages)

        self.assertEqual(0, report.consolidated_episode_count)
        self.assertIsNotNone(selection)
        self.assertEqual(0, selection.consolidated_episode_count)
        self.assertEqual(8, selection.selected_episode_count)
        self.assertEqual(0, selection.lower_level_episodes_replaced)

    def test_corrupt_or_version_mismatched_era_falls_back_to_valid_lower_rows(self):
        messages = exchanges(581)
        self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
        row = self.store.connection.execute(
            "SELECT summary_id, legacy_metadata_json FROM summaries WHERE summary_level=?",
            (ERA_SUMMARY_LEVEL,),
        ).fetchone()
        metadata = json.loads(row["legacy_metadata_json"])
        metadata["compaction_version"] = ERA_COMPACTION_VERSION + 1
        self.store.connection.execute(
            "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
            (json.dumps(metadata), row["summary_id"]),
        )

        selection = self.cache.select_for_context(messages)
        self.assertIsNotNone(selection)
        self.assertEqual(0, selection.consolidated_episode_count)
        self.assertEqual(8, selection.selected_episode_count)

    def test_retention_version_mismatch_or_missing_metadata_falls_back_to_lower(self):
        messages = exchanges(581)
        for mutation in ("version", "missing"):
            with self.subTest(mutation=mutation):
                self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
                row = self.store.connection.execute(
                    "SELECT summary_id, legacy_metadata_json FROM summaries WHERE summary_level=?",
                    (ERA_SUMMARY_LEVEL,),
                ).fetchone()
                metadata = json.loads(row["legacy_metadata_json"])
                if mutation == "version":
                    metadata["retention_gate_version"] = ERA_RETENTION_GATE_VERSION + 1
                else:
                    metadata.pop("retention_gate_passed")
                self.store.connection.execute(
                    "UPDATE summaries SET legacy_metadata_json=? WHERE summary_id=?",
                    (json.dumps(metadata), row["summary_id"]),
                )
                selection = self.cache.select_for_context(messages)
                self.assertEqual(0, selection.consolidated_episode_count)
                self.assertEqual(8, selection.selected_episode_count)

        self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
        era_ids = [row[0] for row in self.store.connection.execute(
            "SELECT summary_id FROM summaries WHERE summary_level=?",
            (ERA_SUMMARY_LEVEL,),
        ).fetchall()]
        self.store.connection.executemany(
            "DELETE FROM summary_source_ranges WHERE character_id=? AND summary_id=?",
            [(self.character_id, summary_id) for summary_id in era_ids],
        )
        self.store.connection.execute(
            "DELETE FROM summaries WHERE summary_level=?",
            (ERA_SUMMARY_LEVEL,),
        )
        selection = self.cache.select_for_context(messages)
        self.assertIsNotNone(selection)
        self.assertEqual(0, selection.consolidated_episode_count)
        self.assertEqual(8, selection.selected_episode_count)

    def test_lower_summaries_remain_rebuildable_but_are_not_double_represented(self):
        messages = exchanges(581)
        self.cache.rebuild(messages, EpisodeCompactor(_EraProvider()))
        consolidated = self.cache.select_for_context(messages)
        lower_only = self.cache.select_for_context(messages, use_consolidated=False)
        lower_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM summaries WHERE summary_level='episode_compaction'",
        ).fetchone()[0]

        self.assertEqual(14, lower_count)
        self.assertEqual(1, consolidated.consolidated_episode_count)
        self.assertEqual(634, consolidated.represented_source_record_count)
        self.assertEqual(8, lower_only.selected_episode_count)
        self.assertEqual(0, lower_only.consolidated_episode_count)
        self.assertNotEqual(consolidated.context_block, lower_only.context_block)


if __name__ == "__main__":
    unittest.main()
