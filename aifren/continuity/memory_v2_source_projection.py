"""Bounded query-time passages; the index locates sources, never supplies truth.

No archive scan, embedding call, rewritten quote or persisted derivative. Offsets
refer to the exact source content checked against its canonical record identity.
"""
from dataclasses import dataclass
import re
from typing import Callable, Iterable, Mapping, Sequence

from aifren.memory_v2_store.models import HistoricalSourceSegment
from aifren.dialogue.dialogue_semantics import parse_dialogue, DialogueSpanKind

MAX_SOURCE_CHARACTERS = 100_000
MAX_PASSAGES = 2
MAX_PASSAGE_CHARACTERS = 220  # total, not an increase to the old per-item bound
MAX_SENTENCES = 512


@dataclass(frozen=True)
class SourceProjection:
    segments: tuple[HistoricalSourceSegment, ...] = ()
    reason: str = ""


def passage_substance_rank(segments) -> float:
    """Prefer an account over a topical invitation/question or quoted retelling.

    This only ranks already eligible exact speech. It never proves current
    truth, converts polarity, or adds semantic equivalences to the query.
    """
    if not segments:
        return 0.0
    from aifren.dialogue.dialogue_semantics import spoken_text
    text = spoken_text(segments[0].text).strip()
    if (re.match(r"^(?:I|We|My|Our)\b", text, re.I)
            and not re.search(r"\b(?:if|would|might|could|maybe|perhaps)\b|\?", text, re.I)
            and not re.match(r"^(?:I|We)\s+(?:told|said|mentioned|remember)\b", text, re.I)):
        return 2.5
    return 0.0


def _sentences(raw: str):
    """Find exact spoken spans with the canonical typed emote interpretation."""
    cursor = 0
    ranges = []
    run = None
    for span in parse_dialogue(raw):
        start = raw.find(span.text, cursor)
        if start < 0:
            return ()
        end = start + len(span.text)
        cursor = end
        if span.kind == DialogueSpanKind.EMOTE:
            if run is not None:
                ranges.append((run[0], run[1]))
                run = None
        else:
            if span.kind == DialogueSpanKind.EMPHASIS:
                for marker in ('**', '*'):
                    n = len(marker)
                    if raw[max(0, start-n):start] == marker and raw[end:end+n] == marker:
                        start, end = start-n, end+n
                        break
            if run is None:
                run = [start, end]
            else:
                run[1] = end
    if run is not None:
        ranges.append(tuple(run))
    sentences = []
    for a, b in ranges:
        # Sentence boundaries retain punctuation and exact whitespace. Never
        # cut an overlong sentence to discard its trailing negation/modality.
        for match in re.finditer(r"\S[\s\S]*?(?:[.!?](?=\s|$)|$)", raw[a:b]):
            start, end = a + match.start(), a + match.end()
            if start < end:
                sentences.append((start, end))
            if len(sentences) > MAX_SENTENCES:
                return ()
    return tuple(sentences)


def project_source(messages: Sequence[Mapping[str, object]], index: int, source_id: str,
                   speaker: str, terms: Iterable[str], tokenize: Callable,
                   *, requested_speech_act: str = "", allow_whole_source: bool = False) -> SourceProjection:
    from aifren.continuity.memory_v2_episode_compaction import canonical_record_id
    if not isinstance(index, int) or not 0 <= index < len(messages):
        return SourceProjection(reason="source_changed")
    record = messages[index]
    raw = record.get("content", "")
    if (not isinstance(raw, str) or record.get("role") != speaker
            or len(raw) > MAX_SOURCE_CHARACTERS):
        return SourceProjection(reason="candidate_bound")
    if canonical_record_id(index, record) != source_id:
        return SourceProjection(reason="source_changed")
    wanted = set(terms)
    ranges = _sentences(raw)
    if not ranges:
        return SourceProjection(reason="candidate_bound")
    choices = []
    all_hits = set()
    for i, (start, end) in enumerate(ranges):
        text = raw[start:end]
        hits = wanted & set(tokenize(text))
        if not hits:
            continue
        all_hits.update(hits)
        if re.search(r"\?[!?'\"”]*$", text.rstrip()) and requested_speech_act != "question":
            continue
        # A neighboring qualification belongs with its proposition. If it
        # cannot fit, abstain instead of dropping the qualifier. This is a
        # conservative boundary guard, not semantic inference about activities.
        for j in (i - 1, i + 1):
            if not 0 <= j < len(ranges):
                continue
            a, b = ranges[j]
            neighbor = raw[a:b]
            if (re.match(r"(?:but|however|actually|except|unless|if|suppose|imagine|maybe|perhaps|that was|this was)\b", neighbor, re.I)
                    or re.search(r"\b(?:imagined?|hypothetical|fictional|dream|might|would|could|not|never)\b", neighbor, re.I)):
                start, end = min(start, a), max(end, b)
        text = raw[start:end]
        if len(text) > MAX_PASSAGE_CHARACTERS:
            continue
        # An unbalanced quotation can move a quoted/hypothetical sentence out
        # of its original frame. Leave such sources explicitly limited.
        if text.count('"') % 2 or text.count('“') != text.count('”'):
            continue
        choices.append((start, end, wanted & set(tokenize(text))))
    if not all_hits:
        if allow_whole_source and not (wanted & set(tokenize(raw))):
            if raw and len(raw) <= MAX_PASSAGE_CHARACTERS:
                return SourceProjection((HistoricalSourceSegment(0, len(raw), raw, len(raw)),))
            return SourceProjection(reason="candidate_bound")
        return SourceProjection(reason="irrelevant")
    if not choices:
        return SourceProjection(reason="candidate_bound")
    # Cover the query's terms present in this exact source. Other retrieval
    # gates still decide whether that source answers the requested relation.
    remaining = set(all_hits)
    selected = []
    used = 0
    for _ in range(MAX_PASSAGES):
        eligible = [x for x in choices if x[2] & remaining]
        if not eligible:
            break
        a, b, hits = min(eligible, key=lambda x: (-len(x[2] & remaining), x[1] - x[0], x[0]))
        if used + b - a > MAX_PASSAGE_CHARACTERS:
            return SourceProjection(reason="candidate_bound")
        selected.append((a, b))
        used += b - a
        remaining -= hits
    if remaining:
        return SourceProjection(reason="candidate_bound")
    # Independent exact segments are never manufactured into one quotation.
    return SourceProjection(tuple(HistoricalSourceSegment(a, b, raw[a:b], len(raw))
                                  for a, b in sorted(set(selected))))


def expand_source_segments(candidates):
    """Project independent prompt items, preserving their common source ID."""
    from dataclasses import replace
    for candidate in candidates:
        segments = getattr(candidate, "source_segments", ())
        if not segments:
            yield candidate
        else:
            for segment in segments:
                yield replace(candidate, content=segment.text, source_segments=(segment,))


def segment_identity(candidate):
    return (str(getattr(candidate, "canonical_record_id", "")),
            tuple((s.start, s.end) for s in getattr(candidate, "source_segments", ())))
