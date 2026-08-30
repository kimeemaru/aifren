"""Reusable, offline historical context-architecture regression harness.

This module is deliberately evaluation-only.  It reconstructs archive prefixes
in memory, places derived episode state in temporary SQLite stores, and writes
only caller-selected test artifacts.  Canonical conversation and memory files
are never opened for writing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import statistics
import time
from typing import Any, Iterable, Mapping, Sequence

from assistant import build_character_prompt, load_character
from config import LOCAL_LLM_CONTEXT_CHAR_BUDGET
from conversation.context_hygiene import ContextHygiene
from conversation.conversation import ContextManager, Conversation, RECENT_MESSAGES
from llm.output_canonicalization import canonicalize_model_output
from memory_v2_episode_compaction import (
    COMPACTION_VERSION,
    CONTINUITY_ANCHOR_VERSION,
    GENERATOR_VERSION,
    ContinuityAnchor,
    EpisodeCompactionCache,
    EpisodeCompactor,
    EraCompactionDecision,
    LowerEpisodeCompaction,
)
from memory_v2_store import MemoryV2Store
from presentation_metadata import parse_assistant_response


class ContextArm(str, Enum):
    LEGACY = "legacy"
    LOWER_EPISODES = "lower_episodes"
    CONSOLIDATED = "consolidated"
    RETRIEVED_EPISODES = "retrieved_episodes"


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    user_record_index: int
    source_sha256: str
    category: str
    tags: tuple[str, ...]
    capability: str
    expected_terms: tuple[str, ...]
    natural_mirror_allowed: bool
    poison_relevant: bool
    archive_state_end_exclusive: int | None = None
    probe_text: str | None = None
    continuity_source_record_indices: tuple[int, ...] = ()
    use_current_derived_cache: bool = False

    def request_text(self, archive: Sequence[Mapping[str, object]]) -> str:
        return self.probe_text if self.probe_text is not None else str(archive[self.user_record_index].get("content", ""))


@dataclass(frozen=True)
class FixtureManifest:
    schema: str
    version: int
    character: str
    archive_minimum_records: int
    seeds: tuple[int, ...]
    fixtures: tuple[Fixture, ...]


@dataclass(frozen=True)
class PreparedArm:
    fixture_id: str
    arm: ContextArm
    context: tuple[dict[str, str], ...]
    context_characters: int
    raw_start_index: int
    hygiene_metrics: dict[str, int]
    selected_episode_count: int = 0
    consolidated_episode_count: int = 0
    represented_source_record_count: int = 0
    retrieval_candidate_count: int = 0
    retrieved_episode_count: int = 0
    retrieval_signal: str = "none"
    retrieved_source_start_index: int = -1
    retrieved_source_end_index_exclusive: int = -1
    temporal_query_present: bool = False
    temporal_candidate_count: int = 0
    temporal_raw_match_count: int = 0
    retrieval_result_state: str = "no_match"
    temporal_source_span_count: int = 0
    temporal_source_record_count: int = 0
    temporal_distinct_result_count: int = 0
    temporal_related_result_count: int = 0
    temporal_source_episode_count: int = 0
    temporal_result_truncated: bool = False
    temporal_source_ranges: tuple[tuple[int, int], ...] = ()
    derived_source: str = "rebuilt_historical"


_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)?")
_ACTION = re.compile(r"\*+[^*]+\*+")
_POISON_GROUPS = {
    "testing_repetition": (
        "testing", "test mode", "repeat", "repeating", "repetition", "echo",
        "filtering echoes", "no more echo", "loop", "looping",
    ),
    "glitch_protocol": (
        "glitch", "freeze", "unfreeze", "protocol", "system update",
        "system message", "processing", "acknowledging input", "robot mode",
    ),
    "stop_start": (
        "stop signal", "start signal", "red light", "green light",
    ),
    "resource_weather": (
        "low ram", "ram thing", "static-filled", "static-y", "brain is static",
    ),
}
_GENERIC_SYSTEM = (
    "as an ai", "system", "protocol", "processing", "input", "model",
    "cannot comply", "assistant",
)
_WORLD_TERMS = (
    "japari", "serval", "cerulean", "savannah", "tree", "tail", "ears",
    "paws", "bun", "climb", "pounce", "park",
)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def load_manifest(path: str | Path, archive: Sequence[Mapping[str, object]]) -> FixtureManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "aifren.context_regression.fixtures":
        raise ValueError("unsupported context-regression fixture schema")
    if len(archive) < int(payload.get("archive_minimum_records", 0)):
        raise ValueError("canonical archive is older than the fixture manifest")
    fixtures: list[Fixture] = []
    seen: set[str] = set()
    for value in payload.get("fixtures", ()):
        fixture_id = str(value["id"])
        if fixture_id in seen:
            raise ValueError(f"duplicate fixture id: {fixture_id}")
        seen.add(fixture_id)
        index = int(value["user_record_index"])
        if not 0 <= index < len(archive) or str(archive[index].get("role")) != "user":
            raise ValueError(f"fixture {fixture_id} does not identify a canonical user record")
        digest = hashlib.sha256(str(archive[index].get("content", "")).encode("utf-8")).hexdigest()
        if digest != str(value["source_sha256"]):
            raise ValueError(f"fixture {fixture_id} source record changed")
        fixtures.append(Fixture(
            fixture_id=fixture_id,
            user_record_index=index,
            source_sha256=digest,
            category=str(value["category"]),
            tags=tuple(str(item) for item in value.get("tags", ())),
            capability=str(value["capability"]),
            expected_terms=tuple(str(item).casefold() for item in value.get("expected_terms", ())),
            natural_mirror_allowed=bool(value.get("natural_mirror_allowed", False)),
            poison_relevant=bool(value.get("poison_relevant", False)),
            archive_state_end_exclusive=(
                int(value["archive_state_end_exclusive"])
                if value.get("archive_state_end_exclusive") is not None else None
            ),
            probe_text=(str(value["probe_text"]) if value.get("probe_text") is not None else None),
            continuity_source_record_indices=tuple(int(item) for item in value.get("continuity_source_record_indices", ())),
            use_current_derived_cache=bool(value.get("use_current_derived_cache", False)),
        ))
        fixture = fixtures[-1]
        if fixture.archive_state_end_exclusive is not None:
            if not fixture.probe_text or not 0 <= fixture.archive_state_end_exclusive <= len(archive):
                raise ValueError(f"fixture {fixture_id} has an invalid counterfactual archive state")
            if any(not 0 <= item < fixture.archive_state_end_exclusive for item in fixture.continuity_source_record_indices):
                raise ValueError(f"fixture {fixture_id} has invalid continuity provenance")
    seeds = tuple(int(value) for value in payload.get("seeds", ()))
    if len(seeds) < 3 or len(set(seeds)) != len(seeds):
        raise ValueError("fixture manifest requires at least three unique explicit seeds")
    return FixtureManifest(
        schema=str(payload["schema"]), version=int(payload["version"]),
        character=str(payload["character"]),
        archive_minimum_records=int(payload["archive_minimum_records"]),
        seeds=seeds, fixtures=tuple(fixtures),
    )


def _words(value: str) -> list[str]:
    return [match.group(0).casefold().replace("’", "'") for match in _WORD.finditer(value)]


def _ngrams(tokens: Sequence[str], size: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[index:index + size]) for index in range(max(0, len(tokens) - size + 1))]


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _longest_common_run(left: Sequence[str], right: Sequence[str]) -> int:
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_value in left:
        current = [0]
        for index, right_value in enumerate(right, 1):
            value = previous[index - 1] + 1 if left_value == right_value else 0
            current.append(value)
            longest = max(longest, value)
        previous = current
    return longest


def _repeated_ngram_ratio(tokens: Sequence[str], size: int) -> float:
    values = _ngrams(tokens, size)
    return _safe_ratio(len(values) - len(set(values)), len(values))


def _opening_classification(user_text: str, output: str, natural_allowed: bool) -> str:
    spoken = _ACTION.sub(" ", output).lstrip(" .…-—\n\t")
    assistant_tokens = _words(spoken)
    user_tokens = _words(user_text)
    if not assistant_tokens or not user_tokens:
        return "none"
    common_run = _longest_common_run(user_tokens, assistant_tokens[:max(8, len(user_tokens))])
    if common_run >= 3 or (
        len(user_tokens) >= 2 and assistant_tokens[:min(3, len(user_tokens))] == user_tokens[:min(3, len(user_tokens))]
    ):
        return "natural_mirror" if natural_allowed else "phrase_parroting"
    first = assistant_tokens[0]
    if first in set(user_tokens):
        return "natural_mirror" if natural_allowed else "habitual_one_word_echo"
    return "none"


def analyze_response(fixture: Fixture, user_text: str, output: str) -> dict[str, object]:
    user_tokens = _words(user_text)
    output_tokens = _words(output)
    output_lower = output.casefold()
    overlaps: dict[str, float] = {}
    for size in (3, 4, 5):
        user_values = set(_ngrams(user_tokens, size))
        output_values = set(_ngrams(output_tokens, size))
        overlaps[str(size)] = _safe_ratio(len(user_values & output_values), len(user_values))
    poison_counts = {
        name: sum(output_lower.count(term) for term in terms)
        for name, terms in _POISON_GROUPS.items()
    }
    expected_hit = None
    if fixture.expected_terms:
        expected_hit = any(term in output_lower for term in fixture.expected_terms)
    opening = _opening_classification(user_text, output, fixture.natural_mirror_allowed)
    repeated = {str(size): _repeated_ngram_ratio(output_tokens, size) for size in (3, 4, 5)}
    action_count = len(_ACTION.findall(output))
    world_hits = sum(output_lower.count(term) for term in _WORLD_TERMS)
    generic_hits = sum(output_lower.count(term) for term in _GENERIC_SYSTEM)
    malformed = not output.strip() or len(output_tokens) < 2 or output.count("{") != output.count("}")
    return {
        "opening_echo": opening,
        "problematic_echo": opening in {"habitual_one_word_echo", "phrase_parroting"},
        "phrase_parroting": opening == "phrase_parroting" or _longest_common_run(user_tokens, output_tokens) >= 5,
        "user_output_ngram_overlap": overlaps,
        "user_output_longest_common_run": _longest_common_run(user_tokens, output_tokens),
        "self_repeated_ngram_ratio": repeated,
        "poison_theme_counts": poison_counts,
        "poison_theme_recurrence": sum(poison_counts.values()) > 0,
        "unnecessary_poison_reversion": sum(poison_counts.values()) > 0 and not fixture.poison_relevant,
        "expected_continuity_hit": expected_hit,
        "action_span_count": action_count,
        "world_personality_term_count": world_hits,
        "generic_system_term_count": generic_hits,
        "malformed_or_degenerate": malformed,
        "output_words": len(output_tokens),
        "output_characters": len(output),
    }


class _NoMemory:
    def get_relevant_memories(self, _message: str, max_memories: int = 5) -> list[object]:
        del max_memories
        return []


class _DeterministicProvider:
    """Force reproducible seeds for all derived preparation calls."""

    def __init__(self, provider, namespace: str = "aifren-context-regression-prep-v1") -> None:
        self.provider = provider
        self.namespace = namespace
        self.model = str(getattr(provider, "model", "") or "")
        self.fresh_request_seeds = bool(getattr(provider, "fresh_request_seeds", False))

    def request_sampling_metadata(self) -> dict[str, str | int | float]:
        values = getattr(self.provider, "request_sampling_metadata", lambda: {})()
        return dict(values) if isinstance(values, Mapping) else {}

    def generate(self, messages, prompt, *, seed: int | None = None) -> str:
        if seed is None:
            digest = hashlib.sha256((self.namespace + "\n" + str(prompt)).encode("utf-8")).digest()
            seed = int.from_bytes(digest[:4], "big") % ((1 << 32) - 1)
        # Derived preparation contracts ask for at most 120/180 words. Bound
        # only this offline preparation seam so one malformed historical
        # summary cannot consume the model's entire remaining context window.
        # Candidate inference remains identical to production and unbounded by
        # the harness.
        request_builder = getattr(self.provider, "_request", None)
        client = getattr(self.provider, "client", None)
        if callable(request_builder) and client is not None:
            request = request_builder(messages, prompt, seed=int(seed), stream=False)
            request["max_tokens"] = 512
            response = client.chat.completions.create(**request)
            return str(response.choices[0].message.content or "")
        return str(self.provider.generate(messages, prompt, seed=int(seed)))


class _MemoizingCompactor(EpisodeCompactor):
    def __init__(self, provider, cache_path: Path) -> None:
        super().__init__(_DeterministicProvider(provider))
        self.cache_path = cache_path
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            payload = {}
        self.lower: dict[str, object] = dict(payload.get("lower", {}))
        self.eras: dict[str, object] = dict(payload.get("eras", {}))

    def _save(self) -> None:
        _atomic_json(self.cache_path, {"lower": self.lower, "eras": self.eras})

    def compact_episode(self, messages, boundary, *, derived_seed=None):
        key = (
            f"lower-v{COMPACTION_VERSION}-g{GENERATOR_VERSION}"
            f"-a{CONTINUITY_ANCHOR_VERSION}:{self.provider_identity_digest}:"
            f"{boundary.source_digest}"
        )
        if key not in self.lower:
            self.lower[key] = asdict(super().compact_episode(
                messages, boundary, derived_seed=derived_seed,
            ))
            self._save()
        value = self.lower[key]
        if not isinstance(value, Mapping):
            raise RuntimeError("historical lower-compaction cache has obsolete data")
        anchors = tuple(
            ContinuityAnchor(
                anchor_id=str(anchor["anchor_id"]),
                detail=str(anchor["detail"]),
                source_record_indices=tuple(int(index) for index in anchor["source_record_indices"]),
                key_terms=tuple(str(item) for item in anchor.get("key_terms", ())),
            )
            for anchor in value.get("anchors", ())
        )
        return LowerEpisodeCompaction(
            content=str(value["content"]),
            anchors=anchors,
            verification_status=str(value["verification_status"]),
            missing_anchor_ids=tuple(str(item) for item in value.get("missing_anchor_ids", ())),
            refinement_attempted=bool(value["refinement_attempted"]),
            extraction_duration_ms=float(value["extraction_duration_ms"]),
            summary_generation_duration_ms=float(value["summary_generation_duration_ms"]),
            verification_duration_ms=float(value["verification_duration_ms"]),
            refinement_duration_ms=float(value["refinement_duration_ms"]),
        )

    def consolidate(self, messages, episodes):
        identity = "|".join(
            f"{item.episode_id}:{hashlib.sha256(item.content.encode('utf-8')).hexdigest()}"
            for item in episodes
        )
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if key not in self.eras:
            decision = super().consolidate(messages, episodes)
            self.eras[key] = None if decision is None else asdict(decision)
            self._save()
        value = self.eras[key]
        return None if value is None else EraCompactionDecision(**value)


class _SelectionView:
    def __init__(
        self,
        cache: EpisodeCompactionCache,
        use_consolidated: bool,
        enable_retrieval: bool,
    ) -> None:
        self.cache = cache
        self.use_consolidated = use_consolidated
        self.enable_retrieval = enable_retrieval

    def select_for_context(self, messages, **kwargs):
        return self.cache.select_for_context(
            messages,
            use_consolidated=self.use_consolidated,
            enable_retrieval=self.enable_retrieval,
            **kwargs,
        )


class HistoricalContextBuilder:
    """Reconstruct current context arms at immutable historical archive prefixes."""

    def __init__(
        self,
        *,
        archive: Sequence[Mapping[str, object]],
        provider,
        character_id: str,
        character_prompt: str,
        artifact_directory: Path,
        current_derived_cache_path: Path | None = None,
    ) -> None:
        self.archive = tuple(dict(value) for value in archive)
        self.provider = provider
        self.character_id = character_id
        self.character_prompt = character_prompt
        self.artifact_directory = artifact_directory
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        self.compactor = _MemoizingCompactor(provider, artifact_directory / "preparation_cache.json")
        self.context_manager = ContextManager()
        self.hygiene = ContextHygiene()
        self._current_store = None
        self._current_cache = None
        if current_derived_cache_path is not None and current_derived_cache_path.is_file():
            connection = sqlite3.connect(
                f"file:{current_derived_cache_path.resolve()}?mode=ro", uri=True,
            )
            connection.row_factory = sqlite3.Row
            self._current_store = type("ReadOnlyDerivedStore", (), {"connection": connection})()
            self._current_cache = EpisodeCompactionCache(self._current_store, self.character_id)
        legacy_root = self.artifact_directory / "legacy_replay_work"
        legacy_root.mkdir(parents=True, exist_ok=True)
        self._legacy_conversation_path = legacy_root / "conversation.json"
        self._legacy_summary_path = legacy_root / "summary.json"
        self._legacy_conversation_path.write_text("[]", encoding="utf-8")
        replay_state_path = self.artifact_directory / "legacy_replay_state.json"
        try:
            replay_state = json.loads(replay_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            replay_state = {"completed_record_count": 0, "summary_data": {"summary": "", "summarized_messages": 0}}
        self._legacy_completed_record_count = int(replay_state.get("completed_record_count", 0))
        self._legacy_summary_data = dict(replay_state.get("summary_data", {}))
        self._legacy_summary_path.write_text(
            json.dumps(self._legacy_summary_data, ensure_ascii=False), encoding="utf-8",
        )
        self._legacy_conversation = Conversation(
            _DeterministicProvider(self.provider, "aifren-context-regression-summary-v1"),
            conversation_file=str(self._legacy_conversation_path),
            summary_file=str(self._legacy_summary_path),
        )

    def _save_legacy_replay_state(self) -> None:
        _atomic_json(self.artifact_directory / "legacy_replay_state.json", {
            "completed_record_count": self._legacy_completed_record_count,
            "summary_data": self._legacy_conversation.summary_data,
        })

    def _legacy_summary(self, fixture: Fixture, messages: Sequence[Mapping[str, object]]) -> str:
        summaries_path = self.artifact_directory / "legacy_summaries.json"
        try:
            summaries = json.loads(summaries_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            summaries = {}
        if fixture.fixture_id in summaries:
            return str(summaries[fixture.fixture_id])
        # Production updates the legacy rolling summary after each completed
        # assistant record, not after accepting the next user request. Replay
        # that lifecycle incrementally so a long historical prefix is never
        # summarized in one context-overflowing provider request.
        completed_record_count = len(messages) - 1
        if completed_record_count < self._legacy_completed_record_count:
            raise RuntimeError("historical fixtures must be prepared in canonical order")
        for end in range(self._legacy_completed_record_count + 2, completed_record_count + 1, 2):
            self._legacy_conversation.messages = list(self.archive[:end])
            self._legacy_conversation.update_summary()
            self._legacy_completed_record_count = end
            self._save_legacy_replay_state()
        # Odd/non-paired gaps are deliberately not treated as completed turns.
        self._legacy_conversation.messages = list(self.archive[:completed_record_count])
        summary = str(self._legacy_conversation.summary_data.get("summary", ""))
        summaries[fixture.fixture_id] = summary
        _atomic_json(summaries_path, summaries)
        return summary

    def _episode_cache(self, messages: Sequence[Mapping[str, object]], fixture: Fixture) -> EpisodeCompactionCache:
        if fixture.use_current_derived_cache:
            if self._current_cache is None:
                raise RuntimeError("fixture requires the current derived episode cache")
            return self._current_cache
        store = MemoryV2Store(":memory:")
        store.create_character(self.character_id, "Serval context-regression fixture")
        cache = EpisodeCompactionCache(store, self.character_id)
        cache.rebuild(messages, self.compactor)
        return cache

    def _assemble(
        self,
        fixture: Fixture,
        messages: Sequence[Mapping[str, object]],
        arm: ContextArm,
        summary: str,
        cache: EpisodeCompactionCache,
    ) -> PreparedArm:
        selection = None
        if arm != ContextArm.LEGACY:
            selection = _SelectionView(
                cache,
                arm == ContextArm.CONSOLIDATED,
                arm == ContextArm.RETRIEVED_EPISODES,
            ).select_for_context(
                messages,
                maximum_raw_messages=RECENT_MESSAGES,
                maximum_raw_characters=self.context_manager.max_recent_chars,
            )
            if selection is None:
                raise RuntimeError(f"{arm.value} derived cache was invalid at {fixture.fixture_id}")
            raw = list(messages[selection.raw_start_index:])
            summary = ""
            raw_start = selection.raw_start_index
        else:
            count_bounded = list(messages[-RECENT_MESSAGES:])
            raw = self.context_manager.build_recent_context(count_bounded)
            raw_start = len(messages) - len(raw)
        hygiene_result = self.hygiene.filter(raw)
        context_budget = max(1, int(LOCAL_LLM_CONTEXT_CHAR_BUDGET) - len(self.character_prompt))
        context = self.context_manager.build_context(
            summary, [], list(hygiene_result.messages),
            admitted_episode_context=selection.context_block if selection is not None else None,
            max_context_chars=context_budget,
        )
        stats = hygiene_result.stats
        return PreparedArm(
            fixture_id=fixture.fixture_id, arm=arm,
            context=tuple({"role": str(item["role"]), "content": str(item["content"])} for item in context),
            context_characters=self.context_manager.calculate_characters(context),
            raw_start_index=raw_start,
            hygiene_metrics={
                "raw_recent_messages": stats.raw_recent_message_count,
                "admitted_recent_messages": len(hygiene_result.messages),
                "assistant_only_suppressed": stats.assistant_only_suppressed_count,
                "exchange_pairs_suppressed": stats.exchange_pairs_suppressed_count,
            },
            selected_episode_count=selection.selected_episode_count if selection is not None else 0,
            consolidated_episode_count=selection.consolidated_episode_count if selection is not None else 0,
            represented_source_record_count=selection.represented_source_record_count if selection is not None else 0,
            retrieval_candidate_count=selection.retrieval_candidate_count if selection is not None else 0,
            retrieved_episode_count=selection.retrieved_episode_count if selection is not None else 0,
            retrieval_signal=selection.retrieval_signal if selection is not None else "none",
            retrieved_source_start_index=(
                selection.retrieved_source_start_index if selection is not None else -1
            ),
            retrieved_source_end_index_exclusive=(
                selection.retrieved_source_end_index_exclusive if selection is not None else -1
            ),
            temporal_query_present=(
                selection.temporal_query_present if selection is not None else False
            ),
            temporal_candidate_count=(
                selection.temporal_candidate_count if selection is not None else 0
            ),
            temporal_raw_match_count=(
                selection.temporal_raw_match_count if selection is not None else 0
            ),
            retrieval_result_state=(
                selection.retrieval_result_state if selection is not None else "no_match"
            ),
            temporal_source_span_count=(
                selection.temporal_source_span_count if selection is not None else 0
            ),
            temporal_source_record_count=(
                selection.temporal_source_record_count if selection is not None else 0
            ),
            temporal_distinct_result_count=(
                selection.temporal_distinct_result_count if selection is not None else 0
            ),
            temporal_related_result_count=(
                selection.temporal_related_result_count if selection is not None else 0
            ),
            temporal_source_episode_count=(
                selection.temporal_source_episode_count if selection is not None else 0
            ),
            temporal_result_truncated=(
                selection.temporal_result_truncated if selection is not None else False
            ),
            temporal_source_ranges=(
                selection.temporal_source_ranges if selection is not None else ()
            ),
            derived_source=("current_cache" if fixture.use_current_derived_cache else "rebuilt_historical"),
        )

    def prepare(
        self,
        fixture: Fixture,
        arms: Sequence[ContextArm] = tuple(ContextArm),
    ) -> dict[ContextArm, PreparedArm]:
        # Provider request state includes the selected current user message but
        # never the historical assistant answer that followed it.
        if fixture.archive_state_end_exclusive is None:
            messages = self.archive[:fixture.user_record_index + 1]
        else:
            prefix = self.archive[:fixture.archive_state_end_exclusive]
            reference_timestamp = str(prefix[-1].get("timestamp", "")) if prefix else ""
            messages = prefix + ({
                "role": "user", "content": fixture.request_text(self.archive),
                # Preserve the reconstructed state's local calendar reference;
                # the probe itself is never persisted.
                "timestamp": reference_timestamp,
            },)
        if str(messages[-1].get("role")) != "user":
            raise ValueError("historical fixture does not end at a user request")
        selected_arms = tuple(dict.fromkeys(arms))
        if not selected_arms:
            raise ValueError("at least one context arm is required")
        summary = self._legacy_summary(fixture, messages) if ContextArm.LEGACY in selected_arms else ""
        cache = (
            self._episode_cache(messages, fixture)
            if any(arm != ContextArm.LEGACY for arm in selected_arms)
            else None
        )
        return {
            arm: self._assemble(fixture, messages, arm, summary, cache)
            for arm in selected_arms
        }


def character_prompt_from_paths(character_path: Path, personality_path: Path) -> str:
    character, personality = load_character(str(character_path), str(personality_path))
    return build_character_prompt(character, personality)


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _mean(values: Iterable[float | int | None]) -> float | None:
    measured = [float(value) for value in values if value is not None]
    return statistics.fmean(measured) if measured else None


def _pairwise_diversity(records: Sequence[Mapping[str, object]]) -> float | None:
    outputs = [str(record.get("output", "")) for record in records]
    if len(outputs) < 2:
        return None
    ratios = []
    from difflib import SequenceMatcher
    for index, left in enumerate(outputs):
        for right in outputs[index + 1:]:
            ratios.append(1.0 - SequenceMatcher(None, left.casefold(), right.casefold()).ratio())
    return _mean(ratios)


def aggregate_results(records: Sequence[Mapping[str, object]], fixtures: Mapping[str, Fixture]) -> dict[str, object]:
    def aggregate_group(values: Sequence[Mapping[str, object]]) -> dict[str, object]:
        count = len(values)
        bool_fields = (
            "problematic_echo", "phrase_parroting", "poison_theme_recurrence",
            "unnecessary_poison_reversion", "malformed_or_degenerate",
        )
        result: dict[str, object] = {"generations": count}
        for field in bool_fields:
            successes = sum(bool(value["metrics"].get(field)) for value in values)
            result[field] = {
                "count": successes, "rate": _safe_ratio(successes, count),
                "wilson_95": _wilson(successes, count),
            }
        result.update({
            "judge_coverage": _safe_ratio(sum(bool(value.get("judge")) for value in values), count),
            "mean_prompt_tokens": _mean(value.get("prompt_tokens") for value in values),
            "mean_completion_tokens": _mean(value.get("completion_tokens") for value in values),
            "mean_generation_seconds": _mean(value.get("generation_seconds") for value in values),
            "mean_tokens_per_second": _mean(value.get("tokens_per_second") for value in values),
            "mean_personality_score": _mean(value.get("judge", {}).get("personality_score") for value in values),
            "topic_adherence_rate": _mean(
                1 if value.get("judge", {}).get("topic_adherence", 0) >= 1 else 0
                for value in values if value.get("judge")
            ),
            "topic_reversion_rate": _mean(
                1 if value.get("judge", {}).get("unnecessary_topic_reversion") else 0
                for value in values if value.get("judge")
            ),
            "continuity_success_rate": _mean(
                1 if value.get("judge", {}).get("continuity_success") else 0
                for value in values
                if value.get("judge", {}).get("continuity_success") is not None
            ),
            "coherence_failure_rate": _mean(
                1 if value.get("judge", {}).get("coherence_failure") else 0
                for value in values if value.get("judge")
            ),
            "deterministic_continuity_rate": _mean(
                1 if value["metrics"].get("expected_continuity_hit") else 0
                for value in values if value["metrics"].get("expected_continuity_hit") is not None
            ),
        })
        groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for value in values:
            groups.setdefault((str(value["fixture_id"]), str(value["arm"])), []).append(value)
        result["mean_across_seed_diversity"] = _mean(_pairwise_diversity(group) for group in groups.values())
        return result

    arms = {
        arm.value: aggregate_group([value for value in records if value["arm"] == arm.value])
        for arm in ContextArm
    }
    categories = sorted({fixture.category for fixture in fixtures.values()})
    category_values = {
        category: {
            arm.value: aggregate_group([
                value for value in records
                if value["arm"] == arm.value and fixtures[str(value["fixture_id"])].category == category
            ])
            for arm in ContextArm
        }
        for category in categories
    }
    tag_names = sorted({tag for fixture in fixtures.values() for tag in fixture.tags})
    tag_values = {
        tag: {
            arm.value: aggregate_group([
                value for value in records
                if value["arm"] == arm.value and tag in fixtures[str(value["fixture_id"])].tags
            ])
            for arm in ContextArm
        }
        for tag in tag_names
    }
    return {"arms": arms, "categories": category_values, "tags": tag_values}


def shuffled_arm_order(fixture_id: str, seed: int) -> tuple[ContextArm, ...]:
    arms = list(ContextArm)
    random.Random(f"{fixture_id}:{seed}:matched-arm-order-v1").shuffle(arms)
    return tuple(arms)


def blinded_labels(fixture_id: str, seed: int) -> dict[ContextArm, str]:
    labels = [chr(ord("A") + index) for index in range(len(ContextArm))]
    random.Random(f"{fixture_id}:{seed}:blind-v1").shuffle(labels)
    return dict(zip(ContextArm, labels))
