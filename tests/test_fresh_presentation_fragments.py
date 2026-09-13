"""Fresh optional serialization never becomes canonical/spoken transport text."""
from __future__ import annotations

import json
import unittest

from dialogue_semantics import spoken_text
from llm.output_canonicalization import ModelOutputCanonicalizer
from presentation_metadata import parse_assistant_response


CASES = (
    {
        "id": "emote-envelope-and-presentation",
        "raw": '*Rowan shifts his weight slightly, a subtle acknowledgment of the change.*\n'
               '{"dialogue":"Indeed. The red has been replaced by blue on my right hand."}\n'
               '{"presentation":{"emotion":"neutral"}}',
        "canonical": '*Rowan shifts his weight slightly, a subtle acknowledgment of the change.*\n'
                     'Indeed. The red has been replaced by blue on my right hand.',
        "spoken": 'Indeed. The red has been replaced by blue on my right hand.',
        "emotion": "neutral",
    },
    {
        "id": "prose-and-presentation",
        "raw": '*Rowan settles back.* That sounds comfortable.\n'
               '{"presentation":{"emotion":"relaxed","intensity":0.4}}',
        "canonical": '*Rowan settles back.* That sounds comfortable.',
        "spoken": 'That sounds comfortable.',
        "emotion": "relaxed",
    },
)


class FreshPresentationFragmentsTests(unittest.TestCase):
    def parse(self, raw):
        return parse_assistant_response(raw, normalize_generated_dialogue=True)

    def test_captured_closed_shapes_preserve_every_dialogue_and_action_word(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                parsed = self.parse(case["raw"])
                self.assertEqual(case["canonical"], parsed.dialogue)
                self.assertEqual(case["spoken"], spoken_text(parsed.dialogue))
                self.assertEqual(case["emotion"], parsed.presentation.emotion)
                self.assertEqual("valid", parsed.contract_status)
                self.assertTrue(parsed.has_presentation_contract)
                self.assertIsNone(parsed.companion_action)
                self.assertFalse(parsed.response_mode_supplied)
                self.assertIsNone(parsed.spoken_content)
                self.assertIsNotNone(parsed.format_normalization)

    def test_default_and_authoritative_data_parser_remain_unchanged(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["raw"], parse_assistant_response(case["raw"]).dialogue)
                # A typed envelope's dialogue is data. Decoding that value is
                # not a fresh opportunity to execute JSON found inside it.
                encoded = json.dumps({"dialogue": case["raw"]})
                parsed = self.parse(encoded)
                self.assertEqual(case["raw"], parsed.dialogue)
                self.assertIsNone(parsed.presentation)

    def test_each_provider_delta_split_has_identical_final_normalization(self):
        for case in CASES:
            for split in range(len(case["raw"]) + 1):
                with self.subTest(case=case["id"], split=split):
                    stream = ModelOutputCanonicalizer()
                    generated = (stream.feed(case["raw"][:split])
                                 + stream.feed(case["raw"][split:]) + stream.finish())
                    parsed = self.parse(generated)
                    self.assertEqual(case["canonical"], parsed.dialogue)
                    self.assertEqual(case["emotion"], parsed.presentation.emotion)

    def test_multiple_existing_typed_emotes_and_escaped_dialogue_are_preserved(self):
        actions = '*nods*\n*smiles*'
        dialogue = 'You said "maybe". **Not** certain.\nStill tentative.'
        raw = actions + '\n' + json.dumps({"dialogue": dialogue}) + '\n' + json.dumps({
            "presentation": {"gesture": "agreement"}})
        parsed = self.parse(raw)
        self.assertEqual(actions + '\n' + dialogue, parsed.dialogue)
        self.assertEqual("agreement", parsed.presentation.gesture)
        self.assertIsNone(parsed.presentation.emotion)

    def test_quoted_fenced_inline_and_introduced_examples_remain_literal(self):
        presentation = '{"presentation":{"emotion":"relaxed","intensity":0.4}}'
        cases = (
            'This is literal `' + presentation + '`.',
            'Example:\n' + presentation,
            'Here is a JSON example:\n' + presentation,
            '```json\n' + presentation + '\n```',
            '> ' + presentation,
            '"Quoted reply.\n' + presentation + '"',
            '“Quoted reply.\n' + presentation + '”',
            'Use this literal object.\n`' + presentation + '`',
            'Ordinary inline data ' + presentation,
            'An unfinished quoted example.\n```json\n' + presentation,
            'An unfinished quoted example.\n"' + presentation,
        )
        for raw in cases:
            with self.subTest(raw=raw):
                parsed = self.parse(raw)
                default = parse_assistant_response(raw)
                self.assertEqual(default.dialogue, parsed.dialogue)
                self.assertEqual(default.presentation, parsed.presentation)
                self.assertIsNone(parsed.format_normalization)

    def test_conflicts_machine_fields_unknown_shapes_and_ambiguity_are_not_folded(self):
        known = '{"presentation":{"emotion":"happy"}}'
        cases = (
            '*nods*\n{"dialogue":"One.","dialogue":"Two."}\n' + known,
            '*nods*\n{"dialogue":"One.","presentation":{}}\n' + known,
            '*nods*\n{"dialogue":"One.","spoken_content":"different"}\n' + known,
            '*nods*\n{"dialogue":"One.","response_mode":"waking"}\n' + known,
            '*nods*\n{"dialogue":"One.","companion_action":{"operation":"remove"}}\n' + known,
            '*nods*\n{"dialogue":"One.","capability_compliance":["vision"]}\n' + known,
            '*nods*\n{"dialogue":"One."}\n' + known + '\n' + known,
            'One.\n' + known + '\n' + known,
            'One.\n{"presentation":{"emotion":"happy","emotion":"sad"}}',
            'One.\n{"presentation":{"emotion":"happy"},"presentation":{}}',
            'One.\n{"presentation":{"emotion":"happy"},"companion_action":{}}',
            'One.\n{"presentation":{"emotion":"curious"}}',
            'One.\n{"presentation":{"gesture":"private_clip"}}',
            'One.\n{"presentation":{"emotion":"happy","vision_mode":"normal"}}',
            'One.\n{"presentation":{"pose":"sleeping"}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":NaN}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":Infinity}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":true}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":-0.01}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":1.2}}',
            'One.\n{"presentation":{"emotion":"happy","intensity":' + '9' * 1000 + '}}',
            'One.\n{"presentation":{"intensity":0.4}}',
            'One.\n{"presentation":{}}',
            'One.\n{"presentation":',
            'One.\n' + known + '\nMore dialogue.',
            'Introductory prose.\n{"dialogue":"One."}\n' + known,
            '**Emphasis**\n{"dialogue":"One."}\n' + known,
            '*unclosed action\n{"dialogue":"One."}\n' + known,
            'A' * 32768 + '.\n' + known,
        )
        for raw in cases:
            with self.subTest(raw=raw[:150]):
                parsed = self.parse(raw)
                self.assertIsNone(parsed.format_normalization)
                self.assertIsNone(parsed.presentation)
                self.assertIsNone(parsed.companion_action)
                self.assertEqual(parse_assistant_response(raw).dialogue, parsed.dialogue)


class FreshPresentationServiceTests(unittest.TestCase):
    def setUp(self):
        from test_speech_projection_paths import SpeechProjectionServiceTests
        SpeechProjectionServiceTests.setUp(self)

    def cleanup_speech(self):
        from test_speech_projection_paths import SpeechProjectionServiceTests
        SpeechProjectionServiceTests.cleanup_speech(self)

    def check_synthesis(self, case, *, responsive, split=None):
        from test_speech_projection_paths import SpeechProjectionServiceTests
        SpeechProjectionServiceTests.check_synthesis(self, case, responsive=responsive, split=split)

    def test_final_publication_full_and_responsive_speech_exclude_disjoint_json(self):
        for responsive in (False, True):
            for case in CASES:
                for split in (None, case["raw"].index('{') + 1,
                              case["raw"].rindex('"emotion"') + 4):
                    with self.subTest(responsive=responsive, case=case["id"], split=split):
                        before = len(self.llm.calls)
                        events_before = len(self.events)
                        self.check_synthesis(case, responsive=responsive, split=split)
                        self.assertEqual(1, len(self.llm.calls) - before, "Optional format needs no repair")
                        events = self.events[events_before:]
                        response = [e.data for e in events if e.type == "assistant_response"]
                        self.assertEqual(1, len(response))
                        self.assertEqual(case["emotion"], response[0]["presentation"]["emotion"])
                        deltas = ''.join(e.data.get("content", "") for e in events
                                         if e.type == "assistant_delta")
                        self.assertNotIn('"presentation"', deltas)
                        self.assertNotIn('"dialogue"', deltas)

    def test_user_and_prior_canonical_markers_cannot_dispatch_presentation(self):
        raw = 'Literal fixture:\n{"presentation":{"emotion":"angry"}}'
        self.llm.response = "That is literal text."
        result = self.s.process_text_turn(raw, speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(raw, self.s.conversation.messages[-2]["content"])
        self.assertIsNone(getattr(result.presentation, "emotion", None))
        self.s.conversation.add_assistant_message(raw)
        self.s.conversation.save()
        result = self.s.process_text_turn("Let's continue.", speak=False)
        self.assertTrue(result.succeeded, result.error)
        self.assertIsNone(getattr(result.presentation, "emotion", None))
        self.assertIn(raw, [message["content"] for message in self.s.conversation.messages])


if __name__ == "__main__":
    unittest.main()
