"""Scene commands against synthetic canonical JSON, SQLite and real service paths."""

import json
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from benchmarks.active_state.production_session import (
    ProductionSession, SyntheticLifecycleTts, response_envelope,
)
from conversation.conversation import Conversation
from test_cancelled_response_repair import FocusedPtt


class PreparedTts(SyntheticLifecycleTts):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def prepare_stream_chunk(self, text):
        self.entered.set()
        if self.block and not self.release.wait(3):
            raise TimeoutError("Synthetic synthesis gate timed out")
        return text

    def start_prepared_chunk(self, prepared):
        return super().speak(prepared)


class SceneEventPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.session = ProductionSession(self.id(), tts=PreparedTts())
        self.addCleanup(self.session.close)
        self.threads = []
        self.gates = [self.session.tts.release]
        self.addCleanup(self.join_workers)
        self.session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        self.service = self.session.service
        self.service._response_generator = None
        self.service._ptt_factory = FocusedPtt
        self.session.provider.generate = Mock(return_value=response_envelope("*Blinks.*"))
        self.events = []
        self.service.subscribe(self.events.append)
        self.recorder = Mock(enabled=False)
        recorder = patch("assistant_service.development_flight_recorder", return_value=self.recorder)
        recorder.start()
        self.addCleanup(recorder.stop)
        self.session.memory.process = Mock()
        snapshot = self.service.continuity_snapshot()
        row = next(row for row in snapshot["scene_relations"] if row["cause"] == "blindfold")
        self.command = dict(
            command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=snapshot["revision"], action_token=row["clear_token"],
        )

    def gate(self):
        event = threading.Event()
        self.gates.append(event)
        return event

    def start(self, callback):
        future = Future()

        def run():
            try:
                future.set_result(callback())
            except BaseException as error:
                future.set_exception(error)

        thread = threading.Thread(target=run)
        self.threads.append(thread)
        thread.start()
        return future

    def join_workers(self):
        for gate in self.gates:
            gate.set()
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive(), "Synthetic worker did not stop")

    def control_rows(self):
        return self.session.writer.store.connection.execute(
            "SELECT event_id,payload_json FROM events WHERE character_id=? AND event_type='continuity_control'",
            (self.session.character_id,),
        ).fetchall()

    def reopen(self):
        return Conversation(
            self.session.provider, conversation_file=self.session.conversation_file,
            summary_file=self.session.summary_file,
        ).messages

    def assert_terminal(self, outcome):
        starts = [event.data["turn_id"] for event in self.events if event.type == "turn_started"]
        self.assertEqual(1, len(starts))
        terminals = [call.kwargs for call in self.recorder.mark.call_args_list
                     if call.args == ("turn_terminal_outcome",) and call.kwargs.get("turn_id") == starts[0]]
        self.assertEqual([outcome], [row["outcome"] for row in terminals])
        public = [event for event in self.events
                  if event.type in {"assistant_response", "turn_cancelled", "error"}
                  and event.data.get("turn_id") == starts[0]]
        self.assertEqual(1, len(public))
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def assert_no_reaction(self):
        self.assertFalse(any(event.type in {"turn_started", "assistant_delta", "assistant_response", "turn_cancelled"}
                             for event in self.events))
        self.session.provider.generate.assert_not_called()

    @staticmethod
    def partial_write(_data, handle, **_kwargs):
        handle.write('[{"role":')
        raise OSError("synthetic-private-path fake-secret")

    def records(self):
        return json.loads(self.session.conversation_file.read_text(encoding="utf-8"))

    def scene_records(self):
        return [row for row in self.records() if row.get("origin", {}).get("kind") == "scene_ui"]

    def test_unavailable_reaction_still_commits_exactly_one_event_across_retry(self):
        self.service._response_generator = None
        self.session.provider.is_available = False
        result = self.service.apply_continuity_control(**self.command)
        retry = self.service.apply_continuity_control(**self.command)
        self.assertEqual("applied", result["outcome"])
        self.assertTrue(retry["duplicate"])
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual(1, len(self.scene_records()))
        self.assertEqual("*I take off your blindfold.*", self.scene_records()[0]["content"])
        self.assertEqual(1, len(self.control_rows()))
        mutations = self.session.writer.store.connection.execute(
            "SELECT COUNT(*) FROM active_scene_relation_events WHERE event_id=? AND operation='clear'",
            (self.control_rows()[0]["event_id"],),
        ).fetchone()[0]
        self.assertEqual(1, mutations)
        self.assert_no_reaction()
        self.assertEqual(self.records(), self.reopen())

    def test_scene_reaction_expression_is_carried_without_replaying_generated_prose(self):
        self.session.provider.generate.return_value = (
            '{"dialogue":"*Smiles.*","presentation":{"emotion":"happy"}}')
        result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("committed", result["canonical_event"]["state"])
        final = next(e for e in self.events if e.type == "assistant_response")
        self.assertEqual("happy", final.data["presentation"]["emotion"])
        self.assertEqual(1, len(self.scene_records()))
        self.session.provider.generate.return_value = "I am listening."
        self.assertTrue(self.service.process_text_turn("Hello there.", speak=False).succeeded)
        self.assertIn("Last published model-metadata facial request: happy", self.session.provider.generate.call_args.args[1])
        self.assertEqual(1, len(self.scene_records()))

    def test_event_commits_before_generation_and_is_published_once(self):
        def generate(*_args):
            self.assertEqual(1, len(self.scene_records()))
            self.assertEqual("available", self.session.effects().vision_mode)
            self.assertEqual(self.records(), self.reopen())
            return response_envelope("It's good to see you again.")

        self.session.provider.generate.side_effect = generate
        result = self.service.apply_continuity_control(**self.command)
        record = self.scene_records()[0]
        self.assertTrue(result["reaction"]["published"])
        self.assertEqual("user", record["role"])
        self.assertEqual(self.command["command_id"], record["origin"]["command_id"])
        self.assertEqual(self.session.character_id, record["origin"]["character_id"])
        self.assertEqual(self.control_rows()[0]["event_id"], record["origin"]["control_event_id"])
        self.assertEqual(self.service.truth_scope_provenance(), record["truth_scope"])
        kinds = [event.type for event in self.events]
        self.assertLess(kinds.index("conversation_message"), kinds.index("turn_started"))
        self.assertFalse(any(event.type == "assistant_delta" for event in self.events))
        self.assertEqual(1, sum(event.type == "conversation_message" and event.data["role"] == "user"
                                for event in self.events))
        self.assertEqual(["It's good to see you again."], self.session.tts.spoken)
        self.assert_terminal("published")
        before = self.records()
        calls = self.session.provider.generate.call_count
        for _ in range(3):
            retry = self.service.apply_continuity_control(**self.command)
            self.assertTrue(retry["duplicate"])
            self.assertNotIn("reaction", retry)
        self.assertEqual(before, self.records())
        self.assertEqual(calls, self.session.provider.generate.call_count)

    def test_generation_failure_preserves_event_and_next_input_works(self):
        self.session.provider.generate.side_effect = RuntimeError("synthetic-private-path fake-secret")
        result = self.service.apply_continuity_control(**self.command)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual(1, len(self.scene_records()))
        self.assert_terminal("error")
        errors = json.dumps([event.data for event in self.events if event.type == "error"])
        self.assertNotIn("fake-secret", errors)
        self.assertNotIn("synthetic-private-path", errors)
        self.session.provider.generate.side_effect = None
        self.assertTrue(self.service.process_text_turn("Hello again.", speak=False).succeeded)

    def test_invalid_draft_and_failed_repair_keep_event_without_leaking_drafts(self):
        self.session.provider.generate.return_value = response_envelope("The blindfold is still on me.")
        self.session.provider.generate_bounded = Mock(side_effect=RuntimeError("synthetic repair failure"))
        result = self.service.apply_continuity_control(**self.command)
        self.assertTrue(result["reaction"]["published"])
        self.session.provider.generate_bounded.assert_called_once()
        self.assertEqual(1, len(self.scene_records()))
        self.assertFalse(any(event.type == "assistant_delta" for event in self.events))
        self.assertNotIn("blindfold is still", self.records()[-1]["content"])
        self.assert_terminal("published")

    def test_partial_write_preserves_state_then_retry_completes_only_event(self):
        before = self.session.conversation_file.read_bytes()
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("applied_record_incomplete", result["outcome"])
        self.assertEqual("pending", result["canonical_event"]["state"])
        self.assertEqual(before, self.session.conversation_file.read_bytes())
        self.assertEqual(self.records(), self.session.conversation.messages)
        self.assertEqual("available", self.session.effects().vision_mode)
        payload = json.loads(self.control_rows()[0]["payload_json"])
        self.assertEqual("applied", payload["outcome"])
        self.assertEqual("*I take off your blindfold.*", payload["scene_event"]["message"]["content"])
        self.assert_no_reaction()
        self.events.clear()
        result = self.service.apply_continuity_control(**self.command)
        self.assertTrue(result["duplicate"])
        self.assertTrue(result["canonical_event"]["recovered"])
        self.assertEqual([payload["scene_event"]["message"]], self.scene_records())
        self.assertEqual(1, len(self.control_rows()))
        self.assert_no_reaction()
        self.assertEqual(self.records(), self.reopen())
        self.assertTrue(self.service.process_text_turn("Hello again.", speak=False).succeeded)

    def test_retry_after_restart_preserves_original_operation_time_and_scope(self):
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            self.service.apply_continuity_control(**self.command)
        captured = json.loads(self.control_rows()[0]["payload_json"])["scene_event"]["message"]
        self.session.now += timedelta(days=2)
        self.session.restart()
        self.service = self.session.service
        self.session.turn("Let's roleplay that we're in the Labyrinth.", response_envelope("*Nods.*"))
        self.session.turn("You're wearing a red scarf.", response_envelope("*Nods.*"))
        provider = Mock(side_effect=AssertionError("Recovered commands must not react"))
        self.service._response_generator = provider
        result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("committed", result["canonical_event"]["state"])
        self.assertEqual([captured], self.scene_records())
        self.assertEqual("real_world", captured["truth_scope"]["kind"])
        self.assertEqual("scenario", self.service.truth_scope_provenance()["kind"])
        self.assertTrue(any(row.cause == "red scarf" for row in self.session.relations()))
        provider.assert_not_called()
        self.assertEqual(1, len(self.control_rows()))

    def test_interruption_between_sqlite_and_json_is_recoverable_after_restart(self):
        class SyntheticProcessExit(BaseException):
            pass

        with patch.object(self.service, "_complete_scene_control_record", side_effect=SyntheticProcessExit):
            with self.assertRaises(SyntheticProcessExit):
                self.service.apply_continuity_control(**self.command)
        self.assertEqual([], self.scene_records())
        self.assertEqual(1, len(self.control_rows()))
        self.session.restart()
        result = self.session.service.apply_continuity_control(**self.command)
        self.assertTrue(result["canonical_event"]["recovered"])
        self.assertEqual(1, len(self.scene_records()))
        self.assertNotIn("reaction", result)

    def test_post_replacement_failure_does_not_append_or_react_on_retry_or_restart(self):
        with patch("conversation.persistence._sync_directory", side_effect=OSError("synthetic-private-path")):
            result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("applied_durability_unconfirmed", result["outcome"])
        self.assertTrue(result["canonical_event"]["replacement_committed"])
        self.assertEqual(1, len(self.scene_records()))
        before = self.records()
        self.assert_no_reaction()
        self.session.restart()
        with patch.object(self.session.conversation, "save", wraps=self.session.conversation.save) as save:
            retry = self.session.service.apply_continuity_control(**self.command)
            save.assert_not_called()
        self.assertTrue(retry["duplicate"])
        self.assertNotIn("reaction", retry)
        self.assertEqual(before, self.records())

    def test_rejected_commands_and_administrative_clear_create_no_scene_event(self):
        before = self.records()
        for change in ({"command_id": "bad"}, {"expected_revision": "stale"},
                       {"action_token": "relation:99"}):
            with self.subTest(change=change):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.service.apply_continuity_control(**{**self.command, **change})
                self.assertEqual(before, self.records())
                self.assertEqual("unavailable", self.session.effects().vision_mode)
        result = self.service.apply_continuity_control(**{**self.command, "action": "clear_scene_relation"})
        self.assertEqual("applied", result["outcome"])
        self.assertNotIn("reaction", result)
        self.assertEqual(before, self.records())
        self.assert_no_reaction()

    def test_legacy_or_damaged_control_payload_is_not_reconstructed_from_current_state(self):
        with patch("conversation.persistence.json.dump", side_effect=self.partial_write):
            self.service.apply_continuity_control(**self.command)
        row = self.control_rows()[0]
        payload = json.loads(row["payload_json"])
        payload.pop("scene_event")
        with self.session.writer.store.transaction():
            self.session.writer.store.connection.execute(
                "UPDATE events SET payload_json=? WHERE event_id=?",
                (json.dumps(payload), row["event_id"]),
            )
        result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("unrecoverable", result["canonical_event"]["state"])
        self.assertEqual([], self.scene_records())
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assert_no_reaction()

    def test_generated_prose_is_not_observed_as_new_user_evidence(self):
        with patch.object(self.service, "_observe_current_continuity") as current, \
                patch.object(self.service, "_observe_durable_identity_name") as durable, \
                patch.object(self.service, "_observe_contextual_open_thread_shadow") as threads:
            self.service.apply_continuity_control(**self.command)
        current.assert_not_called()
        durable.assert_not_called()
        threads.assert_not_called()
        self.session.memory.process.assert_not_called()
        from memory_v2_historical_evidence import resolve_historical_evidence
        record = self.scene_records()[0]
        decision = resolve_historical_evidence(
            self.records(), self.records().index(record),
            valid_scope_ids={record["truth_scope"]["scope_id"]},
        )
        self.assertTrue(decision.accepted)
        self.assertEqual("generated_scene_ui", decision.evidence.source_class)
        self.assertFalse(decision.evidence.retrieval_eligible)
        mirrored = self.session.writer.observe_canonical_user_message(
            record, conversation_index=self.records().index(record),
            conversation_file=self.session.conversation_file,
        )
        self.assertEqual("ignored", mirrored["state"])
        from memory_v2_episode_compaction import canonical_episode_source_groups
        groups = canonical_episode_source_groups(
            self.records(), valid_scope_ids={record["truth_scope"]["scope_id"]},
        )
        self.assertEqual("generated_scene_ui_run", groups[-1].source_kind)
        self.assertEqual(len(self.records()), groups[-1].end_index_exclusive)

    def blocked_generation(self, *, repair=False, fail=False):
        entered, release = self.gate(), self.gate()

        def blocked(*_args, **_kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Synthetic provider gate timed out")
            if fail:
                raise RuntimeError("Synthetic provider failure")
            return response_envelope("*Blinks naturally.*")

        if repair:
            self.session.provider.generate.return_value = response_envelope("The blindfold is still on me.")
            self.session.provider.generate_bounded = Mock(side_effect=blocked)
        else:
            self.session.provider.generate.side_effect = blocked
        future = self.start(lambda: self.service.apply_continuity_control(**self.command))
        self.assertTrue(entered.wait(3))
        self.assertEqual(1, len(self.scene_records()))
        return future, release

    def cancel_blocked_reaction(self, *, repair=False, fail=False):
        future, release = self.blocked_generation(repair=repair, fail=fail)
        press = self.start(self.service.push_to_talk_press)
        press.result(3)
        self.assertFalse(release.is_set())
        release.set()
        result = future.result(3)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual(1, len(self.scene_records()))
        self.assertFalse(any(event.type in {"assistant_response", "assistant_delta"} for event in self.events))
        self.assertEqual([], self.session.tts.spoken)
        self.assert_terminal("cancelled")
        self.service.push_to_talk_release()
        self.session.provider.generate.side_effect = None
        self.session.provider.generate.return_value = response_envelope("*Nods.*")
        self.assertTrue(self.service.process_text_turn("Hello again.", speak=False).succeeded)

    def test_ptt_during_generation_preserves_only_scene_event(self):
        self.cancel_blocked_reaction()

    def test_ptt_during_valid_repair_preserves_only_scene_event(self):
        self.cancel_blocked_reaction(repair=True)

    def test_ptt_during_failed_repair_preserves_only_scene_event(self):
        self.cancel_blocked_reaction(repair=True, fail=True)

    def test_replacement_during_repair_keeps_event_and_replacement_ownership(self):
        future, release = self.blocked_generation(repair=True)
        cancel_seen = self.gate()
        self.session.provider.cancel_active_generation = cancel_seen.set
        self.session.provider.generate.return_value = response_envelope("Fresh reply.")
        replacement = self.start(lambda: self.service.process_text_turn("Hello again.", speak=False))
        self.assertTrue(cancel_seen.wait(3))
        release.set()
        self.assertFalse(future.result(3)["reaction"]["published"])
        self.assertTrue(replacement.result(3).succeeded)
        self.assertEqual(1, len(self.scene_records()))
        self.assertEqual("Fresh reply.", self.records()[-1]["content"])
        self.assertEqual(0, self.session.reducer.phantom_turns)
        self.assertIsNone(self.service._active_turn_cancel)

    def test_character_switch_preparation_cancels_reaction_but_keeps_original_event(self):
        future, release = self.blocked_generation()
        self.service.prepare_character_switch()
        release.set()
        self.assertFalse(future.result(3)["reaction"]["published"])
        self.assertTrue(self.service.wait_for_character_switch_idle(1))
        self.assertEqual(self.session.character_id, self.scene_records()[0]["origin"]["character_id"])
        self.assert_terminal("cancelled")

    def test_ptt_between_event_save_and_reaction_start_cannot_be_reclaimed(self):
        original = self.service.process_text_turn

        def interrupt_before_reaction(*args, **kwargs):
            self.service.push_to_talk_press()
            return original(*args, **kwargs)

        with patch.object(self.service, "process_text_turn", side_effect=interrupt_before_reaction):
            result = self.service.apply_continuity_control(**self.command)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual(1, len(self.scene_records()))
        self.assert_no_reaction()

    def test_delayed_reaction_entry_cannot_stop_replacement_playback(self):
        original = self.service.process_text_turn
        self.session.provider.generate.return_value = response_envelope("Fresh spoken response.")
        replacement_playback = []

        def replacement_before_reaction(*args, **kwargs):
            self.assertTrue(original("Hello again.", speak=True).succeeded)
            replacement_playback.append(self.service._active_tts_playback_id)
            self.assertNotEqual(0, replacement_playback[-1])
            return original(*args, **kwargs)

        with patch.object(self.service, "process_text_turn", side_effect=replacement_before_reaction):
            result = self.service.apply_continuity_control(**self.command)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual(replacement_playback[0], self.service._active_tts_playback_id)
        self.assertEqual(["Fresh spoken response."], self.session.tts.spoken)
        self.assertEqual(1, len(self.scene_records()))

    def test_ptt_completes_while_committed_reaction_synthesis_remains_blocked(self):
        self.session.provider.generate.return_value = response_envelope("Good to see you.")
        self.session.tts.block = True
        future = self.start(lambda: self.service.apply_continuity_control(**self.command))
        self.assertTrue(self.session.tts.entered.wait(3))
        self.assertEqual("Good to see you.", self.records()[-1]["content"])
        self.start(self.service.push_to_talk_press).result(3)
        self.assertFalse(self.session.tts.release.is_set())
        self.assertTrue(any(event.type == "voice_state" and event.data.get("state") == "listening"
                            for event in self.events))
        self.session.tts.release.set()
        self.assertTrue(future.result(3)["reaction"]["published"])
        self.assertEqual([], self.session.tts.spoken)
        self.assertFalse(any(event.type == "tts_state" and event.data.get("state") == "playback_started"
                             for event in self.events))
        self.assertEqual(1, len(self.scene_records()))
        self.assert_terminal("published")

    def test_event_save_is_mandatory_when_ptt_wins_before_replacement(self):
        entered, release = self.gate(), self.gate()
        replace = os.replace

        def blocked(source, destination):
            if Path(destination) == self.session.conversation_file:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Synthetic replacement gate timed out")
            return replace(source, destination)

        with patch("conversation.persistence.os.replace", side_effect=blocked):
            future = self.start(lambda: self.service.apply_continuity_control(**self.command))
            self.assertTrue(entered.wait(3))
            self.assertEqual([], self.scene_records())
            self.start(self.service.push_to_talk_press).result(3)
            self.assertFalse(release.is_set())
            release.set()
            result = future.result(3)
        self.assertEqual(1, len(self.scene_records()))
        self.assertFalse(result["reaction"]["published"])
        self.assert_no_reaction()

    def test_failed_assistant_save_cannot_remove_committed_scene_event(self):
        dump = json.dump
        writes = []

        def second_write_fails(data, handle, **kwargs):
            writes.append(True)
            if len(writes) == 2:
                self.partial_write(data, handle, **kwargs)
            return dump(data, handle, **kwargs)

        with patch("conversation.persistence.json.dump", side_effect=second_write_fails):
            result = self.service.apply_continuity_control(**self.command)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual(1, len(self.scene_records()))
        self.assertEqual(self.scene_records()[0], self.records()[-1])
        self.assertEqual(self.records(), self.reopen())
        self.assertFalse(any(event.type == "assistant_response" for event in self.events))
        self.assert_terminal("persistence_error")

    def test_speak_only_extension_cannot_synthesize_under_interruption_lock(self):
        self.session.tts.prepare_stream_chunk = None
        self.session.tts.start_prepared_chunk = None
        self.session.provider.generate.return_value = response_envelope("Good to see you.")
        result = self.service.apply_continuity_control(**self.command)
        self.assertTrue(result["reaction"]["published"])
        self.assertEqual([], self.session.tts.spoken)
        self.assert_terminal("published")

    def test_later_scene_command_cancels_repair_and_preserves_both_events(self):
        self.session.turn("I cover your eyes with my hands.", response_envelope("*Nods.*"))
        snapshot = self.service.continuity_snapshot()
        self.command.update(
            expected_revision=snapshot["revision"],
            action_token=next(row["clear_token"] for row in snapshot["scene_relations"]
                              if row["cause"] == "blindfold"),
        )
        self.events.clear()
        future, release = self.blocked_generation(repair=True)
        cancel_seen = self.gate()
        self.session.provider.cancel_active_generation = cancel_seen.set
        after = self.service.continuity_snapshot()
        self.session.provider.generate.return_value = response_envelope("*Blinks naturally.*")
        second = self.start(lambda: self.service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=after["revision"], action_token=after["scene_relations"][0]["clear_token"],
        ))
        self.assertTrue(cancel_seen.wait(3))
        release.set()
        self.assertFalse(future.result(3)["reaction"]["published"])
        self.assertTrue(second.result(3)["reaction"]["published"])
        self.assertEqual(2, len(self.scene_records()))
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual(2, len(self.control_rows()))
        self.assertEqual(0, self.session.reducer.phantom_turns)
        self.assertEqual(1, sum(event.type == "turn_cancelled" for event in self.events))
        self.assertEqual(1, sum(event.type == "assistant_response" for event in self.events))

    def test_authoritative_scope_change_without_token_change_discards_reaction(self):
        future, release = self.blocked_generation()
        store, character = self.session.writer.store, self.session.character_id
        event_id = str(uuid.uuid4())
        with store.transaction():
            sequence = store.connection.execute(
                "SELECT MAX(sequence)+1 FROM events WHERE character_id=?", (character,),
            ).fetchone()[0]
            store.add_event(character, event_id, sequence, content_text="Labyrinth")
            scope = store.create_scenario_truth_scope(
                character, "Labyrinth", evidence_event_id=event_id,
                evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=9,
            )
            store.activate_truth_scope(
                character, scope, evidence_event_id=event_id,
                evidence_excerpt_start_cp=0, evidence_excerpt_end_cp=9,
            )
        release.set()
        self.assertFalse(future.result(3)["reaction"]["published"])
        self.assertEqual("real_world", self.scene_records()[0]["truth_scope"]["kind"])
        self.assertEqual("scenario", self.service.truth_scope_provenance()["kind"])
        self.assert_terminal("cancelled")

    def test_reaction_start_after_character_change_is_discarded(self):
        # Rebind at the exact pre-reaction seam using a second wholly synthetic
        # owner. Production character switching cancels before rebinding too.
        second = ProductionSession(self.id() + "-second")
        self.addCleanup(second.close)
        original = self.service.process_text_turn

        def switched(*args, **kwargs):
            with patch.object(self.service, "character_id", second.character_id), \
                    patch.object(self.service, "character", second.service.character), \
                    patch.object(self.service, "_memory_v2_shadow_writer", second.writer), \
                    patch.object(self.service, "conversation", second.conversation):
                return original(*args, **kwargs)

        with patch.object(self.service, "process_text_turn", side_effect=switched):
            result = self.service.apply_continuity_control(**self.command)
        self.assertFalse(result["reaction"]["published"])
        self.assertEqual([], second.conversation.messages)
        self.assertEqual(1, len(self.scene_records()))
        self.assert_no_reaction()

    def test_revision_rejects_a_command_from_another_character(self):
        second = ProductionSession(self.id() + "-second")
        self.addCleanup(second.close)
        second.turn("I blindfold you.", response_envelope("*Holds still.*"))
        before = second.conversation_file.read_bytes()
        with self.assertRaises(RuntimeError):
            second.service.apply_continuity_control(**self.command)
        self.assertEqual(before, second.conversation_file.read_bytes())
        self.assertEqual("unavailable", second.effects().vision_mode)

    def test_v2_reaction_composes_scene_and_retrospective_guards(self):
        from assistant_service import AssistantService
        from memory_v2_authority import DevelopmentV2MemoryAuthority

        authority = DevelopmentV2MemoryAuthority(
            self.session.writer.store, self.session.character_id, self.session.conversation.messages,
        )
        self.service = self.session.service = AssistantService(
            self.session.provider, self.session.memory, self.session.conversation, object(),
            self.session.service.character, "Synthetic companion.", self.session.tts,
            memory_v2_shadow_writer=self.session.writer, character_id=self.session.character_id,
            memory_authority="v2", memory_v2_authority=authority,
        )
        self.service.subscribe(self.events.append)
        self.service.subscribe(self.session._collect_event)
        self.session.memory.get_relevant_memories = Mock(side_effect=AssertionError("V1 read forbidden"))
        self.session.provider.generate.return_value = response_envelope("The blindfold is still on me.")
        self.session.provider.generate_bounded = Mock(return_value=response_envelope(
            "You told me your favorite color is blue.",
        ))
        result = self.service.apply_continuity_control(**self.command)
        self.assertTrue(result["reaction"]["published"])
        self.session.provider.generate_bounded.assert_called_once()
        self.assertNotIn("favorite color", self.records()[-1]["content"])
        self.assertNotIn("still on", self.records()[-1]["content"])
        self.assertFalse(any(event.type == "assistant_delta" for event in self.events))
        self.assertEqual(1, len(self.scene_records()))
        self.session.memory.get_relevant_memories.assert_not_called()
        self.session.memory.process.assert_not_called()
        self.assert_terminal("published")

    def test_suppressed_reaction_still_records_event(self):
        from scene_ui_event import scene_ui_clear_event

        with patch("scene_ui_event.scene_ui_clear_event", side_effect=lambda *args: replace(
            scene_ui_clear_event(*args), reaction_opportunity=False,
        )):
            result = self.service.apply_continuity_control(**self.command)
        self.assertNotIn("reaction", result)
        self.assertEqual(1, len(self.scene_records()))
        self.assert_no_reaction()

    def test_invalid_command_linkage_is_not_promoted_to_historical_user_evidence(self):
        from copy import deepcopy
        from memory_v2_historical_evidence import resolve_historical_evidence

        self.service.apply_continuity_control(**self.command)
        original = self.scene_records()[0]
        for change in ({"control_event_id": str(uuid.uuid4())}, {"command_id": str(uuid.uuid4())},
                       {"character_id": str(uuid.uuid4())}, {"generated_event": 1}, {"extra": "unknown"}):
            with self.subTest(change=change):
                record = deepcopy(original)
                record["origin"].update(change)
                decision = resolve_historical_evidence(
                    [record], 0, valid_scope_ids={record["truth_scope"]["scope_id"]},
                )
                self.assertFalse(decision.accepted)

    def test_replacement_failure_reports_pending_record_then_retry_succeeds(self):
        before = self.session.conversation_file.read_bytes()
        with patch("conversation.persistence.os.replace", side_effect=OSError("synthetic replacement failure")):
            result = self.service.apply_continuity_control(**self.command)
        self.assertEqual("replace", result["canonical_event"]["persistence_stage"])
        self.assertEqual(before, self.session.conversation_file.read_bytes())
        self.assert_no_reaction()
        self.assertEqual("committed", self.service.apply_continuity_control(**self.command)["canonical_event"]["state"])
        self.assertEqual(1, len(self.scene_records()))


if __name__ == "__main__":
    unittest.main()
