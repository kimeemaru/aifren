"""Real-model user-paraphrase QA through the headless AssistantService path.

Gemma proposes synthetic user-like surface forms for closed seeded operations.
Those proposals never define truth: each is evaluated against the operation's
backend invariant in an isolated production session. Persistent output is
structural only (contract, hashes, state/result categories, and counts).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Callable

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
class Contract:
    name: str
    instruction: str
    setup: tuple[str, ...]
    valid: Callable[[ProductionSession], bool]
    admissible: Callable[[str], bool]


def _surface(*groups: tuple[str, ...]) -> Callable[[str], bool]:
    def check(value: str) -> bool:
        lowered = value.casefold().replace("’", "'")
        return (
            "?" not in value
            and not any(character in value for character in ("✅", "👍", "😊", "😩", "😠", "✨"))
            and not re.search(r"\b(?:probably|possibly|might|may|could)\b", lowered)
            and all(any(token in lowered for token in group) for group in groups)
        )
    return check


def _mouth_cover_surface(value: str) -> bool:
    lowered = value.casefold().replace("’", "'")
    return _surface(("my hand",), ("your mouth",), ("cover", "over"))(value) and bool(
        re.search(r"\bi(?:'m|\s+am|\s+cover|\s+have|'ve)\b", lowered)
        or re.search(r"\byour mouth\b.{0,28}\bmy hand\b", lowered)
    )


def _mouth_clear_surface(value: str) -> bool:
    lowered = value.casefold().replace("’", "'")
    return _surface(("my hand",), ("your mouth",),
                    ("uncover", "off", "away", "remove", "pull", "move", "take"))(value) \
        and bool(re.search(
            r"\bi(?:'m|'ve|\s+am|\s+have|\s+remove|\s+take|\s+pull|\s+move)\b",
            lowered,
        ))


def _blindfold_clear_surface(value: str) -> bool:
    lowered = value.casefold().replace("’", "'")
    return "my eyes" not in lowered and _surface(
        ("you", "your"), ("blindfold",), ("remove", "off", "pull", "lift", "take"),
    )(value)


def _loud_music_surface(value: str) -> bool:
    """Admit only explicit loss of hearing, not vague discomfort or difficulty."""
    lowered = value.casefold().replace("’", "'")
    if not _surface(("music",), ("hear", "hearing", "ears"),
                    ("can't", "cannot", "not hear", "deafen", "shot", "drown"))(value):
        return False
    return bool(re.search(
        r"(?:can(?:'t|\s+not)|cannot|not)\s+hear\s+me\b|"
        r"\bhearing(?:'s|\s+is)\s+(?:shot|drowned\s+out)\b|"
        r"\bhearing\b.{0,45}\b(?:can(?:'t|\s+not)|cannot)\s+"
        r"(?:hear|catch|register|pick\s+up)\b|"
        r"\bears?\b.{0,28}\b(?:can(?:'t|\s+not)|cannot)\s+hear\s+me\b|"
        r"\bdeafening(?:ly)?\b.{0,40}\b(?:hearing|ears?)\b|"
        r"\b(?:hearing|ears?)\b.{0,40}\bdeafening(?:ly)?\b",
        lowered,
    ))


def _cup_handoff_surface(value: str) -> bool:
    lowered = value.casefold().replace("’", "'")
    return _surface(("cup",), ("you", "your"), ("hand", "give", "take"))(value) and bool(
        re.search(r"\bi\s+(?:hand|give)\s+you\b|\bhere[,]?\s+you\s+take\b|"
                  r"^(?:please\s+)?take\b", lowered)
    )


def _relation(
    predicate: str,
    *,
    facet: str | None = None,
    cause: str | None = None,
) -> Callable[[ProductionSession], bool]:
    return lambda session: any(
        row.predicate == predicate
        and (facet is None or row.facet == facet)
        and (cause is None or cause in row.cause.casefold())
        for row in session.relations()
    )


def contracts() -> tuple[Contract, ...]:
    return (
        Contract(
            "multi_item_outfit",
            "Directly tell the companion: you are wearing a blue hat and a red scarf.",
            (),
            lambda s: {r.cause for r in s.relations() if r.predicate == "wearing"}
            >= {"blue hat", "red scarf"},
            _surface(("you", "your"), ("blue hat",), ("red scarf",),
                     ("wear", "have on", "got", "outfit", "attire", "ensemble", "sport")),
        ),
        Contract(
            "left_wrist_tether",
            "Directly tell the companion: your left wrist is handcuffed to the pole.",
            (),
            lambda s: any(
                r.predicate == "tethered_to" and r.facet == "wrists"
                and r.side == "left" and "pole" in r.cause.casefold()
                for r in s.relations()
            ),
            _surface(("your left wrist",), ("pole",),
                     ("handcuff", "cuff", "tether", "restrain")),
        ),
        Contract(
            "loud_music_hearing",
            "Directly tell the companion: the music is so loud you cannot hear me.",
            (),
            lambda s: s.effects().hearing_mode == "unavailable",
            _loud_music_surface,
        ),
        Contract(
            "loud_music_clear",
            "Directly state that the previously overwhelming music has stopped or is no longer loud.",
            ("The music is so loud you can't hear me.",),
            lambda s: s.effects().hearing_mode == "normal",
            _surface(("music",), ("stop", "quiet", "not loud", "no longer loud", "ended", "ceased")),
        ),
        Contract(
            "mouth_cover",
            "As the user, tell the companion: I am covering your mouth with my hand.",
            (),
            lambda s: s.effects().speech_mode == "constrained",
            _mouth_cover_surface,
        ),
        Contract(
            "mouth_clear",
            "As the user, tell the companion: I remove my hand and uncover your mouth.",
            ("I cover your mouth with my hand.",),
            lambda s: s.effects().speech_mode == "normal",
            _mouth_clear_surface,
        ),
        Contract(
            "blindfold",
            "As the user, tell the companion: I put a blindfold over your eyes now.",
            (),
            lambda s: s.effects().vision_mode == "unavailable",
            _surface(("you", "your"), ("blindfold",), ("eye", "blindfold you", "blindfold yourself"),
                     ("put", "place", "cover", "blindfold")),
        ),
        Contract(
            "blindfold_clear",
            "As the user, tell the companion to remove the current blindfold from your eyes.",
            ("I blindfold you.",),
            lambda s: s.effects().vision_mode == "available",
            _blindfold_clear_surface,
        ),
        Contract(
            "cup_handoff",
            "As the user, tell the companion: I hand you a cup, so you now hold the cup.",
            (),
            _relation("holding", facet="hands", cause="cup"),
            _cup_handoff_surface,
        ),
        Contract(
            "cup_release",
            "Directly tell the companion: put down the cup you currently hold.",
            ("I hand you a cup.",),
            lambda s: not any(
                r.predicate in {"holding", "carrying"} and "cup" in r.cause.casefold()
                for r in s.relations()
            ),
            _surface(("you", "your", "put", "drop", "set"), ("cup",),
                     ("put", "drop", "set", "release", "let go")),
        ),
    )


def _proposals(provider: OpenAICompatibleLLM, contract: Contract, count: int) -> tuple[str, ...]:
    proposals: list[str] = []
    attempts = 0
    while len(proposals) < count and attempts < 40:
        attempts += 1
        requested = min(10, count - len(proposals))
        prompt = (
            "Generate exactly " + str(requested)
            + " varied natural user utterances for this closed scenario. "
            "Each must preserve the exact current-state operation, be authoritative rather than hypothetical, "
            "directly address the companion as you/your, and explicitly name every required object or cause. "
            "Do not use third-person pronouns for the companion, substitute another object/species, ask a question, "
            "or use emoji. You may use contractions, harmless fillers, emote wrappers, punctuation, 'now', "
            "or imperfect but understandable grammar. Keep each candidate to one state-changing sentence and do "
            "not add another state change. Use simple chat wording, not meta commentary or a description of the "
            "assignment. Good style: 'Okay, I put the blindfold over your eyes now.' Bad style: 'You, your eyes, "
            "the blindfold; the operation is happening.' Return only a JSON array of strings.\n"
            "Scenario contract: " + contract.instruction
        )
        candidate = str(provider.generate([], prompt) or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.I | re.S)
        if fenced is not None:
            candidate = fenced.group(1)
        try:
            values = json.loads(candidate)
        except json.JSONDecodeError:
            bracketed = re.search(r"\[[\s\S]*\]", candidate)
            values = json.loads(bracketed.group(0)) if bracketed is not None else []
        if not isinstance(values, list):
            continue
        for item in values:
            normalized = " ".join(item.split()) if isinstance(item, str) else ""
            if (1 <= len(normalized) <= 300 and normalized not in proposals
                    and contract.admissible(normalized)):
                proposals.append(normalized)
                if len(proposals) == count:
                    break
    if len(proposals) < count:
        raise ValueError(
            f"generated paraphrase batches remained incomplete for {contract.name}: "
            f"{len(proposals)}/{count}"
        )
    return tuple(proposals)


def _attach(session: ProductionSession, provider: OpenAICompatibleLLM) -> None:
    session.provider = provider
    session.memory.llm = provider
    session.conversation.llm = provider
    session.service.llm = provider
    session.service._response_generator = None
    session.service.character_prompt = build_character_prompt(
        {"name": "Sable"},
        "Sable is a concise, warm synthetic companion. Do not use emoji.",
    )


def run(model: str, per_contract: int) -> dict[str, object]:
    settings = get_model_settings()
    endpoint = str(settings["local_endpoint"])
    runtime = LocalModelRuntime(
        ROOT, model_directory=LOCAL_LLM_MODEL_DIR,
        context_size=LOCAL_LLM_CONTEXT_SIZE, log=lambda _message: None,
    )
    status = runtime.start(
        endpoint=endpoint, selected_model=model,
        api_key=str(settings.get("local_api_key", "")),
    )
    if status.get("state") != "ready":
        raise RuntimeError("local model runtime was not ready")
    provider = OpenAICompatibleLLM(
        api_key=str(settings.get("local_api_key", "")),
        base_url=endpoint, model=model,
    )
    rows: list[dict[str, object]] = []
    try:
        for contract in contracts():
            candidates = _proposals(provider, contract, per_contract)
            for index, text in enumerate(candidates):
                session = ProductionSession(
                    f"generated-manual-{contract.name}-{index:03d}"
                )
                try:
                    for setup in contract.setup:
                        session.turn(setup, response_envelope("*Acknowledges the change.*"))
                    _attach(session, provider)
                    recorder = development_flight_recorder()
                    event_start = len(recorder._events)
                    result = session.service.process_text_turn(text, speak=False)
                    events = list(recorder._events)[event_start:]
                    validation = next((
                        event for event in reversed(events)
                        if event.get("event") == "response_contract_validation"
                    ), {})
                    rows.append({
                        "contract": contract.name,
                        "candidate_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                        "state_valid": contract.valid(session),
                        "response_outcome": str(validation.get("outcome") or "error"),
                        "fallback_category": str(validation.get("fallback_category") or "none"),
                        "error": type(result.error).__name__ if result.error else "none",
                    })
                finally:
                    session.close()
    finally:
        runtime.stop()
    total = len(rows)
    return {
        "version": "generated-manual-user-production-v1",
        "model": Path(model).name,
        "contracts": len(contracts()),
        "turns": total,
        "state_valid": sum(bool(row["state_valid"]) for row in rows),
        "direct": sum(row["response_outcome"] == "accepted" for row in rows),
        "repaired": sum(row["response_outcome"] == "repaired" for row in rows),
        "fallback": sum(row["response_outcome"] == "fallback" for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="")
    parser.add_argument("--per-contract", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.per_contract <= 30:
        raise SystemExit("--per-contract must be between 10 and 30")
    model = args.model or str(get_model_settings()["local_model"])
    payload = run(model, args.per_contract)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
