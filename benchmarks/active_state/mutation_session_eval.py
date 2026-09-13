"""Mutation-heavy real-model evaluation through the actual AssistantService.

All evidence, history, character data, and output are synthetic.  The runner
does not change saved model settings and records structural metrics only.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from aifren.assistant import build_character_prompt
from benchmarks.active_state.production_session import ProductionSession, response_envelope
from aifren.state.capability_policy import validate_capability_response
from aifren.runtime.config import LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_MODEL_DIR
from aifren.runtime.development_flight_recorder import development_flight_recorder
from aifren.llm.llm import QWEN35_NON_THINKING_GENERAL
from aifren.llm.openai_compatible import OpenAICompatibleLLM
from aifren.runtime.local_model_runtime import LocalModelRuntime
from aifren.runtime.model_settings import get_model_settings
from aifren.dialogue.presentation_metadata import parse_assistant_response
from aifren.dialogue.response_requirements import derive_response_requirement, validate_response_requirement


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TurnCase:
    bucket: str
    text: str


def cases() -> tuple[TurnCase, ...]:
    prefix = (
        TurnCase("sleep_combined", "Go to sleep."),
        TurnCase("sleep_combined", "I gently touch your shoulder."),
        TurnCase("state_mutation_clearing_constraint", "You can wake up now."),
        TurnCase("state_mutation_clearing_constraint", "I uncover your mouth."),
        TurnCase("state_mutation_clearing_constraint", "I uncover your eyes."),
        TurnCase("state_mutation_unrestricted", "Put the book down."),
        TurnCase("holder_transfer_release", "Give me the cup."),
        TurnCase("holder_transfer_release", "I put the cup on the table."),
    )
    cycle = (
        TurnCase("state_mutation_unrestricted", "I put a blue hat on you."),
        TurnCase("ordinary_conversation", "That color is cheerful."),
        TurnCase("direct_current_state_query", "What are you wearing?"),
        TurnCase("state_mutation_unrestricted", "Take off the hat."),
        TurnCase("state_mutation_causing_constraint", "I cover your mouth with my hand."),
        TurnCase("speech_constrained", "How do you feel?"),
        TurnCase("speech_constrained", "A soft noise sounds nearby."),
        TurnCase("state_mutation_clearing_constraint", "I uncover your mouth."),
        TurnCase("direct_current_state_query", "Can you talk normally?"),
        TurnCase("state_mutation_causing_constraint", "I blindfold you."),
        TurnCase("vision_unavailable", "I gently tap your shoulder."),
        TurnCase("direct_current_state_query", "Can you see?"),
        TurnCase("state_mutation_clearing_constraint", "I take the blindfold off you."),
        TurnCase("state_mutation_unrestricted", "You're holding a cup."),
        TurnCase("holder_transfer_release", "Give me the cup."),
        TurnCase("holder_transfer_release", "I put the cup on the table."),
        TurnCase("direct_current_state_query", "What are you holding?"),
        TurnCase("state_mutation_unrestricted", "Sit down."),
        TurnCase("ordinary_conversation", "Is that comfortable?"),
        TurnCase("state_mutation_unrestricted", "Stand up."),
        TurnCase("state_mutation_unrestricted", "You're wearing a red scarf."),
        TurnCase("direct_current_state_query", "What are you wearing?"),
        TurnCase("state_mutation_unrestricted", "Take off the scarf."),
    )
    breadth_cycle = (
        TurnCase("state_mutation_causing_constraint", "Well, the room is pitch black."),
        TurnCase("vision_unavailable", "I squeeze your hand reassuringly."),
        TurnCase("direct_current_state_query", "So what can you see?"),
        TurnCase("state_mutation_clearing_constraint", "The lights come on."),
        TurnCase("direct_current_state_query", "Can you see now?"),
        TurnCase("state_mutation_causing_constraint", "By the way, the music is so loud you can't hear me."),
        TurnCase("direct_current_state_query", "Can you hear me?"),
        TurnCase("state_mutation_clearing_constraint", "The music stops."),
        TurnCase("state_mutation_causing_constraint", "I put earplugs in your ears."),
        TurnCase("ordinary_conversation", "I tap lightly on the table."),
        TurnCase("state_mutation_clearing_constraint", "I remove the earplugs."),
        TurnCase("state_mutation_causing_constraint", "The perfume makes it hard to smell."),
        TurnCase("direct_current_state_query", "Can you smell anything?"),
        TurnCase("state_mutation_clearing_constraint", "The perfume clears."),
        TurnCase("state_mutation_causing_constraint", "The spice makes it impossible to taste."),
        TurnCase("direct_current_state_query", "Can you taste anything?"),
        TurnCase("state_mutation_clearing_constraint", "The spice is gone."),
        TurnCase("state_mutation_unrestricted", "Your blue and purple sparkly scrunchie is on your wrist."),
        TurnCase("ordinary_conversation", "That looks playful."),
        TurnCase("state_mutation_causing_constraint", "Your left wrist is handcuffed to a pole."),
        TurnCase("direct_current_state_query", "Are your hands free?"),
        TurnCase("state_mutation_clearing_constraint", "Release the left handcuff."),
        TurnCase("state_mutation_unrestricted", "I put a blue hat on you."),
        TurnCase("state_mutation_unrestricted", "Your blue hat is red now."),
        TurnCase("direct_current_state_query", "What are you wearing?"),
        TurnCase("state_mutation_unrestricted", "I replace your red hat with a green one."),
        TurnCase("ordinary_conversation", "Does the new color suit you?"),
        TurnCase("state_mutation_unrestricted", "A bicycle is nearby."),
        TurnCase("state_mutation_unrestricted", "You get on the bicycle."),
        TurnCase("direct_current_state_query", "How are you moving?"),
        TurnCase("state_mutation_clearing_constraint", "You get off the bicycle."),
        TurnCase("state_mutation_causing_constraint", "Your left arm is missing."),
        TurnCase("ordinary_conversation", "You can still react however feels natural."),
        TurnCase("state_mutation_clearing_constraint", "Your left arm is available again."),
        TurnCase("state_mutation_unrestricted", "I hand you a red lantern."),
        TurnCase("holder_transfer_release", "Put the red lantern down."),
        TurnCase("state_mutation_unrestricted", "Throw the red lantern away."),
        TurnCase("state_mutation_unrestricted", "Pick up the red lantern."),
        TurnCase("direct_current_state_query", "What are you holding?"),
        TurnCase("companion_action", "What would you like to do?"),
        TurnCase("state_mutation_unrestricted", "Let's roleplay that we're in a quiet observatory."),
        TurnCase("state_mutation_unrestricted", "You're wearing a silver cap."),
        TurnCase("direct_current_state_query", "What are you wearing?"),
        TurnCase("state_mutation_clearing_constraint", "Back to real life."),
        TurnCase("direct_current_state_query", "What are you wearing?"),
    )
    adversarial_cycle = (
        TurnCase("state_mutation_unrestricted", "I'm holding a lantern."),
        TurnCase("direct_current_state_query", "What am I holding?"),
        TurnCase("state_mutation_unrestricted", "I put the lantern on the table."),
        TurnCase("direct_current_state_query", "Where is the lantern?"),
        TurnCase("state_mutation_unrestricted", "You're holding a book in your left hand."),
        TurnCase("direct_current_state_query", "Which hand is free?"),
        TurnCase("direct_current_state_query", "What is affecting your hands?"),
        TurnCase("direct_current_state_query", "Are you still holding the book?"),
        TurnCase("holder_transfer_release", "Put the book down."),
        TurnCase("state_mutation_unrestricted", "Sit down."),
        TurnCase("direct_current_state_query", "What position are you in?"),
        TurnCase("state_mutation_unrestricted", "You're wearing a green hat."),
        TurnCase("state_mutation_unrestricted", "Your hat is blue. Sorry, red."),
        TurnCase("direct_current_state_query", "Are you still wearing the red hat?"),
        TurnCase("ordinary_conversation", "I'm holding a lantern?"),
        TurnCase("ordinary_conversation", "I wonder what would happen if the smoke makes it hard to see."),
        TurnCase("ordinary_conversation", "The wheelchair might be nearby."),
        TurnCase("state_mutation_causing_constraint", "It is too loud to hear me."),
        TurnCase("direct_current_state_query", "What can you hear?"),
        TurnCase("state_mutation_clearing_constraint", "It quiets down."),
        TurnCase("state_mutation_unrestricted", "You're wearing a left glove and a right glove."),
        TurnCase("state_mutation_unrestricted", "Your left glove is wet."),
        TurnCase("ordinary_conversation", "The glove colors are fun."),
        TurnCase("state_mutation_unrestricted", "Take off the left glove."),
    )
    direct_extension = (
        TurnCase("direct_current_state_query", "What are you wearing?"),
        TurnCase("direct_current_state_query", "What are you holding?"),
        TurnCase("direct_current_state_query", "What am I holding?"),
        TurnCase("direct_current_state_query", "Can you see?"),
        TurnCase("direct_current_state_query", "What is covering your eyes?"),
        TurnCase("direct_current_state_query", "Can you hear me?"),
        TurnCase("direct_current_state_query", "Can you talk normally?"),
        TurnCase("direct_current_state_query", "Are your hands free?"),
        TurnCase("direct_current_state_query", "Which hand is free?"),
        TurnCase("direct_current_state_query", "What is affecting your hands?"),
        TurnCase("direct_current_state_query", "What are you doing?"),
        TurnCase("direct_current_state_query", "What position are you in?"),
        TurnCase("direct_current_state_query", "How are you moving?"),
        TurnCase("direct_current_state_query", "Where is the cup?"),
        TurnCase("direct_current_state_query", "Are you still wearing the hat?"),
    )
    casual_extension = tuple(TurnCase("ordinary_conversation", text) for text in (
        "That was a lot of testing.", "Tell me something cheerful.",
        "I think tea sounds nice.", "What kind of afternoon would you enjoy?",
        "That answer made me laugh.", "Let's pause for a moment.",
        "I was thinking about books today.", "You seem thoughtful.",
        "This has been an oddly busy conversation.", "Tell me a tiny joke.",
        "I like the quiet between questions.", "Maybe we should relax.",
        "What is your favorite kind of weather?", "That sounds cozy.",
        "I appreciate the company.", "Let's keep chatting normally.",
        "I wonder what tomorrow will feel like.", "That is an interesting thought.",
        "We can take our time.", "How has the conversation felt to you?",
        "I finally remembered to water the plants.", "That was surprisingly satisfying.",
        "Some days feel longer than others.", "Today has been a little strange.",
        "I could use a quiet evening.", "What helps you unwind?",
        "I found an old notebook earlier.", "It was full of half-finished ideas.",
        "Maybe unfinished ideas are still useful.", "What do you think?",
        "I heard a funny story this morning.", "The ending was completely unexpected.",
        "That reminds me of a silly mistake I made.", "At least I can laugh about it now.",
        "The room feels peaceful today.", "I like conversations without a deadline.",
        "We have covered a lot of ground.", "A normal chat sounds good now.",
        "I might make something warm to drink later.", "Tea is hard to get wrong.",
        "There is something nice about familiar routines.", "Small comforts count.",
        "I have been thinking about taking a walk tomorrow.", "Fresh air usually helps.",
        "No rush on answering this one.", "What has caught your curiosity lately?",
        "I enjoy hearing how other people notice things.", "Different perspectives are useful.",
        "That thought can sit for a while.", "We do not need to solve everything.",
        "I am glad this feels more like a conversation.", "It is okay to be a little playful.",
        "Tell me one harmless odd observation.", "That is exactly the sort of thing I meant.",
        "I should probably stretch soon.", "Sitting too long makes me restless.",
        "Anyway, I am still here.", "We can keep this easygoing.",
        "What would make the next hour pleasant?", "That sounds like a decent plan.",
    ))
    rows = prefix + cycle * 5 + breadth_cycle * 4 + adversarial_cycle * 2 + direct_extension + casual_extension
    if not 420 <= len(rows) <= 440:
        raise AssertionError("pre-manual production evaluation must contain 420–440 turns")
    return rows


def _latest_validation(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event") in {"response_contract_validation", "sleep_reaction_validation"}:
            return event
    return None


def _metric_template() -> dict[str, int]:
    return {"total": 0, "direct": 0, "repaired": 0, "fallback": 0, "error": 0}


def run(model: str, output: Path, *, direct_only: bool = False) -> dict[str, Any]:
    settings = get_model_settings()
    endpoint = str(settings["local_endpoint"])
    runtime = LocalModelRuntime(
        ROOT, model_directory=LOCAL_LLM_MODEL_DIR,
        context_size=LOCAL_LLM_CONTEXT_SIZE,
        log=lambda message: print("[runtime] " + str(message), flush=True),
    )
    status = runtime.start(
        endpoint=endpoint, selected_model=model,
        api_key=str(settings.get("local_api_key", "")),
    )
    if status.get("state") != "ready":
        raise RuntimeError("local runtime did not become ready: " + str(status.get("error", "unknown")))
    normalized = re.sub(r"[^a-z0-9]", "", model.casefold())
    qwen = "qwen35" in normalized
    provider = OpenAICompatibleLLM(
        api_key=str(settings.get("local_api_key", "")), base_url=endpoint, model=model,
        sampling_preset="qwen3.5_non_thinking_general" if qwen else "",
        sampling_options=QWEN35_NON_THINKING_GENERAL if qwen else {},
    )
    session = ProductionSession("mutation-real-model-" + normalized)
    started_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    try:
        # Establish cumulative state with the deterministic production fixture,
        # persist it, add a long archive, and restart before real generation.
        session.turn(
            "You're wearing a black dress, a red scarf, and boots.",
            response_envelope("*Adjusts the outfit.*"),
        )
        session.turn("I blindfold you.", response_envelope("*Holds still.*"))
        session.turn(
            "I cover your mouth with my hand.",
            response_envelope("*Their ears flick.* Mmph...", mode="speech_constrained", spoken="Mmph..."),
        )
        session.turn(
            "You're holding a cup in one hand and a book in the other.",
            response_envelope("*Balances both objects.* Mmph...", mode="speech_constrained", spoken="Mmph..."),
        )
        session.append_synthetic_history(180)
        session.restart()

        def attach_real_provider() -> None:
            session.provider = provider
            session.memory.llm = provider
            session.conversation.llm = provider
            session.service.llm = provider
            session.service._response_generator = None
            session.service.character_prompt = build_character_prompt(
                {"name": "Sable"},
                "Sable is a concise, warm, playful synthetic companion. She reacts naturally and characterfully.",
            )

        attach_real_provider()

        recorder = development_flight_recorder()
        previous_fallback = None
        repeated_fallbacks = 0
        longest_fallback_run = 0
        current_fallback_run = 0
        for index, case in enumerate(cases(), 1):
            if index in {55, 105, 155, 205, 255, 305}:
                session.restart()
                attach_real_provider()
            print(f"[{index}/{len(cases())}] {case.bucket}", flush=True)
            actual_model_turn = not direct_only or case.bucket == "direct_current_state_query"
            if not actual_model_turn:
                session.service._response_generator = session.script
                session.script.queue((response_envelope(
                    "*Reacts without asserting additional scene facts.*",
                    mode="nonverbal_reaction", spoken="",
                ),))
                session.service.process_text_turn(case.text, speak=False)
                continue
            session.service._response_generator = None
            event_start = len(recorder._events)
            before_snapshot = session.service.continuity_snapshot()
            result = session.service.process_text_turn(case.text, speak=False)
            after_snapshot = session.service.continuity_snapshot()
            events = list(recorder._events)[event_start:]
            validation = _latest_validation(events)
            outcome = str((validation or {}).get("outcome") or "error")
            if validation and validation.get("event") == "sleep_reaction_validation":
                outcome = "fallback" if validation.get("fallback_used") else "direct"
            if outcome == "accepted":
                outcome = "direct"
            if result.error:
                outcome = "error"
            effects = session.repository.capability_effects(session.character_id)
            parsed_final = parse_assistant_response(result.reply)
            final_capability = validate_capability_response(parsed_final, effects)
            requirement = derive_response_requirement(
                session.repository, session.character_id, case.text,
                local_datetime=session.now,
                companion_effects=effects,
                user_effects=session.repository.capability_effects(
                    session.character_id, target="user",
                ),
            )
            factual = (
                validate_response_requirement(requirement, result.reply).accepted
                if requirement is not None else True
            )
            fallback_key = None
            if outcome == "fallback":
                fallback_key = hashlib.sha256(result.reply.encode("utf-8")).hexdigest()[:16]
                current_fallback_run = current_fallback_run + 1 if fallback_key == previous_fallback else 1
                repeated_fallbacks += int(fallback_key == previous_fallback)
                longest_fallback_run = max(longest_fallback_run, current_fallback_run)
            else:
                current_fallback_run = 0
            previous_fallback = fallback_key
            rows.append({
                "index": index,
                "bucket": case.bucket,
                "outcome": outcome,
                "category": str((validation or {}).get("category") or "none"),
                "contract_admission": str((validation or {}).get("primary_contract_admission") or "none"),
                "primary_parse_failure": str((validation or {}).get("primary_parse_failure") or "none"),
                "primary_semantic_rejection": str(
                    (validation or {}).get("primary_semantic_rejection") or "none"
                ),
                "repair_attempted": bool((validation or {}).get("repair_attempted")),
                "repair_succeeded": bool((validation or {}).get("repair_succeeded")),
                "fallback_category": str((validation or {}).get("fallback_category") or "none"),
                "mutation_applied_before_generation": any(
                    item.get("event") == "mutation_authority_boundary"
                    and item.get("applied_before_generation") is True
                    for item in events
                ),
                "fallback_signature": fallback_key,
                "reply_characters": len(result.reply),
                "state_changed": before_snapshot.get("revision") != after_snapshot.get("revision"),
                "scope": str((after_snapshot.get("scope") or {}).get("kind") or "unknown"),
                "scene_subject_count": len(after_snapshot.get("scene_subjects") or ()),
                "scene_relation_count": len(after_snapshot.get("scene_relations") or ()),
                "capability_effect_count": len(after_snapshot.get("capability_effects") or ()),
                "vision_mode": session.repository.capability_effects(session.character_id).vision_mode,
                "hearing_mode": session.repository.capability_effects(session.character_id).hearing_mode,
                "speech_mode": session.repository.capability_effects(session.character_id).speech_mode,
                "hands_mode": session.repository.capability_effects(session.character_id).hands_mode,
                "locomotion_mode": session.repository.capability_effects(session.character_id).locomotion_mode,
                "published_capability_compliant": final_capability.accepted,
                "published_capability_category": final_capability.category,
                "direct_requirement_present": requirement is not None,
                "direct_requirement_correct": factual,
            })

        metrics: dict[str, dict[str, int]] = defaultdict(_metric_template)
        for row in rows:
            bucket = metrics[row["bucket"]]
            bucket["total"] += 1
            outcome = str(row["outcome"])
            if outcome in bucket:
                bucket[outcome] += 1
            else:
                bucket["error"] += 1
        for bucket in metrics.values():
            bucket["accepted"] = bucket["direct"] + bucket["repaired"]
        total = len(rows)
        fallback = sum(row["outcome"] == "fallback" for row in rows)
        accepted = sum(row["outcome"] in {"direct", "repaired"} for row in rows)
        payload = {
            "version": (
                "active-state-direct-query-production-v1" if direct_only
                else "active-state-adversarial-production-v2"
            ),
            "model": model,
            "runtime_compute": status.get("compute"),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "turns": total,
            "accepted": accepted,
            "fallback": fallback,
            "fallback_ratio": round(fallback / total, 4),
            "repeated_fallbacks": repeated_fallbacks,
            "longest_identical_fallback_run": longest_fallback_run,
            "published_capability_violations": sum(
                not bool(row["published_capability_compliant"]) for row in rows
            ),
            "direct_query_total": sum(
                bool(row["direct_requirement_present"]) for row in rows
            ),
            "direct_query_correct": sum(
                bool(row["direct_requirement_present"]) and bool(row["direct_requirement_correct"])
                for row in rows
            ),
            "state_mutation_turns": sum(row["bucket"].startswith("state_mutation") for row in rows),
            "buckets": dict(metrics),
            "results": rows,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload
    finally:
        session.close()
        if status.get("ownership") == "managed":
            runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--direct-only", action="store_true")
    args = parser.parse_args()
    payload = run(args.model, args.output, direct_only=args.direct_only)
    print(json.dumps({key: payload[key] for key in (
        "model", "turns", "accepted", "fallback", "fallback_ratio",
        "repeated_fallbacks", "longest_identical_fallback_run",
    )}, indent=2))


if __name__ == "__main__":
    main()
