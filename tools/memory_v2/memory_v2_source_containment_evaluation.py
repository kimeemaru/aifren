"""Disconnected matched-policy QA for grounded Memory V2 response containment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import statistics
import time
from typing import Mapping, Sequence

from aifren.llm.output_canonicalization import canonicalize_model_output
from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerRequirement,
    bind_memory_answer_source_containment,
    memory_answer_repair_prompt,
    memory_answer_system_prompt,
    validate_memory_answer_response,
)
from tools.memory_v2.memory_v2_replacement_evaluation import ReplacementEvaluationCase
from aifren.continuity.memory_v2_replacement_shadow import V2ReplacementContext
from aifren.continuity.memory_v2_source_containment import (
    RECENT_CONVERSATION_BOUNDARY,
    RECENT_POLICIES,
    RecentOnlyAnchorSource,
    recent_only_anchor_sources,
    select_recent_messages,
)
from aifren.dialogue.presentation_metadata import parse_assistant_response


DEFAULT_CONTAINMENT_SEEDS = (141421, 173205)
CONTAINMENT_CASE_IDS = frozenset({
    "project_hobby",
    "boop_interaction",
    "head_pat_interaction",
    "assistant_game_boy_callback",
    "user_project_callback",
    "handheld_paraphrase",
    "first_project",
})


@dataclass(frozen=True)
class ContainedMemoryContext:
    policy: str
    context: tuple[dict[str, object], ...]
    requirement: MemoryAnswerRequirement
    recent_messages: tuple[dict[str, object], ...]
    recent_characters: int
    total_characters: int
    recent_only_sources: tuple[RecentOnlyAnchorSource, ...]
    composer_latency_ms: float


def _is_raw_conversation_message(item: Mapping[str, object]) -> bool:
    return (
        str(item.get("role", "")) in {"user", "assistant"}
        and any(key in item for key in ("timestamp", "origin", "truth_scope"))
    )


def compose_contained_memory_context(
    design: V2ReplacementContext,
    query_text: str,
    policy: str,
) -> ContainedMemoryContext:
    """Copy one V2 design into an explicitly partitioned recent-context shape."""
    started = time.perf_counter()
    if policy not in RECENT_POLICIES:
        raise ValueError("unknown V2 recent-context policy")
    copied = tuple(dict(item) for item in design.context)
    raw = tuple(item for item in copied if _is_raw_conversation_message(item))
    selected = select_recent_messages(raw, policy, maximum_messages=12)
    static = tuple(item for item in copied if not _is_raw_conversation_message(item))
    context = (
        *static,
        {"role": "user", "content": RECENT_CONVERSATION_BOUNDARY},
        *selected,
    )
    evidence_texts = tuple(
        value
        for item in design.memory_answer_requirement.evidence
        for value in (item.source_text, item.subject_key, item.value)
        if value
    )
    sources = recent_only_anchor_sources(query_text, evidence_texts, selected)
    requirement = bind_memory_answer_source_containment(
        design.memory_answer_requirement, query_text, selected,
    )
    return ContainedMemoryContext(
        policy,
        tuple(dict(item) for item in context),
        requirement,
        tuple(dict(item) for item in selected),
        sum(len(str(item.get("content", ""))) for item in selected),
        sum(len(str(item.get("content", ""))) for item in context),
        sources,
        round((time.perf_counter() - started) * 1000.0, 6),
    )


def _dialogue(value: object) -> str:
    return parse_assistant_response(canonicalize_model_output(value)).dialogue


def _generate(provider: object, context, prompt: str, seed: int) -> tuple[str, float]:
    started = time.perf_counter()
    response = provider.generate(context, prompt, seed=seed)
    return _dialogue(response), round((time.perf_counter() - started) * 1000.0, 3)


def _repair(
    provider: object,
    character_prompt: str,
    requirement: MemoryAnswerRequirement,
    draft: str,
    seed: int,
) -> dict[str, object]:
    started = time.perf_counter()
    prompt = character_prompt + "\n\n" + memory_answer_repair_prompt(requirement, draft)
    bounded = getattr(provider, "generate_bounded", None)
    if callable(bounded):
        response = bounded([], prompt, max_output_tokens=220, seed=seed)
    else:
        response = provider.generate([], prompt, seed=seed)
    dialogue = _dialogue(response)
    validation = validate_memory_answer_response(requirement, dialogue)
    return {
        "response": dialogue,
        "validation": asdict(validation),
        "generation_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "fallback_used": not validation.accepted,
        "fallback_response": (
            requirement.fallback_dialogue if not validation.accepted else ""
        ),
    }


def _matched_recent_sources(
    response: str,
    sources: Sequence[RecentOnlyAnchorSource],
) -> tuple[dict[str, object], ...]:
    lower = response.casefold()
    return tuple(
        asdict(item) for item in sources
        if re.search(rf"(?<!\w){re.escape(item.anchor)}(?!\w)", lower)
    )


def run_source_containment_evaluation(
    provider: object,
    character_prompt: str,
    inputs: Sequence[Mapping[str, object]],
    *,
    policies: Sequence[str] = tuple(sorted(RECENT_POLICIES)),
    seeds: Sequence[int] = DEFAULT_CONTAINMENT_SEEDS,
    repair: bool = True,
) -> dict[str, object]:
    """Evaluate identical grounded inputs under R0/R1/R2 before repair."""
    rows: list[dict[str, object]] = []
    for source in tuple(inputs):
        case = source.get("case")
        design = source.get("v2_design")
        if not isinstance(case, ReplacementEvaluationCase):
            raise TypeError("containment evaluation case is invalid")
        if not isinstance(design, V2ReplacementContext):
            raise TypeError("containment evaluation context is invalid")
        if (
            case.case_id not in CONTAINMENT_CASE_IDS
            or design.memory_answer_requirement.evidence_state != "grounded_evidence"
        ):
            continue
        for policy in tuple(str(item) for item in policies):
            contained = compose_contained_memory_context(design, case.query, policy)
            prompt = memory_answer_system_prompt(character_prompt, contained.requirement)
            for seed in tuple(int(item) for item in seeds):
                response, generation_ms = _generate(
                    provider, contained.context, prompt, seed,
                )
                validation = validate_memory_answer_response(
                    contained.requirement, response,
                )
                matched = _matched_recent_sources(
                    response, contained.recent_only_sources,
                )
                repaired = None
                if repair and not validation.accepted:
                    repaired = _repair(
                        provider, character_prompt, contained.requirement,
                        response, seed,
                    )
                rows.append({
                    "case_id": case.case_id,
                    "category": case.category,
                    "query": case.query,
                    "policy": policy,
                    "seed": seed,
                    "admitted_evidence": tuple(
                        asdict(item) for item in contained.requirement.evidence
                    ),
                    "response": response,
                    "raw_validation": asdict(validation),
                    "raw_pass": validation.accepted,
                    "unrelated_recent_additions": matched,
                    "repair": repaired,
                    "fallback_used": bool(
                        repaired is not None and repaired["fallback_used"]
                    ),
                    "context_characters": contained.total_characters,
                    "recent_characters": contained.recent_characters,
                    "recent_message_roles": tuple(
                        str(item.get("role", ""))
                        for item in contained.recent_messages
                    ),
                    "generation_ms": generation_ms,
                    "composer_ms": contained.composer_latency_ms,
                })
    summaries: dict[str, dict[str, object]] = {}
    for policy in tuple(str(item) for item in policies):
        selected = tuple(row for row in rows if row["policy"] == policy)
        latencies = tuple(float(row["generation_ms"]) for row in selected)
        contexts = tuple(int(row["context_characters"]) for row in selected)
        repairs = tuple(row for row in selected if row["repair"] is not None)
        summaries[policy] = {
            "draft_count": len(selected),
            "raw_pass_count": sum(bool(row["raw_pass"]) for row in selected),
            "raw_pass_rate": (
                round(sum(bool(row["raw_pass"]) for row in selected) / len(selected), 4)
                if selected else None
            ),
            "unrelated_decoration_count": sum(
                bool(row["unrelated_recent_additions"]) for row in selected
            ),
            "unrelated_decoration_rate": (
                round(sum(bool(row["unrelated_recent_additions"]) for row in selected) / len(selected), 4)
                if selected else None
            ),
            "repair_count": len(repairs),
            "repair_success_count": sum(
                bool(row["repair"]["validation"]["accepted"]) for row in repairs
            ),
            "fallback_count": sum(bool(row["fallback_used"]) for row in selected),
            "mean_context_characters": (
                round(statistics.mean(contexts), 1) if contexts else 0
            ),
            "mean_generation_ms": (
                round(statistics.mean(latencies), 3) if latencies else None
            ),
            "median_generation_ms": (
                round(statistics.median(latencies), 3) if latencies else None
            ),
            "max_generation_ms": max(latencies) if latencies else None,
        }
    return {
        "rows": rows,
        "policies": summaries,
        "case_ids": tuple(sorted({row["case_id"] for row in rows})),
        "production_prompt_influenced": False,
    }


__all__ = [
    "CONTAINMENT_CASE_IDS",
    "ContainedMemoryContext",
    "DEFAULT_CONTAINMENT_SEEDS",
    "compose_contained_memory_context",
    "run_source_containment_evaluation",
]
