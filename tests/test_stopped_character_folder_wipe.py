"""Synthetic diagnostic: deleting a profile folder is not a V2 state reset.

All files belong to this fixture. The shared database is deliberately outside
the two UUID folders, matching the current ownership layout. No migration or
recovery replay is requested by this diagnostic.

Observed outcome: original and explicitly V1-imported rows survive; normal V2
does not reread/import/write V1. Missing canonical history leaves existing
cursors unresolved, and an explicit memory query reports unavailability before
generation/publication rather than inventing a healthy reset or absence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from assistant import build_character_prompt
from assistant_service import AssistantService
from conversation.conversation import Conversation
from memory_v2_authority import DevelopmentV2MemoryAuthority
from memory_v2_shadow_writer import MemoryV2ShadowWriter, default_v2_path
from memory_v2_store import MemoryV2Repository, MemoryV2Store
from memory_v2_store.production_import import import_v1_memories, v1_import_scope
from test_assistant_service_v2_authority import _LLM, _Memory, _TTS
from test_memory_v2_embeddings import ToyEmbeddingProvider


class StoppedCharacterFolderWipeTests(unittest.TestCase):
    TABLES = (
        "characters", "truth_scopes", "events", "claims", "claim_evidence",
        "claim_status_events", "active_scene_subjects", "active_scene_relations",
        "active_scene_relation_events", "open_threads", "historical_evidence",
        "canonical_observation_progress", "canonical_observation_dispositions",
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.root)
        self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4())
        self.scenes = {
            self.a: "I slip a blue glove onto your left hand.",
            self.b: "I put red paint on your right middle fingernail.",
        }
        self.legacy = {
            self.a: "The user collects saffron chess pieces.",
            self.b: "The user collects cobalt lanterns.",
        }
        self.colors = {self.a: "green", self.b: "purple"}
        self.services = []
        self.addCleanup(self.close_services)
        for character_id, label in ((self.a, "Mira A"), (self.b, "Noa B")):
            folder = self.folder(character_id)
            folder.mkdir(parents=True)
            (folder / "character.json").write_text(json.dumps({"name": label}))
            (folder / "personality.md").write_text("A thoughtful synthetic human companion.")
            (folder / "memories.json").write_text(json.dumps([{
                "id": 7, "content": self.legacy[character_id], "category": "fact",
                "importance": 5, "created": "2026-01-01T12:00:00Z",
            }]))
            (folder / "conversation_summary.json").write_text(json.dumps({
                "summary": "ROLLING_V1_SUMMARY_CANARY", "summarized_messages": 100,
            }))
        self.database = default_v2_path(self.root)
        self.database.parent.mkdir(parents=True)
        store = MemoryV2Store(str(self.database))
        try:
            for character_id in (self.a, self.b):
                result = import_v1_memories(
                    store, self.folder(character_id), character_id=character_id,
                    display_name="Synthetic import provenance",
                )
                self.assertEqual(1, result.imported)
        finally:
            store.close()

        # This import is a deliberate preparation step above. Neither normal
        # service startup nor any following V2 turn may invoke it implicitly.
        for patcher in (
            patch("memory_v2_shadow_writer.import_v1_memories",
                  side_effect=AssertionError("Normal V2 must not import V1")),
            patch.object(MemoryV2ShadowWriter, "reconcile",
                         side_effect=AssertionError("Normal V2 must not reconcile V1")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for character_id in (self.a, self.b):
            service = self.open_service(character_id)
            self.turn(service, self.scenes[character_id])
            self.turn(service, f"My favorite color is {self.colors[character_id]}.")
            self.assertTrue(service.continuity_snapshot()["scene_relations"])
            service.close()

    def folder(self, character_id):
        return self.root / "characters" / character_id

    def close_services(self):
        for service in self.services:
            service.close()

    def open_service(self, character_id):
        folder = self.folder(character_id)
        character = json.loads((folder / "character.json").read_text())
        character["_character_id"] = character_id
        llm = _LLM("Understood.")
        llm.local_presentation = True
        llm.companion_memory_realization = True
        memory = _Memory()
        memory.memory_file = str(folder / "memories.json")
        memory.memories = [{"id": 999, "content": "UNREAD_LIVE_V1_OBJECT_CANARY"}]
        conversation = Conversation(
            llm, conversation_file=folder / "conversation.json",
            summary_file=folder / "conversation_summary.json", memory_authority="v2",
        )
        writer = MemoryV2ShadowWriter(
            self.root, character_id=character_id, display_name=character["name"],
            memory_file=folder / "memories.json",
        )
        writer._embedding_provider = ToyEmbeddingProvider()
        MemoryV2Repository(writer.store).ensure_character(character_id, character["name"])
        authority = DevelopmentV2MemoryAuthority(
            writer.store, character_id, conversation.messages,
            embedding_provider=ToyEmbeddingProvider(),
        )
        service = AssistantService(
            llm, memory, conversation, object(), character,
            build_character_prompt(character, (folder / "personality.md").read_text()),
            _TTS(), character_id=character_id, memory_authority="v2",
            memory_v2_shadow_writer=writer, memory_v2_authority=authority,
        )
        self.services.append(service)
        self.assertEqual("v2", service.memory_authority_status()["mode"])
        return service

    def turn(self, service, text):
        result = service.process_text_turn(text, speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(0, service.memory.retrieval_calls)
        self.assertEqual([], service.memory.processed)
        self.assertEqual(0, service.memory.save_calls)
        request = repr(service.llm.calls)
        self.assertNotIn("UNREAD_LIVE_V1_OBJECT_CANARY", request)
        self.assertNotIn("ROLLING_V1_SUMMARY_CANARY", request)
        return result

    def rows(self, character_id):
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
        try:
            result = {table: tuple(connection.execute(
                f"SELECT * FROM {table} WHERE character_id=? ORDER BY rowid", (character_id,),
            )) for table in self.TABLES}
            result["v1_import_records"] = tuple(connection.execute(
                "SELECT * FROM v1_import_records WHERE source_scope=? ORDER BY legacy_memory_id",
                (v1_import_scope(character_id),),
            ))
            return result
        finally:
            connection.close()

    def wipe_stopped_a_folder(self):
        self.close_services()
        folder = self.folder(self.a)
        self.assertEqual(folder.parent, self.root / "characters")
        self.assertEqual({"character.json", "personality.md", "memories.json",
                          "conversation.json", "conversation_summary.json"},
                         {path.name for path in folder.iterdir()})
        preserved = {name: (folder / name).read_bytes()
                     for name in ("character.json", "personality.md")}
        for name in ("memories.json", "conversation.json", "conversation_summary.json"):
            path = folder / name
            self.assertFalse(path.is_symlink())
            path.unlink()
        self.assertEqual(set(preserved), {path.name for path in folder.iterdir()})
        return preserved

    def test_stopped_folder_wipe_preserves_external_v2_and_other_character(self):
        before_a, before_b = self.rows(self.a), self.rows(self.b)
        before_b_files = {path.name: path.read_bytes() for path in self.folder(self.b).iterdir()}
        preserved = self.wipe_stopped_a_folder()
        self.assertTrue(self.database.is_file())
        self.assertEqual(before_a, self.rows(self.a))
        self.assertEqual(before_b, self.rows(self.b))
        self.assertEqual(before_b_files, {path.name: path.read_bytes()
                                        for path in self.folder(self.b).iterdir()})
        self.assertEqual(preserved, {name: (self.folder(self.a) / name).read_bytes()
                                    for name in preserved})

        service = self.open_service(self.a)
        self.assertEqual([], service.conversation.messages)
        snapshot = service.continuity_snapshot()
        self.assertIn("blue glove", json.dumps(snapshot["scene_relations"]))
        self.assertNotIn("red paint", json.dumps(snapshot))
        self.assertEqual(before_a, self.rows(self.a))
        self.assertEqual(before_b, self.rows(self.b))

    def test_provenance_distinguishes_explicit_legacy_import_from_v2_observation(self):
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
        try:
            for character_id in (self.a, self.b):
                imported = connection.execute(
                    """SELECT c.content,c.provenance_state,c.curator_name,e.source_origin,
                              e.source_reference,e.actor_kind
                         FROM claims c JOIN claim_evidence ce USING(character_id,claim_id)
                         JOIN events e USING(character_id,event_id)
                        WHERE c.character_id=? AND c.content=?""",
                    (character_id, self.legacy[character_id]),
                ).fetchone()
                self.assertEqual((self.legacy[character_id], "legacy_unverified", "memory_v1_import",
                                  "legacy_v1_import", "memories.json#7", "system"), imported)
                scene = connection.execute(
                    """SELECT e.content_text,e.source_origin,e.source_reference,e.actor_kind
                         FROM active_scene_relation_events r JOIN events e USING(character_id,event_id)
                        WHERE r.character_id=? AND r.operation='set'""", (character_id,),
                ).fetchone()
                self.assertIsNotNone(scene)
                self.assertEqual(self.scenes[character_id], scene[0])
                self.assertEqual("canonical_conversation", scene[1])
                self.assertTrue(scene[2].startswith(f"characters/{character_id}/conversation.json#"))
                self.assertEqual("user", scene[3])
        finally:
            connection.close()

        # A newly edited V1 file does not become a normal-V2 import or prompt.
        path = self.folder(self.a) / "memories.json"
        data = json.loads(path.read_text())
        data.append({"id": 8, "content": "UNIMPORTED_V1_FILE_CANARY"})
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        service = self.open_service(self.a)
        self.turn(service, "Hello.")
        self.assertEqual(before, path.read_bytes())
        self.assertNotIn("UNIMPORTED_V1_FILE_CANARY", repr(service.llm.calls))
        self.assertNotIn("UNIMPORTED_V1_FILE_CANARY", repr(self.rows(self.a)))
        self.assertEqual(1, len(self.rows(self.a)["v1_import_records"]))

    def test_wiped_canonical_source_is_unresolved_not_recreated_as_healthy_memory(self):
        before = self.rows(self.a)
        self.wipe_stopped_a_folder()
        service = self.open_service(self.a)
        recovery = service._canonical_observation_recovery
        outcomes = recovery.last_status["consumers"]
        self.assertTrue(outcomes)
        self.assertTrue(all(row["state"] == "unresolved" and row["reason"] == "canonical_prefix_changed"
                            and row["processed"] == 0 for row in outcomes.values()))
        self.assertEqual("recovery_unresolved", recovery.inspection_status())
        self.assertEqual(before["canonical_observation_progress"],
                         self.rows(self.a)["canonical_observation_progress"])
        self.assertFalse((self.folder(self.a) / "conversation.json").exists())

        captured = []
        prepare = service._memory_v2_authority.prepare
        def capture(*args, **kwargs):
            value = prepare(*args, **kwargs)
            captured.append(value)
            return value
        events = []
        service.subscribe(events.append)
        with patch.object(service._memory_v2_authority, "prepare", side_effect=capture):
            result = service.process_text_turn("What is my favorite color?", speak=False)
        self.assertFalse(result.succeeded)
        self.assertTrue(any(event.type == "error" and event.data.get("code") == "memory_lookup_unavailable"
                            for event in events))
        self.assertTrue(captured[-1].requirement.lookup_unavailable)
        self.assertFalse(captured[-1].authoritative_no_evidence)
        self.assertNotIn("green", result.reply.casefold())
        self.assertNotIn("purple", result.reply.casefold())
        self.assertEqual([], service.conversation.messages)
        self.assertFalse((self.folder(self.a) / "conversation.json").exists())
        self.assertNotIn(self.scenes[self.a], repr(service.conversation.messages))
        self.assertEqual([], service.llm.calls)
        self.assertEqual(0, service.memory.retrieval_calls)
        self.assertEqual([], service.memory.processed)
        self.assertEqual(0, service.memory.save_calls)
        self.assertEqual(before["active_scene_relations"], self.rows(self.a)["active_scene_relations"])
        self.assertEqual("recovery_unresolved", recovery.inspection_status())
        print("Synthetic folder-wipe outcome:", json.dumps({
            "recovery": recovery.inspection_status(),
            "reasons": sorted({row["reason"] for row in outcomes.values()}),
            "published_assistant": False,
            "canonical_rows_after_query": len(service.conversation.messages),
            "provider_calls": len(service.llm.calls),
        }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
