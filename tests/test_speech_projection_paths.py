"""Synthetic accepted replies cross the real service and both synthesis owners."""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from aifren.dialogue.dialogue_semantics import SemanticSentenceAccumulator, spoken_text
from aifren.llm.output_canonicalization import ModelOutputCanonicalizer
from aifren.dialogue.presentation_metadata import parse_assistant_response


FIXTURES = json.loads((Path(__file__).parent / "fixtures" /
                       "dialogue_speech_projection.json").read_text())["cases"]


class SpeechProjectionBoundaryTests(unittest.TestCase):
    def test_fresh_decoded_dialogue_normalization_is_opt_in_and_idempotent(self):
        for case in FIXTURES:
            raw = json.dumps({"dialogue": case["raw"]})
            with self.subTest(case=case["id"]):
                default = parse_assistant_response(raw)
                self.assertEqual(case["raw"], default.dialogue)
                fresh = parse_assistant_response(raw, normalize_generated_dialogue=True)
                self.assertEqual(case["canonical"], fresh.dialogue)
                again = parse_assistant_response(json.dumps({"dialogue": fresh.dialogue}),
                    normalize_generated_dialogue=True)
                self.assertEqual(fresh, again)

    def test_generated_boundary_and_every_provider_delta_split(self):
        for case in FIXTURES:
            raw = case["raw"]
            for split in range(len(raw) + 1):
                with self.subTest(case=case["id"], split=split):
                    stream = ModelOutputCanonicalizer()
                    canonical = stream.feed(raw[:split]) + stream.feed(raw[split:]) + stream.finish()
                    self.assertEqual(case["canonical"], canonical)
                    parsed = parse_assistant_response(canonical)
                    self.assertEqual(case["spoken"], spoken_text(parsed.dialogue))

    def test_complete_literal_projection_is_independent_of_each_delta_boundary(self):
        # Early speech keeps its existing conservative unfinished-marker
        # policy. Complete literal spans/escapes must never become actions
        # merely because the provider split a delimiter or escape sequence.
        for case in FIXTURES:
            if not (case["id"].startswith("literal-") or case["id"] == "escaped-literal"):
                continue
            for split in range(len(case["canonical"]) + 1):
                with self.subTest(case=case["id"], split=split):
                    stream = SemanticSentenceAccumulator()
                    result = stream.feed(case["canonical"][:split]) + stream.feed(case["canonical"][split:]) + stream.finish()
                    self.assertEqual(case["spoken"], " ".join(result))


class SpeechProjectionServiceTests(unittest.TestCase):
    def setUp(self):
        from test_natural_companion import NaturalServiceTests
        from test_responsive_speech import SyntheticKokoro, StreamFactory, audio_owner
        NaturalServiceTests.setUp(self)
        self.s.conversation_style = "roleplay"
        self.s.explicit_avatar_cues = False
        self.s.automatic_expressions = False
        self.provider = SyntheticKokoro()
        self.device = StreamFactory()
        audio_patch = patch.object(audio_owner.sd, "OutputStream", self.device)
        audio_patch.start(); self.addCleanup(audio_patch.stop)
        self.s.tts = self.provider
        self.s._configure_tts_playback_events()
        self.queues = []
        self.addCleanup(self.cleanup_speech)

    def cleanup_speech(self):
        self.s.stop_speaking()
        for queue in self.queues:
            queue.cancel(); queue.join(3)

    def check_synthesis(self, case, *, responsive, split=None):
        self.s.responsive_speech = responsive
        self.llm.response = case["raw"]
        if split is None:
            if hasattr(self.llm, "stream_generate"):
                del self.llm.stream_generate
        else:
            def stream(context, prompt, **_kwargs):
                self.llm.calls.append((tuple(context), prompt))
                yield case["raw"][:split]
                yield case["raw"][split:]
            self.llm.stream_generate = stream
        units_before = len(self.provider.units)
        events_before = len(self.events)
        committed = []
        def before(_index, _text):
            conversation = self.s.conversation
            committed.append(conversation.is_message_persisted(
                len(conversation.messages) - 1, conversation.messages[-1]))
        self.provider.before_unit = before
        result = self.s.process_text_turn("Please give your response.", speak=True)
        queue = self.s._streaming_speech_queue
        if queue is not None:
            self.queues.append(queue); queue.join(3)
            self.assertEqual("completed", queue.outcome)
        else:
            self.assertTrue(self.provider.playback_finished.wait(3))
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(case["canonical"], result.reply)
        self.assertEqual(case["spoken"], result.spoken_text)
        self.assertEqual(case["spoken"], "".join(self.provider.units[units_before:]))
        self.assertEqual(case["canonical"], self.s.conversation.messages[-1]["content"])
        events = self.events[events_before:]
        replies = [e.data for e in events if e.type == "assistant_response"]
        self.assertEqual([case["canonical"]], [e["content"] for e in replies])
        starts = [e.data for e in events if e.type == "tts_state" and e.data.get("state") == "playback_started"]
        if case["spoken"]:
            self.assertTrue(committed and all(committed))
            self.assertTrue(starts)
            self.assertEqual(1, len({e["playback_id"] for e in starts}))
            if responsive:
                self.assertTrue(all(e["complete_text"] == case["spoken"] for e in starts))
        else:
            self.assertEqual([], starts)
            self.assertEqual([], committed)

    def test_actual_full_and_responsive_synthesis_receive_only_permitted_words(self):
        for responsive in (False, True):
            for case in FIXTURES:
                with self.subTest(case=case["id"], responsive=responsive):
                    self.check_synthesis(case, responsive=responsive)

    def test_fresh_act_is_separated_but_quoted_and_user_syntax_are_inert(self):
        from aifren.dialogue.act_presentation import PREFIX
        self.s.explicit_avatar_cues = True
        for responsive in (False, True):
            case = {"raw": "<|ACT:emotion=happy|>*I look *really* confused.* Wait, what?",
                    "canonical": "*I look *really* confused.* Wait, what?", "spoken": "Wait, what?"}
            with self.subTest(responsive=responsive):
                self.check_synthesis(case, responsive=responsive)
                response = [e.data for e in self.events if e.type == "assistant_response"][-1]
                self.assertEqual("happy", response["presentation"]["emotion"])
                literal = next(c for c in FIXTURES if c["id"] == "literal-act")
                self.check_synthesis(literal, responsive=responsive)
                response = [e.data for e in self.events if e.type == "assistant_response"][-1]
                self.assertFalse(response["has_presentation"])
        self.llm.response = "That is literal text."
        user = "The text `<|ACT:emotion=angry|>` is an example."
        result = self.s.process_text_turn(user, speak=False)
        self.assertTrue(result.succeeded, result.error)
        # Capability metadata may legitimately be present. User-authored
        # syntax must not acquire an explicit presentation owner/channel.
        self.assertIsNone(getattr(result.presentation, "emotion", None))
        self.assertIsNone(getattr(result.presentation, "gesture", None))
        self.assertNotEqual("act", getattr(result.presentation, "origin", None))
        self.assertEqual(user, self.s.conversation.messages[-2]["content"])
        self.assertIn(PREFIX, self.s.conversation.messages[-2]["content"])

    def test_nested_action_at_each_delimiter_split_through_both_service_paths(self):
        case = next(c for c in FIXTURES if c["id"] == "parenthesized-nested-single")
        boundaries = {0, len(case["raw"])}
        for index, character in enumerate(case["raw"]):
            if character in "*()":
                boundaries.update((index, index + 1))
        for responsive in (False, True):
            for split in sorted(boundaries):
                with self.subTest(responsive=responsive, split=split):
                    self.check_synthesis(case, responsive=responsive, split=split)

    def test_fresh_structured_dialogue_reaches_same_full_and_responsive_projection(self):
        for responsive in (False, True):
            for case in FIXTURES:
                if case["id"] not in {"parenthesized-nested-single", "nested-double", "literal-quoted-action"}:
                    continue
                encoded = dict(case, raw=json.dumps({"dialogue": case["raw"]}))
                for split in (None, encoded["raw"].index("*")):
                    with self.subTest(case=case["id"], responsive=responsive, split=split):
                        self.check_synthesis(encoded, responsive=responsive, split=split)


if __name__ == "__main__":
    unittest.main()
