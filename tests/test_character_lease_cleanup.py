"""Failed synthetic initialization/switch/shutdown never strands writer leases."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import assistant
from assistant_service import AssistantService
from character_registry import CharacterRegistry, CharacterStorageError
from character_storage_runtime import acquire_runtime_lease, maintenance_lease
from memory_v2_shadow_writer import MemoryV2ShadowWriter
import test_character_switch_ownership as switching


class CharacterLeaseCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aifren-synthetic-lease-cleanup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = CharacterRegistry(self.root)
        self.character = self.registry.create("Synthetic Cleanup", personality="Synthetic only.")
        self.registry.select(self.character.character_id)
        self.leases = []

        def capture(*args, **kwargs):
            lease = acquire_runtime_lease(*args, **kwargs)
            self.leases.append(lease)
            return lease

        capture_patch = patch("character_storage_runtime.acquire_runtime_lease", side_effect=capture)
        capture_patch.start()
        self.addCleanup(capture_patch.stop)
        self.addCleanup(lambda: [lease.close() for lease in self.leases])

    def assert_available(self, registry=None, character_id=None):
        with maintenance_lease(registry or self.registry, character_id or self.character.character_id):
            pass

    @contextmanager
    def service_fixture(self):
        case = switching.CharacterSwitchOwnershipTests()
        from memory_v2_authority import DevelopmentV2MemoryAuthority
        from test_memory_v2_embeddings import ToyEmbeddingProvider

        def authority(*args, **kwargs):
            kwargs.setdefault("embedding_provider", ToyEmbeddingProvider())
            return DevelopmentV2MemoryAuthority(*args, **kwargs)

        with patch("memory_v2_authority.DevelopmentV2MemoryAuthority", side_effect=authority):
            case.setUp()
            try:
                yield case
            finally:
                case.tearDown()

    def test_writer_wrong_override_or_memory_path_releases_acquired_lease(self):
        paths = self.registry.runtime_paths(self.character.character_id)
        for changes in ({"database_path": self.root / "wrong.sqlite3"},
                        {"memory_file": self.root / "wrong-memory.json"}):
            with self.subTest(changes=tuple(changes)):
                arguments = dict(application_dir=self.root, character_id=self.character.character_id,
                                 display_name=self.character.display_name, memory_file=paths["memory"])
                arguments.update(changes)
                with self.assertRaises(CharacterStorageError):
                    MemoryV2ShadowWriter(**arguments)
                self.assert_available()

    def test_writer_post_store_setup_failure_closes_store_and_lease(self):
        paths = self.registry.runtime_paths(self.character.character_id)
        with patch("memory_v2_shadow_writer.WorkingRecallCache", side_effect=RuntimeError("Synthetic cache failure")):
            with self.assertRaises(RuntimeError):
                MemoryV2ShadowWriter(self.root, character_id=self.character.character_id,
                                    display_name=self.character.display_name, memory_file=paths["memory"])
        self.assert_available()

    def test_assistant_initialization_failure_releases_lease_at_each_later_owner(self):
        for failure in ("Memory", "embeddings", "metadata", "Conversation", "VoiceInput", "TextToSpeech", "load_character", "build_character_prompt"):
            with self.subTest(failure=failure), ExitStack() as patches:
                previous = Path.cwd()
                os.chdir(self.root)
                patches.callback(os.chdir, previous)
                memory = SimpleNamespace(generate_missing_embeddings=Mock(), generate_missing_metadata=Mock())
                conversation = SimpleNamespace(messages=[])
                replacements = {
                    "create_llm": Mock(return_value=object()), "Memory": Mock(return_value=memory),
                    "Conversation": Mock(return_value=conversation), "VoiceInput": Mock(return_value=object()),
                    "TextToSpeech": Mock(return_value=object()),
                    "load_character": Mock(return_value=({"name": "Synthetic"}, "Synthetic only.")),
                    "build_character_prompt": Mock(return_value="Synthetic only."),
                }
                if failure in {"embeddings", "metadata"}:
                    getattr(memory, "generate_missing_" + failure).side_effect = RuntimeError("Synthetic setup failure")
                else:
                    replacements[failure].side_effect = RuntimeError("Synthetic setup failure")
                for name, value in replacements.items():
                    patches.enter_context(patch.object(assistant, name, value))
                with self.assertRaises(RuntimeError):
                    assistant.initialize(prepare_v1_memory=True)
                self.assert_available()

    def test_switch_old_save_failure_releases_candidate_but_preserves_old_owner(self):
        with self.service_fixture() as case:
            old_conversation, old_lease = case.service.conversation, case.service._storage_lease
            with patch.object(case.service, "save", side_effect=RuntimeError("Synthetic save failure")):
                with self.assertRaises(RuntimeError):
                    case.service.switch_character_state(
                        character_id=case.b.character_id, display_name=case.b.display_name,
                        runtime_paths=case.registry.runtime_paths(case.b.character_id), application_dir=case.root)
            self.assertIs(old_conversation, case.service.conversation)
            old_lease.assert_current()
            self.assert_available(case.registry, case.b.character_id)

    def test_default_factory_failure_releases_initialized_and_writer_leases(self):
        for failure in ("writer", "authority", "service"):
            with self.subTest(failure=failure), ExitStack() as patches:
                previous = Path.cwd()
                os.chdir(self.root)
                patches.callback(os.chdir, previous)
                lease = acquire_runtime_lease(self.registry, self.character.character_id)
                patches.callback(lease.close)
                paths = lease.paths
                conversation = SimpleNamespace(messages=[], _storage_lease=lease,
                                               close_episode_compaction_rollover=Mock())
                memory = SimpleNamespace(memory_file=str(paths["memory"]))
                initialized = (object(), memory, conversation, object(),
                               {"_character_id": self.character.character_id, "name": "Synthetic"},
                               "Synthetic only.", object(), None)
                patches.enter_context(patch.object(assistant, "initialize", return_value=initialized))
                patches.enter_context(patch("config.configured_memory_authority", return_value="v2"))
                if failure == "writer":
                    patches.enter_context(patch("memory_v2_shadow_writer.MemoryV2ShadowWriter",
                                                side_effect=RuntimeError("Synthetic writer setup failure")))
                else:
                    authority = Mock(side_effect=RuntimeError("Synthetic authority failure")) if failure == "authority" else Mock(return_value=SimpleNamespace(close=Mock()))
                    patches.enter_context(patch("memory_v2_authority.DevelopmentV2MemoryAuthority", authority))
                service_type = AssistantService
                if failure == "service":
                    class FailedService(AssistantService):
                        def __init__(self, **_kwargs):
                            raise RuntimeError("Synthetic service setup failure")
                    service_type = FailedService
                with self.assertRaises(RuntimeError):
                    service_type.create_default()
                self.assertTrue(lease.closed)
                self.assert_available()

    def test_failed_candidate_cleanup_still_releases_both_candidate_leases(self):
        with self.service_fixture() as case:
            with patch("memory.memory.Memory.subscribe_mutations", return_value=Mock(side_effect=RuntimeError("Synthetic unsubscribe failure"))):
                with self.assertRaises(RuntimeError):
                    case.service.switch_character_state(
                        character_id=case.b.character_id, display_name=case.b.display_name,
                        runtime_paths=case.registry.runtime_paths(case.b.character_id), application_dir=case.root,
                        publish_selection=Mock(side_effect=RuntimeError("Synthetic registry failure")))
            self.assertEqual(case.a.character_id, case.service.character_id)
            self.assert_available(case.registry, case.b.character_id)

    def test_shutdown_early_or_late_failure_does_not_strand_either_lease(self):
        for failure in ("episode", "ptt", "speech", "tts", "save", "unsubscribe", "store"):
            with self.subTest(failure=failure), self.service_fixture() as case:
                case._switch(case.b)
                service = case.service
                canonical_lease = service._storage_lease
                writer = service._memory_v2_shadow_writer
                writer_lease = writer.runtime_lease
                self.assertIsNot(canonical_lease, writer_lease)
                error = RuntimeError("Synthetic cleanup failure")
                with ExitStack() as patches:
                    if failure == "episode":
                        patches.enter_context(patch.object(service.conversation, "close_episode_compaction_rollover", side_effect=error))
                    elif failure == "ptt":
                        service._ptt = SimpleNamespace(stop=Mock(side_effect=error))
                    elif failure == "speech":
                        patches.enter_context(patch.object(service, "stop_speaking", side_effect=error))
                    elif failure == "tts":
                        patches.enter_context(patch.object(service.tts, "close", side_effect=error, create=True))
                    elif failure == "save":
                        patches.enter_context(patch.object(service, "save", side_effect=error))
                    elif failure == "unsubscribe":
                        service._memory_v2_unsubscribe = Mock(side_effect=error)
                    else:
                        patches.enter_context(patch.object(writer.store, "close", side_effect=error))
                    with self.assertRaises(RuntimeError):
                        service.close()
                self.assertTrue(canonical_lease.closed)
                self.assertTrue(writer_lease.closed)
                self.assert_available(case.registry, case.b.character_id)
                service.close()  # Exactly-once retirement remains idempotent.

    def test_retired_cleanup_error_does_not_strand_fully_built_replacement(self):
        for failure in ("unsubscribe", "lease"):
            with self.subTest(failure=failure), self.service_fixture() as case:
                old_lease = case.service._storage_lease
                with ExitStack() as patches:
                    if failure == "unsubscribe":
                        case.service._memory_v2_unsubscribe = Mock(side_effect=RuntimeError("Synthetic retired unsubscribe failure"))
                    else:
                        close = old_lease.close

                        def close_then_fail():
                            close()
                            raise RuntimeError("Synthetic retired lease cleanup failure")

                        patches.enter_context(patch.object(old_lease, "close", side_effect=close_then_fail))
                    case._switch(case.b)
                self.assertTrue(old_lease.closed)
                self.assertEqual(case.b.character_id, case.service.character_id)
                case.service._storage_lease.assert_current()
                case.service.close()
                self.assert_available(case.registry, case.b.character_id)
