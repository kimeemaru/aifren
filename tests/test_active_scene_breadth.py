"""Focused breadth/invariant tests for sparse governed scene semantics."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from tests.active_state_support import SyntheticSession
from active_scene import extract_active_scene_mutation
from capability_policy import capability_context_block, validate_capability_response
from companion_action import (
    action_narration_valid,
    companion_action_relevant,
    unauthorized_action_narrated,
    validate_companion_action_decision,
)
from presentation_metadata import parse_assistant_response
from response_requirements import derive_response_requirement


class ActiveSceneBreadthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SyntheticSession(self.id())

    def tearDown(self) -> None:
        self.session.close()

    def _subjects(self):
        return self.session.repository.list_scene_subjects(
            self.session.character_id, limit=128,
        )

    def _attributes(self, subject_id: str) -> dict[str, str]:
        return {
            row.subject_key.rsplit(".", 1)[-1]: row.value
            for row in self.session.repository.lookup_scene_attributes(
                self.session.character_id, subject_id,
            )
        }

    def test_sixty_conventional_blindfold_surfaces_all_mean_eye_coverage(self):
        for verb in ("put", "place", "placed", "apply", "applied"):
            for article in ("a", "the", "this"):
                for destination in ("on you", "over your eyes", "on your eyes", "across your eyes"):
                    text = f"I {verb} {article} blindfold {destination}."
                    mutation = extract_active_scene_mutation(text, subjects={})
                    with self.subTest(text=text):
                        self.assertIsNotNone(mutation)
                        relations = tuple(mutation.relations)
                        self.assertEqual(1, len(relations))
                        self.assertEqual(
                            ("eyes", "covered_by", "vision_obstruction"),
                            (relations[0].facet, relations[0].predicate, relations[0].semantic_family),
                        )

    def test_manual_posture_choice_is_punctuation_tolerant(self):
        self.assertTrue(companion_action_relevant(
            "Decide whether you want to sit, or lie down, then do it."
        ))

    def test_attribute_change_keeps_identity_and_relabels_current_relation(self):
        self.session.turn("You're wearing a blue hat.")
        original = next(row for row in self._subjects() if self._attributes(row.scene_subject_id).get("kind") == "hat")
        self.session.turn("Your blue hat is red now.")

        hats = [row for row in self._subjects() if self._attributes(row.scene_subject_id).get("kind") == "hat"]
        self.assertEqual([original.scene_subject_id], [row.scene_subject_id for row in hats])
        self.assertEqual("red", self._attributes(original.scene_subject_id)["color"])
        worn = [row for row in self.session.relations() if row.predicate == "wearing"]
        self.assertEqual([(original.scene_subject_id, "red hat")], [
            (row.cause_subject_id, row.cause) for row in worn
        ])

    def test_replacement_creates_new_subject_and_preserves_old_history(self):
        self.session.turn("You're wearing a blue hat.")
        old_id = next(row.scene_subject_id for row in self._subjects())
        self.session.turn("I replace your blue hat with a red one.")

        hats = [(row, self._attributes(row.scene_subject_id)) for row in self._subjects()]
        self.assertEqual(2, len(hats))
        current = [row for row, attrs in hats if attrs.get("worn_by") == "companion"]
        self.assertEqual(1, len(current))
        self.assertNotEqual(old_id, current[0].scene_subject_id)
        self.assertEqual("red", self._attributes(current[0].scene_subject_id)["color"])
        old = next(row for row, _attrs in hats if row.scene_subject_id == old_id)
        self.assertEqual("dormant", old.lifecycle_state)

    def test_multiple_worn_replacements_validate_and_apply_as_one_atomic_batch(self):
        self.session.turn("You're wearing boots, a red scarf, and a black dress.")
        before_ids = {row.scene_subject_id for row in self._subjects()}
        self.session.turn("Take off the boots and the red scarf and put on sneakers and a necklace.")
        worn = {row.cause for row in self.session.relations() if row.predicate == "wearing"}
        self.assertEqual({"sneakers", "necklace", "black dress"}, worn)
        after_ids = {row.scene_subject_id for row in self._subjects()}
        self.assertGreaterEqual(len(after_ids - before_ids), 2)

        before = self.session.structural_snapshot()
        result = self.session.turn(
            "Take off the sneakers and the necklace and put on boots and rollerblades."
        )
        self.assertEqual(before, self.session.structural_snapshot())
        self.assertEqual(0, int(result.get("scene_relation_updates", 0)))

    def test_qualified_replacement_ignores_other_item_sharing_color(self):
        self.session.turn("You're wearing boots, a red scarf, a red hat, and a black dress.")
        self.session.turn(
            "Take off the boots and the red scarf and put on sneakers and a necklace."
        )

        worn = {row.cause for row in self.session.relations() if row.predicate == "wearing"}
        self.assertEqual({"sneakers", "necklace", "red hat", "black dress"}, worn)

    def test_location_is_a_relation_and_does_not_require_a_current_holder(self):
        self.session.turn("I hand you a red cup.")
        self.session.turn("Put the red cup down.")
        self.session.turn("Put the red cup on the table.")
        located = next(row for row in self.session.relations() if row.predicate == "located_on")
        self.assertEqual("table", located.cause)
        attributes = self._attributes(located.target)
        self.assertEqual("table", attributes["location"])
        self.assertNotIn("held_by", attributes)

    def test_environmental_senses_are_independent_and_clear_by_cause(self):
        self.session.turn("The room is pitch black.")
        self.session.turn("The music is so loud you can't hear me.")
        effects = self.session.effects()
        self.assertEqual("unavailable", effects.vision_mode)
        self.assertEqual("unavailable", effects.hearing_mode)
        self.assertEqual("normal", effects.smell_mode)

        self.session.turn("The music stops.")
        effects = self.session.effects()
        self.assertEqual("unavailable", effects.vision_mode)
        self.assertEqual("normal", effects.hearing_mode)
        self.session.turn("The lights come on.")
        self.assertEqual("available", self.session.effects().vision_mode)

    def test_one_ear_and_wrist_restraint_do_not_leak_domains(self):
        self.session.turn("I cover your left ear.")
        effects = self.session.effects()
        self.assertEqual("constrained", effects.hearing_mode)
        self.assertEqual("normal", effects.speech_mode)
        self.assertEqual("available", effects.vision_mode)

        self.session.turn("Your sparkly scrunchie is on your wrist.")
        self.assertTrue(any(row.predicate == "wearing" and row.facet == "wrists" for row in self.session.relations()))
        self.assertEqual("free", self.session.effects().hands_mode)
        self.session.turn("Your blue and purple sparkly scrunchie is on your wrist.")
        scrunchie = next(
            self._attributes(row.scene_subject_id) for row in self._subjects()
            if self._attributes(row.scene_subject_id).get("kind") == "scrunchie"
            and self._attributes(row.scene_subject_id).get("color") == "blue and purple"
        )
        self.assertEqual("sparkly", scrunchie["condition"])
        self.assertEqual("free", self.session.effects().hands_mode)
        self.session.turn("Your left wrist is handcuffed to a pole.")
        effects = self.session.effects()
        self.assertEqual("constrained", effects.left_arm_mode)
        self.assertEqual("normal", effects.right_arm_mode)
        self.assertEqual("constrained", effects.hands_mode)
        self.assertEqual("normal", effects.speech_mode)
        self.assertEqual("available", effects.vision_mode)

    def test_earplugs_are_current_equipment_and_only_constrain_hearing(self):
        self.session.turn("I put earplugs in your ears.")
        relation = next(row for row in self.session.relations() if row.cause == "earplugs")
        self.assertEqual(("ears", "hearing_obstruction"), (relation.facet, relation.semantic_family))
        effects = self.session.effects()
        self.assertEqual("constrained", effects.hearing_mode)
        self.assertEqual("available", effects.vision_mode)
        self.assertEqual("normal", effects.speech_mode)
        self.session.turn("I remove the earplugs.")
        self.assertEqual("normal", self.session.effects().hearing_mode)

    def test_one_ear_and_both_ear_coverage_preserve_bounded_side_evidence(self):
        self.session.turn("I cover your left ear.")
        first = next(row for row in self.session.relations() if row.facet == "ears")
        self.assertEqual("left", first.side)
        self.assertEqual("constrained", self.session.effects().hearing_mode)
        self.session.turn("I uncover your left ear.")
        self.assertEqual("normal", self.session.effects().hearing_mode)

        self.session.turn("I cover both of your ears.")
        both = next(row for row in self.session.relations() if row.facet == "ears")
        self.assertEqual("both", both.side)
        self.assertEqual("constrained", self.session.effects().hearing_mode)
        self.session.turn("I uncover your ears.")
        self.assertEqual("normal", self.session.effects().hearing_mode)

    def test_body_unavailability_is_side_aware_and_leg_does_not_invent_immobility(self):
        self.session.turn("Your left arm is missing.")
        effects = self.session.effects()
        self.assertEqual("unavailable", effects.left_arm_mode)
        self.assertEqual("normal", effects.right_arm_mode)
        self.assertEqual("constrained", effects.hands_mode)
        self.assertEqual("walking", effects.locomotion_mode)
        self.session.turn("Your leg is missing.")
        self.assertEqual("walking", self.session.effects().locomotion_mode)
        self.assertEqual("normal", self.session.effects().locomotion_constraint)
        self.session.turn("You're in a wheelchair.")
        self.assertEqual("assisted", self.session.effects().locomotion_mode)
        self.session.turn("You get out of the wheelchair.")
        self.session.turn("Your left arm is available again.")
        self.assertEqual("normal", self.session.effects().left_arm_mode)

    def test_explicit_prosthetic_assistance_composes_without_erasing_body_history(self):
        self.session.turn("Your left arm is missing.")
        self.assertEqual("unavailable", self.session.effects().left_arm_mode)
        self.session.turn("You're wearing a left prosthetic arm.")
        self.assertEqual("constrained", self.session.effects().left_arm_mode)
        self.assertEqual("normal", self.session.effects().right_arm_mode)
        self.session.turn("Take off the prosthetic arm.")
        self.assertEqual("unavailable", self.session.effects().left_arm_mode)

        self.session.turn("Your left leg is missing.")
        self.assertEqual("walking", self.session.effects().locomotion_mode)
        self.session.turn("You're wearing a left prosthetic leg.")
        self.assertEqual("assisted", self.session.effects().locomotion_mode)
        self.session.turn("Take off the prosthetic leg.")
        self.assertEqual("walking", self.session.effects().locomotion_mode)

    def test_nearby_transport_does_not_activate_mode_or_block_later_dismount(self):
        self.session.turn("A bicycle is nearby.")
        bicycle_id = next(
            row.scene_subject_id for row in self._subjects()
            if self._attributes(row.scene_subject_id).get("kind") == "bicycle"
        )
        self.assertEqual("walking", self.session.effects().locomotion_mode)
        self.session.turn("You get on the bicycle.")
        self.assertEqual("cycling", self.session.effects().locomotion_mode)
        riding = next(row for row in self.session.relations() if row.predicate == "riding")
        self.assertEqual(bicycle_id, riding.cause_subject_id)
        self.session.turn("You get off the bicycle.")
        self.assertEqual("walking", self.session.effects().locomotion_mode)

    def test_carried_equipment_and_nearby_assistance_require_active_relation_semantics(self):
        self.session.turn("You're holding rollerblades.")
        self.assertEqual("walking", self.session.effects().locomotion_mode)
        self.assertEqual("occupied", self.session.effects().hands_mode)
        rollerblade_id = next(
            row.scene_subject_id for row in self._subjects()
            if self._attributes(row.scene_subject_id).get("kind") == "rollerblades"
        )
        self.session.turn("You're wearing the rollerblades.")
        self.assertEqual("skating", self.session.effects().locomotion_mode)
        self.assertEqual("free", self.session.effects().hands_mode)
        wearing = next(row for row in self.session.relations() if row.predicate == "wearing")
        self.assertEqual(rollerblade_id, wearing.cause_subject_id)

        self.session.turn("A wheelchair is nearby.")
        wheelchair_id = next(
            row.scene_subject_id for row in self._subjects()
            if self._attributes(row.scene_subject_id).get("kind") == "wheelchair"
        )
        self.assertEqual("skating", self.session.effects().locomotion_mode)
        self.session.turn("You're in the wheelchair.")
        seated = next(row for row in self.session.relations() if row.predicate == "seated_in")
        self.assertEqual(wheelchair_id, seated.cause_subject_id)
        self.assertEqual("assisted", self.session.effects().locomotion_mode)

    def test_held_obstruction_has_no_sensory_effect_until_applied(self):
        self.session.turn("You're holding a blindfold.")
        blindfold_id = next(
            row.scene_subject_id for row in self._subjects()
            if self._attributes(row.scene_subject_id).get("kind") == "blindfold"
        )
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual("partially_occupied", self.session.effects().hands_mode)

        self.session.turn("I blindfold you.")
        coverage = next(row for row in self.session.relations() if row.facet == "eyes")
        self.assertEqual(blindfold_id, coverage.cause_subject_id)
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.assertEqual("free", self.session.effects().hands_mode)

        nested = SyntheticSession(self.id() + "-earplugs")
        try:
            nested.turn("You're holding earplugs.")
            self.assertEqual("normal", nested.effects().hearing_mode)
            nested.turn("I put the earplugs in your ears.")
            self.assertEqual("constrained", nested.effects().hearing_mode)
            self.assertEqual("free", nested.effects().hands_mode)
        finally:
            nested.close()

    def test_generic_environmental_cause_clears_without_physics(self):
        self.session.turn("The perfume makes it hard to smell.")
        self.assertEqual("constrained", self.session.effects().smell_mode)
        self.session.turn("The perfume clears.")
        self.assertEqual("normal", self.session.effects().smell_mode)

    def test_clause_salvage_applies_clear_clauses_and_abstains_malformed_fragment(self):
        self.session.turn("Let's play a game. I blindfold you and cover you hands with my eyes.")
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.assertEqual(1, len(self.session.relations()))

        self.session.turn("Well, the music is so loud you can't hear me. Your scarf is maybe wet.")
        self.assertEqual("unavailable", self.session.effects().hearing_mode)
        self.assertFalse(any(row.cause == "maybe wet" for row in self.session.relations()))

    def test_interrogative_surface_never_falls_through_to_legacy_relation_parser(self):
        before = self.session.structural_snapshot()
        result = self.session.turn("I blindfold you?")
        self.assertEqual(before, self.session.structural_snapshot())
        self.assertEqual((), self.session.relations())
        self.assertEqual(0, int(result.get("scene_relation_updates", 0)))

    def test_dormant_budget_never_retires_current_cause_and_generic_does_not_reactivate(self):
        for _index in range(36):
            self.session.turn("I hand you a cup.")
            self.session.turn("Put the cup down.")
        retired = self.session.repository.list_retired_scene_subjects(self.session.character_id)
        self.assertGreaterEqual(len(retired), 4)

        self.session.turn("I hand you a cup.")
        current_relation = next(row for row in self.session.relations() if row.predicate == "holding")
        self.assertNotIn(current_relation.cause_subject_id, {row.scene_subject_id for row in retired})
        self.assertEqual("partially_occupied", self.session.effects().hands_mode)

    def test_distinct_retired_subject_reactivates_only_on_explicit_unique_identity(self):
        self.session.turn("You're wearing a red hat.")
        subject_id = next(row.scene_subject_id for row in self._subjects())
        self.session.turn("Throw the red hat away.")
        self.assertEqual((), self._subjects())
        self.session.turn("Put on the red hat.")
        current = self._subjects()
        self.assertEqual([subject_id], [row.scene_subject_id for row in current])
        self.assertEqual("companion", self._attributes(subject_id)["worn_by"])

    def test_generic_retired_wearable_does_not_resurrect_by_kind_alone(self):
        self.session.turn("You're wearing a hat.")
        original = next(row.scene_subject_id for row in self._subjects())
        self.session.turn("Throw the hat away.")
        self.session.turn("Put on the hat.")
        current = self._subjects()
        self.assertEqual(1, len(current))
        self.assertNotEqual(original, current[0].scene_subject_id)

    def test_capability_context_contains_every_hard_domain_without_internal_ids(self):
        for text in (
            "The room is pitch black.",
            "The music is so loud you can't hear me.",
            "The smoke makes it difficult to smell.",
            "The spice makes it impossible to taste.",
            "The gloves make it hard to feel.",
            "I cover your mouth with my hand.",
            "Your left wrist is tethered to a pole.",
        ):
            self.session.turn(text)
        block = capability_context_block(self.session.effects())
        for name in (
            "vision", "hearing", "smell", "taste", "touch", "speech", "manipulation",
        ):
            self.assertIn(name, block)
        self.assertNotIn("scene-", block)
        self.assertLess(len(block), 2601)

    def test_direct_sense_requirement_and_unavailable_semantic_rejection(self):
        self.session.turn("The music is so loud you can't hear me.")
        requirement = derive_response_requirement(
            self.session.repository, self.session.character_id, "Can you hear me?",
            local_datetime=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
            companion_effects=self.session.effects(), user_effects=self.session.effects(target="user"),
        )
        self.assertIsNotNone(requirement)
        self.assertIn("unavailable", requirement.context_block)

        invalid = parse_assistant_response('{"dialogue":"I can just make out your voice.","response_mode":"normal_conversation"}')
        result = validate_capability_response(invalid, self.session.effects())
        self.assertFalse(result.accepted)
        self.assertEqual("prohibited_hearing_claim", result.category)
        valid = parse_assistant_response('{"dialogue":"I cannot hear you, but I can still see you.","response_mode":"normal_conversation"}')
        self.assertTrue(validate_capability_response(valid, self.session.effects()).accepted)

    def test_unavailable_senses_reject_hedged_current_perception_but_allow_remaining_channels(self):
        setup_and_claim = (
            ("The perfume makes it impossible to smell.", "I can catch a faint scent."),
            ("The spice makes it impossible to taste.", "I can make out the flavour."),
            ("The numbness makes it impossible to feel.", "I can barely sense your touch."),
        )
        for setup, claim in setup_and_claim:
            nested = SyntheticSession(self.id() + setup)
            try:
                nested.turn(setup)
                parsed = parse_assistant_response(json.dumps({"dialogue": claim}))
                self.assertFalse(validate_capability_response(parsed, nested.effects()).accepted)
                allowed = parse_assistant_response(json.dumps({
                    "dialogue": "I can't use that sense right now, but I can still hear you.",
                }))
                self.assertTrue(validate_capability_response(allowed, nested.effects()).accepted)
            finally:
                nested.close()

    def test_companion_action_can_use_existing_subject_but_never_user_owned_subject(self):
        self.session.turn("You're wearing a red hat.")
        self.session.turn("Take off the red hat.")
        proposal = parse_assistant_response(
            '{"dialogue":"","response_mode":"action_decision","spoken_content":"",'
            '"companion_action":{"family":"wear","operation":"set","value":"red hat"}}'
        )
        decision = validate_companion_action_decision(
            proposal, self.session.repository, self.session.character_id, self.session.effects(),
        )
        self.assertTrue(decision.accepted)
        applied = self.session.apply_action(decision.plan)
        self.assertEqual("applied", applied["state"])
        self.assertEqual("companion", next(
            attrs["worn_by"] for attrs in self.session.scene() if attrs.get("kind") == "hat"
        ))
        narration = parse_assistant_response('{"dialogue":"*She puts on the red hat.*"}')
        self.assertTrue(action_narration_valid(decision.plan, narration))
        self.assertTrue(action_narration_valid(
            decision.plan, parse_assistant_response('{"dialogue":"*She puts it on.*"}'),
        ))
        self.assertFalse(action_narration_valid(
            decision.plan, parse_assistant_response('{"dialogue":"*She puts on a blue coat.*"}'),
        ))

        self.session.turn("I put on a blue hat.")
        user_hat = parse_assistant_response(
            '{"dialogue":"","response_mode":"action_decision","spoken_content":"",'
            '"companion_action":{"family":"wear","operation":"set","value":"blue hat"}}'
        )
        rejected = validate_companion_action_decision(
            user_hat, self.session.repository, self.session.character_id, self.session.effects(),
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual("action_user_authority", rejected.category)
        self.assertTrue(unauthorized_action_narrated(
            parse_assistant_response('{"dialogue":"*She puts on the blue hat.*"}')
        ))

    def test_companion_action_hold_obeys_current_hand_capability(self):
        self.session.turn("I hand you a book.")
        self.session.turn("Put the book down.")
        self.session.turn("You're holding a cup in one hand and a box in the other.")
        proposal = parse_assistant_response(
            '{"dialogue":"","response_mode":"action_decision","spoken_content":"",'
            '"companion_action":{"family":"hold","operation":"set","value":"book"}}'
        )
        decision = validate_companion_action_decision(
            proposal, self.session.repository, self.session.character_id, self.session.effects(),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual("action_hands_conflict", decision.category)


if __name__ == "__main__":
    unittest.main()
