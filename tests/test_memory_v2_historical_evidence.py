import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
import uuid

from aifren.memory_v2_store.models import EmbeddingIdentity, RetrievalQuery
from aifren.character.character_registry import CharacterRegistry
from aifren.continuity.memory_v2_historical_evidence import (
    HistoricalEvidenceError,
    HistoricalEvidenceIndexer,
    canonical_record_id,
    open_staged_historical_evidence_writer,
)
from aifren.continuity.memory_v2_hybrid_recall import HybridMemoryV2Recall
from aifren.continuity.memory_v2_shadow_writer import default_v2_path
from aifren.continuity.memory_v2_staged_clone import create_memory_v2_staged_clone
from aifren.memory_v2_store import EmbeddingLifecycle, MemoryV2Store, SemanticRetrievalV2, StoreError


class _ConceptEmbedding:
    provider = "synthetic"
    model = "historical-concepts"
    model_version = "1"
    dimensions = 5
    normalized = True
    dtype = "float32"
    preprocessing_fingerprint = "synthetic-concepts-v1"
    device = "cpu"

    _groups = (
        {"python", "project", "refactor", "refactoring", "coding", "software", "hobby"},
        {"game", "boy", "handheld", "console", "retro", "battery"},
        {"boop", "pat", "head", "interaction"},
        {"motorcycle", "bike", "buy", "bought", "own", "owned"},
        {"plasma", "sword", "scenario"},
    )

    def embed(self, texts):
        vectors = []
        for text in texts:
            tokens = {value.strip(".,?!:*()'").casefold() for value in str(text).split()}
            vector = [float(len(tokens & group)) for group in self._groups]
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector] if norm else vector)
        return vectors

    def identity_for(self, text):
        return EmbeddingIdentity(
            self.provider, self.model, self.dimensions,
            self.preprocessing_fingerprint,
            hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
        )


class HistoricalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.clone = self.root / "clone"
        self.character_id = str(uuid.uuid4())
        self._build_source()
        self.report = create_memory_v2_staged_clone(
            self.source, self.clone, self.character_id,
        )
        registry = CharacterRegistry(self.clone)
        paths = registry.runtime_paths(self.character_id)
        self.conversation_path = paths["conversation"]
        self.writer = open_staged_historical_evidence_writer(
            self.clone, self.character_id, self.report.target_database,
        )
        self.indexer = HistoricalEvidenceIndexer(
            self.writer, self.conversation_path,
            confirm_staged_disposable=True,
        )

    def tearDown(self):
        if getattr(self, "writer", None) is not None:
            self.writer.close()
        self.temporary.cleanup()

    def _build_source(self):
        character_dir = self.source / "characters" / self.character_id
        character_dir.mkdir(parents=True)
        registry = {
            "version": 1,
            "active_character_id": self.character_id,
            "characters": [{
                "character_id": self.character_id,
                "display_name": "Historical test",
                "config_directory": f"characters/{self.character_id}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "legacy_default": False,
            }],
        }
        (self.source / "characters" / "registry.json").write_text(
            json.dumps(registry), encoding="utf-8",
        )
        for filename, value in (
            ("character.json", {"name": "Historical test", "voice": {"provider": None}}),
            ("memories.json", []),
            ("conversation_summary.json", {}),
        ):
            (character_dir / filename).write_text(json.dumps(value), encoding="utf-8")
        (character_dir / "personality.md").write_text("Synthetic.\n", encoding="utf-8")

        database = default_v2_path(self.source)
        database.parent.mkdir()
        store = MemoryV2Store(str(database))
        try:
            store.create_character(self.character_id, "Historical test", created_at_us=1)
            self.real_scope_id = store.default_truth_scope_id(self.character_id)
            self.scenario_scope_id = f"scope-{uuid.uuid4()}"
            store.connection.execute(
                "INSERT INTO truth_scopes VALUES (?, ?, 'scenario', 'Synthetic RP', 'inactive', 2, 2)",
                (self.character_id, self.scenario_scope_id),
            )
            store.add_event(
                self.character_id, "legacy-event", 1, recorded_at_us=3,
                content_text="Imported V1-only legacy row.", source_origin="v1_import",
            )
            store.add_claim(
                self.character_id, "legacy-claim", claim_type="legacy_memory",
                assertion_scope="legacy", content="Imported V1-only legacy row.",
                provenance_state="legacy_unverified", created_at_us=3,
            )
            store.attach_evidence(
                self.character_id, "legacy-claim", "legacy-event", created_at_us=3,
            )
            store.add_event(
                self.character_id, "ordinary-event", 2, recorded_at_us=4,
                content_text="I am tired today.", source_origin="synthetic_observer",
            )
            store.add_claim(
                self.character_id, "ordinary-claim", claim_type="profile_fact",
                assertion_scope="durable", content="I am tired today.",
                provenance_state="complete", created_at_us=4,
            )
            store.attach_evidence(
                self.character_id, "ordinary-claim", "ordinary-event", created_at_us=4,
            )
            store.ensure_fts()
        finally:
            store.close()

        records = [
            self._record("user", "Do you think I should buy a motorcycle?", 0),
            self._record(
                "assistant", "I suggested looking at a silver telescope instead.", 1,
            ),
            self._record(
                "user", "I spent the afternoon refactoring my Python project.", 2,
                scope={"kind": "real_world", "scope_id": self.real_scope_id},
            ),
            self._record(
                "assistant", "That sounds productive.", 3,
                scope={"kind": "real_world", "scope_id": self.real_scope_id},
            ),
            self._record(
                "user", "I own a glowing plasma sword in this story.", 4,
                scope={"kind": "scenario", "scope_id": self.scenario_scope_id},
            ),
            self._record(
                "assistant", "The blade hums.", 5,
                scope={"kind": "scenario", "scope_id": self.scenario_scope_id},
            ),
            self._record("user", "boop", 6),
            self._record("assistant", "Boop!", 7),
            self._record(
                "user", "I repaired my old Game Boy by replacing a corroded battery contact.", 8,
            ),
            self._record("assistant", "That is a satisfying repair.", 9),
            self._record("user", "*pats your head*", 10),
            self._record("assistant", "*leans into the pat*", 11),
            {
                **self._record(
                    "user", "Synthetic scene control cleared a relation.", 12,
                    scope={"kind": "real_world", "scope_id": self.real_scope_id},
                ),
                "origin": {
                    "kind": "scene_ui", "generated_event": True,
                    "operation": "clear_relation",
                },
            },
            self._record(
                "assistant", "I reacted to the generated scene control.", 13,
                scope={"kind": "real_world", "scope_id": self.real_scope_id},
            ),
        ]
        (character_dir / "conversation.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8",
        )
        self.records = records

    @staticmethod
    def _record(role, content, offset, *, scope=None):
        value = {
            "role": role,
            "content": content,
            "timestamp": f"2026-01-01T00:00:{offset:02d}+00:00",
        }
        if scope is not None:
            value["truth_scope"] = scope
        return value

    def _finish_index(self, page_size=3):
        pages = []
        while not pages or not pages[-1].complete:
            pages.append(self.indexer.run_page(maximum_records=page_size))
        self.writer.store.rebuild_fts()
        claim_ids = tuple(
            str(row[0]) for row in self.writer.store.connection.execute(
                """SELECT claim_id FROM historical_evidence
                     WHERE character_id=? AND retrieval_eligible=1
                     ORDER BY canonical_index""",
                (self.character_id,),
            ).fetchall()
        )
        EmbeddingLifecycle(
            self.writer.store, _ConceptEmbedding(),
        ).rebuild_claims(claim_ids)
        return pages

    def _retrieve(self, text, *, scope_id=None, recent_user_turns=()):
        semantic = SemanticRetrievalV2(
            self.writer.store, embedding_provider=_ConceptEmbedding(),
            include_historical_evidence=True,
        )
        query = RetrievalQuery(
            self.character_id, text, "2026-02-01T00:00:00+00:00",
            recent_user_turns=tuple(recent_user_turns),
        )
        return semantic.retrieve(query, truth_scope_id=scope_id)

    def test_index_preserves_exact_source_role_scope_and_occurrence_semantics(self):
        before = hashlib.sha256(self.conversation_path.read_bytes()).hexdigest()
        pages = self._finish_index()
        self.assertGreater(len(pages), 1)
        self.assertEqual(before, hashlib.sha256(self.conversation_path.read_bytes()).hexdigest())
        rows = self.writer.store.connection.execute(
            "SELECT * FROM historical_evidence ORDER BY canonical_index",
        ).fetchall()
        self.assertEqual(len(rows), len(self.records))
        question = rows[0]
        self.assertEqual(question["canonical_record_id"], canonical_record_id(0, self.records[0]))
        self.assertEqual(question["speaker_role"], "user")
        self.assertEqual(question["speech_act"], "question")
        self.assertEqual(question["scope_state"], "unknown_scope")
        self.assertIsNone(question["truth_scope_id"])
        self.assertEqual(
            question["source_content_sha256"],
            hashlib.sha256(self.records[0]["content"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(rows[1]["retrieval_eligible"], 0)
        self.assertEqual("generated_scene_ui", rows[12]["source_class"])
        self.assertEqual(0, rows[12]["retrieval_eligible"])
        self.assertEqual(rows[2]["scope_state"], "real_world")
        self.assertEqual(rows[4]["truth_scope_id"], self.scenario_scope_id)
        claim = self.writer.store.connection.execute(
            "SELECT * FROM claims WHERE claim_id=?", (question["claim_id"],),
        ).fetchone()
        self.assertEqual(claim["claim_type"], "historical_evidence")
        self.assertEqual(claim["assertion_scope"], "historical_occurrence")
        self.assertIn("NOT CURRENT TRUTH", self._retrieve(
            "Do you remember me asking whether I should buy a motorcycle?",
        ).selected_memories[0].label)

    def test_question_with_trailing_prose_remains_a_question_not_a_fact(self):
        records = json.loads(self.conversation_path.read_text(encoding="utf-8"))
        records[0]["content"] = (
            "Do you remember any project I discussed? Give me one detail."
        )
        self.conversation_path.write_text(json.dumps(records), encoding="utf-8")
        self._finish_index()
        row = self.writer.store.connection.execute(
            "SELECT speech_act FROM historical_evidence WHERE canonical_index=0",
        ).fetchone()
        self.assertEqual(row["speech_act"], "question")
        unrelated = self._retrieve(
            "Do you remember any project or hobby I talked about before?",
        )
        self.assertNotIn(
            "Give me one detail",
            " ".join(memory.content for memory in unrelated.selected_memories),
        )

    def test_index_is_idempotent_and_never_escalates_legacy_or_reads_v1(self):
        self._finish_index(page_size=4)
        repeat = self.indexer.run_page(maximum_records=4)
        self.assertTrue(repeat.complete)
        self.assertEqual(repeat.processed_record_count, 0)
        legacy = self.writer.store.connection.execute(
            "SELECT provenance_state, claim_type FROM claims WHERE claim_id='legacy-claim'",
        ).fetchone()
        self.assertEqual(tuple(legacy), ("legacy_unverified", "legacy_memory"))
        self.assertEqual(
            self.writer.store.connection.execute(
                "SELECT COUNT(*) FROM historical_evidence",
            ).fetchone()[0], len(self.records),
        )
        # Neither indexing nor a derived rebuild consumes Memory V1.
        memory_path = CharacterRegistry(self.clone).runtime_paths(self.character_id)["memory"]
        memory_path.write_text("not valid JSON", encoding="utf-8")
        self.writer.store.rebuild_fts()
        result = EmbeddingLifecycle(
            self.writer.store, _ConceptEmbedding(),
        ).rebuild_stale_or_missing()
        self.assertEqual(result["failed"], 0)

    def test_historical_indexes_do_not_change_default_retrieval_corpora(self):
        ordinary_fts_before = self.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims_fts",
        ).fetchone()[0]
        self._finish_index()

        self.assertEqual(
            self.writer.store.connection.execute(
                "SELECT COUNT(*) FROM claims_fts",
            ).fetchone()[0],
            ordinary_fts_before,
        )
        self.assertEqual(
            self.writer.store.connection.execute(
                "SELECT COUNT(*) FROM historical_evidence_fts",
            ).fetchone()[0],
            len(self.records),
        )
        self.assertFalse(self.writer.store.search_fts(
            self.character_id, "python", 8,
        ))
        self.assertTrue(self.writer.store.search_fts(
            self.character_id, "python", 8,
            include_historical_evidence=True,
        ))

        provider = _ConceptEmbedding()
        ordinary_ann_count = self.writer.store.ann_embedding_count(
            self.character_id, provider,
        )
        historical_ann_count = self.writer.store.ann_embedding_count(
            self.character_id, provider,
            include_historical_evidence=True,
        )
        self.assertEqual(ordinary_ann_count, 0)
        self.assertGreater(historical_ann_count, ordinary_ann_count)
        self.assertIsNone(self.writer.store.connection.execute(
            """SELECT state FROM claim_embeddings
                 WHERE character_id=? AND claim_id='ordinary-claim'
                   AND provider=? AND model=?""",
            (self.character_id, provider.provider, provider.model),
        ).fetchone())

    def test_historical_metadata_lookup_is_bounded(self):
        self._finish_index()
        claim_ids = tuple(
            f"synthetic-{index}" for index in range(257)
        )
        with self.assertRaisesRegex(StoreError, "metadata limit is invalid"):
            self.writer.store.historical_evidence_metadata(
                self.character_id, claim_ids, limit=257,
            )

    def test_retrieval_recalls_history_without_promoting_questions_or_crossing_scope(self):
        self._finish_index()
        project = self._retrieve(
            "Do you remember any project or hobby I talked about before?",
        )
        self.assertTrue(project.claim_ids)
        self.assertIn("Python project", project.selected_memories[0].content)

        paraphrase = self._retrieve(
            "Can you remember the old handheld console repair I described?",
        )
        self.assertTrue(paraphrase.claim_ids)
        self.assertIn("Game Boy", paraphrase.selected_memories[0].content)

        interaction = self._retrieve("boop")
        self.assertTrue(interaction.claim_ids)
        self.assertIn("boop", interaction.selected_memories[0].content.casefold())

        ownership = self._retrieve(
            "Do you remember which motorcycle I said I owned?",
        )
        self.assertFalse(ownership.claim_ids)
        asked = self._retrieve(
            "Do you remember me asking whether I should buy a motorcycle?",
        )
        self.assertTrue(asked.claim_ids)

        generic = self._retrieve(
            "What's something you remember about me that I haven't mentioned recently?",
            recent_user_turns=(
                "I spent the afternoon refactoring my Python project.",
            ),
        )
        self.assertFalse(generic.claim_ids)

        real_scenario = self._retrieve(
            "Do you remember the plasma sword I owned?",
        )
        self.assertFalse(real_scenario.claim_ids)
        scenario = self._retrieve(
            "Do you remember the plasma sword I owned in the story?",
            scope_id=self.scenario_scope_id,
        )
        self.assertTrue(scenario.claim_ids)
        self.assertEqual(
            self.writer.store.historical_evidence_metadata(
                self.character_id, scenario.claim_ids,
            )[scenario.claim_ids[0]]["scope_state"],
            "scenario",
        )

    def test_assistant_callback_is_explicitly_owned_and_cannot_become_user_truth(self):
        self._finish_index()
        callback = self._retrieve(
            "What did you tell me before about the silver telescope?",
        )
        self.assertTrue(callback.claim_ids)
        metadata = self.writer.store.historical_evidence_metadata(
            self.character_id, callback.claim_ids,
        )[callback.claim_ids[0]]
        self.assertEqual("assistant", metadata["speaker_role"])
        self.assertEqual(0, metadata["retrieval_eligible"])
        self.assertIn("Historical assistant", callback.selected_memories[0].content)

        false_attribution = self._retrieve(
            "Did I say I owned the silver telescope?",
        )
        self.assertFalse(false_attribution.claim_ids)

        shared = self._retrieve(
            "Do you remember what we talked about with the silver telescope?",
        )
        self.assertTrue(shared.claim_ids)
        shared_metadata = self.writer.store.historical_evidence_metadata(
            self.character_id, shared.claim_ids,
        )
        self.assertIn(
            "assistant", {str(value["speaker_role"]) for value in shared_metadata.values()},
        )

    def test_hybrid_identifies_lane_and_projects_bounded_canonical_reference(self):
        self._finish_index()
        semantic = SemanticRetrievalV2(
            self.writer.store, embedding_provider=_ConceptEmbedding(),
            include_historical_evidence=True,
        )
        result = HybridMemoryV2Recall(
            self.writer.store, self.character_id, semantic_retriever=semantic,
        ).retrieve(RetrievalQuery(
            self.character_id,
            "Do you remember my Python project hobby?",
            "2026-02-01T00:00:00+00:00",
        ))
        self.assertTrue(result.candidates)
        candidate = result.candidates[0]
        self.assertEqual(candidate.lane, "historical_evidence")
        self.assertEqual(candidate.status, "historical_real_world")
        self.assertEqual("user", candidate.speaker_role)
        self.assertEqual("assertion", candidate.speech_act)
        self.assertEqual("ordinary_conversation", candidate.source_class)
        self.assertEqual("real_world", candidate.scope_state)
        self.assertEqual(canonical_record_id(2, self.records[2]), candidate.canonical_record_id)
        self.assertEqual(2, candidate.canonical_index)
        self.assertIn(("historical_evidence", 1.0), candidate.signals)
        self.assertEqual(candidate.evidence[0].source_reference, "conversation.json#2")
        self.assertLessEqual(len(candidate.evidence), 3)

    def test_changed_or_malformed_canonical_history_fails_closed(self):
        self.indexer.run_page(maximum_records=2)
        changed = json.loads(self.conversation_path.read_text(encoding="utf-8"))
        changed[0]["content"] = "Changed canonical source"
        self.conversation_path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(HistoricalEvidenceError, "source prefix changed"):
            self.indexer.run_page(maximum_records=2)

    def test_malformed_archive_creates_no_historical_records(self):
        self.conversation_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(HistoricalEvidenceError):
            self.indexer.run_page(maximum_records=2)
        self.assertEqual(
            self.writer.store.connection.execute(
                "SELECT COUNT(*) FROM historical_evidence",
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
