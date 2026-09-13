"""Headless production-path Active State session driver.

Unlike the storage simulator, this fixture owns a real AssistantService,
canonical Conversation, MemoryV2ShadowWriter, serialized backend event stream,
frontend lifecycle reducer, and TTS callback boundary. It contains no world
model and uses synthetic character/evidence data only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterable
import uuid

from aifren.assistant_service import AssistantService, TurnResult
from aifren.backend_host import AIFrenWebSocketHost
from aifren.conversation.conversation import Conversation
from aifren.runtime.development_flight_recorder import development_flight_recorder
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository


BASE_TIME = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)


def response_envelope(
    dialogue: str,
    *,
    mode: str = "normal_conversation",
    spoken: str | None = None,
    presentation: dict[str, object] | None = None,
) -> str:
    return json.dumps({
        "dialogue": dialogue,
        "response_mode": mode,
        "spoken_content": spoken,
        "presentation": presentation,
        "companion_action": None,
        "capability_compliance": [],
    }, separators=(",", ":"))


class _Memory:
    def __init__(self) -> None:
        self.memories: list[object] = []
        self.llm = None

    def get_relevant_memories(self, _query, max_memories=5):
        return []

    def process(self, _user, _assistant):
        return None

    def save(self):
        return None


class _Provider:
    is_available = True

    def __init__(self) -> None:
        self.proactive_draft = "A brief synthetic follow-up?"
        self.proactive_error: Exception | None = None
        self._bounded_responses: list[str] = []

    def queue_bounded(self, responses: Iterable[str]) -> None:
        self._bounded_responses.extend(str(item) for item in responses)

    def generate(self, _context, _prompt):
        if self.proactive_error is not None:
            raise self.proactive_error
        return self.proactive_draft

    def generate_bounded(self, _context, _prompt, max_output_tokens=0):
        if self._bounded_responses:
            return self._bounded_responses.pop(0)
        return "{}"


class _ResponseScript:
    def __init__(self) -> None:
        self._responses: list[str] = []

    def queue(self, responses: Iterable[str]) -> None:
        self._responses.extend(str(item) for item in responses)

    def __call__(self, _llm, _conversation, _memory, _user, _prompt):
        if self._responses:
            return self._responses.pop(0)
        return response_envelope("A synthetic neutral response.")


class SyntheticLifecycleTts:
    """Production callback surface without audio hardware or private voices."""

    synthesis_strategy = "whole_response"

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self._started = None
        self._finished = None
        self._playback_id = 0
        self._pending_id = 0

    def set_playback_started_callback(self, callback):
        self._started = callback

    def set_playback_finished_callback(self, callback):
        self._finished = callback

    def speak(self, text):
        self.spoken.append(str(text))
        self._playback_id += 1
        self._pending_id = self._playback_id
        if self._started is not None:
            self._started(.05, [], [], self._pending_id)
        return True

    def complete(self):
        pending = self._pending_id
        self._pending_id = 0
        if pending and self._finished is not None:
            self._finished(pending)

    def stop(self):
        pending = self._pending_id
        self._pending_id = 0
        return pending

    def set_volume(self, _value):
        return None


@dataclass
class UnityEquivalentState:
    status: str = "ready"
    dialogue: str = ""
    active_turn_id: int = 0
    active_origin: str = ""
    announced_turns: int = 0
    terminal_turns: int = 0
    proactive_messages: int = 0
    tts_state: str = "stopped"


class UnityEquivalentReducer:
    """Small lifecycle oracle paired with C# reducer/EditMode fixtures."""

    def __init__(self) -> None:
        self.state = UnityEquivalentState()
        self._announced: set[int] = set()
        self._terminal: set[int] = set()

    def consume(self, artifact: dict[str, object]) -> None:
        event = artifact.get("event", {}) if isinstance(artifact, dict) else {}
        if not isinstance(event, dict):
            return
        kind = str(event.get("type", ""))
        data = event.get("data", {})
        data = data if isinstance(data, dict) else {}
        turn_id = int(data.get("turn_id", 0) or 0)
        origin = str(data.get("generation_origin", "") or "")
        proactive = bool(data.get("proactive")) or origin == "proactive"
        if kind == "turn_started":
            if turn_id:
                self._announced.add(turn_id)
            self.state.active_turn_id = turn_id
            self.state.active_origin = "proactive" if proactive else (origin or "user")
            # Background publication must not manufacture an ordinary
            # pre-response Thinking phase.
            if not proactive:
                self.state.status = "thinking"
        elif kind == "status":
            self.state.status = str(data.get("state", self.state.status))
        elif kind == "assistant_response":
            self.state.dialogue = str(data.get("content", ""))
            if turn_id:
                self._terminal.add(turn_id)
            if proactive:
                self.state.proactive_messages += 1
        elif kind == "turn_cancelled":
            if turn_id:
                self._terminal.add(turn_id)
            self.state.status = "ready"
        elif kind == "error":
            if turn_id:
                self._terminal.add(turn_id)
            self.state.status = "error"
        elif kind == "tts_state":
            self.state.tts_state = str(data.get("state", self.state.tts_state))
        self.state.announced_turns = len(self._announced)
        self.state.terminal_turns = len(self._terminal)

    @property
    def phantom_turns(self) -> int:
        return len(self._announced - self._terminal)


class ProductionSession:
    """Drive natural turns through the real application service boundary."""

    def __init__(
        self, case_id: str = "production-session", *,
        tts: SyntheticLifecycleTts | None = None,
    ) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="aifren-production-session-")
        self.root = Path(self._temp.name)
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.conversation_file.write_text("[]", encoding="utf-8")
        self.summary_file = self.root / "conversation_summary.json"
        self.character_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, "aifren:production-session:" + case_id,
        ))
        self.now = BASE_TIME
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id,
            display_name="Synthetic", memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(
            self.character_id, "Synthetic", legacy_config_key="characters/default",
        )
        # Memory retrieval parity is tested in its own suites. This harness is
        # specifically the turn/state/transport/presentation product path and
        # must not load an embedding model per isolated session.
        self.writer.compare = lambda *_args, **_kwargs: {}
        self.provider = _Provider()
        self.conversation = Conversation(
            self.provider, conversation_file=self.conversation_file,
            summary_file=self.summary_file, clock=lambda: self.now,
        )
        self.memory = _Memory()
        self.memory.llm = self.provider
        self.tts = tts or SyntheticLifecycleTts()
        self.script = _ResponseScript()
        self.service = self._create_service()
        self.artifacts: list[dict[str, object]] = []
        self.reducer = UnityEquivalentReducer()
        self.service.subscribe(self._collect_event)
        recorder = development_flight_recorder()
        self._owns_recorder = not recorder.enabled
        if self._owns_recorder:
            recorder.start(unity_pid=0, state_provider=lambda: {})
        self._telemetry_start = len(recorder._events)

    def _create_service(self) -> AssistantService:
        return AssistantService(
            self.provider, self.memory, self.conversation, object(),
            {"_character_id": self.character_id, "name": "Synthetic"},
            "Synthetic production-session character.", self.tts,
            # Legacy cross-authority state/speech fixture; V2 memory QA uses its
            # separate normal-authority service fixture with an admitted owner.
            memory_authority="v1",
            response_generator=self.script,
            memory_v2_shadow_writer=self.writer,
            character_id=self.character_id,
        )

    def _collect_event(self, event) -> None:
        artifact = {
            "type": "event",
            "event": {
                "type": str(event.type),
                "data": AIFrenWebSocketHost._json_safe(event.data),
            },
        }
        self.artifacts.append(artifact)
        self.reducer.consume(artifact)

    def close(self) -> None:
        complete = getattr(self.tts, "complete", None)
        if callable(complete):
            complete()
        self.service.close()
        if self._owns_recorder:
            development_flight_recorder().stop()
        self._temp.cleanup()

    def turn(self, text: str, *responses: str, speak: bool = False) -> TurnResult:
        if responses:
            # The first response drives ordinary generation. Any subsequent
            # response is an explicit bounded repair draft for this same turn;
            # it must not leak into the next user turn's primary generation.
            self.script.queue(responses[:1])
            self.provider.queue_bounded(responses[1:])
        result = self.service.process_text_turn(text, speak=speak)
        if speak:
            complete = getattr(self.tts, "complete", None)
            if callable(complete):
                complete()
        self.now += timedelta(seconds=2)
        return result

    def append_synthetic_history(self, message_pairs: int) -> None:
        """Grow only the canonical synthetic archive; no state is inferred."""
        for index in range(max(0, int(message_pairs))):
            self.conversation.add_user_message(f"Synthetic unrelated history turn {index}.")
            self.conversation.add_assistant_message(f"Synthetic unrelated reply {index}.")
        self.conversation.save()

    def restart(self) -> None:
        """Reconstruct the production service while retaining canonical/V2 state."""
        self.service.close()
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id,
            display_name="Synthetic", memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(
            self.character_id, "Synthetic", legacy_config_key="characters/default",
        )
        self.writer.compare = lambda *_args, **_kwargs: {}
        self.conversation = Conversation(
            self.provider, conversation_file=self.conversation_file,
            summary_file=self.summary_file, clock=lambda: self.now,
        )
        self.service = self._create_service()
        self.service.subscribe(self._collect_event)

    def proactive(self, *, now: datetime, draft: str | None = None,
                  error: Exception | None = None, speak: bool = False) -> TurnResult:
        if draft is not None:
            self.provider.proactive_draft = draft
        self.provider.proactive_error = error
        self.now = now
        return self.service.process_proactive_checkin(
            now_us=int(now.timestamp() * 1_000_000), speak=speak,
        )

    def effects(self, *, target: str = "companion"):
        return self.repository.capability_effects(self.character_id, target=target)

    def relations(self):
        return self.repository.list_scene_relations(self.character_id, limit=96)

    def activity(self, actor="companion") -> str | None:
        state = self.repository.lookup_actor_state(
            self.character_id, actor, "activity",
        ).state
        return state.value if state is not None else None

    def snapshot_projection(self) -> dict[str, int]:
        snapshot = self.service.continuity_snapshot()
        return {
            "subjects": len(snapshot.get("scene_subjects", ())),
            "relations": len(snapshot.get("scene_relations", ())),
            "effects": len(snapshot.get("capability_effects", ())),
            "baselines": len(snapshot.get("profile_baseline", ())),
        }

    def validation_metrics(self) -> dict[str, int]:
        events = list(development_flight_recorder()._events)[self._telemetry_start:]
        relevant = [item for item in events if item.get("event") in {
            "response_contract_validation", "sleep_reaction_validation",
        }]
        return {
            "total": len(relevant),
            "accepted": sum(item.get("outcome") == "accepted" for item in relevant),
            "repaired": sum(item.get("outcome") == "repaired" for item in relevant),
            "fallback": sum(item.get("outcome") == "fallback" for item in relevant),
        }
