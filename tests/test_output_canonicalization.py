import unittest

from llm.output_canonicalization import ModelOutputCanonicalizer, canonicalize_model_output


class ModelOutputCanonicalizationTests(unittest.TestCase):
    def test_complete_reasoning_block_is_removed_without_touching_final_answer(self):
        raw = "<think>private analysis\nwith details</think>\n\n*waves* Hello there."
        self.assertEqual("*waves* Hello there.", canonicalize_model_output(raw))

    def test_markers_and_reasoning_may_cross_arbitrary_stream_boundaries(self):
        canonicalizer = ModelOutputCanonicalizer()
        chunks = ("<th", "ink>do not ", "show</thi", "nk>\nFinal ", "answer.")
        visible = [canonicalizer.feed(chunk) for chunk in chunks]
        visible.append(canonicalizer.finish())

        self.assertEqual("Final answer.", "".join(visible))
        self.assertFalse(any("do not show" in chunk for chunk in visible))
        self.assertFalse(any("think" in chunk.lower() for chunk in visible))

    def test_multiple_and_unclosed_reasoning_blocks_never_leak(self):
        self.assertEqual("One. Two.", canonicalize_model_output(
            "<THINK>first</THINK> One. <think>second</think>Two."
        ))
        self.assertEqual("Visible. ", canonicalize_model_output("Visible. <think>unfinished private text"))

    def test_ordinary_angle_bracket_text_is_unchanged(self):
        ordinary = "Use <emphasis> only when the format asks for it."
        self.assertEqual(ordinary, canonicalize_model_output(ordinary))

    def test_generated_emoji_are_removed_without_damaging_unicode(self):
        self.assertEqual("Okay!", canonicalize_model_output("Okay! 😊"))
        self.assertEqual("That's cute", canonicalize_model_output("That's cute ✨✨"))
        self.assertEqual("", canonicalize_model_output("❤️"))
        self.assertEqual("日本語の返事。", canonicalize_model_output("日本語の返事。"))

    def test_streamed_emoji_projection_matches_whole_output(self):
        raw = "Fine 1️⃣ — 日本語 👍🏽 family 👩‍👩‍👧‍👦."
        expected = canonicalize_model_output(raw)
        canonicalizer = ModelOutputCanonicalizer()
        chunks = ("Fine 1", "️", "⃣ — 日本", "語 👍", "🏽 family 👩‍", "👩‍👧", "‍👦.")
        actual = "".join(canonicalizer.feed(chunk) for chunk in chunks) + canonicalizer.finish()
        self.assertEqual(expected, actual)
        self.assertEqual("Fine — 日本語 family.", actual)


    def test_parenthetical_action_normalization_is_stream_equivalent(self):
        cases = (
            "(smiles)",
            "(She looks away and folds her arms.)",
            "Though... (she pauses, sniffing the air dramatically)...it feels fuzzy.",
            "I think that's fine (for now).",
            "The value is approximately 12 (depending on rounding).",
            'The literal "(She looks away)" is quoted.',
            "I saw her (she looks away when nervous).",
            "(Mrow!) Ordinary dialogue.",
            "*She pauses (for a moment) and looks away.* Fine.",
        )
        for raw in cases:
            expected = canonicalize_model_output(raw)
            for split in range(len(raw) + 1):
                with self.subTest(raw=raw, split=split):
                    canonicalizer = ModelOutputCanonicalizer()
                    actual = (
                        canonicalizer.feed(raw[:split])
                        + canonicalizer.feed(raw[split:])
                        + canonicalizer.finish()
                    )
                    self.assertEqual(expected, actual)

        self.assertEqual("*smiles*", canonicalize_model_output("(smiles)"))
        self.assertEqual(
            "*She looks away and folds her arms.*",
            canonicalize_model_output("(She looks away and folds her arms.)"),
        )
        self.assertEqual("I think that's fine (for now).", canonicalize_model_output(
            "I think that's fine (for now)."
        ))
        self.assertEqual(
            "The value is approximately 12 (depending on rounding).",
            canonicalize_model_output("The value is approximately 12 (depending on rounding)."),
        )
        self.assertEqual(
            'The literal "(She looks away)" is quoted.',
            canonicalize_model_output('The literal "(She looks away)" is quoted.'),
        )
        self.assertEqual(
            "I saw her (she looks away when nervous).",
            canonicalize_model_output("I saw her (she looks away when nervous)."),
        )
        self.assertEqual("(Mrow!) Ordinary dialogue.", canonicalize_model_output(
            "(Mrow!) Ordinary dialogue."
        ))


if __name__ == "__main__":
    unittest.main()
