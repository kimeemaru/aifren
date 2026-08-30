import json
from datetime import datetime, timezone
import unittest
import uuid

from assistant_service import AssistantService
from tests.active_state_support import BASE_TIME, SyntheticSession
from capability_policy import (
    normalize_constrained_caption,
    preview_capability_effects,
    validate_capability_response,
)
from character_scene_profile import (
    cache_character_scene_profile,
    derive_character_scene_profile,
    effective_profile_worn_items,
)
from companion_action import (
    action_narration_valid,
    unauthorized_action_narrated,
    validate_companion_action_decision,
)
from conversation.conversation import Conversation
from interaction_policy import (
    InteractionPolicyDecision,
    SleepReactionSignature,
    render_sleep_reaction,
    sleep_reaction_prompt,
)
from presentation_metadata import parse_assistant_response, response_contract_prompt
from response_requirements import (
    derive_mutation_response_requirement,
    derive_response_requirement,
    validate_response_requirement,
)
from memory_v2_store.repository import ActiveSceneRelationRecord
from memory_v2_store.scene_relation_contract import (
    SceneRelationProposal,
    compose_capability_effects,
)


def _envelope(
    dialogue,
    *,
    mode="normal_conversation",
    spoken=None,
    presentation=None,
    action=None,
):
    return json.dumps({
        "dialogue": dialogue,
        "response_mode": mode,
        "spoken_content": spoken,
        "presentation": presentation,
        "companion_action": action,
        "capability_compliance": [],
    })


class _ServiceMemory:
    def __init__(self):
        self.memories = []
        self.llm = None

    def get_relevant_memories(self, _query, max_memories=5):
        return []

    def process(self, _user, _assistant):
        return None


class _ServiceLlm:
    is_available = True

    def __init__(self, action_value="stretching"):
        self.action_value = action_value

    def generate_bounded(self, _context, prompt, max_output_tokens=0):
        if "GOVERNED COMPANION ACTION DECISION" in prompt:
            return _envelope(
                "", mode="action_decision", spoken="",
                action={
                    "family": "activity", "operation": "set",
                    "value": self.action_value,
                },
            )
        return "{}"

    def generate(self, _context, _prompt):
        return "{}"


class _ServiceTts:
    synthesis_strategy = "whole_response"

    def speak(self, _text):
        return True

    def stop(self):
        return 0


class ActiveStateImmersionTests(unittest.TestCase):
    def setUp(self):
        self.session = SyntheticSession(self.id())

    def tearDown(self):
        self.session.close()

    def test_one_response_contract_covers_normal_sleep_and_action_modes(self):
        prompt = response_contract_prompt()
        self.assertIn('{"dialogue":"your canonical natural reply"}', prompt)
        self.assertIn("sleep_reaction", prompt)
        self.assertIn("action_decision", prompt)
        self.assertNotIn('{"kind","action"', prompt)

        minimal = parse_assistant_response('{"dialogue":"Hello."}')
        self.assertEqual(minimal.contract_status, "valid")
        self.assertEqual(minimal.response_mode, "normal_conversation")
        self.assertFalse(minimal.response_mode_supplied)
        self.assertFalse(minimal.has_presentation_contract)

        normal = parse_assistant_response(_envelope("Hello."))
        sleep = parse_assistant_response(_envelope(
            "*Their ears twitch, then settle.*", mode="sleep_reaction", spoken="",
        ))
        self.assertEqual((normal.contract_status, sleep.contract_status), ("valid", "valid"))
        malformed = parse_assistant_response('{"dialogue":"Safe recovery",')
        self.assertEqual(malformed.dialogue, "Safe recovery")
        self.assertEqual(malformed.failure_category, "json_parse")
        unknown = json.loads(_envelope("No."))
        unknown["sleep_schema"] = {}
        rejected = parse_assistant_response(json.dumps(unknown))
        self.assertEqual(rejected.failure_category, "unknown_top_level_field")

        optional = json.loads(_envelope("Still safe."))
        optional["capability_compliance"] = [
            "perception", "communication:speech", "left", "awareness",
        ]
        sanitized = parse_assistant_response(json.dumps(optional))
        self.assertEqual(sanitized.contract_status, "valid")
        self.assertEqual(sanitized.capability_compliance, ("perception", "awareness"))

    def test_sleep_reactions_accept_creative_beats_and_reject_awake_answers(self):
        decision = InteractionPolicyDecision(
            "asleep", None, False, "sleep_relevant_stimulus", "sleepy_stimulus",
        )
        recent = (SleepReactionSignature("stir", "nonverbal", "shift_turn", False),)
        prompt = sleep_reaction_prompt(
            decision, "I softly touch your shoulder.", character_context="playful fox",
            capability_state={"perception": {"vision": "unavailable"}},
            recent_signatures=recent,
        )
        self.assertIn("AUTHORITATIVE RESPONSE FORMAT", prompt)
        self.assertIn("Immediate canonical user stimulus", prompt)
        self.assertIn('"movement":"shift_turn"', prompt)
        self.assertNotIn("Safe recovery", prompt)

        accepted = render_sleep_reaction(
            decision,
            _envelope(
                "*Their ears flick toward the touch, then relax.* Mmm...",
                mode="sleep_reaction", spoken="Mmm...",
                presentation={
                    "emotion": "relaxed", "pose": "sleeping", "gaze_mode": "suppressed",
                    "reaction": "stir", "speech_mode": "mumble",
                },
            ),
            user_message="I softly touch your shoulder.", recent_signatures=recent,
        )
        self.assertFalse(accepted.used_fallback)
        self.assertEqual(accepted.validation_category, "accepted")
        self.assertEqual(accepted.signature.movement_family, "ear_tail")

        projected_mumble = render_sleep_reaction(
            decision,
            _envelope(
                "*Their tail curls closer as they settle.*",
                mode="sleep_reaction", spoken="Mmmph...",
                presentation={"pose": "sleeping", "gaze_mode": "suppressed"},
            ),
            user_message="I tuck the blanket around you.",
        )
        self.assertFalse(projected_mumble.used_fallback)
        self.assertEqual(projected_mumble.spoken_text, "Mmmph...")

        safe_projection_variation = render_sleep_reaction(
            decision,
            _envelope(
                "*Their tail gives a sleepy twitch.* Mmm...",
                mode="sleep_reaction", spoken="M-mmph...",
            ),
            user_message="A floorboard creaks.",
        )
        self.assertFalse(safe_projection_variation.used_fallback)
        self.assertEqual(safe_projection_variation.spoken_text, "M-mmph...")

        null_nonverbal = json.loads(_envelope(
            "*One ear flicks before growing still.*", mode="sleep_reaction", spoken=None,
        ))
        null_nonverbal["capability_compliance"] = ["awareness", "perception"]
        null_reaction = render_sleep_reaction(
            decision, json.dumps(null_nonverbal), user_message="A soft noise sounds nearby.",
        )
        self.assertFalse(null_reaction.used_fallback)
        self.assertEqual(null_reaction.spoken_text, "")

        smuggled_answer = render_sleep_reaction(
            decision,
            _envelope(
                "*Their breathing remains slow and even.*", mode="sleep_reaction",
                spoken="The answer is forty-two.",
            ),
            user_message="What is the answer?",
        )
        self.assertTrue(smuggled_answer.used_fallback)
        self.assertEqual(smuggled_answer.fallback_category, "informative_speech")

        rejected = render_sleep_reaction(
            decision,
            _envelope(
                "*Gets up and checks the time.* It is noon.",
                mode="sleep_reaction", spoken="It is noon.",
            ),
            user_message="What time is it?",
        )
        self.assertTrue(rejected.used_fallback)
        self.assertIn(rejected.fallback_category, {"awake_behavior", "informative_speech"})
        self.assertNotIn("noon", rejected.dialogue.casefold())

        one_word = render_sleep_reaction(
            decision,
            _envelope(
                "*Twitches.*", mode="sleep_reaction", spoken="",
                presentation={"pose": "sleeping", "gaze_mode": "suppressed"},
            ),
            user_message="A floorboard creaks.",
        )
        self.assertFalse(one_word.used_fallback)

    def test_sleep_reaction_consumes_composed_vision_speech_and_hand_envelope(self):
        self.session.turn("I blindfold you.")
        self.session.turn("I cover your mouth with my hand.")
        self.session.turn("You're carrying two boxes.")
        self.session.turn("Go to sleep.")
        decision = InteractionPolicyDecision(
            "asleep", None, False, "sleep_relevant_stimulus", "sleepy_stimulus",
        )
        effects = self.session.effects()
        self.assertEqual(
            (effects.awareness_mode, effects.vision_mode, effects.speech_mode, effects.hands_mode),
            ("asleep", "unavailable", "constrained", "occupied"),
        )
        accepted = render_sleep_reaction(
            decision, '{"dialogue":"*Their ears twitch and settle.* Mmmph..."}',
            user_message="A floorboard creaks.", capability_effects=effects,
        )
        self.assertFalse(accepted.used_fallback)
        self.assertEqual("Mmmph...", accepted.spoken_text)
        self.assertEqual("suppressed", accepted.presentation.gaze_mode)
        self.assertEqual("occupied", accepted.presentation.hands_mode)
        self.assertEqual("asleep", accepted.presentation.awareness_mode)

        visual = render_sleep_reaction(
            decision, '{"dialogue":"*They make out your silhouette and wave both hands.*"}',
            user_message="A floorboard creaks.", capability_effects=effects,
        )
        self.assertTrue(visual.used_fallback)
        self.assertIn(
            visual.fallback_category,
            {"prohibited_visual_claim", "hands_occupied_action"},
        )

    def test_correction_is_user_bound_non_destructive_and_activity_parser_is_hardened(self):
        self.session.turn("Go work.")
        self.assertEqual(self.session.activity("companion"), "working")
        self.session.turn("That was a typo. I meant sleeping.")
        self.assertEqual(self.session.activity("companion"), "sleeping")
        history = self.session.writer.store.connection.execute(
            """SELECT COUNT(*) FROM claims WHERE character_id=?
                 AND subject_key='active.actor.companion.activity'""",
            (self.session.character_id,),
        ).fetchone()[0]
        self.assertGreaterEqual(history, 2)

        before = self.session.structural_snapshot()
        self.session.turn("Sorry, I meant that.")
        self.assertEqual(self.session.structural_snapshot(), before)
        self.session.turn("Go back.")
        self.assertEqual(self.session.activity("companion"), "sleeping")
        self.session.turn("Now go back to sleep and dream about it.")
        self.assertEqual(self.session.activity("companion"), "sleeping")

    def test_natural_compound_sleep_and_wake_clauses_keep_exact_authority(self):
        result = self.session.turn("I'll finish up here, time for you to take a nap.")
        self.assertEqual("applied", result.get("state"))
        self.assertEqual("sleeping", self.session.activity("companion"))

        self.session.turn("You can wake up now. Did you sleep okay?")
        self.assertIsNone(self.session.activity("companion"))
        self.session.turn("Go to sleep.")
        self.session.turn("It's time to wake up.")
        self.assertIsNone(self.session.activity("companion"))
        self.session.turn("Go to sleep.")
        self.session.turn("Wake up Synthetic.")
        self.assertIsNone(self.session.activity("companion"))

    def test_clause_salvage_keeps_valid_relation_and_abstains_on_malformed_neighbor(self):
        result = self.session.turn(
            "Let's play a game. I blindfold you and cover you hands with my eyes."
        )
        self.assertEqual("applied", result.get("state"))
        current = self.session.relations()
        self.assertEqual(1, len(current))
        self.assertEqual(("eyes", "covered_by", "blindfold"), (
            current[0].body_region, current[0].predicate, current[0].cause,
        ))
        self.assertEqual("unavailable", self.session.effects().vision_mode)

    def test_natural_unique_relation_clears_and_hand_objects_are_normalized(self):
        self.session.turn("I cover your eyes with my hands.")
        self.session.turn("I take my hands off your eyes.")
        self.assertEqual("available", self.session.effects().vision_mode)
        self.session.turn("I cover your mouth with my hand.")
        self.session.turn("I uncover your mouth.")
        self.assertEqual("normal", self.session.effects().speech_mode)

        self.session.turn("You're holding a cup in one hand and a book in the other.")
        held = [item for item in self.session.relations() if item.predicate == "holding"]
        self.assertEqual({"cup", "book"}, {item.cause for item in held})
        self.assertEqual({None}, {item.side for item in held})
        self.assertEqual("occupied", self.session.effects().hands_mode)
        self.session.turn("Put the cup down.")
        self.assertEqual("partially_occupied", self.session.effects().hands_mode)
        self.session.turn("Hand me the book.")
        self.assertEqual("free", self.session.effects().hands_mode)

    def test_transfer_then_holder_aware_release_keeps_relation_attribute_parity(self):
        self.session.turn("You're holding a cup.")
        self.session.turn("Give me the cup.")
        cup = next(row for row in self.session.relations() if row.cause == "cup")
        self.assertEqual((cup.target, cup.predicate), ("user", "holding"))
        attributes = {
            row.subject_key.rsplit(".", 1)[-1]: row.value
            for row in self.session.repository.lookup_scene_attributes(
                self.session.character_id, cup.cause_subject_id,
            )
        }
        self.assertEqual("user", attributes.get("held_by"))

        preview = preview_capability_effects(
            self.session.repository, self.session.character_id,
            "I put the cup on the table.",
        )
        requirement = derive_mutation_response_requirement(preview.extraction)
        self.assertIsNotNone(requirement)
        self.assertEqual("mutation_release", requirement.intent)
        self.assertFalse(validate_response_requirement(
            requirement, "Which cup do you mean?",
        ).accepted)
        self.assertTrue(validate_response_requirement(
            requirement, "*The cup is set down on the table.*",
        ).accepted)

        self.session.turn("I put the cup on the table.")
        self.assertEqual("free", self.session.effects(target="user").hands_mode)
        self.assertFalse(any(
            row.predicate in {"holding", "carrying"} and row.cause == "cup"
            for row in self.session.relations()
        ))
        attributes = {
            row.subject_key.rsplit(".", 1)[-1]: row.value
            for row in self.session.repository.lookup_scene_attributes(
                self.session.character_id, cup.cause_subject_id,
            )
        }
        self.assertNotIn("held_by", attributes)
        self.assertEqual("table", attributes.get("location"))

        for phrase in ("I set it down.", "I drop it.", "I let go of the cup."):
            fresh = SyntheticSession(self.id() + phrase)
            try:
                fresh.turn("You're holding a cup.")
                fresh.turn("Give me the cup.")
                fresh.turn(phrase)
                self.assertEqual("free", fresh.effects(target="user").hands_mode)
                self.assertFalse(any(
                    row.predicate in {"holding", "carrying"}
                    for row in fresh.relations()
                ))
            finally:
                fresh.close()

    def test_capability_composition_uses_all_active_relation_causes(self):
        self.session.turn("You're blindfolded.")
        self.session.turn("I cover your eyes with my hands.")
        effects = self.session.effects()
        self.assertEqual(effects.vision_mode, "unavailable")
        self.assertEqual(set(effects.vision_causes), {"blindfold", "user hands"})
        self.session.turn("I take my hands away from your eyes.")
        self.assertEqual(set(self.session.effects().vision_causes), {"blindfold"})
        self.session.turn("I take the blindfold off you.")
        self.assertEqual(self.session.effects().vision_mode, "available")

        self.session.turn("Your mouth is full.")
        self.assertEqual(self.session.effects().speech_mode, "constrained")
        self.session.turn("I cover your mouth with my hand.")
        self.assertEqual(self.session.effects().speech_mode, "constrained")
        self.session.turn("I take my hand away from your mouth.")
        self.assertEqual(self.session.effects().speech_mode, "constrained")
        projected = validate_capability_response(
            parse_assistant_response(_envelope(
                "*Tilts their head toward the sound.*", mode="speech_constrained",
                spoken="Mmph.",
            )),
            self.session.effects(),
        )
        self.assertTrue(projected.accepted)
        self.assertEqual(projected.spoken_text, "Mmph.")

        explicit_total = ActiveSceneRelationRecord(
            "synthetic-relation", self.session.character_id, "companion", "mouth",
            "obstructed_by", "state", "sealed gag", 1, None, "synthetic-scope",
            semantic_family="speech_obstruction",
        )
        total_effects = compose_capability_effects(
            (explicit_total,), truth_scope_id="synthetic-scope",
        )
        self.assertEqual("unavailable", total_effects.speech_mode)
        silenced = validate_capability_response(
            parse_assistant_response('{"dialogue":"*Their ears flatten in alarm.*"}'),
            total_effects,
        )
        self.assertTrue(silenced.accepted)
        self.assertEqual("", silenced.spoken_text)

    def test_speech_projection_accepts_same_stutter_and_normalizes_explicit_caption(self):
        self.session.turn("Your mouth is full.")
        constrained = validate_capability_response(
            parse_assistant_response(_envelope(
                "*Their ears angle forward.* H-hello...",
                mode="speech_constrained", spoken="h-hello",
            )),
            self.session.effects(),
        )
        self.assertTrue(constrained.accepted)

        derived_projection = validate_capability_response(
            parse_assistant_response(
                '{"dialogue":"*Their brows lift in surprise.* M-mmph..."}'
            ),
            self.session.effects(),
        )
        self.assertTrue(derived_projection.accepted)
        self.assertEqual("M-mmph...", derived_projection.spoken_text)
        accurately_constrained = validate_capability_response(
            parse_assistant_response(_envelope(
                "*Their ears angle back uneasily.* I can't speak normally—mmph...",
                mode="speech_constrained", spoken="I can't speak normally—mmph...",
            )),
            self.session.effects(),
        )
        self.assertTrue(accurately_constrained.accepted)
        for dialogue, spoken in (
            ("*Mouth shifts slightly* ...uh... can't talk *normally*?",
             "uh... can't talk normally?"),
            ("*Muffled hum...* Uhh... 'cause the user's hand...",
             "Uhh... 'cause the user's hand..."),
            ("*Mouth shifts slightly* ...uh... can't talk *normally*?",
             "uh... can't talk"),
        ):
            response = validate_capability_response(
                parse_assistant_response(_envelope(
                    dialogue, mode="speech_constrained", spoken=spoken,
                )),
                self.session.effects(),
            )
            self.assertTrue(response.accepted, response.category)
        for contradiction in (
            "*Their ears flatten.* I can't speak at all.",
            "*They straighten.* I can speak clearly.",
        ):
            rejected = validate_capability_response(
                parse_assistant_response(_envelope(
                    contradiction, mode="speech_constrained", spoken="Mmph...",
                )),
                self.session.effects(),
            )
            self.assertFalse(rejected.accepted)
            self.assertEqual("speech_capability_contradiction", rejected.category)

        self.session.turn("I cover your mouth with my hand.")
        draft = parse_assistant_response(_envelope(
            "*Their brows lift toward the touch.* "
            "(Caption: Their mouth is covered by the user's hand, so speech is constrained.)",
            mode="nonverbal_reaction", spoken="",
        ))
        normalized = normalize_constrained_caption(draft, self.session.effects())
        self.assertNotEqual(draft.dialogue, normalized.dialogue)
        self.assertTrue(normalized.dialogue.startswith("*"))
        self.assertTrue(normalized.dialogue.endswith("*"))
        unavailable = validate_capability_response(normalized, self.session.effects())
        self.assertTrue(unavailable.accepted)
        self.assertEqual("", unavailable.spoken_text)

    def test_unavailable_vision_rejects_implied_sight_but_allows_darkness(self):
        self.session.turn("You're blindfolded.")
        for dialogue in (
            "I can make out dark blobs.",
            "I can sort of see shapes.",
            "Your face has sharp details.",
            "I can distinguish a red color.",
            "I can't see clearly, but there are vague outlines.",
            "I can make out your silhouette.",
            "Everything is a blur.",
            "I can see dark blobs.",
            "I can sort of tell where you are visually.",
            "Your outline is faint.",
        ):
            with self.subTest(dialogue=dialogue):
                result = validate_capability_response(
                    parse_assistant_response(_envelope(dialogue)),
                    self.session.effects(),
                )
                self.assertFalse(result.accepted)
                self.assertEqual("prohibited_visual_claim", result.category)
        allowed = validate_capability_response(
            parse_assistant_response(_envelope(
                "I can't visually distinguish anything through the darkness."
            )),
            self.session.effects(),
        )
        self.assertTrue(allowed.accepted)

    def test_unavailable_vision_rejects_grounding_variants(self):
        self.session.turn("*I blindfold you.*")
        effects = self.session.effects()
        for dialogue in (
            "*Her gaze darts from you to the pole.*",
            "*She looks down at the cup.* It looks smooth.",
            "*She peers through the fabric toward you.*",
            "The cup reflects light from its surface.",
        ):
            result = validate_capability_response(
                parse_assistant_response(dialogue), effects,
            )
            self.assertFalse(result.accepted, dialogue)
        nonvisual = validate_capability_response(
            parse_assistant_response(_envelope(
                "I can't see, but I can hear you nearby."
            )),
            self.session.effects(),
        )
        self.assertTrue(nonvisual.accepted)
        for rhetorical in (
            "Well, I can't really tell you what I see right now! My vision is unavailable.",
            "Can I see? Well, right now it's unavailable; the blindfold blocks my vision.",
        ):
            response = validate_capability_response(
                parse_assistant_response(_envelope(rhetorical)),
                self.session.effects(),
            )
            self.assertTrue(response.accepted, response.category)

    def test_conversational_prefix_still_derives_direct_vision_requirement(self):
        self.session.turn("You're blindfolded.")
        requirement = derive_response_requirement(
            self.session.repository, self.session.character_id,
            "So what can you see?",
            local_datetime=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
            companion_effects=self.session.effects(),
        )
        self.assertIsNotNone(requirement)
        self.assertEqual("vision", requirement.intent)

    def test_quantity_update_supersedes_relation_and_changes_hand_capability(self):
        self.session.turn("You're carrying a box in one hand.")
        self.assertEqual(self.session.effects().hands_mode, "partially_occupied")
        preview = preview_capability_effects(
            self.session.repository, self.session.character_id,
            "You're carrying two boxes.",
        )
        self.assertTrue(preview.changed_by_current_evidence)
        self.assertEqual(preview.effects.hands_mode, "occupied")
        self.session.turn("You're carrying two boxes.")
        self.assertEqual(self.session.effects().hands_mode, "occupied")
        rows = self.session.writer.store.connection.execute(
            """SELECT quantity, valid_to_us FROM active_scene_relations
                 WHERE character_id=? AND cause='box' ORDER BY valid_from_us""",
            (self.session.character_id,),
        ).fetchall()
        self.assertEqual([row["quantity"] for row in rows], [1, 2])
        self.assertIsNotNone(rows[0]["valid_to_us"])
        self.assertIsNone(rows[1]["valid_to_us"])

    def test_compound_slots_transfer_and_mobility_share_relation_architecture(self):
        self.session.turn("You're wearing a black dress, a red scarf, and boots.")
        self.assertEqual(
            {item.cause for item in self.session.relations() if item.predicate == "wearing"},
            {"black dress", "red scarf", "boots"},
        )
        self.session.turn("Take off the scarf and put on the blue coat.")
        worn = {item.cause for item in self.session.relations() if item.predicate == "wearing"}
        self.assertEqual(worn, {"black dress", "blue coat", "boots"})
        self.session.turn("Take off the boots and put on sneakers.")
        worn = {item.cause for item in self.session.relations() if item.predicate == "wearing"}
        self.assertIn("sneakers", worn)
        self.assertNotIn("boots", worn)

        self.session.turn("I hand you the umbrella.")
        self.session.turn("Give me the umbrella.")
        umbrella = [item for item in self.session.relations() if item.cause == "umbrella"]
        self.assertEqual([(item.target, item.predicate) for item in umbrella], [("user", "holding")])

        self.session.turn("You're wearing rollerblades.")
        self.assertEqual(self.session.effects().locomotion_mode, "skating")
        self.session.turn("You get on a bicycle.")
        self.assertEqual(self.session.effects().locomotion_mode, "cycling")
        self.session.turn("You get off the bicycle.")
        self.assertEqual(self.session.effects().locomotion_mode, "skating")

    def test_destructive_replacement_abstains_atomically_when_new_half_is_invalid(self):
        self.session.turn("You're wearing boots.")
        before = self.session.structural_snapshot()
        result = self.session.turn("Take off the boots and put on maybe sandals.")
        self.assertEqual(result.get("state"), "ignored")
        self.assertEqual(self.session.structural_snapshot(), before)

    def test_stable_subject_has_one_owner_and_one_explicit_location(self):
        self.session.turn("I hand you the umbrella.")
        self.session.turn("Give me the umbrella.")
        current = [item for item in self.session.relations() if item.cause == "umbrella"]
        self.assertEqual([(item.target, item.predicate) for item in current], [("user", "holding")])

        self.session.turn("You're holding a cup.")
        self.session.turn("Put the cup on the table.")
        self.session.turn("I hand you the cup.")
        self.session.turn("Put the cup on the desk.")
        locations = [
            item for item in self.session.relations()
            if item.target_kind == "scene" and item.predicate in {"located_on", "located_in"}
        ]
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].cause, "desk")

    def test_hard_capability_lane_survives_relation_clutter_without_prompt_ids(self):
        self.session.turn("You're blindfolded.")
        self.session.turn("You're holding a cup.")

        labels = tuple(f"detail {index:02d}" for index in range(32))
        content = "Current synthetic details: " + ", ".join(labels) + "."
        event_id = str(uuid.uuid4())
        recorded_at_us = int(
            (datetime(2026, 8, 27, 12, 0, 3, 500000, tzinfo=timezone.utc)).timestamp()
            * 1_000_000
        )
        sequence = self.session.writer.store.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
            (self.session.character_id,),
        ).fetchone()[0]
        self.session.writer.store.add_event(
            self.session.character_id, event_id, sequence,
            event_type="canonical_user_message", actor_kind="user",
            recorded_at_us=recorded_at_us, temporal_precision="instant",
            content_text=content, source_origin="synthetic_test",
            source_reference="synthetic-clutter",
        )
        proposals = tuple(
            SceneRelationProposal(
                "set", "companion", "torso", "covering", "state", label, None,
                0, len(content),
            )
            for label in labels
        )
        self.session.writer.store.apply_scene_relation_proposals(
            self.session.character_id, proposals, evidence_event_id=event_id,
        )
        self.session.turn("Put the cup on the table.")

        self.assertEqual(self.session.effects().vision_mode, "unavailable")
        context = self.session.context("Where is the cup, and can you see?")
        self.assertIn('"vision":"unavailable"', context)
        self.assertIn('"target":"cup"', context)
        self.assertNotIn("scene-", context)

    def test_profile_baseline_is_lower_authority_and_can_be_explicitly_restored(self):
        profile = derive_character_scene_profile({
            "name": "Synthetic", "default_scene": {"worn": ["black dress"]},
        })
        self.assertIsNotNone(profile)
        cache_character_scene_profile(
            self.session.repository, self.session.character_id, profile,
        )
        self.assertEqual(
            tuple(item.label for item in effective_profile_worn_items(
                self.session.repository, self.session.character_id,
            )),
            ("black dress",),
        )
        self.session.turn("You're wearing a blue coat.")
        self.assertEqual(effective_profile_worn_items(
            self.session.repository, self.session.character_id,
        ), ())
        self.session.restart()
        self.assertEqual(effective_profile_worn_items(
            self.session.repository, self.session.character_id,
        ), ())
        self.session.turn("Change back into your usual clothes.")
        self.assertEqual(
            tuple(item.label for item in effective_profile_worn_items(
                self.session.repository, self.session.character_id,
            )),
            ("black dress",),
        )

    def test_profile_default_outfit_free_form_is_conservative_and_versioned(self):
        profile = derive_character_scene_profile(
            {},
            "CHARACTER PERSONALITY:\nTheir default outfit is a black dress and red scarf.\n"
            "CHARACTER CONSISTENCY:",
        )
        self.assertIsNotNone(profile)
        self.assertEqual("derived_stable_personality_form", profile.source)
        self.assertEqual(("black dress", "red scarf"), tuple(
            item.label for item in profile.worn_items
        ))

    def test_companion_action_is_separate_validated_and_companion_only(self):
        parsed = parse_assistant_response(_envelope(
            "", mode="action_decision", spoken="",
            action={"family": "activity", "operation": "set", "value": "stretching"},
        ))
        decision = validate_companion_action_decision(
            parsed, self.session.repository, self.session.character_id, self.session.effects(),
        )
        self.assertTrue(decision.accepted)
        self.assertIsNone(self.session.activity("companion"))
        self.session.turn("Do whatever you'd like.", assistant="*Begins stretching.*")
        self.session.apply_action(decision.plan)
        self.assertEqual(self.session.activity("companion"), "stretching")
        narration = parse_assistant_response(_envelope("*Begins stretching.*"))
        self.assertTrue(action_narration_valid(decision.plan, narration))
        self.assertTrue(action_narration_valid(
            decision.plan, parse_assistant_response(_envelope("*Stretches with a small smile.*")),
        ))
        self.assertTrue(action_narration_valid(
            decision.plan, parse_assistant_response(_envelope("I start to stretch.")),
        ))
        negated = parse_assistant_response(_envelope("*Does not begin stretching.*"))
        self.assertFalse(action_narration_valid(decision.plan, negated))
        self.assertFalse(action_narration_valid(
            decision.plan, parse_assistant_response(_envelope("*Doesn't stretch.*")),
        ))
        self.assertFalse(action_narration_valid(
            decision.plan, parse_assistant_response(_envelope("I don't stretch.")),
        ))
        self.assertFalse(action_narration_valid(
            decision.plan, parse_assistant_response(_envelope("*Starts knitting.*")),
        ))
        for claim in (
            "*Begins stretching.*",
            "*She begins stretching.*",
            "I start stretching.",
            "*The companion sits down.*",
        ):
            self.assertTrue(unauthorized_action_narrated(parse_assistant_response(_envelope(claim))))
        for harmless in (
            "*Tilts her head thoughtfully.*",
            "I haven't decided what to do.",
            "*She does not begin stretching.*",
        ):
            self.assertFalse(unauthorized_action_narrated(parse_assistant_response(_envelope(harmless))))

        applied = self.session.writer.apply_governed_companion_action(
            decision.plan, decision_reference="synthetic-action-1",
            recorded_at_us=int(datetime(2026, 8, 27, 13, tzinfo=timezone.utc).timestamp() * 1_000_000),
        )
        repeated = self.session.writer.apply_governed_companion_action(
            decision.plan, decision_reference="synthetic-action-1",
            recorded_at_us=int(datetime(2026, 8, 27, 13, tzinfo=timezone.utc).timestamp() * 1_000_000),
        )
        self.assertEqual(applied.get("state"), "applied")
        self.assertEqual(repeated.get("state"), "unchanged")

        user_target = json.loads(_envelope(
            "", mode="action_decision", spoken="",
            action={"family": "activity", "operation": "set", "value": "reading"},
        ))
        user_target["companion_action"]["target"] = "user"
        rejected = validate_companion_action_decision(
            parse_assistant_response(json.dumps(user_target)), self.session.repository,
            self.session.character_id, self.session.effects(),
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.category, "action_unknown_field")

        conversational_decision = parse_assistant_response(_envelope(
            "I think I'll stretch.", mode="normal_conversation",
            action={"family": "activity", "operation": "set", "value": "dancing"},
        ))
        normalized = validate_companion_action_decision(
            conversational_decision, self.session.repository,
            self.session.character_id, self.session.effects(),
        )
        self.assertTrue(normalized.accepted)
        self.assertEqual(normalized.category, "accepted_discarded_prose")

        for value, category in (
            ("deleting files", "action_world_mutation"),
            ("skating", "action_locomotion_conflict"),
        ):
            unsafe = validate_companion_action_decision(
                parse_assistant_response(_envelope(
                    "", mode="action_decision", spoken="",
                    action={"family": "activity", "operation": "set", "value": value},
                )),
                self.session.repository, self.session.character_id, self.session.effects(),
            )
            self.assertFalse(unsafe.accepted)
            self.assertEqual(unsafe.category, category)

    def test_service_applies_action_event_before_persisting_success_narration(self):
        conversation = Conversation(
            object(), conversation_file=self.session.conversation_file,
            summary_file=self.session.root / "summary.json", clock=lambda: BASE_TIME,
        )
        memory, llm = _ServiceMemory(), _ServiceLlm()
        memory.llm = llm
        self.session.writer.compare = lambda *_args, **_kwargs: {}
        application_roles = []
        original_apply = self.session.writer.apply_governed_companion_action

        def observed_apply(*args, **kwargs):
            application_roles.append(tuple(
                item.get("role") for item in conversation.messages if isinstance(item, dict)
            ))
            return original_apply(*args, **kwargs)

        self.session.writer.apply_governed_companion_action = observed_apply
        final = _envelope("*Begins stretching.*", spoken="")
        service = AssistantService(
            llm, memory, conversation, object(),
            {"_character_id": self.session.character_id}, "character", _ServiceTts(),
            response_generator=lambda *_args: final,
            memory_v2_shadow_writer=self.session.writer,
            character_id=self.session.character_id,
        )
        result = service.process_text_turn("Do whatever you'd like.", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(result.reply, "*Begins stretching.*")
        self.assertEqual(application_roles, [("user",)])
        state = self.session.repository.lookup_actor_state(
            self.session.character_id, "companion", "activity",
        ).state
        self.assertEqual(state.value, "stretching")
        self.assertEqual(len(state.evidence_event_ids), 1)
        event = self.session.writer.store.connection.execute(
            "SELECT content_text, source_origin FROM events WHERE character_id=? AND event_id=?",
            (self.session.character_id, state.evidence_event_ids[0]),
        ).fetchone()
        self.assertEqual(event["source_origin"], "governed_companion_action")
        self.assertNotEqual(event["content_text"], result.reply)

    def test_service_suppresses_success_narration_when_action_application_fails(self):
        conversation = Conversation(
            object(), conversation_file=self.session.conversation_file,
            summary_file=self.session.root / "summary.json", clock=lambda: BASE_TIME,
        )
        memory, llm = _ServiceMemory(), _ServiceLlm()
        memory.llm = llm
        self.session.writer.compare = lambda *_args, **_kwargs: {}
        self.session.writer.apply_governed_companion_action = (
            lambda *_args, **_kwargs: {"state": "failed", "reason": "synthetic_failure"}
        )
        service = AssistantService(
            llm, memory, conversation, object(),
            {"_character_id": self.session.character_id}, "character", _ServiceTts(),
            response_generator=lambda *_args: _envelope("*Begins stretching.*", spoken=""),
            memory_v2_shadow_writer=self.session.writer,
            character_id=self.session.character_id,
        )
        result = service.process_text_turn("Do whatever you'd like.", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertNotIn("stretch", result.reply.casefold())
        self.assertIsNone(self.session.activity("companion"))

    def test_service_suppresses_third_person_narration_of_rejected_action(self):
        conversation = Conversation(
            object(), conversation_file=self.session.conversation_file,
            summary_file=self.session.root / "summary.json", clock=lambda: BASE_TIME,
        )
        memory, llm = _ServiceMemory(), _ServiceLlm(action_value="skating")
        memory.llm = llm
        self.session.writer.compare = lambda *_args, **_kwargs: {}
        service = AssistantService(
            llm, memory, conversation, object(),
            {"_character_id": self.session.character_id}, "character", _ServiceTts(),
            response_generator=lambda *_args: _envelope("*She begins skating.*", spoken=""),
            memory_v2_shadow_writer=self.session.writer,
            character_id=self.session.character_id,
        )
        result = service.process_text_turn("Do whatever you'd like.", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertNotIn("skating", result.reply.casefold())
        self.assertIsNone(self.session.activity("companion"))

    def test_service_same_turn_correction_enforces_sleep_before_storage_catches_up(self):
        self.session.turn("Go work.")
        conversation = Conversation(
            object(), conversation_file=self.session.conversation_file,
            summary_file=self.session.root / "summary.json", clock=lambda: BASE_TIME,
        )
        memory, llm = _ServiceMemory(), _ServiceLlm()
        memory.llm = llm
        self.session.writer.compare = lambda *_args, **_kwargs: {}
        service = AssistantService(
            llm, memory, conversation, object(),
            {"_character_id": self.session.character_id}, "character", _ServiceTts(),
            response_generator=lambda *_args: _envelope(
                "I'm fully awake and ready to answer.", spoken="I'm fully awake and ready to answer.",
            ),
            memory_v2_shadow_writer=self.session.writer,
            character_id=self.session.character_id,
        )
        result = service.process_text_turn(
            "That was a typo. I meant sleeping.", speak=False,
        )
        self.assertTrue(result.succeeded, result.error)
        self.assertNotIn("fully awake", result.reply.casefold())
        self.assertEqual(result.presentation.pose, "sleeping")
        self.assertEqual(result.presentation.gaze_mode, "suppressed")
        self.assertEqual(self.session.activity("companion"), "sleeping")

    def test_direct_requirements_reject_contradictions_and_do_not_mutate(self):
        self.session.turn("You're wearing rollerblades.")
        requirement = derive_response_requirement(
            self.session.repository, self.session.character_id, "How are you moving?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertIsNotNone(requirement)
        self.assertTrue(validate_response_requirement(
            requirement, requirement.fallback_dialogue,
        ).accepted)
        self.assertFalse(validate_response_requirement(
            requirement, "I'm walking normally.",
        ).accepted)
        before = self.session.structural_snapshot()
        self.session.turn("How are you moving?")
        self.assertEqual(self.session.structural_snapshot(), before)

    def test_attire_query_keeps_unknown_distinct_from_none_and_includes_attached_equipment(self):
        unknown = derive_response_requirement(
            self.session.repository, self.session.character_id, "What are you wearing?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertEqual("unknown", unknown.facts[0].value)
        self.assertIn("isn't explicitly established", unknown.fallback_dialogue)
        self.assertTrue(validate_response_requirement(
            unknown, "My current attire isn't explicitly established.",
        ).accepted)
        self.assertFalse(validate_response_requirement(
            unknown, "I'm not wearing anything.",
        ).accepted)

        self.session.turn("I blindfold you.")
        equipped = derive_response_requirement(
            self.session.repository, self.session.character_id, "What are you wearing?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertIn("blindfold", tuple(fact.value for fact in equipped.facts))
        self.assertIn("blindfold", equipped.fallback_dialogue)

    def test_mutation_requirement_prevents_contradiction_without_demanding_restatement(self):
        preview = preview_capability_effects(
            self.session.repository, self.session.character_id,
            "I put a blue hat on you.",
        )
        requirement = derive_mutation_response_requirement(preview.extraction)
        self.assertEqual("must_respect", requirement.mode)
        self.assertTrue(validate_response_requirement(
            requirement, "Oh, that's cute! How does it look?",
        ).accepted)
        self.assertFalse(validate_response_requirement(
            requirement, "I'm not wearing a hat.",
        ).accepted)

        self.session.turn("I put a blue hat on you.")
        clear_preview = preview_capability_effects(
            self.session.repository, self.session.character_id, "Take off the hat.",
        )
        self.assertTrue(clear_preview.changed_by_current_evidence)
        self.assertEqual("mutation_remove_worn", derive_mutation_response_requirement(
            clear_preview.extraction,
        ).intent)

        restraint_preview = preview_capability_effects(
            self.session.repository, self.session.character_id,
            "Your left wrist is handcuffed to a pole.",
        )
        restraint_requirement = derive_mutation_response_requirement(restraint_preview.extraction)
        self.assertEqual("mutation_scene_relation", restraint_requirement.intent)
        self.assertTrue(validate_response_requirement(
            restraint_requirement, "*Tests the limited reach with a careful tug.*",
        ).accepted)
        self.assertFalse(validate_response_requirement(
            restraint_requirement, "What do you mean? That didn't happen.",
        ).accepted)

    def test_requirements_accept_natural_equivalents_without_accepting_contradictions(self):
        self.session.turn("You're holding a cup and a book.")
        hands = derive_response_requirement(
            self.session.repository, self.session.character_id, "Are your hands free?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertTrue(validate_response_requirement(
            hands, "They're currently holding a book and this little cup.",
        ).accepted)
        self.assertFalse(validate_response_requirement(
            hands, "I'm holding a book and a cup, but both hands are free.",
        ).accepted)

        date = derive_response_requirement(
            self.session.repository, self.session.character_id, "What day is it?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertTrue(validate_response_requirement(
            date, "It's Thursday, August 27th, 2026.",
        ).accepted)

        self.session.turn("I blindfold you.")
        vision = derive_response_requirement(
            self.session.repository, self.session.character_id, "Can you see?",
            local_datetime=datetime(2026, 8, 27, 13, 1),
            companion_effects=self.session.effects(),
        )
        self.assertTrue(validate_response_requirement(
            vision, "Can I see? Well, right now... it's unavailable.",
        ).accepted)

        self.session.turn("You're holding a plate.")
        self.session.turn("Give me the plate.")
        release_preview = preview_capability_effects(
            self.session.repository, self.session.character_id,
            "I put the plate on the table.",
        )
        release = derive_mutation_response_requirement(release_preview.extraction)
        self.assertTrue(validate_response_requirement(
            release, "There! I put the plate down on the table.",
        ).accepted)

    def test_capability_validation_drives_semantic_presentation(self):
        self.session.turn("You're blindfolded and carrying two boxes.")
        parsed = parse_assistant_response(_envelope(
            "*Tilts their head toward the sound.*",
            mode="constrained_reaction", spoken="",
            presentation={"emotion": "surprised", "gesture": "greeting"},
        ))
        validation = validate_capability_response(parsed, self.session.effects())
        self.assertTrue(validation.accepted)
        self.assertEqual(validation.presentation.gaze_mode, "suppressed")
        self.assertIsNone(validation.presentation.gesture)
        self.assertEqual(validation.presentation.hands_mode, "occupied")
        self.assertEqual(validation.presentation.vision_mode, "unavailable")

        visual_claim = parse_assistant_response(_envelope(
            "I can see you clearly.", mode="constrained_reaction", spoken="I can see you clearly.",
        ))
        rejected = validate_capability_response(visual_claim, self.session.effects())
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.category, "prohibited_visual_claim")

        clear_caption_muffled_audio = parse_assistant_response(_envelope(
            "I can answer you clearly.", mode="speech_constrained", spoken="Mmph.",
        ))
        self.session.turn("Your mouth is full.")
        rejected_projection = validate_capability_response(
            clear_caption_muffled_audio, self.session.effects(),
        )
        self.assertFalse(rejected_projection.accepted)
        self.assertEqual(rejected_projection.category, "speech_constraint_fluency")

        creative_one_word_beat = parse_assistant_response(
            "*Flinches* Mmph..."
        )
        accepted_beat = validate_capability_response(
            creative_one_word_beat, self.session.effects(),
        )
        self.assertTrue(accepted_beat.accepted)
        self.assertEqual("Mmph...", accepted_beat.spoken_text)

        self.session.turn("Your mouth is clear now.")
        self.session.turn("I take the blindfold off you.")
        restored = validate_capability_response(
            parse_assistant_response(_envelope("Hello.", spoken="Hello.")),
            self.session.effects(),
        )
        self.assertTrue(restored.accepted)
        self.assertEqual(restored.presentation.gaze_mode, "normal")

    def test_constrained_repair_preserves_creative_actions_and_drops_fluent_tail(self):
        self.session.turn("I cover your mouth with my hand.")
        parsed = parse_assistant_response(
            "*Their ears flatten and shoulders tense.* I can give you a detailed explanation now."
        )
        normalized = normalize_constrained_caption(parsed, self.session.effects())
        self.assertIn("ears flatten", normalized.dialogue)
        self.assertNotIn("detailed explanation", normalized.dialogue)
        self.assertEqual("", normalized.spoken_content)
        self.assertTrue(
            validate_capability_response(normalized, self.session.effects()).accepted
        )


if __name__ == "__main__":
    unittest.main()
