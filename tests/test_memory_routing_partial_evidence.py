"""Production turns over synthetic canonical sources and healthy controlled recall."""

from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aifren.assistant_service import AssistantService
from aifren.memory_v2_store.models import RetrievalHealth, RetrievalLaneHealth
from aifren.conversation.conversation import Conversation
from aifren.continuity.memory_v2_authority import DevelopmentV2MemoryAuthority
from aifren.continuity.memory_v2_episode_compaction import canonical_record_id
from test_assistant_service_v2_authority import _LLM, _Memory, _TTS
from test_continuity_companion_tranche import _Harness
from test_memory_v2_embeddings import ToyEmbeddingProvider
from test_memory_v2_replacement_shadow import _Candidate


class HealthyRecall:
    """Supply eligible search results; absence here never represents a search error."""

    def __init__(self):
        self.candidates = ()
        self.decisions = []

    def retrieve(self, query, *, memory_query_decision, **kwargs):
        self.decisions.append(memory_query_decision)
        return SimpleNamespace(candidates=self.candidates, abstention_reason="", generated_counts=(),
                               health=RetrievalHealth((RetrievalLaneHealth("claims", "complete"),)))


class MemoryRoutingPartialEvidenceTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in tuple(os.environ):
            if name.startswith("AIFREN_"):
                del os.environ[name]
        self.h = _Harness()
        self.addCleanup(self.h.close)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.h.root)
        self.h.writer.compare = lambda *_a, **_k: {}
        self.h.writer._embedding_provider = ToyEmbeddingProvider()
        self.llm = _LLM("I'm doing well. How are you?")
        self.memory, self.tts = _Memory(), _TTS()
        self.conversation = Conversation(
            self.llm, conversation_file=self.h.conversation_file,
            summary_file=self.h.root / "summary.json", memory_authority="v2",
        )
        self.recall = HealthyRecall()
        self.authority = DevelopmentV2MemoryAuthority(
            self.h.writer.store, self.h.character_id, self.conversation.messages,
            recall=self.recall,
        )
        self.service = AssistantService(
            self.llm, self.memory, self.conversation, object(),
            {"_character_id": self.h.character_id}, "Synthetic character.", self.tts,
            character_id=self.h.character_id, memory_v2_shadow_writer=self.h.writer,
            memory_authority="v2", memory_v2_authority=self.authority,
        )
        self.addCleanup(self.service.close)
        self.events = []
        self.service.subscribe(self.events.append)
        self.recorder = Mock(enabled=False)
        recorder = patch('aifren.assistant_service.development_flight_recorder', return_value=self.recorder)
        recorder.start()
        self.addCleanup(recorder.stop)
        self.turns = []
        self.context_decisions = []
        build = self.conversation.build_context

        def context(*args, **kwargs):
            self.context_decisions.append(kwargs.get("memory_query_decision"))
            return build(*args, **kwargs)

        self.conversation.build_context = context
        original = self.authority.prepare

        def prepare(*args, **kwargs):
            turn = original(*args, **kwargs)
            self.turns.append(turn)
            return turn

        self.authority.prepare = prepare

    def source(self, text, *, speaker="user", speech_act="assertion", **changes):
        index = len(self.conversation.messages)
        add = (self.conversation.add_user_message if speaker == "user"
               else self.conversation.add_assistant_message)
        add(text)
        self.conversation.save()
        candidate = _Candidate(
            f"source-{index}", content=text, speaker_role=speaker,
            speech_act=speech_act, canonical_index=index,
            canonical_record_id=canonical_record_id(index, self.conversation.messages[index]), **changes,
        )
        self.recall.candidates += (candidate,)
        return candidate

    def turn(self, text):
        event_start = len(self.events)
        result = self.service.process_text_turn(text, speak=True)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(result.reply, json.loads(self.h.conversation_file.read_bytes())[-1]["content"])
        self.assertEqual(0, self.memory.retrieval_calls)
        self.assertEqual([], self.memory.processed)
        self.assertIs(self.recall.decisions[-1], self.turns[-1].memory_query_decision)
        self.assertIs(self.recall.decisions[-1], self.turns[-1].requirement.memory_query_decision)
        if not self.turns[-1].authoritative_no_evidence:
            self.assertIs(self.recall.decisions[-1], self.context_decisions[-1])
        events = self.events[event_start:]
        starts = [e for e in events if e.type == "turn_started"]
        self.assertEqual(1, len(starts))
        turn_id = starts[0].data["turn_id"]
        terminals = [call for call in self.recorder.mark.call_args_list
                     if call.args == ("turn_terminal_outcome",) and call.kwargs.get("turn_id") == turn_id]
        self.assertEqual(1, len(terminals))
        self.assertEqual(1, sum(e.type == "assistant_response" for e in events))
        return result

    def test_incidental_before_keeps_ordinary_provider_response(self):
        result = self.turn("Before we start, how are you?")
        self.assertEqual("I'm doing well. How are you?", result.reply)
        self.assertEqual(1, len(self.llm.calls))
        self.assertFalse(self.turns[-1].memory_query_decision.contain_recent_context)

    def test_adding_missing_food_keeps_supported_color(self):
        self.source("My favorite color is green.")
        self.llm.response = "You said your favorite color is green."
        alone = self.turn("What is my favorite color?")
        self.assertIn("green", alone.reply)
        calls = len(self.llm.calls)
        mixed = self.turn("What is my favorite color and what is my favorite food?")
        self.assertIn("green", mixed.reply)
        self.assertIn("favorite food", mixed.reply)
        self.assertIn("don't remember", mixed.reply)
        self.assertGreater(len(self.llm.calls), calls)

    def test_failed_visual_repair_keeps_memory_answer_under_sight_obstruction(self):
        self.turn("My favorite color is green.")
        self.llm.response = "I can't see through the cloth."
        self.turn("A red cloth covers your eyes and prevents your sight.")
        self.assertFalse(self.h.repository.capability_effects(self.h.character_id).vision_available)
        self.llm.response = "I can see your face. Your favorite food is pizza."
        result = self.turn("What is my favorite color and favorite food?")
        self.assertIn("green", result.reply)
        self.assertIn("favorite food", result.reply)
        self.assertIn("don't remember", result.reply)
        self.assertNotIn("pizza", result.reply)
        self.assertNotIn("I can see", result.reply)
        self.assertNotIn("pizza", " ".join(str(e.data.get("content", "")) for e in self.events
            if e.type in {"assistant_response", "assistant_delta"}))
        self.llm.response = "Your favourite colour is green."
        calls = len(self.llm.calls)
        supported = self.turn("What is my favorite color?")
        self.assertIn("green", supported.reply)
        self.assertEqual(calls + 1, len(self.llm.calls), "a valid preference needs no repair")

    def test_ordinary_wording_and_real_recall_pairs_use_distinct_paths(self):
        self.conversation.add_user_message("A quiet synthetic afternoon.")
        self.conversation.save()
        pairs = (
            ("Before dinner, how are you?", "What did I tell you before dinner?"),
            ("Please remember to keep this brief.", "Do you remember anything about my telescope?"),
            ("Can you remember to keep this brief?", "What did I used to prefer before?"),
            ("Remember before you answer to keep it brief.", "Remember the Python project"),
            ("Remind me of how photosynthesis works.", "Do you remember anything about my garden?"),
            ("Remember my favorite color is green.", "What was my favorite color before?"),
            ("Please remember that my favorite color is green.", "Do you remember my favorite color?"),
            ('Explain the phrase "What did I say before?".', "What did I say before?"),
            ("Can you explain how memory works?", "What do you remember about our conversation?"),
            ("How do people recall dreams?", "Have we talked about dreams before?"),
            ("I remember enjoying this weather. How are you?", "Did you ever tell me about the weather?"),
            ("Can you write a story about remembering a trip?", "Do you recall a place I visited?"),
            ('Can you explain "Which programming language was it?"?', "Which programming language was it?"),
            ("Can you explain ‘Which programming language was it?’?", "Do you remember what we talked about before?"),
        )
        for ordinary, recall in pairs:
            with self.subTest(ordinary=ordinary):
                calls = len(self.llm.calls)
                previous = self.conversation.messages[-1]["content"]
                result = self.turn(ordinary)
                self.assertEqual(self.llm.response, result.reply)
                self.assertEqual(calls + 1, len(self.llm.calls))
                decision = self.turns[-1].memory_query_decision
                self.assertFalse(decision.applicable)
                self.assertFalse(decision.contain_recent_context)
                self.assertGreater(self.turns[-1].recent_message_count, 1)
                self.assertIn(previous, [item.get("content") for item in self.llm.calls[-1][0]])
            with self.subTest(recall=recall):
                calls = len(self.llm.calls)
                result = self.turn(recall)
                self.assertEqual(calls, len(self.llm.calls))
                self.assertTrue(self.turns[-1].authoritative_no_evidence)
                self.assertEqual(1, self.turns[-1].recent_message_count)
                self.assertNotEqual(self.llm.response, result.reply)

    def test_mixed_question_order_does_not_change_supported_answers(self):
        self.source("My favorite color is green.")
        self.llm.response = "You said your favorite color is green."
        for query in (
            "What's my favorite food and what's my favorite color?",
            "What is my favorite color and food?",
            "What is my favorite food and color?",
        ):
            with self.subTest(query=query):
                result = self.turn(query)
                self.assertEqual("You said your favorite color is green. I don't remember you telling me your favorite food.", result.reply)
                self.assertEqual("partial_evidence", self.turns[-1].requirement.evidence_state)
                self.assertFalse(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_known_food_missing_color_is_the_converse(self):
        self.source("My favorite food is watermelon.")
        self.llm.response = "You said your favorite food was watermelon."
        for query in ("What is my favorite food and color?", "What's my favorite color and what's my favorite food?"):
            with self.subTest(query=query):
                result = self.turn(query)
                self.assertIn("watermelon", result.reply)
                self.assertTrue(result.reply.endswith("I don't remember you telling me your favorite color."))
                self.assertFalse(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_both_supported_preserve_provider_phrasing(self):
        self.source("My favorite color is green.")
        self.source("My favorite food is watermelon.")
        self.llm.response = "You said your favorite color is green. You said your favorite food is watermelon."
        result = self.turn("What is my favorite color and food?")
        self.assertEqual(self.llm.response, result.reply)
        self.assertEqual("grounded_evidence", self.turns[-1].requirement.evidence_state)
        self.assertEqual(1, len(self.llm.calls))

    def test_neither_supported_stays_provider_free(self):
        result = self.turn("What is my favorite food and color?")
        self.assertIn("food", result.reply)
        self.assertIn("color", result.reply)
        self.assertEqual([], self.llm.calls)
        self.assertTrue(self.turns[-1].authoritative_no_evidence)

    def test_two_topic_only_slots_still_name_each_unknown_without_provider(self):
        self.source("Blue, almost like my favorite color.")
        self.source("Pizza, almost like my favorite food.")
        result = self.turn("What is my favorite color and food?")
        self.assertIn("your favorite color", result.reply)
        self.assertIn("your favorite food", result.reply)
        self.assertNotIn("blue", result.reply)
        self.assertNotIn("pizza", result.reply)
        self.assertEqual([], self.llm.calls)

    def test_wrong_speaker_questions_fragments_and_modality_cannot_supply_missing_slot(self):
        color = self.source("My favorite color is green.")
        rejected = (
            self.source("My favorite food is pizza.", speaker="assistant"),
            self.source("Is my favorite food pizza?", speech_act="question"),
            self.source("Pizza, almost like my favorite food."),
            self.source("My favorite food is not pizza."),
            self.source("My favorite food is perhaps pizza."),
            self.source("If my favorite food is pizza, we could order it."),
            self.source('"My favorite food is pizza."'),
            self.source('I wrote "My favorite food is pizza." as an example.'),
            self.source("My favorite food is pizza if we are pretending."),
            self.source("For example, my favorite food is pizza."),
            self.source("My favorite food is supposedly pizza."),
            self.source("It isn't true that my favorite food is pizza."),
        )
        self.llm.response = "You said your favorite color is green."
        for candidate in rejected:
            with self.subTest(source=candidate.memory_id):
                self.recall.candidates = (color, candidate)
                result = self.turn("What is my favorite color and food?")
                self.assertIn("green", result.reply)
                self.assertNotIn("pizza", result.reply)
                self.assertTrue(result.reply.endswith("I don't remember you telling me your favorite food."))

    def test_scope_lifecycle_and_source_exclusions_cannot_fill_a_slot(self):
        color = self.source("My favorite color is green.")
        food = self.source("My favorite food is pizza.")
        self.llm.response = "You said your favorite color is green."
        for bad in (
            replace(food, scope_state="scenario", truth_scope_id="different-scope"),
            replace(food, status="superseded"),
            replace(food, attribution_state="user_attribution_unsupported"),
            replace(food, canonical_record_id=""),
            replace(food, source_class="generated_scene_event"),
        ):
            with self.subTest(source=bad):
                self.recall.candidates = (color, bad)
                result = self.turn("What is my favorite color and food?")
                self.assertIn("green", result.reply)
                self.assertNotIn("pizza", result.reply)
                self.assertEqual("partial_evidence", self.turns[-1].requirement.evidence_state)

    def test_known_semantic_failure_uses_owned_fallback_without_repair_or_leaks(self):
        self.source("My favorite color is green.")
        for draft, repair in (
            ("You said your favorite color is green. Your favorite food is pizza.", "Pizza is your favorite food."),
            ("I don't remember anything.", "Your favorite food is pizza."),
            ("You said your favorite color is green. Pizza, of course!", "I can't remember."),
            ("Your favorite food is green.", "Your favorite color is pizza."),
        ):
            with self.subTest(draft=draft):
                self.llm.calls.clear()
                self.events.clear()
                self.tts.spoken.clear()
                self.llm.response = [draft, repair]
                result = self.turn("What is my favorite color and food?")
                self.assertEqual("You said your favorite color was green. I don't remember you telling me your favorite food.", result.reply)
                self.assertEqual(1, len(self.llm.calls))
                self.assertEqual('bounded_memory_semantic_rejection',
                                 self.service._last_memory_authority_diagnostics['repair_skipped_reason'])
                self.assertTrue(self.service._last_memory_authority_diagnostics["fallback_used"])
                self.assertEqual([result.spoken_text], self.tts.spoken)
                self.assertFalse(any(e.type == "assistant_delta" for e in self.events))
                self.assertFalse(any("pizza" in str(e.data).lower() for e in self.events))
                self.assertEqual([result.reply], [e.data["content"] for e in self.events if e.type == "assistant_response"])

    def test_valid_format_repair_keeps_phrasing_and_backend_appends_unknown(self):
        self.source("My favorite color is green.")
        repair = "Yes, you said your favorite color is green."
        # Format repair remains available; measured semantic realization
        # failures now take the existing deterministic fallback directly.
        self.llm.response = ['{"dialogue":"Your favorite food is pizza."} trailing prose', repair]
        result = self.turn("What is my favorite color and food?")
        self.assertEqual(repair + " I don't remember you telling me your favorite food.", result.reply)
        self.assertTrue(self.service._last_memory_authority_diagnostics["repair_succeeded"])
        self.assertFalse(self.service._last_memory_authority_diagnostics["fallback_used"])
        self.assertNotIn("pizza", str(self.events))

    def test_two_supported_slots_reject_swapped_or_trailing_contradictory_values(self):
        self.source("My favorite color is green.")
        self.source("My favorite food is watermelon.")
        for draft in (
            "You said your favorite color is watermelon. You said your favorite food is green.",
            "You said your favorite color is green. You said your favorite food is watermelon. Your favorite color is watermelon.",
            "You said your favorite color is green. You said your favorite food is watermelon. Watermelon is your favorite color.",
        ):
            with self.subTest(draft=draft):
                self.llm.response = draft
                result = self.turn("What is my favorite color and food?")
                self.assertEqual("You said your favorite color was green. You said your favorite food was watermelon.", result.reply)
                self.assertTrue(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_current_correction_wins_without_erasing_historical_answer(self):
        old = self.source("My favorite color is green.")
        self.llm.response = "Got it."
        self.turn("My favorite color is green.")
        self.turn("Actually, my favorite color is blue.")
        self.recall.candidates = (old,)
        self.llm.response = "Your favorite color is green."
        current = self.turn("What is my favorite color and food?")
        self.assertIn("Your favorite color is blue.", current.reply)
        self.assertNotIn("green", current.reply)
        self.llm.response = "You said your favorite color was green."
        historical = self.turn("What did I say my favorite color was?")
        self.assertIn("green", historical.reply)
        self.assertNotIn("blue", historical.reply)
        self.assertEqual("historical", self.turns[-1].memory_query_decision.time_semantics)

    def test_embedded_assertion_history_opinion_and_uncertainty_stay_distinct(self):
        self.llm.response = "Got it."
        self.turn("Did you know that I like watermelon?")
        self.assertFalse(self.turns[-1].memory_query_decision.applicable)
        facts = self.h.repository.list_current_durable_core(self.h.character_id)
        self.assertTrue(any("watermelon" in fact.content for fact in facts))
        self.turn("Did I ever tell you that I like watermelon?")
        self.assertEqual("user_historical_source", self.turns[-1].memory_query_decision.intent)
        self.turn("Do you think I like pizza?")
        self.assertFalse(self.turns[-1].memory_query_decision.applicable)
        self.turn("I might like pizza.")
        self.assertFalse(self.turns[-1].memory_query_decision.applicable)
        facts = self.h.repository.list_current_durable_core(self.h.character_id)
        self.assertFalse(any("pizza" in fact.content for fact in facts))

    def test_spoken_projection_uses_complete_assembled_canonical_answer(self):
        self.source("My favorite color is green.")
        self.llm.response = json.dumps({
            "dialogue": "You said your favorite color is green.",
            "spoken_content": "Your favorite food is pizza.",
        })
        result = self.turn("What is my favorite color and food?")
        self.assertEqual([result.reply], self.tts.spoken)
        self.assertNotIn("pizza", str(self.events))

    def test_invalid_draft_gestures_never_reach_publication(self):
        self.source("My favorite color is green.")
        self.llm.response = json.dumps({
            "dialogue": "Your favorite food is pizza.",
            "presentation": {"gesture": "greeting"},
        })
        result = self.turn("What is my favorite color and food?")
        self.assertIn("green", result.reply)
        self.assertNotIn("pizza", str(self.events))
        self.assertFalse(any(e.type == "presentation" and e.data.get("gesture") == "greeting"
                             for e in self.events))
        self.assertIsNone(result.presentation.gesture)

    def test_failed_repair_uses_same_mixed_fallback(self):
        self.source("My favorite color is green.")
        self.llm.response = "Your favorite food is pizza."
        with patch.object(self.llm, "generate_bounded", side_effect=RuntimeError("Synthetic repair failure")):
            result = self.turn("What is my favorite color and food?")
        self.assertEqual("You said your favorite color was green. I don't remember you telling me your favorite food.", result.reply)
        self.assertTrue(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_ordinary_retrospective_decoration_is_still_governed(self):
        self.llm.response = ["I'm well. You told me about your pet ferret.", "I'm doing well."]
        result = self.turn("Before dinner, how are you?")
        self.assertEqual("I'm doing well.", result.reply)
        self.assertFalse(self.turns[-1].memory_query_decision.applicable)
        self.assertEqual(2, len(self.llm.calls))
        self.assertNotIn("ferret", str(self.events))

    def test_current_governed_values_fill_only_their_own_slots(self):
        self.llm.response = "Got it."
        self.turn("My favorite color is green.")
        self.turn("I like watermelon.")
        self.llm.response = "Your favorite color is green."
        mixed = self.turn("What is my favorite color and food?")
        self.assertIn("Your favorite color is green.", mixed.reply)
        self.assertTrue(mixed.reply.endswith("I don't remember you telling me your favorite food."))
        self.assertNotIn("watermelon", mixed.reply)
        self.llm.response = "Got it."
        self.turn("My favorite food is watermelon.")
        self.llm.response = "Your favorite color is green. Your favorite food is watermelon."
        complete = self.turn("What is my favorite color and food?")
        self.assertEqual(self.llm.response, complete.reply)
        self.assertFalse(self.service._last_memory_authority_diagnostics["fallback_used"])

    def test_bare_remember_topic_preserves_genuine_historical_recall(self):
        self.source("I worked on a Python project.")
        self.llm.response = "I remember you saying, “I worked on a Python project.”"
        result = self.turn("Remember Python project work")
        self.assertEqual(self.llm.response, result.reply)
        self.assertTrue(self.turns[-1].memory_query_decision.applicable)
        self.assertEqual("grounded_evidence", self.turns[-1].requirement.evidence_state)
        self.assertEqual(1, len(self.llm.calls))

    def test_shared_recall_preserves_assistant_source_ownership(self):
        self.source("I described the silver telescope.", speaker="assistant")
        self.llm.response = "I remember saying, “I described the silver telescope.”"
        result = self.turn("Do you remember what we talked about with the silver telescope?")
        self.assertEqual(self.llm.response, result.reply)
        self.assertEqual("shared", self.turns[-1].memory_query_decision.requested_speaker)
        self.assertEqual("assistant", self.turns[-1].requirement.evidence[0].speaker_role)

    def test_assistant_favorite_history_accepts_the_production_speech_act(self):
        # Historical indexing labels assistant dialogue 'other', even when
        # its content is a plain assertion. It still owns only assistant history.
        self.source("My favorite food is pizza.", speaker="assistant", speech_act="other")
        self.llm.response = "I said my favorite food was pizza."
        result = self.turn("What did you say your favorite food was?")
        self.assertEqual(self.llm.response, result.reply)
        mixed = self.turn("What did you say your favorite color and food were?")
        self.assertEqual(self.llm.response + " I don't remember saying my favorite color.", mixed.reply)

    def test_independent_assertion_survives_uncertain_peer_in_one_source(self):
        self.source("My favorite color is green. Maybe my favorite food is pizza.")
        self.llm.response = "You said your favorite color was green."
        result = self.turn("What is my favorite color and food?")
        self.assertEqual("You said your favorite color was green. I don't remember you telling me your favorite food.", result.reply)

    def test_governed_values_keep_modifiers_and_multiword_food(self):
        self.llm.response = "Got it."
        self.turn("My favorite color is forest green.")
        self.turn("My favorite food is mac and cheese.")
        self.llm.response = "Your favorite color is green."
        result = self.turn("What is my favorite color and food?")
        self.assertEqual("Your favorite color is forest green. Your favorite food is mac and cheese.", result.reply)

    def test_historical_value_keeps_its_color_modifier(self):
        self.source("My favorite color is dark green.")
        self.llm.response = "I don't remember anything."
        result = self.turn("What is my favorite color and food?")
        self.assertEqual("You said your favorite color was dark green. I don't remember you telling me your favorite food.", result.reply)

    def test_context_truncation_cannot_promote_a_partial_food_value(self):
        self.source("My favorite color is green.")
        self.source("An ordinary note. " * 14 + "My favorite food is slow roasted potatoes with rosemary and garlic.")
        self.llm.response = "You said your favorite color was green."
        result = self.turn("What is my favorite color and food?")
        self.assertEqual("You said your favorite color was green. I don't remember you telling me your favorite food.", result.reply)

    def test_explicit_nonspoken_answer_keeps_caption_and_missing_slot_without_audio(self):
        self.source("My favorite color is green.")
        self.llm.response = json.dumps({
            "dialogue": "You said your favorite color is green.",
            "response_mode": "nonverbal_reaction", "spoken_content": "",
        })
        result = self.turn("What is my favorite color and food?")
        self.assertIn("green", result.reply)
        self.assertIn("I don't remember you telling me your favorite food.", result.reply)
        self.assertEqual([], self.tts.spoken)


if __name__ == "__main__":
    unittest.main()
