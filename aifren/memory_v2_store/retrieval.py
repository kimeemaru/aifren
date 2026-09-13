"""Deterministic, local V2 retrieval over the governed SQLite store.

It has no access to V1 persistence or prompt construction. Shadow dual-read
may invoke it for telemetry; the same seam is prompt-facing only inside the
explicit guarded Development V2-authority mode. Normal launches remain V1.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import inspect
import math
import re
from typing import Iterable

from aifren.memory_v2_store.models import (
    HistoricalOrderWitness, HistoricalSourceSegment, RetrievalHealth, RetrievalLaneHealth, RetrievalOutcome, RetrievalQuery, RetrievalTrace, TypedMemory,
)
from aifren.continuity.memory_query_decision import MemoryQueryDecision, decide_memory_query
from aifren.continuity.memory_v2_source_projection import project_source, passage_substance_rank

from .store import HISTORICAL_EVIDENCE, MemoryV2Store, parse_timestamp_us, utc_now_us


_STOP_WORDS = {
    "a", "an", "and", "are", "about", "can", "did", "do", "for", "how", "i", "in", "is", "it", "me", "my",
    "of", "our", "please", "the", "tell", "to", "we", "what", "where", "when", "you", "your", "now", "still", "today", "was", "day", "food", "weather", "read", "these", "not",
}
_ALIASES = {
    "n64": ("nintendo", "64"), "allergy": ("allerg",), "allergic": ("allerg",),
    "preferred": ("prefer",), "lived": ("live",), "lives": ("live",), "work": ("engineer",),
    # Common conversational abbreviation, not fixture-specific wording.
    "mic": ("microphone",),
    # Common irregular event verbs let an asserted-action query compare its
    # premise with stored evidence without pretending topical similarity is
    # evidence that the action occurred.
    "bought": ("buy",), "built": ("build",), "felt": ("feel",),
    "found": ("find",), "left": ("leave",), "lost": ("lose",),
    "made": ("make",), "met": ("meet",), "sold": ("sell",),
    "took": ("take",), "went": ("go",),
}
_HISTORICAL_WORDS = {"was", "were", "did", "used", "historical", "then", "previously"}
_GENERIC_REQUEST_WORDS = {"could", "should", "would", "have", "been", "something", "interesting", "solve", "problem", "recommend", "recommendation"}
_MEDICAL_QUERY_TERMS = {"allerg", "sensitivity", "sensitive", "sick"}
_MEDICAL_GENERIC_TERMS = _MEDICAL_QUERY_TERMS | {"food", "make", "remind", "remember"}
_EXPLICIT_ATTRIBUTE_TERMS = {
    "address", "code", "color", "colour", "date", "name", "number", "serial", "size",
}
_HISTORICAL_GENERIC_TERMS = {
    "ago", "another", "back", "before", "connect", "connected", "conversation", "detail", "earlier",
    "different", "even", "give", "go", "history", "just", "later", "long",
    "first", "latest", "memory", "mention", "most", "oddly", "old", "place",
    "plan", "previously", "recent", "relate",
    "related", "remember", "say", "specific", "something", "talk", "tell",
    "then", "thing", "think", "time", "together", "use", "word",
}
_ASSERTED_RELATION = re.compile(
    r"\b(?:i|we)\s+(?:said\s+(?:i|we)\s+)?"
    r"(?P<verb>own(?:ed)?|have|had|bought|buy|sold|sell|visited?|went|go|built?|made|used?|live[ds]?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RetrievalLimits:
    """Hard limits for this test-only lexical/structural retrieval engine."""

    exact_candidates: int = 8
    fts_candidates: int = 12
    structural_candidates: int = 12
    semantic_candidates: int = 12
    final_count: int = 3
    token_budget: int = 180


@dataclass
class _Candidate:
    row: object
    channels: set[str]
    channel_ranks: dict[str, int]
    exact_strength: float = 0.0
    fts_score: float = 0.0
    lexical_overlap: int = 0
    semantic_score: float = 0.0
    semantic_rank: int = 99_999
    source_segments: tuple[HistoricalSourceSegment, ...] = ()


@dataclass(frozen=True)
class _RetrievalIntent:
    kind: str
    slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SemanticRows:
    rows: list[tuple[str, float]]
    health: RetrievalLaneHealth


def _retrieval_intent(decision: MemoryQueryDecision) -> _RetrievalIntent:
    """Project the shared authority decision into retrieval trace vocabulary."""
    return _RetrievalIntent(decision.retrieval_intent, decision.retrieval_slots)


def _infer_intent(query_text: str, _query_tokens=()) -> _RetrievalIntent:
    """Backward-compatible benchmark adapter over the shared decision.

    Older diagnostic harnesses import this private name.  Keep the seam while
    ensuring it owns no grammar and cannot diverge from runtime authority.
    """
    return _retrieval_intent(decide_memory_query(query_text))


def _has_specific_medical_conflict(query_tokens: tuple[str, ...], content_tokens: set[str]) -> bool:
    """Do not convert a named allergy/sensitivity into a different named fact."""
    if not (set(query_tokens) & _MEDICAL_QUERY_TERMS):
        return False
    requested = {token for token in query_tokens if token not in _MEDICAL_GENERIC_TERMS and len(token) >= 4}
    known = {token for token in content_tokens if token not in _MEDICAL_GENERIC_TERMS and len(token) >= 4}
    return bool(requested and known and not (requested & known))


def _has_explicit_attribute_mismatch(
    query_tokens: tuple[str, ...], content_tokens: set[str],
) -> bool:
    """Reject a nearby entity fact that does not support the asked attribute.

    This is an abstention gate, not attribute inference. It only applies when
    the user explicitly names one of a small concrete attributes.
    """
    requested = set(query_tokens) & _EXPLICIT_ATTRIBUTE_TERMS
    if not requested or requested & content_tokens:
        return False
    # Natural facts usually state an attribute value rather than its class:
    # "Pesto wears a violet collar" does not need the literal word "color".
    # Permit that representation only when the candidate independently agrees
    # on at least three concrete entity/relation terms. A nearby object such as
    # a passport case or Nintendo 64 still cannot answer its missing number or
    # color from one or two topical terms.
    relation_overlap = (set(query_tokens) - requested) & content_tokens
    return len(relation_overlap) < 3


def _event_terms(text: str) -> set[str]:
    """Normalize event verbs without losing silent-e grammatical variants."""
    result: set[str] = set()
    for raw in re.findall(r"[a-z]+", text.casefold()):
        for term in _ALIASES.get(raw, (raw,)):
            result.add(term)
            if term.endswith("ing") and len(term) > 5:
                result.update((term[:-3], term[:-3] + "e"))
            elif term.endswith("ed") and len(term) > 4:
                result.update((term[:-2], term[:-2] + "e"))
            elif term.endswith("s") and len(term) > 4:
                result.add(term[:-1])
    return result


def _has_asserted_event_mismatch(query_text: str, content_text: str) -> bool:
    """Reject topical neighbors that do not support an asserted user action."""
    match = re.search(r"\bdid\s+(?:i|we)\s+(?:ever\s+)?([a-z]+)\b", query_text.casefold())
    if match is None:
        return False
    action_tokens = _event_terms(match.group(1))
    # Generic auxiliaries do not identify the event; downstream temporal or
    # relationship lanes may resolve questions such as "What did we do?".
    action_tokens -= {"be", "do", "have"}
    return bool(action_tokens and not (action_tokens & _event_terms(content_text)))


def _has_possessive_role_mismatch(query_text: str, content_tokens: set[str]) -> bool:
    """Require a named personal role when a query asks who fills that role."""
    match = re.search(r"\bwho\s+(?:is|was)\s+my\s+([a-z][a-z-]*)\b", query_text.casefold())
    if match is None:
        return False
    role_tokens = set(_tokens(match.group(1)))
    return bool(role_tokens and not (role_tokens & content_tokens))


def _tokens(text: str) -> tuple[str, ...]:
    result = []
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if raw.isdigit() and len(raw) == 4:
            continue
        expanded = _ALIASES.get(raw, (raw,))
        for token in expanded:
            if token.endswith("ing") and len(token) > 5:
                token = token[:-3]
                if token.endswith("k"):
                    token += "e"
            elif token.endswith("ed") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            if token and token not in _STOP_WORDS:
                result.append(token)
    return tuple(dict.fromkeys(result))


def _historical_distinctive_terms(tokens: Iterable[str]) -> set[str]:
    """Discard normalized request language before broad history search.

    ``_tokens`` deliberately stems words, so comparing its output to raw
    English constants lets generic contractions/stems masquerade as entities.
    A broad historical prompt needs at least one content-bearing term; a
    one-character contraction fragment can never provide that identity.
    """
    generic = set(_tokens(" ".join(
        sorted(_HISTORICAL_GENERIC_TERMS | _GENERIC_REQUEST_WORDS)
    )))
    generic.update({
        "any", "going", "haven", "if", "one", "or", "re", "recently", "that",
    })
    return {token for token in tokens if len(token) > 1 and token not in generic}


def historical_callback_source_kind(query_text: str) -> str | None:
    """Compatibility adapter over the single shared per-query classifier."""
    return decide_memory_query(query_text).callback_source_kind or None


def _safe_fts_query(tokens: Iterable[str]) -> str:
    """Quote normalized alphanumeric terms so punctuation cannot alter FTS syntax."""
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens if token)


def _historical_time(
    query: RetrievalQuery, decision: MemoryQueryDecision | None = None,
) -> tuple[bool, int]:
    # Standalone benchmarks historically called this helper directly. Keep
    # that compatibility seam without restoring a second intent grammar.
    decision = decision or decide_memory_query(query.current_user_text)
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", query.current_user_text)
    tokens = set(_tokens(query.current_user_text))
    lower = query.current_user_text.lower()
    if "not in " in lower or "these days" in lower or "currently" in lower:
        return False, parse_timestamp_us(query.at)
    if decision.historical or query.mode == "historical" or years or tokens & _HISTORICAL_WORDS:
        year = int(years[-1]) if years else datetime.fromisoformat(query.at.replace("Z", "+00:00")).year
        return True, int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1_000_000)
    # Ordinary retrieval asks for current truth.  It must use the current
    # lifecycle state, not the timestamp at which a client happened to issue
    # the query; otherwise a correction recorded after that timestamp can
    # resurrect superseded claims in a normal current-truth result.
    return False, utc_now_us()


def _typed_label(row, historical: bool) -> str:
    if row["claim_type"] == HISTORICAL_EVIDENCE:
        return "HISTORICAL CONVERSATION EVIDENCE — NOT CURRENT TRUTH"
    if row["provenance_state"] != "complete":
        return "LEGACY UNVERIFIED"
    # ``shared_episode`` is the canonical V2 repository type.  Keep the
    # legacy fixture spelling for backward-compatible diagnostic fixtures,
    # but never make the production spelling a second-class retrieval type.
    if row["claim_type"] in {"shared_episode", "episode", "relationship", "running_joke"}:
        return "SHARED EPISODE"
    if row["claim_type"] in {"temporary_state", "future_event"}:
        return "TEMPORARY/PLAN"
    if historical or row["effective_status"] == "superseded":
        return "HISTORICAL USER FACT"
    return "CURRENT USER FACT"


class SemanticRetrievalV2:
    """Derived-index, deterministic candidate retrieval for synthetic V2 stores."""

    def __init__(self, store: MemoryV2Store, limits: RetrievalLimits | None = None, embedding_provider=None, *, allow_legacy_unverified: bool = False, include_historical_evidence: bool = False, enable_explicit_attribute_gate: bool = True, ann_ef: int = 4096, ann_candidate_multiplier: int = 16):
        self.store = store
        self.limits = limits or RetrievalLimits()
        self.embedding_provider = embedding_provider
        self.allow_legacy_unverified = allow_legacy_unverified
        self.include_historical_evidence = bool(include_historical_evidence)
        self.enable_explicit_attribute_gate = bool(enable_explicit_attribute_gate)
        self.ann_ef = max(1, int(ann_ef))
        self.ann_candidate_multiplier = max(1, int(ann_candidate_multiplier))
        if min(self.limits.exact_candidates, self.limits.fts_candidates, self.limits.structural_candidates, self.limits.semantic_candidates, self.limits.final_count, self.limits.token_budget) < 1:
            raise ValueError("retrieval limits must be positive")
        self.store.ensure_fts()

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        recent_visible_claim_ids: Iterable[str] = (),
        recently_used_claim_ids: Iterable[str] = (),
        embedding_state: str | None = None,
        final_count: int | None = None,
        token_budget: int | None = None,
        truth_scope_id: str | None = None,
        memory_query_decision: MemoryQueryDecision | None = None,
        canonical_messages=None,
    ) -> RetrievalOutcome:
        """Return typed claims and privacy-safe traces; never constructs a prompt."""
        if embedding_state in {"stale", "failed", "retryable"}:
            return self._abstain(query, "embedding_state_not_current", health=RetrievalHealth((
                RetrievalLaneHealth("semantic", "incomplete", "embedding", "embedding_not_current"),
            )))
        truth_scope_id = self.store._require_truth_scope(
            query.character_id,
            (
                self.store.active_truth_scope_id(query.character_id)
                if truth_scope_id is None else truth_scope_id
            ),
            require_active=False,
        )
        decision = memory_query_decision or decide_memory_query(query.current_user_text)
        if decision.source_order is not None:
            return self._retrieve_source_order(query, decision, truth_scope_id, final_count, token_budget)
        historical, at_us = _historical_time(query, decision)
        current_query_tokens = _tokens(query.current_user_text)
        explicit_historical_recall = bool(decision.applicable and decision.historical)
        interaction_shorthand = self._interaction_shorthand(
            query.current_user_text, current_query_tokens,
        )
        use_historical_evidence = bool(
            self.include_historical_evidence
            and (explicit_historical_recall or interaction_shorthand)
        )
        distinctive_historical_terms = _historical_distinctive_terms(
            current_query_tokens,
        )
        allowed_historical_speakers = decision.allowed_historical_speakers
        # Historical occurrence evidence must agree with the current user
        # turn. Letting unrelated recent turns supply its terms can make a
        # vague recall question select a plausible but unrelated old event.
        # The default governed-claim path retains its established bounded
        # recent-user context; only the explicit shadow evidence lane uses the
        # stricter current-turn query.
        retrieval_text = (
            query.current_user_text
            if use_historical_evidence
            else " ".join((query.current_user_text, *query.recent_user_turns))
        )
        query_tokens = _tokens(retrieval_text)
        if not query_tokens:
            return self._abstain(query, "no_searchable_terms")
        intent = _retrieval_intent(decision)
        if intent.kind in {"assistant_opinion", "generic_reasoning", "ambiguous_memory"}:
            return self._abstain(query, f"intent_{intent.kind}", intent=intent)
        exact_terms = self._exact_terms(query.current_user_text)
        lanes = []

        def lookup(lane, call, *, applicable=True):
            if not applicable:
                lanes.append(RetrievalLaneHealth(lane, "unused", "routing", "not_applicable"))
                return []
            try:
                rows = call()
            except Exception:
                lanes.append(RetrievalLaneHealth(lane, "incomplete", "lookup", "lookup_failed"))
                return []
            lanes.append(RetrievalLaneHealth(lane, "complete"))
            return rows

        exact_ids = lookup("exact", lambda: self.store.exact_claim_ids(
            query.character_id, exact_terms, self.limits.exact_candidates,
            truth_scope_id=truth_scope_id,
            include_historical_evidence=use_historical_evidence,
        ), applicable=bool(exact_terms))
        fts_tokens = (
            tuple(sorted(distinctive_historical_terms))
            if use_historical_evidence and distinctive_historical_terms
            else query_tokens
        )
        if use_historical_evidence:
            # FTS stores the original words, whereas final relevance uses
            # normalized terms. A query's "chasing" becomes "chas" there;
            # sending only that stem to an exact-word index loses even a
            # literal source match. Include raw spellings of the same topical
            # terms without expanding the requested subject or FTS syntax.
            raw_terms = (
                word for word in re.findall(r"[a-z0-9]+", retrieval_text.lower())
                if set(_tokens(word)) & distinctive_historical_terms
            )
            fts_tokens = tuple(dict.fromkeys((*fts_tokens, *raw_terms)))
        fts_rows = lookup("lexical", lambda: self.store.search_fts(
            query.character_id, _safe_fts_query(fts_tokens), self.limits.fts_candidates,
            truth_scope_id=truth_scope_id,
            include_historical_evidence=use_historical_evidence,
        ))
        semantic_method = self._semantic_rows
        semantic_parameters = inspect.signature(semantic_method).parameters
        accepts_options = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in semantic_parameters.values()
        )
        if accepts_options or "memory_query_decision" in semantic_parameters:
            semantic_rows = semantic_method(
                query, retrieval_text, truth_scope_id,
                memory_query_decision=decision,
            )
        else:
            # Preserve the existing private evaluation/test override seam.
            # Production's implementation always receives the exact shared
            # decision through the branch above.
            semantic_rows = semantic_method(query, retrieval_text, truth_scope_id)
        if isinstance(semantic_rows, _SemanticRows):
            lanes.append(semantic_rows.health)
            semantic_rows = semantic_rows.rows
        else:
            # The legacy controlled override returns rows directly and raises
            # on execution failure. Production always supplies typed health.
            lanes.append(RetrievalLaneHealth("semantic", "complete"))
        structural_types = self._structural_types(query_tokens)
        structural_rows = lookup("structural", lambda: self.store.structural_claims(
            query.character_id, at_us, historical=historical, claim_types=structural_types,
            limit=self.limits.structural_candidates, truth_scope_id=truth_scope_id,
            include_historical_evidence=use_historical_evidence,
        ), applicable=bool(structural_types))
        health = RetrievalHealth(tuple(lanes))
        candidate_ids = tuple(dict.fromkeys([*exact_ids, *(row["claim_id"] for row in fts_rows), *(row[0] for row in semantic_rows), *(row["claim_id"] for row in structural_rows)]))
        eligible_rows = self.store.structural_claims(
            query.character_id, at_us, historical=historical, claim_ids=candidate_ids,
            truth_scope_id=truth_scope_id,
            include_historical_evidence=use_historical_evidence,
        ) if candidate_ids else []
        eligible_states = {"complete"}
        if self.allow_legacy_unverified:
            eligible_states.add("legacy_unverified")
        eligible = {row["claim_id"]: row for row in eligible_rows if row["provenance_state"] in eligible_states}
        if use_historical_evidence:
            # The historical occurrence lane has its own bounded derived
            # corpus.  Do not admit an ordinary claim merely because it was
            # a neighbour in a differently worded historical-shadow query;
            # ordinary retrieval retains its unchanged default path.
            eligible = {
                claim_id: row for claim_id, row in eligible.items()
                if row["claim_type"] == HISTORICAL_EVIDENCE
            }
        if not eligible:
            return self._abstain(query, "no_structurally_eligible_claims", health=health)

        candidates: dict[str, _Candidate] = {}
        def add(claim_id, channel, rank, *, exact=0.0, fts=0.0, semantic=0.0):
            row = eligible.get(claim_id)
            if row is None:
                return
            candidate = candidates.setdefault(claim_id, _Candidate(row, set(), {}))
            candidate.channels.add(channel)
            candidate.channel_ranks[channel] = min(rank, candidate.channel_ranks.get(channel, rank))
            candidate.exact_strength = max(candidate.exact_strength, exact)
            candidate.fts_score = max(candidate.fts_score, fts)
            if semantic > candidate.semantic_score:
                candidate.semantic_score = semantic
                candidate.semantic_rank = rank if channel == "semantic" else candidate.semantic_rank

        for rank, claim_id in enumerate(exact_ids, 1):
            row = eligible.get(claim_id)
            if row is None:
                continue
            hits = sum(1 for term in exact_terms if term in row["content"].lower())
            add(claim_id, "exact", rank, exact=float(hits))
        for rank, row in enumerate(fts_rows, 1):
            # bm25 ranks lower-is-better; retain only a visible positive component.
            add(row["claim_id"], "fts", rank, fts=1.0 / rank)
        for rank, (claim_id, score) in enumerate(semantic_rows, 1):
            add(claim_id, "semantic", rank, semantic=score)

        for rank, row in enumerate(structural_rows, 1):
            add(row["claim_id"], "structural", rank)

        if not candidates:
            return self._abstain(query, "no_relevant_candidates", health=health)

        historical_ids = tuple(
            claim_id for claim_id, candidate in candidates.items()
            if candidate.row["claim_type"] == HISTORICAL_EVIDENCE
        )
        historical_metadata = self.store.historical_evidence_metadata(
            query.character_id, historical_ids,
            limit=min(256, max(1, len(historical_ids))),
        ) if historical_ids else {}

        visible = set(recent_visible_claim_ids)
        recently_used = set(recently_used_claim_ids)
        explicit_repeat = query.mode == "explicit_repeat"
        traces = []
        scored = []
        projection_error = ""
        for claim_id, candidate in candidates.items():
            row = candidate.row
            content_tokens = set(_tokens(row["content"] + " " + row["claim_type"]))
            candidate.lexical_overlap = len(set(query_tokens) & content_tokens)
            historical_row = historical_metadata.get(claim_id)
            if historical_row is not None:
                # The recall wrapper ("remember me saying one...") is not
                # evidence of the requested subject. Use the same distinctive
                # terms already owned by historical FTS selection for final
                # lexical admission/ranking, including broad vector candidates.
                candidate.lexical_overlap = len(distinctive_historical_terms & content_tokens)
                speaker_role = str(historical_row["speaker_role"])
                if speaker_role not in allowed_historical_speakers:
                    traces.append(self._trace(
                        candidate, intent,
                        exclusion="historical_source_speaker_not_requested",
                    ))
                    continue
                if speaker_role == "user" and not int(historical_row["retrieval_eligible"]):
                    traces.append(self._trace(
                        candidate, intent,
                        exclusion="historical_source_not_user_retrievable",
                    ))
                    continue
                if speaker_role == "assistant" and str(
                    historical_row["source_class"]
                ) != "ordinary_conversation":
                    traces.append(self._trace(
                        candidate, intent,
                        exclusion="historical_assistant_source_not_conversational",
                    ))
                    continue
                if not interaction_shorthand and not explicit_historical_recall:
                    traces.append(self._trace(candidate, intent, exclusion="historical_recall_not_requested"))
                    continue
                if explicit_historical_recall and not distinctive_historical_terms:
                    traces.append(self._trace(candidate, intent, exclusion="historical_query_too_unspecific"))
                    continue
                if speaker_role == "user" and (
                    str(historical_row["speech_act"]) == "question"
                    and decision.requested_speech_act != "question"
                ):
                    traces.append(self._trace(
                        candidate, intent,
                        exclusion="historical_question_not_requested",
                    ))
                    continue
                query_hash = hashlib.sha256(query.current_user_text.encode("utf-8")).hexdigest()
                if not interaction_shorthand and query_hash == str(
                    historical_row["source_content_sha256"]
                ):
                    traces.append(self._trace(candidate, intent, exclusion="historical_query_echo"))
                    continue
                if self._historical_premise_mismatch(
                    query.current_user_text, str(row["content"]),
                    str(historical_row["speech_act"]), decision,
                ):
                    traces.append(self._trace(candidate, intent, exclusion="historical_premise_mismatch"))
                    continue
                if canonical_messages is not None:
                    projection = project_source(
                        canonical_messages, int(historical_row["canonical_index"]),
                        str(historical_row["canonical_record_id"]), speaker_role,
                        distinctive_historical_terms, _tokens,
                        requested_speech_act=decision.requested_speech_act,
                        allow_whole_source=candidate.semantic_score >= 0.70,
                    )
                    if not projection.segments:
                        if projection.reason != "irrelevant":
                            projection_error = projection.reason
                        traces.append(self._trace(candidate, intent, exclusion="historical_source_projection_" + projection.reason))
                        continue
                    candidate.source_segments = projection.segments
                    content_tokens = set(_tokens(" ".join(s.text for s in projection.segments)))
                    candidate.lexical_overlap = len(distinctive_historical_terms & content_tokens)
            if claim_id in visible and not explicit_repeat:
                traces.append(self._trace(candidate, intent, exclusion="recent_visible", suppression="recent_visible"))
                continue
            if claim_id in recently_used and not explicit_repeat:
                traces.append(self._trace(candidate, intent, exclusion="recently_used", suppression="topic_cooldown"))
                continue
            # Candidate lanes are deliberately broad.  Final eligibility is
            # stricter: rank-one FTS alone is not relevance evidence.
            single_domain_term = len(query_tokens) == 1 and len(query_tokens[0]) >= 4
            historical_direct = bool(
                historical_row is not None
                and candidate.lexical_overlap >= 1
                and candidate.channels & {"exact", "fts"}
                and distinctive_historical_terms & content_tokens
            )
            lexical_eligible = (
                candidate.lexical_overlap >= 2
                or (single_domain_term and candidate.lexical_overlap == 1)
                or historical_direct
            )
            structural_direct = (
                intent.kind in {"user_memory", "historical_user_fact", "multi_user_memory"}
                # A shared broad type (for example two preferences) is not
                # enough to surface a nearby but wrong fact.  Require a
                # second concrete overlap; semantic/FTS lanes still handle
                # genuine paraphrases without this permissive shortcut.
                and row["claim_type"] in structural_types
                and (
                    candidate.lexical_overlap >= 2
                    # "Where do I live now?" is a narrow, typed location
                    # question even though it shares just the normalized
                    # ``live`` token with a stored location fact.
                    or (row["claim_type"] == "location" and candidate.lexical_overlap >= 1)
                )
            )
            semantic_eligible = candidate.semantic_score >= 0.55
            paraphrase_eligible = (
                intent.kind in {"user_memory", "historical_user_fact", "multi_user_memory"}
                # A rank-one vector alone becomes noisy in a long history.
                # Keep this below the strong semantic lane, but high enough
                # for genuine indirect episode phrasing in the local MiniLM.
                and candidate.semantic_rank == 1 and candidate.semantic_score >= 0.40
            )
            episode_paraphrase_eligible = (
                row["claim_type"] in {"shared_episode", "episode"}
                # Compact episodic accounts are commonly queried without the
                # literal user/companion phrasing retained by their source.
                # Keep the relaxation rank-one and close to the strong global
                # semantic gate; ordinary facts retain the stricter contract.
                and candidate.semantic_rank == 1 and candidate.semantic_score >= 0.50
            )
            exact_eligible = candidate.exact_strength > 0
            if (
                historical_row is not None
                and candidate.lexical_overlap == 0
                and candidate.semantic_score < 0.70
            ):
                traces.append(self._trace(
                    candidate, intent,
                    exclusion="historical_content_agreement_too_weak",
                ))
                continue
            if _has_specific_medical_conflict(query_tokens, content_tokens):
                traces.append(self._trace(candidate, intent, exclusion="specific_medical_entity_mismatch"))
                continue
            # The source record proves the outer act of saying/asking/discussing;
            # its literal text need not narrate that act again. Ownership and
            # speech-act checks above, including the inner premise check, still
            # apply. Ordinary asserted actions keep their existing relation gate.
            source_speech_request = historical_row is not None and decision.intent in {
                "user_historical_source", "assistant_historical_source",
                "shared_historical_conversation",
            }
            if not source_speech_request and _has_asserted_event_mismatch(query.current_user_text, str(row["content"])):
                traces.append(self._trace(candidate, intent, exclusion="asserted_event_mismatch"))
                continue
            if _has_possessive_role_mismatch(query.current_user_text, content_tokens):
                traces.append(self._trace(candidate, intent, exclusion="possessive_role_mismatch"))
                continue
            if (
                self.enable_explicit_attribute_gate
                and intent.kind != "multi_user_memory"
                and _has_explicit_attribute_mismatch(query_tokens, content_tokens)
            ):
                traces.append(self._trace(candidate, intent, exclusion="explicit_attribute_mismatch"))
                continue
            if not (
                exact_eligible or lexical_eligible or structural_direct
                or semantic_eligible or paraphrase_eligible or episode_paraphrase_eligible
            ):
                traces.append(self._trace(candidate, intent, exclusion="below_final_relevance_gate"))
                continue
            # Importance is deliberately bounded: it cannot make irrelevant
            # records eligible because the hard overlap gate precedes scoring.
            legacy_penalty = 0.20 if row["provenance_state"] == "legacy_unverified" else 0.0
            identifier_penalty = 6.0 if row["claim_type"] == "identifier" and not ({"serial", "code", "identifier"} & set(query_tokens)) else 0.0
            score = (
                candidate.exact_strength * 8.0
                + (candidate.fts_score * 2.0 if lexical_eligible and not exact_eligible else 0.0)
                + candidate.lexical_overlap * 2.0
                + passage_substance_rank(candidate.source_segments)
                + max(0.0, candidate.semantic_score - 0.55) * 4.0
                + min(row["importance"], 10) * 0.05
                + (0.25 if historical else 0.5)
                - legacy_penalty
                - identifier_penalty
            )
            scored.append((score, candidate))

        if projection_error:
            health = RetrievalHealth((*health.lanes, RetrievalLaneHealth(
                "claims", "incomplete", "source", projection_error,
            )))
        if not scored:
            traces.append(self._summary_trace(query, candidates, 0, 0, 0, "all_candidates_suppressed_or_weak", intent))
            return RetrievalOutcome(traces=tuple(traces), abstention_reason="all_candidates_suppressed_or_weak", health=health)

        final_limit = min(final_count or self.limits.final_count, self.limits.final_count)
        budget = min(token_budget or self.limits.token_budget, self.limits.token_budget)
        selected = []
        used_tokens = 0
        covered_slots = set()
        def ranking(item):
            score, candidate = item
            content_tokens = set(_tokens(candidate.row["content"] + " " + candidate.row["claim_type"]))
            coverage = sum(1 for slot in intent.slots if slot in content_tokens and slot not in covered_slots)
            return (-coverage, -score, candidate.row["claim_id"])
        remaining = list(scored)
        while remaining:
            # Re-evaluate coverage after each choice so a first strong match
            # cannot starve a separately requested slot.
            score, candidate = min(remaining, key=ranking)
            remaining.remove((score, candidate))
            row = candidate.row
            segments = candidate.source_segments
            content = segments[0].text if segments else row["content"]
            estimate = max(1, (sum(len(s.text) for s in segments) + 3) // 4) if segments else max(1, (len(content) + 3) // 4)
            if len(selected) >= final_limit:
                traces.append(self._trace(candidate, intent, exclusion="final_count_cap"))
                continue
            if used_tokens + estimate > budget:
                traces.append(self._trace(candidate, intent, exclusion="token_budget_cap"))
                continue
            rank = len(selected) + 1
            selected.append((candidate, TypedMemory(row["claim_id"], _typed_label(row, historical), content, estimate, source_segments=segments)))
            used_tokens += estimate
            content_tokens = set(_tokens(row["content"] + " " + row["claim_type"]))
            claim_slots = tuple(slot for slot in intent.slots if slot in content_tokens)
            covered_slots.update(claim_slots)
            traces.append(self._trace(candidate, intent, selected_rank=rank, selection="selected", covered_slots=claim_slots))
            if intent.slots and set(intent.slots).issubset(covered_slots):
                break

        if not selected:
            traces.append(self._summary_trace(query, candidates, len(candidates), 0, used_tokens, "final_budget_exhausted", intent))
            # Relevant original evidence was found but could not be projected
            # within this lane's bound. That is not a successful empty lookup.
            # Keep the bound and use the existing recoverable health contract;
            # do not truncate away source qualifiers to manufacture an answer.
            bounded_health = RetrievalHealth((*health.lanes, RetrievalLaneHealth(
                "claims", "incomplete", "source", "candidate_bound",
            )))
            return RetrievalOutcome(traces=tuple(traces), abstention_reason="final_budget_exhausted", health=bounded_health)
        traces.append(self._summary_trace(query, candidates, len(candidates), len(selected), used_tokens, None, intent, tuple(sorted(covered_slots))))
        memories = tuple(memory for _, memory in selected)
        return RetrievalOutcome(
            claim_ids=tuple(memory.claim_id for memory in memories), selected_memories=memories,
            traces=tuple(traces), health=health,
        )

    def _retrieve_source_order(self, query, decision, scope_id, final_count, token_budget):
        """Bounded same-family FTS lookup; canonical indices prove order.

        Similarity, current claims, episode summaries and timestamps cannot
        supply either side. A full candidate page is unresolved, not complete.
        """
        from types import SimpleNamespace
        from aifren.continuity.memory_v2_evidence_sufficiency import admit_requested_slots

        order = decision.source_order
        complete = RetrievalHealth((RetrievalLaneHealth("claims", "complete"),
                                    RetrievalLaneHealth("semantic", "unused", "routing", "not_applicable")))
        def empty(reason, incomplete=False):
            return RetrievalOutcome(abstention_reason="source_order_" + reason,
                health=RetrievalHealth((RetrievalLaneHealth("claims", "incomplete", "source", "candidate_bound"),))
                if incomplete else complete)
        if not self.include_historical_evidence or not order.supported:
            return empty("unsupported_anchor")
        # AND narrows to the literal relation family; never search only the
        # correction's value, which would preferentially retrieve the anchor.
        key = '"color" OR "colour"' if order.slot.key == "color" else _safe_fts_query((order.slot.key,))
        safe_query = '("favorite" OR "favourite") AND (' + key + ')'
        rows = self.store.search_fts(query.character_id, safe_query, 65,
            truth_scope_id=scope_id, include_historical_evidence=True)
        if len(rows) >= 65:
            return empty("candidate_bound", True)
        ids = tuple(row["claim_id"] for row in rows)
        metadata = self.store.historical_evidence_metadata(query.character_id, ids)
        eligible = self.store.structural_claims(query.character_id, utc_now_us(), historical=True,
            claim_ids=ids, truth_scope_id=scope_id, include_historical_evidence=True) if ids else []
        base_decision = replace(decision, source_order=None)
        values = []
        for row in eligible:
            m = metadata.get(row["claim_id"])
            if (m is None or row["provenance_state"] != "complete" or
                str(m["speaker_role"]) != order.anchor_speaker or
                str(m["scope_state"]) not in {"real_world", "scenario"} or
                str(row["truth_scope_id"]) != scope_id or
                str(m["source_class"]) != "ordinary_conversation" or
                (order.anchor_speaker == "user" and not int(m["retrieval_eligible"]))):
                continue
            c = SimpleNamespace(content=str(row["content"]), speaker_role=str(m["speaker_role"]),
                                speech_act=str(m["speech_act"]), authority_class="historical_conversation_only")
            slots = admit_requested_slots((c,), base_decision)
            if slots and len(slots[0].values) == 1:
                values.append((row, m, slots[0].values[0]))
        # A correction anchor needs explicit source correction/update wording,
        # not merely a different value somewhere in history.
        def is_anchor(value):
            row, meta, literal = value
            if literal.casefold() != order.anchor_value.casefold():
                return False
            text = re.sub(r"^Historical (?:user|assistant) (?:record|statement|question|interaction):\s*", "", str(row["content"]), flags=re.I)
            return order.anchor_kind == "statement" or bool(re.match(
                r"\s*(?:actually\b|correction\b|(?:I\s+)?(?:changed|corrected|updated)\b)", text, re.I)
                or re.search(r"\bmy\s+favou?rite\s+\w+\s+is\s+now\b", text, re.I))
        anchors = [v for v in values if is_anchor(v)]
        if len(anchors) != 1:
            return empty("anchor_missing" if not anchors else "anchor_ambiguous")
        _, anchor, _ = anchors[0]
        index = int(anchor["canonical_index"])
        answers = [v for v in values if (int(v[1]["canonical_index"]) < index if order.direction == "before"
                                         else int(v[1]["canonical_index"]) > index)]
        distinct = {v[2].casefold() for v in answers}
        if len(distinct) != 1:
            return empty("range_empty" if not distinct else "values_ambiguous")
        # Equal repeated assertions are not competing values. Keep one exact
        # source without claiming it is the immediate predecessor/successor.
        row, m, _ = min(answers, key=lambda v: int(v[1]["canonical_index"]))
        estimate = max(1, (len(row["content"]) + 3) // 4)
        if estimate > min(token_budget or self.limits.token_budget, self.limits.token_budget):
            return empty("candidate_bound", True)
        witness = HistoricalOrderWitness(order.direction, str(m["canonical_record_id"]), int(m["canonical_index"]),
            str(anchor["canonical_record_id"]), index, order.anchor_value, scope_id, order.anchor_speaker)
        memory = TypedMemory(str(row["claim_id"]), "historical_conversation_only", str(row["content"]), estimate, witness)
        return RetrievalOutcome(claim_ids=(memory.claim_id,), selected_memories=(memory,), health=complete)

    def _semantic_rows(
        self,
        query: RetrievalQuery,
        text: str,
        truth_scope_id: str,
        *,
        memory_query_decision: MemoryQueryDecision | None = None,
    ) -> _SemanticRows:
        if self.embedding_provider is None:
            return _SemanticRows([], RetrievalLaneHealth("semantic", "unused", "routing", "not_configured"))
        decision = memory_query_decision or decide_memory_query(
            query.current_user_text,
        )
        current_tokens = _tokens(query.current_user_text)
        include_historical_evidence = bool(
            self.include_historical_evidence
            and (
                (decision.applicable and decision.historical)
                or self._interaction_shorthand(query.current_user_text, current_tokens)
            )
        )
        stage = "embedding"
        try:
            vector = self.embedding_provider.embed([text])[0]
            if len(vector) != self.embedding_provider.dimensions or any(not math.isfinite(float(value)) for value in vector):
                return _SemanticRows([], RetrievalLaneHealth("semantic", "incomplete", stage, "embedding_invalid"))
            stage = "ann"
            try:
                from .ann import HnswClaimIndex
                vector_count = self.store.ann_embedding_count(
                    query.character_id, self.embedding_provider,
                    include_historical_evidence=include_historical_evidence,
                )
                # At 100k, a measured brute-force rank 2/7 result was absent
                # from ef=4096 HNSW candidates.  Deep retrieval therefore
                # grows its search breadth with the derived index; the 10k
                # measured configuration remains exactly ef=4096.
                effective_ef = self.ann_ef
                if self.ann_ef >= 4096:
                    effective_ef = max(effective_ef, min(vector_count, vector_count // 2))
                rows = HnswClaimIndex(
                    self.store, query.character_id, self.embedding_provider,
                    include_historical_evidence=include_historical_evidence,
                ).query(
                    vector, self.limits.semantic_candidates,
                    ef=effective_ef, candidate_multiplier=self.ann_candidate_multiplier,
                    truth_scope_id=truth_scope_id,
                    include_historical_evidence=include_historical_evidence,
                )
                return _SemanticRows(rows, RetrievalLaneHealth("semantic", "complete", "ann"))
            except Exception:
                # The index is derived state.  For small/test stores retain
                # the existing exact cosine fallback; larger live stores keep
                # the bounded lexical lanes rather than scanning every vector.
                # Count the same historical/current partition the fallback
                # searches. Do not materialize an unbounded vector list here.
                if self.store.ann_embedding_count(
                    query.character_id, self.embedding_provider,
                    include_historical_evidence=include_historical_evidence,
                ) > 2_000:
                    return _SemanticRows([], RetrievalLaneHealth("semantic", "incomplete", "ann", "fallback_bound"))
            stage = "cosine"
            rows = self.store.semantic_candidates(
                query.character_id, self.embedding_provider, vector,
                self.limits.semantic_candidates, truth_scope_id=truth_scope_id,
                include_historical_evidence=include_historical_evidence,
            )
            return _SemanticRows(rows, RetrievalLaneHealth("semantic", "recovered", "ann", "ann_failed"))
        except Exception:
            # Retain lexical evidence, but never let this empty lane certify
            # absence. Exception messages, types, and paths never leave this owner.
            code = {"embedding": "embedding_failed", "ann": "ann_failed", "cosine": "cosine_failed"}[stage]
            return _SemanticRows([], RetrievalLaneHealth("semantic", "incomplete", stage, code))

    @staticmethod
    def _exact_terms(text: str) -> tuple[str, ...]:
        quoted = re.findall(r'"([^"\n]{2,})"', text.lower())
        identifiers = [
            value for value in re.findall(r"\b[a-z]+\d[\w-]*\b", text.lower())
            if "-" in value or len(value) > 3
        ]
        return tuple(dict.fromkeys([*quoted, *identifiers]))

    @staticmethod
    def _structural_types(tokens: tuple[str, ...]) -> tuple[str, ...]:
        types = []
        if "live" in tokens:
            types.append("location")
        if "prefer" in tokens:
            types.append("preference")
        if "allerg" in tokens:
            types.append("fact")
        if {"remember", "watch", "troubleshoot"} & set(tokens):
            types.extend(("shared_episode", "episode", "relationship"))
        if {"pokemon", "stadium", "evening"} & set(tokens):
            types.extend(("shared_episode", "episode"))
        if {"nintendo", "64"} & set(tokens):
            types.extend(("profile_fact", "identifier"))
        if "joke" in tokens:
            types.append("running_joke")
        return tuple(dict.fromkeys(types))

    @staticmethod
    def _interaction_shorthand(text: str, tokens: tuple[str, ...]) -> bool:
        stripped = text.strip()
        if not stripped or stripped.endswith("?"):
            return False
        if len(tokens) == 1:
            return True
        return len(tokens) <= 6 and stripped.startswith("*") and stripped.endswith("*")

    @staticmethod
    def _historical_premise_mismatch(
        query_text: str,
        candidate_text: str,
        speech_act: str,
        decision: MemoryQueryDecision,
    ) -> bool:
        match = _ASSERTED_RELATION.search(query_text)
        if match is None:
            return False
        query_relation = _event_terms(match.group("verb"))
        candidate_relations = _event_terms(candidate_text)
        if not query_relation & candidate_relations:
            return True
        # A prior question is evidence that the question was asked, not that
        # its premise was true. It is eligible only when the new query itself
        # asks about that historical act of asking.
        if speech_act == "question" and decision.requested_speech_act != "question":
            return True
        return False

    @staticmethod
    def _trace(candidate: _Candidate, intent: _RetrievalIntent, *, exclusion=None, suppression=None, selection=None, selected_rank=None, covered_slots=()) -> RetrievalTrace:
        score_components = (
            ("exact", candidate.exact_strength), ("fts", candidate.fts_score),
            ("lexical_overlap", float(candidate.lexical_overlap)),
            ("semantic", candidate.semantic_score),
            ("importance", float(candidate.row["importance"])),
        )
        return RetrievalTrace(
            candidate.row["claim_id"], tuple(sorted(candidate.channels)),
            selection_reason="deterministic_lexical_structural" if selection else None,
            exclusion_reason=exclusion, suppression_reason=suppression,
            channel_ranks=tuple(sorted(candidate.channel_ranks.items())), score_components=score_components,
            structural_eligible=True, selection_state=selection or ("excluded" if exclusion else None),
            final_selected_rank=selected_rank, retrieval_intent=intent.kind,
            requested_slots=intent.slots, covered_slots=tuple(covered_slots),
            relevance_gate="passed" if selection else ("rejected" if exclusion == "below_final_relevance_gate" else None),
            legacy_uncertainty_penalty=0.20 if candidate.row["provenance_state"] == "legacy_unverified" else 0.0,
        )

    @staticmethod
    def _summary_trace(query, candidates, deduplicated, final_count, tokens, abstention, intent, covered_slots=()) -> RetrievalTrace:
        counts = {"exact": 0, "fts": 0, "structural": 0, "semantic": 0}
        for candidate in candidates.values():
            for channel in candidate.channels:
                counts[channel] += 1
        return RetrievalTrace(
            "__query__", query_mode=query.mode, candidate_counts=tuple(sorted(counts.items())),
            deduplicated_candidate_count=deduplicated, final_count=final_count,
            estimated_token_count=tokens, abstention_reason=abstention,
            retrieval_intent=intent.kind, requested_slots=intent.slots, covered_slots=tuple(covered_slots),
        )

    @staticmethod
    def _abstain(query: RetrievalQuery, reason: str, *, intent: _RetrievalIntent | None = None, health: RetrievalHealth | None = None) -> RetrievalOutcome:
        return RetrievalOutcome(
            traces=(RetrievalTrace("__query__", query_mode=query.mode, candidate_counts=(("exact", 0), ("fts", 0), ("semantic", 0), ("structural", 0)), deduplicated_candidate_count=0, final_count=0, estimated_token_count=0, abstention_reason=reason, retrieval_intent=intent.kind if intent else None, requested_slots=intent.slots if intent else ()),),
            abstention_reason=reason,
            health=health if health is not None else RetrievalHealth((
                RetrievalLaneHealth("claims", "unused", "routing", "not_applicable"),
            )),
        )
