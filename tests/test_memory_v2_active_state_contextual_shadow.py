import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aifren.assistant_service import AssistantService
from aifren.continuity.memory_v2_active_state_contextual_shadow import (
    ActiveStateContextualShadowObserver,
    JsonlActiveStateShadowTraceStore,
)
from aifren.memory_v2_store import (
    ActiveSceneSubjectIntroduction,
    ActiveStateProposal,
    ActiveStateProposalUpdate,
    MemoryV2Repository,
    MemoryV2Store,
)


class _Extractor:
    provider_id = "local-test"
    model_id = "deterministic-v1"

    def __init__(self, result):
        self.result = result
        self.inputs = []

    def propose(self, extraction_input):
        self.inputs.append(extraction_input)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Conversation:
    def __init__(self, path):
        self.conversation_file = str(path)
        self.messages = []
        self.saved = 0

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content, "timestamp": "2026-01-01T00:00:00Z"})

    def add_assistant_message(self, content):
        self.messages.append({"role": "assistant", "content": content, "timestamp": "2026-01-01T00:00:01Z"})

    def save(self):
        self.saved += 1
        Path(self.conversation_file).write_text(json.dumps(self.messages), encoding="utf-8")

    def update_summary(self):
        return None


class _Memory:
    memories = []

    def process(self, *_args):
        return None


class _Tts:
    def set_volume(self, _value):
        return None

    def speak(self, _text):
        return True

    def stop(self):
        return 0


class _BrokenTraceStore:
    def append(self, _trace):
        raise OSError("synthetic trace failure")


class ActiveStateContextualShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conversation = self.root / "conversation.json"
        self.store = MemoryV2Store()
        self.character = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.store.create_character(self.character, "Synthetic")
        self.store.create_character(self.other, "Other")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def canonical_message(self, content, *, role="user"):
        message = {"role": role, "content": content, "timestamp": "2026-01-01T00:00:00Z"}
        self.conversation.write_text(json.dumps([message]), encoding="utf-8")
        return message

    def observer(self, extractor, *, enabled=True):
        return ActiveStateContextualShadowObserver(
            self.store, character_id=self.character, extractor=extractor,
            trace_store=JsonlActiveStateShadowTraceStore(self.root / "active-shadow.jsonl"), enabled=enabled,
        )

    @staticmethod
    def actor_update(text, actor, attribute, value, fragment):
        start = text.index(fragment)
        return ActiveStateProposalUpdate(None, "set", value, start, start + len(fragment), "actor", actor, attribute)

    @staticmethod
    def scene_update(text, reference, attribute, value, fragment):
        start = text.index(fragment)
        return ActiveStateProposalUpdate(None, "set", value, start, start + len(fragment), "scene", reference, attribute)

    def test_disabled_mode_does_not_call_extractor_create_trace_or_mutate_state(self):
        message = self.canonical_message("I am in the kitchen.")
        extractor = _Extractor(ActiveStateProposal((self.actor_update(message["content"], "user", "location", "kitchen", "kitchen"),)))
        observer = self.observer(extractor, enabled=False)
        result = observer.observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.conversation)
        self.assertEqual("disabled", result["state"])
        self.assertEqual([], extractor.inputs)
        self.assertFalse((self.root / "active-shadow.jsonl").exists())
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

    def test_valid_candidate_is_bounded_validated_traced_and_never_applied(self):
        text = "I am in the kitchen."
        message = self.canonical_message(text)
        proposal = ActiveStateProposal((self.actor_update(text, "user", "location", "kitchen", "kitchen"),))
        extractor = _Extractor(proposal)
        observer = self.observer(extractor)
        # A call to the application API would fail this test. The observer
        # performs only validate_active_state_proposal plus source binding.
        self.store.apply_active_state_proposal = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not apply"))
        result = observer.observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.conversation)
        self.assertEqual("validated", result["state"])
        self.assertEqual("accepted", result["trace"]["validation_status"])
        self.assertEqual("I am in the kitchen.", extractor.inputs[0].latest_user_turn)
        self.assertEqual((), extractor.inputs[0].recent_user_turns)
        self.assertEqual((), extractor.inputs[0].scene_subjects)
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        trace = json.loads((self.root / "active-shadow.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn(text, json.dumps(trace))
        self.assertEqual("local-test", trace["provider"])

    def test_invalid_malformed_provider_and_evidence_fail_open_without_mutation(self):
        text = "I am in the kitchen."
        message = self.canonical_message(text)
        invalid = _Extractor(ActiveStateProposal((
            self.actor_update(text, "user", "mood", "happy", "kitchen"),
        )))
        self.assertEqual("rejected", self.observer(invalid).observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )["state"])
        self.assertEqual("rejected", self.observer(_Extractor("not a proposal")).observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )["state"])
        self.assertEqual("failed", self.observer(_Extractor(RuntimeError("synthetic timeout"))).observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )["state"])
        assistant = self.canonical_message(text, role="assistant")
        blocked = _Extractor(None)
        self.assertEqual("rejected", self.observer(blocked).observe_canonical_user_turn(
            assistant, conversation_index=0, conversation_file=self.conversation,
        )["state"])
        self.assertEqual([], blocked.inputs)
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

    def test_actor_scene_introduction_and_multi_update_candidates_are_validated_but_not_applied(self):
        seed_text = "There is food here."
        self.store.add_event(self.character, "seed", 1, actor_kind="user", recorded_at_us=100,
                             content_text=seed_text, source_origin="synthetic")
        seed = self.store.apply_active_state_proposal(
            self.character,
            ActiveStateProposal((), (ActiveSceneSubjectIntroduction("food", "food", 9, 13),)),
            evidence_event_id="seed",
        )
        food_id = seed[0]["scene_subject_id"]
        text = "I am cooking while you are reading; the food is hot, and there is a cup."
        message = self.canonical_message(text)
        cup_start = text.index("cup")
        proposal = ActiveStateProposal(
            (
                self.actor_update(text, "user", "activity", "cooking", "cooking"),
                self.actor_update(text, "companion", "activity", "reading", "reading"),
                self.scene_update(text, "s1", "condition", "hot", "food is hot"),
            ),
            (ActiveSceneSubjectIntroduction("cup_1", "cup", cup_start, cup_start + 3),),
        )
        result = self.observer(_Extractor(proposal)).observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )
        self.assertEqual("validated", result["state"])
        self.assertEqual("food", MemoryV2Repository(self.store).lookup_scene_attributes(self.character, food_id)[0].value)
        self.assertIsNone(MemoryV2Repository(self.store).lookup_actor_state(self.character, "user", "activity").state)
        self.assertEqual(1, len(MemoryV2Repository(self.store).list_scene_subjects(self.character)))
        labels = set(result["trace"]["risk_labels"])
        self.assertTrue({"actor_target_user", "actor_target_companion", "introduction", "multi_update"} <= labels)

    def test_trace_write_failure_is_fail_open_and_never_applies_candidate(self):
        text = "I am in the kitchen."
        message = self.canonical_message(text)
        observer = ActiveStateContextualShadowObserver(
            self.store,
            character_id=self.character,
            extractor=_Extractor(ActiveStateProposal((
                self.actor_update(text, "user", "location", "kitchen", "kitchen"),
            ))),
            trace_store=_BrokenTraceStore(),
            enabled=True,
        )
        result = observer.observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )
        self.assertEqual("validated", result["state"])
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

    def test_unknown_foreign_or_unpersisted_scene_reference_is_rejected(self):
        other_text = "There is a book here."
        self.store.add_event(self.other, "other-seed", 1, actor_kind="user", recorded_at_us=100,
                             content_text=other_text, source_origin="synthetic")
        foreign = self.store.apply_active_state_proposal(
            self.other,
            ActiveStateProposal((), (ActiveSceneSubjectIntroduction("book", "book", 9, 13),)),
            evidence_event_id="other-seed",
        )[0]["scene_subject_id"]
        text = "The book is open."
        message = self.canonical_message(text)
        bad = ActiveStateProposal((self.scene_update(text, foreign, "state", "open", "open"),))
        result = self.observer(_Extractor(bad)).observe_canonical_user_turn(
            message, conversation_index=0, conversation_file=self.conversation,
        )
        self.assertEqual(("rejected", "unknown_scene_reference"), (result["state"], result["reason"]))
        unpersisted = {**message, "content": "The book is closed."}
        self.assertEqual("rejected", self.observer(_Extractor(None)).observe_canonical_user_turn(
            unpersisted, conversation_index=0, conversation_file=self.conversation,
        )["state"])

    def test_service_runs_enabled_observer_only_after_save_and_disabled_observer_is_invisible(self):
        conversation = _Conversation(self.root / "service-conversation.json")
        text = "I am in the kitchen."
        proposal = ActiveStateProposal((self.actor_update(text, "user", "location", "kitchen", "kitchen"),))
        enabled = self.observer(_Extractor(proposal))
        service = AssistantService(
            object(), _Memory(), conversation, object(), {"_character_id": self.character}, "", _Tts(),
            response_generator=lambda *_args: "reply", active_state_contextual_shadow=enabled,
            memory_authority="v1",
        )
        events = []
        service.subscribe(events.append)
        self.assertTrue(service.process_text_turn(text, speak=False).succeeded)
        self.assertEqual(2, conversation.saved)
        self.assertTrue(any(event.type == "active_state_contextual_shadow" for event in events))
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

        disabled_extractor = _Extractor(proposal)
        disabled = self.observer(disabled_extractor, enabled=False)
        second = _Conversation(self.root / "disabled-conversation.json")
        disabled_service = AssistantService(
            object(), _Memory(), second, object(), {"_character_id": self.character}, "", _Tts(),
            response_generator=lambda *_args: "reply", active_state_contextual_shadow=disabled,
            memory_authority="v1",
        )
        disabled_events = []
        disabled_service.subscribe(disabled_events.append)
        self.assertTrue(disabled_service.process_text_turn(text, speak=False).succeeded)
        self.assertEqual([], disabled_extractor.inputs)
        self.assertFalse(any(event.type == "active_state_contextual_shadow" for event in disabled_events))

        failed = self.observer(_Extractor(RuntimeError("synthetic provider failure")))
        third = _Conversation(self.root / "failed-conversation.json")
        failed_service = AssistantService(
            object(), _Memory(), third, object(), {"_character_id": self.character}, "", _Tts(),
            response_generator=lambda *_args: "reply", active_state_contextual_shadow=failed,
            memory_authority="v1",
        )
        failed_events = []
        failed_service.subscribe(failed_events.append)
        self.assertTrue(failed_service.process_text_turn(text, speak=False).succeeded)
        self.assertTrue(any(
            event.type == "active_state_contextual_shadow" and event.data.get("state") == "failed"
            for event in failed_events
        ))


if __name__ == "__main__":
    unittest.main()
