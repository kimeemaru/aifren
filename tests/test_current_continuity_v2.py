"""Deterministic synthetic regression matrix for Current Continuity V2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from assistant_service import AssistantService
from conversation.conversation import Conversation
from conversation.conversation import ContextManager
from conversation.temporal_context import build_temporal_context_block, derive_temporal_context_facts
from conversation.truth_scope import (
    INVALID_SCOPE,
    LEGACY_UNTAGGED_SCOPE,
    active_scope_from_provenance,
    filter_scope_compatible_history,
    parse_canonical_truth_scope,
)
from current_continuity import (
    admit_current_continuity_context,
    extract_active_state_proposal,
    extract_open_thread_proposal,
    extract_scenario_transition,
    game_event_is_contextual,
)
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository


class _ServiceMemory:
    def __init__(self):
        self.memories = []
        self.processed = []

    def get_relevant_memories(self, _query, max_memories):
        self.maximum = max_memories
        return []

    def process(self, user, reply):
        self.processed.append((user, reply))


class _SilentTts:
    def set_playback_started_callback(self, _callback): pass
    def set_playback_finished_callback(self, _callback): pass
    def stop(self): return 0


class CurrentContinuityV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.character_id = str(uuid.uuid4())
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.rows = []
        self.base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        self.writer = self._new_writer()
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(self.character_id, "Synthetic", legacy_config_key="characters/default")

    def tearDown(self):
        if self.writer is not None:
            self.writer.close()
        self.temp.cleanup()

    def _new_writer(self):
        return MemoryV2ShadowWriter(
            self.root, character_id=self.character_id, display_name="Synthetic",
            memory_file=self.memory_file,
        )

    def _message(self, text, *, minute=None, timestamp=None):
        index = len(self.rows)
        at = self.base + timedelta(minutes=(index if minute is None else minute))
        message = {"role": "user", "content": text, "timestamp": timestamp or at.isoformat()}
        return message

    def _turn(self, text, *, minute=None, timestamp=None):
        message = self._message(text, minute=minute, timestamp=timestamp)
        index = len(self.rows)
        self.rows.extend((message, {
            "role": "assistant", "content": "Synthetic reply.",
            "timestamp": (self.base + timedelta(minutes=(index if minute is None else minute), seconds=1)).isoformat(),
        }))
        self.conversation_file.write_text(json.dumps(self.rows), encoding="utf-8")
        result = self.writer.observe_canonical_user_continuity(
            message, conversation_index=index, conversation_file=self.conversation_file,
        )
        return result, message

    def _activity(self):
        return self.repository.lookup_actor_state(self.character_id, "user", "activity").state

    def _threads(self):
        return self.repository.list_open_threads(self.character_id).threads

    def _admission(self, query, *, minute=1000):
        now = int((self.base + timedelta(minutes=minute)).timestamp() * 1_000_000)
        return admit_current_continuity_context(self.repository, self.character_id, query, now_us=now)

    def _service(self, *, memory=None):
        conversation = Conversation(
            object(), conversation_file=self.conversation_file,
            summary_file=self.root / "summary.json",
            clock=lambda: self.base + timedelta(days=2),
        )
        service_memory = memory or _ServiceMemory()
        service = AssistantService(
            llm=object(), memory=service_memory, conversation=conversation, voice=object(),
            character={"name": "Synthetic", "_character_id": self.character_id},
            character_prompt="prompt", tts=_SilentTts(),
            response_generator=lambda *_args: "Synthetic reply.",
            memory_v2_shadow_writer=self.writer, character_id=self.character_id,
            memory_authority="v1",
        )
        return service, service_memory

    def _restart_writer_for_service(self, service):
        self.writer.close()
        self.writer = self._new_writer()
        self.repository = MemoryV2Repository(self.writer.store)
        service._memory_v2_shadow_writer = self.writer

    def test_canonical_save_is_required_before_any_application(self):
        message = self._message("I'm playing Noita.")
        self.assertEqual("ignored", self.writer.observe_canonical_user_continuity(
            message, conversation_index=0, conversation_file=self.conversation_file,
        )["state"])
        self.assertIsNone(self._activity())

    def test_activity_start_reconfirmation_change_and_temporal_lifecycle(self):
        first, _ = self._turn("I'm playing Noita.", minute=1)
        original = self._activity()
        self.assertEqual("applied", first["state"])
        self.assertEqual("playing Noita", original.value)
        self._turn("I'm playing Noita.", minute=20)
        confirmed = self._activity()
        self.assertEqual(original.state_id, confirmed.state_id)
        self.assertEqual(original.valid_from_us, confirmed.valid_from_us)
        self.assertGreater(confirmed.last_confirmed_at_us, original.last_confirmed_at_us)
        self._turn("I'm watching Alien.", minute=30)
        changed = self._activity()
        self.assertEqual("watching Alien", changed.value)
        self.assertNotEqual(original.state_id, changed.state_id)
        predecessor = self.writer.store.connection.execute(
            "SELECT valid_to_us FROM claims WHERE character_id=? AND claim_id=?",
            (self.character_id, original.state_id),
        ).fetchone()
        self.assertEqual(changed.valid_from_us, predecessor["valid_to_us"])

    def test_supported_activity_grammars(self):
        cases = (
            ("I'm going to bed.", "sleeping"),
            ("I'm going to sleep.", "sleeping"),
            ("I'm heading to work.", "working"),
            ("I'm going out.", "away"),
            ("I'm playing a game.", "playing a game"),
            ("Let's watch Alien.", "watching Alien"),
            ("I'm installing my GPU.", "installing my GPU"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self._turn(text)
                self.assertEqual(expected, self._activity().value)

    def test_return_and_completion_clear_only_matching_temporary_activity(self):
        self._turn("I'm going out.")
        self._turn("I'm back.")
        self.assertIsNone(self._activity())
        self._turn("I'm playing Noita.")
        self._turn("I'm back.")
        self.assertEqual("playing Noita", self._activity().value)
        self._turn("I'm installing my GPU.")
        self._turn("I finished installing it.")
        self.assertIsNone(self._activity())
        self.assertEqual((), self._threads())

    def test_time_never_mutates_activity_or_invents_duration(self):
        self._turn("I'm going to bed.", minute=1)
        state = self._activity()
        self.assertEqual("sleeping", state.value)
        self.assertEqual(state.state_id, self.repository.lookup_actor_state(
            self.character_id, "user", "activity", historical_at_us=state.valid_from_us + 40_000_000_000,
        ).state.state_id)
        block = self._admission("How long has it been?", minute=60 * 24).active_state_context
        self.assertIn("does not prove continuation, completion, or duration", block)
        self.assertNotIn("slept", block.casefold())

    def test_game_stays_real_world_and_ambiguous_death_is_contextual(self):
        self._turn("I'm playing Noita.")
        self._turn("Oh, I died.")
        self.assertEqual("real_world", self.repository.active_truth_scope(self.character_id).kind)
        self.assertEqual("playing Noita", self._activity().value)
        self.assertTrue(game_event_is_contextual("Oh, I died.", self._activity()))
        rendered = self._admission("Oh, I died.").active_state_context
        self.assertIn('"activity":"playing Noita"', rendered)
        self.assertIn("not literal real-world user events", rendered)

    def test_activity_abstention_matrix(self):
        scope = self.repository.active_truth_scope(self.character_id)
        cases = (
            "I went to bed ten hours ago.",
            "What if I'm playing Noita?",
            "I'm not playing Noita.",
            '"I\'m watching Alien," she said.',
            "If I install my GPU, it should help.",
            "Am I playing Noita?",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(extract_active_state_proposal(text, current_activity=None))
                self.assertIsNone(extract_scenario_transition(text, active_scope=scope))

    def test_open_thread_open_reconfirm_resolve_and_provenance(self):
        self._turn("I'm waiting for my GPU to arrive.", minute=1)
        opened = self._threads()[0]
        self.assertEqual("waiting", opened.kind)
        self.assertEqual(1, len(opened.evidence_event_ids))
        self._turn("I'm still waiting for my GPU to arrive.", minute=20)
        reconfirmed = self._threads()[0]
        self.assertEqual(opened.thread_id, reconfirmed.thread_id)
        self.assertEqual(opened.opened_at_us, reconfirmed.opened_at_us)
        self.assertGreater(reconfirmed.last_mentioned_at_us, opened.last_mentioned_at_us)
        self.assertEqual(2, len(reconfirmed.evidence_event_ids))
        closure, _ = self._turn("It arrived.", minute=30)
        self.assertEqual(1, closure["open_thread_updates"])
        self.assertEqual((), self._threads())
        history = self.repository.get_open_thread(self.character_id, opened.thread_id)
        self.assertEqual("resolved", history.status)
        self.assertEqual(3, len(history.evidence_event_ids))

    def test_open_thread_plan_task_shared_and_decision_grammars(self):
        cases = (
            ("I'm installing Ubuntu tonight.", "plan_or_intention", "user", "install Ubuntu"),
            ("I need to finish setting up Plex.", "unresolved_problem", "user", "finish setting up Plex"),
            ("Let's watch Alien later.", "plan_or_intention", "shared", "watch Alien"),
            ("I'm waiting for this download.", "waiting", "user", "waiting for this download"),
            ("I decided I'm going to upgrade the RAM.", "plan_or_intention", "user", "upgrade the RAM"),
        )
        for text, kind, scope, topic in cases:
            with self.subTest(text=text):
                # Isolate exact duplicate matching while preserving one store.
                proposal = extract_open_thread_proposal(text, current_threads=())
                self.assertIsNotNone(proposal)
                operation = proposal.operations[0]
                self.assertEqual((kind, scope, topic), (operation.kind, operation.participant_scope, operation.description))

    def test_open_thread_cancellation_and_ambiguity(self):
        self._turn("I need to finish setting up Plex.")
        thread_id = self._threads()[0].thread_id
        self._turn("I'm not doing that anymore.")
        self.assertEqual("cancelled", self.repository.get_open_thread(self.character_id, thread_id).status)

        self._turn("I'm waiting for my GPU.")
        self._turn("I'm waiting for my package.")
        result, _ = self._turn("It arrived.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual(2, len(self._threads()))

    def test_open_thread_abstention_matrix(self):
        cases = (
            "I was waiting for a GPU last year.",
            "What if I wait for the download?",
            "I'm not waiting for a package.",
            'She said, "I need to finish Plex."',
            "Do I need to install Ubuntu?",
            "The download is interesting.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(extract_open_thread_proposal(text, current_threads=()))

    def test_open_thread_admission_is_relevant_bounded_and_closure_immediate(self):
        self._turn("I'm waiting for my GPU to arrive.")
        self.assertIsNone(self._admission("How was your day?").open_thread_context)
        relevant = self._admission("Is the GPU still what I'm waiting for?")
        self.assertEqual(1, relevant.admitted_thread_count)
        self.assertIn("waiting for my GPU", relevant.open_thread_context)
        self.assertNotIn("thread-", relevant.open_thread_context)
        self.assertNotIn("event", relevant.open_thread_context)
        self._turn("It arrived.")
        self.assertIsNone(self._admission("Any update on the GPU?").open_thread_context)

    def test_scenario_entry_exit_reentry_and_scope_restoration(self):
        self._turn("I'm playing Noita.")
        self._turn("Let's roleplay that we're in Silvervale.")
        scenario = self.repository.active_truth_scope(self.character_id)
        self.assertEqual(("scenario", "Silvervale"), (scenario.kind, scenario.label))
        self._turn("I'm going out.")
        self.assertEqual("away", self._activity().value)
        self._turn("Back to real life.")
        self.assertEqual("playing Noita", self._activity().value)
        self._turn("Let's roleplay that we're in Silvervale.")
        resumed = self.repository.active_truth_scope(self.character_id)
        self.assertEqual(scenario.truth_scope_id, resumed.truth_scope_id)
        self.assertEqual("away", self._activity().value)

    def test_explicit_spoken_role_play_entry_variants(self):
        self._turn("Let's role play that we're in Get Sokyo.")
        scope = self.repository.active_truth_scope(self.character_id)
        self.assertEqual(("scenario", "Get Sokyo"), (scope.kind, scope.label))
        self._turn("Back to real life.")
        self._turn("lets roleplay we're in silvervale")
        scope = self.repository.active_truth_scope(self.character_id)
        self.assertEqual(("scenario", "silvervale"), (scope.kind, scope.label))

    def test_scenario_a_b_isolation_and_real_world_threads(self):
        self._turn("I'm waiting for my GPU.")
        real_thread = self._threads()[0].thread_id
        self._turn("Let's roleplay that we're in Silvervale.")
        self._turn("I'm waiting for the moon rabbit.")
        scenario_a = self.repository.active_truth_scope(self.character_id).truth_scope_id
        scenario_a_thread = self._threads()[0].thread_id
        self._turn("Back to real life.")
        self.assertEqual(real_thread, self._threads()[0].thread_id)
        self._turn("Let's do an RP where we're adventurers.")
        scenario_b = self.repository.active_truth_scope(self.character_id).truth_scope_id
        self.assertNotEqual(scenario_a, scenario_b)
        self.assertEqual((), self._threads())
        self._turn("Back to real life.")
        self._turn("Let's roleplay that we're in Silvervale.")
        self.assertEqual(scenario_a_thread, self._threads()[0].thread_id)

    def test_non_scenario_matrix_never_activates_rp(self):
        cases = (
            "I'm playing Noita.",
            "I'm watching Alien.",
            "I changed my avatar.",
            "Silvervale is a fictional place.",
            'She said, "Let\'s roleplay that we\'re in Silvervale."',
            "What if we lived on Mars?",
        )
        for text in cases:
            with self.subTest(text=text):
                self._turn(text)
                self.assertEqual("real_world", self.repository.active_truth_scope(self.character_id).kind)

    def test_scenario_prompt_is_typed_and_has_no_internal_ids(self):
        self._turn("In this roleplay, we live on a spaceship.")
        admission = self._admission("Where do we live?")
        self.assertIn('{"kind":"scenario","label":"spaceship"}', admission.truth_scope_context)
        self.assertNotIn("scope-", admission.truth_scope_context)
        self.assertNotIn("sha", admission.truth_scope_context.casefold())

    def test_process_restart_reconstructs_scoped_state_and_threads(self):
        self._turn("Let's roleplay that we're in Silvervale.")
        self._turn("I'm waiting for the moon rabbit.")
        scope_id = self.repository.active_truth_scope(self.character_id).truth_scope_id
        thread_id = self._threads()[0].thread_id
        self.writer.close()
        self.writer = self._new_writer()
        self.repository = MemoryV2Repository(self.writer.store)
        self.assertEqual(scope_id, self.repository.active_truth_scope(self.character_id).truth_scope_id)
        self.assertEqual(thread_id, self._threads()[0].thread_id)

    def test_context_order_budget_and_provider_neutral_representation(self):
        manager = ContextManager(max_recent_chars=1000)
        context = manager.build_context(
            "summary", [{"category": "fact", "content": "durable V1"}],
            [{"role": "user", "content": "latest"}],
            admitted_truth_scope_context="[scope]",
            admitted_active_state_context="[active]",
            admitted_open_thread_context="[thread]",
            admitted_durable_context="[durable]",
            admitted_episode_context="[episode]",
            temporal_context="[temporal]",
            max_context_chars=50,
        )
        contents = [item["content"] for item in context]
        self.assertEqual("[temporal]", contents[0])
        self.assertEqual("[scope]", contents[1])
        self.assertEqual("[active]", contents[2])
        self.assertEqual("[thread]", contents[3])
        self.assertIn("AUTHORITATIVE LIFELONG MEMORIES", contents[4])
        self.assertEqual("[durable]", contents[5])
        self.assertIn("LONG-TERM", contents[6])
        self.assertEqual("[episode]", contents[7])
        self.assertEqual("latest", contents[-1])
        self.assertEqual(1, contents.count("[thread]"))

    def test_temporal_fact_and_malformed_legacy_timestamp_safety(self):
        messages = [
            {"role": "user", "content": "I'm going to bed.", "timestamp": "malformed"},
            {"role": "assistant", "content": "Good night.", "timestamp": "malformed"},
            {"role": "user", "content": "I'm back.", "timestamp": self.base.isoformat()},
        ]
        facts = derive_temporal_context_facts(messages, "I'm back.", clock=lambda: self.base + timedelta(hours=10))
        self.assertIsNone(facts.elapsed_since_previous_user_interaction)
        rendered = build_temporal_context_block(facts)
        self.assertNotIn("slept", rendered.casefold())
        result, _ = self._turn("I'm playing Noita.", timestamp="not-a-timestamp")
        self.assertEqual("ignored", result["state"])
        self.assertIsNone(self._activity())

    def test_latest_explicit_evidence_wins_over_stale_current_state(self):
        self._turn("I'm playing Noita.", minute=1)
        self._turn("I'm watching Alien.", minute=2)
        admission = self._admission("What am I doing?", minute=3)
        self.assertIn("watching Alien", admission.active_state_context)
        self.assertNotIn("playing Noita", admission.active_state_context)

    def test_zero_context_abstention_and_no_episode_duplication(self):
        admission = self._admission("Tell me a joke.")
        self.assertIsNone(admission.active_state_context)
        self.assertIsNone(admission.open_thread_context)
        self.assertIn('{"kind":"real_world"}', admission.truth_scope_context)
        self.assertNotIn("open", admission.truth_scope_context.casefold())

    def test_assistant_service_applies_after_save_emits_scope_and_protects_memory_v1(self):
        conversation = Conversation(
            object(), conversation_file=self.conversation_file, summary_file=self.root / "summary.json",
            clock=lambda: self.base + timedelta(days=2),
        )
        memory = _ServiceMemory()
        service = AssistantService(
            llm=object(), memory=memory, conversation=conversation, voice=object(),
            character={"name": "Synthetic", "_character_id": self.character_id},
            character_prompt="prompt", tts=_SilentTts(),
            response_generator=lambda *_args: "Synthetic reply.",
            memory_v2_shadow_writer=self.writer, character_id=self.character_id,
            memory_authority="v1",
        )
        events = []
        service.subscribe(events.append)

        self.assertTrue(service.process_text_turn("I'm playing Noita.", speak=False).succeeded)
        self.assertEqual("playing Noita", self._activity().value)
        self.assertEqual([], memory.processed)
        self.assertTrue(service.process_text_turn("Oh, I died.", speak=False).succeeded)
        self.assertEqual([], memory.processed)
        self.assertTrue(service.process_text_turn("Tell me a joke.", speak=False).succeeded)
        self.assertEqual(1, len(memory.processed))
        self.assertTrue(service.process_text_turn("Let's roleplay that we're in Silvervale.", speak=False).succeeded)
        scope_event = [event for event in events if event.type == "truth_scope_changed"][-1]
        self.assertEqual(("scenario", "Silvervale"), (
            scope_event.data["scope_kind"], scope_event.data["scope_label"],
        ))
        self.assertEqual("scenario", service.truth_scope_status()["kind"])
        self.assertEqual(1, len(memory.processed))

    def test_transition_response_uses_new_scope_and_restart_remains_coherent(self):
        service, _memory = self._service()
        real_scope_id = self.repository.active_truth_scope(self.character_id).truth_scope_id

        self.assertTrue(service.process_text_turn("Hello in real life.", speak=False).succeeded)
        self.assertTrue(service.process_text_turn(
            "Let's roleplay that we're in Silvervale.", speak=False,
        ).succeeded)
        scenario_scope_id = self.repository.active_truth_scope(self.character_id).truth_scope_id
        self.assertNotEqual(real_scope_id, scenario_scope_id)
        self.assertTrue(service.process_text_turn("I live in the Scarlet Mansion.", speak=False).succeeded)
        self.assertTrue(service.process_text_turn("Back to real life.", speak=False).succeeded)
        self.assertTrue(service.process_text_turn("Where are we now?", speak=False).succeeded)

        records = service.conversation.messages
        pair_scopes = [records[index]["truth_scope"] for index in range(0, len(records), 2)]
        self.assertEqual([
            {"kind": "real_world", "scope_id": real_scope_id},
            {"kind": "real_world", "scope_id": real_scope_id},
            {"kind": "scenario", "scope_id": scenario_scope_id},
            {"kind": "scenario", "scope_id": scenario_scope_id},
            {"kind": "real_world", "scope_id": real_scope_id},
        ], pair_scopes)
        self.assertEqual(records[0]["truth_scope"], records[1]["truth_scope"])
        self.assertEqual("scenario", records[3]["truth_scope"]["kind"])
        self.assertEqual(records[4]["truth_scope"], records[5]["truth_scope"])
        self.assertEqual("real_world", records[7]["truth_scope"]["kind"])
        self.assertEqual(records[8]["truth_scope"], records[9]["truth_scope"])

        reloaded = Conversation(
            object(), conversation_file=self.conversation_file,
            summary_file=self.root / "summary.json",
        )
        self.assertEqual(records, reloaded.messages)
        self._restart_writer_for_service(service)
        self.assertEqual(real_scope_id, service.truth_scope_provenance()["scope_id"])

    def test_failed_turn_does_not_persist_a_half_scoped_exchange(self):
        service, _memory = self._service()
        service._response_generator = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("synthetic provider failure")
        )
        result = service.process_text_turn("This turn must roll back.", speak=False)
        self.assertFalse(result.succeeded)
        self.assertEqual([], service.conversation.messages)
        if self.conversation_file.exists():
            self.assertEqual([], json.loads(self.conversation_file.read_text(encoding="utf-8")))

    def test_legacy_and_malformed_scope_records_follow_conservative_policy(self):
        scope_id = self.repository.active_truth_scope(self.character_id).truth_scope_id
        active = active_scope_from_provenance({"kind": "real_world", "scope_id": scope_id})
        legacy = {"role": "user", "content": "legacy"}
        legacy_reply = {"role": "assistant", "content": "legacy reply"}
        malformed = {
            "role": "assistant", "content": "bad",
            "truth_scope": {"kind": "unknown", "scope_id": scope_id},
        }
        unknown_id = {
            "role": "user", "content": "unknown id",
            "truth_scope": {"kind": "real_world", "scope_id": "scope-" + str(uuid.uuid4())},
        }
        self.assertEqual(LEGACY_UNTAGGED_SCOPE, parse_canonical_truth_scope(legacy).kind)
        self.assertEqual(INVALID_SCOPE, parse_canonical_truth_scope(malformed).kind)
        self.assertEqual(INVALID_SCOPE, parse_canonical_truth_scope(
            unknown_id, valid_scope_ids={scope_id},
        ).kind)
        self.assertEqual([legacy, legacy_reply], filter_scope_compatible_history(
            [legacy, legacy_reply, malformed, unknown_id], active, valid_scope_ids={scope_id},
        ))

    def test_scenario_biography_attempts_never_reach_memory_v1_until_real_world(self):
        memory = _ServiceMemory()
        memory.memories = [{"id": "old", "category": "identity", "content": "Existing fact"}]
        service, _ = self._service(memory=memory)
        self.assertTrue(service.process_text_turn(
            "Let's roleplay that we're in Silvervale.", speak=False,
        ).succeeded)
        attempts = (
            "My real name is Marisa.",
            "I live at 10 Youkai Road.",
            "I work as a shrine maiden.",
            "My favorite food is magic mushrooms.",
            "I have always owned a dragon.",
        )
        for statement in attempts:
            self.assertTrue(service.process_text_turn(statement, speak=False).succeeded)
        self.assertEqual([], memory.processed)
        self.assertEqual([{"id": "old", "category": "identity", "content": "Existing fact"}], memory.memories)

        self.assertTrue(service.process_text_turn("Back to real life.", speak=False).succeeded)
        self.assertEqual([], memory.processed)
        for statement in attempts:
            self.assertTrue(service.process_text_turn(statement, speak=False).succeeded)
        self.assertEqual(list(attempts), [item[0] for item in memory.processed])
        self.assertEqual([{"id": "old", "category": "identity", "content": "Existing fact"}], memory.memories)

    def test_explicit_continuity_controls_are_idempotent_restart_safe_and_non_destructive(self):
        service, _memory = self._service()

        self.assertTrue(service.process_text_turn("I'm playing Noita.", speak=False).succeeded)
        before_clear = self.conversation_file.read_bytes()
        clear_revision = service.continuity_snapshot()["revision"]
        clear_id = str(uuid.uuid4())
        cleared = service.apply_continuity_control(
            command_id=clear_id, action="clear_activity",
            expected_revision=clear_revision,
        )
        self.assertEqual("applied", cleared["outcome"])
        self.assertIsNone(cleared["continuity"]["activity"])
        self.assertEqual(before_clear, self.conversation_file.read_bytes())
        self._restart_writer_for_service(service)
        self.assertIsNone(service.continuity_snapshot()["activity"])
        duplicate = service.apply_continuity_control(
            command_id=clear_id, action="clear_activity",
            expected_revision=clear_revision,
        )
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(RuntimeError):
            service.apply_continuity_control(
                command_id=str(uuid.uuid4()), action="clear_activity",
                expected_revision=clear_revision,
            )

        self.assertTrue(service.process_text_turn("I'm waiting for my GPU.", speak=False).succeeded)
        resolve_snapshot = service.continuity_snapshot()
        resolved_token = resolve_snapshot["open_threads"][0]["action_token"]
        resolved_thread_id = self._threads()[0].thread_id
        canonical_before_resolve = self.conversation_file.read_bytes()
        service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="resolve_thread",
            expected_revision=resolve_snapshot["revision"], action_token=resolved_token,
        )
        self.assertEqual(canonical_before_resolve, self.conversation_file.read_bytes())
        self._restart_writer_for_service(service)
        self.assertEqual("resolved", self.repository.get_open_thread(
            self.character_id, resolved_thread_id,
        ).status)

        self.assertTrue(service.process_text_turn("I need to finish setting up Plex.", speak=False).succeeded)
        cancel_snapshot = service.continuity_snapshot()
        cancelled_token = cancel_snapshot["open_threads"][0]["action_token"]
        cancelled_thread_id = self._threads()[0].thread_id
        service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="cancel_thread",
            expected_revision=cancel_snapshot["revision"], action_token=cancelled_token,
        )
        self._restart_writer_for_service(service)
        self.assertEqual("cancelled", self.repository.get_open_thread(
            self.character_id, cancelled_thread_id,
        ).status)

        self.assertTrue(service.process_text_turn(
            "Let's roleplay that we're in Silvervale.", speak=False,
        ).succeeded)
        scenario_id = self.repository.active_truth_scope(self.character_id).truth_scope_id
        leave_snapshot = service.continuity_snapshot()
        canonical_before_leave = self.conversation_file.read_bytes()
        service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="leave_scenario",
            expected_revision=leave_snapshot["revision"],
        )
        self.assertEqual(canonical_before_leave, self.conversation_file.read_bytes())
        self._restart_writer_for_service(service)
        self.assertEqual("real_world", self.repository.active_truth_scope(self.character_id).kind)
        self.assertEqual("inactive", next(
            item.status for item in self.repository.list_truth_scopes(self.character_id)
            if item.truth_scope_id == scenario_id
        ))

    def test_development_snapshot_exposes_bounded_actor_and_scene_qa_details(self):
        service, _memory = self._service()
        for text in (
            "I'm going to bed.", "Go make dinner.", "*I put on my white hoodie*",
            "Oh I spilled coffee on it.", "It got wet.",
        ):
            self.assertTrue(service.process_text_turn(text, speak=False).succeeded)
        snapshot = service.continuity_snapshot()
        self.assertEqual("sleeping", snapshot["activity"]["value"])
        self.assertEqual("making dinner", snapshot["companion_activity"]["value"])
        self.assertEqual(1, len(snapshot["scene_subjects"]))
        scene = snapshot["scene_subjects"][0]
        self.assertEqual("hoodie", scene["kind"])
        self.assertEqual("Real world", scene["scope"])
        self.assertIn("color: white", scene["summary"])
        self.assertIn("stain: coffee", scene["summary"])
        self.assertIn("wet: true", scene["summary"])
        self.assertIn("worn by: user", scene["summary"])
        self.assertRegex(scene["confirmed"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
        self.assertNotIn("scene_subject_id", json.dumps(snapshot))

    def test_scene_administrative_controls_clear_only_one_cause_and_retire_only_dormant(self):
        service, _memory = self._service()
        for text in (
            "I blindfold you.",
            "I cover your eyes with my hands.",
            "I hand you a red cup.",
            "Put the red cup down.",
        ):
            self.assertTrue(service.process_text_turn(text, speak=False).succeeded)
        canonical_before = self.conversation_file.read_bytes()
        snapshot = service.continuity_snapshot()
        vision_causes = [
            row for row in snapshot["scene_relations"]
            if row.get("facet") == "eyes" and row.get("can_clear")
        ]
        self.assertEqual(2, len(vision_causes))
        self.assertTrue(all(str(row["clear_token"]).startswith("relation:") for row in vision_causes))

        first = service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="clear_scene_relation",
            expected_revision=snapshot["revision"], action_token=vision_causes[0]["clear_token"],
        )
        self.assertEqual("applied", first["outcome"])
        effects = self.repository.capability_effects(self.character_id)
        self.assertEqual("unavailable", effects.vision_mode)
        self.assertEqual(1, len(effects.vision_causes))

        updated = first["continuity"]
        remaining = next(
            row for row in updated["scene_relations"]
            if row.get("facet") == "eyes" and row.get("can_clear")
        )
        second = service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="clear_scene_relation",
            expected_revision=updated["revision"], action_token=remaining["clear_token"],
        )
        self.assertEqual("available", self.repository.capability_effects(self.character_id).vision_mode)

        dormant = next(
            row for row in second["continuity"]["scene_subjects"]
            if row.get("lifecycle") == "dormant" and row.get("can_remove")
            and row.get("kind") == "cup"
        )
        retired = service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="retire_scene_subject",
            expected_revision=second["continuity"]["revision"],
            action_token=dormant["remove_token"],
        )
        self.assertEqual("applied", retired["outcome"])
        self.assertNotIn("red cup", json.dumps(retired["continuity"]).casefold())
        self.assertEqual(canonical_before, self.conversation_file.read_bytes())


if __name__ == "__main__":
    unittest.main()
