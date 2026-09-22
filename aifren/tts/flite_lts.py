"""Offline English unknown-word fallback for Misaki, using CMU Flite LTS data.

Original Python evaluator of the pinned six-byte CMU decision-tree format.
Tree/context semantics: Alan W Black and Kevin A. Lenzo, Carnegie Mellon
University. See docs/ENGLISH_G2P_PROVENANCE.md for the permissive source/data
licence, attribution and modifications. No Flite synthesis engine is used.
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata


REPOSITORY = "festvox/flite"
REVISION = "6c9f20dc915b17f5619340069889db0aa007fcdc"
MODEL_FILENAME = "cmu_lts.json"
MODEL_SHA256 = "1020599f803c75641ca680208bd06cc5d0e5e31ed2742e59086542838466b1b7"
MODEL_BYTES = 306_767
_IPA = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "W", "AY": "I",
    "AX": "ə", "B": "b", "CH": "ʧ", "D": "d", "DH": "ð", "EH": "ɛ",
    "ER": "ɜɹ", "EY": "A", "F": "f", "G": "ɡ", "HH": "h", "IH": "ɪ",
    "IY": "i", "JH": "ʤ", "K": "k", "L": "l", "M": "m", "N": "n",
    "NG": "ŋ", "OW": "O", "OY": "Y", "P": "p", "R": "ɹ", "S": "s",
    "SH": "ʃ", "T": "t", "TH": "θ", "UH": "ʊ", "UW": "u", "V": "v",
    "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)*\Z", re.ASCII)


class PronunciationUnavailable(RuntimeError):
    """A complete supported pronunciation is unavailable; never skip the word."""


def phones_to_misaki(phones) -> str:
    """Convert CMU phone names to the stock American-English Kokoro alphabet."""
    result = []
    for phone in phones:
        phone = phone.upper()
        stress = phone[-1:] if phone[-1:] in {"0", "1", "2"} else ""
        base = phone[:-1] if stress else phone
        if base not in _IPA:
            raise PronunciationUnavailable("English pronunciation contains an unsupported phoneme.")
        sound = _IPA[base]
        if stress == "0" and base in {"AH", "ER"}:
            sound = "ə" if base == "AH" else "əɹ"
        result.append(("ˈ" if stress == "1" else "ˌ" if stress == "2" else "") + sound)
    if not result:
        raise PronunciationUnavailable("English pronunciation produced no phonemes.")
    return "".join(result)


def _normalized_words(text: str) -> tuple[str, ...]:
    if not isinstance(text, str) or not text.strip() or len(text) > 256:
        raise PronunciationUnavailable("English fallback token is empty or exceeds its work bound.")
    folded = text.lower().translate(str.maketrans({"’": "'", "‘": "'", "æ": "ae", "œ": "oe"}))
    folded = "".join(c for c in unicodedata.normalize("NFKD", folded)
                     if unicodedata.category(c) != "Mn")
    words = tuple(part for part in re.split(r"[-\s]+", folded) if part)
    if not words or not all(_WORD.fullmatch(word) and len(word) <= 128 for word in words):
        raise PronunciationUnavailable("English fallback received an unsupported token; speech was not omitted.")
    return words


class FliteEnglishFallback:
    """A small non-neural LTS evaluator; no inference-device fallback is involved.

    Bind ``lexicon`` to the owning Misaki G2P's lexicon after construction. It
    remains the owner of numeric/composite subtokens such as clock times.
    No download, native library, subprocess or persistent word cache is used.
    """

    device = "non_neural_host_rules"

    def __init__(self, model_path: str | Path, *, british: bool = False, lexicon=None):
        if british:
            raise PronunciationUnavailable("The bundled fallback supports American English only.")
        try:
            with Path(model_path).open("rb") as stream:
                content = stream.read(MODEL_BYTES + 1)
        except OSError as error:
            raise PronunciationUnavailable("The bundled English pronunciation data is unavailable.") from error
        if len(content) != MODEL_BYTES or hashlib.sha256(content).hexdigest() != MODEL_SHA256:
            raise PronunciationUnavailable("English pronunciation data differs from the reviewed identity.")
        try:
            data = json.loads(content)
            if data["schema"] != 1 or data["revision"] != REVISION or data["window"] != 4:
                raise ValueError("unsupported data version")
            states = bytes.fromhex(data["states_hex"])
            count = len(states) // 6
            phones, indexes = tuple(data["phones"]), tuple(data["indexes"])
            if not count or len(states) % 6 or count > 65536 or len(indexes) != 26:
                raise ValueError("invalid table dimensions")
            if not all(type(index) is int and 0 <= index < count for index in indexes):
                raise ValueError("invalid root")
            if not phones or phones[0] != "epsilon":
                raise ValueError("invalid phone table")
            for phone in phones[1:]:
                phones_to_misaki(phone.split("-"))
            for offset in range(0, len(states), 6):
                feature, value, tl, th, fl, fh = states[offset:offset + 6]
                if feature == 255:
                    if value >= len(phones):
                        raise ValueError("invalid leaf")
                elif feature >= 8 or tl + 256 * th >= count or fl + 256 * fh >= count:
                    raise ValueError("invalid branch")
        except (KeyError, TypeError, ValueError, PronunciationUnavailable) as error:
            raise PronunciationUnavailable("English pronunciation data validation failed.") from error
        self._states, self._phones, self._indexes = states, phones, indexes
        self.lexicon = lexicon
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()

    def predict(self, word: str) -> tuple[str, ...]:
        normalized = _normalized_words(word)
        if len(normalized) != 1:
            raise PronunciationUnavailable("English prediction requires one word.")
        word = normalized[0]
        with self._lock:
            if word in self._cache:
                self._cache.move_to_end(word)
                return self._cache[word]
            # Flite uses four neighbours on either side, '#' word boundaries,
            # and '0' padding. Apostrophes affect context but emit no phone.
            padded = "000#" + word + "#000"
            result = []
            for position, letter in enumerate(word, 4):
                if letter == "'":
                    continue
                context = padded[position - 4:position] + padded[position + 1:position + 5]
                state = self._indexes[ord(letter) - ord("a")]
                for _ in range(256):
                    offset = state * 6
                    feature, value, tl, th, fl, fh = self._states[offset:offset + 6]
                    if feature == 255:
                        break
                    state = tl + 256 * th if ord(context[feature]) == value else fl + 256 * fh
                else:
                    raise PronunciationUnavailable("English pronunciation exceeded its work bound.")
                phone = self._phones[value]
                if phone != "epsilon":
                    result.extend(phone.split("-"))
            phones = tuple(result)
            phones_to_misaki(phones)  # Never report an empty word as success.
            self._cache[word] = phones
            if len(self._cache) > 256:
                self._cache.popitem(last=False)
            return phones

    def __call__(self, token) -> tuple[str, int]:
        text = getattr(token, "text", None)
        try:
            words = _normalized_words(text)
        except PronunciationUnavailable:
            if not self.lexicon or not isinstance(text, str) or len(text) > 256:
                raise
            from misaki.en import MToken, PUNCTS, TokenContext, subtokenize
            parts = subtokenize(text)
            if not parts or "".join(parts) != text:
                raise PronunciationUnavailable("English fallback could not preserve its complete token.")
            sounds = []
            for part in parts:
                if part in PUNCTS:
                    sounds.append(part)
                    continue
                if all(c in "-_" for c in part):
                    sounds.append(" ")
                    continue
                item = MToken(text=part, tag="CD" if part.isdecimal() else "NN", whitespace="",
                              _=MToken.Underscore(is_head=True, num_flags=""))
                phonemes, _rating = self.lexicon(item, TokenContext())
                if phonemes is None:
                    phonemes = " ".join(phones_to_misaki(self.predict(word))
                                        for word in _normalized_words(part))
                if not phonemes:
                    raise PronunciationUnavailable("English fallback could not pronounce a complete token.")
                sounds.append(phonemes)
            return " ".join(sounds), 2
        return " ".join(phones_to_misaki(self.predict(word)) for word in words), 2
