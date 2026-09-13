"""Real-Gemma evaluation of post-mutation Current Scene overlay reactions.

All state, dialogue, and identities are synthetic. Persistent output is
structural only; generated text is represented by a short hash.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aifren.assistant import build_character_prompt
from benchmarks.active_state.production_session import ProductionSession, response_envelope
from aifren.runtime.config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR
from aifren.runtime.development_flight_recorder import development_flight_recorder
from aifren.llm.openai_compatible import OpenAICompatibleLLM
from aifren.runtime.local_model_runtime import LocalModelRuntime
from aifren.runtime.model_settings import get_model_settings


@dataclass(frozen=True)
class UiCase:
    name: str
    setup: tuple[str, ...]
    select: str
    expected_mode: tuple[str, str]


def cases() -> tuple[UiCase, ...]:
    base = (
        UiCase("remove_scarf", ("You're wearing a red scarf.",), "red scarf", ("speech", "normal")),
        UiCase("remove_blindfold", ("I blindfold you.",), "blindfold", ("vision", "available")),
        UiCase("remove_one_eye_cause", ("I blindfold you.", "I cover your eyes with my hands."),
               "blindfold", ("vision", "unavailable")),
        UiCase("remove_mouth_hand", ("I cover your mouth with my hand.",), "user hand", ("speech", "normal")),
        UiCase("put_down_cup", ("I hand you a cup.",), "cup", ("hands", "free")),
        UiCase("remove_earplugs", ("I put earplugs in your ears.",), "earplugs", ("hearing", "normal")),
        UiCase("release_wrist", ("Your left wrist is handcuffed to the pole.",), "pole", ("left_arm", "normal")),
        UiCase("stop_music", ("The music is so loud you can't hear me.",), "loud music", ("hearing", "normal")),
        UiCase("dismount_bicycle", ("You get on a bicycle.",), "bicycle", ("locomotion", "walking")),
    )
    return tuple(case for _ in range(4) for case in base)


def _attach(session: ProductionSession, provider: OpenAICompatibleLLM) -> None:
    session.provider = provider
    session.memory.llm = provider
    session.conversation.llm = provider
    session.service.llm = provider
    session.service._response_generator = None
    session.service.character_prompt = build_character_prompt(
        {"name": "Sable"},
        "Sable is a concise, warm synthetic companion. React naturally without emoji.",
    )


def _mode(session: ProductionSession, domain: str) -> str:
    effects = session.effects()
    return {
        "vision": effects.vision_mode,
        "speech": effects.speech_mode,
        "hands": effects.hands_mode,
        "hearing": effects.hearing_mode,
        "left_arm": effects.left_arm_mode,
        "locomotion": effects.locomotion_mode,
    }[domain]


def run(model: str) -> dict[str, object]:
    settings = get_model_settings()
    endpoint = str(settings["local_endpoint"])
    runtime = LocalModelRuntime(
        ROOT, model_directory=LOCAL_LLM_MODEL_DIR,
        context_size=LOCAL_LLM_CONTEXT_SIZE, log=lambda message: print("[runtime] " + str(message), flush=True),
    )
    status = runtime.start(
        endpoint=endpoint, selected_model=model,
        api_key=str(settings.get("local_api_key", "")),
    )
    if status.get("state") != "ready":
        raise RuntimeError("local model runtime was not ready")
    provider = OpenAICompatibleLLM(
        api_key=str(settings.get("local_api_key", "")), base_url=endpoint, model=model,
    )
    rows: list[dict[str, object]] = []
    try:
        for index, case in enumerate(cases()):
            session = ProductionSession(f"scene-ui-real-{case.name}-{index}")
            try:
                for setup in case.setup:
                    session.turn(setup, response_envelope("*Acknowledges the scene change.*"))
                snapshot = session.service.continuity_snapshot()
                relation = next(
                    row for row in snapshot["scene_relations"]
                    if case.select.casefold() in str(row.get("cause") or "").casefold()
                )
                _attach(session, provider)
                recorder = development_flight_recorder()
                start = len(recorder._events)
                result = session.service.apply_continuity_control(
                    command_id=str(uuid.uuid4()), action="interact_scene_relation",
                    expected_revision=snapshot["revision"], action_token=relation["clear_token"],
                )
                events = list(recorder._events)[start:]
                validation = next((event for event in reversed(events)
                    if event.get("event") == "response_contract_validation"), {})
                reply = str(session.conversation.messages[-1].get("content") or "")
                rows.append({
                    "case": case.name,
                    "mutation": result.get("outcome"),
                    "reaction_published": bool((result.get("reaction") or {}).get("published")),
                    "outcome": str(validation.get("outcome") or "error"),
                    "fallback_category": str(validation.get("fallback_category") or "none"),
                    "state_valid": _mode(session, case.expected_mode[0]) == case.expected_mode[1],
                    "reply_hash": hashlib.sha256(reply.encode("utf-8")).hexdigest()[:16],
                    "reply_characters": len(reply),
                    "internal_id_exposed": bool(re.search(r"relation:\d|[0-9a-f]{8}-[0-9a-f-]{27}", reply, re.I)),
                })
            finally:
                session.close()
    finally:
        if status.get("ownership") == "managed":
            runtime.stop()
    total = len(rows)
    fallback = sum(row["outcome"] == "fallback" for row in rows)
    return {
        "version": "scene-ui-event-real-model-v1",
        "model": Path(model).name,
        "turns": total,
        "direct": sum(row["outcome"] == "accepted" for row in rows),
        "repaired": sum(row["outcome"] == "repaired" for row in rows),
        "fallback": fallback,
        "fallback_ratio": round(fallback / total, 4) if total else 0.0,
        "state_valid": sum(bool(row["state_valid"]) for row in rows),
        "reactions_published": sum(bool(row["reaction_published"]) for row in rows),
        "internal_id_exposures": sum(bool(row["internal_id_exposed"]) for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    model = args.model or str(get_model_settings()["local_model"])
    payload = run(model)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "model", "turns", "direct", "repaired", "fallback", "fallback_ratio",
        "state_valid", "reactions_published", "internal_id_exposures",
    )}, indent=2))


if __name__ == "__main__":
    main()
