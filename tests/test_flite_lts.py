"""Synthetic pronunciation-data boundaries; no installed model or network needed."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from aifren.tts import flite_lts as lts


class _Token(SimpleNamespace):
    Underscore = SimpleNamespace


@contextmanager
def synthetic_model(*, states=None, phones=None, indexes=None):
    # A tiny decision tree: next letter 'b' -> k, otherwise aa1.
    states = states if states is not None else bytes([
        4, ord("b"), 1, 0, 2, 0, 255, 2, 0, 0, 0, 0, 255, 1, 0, 0, 0, 0])
    value = {"schema": 1, "revision": lts.REVISION, "window": 4,
             "states_hex": states.hex(), "phones": phones or ["epsilon", "aa1", "k"],
             "indexes": indexes if indexes is not None else [0] * 26}
    content = json.dumps(value).encode()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic_lts.json"
        path.write_bytes(content)
        with patch.object(lts, "MODEL_BYTES", len(content)), patch.object(
                lts, "MODEL_SHA256", hashlib.sha256(content).hexdigest()):
            yield path


class FliteEnglishFallbackTests(unittest.TestCase):
    def test_neighbour_branches_and_word_boundaries(self):
        with synthetic_model() as path:
            fallback = lts.FliteEnglishFallback(path)
        self.assertEqual(("aa1",), fallback.predict("a"))
        self.assertEqual(("k", "aa1"), fallback.predict("ab"))
        self.assertEqual(("ˈɑ", 2), fallback(SimpleNamespace(text="a")))
        self.assertEqual("non_neural_host_rules", fallback.device)

    def test_diphthongs_stress_schwa_and_rhotic_mapping(self):
        self.assertEqual("bˈɜɹd", lts.phones_to_misaki(["b", "er1", "d"]))
        self.assertEqual("kˈʌləɹ", lts.phones_to_misaki(["k", "ah1", "l", "er0"]))
        self.assertEqual("ə k s", " ".join(lts.phones_to_misaki([p]) for p in ("ax0", "k", "s")))

    def test_dual_phones_expand_and_silent_letters_do_not_drop_the_word(self):
        with synthetic_model(states=bytes([255, 1, 0, 0, 0, 0]), phones=["epsilon", "k-s"]) as path:
            fallback = lts.FliteEnglishFallback(path)
        self.assertEqual(("k", "s"), fallback.predict("x"))
        with synthetic_model(states=bytes([255, 0, 0, 0, 0, 0])) as path:
            fallback = lts.FliteEnglishFallback(path)
        with self.assertRaisesRegex(lts.PronunciationUnavailable, "no phonemes"):
            fallback.predict("x")

    def test_accents_ligatures_apostrophes_and_hyphenated_names_keep_words(self):
        self.assertEqual(("eloise", "maeve"), lts._normalized_words("Éloïse-Maeve"))
        self.assertEqual(("o'quinn",), lts._normalized_words("O’Quinn"))
        self.assertEqual(("encyclopaedia",), lts._normalized_words("encyclopædia"))
        with synthetic_model() as path:
            fallback = lts.FliteEnglishFallback(path)
        fallback.predict = Mock(return_value=("m", "ey1", "v"))
        self.assertEqual(("mˈAv mˈAv", 2), fallback(SimpleNamespace(text="Éloïse-Maeve")))
        self.assertEqual(["eloise", "maeve"], [c.args[0] for c in fallback.predict.call_args_list])

    def test_numbers_and_punctuation_reuse_the_owning_misaki_lexicon(self):
        with synthetic_model() as path:
            fallback = lts.FliteEnglishFallback(path)
        lexicon = Mock(side_effect=lambda token, _context: ({"12": "twˈɛlv", "30": "θˈɜɹTi"}.get(token.text), 4))
        fallback.lexicon = lexicon
        fallback.predict = Mock(side_effect=AssertionError("numbers must not reach letter prediction"))
        module = SimpleNamespace(MToken=_Token, PUNCTS={":"},
                                 TokenContext=lambda: None, subtokenize=lambda _: ["12", ":", "30"])
        with patch.dict("sys.modules", {"misaki.en": module}):
            self.assertEqual(("twˈɛlv : θˈɜɹTi", 2), fallback(SimpleNamespace(text="12:30")))
        self.assertEqual(["12", "30"], [call.args[0].text for call in lexicon.call_args_list])

    def test_unresolved_composite_value_never_becomes_silence(self):
        with synthetic_model() as path:
            fallback = lts.FliteEnglishFallback(path, lexicon=lambda *_: (None, None))
        module = SimpleNamespace(MToken=_Token, PUNCTS=set(),
                                 TokenContext=lambda: None, subtokenize=lambda _: ["123"])
        with patch.dict("sys.modules", {"misaki.en": module}), self.assertRaises(lts.PronunciationUnavailable):
            fallback(SimpleNamespace(text="123"))

    def test_real_misaki_number_metadata_is_complete(self):
        from misaki.en import Lexicon
        with synthetic_model() as path:
            fallback = lts.FliteEnglishFallback(path, lexicon=Lexicon(british=False))
        fallback.predict = Mock(side_effect=AssertionError("numeric token reached LTS"))
        # The final G2P owner subsequently projects ɾ -> T; this boundary must
        # retain the owning lexicon's pre-projection value.
        self.assertEqual(("twˈɛlv : θˈɜɹɾi", 2), fallback(SimpleNamespace(text="12:30")))

    def test_unsupported_input_and_work_limit_fail_instead_of_deleting_text(self):
        for text in (None, "", "   ", "東京", "a123", "***", "a" * 129, "a" * 257):
            with self.subTest(text=text), self.assertRaises(lts.PronunciationUnavailable):
                lts._normalized_words(text)

    def test_unknown_phone_and_non_american_variant_fail_visibly(self):
        with self.assertRaises(lts.PronunciationUnavailable):
            lts.phones_to_misaki(["xx1"])
        with self.assertRaisesRegex(lts.PronunciationUnavailable, "American English"):
            lts.FliteEnglishFallback("missing", british=True)

    def test_missing_modified_or_oversized_data_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.json"
            with self.assertRaisesRegex(lts.PronunciationUnavailable, "unavailable"):
                lts.FliteEnglishFallback(path)
            path.write_bytes(b"{}")
            with self.assertRaisesRegex(lts.PronunciationUnavailable, "reviewed identity"):
                lts.FliteEnglishFallback(path)
            path.write_bytes(b"x" * (lts.MODEL_BYTES + 1))
            with self.assertRaisesRegex(lts.PronunciationUnavailable, "reviewed identity"):
                lts.FliteEnglishFallback(path)

    def test_invalid_states_and_roots_are_rejected_before_lookup(self):
        for states in (bytes([8, 1, 0, 0, 0, 0]), bytes([255, 90, 0, 0, 0, 0]), bytes([4, 1, 9, 0, 0, 0])):
            with self.subTest(states=states), synthetic_model(states=states) as path:
                with self.assertRaisesRegex(lts.PronunciationUnavailable, "validation failed"):
                    lts.FliteEnglishFallback(path)
        with synthetic_model(indexes=[100] * 26) as path:
            with self.assertRaises(lts.PronunciationUnavailable):
                lts.FliteEnglishFallback(path)

    def test_cyclic_data_cannot_hold_the_synthesis_lane_indefinitely(self):
        with synthetic_model(states=bytes([4, ord("b"), 0, 0, 0, 0])) as path:
            fallback = lts.FliteEnglishFallback(path)
        with self.assertRaisesRegex(lts.PronunciationUnavailable, "work bound"):
            fallback.predict("a")

    def test_cache_is_bounded_and_does_not_write_pronunciation_data(self):
        with synthetic_model() as path:
            original = path.read_bytes()
            fallback = lts.FliteEnglishFallback(path)
            for i in range(257):
                fallback.predict(chr(97 + i // 26) + chr(97 + i % 26))
            self.assertEqual(original, path.read_bytes())
        self.assertEqual(256, len(fallback._cache))
        self.assertNotIn("aa", fallback._cache)


if __name__ == "__main__":
    unittest.main()
