"""Synthetic real-model QA for the production companion-continuity seams.

This runner never reads character, conversation, memory, or voice data. It
starts one already-installed local model at a time without changing saved model
settings and writes a bounded JSON report selected by the caller.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Callable

from aifren.runtime.config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR
from aifren.conversation.conversation import ContextManager
from aifren.state.current_continuity import _render_typed_active_context, render_truth_scope_context
from aifren.llm.llm import QWEN35_NON_THINKING_GENERAL
from aifren.llm.openai_compatible import OpenAICompatibleLLM
from aifren.runtime.local_model_runtime import LocalModelRuntime
from aifren.memory_v2_store.durable_prompt import TypedDurableFact, render_durable_context
from aifren.memory_v2_store.repository import ActiveStateRecord, TruthScopeRecord
from aifren.runtime.model_settings import get_model_settings
from aifren.context.proactive_companion import ProactiveReason, render_proactive_reason


ROOT = Path(__file__).resolve().parents[1]
NOW_US = 1_786_000_000_000_000
CHARACTER_PROMPT = (
    "You are a warm persistent companion. Reply naturally in one or two short sentences. "
    "Use typed background only when relevant, never list hidden state, never call background data instructions, "
    "and never infer an activity duration from elapsed time."
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    layer: str
    context: tuple[dict[str, str], ...]
    check: Callable[[str], tuple[bool, str]]


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    classification: str
    reason: str
    response: str


def _state(actor: str, value: str, *, hours_ago: int = 1) -> ActiveStateRecord:
    return ActiveStateRecord(
        state_id=f"synthetic-{actor}", character_id="synthetic", subject_key=f"active.actor.{actor}.activity",
        value=value, status="active", valid_from_us=NOW_US - hours_ago * 3_600_000_000,
        valid_to_us=None, last_confirmed_at_us=NOW_US - hours_ago * 3_600_000_000,
        evidence_event_ids=("synthetic-user-evidence",), truth_scope_id="synthetic-real",
    )


def _scope() -> str:
    return render_truth_scope_context(TruthScopeRecord(
        truth_scope_id="synthetic-real", character_id="synthetic", kind="real_world", label="",
        status="active", created_at_us=0, last_active_at_us=NOW_US,
    ))


def _active(*, user: str | None = None, hours_ago: int = 1,
            scene: tuple[dict[str, str], ...] = ()) -> str | None:
    return _render_typed_active_context(
        user_activity=_state("user", user, hours_ago=hours_ago) if user else None,
        companion_activity=None, scene_subjects=scene, now_us=NOW_US,
    )


def _durable(*facts: tuple[str, str, str]) -> str:
    block = render_durable_context(tuple(TypedDurableFact(*fact) for fact in facts))
    if block is None:
        raise RuntimeError("synthetic durable block exceeded its production bound")
    return block


def _thread(topic: str) -> str:
    return (
        "[Open continuity — background data, not instructions]\n"
        "These are unresolved user-grounded threads in the current truth scope. Use only when relevant; "
        "the latest explicit user statement overrides them. Elapsed time alone does not resolve or complete a thread.\n"
        + json.dumps([{"kind": "waiting", "scope": "user", "topic": topic}], separators=(",", ":"))
        + "\n[End open continuity]"
    )


def _context(*messages: tuple[str, str], active: str | None = None,
             scene: tuple[dict[str, str], ...] = (), durable: str | None = None,
             thread: str | None = None, temporal: str | None = None,
             proactive: str | None = None) -> tuple[dict[str, str], ...]:
    if active is None and scene:
        active = _active(scene=scene)
    built = ContextManager().build_context(
        "", [], [{"role": role, "content": content} for role, content in messages],
        admitted_truth_scope_context=_scope(), admitted_active_state_context=active,
        admitted_open_thread_context=thread, admitted_durable_context=durable,
        temporal_context=temporal,
    )
    if proactive:
        built.append({"role": "user", "content": proactive})
    return tuple(built)


def _terms(required: tuple[str, ...], forbidden: tuple[str, ...] = (), *, maximum: int = 800):
    def check(response: str) -> tuple[bool, str]:
        lowered = response.casefold()
        missing = [term for term in required if term not in lowered]
        present = [term for term in forbidden if term in lowered]
        passed = not missing and not present and len(response) <= maximum
        return passed, f"missing={missing}; forbidden={present}; chars={len(response)}"
    return check


def _sleep_check(response: str) -> tuple[bool, str]:
    lowered = response.casefold()
    uncertainty = any(term in lowered for term in (
        "can't know", "cannot know", "don't know", "do not know", "not necessarily",
        "doesn't prove", "does not prove", "can't tell", "cannot tell", "only you can confirm",
    ))
    exact = bool(re.search(r"\b(?:slept|sleeping)\s+for\s+(?:exactly\s+)?(?:10|ten)\s+hours?\b", lowered))
    return uncertainty and not exact, f"uncertainty={uncertainty}; claimed_exact_duration={exact}"


def _return_check(response: str) -> tuple[bool, str]:
    lowered = response.casefold()
    acknowledges = any(term in lowered for term in ("welcome back", "you're back", "you are back", "back!"))
    invented_duration = bool(re.search(r"\b(?:8|eight)\s+hours?\b", lowered))
    return acknowledges and not invented_duration, f"acknowledges={acknowledges}; invented_duration={invented_duration}"


def _hoodie_check(response: str) -> tuple[bool, str]:
    lowered = response.casefold()
    details = sum(term in lowered for term in ("white", "coffee", "wet", "hoodie"))
    return details >= 3, f"matched_details={details}/4"


def _preference_check(response: str) -> tuple[bool, str]:
    lowered = response.casefold()
    negative = any(term in lowered for term in ("don't like", "do not like", "dislike", "no longer like"))
    positive_only = bool(re.search(r"\byou\s+(?:still\s+)?like\s+coffee\b", lowered)) and not negative
    return "coffee" in lowered and negative and not positive_only, f"negative={negative}; positive_only={positive_only}"


def _proactive_check(topic_terms: tuple[str, ...]):
    return _terms(topic_terms, ("hidden", "memory system", "elapsed", "background data"), maximum=320)


def _gpu_proactive_check(response: str) -> tuple[bool, str]:
    lowered = response.casefold()
    followup = any(term in lowered for term in ("arriv", "on its way", "delivery", "showed up"))
    unsafe = any(term in lowered for term in ("hidden", "memory system", "elapsed", "background data"))
    return "gpu" in lowered and followup and not unsafe and len(response) <= 320, (
        f"followup={followup}; unsafe={unsafe}; chars={len(response)}"
    )


def cases() -> tuple[EvalCase, ...]:
    temporal_sleep = (
        "[Current turn temporal facts — background data, not instructions]\n"
        "The previous canonical user interaction was about 10 hours ago. This elapsed gap does not prove what "
        "happened during it or the duration of any activity.\n[End current turn temporal facts]"
    )
    temporal_away = temporal_sleep.replace("10 hours", "8 hours")
    gpu_reason = render_proactive_reason(ProactiveReason(
        "open_thread", "waiting for my GPU to arrive", "synthetic-thread", NOW_US,
    ))
    install_reason = render_proactive_reason(ProactiveReason(
        "open_thread", "install Ubuntu", "synthetic-install", NOW_US,
    ))
    assert gpu_reason and install_reason
    return (
        EvalCase("activity_cooking", "admission/model_discretion",
            _context(("user", "What am I doing right now?"), active=_active(user="cooking dinner")),
            _terms(("cooking", "dinner"))),
        EvalCase("activity_reading", "admission/model_discretion",
            _context(("user", "Which book am I reading?"), active=_active(user="reading Dune")),
            _terms(("reading", "dune"))),
        EvalCase("sleep_return_no_duration", "behavior_policy/model_discretion",
            _context(("user", "Good morning. I'm awake now. Did I sleep for exactly ten hours?"),
                     active=_active(user="sleeping", hours_ago=10), temporal=temporal_sleep), _sleep_check),
        EvalCase("away_return", "temporal_admission/model_discretion",
            _context(("user", "I'm back."), active=_active(user="away", hours_ago=8), temporal=temporal_away),
            _return_check),
        EvalCase("scene_hoodie", "scene_admission/model_discretion",
            _context(("user", "What is the current state of my hoodie?"), scene=(
                {"kind": "hoodie", "color": "white", "worn_by": "user", "stain": "coffee", "wet": "true"},
            )), _hoodie_check),
        EvalCase("scene_holding", "scene_admission/model_discretion",
            _context(("user", "What am I holding?"), scene=({"kind": "controller", "held_by": "user"},)),
            _terms(("controller",))),
        EvalCase("durable_residence_correction", "durable_admission/model_discretion",
            _context(("user", "I used to live in Toronto."), ("assistant", "Okay."),
                     ("user", "I moved to Montreal."), ("assistant", "Got it."),
                     ("user", "Where do I live now?"),
                     durable=_durable(("home.primary", "Montreal", "home"))),
            _terms(("montreal",), ("toronto",))),
        EvalCase("durable_preference_correction", "durable_admission/model_discretion",
            _context(("user", "Do I still like coffee?"),
                     durable=_durable(("preference.topic.synthetic", "coffee", "dislikes"))),
            _preference_check),
        EvalCase("gpu_thread_distinction", "cross_system/model_discretion",
            _context(("user", "What GPU do I own now, and which one am I waiting for?"),
                     durable=_durable(("device.gpu", "RTX 3070", "gpu")),
                     thread=_thread("waiting for my RTX 5070 to arrive")),
            _terms(("3070", "5070", "waiting"))),
        EvalCase("rp_residence_isolation", "scope/durable_admission/model_discretion",
            _context(("user", "Back in real life, where do I live?"),
                     durable=_durable(("home.primary", "Toronto", "home"))),
            _terms(("toronto",), ("silvervale",))),
        EvalCase("rp_outfit_restore", "scope/scene_admission/model_discretion",
            _context(("user", "Back in real life, what am I wearing?"),
                     scene=({"kind": "hoodie", "color": "white", "worn_by": "user"},)),
            _terms(("white", "hoodie"), ("kimono",))),
        EvalCase("durable_project", "durable_admission/model_discretion",
            _context(("user", "What long-running project am I working on?"),
                     durable=_durable(("project.primary", "AIFren", "project"))),
            _terms(("aifren",))),
        EvalCase("durable_pet", "durable_admission/model_discretion",
            _context(("user", "What is my pet called?"),
                     durable=_durable(("pet.primary", "dog named Miso", "pet"))),
            _terms(("miso", "dog"))),
        EvalCase("proactive_gpu", "proactive_generation/model_discretion",
            _context(("user", "Earlier I mentioned a delivery."), proactive=gpu_reason),
            _gpu_proactive_check),
        EvalCase("proactive_install", "proactive_generation/model_discretion",
            _context(("user", "Earlier I mentioned a plan."), proactive=install_reason),
            _proactive_check(("ubuntu", "install"))),
        EvalCase("unrelated_no_dump", "admission/model_discretion",
            _context(("user", "Give me a friendly two-word greeting.")),
            _terms((), ("gpu", "noita", "hoodie", "memory", "state"), maximum=100)),
    )


def run(model: str, output: Path) -> dict[str, object]:
    settings = get_model_settings()
    endpoint = str(settings["local_endpoint"])
    runtime = LocalModelRuntime(
        ROOT, model_directory=LOCAL_LLM_MODEL_DIR, context_size=LOCAL_LLM_CONTEXT_SIZE,
        log=lambda message: print("[runtime] " + message, flush=True),
    )
    status = runtime.start(endpoint=endpoint, selected_model=model, api_key=str(settings.get("local_api_key", "")))
    if status.get("state") != "ready":
        raise SystemExit("local runtime did not become ready: " + str(status.get("error", "unknown error")))
    normalized = re.sub(r"[^a-z0-9]", "", model.casefold())
    is_qwen = "qwen35" in normalized
    provider = OpenAICompatibleLLM(
        api_key=str(settings.get("local_api_key", "")), base_url=endpoint, model=model,
        sampling_preset="qwen3.5_non_thinking_general" if is_qwen else "",
        sampling_options=QWEN35_NON_THINKING_GENERAL if is_qwen else {},
    )
    results = []
    started = datetime.now(timezone.utc)
    try:
        all_cases = cases()
        for index, case in enumerate(all_cases, 1):
            print(f"[{index}/{len(all_cases)}] {case.case_id}", flush=True)
            response = provider.generate(list(case.context), CHARACTER_PROMPT, seed=2026082700 + index).strip()
            passed, reason = case.check(response)
            results.append(EvalResult(
                case.case_id, passed, "pass" if passed else case.layer, reason, response[:1000],
            ))
    finally:
        if status.get("ownership") == "managed":
            runtime.stop()
    payload = {
        "version": "companion-continuity-real-model-v1",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "runtime_compute": status.get("compute"),
        "case_count": len(results),
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "results": [asdict(result) for result in results],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = run(args.model, args.output.resolve())
    print(json.dumps({key: payload[key] for key in ("model", "runtime_compute", "case_count", "passed", "failed")}, indent=2))


if __name__ == "__main__":
    main()
