"""Benchmark-only counterfactual precedence audit for the V2 intent router."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from aifren.memory_v2_store.retrieval import _infer_intent, _tokens

from .router_intent import RouterIntentSuite


HOLDOUT_VERSION = "memory-v2-router-intent-generic-holdout-v1"
_HOLDOUT_PATH = Path(__file__).with_name("router_intent_generic_holdout.json")
_SKIP_INTENTS = frozenset({"assistant_opinion", "generic_reasoning", "ambiguous_memory"})
_RECALL_MARKERS = re.compile(r"\b(?:again|usual|remember|remind)\b", re.IGNORECASE)
_PERSONAL_DURABLE_CUE = re.compile(
    r"\b(?:call\s+me|my\s+(?:name|nickname|home|address|city|job|occupation|"
    r"sister|brother|partner|parent|friend|usual))\b",
    re.IGNORECASE,
)


class GenericHoldoutError(ValueError):
    """Raised when the frozen independent generic holdout is malformed."""


@dataclass(frozen=True)
class GenericHoldoutCase:
    case_id: str
    query: str
    expected_search_behavior: str


@dataclass(frozen=True)
class CounterfactualPolicy:
    name: str
    description: str

    def classify(self, query: str, current_intent: str) -> str:
        if self.name == "current":
            return current_intent
        if current_intent not in _SKIP_INTENTS:
            return current_intent
        if self.name == "recall-marker-precedence" and _RECALL_MARKERS.search(query):
            return "user_memory"
        if self.name == "personal-durable-cue-precedence" and _PERSONAL_DURABLE_CUE.search(query):
            return "user_memory"
        return current_intent


@dataclass(frozen=True)
class RouteComparison:
    case_id: str
    query: str
    suite: str
    expected_search_behavior: str
    current_intent: str
    counterfactual_intent: str
    current_retrieval_attempted: bool
    counterfactual_retrieval_attempted: bool
    classification_changed: bool
    desirable_change: bool | None


@dataclass(frozen=True)
class CounterfactualReport:
    policy: str
    durable_queries_tested: int
    durable_correctly_routed_to_memory: int
    durable_false_skips: int
    direct_false_skips: int
    paraphrase_false_skips: int
    generic_pattern_collision_false_skips: int
    original_generic_controls: int
    original_generic_controls_correctly_skipped: int
    original_unnecessary_memory_search_activations: int
    independent_generic_holdout: int
    independent_holdout_correctly_skipped: int
    independent_holdout_unnecessary_memory_search_activations: int
    comparisons: tuple[RouteComparison, ...]

    def to_dict(self) -> dict:
        return asdict(self)


CURRENT_POLICY = CounterfactualPolicy("current", "Unmodified production classifier observed in benchmark code.")
RECALL_MARKER_POLICY = CounterfactualPolicy(
    "recall-marker-precedence",
    "Let a general recall marker override an otherwise skipped advice/opinion intent.",
)
PERSONAL_DURABLE_CUE_POLICY = CounterfactualPolicy(
    "personal-durable-cue-precedence",
    "Let a general first-person durable-fact cue override an otherwise skipped advice/opinion intent.",
)
POLICIES = (CURRENT_POLICY, RECALL_MARKER_POLICY, PERSONAL_DURABLE_CUE_POLICY)


def load_generic_holdout(path: str | Path | None = None) -> tuple[GenericHoldoutCase, ...]:
    source = Path(path) if path is not None else _HOLDOUT_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GenericHoldoutError("generic holdout is unreadable") from error
    if not isinstance(payload, dict) or payload.get("version") != HOLDOUT_VERSION or not isinstance(payload.get("cases"), list):
        raise GenericHoldoutError("unexpected generic holdout version")
    cases = []
    for value in payload["cases"]:
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("case_id", "query", "expected_search_behavior")):
            raise GenericHoldoutError("generic holdout case has missing text")
        if value["expected_search_behavior"] != "skip":
            raise GenericHoldoutError("generic holdout cases must expect skip")
        cases.append(GenericHoldoutCase(value["case_id"], value["query"], value["expected_search_behavior"]))
    if len(cases) != 12 or len({case.case_id for case in cases}) != len(cases):
        raise GenericHoldoutError("frozen generic holdout must contain 12 unique cases")
    return tuple(cases)


def current_intent(query: str) -> str:
    """Observe the production classifier without modifying it."""
    return _infer_intent(query, _tokens(query)).kind


def evaluate_counterfactual(
    suite: RouterIntentSuite,
    holdout: tuple[GenericHoldoutCase, ...],
    policy: CounterfactualPolicy,
) -> CounterfactualReport:
    comparisons = []
    for case in suite.cases:
        comparisons.append(_compare(case.case_id, case.query, "original", case.expected_search_behavior, policy))
    for case in holdout:
        comparisons.append(_compare(case.case_id, case.query, "independent_holdout", case.expected_search_behavior, policy))

    original = comparisons[:len(suite.cases)]
    independent = comparisons[len(suite.cases):]
    durable_cases = [case for case in suite.cases if case.expected_search_behavior == "memory_search"]
    by_id = {comparison.case_id: comparison for comparison in original}
    def false_skips(style=None):
        return sum(
            not by_id[case.case_id].counterfactual_retrieval_attempted
            for case in durable_cases if style is None or case.query_style == style
        )
    generic_original = [comparison for comparison in original if comparison.expected_search_behavior == "skip"]
    return CounterfactualReport(
        policy=policy.name,
        durable_queries_tested=len(durable_cases),
        durable_correctly_routed_to_memory=len(durable_cases) - false_skips(),
        durable_false_skips=false_skips(),
        direct_false_skips=false_skips("direct"),
        paraphrase_false_skips=false_skips("paraphrase"),
        generic_pattern_collision_false_skips=false_skips("generic_pattern_collision"),
        original_generic_controls=len(generic_original),
        original_generic_controls_correctly_skipped=sum(not item.counterfactual_retrieval_attempted for item in generic_original),
        original_unnecessary_memory_search_activations=sum(item.counterfactual_retrieval_attempted for item in generic_original),
        independent_generic_holdout=len(independent),
        independent_holdout_correctly_skipped=sum(not item.counterfactual_retrieval_attempted for item in independent),
        independent_holdout_unnecessary_memory_search_activations=sum(item.counterfactual_retrieval_attempted for item in independent),
        comparisons=tuple(comparisons),
    )


def _compare(case_id: str, query: str, suite: str, expected_search_behavior: str, policy: CounterfactualPolicy) -> RouteComparison:
    observed = current_intent(query)
    counterfactual = policy.classify(query, observed)
    current_attempted = observed not in _SKIP_INTENTS
    counterfactual_attempted = counterfactual not in _SKIP_INTENTS
    changed = observed != counterfactual
    desirable = None
    if changed:
        desirable = (
            counterfactual_attempted
            if expected_search_behavior == "memory_search"
            else not counterfactual_attempted
        )
    return RouteComparison(
        case_id, query, suite, expected_search_behavior, observed, counterfactual,
        current_attempted, counterfactual_attempted, changed, desirable,
    )
