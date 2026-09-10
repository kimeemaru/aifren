import unittest

from dialogue_semantics import (
    DialogueSpanKind,
    SemanticSentenceAccumulator,
    SemanticSpeechGrouper,
    StableDialogueTextStream,
    parse_dialogue,
    spoken_text,
)


class DialogueSemanticsTests(unittest.TestCase):
    @staticmethod
    def projected_sentences(chunks):
        projection = SemanticSentenceAccumulator()
        return tuple(
            sentence for chunk in chunks for sentence in projection.feed(chunk)
        ) + projection.finish()

    def test_shared_span_rules_preserve_spoken_emphasis_and_remove_emotes(self):
        value = "*waves softly* Hello, *very close* friend. **Absolutely certain.** *this is a longer quiet action*"
        spans = parse_dialogue(value)
        self.assertEqual(
            [DialogueSpanKind.EMOTE, DialogueSpanKind.PLAIN_TEXT, DialogueSpanKind.EMPHASIS,
             DialogueSpanKind.PLAIN_TEXT, DialogueSpanKind.EMPHASIS, DialogueSpanKind.PLAIN_TEXT,
             DialogueSpanKind.EMOTE],
            [span.kind for span in spans],
        )
        self.assertEqual("Hello, very close friend. Absolutely certain.", spoken_text(value))

    def test_stream_holds_single_marker_action_until_closing_delta(self):
        stream = StableDialogueTextStream()
        self.assertEqual("Before ", stream.feed("Before *"))
        self.assertEqual("", stream.feed("leans closer. Still "))
        stable = stream.feed("waiting* After." )
        self.assertEqual("*leans closer. Still waiting* After.", stable)
        self.assertEqual("After.", spoken_text(stable))

    def test_stream_holds_ambiguous_double_marker_until_complete(self):
        stream = StableDialogueTextStream()
        self.assertEqual("Say ", stream.feed("Say *"))
        self.assertEqual("", stream.feed("*very "))
        stable = stream.feed("clearly** now.")
        self.assertEqual("**very clearly** now.", stable)
        self.assertEqual("very clearly now.", spoken_text(stable))

    def test_every_stream_split_matches_whole_projection(self):
        failed_response = (
            "So yes, it’s **PURPLE**! Are we going to celebrate that in a vibrant purple "
            "field? Or maybe... is there a rare purple Pokémon we need to find?!"
        )
        cases = (
            "Before **bold emphasis** after.",
            "Before *quiet emphasis* after.",
            "Before *leans closer and smiles softly* after.",
            "One **bold** phrase and *soft words* together. Next sentence.",
            failed_response,
            "Okay! 😊",
            "That's cute ✨✨",
            "Hello 👩🏽‍💻 friend.",
            "Flag 🇨🇦 and keycap 1️⃣ are display-only.",
        )
        for text in cases:
            wanted = self.projected_sentences((text,))
            for split in range(len(text) + 1):
                with self.subTest(text=text[:28], split=split):
                    self.assertEqual(wanted, self.projected_sentences((text[:split], text[split:])))
            self.assertEqual(wanted, self.projected_sentences(tuple(text)))

    def test_nested_formatting_never_closes_outer_nonspoken_action(self):
        cases = {
            "*I slowly *really* lean closer.*": "",
            "*I **carefully** put the book down.*": "",
            "*She pauses, looking *very* confused.*": "",
            "*I look at you.* Then I say hello.": "Then I say hello.",
            "Hello. *I look *very* confused.* Are you okay?": "Hello. Are you okay?",
            "*Entire action containing multiple *emphasized* words and another *emphasized* phrase.*": "",
            "I *really* mean it.": "I really mean it.",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, spoken_text(raw))
                wanted = self.projected_sentences((raw,))
                for split in range(len(raw) + 1):
                    self.assertEqual(wanted, self.projected_sentences((raw[:split], raw[split:])))
                self.assertEqual(wanted, self.projected_sentences(tuple(raw)))

    def test_nested_action_sequences_preserve_contractions_punctuation_and_order(self):
        raw = (
            "I don't know. *I *really* don't move.*\n"
            "Then I speak. *I **carefully** set it down.* Finally, I'm done."
        )
        self.assertEqual(
            "I don't know.\nThen I speak. Finally, I'm done.",
            spoken_text(raw),
        )

    def test_stream_never_releases_unclosed_action_to_tts(self):
        stream = StableDialogueTextStream()
        self.assertEqual("A safe prefix. ", stream.feed("A safe prefix. *starts an action"))
        self.assertEqual("", stream.finish())

    def test_contextual_emphasis_and_outer_action_ownership(self):
        cases = (
            ("I *really* don't like that.", "I really don't like that."),
            ("*smile*", ""),
            ("*I look *really* confused.*", ""),
            ("Hello. *I smile.* How are you?", "Hello. How are you?"),
            ("I **really** mean this.", "I really mean this."),
            ("*I **carefully** put the book down.*", ""),
            ("Hello. *I look *very* confused.* Are you okay?", "Hello. Are you okay?"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(expected, spoken_text(raw))

    def test_genuine_emphasis_and_parenthetical_prose_remain_spoken(self):
        cases = {
            "I *really* mean it.": "I really mean it.",
            "Hello (softly), it's good to see you.": "Hello (softly), it's good to see you.",
            "(that was *really* strange)": "(that was really strange)",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, spoken_text(raw))

    def test_sentence_projection_preserves_provider_fragment_whitespace(self):
        cases = (
            (("Did", " the", " testing", " work? "), "Did the testing work?"),
            (("Did ", "the ", "testing work", "! "), "Did the testing work!"),
            (("Did", " ", "the", " ", "testing work. "), "Did the testing work."),
            (("Wait", "... ", "Really", "? "), ("Wait...", "Really?")),
        )
        for fragments, expected in cases:
            with self.subTest(fragments=fragments):
                projection = SemanticSentenceAccumulator()
                admitted = tuple(
                    sentence
                    for fragment in fragments
                    for sentence in projection.feed(fragment)
                ) + projection.finish()
                wanted = expected if isinstance(expected, tuple) else (expected,)
                self.assertEqual(wanted, admitted)

    def test_sentence_projection_holds_split_emotes_and_preserves_emphasis(self):
        projection = SemanticSentenceAccumulator()
        fragments = (
            "Hello ", "*leans", " closer and smiles", " warmly* ",
            "my *dear", " friend", "*. ", "Next **clear", " thought**! ",
        )
        admitted = tuple(
            sentence
            for fragment in fragments
            for sentence in projection.feed(fragment)
        ) + projection.finish()
        self.assertEqual(("Hello my dear friend.", "Next clear thought!"), admitted)

    def test_sentence_projection_flushes_safe_final_tail_once(self):
        projection = SemanticSentenceAccumulator()
        self.assertEqual(("A complete sentence.",), projection.feed("A complete sentence. And a final tail"))
        self.assertEqual(("And a final tail",), projection.finish())
        self.assertEqual((), projection.finish())

    def test_speech_grouping_keeps_first_sentence_immediate_then_pairs(self):
        grouping = SemanticSpeechGrouper(maximum_chars=100)
        emitted = []
        for sentence in ("First sentence.", "Second sentence.", "Third sentence.",
                         "Fourth sentence.", "Final tail"):
            emitted.extend(grouping.feed(sentence))
        emitted.extend(grouping.finish())
        self.assertEqual(
            ["First sentence.", "Second sentence. Third sentence.",
             "Fourth sentence. Final tail"],
            emitted,
        )

    def test_speech_grouping_preserves_projected_emotes_emphasis_and_spacing(self):
        projection = SemanticSentenceAccumulator()
        grouping = SemanticSpeechGrouper(maximum_chars=180)
        output = []
        fragments = (
            "Did", " ", "the test work? ", "*leans closer and smiles warmly* ",
            "It **absolutely** did! ", "One final tail",
        )
        for fragment in fragments:
            for sentence in projection.feed(fragment):
                output.extend(grouping.feed(sentence))
        for sentence in projection.finish():
            output.extend(grouping.feed(sentence))
        output.extend(grouping.finish())
        self.assertEqual(
            ["Did the test work?", "It absolutely did! One final tail"],
            output,
        )

    def test_speech_grouping_does_not_make_overlarge_utterances(self):
        grouping = SemanticSpeechGrouper(maximum_chars=80)
        first = "First sentence."
        second = "Second sentence consumes much of the grouping budget."
        third = "Third sentence makes their combined request too long."
        self.assertEqual((first,), grouping.feed(first))
        self.assertEqual((), grouping.feed(second))
        self.assertEqual((second,), grouping.feed(third))
        self.assertEqual((third,), grouping.finish())
