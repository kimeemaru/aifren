"""Small streaming sentence/clause chunker; no heavyweight NLP dependency."""

from __future__ import annotations

from config import TTS_CHUNK_HARD_MAX_CHARS, TTS_CHUNK_MIN_CHARS, TTS_CHUNK_PREFERRED_MAX_CHARS


class SpeechChunker:
    def __init__(self, *, minimum: int = TTS_CHUNK_MIN_CHARS,
                 preferred_maximum: int = TTS_CHUNK_PREFERRED_MAX_CHARS,
                 hard_maximum: int = TTS_CHUNK_HARD_MAX_CHARS,
                 minimum_chars: int | None = None, preferred_max_chars: int | None = None,
                 hard_max_chars: int | None = None,
                 preserve_whitespace: bool = False) -> None:
        minimum = minimum if minimum_chars is None else minimum_chars
        preferred_maximum = preferred_maximum if preferred_max_chars is None else preferred_max_chars
        hard_maximum = hard_maximum if hard_max_chars is None else hard_max_chars
        if not 1 <= minimum <= preferred_maximum <= hard_maximum:
            raise ValueError("invalid TTS chunk bounds")
        self.minimum, self.preferred_maximum, self.hard_maximum = minimum, preferred_maximum, hard_maximum
        self._buffer = ""
        self._preserve_whitespace = preserve_whitespace

    def feed(self, delta: object) -> tuple[str, ...]:
        self._buffer += str(delta or "")
        chunks: list[str] = []
        while True:
            boundary = self._best_boundary(final=False)
            if boundary is None:
                break
            chunk = self._take(boundary)
            if chunk:
                chunks.append(chunk)
        return tuple(chunks)

    def finish(self) -> tuple[str, ...]:
        text, self._buffer = self._buffer, ""
        if not self._preserve_whitespace:
            text = text.strip()
        return (text,) if text else ()

    def _best_boundary(self, *, final: bool) -> int | None:
        text = self._buffer
        if not text.strip():
            return None
        sentence = [index + 1 for index, char in enumerate(text) if char in ".!?;" and (index + 1 == len(text) or text[index + 1].isspace())]
        eligible = [index for index in sentence if index >= self.minimum]
        if eligible:
            preferred = [index for index in eligible if index <= self.preferred_maximum]
            if preferred:
                return preferred[-1]
            # A sentence boundary is preferable only while it respects the
            # actual hard limit. Audio8 can produce a repeatable late click on
            # long generations, so an oversized sentence must still split at
            # a substantial clause/word boundary below.
            if eligible[0] <= self.hard_maximum:
                return eligible[0]
        if len(text) < self.hard_maximum:
            return None
        clauses = [index + 1 for index, char in enumerate(text[:self.hard_maximum]) if char in ",:" and index + 1 >= self.minimum]
        if clauses:
            return clauses[-1]
        whitespace = text.rfind(" ", self.minimum, self.hard_maximum)
        return whitespace if whitespace > 0 else self.hard_maximum

    def _take(self, boundary: int) -> str:
        if self._preserve_whitespace:
            value, self._buffer = self._buffer[:boundary], self._buffer[boundary:]
            return value
        value, self._buffer = self._buffer[:boundary].strip(), self._buffer[boundary:].lstrip()
        return value
