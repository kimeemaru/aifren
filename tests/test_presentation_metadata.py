import unittest

from presentation_metadata import StreamingResponseDialogue, parse_assistant_response, response_contract_prompt


class PresentationMetadataTests(unittest.TestCase):
    def test_response_style_requires_star_actions_not_parenthetical_actions(self):
        prompt = response_contract_prompt()
        self.assertIn("Physical actions and emotes MUST use", prompt)
        self.assertIn("MUST NOT use (parentheses)", prompt)
        self.assertIn("Parentheses remain spoken prose", prompt)

    def test_generated_emoji_is_removed_after_json_escape_decoding(self):
        parsed = parse_assistant_response(
            '{"dialogue":"Okay \\ud83d\\ude0a — こんにちは。 \\u2728"}'
        )
        self.assertEqual("Okay — こんにちは。", parsed.dialogue)

    def test_state_effect_presentation_fields_remain_closed_and_semantic(self):
        parsed = parse_assistant_response(
            '{"dialogue":"Mmm.","presentation":{"pose":"sleeping","gaze_mode":"suppressed",'
            '"reaction":"stir","speech_mode":"mumble"}}'
        )
        self.assertEqual("sleeping", parsed.presentation.pose)
        self.assertEqual("suppressed", parsed.presentation.gaze_mode)
        self.assertEqual("stir", parsed.presentation.reaction)
        self.assertEqual("mumble", parsed.presentation.speech_mode)
        rejected = parse_assistant_response(
            '{"dialogue":"No.","presentation":{"pose":"model-specific-clip","reaction":"dance_file.vrma"}}'
        )
        self.assertIsNone(rejected.presentation)

    def test_streaming_contract_projects_only_exact_decoded_dialogue(self):
        stream = StreamingResponseDialogue()
        fragments = (
            '{"dia', 'logue":"  Did', ' the', ' testing\\n',
            'preserve **spoken** emphasis?   ","presentation":',
            '{"emotion":"happy"}}',
        )

        projected = "".join(stream.feed(fragment) for fragment in fragments) + stream.finish()

        self.assertEqual("Did the testing\npreserve **spoken** emphasis?", projected)
        self.assertNotIn("happy", projected)

    def test_valid_envelope_extracts_dialogue_and_normalizes_metadata(self):
        parsed = parse_assistant_response(
            '{"dialogue":"Hello there.","presentation":{"emotion":" HAPPY ","intensity":1.4,"gesture":"greeting"}}'
        )

        self.assertEqual("Hello there.", parsed.dialogue)
        self.assertTrue(parsed.has_presentation_contract)
        self.assertEqual("happy", parsed.presentation.emotion)
        self.assertEqual(1.0, parsed.presentation.intensity)
        self.assertEqual("greeting", parsed.presentation.gesture)

    def test_null_emotion_and_unknown_values_do_not_create_metadata(self):
        parsed = parse_assistant_response(
            '{"dialogue":"Still here.","presentation":{"emotion":"unknown","intensity":"bad","gesture":"unknown"}}'
        )

        self.assertEqual("Still here.", parsed.dialogue)
        self.assertTrue(parsed.has_presentation_contract)
        self.assertIsNone(parsed.presentation)

    def test_null_presentation_preserves_existing_expression_semantics(self):
        parsed = parse_assistant_response('{"dialogue":"Ordinary reply.","presentation":null}')

        self.assertEqual("Ordinary reply.", parsed.dialogue)
        self.assertTrue(parsed.has_presentation_contract)
        self.assertIsNone(parsed.presentation)

    def test_malformed_output_remains_plain_dialogue_and_cannot_break_turn(self):
        raw = '{"dialogue":"unfinished"'
        parsed = parse_assistant_response(raw)

        self.assertEqual("unfinished", parsed.dialogue)
        self.assertFalse(parsed.has_presentation_contract)
        self.assertIsNone(parsed.presentation)

    def test_json_fence_is_tolerated_but_json_is_not_extracted_from_prose(self):
        fenced = '```json\n{"dialogue":"Hi","presentation":null}\n```'
        self.assertEqual("Hi", parse_assistant_response(fenced).dialogue)

        prose = 'Here: {"dialogue":"not a contract"}'
        self.assertEqual(prose, parse_assistant_response(prose).dialogue)

    def test_neutral_and_gesture_are_independent_and_intensity_is_clamped(self):
        parsed = parse_assistant_response(
            '{"dialogue":"All calm.","presentation":{"emotion":"neutral","intensity":-2,"gesture":"thinking"}}'
        )

        self.assertEqual("neutral", parsed.presentation.emotion)
        self.assertEqual(0.0, parsed.presentation.intensity)
        self.assertEqual("thinking", parsed.presentation.gesture)

    def test_relaxed_is_a_closed_persistent_state_expression(self):
        parsed = parse_assistant_response(
            '{"dialogue":"Resting.","presentation":{"emotion":"relaxed","intensity":0.35}}'
        )

        self.assertEqual("relaxed", parsed.presentation.emotion)
        self.assertEqual(0.35, parsed.presentation.intensity)


if __name__ == "__main__":
    unittest.main()
