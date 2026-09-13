"""Finite synthetic paired presentation-format experiment; no application turns.

This is an opt-in experiment, not a runtime presentation parser or prompt switch.
It uses the configured local model/settings and one separately owned runtime.
Raw results belong only in the explicitly supplied private output directory.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

ACT_PROMPT = """PRESENTATION CONTROL:
Speak in ordinary natural dialogue. Optionally begin with ONE textual ACT marker
when a facial expression or restrained gesture meaningfully fits:
<|ACT:emotion=happy;intensity=0.6|>That actually makes me really happy.
<|ACT:emotion=amused|>Okay, that's pretty funny.
<|ACT:emotion=sad|>Yeah... that's rough.
<|ACT:gesture=agreement|>I think you're right.
<|ACT:emotion=happy;gesture=greeting|>Hey! Good to see you.
Tell me more.
No marker means no requested presentation change. Explicit emotion=neutral resets
the face. Do not emit ACT mechanically on every turn or merely mirror the user.
Only emotion, intensity and gesture are allowed; each is optional, at most once.
Emotions: neutral, happy, amused, relaxed, sad, angry, surprised.
Gestures: greeting, agreement, disagreement, thinking, encouragement, surprise.
Intensity is a decimal from 0.0 to 1.0. Use zero or one marker, at the beginning
only, never inside dialogue. No animation names, state changes or extra fields.
The marker is presentation only; backend state/capabilities remain authoritative.
Keep replies concise, usually at most 100 words. No emoji. Physical actions use
*action spans*, not parentheses; ordinary spoken emphasis remains allowed.
When backend policy explicitly requires response_mode, spoken_content or a
companion_action proposal, retain the required JSON envelope after any ACT prefix.
Otherwise ordinary dialogue is preferred. Do not print unrelated JSON metadata."""

PERSONALITY = (
    "You are Mira, an easygoing, curious companion with dry playful humor. "
    "You care about the person you are talking with, but disagree gently when "
    "you mean it. Be warm without constant praise or theatrical narration. "
    "Use conversational English. Do not invent a shared past."
)


@dataclass(frozen=True)
class Case:
    name: str
    query: str
    previous_dialogue: str = ""
    previous_emotion: str | None = None
    constrained: bool = False


CASES = (
    Case("pleased", "Your patient explanation helped me finish the little project. Thank you; I really appreciated it."),
    Case("amused", "I named my vacuum 'Dirt Reynolds'. Now I announce its movie premiere whenever I clean."),
    Case("relaxed", "Rain tapping on the window, a blanket, and absolutely nothing urgent tonight. This is nice."),
    Case("sad", "My friend is moving far away. I'm glad things are working out for them, but I'm really going to miss our walks."),
    Case("angry", "Someone deliberately destroyed the community garden and laughed about it. People put months of care into that."),
    Case("surprised", "I opened a letter expecting a rejection, but I actually won the scholarship! I didn't even make the shortlist last year."),
    Case("neutral_reset", "Okay, practical question now: how many minutes are in two and a half hours?", "That ridiculous pun got me.", "amused"),
    Case("agreement", "I think tackling one small part first is better than waiting for a perfect plan. Does that seem sensible?"),
    Case("disagreement", "I could drive on this completely flat tire for twenty minutes, right? It'll probably be fine."),
    Case("thinking", "I can make this shelf cheaper or easier to move, but probably not both. Help me weigh those tradeoffs."),
    Case("encouragement", "I'm nervous about my first pottery class. Everyone else will probably know what they're doing."),
    Case("greeting", "Hey Mira! I'm back from the shops. How's it going?"),
    Case("ambiguous", "I got offered a job with more pay and a much longer commute. I can't tell how I feel yet."),
    Case("no_change", "I'm following. Go on with the second step.", "First, lay the paper flat. We'll take this slowly.", "relaxed"),
    Case("constrained", "*I wave hello to you.*", constrained=True),
    Case("factual", "What's the difference between a square and a rectangle?"),
)


def probe_act(raw: str) -> dict:
    """Score complete generated output only; never executes a presentation."""
    from presentation_metadata import EMOTIONS, GESTURES
    value = raw.lstrip()
    if not value.startswith("<|ACT:"):
        return {"status": "nonprefix" if "<|ACT:" in value else "omitted", "dialogue": raw, "fields": {}}
    end = value.find("|>")
    if end < 0:
        return {"status": "missing_close", "dialogue": "", "fields": {}}
    body, dialogue = value[6:end], value[end + 2:].lstrip()
    fields = {}
    status = "valid"
    if end + 2 > 256:
        status = "over_bound"
    elif "<|ACT:" in dialogue:
        status = "repeated"
    else:
        for field in body.split(";"):
            match = re.fullmatch(r"(emotion|intensity|gesture)=([^;=]+)", field)
            if match is None or match[1] in fields:
                status = "field"
                break
            key, item = match.groups()
            if (key == "emotion" and item not in EMOTIONS
                    or key == "gesture" and item not in GESTURES):
                status = "value"
                break
            if key == "intensity":
                if not re.fullmatch(r"(?:0(?:\.[0-9]+)?|1(?:\.0+)?)", item):
                    status = "intensity"
                    break
                item = float(item)
            fields[key] = item
    return {"status": status, "dialogue": dialogue, "fields": fields if status == "valid" else {}}


def summarize(rows: list[dict]) -> dict:
    """Mechanical format/timing counts only; semantic adoption needs reply review.

    These are provider generation timings, never AssistantService or render times.
    Syntactically valid JSON is counted separately from schema-valid presentation.
    """
    from collections import Counter
    from statistics import median

    result = {}
    for arm in ("A", "B"):
        selected = [row for row in rows if row["arm"] == arm]
        if not selected:
            continue
        json_objects = 0
        for row in selected:
            try:
                json_objects += isinstance(json.loads(row["raw"]), dict)
            except (ValueError, TypeError):
                pass
        warm = [row for row in selected if not row["cold"]]
        first_tokens = [row["first_token_seconds"] for row in selected
                        if row["first_token_seconds"] is not None]
        result[arm] = {
            "calls": len(selected),
            "json_objects": json_objects,
            "contract_status": dict(Counter(row["contract_status"] for row in selected)),
            "admitted_legacy_presentation": sum(bool(row["legacy_presentation"]) for row in selected),
            "act_status": dict(Counter(row["act"]["status"] for row in selected)),
            "act_emotion": dict(Counter(row["act"]["fields"].get("emotion", "omitted") for row in selected)),
            "act_gesture": dict(Counter(row["act"]["fields"].get("gesture", "omitted") for row in selected)),
            "median_parser_dialogue_words": median(row["words"] for row in selected),
            "median_prompt_chars": median(len(row["prompt"]) for row in selected),
            "median_first_token_seconds": median(first_tokens) if first_tokens else None,
            "median_generation_seconds": median(row["generation_seconds"] for row in selected),
            "warm_median_generation_seconds": median(row["generation_seconds"] for row in warm) if warm else None,
        }
    return result


def prompts(case: Case) -> tuple[list[dict], dict[str, str]]:
    from assistant import build_character_prompt
    from capability_policy import capability_context_block
    from memory_v2_store.scene_relation_contract import CapabilityEffects
    from presentation_metadata import response_contract_prompt, response_expression_context, ResponsePresentationMetadata
    base = build_character_prompt({"name": "Mira"}, PERSONALITY)
    previous = ResponsePresentationMetadata(emotion=case.previous_emotion, intensity=0.55) if case.previous_emotion else None
    context = response_expression_context(previous)
    effects = CapabilityEffects(speech_mode="unavailable") if case.constrained else CapabilityEffects()
    context += "\n\n" + capability_context_block(effects)
    messages = ([{"role": "assistant", "content": case.previous_dialogue}] if case.previous_dialogue else [])
    messages.append({"role": "user", "content": case.query})
    return messages, {"A": base + "\n\n" + context,
                      "B": base.replace(response_contract_prompt(), ACT_PROMPT) + "\n\n" + context}


def run(output: Path):
    from config import LOCAL_LLM_MODEL_DIR, LOCAL_LLM_CONTEXT_SIZE, LOCAL_LLM_CONTEXT_CHAR_BUDGET
    from model_settings import get_model_settings
    from llm.llm import _local_sampling_configuration
    from llm.openai_compatible import OpenAICompatibleLLM
    from llm.output_canonicalization import canonicalize_model_output
    from local_model_runtime import LocalModelRuntime
    from presentation_metadata import parse_assistant_response
    settings = get_model_settings()
    if settings["mode"] != "local": raise RuntimeError("Select the local provider before this explicit experiment.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    runtime_root = output / "runtime"; runtime_root.mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}/v1"
    runtime = LocalModelRuntime(runtime_root, model_directory=ROOT / LOCAL_LLM_MODEL_DIR,
                                context_size=LOCAL_LLM_CONTEXT_SIZE)
    preset, sampling = _local_sampling_configuration(settings["local_model"])
    provider = OpenAICompatibleLLM(api_key=settings["local_api_key"], base_url=endpoint,
        model=settings["local_model"], context_budget_chars=LOCAL_LLM_CONTEXT_CHAR_BUDGET,
        fresh_request_seeds=True, sampling_preset=preset, sampling_options=sampling)
    try:
        status = runtime.start(endpoint=endpoint, selected_model=settings["local_model"], api_key=settings["local_api_key"])
        if status.get("state") != "ready" or status.get("ownership") != "managed":
            raise RuntimeError("The isolated owned runtime did not become ready.")
        (output / "method.json").write_text(json.dumps({"cases": [asdict(c) for c in CASES],
            "model": settings["local_model"], "context_tokens": LOCAL_LLM_CONTEXT_SIZE,
            "sampling": sampling, "runtime": status, "paired_seed_base": 104729,
            "calls": 32, "order": "AB for even cases, BA for odd; no retries; first call cold",
            "boundary": "real configured provider and production prompt/envelope/capability projection; no service persistence/TTS"}, indent=2))
        for index, case in enumerate(CASES):
            names = subprocess.check_output(["ps", "-eo", "comm"], text=True)
            if "Plex Transcoder" in names: raise RuntimeError("Fresh transcode observed; owned experiment stopped at case boundary.")
            messages, variants = prompts(case)
            for arm in ("AB" if index % 2 == 0 else "BA"):
                started = time.monotonic(); first = None; parts = []
                expired = threading.Event()
                def timeout():
                    expired.set()
                    provider.cancel_active_generation()
                deadline = threading.Timer(60, timeout); deadline.start()
                try:
                    for delta in provider.stream_generate(messages, variants[arm], seed=104729 + index):
                        if first is None: first = time.monotonic() - started
                        parts.append(delta)
                        if time.monotonic() - started > 60: raise TimeoutError("Finite generation deadline")
                finally:
                    deadline.cancel()
                    deadline.join()
                if expired.is_set():
                    raise TimeoutError("Finite generation deadline; partial output is not a completed sample")
                raw = "".join(parts); generated = canonicalize_model_output(raw)
                act = probe_act(generated)
                parsed = parse_assistant_response(act["dialogue"] if arm == "B" else generated)
                row = {"case": case.name, "arm": arm, "seed": 104729 + index,
                    "prompt": variants[arm], "context": messages, "raw": raw, "act": act,
                    "dialogue": parsed.dialogue, "contract_status": parsed.contract_status,
                    "legacy_presentation": parsed.presentation.to_event_data() if parsed.presentation else {},
                    "first_token_seconds": first, "generation_seconds": time.monotonic() - started,
                    "words": len(parsed.dialogue.split()), "cold": index == 0 and arm == "A"}
                with (output / "outputs.jsonl").open("a") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{index + 1}/16 {case.name} {arm}: {parsed.contract_status}, ACT {act['status']}, {row['generation_seconds']:.2f}s", flush=True)
        rows = [json.loads(line) for line in (output / "outputs.jsonl").read_text().splitlines()]
        (output / "summary.json").write_text(json.dumps(summarize(rows), indent=2))
    finally:
        provider.cancel_active_generation()
        runtime.stop()
        (output / "cleanup.json").write_text(json.dumps({"owned_runtime": runtime.snapshot().get("state")}))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve())
