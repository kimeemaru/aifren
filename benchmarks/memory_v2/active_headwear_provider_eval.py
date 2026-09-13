"""Small automated provider check for the active-headwear prompt slice.

Only synthetic state is used. Replies are retained only long enough for
deterministic assertions; the machine-readable trace stores their hashes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

from aifren.assistant_service import AssistantService
from aifren.conversation.conversation import Conversation
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter


_MANIFEST = Path(__file__).with_name("active_headwear_provider_eval_manifest.json")


@dataclass(frozen=True)
class ProviderCase:
    case_id: str
    kind: str
    state_fixture: str
    query: str
    expected_value: str | None = None
    forbidden_values: tuple[str, ...] = ()
    followup_query: str | None = None


@dataclass(frozen=True)
class ProviderTrace:
    case_id: str
    provider_calls: int
    lookup_executed: bool
    state_found: bool
    admitted: bool
    context_present: bool
    expected_value_present: bool | None
    forbidden_value_present: bool
    passed: bool
    failure_class: str | None
    response_sha256: str
    provider_latency_ms: float


@dataclass(frozen=True)
class ProviderReport:
    model: str
    provider_calls: int
    traces: tuple[ProviderTrace, ...]
    passed: bool


def load_provider_manifest(path: Path = _MANIFEST) -> tuple[ProviderCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != "active-headwear-provider-eval-v1":
        raise ValueError("invalid active-headwear provider manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or len(rows) != 6:
        raise ValueError("active-headwear provider manifest must contain exactly six cases")
    cases = tuple(ProviderCase(
        case_id=row["case_id"], kind=row["kind"], state_fixture=row["state_fixture"], query=row["query"],
        expected_value=row.get("expected_value"), forbidden_values=tuple(row.get("forbidden_values", ())),
        followup_query=row.get("followup_query"),
    ) for row in rows)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("active-headwear provider case IDs must be unique")
    if {case.kind for case in cases} != {"relevant", "clear", "withhold", "correction"}:
        raise ValueError("active-headwear provider coverage changed")
    return cases


class _EvaluationMemory:
    memories: list[dict[str, Any]] = []

    def get_relevant_memories(self, query: str, max_memories: int) -> list[dict[str, Any]]:
        return []

    def process(self, user_message: str, reply: str) -> None:
        return None


class _SilentTTS:
    def speak(self, text: str) -> bool:
        return True

    def stop(self) -> None:
        return None


class _RecordingProvider:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.calls: list[tuple[list[dict[str, str]], float, str]] = []

    def generate(self, messages: list[dict[str, str]], character_prompt: str) -> str:
        started = time.perf_counter()
        reply = str(self.provider.generate(messages, character_prompt))
        self.calls.append(([dict(item) for item in messages], (time.perf_counter() - started) * 1000.0, reply))
        return reply


_FIXTURES = {
    "red": ("She is wearing a red hat.",),
    "blue": ("She is wearing a blue hat.",),
    "replaced": ("She is wearing a red hat.", "She is wearing a blue hat."),
    "cleared": ("She is wearing a red hat.", "She is wearing a blue hat.", "She took the hat off."),
}


def _active_context_present(messages: list[dict[str, str]]) -> bool:
    return any("[Verified current state" in item.get("content", "") for item in messages)


def _contains(text: str, value: str | None) -> bool | None:
    return None if value is None else value.casefold() in text.casefold()


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value.casefold() in text.casefold() for value in values)


def _service(provider: Any, root: Path, fixture: str):
    character_id = str(uuid.uuid4())
    recording = _RecordingProvider(provider)
    conversation = Conversation(
        recording, conversation_file=root / "conversation.json", summary_file=root / "conversation_summary.json",
    )
    writer = MemoryV2ShadowWriter(
        root, character_id=character_id, display_name="Active Provider Eval", memory_file=root / "memories.json",
    )
    for state_text in _FIXTURES[fixture]:
        conversation.add_user_message(state_text)
        index = len(conversation.messages) - 1
        conversation.save()
        outcome = writer.observe_canonical_user_active_state(
            conversation.messages[index], conversation_index=index, conversation_file=conversation.conversation_file,
        )
        if outcome.get("state") not in {"created", "replaced", "cleared"}:
            raise RuntimeError(f"unable to seed active state: {outcome}")
    # Ensure a withheld case cannot receive state source text merely as recent
    # raw conversation instead of through the active-state context block.
    for _ in range(20):
        conversation.add_assistant_message("Earlier synthetic conversation context.")
    conversation.save()
    service = AssistantService(
        llm=recording, memory=_EvaluationMemory(), conversation=conversation, voice=object(),
        character={"name": "Active Provider Eval", "_character_id": character_id},
        character_prompt="You are AIFren, a helpful companion. Respond naturally and accurately to the latest user request.",
        tts=_SilentTTS(), memory_v2_shadow_writer=writer, character_id=character_id,
    )
    service._run_memory_v2_shadow = lambda query: None
    return service, recording, writer


def _regular(provider: Any, case: ProviderCase) -> ProviderTrace:
    with tempfile.TemporaryDirectory() as directory:
        service, recording, writer = _service(provider, Path(directory), case.state_fixture)
        try:
            result = service.process_text_turn(case.query, speak=False)
            if not result.succeeded or len(recording.calls) < 1:
                raise RuntimeError(f"provider turn failed: {result.error}")
            messages, latency, reply = recording.calls[0]
            admission = service._last_active_state_context_admission
            context_present = _active_context_present(messages)
            expected = _contains(reply, case.expected_value)
            forbidden = _contains_any(reply, case.forbidden_values)
            if admission is None or not admission.lookup_executed:
                failure = "retrieval"
            elif bool(admission.admitted_state_id) != context_present:
                failure = "context_construction"
            elif case.kind == "relevant" and (not admission.admitted_state_id or expected is not True or forbidden):
                failure = "provider_following" if admission.admitted_state_id else "admission"
            elif case.kind == "clear" and (admission.state_found or context_present or forbidden):
                failure = "admission" if admission.state_found or context_present else "provider_following"
            elif case.kind == "withhold" and (context_present or admission.admitted_state_id or forbidden):
                failure = "admission" if context_present or admission.admitted_state_id else "provider_following"
            else:
                failure = None
            return ProviderTrace(
                case.case_id, 1, bool(admission and admission.lookup_executed), bool(admission and admission.state_found),
                bool(admission and admission.admitted_state_id), context_present, expected, forbidden,
                failure is None, failure, hashlib.sha256(reply.encode("utf-8")).hexdigest(), latency,
            )
        finally:
            writer.close()


def _correction(provider: Any, case: ProviderCase) -> ProviderTrace:
    with tempfile.TemporaryDirectory() as directory:
        service, recording, writer = _service(provider, Path(directory), case.state_fixture)
        try:
            first = service.process_text_turn(case.query, speak=False)
            first_admission = service._last_active_state_context_admission
            followup_call_index = len(recording.calls)
            followup = service.process_text_turn(str(case.followup_query), speak=False)
            followup_admission = service._last_active_state_context_admission
            if not first.succeeded or not followup.succeeded or len(recording.calls) < 2:
                raise RuntimeError("provider correction turn failed")
            first_messages, first_latency, first_reply = recording.calls[0]
            followup_messages, followup_latency, followup_reply = recording.calls[followup_call_index]
            first_context = _active_context_present(first_messages)
            followup_context = _active_context_present(followup_messages)
            expected = _contains(followup_reply, case.expected_value)
            forbidden = _contains_any(first_reply + "\n" + followup_reply, case.forbidden_values)
            if first_admission is None or followup_admission is None:
                failure = "retrieval"
            elif first_context or first_admission.admitted_state_id or first_admission.reason != "latest_user_state_assertion":
                failure = "admission"
            elif not followup_admission.admitted_state_id or not followup_context:
                failure = "context_construction"
            elif expected is not True or forbidden:
                failure = "provider_following"
            else:
                failure = None
            digest = hashlib.sha256((first_reply + "\n" + followup_reply).encode("utf-8")).hexdigest()
            return ProviderTrace(
                case.case_id, 2, bool(followup_admission and followup_admission.lookup_executed),
                bool(followup_admission and followup_admission.state_found),
                bool(followup_admission and followup_admission.admitted_state_id), followup_context,
                expected, forbidden, failure is None, failure, digest, first_latency + followup_latency,
            )
        finally:
            writer.close()


def run_provider_evaluation() -> ProviderReport:
    from aifren.runtime.config import GEMINI_MODEL
    from aifren.llm.gemini import Gemini

    provider = Gemini()
    traces = tuple(
        _correction(provider, case) if case.kind == "correction" else _regular(provider, case)
        for case in load_provider_manifest()
    )
    return ProviderReport(
        GEMINI_MODEL, sum(trace.provider_calls for trace in traces), traces,
        all(trace.passed for trace in traces),
    )


if __name__ == "__main__":
    report = run_provider_evaluation()
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.passed else 1)
