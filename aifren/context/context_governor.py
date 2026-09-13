"""Request planning, not memory admission. No retrieval, persistence or inference.

Only the owners of admitted context construct items. Historical payloads keep
their wire roles and are never parsed as instructions or presentation controls.
Numerical scan/counter ceilings bound work, not the meaning of recent memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import time
from typing import Callable


HISTORY_WORK_MESSAGES = 4096
HISTORY_WORK_CHARACTERS = 2_000_000
COUNTER_WORK_CALLS = 6  # mandatory/final/correction, shared by compression replan
AUTHORITY_RANK = {"mandatory": 0, "current": 1, "source": 2, "derived": 3, "optional": 4}


class ContextBudgetExceeded(ValueError):
    """A required request cannot fit. Never silently truncate or select V1."""


def governor_enabled(authority: str) -> bool:
    # Process-local diagnosis/rollback; never changes memory authority.
    from aifren.runtime.config import CONTEXT_GOVERNOR_DEFAULT_ENABLED
    default = "1" if CONTEXT_GOVERNOR_DEFAULT_ENABLED else "0"
    return authority == "v2" and os.environ.get("AIFREN_CONTEXT_GOVERNOR", default) != "0"


@dataclass(frozen=True)
class ContextBudget:
    capacity_tokens: int
    output_reserve_tokens: int
    framing_reserve_tokens: int
    source: str
    token_counter: Callable[[str], int] | None = field(default=None, repr=False, compare=False)
    operating_target_tokens: int | None = None
    performance_profile: str = "synthetic_hard_budget"
    counter_identity: str = field(default="", repr=False)

    @property
    def input_tokens(self) -> int:
        return self.capacity_tokens - self.output_reserve_tokens - self.framing_reserve_tokens

    @classmethod
    def for_provider(cls, provider, *, max_output_tokens=None):
        from aifren.runtime.config import CONTEXT_GOVERNOR_FALLBACK_CAPACITY, CONTEXT_OPERATING_TARGET_TOKENS
        capacity = getattr(provider, "context_capacity_tokens", None)
        source = "provider_configured_capacity"
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            # Unknown online models are NOT assigned an invented advertised
            # capacity. This is an explicit conservative deployment ceiling.
            capacity = CONTEXT_GOVERNOR_FALLBACK_CAPACITY
            source = "deployment_ceiling_capacity_unknown"
            chars = getattr(provider, "context_budget_chars", None)
            if isinstance(chars, int) and chars > 0:
                capacity = min(capacity, max(512, chars // 3))
                source = "legacy_provider_ceiling_estimated"
        output = max_output_tokens or getattr(provider, "max_output_tokens", None)
        if not isinstance(output, int) or isinstance(output, bool) or output <= 0:
            # Unbounded provider output: reserve capacity, do not change sampling
            # or silently impose a new response length on the adapter.
            output = max(256, capacity // 8)
        counter = getattr(provider, "count_context_tokens", None)
        target = getattr(provider, "context_operating_target_tokens", None)
        profile = "provider_operating_target"
        if target is None:
            target, profile = CONTEXT_OPERATING_TARGET_TOKENS, "deployment_responsive_v1"
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            raise ValueError("context operating target must be a positive token count")
        identity = repr((type(provider).__name__, getattr(provider, "base_url", ""),
                         getattr(provider, "model", "")))
        return cls(capacity, output, max(128, capacity // 32), source,
                   counter if callable(counter) else None, target, profile, identity)


@dataclass(frozen=True)
class ContextPlanItem:
    owner: str
    kind: str
    authority: str
    messages: tuple[dict, ...]
    source_refs: tuple[str, ...] = ()
    character_id: str = ""
    truth_scope: str = ""
    required: bool = False
    order: int = 0
    # Only owner-proven equivalent projections may shadow each other. Sharing
    # one source does NOT make distinct propositions equivalent.
    equivalence_key: str = ""
    raw_supersedes: bool = False

    def __post_init__(self):
        if self.authority not in AUTHORITY_RANK:
            raise ValueError("unknown context authority")

    @property
    def estimated_tokens(self):
        return sum(_estimate(str(m.get("content", ""))) + 32 for m in self.messages)

    @classmethod
    def block(cls, owner, kind, text, **kwargs):
        return cls(owner, kind, kwargs.pop("authority", "mandatory"),
                   ({"role": "user", "content": str(text)},), **kwargs)


@dataclass(frozen=True)
class ContextPlan:
    messages: tuple[dict, ...]
    items: tuple[ContextPlanItem, ...]
    diagnostics: dict


def canonical_source_ref(message: dict) -> str:
    """Exact canonical identity used only for dedup, never as factual evidence."""
    if message.get("_context_source_ref"):
        return str(message["_context_source_ref"])
    data = {key: message[key] for key in ("role", "content", "timestamp", "truth_scope") if key in message}
    return "canonical-sha256:" + hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def canonical_user_event_ref(index, message):
    """Older continuity events bind index + speaker + time + content hash.

    Their payload predates full-record IDs. This exact alternative is only for
    representation dedup; it never admits facts or changes event records.
    """
    from aifren.memory_v2_store.store import parse_timestamp_us
    if message.get("role") != "user":
        return None
    try:
        at = parse_timestamp_us(message.get("timestamp"))
    except (ValueError, TypeError):
        return None
    if at is None:
        return None
    digest = hashlib.sha256(str(message.get("content", "")).encode()).hexdigest()
    return f"canonical-user:{index}:{at}:{digest}"


def _estimate(text: str) -> int:
    # English-compatible conservative chars/3; non-ASCII uses the byte-token
    # ceiling. Explicitly an estimate, never reported as exact tokenization.
    ascii_count = sum(ord(char) < 128 for char in text)
    return math.ceil(ascii_count / 3) + len(text.encode()) - ascii_count


class _Cost:
    """Request-local costs only; no persistent content or cross-identity cache.

    Candidate costs are estimates. Only mandatory and final complete contents
    use HTTP counting; fragment addition is never claimed as exact tokenization.
    """
    def __init__(self, budget, *, check_current=None, identity=()):
        self.budget = budget
        self.calls = 0
        self.cache = {}
        self.estimates = {}
        self.counter = budget.token_counter
        self.check_current = check_current or (lambda: None)
        self.identity = (budget.counter_identity, *identity)
        self.tokenizer_ms = 0.0
        self.method = "conservative_text_estimate_plus_framing"

    def estimate_text(self, text):
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in self.estimates:
            self.estimates[key] = _estimate(text)
        return self.estimates[key]

    def estimate(self, system_prompt, messages, *, scale=1.0):
        texts = [system_prompt] + [str(m.get("content", "")) for m in messages]
        # Separator costs deliberately included, with exact full verification
        # whenever the endpoint is available. This sum is an estimate only.
        return math.ceil(scale * sum(self.estimate_text(t) + 1 for t in texts)) + 32 * len(texts)

    def __call__(self, system_prompt, messages):
        self.check_current()
        texts = [system_prompt] + [str(m.get("content", "")) for m in messages]
        joined = "\n".join(texts)
        key = (self.identity, hashlib.sha256(joined.encode()).hexdigest())
        if self.counter is not None and key not in self.cache:
            if self.calls >= COUNTER_WORK_CALLS:
                self.counter = None
            else:
                self.calls += 1
                start = time.perf_counter()
                try:
                    result = self.counter(joined)
                    if isinstance(result, int) and not isinstance(result, bool) and result >= 0:
                        self.cache[key] = result
                    else:
                        self.counter = None
                except Exception:
                    self.counter = None
                finally:
                    self.tokenizer_ms += (time.perf_counter() - start) * 1000
                # Ownership cancellation is deliberately outside transport-error
                # handling. It stops further counting and response inference.
                self.check_current()
        if self.counter is not None and key in self.cache:
            self.method = "provider_content_tokens_plus_conservative_framing"
            return self.cache[key] + 32 * len(texts)
        self.method = "conservative_text_estimate_plus_framing"
        return self.estimate(system_prompt, messages)


def _exchanges(messages):
    groups = []
    for message in messages:
        if message.get("role") == "user":
            groups.append([])
        if groups:
            groups[-1].append(message)
    return groups


def plan_context(*, system_prompt: str, budget: ContextBudget, current: dict,
                 recent: list[dict], items=(), character_id="", truth_scope="",
                 contained=False, _counter=None, check_current=None) -> ContextPlan:
    """Whole authority and immediate continuity, within a soft working target.

    Capacity is a hard ceiling, never a utilization objective. Already-admitted
    continuity gets consideration before older expendable raw history.
    """
    started = time.perf_counter()
    cost = _counter or _Cost(budget, check_current=check_current,
                            identity=(character_id, truth_scope))
    cost.check_current()
    items = tuple(items)
    initial_drops = []
    eligible = []
    for item in sorted(items, key=lambda i: (AUTHORITY_RANK[i.authority], i.order, i.owner, i.kind)):
        if ((item.character_id and item.character_id != character_id)
                or (item.truth_scope and item.truth_scope != truth_scope)):
            if item.required:
                raise ContextBudgetExceeded("Required context identity does not match the current turn.")
            initial_drops.append({"owner": item.owner, "kind": item.kind, "reason": "identity_mismatch"})
            continue
        equivalent = next((prior for prior in eligible if item.equivalence_key
                           and item.equivalence_key == prior.equivalence_key), None)
        if equivalent is not None:
            initial_drops.append({"owner": item.owner, "kind": item.kind, "reason": "stronger_equivalent_authority"})
            continue
        eligible.append(item)
    mandatory = [i for i in eligible if i.required]

    def render(chosen, history):
        return [m for i in sorted(chosen, key=lambda i: (i.order, i.owner, i.kind)) for m in i.messages] + history + [current]

    def covered(item, history):
        if not item.raw_supersedes or not item.source_refs:
            return False
        refs = {canonical_source_ref(m) for m in history + [current]}
        refs.update(ref for m in history + [current] for ref in m.get("_context_source_refs", ()))
        return set(item.source_refs) <= refs

    mandatory_messages = render(mandatory, [])
    mandatory_cost = cost(system_prompt, mandatory_messages)
    if mandatory_cost > budget.input_tokens:
        raise ContextBudgetExceeded(
            f"Required context exceeds the configured input budget ({mandatory_cost} > {budget.input_tokens} tokens).")
    configured_target = budget.operating_target_tokens or budget.input_tokens
    target = min(budget.input_tokens, max(mandatory_cost, configured_target))
    groups = [] if contained else _exchanges(recent)
    counted = cost.counter is not None
    framing = 32 * (len(mandatory_messages) + 1)
    denominator = max(1, cost.estimate(system_prompt, mandatory_messages) - framing)
    scale = max(0.05, (mandatory_cost - framing) / denominator) if counted else 1.0

    def select(scale):
        chosen, history, count = list(mandatory), [], 0
        base_estimate = cost.estimate(system_prompt, mandatory_messages, scale=scale)
        def fits(candidate, raw):
            cost.check_current()
            variable = cost.estimate(system_prompt, render(candidate, raw), scale=scale) - base_estimate
            return mandatory_cost + variable <= target
        # Keep the adjacent exchange first when it fits; never cut it in half.
        if groups and fits(chosen, groups[-1]):
            history, count = list(groups[-1]), 1
        # Current thread status is an admitted proposition, not a bare topic.
        # Newer status/corrections cannot be replaced by older supporting prose.
        for item in eligible:
            if item.required or item.authority == "optional" or contained or covered(item, history):
                continue
            if fits(chosen + [item], history):
                chosen.append(item)
        # Extend only a coherent suffix, never skip a large adjacent exchange
        # to fish for smaller disconnected old messages.
        if count:
            for group in reversed(groups[:-1]):
                candidate_history = list(group) + history
                candidate = [i for i in chosen if i.required or not covered(i, candidate_history)]
                if not fits(candidate, candidate_history):
                    break
                chosen, history, count = candidate, candidate_history, count + 1
        for item in eligible:
            if item.required or item.authority != "optional" or contained or covered(item, history):
                continue
            if fits(chosen + [item], history):
                chosen.append(item)
        return chosen, history, count

    accepted, history, low = select(scale)
    messages = render(accepted, history)
    final_cost = cost(system_prompt, messages)
    if counted and cost.counter is None:
        # No invisible mixed exact/estimate plan after an endpoint failure.
        # Recompute all selection, mandatory and final costs with one method.
        return plan_context(system_prompt=system_prompt, budget=budget, current=current,
            recent=recent, items=items, character_id=character_id, truth_scope=truth_scope,
            contained=contained, _counter=cost)
    corrections = 0
    if final_cost > target:
        corrections = 1
        # One bounded correction, no HTTP binary search. If the estimate still
        # cannot safely fit, retain the already-verified mandatory request.
        room = max(1, target - mandatory_cost)
        scale *= max(1.1, (final_cost - mandatory_cost) / room * 1.08)
        accepted, history, low = select(scale)
        messages = render(accepted, history)
        final_cost = cost(system_prompt, messages)
        if counted and cost.counter is None:
            return plan_context(system_prompt=system_prompt, budget=budget, current=current,
                recent=recent, items=items, character_id=character_id, truth_scope=truth_scope,
                contained=contained, _counter=cost)
        if final_cost > target:
            accepted, history, low = list(mandatory), [], 0
            messages, final_cost = mandatory_messages, mandatory_cost
    if final_cost > budget.input_tokens:
        raise ContextBudgetExceeded("Final context no longer fits the configured input budget.")
    dropped = list(initial_drops)
    for item in eligible:
        if item not in accepted:
            reason = ("memory_query_containment" if contained else
                      "exact_sources_already_raw" if covered(item, history) else "operating_target")
            dropped.append({"owner": item.owner, "kind": item.kind, "reason": reason})
    cost.check_current()
    assistant = [str(m.get("content", "")) for m in history if m.get("role") == "assistant"]
    diagnostics = {
        "capacity_tokens": budget.capacity_tokens, "capacity_source": budget.source,
        "output_reserve_tokens": budget.output_reserve_tokens,
        "framing_reserve_tokens": budget.framing_reserve_tokens,
        "input_budget_tokens": budget.input_tokens, "mandatory_tokens": mandatory_cost,
        "continuity_budget_tokens": max(0, target - mandatory_cost),
        "operating_target_tokens": configured_target, "effective_target_tokens": target,
        "mandatory_above_target": mandatory_cost > configured_target,
        "performance_profile": budget.performance_profile,
        "target_headroom_tokens": target - final_cost,
        "candidate_cost_method": "calibrated_estimate_final_verified" if counted else "conservative_text_estimate",
        "verification_corrections": corrections,
        "tokenizer_ms": cost.tokenizer_ms,
        "planning_ms": (time.perf_counter() - started) * 1000,
        "final_tokens": final_cost, "headroom_tokens": budget.input_tokens - final_cost,
        "final_request_characters": len(system_prompt) + sum(len(str(m.get("content", ""))) for m in messages),
        "counting_method": cost.method, "tokenizer_calls": cost.calls,
        "recent_messages": len(history) + 1, "recent_exchanges": low,
        "recent_characters": sum(len(str(m.get("content", ""))) for m in history + [current]),
        "included_owners": list(dict.fromkeys(i.owner for i in accepted)),
        "dropped": dropped[:32], "dropped_count": len(dropped),
        "duplicate_items": sum(d["reason"] in {"exact_sources_already_raw", "stronger_equivalent_authority"} for d in dropped),
        "memory_contained": contained,
        "history_assistant_count": len(assistant),
        "history_leading_action_count": sum(s.lstrip().startswith("*") for s in assistant),
        "history_closing_question_count": sum(s.rstrip().rstrip('*"').rstrip().endswith("?") for s in assistant),
        "history_long_reply_count": sum(len(s.split()) > 100 for s in assistant),
    }
    planned = [ContextPlanItem.block("character", "personality_and_response_obligations", system_prompt,
                   required=True, character_id=character_id, truth_scope=truth_scope, order=0), *accepted]
    if history:
        planned.append(ContextPlanItem("canonical_history", "raw_recent", "source", tuple(history),
            tuple(canonical_source_ref(m) for m in history), character_id, truth_scope, order=100))
    planned.append(ContextPlanItem("current_user", "current_turn", "mandatory", (current,),
        (canonical_source_ref(current),), character_id, truth_scope, required=True, order=101))
    diagnostics["included_owners"] = list(dict.fromkeys(i.owner for i in planned))
    diagnostics["items"] = [dict(owner=i.owner, kind=i.kind, authority=i.authority,
        required=i.required, estimated_tokens=i.estimated_tokens, source_count=len(i.source_refs)) for i in planned[:32]]
    return ContextPlan(tuple(messages), tuple(planned), diagnostics)
