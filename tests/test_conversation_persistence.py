"""Production save/turn boundaries with only temporary records and fake providers."""

import builtins
from contextlib import ExitStack, contextmanager
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from assistant_service import AssistantService
from conversation.conversation import Conversation
from conversation.persistence import ConversationPersistenceError
from memory_v2_authority import DevelopmentV2MemoryAuthority


class Provider:
    def __init__(self):
        self.calls = []
        self.callback = None

    def generate(self, context, prompt):
        self.calls.append(context)
        if self.callback:
            self.callback()
        return "Synthetic reply."


class Memory:
    def __init__(self):
        self.memories = []
        self.process = Mock()
        self.save = Mock()
        self.generate_missing_embeddings = Mock()
        self.generate_missing_metadata = Mock()

    def get_relevant_memories(self, *_args, **_kwargs):
        return []


class ConversationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aifren-persistence-test-")
        self.root = Path(self.temporary.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(os.chdir, self.previous_cwd)
        self.path = self.root / "conversation.json"
        self.summary = self.root / "summary.json"
        self.provider = Provider()
        self.memory = Memory()

    def conversation(self, mode="v1"):
        return Conversation(self.provider, conversation_file=self.path,
                            summary_file=self.summary, memory_authority=mode)

    def seed(self):
        records = [{
            "role": "user", "content": "Synthetic retained record.",
            "timestamp": "2030-01-02T03:04:05.123456+00:00",
            "truth_scope": {"kind": "real_world", "scope_id": "scope-synthetic"},
            "origin": {"kind": "scene_ui", "generated_event": True},
            "semantic_admission": {"channel": "hearing", "understood": False},
        }]
        self.path.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
        return records

    def service(self, mode="v1", conversation=None, tts=None):
        c = conversation or self.conversation(mode)
        character_id = "11111111-1111-4111-8111-111111111111"
        scope = {"kind": "real_world", "scope_id": "scope-22222222-2222-4222-8222-222222222222"}
        authority = None
        if mode == "v2":
            authority = DevelopmentV2MemoryAuthority(
                SimpleNamespace(active_truth_scope_id=lambda _: scope["scope_id"]),
                character_id, c.messages,
                recall=SimpleNamespace(retrieve=lambda *_a, **_k: SimpleNamespace(
                    candidates=(), abstention_reason="no_candidates",
                )),
            )
        service = AssistantService(
            self.provider, self.memory, c, object(), {"_character_id": character_id},
            "Synthetic character.", tts or SimpleNamespace(stop=lambda: None),
            character_id=character_id, memory_authority=mode, memory_v2_authority=authority,
        )
        if mode == "v2":
            service.truth_scope_provenance = lambda: scope
        return service

    @staticmethod
    def partial_write(_data, handle, **_kwargs):
        handle.write('[{"role":')
        raise OSError("synthetic private sentinel that must not enter the public error")

    def assert_no_success(self, service, events, result):
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type in {"assistant_response", "conversation_message", "memory_updated"}
                             for e in events))
        self.assertFalse(any(e.type == "status" and e.data.get("state") == "ready" for e in events))
        self.memory.process.assert_not_called()
        self.assertFalse(service._turn_lock.locked())
        errors = [e for e in events if e.type == "error"]
        self.assertEqual("conversation_persistence", errors[-1].data["source"])
        self.assertNotIn("private sentinel", result.error)

    @contextmanager
    def summary_mutations(self):
        """Observe both direct opens and the actual atomic-file write path."""
        attempts = []

        def summary_path(path):
            if not isinstance(path, (str, bytes, os.PathLike)):
                return False
            name = Path(os.fsdecode(path)).name
            return name == self.summary.name or name.startswith("." + self.summary.name + ".")

        original_open, original_io_open = builtins.open, io.open
        original_os_open, original_replace = os.open, os.replace

        def open_file(path, mode="r", *args, **kwargs):
            if summary_path(path) and any(flag in str(mode) for flag in "wax+"):
                attempts.append("open")
            return original_open(path, mode, *args, **kwargs)

        def open_fd(path, flags, *args, **kwargs):
            if summary_path(path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                attempts.append("os.open")
            return original_os_open(path, flags, *args, **kwargs)

        def open_path(path, mode="r", *args, **kwargs):
            if summary_path(path) and any(flag in str(mode) for flag in "wax+"):
                attempts.append("io.open")
            return original_io_open(path, mode, *args, **kwargs)

        def replace(source, destination, *args, **kwargs):
            if summary_path(source) or summary_path(destination):
                attempts.append("replace")
            return original_replace(source, destination, *args, **kwargs)

        with patch("builtins.open", side_effect=open_file), \
                patch("io.open", side_effect=open_path), \
                patch("os.open", side_effect=open_fd), \
                patch("os.replace", side_effect=replace):
            yield attempts

    def test_service_partial_write_preserves_archive_reopen_and_next_turn(self):
        expected = self.seed()
        original = self.path.read_bytes()
        service = self.service()
        shared_messages = service.conversation.messages
        events = []
        service.subscribe(events.append)
        with patch.object(service, "_observe_current_continuity") as continuity, \
                patch.object(service, "_observe_durable_identity_name") as durable, \
                patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            result = service.process_text_turn("Synthetic pending input.", speak=False)
        self.assert_no_success(service, events, result)
        continuity.assert_not_called()
        durable.assert_not_called()
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(expected, self.conversation().messages)
        self.assertIs(shared_messages, service.conversation.messages)
        self.assertEqual(expected, shared_messages)
        self.assertEqual([], list(self.root.glob(".*.tmp")))
        following = service.process_text_turn("Synthetic next input.", speak=False)
        self.assertTrue(following.succeeded, following.error)
        self.assertEqual(3, len(self.conversation().messages))
        self.assertEqual("Synthetic next input.", shared_messages[-2]["content"])

    def test_failed_assistant_save_retains_previously_committed_user_only(self):
        self.seed()
        service = self.service()
        events = []
        service.subscribe(events.append)
        original_dump = json.dump
        calls = 0

        def fail_second(data, handle, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.partial_write(data, handle)
            return original_dump(data, handle, **kwargs)

        with patch("conversation.persistence.json.dump", side_effect=fail_second), \
                patch.object(service, "_observe_durable_identity_name") as durable:
            result = service.process_text_turn("Synthetic committed input.", speak=False)
        self.assert_no_success(service, events, result)
        durable.assert_not_called()
        self.assertEqual(["user", "user"], [m["role"] for m in service.conversation.messages])
        self.assertEqual(service.conversation.messages, self.conversation().messages)
        service.save()
        self.assertEqual(2, len(self.conversation().messages))

    def test_streaming_deltas_are_provisional_and_failed_save_cancels_speech(self):
        from test_assistant_service import FakeTTS
        self.seed()
        original = self.path.read_bytes()
        self.provider.stream_generate = lambda *_a, **_k: iter((
            "This synthetic sentence is long enough to enter the normal early speech queue. ",
            "A second synthetic sentence follows.",
        ))
        tts = FakeTTS()
        service = self.service(tts=tts)
        events, queues = [], []
        service.subscribe(events.append)

        def fail(data, handle, **kwargs):
            queues.append(service._streaming_speech_queue)
            self.partial_write(data, handle, **kwargs)

        with patch("conversation.persistence.json.dump", side_effect=fail):
            result = service.process_text_turn("Synthetic input.", speak=True)
        self.assertTrue(any(e.type == "assistant_delta" for e in events))
        self.assertIsNotNone(queues[0])
        queues[0].join(timeout=1)
        self.assert_no_success(service, events, result)
        self.assertTrue(queues[0]._cancelled.is_set())
        self.assertGreaterEqual(tts.stopped, 1)
        self.assertEqual(original, self.path.read_bytes())

    def test_serialization_failures_restore_last_saved_data(self):
        expected = self.seed()
        original = self.path.read_bytes()
        for invalid in (object(), float("nan")):
            with self.subTest(kind=type(invalid).__name__):
                c = self.conversation()
                c.add_user_message("Synthetic pending input.")
                c.messages[-1]["unsupported_metadata"] = invalid
                with self.assertRaises(ConversationPersistenceError) as raised:
                    c.save()
                self.assertFalse(raised.exception.committed)
                self.assertEqual("serialize", raised.exception.stage)
                self.assertEqual(expected, c.messages)
                self.assertEqual(original, self.path.read_bytes())

    def test_file_flush_and_replace_failures_leave_previous_archive(self):
        expected = self.seed()
        original = self.path.read_bytes()
        for target, stage in (("os.fsync", "flush"), ("os.replace", "replace"),
                              ("tempfile.mkstemp", "write")):
            with self.subTest(stage=stage):
                c = self.conversation()
                c.add_assistant_message("Synthetic pending reply.")
                with patch("conversation.persistence." + target, side_effect=OSError("synthetic")):
                    with self.assertRaises(ConversationPersistenceError) as raised:
                        c.save()
                self.assertFalse(raised.exception.committed)
                self.assertEqual(stage, raised.exception.stage)
                self.assertEqual(expected, c.messages)
                self.assertEqual(original, self.path.read_bytes())
                self.assertEqual([], list(self.root.glob(".*.tmp")))

    def test_first_run_creation_and_metadata_survive_atomic_replacement(self):
        c = self.conversation()
        self.assertEqual([], c.messages)
        self.assertFalse(self.path.exists())
        c.save()
        self.assertEqual([], self.conversation().messages)
        records = self.seed()
        c.reload()
        c.add_assistant_message("Synthetic reply.", truth_scope={"kind": "scenario", "scope_id": "scope-other"})
        expected = list(c.messages)
        from assistant_service import canonical_message_identity
        identity = canonical_message_identity(0, c.messages[0])
        original_replace = os.replace
        with patch("conversation.persistence.os.replace", wraps=original_replace) as replace:
            c.save()
        source, destination = replace.call_args.args
        self.assertEqual(self.path.parent, Path(source).parent)
        self.assertEqual(self.path, destination)
        reopened = self.conversation()
        self.assertEqual(expected, reopened.messages)
        self.assertEqual(records[0], reopened.messages[0])
        self.assertEqual(identity, canonical_message_identity(0, reopened.messages[0]))
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(self.path.stat().st_mode))

    def test_first_run_failed_write_does_not_create_a_canonical_archive(self):
        c = self.conversation()
        c.add_user_message("Synthetic pending input.")
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            with self.assertRaises(ConversationPersistenceError):
                c.save()
        self.assertFalse(self.path.exists())
        self.assertEqual([], c.messages)
        c.save()
        self.assertEqual([], self.conversation().messages)

    def test_post_replace_sync_failure_retains_commit_and_is_not_retried(self):
        self.seed()
        service = self.service()
        events = []
        service.subscribe(events.append)
        with patch("conversation.persistence._sync_directory", side_effect=[None, OSError("synthetic")]) as sync, \
                patch.object(service, "_observe_durable_identity_name") as durable:
            result = service.process_text_turn("Synthetic committed input.", speak=False)
        self.assert_no_success(service, events, result)
        durable.assert_not_called()
        self.assertEqual(2, sync.call_count)
        self.assertEqual(1, len(self.provider.calls))
        self.assertEqual(3, len(service.conversation.messages))
        self.assertEqual(service.conversation.messages, self.conversation().messages)
        failure = next(e for e in events if e.type == "error")
        self.assertTrue(failure.data["replacement_committed"])
        self.assertTrue(failure.data["assistant_persisted"])
        self.assertIn("do not resend", result.error)
        service.save()
        self.assertEqual(3, len(self.conversation().messages))

    def test_existing_corrupt_or_invalid_archive_fails_without_changes(self):
        for raw in (b"", b'[{"role":', b"{}", b"null", b"[null]", b"\xff"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                backup = self.root / "conversation.json.bak"
                backup.write_bytes(b"synthetic prior copy is never overwritten")
                with self.assertRaises(ConversationPersistenceError) as raised:
                    self.conversation()
                self.assertEqual("load", raised.exception.stage)
                self.assertEqual(raw, self.path.read_bytes())
                self.assertEqual(b"synthetic prior copy is never overwritten", backup.read_bytes())

    def test_unreadable_archive_is_not_first_run(self):
        self.seed()
        original = self.path.read_bytes()
        with patch("conversation.persistence.open", side_effect=PermissionError("synthetic")):
            with self.assertRaises(ConversationPersistenceError):
                self.conversation()
        self.assertEqual(original, self.path.read_bytes())

    @unittest.skipIf(os.name == "nt", "file symlinks may require Windows privileges")
    def test_dangling_archive_symlink_is_not_first_run(self):
        self.path.symlink_to(self.root / "missing-target")
        with self.assertRaises(ConversationPersistenceError):
            self.conversation()
        self.assertTrue(self.path.is_symlink())

    def test_failed_reload_preserves_state_and_blocks_until_explicit_recovery(self):
        original = self.seed()
        c = self.conversation()
        shared = c.messages
        self.path.write_bytes(b"[")
        with self.assertRaises(ConversationPersistenceError):
            c.reload()
        self.assertEqual(original, c.messages)
        with self.assertRaises(ConversationPersistenceError):
            c.save()
        service = self.service(conversation=c)
        result = service.process_text_turn("Synthetic blocked input.", speak=False)
        self.assertFalse(result.succeeded)
        self.assertEqual([], self.provider.calls)
        self.assertEqual(b"[", self.path.read_bytes())
        self.path.write_text(json.dumps(original), encoding="utf-8")
        c.reload()
        self.assertIs(shared, c.messages)
        self.assertTrue(service.process_text_turn("Synthetic recovered input.", speak=False).succeeded)

    def test_failed_write_and_unreadable_rollback_block_pending_state_until_reload(self):
        original = self.seed()
        original_bytes = self.path.read_bytes()
        service = self.service()
        shared = service.conversation.messages
        events = []
        service.subscribe(events.append)
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write), \
                patch("conversation.persistence.open", side_effect=PermissionError("synthetic rollback denial")):
            result = service.process_text_turn("Synthetic pending input.", speak=False)
        self.assert_no_success(service, events, result)
        self.assertEqual(original_bytes, self.path.read_bytes())
        # Recovery could not validate the saved prefix. Retain the in-memory
        # pending state for now, but prevent it from escaping via another save.
        self.assertEqual(2, len(shared))
        with self.assertRaises(ConversationPersistenceError):
            service.save()
        calls = len(self.provider.calls)
        self.assertFalse(service.process_text_turn("Synthetic blocked input.", speak=False).succeeded)
        self.assertEqual(calls, len(self.provider.calls))
        service.conversation.reload()
        self.assertIs(shared, service.conversation.messages)
        self.assertEqual(original, shared)
        self.assertTrue(service.process_text_turn("Synthetic safe input.", speak=False).succeeded)

    def test_previously_loaded_archive_disappearance_is_not_first_run(self):
        c = self.conversation()
        c.save()
        self.path.unlink()
        with self.assertRaises(ConversationPersistenceError):
            c.reload()
        with self.assertRaises(ConversationPersistenceError):
            c.save()
        self.assertFalse(self.path.exists())

    def test_failed_clear_restores_messages_without_clearing_summary(self):
        original = self.seed()
        c = self.conversation()
        c.summary_data = {"summary": "Synthetic retained summary.", "summarized_messages": 1}
        c.save_summary()
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            with self.assertRaises(ConversationPersistenceError):
                c.clear_conversation(keep_summary=False)
        self.assertEqual(original, c.messages)
        self.assertEqual("Synthetic retained summary.", c.summary_data["summary"])

    def test_v1_summary_explicit_save_and_generation_still_persist(self):
        service = self.service()
        service.conversation.summary_data = {"summary": "Synthetic V1 summary.", "summarized_messages": 2}
        with self.summary_mutations() as attempts:
            service.save()
        # Positive control: identical instrumentation really detects the
        # summary's temporary write and replacement when V1 owns it.
        self.assertEqual(["os.open", "replace"], attempts)
        self.assertEqual(service.conversation.summary_data, json.loads(self.summary.read_text()))
        self.memory.save.assert_called_once()
        c = service.conversation
        for _ in range(22):
            c.add_user_message("Synthetic old input.")
        c.save()
        with patch.object(c, "_recent_context_start_index", return_value=22):
            c.update_summary()
        self.assertEqual({"summary": "Synthetic reply.", "summarized_messages": 22},
                         self.conversation().summary_data)

    def test_v1_summary_precommit_failure_does_not_roll_back_saved_conversation(self):
        self.summary.write_text('{"summary":"prior","summarized_messages":0}', encoding="utf-8")
        service = self.service()
        service.conversation.add_user_message("Synthetic committed input.")
        service.conversation.summary_data = {"summary": "pending", "summarized_messages": 1}
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.summary:
                raise OSError("synthetic summary replacement failure")
            return original_replace(source, destination)

        with patch("conversation.persistence.os.replace", side_effect=replace):
            with self.assertRaises(ConversationPersistenceError) as raised:
                service.save()
        self.assertEqual("summary", raised.exception.record_kind)
        self.assertEqual(1, len(self.conversation().messages))
        self.assertEqual("prior", service.conversation.summary_data["summary"])

    def test_turn_summary_failure_explicitly_retains_already_published_response(self):
        from test_assistant_service import FakeTTS
        self.provider.stream_generate = lambda *_a, **_k: iter((
            "This synthetic sentence is long enough to enter the normal early speech queue. ",
            "A second synthetic sentence follows.",
        ))
        tts = FakeTTS()
        service = self.service(tts=tts)
        for _ in range(22):
            service.conversation.add_user_message("Synthetic older input.")
        service.conversation.save()
        events, queues = [], []

        def collect(event):
            events.append(event)
            if event.type == "assistant_response":
                # Capture before close: natural queue completion may retire
                # the service reference while summary persistence is running.
                queues.append(service._streaming_speech_queue)

        service.subscribe(collect)
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.summary:
                raise OSError("synthetic summary failure")
            return original_replace(source, destination)

        with patch.object(service.conversation, "_recent_context_start_index", return_value=22), \
                patch("conversation.persistence.os.replace", side_effect=replace):
            result = service.process_text_turn("Synthetic current input.", speak=True)
        self.assertEqual(1, len(queues))
        queue = queues[0]
        self.assertIsNotNone(queue)
        queue.join(timeout=1)
        self.assertFalse(queue._cancelled.is_set())
        self.assertEqual(0, tts.stopped)
        self.assertEqual(self.conversation().messages[-1]["content"], " ".join(tts.spoken))
        self.assertFalse(result.succeeded)
        self.assertIn("assistant response is saved; do not resend", result.error)
        self.assertEqual(24, len(self.conversation().messages))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in events))
        failure = next(e for e in events if e.type == "error")
        self.assertEqual("summary", failure.data["record_kind"])
        self.assertFalse(failure.data["replacement_committed"])
        self.assertTrue(failure.data["assistant_persisted"])

    def test_v2_has_zero_summary_mutation_attempts_across_lifecycle(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.path.unlink(missing_ok=True)
                self.summary.unlink(missing_ok=True)
                raw = b'{ "summarized_messages": 7, "summary": "Synthetic V1 rollback." }\n'
                if existing:
                    self.summary.write_bytes(raw)
                with self.summary_mutations() as attempts:
                    service = self.service("v2")
                    self.assertTrue(service.process_text_turn("Hello.", speak=False).succeeded)
                    self.provider.callback = lambda: (_ for _ in ()).throw(RuntimeError("synthetic provider failure"))
                    self.assertFalse(service.process_text_turn("Synthetic failing input.", speak=False).succeeded)
                    self.provider.callback = service._cancel_active_turn
                    self.assertEqual("interrupted", service.process_text_turn("Synthetic cancelled input.", speak=False).error)
                    self.provider.callback = None
                    with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
                        self.assertFalse(service.process_text_turn("Synthetic unsaved input.", speak=False).succeeded)
                    service.conversation.update_summary()
                    service.conversation.save_summary()
                    service.save()
                    service.close()
                self.assertEqual([], attempts)
                self.assertEqual(existing, self.summary.exists())
                if existing:
                    self.assertEqual(raw, self.summary.read_bytes())
                self.assertEqual(2, len(self.conversation("v2").messages))
                self.memory.save.assert_not_called()

    def test_initialize_passes_authority_before_conversation_load(self):
        import assistant
        paths = {"memory": self.root / "memories.json", "conversation": self.path,
                 "summary": self.summary, "character": self.root / "character.json",
                 "personality": self.root / "personality.md"}
        registry = SimpleNamespace(active=lambda: SimpleNamespace(character_id="synthetic", display_name="Synthetic"),
                                   runtime_paths=lambda _: paths)
        # Corrupt unused compatibility data must not block V2 startup or be repaired there.
        self.summary.write_bytes(b"[")
        with self.summary_mutations() as attempts, ExitStack() as stack:
            for target, value in (("create_llm", self.provider), ("CharacterRegistry", registry),
                                  ("Memory", self.memory), ("VoiceInput", object()),
                                  ("TextToSpeech", object()), ("load_character", ({"name": "Synthetic"}, "Synthetic"))):
                stack.enter_context(patch.object(assistant, target, return_value=value))
            result = assistant.initialize(prepare_v1_memory=False)
        self.assertEqual([], attempts)
        self.assertEqual("v2", result[2]._memory_authority)
        self.assertEqual(b"[", self.summary.read_bytes())
        self.assertFalse(self.path.exists())
        self.memory.generate_missing_embeddings.assert_not_called()
        self.memory.generate_missing_metadata.assert_not_called()

    def test_v2_switch_reload_and_clear_never_mutate_either_summary(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.summary.unlink(missing_ok=True)
                target = self.root / ("existing" if existing else "absent")
                target.mkdir()
                paths = {key: target / name for key, name in (
                    ("memory", "memories.json"), ("conversation", "conversation.json"),
                    ("summary", "summary.json"), ("character", "character.json"),
                    ("personality", "personality.md"),
                )}
                paths["character"].write_text('{"name":"Synthetic"}', encoding="utf-8")
                paths["personality"].write_text("Synthetic personality.", encoding="utf-8")
                paths["memory"].write_text("[]", encoding="utf-8")
                raw = b'{ "summarized_messages": 7, "summary": "Synthetic V1 rollback." }\n'
                if existing:
                    self.summary.write_bytes(raw)
                    paths["summary"].write_bytes(raw)
                with self.summary_mutations() as attempts, \
                        patch("memory.memory.Memory", return_value=Memory()), \
                        patch("config.MEMORY_V2_SHADOW_WRITE_ENABLED", True), \
                        patch("config.MEMORY_V2_REAL_TURN_SHADOW_ENABLED", False):
                    service = self.service("v2")
                    try:
                        service.conversation.reload()
                        service.conversation.clear_conversation(keep_summary=False)
                        service.switch_character_state(
                            character_id="33333333-3333-4333-8333-333333333333",
                            display_name="Synthetic", runtime_paths=paths,
                            application_dir=target,
                        )
                        self.assertEqual("v2", service.conversation._memory_authority)
                        service.conversation.add_user_message("Synthetic saved input.")
                        service.save()
                        self.assertEqual(1, len(json.loads(paths["conversation"].read_bytes())))
                        service.conversation.reload()
                        service.conversation.clear_conversation(keep_summary=False)
                    finally:
                        service.close()
                self.assertEqual([], attempts)
                for summary in (self.summary, paths["summary"]):
                    self.assertEqual(existing, summary.exists())
                    if existing:
                        self.assertEqual(raw, summary.read_bytes())

    def test_proactive_partial_write_never_publishes_or_observes_a_checkin(self):
        from test_continuity_companion_tranche import _Harness, _Llm
        h = _Harness()
        self.addCleanup(h.close)
        h.turn("I'm waiting for my GPU to arrive.")
        original = h.conversation_file.read_bytes()
        now = h.base + timedelta(hours=8)
        llm = _Llm()
        c = Conversation(llm, conversation_file=h.conversation_file,
                         summary_file=h.root / "summary.json", clock=lambda: now)
        h.writer.compare = lambda *_a, **_k: {}
        service = AssistantService(
            llm, self.memory, c, object(), {"_character_id": h.character_id},
            "Synthetic character.", SimpleNamespace(stop=lambda: None),
            memory_v2_shadow_writer=h.writer, character_id=h.character_id,
            memory_authority="v1",
        )
        events = []
        service.subscribe(events.append)
        with patch("model_settings.proactive_behavior_status", return_value={"enabled": True}), \
                patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            result = service.process_proactive_checkin(now_us=int(now.timestamp() * 1_000_000), speak=False)
        self.assert_no_success(service, events, result)
        self.assertFalse(any(e.type == "turn_started" for e in events))
        self.assertEqual(original, h.conversation_file.read_bytes())
        self.assertEqual(json.loads(original), c.messages)
        self.assertEqual(0, h.writer.store.connection.execute("SELECT COUNT(*) FROM proactive_checkins").fetchone()[0])
        self.assertEqual("persistence_error", h.writer.store.connection.execute(
            "SELECT outcome FROM proactive_attempts",
        ).fetchone()[0])

    def test_explicit_save_and_shutdown_surface_failure_and_close_owned_store(self):
        self.seed()
        service = self.service()
        service._memory_v2_shadow_writer = SimpleNamespace(close=Mock())
        original = self.path.read_bytes()
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            with self.assertRaises(ConversationPersistenceError):
                service.save()
            with self.assertRaises(ConversationPersistenceError):
                service.close()
        self.assertEqual(original, self.path.read_bytes())
        service._memory_v2_shadow_writer.close.assert_called_once()
        self.memory.save.assert_not_called()

    def test_failed_old_owner_save_prevents_character_switch(self):
        self.seed()
        service = self.service()
        old_conversation, old_character = service.conversation, service.character_id
        paths = {key: self.root / "new" / key for key in (
            "conversation", "summary", "memory", "character", "personality",
        )}
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            with self.assertRaises(ConversationPersistenceError):
                service.switch_character_state(
                    character_id="33333333-3333-4333-8333-333333333333",
                    display_name="Synthetic next character", runtime_paths=paths,
                    application_dir=self.root,
                )
        self.assertIs(old_conversation, service.conversation)
        self.assertEqual(old_character, service.character_id)
        self.assertFalse((self.root / "new").exists())


if __name__ == "__main__":
    unittest.main()
