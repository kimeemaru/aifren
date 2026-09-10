from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from assistant_service import AssistantService
from conversation.conversation import Conversation
from current_continuity import admit_current_continuity_context, extract_active_state_proposal
from durable_fact_curation import extract_durable_fact_proposal
from interaction_policy import classify_interaction_policy, render_sleep_reaction
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository, MemoryV2Store, admit_durable_context
from memory_v2_store.durable_prompt import filter_v1_duplicates
from proactive_companion import (
    evaluate_proactive_eligibility,
    mark_checkins_responded,
    record_displayed_checkin,
)


ACTIVITY_CASES = (
    ("I'm cooking dinner.", "cooking dinner"),
    ("I'm eating.", "eating"),
    ("I'm showering.", "showering"),
    ("I'm cleaning the kitchen.", "cleaning the kitchen"),
    ("I'm studying calculus.", "studying calculus"),
    ("I'm reading Dune.", "reading Dune"),
    ("I'm writing a letter.", "writing a letter"),
    ("I'm coding AIFren.", "coding AIFren"),
    ("I'm exercising.", "exercising"),
    ("I'm making love.", "making love"),
    ("I'm walking downtown.", "walking downtown"),
    ("I'm shopping for groceries.", "shopping for groceries"),
    ("I'm driving home.", "driving home"),
    ("I'm travelling to Ottawa.", "travelling to Ottawa"),
    ("I'm commuting.", "commuting"),
    ("I'm fishing.", "fishing"),
    ("I'm gaming with friends.", "gaming with friends"),
    ("I'm watching Alien.", "watching Alien"),
    ("I'm listening to music.", "listening to music"),
    ("I'm installing Ubuntu.", "installing Ubuntu"),
    ("I'm building a kernel.", "building a kernel"),
    ("I'm configuring my router.", "configuring my router"),
    ("I'm working.", "working"),
    ("I'm resting.", "resting"),
    ("I'm sleeping.", "sleeping"),
    ("I'm getting ready.", "getting ready"),
    ("I'm talking with Morgan.", "talking with Morgan"),
    ("I'm doing chores.", "doing chores"),
    ("I'm browsing the forum.", "browsing the forum"),
    ("I'm drawing a fox.", "drawing a fox"),
    ("I'm repairing my keyboard.", "repairing my keyboard"),
    ("I'm soldering a board.", "soldering a board"),
    ("I'm baking bread.", "baking bread"),
    ("I'm gardening.", "gardening"),
    ("I'm painting the wall.", "painting the wall"),
    ("im cookin' dinner.", "cooking dinner"),
)


def _activity_adversarials(text: str):
    body = text.rstrip(".")
    return (
        ("past", "Yesterday " + body.replace("I'm", "I was").replace("im", "I was")),
        ("hypothetical", "What if " + body + "?"),
        ("negated", body.replace("I'm ", "I'm not ").replace("im ", "im not ") + "."),
    )


class _Harness:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.character_id = str(uuid.uuid4())
        self.base = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        self.rows = []
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id, display_name="Synthetic",
            memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(self.character_id, "Synthetic")

    def close(self):
        self.writer.close()
        self.temp.cleanup()

    def turn(self, text: str):
        index = len(self.rows)
        scope = self.repository.active_truth_scope(self.character_id)
        timestamp = (self.base + timedelta(minutes=index)).isoformat()
        provenance = {"kind": scope.kind, "scope_id": scope.truth_scope_id}
        message = {"role": "user", "content": text, "timestamp": timestamp, "truth_scope": provenance}
        self.rows.extend((message, {
            "role": "assistant", "content": "Synthetic response.",
            "timestamp": (self.base + timedelta(minutes=index, seconds=1)).isoformat(),
            "truth_scope": provenance,
        }))
        self.conversation_file.write_text(json.dumps(self.rows), encoding="utf-8")
        continuity = self.writer.observe_canonical_user_continuity(
            message, conversation_index=index, conversation_file=self.conversation_file,
        )
        durable = self.writer.observe_canonical_user_durable_facts(
            message, conversation_index=index, conversation_file=self.conversation_file,
        )
        return continuity, durable


class ActivityMatrixTests(unittest.TestCase):
    def test_144_generic_activity_positive_and_adversarial_cases(self):
        # 36 phrasings x positive/past/hypothetical/negated = 144 cases.
        for text, expected in ACTIVITY_CASES:
            with self.subTest(kind="positive", text=text):
                proposal = extract_active_state_proposal(text, current_activity=None)
                self.assertIsNotNone(proposal)
                self.assertEqual(expected, proposal.updates[0].value)
                self.assertEqual("user", proposal.updates[0].target_ref)
            for kind, adversarial in _activity_adversarials(text):
                with self.subTest(kind=kind, text=adversarial):
                    self.assertIsNone(extract_active_state_proposal(adversarial, current_activity=None))

    def test_36_production_pipeline_start_reassert_stop_sequences(self):
        harness = _Harness()
        try:
            for text, expected in ACTIVITY_CASES:
                with self.subTest(text=text):
                    first, _ = harness.turn(text)
                    self.assertEqual("applied", first["state"])
                    current = harness.repository.lookup_actor_state(harness.character_id, "user", "activity").state
                    self.assertEqual(expected, current.value)
                    original_start = current.valid_from_us
                    harness.turn(text)
                    reconfirmed = harness.repository.lookup_actor_state(harness.character_id, "user", "activity").state
                    self.assertEqual(original_start, reconfirmed.valid_from_us)
                    self.assertGreaterEqual(reconfirmed.last_confirmed_at_us, original_start)
                    harness.turn("I'm done with that.")
                    self.assertIsNone(harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state)
        finally:
            harness.close()

    def test_108_pipeline_adversarial_sequences_preserve_current_state(self):
        harness = _Harness()
        try:
            for text, _ in ACTIVITY_CASES:
                for kind, adversarial in _activity_adversarials(text):
                    with self.subTest(kind=kind, text=adversarial):
                        harness.turn("I'm resting.")
                        result, _ = harness.turn(adversarial)
                        self.assertEqual("ignored", result["state"])
                        self.assertEqual("resting", harness.repository.lookup_actor_state(
                            harness.character_id, "user", "activity",
                        ).state.value)
                        harness.turn("I'm done with that.")
        finally:
            harness.close()

    def test_36_pipeline_rp_scope_sequences_restore_real_activity(self):
        harness = _Harness()
        try:
            for text, expected in ACTIVITY_CASES:
                with self.subTest(text=text):
                    harness.turn("I'm resting.")
                    harness.turn("Let's roleplay that we're in a synthetic test scene.")
                    harness.turn(text)
                    self.assertEqual(expected, harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state.value)
                    harness.turn("Back to real life.")
                    self.assertEqual("resting", harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state.value)
                    harness.turn("I'm done with that.")
        finally:
            harness.close()

    def test_36_pipeline_restart_sequences_preserve_generic_activity(self):
        harness = _Harness()
        try:
            for text, expected in ACTIVITY_CASES:
                with self.subTest(text=text):
                    harness.turn(text)
                    database = harness.writer.database_path
                    harness.writer.close()
                    harness.writer = MemoryV2ShadowWriter(
                        harness.root, character_id=harness.character_id,
                        display_name="Synthetic", memory_file=harness.memory_file,
                        database_path=database,
                    )
                    harness.repository = MemoryV2Repository(harness.writer.store)
                    self.assertEqual(expected, harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state.value)
                    harness.turn("I'm done with that.")
        finally:
            harness.close()

    def test_actor_targeting_uses_the_second_person_clause_not_nearby_first_person_narration(self):
        harness = _Harness()
        try:
            for text in ("*I pat your head while you sleep*", "*I watch you sleep*"):
                with self.subTest(text=text):
                    result, _ = harness.turn(text)
                    self.assertEqual("ignored", result["state"])
                    self.assertIsNone(harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state)
                    self.assertIsNone(harness.repository.lookup_actor_state(
                        harness.character_id, "companion", "activity",
                    ).state)

            commands = (
                ("Time for you to take a nap.", "sleeping"),
                ("Wake up.", None),
                ("Go to sleep, I'll watch over you.", "sleeping"),
                ("Wake up.", None),
                ("Why don't you go cook us some more chicken tenders to celebrate?",
                 "cooking us some more chicken tenders to celebrate"),
                ("Stop cooking.", None),
                ("Go make dinner.", "making dinner"),
                ("Stop making dinner.", None),
                ("Start reading for a bit.", "reading for a bit"),
            )
            for text, expected in commands:
                with self.subTest(text=text):
                    result, _ = harness.turn(text)
                    self.assertEqual("applied", result["state"])
                    state = harness.repository.lookup_actor_state(
                        harness.character_id, "companion", "activity",
                    ).state
                    self.assertEqual(expected, state.value if state is not None else None)
                    self.assertIsNone(harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state)
            preserved = harness.repository.lookup_actor_state(
                harness.character_id, "companion", "activity",
            ).state.value
            for text in (
                "What if you go cook dinner?", '"Go cook dinner."', "Don't go cook dinner.",
                "Maybe go cook dinner later.", "Let's cook dinner.", "We're cooking dinner.",
            ):
                with self.subTest(kind="companion_abstain", text=text):
                    result, _ = harness.turn(text)
                    self.assertEqual("ignored", result["state"])
                    self.assertEqual(preserved, harness.repository.lookup_actor_state(
                        harness.character_id, "companion", "activity",
                    ).state.value)
        finally:
            harness.close()

    def test_user_and_companion_activity_lifecycles_are_independent(self):
        harness = _Harness()
        try:
            harness.turn("I'm going to bed.")
            harness.turn("Go make dinner.")
            self.assertEqual("sleeping", harness.repository.lookup_actor_state(
                harness.character_id, "user", "activity",
            ).state.value)
            self.assertEqual("making dinner", harness.repository.lookup_actor_state(
                harness.character_id, "companion", "activity",
            ).state.value)
            harness.turn("Stop making dinner.")
            self.assertEqual("sleeping", harness.repository.lookup_actor_state(
                harness.character_id, "user", "activity",
            ).state.value)
            self.assertIsNone(harness.repository.lookup_actor_state(
                harness.character_id, "companion", "activity",
            ).state)
        finally:
            harness.close()

    def test_generic_completion_matches_only_the_current_action_family(self):
        harness = _Harness()
        try:
            harness.turn("I'm eating now.")
            result, _ = harness.turn("They're great. I'm done eating now.")
            self.assertEqual("applied", result["state"])
            self.assertIsNone(harness.repository.lookup_actor_state(
                harness.character_id, "user", "activity",
            ).state)

            for start, wrong, correct in (
                ("I'm cooking dinner.", "I'm done reading now.", "I finished cooking dinner."),
                ("I'm coding AIFren.", "I stopped shopping.", "I'm not coding anymore."),
            ):
                with self.subTest(start=start):
                    harness.turn(start)
                    result, _ = harness.turn(wrong)
                    self.assertEqual("ignored", result["state"])
                    self.assertIsNotNone(harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state)
                    harness.turn(correct)
                    self.assertIsNone(harness.repository.lookup_actor_state(
                        harness.character_id, "user", "activity",
                    ).state)
        finally:
            harness.close()


class SceneSleepAndScopeTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()

    def tearDown(self):
        self.h.close()

    def scene(self):
        subjects = self.h.repository.list_scene_subjects(self.h.character_id)
        return [
            {record.subject_key.rsplit(".", 1)[-1]: record.value
             for record in self.h.repository.lookup_scene_attributes(self.h.character_id, subject.scene_subject_id)}
            for subject in subjects
        ]

    def test_hoodie_attributes_evolve_independently_and_survive_rp(self):
        self.h.turn("I put on my white hoodie.")
        self.h.turn("I spilled coffee on it.")
        self.h.turn("It got wet.")
        self.assertEqual({
            "color": "white", "kind": "hoodie", "region": "torso",
            "stain": "coffee", "wet": "true", "worn_by": "user",
        }, self.scene()[0])
        self.h.turn("Let's roleplay that we're in Silvervale.")
        self.h.turn("I put on my red coat.")
        self.assertEqual("coat", self.scene()[0]["kind"])
        self.h.turn("Back to real life.")
        self.assertEqual("hoodie", self.scene()[0]["kind"])
        self.h.turn("I took it off.")
        current = self.scene()[0]
        self.assertNotIn("worn_by", current)
        self.assertEqual(("white", "coffee", "true"), (current["color"], current["stain"], current["wet"]))

    def test_exact_manual_hoodie_sequence_uses_canonical_evidence_and_typed_admission(self):
        first = "*I put on my white hoodie*"
        result, _ = self.h.turn(first)
        self.assertEqual("applied", result["state"])
        subject = self.h.repository.list_scene_subjects(self.h.character_id)[0]
        introduction = self.h.writer.store.connection.execute(
            """SELECT e.content_text, s.introduced_excerpt_start_cp, s.introduced_excerpt_end_cp
                 FROM active_scene_subjects s JOIN events e
                   ON e.character_id=s.character_id AND e.event_id=s.introduced_event_id
                WHERE s.character_id=? AND s.scene_subject_id=?""",
            (self.h.character_id, subject.scene_subject_id),
        ).fetchone()
        self.assertEqual(first, introduction["content_text"])
        self.assertEqual("I put on my white hoodie", introduction["content_text"][
            introduction["introduced_excerpt_start_cp"]:introduction["introduced_excerpt_end_cp"]
        ])

        second = "Oh I spilled coffee on it."
        self.h.turn(second)
        self.h.turn("It got wet.")
        expected = {
            "color": "white", "kind": "hoodie", "region": "torso", "stain": "coffee",
            "wet": "true", "worn_by": "user",
        }
        self.assertEqual(expected, self.scene()[0])
        stain = next(record for record in self.h.repository.lookup_scene_attributes(
            self.h.character_id, subject.scene_subject_id,
        ) if record.subject_key.endswith(".stain"))
        evidence = self.h.writer.store.connection.execute(
            """SELECT e.content_text, ce.excerpt_start_cp, ce.excerpt_end_cp
                 FROM claim_evidence ce JOIN events e
                   ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                WHERE ce.character_id=? AND ce.claim_id=?""",
            (self.h.character_id, stain.state_id),
        ).fetchone()
        self.assertEqual("coffee", evidence["content_text"][
            evidence["excerpt_start_cp"]:evidence["excerpt_end_cp"]
        ])

        admission = admit_current_continuity_context(
            self.h.repository, self.h.character_id,
            "what am I wearing and what happened to it",
            now_us=int((self.h.base + timedelta(hours=1)).timestamp() * 1_000_000),
        )
        self.assertIn('"kind":"hoodie"', admission.active_state_context)
        self.assertIn('"color":"white"', admission.active_state_context)
        self.assertIn('"stain":"coffee"', admission.active_state_context)
        self.assertIn('"wet":"true"', admission.active_state_context)
        self.assertIn('"worn_by":"user"', admission.active_state_context)

        self.h.turn("I took it off because it got wet.")
        current = self.scene()[0]
        self.assertNotIn("worn_by", current)
        self.assertEqual(("white", "coffee", "true"), (
            current["color"], current["stain"], current["wet"],
        ))

    def test_scene_pronoun_without_a_subject_abstains(self):
        result, _ = self.h.turn("Oh I spilled coffee on it.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual((), self.h.repository.list_scene_subjects(self.h.character_id))

    def test_scene_update_does_not_clear_or_retarget_actor_activity(self):
        self.h.turn("I'm going to bed.")
        self.h.turn("Go make dinner.")
        self.h.turn("*I put on my white hoodie*")
        self.assertEqual("sleeping", self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity",
        ).state.value)
        self.assertEqual("making dinner", self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state.value)

    def test_held_object_moves_without_becoming_inventory(self):
        self.h.turn("I'm holding my controller.")
        self.assertEqual("user", self.scene()[0]["held_by"])
        self.h.turn("I put it on my desk.")
        current = self.scene()[0]
        self.assertNotIn("held_by", current)
        self.assertEqual("desk", current["location"])

    def test_scene_clear_and_retirement_preserve_history(self):
        self.h.turn("I'm holding my controller.")
        subject = self.h.repository.list_scene_subjects(self.h.character_id)[0]
        self.h.turn("I'm no longer holding it.")
        self.assertNotIn("held_by", self.scene()[0])
        self.h.turn("I put it away.")
        self.assertEqual((), self.h.repository.list_scene_subjects(self.h.character_id))
        historical = self.h.repository.lookup_scene_attributes(
            self.h.character_id, subject.scene_subject_id,
            historical_at_us=subject.introduced_at_us,
        )
        self.assertTrue(any(row.subject_key.endswith(".kind") for row in historical))

    def test_non_color_modifier_is_part_of_kind_not_color(self):
        self.h.turn("I put on my favorite hoodie.")
        current = self.scene()[0]
        self.assertEqual("favorite hoodie", current["kind"])
        self.assertNotIn("color", current)

    def test_two_subject_pronoun_is_ambiguous_and_abstains(self):
        self.h.turn("I'm holding my controller.")
        self.h.turn("I'm holding my phone.")
        result, _ = self.h.turn("It got wet.")
        self.assertEqual("ignored", result["state"])
        self.assertTrue(all("wet" not in item for item in self.scene()))

    def test_user_wake_before_rp_never_resurrects_sleep(self):
        self.h.turn("I'm going to bed.")
        self.h.turn("Good morning.")
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id, "user", "activity").state)

    def test_good_night_is_user_sleep_and_good_morning_closes_only_user_sleep(self):
        self.h.turn("Good night.")
        self.assertEqual("sleeping", self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity",
        ).state.value)
        self.assertIsNone(self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state)
        self.h.turn("Good morning.")
        self.assertIsNone(self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity",
        ).state)
        self.h.turn("Let's roleplay that we're in Silvervale.")
        self.h.turn("Back to real life.")
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id, "user", "activity").state)

    def test_companion_sleep_policy_selects_bounded_modes_and_wake_is_actor_scoped(self):
        self.h.turn("Go to sleep.")
        decision = classify_interaction_policy(self.h.repository, self.h.character_id, "Tell me a joke.")
        self.assertEqual(("asleep", "asleep_quiet", False),
                         (decision.policy_class, decision.response_mode, decision.allow_proactive))
        touch = classify_interaction_policy(
            self.h.repository, self.h.character_id, "*I stroke your hair*",
        )
        self.assertEqual("sleepy_stimulus", touch.response_mode)
        blanket = classify_interaction_policy(
            self.h.repository, self.h.character_id, "I put a blanket on you.",
        )
        self.assertEqual("sleepy_stimulus", blanket.response_mode)
        unrelated_put = classify_interaction_policy(
            self.h.repository, self.h.character_id, "I put my controller on the desk.",
        )
        self.assertEqual("asleep_quiet", unrelated_put.response_mode)
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id, "user", "activity").state)
        self.h.turn("Wake up.")
        self.assertIsNone(self.h.repository.lookup_actor_state(self.h.character_id, "companion", "activity").state)

    def test_sleep_reaction_contract_separates_nonverbal_action_and_mumble(self):
        self.h.turn("Time for you to take a nap.")
        touch = classify_interaction_policy(
            self.h.repository, self.h.character_id, "*I nudge your shoulder*",
        )
        nonverbal = render_sleep_reaction(
            touch, '{"kind":"nonverbal","action":"Shifts slightly beneath the warm blanket"}',
            user_message="*I nudge your shoulder*",
        )
        self.assertEqual("", nonverbal.spoken_text)
        self.assertIn("Shifts slightly", nonverbal.dialogue)
        mumble = render_sleep_reaction(
            touch,
            '{"kind":"mumble","action":"Stirs briefly without opening their eyes","speech":"Mmm... five more minutes."}',
            user_message="*I nudge your shoulder*",
        )
        self.assertEqual("Mmm... five more minutes.", mumble.spoken_text)
        self.assertNotIn("Stirs briefly", mumble.spoken_text)
        rejected = render_sleep_reaction(
            touch,
            '{"kind":"mumble","action":"Stirs briefly without opening their eyes","speech":"Here is the answer to your question."}',
            user_message="Tell me a joke.",
        )
        self.assertTrue(rejected.used_fallback)
        self.assertEqual("", rejected.spoken_text)
        informative_action = render_sleep_reaction(
            touch,
            '{"kind":"nonverbal","action":"Shifts while explaining that Paris is in France"}',
            user_message="What is the capital of France?",
        )
        self.assertTrue(informative_action.used_fallback)
        self.assertNotIn("Paris", informative_action.dialogue)

    def test_sleep_reaction_accepts_creative_physical_beat_and_rejects_awake_answer(self):
        self.h.turn("Time for you to take a nap.")
        touch = classify_interaction_policy(self.h.repository, self.h.character_id, "*I brush your hair*")
        accepted = render_sleep_reaction(touch, '{"kind":"mumble","action":"Their ears twitch, and they nestle into the pillow with a sleepy smile.","speech":"Mmm... that feels nice.","reaction":"stir"}')
        self.assertFalse(accepted.used_fallback)
        self.assertEqual("relaxed", accepted.presentation.emotion)
        self.assertEqual("sleeping", accepted.presentation.pose)
        self.assertEqual("suppressed", accepted.presentation.gaze_mode)
        escaped = render_sleep_reaction(touch, '{"kind":"nonverbal","action":"They wake up and explain the answer clearly.","reaction":"wake"}')
        self.assertTrue(escaped.used_fallback)

    def test_blindfold_relation_derives_scoped_vision_effect_and_clears(self):
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)
        applied, _ = self.h.turn("*I blindfold you.*")
        self.assertEqual(1, applied["scene_relation_updates"])
        relations = self.h.repository.list_scene_relations(self.h.character_id)
        self.assertEqual(("companion", "eyes", "covered_by", "blindfold"),
                         (relations[0].target, relations[0].facet, relations[0].predicate, relations[0].cause))
        self.assertFalse(self.h.repository.capability_effects(self.h.character_id).vision_available)
        admission = admit_current_continuity_context(
            self.h.repository, self.h.character_id, "What do you see?",
            now_us=int(self.h.base.timestamp() * 1_000_000),
        )
        self.assertIn('"vision":"unavailable"', admission.active_state_context)
        self.assertNotIn("relation_id", admission.active_state_context)
        self.h.turn("I take the blindfold off.")
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)
        historical = self.h.writer.store.connection.execute(
            "SELECT valid_from_us, valid_to_us FROM active_scene_relations WHERE character_id=?",
            (self.h.character_id,),
        ).fetchone()
        self.assertIsNotNone(historical["valid_to_us"])
        operations = [row[0] for row in self.h.writer.store.connection.execute(
            "SELECT operation FROM active_scene_relation_events WHERE character_id=? ORDER BY relation_event_id",
            (self.h.character_id,),
        )]
        self.assertEqual(["set", "clear"], operations)

    def test_user_hands_cover_relation_survives_restart_and_scope_isolates(self):
        self.h.turn("*I cover your eyes with my hands.*")
        self.assertEqual("user hands", self.h.repository.list_scene_relations(self.h.character_id)[0].cause)
        reopened = MemoryV2Store(str(self.h.writer.store.path))
        try:
            repo = MemoryV2Repository(reopened)
            self.assertFalse(repo.capability_effects(self.h.character_id).vision_available)
        finally:
            reopened.close()
        self.h.turn("Let's roleplay in the library.")
        self.assertTrue(self.h.repository.capability_effects(self.h.character_id).vision_available)
        self.h.turn("We're back in the real world.")
        self.assertFalse(self.h.repository.capability_effects(self.h.character_id).vision_available)

    def test_companion_generic_completion_is_actor_aware_and_questions_abstain(self):
        self.h.turn("Go cook dinner.")
        self.assertEqual("cooking dinner", self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state.value)
        admission = admit_current_continuity_context(
            self.h.repository, self.h.character_id, "What are you doing?",
            now_us=int(self.h.base.timestamp() * 1_000_000),
        )
        self.assertIn('"companion":{"family":"cooking","activity":"cooking dinner"',
                      admission.active_state_context)
        result, _ = self.h.turn("Are you done cooking?")
        self.assertEqual("ignored", result["state"])
        result, _ = self.h.turn("You finished cooking?")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("cooking dinner", self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state.value)
        result, _ = self.h.turn("you finished cooking")
        self.assertEqual("applied", result["state"])
        self.assertIsNone(self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state)

        self.h.turn("Go make dinner.")
        result, _ = self.h.turn("go finish making dinner")
        self.assertEqual("applied", result["state"])
        self.assertIsNone(self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state)

        self.h.turn("Go cook dinner.")
        result, _ = self.h.turn("stop reading")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("cooking dinner", self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state.value)

    def test_observed_hoodie_takeoff_variants_and_combined_condition(self):
        self.h.turn("I put on my white hoodie.")
        self.h.turn("It's still stained with coffee and wet")
        self.assertEqual(
            {"color": "white", "kind": "hoodie", "region": "torso", "stain": "coffee", "wet": "true", "worn_by": "user"},
            self.scene()[0],
        )
        self.h.turn("I took it off because it was wet.")
        self.assertNotIn("worn_by", self.scene()[0])

        self.h.turn("I put on my white hoodie.")
        self.h.turn("I take off the hoodie that I'm wearing.")
        self.assertNotIn("worn_by", self.scene()[0])

        self.h.turn("I put on my white hoodie.")
        self.h.turn("I'm not wearing anything.")
        current = self.scene()[0]
        self.assertNotIn("worn_by", current)
        self.assertEqual(("white", "coffee", "true"), (
            current["color"], current["stain"], current["wet"],
        ))

    def test_not_wearing_anything_requires_exactly_one_worn_candidate(self):
        result, _ = self.h.turn("I'm not wearing anything.")
        self.assertEqual("ignored", result["state"])
        self.h.turn("I put on my hoodie.")
        self.h.turn("I put on my coat.")
        result, _ = self.h.turn("I'm not wearing anything.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual(2, sum(item.get("worn_by") == "user" for item in self.scene()))

    def test_scene_admission_is_relevant_bounded_and_contains_no_internal_ids(self):
        self.h.turn("I put on my white hoodie.")
        self.h.turn("I spilled coffee on it.")
        admission = admit_current_continuity_context(
            self.h.repository, self.h.character_id, "What color is my hoodie?",
            now_us=int((self.h.base + timedelta(hours=1)).timestamp() * 1_000_000),
        )
        self.assertIn('"kind":"hoodie"', admission.active_state_context)
        self.assertIn('"stain":"coffee"', admission.active_state_context)
        self.assertNotIn("scene_subject_id", admission.active_state_context)
        self.assertLessEqual(len(admission.active_state_context), 820)
        unrelated = admit_current_continuity_context(
            self.h.repository, self.h.character_id, "Tell me about astronomy.",
            now_us=int((self.h.base + timedelta(hours=1)).timestamp() * 1_000_000),
        )
        self.assertIsNone(unrelated.active_state_context)


class DurableFactMatrixTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()

    def tearDown(self):
        self.h.close()

    def test_closed_schema_positive_negative_matrix(self):
        positives = (
            "I live in Toronto.", "My university is University of Toronto.",
            "My GPU is an RTX 3070.", "My computer is a Framework laptop.",
            "My long-running project is AIFren.",
            "My favorite game is Noita.", "I like coffee.", "I hate olives.",
            "My hobby is drawing.", "I own a soldering station.",
        )
        for text in positives:
            with self.subTest(kind="positive", text=text):
                self.assertIsNotNone(extract_durable_fact_proposal(text))
            for prefix in ("What if ", "Yesterday ", "She said, "):
                with self.subTest(kind="negative", text=prefix + text):
                    self.assertIsNone(extract_durable_fact_proposal(prefix + text))
        for temporary in (
            "I'm working on AIFren.", "I often shower.", "My dog is tired.",
            "I live in Toronto for now.", "My dog is named Miso.",
        ):
            with self.subTest(kind="temporary", text=temporary):
                self.assertIsNone(extract_durable_fact_proposal(temporary))

    def test_corrections_supersede_without_deleting_history(self):
        self.h.turn("I live in Toronto.")
        old = self.h.repository.lookup_durable_core(self.h.character_id, "home.primary").candidates[0]
        self.h.turn("I live in Montreal.")
        current = self.h.repository.lookup_durable_core(self.h.character_id, "home.primary").candidates[0]
        self.assertEqual("The user lives in Montreal.", current.content)
        historical = self.h.repository.lookup_durable_core(
            self.h.character_id, "home.primary", historical_at_us=old.valid_from_us,
        ).candidates
        self.assertEqual("The user lives in Toronto.", historical[0].content)
        status = self.h.writer.store.connection.execute(
            """SELECT status FROM claim_status_events WHERE character_id=? AND claim_id=?
                 ORDER BY status_event_id DESC LIMIT 1""",
            (self.h.character_id, old.claim_id),
        ).fetchone()[0]
        self.assertEqual("superseded", status)

        self.h.turn("I like coffee.")
        self.h.turn("Actually I don't really like coffee anymore.")
        preference = next(item for item in self.h.repository.list_current_durable_core(self.h.character_id)
                          if item.subject_key.startswith("preference.topic."))
        self.assertEqual("The user dislikes coffee.", preference.content)

    def test_reassertion_reconfirms_without_resetting_durable_validity(self):
        self.h.turn("I live in Toronto.")
        first = self.h.repository.lookup_durable_core(
            self.h.character_id, "home.primary",
        ).candidates[0]
        self.h.turn("I live in Toronto.")
        current = self.h.repository.lookup_durable_core(
            self.h.character_id, "home.primary",
        ).candidates[0]
        self.assertEqual(first.claim_id, current.claim_id)
        self.assertEqual(first.valid_from_us, current.valid_from_us)
        roles = self.h.writer.store.connection.execute(
            "SELECT evidence_role FROM claim_evidence WHERE character_id=? AND claim_id=?",
            (self.h.character_id, current.claim_id),
        ).fetchall()
        self.assertEqual(
            {"direct_user_statement", "user_confirmation"},
            {row[0] for row in roles},
        )

    def test_reconfirmation_rejects_assistant_evidence_and_superseded_claims(self):
        self.h.turn("I live in Toronto.")
        old = self.h.repository.lookup_durable_core(
            self.h.character_id, "home.primary",
        ).candidates[0]
        next_sequence = self.h.writer.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
            (self.h.character_id,),
        ).fetchone()[0]
        assistant_event = "synthetic-assistant-evidence"
        self.h.writer.store.add_event(
            self.h.character_id, assistant_event, next_sequence,
            actor_kind="assistant", recorded_at_us=old.valid_from_us + 1,
            content_text="I say the user lives in Toronto.",
        )
        with self.assertRaises(ValueError):
            self.h.writer.store.reconfirm_durable_claim(
                self.h.character_id, old.claim_id,
                evidence_event_id=assistant_event,
                evidence_excerpt_start_cp=0,
                evidence_excerpt_end_cp=1,
            )

        self.h.turn("I live in Montreal.")
        newest_user_event = self.h.writer.store.connection.execute(
            """SELECT event_id, content_text FROM events
                 WHERE character_id=? AND actor_kind='user' ORDER BY sequence DESC LIMIT 1""",
            (self.h.character_id,),
        ).fetchone()
        with self.assertRaises(ValueError):
            self.h.writer.store.reconfirm_durable_claim(
                self.h.character_id, old.claim_id,
                evidence_event_id=newest_user_event["event_id"],
                evidence_excerpt_start_cp=0,
                evidence_excerpt_end_cp=len(newest_user_event["content_text"]),
            )

    def test_past_and_scenario_biography_never_become_current(self):
        for text in ("I used to live in Toronto.", "I liked Noita when I was younger."):
            _, durable = self.h.turn(text)
            self.assertEqual("ignored", durable["state"])
        self.h.turn("I live in Toronto.")
        self.h.turn("Let's roleplay that we're in Silvervale.")
        _, durable = self.h.turn("I live in Silvervale.")
        self.assertEqual("truth_scope_not_real_world", durable["reason"])
        self.h.turn("Back to real life.")
        current = self.h.repository.lookup_durable_core(self.h.character_id, "home.primary").candidates
        self.assertEqual("The user lives in Toronto.", current[0].content)

    def test_relevant_admission_has_no_internal_hash_and_withholds_stale_correction(self):
        self.h.turn("I like coffee.")
        admission = admit_durable_context(self.h.repository, self.h.character_id, "Do I like coffee?")
        self.assertIn("likes: coffee", admission.context_block)
        self.assertNotIn("preference.topic.", admission.context_block)
        stale = admit_durable_context(
            self.h.repository, self.h.character_id,
            "Actually I don't really like coffee anymore.",
        )
        self.assertIsNone(stale.context_block)

    def test_v1_dedup_suppresses_only_the_admitted_governed_slot(self):
        self.h.turn("I live in Montreal.")
        admission = admit_durable_context(self.h.repository, self.h.character_id, "Where do I live?")
        memories = [
            {"category": "profile", "content": "The user lives in Toronto."},
            {"category": "profile", "content": "The user likes autumn."},
            {"category": "event", "content": "The user enjoys home cooking."},
        ]
        retained = filter_v1_duplicates(memories, admission.facts)
        self.assertEqual(memories[1:], retained)

    def test_favorite_color_correction_is_singleton_historical_and_narrowly_deduplicated(self):
        self.h.turn("My favorite color is green.")
        old = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates[0]
        self.h.turn("My favorite is purple actually.")
        current = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates
        self.assertEqual(1, len(current))
        self.assertEqual("The user's favorite color is purple.", current[0].content)
        historical = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color", historical_at_us=old.valid_from_us,
        ).candidates
        self.assertEqual("The user's favorite color is green.", historical[0].content)
        status = self.h.writer.store.connection.execute(
            """SELECT status FROM claim_status_events WHERE character_id=? AND claim_id=?
                 ORDER BY status_event_id DESC LIMIT 1""",
            (self.h.character_id, old.claim_id),
        ).fetchone()[0]
        self.assertEqual("superseded", status)
        original_current_id = current[0].claim_id
        original_current_start = current[0].valid_from_us
        self.h.turn("My favorite color is purple.")
        reconfirmed = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates[0]
        self.assertEqual(original_current_id, reconfirmed.claim_id)
        self.assertEqual(original_current_start, reconfirmed.valid_from_us)

        admission = admit_durable_context(
            self.h.repository, self.h.character_id, "What's my favorite color?",
        )
        self.assertEqual(("preference.color",), tuple(fact.subject_key for fact in admission.facts))
        self.assertIn("purple", admission.context_block)
        memories = [
            {"category": "preference", "content": "The user's absolute favorite color is green."},
            {"category": "preference", "content": "The user's favorite color is purple."},
            {"category": "preference", "content": "The user likes green."},
            {"category": "preference", "content": "The user likes blue clothes."},
        ]
        self.assertEqual(memories[2:], filter_v1_duplicates(memories, admission.facts))
        self.h.turn("I like green.")
        self.assertTrue(any(
            item.content == "The user likes green."
            for item in self.h.repository.list_current_durable_core(self.h.character_id)
        ))
        self.assertEqual("The user's favorite color is purple.", self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates[0].content)

        for unsafe in (
            "What if my favorite color is red?", 'She said, "My favorite color is red."',
            "My favorite color was red when I was younger.",
        ):
            with self.subTest(kind="favorite_abstain", text=unsafe):
                self.assertIsNone(extract_durable_fact_proposal(unsafe))

        self.h.turn("Let's roleplay that we're in Silvervale.")
        _, durable = self.h.turn("My favorite color is red.")
        self.assertEqual("truth_scope_not_real_world", durable["reason"])
        self.h.turn("We're back in the real world.")
        self.assertEqual("The user's favorite color is purple.", self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates[0].content)

    def test_contextual_favorite_color_correction_requires_a_governed_predecessor(self):
        _, durable = self.h.turn("My favorite is purple actually.")
        self.assertEqual("no_clear_durable_fact", durable["reason"])
        self.assertEqual((), self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates)

    def test_explicit_favorite_color_bootstraps_one_governed_correction(self):
        created = (self.h.base - timedelta(days=1)).isoformat()
        memories = [
            {
                "id": 1, "category": "preference",
                "content": "The user's absolute favorite color is green.",
                "importance": 8, "created": created,
            },
            {
                "id": 2, "category": "preference",
                "content": "The user likes green.",
                "importance": 5, "created": created,
            },
        ]
        self.h.memory_file.write_text(json.dumps(memories), encoding="utf-8")
        self.assertEqual("ok", self.h.writer.reconcile()["state"])

        same_turn = admit_durable_context(
            self.h.repository, self.h.character_id, "Actually, my favorite color is purple.",
        )
        self.assertEqual((), same_turn.facts)
        self.assertEqual(
            [memories[1]],
            filter_v1_duplicates(memories, same_turn.v1_suppression_facts),
        )

        _, durable = self.h.turn("Actually, my favorite color is purple.")
        self.assertEqual("superseded", durable["state"])
        self.assertTrue(durable["v1_favorite_bridge"])
        current = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates
        self.assertEqual(1, len(current))
        self.assertEqual("The user's favorite color is purple.", current[0].content)
        historical = self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
            historical_at_us=int((self.h.base - timedelta(hours=12)).timestamp() * 1_000_000),
        ).candidates
        self.assertEqual(1, len(historical))
        self.assertEqual("The user's favorite color is green.", historical[0].content)

        admission = admit_durable_context(
            self.h.repository, self.h.character_id, "What's my favorite color?",
        )
        retained = filter_v1_duplicates(memories, admission.facts)
        self.assertEqual([memories[1]], retained)

        database = self.h.writer.database_path
        self.h.writer.close()
        self.h.writer = MemoryV2ShadowWriter(
            self.h.root, character_id=self.h.character_id, display_name="Synthetic",
            memory_file=self.h.memory_file, database_path=database,
        )
        self.h.repository = MemoryV2Repository(self.h.writer.store)
        self.assertEqual("The user's favorite color is purple.", self.h.repository.lookup_durable_core(
            self.h.character_id, "preference.color",
        ).candidates[0].content)

    def test_gpu_thread_activity_and_durable_lifecycles_remain_distinct(self):
        self.h.turn("My GPU is an RTX 3070.")
        self.h.turn("I'm waiting for my RTX 5070 to arrive.")
        self.h.turn("I'm playing Noita.")
        self.h.turn("It arrived.")
        self.assertEqual((), self.h.repository.list_open_threads(self.h.character_id).threads)
        self.assertEqual("playing Noita", self.h.repository.lookup_actor_state(
            self.h.character_id, "user", "activity",
        ).state.value)
        self.assertEqual("The user's GPU is RTX 3070.", self.h.repository.lookup_durable_core(
            self.h.character_id, "device.gpu",
        ).candidates[0].content)
        self.h.turn("My GPU is an RTX 5070.")
        self.assertEqual("The user's GPU is RTX 5070.", self.h.repository.lookup_durable_core(
            self.h.character_id, "device.gpu",
        ).candidates[0].content)

    def test_restart_preserves_durable_companion_and_scene_state(self):
        self.h.turn("I live in Toronto.")
        self.h.turn("Go to sleep.")
        self.h.turn("I'm holding my controller.")
        database = self.h.writer.database_path
        self.h.writer.close()
        self.h.writer = MemoryV2ShadowWriter(
            self.h.root, character_id=self.h.character_id, display_name="Synthetic",
            memory_file=self.h.memory_file, database_path=database,
        )
        self.h.repository = MemoryV2Repository(self.h.writer.store)
        self.assertEqual("The user lives in Toronto.", self.h.repository.lookup_durable_core(
            self.h.character_id, "home.primary",
        ).candidates[0].content)
        self.assertEqual("sleeping", self.h.repository.lookup_actor_state(
            self.h.character_id, "companion", "activity",
        ).state.value)
        self.assertEqual(1, len(self.h.repository.list_scene_subjects(self.h.character_id)))


class RpDiscourseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()

    def tearDown(self):
        self.h.close()

    def test_old_enactment_cue_restart_and_back_home_cannot_create_a_scenario(self):
        first, _ = self.h.turn("Let's roleplay.")
        self.assertEqual("ignored", first["state"])
        self.h.base += timedelta(hours=2)
        database = self.h.writer.database_path
        self.h.writer.close()
        self.h.writer = MemoryV2ShadowWriter(
            self.h.root, character_id=self.h.character_id, display_name="Synthetic",
            memory_file=self.h.memory_file, database_path=database,
        )
        self.h.repository = MemoryV2Repository(self.h.writer.store)
        result, _ = self.h.turn("Back home.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("real_world", self.h.repository.active_truth_scope(self.h.character_id).kind)

    def test_recent_multiturn_rp_still_completes_and_declarative_reality_exits(self):
        self.h.turn("Let's roleplay.")
        result, _ = self.h.turn("Silvervale.")
        self.assertEqual("applied", result["state"])
        self.assertEqual("scenario", self.h.repository.active_truth_scope(self.h.character_id).kind)
        result, _ = self.h.turn("we are in real life")
        self.assertEqual("applied", result["state"])
        self.assertEqual("real_world", self.h.repository.active_truth_scope(self.h.character_id).kind)

    def test_reality_words_are_not_scenario_labels_outside_a_scenario(self):
        for text in ("Home.", "Real life.", "Real world.", "Reality.", "Back home."):
            with self.subTest(text=text):
                result, _ = self.h.turn(text)
                self.assertEqual("ignored", result["state"])
                self.assertEqual("real_world", self.h.repository.active_truth_scope(self.h.character_id).kind)


class ProactiveMatrixTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()
        self.h.turn("I'm waiting for my GPU to arrive.")
        self.latest_user_us = int(self.h.base.timestamp() * 1_000_000)

    def tearDown(self):
        self.h.close()

    def eligibility(self, hours, enabled=True):
        return evaluate_proactive_eligibility(
            self.h.repository, self.h.character_id, self.h.rows,
            now_us=self.latest_user_us + int(hours * 3_600_000_000), enabled=enabled,
        )

    def test_eligibility_too_soon_disabled_zero_reason_and_resolved(self):
        self.assertEqual("too_soon", self.eligibility(.5).outcome)
        self.assertEqual("disabled", self.eligibility(8, enabled=False).outcome)
        eligible = self.eligibility(8)
        self.assertTrue(eligible.eligible)
        self.assertEqual("waiting for my GPU to arrive", eligible.reason.topic)
        self.h.turn("It arrived.")
        self.assertEqual("no_eligible_reason", self.eligibility(10).outcome)

    def test_sleep_and_busy_activity_suppress_even_when_thread_is_ready(self):
        self.h.turn("I'm going to bed.")
        self.assertEqual("user_sleeping", self.eligibility(10).outcome)
        self.h.turn("Good morning.")
        self.h.turn("Go to sleep.")
        self.assertEqual("companion_sleeping", self.eligibility(12).outcome)

    def test_ignored_checkin_backs_off_and_user_response_releases_it(self):
        eligible = self.eligibility(8)
        shown = self.latest_user_us + 8 * 3_600_000_000
        record_displayed_checkin(
            self.h.writer.store, self.h.character_id, eligible.reason,
            displayed_at_us=shown, conversation_index=2, assistant_content="Did it arrive?",
        )
        backed_off = evaluate_proactive_eligibility(
            self.h.repository, self.h.character_id, self.h.rows,
            now_us=shown + 2 * 3_600_000_000, enabled=True,
        )
        self.assertEqual("backed_off_prior_checkin_unanswered", backed_off.outcome)
        self.assertEqual(1, backed_off.ignored_streak)
        self.assertEqual(1, mark_checkins_responded(
            self.h.writer.store, self.h.character_id,
            responded_at_us=shown + 3 * 3_600_000_000,
        ))
        returned = evaluate_proactive_eligibility(
            self.h.repository, self.h.character_id, self.h.rows,
            now_us=shown + 4 * 3_600_000_000, enabled=True,
        )
        self.assertEqual(0, returned.ignored_streak)

    def test_thirty_second_interval_is_only_a_gate_and_afk_backoff_resets(self):
        too_soon = evaluate_proactive_eligibility(
            self.h.repository, self.h.character_id, self.h.rows,
            now_us=self.latest_user_us + 29_000_000, enabled=True, minimum_interval_us=30_000_000,
        )
        self.assertEqual("too_soon", too_soon.outcome)
        # The waiting thread itself is not ready merely because 30 seconds passed.
        no_reason = evaluate_proactive_eligibility(
            self.h.repository, self.h.character_id, self.h.rows,
            now_us=self.latest_user_us + 31_000_000, enabled=True, minimum_interval_us=30_000_000,
        )
        self.assertEqual("no_eligible_reason", no_reason.outcome)


class _Memory:
    def __init__(self):
        self.memories = []
        self.llm = None

    def get_relevant_memories(self, _query, max_memories=5):
        return []

    def process(self, _user, _assistant):
        return None


class _Llm:
    is_available = True

    def __init__(self):
        self.calls = 0
        self.last_context = None

    def generate(self, context, prompt):
        self.calls += 1
        self.last_context = context
        return "Did your GPU end up arriving?"


class _ProactiveDraftLlm(_Llm):
    def __init__(self, draft=None, error=None):
        super().__init__()
        self.draft = draft
        self.error = error

    def generate(self, context, prompt):
        self.calls += 1
        self.last_context = context
        if self.error is not None:
            raise self.error
        return self.draft


class _Tts:
    synthesis_strategy = "whole_response"

    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)
        return True

    def stop(self):
        return 0

    def set_volume(self, _value):
        pass


class ServiceBehaviorTests(unittest.TestCase):
    def test_vision_obstruction_blocks_streaming_visual_claim_and_repairs_before_publish(self):
        h = _Harness()
        try:
            h.turn("*I blindfold you.*")
            conversation = Conversation(object(), conversation_file=h.conversation_file,
                                        summary_file=h.root / "summary.json", clock=lambda: h.base)
            memory, llm, tts = _Memory(), _Llm(), _Tts(); memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            service = AssistantService(
                llm, memory, conversation, object(), {"_character_id": h.character_id},
                "character", tts, response_generator=lambda *_: "I can see your red shirt clearly.",
                memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                memory_authority="v1",
            )
            result = service.process_text_turn("What do you see?", speak=False)
            self.assertTrue(result.succeeded)
            self.assertNotIn("I can see", result.reply)
            self.assertIn("can't see", result.reply.casefold())
        finally:
            h.close()

    def test_sleep_uses_bounded_reactions_and_wake_restores_normal_model(self):
        h = _Harness()
        try:
            conversation = Conversation(object(), conversation_file=h.conversation_file,
                                        summary_file=h.root / "summary.json", clock=lambda: h.base)
            memory, llm, tts = _Memory(), _Llm(), _Tts()
            memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            service = AssistantService(
                llm, memory, conversation, object(), {"_character_id": h.character_id},
                "character", tts, response_generator=lambda *_: "ordinary chatter",
                memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                memory_authority="v1",
            )
            presentations = []
            service.subscribe(lambda event: presentations.append(event.data.get("presentation"))
                              if event.type == "assistant_response" else None)
            sleep_start = service.process_text_turn("Time for you to take a nap.", speak=False)
            self.assertNotEqual("ordinary chatter", sleep_start.reply)
            self.assertEqual("sleeping", h.repository.lookup_actor_state(
                h.character_id, "companion", "activity",
            ).state.value)
            self.assertEqual("sleeping", presentations[-1]["pose"])
            self.assertEqual("suppressed", presentations[-1]["gaze_mode"])
            touch = service.process_text_turn("*I stroke your hair*", speak=True)
            self.assertNotEqual("ordinary chatter", touch.reply)
            self.assertEqual([], tts.spoken, "A typed nonverbal reaction must not enter TTS.")
            self.assertEqual("nonverbal", presentations[-1]["speech_mode"])
            self.assertEqual("sleeping", h.repository.lookup_actor_state(
                h.character_id, "companion", "activity",
            ).state.value)
            asleep = service.process_text_turn("Tell me a joke.", speak=False)
            self.assertNotIn("joke", asleep.reply.casefold())
            self.assertNotEqual(touch.reply, asleep.reply)
            repeated = service.process_text_turn("Tell me a joke.", speak=False)
            self.assertNotEqual(asleep.reply, repeated.reply)
            self.assertEqual("sleeping", h.repository.lookup_actor_state(
                h.character_id, "companion", "activity",
            ).state.value)
            wake = service.process_text_turn("Wake up.", speak=True)
            self.assertIn("awake", wake.reply.casefold())
            self.assertEqual(["I'm awake."], tts.spoken)
            self.assertIsNone(h.repository.lookup_actor_state(
                h.character_id, "companion", "activity",
            ).state)
            self.assertEqual("awake", presentations[-1]["pose"])
            self.assertEqual("normal", presentations[-1]["gaze_mode"])
            self.assertEqual("ordinary chatter", service.process_text_turn("Hello again.", speak=False).reply)
        finally:
            h.close()

    def test_valid_sleep_mumble_voices_only_the_typed_speech(self):
        h = _Harness()
        try:
            h.turn("Go to sleep.")
            conversation = Conversation(
                object(), conversation_file=h.conversation_file,
                summary_file=h.root / "summary.json", clock=lambda: h.base,
            )
            memory, llm, tts = _Memory(), _Llm(), _Tts()
            memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            proposal = (
                '{"kind":"mumble","action":"Stirs briefly without opening their eyes",'
                '"speech":"Mmm... five more minutes."}'
            )
            service = AssistantService(
                llm, memory, conversation, object(), {"_character_id": h.character_id},
                "character", tts, response_generator=lambda *_: proposal,
                memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                memory_authority="v1",
            )

            result = service.process_text_turn("*I nudge your shoulder*", speak=True)

            self.assertIn("Stirs briefly", result.reply)
            self.assertEqual("Mmm... five more minutes.", result.spoken_text)
            self.assertEqual(["Mmm... five more minutes."], tts.spoken)
            self.assertEqual("sleeping", h.repository.lookup_actor_state(
                h.character_id, "companion", "activity",
            ).state.value)
        finally:
            h.close()

    def test_proactive_generation_uses_one_reason_and_persists_displayed_assistant(self):
        h = _Harness()
        try:
            h.turn("I'm waiting for my GPU to arrive.")
            now = h.base + timedelta(hours=8)
            conversation = Conversation(
                object(), conversation_file=h.conversation_file,
                summary_file=h.root / "summary.json", clock=lambda: now,
            )
            memory, llm, tts = _Memory(), _Llm(), _Tts()
            memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            service = AssistantService(
                llm, memory, conversation, object(), {"_character_id": h.character_id},
                "character", tts, memory_v2_shadow_writer=h.writer,
                character_id=h.character_id,
                memory_authority="v1",
            )
            with patch("model_settings.proactive_behavior_status", return_value={"enabled": True}):
                result = service.process_proactive_checkin(
                    now_us=int(now.timestamp() * 1_000_000), speak=False,
                )
            self.assertTrue(result.succeeded, result.error)
            self.assertEqual("Did your GPU end up arriving?", result.reply)
            self.assertEqual("assistant", conversation.messages[-1]["role"])
            self.assertEqual(1, h.writer.store.connection.execute(
                "SELECT COUNT(*) FROM proactive_checkins WHERE character_id=?",
                (h.character_id,),
            ).fetchone()[0])
            reason_blocks = [item["content"] for item in llm.last_context
                             if item["content"].startswith("[Eligible proactive follow-up")]
            self.assertEqual(1, len(reason_blocks))
            self.assertIn("waiting for my GPU", reason_blocks[0])
            self.assertNotIn("thread-", reason_blocks[0])
        finally:
            h.close()

    def test_proactive_generation_is_private_until_publishable(self):
        cases = (
            ("", None, "empty_output"),
            ("x" * 321, None, "overlength_output"),
            (None, RuntimeError("synthetic provider failure"), "provider_error"),
            (None, TimeoutError("synthetic timeout"), "provider_timeout"),
        )
        for draft, error, expected in cases:
            with self.subTest(expected=expected):
                h = _Harness()
                try:
                    h.turn("I'm waiting for my GPU to arrive.")
                    now = h.base + timedelta(hours=8)
                    conversation = Conversation(
                        object(), conversation_file=h.conversation_file,
                        summary_file=h.root / "summary.json", clock=lambda: now,
                    )
                    memory, tts = _Memory(), _Tts()
                    llm = _ProactiveDraftLlm(draft=draft, error=error)
                    memory.llm = llm
                    h.writer.compare = lambda *_a, **_k: {}
                    service = AssistantService(
                        llm, memory, conversation, object(),
                        {"_character_id": h.character_id}, "character", tts,
                        memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                        memory_authority="v1",
                    )
                    events = []
                    service.subscribe(events.append)
                    with patch("model_settings.proactive_behavior_status", return_value={
                        "enabled": True, "interval_seconds": 30,
                    }):
                        result = service.process_proactive_checkin(
                            now_us=int(now.timestamp() * 1_000_000), speak=False,
                        )
                    self.assertFalse(result.succeeded)
                    self.assertFalse(any(event.type == "turn_started" for event in events))
                    self.assertFalse(any(event.type == "status" for event in events))
                    self.assertEqual(1, h.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM proactive_attempts WHERE character_id=? AND outcome=?",
                        (h.character_id, expected),
                    ).fetchone()[0])
                    self.assertEqual(0, h.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM proactive_checkins WHERE character_id=?",
                        (h.character_id,),
                    ).fetchone()[0])
                finally:
                    h.close()

    def test_failed_proactive_attempt_throttles_restart_without_afk_penalty(self):
        h = _Harness()
        try:
            h.turn("I'm waiting for my GPU to arrive.")
            now = h.base + timedelta(hours=8)

            def make_service(at):
                conversation = Conversation(
                    object(), conversation_file=h.conversation_file,
                    summary_file=h.root / "summary.json", clock=lambda: at,
                )
                memory, llm, tts = _Memory(), _ProactiveDraftLlm(draft=""), _Tts()
                memory.llm = llm
                return AssistantService(
                    llm, memory, conversation, object(),
                    {"_character_id": h.character_id}, "character", tts,
                    memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                    memory_authority="v1",
                )

            with patch("model_settings.proactive_behavior_status", return_value={
                "enabled": True, "interval_seconds": 30,
            }):
                first = make_service(now)
                self.assertFalse(first.process_proactive_checkin(
                    now_us=int(now.timestamp() * 1_000_000), speak=False,
                ).succeeded)
                restarted = make_service(now + timedelta(seconds=10))
                result = restarted.process_proactive_checkin(
                    now_us=int((now + timedelta(seconds=10)).timestamp() * 1_000_000),
                    speak=False,
                )
                self.assertEqual("backed_off_failed_generation", result.error)
            self.assertEqual(1, h.writer.store.connection.execute(
                "SELECT COUNT(*) FROM proactive_attempts WHERE character_id=?",
                (h.character_id,),
            ).fetchone()[0])
            self.assertEqual(0, h.writer.store.connection.execute(
                "SELECT COUNT(*) FROM proactive_checkins WHERE character_id=?",
                (h.character_id,),
            ).fetchone()[0])
        finally:
            h.close()

    def test_user_turn_arriving_during_private_proactive_generation_wins(self):
        h = _Harness()
        try:
            h.turn("I'm waiting for my GPU to arrive.")
            now = h.base + timedelta(hours=8)
            entered = threading.Event()
            release = threading.Event()
            replacement_claimed = threading.Event()

            class BlockingProactiveLlm(_Llm):
                def cancel_active_generation(self):
                    replacement_claimed.set()

                def generate(self, context, prompt):
                    self.last_context = context
                    entered.set()
                    release.wait(2)
                    return "Did it arrive?"

            conversation = Conversation(
                object(), conversation_file=h.conversation_file,
                summary_file=h.root / "summary.json", clock=lambda: now,
            )
            memory, llm, tts = _Memory(), BlockingProactiveLlm(), _Tts()
            memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            service = AssistantService(
                llm, memory, conversation, object(),
                {"_character_id": h.character_id}, "character", tts,
                response_generator=lambda *_args: "User turn response.",
                memory_v2_shadow_writer=h.writer, character_id=h.character_id,
                memory_authority="v1",
            )
            events = []
            service.subscribe(events.append)
            proactive_result = []
            user_result = []
            with patch("model_settings.proactive_behavior_status", return_value={
                "enabled": True, "interval_seconds": 30,
            }):
                proactive = threading.Thread(target=lambda: proactive_result.append(
                    service.process_proactive_checkin(
                        now_us=int(now.timestamp() * 1_000_000), speak=False,
                    )
                ))
                proactive.start()
                self.assertTrue(entered.wait(1))
                user = threading.Thread(target=lambda: user_result.append(
                    service.process_text_turn("It just arrived.", speak=False)
                ))
                user.start()
                # A private proactive attempt now owns an internal token too.
                # Observe replacement itself instead of assuming generation 1.
                self.assertTrue(replacement_claimed.wait(1))
                release.set()
                proactive.join(2)
                user.join(2)

            self.assertEqual("interrupted", proactive_result[0].error)
            self.assertTrue(user_result[0].succeeded)
            starts = [event for event in events if event.type == "turn_started"]
            self.assertEqual(1, len(starts))
            self.assertEqual("user", starts[0].data.get("generation_origin"))
            self.assertEqual(0, h.writer.store.connection.execute(
                "SELECT COUNT(*) FROM proactive_checkins WHERE character_id=?",
                (h.character_id,),
            ).fetchone()[0])
        finally:
            h.close()

    def test_ptt_style_invalidation_cannot_resume_stale_proactive_audio(self):
        h = _Harness()
        try:
            h.turn("I'm waiting for my GPU to arrive.")
            now = h.base + timedelta(hours=8)
            conversation = Conversation(
                object(), conversation_file=h.conversation_file,
                summary_file=h.root / "summary.json", clock=lambda: now,
            )
            memory, llm, tts = _Memory(), _Llm(), _Tts()
            memory.llm = llm
            h.writer.compare = lambda *_a, **_k: {}
            service = AssistantService(
                llm, memory, conversation, object(), {"_character_id": h.character_id},
                "character", tts, memory_v2_shadow_writer=h.writer,
                character_id=h.character_id,
                memory_authority="v1",
            )
            service.subscribe(lambda event: service.stop_speaking(interrupted=True)
                              if event.type == "assistant_response" else None)
            with patch("model_settings.proactive_behavior_status", return_value={"enabled": True}):
                result = service.process_proactive_checkin(
                    now_us=int(now.timestamp() * 1_000_000), speak=True,
                )
            self.assertTrue(result.succeeded, result.error)
            self.assertEqual([], tts.spoken)
        finally:
            h.close()


# Counted matrix: 144 direct activity fixtures + 216 production-pipeline
# activity sequences + 9 scene/sleep/admission sequences + 52 durable fixtures
# + 8 durable lifecycle sequences + 3 proactive sequences + 3 full
# AssistantService sequences + 1 typed-mumble service sequence = 436 fixtures,
# including 240 multi-turn sequences.
TRANCHE_MATRIX_CASES = 436
TRANCHE_MULTI_TURN_SEQUENCES = 240
