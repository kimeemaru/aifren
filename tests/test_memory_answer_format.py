"""Complete grounded dialogue can survive harmless presentation JSON fragmentation."""
import json
import unittest
import threading
from unittest.mock import patch

import test_memory_v2_ordering as ordering_tests
from aifren.dialogue.presentation_metadata import parse_assistant_response
from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerEvidence, compose_memory_answer_requirement, validate_memory_answer_response,
)


class MemoryAnswerFormatParserTests(unittest.TestCase):
    def parse(self, raw):
        return parse_assistant_response(raw, normalize_presentation_format=True)

    def test_split_objects_preserve_exact_dialogue_and_optional_metadata(self):
        dialogue = '*smiles* You said "perhaps"; **not** certain.\nStill tentative.'
        raw = json.dumps({'dialogue': dialogue}) + '\n' + json.dumps({
            'presentation': {'emotion': 'happy', 'intensity': .4}, 'gesture': 'agreement'})
        parsed = self.parse(raw)
        self.assertEqual('valid', parsed.contract_status)
        self.assertEqual(dialogue, parsed.dialogue)
        self.assertEqual('happy', parsed.presentation.emotion)
        self.assertEqual('agreement', parsed.presentation.gesture)
        self.assertEqual('presentation_fragments', parsed.format_normalization)
        self.assertEqual('malformed', parse_assistant_response(raw).contract_status)

    def test_single_misplaced_known_gesture_moves_without_rewriting_dialogue(self):
        parsed = self.parse('{"dialogue":"You said blue.","gesture":"thinking"}')
        self.assertEqual('valid', parsed.contract_status)
        self.assertEqual('thinking', parsed.presentation.gesture)
        self.assertEqual('presentation_gesture_field', parsed.format_normalization)

    def test_unknown_optional_emotion_keeps_existing_closed_metadata_semantics(self):
        parsed = self.parse('{"dialogue":"You said blue."}\n{"presentation":{"emotion":"curious"}}')
        self.assertEqual('valid', parsed.contract_status)
        self.assertIsNone(parsed.presentation)

    def test_no_prose_second_answer_action_or_conflict_can_be_discarded(self):
        first = '{"dialogue":"Your favorite color is blue."}'
        tails = (
            ' Your favorite animal is a fox.',
            '\n{"dialogue":"Your favorite color is green."}',
            '\n{"presentation":{},"companion_action":{"operation":"remove"}}',
            '\n{"presentation":{},"spoken_content":"green"}',
            '\n{"presentation":{}}\n{"presentation":{"emotion":"happy"}}',
            '\n{"presentation":{}} trailing prose',
            '\n{"presentation":{},"gesture":"private_model_clip"}',
            '\n{"presentation":{"gesture":"thinking"},"gesture":"agreement"}',
            '\n{"presentation":{},"presentation":{"emotion":"happy"}}',
        )
        for tail in tails:
            with self.subTest(tail=tail):
                parsed = self.parse(first + tail)
                self.assertNotEqual('valid', parsed.contract_status)
                self.assertIsNone(parsed.format_normalization)

    def test_conflicting_or_duplicate_primary_fields_cannot_be_normalized(self):
        cases = (
            '{"dialogue":"blue","dialogue":"green"}\n{"presentation":{}}',
            '{"dialogue":"blue","presentation":{}}\n{"presentation":{"emotion":"neutral"}}',
            '{"dialogue":"blue","gesture":"thinking","presentation":{"gesture":"agreement"}}',
            '{"dialogue":"blue","unsupported":"data"}\n{"presentation":{}}',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertNotEqual('valid', self.parse(raw).contract_status)

    def test_bounded_input_and_minimal_valid_forms(self):
        raw = json.dumps({'dialogue': 'x' * 32768}) + '\n{"presentation":{}}'
        self.assertIsNone(self.parse(raw).format_normalization)
        for value in ('Your favorite color is blue.', '{"dialogue":"Your favorite color is blue."}'):
            self.assertEqual(parse_assistant_response(value), self.parse(value))


class MemoryAnswerFormatServiceTests(unittest.TestCase):
    setUp = ordering_tests.HistoricalOrderingServiceTests.setUp
    open_service = ordering_tests.HistoricalOrderingServiceTests.open_service
    reopen = ordering_tests.HistoricalOrderingServiceTests.reopen
    learn = ordering_tests.HistoricalOrderingServiceTests.learn
    ask = ordering_tests.HistoricalOrderingServiceTests.ask

    def test_grounded_answer_needs_no_model_repair_for_split_presentation(self):
        self.learn('My favorite color is blue.')
        dialogue = 'Your favorite color is blue.'
        self.llm.response = json.dumps({'dialogue': dialogue}) + '\n' + json.dumps({
            'presentation': {'emotion': 'happy', 'intensity': .4}, 'gesture': 'agreement'})
        before = len(self.llm.calls)
        events = []
        self.service.subscribe(events.append)
        result = self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(dialogue, result.reply)
        self.assertEqual(1, len(self.llm.calls) - before)
        self.assertFalse(self.service._last_memory_authority_diagnostics['repair_attempted'])
        published = [e for e in events if e.type == 'assistant_response']
        self.assertEqual(1, len(published))
        self.assertEqual(dialogue, self.conversation.messages[-1]['content'])
        self.assertEqual('presentation_fragments',
                         self.service._last_memory_authority_diagnostics['primary_format_normalization'])

    def test_wrong_current_value_speaker_and_extra_fact_never_publish(self):
        self.learn('My favorite color is blue.')
        for dialogue in ('Your favorite color is green.', 'My favorite color is blue.',
                         'Blue is my favorite color.',
                         'Your favorite color is blue. My favorite color is blue.',
                         'Your favorite color is blue. You own a motorcycle.',
                         'Your favorite color is blue and your favorite animal is a fox.'):
            with self.subTest(dialogue=dialogue):
                self.llm.response = json.dumps({'dialogue': dialogue}) + '\n{"presentation":{}}'
                before = len(self.llm.calls)
                result = self.ask('What is my favorite color?')
                self.assertTrue(result.succeeded, result.error)
                self.assertEqual('Your favorite color is blue.', result.reply)
                extra_detail = dialogue in (
                    'Your favorite color is blue. You own a motorcycle.',
                    'Your favorite color is blue and your favorite animal is a fox.',
                )
                self.assertEqual(1 if extra_detail else 2, len(self.llm.calls) - before)
                self.assertEqual('bounded_memory_semantic_rejection' if extra_detail else 'none',
                    self.service._last_memory_authority_diagnostics['repair_skipped_reason'])
                self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])

    def test_uncertainty_and_irrelevant_quotes_are_not_admitted_by_format_repair(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message('Maybe I will build a paper boat.', truth_scope=scope)
        self.conversation.add_assistant_message('Understood.', truth_scope=scope)
        self.conversation.save()
        for dialogue in ('You told me you built a paper boat.',
                         'You said "I bought a telescope."'):
            with self.subTest(dialogue=dialogue):
                self.llm.response = json.dumps({'dialogue': dialogue}) + '\n{"presentation":{}}'
                result = self.ask('What did I say about the paper boat?')
                self.assertNotEqual(dialogue, result.reply)
                self.assertNotIn('bought a telescope', result.reply or '')
                self.assertNotIn('you built', (result.reply or '').lower())

    def test_cancelled_fragmented_completion_does_not_publish_or_repair(self):
        self.learn('My favorite color is blue.')
        entered, release = threading.Event(), threading.Event()
        results = []
        events = []
        self.service.subscribe(events.append)
        def generate(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('Synthetic completion gate')
            return '{"dialogue":"Your favorite color is blue."}\n{"presentation":{"emotion":"happy"}}'
        worker = threading.Thread(target=lambda: results.append(self.ask('What is my favorite color?')))
        with patch.object(self.llm, 'generate', side_effect=generate):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.service._handle_ptt_tts_interrupt()
            finally:
                release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual('interrupted', results[0].error)
        self.assertFalse(any(e.type == 'assistant_response' for e in events))
        self.llm.response = '{"dialogue":"Your favorite color is blue."}\n{"presentation":{}}'
        self.assertEqual('Your favorite color is blue.', self.ask('What is my favorite color?').reply)

    def test_order_witness_survives_format_normalization_and_reopen(self):
        self.learn('My favorite color is green.', 'Actually, my favorite color is blue.')
        self.reopen()
        good = 'You said your favorite color was green.'
        self.llm.response = json.dumps({'dialogue': good}) + '\n{"presentation":{}}'
        before = len(self.llm.calls)
        self.assertEqual(good, self.ask('What favorite color did I say before I changed it to blue?').reply)
        self.assertEqual(1, len(self.llm.calls) - before)
        for bad in ('You said your favorite color was blue.',
                    'You said your favorite color was green after you changed it to blue.'):
            self.llm.response = json.dumps({'dialogue': bad}) + '\n{"presentation":{}}'
            result = self.ask('What favorite color did I say before I changed it to blue?')
            self.assertEqual(good, result.reply)
            self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])

    def test_safe_natural_historical_paraphrase_keeps_one_call(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message('I built a little paper boat.', truth_scope=scope)
        self.conversation.add_assistant_message('Understood.', truth_scope=scope)
        self.conversation.save()
        dialogue = 'You told me you built a little paper boat.'
        self.llm.response = json.dumps({'dialogue': dialogue}) + '\n{"presentation":{"emotion":"happy"}}'
        before = len(self.llm.calls)
        result = self.ask('What did I say about the paper boat?')
        self.assertEqual(dialogue, result.reply)
        self.assertEqual(1, len(self.llm.calls) - before)

    def test_historical_pronoun_tail_cannot_add_a_property_after_grounded_recall(self):
        scope = self.service.truth_scope_provenance()
        self.conversation.add_user_message(
            'I watched a bronze paper lantern beside a stone bridge.', truth_scope=scope)
        self.conversation.add_assistant_message('Understood.', truth_scope=scope)
        self.conversation.save()
        grounded = 'You said you watched a bronze paper lantern beside a stone bridge.'
        for lead in (grounded, 'You mean the bronze paper lantern you watched beside a stone bridge?'):
            self.llm.response = json.dumps({'dialogue': lead +
                ' Was that when...? It was electric and purple!'})
            result = self.ask('What did I say about the paper lantern?')
            self.assertTrue(result.succeeded, result.error)
            self.assertNotIn('electric', result.reply)
            self.assertTrue(self.service._last_memory_authority_diagnostics['fallback_used'])
        self.llm.response = json.dumps({'dialogue': grounded + ' It was bronze.'})
        before = len(self.llm.calls)
        result = self.ask('What did I say about the paper lantern?')
        self.assertEqual(grounded + ' It was bronze.', result.reply)
        self.assertEqual(1, len(self.llm.calls) - before)

    def test_failed_canonical_save_cannot_publish_normalized_draft(self):
        self.learn('My favorite color is blue.')
        self.llm.response = '{"dialogue":"Your favorite color is blue."}\n{"presentation":{"emotion":"happy"}}'
        events = []
        self.service.subscribe(events.append)
        original = self.conversation.save
        def fail_assistant_save(*args, **kwargs):
            if self.conversation.messages[-1]['role'] == 'assistant':
                raise OSError('Synthetic save failure')
            return original(*args, **kwargs)
        with patch.object(self.conversation, 'save', side_effect=fail_assistant_save):
            result = self.ask('What is my favorite color?')
        self.assertFalse(result.succeeded)
        self.assertFalse(any(e.type == 'assistant_response' for e in events))


class HistoricalContinuationAdmissionTests(unittest.TestCase):
    source = 'I watched a bronze paper lantern beside a stone bridge.'
    answer = 'You said you watched a bronze paper lantern beside a stone bridge.'

    def requirement(self, source=None, other=()):
        item = MemoryAnswerEvidence('lantern-record', 'historical_conversation_only',
                                    'user', 'real_world', 'assertion', source or self.source)
        return compose_memory_answer_requirement('What did I say about the paper lantern?', (item, *other))

    def test_same_source_property_question_and_present_opinion_remain_valid(self):
        requirement = self.requirement()
        for tail in ('It was bronze.', 'Was it bronze?', 'That sounds peaceful.', '*Smiles warmly.*'):
            with self.subTest(tail=tail):
                result = validate_memory_answer_response(requirement, self.answer + ' ' + tail)
                self.assertTrue(result.accepted, result)

    def test_short_property_negation_and_extra_property_cannot_be_added(self):
        for tail in ('It was red.', 'It was not bronze.', 'It was bronze and decorated.',
                     'Was that when...? It looked purple.'):
            with self.subTest(tail=tail):
                result = validate_memory_answer_response(self.requirement(), self.answer + ' ' + tail)
                self.assertFalse(result.accepted, result)

    def test_other_record_or_speaker_cannot_supply_anaphoric_property(self):
        for speaker in ('user', 'assistant'):
            other = MemoryAnswerEvidence('other-record', 'historical_conversation_only',
                speaker, 'real_world', 'assertion', 'I watched a purple balloon beside a stone bridge.')
            result = validate_memory_answer_response(
                self.requirement(other=(other,)), self.answer + ' It was purple.')
            self.assertFalse(result.accepted, result)

    def test_ambiguous_antecedent_does_not_choose_another_record(self):
        other = MemoryAnswerEvidence('another-lantern', 'historical_conversation_only',
            'user', 'real_world', 'assertion', self.source)
        result = validate_memory_answer_response(
            self.requirement(other=(other,)), self.answer + ' It was bronze.')
        self.assertIn('historical_continuation_antecedent_unproved', result.violations)

    def test_implicit_reference_requires_unique_historical_support_too(self):
        lead = 'You mean the bronze paper lantern you watched beside a stone bridge?'
        for tail, accepted in (('It was bronze.', True), ('It was purple.', False)):
            result = validate_memory_answer_response(self.requirement(), lead + ' ' + tail)
            self.assertEqual(accepted, result.accepted, result)
        other = MemoryAnswerEvidence('other-record', 'historical_conversation_only',
            'user', 'real_world', 'assertion', 'I watched a purple balloon beside a stone bridge.')
        result = validate_memory_answer_response(
            self.requirement(other=(other,)), lead + ' It was purple.')
        self.assertIn('historical_continuation_antecedent_unproved', result.violations)

    def test_tentative_source_does_not_license_certain_continuation(self):
        requirement = self.requirement(source='Perhaps ' + self.source)
        result = validate_memory_answer_response(
            requirement, 'You said perhaps you watched a bronze paper lantern beside a stone bridge. It was bronze.')
        self.assertFalse(result.accepted, result)


if __name__ == '__main__':
    unittest.main()
