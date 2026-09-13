"""Small, frozen end-to-end provider check for durable ``identity.name``.

This exercises the real AssistantService and configured LLM with synthetic
characters only.  It records assertions and context-stage traces, never raw
provider replies, so the result is machine-readable without becoming a broad
LLM evaluation framework.
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


_MANIFEST = Path(__file__).with_name("identity_name_provider_eval_manifest.json")


@dataclass(frozen=True)
class ProviderCase:
    case_id: str
    kind: str
    query: str
    expected_name: str | None = None
    forbidden_name: str | None = None
    followup_query: str | None = None


@dataclass(frozen=True)
class ProviderTrace:
    case_id: str
    provider_calls: int
    lookup_executed: bool
    admitted: bool
    context_present: bool
    seed_name_visible_in_recent_context: bool
    response_contains_expected_name: bool | None
    response_contains_forbidden_name: bool | None
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
    if not isinstance(raw, dict) or raw.get("version") != "identity-name-provider-eval-v1":
        raise ValueError("invalid identity-name provider manifest version")
    rows = raw.get("cases")
    if not isinstance(rows, list) or len(rows) != 8:
        raise ValueError("identity-name provider manifest must contain exactly eight cases")
    cases = tuple(ProviderCase(**row) for row in rows)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("identity-name provider case IDs must be unique")
    expected = {"relevant": 3, "withhold": 3, "hypothetical": 1, "correction": 1}
    counts = {kind: sum(case.kind == kind for case in cases) for kind in expected}
    if counts != expected:
        raise ValueError("identity-name provider coverage changed")
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
    """Delegate to the real provider while retaining only test-safe traces."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.calls: list[tuple[list[dict[str, str]], float, str]] = []

    def generate(self, messages: list[dict[str, str]], character_prompt: str) -> str:
        started = time.perf_counter()
        reply = self.provider.generate(messages, character_prompt)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.calls.append(([dict(item) for item in messages], elapsed, str(reply)))
        return str(reply)


def _contains(text: str, value: str | None) -> bool | None:
    return None if value is None else value.casefold() in text.casefold()


def _durable_context_present(messages: list[dict[str, str]]) -> bool:
    return any("[Verified remembered facts" in message.get("content", "") for message in messages)


def _seed_verified_name(conversation: Conversation, writer: MemoryV2ShadowWriter) -> None:
    conversation.add_user_message("My name is Elena.")
    index = len(conversation.messages) - 1
    message = conversation.messages[index]
    conversation.save()
    result = writer.observe_canonical_user_message(
        message, conversation_index=index, conversation_file=conversation.conversation_file,
    )
    if result.get("state") != "created":
        raise RuntimeError(f"unable to seed verified name: {result}")
    # Retain canonical evidence, but move it outside Conversation's bounded
    # recent-context window.  Otherwise a withholding control would still
    # expose Elena as ordinary raw conversation rather than durable context.
    for _ in range(20):
        conversation.add_assistant_message("Earlier synthetic conversation context.")
    conversation.save()


def _service(provider: Any, root: Path) -> tuple[AssistantService, _RecordingProvider, MemoryV2ShadowWriter]:
    character_id = str(uuid.uuid4())
    recording_provider = _RecordingProvider(provider)
    conversation = Conversation(
        recording_provider,
        conversation_file=root / "conversation.json",
        summary_file=root / "conversation_summary.json",
    )
    writer = MemoryV2ShadowWriter(
        root, character_id=character_id, display_name="Provider Eval", memory_file=root / "memories.json",
    )
    _seed_verified_name(conversation, writer)
    service = AssistantService(
        llm=recording_provider,
        memory=_EvaluationMemory(),
        conversation=conversation,
        voice=object(),
        character={"name": "Provider Eval", "_character_id": character_id},
        character_prompt="You are AIFren, a helpful companion. Respond naturally and accurately to the latest user request.",
        tts=_SilentTTS(),
        memory_v2_shadow_writer=writer,
        character_id=character_id,
    )
    # This evaluation isolates the approved durable prompt vertical slice;
    # unrelated V2 shadow comparison is intentionally not part of provider cost.
    service._run_memory_v2_shadow = lambda query: None
    return service, recording_provider, writer


def _run_regular_case(provider: Any, case: ProviderCase) -> ProviderTrace:
    with tempfile.TemporaryDirectory() as temporary:
        service, recording, writer = _service(provider, Path(temporary))
        try:
            result = service.process_text_turn(case.query, speak=False)
            if not result.succeeded or len(recording.calls) != 1:
                raise RuntimeError(f"provider turn failed: {result.error}")
            messages, latency, response = recording.calls[0]
            admission = service._last_durable_context_admission
            context_present = _durable_context_present(messages)
            seed_visible = any("My name is Elena." in message.get("content", "") for message in messages)
            expected = _contains(response, case.expected_name)
            forbidden = _contains(response, case.forbidden_name)
            if seed_visible:
                failure = "context_construction"
            elif admission is None or not admission.lookup_executed:
                failure = "retrieval"
            elif case.kind == "relevant" and not admission.admitted_claim_id:
                failure = "admission"
            elif bool(admission.admitted_claim_id) != context_present:
                failure = "context_construction"
            elif case.kind == "relevant" and expected is not True:
                failure = "provider_following"
            elif case.kind != "relevant" and (context_present or forbidden is True):
                failure = "admission" if context_present else "provider_following"
            else:
                failure = None
            return ProviderTrace(
                case.case_id, 1, bool(admission and admission.lookup_executed),
                bool(admission and admission.admitted_claim_id), context_present, seed_visible, expected, forbidden,
                failure is None, failure, hashlib.sha256(response.encode("utf-8")).hexdigest(), latency,
            )
        finally:
            writer.close()


def _run_correction_case(provider: Any, case: ProviderCase) -> ProviderTrace:
    with tempfile.TemporaryDirectory() as temporary:
        service, recording, writer = _service(provider, Path(temporary))
        try:
            correction = service.process_text_turn(case.query, speak=False)
            correction_admission = service._last_durable_context_admission
            followup = service.process_text_turn(str(case.followup_query), speak=False)
            followup_admission = service._last_durable_context_admission
            if not correction.succeeded or not followup.succeeded or len(recording.calls) != 2:
                raise RuntimeError("provider correction turn failed")
            correction_messages, correction_latency, correction_reply = recording.calls[0]
            followup_messages, followup_latency, followup_reply = recording.calls[1]
            stale_in_correction = _contains(correction_reply, case.forbidden_name)
            expected_followup = _contains(followup_reply, case.expected_name)
            stale_followup = _contains(followup_reply, case.forbidden_name)
            correction_context = _durable_context_present(correction_messages)
            followup_context = _durable_context_present(followup_messages)
            seed_visible = any(
                "My name is Elena." in message.get("content", "")
                for message in correction_messages + followup_messages
            )
            if seed_visible:
                failure = "context_construction"
            elif correction_admission is None or followup_admission is None:
                failure = "retrieval"
            elif correction_context or correction_admission.admitted_claim_id:
                failure = "admission"
            elif not followup_admission.admitted_claim_id or not followup_context:
                failure = "context_construction"
            elif stale_in_correction is True or stale_followup is True or expected_followup is not True:
                failure = "provider_following"
            else:
                failure = None
            digest = hashlib.sha256((correction_reply + "\n" + followup_reply).encode("utf-8")).hexdigest()
            return ProviderTrace(
                case.case_id, 2, bool(followup_admission.lookup_executed),
                bool(followup_admission.admitted_claim_id), followup_context, seed_visible,
                expected_followup, stale_in_correction is True or stale_followup is True,
                failure is None, failure, digest, correction_latency + followup_latency,
            )
        finally:
            writer.close()


def run_provider_evaluation() -> ProviderReport:
    from aifren.runtime.config import GEMINI_MODEL
    from aifren.llm.gemini import Gemini

    provider = Gemini()
    traces = tuple(
        _run_correction_case(provider, case) if case.kind == "correction" else _run_regular_case(provider, case)
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
