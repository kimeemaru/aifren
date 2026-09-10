import hashlib
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import uuid

from benchmarks.memory_v2.models import RetrievalQuery
from character_registry import CharacterRegistry
from development_staged_runtime import (
    DevelopmentStagedRuntimeError,
    attest_development_staged_runtime,
    development_staged_environment,
)
from memory_v2_episode_compaction import (
    EPISODE_PURPOSE_HISTORICAL,
    EPISODE_SOURCE_HISTORICAL,
    EpisodeCompactionCache,
    EpisodeCompactor,
    canonical_record_id,
)
from memory_v2_historical_evidence import (
    HistoricalEvidenceIndexer,
    open_staged_historical_evidence_writer,
)
from memory_v2_historical_episodes import (
    HistoricalEpisodeError,
    HistoricalEpisodeRebuilder,
)
from memory_v2_hybrid_recall import HistoricalRecallAnchor, HybridMemoryV2Recall
from memory_v2_shadow_writer import default_v2_path
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_staged_clone import create_memory_v2_staged_clone
from memory_v2_store import MemoryV2Store, SemanticRetrievalV2


class _DeterministicHistoricalCompactor:
    model = "synthetic-historical-extractive"
    fresh_request_seeds = True

    def request_sampling_metadata(self):
        return {"temperature": 0.0, "evaluation_only": True}

    def generate(self, _history, prompt, *, seed=None):
        if "Extract a SMALL source-grounded set" in prompt:
            values = []
            for index, content in re.findall(r"\[RECORD (\d+)\] USER: ([^\n]+)", prompt)[:6]:
                values.append({
                    "detail": " ".join(content.split())[:96],
                    "source_record_indices": [int(index)],
                    "key_terms": [],
                })
            return json.dumps({"anchors": values})
        if "Verify whether this compact episode account" in prompt:
            return json.dumps({"status": "pass", "missing_anchor_ids": []})
        if "Identify the strongest sub-run" in prompt:
            return json.dumps({
                "consolidate": False, "first_episode": 0,
                "last_episode": 0, "account": "",
            })
        if "Create a compact, neutral third-person account" in prompt:
            statements = [
                " ".join(value.split())
                for value in re.findall(r"\[RECORD \d+\] USER: ([^\n]+)", prompt)
            ]
            return ("During this historical period, the user said or did: " + "; ".join(
                dict.fromkeys(statements)
            ))[:1150]
        raise AssertionError("unexpected deterministic compaction prompt")


class _UnavailableHistoricalCompactor:
    model = "synthetic-unavailable"

    def generate(self, *_args, **_kwargs):
        raise RuntimeError("synthetic provider unavailable")


class HistoricalEpisodeRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.clone = self.root / "clone"
        self.character_id = str(uuid.uuid4())
        self._build_source()
        self.clone_report = create_memory_v2_staged_clone(
            self.source, self.clone, self.character_id,
        )
        paths = CharacterRegistry(self.clone).runtime_paths(self.character_id)
        self.conversation_path = paths["conversation"]
        self.writer = open_staged_historical_evidence_writer(
            self.clone, self.character_id, self.clone_report.target_database,
        )
        self.indexer = HistoricalEvidenceIndexer(
            self.writer, self.conversation_path, confirm_staged_disposable=True,
        )
        self.compactor = EpisodeCompactor(_DeterministicHistoricalCompactor())

    def tearDown(self):
        if getattr(self, "writer", None) is not None:
            self.writer.close()
        self.temporary.cleanup()

    def _build_source(self):
        character_dir = self.source / "characters" / self.character_id
        character_dir.mkdir(parents=True)
        (self.source / "characters" / "registry.json").write_text(json.dumps({
            "version": 1,
            "active_character_id": self.character_id,
            "characters": [{
                "character_id": self.character_id,
                "display_name": "Historical episode test",
                "config_directory": f"characters/{self.character_id}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "legacy_default": False,
            }],
        }), encoding="utf-8")
        for name, value in (
            ("character.json", {"name": "Historical episode test", "voice": {"provider": None}}),
            ("memories.json", [{"id": "v1-only", "text": "must never be read"}]),
            ("conversation_summary.json", {}),
        ):
            (character_dir / name).write_text(json.dumps(value), encoding="utf-8")
        (character_dir / "personality.md").write_text("Synthetic.\n", encoding="utf-8")

        database = default_v2_path(self.source)
        database.parent.mkdir()
        store = MemoryV2Store(str(database))
        try:
            store.create_character(self.character_id, "Historical episode test", created_at_us=1)
            self.real_scope_id = store.default_truth_scope_id(self.character_id)
            self.scenario_scope_id = f"scope-{uuid.uuid4()}"
            store.connection.execute(
                "INSERT INTO truth_scopes VALUES (?, ?, 'scenario', 'Synthetic RP', 'inactive', 2, 2)",
                (self.character_id, self.scenario_scope_id),
            )
            store.add_summary(
                self.character_id, "old-invalid-episode", "Old invalid derived summary.",
                source_count=2, provenance_state="derived_rebuildable",
                generator_name="aifren_episode_compactor", generator_version="3",
                legacy_metadata={
                    "generation_id": "old-invalid-generation",
                    "source_start_index": 0,
                    "source_end_index_exclusive": 2,
                    "source_record_ids": [],
                },
                created_at_us=3, summary_level="episode_compaction",
            )
            store.add_summary_source_range(self.character_id, "old-invalid-episode", 1, 2)
        finally:
            store.close()

        topics = {
            0: "Do you think I should buy a motorcycle?",
            1: "I spent the afternoon refactoring my Python project.",
            2: "I repaired my old Game Boy by replacing a corroded battery contact.",
            3: "boop",
            4: "*pats your head*",
            18: "I own a glowing plasma sword in this story.",
        }
        records = []
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for exchange in range(60):
            scope = None
            if 12 <= exchange < 18:
                scope = {"kind": "real_world", "scope_id": self.real_scope_id}
            elif 18 <= exchange < 24:
                scope = {"kind": "scenario", "scope_id": self.scenario_scope_id}
            user = topics.get(exchange, f"Synthetic unrelated history item {exchange}.")
            assistant = (
                "The fictional blade hums." if exchange == 18
                else f"Synthetic acknowledgement {exchange}."
            )
            for role, content in (("user", user), ("assistant", assistant)):
                record = {
                    "role": role,
                    "content": content,
                    "timestamp": (origin + timedelta(seconds=len(records))).isoformat(),
                }
                if scope is not None:
                    record["truth_scope"] = scope
                records.append(record)
        (character_dir / "conversation.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8",
        )
        self.records = records

    def _finish_index(self):
        page = None
        while page is None or not page.complete:
            page = self.indexer.run_page(maximum_records=32)
        self.writer.store.rebuild_fts()

    def _rebuilder(self):
        return HistoricalEpisodeRebuilder(
            self.writer, self.conversation_path, confirm_staged_disposable=True,
        )

    def test_rebuild_preserves_old_generation_and_uses_shared_validator(self):
        self._finish_index()
        before = hashlib.sha256(self.conversation_path.read_bytes()).hexdigest()
        report = self._rebuilder().rebuild(self.compactor)
        self.assertEqual(before, hashlib.sha256(self.conversation_path.read_bytes()).hexdigest())
        self.assertEqual(report.prior_episode_count, 1)
        self.assertEqual(report.preserved_prior_episode_count, 1)
        self.assertEqual(report.episode_count, 4)
        self.assertEqual(report.valid_episode_count, 4)
        self.assertEqual(report.unknown_scope_episode_count, 2)
        self.assertEqual(report.real_world_episode_count, 1)
        self.assertEqual(report.scenario_episode_count, 1)
        self.assertFalse(report.runtime_context_admitted)
        cache = EpisodeCompactionCache(self.writer.store, self.character_id)
        validation = cache.validate_for_context(
            self.records, allow_historical_recall=True,
        )
        self.assertTrue(validation.accepted)
        self.assertEqual(validation.source_authority, EPISODE_SOURCE_HISTORICAL)
        self.assertEqual(validation.generation_purpose, EPISODE_PURPOSE_HISTORICAL)
        self.assertEqual(validation.record("old-invalid-episode").state, "superseded")
        first = next(value for value in validation.lower_records if value.accepted)
        self.assertEqual(
            tuple(first.metadata["source_record_ids"]),
            tuple(
                canonical_record_id(index, self.records[index])
                for index in range(
                    first.source_start_index, first.source_end_index_exclusive,
                )
            ),
        )
        self.assertIsNone(cache.select_for_context(self.records))

        other_character = str(uuid.uuid4())
        self.writer.store.create_character(other_character, "Other synthetic", created_at_us=9)
        other = EpisodeCompactionCache(self.writer.store, other_character)
        self.assertFalse(other.validate_for_context(
            self.records, allow_historical_recall=True,
        ).accepted)
        self.assertFalse(other.retrieve_candidates(
            self.records, "remember Python project refactoring",
            active_truth_scope={
                "kind": "real_world",
                "scope_id": self.writer.store.default_truth_scope_id(other_character),
            },
            allow_historical_recall=True,
        ).candidates)

    def test_development_runtime_accepts_only_complete_attested_clone(self):
        self._finish_index()
        report = self._rebuilder().rebuild(self.compactor)
        self.assertGreater(report.valid_episode_count, 0)
        environment = {
            "AIFREN_ENABLE_DEVELOPMENT_QA": "1",
            "AIFREN_DEVELOPMENT_STAGED_DATA_ROOT": str(self.clone),
            "AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID": self.character_id,
        }
        runtime = attest_development_staged_runtime(
            self.clone, character_id=self.character_id, environment=environment,
        )
        self.assertIsNotNone(runtime)
        self.assertEqual(self.clone_report.target_database, runtime.database_path)
        self.assertEqual(len(self.records), runtime.historical_evidence_count)
        self.assertGreater(runtime.validated_episode_count, 0)

        self.writer.close()
        self.writer = None
        with mock.patch.dict("os.environ", environment, clear=False):
            resource_before = os.environ.get("AIFREN_RESOURCE_ROOT")
            with development_staged_environment(
                self.clone, self.character_id, resource_root=self.source,
            ):
                self.assertEqual(
                    str(self.source.resolve()), os.environ["AIFREN_RESOURCE_ROOT"],
                )
                first_writer = MemoryV2ShadowWriter(
                    self.clone,
                    character_id=self.character_id,
                    display_name="Historical episode test",
                    memory_file=self.clone / "characters" / self.character_id / "memories.json",
                )
                try:
                    self.assertEqual(
                        self.clone_report.target_database, first_writer.database_path,
                    )
                finally:
                    first_writer.close()

                # Production-shaped disposable turns advance canonical history
                # after the strict startup attestation. A newly constructed
                # service must still use the marker-bound staged database.
                conversation = json.loads(self.conversation_path.read_text(encoding="utf-8"))
                conversation.extend((
                    {"role": "user", "content": "Disposable QA turn."},
                    {"role": "assistant", "content": "Disposable QA response."},
                ))
                self.conversation_path.write_text(json.dumps(conversation), encoding="utf-8")
                second_writer = MemoryV2ShadowWriter(
                    self.clone,
                    character_id=self.character_id,
                    display_name="Historical episode test",
                    memory_file=self.clone / "characters" / self.character_id / "memories.json",
                )
                try:
                    self.assertEqual(
                        self.clone_report.target_database, second_writer.database_path,
                    )
                finally:
                    second_writer.close()
            self.assertEqual(resource_before, os.environ.get("AIFREN_RESOURCE_ROOT"))

        marker = self.clone_report.marker_path
        original = marker.read_bytes()
        marker.write_text("{}", encoding="utf-8")
        try:
            with self.assertRaises(DevelopmentStagedRuntimeError):
                attest_development_staged_runtime(
                    self.clone,
                    character_id=self.character_id,
                    environment=environment,
                )
        finally:
            marker.write_bytes(original)

    def test_host_restart_accepts_committed_append_but_rejects_changed_frozen_prefix(self):
        import asyncio
        from assistant_service import AssistantService
        from backend_host import AIFrenWebSocketHost
        from conversation.conversation import Conversation
        from memory_v2_authority import DevelopmentV2MemoryAuthority
        from test_assistant_service_v2_authority import _LLM, _Memory, _TTS

        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        self.writer.close()
        self.writer = None
        environment = {
            "AIFREN_ENABLE_DEVELOPMENT_QA": "1",
            "AIFREN_MEMORY_AUTHORITY": "v2",
            "AIFREN_DEVELOPMENT_STAGED_DATA_ROOT": str(self.clone),
            "AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID": self.character_id,
        }
        old_cwd = Path.cwd()
        def service():
            writer = MemoryV2ShadowWriter(self.clone, character_id=self.character_id,
                display_name="Synthetic", memory_file=self.conversation_path.parent / "memories.json")
            llm = _LLM("A new synthetic reply.")
            conversation = Conversation(llm, conversation_file=str(self.conversation_path),
                summary_file=str(self.conversation_path.parent / "conversation_summary.json"), memory_authority="v2")
            return AssistantService(llm, _Memory(), conversation, object(), {"name": "Synthetic", "_character_id": self.character_id}, "", _TTS(),
                character_id=self.character_id, memory_v2_shadow_writer=writer, memory_authority="v2",
                memory_v2_authority=DevelopmentV2MemoryAuthority(writer.store, self.character_id, conversation.messages))
        async def session(append):
            host = AIFrenWebSocketHost(resource_root=self.source, data_root=self.clone, port=0,
                service_factory=service)
            await host.start()
            try:
                if append:
                    result = host.service.process_text_turn("A new quiet synthetic afternoon.", speak=False)
                    self.assertTrue(result.succeeded, result.error)
            finally:
                await host.stop()
        try:
            with mock.patch.dict(os.environ, environment):
                asyncio.run(session(True))
                after = self.conversation_path.read_bytes()
                asyncio.run(session(False))
                self.assertEqual(after, self.conversation_path.read_bytes())
                changed = json.loads(after)
                changed[0]["content"] = "Changed original evidence."
                self.conversation_path.write_text(json.dumps(changed))
                with self.assertRaises(DevelopmentStagedRuntimeError):
                    asyncio.run(session(False))
        finally:
            os.chdir(old_cwd)

    def test_idle_runtime_cannot_shrink_a_valid_staged_generation_at_an_ineligible_record(self):
        from assistant_service import AssistantService
        from conversation.conversation import Conversation
        from memory_v2_authority import DevelopmentV2MemoryAuthority
        from memory_v2_episode_compaction import EpisodeCompactionRollover
        from test_assistant_service_v2_authority import _LLM, _Memory, _TTS

        for index in range(120, 240):
            self.records.append({"role": "user" if index % 2 == 0 else "assistant",
                "content": f"Additional synthetic source record {index}.",
                "timestamp": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat(),
                "truth_scope": {"kind": "real_world", "scope_id": self.real_scope_id}})
        self.records[120]["semantic_admission"] = {
            "channel": "hearing", "state": "unavailable", "understood": False,
        }
        self.conversation_path.write_text(json.dumps(self.records), encoding="utf-8")
        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        cache = EpisodeCompactionCache(self.writer.store, self.character_id)
        before = cache.validate_for_context(self.records, allow_historical_recall=True)
        self.assertTrue(before.accepted)
        self.assertGreater(before.raw_start_index, 120)
        originals = self.conversation_path.read_bytes()
        llm = _LLM("A synthetic reply.")
        conversation = Conversation(llm, conversation_file=str(self.conversation_path),
            summary_file=str(self.conversation_path.parent / "conversation_summary.json"), memory_authority="v2")
        with mock.patch.dict(os.environ, {"AIFREN_ENABLE_DEVELOPMENT_QA": "1"}):
            service = AssistantService(llm, _Memory(), conversation, object(),
                {"name": "Synthetic", "_character_id": self.character_id}, "", _TTS(),
                character_id=self.character_id, memory_v2_shadow_writer=self.writer, memory_authority="v2",
                memory_v2_authority=DevelopmentV2MemoryAuthority(self.writer.store, self.character_id, conversation.messages))
        calls = []
        def compactor():
            calls.append(True)
            return self.compactor
        rollover = EpisodeCompactionRollover(cache, compactor, historical_runtime=True,
            canonical_source_provider=lambda: conversation.messages[:conversation._persisted_message_count])
        conversation.episode_compaction_rollover = rollover
        self.addCleanup(rollover.close)
        service.report_model_runtime_available()
        service.maintain_canonical_observers()
        self.assertTrue(rollover.wait(10))
        after = cache.validate_for_context(conversation.messages, allow_historical_recall=True)
        self.assertTrue(after.accepted)
        self.assertEqual(before.generation_id, after.generation_id)
        self.assertEqual(before.raw_start_index, after.raw_start_index)
        self.assertFalse(calls, "a shorter safe prefix cannot authorize replacement of valid derived coverage")
        self.assertEqual(originals, self.conversation_path.read_bytes())

    def test_rebuild_is_idempotent_without_duplicate_ranges(self):
        self._finish_index()
        first = self._rebuilder().rebuild(self.compactor)
        count = self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM summaries WHERE character_id=?",
            (self.character_id,),
        ).fetchone()[0]
        second = self._rebuilder().rebuild(self.compactor)
        self.assertTrue(second.idempotent)
        self.assertFalse(second.published)
        self.assertEqual(first.generation_id, second.generation_id)
        self.assertEqual(count, self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM summaries WHERE character_id=?",
            (self.character_id,),
        ).fetchone()[0])
        self.assertEqual(second.duplicate_source_range_count, 0)
        self.assertEqual(second.orphan_source_range_count, 0)

    def test_unknown_scope_is_historical_only_and_scenario_isolated(self):
        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        cache = EpisodeCompactionCache(self.writer.store, self.character_id)
        real_scope = {"kind": "real_world", "scope_id": self.real_scope_id}
        scenario_scope = {"kind": "scenario", "scope_id": self.scenario_scope_id}
        project = cache.retrieve_candidates(
            self.records, "remember Python project refactoring", active_truth_scope=real_scope,
            allow_historical_recall=True,
        )
        self.assertTrue(project.candidates)
        self.assertEqual(project.candidates[0].scope_state, "unknown_scope")
        refinement = project.candidates[0].source_refinements[0]
        self.assertEqual("user", refinement.speaker_role)
        self.assertEqual(canonical_record_id(2, self.records[2]), refinement.canonical_record_id)
        self.assertEqual(2, refinement.canonical_index)
        self.assertIn("Python project", refinement.content)
        self.assertFalse(cache.retrieve_candidates(
            self.records, "remember Python project refactoring", active_truth_scope=scenario_scope,
            allow_historical_recall=True,
        ).candidates)
        self.assertFalse(cache.retrieve_candidates(
            self.records, "remember glowing plasma sword story", active_truth_scope=real_scope,
            allow_historical_recall=True,
        ).candidates)
        scenario = cache.retrieve_candidates(
            self.records, "remember glowing plasma sword story", active_truth_scope=scenario_scope,
            allow_historical_recall=True,
        )
        self.assertTrue(scenario.candidates)
        self.assertEqual(scenario.candidates[0].scope_state, "scenario")
        self.assertFalse(cache.retrieve_candidates(
            self.records, "Say something silly", active_truth_scope=real_scope,
            allow_historical_recall=True,
        ).candidates)

    def test_explicit_scope_transition_splits_without_mixing_or_mutating_source(self):
        changed = json.loads(self.conversation_path.read_text(encoding="utf-8"))
        changed[1]["truth_scope"] = {"kind": "real_world", "scope_id": self.real_scope_id}
        self.conversation_path.write_text(json.dumps(changed), encoding="utf-8")
        self._finish_index()
        before = hashlib.sha256(self.conversation_path.read_bytes()).hexdigest()
        report = self._rebuilder().rebuild(self.compactor)
        self.assertTrue(report.published)
        self.assertEqual(before, hashlib.sha256(self.conversation_path.read_bytes()).hexdigest())
        validation = EpisodeCompactionCache(
            self.writer.store, self.character_id,
        ).validate_for_context(changed, allow_historical_recall=True)
        self.assertTrue(validation.accepted, validation.reason)
        self.assertTrue(all(
            len({
                str(changed[index].get("truth_scope", {}).get("kind", "legacy_untagged"))
                for index in range(
                    int(record.source_start_index), int(record.source_end_index_exclusive),
                )
            }) == 1
            for record in validation.lower_records if record.accepted
        ))

    def test_source_or_summary_mutation_invalidates_generation(self):
        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        cache = EpisodeCompactionCache(self.writer.store, self.character_id)
        summary_id = next(
            value.record_id for value in cache.validate_for_context(
                self.records, allow_historical_recall=True,
            ).lower_records if value.accepted
        )
        self.writer.store.connection.execute(
            "UPDATE summaries SET content=content || ' unsupported' WHERE summary_id=?",
            (summary_id,),
        )
        self.assertFalse(cache.validate_for_context(
            self.records, allow_historical_recall=True,
        ).accepted)
        self.writer.store.connection.rollback()
        changed = [dict(value) for value in self.records]
        changed[0] = {**changed[0], "content": "Changed canonical source."}
        self.assertFalse(cache.validate_for_context(
            changed, allow_historical_recall=True,
        ).accepted)

    def test_hybrid_marks_episode_lane_and_false_premise_remains_safe(self):
        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        semantic = SemanticRetrievalV2(
            self.writer.store, include_historical_evidence=True,
        )
        hybrid = HybridMemoryV2Recall(
            self.writer.store, self.character_id,
            semantic_retriever=semantic,
            episode_cache=EpisodeCompactionCache(self.writer.store, self.character_id),
            canonical_messages=self.records,
            include_historical_episodes=True,
        )
        project = hybrid.retrieve(RetrievalQuery(
            self.character_id, "remember Python project refactoring", "2026-02-01T00:00:00+00:00",
        ))
        self.assertTrue(project.candidates)
        self.assertNotIn("historical_episode", {value.lane for value in project.candidates})
        source = next(
            value for value in project.candidates
            if value.lane == "historical_episode_source"
        )
        self.assertEqual("historical_unknown_scope_user_source", source.status)
        self.assertEqual("canonical_conversation_user", source.evidence[0].source_type)
        self.assertEqual(
            canonical_record_id(2, self.records[2]), source.evidence[0].source_id,
        )
        self.assertEqual("canonical_index:2", source.evidence[0].source_reference)
        self.assertIn("Historical user record:", source.content)
        self.assertEqual("user", source.speaker_role)
        self.assertEqual(canonical_record_id(2, self.records[2]), source.canonical_record_id)
        self.assertEqual(2, source.canonical_index)
        self.assertEqual(source.episode_id, source.associated_from)
        self.assertEqual(
            "validated_canonical_source_range", source.evidence[1].source_type,
        )
        ownership = hybrid.retrieve(RetrievalQuery(
            self.character_id,
            "Do you remember which motorcycle I said I owned?",
            "2026-02-01T00:00:00+00:00",
        ))
        self.assertFalse(ownership.candidates)

    def test_source_refinement_is_bounded_and_does_not_turn_a_question_into_ownership(self):
        self._finish_index()
        self._rebuilder().rebuild(self.compactor)
        cache = EpisodeCompactionCache(self.writer.store, self.character_id)
        real_scope = {"kind": "real_world", "scope_id": self.real_scope_id}
        detail = cache.retrieve_candidates(
            self.records, "remember old Game Boy battery repair",
            active_truth_scope=real_scope, allow_historical_recall=True,
        )
        self.assertTrue(detail.candidates)
        self.assertLessEqual(len(detail.candidates[0].source_refinements), 2)
        self.assertEqual("user", detail.candidates[0].source_refinements[0].speaker_role)
        self.assertIn("corroded battery contact", detail.candidates[0].source_refinements[0].content)

        false_premise = cache.retrieve_candidates(
            self.records, "remember which motorcycle I said I owned",
            active_truth_scope=real_scope, allow_historical_recall=True,
        )
        self.assertFalse(false_premise.candidates)

        hybrid = HybridMemoryV2Recall(
            self.writer.store, self.character_id,
            semantic_retriever=SemanticRetrievalV2(
                self.writer.store, include_historical_evidence=True,
            ),
            episode_cache=cache, canonical_messages=self.records,
            include_historical_episodes=True,
        )
        ungrounded_followup = hybrid.retrieve(RetrievalQuery(
            self.character_id,
            "Can you remember another detail connected to something you just remembered?",
            "2026-02-01T00:00:00+00:00",
        ))
        self.assertFalse(ungrounded_followup.candidates)
        self.assertEqual(0, dict(ungrounded_followup.generated_counts)["historical_episode"])

        first = hybrid.retrieve(RetrievalQuery(
            self.character_id, "remember Python project refactoring",
            "2026-02-01T00:00:00+00:00",
        ))
        grounded = next(
            value for value in first.candidates
            if value.canonical_index == 2
        )
        anchor = HistoricalRecallAnchor(
            self.character_id, self.real_scope_id, 1,
            (grounded.canonical_record_id,), (grounded.canonical_index,),
            (grounded.episode_id,) if grounded.episode_id else (),
        )
        associated = hybrid.retrieve(RetrievalQuery(
            self.character_id,
            "Can you remember another detail connected to that?",
            "2026-02-01T00:00:01+00:00",
        ), recall_anchor=anchor)
        self.assertTrue(associated.candidates)
        self.assertEqual(
            "historical_recall_anchor_source", associated.candidates[0].lane,
        )
        self.assertEqual("assistant", associated.candidates[0].speaker_role)
        self.assertEqual(3, associated.candidates[0].canonical_index)

        wrong_scope = HistoricalRecallAnchor(
            self.character_id, self.scenario_scope_id, 1,
            anchor.canonical_record_ids, anchor.canonical_indices, anchor.episode_ids,
        )
        self.assertFalse(hybrid.retrieve(RetrievalQuery(
            self.character_id,
            "Can you remember another detail connected to that?",
            "2026-02-01T00:00:02+00:00",
        ), recall_anchor=wrong_scope).candidates)

    def test_rebuild_has_no_v1_dependency_and_requires_complete_index(self):
        memory_path = CharacterRegistry(self.clone).runtime_paths(self.character_id)["memory"]
        memory_path.write_text("not valid JSON", encoding="utf-8")
        with self.assertRaisesRegex(HistoricalEpisodeError, "index is incomplete"):
            self._rebuilder().rebuild(self.compactor)
        self._finish_index()
        report = self._rebuilder().rebuild(self.compactor)
        self.assertTrue(report.integrity_ok)

    def test_provider_failure_preserves_prior_generation_atomically(self):
        self._finish_index()
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            self._rebuilder().rebuild(EpisodeCompactor(_UnavailableHistoricalCompactor()))
        rows = self.writer.store.connection.execute(
            "SELECT summary_id FROM summaries WHERE character_id=? ORDER BY summary_id",
            (self.character_id,),
        ).fetchall()
        self.assertEqual([str(row[0]) for row in rows], ["old-invalid-episode"])


if __name__ == "__main__":
    unittest.main()
