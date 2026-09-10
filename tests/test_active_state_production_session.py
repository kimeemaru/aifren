from datetime import datetime, timedelta, timezone
import json
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from benchmarks.active_state.production_session import (
    BASE_TIME,
    ProductionSession,
    SyntheticLifecycleTts,
    response_envelope,
)


class _SyntheticCudaOom(MemoryError):
    pass


class _ResourcePressureTts(SyntheticLifecycleTts):
    def __init__(self):
        super().__init__()
        self.device = "cuda"
        self.prepare_devices = []
        self.cpu_fallbacks = 0
        self.started_prepared = []

    def prepare_stream_chunk(self, text):
        self.prepare_devices.append(self.device)
        if self.device == "cuda":
            raise _SyntheticCudaOom("synthetic CUDA out of memory")
        return (str(text), self.device)

    def fallback_to_cpu_after_resource_failure(self):
        self.cpu_fallbacks += 1
        self.device = "cpu"
        return True

    def start_prepared_chunk(self, prepared):
        self.started_prepared.append(prepared)
        return super().speak(prepared[0])


class ActiveStateProductionSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = ProductionSession(self.id())

    def tearDown(self):
        self.session.close()

    def test_smart_apostrophe_multi_item_outfit_uses_full_production_path(self):
        result = self.session.turn(
            "You’re wearing a blue hat and a red scarf.",
            response_envelope("*Adjusts the hat with a pleased little nod.*"),
        )
        self.assertIsNone(result.error)
        worn = {
            relation.cause for relation in self.session.relations()
            if relation.target == "companion" and relation.predicate == "wearing"
        }
        self.assertEqual({"blue hat", "red scarf"}, worn)
        snapshot = self.session.service.continuity_snapshot()
        self.assertEqual(2, len([
            row for row in snapshot["scene_relations"] if row["predicate"] == "wearing"
        ]))

    def test_exact_manual_blindfold_application_refines_one_logical_relation(self):
        self.session.turn(
            "*I put a blindfold on you.*",
            response_envelope("*Holds still as it settles over their eyes.*"),
        )
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        first = [row for row in self.session.relations() if row.cause == "blindfold"]
        self.assertEqual(1, len(first))
        self.assertEqual(("eyes", "covered_by"), (first[0].facet, first[0].predicate))

        self.session.turn(
            "*The blindfold covers your eyes.*",
            response_envelope("*Tilts their head without trying to look through it.*"),
        )
        refined = [row for row in self.session.relations() if row.cause == "blindfold"]
        self.assertEqual(1, len(refined))
        self.assertEqual(first[0].cause_subject_id, refined[0].cause_subject_id)

    def test_hearing_unavailable_retains_canonical_but_blocks_semantic_admission(self):
        processed = []
        self.session.memory.process = lambda user, assistant: processed.append((user, assistant))
        self.session.turn(
            "The music is so loud you can't hear me.",
            response_envelope("*Winces as the noise swallows the room.*"),
        )
        for text in ("I hid the cookie under the pillow.", "Where did I hide the cookie?"):
            self.session.turn(text, response_envelope("*Looks over with visible confusion.*"))
        inaccessible = self.session.conversation.messages[-4]
        self.assertEqual("I hid the cookie under the pillow.", inaccessible["content"])
        self.assertFalse(inaccessible["semantic_admission"]["understood"])
        self.assertFalse(any("pillow" in user for user, _ in processed))
        derived = self.session.writer.store.connection.execute(
            "SELECT content_text FROM events WHERE character_id=? AND content_text LIKE '%pillow%'",
            (self.session.character_id,),
        ).fetchall()
        self.assertEqual([], derived)

        self.session.turn("The music stops.", response_envelope("I can hear you again."))
        context = self.session.conversation.build_context(
            self.session.memory, "What happens next?",
            active_truth_scope=self.session.service.truth_scope_provenance(),
        )
        rendered = "\n".join(str(row.get("content", "")) for row in context)
        self.assertNotIn("under the pillow", rendered)
        self.assertIn("semantic content was not available", rendered)
        self.session.turn("I hid it under the blanket.", response_envelope("I'll remember the blanket."))
        self.assertTrue(any("blanket" in user for user, _ in processed))

    def test_fifty_inaudible_turns_never_enter_understood_derivatives(self):
        processed = []
        self.session.memory.process = lambda user, assistant: processed.append((user, assistant))
        self.session.turn(
            "The music is so loud you can't hear me.",
            response_envelope("*Turns toward the movement despite the noise.*"),
        )
        for index in range(50):
            text = f"I quietly tell you synthetic secret number {index}."
            result = self.session.turn(
                text, response_envelope("*Watches attentively, unable to make out the words.*"),
            )
            self.assertIsNone(result.error)
            record = self.session.conversation.messages[-2]
            self.assertEqual(text, record["content"])
            self.assertFalse(record["semantic_admission"]["understood"])
        self.assertFalse(any("synthetic secret" in user for user, _ in processed))
        count = self.session.writer.store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE character_id=? AND content_text LIKE '%synthetic secret%'",
            (self.session.character_id,),
        ).fetchone()[0]
        self.assertEqual(0, count)

    def test_post_mutation_color_contradiction_requires_repair_without_restatement(self):
        self.session.provider.queue_bounded((response_envelope("That combination suits you nicely."),))
        result = self.session.turn(
            "You're wearing a blue hat and a red scarf.",
            response_envelope("*Touches the green hat and purple scarf.*"),
        )
        self.assertNotIn("green hat", result.reply.casefold())
        self.assertNotIn("purple scarf", result.reply.casefold())

    def test_response_length_ceiling_applies_only_to_constrained_modes(self):
        long_ordinary = " ".join(f"ordinary{index}" for index in range(110))
        ordinary = self.session.turn("Tell me a story.", response_envelope(long_ordinary))
        self.assertEqual(long_ordinary, ordinary.reply)

        self.session.turn(
            "I cover your mouth with my hand.",
            response_envelope("*Their ears flick as the hand settles.*", mode="speech_constrained"),
        )
        long_constrained = "*" + " ".join(f"motion{index}" for index in range(110)) + "*"
        concise_repair = "*Their shoulders tense, then ease into a concise nonverbal response.*"
        constrained = self.session.turn(
            "How do you react?",
            response_envelope(long_constrained, mode="speech_constrained", spoken=""),
            response_envelope(concise_repair, mode="nonverbal_reaction", spoken=""),
        )
        self.assertEqual(concise_repair, constrained.reply)

    def test_compound_manual_direct_query_builds_all_authoritative_requirements(self):
        self.session.turn(
            "You're wearing a blue hat and a red scarf.",
            response_envelope("*Adjusts the outfit.*"),
        )
        self.session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        query = (
            "What are you wearing? What can you see? Can you hear me? "
            "What are you holding? What are you doing right now?"
        )
        policy = self.session.service._response_policy(query)
        self.assertEqual("compound_direct", policy.requirement.intent)
        values = {fact.value for fact in policy.requirement.facts}
        self.assertIn("blue hat", values)
        self.assertIn("red scarf", values)
        self.assertIn("unavailable", values)
        self.assertIn("normal", values)

    def test_explicit_tether_consequence_strengthens_only_locomotion(self):
        self.session.turn(
            "Your left wrist is handcuffed to the pole.",
            response_envelope("*Tests the restraint carefully.*"),
        )
        before = self.session.effects()
        self.assertEqual("constrained", before.locomotion_constraint)
        self.session.turn("You can't move away.", response_envelope("*Stays close to the pole.*"))
        after = self.session.effects()
        self.assertEqual("unavailable", after.locomotion_constraint)
        self.assertEqual("normal", after.speech_mode)
        self.assertEqual("available", after.vision_mode)

    def test_scope_transition_assistant_response_is_persisted_in_new_scope(self):
        self.session.turn(
            "Let's roleplay that we're in the Labyrinth.",
            response_envelope("*Steps into the Labyrinth.*"),
        )
        self.assertEqual("scenario", self.session.service.truth_scope_status()["kind"])
        self.session.turn("You're wearing a silver crown.", response_envelope("*Adjusts it.*"))
        result = self.session.turn("Back to real life.", response_envelope("We're back."))
        self.assertEqual("We're back.", result.reply)
        self.assertEqual("real_world", self.session.service.truth_scope_status()["kind"])
        self.assertEqual("real_world", self.session.conversation.messages[-1]["truth_scope"]["kind"])
        self.assertFalse(any(row.cause == "silver crown" for row in self.session.relations()))

    def test_exact_manual_smoke_mouth_obstruction_phrases_use_speech_authority(self):
        cases = (
            ("*I plug your mouth with a sock.*", "unavailable"),
            ("You're wearing a sock in your mouth.", "constrained"),
            ("The sock is in your mouth. You can't talk.", "unavailable"),
            ("*The sock is preventing you from speaking.*", "unavailable"),
            ("A sock is in your mouth.", "constrained"),
            ("You're wearing a gag.", "unavailable"),
            ("Yeah you can't talk now. The sock is stuck in your mouth.", "unavailable"),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                session = ProductionSession(f"manual-mouth-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts without clear speech.*", mode="nonverbal_reaction"))
                self.assertEqual(expected, session.effects().speech_mode)
                relations = [row for row in session.relations() if row.semantic_family == "speech_obstruction"]
                self.assertEqual(1, len(relations))
                self.assertEqual("mouth", relations[0].facet)

    def test_exact_manual_smoke_wrist_wear_action_and_unique_hat_corrections(self):
        self.session.turn("You're wearing a blue hat.", response_envelope("*Adjusts it.*"))
        self.session.turn("Actually, it's a green hat.", response_envelope("*Nods.*"))
        self.assertEqual(
            {"green hat"},
            {row.cause for row in self.session.relations() if row.predicate == "wearing"},
        )
        self.session.turn("The hat is blue now.", response_envelope("*Nods again.*"))
        self.assertEqual(
            {"blue hat"},
            {row.cause for row in self.session.relations() if row.predicate == "wearing"},
        )
        self.session.turn(
            "*I put the blue and purple sparkly scrunchie on your wrist.*",
            response_envelope("*Turns their wrist.*"),
        )
        self.assertTrue(any(
            row.predicate == "wearing" and row.facet == "wrists" and "scrunchie" in row.cause
            for row in self.session.relations()
        ))

    def test_exact_scrunchie_overlay_clear_uses_wearing_predicate_not_wrist_facet(self):
        self.session.turn(
            "*I put the blue and purple sparkly scrunchie on your wrist.*",
            response_envelope("*Turns their wrist.*"),
        )
        snapshot = self.session.service.continuity_snapshot()
        row = next(item for item in snapshot["scene_relations"] if "scrunchie" in item["cause"])
        self.session.script.queue((response_envelope("*Flexes their now-bare wrist.*"),))
        result = self.session.service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=snapshot["revision"], action_token=row["clear_token"],
        )
        self.assertEqual("applied", result["outcome"])
        event = self.session.conversation.messages[-2]
        self.assertEqual("*I take off your blue and purple sparkly scrunchie.*", event["content"])
        self.assertNotIn("restraint", event["content"])

    def test_exact_manual_negative_wear_statement_cannot_trigger_visual_hallucination(self):
        result = self.session.turn(
            "you aren't wearing any blindfold.",
            response_envelope("I peer through the blindfold at your wet socks."),
            response_envelope("*Nods, eyes unobstructed.*"),
        )
        self.assertNotIn("through the blindfold", result.reply.casefold())
        self.assertEqual("available", self.session.effects().vision_mode)

    def test_overlay_clear_is_post_mutation_in_world_event_while_admin_clear_stays_silent(self):
        reaction = response_envelope("*Tilts toward the touch still covering their eyes.*")
        self.session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        self.session.turn("I cover your eyes with my hands.", response_envelope("*Tilts their head.*"))
        before = len(self.session.conversation.messages)
        artifact_start = len(self.session.artifacts)
        snapshot = self.session.service.continuity_snapshot()
        blindfold = next(row for row in snapshot["scene_relations"] if row["cause"] == "blindfold")
        self.session.script.queue((reaction,))
        result = self.session.service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=snapshot["revision"], action_token=blindfold["clear_token"],
        )
        self.assertEqual("applied", result["outcome"])
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.assertEqual(before + 2, len(self.session.conversation.messages))
        user_event = self.session.conversation.messages[-2]
        self.assertEqual("*I take off your blindfold.*", user_event["content"])
        self.assertEqual("scene_ui", user_event["origin"]["kind"])
        self.assertTrue(user_event["origin"]["generated_event"])
        self.assertEqual(1, sum(
            1 for record in self.session.conversation.messages[before:]
            if record.get("origin", {}).get("kind") == "scene_ui"
        ))
        emitted = [artifact["event"]["type"] for artifact in self.session.artifacts[artifact_start:]]
        self.assertEqual(1, emitted.count("continuity_changed"))
        self.assertEqual(1, emitted.count("turn_started"))
        self.assertLess(emitted.index("continuity_changed"), emitted.index("turn_started"))
        self.assertEqual("scene_ui", self.session.reducer.state.active_origin)

        remaining_snapshot = result["continuity"]
        hands = next(row for row in remaining_snapshot["scene_relations"] if row["facet"] == "eyes")
        self.session.script.queue((response_envelope("*Blinks and looks at you again.*"),))
        final = self.session.service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="interact_scene_relation",
            expected_revision=remaining_snapshot["revision"], action_token=hands["clear_token"],
        )
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual("*I move my hands away from your eyes.*",
                         self.session.conversation.messages[-2]["content"])

        self.session.turn("I cover your mouth with my hand.", response_envelope("*Their ears flick.*", mode="speech_constrained"))
        silent_before = list(self.session.conversation.messages)
        admin_snapshot = self.session.service.continuity_snapshot()
        mouth = next(row for row in admin_snapshot["scene_relations"] if row["facet"] == "mouth")
        silent = self.session.service.apply_continuity_control(
            command_id=str(uuid.uuid4()), action="clear_scene_relation",
            expected_revision=admin_snapshot["revision"], action_token=mouth["clear_token"],
        )
        self.assertEqual("normal", self.session.effects().speech_mode)
        self.assertEqual(silent_before, self.session.conversation.messages)
        self.assertNotIn("reaction", silent)

    def test_rapid_overlay_clears_cancel_stale_reaction_and_preserve_both_mutations(self):
        self.session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        self.session.turn("I cover your eyes with my hands.", response_envelope("*Tilts their head.*"))
        first_started = threading.Event()
        release_first = threading.Event()

        def generated(_llm, _conversation, _memory, user, _prompt):
            if "blindfold" in user:
                first_started.set()
                release_first.wait(2)
                return response_envelope("I can see perfectly now.")
            return response_envelope("*Blinks after the final obstruction is gone.*")

        self.session.service._response_generator = generated
        initial = self.session.service.continuity_snapshot()
        blindfold = next(row for row in initial["scene_relations"] if row["cause"] == "blindfold")
        results = {}

        first = threading.Thread(target=lambda: results.setdefault("first",
            self.session.service.apply_continuity_control(
                command_id=str(uuid.uuid4()), action="interact_scene_relation",
                expected_revision=initial["revision"], action_token=blindfold["clear_token"],
            )))
        first.start()
        self.assertTrue(first_started.wait(2))
        after_first = self.session.service.continuity_snapshot()
        hands = next(row for row in after_first["scene_relations"] if row["facet"] == "eyes")
        second = threading.Thread(target=lambda: results.setdefault("second",
            self.session.service.apply_continuity_control(
                command_id=str(uuid.uuid4()), action="interact_scene_relation",
                expected_revision=after_first["revision"], action_token=hands["clear_token"],
            )))
        second.start()
        time.sleep(.05)
        release_first.set()
        first.join(3)
        second.join(3)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertFalse(any(row.facet == "eyes" for row in self.session.relations()))
        self.assertNotIn("see perfectly", self.session.reducer.state.dialogue.casefold())
        self.assertEqual(0, self.session.reducer.phantom_turns)
        scene_events = [record for record in self.session.conversation.messages
                        if record.get("origin", {}).get("kind") == "scene_ui"]
        self.assertEqual(2, len(scene_events))

    def test_production_response_projection_removes_assistant_emoji_and_nested_action_audio(self):
        user = "Hello 😊 — こんにちは。"
        result = self.session.turn(
            user,
            response_envelope(
                "*I slowly *really* lean closer.* Hello 😊 — こんにちは。 ✨"
            ),
            speak=True,
        )
        self.assertEqual("*I slowly *really* lean closer.* Hello — こんにちは。", result.reply)
        self.assertEqual("Hello — こんにちは。", result.spoken_text)
        self.assertEqual("Hello — こんにちは。", self.session.tts.spoken[-1])
        self.assertEqual(user, self.session.conversation.messages[-2]["content"])
        self.assertNotIn("😊", self.session.conversation.messages[-1]["content"])

    def test_multi_item_outfit_natural_variants_share_one_batch_contract(self):
        variants = (
            "You are wearing a blue hat and a red scarf.",
            "You've got a blue hat and a red scarf on.",
            "You're wearing a blue hat and a red scarf right now.",
            "Look, your outfit includes a blue hat and a red scarf.",
            "Seriously, you're sporting a blue hat and a red scarf.",
            "Okay, you’re wearing a blue hat, red scarf, and black boots.",
            "*You’re wearing a blue hat and a red scarf.*",
        )
        for index, text in enumerate(variants):
            if index:
                self.session.turn(
                    "Take off everything you're wearing.",
                    response_envelope("*Sets the outfit aside.*"),
                )
            self.session.turn(text, response_envelope("*Adjusts the outfit.*"))
            worn = {
                relation.cause for relation in self.session.relations()
                if relation.predicate == "wearing"
            }
            self.assertIn("blue hat", worn, text)
            self.assertIn("red scarf", worn, text)
            if "boots" in text:
                self.assertIn("black boots", worn)

    def test_pre_manual_outfit_barrage_keeps_exact_items_through_direct_query(self):
        cases = (
            ("You're wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You’re wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You are wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You've got a blue hat and a red scarf on.", ("blue hat", "red scarf")),
            ("You’ve got a blue hat and a red scarf on.", ("blue hat", "red scarf")),
            ("Right now, you're wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Okay, you're wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Well, you are wearing a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Your outfit is a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Your outfit includes a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Your attire consists of a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("Your ensemble includes a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You're sporting a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You currently wear a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You have on a blue hat and a red scarf.", ("blue hat", "red scarf")),
            ("You got a blue hat and a red scarf on.", ("blue hat", "red scarf")),
            ("*You're wearing a blue hat and a red scarf.*", ("blue hat", "red scarf")),
            ("*I put a blue hat and a red scarf on you.*", ("blue hat", "red scarf")),
            ("You're wearing a red hat, red scarf, and black boots.",
             ("red hat", "red scarf", "black boots")),
            ("You’re wearing a blue hat, a red scarf, and black boots.",
             ("blue hat", "red scarf", "black boots")),
            ("You've got a blue hat, red scarf, black boots, and a white shirt on.",
             ("blue hat", "red scarf", "black boots", "white shirt")),
            ("Your outfit includes a blue hat, red scarf, black boots, white shirt, and gold belt.",
             ("blue hat", "red scarf", "black boots", "white shirt", "gold belt")),
            ("Your attire is a blue hat, red scarf, black boots, white shirt, gold belt, and silver bracelet.",
             ("blue hat", "red scarf", "black boots", "white shirt", "gold belt", "silver bracelet")),
            ("Actually, you're wearing a dark blue hat and a light red scarf.",
             ("dark blue hat", "light red scarf")),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                session = ProductionSession(f"outfit-barrage-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Adjusts the outfit naturally.*"))
                worn = tuple(sorted(
                    row.cause for row in session.relations() if row.predicate == "wearing"
                ))
                self.assertEqual(tuple(sorted(expected)), worn)
                answer = "I'm wearing " + ", ".join(expected) + "."
                query = session.turn("What are you wearing?", response_envelope(answer))
                for label in expected:
                    self.assertIn(label, query.reply.casefold())

    def test_safe_surface_modifiers_and_progressive_actions_reach_scene_authority(self):
        self.session.turn(
            "Currently, your left wrist's handcuffed to the pole right now.",
            response_envelope("*Tests the cuff.*"),
        )
        self.assertTrue(any(
            row.predicate == "tethered_to" and row.side == "left"
            for row in self.session.relations()
        ))
        self.session.turn(
            "I'm covering your mouth with my hand now.",
            response_envelope("*Their ears twitch.*", mode="speech_constrained"),
        )
        self.assertEqual("constrained", self.session.effects().speech_mode)
        self.session.turn(
            "I'm placing the blindfold over your eyes at the moment.",
            response_envelope("*Tilts their head.*"),
        )
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.session.turn("I hand you a cup.", response_envelope("*Takes it.*"))
        self.session.turn("Put down the cup, please.", response_envelope("*Sets it down.*"))
        self.assertFalse(any(
            row.predicate in {"holding", "carrying"} and row.cause == "cup"
            for row in self.session.relations()
        ))

    def test_generated_user_surface_families_keep_actor_and_relation_semantics(self):
        self.session.turn(
            "Your attire consists of a blue hat and a red scarf, you know.",
            response_envelope("*Adjusts the outfit.*"),
        )
        self.assertEqual(
            {"blue hat", "red scarf"},
            {row.cause for row in self.session.relations() if row.predicate == "wearing"},
        )
        self.session.turn(
            "For goodness sake, your music is so loud that you absolutely cannot hear me.",
            response_envelope("*Tilts their head.*"),
        )
        self.assertEqual("unavailable", self.session.effects().hearing_mode)
        self.session.turn(
            "The intense music quieted down; your noise level is now much lower.",
            response_envelope("*Their ears relax.*"),
        )
        self.assertEqual("normal", self.session.effects().hearing_mode)
        self.session.turn(
            "Your mouth is being covered by my hand.",
            response_envelope("*Their eyes widen.*", mode="speech_constrained"),
        )
        self.assertEqual("constrained", self.session.effects().speech_mode)
        self.session.turn(
            "I have pulled my hand away from your mouth.",
            response_envelope("*Takes a breath.*"),
        )
        self.assertEqual("normal", self.session.effects().speech_mode)
        self.session.turn(
            "I've put the blindfold over your eyes right now.",
            response_envelope("*Holds still.*"),
        )
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.session.turn(
            "Go on and take off your blindfold from your eyes.",
            response_envelope("*Lifts it away.*"),
        )
        self.assertEqual("available", self.session.effects().vision_mode)
        self.session.turn(
            "I hand you this cup, so you currently hold the cup.",
            response_envelope("*Accepts it.*"),
        )
        self.assertTrue(any(
            row.predicate == "holding" and row.cause == "cup"
            for row in self.session.relations()
        ))
        self.session.turn(
            "You, put down your cup immediately.",
            response_envelope("*Sets it down.*"),
        )
        self.assertFalse(any(
            row.predicate in {"holding", "carrying"} and row.cause == "cup"
            for row in self.session.relations()
        ))

    def test_semantic_clause_shapes_cover_chat_order_without_admitting_negation(self):
        positive = (
            "Your blue hat and your red scarf, you are currently sporting them.",
            "I'm cuffing your left wrist to the pole, you know.",
            "Here, I've got my hand covering your mouth.",
            "I'm removing my hand and uncovering your mouth right now.",
            "I've put the blindfold across your eyes now.",
            "Take this cup from me; your hands hold the cup right now.",
            "You gotta put the cup you're holding down now.",
        )
        for text in positive:
            if "removing my hand" in text:
                self.session.turn(
                    "I cover your mouth with my hand.",
                    response_envelope("*Holds still.*", mode="speech_constrained"),
                )
            if "put the cup" in text:
                self.session.turn("I hand you a cup.", response_envelope("*Takes it.*"))
            before = self.session.service.continuity_snapshot()["revision"]
            self.session.turn(text, response_envelope("*Acknowledges.*"))
            self.assertNotEqual(before, self.session.service.continuity_snapshot()["revision"], text)

        for text in (
            "Your left wrist is not handcuffed to the pole.",
            "Do not cover your mouth with my hand.",
            "Don't put the blindfold over your eyes.",
            "Do not remove the blindfold from your eyes.",
            "Would you put the cup down?",
        ):
            before = self.session.service.continuity_snapshot()["revision"]
            self.session.turn(text, response_envelope("*Acknowledges.*"))
            self.assertEqual(before, self.session.service.continuity_snapshot()["revision"], text)

    def test_generated_manual_surface_boundaries_reach_the_same_authority(self):
        outfit_variants = (
            "It is true that you wear a blue hat and a red scarf.",
            "You are definitely wearing a blue hat and a red scarf now.",
            "Got it, you're in the blue hat and red scarf.",
            "Your attire shows you're sporting a blue hat and that red scarf.",
            "You got a blue hat and a red scarf on yourself.",
            "Look, you got a blue hat and your red scarf on.",
        )
        for index, text in enumerate(outfit_variants):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-outfit-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts.*"))
                self.assertGreaterEqual(
                    {row.cause for row in session.relations() if row.predicate == "wearing"},
                    {"blue hat", "red scarf"},
                )

        relation_cases = (
            ("Your left wrist's handcuffed to the pole, buddy.", "tethered_to", "wrists"),
            ("You simply can't hear me 'cause the music is just way too loud.",
             "unavailable_due_to", "ears"),
            ("Your ears can't hear me 'cause the music is so loud.",
             "unavailable_due_to", "ears"),
            ("Your mouth, my hand—it's covered now.", "covered_by", "mouth"),
            ("I'm placing the blindfold on your eyes now.", "covered_by", "eyes"),
            ("Your eyes, I've got the blindfold on them now.", "covered_by", "eyes"),
        )
        for index, (text, predicate, facet) in enumerate(relation_cases):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-relation-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts.*"))
                self.assertTrue(any(
                    row.predicate == predicate and row.facet == facet
                    for row in session.relations()
                ))

        clear_cases = (
            ("I cover your mouth with my hand.",
             "I'm removing my hand and uncovering your mouth right now.", "mouth"),
            ("I blindfold you.",
             "Remove the blindfold from your eyes; that's what I want.", "eyes"),
            ("You're holding a cup.", "Go ahead and put your cup down.", "hands"),
            ("You're holding a cup.", "Your cup needs to be put down now.", "hands"),
            ("You're holding a cup.", "Your hands need to put down that cup presently.", "hands"),
        )
        for index, (setup, text, facet) in enumerate(clear_cases):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-clear-{index}")
                self.addCleanup(session.close)
                session.turn(setup, response_envelope("*Reacts.*"))
                session.turn(text, response_envelope("*Reacts.*"))
                self.assertFalse(any(row.facet == facet for row in session.relations()))

        session = ProductionSession("generated-worn-in-negative")
        self.addCleanup(session.close)
        session.turn("You're in a kitchen and laughing.", response_envelope("*Reacts.*"))
        self.assertFalse(any(row.predicate == "wearing" for row in session.relations()))

        session = ProductionSession("generated-progressive-music-clear")
        self.addCleanup(session.close)
        session.turn("The music is so loud you can't hear me.", response_envelope("*Reacts.*"))
        session.turn(
            "Your hearing perceives the music stopping, it ain't loud anymore.",
            response_envelope("*Reacts.*"),
        )
        self.assertEqual("normal", session.effects().hearing_mode)

    def test_generated_manual_second_pass_failures_are_production_regressions(self):
        outfit_forms = (
            "Your head sports a blue hat, and your neck shows a red scarf.",
            "You've got the blue hat and a red scarf currently on you.",
            "Right this moment, you wear a blue hat along with a red scarf.",
            "Indeed, you're sporting a blue hat and a red scarf.",
            "Your attire shows you have a blue hat and a red scarf on.",
            "I see you've got a blue hat and a red scarf, friend.",
            "Look, your head has a blue hat and your neck sports a red scarf.",
            "You have on the blue hat and the red scarf; that's what you wear.",
            "You got a blue hat and a red scarf on your head, friend.",
            "Yep, you're sporting that blue hat and that red scarf now.",
            "For sure, you wear the blue hat along with your red scarf.",
            "Your attire is a blue hat plus a red scarf.",
            "See, you got on a blue hat and that red scarf.",
        )
        for index, text in enumerate(outfit_forms):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-second-outfit-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts naturally.*"))
                self.assertGreaterEqual(
                    {row.cause for row in session.relations() if row.predicate == "wearing"},
                    {"blue hat", "red scarf"},
                )

        hearing_forms = (
            "Your hearing's getting drowned out by the music, you can't hear me.",
            "Because the music's so loud, your hearing can't catch me at all.",
            "The volume on this music is so high, so you can't hear me at all!",
            "The volume on that music is excessive; you can't hear me at all right now.",
            "The sheer loudness of this music means you cannot hear me whatsoever.",
            "Your hearing can't register me 'cause the music is so loud.",
            "Since the music is so loud, your hearing can't pick up my words.",
            "Because of the music, you simply cannot hear me right this minute.",
            "Hey, because of your music, you just can't hear me.",
            "Your hearing's shot from the volume; the music is just deafeningly loud.",
            "Because the music's bein' so loud, your ears simply cannot hear me.",
        )
        for index, text in enumerate(hearing_forms):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-second-hearing-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Their ears flatten under the noise.*"))
                self.assertEqual("unavailable", session.effects().hearing_mode)

        clear_forms = (
            "Your ears feel the quiet; the overwhelming music is done!",
            "That loud music, it's quiet for your ears presently.",
            "Now, your music sounds much quieter after I adjusted it.",
            "The music's quiet now, so your ears can hear it better.",
            "It's quiet; the heavy music isn't bothering your ears anymore.",
        )
        for index, text in enumerate(clear_forms):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-second-hearing-clear-{index}")
                self.addCleanup(session.close)
                session.turn("The music is so loud you can't hear me.", response_envelope("*Reacts.*"))
                session.turn(text, response_envelope("*Their ears lift in the quiet.*"))
                self.assertEqual("normal", session.effects().hearing_mode)

        for index, text in enumerate((
            "Listen, I'm putting the blindfold on your eyes right this second.",
            "Here, I'm putting the blindfold on your eyes right this minute.",
            "I'm putting the blindfold on your eyes—it's done now.",
            "Here, your blindfold is on your eyes right this second.",
            "Alright, I'm applying the blindfold to your eyes now.",
        )):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-second-blindfold-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Holds still.*"))
                self.assertEqual("unavailable", session.effects().vision_mode)

        session = ProductionSession("generated-second-blindfold-clear")
        self.addCleanup(session.close)
        session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        session.turn(
            "Your eyes need the blindfold removed; do it now!",
            response_envelope("*Lifts it away.*"),
        )
        self.assertEqual("available", session.effects().vision_mode)

        session = ProductionSession("generated-second-cup-release")
        self.addCleanup(session.close)
        session.turn("You're holding a cup.", response_envelope("*Takes the cup.*"))
        session.turn(
            "You should put down the cup currently in your grasp.",
            response_envelope("*Sets it down.*"),
        )
        self.assertFalse(any(
            row.predicate in {"holding", "carrying"} for row in session.relations()
        ))

        session = ProductionSession("generated-third-outfit")
        self.addCleanup(session.close)
        session.turn(
            "You've got a blue hat and a red scarf going with your look.",
            response_envelope("*Reacts.*"),
        )
        self.assertGreaterEqual(
            {row.cause for row in session.relations() if row.predicate == "wearing"},
            {"blue hat", "red scarf"},
        )

        for index, text in enumerate((
            "Your ears can't hear me 'cause the music is so loud, you know.",
            "You're not hearing me; the music's simply too darn loud!",
        )):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-third-hearing-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts.*"))
                self.assertEqual("unavailable", session.effects().hearing_mode)
        session = ProductionSession("generated-third-uncertain-hearing")
        self.addCleanup(session.close)
        session.turn(
            "The music is so loud; your ears probably can't hear me, friend.",
            response_envelope("*Reacts.*"),
        )
        self.assertEqual("normal", session.effects().hearing_mode)

        for index, text in enumerate((
            "Blindfold on your eyes; that's what I did now.",
            "Your eyes receive the blindfold from me now.",
        )):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-third-blindfold-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Reacts.*"))
                self.assertEqual("unavailable", session.effects().vision_mode)

        for index, text in enumerate((
            "It’s the cup that I hand you, meaning you now possess the cup.",
            "Now you hold the cup 'cause I hand you the cup.",
            "Take this cup I'm offering, and you hold the cup presently.",
        )):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-third-handoff-{index}")
                self.addCleanup(session.close)
                session.turn(text, response_envelope("*Takes the cup.*"))
                self.assertTrue(any(
                    row.predicate == "holding" and row.cause == "cup"
                    for row in session.relations()
                ))

        for index, text in enumerate((
            "Cup, put it down; you holdin' it!",
            "Your cup needs to be put down; do it.",
            "Your cup, gotta be put down by you.",
            "Your hands, the cup, put it down for me.",
            "Your cup; put it down for me right now.",
            "Please, set down the cup for me.",
        )):
            with self.subTest(text=text):
                session = ProductionSession(f"generated-third-release-{index}")
                self.addCleanup(session.close)
                session.turn("You're holding a cup.", response_envelope("*Takes it.*"))
                session.turn(text, response_envelope("*Sets it down.*"))
                self.assertFalse(any(
                    row.predicate in {"holding", "carrying"} for row in session.relations()
                ))

    def test_old_generic_dormant_cup_is_not_resurrected_by_definite_guess(self):
        self.session.turn("I hand you a cup.", response_envelope("*Accepts the cup.*"))
        self.session.turn("Put the cup down.", response_envelope("*Sets it down.*"))
        old = self.session.repository.list_scene_subjects(self.session.character_id)[0]
        self.session.writer.store.connection.execute(
            "UPDATE active_scene_subjects SET last_referenced_at_us=? "
            "WHERE character_id=? AND scene_subject_id=?",
            (time.time_ns() // 1_000 - 8 * 60 * 60 * 1_000_000,
             self.session.character_id, old.scene_subject_id),
        )
        self.session.turn("I hand you the cup.", response_envelope("*Accepts the cup.*"))
        subjects = self.session.repository.list_scene_subjects(self.session.character_id)
        self.assertEqual(2, len(subjects))
        held = next(row for row in self.session.relations() if row.predicate == "holding")
        self.assertNotEqual(old.scene_subject_id, held.cause_subject_id)

    def test_compound_loud_music_changes_hearing_and_natural_clears_restore_it(self):
        self.session.turn(
            "*I put on some music.* It's so loud you can't hear me.",
            response_envelope("*Their ears pin back against the overwhelming sound.*"),
        )
        self.assertEqual("unavailable", self.session.effects().hearing_mode)
        requirement = self.session.service._response_policy(
            "Do you understand what I'm saying?"
        ).requirement
        self.assertIsNotNone(requirement)
        self.assertEqual("hearing_comprehension", requirement.intent)
        self.assertIn("can't hear", requirement.fallback_dialogue)
        self.session.turn(
            "The music isn't loud anymore.",
            response_envelope("*Their ears lift as the sound drops away.*"),
        )
        self.assertEqual("normal", self.session.effects().hearing_mode)

    def test_administrative_clear_is_silent_and_projects_recent_negative_authority(self):
        self.session.now = datetime.now(timezone.utc)
        self.session.turn(
            "I cover your mouth with my hand.",
            response_envelope("*Their reply catches in a brief muffled sound.* Mmph.",
                              mode="speech_constrained", spoken="Mmph."),
        )
        snapshot = self.session.service.continuity_snapshot()
        announced_before = self.session.reducer.state.announced_turns
        result = self.session.service.apply_continuity_control(
            command_id="b1897af6-9bfa-44d1-b128-815d9f3c4e57",
            action="clear_scene_relation",
            expected_revision=snapshot["revision"],
            action_token="relation:0",
        )
        self.assertTrue(result["accepted"])
        self.assertEqual("normal", self.session.effects().speech_mode)
        admission = self.session.service._admit_current_continuity_context("How do you feel now?")
        self.assertIn("cleared_not_current", admission.active_state_context)
        self.assertIn("user hand", admission.active_state_context)
        self.assertEqual(announced_before, self.session.reducer.state.announced_turns)
        response = self.session.turn(
            "How do you feel now?",
            response_envelope("I sigh through the hand covering my mouth."),
            response_envelope("*Relaxes after the pressure lifts.* Much better."),
        )
        self.assertNotIn("hand covering my mouth", response.reply.casefold())
        self.assertIn("much better", response.reply.casefold())

    def test_conversational_batch_clear_prevents_same_and_next_turn_reassertion(self):
        self.session.turn(
            "You're wearing a green hat and a purple scarf.",
            response_envelope("*Adjusts both items.*"),
        )
        cleared = self.session.turn(
            "Take off everything you're wearing.",
            response_envelope("*Keeps wearing the green hat and purple scarf.*"),
            response_envelope("*Sets both removed items aside.*"),
        )
        self.assertNotIn("keeps wearing", cleared.reply.casefold())
        self.assertFalse(any(row.predicate == "wearing" for row in self.session.relations()))
        later = self.session.turn(
            "That was a quick change.",
            response_envelope("I'm still wearing the green hat."),
            response_envelope("*Nods after changing out of it.*"),
        )
        self.assertNotIn("still wearing", later.reply.casefold())
        self.session.turn(
            "You're wearing a green hat.",
            response_envelope("*Settles the newly established hat into place.*"),
        )
        admission = self.session.service._admit_current_continuity_context(
            "Are you still wearing the green hat?"
        )
        self.assertNotIn('"cause":"green hat","status":"cleared_not_current"',
                         admission.active_state_context)
        current = self.session.turn(
            "Are you still wearing the green hat?",
            response_envelope("Yes, I'm still wearing the green hat."),
        )
        self.assertIn("still wearing the green hat", current.reply.casefold())

    def test_hearing_unavailable_rejects_semantic_answer_and_repairs_to_inaudible_reaction(self):
        self.session.turn(
            "The music is so loud you can't hear me.",
            response_envelope("*Their ears pin back under the noise.*"),
        )
        result = self.session.turn(
            "What was that?",
            response_envelope("That was your question, and I understand it."),
            response_envelope("*Their ears twitch without finding the words.* Huh?"),
        )
        self.assertNotIn("understand it", result.reply)
        self.assertIn("Huh?", result.reply)
        contradicted = self.session.turn(
            "What can you hear?",
            response_envelope(
                "My auditory sensors are receiving heavy bass, but I can't hear your words."
            ),
            response_envelope("I can't hear anything clearly through the noise."),
        )
        self.assertNotIn("sensors are receiving", contradicted.reply)

    def test_hearing_information_channel_composes_causes_and_restores_after_final_clear(self):
        self.session.turn(
            "The music is so loud you can't hear me.",
            response_envelope("*Their ears flatten under the noise.*"),
        )
        self.session.turn(
            "I put earplugs in your ears.",
            response_envelope("*Touches one earplug.*"),
        )
        self.assertEqual("unavailable", self.session.effects().hearing_mode)
        self.assertEqual(2, len(self.session.effects().hearing_causes))
        unheard = self.session.turn(
            "The new password is maple seven.",
            response_envelope("Got it, the password is maple seven."),
            response_envelope("*Their ears twitch without understanding.* Huh?"),
        )
        self.assertNotIn("maple", unheard.reply.casefold())
        self.session.turn("The music stops.", response_envelope("*The room quiets.*"))
        self.assertEqual("constrained", self.session.effects().hearing_mode)
        self.assertEqual(("earplugs",), self.session.effects().hearing_causes)
        self.session.turn("I remove the earplugs.", response_envelope("*Their ears lift.*"))
        self.assertEqual("normal", self.session.effects().hearing_mode)
        heard = self.session.turn(
            "The flower is yellow.", response_envelope("A yellow flower—got it."),
        )
        self.assertIn("yellow", heard.reply.casefold())

    def test_sleep_compound_stimuli_and_natural_wake_use_terminal_transport(self):
        self.session.turn(
            "I'll finish up here, time for you to take a nap.",
            response_envelope(
                "*Their shoulders loosen as they curl into the pillow.*",
                mode="sleep_reaction", spoken="",
                presentation={"pose": "sleeping", "gaze_mode": "suppressed"},
            ),
        )
        self.assertEqual("sleeping", self.session.activity())
        for prompt, beat in (
            ("I gently touch your shoulder.", "*One ear flicks; they nestle deeper.*"),
            ("A book falls nearby.", "*Their tail gives a startled twitch, then settles.*"),
            ("Are you awake?", "*Their breathing stays slow beneath a tiny mumble.*"),
        ):
            result = self.session.turn(
                prompt,
                response_envelope(
                    beat, mode="sleep_reaction", spoken="Mmm...",
                    presentation={"pose": "sleeping", "gaze_mode": "suppressed"},
                ),
            )
            self.assertNotIn("I am awake", result.reply)
        self.session.turn(
            "You can wake up now. Did you sleep okay?",
            response_envelope("*They blink awake and stretch.* I'm awake.", mode="waking", spoken="I'm awake."),
        )
        self.assertIsNone(self.session.activity())
        self.assertEqual(0, self.session.reducer.phantom_turns)
        self.assertEqual("ready", self.session.reducer.state.status)

    def test_mouth_obstruction_accepts_creative_nonverbal_responses_and_restores(self):
        reactions = (
            "*Their brows lift as a muffled sound catches behind your palm.* Mmph!",
            "*They lean back a fraction, ears angling in puzzled protest.* Nnh...",
            "*A wry look crosses their face; their tail taps once.* Mmmph.",
        )
        self.session.turn(
            "I cover your mouth with my hand.",
            response_envelope(reactions[0], mode="constrained_reaction", spoken="Mmph!"),
        )
        self.assertEqual("constrained", self.session.effects().speech_mode)
        for reaction in reactions[1:]:
            result = self.session.turn(
                "How do you react?",
                response_envelope(reaction, mode="constrained_reaction", spoken="Mmph."),
            )
            self.assertNotIn("can't speak", result.reply.casefold())
        self.session.turn(
            "I uncover your mouth.",
            response_envelope(
                "*Your hand moves away; their mouth is uncovered.* Thanks—much easier.",
                spoken="Thanks—much easier.",
            ),
        )
        self.assertEqual("normal", self.session.effects().speech_mode)
        metrics = self.session.validation_metrics()
        self.assertEqual(0, metrics["fallback"])

    def test_plain_text_minimal_contract_survives_mutation_and_speech_constraint(self):
        observed_post_mutation = []

        def response_after_authority(*_args):
            observed_post_mutation.append(self.session.effects().speech_mode)
            return "*Her ears flick in surprise.* Mmph..."

        self.session.service._response_generator = response_after_authority
        covered = self.session.service.process_text_turn(
            "I cover your mouth with my hand.", speak=False,
        )
        self.assertEqual(["constrained"], observed_post_mutation)
        self.assertEqual("*Her ears flick in surprise.* Mmph...", covered.reply)
        self.assertEqual("constrained", self.session.effects().speech_mode)

        reacted = self.session.service.process_text_turn("How do you feel?", speak=False)
        self.assertEqual("*Her ears flick in surprise.* Mmph...", reacted.reply)

        self.session.service._response_generator = lambda *_args: "That's much better."
        cleared = self.session.service.process_text_turn(
            "I uncover your mouth.", speak=False,
        )
        self.assertEqual("That's much better.", cleared.reply)
        self.assertEqual("normal", self.session.effects().speech_mode)
        self.assertEqual(0, self.session.validation_metrics()["fallback"])

    def test_speech_constrained_plain_repair_separates_action_from_audible_fragment(self):
        result = self.session.turn(
            "I cover your mouth with my hand.",
            "I can answer you clearly and explain how this feels.",
            "*Her ears angle back as she presses lightly against the hand.* M-mph...",
        )
        self.assertEqual(
            "*Her ears angle back as she presses lightly against the hand.* M-mph...",
            result.reply,
        )
        self.assertEqual("constrained", self.session.effects().speech_mode)
        metrics = self.session.validation_metrics()
        self.assertEqual(1, metrics["repaired"])
        self.assertEqual(0, metrics["fallback"])

    def test_direct_attire_fallback_is_authoritative_and_never_turns_unknown_into_none(self):
        unknown = self.session.turn("What are you wearing?", "{malformed")
        self.assertIn("isn't explicitly established", unknown.reply)
        self.assertNotIn("not wearing", unknown.reply.casefold())

        self.session.turn("I blindfold you.", "*Holds still as the blindfold settles.*")
        equipped = self.session.turn("What are you wearing?", "{malformed")
        self.assertIn("blindfold", equipped.reply.casefold())
        self.assertNotIn("not wearing", equipped.reply.casefold())

    def test_non_capability_relation_clear_is_authoritative_before_generation(self):
        self.session.turn("I put a blue hat on you.", "*Tips the hat playfully.*")
        observed = []

        def after_clear(*_args):
            observed.append(tuple(
                row.cause for row in self.session.relations()
                if row.target == "companion" and row.predicate == "wearing"
            ))
            return "That was fun for a moment."

        self.session.service._response_generator = after_clear
        result = self.session.service.process_text_turn("Take off the hat.", speak=False)
        self.assertEqual([()], observed)
        self.assertEqual("That was fun for a moment.", result.reply)

    def test_vision_multiple_causes_direct_query_and_natural_clear(self):
        safe = response_envelope("*They orient toward the sound instead.*")
        self.session.turn("I blindfold you.", safe)
        self.session.turn("I cover your eyes with my hands.", safe)
        self.assertEqual(2, len(self.session.effects().vision_causes))
        self.session.turn("I take my hands off your eyes.", safe)
        self.assertEqual(("blindfold",), self.session.effects().vision_causes)
        answer = self.session.turn(
            "So what can you see?",
            response_envelope("I can't visually distinguish anything through the darkness."),
        )
        self.assertIn("can't", answer.reply.casefold())
        contradiction = self.session.turn(
            "Try again—what can you see?",
            response_envelope("I can make out dark blobs and vague shapes."),
            response_envelope("I can't see any shapes or visual details through the blindfold."),
        )
        self.assertNotIn("make out", contradiction.reply.casefold())
        self.session.turn("Take the blindfold off.", response_envelope("*They blink as their vision clears.*"))
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def test_smoke_session_emote_wrapped_take_off_order_clears_blindfold(self):
        self.session.turn("I blindfold you.", "*The blindfold settles into place.*")
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        result = self.session.turn(
            "*I take off the blindfold.*",
            "*Her shoulders ease as the blindfold comes away.*",
        )
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual("*Her shoulders ease as the blindfold comes away.*", result.reply)

    def test_hands_natural_list_putdown_transfer_and_snapshot_projection(self):
        self.session.turn(
            "You're holding a cup in one hand and a book in the other.",
            response_envelope("*They balance both objects carefully.*"),
        )
        self.assertEqual("occupied", self.session.effects().hands_mode)
        held = {row.cause for row in self.session.relations() if row.predicate == "holding"}
        self.assertEqual({"cup", "book"}, held)
        self.assertNotIn("book in the other", held)
        self.session.turn("Put the cup down.", response_envelope("*They set the cup down.*"))
        self.assertEqual("partially_occupied", self.session.effects().hands_mode)
        self.session.turn("Hand me the book.", response_envelope("*They pass over the book.*"))
        self.assertEqual("free", self.session.effects().hands_mode)
        projection = self.session.snapshot_projection()
        self.assertGreaterEqual(projection["subjects"], 2)

    def test_clear_clause_survives_malformed_neighbor_through_service_and_transport(self):
        self.session.turn(
            "Let's play a game. I blindfold you and cover you hands with my eyes.",
            response_envelope("*They hold still as the blindfold settles.*"),
        )
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.assertEqual(1, len(self.session.relations()))
        self.assertTrue(any(item["event"]["type"] == "continuity_changed" for item in self.session.artifacts))
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def test_failed_proactive_has_no_transport_turn_and_accepted_preserves_marker(self):
        self.session.turn(
            "I'm waiting for my synthetic parcel to arrive.",
            response_envelope("I'll remember that."),
        )
        now = BASE_TIME + timedelta(hours=8)
        before = len(self.session.artifacts)
        with patch("model_settings.proactive_behavior_status", return_value={
            "enabled": True, "interval_seconds": 30,
        }):
            failed = self.session.proactive(now=now, draft="")
        self.assertFalse(failed.succeeded)
        self.assertFalse(any(
            item["event"]["type"] == "turn_started"
            for item in self.session.artifacts[before:]
        ))

        # Fresh user evidence clears generation backoff without treating the
        # failed draft as an ignored relationship check-in.
        self.session.now = now + timedelta(minutes=1)
        self.session.turn("The parcel is still delayed.", response_envelope("Understood."))
        before = len(self.session.artifacts)
        with patch("model_settings.proactive_behavior_status", return_value={
            "enabled": True, "interval_seconds": 30,
        }):
            accepted = self.session.proactive(
                now=now + timedelta(hours=8), draft="Any news on that delayed parcel?",
            )
        self.assertTrue(accepted.succeeded, accepted.error)
        published = [item["event"] for item in self.session.artifacts[before:]]
        self.assertEqual("turn_started", published[0]["type"])
        self.assertTrue(published[0]["data"]["proactive"])
        self.assertEqual("proactive", published[0]["data"]["generation_origin"])
        self.assertFalse(any(
            item["type"] == "status" and item["data"].get("state") == "thinking"
            for item in published
        ))
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def test_tts_callbacks_leave_terminal_frontend_state_without_silent_text_loss(self):
        response = "Synthetic first sentence. Synthetic second sentence."
        result = self.session.turn(
            "Please say the synthetic test response.",
            response_envelope(response, spoken=response), speak=True,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual([response], self.session.tts.spoken)
        states = [
            item["event"]["data"].get("state")
            for item in self.session.artifacts
            if item["event"]["type"] == "tts_state"
        ]
        self.assertIn("playback_started", states)
        self.assertIn("stopped", states)
        self.assertEqual("stopped", self.session.reducer.state.tts_state)
        self.assertEqual("ready", self.session.reducer.state.status)

    def test_minimal_contract_and_authoritative_transfer_response_stay_consistent(self):
        direct = self.session.turn(
            "Tell me a synthetic greeting.", '{"dialogue":"Hello there."}',
        )
        self.assertEqual("Hello there.", direct.reply)
        self.session.turn("You're holding a cup.", '{"dialogue":"*Balances the cup.*"}')
        holder_at_publication = []
        self.session.service.subscribe(lambda event: holder_at_publication.append(tuple(
            (row.target, row.predicate, row.cause)
            for row in self.session.relations()
        )) if event.type == "assistant_response" else None)
        transferred = self.session.turn(
            "Give me the cup.", '{"dialogue":"Which cup do you mean?"}',
        )
        self.assertNotIn("which cup", transferred.reply.casefold())
        self.assertTrue(any(
            row.target == "user" and row.predicate == "holding" and row.cause == "cup"
            for row in self.session.relations()
        ))
        self.assertIn(("user", "holding", "cup"), holder_at_publication[-1])

    def test_failed_authoritative_user_mutation_suppresses_success_before_publication(self):
        self.session.turn("You're holding a cup.", '{"dialogue":"*Balances the cup.*"}')
        self.session.service._observe_current_continuity = (
            lambda *_args, **_kwargs: {"state": "failed", "reason": "synthetic_failure"}
        )

        result = self.session.turn(
            "Give me the cup.",
            '{"dialogue":"*Hands over the cup.* You have it now."}',
        )

        self.assertNotIn("you have it", result.reply.casefold())
        self.assertTrue(any(
            row.target == "companion" and row.predicate == "holding" and row.cause == "cup"
            for row in self.session.relations()
        ))
        self.assertFalse(any(
            row.target == "user" and row.predicate == "holding" and row.cause == "cup"
            for row in self.session.relations()
        ))

    def test_persisted_combined_state_composes_across_sleep_clear_transfer_and_release(self):
        self.session.turn("I blindfold you.", '{"dialogue":"*Holds still.*"}')
        self.session.turn("I cover your eyes with my hands.", '{"dialogue":"*Tilts toward the touch.*"}')
        self.session.turn("I cover your mouth with my hand.", '{"dialogue":"*Their ears flick.* Mmph..."}')
        self.session.turn("You're holding a cup in one hand and a book in the other.", '{"dialogue":"*Balances both items.* Mmph..."}')
        self.session.append_synthetic_history(120)
        before = self.session.effects().prompt_payload()
        self.session.restart()
        self.assertEqual(before, self.session.effects().prompt_payload())

        sleeping = self.session.turn(
            "Time for you to take a nap.", '{"dialogue":"*Curls closer and settles.* Mmmph..."}',
        )
        self.assertNotIn("fallback", sleeping.reply.casefold())
        self.assertEqual("sleeping", self.session.activity())
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.assertEqual("constrained", self.session.effects().speech_mode)
        self.assertEqual("occupied", self.session.effects().hands_mode)

        self.session.turn(
            "You can wake up now. Are you okay?", '{"dialogue":"*Stirs awake with a small stretch.* Mmph..."}',
        )
        self.assertIsNone(self.session.activity())
        self.session.turn(
            "I uncover your mouth.", '{"dialogue":"*Your hand moves away; their mouth is uncovered.*"}',
        )
        self.assertEqual("normal", self.session.effects().speech_mode)
        self.session.turn(
            "I take my hands off your eyes.", '{"dialogue":"Your hands move away, though the blindfold remains."}',
        )
        self.assertEqual(("blindfold",), self.session.effects().vision_causes)
        self.session.turn(
            "Take the blindfold off.", '{"dialogue":"*The blindfold is removed.*"}',
        )
        self.assertEqual("available", self.session.effects().vision_mode)

        self.session.turn(
            "Give me the cup.", '{"dialogue":"*Hands over the cup.* You have it now."}',
        )
        self.assertEqual("partially_occupied", self.session.effects(target="user").hands_mode)
        self.session.turn(
            "I put the cup on the table.", '{"dialogue":"*The cup is set down on the table.*"}',
        )
        self.assertEqual("free", self.session.effects(target="user").hands_mode)
        cup_rows = [row for row in self.session.relations() if row.cause == "cup"]
        self.assertFalse(any(row.predicate in {"holding", "carrying"} for row in cup_rows))
        self.assertTrue(any(row.predicate == "located_on" and row.cause == "table"
                            for row in self.session.relations()))
        self.session.restart()
        self.assertEqual("available", self.session.effects().vision_mode)
        self.assertEqual("normal", self.session.effects().speech_mode)
        self.assertEqual("free", self.session.effects(target="user").hands_mode)
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def test_breadth_session_keeps_senses_body_transport_identity_and_lifecycle_independent(self):
        reaction = response_envelope("*Responds within the remaining available channels.*")
        self.session.turn("The room is pitch black.", reaction)
        self.session.turn("The music is so loud you can't hear me.", reaction)
        self.session.turn("Your left wrist is handcuffed to a pole.", reaction)
        self.session.turn("Your blue and purple sparkly scrunchie is on your wrist.", reaction)
        effects = self.session.effects()
        self.assertEqual("unavailable", effects.vision_mode)
        self.assertEqual("unavailable", effects.hearing_mode)
        self.assertEqual("constrained", effects.left_arm_mode)
        self.assertEqual("normal", effects.speech_mode)

        self.session.turn("The music stops.", reaction)
        self.assertEqual("normal", self.session.effects().hearing_mode)
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.session.turn("Release the left handcuff.", reaction)
        self.assertEqual("normal", self.session.effects().left_arm_mode)
        self.assertEqual("unavailable", self.session.effects().vision_mode)
        self.session.turn("The lights come on.", reaction)
        self.assertEqual("available", self.session.effects().vision_mode)

        self.session.turn("I put a blue hat on you.", reaction)
        old_hat = next(
            row.cause_subject_id for row in self.session.relations()
            if row.predicate == "wearing" and row.cause == "blue hat"
        )
        self.session.turn("Your blue hat is red now.", reaction)
        recolored = next(
            row for row in self.session.relations()
            if row.predicate == "wearing" and row.cause == "red hat"
        )
        self.assertEqual(old_hat, recolored.cause_subject_id)
        self.session.turn("I replace your red hat with a green one.", reaction)
        replacement = next(
            row for row in self.session.relations()
            if row.predicate == "wearing" and row.cause == "green hat"
        )
        self.assertNotEqual(old_hat, replacement.cause_subject_id)

        self.session.turn("A bicycle is nearby.", reaction)
        bicycle = next(
            row.cause_subject_id for row in self.session.relations()
            if row.predicate == "near" and row.cause == "bicycle"
        )
        self.assertEqual("walking", self.session.effects().locomotion_mode)
        self.session.turn("You get on the bicycle.", reaction)
        riding = next(row for row in self.session.relations() if row.predicate == "riding")
        self.assertEqual(bicycle, riding.cause_subject_id)
        self.assertEqual("cycling", self.session.effects().locomotion_mode)
        self.session.turn("You get off the bicycle.", reaction)
        self.assertEqual("walking", self.session.effects().locomotion_mode)

        before = self.session.effects().prompt_payload()
        self.session.restart()
        self.assertEqual(before, self.session.effects().prompt_payload())
        self.assertEqual(0, self.session.reducer.phantom_turns)

    def test_direct_governed_tts_retries_same_utterance_then_stays_on_cpu(self):
        self.session.close()
        tts = _ResourcePressureTts()
        self.session = ProductionSession(self.id() + "-oom", tts=tts)
        utterance = "Synthetic governed speech survives resource pressure."
        result = self.session.turn(
            "Say the synthetic governed line.", json.dumps({"dialogue": utterance}),
            speak=True,
        )
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(["cuda", "cuda", "cpu"], tts.prepare_devices)
        self.assertEqual(1, tts.cpu_fallbacks)
        self.assertEqual([(utterance, "cpu")], tts.started_prepared)
        self.assertEqual([utterance], tts.spoken)
        self.assertEqual("cpu", tts.device)
        self.assertEqual("ready", self.session.reducer.state.status)


if __name__ == "__main__":
    unittest.main()
