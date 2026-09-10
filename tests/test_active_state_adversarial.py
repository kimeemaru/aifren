"""Adversarial regressions for production Active State boundaries.

These cases intentionally combine otherwise-supported surfaces.  They protect
against parser ordering, projection, and composed-capability defects that clean
single-feature fixtures do not expose.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from benchmarks.active_state.harness import BASE_TIME, SyntheticSession
from benchmarks.active_state.breadth_corpus import planned_case_count
from benchmarks.active_state.adversarial import run_capability_combinations
from companion_action import validate_companion_action_decision
from memory_v2_store.store import (
    DISTINCT_DORMANT_RETIRE_AFTER_US,
    GENERIC_DORMANT_RETIRE_AFTER_US,
)
from response_requirements import derive_response_requirement, validate_response_requirement
from presentation_metadata import parse_assistant_response


class ActiveStateAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SyntheticSession(self.id())

    def tearDown(self) -> None:
        self.session.close()

    def _attributes(self) -> tuple[dict[str, str], ...]:
        rows = []
        for subject in self.session.repository.list_scene_subjects(
            self.session.character_id, limit=128,
        ):
            rows.append({
                row.subject_key.rsplit(".", 1)[-1]: row.value
                for row in self.session.repository.lookup_scene_attributes(
                    self.session.character_id, subject.scene_subject_id,
                )
            })
        return tuple(rows)

    def _requirement(self, text: str):
        return derive_response_requirement(
            self.session.repository,
            self.session.character_id,
            text,
            local_datetime=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
            companion_effects=self.session.effects(),
            user_effects=self.session.effects(target="user"),
        )

    def test_generated_torture_corpus_exceeds_one_thousand_cases(self):
        self.assertGreaterEqual(planned_case_count(), 1_000)

    def test_pairwise_and_maximal_capability_composition(self):
        report = run_capability_combinations()
        self.assertEqual(40, report.cases)
        self.assertEqual(0, report.failed, report.failure_categories)

    def test_sentence_boundary_cannot_become_wearable_object_identity(self):
        self.session.turn("You're wearing a hat.")
        self.session.turn("You're wearing the hat. Actually, take it off.")

        self.assertFalse(any(
            relation.predicate == "wearing" and relation.target == "companion"
            for relation in self.session.relations()
        ))
        self.assertNotIn("take it off", {
            attributes.get("kind") for attributes in self._attributes()
        })

        self.session.turn("You're wearing the hat, but actually take it off.")
        self.assertFalse(any(
            relation.predicate == "wearing" and relation.target == "companion"
            for relation in self.session.relations()
        ))
        self.assertNotIn("but actually take it off", {
            attributes.get("kind") for attributes in self._attributes()
        })

    def test_ordered_same_turn_attribute_correction_uses_final_value(self):
        self.session.turn("You're wearing a green hat.")
        self.session.turn("Your hat is blue. Sorry, red.")

        hats = [row for row in self._attributes() if row.get("kind") == "hat"]
        self.assertEqual(1, len(hats))
        self.assertEqual("red", hats[0].get("color"))
        self.assertEqual({"red hat"}, {
            relation.cause for relation in self.session.relations()
            if relation.predicate == "wearing"
        })
        self.session.turn("Your hat is blue, no, green.")
        self.assertEqual({"green hat"}, {
            relation.cause for relation in self.session.relations()
            if relation.predicate == "wearing"
        })

    def test_bilateral_unavailability_is_stronger_than_stale_occupancy(self):
        self.session.turn("You're holding a cup in your left hand and a book in your right hand.")
        self.session.turn("Your both arms are missing.")

        effects = self.session.effects()
        self.assertEqual("unavailable", effects.left_hand_mode)
        self.assertEqual("unavailable", effects.right_hand_mode)
        self.assertEqual("unavailable", effects.hands_mode)

    def test_qualified_identity_does_not_alias_dormant_same_kind(self):
        self.session.turn("You're wearing a blue hat.")
        self.session.turn("I replace your blue hat with a red one.")
        self.session.turn("Your red hat is green now.")

        current = [
            attributes for attributes in self._attributes()
            if attributes.get("worn_by") == "companion"
        ]
        self.assertEqual(1, len(current))
        self.assertEqual(("hat", "green"), (
            current[0].get("kind"), current[0].get("color"),
        ))
        self.session.turn("Your hat is blue. Sorry, red.")
        current = [
            attributes for attributes in self._attributes()
            if attributes.get("worn_by") == "companion"
        ]
        self.assertEqual("red", current[0].get("color"))

    def test_identity_overlap_indefinite_subjects_and_plurality_remain_distinct(self):
        self.session.turn("You're wearing a red hat and a red scarf.")
        self.session.turn("Your red scarf is wet.")
        rows = self._attributes()
        self.assertNotIn("wet", next(row for row in rows if row.get("kind") == "hat"))
        self.assertEqual(
            "true", next(row for row in rows if row.get("kind") == "scarf").get("wet"),
        )

        necklace_session = SyntheticSession(self.id() + "-necklaces")
        try:
            necklace_session.turn("A moon necklace is nearby.")
            necklace_session.turn("A necklace is nearby.")
            relations = tuple(
                relation for relation in necklace_session.relations()
                if relation.predicate == "near"
            )
            self.assertEqual({"moon necklace", "necklace"}, {
                relation.cause for relation in relations
            })
            self.assertEqual(2, len({relation.cause_subject_id for relation in relations}))
        finally:
            necklace_session.close()

        cups = SyntheticSession(self.id() + "-cups")
        try:
            cups.turn("You're holding a cup and a cup.")
            held = tuple(
                relation for relation in cups.relations()
                if relation.predicate == "holding"
            )
            self.assertEqual(2, len({relation.cause_subject_id for relation in held}))
            before = cups.structural_snapshot()
            cups.turn("Give me the cup.")
            self.assertEqual(before, cups.structural_snapshot())
        finally:
            cups.close()

        boxes = SyntheticSession(self.id() + "-boxes")
        try:
            boxes.turn("You're holding two identical boxes.")
            subject = boxes.repository.list_scene_subjects(boxes.character_id, limit=4)[0]
            attributes = {
                row.subject_key.rsplit(".", 1)[-1]: row.value
                for row in boxes.repository.lookup_scene_attributes(
                    boxes.character_id, subject.scene_subject_id,
                )
            }
            self.assertEqual("box", attributes.get("kind"))
            self.assertEqual("2", attributes.get("quantity"))
        finally:
            boxes.close()

    def test_implicit_obstruction_action_never_guesses_between_duplicate_objects(self):
        self.session.turn("You're holding a blindfold and a blindfold.")
        before = self.session.structural_snapshot()

        result = self.session.turn("I blindfold you.")

        self.assertEqual(before, self.session.structural_snapshot())
        self.assertNotEqual("applied", result.get("state"))
        self.assertEqual("available", self.session.effects().vision_mode)

    def test_companion_action_subject_resolution_rejects_wrong_ambiguous_retired_and_scoped(self):
        def decision(session: SyntheticSession, value: str):
            parsed = parse_assistant_response(json.dumps({
                "dialogue": "", "response_mode": "action_decision",
                "spoken_content": "", "presentation": None,
                "companion_action": {
                    "family": "hold", "operation": "set", "value": value,
                },
                "capability_compliance": [],
            }))
            return validate_companion_action_decision(
                parsed, session.repository, session.character_id, session.effects(),
            )

        before = self.session.structural_snapshot()
        wrong = decision(self.session, "purple orb")
        self.assertEqual("action_scene_subject_ambiguous", wrong.category)
        self.assertEqual(before, self.session.structural_snapshot())

        self.session.turn("I hand you a cup.")
        self.session.turn("Put the cup down.")
        self.session.turn("I hand you a cup.")
        self.session.turn("Put the cup down.")
        before = self.session.structural_snapshot()
        ambiguous = decision(self.session, "cup")
        self.assertEqual("action_scene_subject_ambiguous", ambiguous.category)
        self.assertEqual(before, self.session.structural_snapshot())

        retired = SyntheticSession(self.id() + "-retired")
        try:
            retired.turn("I hand you a red lantern.")
            retired.turn("Put the red lantern down.")
            retired.turn("Throw the red lantern away.")
            before = retired.structural_snapshot()
            rejected = decision(retired, "red lantern")
            self.assertEqual("action_scene_subject_ambiguous", rejected.category)
            self.assertEqual(before, retired.structural_snapshot())
        finally:
            retired.close()

        scoped = SyntheticSession(self.id() + "-scope")
        try:
            scoped.turn("A red lantern is nearby.")
            scoped.turn("Let's roleplay that we're in a studio.")
            before = scoped.structural_snapshot()
            rejected = decision(scoped, "red lantern")
            self.assertEqual("action_scene_subject_ambiguous", rejected.category)
            self.assertEqual(before, scoped.structural_snapshot())
        finally:
            scoped.close()

    def test_opposite_side_wearables_coexist_without_subject_collapse(self):
        self.session.turn("You're wearing a left glove and a right glove.")
        self.session.turn("Your left glove is wet.")

        worn = [
            relation for relation in self.session.relations()
            if relation.target == "companion" and relation.predicate == "wearing"
        ]
        self.assertEqual({("left glove", "left"), ("right glove", "right")}, {
            (relation.cause, relation.side) for relation in worn
        })
        self.assertEqual(2, len({relation.cause_subject_id for relation in worn}))
        gloves = [row for row in self._attributes() if row.get("kind") == "glove"]
        self.assertEqual("true", next(row for row in gloves if row.get("side") == "left").get("wet"))
        self.assertNotIn("wet", next(row for row in gloves if row.get("side") == "right"))

    def test_short_explicit_environmental_consequence_has_bounded_lifecycle(self):
        self.session.turn("It is too loud to hear me.")
        self.assertEqual("unavailable", self.session.effects().hearing_mode)
        self.assertEqual("available", self.session.effects().vision_mode)
        self.session.turn("It quiets down.")
        self.assertEqual("normal", self.session.effects().hearing_mode)

    def test_relation_contrasts_persist_fact_without_inventing_capability(self):
        cases = (
            ("The rollerblades are beside you.", "near", "rollerblade"),
            ("The blindfold is in your hand.", "holding", "blindfold"),
            ("The earplugs are on the table.", "located_on", "table"),
        )
        for index, (text, predicate, cause) in enumerate(cases):
            nested = SyntheticSession(f"{self.id()}-{index}")
            try:
                result = nested.turn(text)
                self.assertEqual("applied", result.get("state"), text)
                self.assertTrue(any(
                    relation.predicate == predicate
                    and cause in relation.cause.casefold()
                    for relation in nested.relations()
                ), text)
                effects = nested.effects()
                self.assertEqual("available", effects.vision_mode, text)
                self.assertEqual("normal", effects.hearing_mode, text)
                self.assertEqual("walking", effects.locomotion_mode, text)
            finally:
                nested.close()

    def test_relation_semantic_transition_reuses_subject_and_recomputes_effect(self):
        transitions = (
            (
                "You're wearing rollerblades.", "The rollerblades are beside you.",
                "locomotion_mode", "skating", "walking", "near",
            ),
            (
                "I blindfold you.", "The blindfold is in your hand.",
                "vision_mode", "unavailable", "available", "holding",
            ),
            (
                "I put earplugs in your ears.", "The earplugs are on the table.",
                "hearing_mode", "constrained", "normal", "located_on",
            ),
        )
        for index, (setup, move, field, before, after, predicate) in enumerate(transitions):
            nested = SyntheticSession(f"{self.id()}-{index}")
            try:
                nested.turn(setup)
                original = nested.repository.list_scene_subjects(
                    nested.character_id, limit=4,
                )[0].scene_subject_id
                self.assertEqual(before, getattr(nested.effects(), field), setup)
                nested.turn(move)
                self.assertEqual(after, getattr(nested.effects(), field), move)
                relation = next(
                    row for row in nested.relations() if row.predicate == predicate
                )
                subject_id = (
                    relation.target if relation.target_kind == "scene"
                    else relation.cause_subject_id
                )
                self.assertEqual(original, subject_id, move)
            finally:
                nested.close()

    def test_questions_hypotheticals_modals_and_self_negation_never_mutate(self):
        unsafe = (
            "I'm holding a lantern?",
            "I'm riding a bicycle?",
            "I wonder what would happen if the smoke makes it hard to see.",
            "Maybe the perfume makes it hard to smell.",
            "In a hypothetical story, I blindfold you.",
            "The wheelchair might be nearby.",
            "You're wearing a hat, or maybe not.",
        )
        for index, text in enumerate(unsafe):
            with self.subTest(text=text):
                before = self.session.structural_snapshot()
                result = self.session.turn(text)
                self.assertEqual(before, self.session.structural_snapshot())
                self.assertNotEqual("applied", result.get("state"), index)

    def test_direct_queries_cover_user_holder_posture_location_and_side(self):
        self.session.turn("I hand you a cup.")
        self.session.turn("Give me the cup.")
        self.session.turn("I put the cup on the table.")
        self.session.turn("You're holding a book in your left hand.")
        self.session.turn("Sit down.")

        for text in (
            "What am I holding?",
            "Where is the cup?",
            "Which hand is free?",
            "What position are you in?",
            "What is affecting your hands?",
            "Are you still holding the book?",
        ):
            with self.subTest(text=text):
                requirement = self._requirement(text)
                self.assertIsNotNone(requirement)
                assert requirement is not None
                self.assertTrue(validate_response_requirement(
                    requirement, requirement.fallback_dialogue,
                ).accepted)

        user_holding = self._requirement("What am I holding?")
        assert user_holding is not None
        self.assertIn("nothing", user_holding.fallback_dialogue.casefold())
        location = self._requirement("Where is the cup?")
        assert location is not None
        self.assertIn("table", location.fallback_dialogue.casefold())

    def test_location_query_projects_explicit_current_relation_without_guessing(self):
        cases = (
            ("The rollerblades are beside you.", "Where are the rollerblades?", "nearby"),
            ("The blindfold is in your hand.", "Where is the blindfold?", "holding"),
            ("You're wearing a red hat.", "Where is the red hat?", "wearing"),
        )
        for index, (setup, query, expected) in enumerate(cases):
            nested = SyntheticSession(f"{self.id()}-{index}")
            try:
                nested.turn(setup)
                requirement = derive_response_requirement(
                    nested.repository, nested.character_id, query,
                    local_datetime=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
                    companion_effects=nested.effects(),
                    user_effects=nested.effects(target="user"),
                )
                self.assertIsNotNone(requirement, query)
                assert requirement is not None
                self.assertIn(expected, requirement.fallback_dialogue.casefold(), query)
                self.assertTrue(validate_response_requirement(
                    requirement, requirement.fallback_dialogue,
                ).accepted, query)
            finally:
                nested.close()

    def test_direct_query_known_absence_unknown_scope_and_recent_replacement(self):
        unknown_attire = self._requirement("What are you wearing?")
        assert unknown_attire is not None
        self.assertEqual("unknown", unknown_attire.facts[0].value)
        self.assertNotIn("nothing", unknown_attire.fallback_dialogue.casefold())

        self.session.turn("You're wearing a blue hat.")
        self.session.turn("I replace your blue hat with a red one.")
        old = self._requirement("Are you still wearing the blue hat?")
        new = self._requirement("Are you still wearing the red hat?")
        assert old is not None and new is not None
        self.assertEqual("absent", old.facts[-1].value)
        self.assertEqual("current", new.facts[-1].value)

        self.session.turn("I blindfold you.")
        self.session.turn("The room is pitch black.")
        causes = self._requirement("Why can't you see?")
        assert causes is not None
        self.assertEqual({"blindfold", "darkness"}, {
            fact.value for fact in causes.facts
        })

        self.session.turn("Let's roleplay that we're in a quiet library.")
        scoped_unknown = self._requirement("What are you wearing?")
        assert scoped_unknown is not None
        self.assertEqual("unknown", scoped_unknown.facts[0].value)
        self.session.turn("You're wearing a green coat.")
        scoped_known = self._requirement("What are you wearing?")
        assert scoped_known is not None
        self.assertEqual(["green coat"], [fact.value for fact in scoped_known.facts])
        self.session.turn("Back to real life.")
        restored = self._requirement("What are you wearing?")
        assert restored is not None
        self.assertEqual({"red hat", "blindfold"}, {fact.value for fact in restored.facts})

        # Retired identity is historical, not a current negative claim.
        self.session.turn("Throw the red hat away.")
        retired = self._requirement("Are you still wearing the red hat?")
        assert retired is not None
        self.assertEqual("unknown", retired.facts[-1].value)

    def test_batch_replacement_transfer_and_retirement_roll_back_atomically(self):
        connection = self.session.writer.store.connection

        self.session.turn("You're wearing a blue hat.")
        before = self.session.structural_snapshot()
        connection.execute("""
            CREATE TEMP TRIGGER adversarial_fail_red_relation
            BEFORE INSERT ON active_scene_relations
            WHEN NEW.cause='red hat'
            BEGIN SELECT RAISE(ABORT, 'injected replacement failure'); END
        """)
        failed = self.session.turn("I replace your blue hat with a red one.")
        self.assertEqual("failed", failed.get("state"))
        self.assertEqual(before, self.session.structural_snapshot())
        connection.execute("DROP TRIGGER adversarial_fail_red_relation")

        self.session.turn("You're holding a cup.")
        before = self.session.structural_snapshot()
        connection.execute("""
            CREATE TEMP TRIGGER adversarial_fail_transfer
            BEFORE INSERT ON active_scene_relations
            WHEN NEW.target_actor='user' AND NEW.predicate IN ('holding','carrying')
            BEGIN SELECT RAISE(ABORT, 'injected transfer failure'); END
        """)
        failed = self.session.turn("Give me the cup.")
        self.assertEqual("failed", failed.get("state"))
        self.assertEqual(before, self.session.structural_snapshot())
        connection.execute("DROP TRIGGER adversarial_fail_transfer")

        self.session.turn("I hand you a red lantern.")
        self.session.turn("Put the red lantern down.")
        before = self.session.structural_snapshot()
        connection.execute("""
            CREATE TEMP TRIGGER adversarial_fail_retirement
            BEFORE UPDATE OF retired_at_us ON active_scene_subjects
            WHEN NEW.retired_at_us IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'injected retirement failure'); END
        """)
        failed = self.session.turn("Throw the red lantern away.")
        self.assertEqual("failed", failed.get("state"))
        self.assertEqual(before, self.session.structural_snapshot())
        connection.execute("DROP TRIGGER adversarial_fail_retirement")
        self.session.restart()
        self.assertEqual(before, self.session.structural_snapshot())

    def test_projection_and_scope_failures_roll_back_outer_authority_transaction(self):
        store = self.session.writer.store
        self.session.turn("You're wearing a blue hat.")
        before = self.session.structural_snapshot()
        original_sync = store.synchronize_scene_ownership_mirrors

        def fail_sync(*_args, **_kwargs):
            raise RuntimeError("injected projection failure")

        store.synchronize_scene_ownership_mirrors = fail_sync
        try:
            failed = self.session.turn("You're holding a cup.")
        finally:
            store.synchronize_scene_ownership_mirrors = original_sync
        self.assertEqual("failed", failed.get("state"))
        self.assertEqual(before, self.session.structural_snapshot())

        original_activate = store.activate_truth_scope

        def fail_after_activate(*args, **kwargs):
            original_activate(*args, **kwargs)
            raise RuntimeError("injected scope activation failure")

        store.activate_truth_scope = fail_after_activate
        try:
            failed = self.session.turn("Let's roleplay that we're in an observatory.")
        finally:
            store.activate_truth_scope = original_activate
        self.assertEqual("failed", failed.get("state"))
        self.assertEqual(before, self.session.structural_snapshot())
        self.session.restart()
        self.assertEqual(before, self.session.structural_snapshot())

    def test_dormant_budget_boundaries_and_canonical_history_preservation(self):
        for count in (31, 32, 33, 64, 100):
            nested = SyntheticSession(f"{self.id()}-{count}")
            try:
                for _index in range(count):
                    nested.turn("I hand you a cup.")
                    nested.turn("Put the cup down.")
                current = nested.repository.list_scene_subjects(
                    nested.character_id, limit=128,
                )
                retired = int(nested.writer.store.connection.execute(
                    "SELECT COUNT(*) FROM active_scene_subjects "
                    "WHERE character_id=? AND retired_at_us IS NOT NULL",
                    (nested.character_id,),
                ).fetchone()[0])
                self.assertEqual(min(count, 32), len(current))
                self.assertEqual(max(0, count - 32), retired)
                self.assertEqual(4 * count, len(nested.rows))
                self.assertEqual(0, len(nested.relations()))
            finally:
                nested.close()

    def test_generic_and_distinct_dormant_age_boundaries_are_exact(self):
        for label, threshold in (
            ("cup", GENERIC_DORMANT_RETIRE_AFTER_US),
            ("red cup", DISTINCT_DORMANT_RETIRE_AFTER_US),
        ):
            nested = SyntheticSession(f"{self.id()}-{label}")
            try:
                nested.turn(f"I hand you a {label}.")
                nested.turn(f"Put the {label} down.")
                subject = nested.repository.list_scene_subjects(
                    nested.character_id, limit=4,
                )[0]
                last_reference = int(subject.last_referenced_at_us or 0)
                # SyntheticSession timestamps are BASE_TIME + two seconds per turn.
                base_us = int(BASE_TIME.timestamp() * 1_000_000)
                nested.turn_count = max(
                    nested.turn_count,
                    (last_reference + threshold - 1_000_000 - base_us) // 2_000_000,
                )
                nested.turn("A blue lantern is nearby.")
                row = nested.writer.store.connection.execute(
                    "SELECT retired_at_us FROM active_scene_subjects "
                    "WHERE character_id=? AND scene_subject_id=?",
                    (nested.character_id, subject.scene_subject_id),
                ).fetchone()
                self.assertIsNone(row["retired_at_us"], label)
                nested.turn("A green lantern is nearby.")
                row = nested.writer.store.connection.execute(
                    "SELECT retired_at_us FROM active_scene_subjects "
                    "WHERE character_id=? AND scene_subject_id=?",
                    (nested.character_id, subject.scene_subject_id),
                ).fetchone()
                self.assertIsNotNone(row["retired_at_us"], label)
            finally:
                nested.close()

    def test_scope_restart_and_reentry_restore_exact_conflicting_scene(self):
        self.session.turn("You're wearing a black dress.")
        self.session.turn("You're holding a cup.")
        real = self.session.structural_snapshot()

        self.session.turn("Let's roleplay that we're in Castle A.")
        self.session.turn("You're wearing a red coat.")
        self.session.turn("You're blindfolded.")
        self.session.turn("You're holding a book.")
        scope_a = self.session.structural_snapshot()

        self.session.turn("Back to real life.")
        self.assertEqual(real, self.session.structural_snapshot())
        self.session.restart()
        self.assertEqual(real, self.session.structural_snapshot())

        self.session.turn("Let's roleplay that we're in Castle B.")
        self.session.turn("Sit down.")
        self.session.turn("The room is pitch black.")
        scope_b = self.session.structural_snapshot()
        self.assertNotEqual(scope_a, scope_b)
        self.session.turn("Back to real life.")
        self.session.turn("Let's resume our Castle A roleplay.")
        self.assertEqual(scope_a, self.session.structural_snapshot())
        context = self.session.context("What are you wearing?")
        self.assertNotIn("black dress", context.casefold())
        self.assertNotIn("scene-", context)

    def test_retirement_and_explicit_reactivation_are_truth_scope_local(self):
        self.session.turn("You're wearing a red hat.")
        real_id = self.session.repository.list_scene_subjects(
            self.session.character_id, limit=4,
        )[0].scene_subject_id

        self.session.turn("Let's roleplay that we're in a studio.")
        self.session.turn("You're wearing a blue hat.")
        scenario_id = self.session.repository.list_scene_subjects(
            self.session.character_id, limit=4,
        )[0].scene_subject_id
        self.session.turn("Throw the blue hat away.")
        self.assertEqual((), self.session.repository.list_scene_subjects(
            self.session.character_id, limit=4,
        ))

        self.session.turn("Back to real life.")
        self.assertEqual({real_id}, {
            row.scene_subject_id for row in self.session.repository.list_scene_subjects(
                self.session.character_id, limit=4,
            )
        })
        self.session.restart()
        self.session.turn("Let's resume our studio roleplay.")
        self.assertEqual((), self.session.repository.list_scene_subjects(
            self.session.character_id, limit=4,
        ))
        self.session.turn("Put on the blue hat.")
        self.assertEqual({scenario_id}, {
            row.scene_subject_id for row in self.session.repository.list_scene_subjects(
                self.session.character_id, limit=4,
            )
        })
        self.session.turn("Back to real life.")
        self.assertEqual({real_id}, {
            row.scene_subject_id for row in self.session.repository.list_scene_subjects(
                self.session.character_id, limit=4,
            )
        })

    def test_long_holder_chain_keeps_identity_actor_and_projection_parity(self):
        self.session.turn("You're holding a cup.")
        cup_id = next(
            row.scene_subject_id
            for row in self.session.repository.list_scene_subjects(
                self.session.character_id, limit=128,
            )
        )
        self.session.turn("Give me the cup.")
        self.assertEqual({("user", cup_id)}, {
            (relation.target, relation.cause_subject_id)
            for relation in self.session.relations()
            if relation.predicate in {"holding", "carrying"}
        })
        self.session.restart()
        self.session.turn("I put the cup on the table.")
        self.session.turn("Pick up the cup.")
        self.assertEqual({("companion", cup_id)}, {
            (relation.target, relation.cause_subject_id)
            for relation in self.session.relations()
            if relation.predicate in {"holding", "carrying"}
        })
        self.session.turn("Drop the cup.")
        self.session.turn("Throw the cup away.")
        self.assertEqual((), self.session.repository.list_scene_subjects(
            self.session.character_id, limit=128,
        ))


if __name__ == "__main__":
    unittest.main()
