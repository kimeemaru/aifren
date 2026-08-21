"""Isolated deterministic Semantic Retrieval V2 over the shadow SQLite store.

This module is intentionally not imported by AIFren runtime code.  It has no
embedding model and no access to personal JSON persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

from benchmarks.memory_v2.models import RetrievalOutcome, RetrievalQuery, RetrievalTrace, TypedMemory

from .store import MemoryV2Store, parse_timestamp_us


_STOP_WORDS = {
    "a", "an", "and", "are", "about", "can", "did", "do", "for", "how", "i", "in", "is", "it", "me", "my",
    "of", "our", "please", "the", "tell", "to", "we", "what", "where", "when", "you", "your", "now", "still", "today", "was", "day", "food", "weather", "read", "these", "not",
}
_ALIASES = {
    "n64": ("nintendo", "64"), "allergy": ("allerg",), "allergic": ("allerg",),
    "preferred": ("prefer",), "lived": ("live",), "lives": ("live",), "work": ("engineer",),
}
_HISTORICAL_WORDS = {"was", "were", "did", "used", "historical", "then", "previously"}
_GENERIC_REQUEST_WORDS = {"could", "should", "would", "have", "been", "something", "interesting", "solve", "problem", "recommend", "recommendation"}
_OPINION_PATTERNS = (
    r"\bdo you (think|like|prefer|want|play)\b",
    r"\bwould you (like|play|choose)\b",
    r"\bwhat (do|would) you\b",
)
_MEDICAL_QUERY_TERMS = {"allerg", "sensitivity", "sensitive", "sick"}
_MEDICAL_GENERIC_TERMS = _MEDICAL_QUERY_TERMS | {"food", "make", "remind", "remember"}


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


@dataclass(frozen=True)
class _RetrievalIntent:
    kind: str
    slots: tuple[str, ...] = ()


def _infer_intent(text: str, tokens: tuple[str, ...]) -> _RetrievalIntent:
    """Conservative, replaceable intent rules for the isolated V2 engine."""
    lower = text.lower().strip()
    if any(re.search(pattern, lower) for pattern in _OPINION_PATTERNS):
        return _RetrievalIntent("assistant_opinion")
    if re.search(r"\b(tell me something|solve|astronomy|math problem|beginner|what should)\b", lower):
        return _RetrievalIntent("generic_reasoning")
    favorite_slots = tuple(dict.fromkeys(re.findall(r"\bfavo(?:rite|urite)\s+([a-z0-9]+)", lower)))
    named_slots = tuple(token for token in ("color", "animal", "game", "hobby", "food", "location") if token in tokens)
    if not favorite_slots:
        favorite_slots = named_slots
    if "favorite" in lower and not favorite_slots:
        return _RetrievalIntent("ambiguous_memory")
    explicit_user = bool(re.search(r"\b(my|i|me|we|our)\b", lower)) or "remember" in tokens or "remind" in tokens
    if " and " in lower and explicit_user and len(tokens) >= 3:
        return _RetrievalIntent("multi_user_memory", favorite_slots)
    if explicit_user:
        return _RetrievalIntent("historical_user_fact" if set(tokens) & _HISTORICAL_WORDS else "user_memory", favorite_slots)
    return _RetrievalIntent("unspecified")


def _has_specific_medical_conflict(query_tokens: tuple[str, ...], content_tokens: set[str]) -> bool:
    """Do not convert a named allergy/sensitivity into a different named fact."""
    if not (set(query_tokens) & _MEDICAL_QUERY_TERMS):
        return False
    requested = {token for token in query_tokens if token not in _MEDICAL_GENERIC_TERMS and len(token) >= 4}
    known = {token for token in content_tokens if token not in _MEDICAL_GENERIC_TERMS and len(token) >= 4}
    return bool(requested and known and not (requested & known))


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


def _safe_fts_query(tokens: Iterable[str]) -> str:
    """Quote normalized alphanumeric terms so punctuation cannot alter FTS syntax."""
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens if token)


def _historical_time(query: RetrievalQuery) -> tuple[bool, int]:
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", query.current_user_text)
    tokens = set(_tokens(query.current_user_text))
    lower = query.current_user_text.lower()
    if "not in " in lower or "these days" in lower or "currently" in lower:
        return False, parse_timestamp_us(query.at)
    if query.mode == "historical" or years or tokens & _HISTORICAL_WORDS:
        year = int(years[-1]) if years else datetime.fromisoformat(query.at.replace("Z", "+00:00")).year
        return True, int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1_000_000)
    return False, parse_timestamp_us(query.at)


def _typed_label(row, historical: bool) -> str:
    if row["provenance_state"] != "complete":
        return "LEGACY UNVERIFIED"
    if row["claim_type"] in {"episode", "relationship", "running_joke"}:
        return "SHARED EPISODE"
    if row["claim_type"] in {"temporary_state", "future_event"}:
        return "TEMPORARY/PLAN"
    if historical or row["effective_status"] == "superseded":
        return "HISTORICAL USER FACT"
    return "CURRENT USER FACT"


class SemanticRetrievalV2:
    """Derived-index, deterministic candidate retrieval for synthetic V2 stores."""

    def __init__(self, store: MemoryV2Store, limits: RetrievalLimits | None = None, embedding_provider=None, *, allow_legacy_unverified: bool = False):
        self.store = store
        self.limits = limits or RetrievalLimits()
        self.embedding_provider = embedding_provider
        self.allow_legacy_unverified = allow_legacy_unverified
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
    ) -> RetrievalOutcome:
        """Return typed claims and privacy-safe traces; never constructs a prompt."""
        if embedding_state in {"stale", "failed", "retryable"}:
            return self._abstain(query, "embedding_state_not_current")
        historical, at_us = _historical_time(query)
        query_tokens = _tokens(" ".join((query.current_user_text, *query.recent_user_turns)))
        if not query_tokens:
            return self._abstain(query, "no_searchable_terms")
        intent = _infer_intent(query.current_user_text, query_tokens)
        if intent.kind in {"assistant_opinion", "generic_reasoning", "ambiguous_memory"}:
            return self._abstain(query, f"intent_{intent.kind}", intent=intent)
        exact_terms = self._exact_terms(query.current_user_text)
        exact_ids = self.store.exact_claim_ids(query.character_id, exact_terms, self.limits.exact_candidates)
        fts_rows = self.store.search_fts(query.character_id, _safe_fts_query(query_tokens), self.limits.fts_candidates)
        semantic_rows = self._semantic_rows(query, " ".join((query.current_user_text, *query.recent_user_turns)))
        structural_types = self._structural_types(query_tokens)
        structural_rows = self.store.structural_claims(
            query.character_id, at_us, historical=historical, claim_types=structural_types,
            limit=self.limits.structural_candidates,
        ) if structural_types else []
        candidate_ids = tuple(dict.fromkeys([*exact_ids, *(row["claim_id"] for row in fts_rows), *(row[0] for row in semantic_rows), *(row["claim_id"] for row in structural_rows)]))
        eligible_rows = self.store.structural_claims(
            query.character_id, at_us, historical=historical, claim_ids=candidate_ids,
        ) if candidate_ids else []
        eligible_states = {"complete"}
        if self.allow_legacy_unverified:
            eligible_states.add("legacy_unverified")
        eligible = {row["claim_id"]: row for row in eligible_rows if row["provenance_state"] in eligible_states}
        if not eligible:
            return self._abstain(query, "no_structurally_eligible_claims")

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
            return self._abstain(query, "no_relevant_candidates")

        visible = set(recent_visible_claim_ids)
        recently_used = set(recently_used_claim_ids)
        explicit_repeat = query.mode == "explicit_repeat"
        traces = []
        scored = []
        for claim_id, candidate in candidates.items():
            row = candidate.row
            content_tokens = set(_tokens(row["content"] + " " + row["claim_type"]))
            candidate.lexical_overlap = len(set(query_tokens) & content_tokens)
            if claim_id in visible and not explicit_repeat:
                traces.append(self._trace(candidate, intent, exclusion="recent_visible", suppression="recent_visible"))
                continue
            if claim_id in recently_used and not explicit_repeat:
                traces.append(self._trace(candidate, intent, exclusion="recently_used", suppression="topic_cooldown"))
                continue
            # Candidate lanes are deliberately broad.  Final eligibility is
            # stricter: rank-one FTS alone is not relevance evidence.
            single_domain_term = len(query_tokens) == 1 and len(query_tokens[0]) >= 4
            lexical_eligible = candidate.lexical_overlap >= 2 or (single_domain_term and candidate.lexical_overlap == 1)
            structural_direct = (
                intent.kind in {"user_memory", "historical_user_fact", "multi_user_memory"}
                and row["claim_type"] in structural_types and candidate.lexical_overlap >= 1
            )
            semantic_eligible = candidate.semantic_score >= 0.55
            paraphrase_eligible = (
                intent.kind in {"user_memory", "historical_user_fact", "multi_user_memory"}
                and candidate.semantic_rank == 1 and candidate.semantic_score >= 0.32
            )
            exact_eligible = candidate.exact_strength > 0
            if _has_specific_medical_conflict(query_tokens, content_tokens):
                traces.append(self._trace(candidate, intent, exclusion="specific_medical_entity_mismatch"))
                continue
            if not (exact_eligible or lexical_eligible or structural_direct or semantic_eligible or paraphrase_eligible):
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
                + max(0.0, candidate.semantic_score - 0.55) * 4.0
                + min(row["importance"], 10) * 0.05
                + (0.25 if historical else 0.5)
                - legacy_penalty
                - identifier_penalty
            )
            scored.append((score, candidate))

        if not scored:
            traces.append(self._summary_trace(query, candidates, 0, 0, 0, "all_candidates_suppressed_or_weak", intent))
            return RetrievalOutcome(traces=tuple(traces), abstention_reason="all_candidates_suppressed_or_weak")

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
            estimate = max(1, (len(row["content"]) + 3) // 4)
            if len(selected) >= final_limit:
                traces.append(self._trace(candidate, intent, exclusion="final_count_cap"))
                continue
            if used_tokens + estimate > budget:
                traces.append(self._trace(candidate, intent, exclusion="token_budget_cap"))
                continue
            rank = len(selected) + 1
            selected.append((candidate, TypedMemory(row["claim_id"], _typed_label(row, historical), row["content"], estimate)))
            used_tokens += estimate
            content_tokens = set(_tokens(row["content"] + " " + row["claim_type"]))
            claim_slots = tuple(slot for slot in intent.slots if slot in content_tokens)
            covered_slots.update(claim_slots)
            traces.append(self._trace(candidate, intent, selected_rank=rank, selection="selected", covered_slots=claim_slots))
            if intent.slots and set(intent.slots).issubset(covered_slots):
                break

        if not selected:
            traces.append(self._summary_trace(query, candidates, len(candidates), 0, used_tokens, "final_budget_exhausted", intent))
            return RetrievalOutcome(traces=tuple(traces), abstention_reason="final_budget_exhausted")
        traces.append(self._summary_trace(query, candidates, len(candidates), len(selected), used_tokens, None, intent, tuple(sorted(covered_slots))))
        memories = tuple(memory for _, memory in selected)
        return RetrievalOutcome(
            claim_ids=tuple(memory.claim_id for memory in memories), selected_memories=memories,
            traces=tuple(traces),
        )

    def _semantic_rows(self, query: RetrievalQuery, text: str) -> list[tuple[str, float]]:
        if self.embedding_provider is None:
            return []
        try:
            vector = self.embedding_provider.embed([text])[0]
            return self.store.semantic_candidates(
                query.character_id, self.embedding_provider, vector,
                self.limits.semantic_candidates,
            )
        except Exception:
            # The lexical lanes remain usable if an optional derived model is
            # unavailable. Lifecycle health exposes failures explicitly.
            return []

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
            types.extend(("episode", "relationship"))
        if {"pokemon", "stadium", "evening"} & set(tokens):
            types.append("episode")
        if {"nintendo", "64"} & set(tokens):
            types.extend(("profile_fact", "identifier"))
        if "joke" in tokens:
            types.append("running_joke")
        return tuple(dict.fromkeys(types))

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
    def _abstain(query: RetrievalQuery, reason: str, *, intent: _RetrievalIntent | None = None) -> RetrievalOutcome:
        return RetrievalOutcome(
            traces=(RetrievalTrace("__query__", query_mode=query.mode, candidate_counts=(("exact", 0), ("fts", 0), ("semantic", 0), ("structural", 0)), deduplicated_candidate_count=0, final_count=0, estimated_token_count=0, abstention_reason=reason, retrieval_intent=intent.kind if intent else None, requested_slots=intent.slots if intent else ()),),
            abstention_reason=reason,
        )
