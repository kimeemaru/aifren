"""Frontend-independent application service for AIFren.

This module deliberately owns no UI toolkit code.  It provides the same
application turn lifecycle used by the desktop client and exposes lifecycle
events that a future frontend can consume.
"""

from __future__ import annotations

from pathlib import Path

from context_governor import governor_enabled

from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
import re
import time
import threading
from typing import Any, Callable, Optional
import uuid

from conversation.persistence import ConversationPersistenceError

from dialogue_semantics import (
    DialogueSpanKind,
    SemanticSentenceAccumulator,
    SemanticSpeechGrouper,
    parse_dialogue,
    sanitize_spoken_unicode,
    spoken_text,
)
from development_flight_recorder import development_flight_recorder
from presentation_metadata import (
    ParsedAssistantResponse,
    ResponsePresentationMetadata,
    StreamingResponseDialogue,
    parse_assistant_response,
    response_contract_prompt,
    response_expression_context,
)
from llm.output_canonicalization import (
    ModelOutputCanonicalizer,
    canonicalize_model_output,
    require_visible_model_output,
)
from llm.unavailable import ModelTransportError


EventListener = Callable[["AssistantEvent"], None]
ResponseGenerator = Callable[[Any, Any, Any, str, str], str]


def canonical_message_identity(index: int, message: object) -> str:
    """Stable append identity; equal text remains distinct by canonical index."""
    timestamp = message.get("timestamp") if isinstance(message, dict) else ""
    return f"{int(index)}:{str(timestamp or '')}"


def _canonical_message_timestamp(message: object) -> str | None:
    return message.get("timestamp") if isinstance(message, dict) else None


_PERFORMANCE_TIMING = os.environ.get("AIFREN_PERFORMANCE_TIMING", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _timing_log(message: str) -> None:
    if _PERFORMANCE_TIMING:
        print(f"[AIFren Timing] {message}")


@dataclass(frozen=True)
class AssistantEvent:
    """A frontend-neutral notification emitted during assistant activity."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    """The outcome of a submitted text turn."""

    user_message: str
    reply: str = ""
    spoken_text: str = ""
    presentation: ResponsePresentationMetadata | None = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class _ResponsePolicy:
    """One-turn read-only capability/requirement preview."""

    effects: Any = None
    requirement: Any = None
    context_block: str | None = None
    enforce_before_presentation: bool = False
    changed_by_current_evidence: bool = False
    action_plan: Any = None
    action_decision_category: str | None = None
    policy_error: str | None = None
    recent_administrative_clears: tuple[dict[str, object], ...] = ()
    hearing_input_unavailable: bool = False
    current_user_projection: str | None = None
    inaccessible_input_terms: tuple[str, ...] = ()
    memory_answer_requirement: Any = None
    memory_authority_context: str | None = None
    authoritative_memory_response: str | None = None
    memory_authority_retrieval_ms: float | None = None
    memory_authority_turn: Any = None
    memory_realization: Any = None
    memory_realized_response: Any = None
    memory_query_decision: Any = None
    temporal_facts: Any = None


class _TurnCancelled(Exception):
    """Internal control flow for a user turn replaced by newer input."""


class _ContinuityMutationUnavailable(RuntimeError):
    """A committed user input whose expected state update could not complete."""


@dataclass(frozen=True)
class _SceneReaction:
    """One optional reaction to an independently committed scene event."""

    event: Any
    message_index: int
    message: dict[str, Any]
    character_id: str
    revision: str
    turn_id: int
    cancel_event: threading.Event


def _hearing_unavailable_input_response_valid(
    dialogue: object, inaccessible_terms: tuple[str, ...] = (),
) -> bool:
    """Reject comprehension claims; inaccessible content is masked upstream."""
    audible = " ".join(spoken_text(dialogue).casefold().replace("’", "'").split())
    if not audible:
        return True
    if re.search(
        r"\b(?:i\s+(?:heard|understood|caught)\s+(?:you|that|what|it)|i\s+understand\b|"
        r"i\s+(?:can|could)\s+hear\s+(?:you|that|what)|"
        r"got\s+it|i\s+know\s+what\s+you\s+(?:said|told\s+me))\b",
        audible, re.I,
    ):
        return False
    return not any(re.search(rf"\b{re.escape(term)}\b", audible) for term in inaccessible_terms)


def _reasserts_recent_administrative_clear(
    dialogue: object,
    clears: tuple[dict[str, object], ...],
) -> bool:
    """Reject only present-tense reassertions of a recently cleared cause."""
    text = " ".join(str(dialogue or "").casefold().replace("’", "'").split())
    for row in clears:
        facet = str(row.get("region") or "").casefold()
        cause = str(row.get("cause") or "").casefold()
        relation = str(row.get("relation") or "").casefold()
        escaped_cause = re.escape(cause)
        if cause and relation in {"wearing", "worn_by"} and re.search(
            rf"\b(?:still\s+(?:am\s+)?wearing|(?:am|i'm)\s+still\s+wearing)\b.{{0,50}}"
            rf"\b(?:the\s+)?{escaped_cause}\b|\b{escaped_cause}\b.{{0,35}}"
            r"\b(?:is\s+still\s+on\s+me|remains?\s+(?:on|worn))\b",
            text,
        ):
            return True
        if cause and relation in {"holding", "carrying"} and re.search(
            rf"\b(?:still\s+(?:am\s+)?(?:holding|carrying)|(?:am|i'm)\s+still\s+"
            rf"(?:holding|carrying))\b.{{0,50}}\b(?:the\s+)?{escaped_cause}\b|"
            rf"\b{escaped_cause}\b.{{0,35}}\b(?:is\s+still\s+in\s+my\s+hand|remains?\s+held)\b",
            text,
        ):
            return True
        if facet == "mouth" and re.search(
            r"\b(?:muffled\s+by\s+(?:your|the)\s+hand|(?:your|the)\s+hand\s+"
            r"(?:is\s+)?(?:over|covering)\s+(?:my|the|their|her|his)\s+mouth|mouth\s+(?:is\s+)?"
            r"(?:still\s+)?covered)\b", text,
        ):
            return True
        if facet == "eyes" and re.search(
            r"\b(?:still\s+blindfolded|blindfold\s+(?:is\s+)?still\s+in\s+place|"
            r"can't\s+see\s+(?:because|through)|vision\s+(?:is\s+)?still\s+blocked)\b",
            text,
        ):
            return True
        if facet == "wrists" and cause and re.search(
            rf"\b(?:tethered|handcuffed|secured|anchored|held\s+in\s+place)\b.{{0,55}}"
            rf"\b{re.escape(cause)}\b|\b{re.escape(cause)}\b.{{0,55}}"
            r"\b(?:tether|handcuff|stability|holds?\s+me)\b",
            text,
        ):
            return True
        if facet == "ears" and cause and re.search(
            rf"\b(?:can't\s+hear|cannot\s+hear|deafening|overwhelming|too\s+loud)\b.{{0,55}}"
            rf"\b{re.escape(cause)}\b|\b{re.escape(cause)}\b.{{0,55}}"
            r"\b(?:prevents?\s+me\s+hearing|deafening|overwhelming|too\s+loud)\b",
            text,
        ):
            return True
    return False


class AssistantService:
    """Coordinates a conversational turn independently of any frontend.

    Dependencies may be supplied directly for tests or alternate hosts.  The
    ``create_default`` factory retains the current project initialization
    behavior and does not alter any persistent-file schema or location.
    """

    def __init__(
        self,
        llm: Any,
        memory: Any,
        conversation: Any,
        voice: Any,
        character: dict[str, Any],
        character_prompt: str,
        tts: Any,
        response_generator: Optional[ResponseGenerator] = None,
        ptt_factory: Optional[Callable[..., Any]] = None,
        memory_v2_shadow: Any = None,
        memory_v2_shadow_writer: Any = None,
        memory_recall_shadow: Any = None,
        active_state_contextual_shadow: Any = None,
        open_thread_contextual_shadow: Any = None,
        character_id: str | None = None,
        memory_v2_unsubscribe: Callable[[], None] | None = None,
        memory_authority: str | None = None,
        memory_v2_authority: Any = None,
        v2_recent_context_policy: str | None = None,
        recent_pulse_enabled: bool | None = None,
        companion_context_sources: tuple[Any, ...] = (),
        transient_impulse_store: Any = None,
    ) -> None:
        from config import RECENT_PULSE_ENABLED
        self._recent_pulse_enabled = RECENT_PULSE_ENABLED if recent_pulse_enabled is None else bool(recent_pulse_enabled)
        # Small explicit dependency seam, not a registry/plugin framework.
        self._companion_context_sources = tuple(companion_context_sources[:3])
        self._last_companion_context_diagnostics = {}
        self.llm = llm
        self._model_runtime_availability = "unconfigured" if getattr(llm, "is_available", True) is False else "unknown"
        self.memory = memory
        self.conversation = conversation
        self._storage_lease = getattr(conversation, "_storage_lease", None) or getattr(memory_v2_shadow_writer, "runtime_lease", None)
        if self._storage_lease is not None:
            self.conversation.continuity_write_guard = self._storage_lease.assert_current
            self.memory.continuity_write_guard = self._storage_lease.assert_current
        self._closed = False
        self._transient_impulse_store = transient_impulse_store
        self._impulse_session = uuid.uuid4().hex
        self._impulse_turn_owner = None
        self._impulse_lease = None
        self._impulse_receipt_ready = False
        self._impulse_published = False
        self._last_impulse_diagnostics = {}
        if transient_impulse_store is not None:
            from transient_impulses import TransientImpulseStore
            if (not isinstance(transient_impulse_store, TransientImpulseStore)
                    or transient_impulse_store.character_id != str(character_id or character.get("_character_id"))):
                raise ValueError("attention owner does not match the selected character")
            self._last_impulse_diagnostics = transient_impulse_store.recover(conversation)
        self.conversation._temporal_reply_is_human_owned = self._temporal_reply_is_human_owned
        self.voice = voice
        self.character = character
        self.character_id = character_id or character.get("_character_id")
        self._character_binding_lock = threading.RLock()
        self._character_session = str(uuid.uuid4())
        self.character_prompt = character_prompt
        from model_settings import explicit_avatar_cues_enabled, companion_preferences
        self.explicit_avatar_cues = explicit_avatar_cues_enabled()
        preferences = companion_preferences()
        self.conversation_style = preferences["conversation_style"]
        self.responsive_speech = preferences["responsive_speech"]
        self.automatic_expressions = preferences["automatic_expressions"]
        self._automatic_expression_worker = None
        self._automatic_expression_lease = None
        self._automatic_expression_lock = threading.Lock()
        self._published_expression: ResponsePresentationMetadata | None = None
        self._published_expression_owner: tuple | None = None
        self.tts = tts

        self._response_generator = response_generator
        self._ptt_factory = ptt_factory
        self._listeners: list[EventListener] = []
        self._listeners_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._scene_ui_interaction_lock = threading.Lock()
        self._turn_state_lock = threading.Lock()
        self._turn_generation = 0
        self._active_turn_id = 0
        self._active_turn_cancel: threading.Event | None = None
        self._provider_activity_lock = threading.Lock()
        self._active_provider_requests = 0
        self._ptt = None
        self._ptt_binding = "F8"
        self._memory_v2_shadow = memory_v2_shadow
        self._memory_v2_shadow_writer = memory_v2_shadow_writer
        self._memory_recall_shadow = memory_recall_shadow
        self._memory_v2_unsubscribe = memory_v2_unsubscribe
        if memory_authority is None:
            from config import configured_memory_authority
            memory_authority = configured_memory_authority()
        if str(memory_authority) not in {"v1", "v2"}:
            raise ValueError("memory authority must be v1 or v2")
        if str(memory_authority) == "v2" and memory_v2_authority is None:
            raise RuntimeError("Memory V2 authority was enabled but could not initialize.")
        if str(memory_authority) == "v2" and response_generator is not None:
            raise RuntimeError(
                "Memory V2 authority requires the shared context-builder/provider path."
            )
        self._memory_authority = str(memory_authority)
        bind_authority = getattr(self.conversation, "set_memory_authority", None)
        if callable(bind_authority):
            bind_authority(self._memory_authority)
        if v2_recent_context_policy is None:
            from config import V2_AUTHORITY_RECENT_POLICY
            v2_recent_context_policy = V2_AUTHORITY_RECENT_POLICY
        self._v2_recent_context_policy = str(v2_recent_context_policy)
        self._memory_v2_authority = memory_v2_authority
        self._active_state_contextual_shadow = active_state_contextual_shadow
        self._open_thread_contextual_shadow = open_thread_contextual_shadow
        self._last_durable_context_admission = None
        self._last_active_state_context_admission = None
        self._last_current_continuity_admission = None
        self._last_memory_authority_diagnostics: dict[str, object] = {}
        self._last_interaction_policy = None
        self._sleep_reaction_sequence = 0
        self._recent_sleep_reaction_signatures = deque(maxlen=6)
        # Frontends may choose whether an STT result is immediately submitted
        # or presented for review.  The historic desktop/global-PTT behavior
        # remains auto-submit by default.
        self._ptt_auto_submit_transcriptions = True
        self._tts_reports_playback_start = False
        self._tts_state_lock = threading.Lock()
        # A PTT/stop boundary invalidates both audio that already exists and
        # speech work that the current streamed model turn has not created
        # yet. This lock makes generation capture, queue registration, and a
        # non-streamed speak dispatch atomic with respect to invalidation.
        # Some providers synchronously report playback_started from inside
        # speak(). Reentrancy keeps that callback inside the same atomic
        # generation boundary without deadlocking the dispatch thread.
        self._speech_generation_lock = threading.RLock()
        self._speech_generation = 0
        self._active_tts_playback_id = 0
        self._active_stream_playback: dict[str, Any] | None = None
        self._streaming_speech_queue = None
        self._pending_stream_speech: dict[str, Any] | None = None
        self._current_turn_started_at: float | None = None
        self._performance_lock = threading.Lock()
        self._current_turn_timing: dict[str, Any] | None = None
        self._playback_turn_timings: dict[int, dict[str, Any]] = {}
        self._last_ptt_release_at: float | None = None
        self._configure_tts_playback_events()
        self._sync_character_scene_profile()
        self._bind_canonical_observation_recovery()
        if self.automatic_expressions:
            self.apply_companion_preferences(preferences)

    def _bind_canonical_observation_recovery(self) -> None:
        self._canonical_observation_recovery = None
        if getattr(self._memory_v2_shadow_writer, "application_dir", None) is not None:
            self._memory_v2_shadow_writer.enable_loci = self._memory_authority == "v2"
        if (self._memory_authority == "v2"
                and getattr(self._memory_v2_shadow_writer, "application_dir", None) is not None):
            from memory_v2_runtime_observation import CanonicalObservationRecovery
            self._canonical_observation_recovery = CanonicalObservationRecovery(
                self._memory_v2_shadow_writer, self.conversation,
            )
            self._memory_v2_authority.observation_health_provider = (
                self._canonical_observation_recovery.prepare_lookup
            )
            authority = self._memory_v2_authority
            self._canonical_observation_recovery.embedding_provider_getter = lambda: getattr(
                getattr(authority.recall, "semantic", None), "embedding_provider", None,
            )
            self._recover_canonical_observers()

    def _recover_canonical_observers(self) -> None:
        recovery = getattr(self, "_canonical_observation_recovery", None)
        if recovery is None:
            return
        try:
            recovery.run_page()
        except Exception:
            # Canonical success is retained. Bounded structural failure is
            # retryable at the next serialized turn/reopen, never a user fact.
            recovery.last_status = {"state": "failed", "reason": "observer_failed"}

    def maintain_canonical_observers(self) -> None:
        """One idle page using the existing host maintenance/turn ownership."""
        if not self._turn_lock.acquire(blocking=False):
            return
        try:
            self._recover_canonical_observers()
            recovery = self._canonical_observation_recovery
            if recovery is not None:
                provider = getattr(getattr(self._memory_v2_authority.recall, "semantic", None), "embedding_provider", None)
                try:
                    recovery.maintain_embeddings(provider)
                except Exception:
                    # Derived work must remain retryable and must not retire
                    # the host's polling loop or its ordinary turn service.
                    recovery.last_embedding_work = {"embedded": 0, "failed": 1}
                rollover = getattr(self.conversation, "episode_compaction_rollover", None)
                if (rollover is not None and self._model_configuration_error() is None
                        and self._model_runtime_availability not in {"unconfigured", "unavailable"}):
                    try:
                        rollover.request_historical_page(
                            self.conversation.messages[:self.conversation._persisted_message_count],
                        )
                    except Exception:
                        # Source/cache validation is retried by this same
                        # bounded owner. Never invoke a foreground rebuild.
                        pass
        finally:
            self._turn_lock.release()

    def _sync_character_scene_profile(self) -> None:
        """Refresh only the rebuildable profile-baseline cache."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return
        try:
            from character_scene_profile import (
                cache_character_scene_profile,
                derive_character_scene_profile,
            )
            from memory_v2_store import MemoryV2Repository

            repository = MemoryV2Repository(store)
            repository.ensure_character(
                str(self.character_id), str(self.character.get("name") or "AIFren"),
            )
            profile = derive_character_scene_profile(self.character, self.character_prompt)
            cache_character_scene_profile(repository, str(self.character_id), profile)
        except Exception:
            # A baseline is optional and lower authority. Active State and
            # ordinary turns remain available if its rebuildable cache fails.
            pass

    def _provider_request_begin(self) -> None:
        with self._provider_activity_lock:
            self._active_provider_requests += 1

    def _provider_request_end(self) -> None:
        with self._provider_activity_lock:
            self._active_provider_requests = max(0, self._active_provider_requests - 1)

    def provider_request_active(self) -> bool:
        """Report actual live provider-call lifetime for diagnostics only."""
        with self._provider_activity_lock:
            return self._active_provider_requests > 0

    def _begin_turn_timing(
        self,
        *,
        turn_id: int,
        source: str,
        accepted_at: float,
        ptt_release_at: float | None,
        stt_final_at: float | None,
    ) -> None:
        if not _PERFORMANCE_TIMING:
            return
        trace = {
            "turn_id": int(turn_id),
            "source": str(source or "typed"),
            "accepted": accepted_at,
            "ptt_release": ptt_release_at,
            "stt_final": stt_final_at,
        }
        with self._performance_lock:
            self._current_turn_timing = trace
        if stt_final_at is not None:
            _timing_log(f"STT final -> accepted={accepted_at - stt_final_at:.3f}s")

    def _mark_turn_timing(self, name: str, at: float | None = None) -> None:
        if not _PERFORMANCE_TIMING:
            return
        at = time.monotonic() if at is None else at
        with self._performance_lock:
            trace = self._current_turn_timing
            if trace is None or name in trace:
                return
            trace[name] = at
            accepted = trace["accepted"]
        _timing_log(f"{name.replace('_', ' ')} t={at - accepted:.3f}s")

    @staticmethod
    def _duration(trace: dict[str, Any], start: str, end: str) -> str:
        first = trace.get(start)
        second = trace.get(end)
        return f"{second - first:.3f}s" if first is not None and second is not None else "n/a"

    def _log_turn_latency_summary(self, trace: dict[str, Any]) -> None:
        _timing_log(
            "turn latency summary; "
            f"source={trace.get('source', 'typed')}; "
            f"STT final -> accepted={self._duration(trace, 'stt_final', 'accepted')}; "
            f"accepted -> first text={self._duration(trace, 'accepted', 'first_canonical_visible')}; "
            f"accepted -> first sentence={self._duration(trace, 'accepted', 'first_complete_sentence')}; "
            f"accepted -> LLM final={self._duration(trace, 'accepted', 'provider_complete')}; "
            f"LLM final -> TTS submit={self._duration(trace, 'provider_complete', 'tts_submitted')}; "
            f"TTS synth={self._duration(trace, 'tts_submitted', 'tts_synthesis_complete')}; "
            f"accepted -> first audio={self._duration(trace, 'accepted', 'playback_started')}"
        )

    def _configure_tts_playback_events(self) -> None:
        callback_setter = getattr(self.tts, "set_playback_started_callback", None)
        if callable(callback_setter):
            callback_setter(self._on_tts_playback_started)
            self._tts_reports_playback_start = True
        finished_callback_setter = getattr(self.tts, "set_playback_finished_callback", None)
        if callable(finished_callback_setter):
            finished_callback_setter(self._on_tts_playback_finished)

    def _on_tts_playback_started(
        self,
        duration_seconds: float,
        lip_sync_envelope: list[float] | None = None,
        word_start_seconds: list[float] | None = None,
        playback_id: int | None = None,
        *, chunk_metadata: dict | None = None,
    ) -> None:
        """Forward actual local playback start without coupling to a frontend."""
        with self._speech_generation_lock:
            stream_speech = self._pending_stream_speech
            if stream_speech is not None and stream_speech.get("speech_generation") == self._speech_generation:
                self._pending_stream_speech = None
            else:
                stream_speech = None
            if chunk_metadata is not None and stream_speech is None:
                # A late continuous transition cannot fall back to direct
                # speech and reopen a cancelled subtitle/lip-sync session.
                return
            with self._tts_state_lock:
                self._active_tts_playback_id = int(playback_id or 0)
                self._active_stream_playback = stream_speech
            event_data = {
                "state": "playback_started",
                "duration_seconds": float(duration_seconds),
                "lip_sync_envelope": list(lip_sync_envelope or ()),
                "word_start_seconds": list(word_start_seconds or ()),
                "playback_id": int(playback_id or 0),
                "streamed": stream_speech is not None,
            }
            if stream_speech is not None:
                event_data.update(
                    content=stream_speech["spoken"],
                    subtitle_content=stream_speech["subtitle"],
                    chunk_index=stream_speech["chunk_index"],
                    turn_id=stream_speech["turn_id"],
                )
            if chunk_metadata is not None:
                event_data.update(chunk_metadata)
            callback_at = time.monotonic()
            self._mark_turn_timing("first_chunk_ready" if chunk_metadata is not None else "tts_synthesis_complete", callback_at)
            self._mark_turn_timing("playback_started", callback_at)
            with self._performance_lock:
                trace = self._current_turn_timing
                if trace is not None and int(playback_id or 0) > 0:
                    self._playback_turn_timings[int(playback_id)] = trace
            self._emit("tts_state", **event_data)
            if self._current_turn_started_at is not None:
                _timing_log(f"first audio playback t={callback_at - self._current_turn_started_at:.3f}s")
            if trace is not None:
                self._log_turn_latency_summary(trace)

    def _on_tts_playback_finished(self, playback_id: int | None = None) -> None:
        """Forward a natural local playback completion to presentation clients."""
        with self._speech_generation_lock:
            completed_id = int(playback_id or 0)
            with self._tts_state_lock:
                if completed_id and self._active_tts_playback_id != completed_id:
                    development_flight_recorder().mark(
                        "tts_stale_result_discarded", playback_id=completed_id
                    )
                    print(
                        "[AIFren TTS] ignored stale natural completion; "
                        f"id={completed_id}; active={self._active_tts_playback_id}"
                    )
                    return
                stream_speech = self._active_stream_playback
                retire_face = self._active_tts_playback_id == completed_id and completed_id != 0
                self._active_tts_playback_id = 0
                self._active_stream_playback = None
            if retire_face:
                self._retire_automatic_expression()
            print(f"[AIFren TTS] service natural completion; id={completed_id}; PTT ready")
            playback_finished_at = time.monotonic()
            with self._performance_lock:
                trace = self._playback_turn_timings.pop(completed_id, None)
                if trace is not None:
                    trace["playback_ended"] = playback_finished_at
            if trace is not None:
                _timing_log(
                    "playback ended; "
                    f"accepted -> end={self._duration(trace, 'accepted', 'playback_ended')}; "
                    f"playback duration={self._duration(trace, 'playback_started', 'playback_ended')}"
                )
            event_data = {"state": "stopped", "playback_id": completed_id, "streamed": stream_speech is not None}
            if stream_speech is not None:
                event_data.update(chunk_index=stream_speech["chunk_index"], turn_id=stream_speech["turn_id"],
                                  committed_stream=stream_speech.get("committed_stream", False))
            self._emit("tts_state", **event_data)

    @classmethod
    def create_default(cls) -> "AssistantService":
        """Build the service with AIFren's existing default components."""
        # Import lazily so alternative frontends and unit tests do not need to
        # import the local STT/TTS implementations until they use this factory.
        from assistant import initialize
        from config import (
            V2_AUTHORITY_RECENT_POLICY,
            configured_memory_authority,
        )
        memory_authority = configured_memory_authority()

        (
            llm,
            memory,
            conversation,
            voice,
            character,
            character_prompt,
            tts,
            _,
        ) = initialize(prepare_v1_memory=memory_authority == "v1")

        with ExitStack() as setup:
            storage_lease = getattr(conversation, "_storage_lease", None)
            if storage_lease is not None:
                setup.callback(storage_lease.close)
            shadow = None
            shadow_writer = None
            memory_recall_shadow = None
            memory_v2_authority = None
            shadow_unsubscribe = None
            if memory_authority == "v2":
                from memory_v2_shadow_writer import MemoryV2ShadowWriter
                shadow_writer = MemoryV2ShadowWriter(
                    ".",
                    character_id=character["_character_id"],
                    display_name=character.get("_display_name") or character.get("name", "AIFren"),
                    memory_file=getattr(memory, "memory_file", "memories.json"),
                )
                setup.callback(shadow_writer.close)
                from memory_v2_store import MemoryV2Repository
                MemoryV2Repository(shadow_writer.store).ensure_character(
                    character["_character_id"], character.get("_display_name") or character.get("name", "AIFren"),
                )
                try:
                    from memory_v2_episode_compaction import (
                        EpisodeCompactionCache,
                        EpisodeCompactionRollover,
                        EpisodeCompactor,
                    )
                    episode_cache = EpisodeCompactionCache(
                        shadow_writer.store, character["_character_id"],
                    )
                    conversation.episode_compaction_cache = episode_cache

                    def episode_compactor_factory():
                        # Use a distinct transport client so background derived
                        # work never shares mutable stream state with live chat.
                        from llm.llm import create_llm
                        return EpisodeCompactor(create_llm())

                    conversation.episode_compaction_rollover = EpisodeCompactionRollover(
                        episode_cache,
                        episode_compactor_factory,
                        historical_runtime=memory_authority == "v2",
                        canonical_source_provider=lambda: conversation.messages[:conversation._persisted_message_count],
                        event_callback=lambda event, data: development_flight_recorder().mark(
                            event, **data,
                        ),
                    )
                except Exception:
                    # Episode compaction is disposable derived context. A missing
                    # or unreadable cache must never prevent ordinary raw history.
                    conversation.episode_compaction_cache = None
                    conversation.episode_compaction_rollover = None
                close_episode = getattr(conversation, "close_episode_compaction_rollover", None)
                if callable(close_episode):
                    setup.callback(close_episode)
                subscribe = getattr(memory, "subscribe_mutations", None)
                if callable(subscribe):
                    shadow_unsubscribe = subscribe(shadow_writer.observe)
                    setup.callback(shadow_unsubscribe)
                try:
                    from config import MEMORY_V2_REAL_TURN_SHADOW_ENABLED
                    if MEMORY_V2_REAL_TURN_SHADOW_ENABLED:
                        from memory_recall_shadow import RealTurnMemoryShadow
                        memory_recall_shadow = RealTurnMemoryShadow(
                            shadow_writer.database_path,
                            character["_character_id"],
                            recorder=development_flight_recorder(),
                        )
                        setup.callback(memory_recall_shadow.close)
                except Exception:
                    memory_recall_shadow = None

            if memory_authority == "v2":
                if shadow_writer is None:
                    raise RuntimeError(
                        "Memory V2 authority requires the governed V2 store; "
                        "V1 fallback is intentionally disabled."
                    )
                from memory_v2_authority import DevelopmentV2MemoryAuthority
                memory_v2_authority = DevelopmentV2MemoryAuthority(
                    shadow_writer.store,
                    character["_character_id"],
                    conversation.messages,
                    recent_context_policy=V2_AUTHORITY_RECENT_POLICY,
                )
                setup.callback(memory_v2_authority.close)
                print("[AIFren] Memory authority: V2")

            # No external extractor is installed here.  The optional seam is
            # reserved for an explicitly approved local provider and remains
            # disabled unless a future factory supplies one deliberately.
            from config import (ACTIVE_STATE_CONTEXTUAL_SHADOW_ENABLED, ACTIVE_STATE_CONTEXTUAL_SHADOW_PROVIDER,
                                OPEN_THREAD_CONTEXTUAL_SHADOW_ENABLED, OPEN_THREAD_CONTEXTUAL_SHADOW_PROVIDER)
            if ACTIVE_STATE_CONTEXTUAL_SHADOW_ENABLED:
                print(
                    "[Active State shadow] no approved local extractor is installed "
                    f"for provider={ACTIVE_STATE_CONTEXTUAL_SHADOW_PROVIDER!r}; observation remains disabled"
                )
            if OPEN_THREAD_CONTEXTUAL_SHADOW_ENABLED:
                print(
                    "[Open Thread shadow] no approved local extractor is installed "
                    f"for provider={OPEN_THREAD_CONTEXTUAL_SHADOW_PROVIDER!r}; observation remains disabled"
                )

            service = cls(
                llm=llm,
                memory=memory,
                conversation=conversation,
                voice=voice,
                character=character,
                character_prompt=character_prompt,
                tts=tts,
                memory_v2_shadow=shadow,
                memory_v2_shadow_writer=shadow_writer,
                memory_recall_shadow=memory_recall_shadow,
                active_state_contextual_shadow=None,
                open_thread_contextual_shadow=None,
                character_id=character["_character_id"],
                memory_v2_unsubscribe=shadow_unsubscribe,
                memory_authority=memory_authority,
                memory_v2_authority=memory_v2_authority,
            )
            setup.pop_all()  # The fully constructed service owns these resources.
            return service


    def _assert_character_state_ownership(self) -> None:
        """Fail closed if a character-scoped cache was rebound incompletely."""
        expected = str(self.character_id or "")
        # Dependency-injected unit/alternate services may intentionally have
        # no durable character identity. Production factories always do.
        if not expected:
            return
        annotated = str(self.character.get("_character_id") or "") if isinstance(self.character, dict) else ""
        writer = self._memory_v2_shadow_writer
        writer_character = str(getattr(writer, "character_id", expected) or "")
        authority_character = str(
            getattr(self._memory_v2_authority, "character_id", expected) or ""
        )
        if (not expected or annotated != expected or writer_character != expected
                or authority_character != expected):
            raise RuntimeError("Character-scoped state ownership is inconsistent.")

    def character_binding(self) -> dict[str, str]:
        return {"character_id": str(self.character_id or ""),
                "character_session": self._character_session}

    def require_character_binding(self, character_id, character_session) -> None:
        if (str(character_id or "") != str(self.character_id or "")
                or str(character_session or "") != self._character_session):
            raise RuntimeError("This control belongs to a retired character selection. Refresh it.")

    def memory_authority_status(self) -> dict[str, object]:
        return {
            "mode": self._memory_authority,
            "development_only": False,
            "v1_prompt_enabled": self._memory_authority == "v1",
            "v2_ready": self._memory_v2_authority is not None,
        }

    def prepare_character_switch(self) -> None:
        """Invalidate old-character turn/audio authority before rebinding."""
        with self._character_binding_lock:
            self._character_session = str(uuid.uuid4())
        self._cancel_active_turn()
        if self._ptt is not None:
            try:
                self._ptt.stop()
            finally:
                self._ptt = None
        self.stop_speaking(interrupted=True)

    def wait_for_character_switch_idle(self, timeout: float = 30.0) -> bool:
        """Wait for cancelled provider work to leave the serialized turn seam."""
        acquired = self._turn_lock.acquire(timeout=max(0.0, float(timeout)))
        if acquired:
            self._turn_lock.release()
        return acquired

    def switch_character_state(
        self,
        *,
        character_id: str,
        display_name: str,
        runtime_paths: dict[str, Any],
        application_dir: str | os.PathLike[str] = ".",
        publish_selection: Callable[[], Any] | None = None,
    ) -> None:
        """Atomically replace only character-owned continuity state.

        LLM, STT, TTS, and their process/device resources are runtime-owned and
        deliberately survive the switch. Canonical history, Memory V1/V2,
        Active State, Open Threads, scopes, and profile state are freshly
        loaded for the selected character.
        """
        if self._turn_lock.locked():
            raise RuntimeError("Character switch requires an idle assistant.")
        expected = str(uuid.UUID(str(character_id)))
        required = {"character", "personality", "memory", "conversation", "summary"}
        if not isinstance(runtime_paths, dict) or not required.issubset(runtime_paths):
            raise ValueError("Character runtime paths are incomplete.")

        from character_registry import CharacterRegistry
        registry_path = Path(application_dir).resolve() / "characters" / "registry.json"
        candidate_lease = None
        if registry_path.exists():
            from character_storage_runtime import acquire_runtime_lease
            candidate_lease = acquire_runtime_lease(CharacterRegistry(application_dir), expected)
            runtime_paths = candidate_lease.paths

        try:
            from assistant import build_character_prompt, load_character
            from conversation.conversation import Conversation
            from memory.memory import Memory
            # Candidate admission already holds a lease. A failed old-owner
            # save must release it without changing either live identity.
            self.save()
            memory = Memory(
                self.llm,
                memory_file=str(runtime_paths["memory"]),
                embedding_model=getattr(self.memory, "embedding_model", None),
            )
            if self._memory_authority == "v1":
                memory.generate_missing_embeddings()
                memory.generate_missing_metadata()
            conversation = Conversation(
                self.llm,
                conversation_file=str(runtime_paths["conversation"]),
                summary_file=str(runtime_paths["summary"]),
                memory_authority=self._memory_authority,
            )
            character, personality = load_character(
                runtime_paths["character"], runtime_paths["personality"],
            )
            character = dict(character)
            character["_character_id"] = expected
            character["_display_name"] = str(display_name)
            character_prompt = build_character_prompt(character, personality)
        except Exception:
            if candidate_lease is not None: candidate_lease.close()
            raise

        if candidate_lease is not None:
            conversation._storage_lease = candidate_lease
            conversation.continuity_write_guard = candidate_lease.assert_current
            memory.continuity_write_guard = candidate_lease.assert_current
        shadow_writer = None
        shadow_unsubscribe = None
        memory_recall_shadow = None
        memory_v2_authority = None
        try:
            if self._memory_authority == "v2":
                from memory_v2_shadow_writer import MemoryV2ShadowWriter
                shadow_writer = MemoryV2ShadowWriter(
                    application_dir, character_id=expected,
                    display_name=str(display_name or character.get("name") or "AIFren"),
                    memory_file=str(runtime_paths["memory"]),
                )
                from memory_v2_store import MemoryV2Repository
                MemoryV2Repository(shadow_writer.store).ensure_character(expected, str(display_name))
                try:
                    from memory_v2_episode_compaction import (
                        EpisodeCompactionCache,
                        EpisodeCompactionRollover,
                        EpisodeCompactor,
                    )
                    episode_cache = EpisodeCompactionCache(shadow_writer.store, expected)
                    conversation.episode_compaction_cache = episode_cache

                    def episode_compactor_factory():
                        from llm.llm import create_llm
                        return EpisodeCompactor(create_llm())

                    conversation.episode_compaction_rollover = EpisodeCompactionRollover(
                        episode_cache,
                        episode_compactor_factory,
                        historical_runtime=self._memory_authority == "v2",
                        canonical_source_provider=lambda: conversation.messages[:conversation._persisted_message_count],
                        event_callback=lambda event, data: development_flight_recorder().mark(
                            event, **data,
                        ),
                    )
                except Exception:
                    conversation.episode_compaction_cache = None
                    conversation.episode_compaction_rollover = None
                subscribe = getattr(memory, "subscribe_mutations", None)
                if callable(subscribe):
                    shadow_unsubscribe = subscribe(shadow_writer.observe)
                try:
                    from config import MEMORY_V2_REAL_TURN_SHADOW_ENABLED
                    if MEMORY_V2_REAL_TURN_SHADOW_ENABLED:
                        from memory_recall_shadow import RealTurnMemoryShadow
                        memory_recall_shadow = RealTurnMemoryShadow(
                            shadow_writer.database_path, expected,
                            recorder=development_flight_recorder(),
                        )
                except Exception:
                    memory_recall_shadow = None
            if self._memory_authority == "v2":
                if shadow_writer is None:
                    raise RuntimeError(
                        "Memory V2 authority could not bind the selected character; "
                        "V1 fallback is intentionally disabled."
                    )
                from memory_v2_authority import DevelopmentV2MemoryAuthority
                memory_v2_authority = DevelopmentV2MemoryAuthority(
                    shadow_writer.store, expected, conversation.messages,
                    recent_context_policy=self._v2_recent_context_policy,
                )
            # The candidate is complete, but the live owner is still intact.
            # A failed durable pointer write must never publish mixed identity.
            if publish_selection is not None:
                publish_selection()
        except Exception:
            # Cleanup failures cannot strand another provisional owner.
            with ExitStack() as cleanup:
                if candidate_lease is not None:
                    cleanup.callback(candidate_lease.close)
                if shadow_writer is not None:
                    cleanup.callback(shadow_writer.close)
                if memory_v2_authority is not None:
                    cleanup.callback(memory_v2_authority.close)
                if memory_recall_shadow is not None:
                    cleanup.callback(memory_recall_shadow.close)
                if shadow_unsubscribe is not None:
                    cleanup.callback(shadow_unsubscribe)
            raise

        close_episode_rollover = getattr(
            self.conversation, "close_episode_compaction_rollover", None,
        )
        if callable(close_episode_rollover):
            try:
                close_episode_rollover()
            except Exception as error:
                print(f"[Character switch] retired episode cleanup failed: {type(error).__name__}")
        if self._memory_v2_unsubscribe is not None:
            try:
                self._memory_v2_unsubscribe()
            except Exception as error:
                print(f"[Character switch] retired subscription cleanup failed: {type(error).__name__}")
        if self._memory_v2_authority is not None:
            try:
                self._memory_v2_authority.close()
            except Exception:
                pass
        if self._memory_v2_shadow_writer is not None:
            try:
                self._memory_v2_shadow_writer.close()
            except Exception as error:
                # The fully-built new owner must not be discarded because a
                # retired diagnostic/store connection reported cleanup trouble.
                print(
                    "[Character switch] retired Memory V2 cleanup failed: "
                    f"{type(error).__name__}"
                )
        if self._memory_recall_shadow is not None:
            try:
                # A character switch is rare and already serialized. Waiting
                # here guarantees no old-owner SQLite work survives rebinding.
                self._memory_recall_shadow.close()
            except Exception:
                pass
        if self._storage_lease is not None:
            try:
                self._storage_lease.close()
            except Exception as error:
                print(f"[Character switch] retired lease cleanup failed: {type(error).__name__}")
        self._storage_lease = candidate_lease or getattr(shadow_writer, "runtime_lease", None)
        self.memory = memory
        self.conversation = conversation
        self.conversation._temporal_reply_is_human_owned = self._temporal_reply_is_human_owned
        with self._character_binding_lock:
            self.character = character
            self.character_id = expected
            self._character_session = str(uuid.uuid4())
        self.character_prompt = character_prompt
        self._published_expression = None
        self._published_expression_owner = None
        # Explicitly supplied attention stores are character/archive-bound and
        # caller-owned. A character switch never carries their producer forward.
        self._transient_impulse_store = None
        self._memory_v2_shadow_writer = shadow_writer
        self._memory_v2_authority = memory_v2_authority
        self._memory_recall_shadow = memory_recall_shadow
        self._memory_v2_unsubscribe = shadow_unsubscribe
        self._active_state_contextual_shadow = None
        self._open_thread_contextual_shadow = None
        self._last_durable_context_admission = None
        self._last_active_state_context_admission = None
        self._last_current_continuity_admission = None
        self._last_memory_authority_diagnostics = {}
        self._last_interaction_policy = None
        self._sleep_reaction_sequence = 0
        self._recent_sleep_reaction_signatures.clear()
        self._last_ptt_release_at = None
        self._current_turn_started_at = None
        with self._performance_lock:
            self._current_turn_timing = None
            self._playback_turn_timings.clear()
        with self._turn_state_lock:
            self._turn_generation += 1
            self._active_turn_id = 0
            self._active_turn_cancel = None
        self._sync_character_scene_profile()
        self._assert_character_state_ownership()
        self._bind_canonical_observation_recovery()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Subscribe to backend events and return an unsubscribe callback."""
        with self._listeners_lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._listeners_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, event_type: str, **data: Any) -> None:
        data = {**self.character_binding(), **data}
        event = AssistantEvent(event_type, data)

        with self._listeners_lock:
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Frontend event handlers must not interrupt assistant work.
                pass

    @staticmethod
    def clean_text_for_tts(text: str) -> str:
        """Omit emotes using the same complete-span rule mirrored by Unity."""
        return spoken_text(text)

    @staticmethod
    def _record_generated_dialogue_structure(
        dialogue: str,
        spoken_projection: str,
        normalization: dict[str, int],
        *,
        turn_id: int,
    ) -> None:
        """Record Development-only span counts without retaining dialogue."""
        recorder = development_flight_recorder()
        if not recorder.enabled:
            return
        canonical_spans = parse_dialogue(dialogue)
        spoken_spans = parse_dialogue(spoken_projection)
        recorder.mark(
            "generated_dialogue_structure",
            turn_id=int(turn_id),
            canonical_action_span_count=sum(
                span.kind == DialogueSpanKind.EMOTE for span in canonical_spans
            ),
            spoken_emphasis_span_count=sum(
                span.kind == DialogueSpanKind.EMPHASIS for span in canonical_spans
            ),
            normalized_parenthesized_star_action_count=int(
                normalization.get("normalized_parenthesized_star_action_count", 0)
            ),
            normalized_starred_parenthetical_action_count=int(
                normalization.get("normalized_starred_parenthetical_action_count", 0)
            ),
            spoken_projection_action_count=sum(
                span.kind == DialogueSpanKind.EMOTE for span in spoken_spans
            ),
        )

    def _admit_durable_context(self, user_message: str, *, report_failure: bool = False):
        """Prepare optional V2 background data without coupling it to V1.

        Fail-open is deliberate: durable prompt evidence must never make an
        ordinary response unavailable.  The durable repository remains the
        authority for character, lifecycle, provenance, and user-evidence
        eligibility before the narrow admission policy runs.
        """
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            if report_failure:
                raise RuntimeError("durable lookup unavailable")
            return None
        try:
            from memory_v2_store import MemoryV2Repository, admit_durable_context

            return admit_durable_context(
                MemoryV2Repository(store), str(self.character_id), user_message,
            )
        except Exception:
            if report_failure:
                raise
            return None

    def _admit_active_state_context(self, user_message: str):
        """Prepare optional current-state background data without prompt coupling.

        The active-state repository enforces exact character, lifecycle,
        provenance, and user-evidence eligibility before this narrow admission
        policy chooses whether the typed state belongs in this turn's context.
        """
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return None
        try:
            from memory_v2_store import MemoryV2Repository, admit_active_headwear_context

            return admit_active_headwear_context(
                MemoryV2Repository(store), str(self.character_id), user_message,
            )
        except Exception:
            return None

    def _admit_current_continuity_context(self, user_message: str):
        """Select typed, scope-filtered current context for this one turn."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return None
        try:
            from conversation.temporal_context import derive_temporal_context_facts
            from current_continuity import admit_current_continuity_context
            from memory_v2_store import MemoryV2Repository

            facts = derive_temporal_context_facts(
                getattr(self.conversation, "messages", ()), user_message,
                clock=getattr(self.conversation, "_clock", None),
            )
            now_us = int(facts.current_local_datetime.timestamp() * 1_000_000)
            return admit_current_continuity_context(
                MemoryV2Repository(store), str(self.character_id), user_message, now_us=now_us,
            )
        except Exception:
            return None

    def _temporal_reply_is_human_owned(self, index: int, record: object) -> bool:
        """An existing durable proactive receipt cannot consume a human return."""
        store = getattr(self._memory_v2_shadow_writer, "store", None)
        if store is None:
            return True  # Proactive generation requires this store.
        try:
            return store.connection.execute(
                "SELECT 1 FROM proactive_checkins WHERE character_id=? AND conversation_index=? LIMIT 1",
                (str(self.character_id), index),
            ).fetchone() is None
        except Exception:
            return False  # Unknown completion ownership cannot consume an opportunity.

    def _with_temporal_policy(self, user_message: str, policy: _ResponsePolicy, *, active_truth_scope=None) -> _ResponsePolicy:
        if policy.hearing_input_unavailable:
            return policy
        from conversation.temporal_context import (
            derive_temporal_context_facts, build_temporal_context_block,
            temporal_response_requirement,
        )
        facts = derive_temporal_context_facts(
            getattr(self.conversation, "messages", ()), user_message,
            clock=getattr(self.conversation, "_clock", None),
            reply_is_human_owned=self._temporal_reply_is_human_owned,
            active_truth_scope=active_truth_scope,
        )
        requirement = temporal_response_requirement(facts, user_message)
        if facts.return_opportunity is None and requirement is None:
            return policy
        return replace(policy, temporal_facts=facts,
            requirement=policy.requirement or requirement,
            context_block="\n".join(x for x in (policy.context_block, build_temporal_context_block(facts)) if x),
            enforce_before_presentation=True)

    def _prepare_companion_memory_core(self, policy, requirement, authoritative):
        # Return continuity is a nonfactual respect constraint, not a competing
        # factual answer. Keep its validator and temporal guard on the composition.
        other_answer = (policy.requirement is not None and not (
            getattr(policy.requirement, "intent", None) == "return_continuity"
            and policy.requirement.mode == "must_respect" and not policy.requirement.facts))
        if (not getattr(self.llm, "companion_memory_realization", False)
                or not requirement.triggered or other_answer
                or policy.action_plan is not None or policy.policy_error is not None
                or (policy.effects is not None and (
                    policy.effects.speech_mode != "normal" or policy.effects.awareness_mode == "asleep"))):
            return None
        from companion_memory_realizer import CompanionMemoryRealizer
        if not hasattr(self, "_memory_surface_session"):
            self._memory_surface_session = uuid.uuid4().hex
        return CompanionMemoryRealizer().realize(requirement,
            seed=f"{self._memory_surface_session}:{self._active_turn_id}", abstention=authoritative)

    def _apply_memory_authority_policy(
        self,
        user_message: str,
        policy: _ResponsePolicy,
        *,
        active_truth_scope: object,
        memory_query_decision: Any = None,
    ) -> _ResponsePolicy:
        if self._memory_authority != "v2":
            return policy
        authority = self._memory_v2_authority
        if authority is None:
            raise RuntimeError(
                "Memory V2 authority is unavailable; V1 fallback is intentionally disabled."
            )
        scope_id = ""
        if isinstance(active_truth_scope, dict):
            scope_id = str(active_truth_scope.get("scope_id") or "")
        if not scope_id:
            raise RuntimeError(
                "Memory V2 authority cannot resolve the active truth scope."
            )
        from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth
        try:
            durable = self._admit_durable_context(user_message, report_failure=True)
            durable_health = RetrievalHealth((RetrievalLaneHealth("durable", "complete"),))
        except Exception:
            durable = None
            durable_health = RetrievalHealth((RetrievalLaneHealth("durable", "incomplete", "lookup", "lookup_failed"),))
        parameters = inspect.signature(authority.prepare).parameters
        accepts_options = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        options = {
            "active_truth_scope_id": scope_id,
            "active_truth_scope": (
                active_truth_scope if isinstance(active_truth_scope, dict) else None
            ),
            "governed_facts": (durable.facts if durable is not None else ()),
        }
        if accepts_options or "memory_query_decision" in parameters:
            options["memory_query_decision"] = memory_query_decision
        if accepts_options or "current_authoritative_context" in parameters:
            options["current_authoritative_context"] = policy.context_block or ""
        if accepts_options or "governed_lookup_health" in parameters:
            options["governed_lookup_health"] = durable_health
        turn = authority.prepare(user_message, **options)
        decision = getattr(turn, "memory_query_decision", memory_query_decision)
        authoritative = None
        # A pure memory question can be answered directly from authoritative
        # absence.  A composite turn with another typed current-state answer
        # requirement still needs the provider to communicate both results;
        # the no-evidence requirement remains enforced over its full reply.
        if turn.authoritative_no_evidence and policy.requirement is None:
            from memory_v2_authority import render_authoritative_no_evidence
            authoritative = render_authoritative_no_evidence(turn)
        lookup_unavailable = turn.requirement.lookup_unavailable
        lookup_diagnostics = turn.requirement.lookup_health.diagnostics()
        slot_counts = {
            "memory_supported_slots": sum(slot.state == "supported" for slot in turn.requirement.slots),
            "memory_missing_slots": sum(slot.state == "missing" for slot in turn.requirement.slots),
            "memory_unavailable_slots": sum(slot.state == "unavailable" for slot in turn.requirement.slots),
        }
        development_flight_recorder().mark(
            "memory_authority_decision",
            turn_id=int(self._active_turn_id or 0),
            authority="v2",
            recent_context_policy=self._v2_recent_context_policy,
            evidence_state=str(turn.requirement.evidence_state),
            absence_kind=str(turn.absence_kind),
            memory_candidate_count=int(turn.candidate_count),
            insufficient_memory_candidate_count=int(
                getattr(turn, "insufficient_candidate_count", 0)
            ),
            recall_anchor_used=bool(getattr(turn, "recall_anchor_used", False)),
            memory_context_characters=len(str(turn.context_block or "")),
            admitted_recent_message_count=int(
                getattr(turn, "recent_message_count", 0)
            ),
            recent_context_characters=int(
                getattr(turn, "recent_character_count", 0)
            ),
            recent_context_approximate_tokens=(
                int(getattr(turn, "recent_character_count", 0)) + 3
            ) // 4,
            retrieval_ms=float(turn.retrieval_latency_ms),
            provider_bypassed=bool(authoritative) or lookup_unavailable,
            memory_authority="v2",
            memory_answer_state=str(turn.requirement.evidence_state),
            provider_called=not (bool(authoritative) or lookup_unavailable),
            v1_prompt_retrieval_entered=False,
            v1_write_path_enabled=False,
            **(decision.diagnostics() if decision is not None else {}),
            **lookup_diagnostics, **slot_counts,
        )
        self._last_memory_authority_diagnostics = {
            "mode": "v2",
            "recent_context_policy": self._v2_recent_context_policy,
            "evidence_state": str(turn.requirement.evidence_state),
            "absence_kind": str(turn.absence_kind),
            "candidate_count": int(turn.candidate_count),
            "insufficient_candidate_count": int(
                getattr(turn, "insufficient_candidate_count", 0)
            ),
            "recall_anchor_used": bool(getattr(turn, "recall_anchor_used", False)),
            "admitted_items": tuple({
                "memory_id": str(item.memory_id),
                "canonical_record_id": str(item.canonical_record_id),
                "lane": str(item.lane),
                "speaker_role": str(item.speaker_role),
                "authority_class": str(item.authority_class),
                "scope_class": str(item.scope_class),
                "rank": int(item.rank),
                "score": float(item.score),
                "source_segments": tuple({"start": segment.start, "end": segment.end,
                                           "source_length": segment.source_length}
                                          for segment in getattr(item, "source_segments", ())),
            } for item in tuple(getattr(getattr(turn, "design", None), "items", ()))),
            "context_characters": len(str(turn.context_block or "")),
            "recent_message_count": int(
                getattr(turn, "recent_message_count", 0)
            ),
            "recent_context_characters": int(
                getattr(turn, "recent_character_count", 0)
            ),
            "retrieval_ms": float(turn.retrieval_latency_ms),
            "provider_bypassed": bool(authoritative) or lookup_unavailable,
            "provider_called": not (bool(authoritative) or lookup_unavailable),
            "memory_query_intent": str(getattr(decision, "intent", "unknown")),
            "requested_relation": str(getattr(decision, "requested_relation", "not_applicable")),
            "requested_speaker": str(getattr(decision, "requested_speaker", "") or "not_applicable"),
            "v1_prompt_retrieval_entered": False,
            "v1_write_path_enabled": False,
            "repair_attempted": False,
            "repair_succeeded": False,
            "repair_skipped_reason": "none",
            "fallback_used": False,
            **lookup_diagnostics, **slot_counts,
        }
        if lookup_unavailable:
            from memory_v2_authority import MemoryV2AuthorityUnavailable
            # Existing recoverable error completion: retain the already saved
            # user evidence, but no assistant record, observer, speech or guess.
            raise MemoryV2AuthorityUnavailable(turn.requirement.fallback_dialogue)
        requirement = turn.requirement
        opportunity = getattr(policy.temporal_facts, "return_opportunity", None)
        if opportunity is not None and opportunity.departure_kind:
            # A bounded exact canonical departure is continuity evidence, not
            # an invitation to retrieve other old details or bypass slot checks.
            from memory_v2_answer_governance import MemoryAnswerEvidence
            from memory_v2_episode_compaction import canonical_record_id
            source = self.conversation.messages[opportunity.departure_index]
            support = MemoryAnswerEvidence(
                canonical_record_id(opportunity.departure_index, source),
                "historical_conversation_only", "user", "active_scope", "assertion", source["content"],
            )
            requirement = replace(requirement, support_ledger=(support, *requirement.support_ledger)[:24])
        return replace(
            policy,
            memory_answer_requirement=requirement,
            memory_authority_context=turn.context_block,
            authoritative_memory_response=authoritative,
            memory_authority_retrieval_ms=turn.retrieval_latency_ms,
            memory_authority_turn=turn,
            memory_realization=self._prepare_companion_memory_core(policy, requirement, authoritative),
            memory_query_decision=decision,
            enforce_before_presentation=(
                policy.enforce_before_presentation
                or turn.requirement.enforce_before_presentation
            ),
        )

    def _apply_v2_retrospective_only_policy(
        self,
        user_message: str,
        policy: _ResponsePolicy,
        *,
        active_truth_scope: object,
        memory_query_decision: Any = None,
    ) -> _ResponsePolicy:
        """Guard a V2-mode provider path that does not request long-term recall.

        Scene/UI and proactive generation have their own narrow authorities and
        must not run hybrid memory retrieval. They still share the complete-
        response retrospective-claim boundary so provider prose cannot create
        an unsupported memory assertion outside the ordinary turn path.
        """
        if self._memory_authority != "v2":
            return policy
        from config import RECENT_CONTEXT_MAX_MESSAGES
        from conversation.truth_scope import (
            active_scope_from_provenance,
            filter_scope_compatible_history,
        )
        from memory_query_decision import decide_memory_query
        from memory_v2_answer_governance import (
            bind_memory_answer_source_containment,
            compose_memory_answer_requirement,
        )
        from memory_v2_source_containment import select_recent_messages

        decision = memory_query_decision or decide_memory_query("")
        recent_source = tuple(getattr(self.conversation, "messages", ()))[
            -RECENT_CONTEXT_MAX_MESSAGES:
        ]
        recent_source = tuple(filter_scope_compatible_history(
            recent_source,
            active_scope_from_provenance(active_truth_scope),
        ))
        selected_recent = select_recent_messages(
            recent_source,
            self._v2_recent_context_policy,
            maximum_messages=12,
            memory_query_decision=decision,
        )
        requirement = compose_memory_answer_requirement(
            user_message, (), memory_query_decision=decision,
        )
        requirement = bind_memory_answer_source_containment(
            requirement,
            user_message,
            selected_recent,
            memory_query_decision=decision,
            current_authoritative_context=policy.context_block or "",
        )
        return replace(
            policy,
            memory_answer_requirement=requirement,
            memory_query_decision=decision,
            enforce_before_presentation=(
                policy.enforce_before_presentation
                or requirement.enforce_before_presentation
            ),
        )

    def _response_policy(self, user_message: str) -> _ResponsePolicy:
        """Preview this exact user evidence without mutating canonical state."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return _ResponsePolicy()
        try:
            from capability_policy import (
                capability_context_block,
                capability_requires_validation,
                preview_capability_effects,
            )
            from conversation.temporal_context import derive_temporal_context_facts
            from memory_v2_store import MemoryV2Repository
            from response_requirements import (
                derive_mutation_response_requirement,
                derive_response_requirement,
            )
            from current_continuity import recent_administrative_scene_clears

            repository = MemoryV2Repository(store)
            preview = preview_capability_effects(
                repository, str(self.character_id), user_message,
                recent_user_turns=self._recent_policy_user_turns(),
                allow_loci=self._memory_authority == "v2",
            )
            temporal = derive_temporal_context_facts(
                getattr(self.conversation, "messages", ()), user_message,
                clock=getattr(self.conversation, "_clock", None),
            )
            user_effects = repository.capability_effects(str(self.character_id), target="user")
            requirement = derive_response_requirement(
                repository, str(self.character_id), user_message,
                local_datetime=temporal.current_local_datetime,
                companion_effects=preview.effects,
                user_effects=user_effects,
            )
            if (preview.extraction is not None
                    and preview.extraction.reason in {"ambiguous_subject_reference", "unsupported_locus_description"}):
                from response_requirements import scene_clarification_requirement
                requirement = scene_clarification_requirement()
            if requirement is None and preview.changed_by_current_evidence:
                requirement = derive_mutation_response_requirement(preview.extraction)
            recent_clears = recent_administrative_scene_clears(
                repository, str(self.character_id),
                now_us=int(temporal.current_local_datetime.timestamp() * 1_000_000),
            )
            hearing_input_unavailable = bool(
                preview.effects.hearing_mode == "unavailable"
                and bool(spoken_text(user_message).strip())
                and not preview.changed_by_current_evidence
                and (
                    requirement is None
                    or requirement.intent not in {"hearing", "hearing_cause", "hearing_comprehension"}
                )
            )
            current_user_projection = (
                "[The user made an inaudible utterance. Its semantic content is not available to you.]"
                if hearing_input_unavailable else None
            )
            inaccessible_terms = ()
            if hearing_input_unavailable:
                stop = {
                    "about", "after", "again", "before", "could", "did", "does", "have",
                    "hello", "hide", "hid", "into", "just", "made", "said", "say", "tell",
                    "that", "their", "there", "these", "they", "this", "under", "what", "where",
                    "which", "with", "would", "you", "your",
                }
                inaccessible_terms = tuple(dict.fromkeys(
                    token for token in re.findall(r"[a-z]{4,}", user_message.casefold())
                    if token not in stop
                ))[:8]
            hard = capability_context_block(preview.effects)
            parts = [hard]
            if requirement is not None:
                parts.append(requirement.context_block)
            if recent_clears:
                parts.append(
                    "[Recent governed clears — backend policy]\n"
                    "These relations are not current. Older dialogue cannot reassert them.\n"
                    + json.dumps(recent_clears, ensure_ascii=False, separators=(",", ":"))
                    + "\n[End recent governed clears]"
                )
            enforce = (
                preview.changed_by_current_evidence
                or capability_requires_validation(preview.effects)
                or requirement is not None
                or bool(recent_clears)
            )
            # Capability modes and direct/mutation requirements are both hard
            # policy lanes. The closed renderers are independently bounded;
            # never truncate the latter merely because the former is rich.
            context = "\n".join(parts) if enforce else None
            return _ResponsePolicy(
                preview.effects, requirement, context, enforce,
                preview.changed_by_current_evidence,
                recent_administrative_clears=recent_clears,
                hearing_input_unavailable=hearing_input_unavailable,
                current_user_projection=current_user_projection,
                inaccessible_input_terms=inaccessible_terms,
            )
        except Exception as error:
            # A present authority that cannot be projected is not equivalent
            # to unrestricted state. Keep this one turn behind a quiet,
            # validate-before-presentation boundary.
            return _ResponsePolicy(
                context_block=(
                    "[Capability policy unavailable — backend policy]\n"
                    "Respond nonverbally and do not claim a capability or state-changing action.\n"
                    "[End capability policy unavailable]"
                ),
                enforce_before_presentation=True,
                policy_error=type(error).__name__,
            )

    def _response_policy_for_scene_ui_event(self, event: object) -> _ResponsePolicy:
        """Build one post-mutation envelope without reparsing generated prose."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return _ResponsePolicy(
                enforce_before_presentation=True,
                policy_error="scene_authority_unavailable",
            )
        try:
            from capability_policy import capability_context_block
            from conversation.temporal_context import derive_temporal_context_facts
            from current_continuity import recent_administrative_scene_clears
            from memory_v2_store import MemoryV2Repository
            from scene_ui_event import scene_ui_response_requirement

            repository = MemoryV2Repository(store)
            effects = repository.capability_effects(str(self.character_id))
            requirement = scene_ui_response_requirement(event)
            temporal = derive_temporal_context_facts(
                getattr(self.conversation, "messages", ()), event.model_text,
                clock=getattr(self.conversation, "_clock", None),
            )
            recent_clears = recent_administrative_scene_clears(
                repository, str(self.character_id),
                now_us=int(temporal.current_local_datetime.timestamp() * 1_000_000),
            )
            parts = [capability_context_block(effects), requirement.context_block]
            if recent_clears:
                parts.append(
                    "[Recent backend clears — authoritative current-state policy]\n"
                    "These relations are not current. Older dialogue cannot reassert them.\n"
                    + json.dumps(recent_clears, ensure_ascii=False, separators=(",", ":"))
                    + "\n[End recent backend clears]"
                )
            return _ResponsePolicy(
                effects=effects, requirement=requirement,
                context_block="\n".join(parts), enforce_before_presentation=True,
                changed_by_current_evidence=False,
                recent_administrative_clears=recent_clears,
            )
        except Exception as error:
            return _ResponsePolicy(
                context_block=(
                    "[Scene interaction policy unavailable — backend policy]\n"
                    "Respond nonverbally and do not claim current scene facts.\n"
                    "[End scene interaction policy unavailable]"
                ),
                enforce_before_presentation=True,
                policy_error=type(error).__name__,
            )

    def _recent_policy_user_turns(self) -> tuple[object, ...]:
        """Build the same bounded USER-only correction window before save."""
        from continuity_reference import (
            MAX_RECENT_USER_CHARS,
            MAX_RECENT_USER_GAP_US,
            MAX_RECENT_USER_TURNS,
            RecentUserTurn,
        )
        from memory_v2_store.store import parse_timestamp_us

        messages = getattr(self.conversation, "messages", ())
        if not isinstance(messages, list) or not messages:
            return ()
        current = messages[-1]
        if not isinstance(current, dict) or current.get("role") != "user":
            return ()
        provenance = current.get("truth_scope")
        newest_us = parse_timestamp_us(current.get("timestamp"))
        if not isinstance(provenance, dict) or newest_us is None:
            return ()
        scope_id = provenance.get("scope_id")
        scope_kind = provenance.get("kind")
        if not isinstance(scope_id, str) or scope_kind not in {"real_world", "scenario"}:
            return ()
        selected: list[RecentUserTurn] = []
        total_chars = 0
        for index in range(len(messages) - 2, -1, -1):
            record = messages[index]
            if not isinstance(record, dict) or record.get("role") != "user":
                continue
            prior_scope = record.get("truth_scope")
            if (not isinstance(prior_scope, dict)
                    or prior_scope.get("scope_id") != scope_id
                    or prior_scope.get("kind") != scope_kind):
                break
            content = record.get("content")
            recorded_at_us = parse_timestamp_us(record.get("timestamp"))
            if (not isinstance(content, str) or not content or len(content) > 500
                    or recorded_at_us is None or recorded_at_us > newest_us
                    or newest_us - recorded_at_us > MAX_RECENT_USER_GAP_US):
                break
            if (len(selected) >= MAX_RECENT_USER_TURNS
                    or total_chars + len(content) > MAX_RECENT_USER_CHARS):
                break
            selected.append(RecentUserTurn(index, content))
            total_chars += len(content)
            newest_us = recorded_at_us
        return tuple(reversed(selected))

    def _plan_companion_action(
        self,
        user_message: str,
        policy: _ResponsePolicy,
    ) -> _ResponsePolicy:
        """Run an optional separate decision only for explicit autonomy turns."""
        from companion_action import (
            companion_action_decision_prompt,
            companion_action_relevant,
            current_companion_action_subject_labels,
            validate_companion_action_decision,
        )

        if getattr(policy.requirement, "intent", None) == "scene_clarification":
            return policy
        if not companion_action_relevant(user_message) or policy.effects is None:
            return policy
        if policy.changed_by_current_evidence:
            context = "\n".join(item for item in (
                policy.context_block,
                "[Companion action decision — backend policy]\n"
                "Current explicit user evidence takes precedence, so no autonomous companion action "
                "is authorized in this turn. Keep companion_action null.\n"
                "[End companion action decision]",
            ) if item)[:2800]
            return replace(
                policy,
                context_block=context,
                enforce_before_presentation=True,
                action_decision_category="action_explicit_user_state_precedence",
            )
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return replace(
                policy, enforce_before_presentation=True,
                action_decision_category="action_store_unavailable",
            )
        proposal = ""
        try:
            from memory_v2_store import MemoryV2Repository
            prompt = (
                "You are making one bounded companion-action decision.\n\n"
                + response_contract_prompt() + "\n\n"
                + companion_action_decision_prompt(
                    user_message, policy.effects,
                    character_context=self._bounded_sleep_character_context(),
                    current_scene_subjects=current_companion_action_subject_labels(
                        MemoryV2Repository(store), str(self.character_id),
                    ),
                )
            )
            self._provider_request_begin()
            bounded = getattr(self.llm, "generate_bounded", None)
            # The closed prompt already carries the exact canonical request as
            # inert data. Repeating it as the final chat message invites some
            # local models to answer conversationally instead of performing
            # the bounded decision.
            context = []
            if callable(bounded):
                proposal = bounded(context, prompt, max_output_tokens=120)
            else:
                proposal = self.llm.generate(context, prompt)
        except Exception:
            proposal = ""
        finally:
            self._provider_request_end()
        parsed = parse_assistant_response(canonicalize_model_output(proposal))
        try:
            from memory_v2_store import MemoryV2Repository
            decision = validate_companion_action_decision(
                parsed, MemoryV2Repository(store), str(self.character_id), policy.effects,
            )
        except Exception:
            decision = None
        accepted = bool(decision is not None and decision.accepted and decision.plan is not None)
        category = decision.category if decision is not None else "action_validation_error"
        if accepted:
            action_context = decision.plan.context_block
            plan = decision.plan
        else:
            action_context = (
                "[Companion action decision — backend policy]\n"
                "No autonomous companion action was authorized for this turn. Do not narrate a new action as "
                "successfully completed and keep companion_action null.\n"
                "[End companion action decision]"
            )
            plan = None
        context = "\n".join(
            item for item in (policy.context_block, action_context) if item
        )
        development_flight_recorder().mark(
            "companion_action_validation", turn_id=int(self._active_turn_id or 0),
            parse_success=parsed.contract_status == "valid",
            accepted_generated=accepted, category=category,
            outcome="accepted" if accepted else "abstained" if category == "action_abstained" else "rejected",
        )
        return replace(
            policy, context_block=context, enforce_before_presentation=True,
            action_plan=plan, action_decision_category=category,
        )

    def _apply_companion_action_plan(
        self,
        plan: object,
        turn_id: int,
        canonical_user_message: object,
    ) -> dict[str, Any]:
        """Cross the authoritative action boundary before exposing narration."""
        applier = getattr(
            self._memory_v2_shadow_writer, "apply_governed_companion_action", None,
        )
        if not callable(applier):
            return {"state": "unavailable", "reason": "action_applier_unavailable"}
        recorded_at_us = int(time.time() * 1_000_000)
        if isinstance(canonical_user_message, dict):
            try:
                from memory_v2_store.store import parse_timestamp_us
                canonical_us = parse_timestamp_us(canonical_user_message.get("timestamp"))
                if canonical_us is not None:
                    recorded_at_us = canonical_us + 1
            except (TypeError, ValueError):
                pass
        return applier(
            plan,
            decision_reference=f"turn-{int(turn_id)}-{uuid.uuid4().hex}",
            recorded_at_us=recorded_at_us,
        )

    @staticmethod
    def _merge_response_policy_context(
        active_state_context: str | None,
        response_policy_context: str | None,
        *, preserve_whole: bool = False,
    ) -> str | None:
        if not response_policy_context:
            return active_state_context
        if not active_state_context:
            return response_policy_context
        if preserve_whole:
            return response_policy_context + "\n" + active_state_context
        remaining = max(0, 2800 - len(response_policy_context) - 1)
        return response_policy_context + "\n" + active_state_context[:remaining]

    def _assemble_memory_answer_response(
        self, parsed: ParsedAssistantResponse, policy: _ResponsePolicy,
    ) -> ParsedAssistantResponse:
        from memory_v2_answer_governance import assemble_memory_answer_dialogue

        requirement = policy.memory_answer_requirement
        if policy.memory_realized_response is not None or requirement is None or not requirement.slots:
            return parsed
        dialogue = assemble_memory_answer_dialogue(requirement, parsed.dialogue)
        # Normal speech follows the assembled canonical answer. A separate
        # provider speech field cannot omit unknowns or add a guessed value.
        # An explicitly nonspoken caption remains nonspoken.
        spoken = parsed.spoken_content
        if spoken and (policy.effects is None or policy.effects.speech_mode == "normal"):
            spoken = None
        return replace(parsed, dialogue=dialogue, spoken_content=spoken)

    def _validate_governed_response(
        self,
        parsed: ParsedAssistantResponse,
        policy: _ResponsePolicy,
    ) -> tuple[bool, str, str, ResponsePresentationMetadata | None]:
        """Validate canonical, spoken, and semantic output before exposure."""
        spoken = parsed.spoken_content if parsed.spoken_content is not None else self.clean_text_for_tts(parsed.dialogue)
        presentation = parsed.presentation
        if policy.policy_error is not None:
            return False, "capability_policy_unavailable", "", None
        if (policy.enforce_before_presentation
                and parsed.contract_status not in {"valid", "plain_text"}):
            return False, "response_contract", "", None
        if policy.temporal_facts is not None:
            from conversation.temporal_context import temporal_activity_duration_invented
            if temporal_activity_duration_invented(policy.temporal_facts, parsed.dialogue):
                return False, "unsupported_activity_duration", "", None
        constrained_length = bool(
            policy.effects is not None and (
                policy.effects.awareness_mode == "asleep"
                or policy.effects.speech_mode != "normal"
            )
        )
        if constrained_length and len(re.findall(r"\b[\w’'-]+\b", parsed.dialogue)) > 100:
            return False, "response_length", "", None
        if policy.effects is not None:
            from capability_policy import validate_capability_response
            capability = validate_capability_response(parsed, policy.effects)
            if not capability.accepted:
                return False, capability.category, "", None
            spoken = capability.spoken_text
            presentation = capability.presentation
        if (policy.hearing_input_unavailable
                and not _hearing_unavailable_input_response_valid(
                    parsed.dialogue, policy.inaccessible_input_terms,
                )):
            return False, "hearing_input_semantics", "", None
        if (policy.recent_administrative_clears
                and _reasserts_recent_administrative_clear(
                    parsed.dialogue, policy.recent_administrative_clears,
                )):
            return False, "recent_clear_reasserted", "", None
        if policy.requirement is not None:
            from response_requirements import validate_response_requirement
            required = validate_response_requirement(policy.requirement, parsed.dialogue)
            if not required.accepted:
                return False, required.category, "", None
        if policy.memory_answer_requirement is not None:
            from memory_v2_answer_governance import validate_memory_answer_response
            memory_dialogue = parsed.dialogue
            if policy.memory_realized_response is not None:
                from companion_memory_realizer import owned_reaction_valid
                owned = policy.memory_realized_response
                if (parsed.dialogue != owned.dialogue
                        or not owned_reaction_valid(owned)):
                    return False, "memory_realization_ownership", "", None
                memory_dialogue = owned.core.dialogue
            memory_answer = validate_memory_answer_response(
                policy.memory_answer_requirement, memory_dialogue,
            )
            self._last_memory_authority_diagnostics.update({
                "spontaneous_retrospective_claim_count": max(
                    int(self._last_memory_authority_diagnostics.get(
                        "spontaneous_retrospective_claim_count", 0,
                    )),
                    int(memory_answer.retrospective_claim_count),
                ),
                "unsupported_retrospective_claim_count": max(
                    int(self._last_memory_authority_diagnostics.get(
                        "unsupported_retrospective_claim_count", 0,
                    )),
                    int(memory_answer.unsupported_retrospective_claim_count),
                ),
            })
            development_flight_recorder().mark(
                "memory_response_governance",
                turn_id=int(self._active_turn_id or 0),
                retrospective_claim_count=int(memory_answer.retrospective_claim_count),
                unsupported_retrospective_claim_count=int(
                    memory_answer.unsupported_retrospective_claim_count
                ),
                spontaneous_retrospective_claims_detected=(
                    memory_answer.retrospective_claim_count > 0
                ),
                unsupported_retrospective_claims_rejected=(
                    memory_answer.unsupported_retrospective_claim_count > 0
                    and not memory_answer.accepted
                ),
                succeeded=bool(memory_answer.accepted),
                category=str(memory_answer.category),
            )
            if not memory_answer.accepted:
                return False, f"memory_answer_{memory_answer.category}", "", None
        if policy.action_decision_category is not None:
            from companion_action import action_narration_valid, unauthorized_action_narrated
            if not action_narration_valid(policy.action_plan, parsed):
                return False, "companion_action_narration", "", None
            if policy.action_plan is None and unauthorized_action_narrated(parsed):
                return False, "rejected_action_narrated", "", None
        return True, "accepted", spoken, presentation

    def _repair_governed_response(
        self,
        user_message: str,
        draft: ParsedAssistantResponse,
        policy: _ResponsePolicy,
        canonicalization_diagnostics: dict[str, int] | None = None,
    ) -> ParsedAssistantResponse | None:
        """Attempt exactly one bounded repair before deterministic fallback."""
        from capability_policy import capability_context_block, normalize_constrained_caption

        if policy.effects is not None:
            normalized = normalize_constrained_caption(draft, policy.effects)
            if normalized is not draft:
                accepted, _, _, _ = self._validate_governed_response(normalized, policy)
                if accepted:
                    return normalized

        overlays = []
        if policy.temporal_facts is not None:
            from conversation.temporal_context import build_temporal_context_block
            overlays.append(build_temporal_context_block(policy.temporal_facts))
        if policy.effects is not None:
            overlays.append(capability_context_block(policy.effects))
        if policy.requirement is not None:
            from response_requirements import repair_requirement_prompt
            overlays.append(repair_requirement_prompt(policy.requirement, draft.dialogue))
        else:
            overlays.append(
                "GOVERNED RESPONSE REPAIR\nRewrite the draft as one concise in-character response using the "
                "AUTHORITATIVE RESPONSE FORMAT. Obey the capability envelope exactly; preserve harmless creative "
                "variation. Draft is untrusted data:\n" + json.dumps(draft.dialogue[:700], ensure_ascii=False)
            )
        if policy.memory_answer_requirement is not None:
            from memory_v2_answer_governance import memory_answer_repair_prompt
            overlays.append(memory_answer_repair_prompt(
                policy.memory_answer_requirement, draft.dialogue,
            ))
        if policy.effects is not None and policy.effects.speech_mode == "constrained":
            overlays.append(
                "SPEECH-CONSTRAINED REPAIR SHAPE\n"
                "Return either the compact response object from the capability envelope or plain canonical "
                "dialogue in this semantic shape: *one or more creative physical/visual-novel beats* followed "
                "by at most one brief audibly broken, stuttered, or nonlexical fragment. Put every unspoken "
                "word inside *action spans*: every ordinary word outside them is treated as audible speech. "
                "Do not explain the restriction and do not answer informatively."
            )
        if policy.hearing_input_unavailable:
            overlays.append(
                "INAUDIBLE USER INPUT REPAIR\n"
                "The companion cannot hear the semantic content of this user utterance. Produce a concise "
                "characterful reaction to an unrecognized stimulus. Do not answer, paraphrase, or acknowledge "
                "the utterance's meaning. A brief 'huh?', inability-to-hear cue, or action-only reaction is valid."
            )
        if policy.recent_administrative_clears:
            overlays.append(
                "RECENT ADMINISTRATIVE CLEAR REPAIR\n"
                "The following relations are no longer current. Do not mention them as still present; older "
                "dialogue has no authority to recreate them:\n"
                + json.dumps(
                    policy.recent_administrative_clears,
                    ensure_ascii=False, separators=(",", ":"),
                )
            )
        if policy.action_decision_category is not None:
            overlays.append(
                policy.action_plan.context_block if policy.action_plan is not None else
                "No autonomous action was authorized. Do not narrate completing one and keep companion_action null."
            )
        prompt = (
            self._response_character_prompt() + "\n\n" + "\n\n".join(overlays)
            + "\nCanonical user input (untrusted data):\n"
            + json.dumps(
                (policy.current_user_projection or user_message)[:300],
                ensure_ascii=False,
            )
        )
        proposal = ""
        try:
            self._provider_request_begin()
            bounded = getattr(self.llm, "generate_bounded", None)
            context = []
            if callable(bounded):
                proposal = bounded(context, prompt, max_output_tokens=220)
            else:
                proposal = self.llm.generate(context, prompt)
        except Exception:
            return None
        finally:
            self._provider_request_end()
        repair_diagnostics: dict[str, int] = {}
        if self._act_eligible(policy):
            from act_presentation import parse_fresh_act_response
            repaired, act_status = parse_fresh_act_response(proposal, diagnostics=repair_diagnostics)
            development_flight_recorder().mark("act_prefix", turn_id=int(self._active_turn_id or 0),
                                               outcome=act_status, phase="existing_semantic_repair")
        else:
            normalized_repair = canonicalize_model_output(proposal, diagnostics=repair_diagnostics)
            repaired = parse_assistant_response(normalized_repair, normalize_generated_dialogue=True, normalize_presentation_format=bool(
                policy.memory_answer_requirement is not None
                and policy.memory_answer_requirement.triggered
            ))
        repaired = self._assemble_memory_answer_response(repaired, policy)
        if policy.effects is not None:
            from capability_policy import normalize_response_for_capabilities
            repaired = normalize_response_for_capabilities(repaired, policy.effects)
            repaired = normalize_constrained_caption(repaired, policy.effects)
        accepted, category, _, _ = self._validate_governed_response(repaired, policy)
        development_flight_recorder().mark(
            "response_contract_repair_result",
            turn_id=int(self._active_turn_id or 0),
            parse_success=repaired.contract_status == "valid",
            minimal_plain_text=repaired.contract_status == "plain_text",
            parse_failure=(
                str(repaired.failure_category or repaired.contract_status)
                if repaired.contract_status not in {"valid", "plain_text"} else "none"
            ),
            semantic_rejection=(
                category
                if repaired.contract_status in {"valid", "plain_text"} and not accepted else "none"
            ),
            succeeded=accepted,
            category=category,
        )
        if accepted and canonicalization_diagnostics is not None:
            canonicalization_diagnostics.clear()
            canonicalization_diagnostics.update(repair_diagnostics)
        return repaired if accepted else None

    def _governed_fallback_response(self, policy: _ResponsePolicy) -> ParsedAssistantResponse:
        """Exceptional deterministic response consistent with the shared envelope."""
        effects = policy.effects
        if policy.policy_error is not None:
            return ParsedAssistantResponse(
                dialogue="*Remains quietly attentive.*",
                presentation=ResponsePresentationMetadata(speech_mode="nonverbal"),
                has_presentation_contract=True,
                response_mode="nonverbal_reaction",
                spoken_content="",
                contract_status="valid",
            )
        if policy.action_plan is not None:
            from companion_action import action_fallback_dialogue
            dialogue = action_fallback_dialogue(policy.action_plan)
        else:
            dialogue = (
                policy.memory_answer_requirement.fallback_dialogue
                if (policy.memory_answer_requirement is not None
                    and policy.memory_answer_requirement.triggered
                    and policy.memory_answer_requirement.fallback_dialogue.strip()) else
                policy.requirement.fallback_dialogue
                if policy.requirement is not None else
                "*Responds with a small, attentive movement.*"
            )
        mode = "normal_conversation"
        spoken = self.clean_text_for_tts(dialogue)
        presentation = ResponsePresentationMetadata()
        if effects is not None and effects.awareness_mode == "asleep":
            dialogue = "*Remains asleep, shifting slightly before settling again.*"
            spoken = ""
            mode = "sleep_reaction"
            presentation = ResponsePresentationMetadata(
                emotion="relaxed", pose="sleeping", gaze_mode="suppressed",
                reaction="settle", speech_mode="nonverbal",
            )
        elif effects is not None and effects.speech_mode == "unavailable":
            # A scene caption can still communicate a deterministic required
            # fact without inventing speech or feeding it to TTS/lip sync.
            if spoken:
                dialogue = "*Communicates without speaking: " + dialogue.strip().strip("*") + "*"
            spoken = ""
            mode = "nonverbal_reaction"
            presentation = ResponsePresentationMetadata(speech_mode="nonverbal")
        elif effects is not None and effects.speech_mode == "constrained":
            # Preserve the deterministic fact or action as canonical scene
            # text, while the actual audio remains a short constrained sound.
            # This keeps clear ordinary speech from leaking through TTS.
            if spoken:
                visual_fact = dialogue.strip().strip("*")
                dialogue = f"*Communicates with a muffled gesture: {visual_fact}*"
            spoken = "Mmph."
            mode = "speech_constrained"
            presentation = ResponsePresentationMetadata(speech_mode="constrained")
        if effects is not None and not effects.vision_available:
            presentation = ResponsePresentationMetadata(
                emotion=presentation.emotion, intensity=presentation.intensity,
                gesture=presentation.gesture, pose=presentation.pose,
                gaze_mode="suppressed", reaction=presentation.reaction,
                speech_mode=presentation.speech_mode,
            )
        return ParsedAssistantResponse(
            dialogue=dialogue, presentation=presentation,
            has_presentation_contract=True, response_mode=mode,
            spoken_content=spoken, contract_status="valid",
        )

    def interaction_policy(self, user_message: str = ""):
        """Read the governed state-to-behavior policy, failing open if V2 is absent."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return None
        try:
            from interaction_policy import classify_interaction_policy
            from memory_v2_store import MemoryV2Repository
            return classify_interaction_policy(
                MemoryV2Repository(store), str(self.character_id), user_message,
            )
        except Exception:
            return None

    def _generate_sleep_reaction(self, decision, user_message: str):
        """Render one bounded policy response without opening normal dialogue."""
        from interaction_policy import render_sleep_reaction, sleep_reaction_prompt

        character_context = self._bounded_sleep_character_context()
        capability_state: dict[str, object] = {}
        capability_effects = None
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is not None and self.character_id:
            try:
                from memory_v2_store import MemoryV2Repository
                from capability_policy import preview_capability_effects
                capability_effects = preview_capability_effects(
                    MemoryV2Repository(store), str(self.character_id), user_message,
                    recent_user_turns=self._recent_policy_user_turns(),
                ).effects
                capability_state = capability_effects.prompt_payload()
            except Exception:
                capability_state = {}
        recent_signatures = tuple(self._recent_sleep_reaction_signatures)
        proposal = ""
        constrained_system_prompt = ""
        if getattr(self.llm, "is_available", True) is not False:
            prompt = sleep_reaction_prompt(
                decision, user_message, character_context=character_context,
                capability_state=capability_state, recent_signatures=recent_signatures,
            )
            constrained_system_prompt = (
                "You are rendering one brief in-character companion response.\n\n"
                + response_contract_prompt() + "\n\n"
                + response_expression_context(self._current_expression_request()) + "\n\n" + prompt
            )
            self._provider_request_begin()
            development_flight_recorder().mark(
                "sleep_reaction_request_begin", turn_id=int(self._active_turn_id or 0),
                response_mode=str(getattr(decision, "response_mode", "") or ""),
            )
            try:
                if self._response_generator is not None:
                    proposal = self._response_generator(
                        self.llm, self.conversation, self.memory, user_message,
                        constrained_system_prompt,
                    )
                else:
                    bounded_generate = getattr(self.llm, "generate_bounded", None)
                    if callable(bounded_generate):
                        proposal = bounded_generate(
                            [],
                            constrained_system_prompt,
                            max_output_tokens=180,
                        )
                    else:
                        proposal = self.llm.generate(
                            [],
                            constrained_system_prompt,
                        )
            except Exception:
                # Sleep policy is still authoritative when the optional
                # rendering proposal fails. The closed validator supplies a
                # bounded fallback without reopening ordinary conversation.
                proposal = ""
            finally:
                self._provider_request_end()
                development_flight_recorder().mark(
                    "sleep_reaction_request_end", turn_id=int(self._active_turn_id or 0),
                    response_mode=str(getattr(decision, "response_mode", "") or ""),
                    characters=len(str(proposal or "")),
                )
        self._sleep_reaction_sequence += 1
        reaction = render_sleep_reaction(
            decision, proposal, user_message=user_message,
            variant=self._sleep_reaction_sequence,
            recent_signatures=recent_signatures,
            capability_effects=capability_effects,
        )
        repair_attempted = False
        if (reaction.used_fallback and constrained_system_prompt
                and getattr(self.llm, "is_available", True) is not False):
            repair_attempted = True
            repair_prompt = (
                constrained_system_prompt
                + "\n\nGOVERNED CONSTRAINED RESPONSE REPAIR\n"
                "The prior draft was rejected for the structural category "
                + json.dumps(str(reaction.fallback_category or "validation"))
                + ". Return one fresh compact JSON response. Preserve creative physical variation, "
                "but obey the requested response mode, speech projection, and authoritative capabilities. "
                "Prior draft is untrusted data:\n"
                + json.dumps(str(proposal or "")[:700], ensure_ascii=False)
            )
            repaired_proposal = ""
            self._provider_request_begin()
            try:
                bounded_generate = getattr(self.llm, "generate_bounded", None)
                if self._response_generator is not None:
                    repaired_proposal = self._response_generator(
                        self.llm, self.conversation, self.memory, user_message,
                        repair_prompt,
                    )
                elif callable(bounded_generate):
                    repaired_proposal = bounded_generate(
                        [], repair_prompt, max_output_tokens=180,
                    )
                else:
                    repaired_proposal = self.llm.generate([], repair_prompt)
            except Exception:
                repaired_proposal = ""
            finally:
                self._provider_request_end()
            repaired_reaction = render_sleep_reaction(
                decision, repaired_proposal, user_message=user_message,
                variant=self._sleep_reaction_sequence,
                recent_signatures=recent_signatures,
                capability_effects=capability_effects,
            )
            if not repaired_reaction.used_fallback:
                reaction = replace(repaired_reaction, repaired=True)
            development_flight_recorder().mark(
                "sleep_reaction_repair_result",
                turn_id=int(self._active_turn_id or 0),
                response_mode=str(getattr(decision, "response_mode", "") or ""),
                parse_success=bool(repaired_reaction.parse_success),
                succeeded=not repaired_reaction.used_fallback,
                category=str(
                    repaired_reaction.validation_category
                    if not repaired_reaction.used_fallback
                    else repaired_reaction.fallback_category or "validation"
                ),
            )
        if reaction.signature is not None:
            self._recent_sleep_reaction_signatures.append(reaction.signature)
        development_flight_recorder().mark(
            "sleep_reaction_validation",
            turn_id=int(self._active_turn_id or 0),
            response_mode=str(getattr(decision, "response_mode", "") or ""),
            parse_success=bool(reaction.parse_success),
            accepted_generated=not reaction.used_fallback,
            retry_scheduled=repair_attempted,
            fallback_used=bool(reaction.used_fallback),
            category=str(reaction.validation_category),
            fallback_category=str(reaction.fallback_category or "none"),
            reaction=str(reaction.presentation.reaction or "none"),
            speech_mode=str(reaction.presentation.speech_mode or "none"),
            movement_family=(reaction.signature.movement_family if reaction.signature else "none"),
        )
        return reaction

    def _bounded_sleep_character_context(self) -> str:
        """Return only bounded identity/style context, never the full system prompt."""
        fields = []
        for key in ("name", "description"):
            value = self.character.get(key)
            if isinstance(value, str) and value.strip():
                fields.append(f"{key}: {' '.join(value.split())}")
        marker = "CHARACTER PERSONALITY:"
        end_marker = "CHARACTER CONSISTENCY:"
        if marker in self.character_prompt:
            personality = self.character_prompt.split(marker, 1)[1]
            if end_marker in personality:
                personality = personality.split(end_marker, 1)[0]
            compact = " ".join(personality.split())
            if compact:
                fields.append("personality: " + compact)
        return "\n".join(fields)[:900]

    def _current_expression_request(self) -> ResponsePresentationMetadata | None:
        # Turn generation/publication and character rebinding already serialize
        # under _turn_lock. No provider/synthesis work or new lock belongs here.
        scope = self.truth_scope_provenance() or {}
        owner = (self.character_id, scope.get("kind"), scope.get("scope_id"))
        if owner != self._published_expression_owner:
            self._published_expression = None
            self._published_expression_owner = owner
        return self._published_expression

    def _lean_ordinary_eligible(self, policy: _ResponsePolicy | None) -> bool:
        return (getattr(self.llm, "local_ordinary_dialogue", False) is True
                and self._ordinary_text_eligible(policy))

    def _act_eligible(self, policy: _ResponsePolicy | None) -> bool:
        from act_presentation import act_character_prompt
        return (self.explicit_avatar_cues is True
                and getattr(self.llm, "local_presentation", False) is True
                and self._ordinary_text_eligible(policy)
                and act_character_prompt(self.character_prompt) is not None)

    def _ordinary_text_eligible(self, policy: _ResponsePolicy | None, *,
                                allow_completed_state_update: bool = False) -> bool:
        """Output style only. A validation gate is not a format obligation.

        The caller supplies the final ordinary-turn policy, after state/action,
        temporal and memory routing. Other generation owners never opt in.
        """
        if (self._response_generator is not None or self._memory_authority != "v2"
                or not isinstance(policy, _ResponsePolicy)):
            return False
        from memory_query_decision import MemoryQueryDecision
        from memory_v2_store.scene_relation_contract import CapabilityEffects
        from response_requirements import ResponseRequirement
        decision = policy.memory_query_decision
        if not isinstance(decision, MemoryQueryDecision) or decision.applicable:
            return False
        if (policy.policy_error is not None or policy.action_plan is not None
                or policy.action_decision_category is not None
                or (policy.changed_by_current_evidence and not allow_completed_state_update)
                or policy.hearing_input_unavailable
                or policy.current_user_projection is not None or policy.inaccessible_input_terms
                or policy.recent_administrative_clears
                or policy.authoritative_memory_response is not None
                or policy.memory_realization is not None or policy.memory_realized_response is not None):
            return False
        requirement = policy.requirement
        # A greeting after a gap may retain its existing nonfactual respect
        # constraint. All factual, constrained or unknown requirements stay out.
        if requirement is not None and not (
                isinstance(requirement, ResponseRequirement)
                and requirement.intent == "return_continuity"
                and requirement.mode == "must_respect" and not requirement.facts):
            return False
        from memory_v2_answer_governance import MemoryAnswerRequirement
        memory = policy.memory_answer_requirement
        if not isinstance(memory, MemoryAnswerRequirement) or memory.triggered or memory.slots:
            return False
        effects = policy.effects
        if not isinstance(effects, CapabilityEffects):
            return False
        return (all(effects.perception_mode(name) == "normal"
                    for name in ("vision", "hearing", "smell", "taste", "touch"))
                and effects.speech_mode == "normal" and effects.awareness_mode == "normal"
                and effects.hands_mode == effects.left_hand_mode == effects.right_hand_mode == "free"
                and effects.left_arm_mode == effects.right_arm_mode == "normal"
                and effects.locomotion_mode == "walking" and effects.locomotion_constraint == "normal"
                and effects.posture_mode in {None, "sitting", "standing"})

    def _response_character_prompt(self, *, ordinary_policy: _ResponsePolicy | None = None) -> str:
        delivery = {"source": "natural" if self.conversation_style == "natural" else "roleplay",
                    "state": "not_applied",
                    "reason": "roleplay_selected"}
        if self.conversation_style == "natural":
            local = getattr(self.llm, "local_presentation", False) is True
            eligible = self._ordinary_text_eligible(
                ordinary_policy, allow_completed_state_update=True)
            delivery["reason"] = "local_provider_required" if not local else "structured_obligation"
            if local and eligible:
                # A completed backend-owned update alone is not a request for
                # structured model output. Keep every actual machine/memory/
                # capability obligation in the shared gate; ACT/lean retain
                # their stricter no-mutation eligibility.
                from conversation_style import natural_character_prompt
                prompt = natural_character_prompt(self.character_prompt, act=self._act_eligible(ordinary_policy))
                if prompt is not None:
                    self._record_delivery_selection({**delivery, "state": "applied", "reason": "ordinary_local"})
                    return prompt
                delivery["reason"] = "unrecognized_prompt"
        self._record_delivery_selection(delivery)
        if self._act_eligible(ordinary_policy):
            from act_presentation import act_character_prompt
            return act_character_prompt(self.character_prompt) + "\n\n" + response_expression_context(
                self._current_expression_request())
        if self._lean_ordinary_eligible(ordinary_policy):
            from presentation_metadata import lean_ordinary_character_prompt
            prompt = lean_ordinary_character_prompt(self.character_prompt)
            if prompt is not None:
                return prompt
        return self.character_prompt + "\n\n" + response_expression_context(
            self._current_expression_request(),
        )

    def _record_delivery_selection(self, diagnostics: dict[str, str]) -> None:
        # Closed application labels only. Selecting a preference is distinct
        # from applying it; custom/structured paths must not pretend otherwise.
        self._last_delivery_diagnostics = diagnostics
        development_flight_recorder().mark("conversation_delivery", **diagnostics)

    def _context_prompt_parts(self, ordinary_policy, companion_context):
        from context_governor import ContextPlanItem, governor_enabled
        prompt = self._response_character_prompt(ordinary_policy=ordinary_policy)
        if not governor_enabled(self._memory_authority):
            return prompt + ("\n\n" + companion_context if companion_context else ""), ()
        expression = response_expression_context(self._current_expression_request())
        optional = []
        if prompt.endswith("\n\n" + expression):
            prompt = prompt[:-(len(expression) + 2)]
            optional.append(ContextPlanItem.block("presentation", "expression_continuity", expression,
                                                 authority="optional", order=80))
        if companion_context:
            optional.append(ContextPlanItem.block("companion_context", "salience", companion_context,
                                                 authority="optional", order=81))
        return prompt, tuple(optional)

    def _record_context_plan(self):
        plan = getattr(self.conversation, "_last_context_plan", None)
        if plan is None:
            return
        # No raw text, source hashes or IDs are emitted by normal diagnostics.
        development_flight_recorder().mark("context_plan", turn_id=int(self._active_turn_id or 0),
            **plan.diagnostics)
        for item in plan.diagnostics.get("items", ()):
            development_flight_recorder().mark("context_plan_item", turn_id=int(self._active_turn_id or 0),
                context_owner=item["owner"], context_kind=item["kind"],
                estimated_tokens=item["estimated_tokens"], source_count=item["source_count"])
        for item in plan.diagnostics.get("dropped", ()):
            development_flight_recorder().mark("context_plan_drop", turn_id=int(self._active_turn_id or 0),
                context_owner=item["owner"], context_kind=item["kind"], reason=item["reason"])
        if self._impulse_lease is not None and "companion_context" not in plan.diagnostics["included_owners"]:
            self._release_impulse()  # A budget-excluded opportunity was never offered.

    def _ordinary_companion_context(self, user_message, *, active_truth_scope=None,
                                   memory_answer_requirement=None, memory_query_decision=None,
                                   memory_realization=None) -> str:
        """Salience only; never passed into authority, requirements or realization.

        This is called only by ordinary response assembly, not action decisions,
        repair, proactive/scene reactions, observer work or canonical persistence.
        """
        from companion_context import CompanionContextAssembler, CompanionContextRequest
        from conversation.temporal_context import clock_local_datetime
        from memory_query_decision import decide_memory_query
        decision = memory_query_decision or decide_memory_query(user_message)
        if (self._memory_authority != "v2" or decision.applicable
                or bool(getattr(memory_answer_requirement, "triggered", False))
                or memory_realization is not None):
            self._last_companion_context_diagnostics = {"suppressed": "authority_query", "emitted_characters": 0}
            return ""
        if not self._recent_pulse_enabled and not self._companion_context_sources and self._transient_impulse_store is None:
            self._last_companion_context_diagnostics = {"suppressed": "disabled", "emitted_characters": 0}
            return ""
        scope = active_truth_scope or self.truth_scope_provenance()
        context_turn = self._impulse_turn_owner
        turn_key = (f"{self._impulse_session}:{context_turn[0]}" if context_turn is not None
                    else f"{self._active_turn_id}:{len(self.conversation.messages)}")
        request = CompanionContextRequest(str(self.character_id), str((scope or {}).get("scope_id") or ""),
            turn_key,
            clock_local_datetime(getattr(self.conversation, "_clock", None)))
        sources = list(self._companion_context_sources)
        if self._recent_pulse_enabled and self._memory_v2_shadow_writer is not None:
            from recent_pulse import RecentPulseBuilder
            sources.insert(0, RecentPulseBuilder(self._memory_v2_shadow_writer, self.conversation))
        contributions, diagnostics = [], {"recent_pulse_enabled": self._recent_pulse_enabled}
        for source in sources:
            try:
                batch = source.build(request)
                # Impulses require the transaction owner below, never a raw cue source.
                contributions.extend(c for c in batch.contributions[:24] if getattr(c, "kind", None) != "transient_impulse")
                # The only production source currently has fixed content-free keys.
                if type(source).__name__ == "RecentPulseBuilder":
                    diagnostics.update(batch.diagnostics)
            except Exception:
                diagnostics["source_failed"] = True  # Optional context cannot fail the turn.
        if self._transient_impulse_store is not None and context_turn is not None:
            # Only the serialized current generation can lease. Reassembly reuses
            # the same handle, never spends or offers a second queue item.
            with self._turn_state_lock:
                if self._turn_is_current_locked(*context_turn):
                    try:
                        if self._impulse_lease is None:
                            self._impulse_lease = self._transient_impulse_store.lease(request)
                        if self._impulse_lease is not None:
                            contributions.insert(0, self._impulse_lease.contribution)
                            self._last_impulse_diagnostics = dict(self._transient_impulse_store.diagnostics(),
                                disposition="leased", selected_kind=self._impulse_lease.contribution.payload.category,
                                priority=self._impulse_lease.contribution.priority, lease_turn=context_turn[0])
                    except Exception:
                        self._last_impulse_diagnostics = {"disposition": "store_unavailable"}
        assembled = CompanionContextAssembler().assemble(contributions, request)
        if self._impulse_lease is not None and self._impulse_lease.contribution not in assembled.contributions:
            self._release_impulse()
        diagnostics.update(assembled.diagnostics)
        self._last_companion_context_diagnostics = diagnostics
        development_flight_recorder().mark("companion_context",
            recent_pulse_enabled=self._recent_pulse_enabled,
            item_count=diagnostics["item_count"], emitted_characters=len(assembled.block),
            source_failed=bool(diagnostics.get("source_failed")))
        return assembled.block

    def _release_impulse(self) -> None:
        lease = self._impulse_lease
        if lease is not None:
            try:
                self._transient_impulse_store.release(lease)
                self._last_impulse_diagnostics = {"disposition": "released"}
            except Exception:
                self._last_impulse_diagnostics = {"disposition": "release_unavailable"}
            self._impulse_lease = None
        self._impulse_receipt_ready = False

    def _prepare_impulse_publication(self, turn_id, cancel_event) -> None:
        # Caller holds the existing final commit lock; generation/synthesis do not.
        lease = self._impulse_lease
        if lease is None:
            return
        if (self._impulse_turn_owner != (turn_id, cancel_event)
                or not self._turn_is_current_locked(turn_id, cancel_event)
                or str(self.character_id) != lease.contribution.character_id
                or (self.truth_scope_provenance() or {}).get("scope_id") != lease.contribution.truth_scope_id):
            self._release_impulse()
            return
        try:
            index = len(self.conversation.messages) - 1
            self._impulse_receipt_ready = self._transient_impulse_store.prepare_publication(
                lease, index, self.conversation.messages[index])
        except Exception:
            self._impulse_receipt_ready = False
        if not self._impulse_receipt_ready:
            self._release_impulse()

    def _publish_impulse(self) -> None:
        if self._impulse_lease is None or not self._impulse_receipt_ready:
            return
        self._impulse_published = True
        try:
            consumed = self._transient_impulse_store.published(self._impulse_lease)
            self._last_impulse_diagnostics = {"disposition": "consumed" if consumed else "receipt_unavailable"}
        except Exception:
            # Preserve the prepared receipt for exact canonical reconciliation;
            # releasing it now could repeat an already published opportunity.
            self._last_impulse_diagnostics = {"disposition": "receipt_unavailable"}

    def _remember_published_expression(self, presentation: ResponsePresentationMetadata | None) -> None:
        """Called only inside the final commit/publication guard, after saving.

        Retain the last semantic request, not client state or canonical evidence.
        Missing emotion (including capability-only defaults) preserves it.
        Cancelled/rejected drafts, speech callbacks and observer replay never enter.
        """
        self._current_expression_request()
        if presentation is not None and presentation.emotion is not None:
            self._published_expression = ResponsePresentationMetadata(
                emotion=presentation.emotion, intensity=presentation.intensity,
            )

    def _generate_reply(
        self,
        user_message: str,
        *,
        active_truth_scope=None,
        response_policy_context: str | None = None,
        current_user_projection: str | None = None,
        memory_authority_context: str | None = None,
        memory_answer_requirement: Any = None,
        memory_query_decision: Any = None,
        memory_realization: Any = None,
        companion_context_allowed: bool = True,
        ordinary_policy: _ResponsePolicy | None = None,
    ) -> str:
        owned_turn, owned_cancel = self._active_turn_id, self._active_turn_cancel
        def request_ready():
            if owned_turn is not None and owned_cancel is not None and not self._turn_is_current(owned_turn, owned_cancel):
                raise _TurnCancelled()
        semantic_user_message = current_user_projection or user_message
        continuity_admission = self._admit_current_continuity_context(semantic_user_message)
        self._last_current_continuity_admission = continuity_admission
        active_admission = self._admit_active_state_context(semantic_user_message)
        self._last_active_state_context_admission = active_admission
        admission = self._admit_durable_context(semantic_user_message)
        self._last_durable_context_admission = admission
        active_state_context = (
            continuity_admission.active_state_context
            if continuity_admission is not None and continuity_admission.active_state_context
            else (active_admission.context_block if active_admission is not None else None)
        )
        active_state_context = self._merge_response_policy_context(
            active_state_context, response_policy_context,
            preserve_whole=governor_enabled(self._memory_authority),
        )
        durable_context = admission.context_block if admission is not None else None
        companion_context = self._ordinary_companion_context(semantic_user_message,
            active_truth_scope=active_truth_scope, memory_answer_requirement=memory_answer_requirement,
            memory_query_decision=memory_query_decision, memory_realization=memory_realization) if companion_context_allowed else ""
        character_prompt, optional_context_items = self._context_prompt_parts(ordinary_policy, companion_context)
        self._provider_request_begin()
        development_flight_recorder().mark(
            "provider_request_begin", turn_id=int(self._active_turn_id or 0), generating=True,
        )
        reply = ""
        try:
            if self._response_generator is not None:
                reply = self._response_generator(
                    self.llm,
                    self.conversation,
                    self.memory,
                    semantic_user_message,
                    character_prompt,
                )
            else:
                # This preserves the existing response-generation implementation.
                # Stage 2 will make Conversation its single context-builder source.
                from assistant import generate_response

                reply = generate_response(
                    self.llm,
                    self.conversation,
                    self.memory,
                    user_message,
                    character_prompt,
                    admitted_truth_scope_context=(continuity_admission.truth_scope_context if continuity_admission else None),
                    admitted_active_state_context=active_state_context,
                    admitted_open_thread_context=(continuity_admission.open_thread_context if continuity_admission else None),
                    admitted_durable_context=durable_context,
                    admitted_durable_facts=(
                        admission.facts + admission.v1_suppression_facts
                        if admission is not None else ()
                    ),
                    active_truth_scope=active_truth_scope,
                    current_user_projection=current_user_projection,
                    long_term_memory_authority=self._memory_authority,
                    admitted_v2_memory_context=memory_authority_context,
                    memory_answer_requirement=memory_answer_requirement,
                    recent_context_policy=self._v2_recent_context_policy,
                    memory_query_decision=memory_query_decision,
                    memory_realization=memory_realization,
                    request_ready=request_ready,
                    optional_context_items=optional_context_items,
                    open_thread_fragments=getattr(continuity_admission, "open_thread_fragments", ()),
                    context_character_id=str(self.character_id),
                    temporal_facts=getattr(ordinary_policy, "temporal_facts", None),
                    context_source_refs={
                        "active_state_and_response_policy": getattr(continuity_admission, "active_source_refs", ()),
                        "durable_facts": (("claim:" + admission.admitted_claim_id,) if admission is not None and admission.admitted_claim_id else ()),
                        "truth_scope": (("scope:" + str(active_truth_scope.get("scope_id")),) if active_truth_scope else ()),
                    },
                )
                self._record_context_plan()
            hygiene_metrics = getattr(self.conversation, "_last_context_hygiene_metrics", None)
            if isinstance(hygiene_metrics, dict) and hygiene_metrics:
                telemetry = dict(hygiene_metrics)
                final_prompt_characters = int(
                    telemetry.get("final_context_characters", 0)
                ) + len(character_prompt)
                telemetry["final_prompt_characters"] = final_prompt_characters
                telemetry["approximate_final_prompt_tokens"] = (
                    final_prompt_characters + 3
                ) // 4
                development_flight_recorder().mark(
                    "context_hygiene", turn_id=int(self._active_turn_id or 0),
                    memory_authority=self._memory_authority,
                    recent_context_policy=(
                        self._v2_recent_context_policy
                        if self._memory_authority == "v2" else "production_v1"
                    ),
                    v1_prompt_retrieval_entered=self._memory_authority == "v1",
                    v1_write_path_enabled=self._memory_authority == "v1",
                    **telemetry,
                )
            return reply
        finally:
            self._provider_request_end()
            development_flight_recorder().mark(
                "provider_request_end", turn_id=int(self._active_turn_id or 0), generating=False,
                characters=len(str(reply or "")),
            )

    def _stream_reply(
        self,
        user_message: str,
        on_delta: Callable[[str], None],
        on_speech_chunk: Callable[[str, str], None],
        cancel_event: threading.Event,
        active_truth_scope=None,
        response_policy_context: str | None = None,
        current_user_projection: str | None = None,
        memory_authority_context: str | None = None,
        memory_answer_requirement: Any = None,
        memory_query_decision: Any = None,
        canonicalization_diagnostics: dict[str, int] | None = None,
        ordinary_policy: _ResponsePolicy | None = None,
    ) -> str | None:
        """Use a provider's optional stream without changing context semantics.

        The final response is still parsed and persisted once by the ordinary
        turn lifecycle.  Providers which do not expose ``stream_generate``
        simply retain the established non-streaming path.
        """
        if self._response_generator is not None:
            return None
        stream_generate = getattr(self.llm, "stream_generate", None)
        if not callable(stream_generate):
            return None

        def context_check():
            if cancel_event.is_set():
                raise _TurnCancelled()

        semantic_user_message = current_user_projection or user_message
        continuity_admission = self._admit_current_continuity_context(semantic_user_message)
        self._last_current_continuity_admission = continuity_admission
        active_admission = self._admit_active_state_context(semantic_user_message)
        self._last_active_state_context_admission = active_admission
        admission = self._admit_durable_context(semantic_user_message)
        self._last_durable_context_admission = admission
        companion_context = self._ordinary_companion_context(semantic_user_message,
            active_truth_scope=active_truth_scope, memory_answer_requirement=memory_answer_requirement,
            memory_query_decision=memory_query_decision)
        character_prompt, optional_context_items = self._context_prompt_parts(ordinary_policy, companion_context)
        active_state_context = (
            continuity_admission.active_state_context
            if continuity_admission is not None and continuity_admission.active_state_context
            else (active_admission.context_block if active_admission else None)
        )
        active_state_context = self._merge_response_policy_context(
            active_state_context, response_policy_context,
            preserve_whole=governor_enabled(self._memory_authority),
        )
        from assistant import build_response_request
        context, character_prompt = build_response_request(
            self.llm, self.conversation, self.memory, user_message, character_prompt,
            admitted_truth_scope_context=(continuity_admission.truth_scope_context if continuity_admission else None),
            admitted_active_state_context=active_state_context,
            admitted_open_thread_context=(continuity_admission.open_thread_context if continuity_admission else None),
            admitted_durable_context=(admission.context_block if admission else None),
            admitted_durable_facts=(
                admission.facts + admission.v1_suppression_facts
                if admission is not None else ()
            ),
            active_truth_scope=active_truth_scope,
            current_user_projection=current_user_projection,
            long_term_memory_authority=self._memory_authority,
            admitted_v2_memory_context=memory_authority_context,
            recent_context_policy=self._v2_recent_context_policy,
            memory_query_decision=memory_query_decision,
            memory_answer_requirement=memory_answer_requirement,
            optional_context_items=optional_context_items,
            context_check=context_check,
            open_thread_fragments=getattr(continuity_admission, "open_thread_fragments", ()),
            context_character_id=str(self.character_id),
            temporal_facts=getattr(ordinary_policy, "temporal_facts", None),
            context_source_refs={
                "active_state_and_response_policy": getattr(continuity_admission, "active_source_refs", ()),
                "durable_facts": (("claim:" + admission.admitted_claim_id,) if admission is not None and admission.admitted_claim_id else ()),
                "truth_scope": (("scope:" + str(active_truth_scope.get("scope_id")),) if active_truth_scope else ()),
            },
        )
        self._record_context_plan()

        if cancel_event.is_set():
            raise _TurnCancelled()

        hygiene_metrics = getattr(self.conversation, "_last_context_hygiene_metrics", None)
        if isinstance(hygiene_metrics, dict) and hygiene_metrics:
            telemetry = dict(hygiene_metrics)
            final_prompt_characters = int(telemetry.get("final_context_characters", 0)) + len(character_prompt)
            telemetry["final_prompt_characters"] = final_prompt_characters
            telemetry["approximate_final_prompt_tokens"] = (final_prompt_characters + 3) // 4
            development_flight_recorder().mark(
                "context_hygiene", turn_id=int(self._active_turn_id or 0),
                memory_authority=self._memory_authority,
                recent_context_policy=(
                    self._v2_recent_context_policy
                    if self._memory_authority == "v2" else "production_v1"
                ),
                v1_prompt_retrieval_entered=self._memory_authority == "v1",
                v1_write_path_enabled=self._memory_authority == "v1",
                **telemetry,
            )
        speech_projection = SemanticSentenceAccumulator() if on_speech_chunk is not None else None
        speech_grouping = SemanticSpeechGrouper() if speech_projection is not None else None
        parts: list[str] = []
        canonicalizer = ModelOutputCanonicalizer()
        pending_output: list[str] = []
        presentation_contract_stream: bool | None = None
        contract_dialogue_stream = StreamingResponseDialogue()
        from act_presentation import ActPrefixStream
        act_stream = ActPrefixStream() if self._act_eligible(ordinary_policy) else None
        # A reasoning block can expose a non-outer prefix after canonicalization.
        # Withhold that debris too, but never use this projection's metadata.
        act_visible_guard = ActPrefixStream() if act_stream is not None else None
        first_nonspace_seen = False
        first_token_at: float | None = None
        first_visible_at: float | None = None
        visible_text_parts: list[str] = []
        first_sentence_seen = False
        first_character_window_seen = False

        def accept_visible_output(visible: str) -> None:
            nonlocal first_visible_at, first_sentence_seen, first_character_window_seen
            if not visible:
                return
            if cancel_event.is_set():
                raise _TurnCancelled()
            if first_visible_at is None and visible.strip():
                first_visible_at = time.monotonic()
                base = self._current_turn_started_at
                elapsed = first_visible_at - base if base is not None else 0.0
                _timing_log(f"first canonical visible delta t={elapsed:.3f}s")
                self._mark_turn_timing("first_canonical_visible", first_visible_at)
                development_flight_recorder().mark(
                    "first_canonical_delta", turn_id=int(self._active_turn_id or 0)
                )
            on_delta(visible)
            visible_text_parts.append(visible)
            visible_text = "".join(visible_text_parts)
            if not first_sentence_seen and re.search(r"[.!?][\"')\]]*(?:\s|$)", visible_text):
                first_sentence_seen = True
                self._mark_turn_timing("first_complete_sentence")
            if not first_character_window_seen and len(visible_text) >= 160:
                first_character_window_seen = True
                self._mark_turn_timing("first_160_characters")
            if speech_projection is not None:
                # Canonical/display text never passes through this speech
                # projection. It receives exact dialogue deltas and only
                # normalizes a whole semantically complete sentence.
                for sentence in speech_projection.feed(visible):
                    development_flight_recorder().mark(
                        "speech_sentence_completed", turn_id=int(self._active_turn_id or 0),
                        characters=len(sentence), words=len(re.findall(r"\S+", sentence)),
                    )
                    for group in speech_grouping.feed(sentence):
                        _timing_log("semantic TTS group available")
                        development_flight_recorder().mark(
                            "speech_group_admitted", turn_id=int(self._active_turn_id or 0),
                            characters=len(group), words=len(re.findall(r"\S+", group)),
                        )
                        on_speech_chunk(group, group)

        def accept_canonical_output(text: str) -> None:
            nonlocal first_nonspace_seen, presentation_contract_stream, first_visible_at
            nonlocal first_sentence_seen, first_character_window_seen
            if not text:
                return
            if act_stream is None:
                parts.append(text)
            else:
                text = act_visible_guard.feed(text)
                if not text:
                    return
            if not first_nonspace_seen:
                pending_output.append(text)
                stripped = "".join(pending_output).lstrip()
                if not stripped:
                    return
                first_nonspace_seen = True
                # Presentation-contract JSON is parsed only after its final
                # byte. Never expose or speak its transport fields as dialogue.
                presentation_contract_stream = stripped.startswith("{")
                output = tuple(pending_output)
                pending_output.clear()
            else:
                output = (text,)
            if presentation_contract_stream:
                for raw_contract_delta in output:
                    accept_visible_output(contract_dialogue_stream.feed(raw_contract_delta))
                return
            for visible in output:
                accept_visible_output(visible)

        request_seed = None
        new_request_seed = getattr(self.llm, "new_request_seed", None)
        if callable(new_request_seed):
            request_seed = new_request_seed()
        if request_seed is not None:
            development_flight_recorder().mark(
                "local_request_seed", turn_id=int(self._active_turn_id or 0),
                seed=int(request_seed), explicit_seed=True,
            )
        request_sampling_metadata = getattr(self.llm, "request_sampling_metadata", None)
        if callable(request_sampling_metadata):
            sampling_metadata = request_sampling_metadata()
            if sampling_metadata:
                development_flight_recorder().mark(
                    "local_request_sampling", turn_id=int(self._active_turn_id or 0),
                    **sampling_metadata,
                )
        base = self._current_turn_started_at
        provider_requested_at = time.monotonic()
        request_elapsed = provider_requested_at - base if base is not None else 0.0
        _timing_log(f"provider request begins t={request_elapsed:.3f}s")
        self._mark_turn_timing("provider_request", provider_requested_at)
        development_flight_recorder().mark(
            "provider_request_begin", turn_id=int(self._active_turn_id or 0), generating=True
        )
        self._provider_request_begin()
        provider_stream = None
        system_prompt = character_prompt
        try:
            if request_seed is not None:
                provider_stream = iter(stream_generate(
                    context, system_prompt, seed=int(request_seed),
                ))
            else:
                provider_stream = iter(stream_generate(context, system_prompt))
            for delta in provider_stream:
                if cancel_event.is_set():
                    raise _TurnCancelled()
                text = str(delta or "")
                if not text:
                    continue
                if first_token_at is None:
                    first_token_at = time.monotonic()
                    base = self._current_turn_started_at
                    elapsed = time.monotonic() - base if base is not None else 0.0
                    _timing_log(f"first provider delta t={elapsed:.3f}s")
                    self._mark_turn_timing("first_raw_delta", first_token_at)
                    development_flight_recorder().mark(
                        "first_raw_qwen_delta", turn_id=int(self._active_turn_id or 0)
                    )
                if act_stream is not None:
                    parts.append(text)  # Fresh raw output for final-only admission.
                    text = act_stream.feed(text)
                accept_canonical_output(canonicalizer.feed(text))
        finally:
            close = getattr(provider_stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            self._provider_request_end()
            development_flight_recorder().mark(
                "provider_request_end", turn_id=int(self._active_turn_id or 0), generating=False,
                characters=sum(len(part) for part in parts),
            )
        if cancel_event.is_set():
            raise _TurnCancelled()
        completed_at = time.monotonic()
        total_elapsed = completed_at - base if base is not None else 0.0
        generation_elapsed = completed_at - provider_requested_at
        _timing_log(
            "provider stream complete; "
            f"t={total_elapsed:.3f}s; generation={generation_elapsed:.3f}s"
        )
        self._mark_turn_timing("provider_complete", completed_at)
        accept_canonical_output(canonicalizer.finish())
        if act_stream is not None:
            act_stream.finish()
            act_visible_guard.finish()
        if presentation_contract_stream is True:
            accept_visible_output(contract_dialogue_stream.finish())
        if speech_projection is not None:
            for final_sentence in speech_projection.finish():
                development_flight_recorder().mark(
                    "speech_sentence_completed", turn_id=int(self._active_turn_id or 0),
                    characters=len(final_sentence), words=len(re.findall(r"\S+", final_sentence)),
                )
                for group in speech_grouping.feed(final_sentence):
                    _timing_log("final semantic TTS group available")
                    development_flight_recorder().mark(
                        "speech_group_admitted", turn_id=int(self._active_turn_id or 0),
                        characters=len(group), words=len(re.findall(r"\S+", group)),
                    )
                    on_speech_chunk(group, group)
            for final_group in speech_grouping.finish():
                _timing_log("final semantic TTS group available")
                development_flight_recorder().mark(
                    "speech_group_admitted", turn_id=int(self._active_turn_id or 0),
                    characters=len(final_group), words=len(re.findall(r"\S+", final_group)),
                )
                on_speech_chunk(final_group, final_group)
        if canonicalization_diagnostics is not None:
            canonicalization_diagnostics.clear()
            canonicalization_diagnostics.update(canonicalizer.structural_diagnostics())
        return "".join(parts)

    def _model_configuration_error(self) -> str | None:
        """Return a user-safe error before accepting a turn without a model.

        The explicit response-generator seam remains usable for deterministic
        tests and alternate hosts.  Ordinary runtime providers use the small
        provider-neutral unavailable adapter instead of failing backend
        startup.
        """
        if self._response_generator is not None:
            return None
        if getattr(self.llm, "is_available", True) is False:
            return str(getattr(self.llm, "message", "Configure a model in Settings > Model."))
        return None

    def model_runtime_availability(self) -> str:
        """Expose only a compact runtime-health label to the local frontend."""
        return self._model_runtime_availability

    def report_model_runtime_available(self) -> None:
        if getattr(self.llm, "is_available", True) is not False:
            self._model_runtime_availability = "configured"

    def report_model_runtime_unavailable(self) -> None:
        if getattr(self.llm, "is_available", True) is not False:
            self._model_runtime_availability = "unavailable"

    def _discard_unanswered_user_message(self, message: Any, index: int | None) -> None:
        """Discard pending input while retaining independently committed evidence."""
        messages = getattr(self.conversation, "messages", None)
        if not isinstance(messages, list) or index is None or message is None:
            return
        persisted = getattr(self.conversation, "is_message_persisted", None)
        if callable(persisted) and persisted(index, message):
            # User evidence can have its own earlier commit before state
            # application/generation. A later failure cannot undo that record.
            return
        if 0 <= index < len(messages) and messages[index] == message:
            messages.pop(index)

    def _report_conversation_persistence_failure(
        self, error: ConversationPersistenceError, *, turn_id: int,
        generation_origin: str, assistant_persisted: bool = False,
    ) -> str:
        message = str(error)
        if assistant_persisted and error.record_kind == "summary":
            message = "The assistant response is saved; do not resend the turn. " + message
        self._emit(
            "error", source="conversation_persistence", code="conversation_persistence_failed",
            message=message, turn_id=turn_id, generation_origin=generation_origin,
            record_kind=error.record_kind, persistence_stage=error.stage,
            replacement_committed=error.committed,
            assistant_persisted=assistant_persisted,
        )
        self._emit("status", state="error", message=message)
        development_flight_recorder().mark(
            "turn_terminal_outcome", turn_id=int(turn_id), outcome="persistence_error",
            succeeded=False, generation_origin=generation_origin,
        )
        return message

    def _claim_replacement_turn(self) -> tuple[int, threading.Event, bool]:
        """Make this input authoritative and invalidate an older live turn."""
        self._retire_automatic_expression()
        with self._turn_state_lock:
            previous = self._active_turn_cancel
            replaced = previous is not None
            if previous is not None:
                previous.set()
                self._invalidate_memory_recall_anchor()
            self._turn_generation += 1
            turn_id = self._turn_generation
            cancel_event = threading.Event()
            self._active_turn_id = turn_id
            self._active_turn_cancel = cancel_event
        if replaced:
            self._cancel_provider_generation()
        return turn_id, cancel_event, replaced

    def _cancel_active_turn(self) -> bool:
        """Cancel generation without inventing a replacement input yet."""
        self._retire_automatic_expression()
        with self._turn_state_lock:
            if self._active_turn_cancel is None:
                return False
            self._active_turn_cancel.set()
            self._invalidate_memory_recall_anchor()
        self._cancel_provider_generation()
        return True

    def _cancel_provider_generation(self) -> None:
        cancel = getattr(self.llm, "cancel_active_generation", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                # The cancellation event still prevents stale output from
                # crossing the canonical boundary if transport close fails.
                pass

    def _invalidate_memory_recall_anchor(self) -> None:
        invalidate = getattr(self._memory_v2_authority, "invalidate_recall_anchor", None)
        if callable(invalidate):
            invalidate()

    def _turn_is_current(self, turn_id: int, cancel_event: threading.Event) -> bool:
        with self._turn_state_lock:
            return self._turn_is_current_locked(turn_id, cancel_event)

    def _turn_is_current_locked(self, turn_id: int, cancel_event: threading.Event) -> bool:
        return (
            not cancel_event.is_set()
            and self._active_turn_id == turn_id
            and self._active_turn_cancel is cancel_event
        )

    @contextmanager
    def _turn_commit_boundary(self, turn_id: int, cancel_event: threading.Event):
        """Order cancellation against actions, canonical save, and publication.

        Acquire only after provider work. The winner keeps ownership through
        local commit/publication; release before synthesis or later observers.
        A successful file replacement remains the canonical commit point.
        """
        with self._turn_state_lock:
            if not self._turn_is_current_locked(turn_id, cancel_event):
                raise _TurnCancelled()
            yield

    def _finish_turn(self, turn_id: int, cancel_event: threading.Event) -> None:
        with self._turn_state_lock:
            if self._active_turn_id == turn_id and self._active_turn_cancel is cancel_event:
                self._active_turn_cancel = None

    def companion_preferences_snapshot(self) -> dict:
        worker = self._automatic_expression_worker
        status = (worker.status if worker is not None else
                  "CPU classifier starts when automatic expressions are enabled.")
        if callable(status):
            status = status()
        if isinstance(status, dict):
            status = status.get("detail") or status.get("state", "Unavailable")
        return {"conversation_style": self.conversation_style,
                "responsive_speech": self.responsive_speech,
                "automatic_expressions": self.automatic_expressions,
                "automatic_expression_status": str(status)[:220]}

    def apply_companion_preferences(self, preferences: dict) -> None:
        self.conversation_style = preferences["conversation_style"]
        self.responsive_speech = preferences["responsive_speech"]
        self.automatic_expressions = preferences["automatic_expressions"]
        self._retire_automatic_expression()
        if self.automatic_expressions and self._automatic_expression_worker is None:
            from automatic_expression import AutomaticExpressionWorker
            self._automatic_expression_worker = AutomaticExpressionWorker(
                on_status=lambda text: self._emit("automatic_expression_status", message=text))
        if self._automatic_expression_worker is not None:
            self._automatic_expression_worker.set_enabled(self.automatic_expressions)

    def _retire_automatic_expression(self) -> None:
        with self._automatic_expression_lock:
            self._automatic_expression_lease = None
        worker = self._automatic_expression_worker
        if worker is not None:
            worker.cancel_pending()

    def _automatic_expression_input(self, parsed, policy) -> str:
        if not self.automatic_expressions:
            return ""
        if parsed.presentation is not None and parsed.presentation.emotion is not None:
            return ""  # Explicit presentation owns the face, including neutral.
        realized = policy.memory_realized_response
        if realized is not None:
            return realized.reaction  # The historical factual core is never classifier input.
        # Final accepted prose is a presentation input, not an output-format
        # obligation. An updated hat or occupied hand must not disable faces.
        # Memory answer cores and sleep/constrained-speech owners stay excluded.
        from memory_v2_store.scene_relation_contract import CapabilityEffects
        effects = policy.effects
        memory = policy.memory_answer_requirement
        if (policy.policy_error is not None or policy.authoritative_memory_response is not None
                or (memory is not None and memory.triggered)
                or not isinstance(effects, CapabilityEffects)
                or effects.awareness_mode != "normal" or effects.speech_mode != "normal"):
            return ""
        return parsed.dialogue

    def _offer_automatic_expression(self, text, turn_id, cancel_event):
        """One disposable face proposal after canonical commit and publication."""
        if not text or cancel_event.is_set() or self._active_turn_id != turn_id:
            return
        if self._automatic_expression_worker is None:
            from automatic_expression import AutomaticExpressionWorker
            self._automatic_expression_worker = AutomaticExpressionWorker(enabled=True,
                on_status=lambda text: self._emit("automatic_expression_status", message=text))
        scope = self.truth_scope_provenance()
        token = (turn_id, self._turn_generation, str(self.character_id), scope, cancel_event)
        with self._automatic_expression_lock:
            self._automatic_expression_lease = token
        self._automatic_expression_worker.offer(text, token, self._publish_automatic_expression,
                                                deadline_seconds=2.0)

    def _publish_automatic_expression(self, token, result):
        with self._automatic_expression_lock:
            current = (self.automatic_expressions and self._automatic_expression_lease is token
                       and token[1] == self._turn_generation and token[2] == str(self.character_id)
                       and not token[4].is_set())
            if not current:
                return
            self._automatic_expression_lease = None
            proposal = result.proposal
            development_flight_recorder().mark("automatic_expression", turn_id=int(token[0]),
                outcome=str(result.reason)[:64], admitted=proposal is not None)
            if proposal is not None:
                # No canonical write, second final reply, body gesture, or
                # metadata-continuity update. Unity still owns manual/state precedence.
                self._emit("automatic_expression", turn_id=token[0],
                    presentation={"emotion": proposal.emotion, "intensity": proposal.intensity,
                                  "has_intensity": True, "origin": "automatic"})

    def _scene_reaction_state_is_current(self, reaction: _SceneReaction) -> bool:
        """Check captured scene ownership under the serialized turn boundary."""
        return (
            str(self.character_id) == reaction.character_id
            and self.truth_scope_provenance() == reaction.message["truth_scope"]
            and self.continuity_snapshot()["revision"] == reaction.revision
            and self.conversation.is_message_persisted(reaction.message_index, reaction.message)
        )

    def _set_pending_stream_speech(
        self,
        spoken: str,
        subtitle: str,
        chunk_index: int,
        turn_id: int,
        speech_generation: int,
        *, committed_stream: bool = False,
    ) -> None:
        with self._speech_generation_lock:
            if speech_generation != self._speech_generation:
                return
            self._pending_stream_speech = {
                "spoken": spoken,
                "subtitle": subtitle,
                "chunk_index": chunk_index,
                "turn_id": turn_id,
                "speech_generation": speech_generation,
                "committed_stream": committed_stream,
            }

    def _announce_stream_speech_chunk(
        self,
        spoken: str,
        subtitle: str,
        chunk_index: int,
        turn_id: int,
        speech_generation: int,
    ) -> None:
        """Give presentation clients synthesis time to prepare chunk layout."""
        with self._speech_generation_lock:
            if speech_generation != self._speech_generation:
                return
        self._emit(
            "tts_state",
            state="chunk_queued",
            streamed=True,
            content=spoken,
            subtitle_content=subtitle,
            chunk_index=chunk_index,
            turn_id=turn_id,
        )

    def _streaming_queue_completed(self, queue: Any) -> None:
        with self._speech_generation_lock:
            if self._streaming_speech_queue is queue:
                self._streaming_speech_queue = None
                self._pending_stream_speech = None

    def _stream_tts_failed(self, reason: str, speech_generation: int, turn_id: int, *, queue=None) -> None:
        with self._speech_generation_lock:
            if speech_generation != self._speech_generation:
                return
            playback_id = int(getattr(queue, "playback_id", 0) or 0)
            with self._tts_state_lock:
                if self._active_tts_playback_id == playback_id:
                    self._active_tts_playback_id = 0
                    self._active_stream_playback = None
            self._emit("tts_state", state="failed", reason=reason, streamed=True, turn_id=turn_id,
                       playback_id=playback_id, committed_stream=bool(getattr(queue, "_committed_text", None)))

    def _dispatch_direct_speech_with_recovery(
        self,
        text: str,
        *,
        cancel_event: threading.Event,
        speech_generation: int,
        require_prepared: bool = False,
    ) -> bool | None:
        """Synthesize one governed utterance through the shared resource policy."""
        if (self.responsive_speech is True
                and getattr(type(self.tts), "supports_owned_continuous_stream", False) is True):
            from tts.streaming import StreamingSpeechQueue
            turn_id = self._active_turn_id
            with self._speech_generation_lock:
                if speech_generation != self._speech_generation or cancel_event.is_set():
                    return None
                self._set_pending_stream_speech(text, text, 0, turn_id, speech_generation, committed_stream=True)
                queue = StreamingSpeechQueue(self.tts, committed_text=text, max_chunks=2,
                    owner_current=lambda: speech_generation == self._speech_generation and not cancel_event.is_set(),
                    provider_generation_active=self.provider_request_active,
                    on_chunk_starting=lambda spoken, subtitle, index: self._set_pending_stream_speech(
                        spoken, subtitle, index, turn_id, speech_generation, committed_stream=True),
                    on_owned_failure=lambda owner, reason: self._stream_tts_failed(
                        reason, speech_generation, turn_id, queue=owner),
                    on_complete=self._streaming_queue_completed)
                self._streaming_speech_queue = queue
            return True
        prepare = getattr(self.tts, "prepare_stream_chunk", None)
        start = getattr(self.tts, "start_prepared_chunk", None)
        if not callable(prepare) or not callable(start):
            if require_prepared:
                # A synchronous speak-only extension cannot separate stale
                # synthesis from playback. Proactive audio must fail closed
                # without blocking PTT; the committed caption stays usable.
                return False
            with self._speech_generation_lock:
                if speech_generation != self._speech_generation or cancel_event.is_set():
                    return None
                return self.tts.speak(text)

        from tts.streaming import TtsSynthesisResourceManager
        manager = TtsSynthesisResourceManager(
            self.tts,
            cancelled=cancel_event,
            provider_generation_active=self.provider_request_active,
            preparation_owner_current=lambda: speech_generation == self._speech_generation,
        )
        recovery = manager.prepare(text, unit_index=0, direct=True)
        if not recovery.succeeded:
            if not recovery.cancelled:
                development_flight_recorder().mark(
                    "tts_synthesis_sequence_failure", reason="tts_direct_synthesis_failed",
                    coherent_sequence_failure=True, direct=True,
                    category=recovery.category,
                    cpu_fallback_used=recovery.cpu_fallback_used,
                )
            return None if recovery.cancelled else False
        with self._speech_generation_lock:
            if speech_generation != self._speech_generation or cancel_event.is_set():
                return None
            return start(recovery.prepared)

    def process_text_turn(
        self,
        user_message: str,
        speak: bool = True,
        *,
        input_source: str = "typed",
        ptt_release_at: float | None = None,
        stt_final_at: float | None = None,
        _scene_reaction: _SceneReaction | None = None,
        character_id: str | None = None,
        character_session: str | None = None,
    ) -> TurnResult:
        """Replace any live turn, then run this input through canonical lifecycle."""
        self._assert_character_state_ownership()
        entry_owner = self.character_binding()
        if character_session is not None:
            self.require_character_binding(character_id, character_session)
            entry_owner = {"character_id": character_id, "character_session": character_session}
        user_message = str(user_message).strip()
        _scene_event = _scene_reaction.event if _scene_reaction is not None else None

        if not user_message:
            error = "A message is required."
            self._emit("error", message=error)
            return TurnResult(user_message=user_message, error=error)

        generation_origin = "scene_ui" if _scene_event is not None else "user"
        # Scene UI prose narrates an already-applied backend mutation. It is
        # never sent through conversational evidence extraction or sleep/
        # scenario interaction parsing a second time.
        policy_decision = None if _scene_event is not None else self.interaction_policy(user_message)
        with self._character_binding_lock:
            self.require_character_binding(**entry_owner)
            if _scene_reaction is None:
                self._last_interaction_policy = policy_decision
            configuration_error = self._model_configuration_error() if _scene_reaction is None else None
            if configuration_error is not None and not (
                policy_decision is not None and (
                    policy_decision.forced_reply is not None
                    or policy_decision.response_mode is not None
                )
            ):
                self._model_runtime_availability = "unconfigured"
                self._emit(
                    "error", source="model", code="model_unconfigured",
                    generation_stage="configuration", provider_called=False,
                    message=configuration_error,
                )
                self._emit("status", state="ready", message=configuration_error)
                return TurnResult(user_message=user_message, error=configuration_error)

            if _scene_reaction is None:
                turn_id, cancel_event, replaced_turn = self._claim_replacement_turn()
            else:
                turn_id, cancel_event = _scene_reaction.turn_id, _scene_reaction.cancel_event
                replaced_turn = False
            with self._tts_state_lock:
                playback_active = self._active_tts_playback_id != 0
            if _scene_reaction is None and (
                replaced_turn or playback_active or self._streaming_speech_queue is not None
            ):
                # New user input owns the output boundary immediately, even while
                # it waits for the old provider iterator to leave the serialized
                # canonical-history section.
                self.stop_speaking(interrupted=True)

        turn_lock_wait_started_at = time.monotonic()
        self._turn_lock.acquire()
        turn_lock_wait_seconds = time.monotonic() - turn_lock_wait_started_at
        with self._character_binding_lock:
            if entry_owner != self.character_binding():
                self._turn_lock.release()
                return TurnResult(user_message=user_message, error="interrupted")

        canonical_user_index: int | None = None
        canonical_user_message = None
        turn_truth_scope = None
        assistant_persisted = False
        action_applied = False
        continuity_result: dict[str, Any] | None = None
        canonical_user_committed_to_state = _scene_reaction is not None
        turn_announced = False
        speech_queue = None
        memory_query_decision = None
        commit_boundary = ExitStack()
        commit_started = False
        response_policy = None
        try:
            if character_session is not None:
                self.require_character_binding(character_id, character_session)
            if not self._turn_is_current(turn_id, cancel_event):
                raise _TurnCancelled()
            self._impulse_turn_owner = (turn_id, cancel_event)
            self._impulse_published = False
            self._recover_canonical_observers()
            if not self._turn_is_current(turn_id, cancel_event):
                raise _TurnCancelled()
            if _scene_reaction is not None and not self._scene_reaction_state_is_current(_scene_reaction):
                raise _TurnCancelled()
            if _scene_reaction is not None:
                self._last_interaction_policy = None
                configuration_error = self._model_configuration_error()
                if configuration_error is not None:
                    with self._turn_commit_boundary(turn_id, cancel_event):
                        self._model_runtime_availability = "unconfigured"
                        self._emit(
                            "error", source="model", code="model_unconfigured",
                            generation_stage="configuration", provider_called=False,
                            message=configuration_error,
                        )
                        self._emit("status", state="ready", message=configuration_error)
                    return TurnResult(user_message=user_message, error=configuration_error)

            turn_started_at = time.monotonic()
            self._current_turn_started_at = turn_started_at
            self._begin_turn_timing(
                turn_id=turn_id,
                source=input_source,
                accepted_at=turn_started_at,
                ptt_release_at=ptt_release_at,
                stt_final_at=stt_final_at,
            )
            _timing_log(
                "user accepted; backend turn started t=0.000s; "
                f"serialization_wait={turn_lock_wait_seconds:.3f}s"
            )
            self._emit(
                "turn_started", user_message=user_message, turn_id=turn_id,
                generation_origin=generation_origin,
            )
            turn_announced = True
            self._emit("status", state="thinking", message="Thinking...")

            if self._memory_authority == "v2":
                from memory_query_decision import decide_memory_query
                memory_query_decision = decide_memory_query(user_message)
                self._last_memory_authority_diagnostics = {
                    "mode": "v2", "state": "not_evaluated",
                    **memory_query_decision.diagnostics(),
                }

            turn_truth_scope = self.truth_scope_provenance()
            generation_truth_scope = turn_truth_scope
            if (
                getattr(self._memory_v2_shadow_writer, "store", None) is not None
                and turn_truth_scope is None
            ):
                raise RuntimeError("Authoritative truth scope is unavailable; the turn was not persisted.")
            if _scene_reaction is not None:
                canonical_user_index = _scene_reaction.message_index
                canonical_user_message = _scene_reaction.message
            elif turn_truth_scope is None:
                # Legacy/test Conversation implementations remain valid when
                # no authoritative V2 scope store is installed.
                self.conversation.add_user_message(user_message)
            else:
                self.conversation.add_user_message(user_message, truth_scope=turn_truth_scope)
            if _scene_reaction is None:
                canonical_user_index = len(getattr(self.conversation, "messages", ())) - 1
                canonical_user_message = (
                    self.conversation.messages[canonical_user_index]
                    if canonical_user_index >= 0 else None
                )

            if (
                self._memory_v2_shadow is not None
                or self._memory_v2_shadow_writer is not None
                or self._memory_recall_shadow is not None
            ):
                setattr(self.conversation, "_capture_v1_retrieval_diagnostics", True)
                setattr(self.conversation, "_last_v1_retrieval_diagnostics", ())
                setattr(self.conversation, "_last_v1_prompt_diagnostics", ())
                setattr(self.conversation, "_last_v1_retrieval_latency_ms", None)
            streamed_speech = False
            policy_spoken_text: str | None = None
            output_canonicalization_diagnostics: dict[str, int] = {}
            response_policy = _ResponsePolicy()
            with self._speech_generation_lock:
                turn_speech_generation = self._speech_generation
            try:
                def emit_delta(delta: str) -> None:
                    if not self._turn_is_current(turn_id, cancel_event):
                        raise _TurnCancelled()
                    self._emit("assistant_delta", content=delta, turn_id=turn_id)

                def enqueue_speech(chunk: str, subtitle_source: str) -> None:
                    nonlocal speech_queue, streamed_speech
                    if not speak or not self._turn_is_current(turn_id, cancel_event):
                        return
                    queue_to_submit = None
                    with self._speech_generation_lock:
                        if turn_speech_generation != self._speech_generation:
                            return
                        if speech_queue is None:
                            from config import TTS_CHUNK_QUEUE_MAX
                            from tts.streaming import StreamingSpeechQueue
                            self._emit("status", state="speaking", message="Speaking...")
                            self._emit("tts_state", state="starting", streamed=True, turn_id=turn_id)
                            _timing_log(
                                f"streamed TTS first chunk requested t={time.monotonic() - turn_started_at:.3f}s"
                            )
                            speech_queue = StreamingSpeechQueue(
                                self.tts, max_chunks=TTS_CHUNK_QUEUE_MAX,
                                on_owned_failure=lambda owner, reason: self._stream_tts_failed(
                                    reason, turn_speech_generation, turn_id, queue=owner
                                ),
                                on_chunk_submitted=lambda spoken, subtitle, index: self._announce_stream_speech_chunk(
                                    spoken, subtitle, index, turn_id, turn_speech_generation
                                ),
                                on_chunk_starting=lambda spoken, subtitle, index: self._set_pending_stream_speech(
                                    spoken, subtitle, index, turn_id, turn_speech_generation
                                ),
                                on_complete=lambda queue: self._streaming_queue_completed(queue),
                                provider_generation_active=self.provider_request_active,
                            )
                            self._streaming_speech_queue = speech_queue
                        queue_to_submit = speech_queue
                    # Queue backpressure must never hold the PTT invalidation
                    # boundary. A concurrent stop cancels this queue, making a
                    # racing submission fail safely without delaying capture.
                    submitted_at = time.monotonic()
                    submitted = queue_to_submit.submit(chunk, presentation_text=subtitle_source)
                    submit_wait = time.monotonic() - submitted_at
                    _timing_log(
                        "TTS chunk submitted; "
                        f"t={time.monotonic() - turn_started_at:.3f}s; wait={submit_wait:.3f}s; "
                        f"accepted={submitted}"
                    )
                    if submitted:
                        streamed_speech = True

                synthesis_strategy = getattr(self.tts, "synthesis_strategy", "manual_chunks")
                speech_chunk_callback = (
                    enqueue_speech
                    if speak and synthesis_strategy in {"manual_chunks", "complete_sentences"}
                    else None
                )
                if policy_decision is not None and policy_decision.response_mode is not None:
                    reaction = self._generate_sleep_reaction(policy_decision, user_message)
                    generated = reaction.dialogue
                    policy_spoken_text = reaction.spoken_text
                elif policy_decision is not None and policy_decision.forced_reply is not None:
                    generated = policy_decision.forced_reply
                else:
                    self._recent_sleep_reaction_signatures.clear()
                    response_policy = (
                        self._response_policy_for_scene_ui_event(_scene_event)
                        if _scene_event is not None else self._response_policy(user_message)
                    )
                    if _scene_event is not None:
                        continuity_result = {
                            "state": "unchanged",
                            "reason": "scene_ui_mutation_already_applied",
                            "memory_v1_allowed": False,
                        }
                        canonical_user_committed_to_state = True
                    elif response_policy.hearing_input_unavailable:
                        # Canonical history records what was sent, while this
                        # structural marker prevents inaccessible semantics
                        # from entering any understood-information pipeline.
                        canonical_user_message["semantic_admission"] = {
                            "channel": "hearing", "state": "unavailable",
                            "understood": False,
                        }
                        self.conversation.save()
                        continuity_result = {
                            "state": "unchanged",
                            "reason": "hearing_unavailable_semantic_admission",
                            "memory_v1_allowed": False,
                        }
                        canonical_user_committed_to_state = True
                    elif response_policy.changed_by_current_evidence:
                        # Explicit canonical user evidence is authoritative
                        # independently of whether a later model reaction can
                        # be rendered. Commit it before generation so context
                        # admission, requirements, validation, and narration
                        # all observe the same post-mutation snapshot.
                        self.conversation.save()
                        continuity_result = self._observe_current_continuity(
                            canonical_user_message, canonical_user_index,
                        )
                        canonical_user_committed_to_state = str(
                            continuity_result.get("state") or ""
                        ) in {"applied", "unchanged"}
                        development_flight_recorder().mark(
                            "mutation_authority_boundary",
                            turn_id=int(turn_id),
                            applied_before_generation=canonical_user_committed_to_state,
                            application_outcome=str(continuity_result.get("state") or "failed"),
                        )
                        if not canonical_user_committed_to_state:
                            # The saved input is retained for existing recovery,
                            # but neither the preview nor a generic model reply
                            # can acknowledge an update that did not complete.
                            # Idempotent ``unchanged`` is a successful observation.
                            raise _ContinuityMutationUnavailable(
                                "Your message is saved, but the current-state update could not be completed."
                            )
                        if continuity_result.get("scope_changed"):
                            generation_truth_scope = self.truth_scope_provenance()
                            post_transition = self._response_policy(user_message)
                            scope = continuity_result.get("truth_scope") or {}
                            transition_context = (
                                "[Completed truth-scope transition — backend policy]\n"
                                f"The active scope is now {str(scope.get('label') or scope.get('kind') or 'real_world')}. "
                                "Generate only from this new active scope. Inactive-scope scene state and open "
                                "threads are not current.\n[End completed truth-scope transition]"
                            )
                            response_policy = replace(
                                post_transition,
                                context_block="\n".join(item for item in (
                                    post_transition.context_block, transition_context,
                                ) if item),
                                enforce_before_presentation=True,
                                changed_by_current_evidence=True,
                                current_user_projection=user_message,
                            )
                    if _scene_event is None:
                        response_policy = self._with_temporal_policy(user_message, response_policy,
                            active_truth_scope=generation_truth_scope)
                        response_policy = self._plan_companion_action(user_message, response_policy)
                        response_policy = self._apply_memory_authority_policy(
                            user_message, response_policy,
                            active_truth_scope=generation_truth_scope,
                            memory_query_decision=memory_query_decision,
                        )
                    elif self._memory_authority == "v2":
                        # Scene/UI source records are generated control input,
                        # not user-authored memory evidence. Keep their own
                        # response requirement and add only the retrospective
                        # output guard; no V2 retrieval is performed here.
                        from memory_query_decision import decide_memory_query
                        response_policy = self._apply_v2_retrospective_only_policy(
                            user_message,
                            response_policy,
                            active_truth_scope=generation_truth_scope,
                            memory_query_decision=decide_memory_query(""),
                        )
                    if response_policy.authoritative_memory_response is not None:
                        generated = response_policy.authoritative_memory_response
                    else:
                        generated = None if response_policy.enforce_before_presentation else self._stream_reply(
                            user_message,
                            emit_delta,
                            speech_chunk_callback,
                            cancel_event,
                            active_truth_scope=generation_truth_scope,
                            response_policy_context=response_policy.context_block,
                            current_user_projection=response_policy.current_user_projection,
                            memory_authority_context=response_policy.memory_authority_context,
                            memory_answer_requirement=response_policy.memory_answer_requirement,
                            memory_query_decision=response_policy.memory_query_decision,
                            canonicalization_diagnostics=output_canonicalization_diagnostics,
                            ordinary_policy=response_policy if _scene_event is None else None,
                        )
                    if generated is None:
                        try:
                            generated = self._generate_reply(
                                user_message, active_truth_scope=generation_truth_scope,
                                response_policy_context=response_policy.context_block,
                                current_user_projection=response_policy.current_user_projection,
                                memory_authority_context=response_policy.memory_authority_context,
                                memory_answer_requirement=response_policy.memory_answer_requirement,
                                memory_query_decision=response_policy.memory_query_decision,
                                memory_realization=response_policy.memory_realization,
                                companion_context_allowed=_scene_event is None,
                                ordinary_policy=response_policy if _scene_event is None else None,
                            )
                            if not (_scene_event is None and self._act_eligible(response_policy)):
                                generated = canonicalize_model_output(generated, diagnostics=output_canonicalization_diagnostics)
                        except ModelTransportError:
                            if response_policy.memory_realization is None:
                                raise
                            generated = ""
                            self._last_memory_authority_diagnostics["reaction_provider_unavailable"] = True
                if not self._turn_is_current(turn_id, cancel_event):
                    raise _TurnCancelled()
                if response_policy.memory_realization is not None:
                    from companion_memory_realizer import CompanionMemoryRealizer, CompanionMemoryResponse
                    core = response_policy.memory_realization
                    if response_policy.authoritative_memory_response is not None:
                        composed = CompanionMemoryResponse(core)
                    else:
                        reaction_parsed = parse_assistant_response(str(generated or ""), normalize_presentation_format=True)
                        composed = CompanionMemoryRealizer().compose(core, reaction_parsed)
                    response_policy = replace(response_policy, memory_realized_response=composed)
                    # Only accepted optional metadata belongs to a retained reaction.
                    # A rejected tail cannot dispatch an expression or action either.
                    generated = composed.dialogue
                    self._last_memory_authority_diagnostics.update({
                        "memory_realization": composed.mode, "memory_surface": core.surface,
                        "reaction_status": composed.reaction_status, "reaction_repair_calls": 0,
                    })
                visible_content_present = bool(str(generated or "").strip())
                development_flight_recorder().mark(
                    "generation_output_normalized", turn_id=int(turn_id),
                    generation_output_state=(
                        "visible" if visible_content_present else "empty_visible"
                    ),
                    reasoning_content_present=bool(
                        output_canonicalization_diagnostics.get("reasoning_content_present", False)
                    ),
                    visible_content_present=visible_content_present,
                )
                generated = require_visible_model_output(generated)
                if (_scene_event is None and (policy_decision is None or (
                        policy_decision.forced_reply is None and policy_decision.response_mode is None))
                        and self._act_eligible(response_policy)):
                    from act_presentation import parse_fresh_act_response
                    parsed_response, act_status = parse_fresh_act_response(
                        generated, diagnostics=output_canonicalization_diagnostics)
                    development_flight_recorder().mark("act_prefix", turn_id=int(turn_id),
                                                       outcome=act_status)
                    # Ambiguous control debris is a bounded output failure, not
                    # an invitation to spend a repair inference on presentation.
                    require_visible_model_output(parsed_response.dialogue)
                else:
                    parsed_response = parse_assistant_response(
                        generated, normalize_generated_dialogue=bool(
                            response_policy.memory_realized_response is None
                            and (policy_decision is None or policy_decision.forced_reply is None)
                        ), normalize_presentation_format=bool(
                            response_policy.memory_answer_requirement is not None
                            and response_policy.memory_answer_requirement.triggered
                        ),
                    )
                if (response_policy.memory_realized_response is not None
                        and response_policy.memory_realized_response.reaction):
                    parsed_response = replace(parsed_response,
                        presentation=reaction_parsed.presentation,
                        has_presentation_contract=reaction_parsed.has_presentation_contract)
                if policy_decision is not None and policy_decision.response_mode is not None:
                    parsed_response = ParsedAssistantResponse(
                        dialogue=parsed_response.dialogue,
                        presentation=reaction.presentation,
                        has_presentation_contract=True,
                        response_mode=("waking" if policy_decision.response_mode == "waking" else "sleep_reaction"),
                        spoken_content=reaction.spoken_text,
                        contract_status="valid",
                    )
                elif policy_decision is None or policy_decision.forced_reply is None:
                    parsed_response = self._assemble_memory_answer_response(parsed_response, response_policy)
                    if response_policy.effects is not None:
                        from capability_policy import normalize_response_for_capabilities
                        parsed_response = normalize_response_for_capabilities(
                            parsed_response, response_policy.effects,
                        )
                    accepted, validation_category, validated_spoken, validated_presentation = (
                        self._validate_governed_response(parsed_response, response_policy)
                    )
                    if not accepted and response_policy.memory_realized_response is not None:
                        from companion_memory_realizer import CompanionMemoryResponse
                        owned = CompanionMemoryResponse(response_policy.memory_realization,
                                                        reaction_status="capability_rejected")
                        response_policy = replace(response_policy, memory_realized_response=owned)
                        parsed_response = parse_assistant_response(owned.dialogue)
                        accepted, validation_category, validated_spoken, validated_presentation = self._validate_governed_response(parsed_response, response_policy)
                        self._last_memory_authority_diagnostics.update(memory_realization=owned.mode,
                                                                       reaction_status=owned.reaction_status)
                    primary_contract_admission = (
                        "json_envelope" if parsed_response.contract_status == "valid"
                        else "plain_text_minimal" if parsed_response.contract_status == "plain_text"
                        else "rejected"
                    )
                    primary_parse_failure = (
                        str(parsed_response.failure_category or parsed_response.contract_status)
                        if parsed_response.contract_status not in {"valid", "plain_text"} else "none"
                    )
                    primary_format_normalization = parsed_response.format_normalization or "none"
                    primary_semantic_rejection = (
                        validation_category
                        if parsed_response.contract_status in {"valid", "plain_text"} and not accepted else "none"
                    )
                    repaired = False
                    repair_attempted = False
                    repair_skipped_reason = "none"
                    fallback_used = False
                    fallback_category: str | None = None
                    if not accepted:
                        # A rejected generation did not receive its opportunity.
                        # Repairs use the existing dedicated contract, no impulse.
                        self._release_impulse()
                        if response_policy.memory_realized_response is not None:
                            response_policy = replace(response_policy, memory_realized_response=None)
                            self._last_memory_authority_diagnostics["memory_realization"] = "emergency_safe_response"
                        fallback_category = validation_category
                        from memory_v2_answer_governance import memory_answer_should_attempt_repair
                        repair_attempted = (
                            response_policy.memory_realization is None
                            and memory_answer_should_attempt_repair(
                                response_policy.memory_answer_requirement,
                                contract_status=parsed_response.contract_status,
                                failure_category=validation_category,
                            )
                        )
                        if not repair_attempted:
                            repair_skipped_reason = (
                                "companion_core_unrepresentable"
                                if response_policy.memory_realization is not None
                                else "bounded_memory_semantic_rejection"
                            )
                        development_flight_recorder().mark(
                            "response_contract_primary_rejection",
                            turn_id=int(turn_id),
                            parse_success=parsed_response.contract_status == "valid",
                            minimal_plain_text=parsed_response.contract_status == "plain_text",
                            contract_admission=primary_contract_admission,
                            parse_failure=primary_parse_failure,
                            semantic_rejection=primary_semantic_rejection,
                            category=validation_category,
                            repair_attempted=repair_attempted,
                            repair_skipped_reason=repair_skipped_reason,
                        )
                        repair = self._repair_governed_response(
                            user_message, parsed_response, response_policy,
                            output_canonicalization_diagnostics,
                        ) if repair_attempted else None
                        if not self._turn_is_current(turn_id, cancel_event):
                            raise _TurnCancelled()
                        if repair is not None:
                            parsed_response = repair
                            repaired = True
                        else:
                            projected_response = None
                            memory_requirement = response_policy.memory_answer_requirement
                            if (
                                memory_requirement is not None
                                and memory_requirement.retrospective_guard_enabled
                                and not memory_requirement.triggered
                            ):
                                from memory_v2_answer_governance import (
                                    remove_unsupported_retrospective_sentences,
                                )
                                projected = remove_unsupported_retrospective_sentences(
                                    memory_requirement, parsed_response.dialogue,
                                )
                                if projected:
                                    candidate = parse_assistant_response(projected)
                                    projection_accepted, _, _, _ = self._validate_governed_response(
                                        candidate, response_policy,
                                    )
                                    if projection_accepted:
                                        projected_response = candidate
                            if projected_response is not None:
                                parsed_response = projected_response
                                fallback_category = "retrospective_projection"
                            else:
                                parsed_response = self._governed_fallback_response(response_policy)
                                fallback_category = fallback_category or "governed_fallback"
                            output_canonicalization_diagnostics.clear()
                            fallback_used = True
                        accepted, final_category, validated_spoken, validated_presentation = (
                            self._validate_governed_response(parsed_response, response_policy)
                        )
                        if not accepted:
                            # The fallback is authored from the same typed
                            # policy. If a future validator tightens, retain a
                            # safe nonverbal boundary instead of leaking the
                            # rejected draft. An action that cannot be safely
                            # narrated is not applied.
                            if response_policy.action_plan is not None:
                                response_policy = replace(
                                    response_policy,
                                    action_plan=None,
                                    action_decision_category="action_response_unrepresentable",
                                )
                            parsed_response = ParsedAssistantResponse(
                                dialogue="*Remains quietly attentive.*",
                                presentation=ResponsePresentationMetadata(
                                    gaze_mode=(
                                        "suppressed" if response_policy.effects is not None
                                        and not response_policy.effects.vision_available else None
                                    ),
                                    speech_mode="nonverbal",
                                ),
                                has_presentation_contract=True,
                                response_mode="nonverbal_reaction",
                                spoken_content="",
                                contract_status="valid",
                            )
                            validated_spoken = ""
                            output_canonicalization_diagnostics.clear()
                            validated_presentation = parsed_response.presentation
                            final_category = "safe_nonverbal"
                        validation_category = final_category
                    if response_policy.action_plan is not None:
                        # The same boundary must cover both the action and
                        # the assistant save: cancellation cannot slip between
                        # a checked action and its canonical narration.
                        commit_boundary.enter_context(self._turn_commit_boundary(turn_id, cancel_event))
                        commit_started = True
                        plan = response_policy.action_plan
                        action_result = self._apply_companion_action_plan(
                            plan, turn_id, canonical_user_message,
                        )
                        action_state = str(action_result.get("state", "failed"))
                        action_applied = action_state in {"applied", "unchanged"}
                        development_flight_recorder().mark(
                            "companion_action_application", turn_id=int(turn_id),
                            outcome=action_state, succeeded=action_applied,
                        )
                        if action_applied:
                            # Re-resolve the shared envelope after mutation so
                            # posture and future capability-bearing action
                            # families reach presentation from authoritative
                            # state, not from the proposal alone.
                            post_effects = response_policy.effects
                            try:
                                from memory_v2_store import MemoryV2Repository
                                post_effects = MemoryV2Repository(
                                    self._memory_v2_shadow_writer.store,
                                ).capability_effects(str(self.character_id))
                            except Exception:
                                if (post_effects is not None
                                        and plan.proposal.family == "posture"):
                                    post_effects = replace(
                                        post_effects,
                                        posture_mode=(
                                            plan.proposal.value
                                            if plan.proposal.operation == "set" else None
                                        ),
                                    )
                            response_policy = replace(response_policy, effects=post_effects)
                            accepted, post_category, validated_spoken, validated_presentation = (
                                self._validate_governed_response(parsed_response, response_policy)
                            )
                            if not accepted:
                                parsed_response = self._governed_fallback_response(response_policy)
                                output_canonicalization_diagnostics.clear()
                                fallback_used = True
                                accepted, post_category, validated_spoken, validated_presentation = (
                                    self._validate_governed_response(parsed_response, response_policy)
                                )
                            validation_category = f"post_action_{post_category}"
                        else:
                            # No success narration can survive an application
                            # failure. Generate a deterministic no-action
                            # response from the still-authoritative old state.
                            response_policy = replace(
                                response_policy,
                                action_plan=None,
                                action_decision_category="action_application_failed",
                            )
                            parsed_response = self._governed_fallback_response(response_policy)
                            output_canonicalization_diagnostics.clear()
                            fallback_used = True
                            accepted, validation_category, validated_spoken, validated_presentation = (
                                self._validate_governed_response(parsed_response, response_policy)
                            )
                            if not accepted:
                                parsed_response = ParsedAssistantResponse(
                                    dialogue="*Remains quietly attentive.*",
                                    presentation=ResponsePresentationMetadata(speech_mode="nonverbal"),
                                    has_presentation_contract=True,
                                    response_mode="nonverbal_reaction",
                                    spoken_content="",
                                    contract_status="valid",
                                )
                                validated_spoken = ""
                                output_canonicalization_diagnostics.clear()
                                validated_presentation = parsed_response.presentation
                                validation_category = "action_application_safe_nonverbal"
                    if fallback_used or not accepted:
                        # Late action application/revalidation can replace an
                        # initially accepted draft too. It did not earn an offer.
                        self._release_impulse()
                    parsed_response = replace(
                        parsed_response, presentation=validated_presentation,
                    )
                    policy_spoken_text = validated_spoken
                    development_flight_recorder().mark(
                        "response_contract_validation",
                        turn_id=int(turn_id),
                        response_mode=parsed_response.response_mode,
                        contract_status=parsed_response.contract_status,
                        parse_success=parsed_response.contract_status == "valid",
                        accepted_generated=accepted and not fallback_used and response_policy.memory_realization is None,
                        accepted_direct=accepted and not repair_attempted and not fallback_used and response_policy.memory_realization is None,
                        memory_realization=(response_policy.memory_realized_response.mode if response_policy.memory_realized_response is not None else self._last_memory_authority_diagnostics.get("memory_realization", "provider_direct")),
                        reaction_status=(response_policy.memory_realized_response.reaction_status if response_policy.memory_realized_response is not None else "not_applicable"),
                        repair_attempted=repair_attempted,
                        repair_succeeded=repaired,
                        repair_skipped_reason=repair_skipped_reason,
                        primary_parse_failure=primary_parse_failure,
                        primary_format_normalization=primary_format_normalization,
                        primary_contract_admission=primary_contract_admission,
                        primary_semantic_rejection=primary_semantic_rejection,
                        retry_scheduled=repair_attempted,
                        fallback_used=fallback_used,
                        authoritative_no_evidence=(
                            response_policy.authoritative_memory_response is not None
                        ),
                        memory_authority=self._memory_authority,
                        category=validation_category,
                        fallback_category=str(fallback_category or "none"),
                        repair_disposition=(
                            "authoritative_no_evidence"
                            if response_policy.authoritative_memory_response is not None
                            else "fallback" if fallback_used
                            else "repaired" if repaired else "direct"
                        ),
                        outcome=(
                            "fallback" if fallback_used else
                            "repaired" if repaired else
                            "authoritative_no_evidence"
                            if response_policy.authoritative_memory_response is not None else
                            "accepted"
                        ),
                    )
                    if self._memory_authority == "v2":
                        self._last_memory_authority_diagnostics.update({
                            "repair_attempted": bool(repair_attempted),
                            "repair_succeeded": bool(repaired),
                            "repair_skipped_reason": repair_skipped_reason,
                            "fallback_used": bool(fallback_used),
                            "validation_category": str(validation_category),
                            "primary_format_normalization": primary_format_normalization,
                            "final_format_normalization": parsed_response.format_normalization or "none",
                            "authoritative_no_evidence": (
                                response_policy.authoritative_memory_response is not None
                            ),
                        })
                reply = parsed_response.dialogue
            finally:
                if (
                    self._memory_v2_shadow is not None
                    or self._memory_v2_shadow_writer is not None
                    or self._memory_recall_shadow is not None
                ):
                    setattr(self.conversation, "_capture_v1_retrieval_diagnostics", False)

            # Generation is complete; any deltas/early speech were provisional.
            # Persist canonical user evidence and apply governed continuity
            # before final response persistence, publication, or direct speech.
            if not commit_started:
                commit_boundary.enter_context(self._turn_commit_boundary(turn_id, cancel_event))
            if _scene_reaction is not None and not self._scene_reaction_state_is_current(_scene_reaction):
                raise _TurnCancelled()
            if continuity_result is None:
                if not action_applied:
                    self.conversation.save()
                continuity_result = self._observe_current_continuity(
                    canonical_user_message, canonical_user_index,
                )
            mutation_expected = bool(response_policy.changed_by_current_evidence)
            if policy_decision is not None and policy_decision.response_mode in {
                "sleep_start", "waking",
            }:
                mutation_expected = True
            mutation_applied = str(continuity_result.get("state") or "") in {
                "applied", "unchanged",
            }
            canonical_user_committed_to_state = mutation_applied
            if mutation_expected and not mutation_applied:
                self._release_impulse()
                # The proposal was safe enough to preview but did not become
                # authoritative. Suppress any success claim before canonical
                # assistant persistence, transport publication, or TTS.
                post_effects = None
                try:
                    from memory_v2_store import MemoryV2Repository
                    post_effects = MemoryV2Repository(
                        self._memory_v2_shadow_writer.store,
                    ).capability_effects(str(self.character_id))
                except Exception:
                    pass
                failure_policy = _ResponsePolicy(
                    effects=post_effects,
                    enforce_before_presentation=post_effects is not None,
                )
                parsed_response = self._governed_fallback_response(failure_policy)
                accepted_failure, _, policy_spoken_text, failure_presentation = (
                    self._validate_governed_response(parsed_response, failure_policy)
                )
                if accepted_failure:
                    parsed_response = replace(
                        parsed_response, presentation=failure_presentation,
                    )
                else:
                    parsed_response = ParsedAssistantResponse(
                        dialogue="*Remains quietly attentive.*",
                        presentation=ResponsePresentationMetadata(speech_mode="nonverbal"),
                        has_presentation_contract=True,
                        response_mode="nonverbal_reaction",
                        spoken_content="",
                        contract_status="valid",
                    )
                    policy_spoken_text = ""
                reply = parsed_response.dialogue
                development_flight_recorder().mark(
                    "mutation_response_consistency", turn_id=int(turn_id),
                    mutation_expected=True, mutation_applied=False,
                    response_suppressed=True,
                    application_outcome=str(continuity_result.get("state") or "failed"),
                )

            if not assistant_persisted:
                # Companion actions were already committed above; ordinary
                # user-evidence mutations were committed by the observer just
                # above. In both cases canonical narration now precedes Unity
                # publication and TTS.
                if generation_truth_scope is None:
                    self.conversation.add_assistant_message(reply)
                else:
                    self.conversation.add_assistant_message(reply, truth_scope=generation_truth_scope)
                self._prepare_impulse_publication(turn_id, cancel_event)
                try:
                    self.conversation.save()
                except ConversationPersistenceError as error:
                    assistant_persisted = error.committed
                    raise
                assistant_persisted = True
            _timing_log(f"full assistant response ready t={time.monotonic() - turn_started_at:.3f}s")
            self._mark_turn_timing("assistant_response_ready")
            self._remember_published_expression(parsed_response.presentation)
            automatic_input = self._automatic_expression_input(parsed_response, response_policy)
            self._emit(
                "assistant_response",
                content=reply,
                turn_id=turn_id,
                generation_origin=generation_origin,
                presentation=parsed_response.presentation.to_event_data() if parsed_response.presentation else None,
                has_presentation=parsed_response.has_presentation_contract,
                automatic_expression_pending=bool(automatic_input),
            )
            self._publish_impulse()
            publish_memory = getattr(self._memory_v2_authority, "publish", None)
            authority_turn = getattr(response_policy, "memory_authority_turn", None)
            if (callable(publish_memory) and authority_turn is not None
                    and not (mutation_expected and not mutation_applied)):
                publish_memory(authority_turn, source_fallback=bool(fallback_used or response_policy.memory_realized_response is not None))
            commit_boundary.close()
            self._offer_automatic_expression(automatic_input, turn_id, cancel_event)

            if speech_queue is not None:
                speech_queue.close()
            spoken_text = (
                policy_spoken_text
                if policy_spoken_text is not None
                else (
                    parsed_response.spoken_content
                    if parsed_response.spoken_content is not None
                    else self.clean_text_for_tts(reply)
                )
            )
            spoken_text = sanitize_spoken_unicode(spoken_text)
            self._record_generated_dialogue_structure(
                reply,
                spoken_text,
                output_canonicalization_diagnostics,
                turn_id=turn_id,
            )
            with self._speech_generation_lock:
                speech_still_current = (
                    not cancel_event.is_set() and turn_speech_generation == self._speech_generation
                )
                should_speak = bool(speak and spoken_text and not streamed_speech and speech_still_current)
                if should_speak:
                    self._emit("status", state="speaking", message="Speaking...")
                    self._emit("tts_state", state="starting")
                elif not streamed_speech and speech_still_current:
                    self._emit("tts_state", state="not_started")
            if should_speak:
                tts_submitted_at = time.monotonic()
                self._mark_turn_timing("tts_submitted", tts_submitted_at)
                _timing_log(f"TTS synthesis requested t={tts_submitted_at - turn_started_at:.3f}s")

                try:
                    # Prepared synthesis runs outside interruption locks;
                    # the helper protects only current audio dispatch.
                    started = self._dispatch_direct_speech_with_recovery(
                        spoken_text,
                        cancel_event=cancel_event,
                        speech_generation=turn_speech_generation,
                        require_prepared=_scene_reaction is not None,
                    )
                    _timing_log(
                        f"TTS synthesis/playback dispatch returned t={time.monotonic() - turn_started_at:.3f}s"
                    )
                    # Cancellation already emitted its authoritative stop.
                    # A late no-audio event would reopen the retired subtitle
                    # session. Identity and completion publication share the
                    # interruption lock; synthesis above remains outside it.
                    with self._speech_generation_lock:
                        if not cancel_event.is_set() and turn_speech_generation == self._speech_generation:
                            if started is False:
                                self._emit("tts_state", state="failed")
                            else:
                                # Preserve legacy speak() providers returning
                                # None after a successful current dispatch.
                                if not self._tts_reports_playback_start:
                                    self._emit("tts_state", state="playback_started")
                                self._emit("tts_state", state="speaking")

                except Exception:
                    # Speaking failure must not discard an otherwise valid
                    # assistant response or prevent memory/summary processing.
                    with self._speech_generation_lock:
                        if not cancel_event.is_set() and turn_speech_generation == self._speech_generation:
                            self._emit("error", source="tts", message="Speech could not be prepared.")
                            self._emit("tts_state", state="failed")

            # The optional V2 shadow observer can run local retrieval and
            # embedding work.  It must not sit between the complete-reply
            # event (which starts frontend reveal) and TTS initiation.
            semantic_admitted = not response_policy.hearing_input_unavailable
            if semantic_admitted:
                with self._turn_state_lock:
                    shadow_generation = self._turn_generation
                self._run_memory_v2_shadow(user_message)
                self._schedule_memory_recall_shadow(
                    user_message,
                    turn_id=turn_id,
                    generation=shadow_generation,
                    canonical_user_index=canonical_user_index,
                    memory_query_decision=memory_query_decision,
                )

            self._mark_proactive_checkins_responded(canonical_user_message)
            start_episode_rollover = getattr(
                self.conversation, "start_episode_compaction_rollover", None,
            )
            if callable(start_episode_rollover):
                start_episode_rollover()
            if continuity_result is None:
                continuity_result = self._observe_current_continuity(
                    canonical_user_message, canonical_user_index,
                )
            if semantic_admitted and _scene_event is None and self._active_state_contextual_shadow is not None:
                self._observe_contextual_active_state_shadow(canonical_user_message, canonical_user_index)
            if semantic_admitted and _scene_event is None and self._open_thread_contextual_shadow is not None:
                self._observe_contextual_open_thread_shadow(canonical_user_message, canonical_user_index)
            if (semantic_admitted and _scene_event is None
                    and self._canonical_observation_recovery is None):
                self._observe_durable_identity_name(canonical_user_message, canonical_user_index)
                self._observe_active_headwear(canonical_user_message, canonical_user_index)
            # Publish the final log/history pair only after the assistant save.
            # Independently saved user evidence may survive a failed response
            # and remains available to later canonical history snapshots.
            if _scene_reaction is None:
                self._emit(
                    "conversation_message", role="user", content=user_message,
                    message_id=canonical_message_identity(canonical_user_index, canonical_user_message),
                    timestamp=_canonical_message_timestamp(canonical_user_message),
                    generation_origin=generation_origin, turn_id=turn_id,
                )
            assistant_index = len(self.conversation.messages) - 1
            canonical_assistant_message = self.conversation.messages[assistant_index]
            self._emit(
                "conversation_message", role="assistant", content=reply,
                message_id=canonical_message_identity(assistant_index, canonical_assistant_message),
                timestamp=_canonical_message_timestamp(canonical_assistant_message),
                generation_origin=generation_origin, turn_id=turn_id,
            )

            v1_write_enabled = self._memory_authority == "v1"
            if (
                v1_write_enabled
                and self._general_memory_allowed(user_message, continuity_result)
            ):
                self.memory.process(user_message, reply)
            development_flight_recorder().mark(
                "memory_v1_write_boundary", turn_id=int(turn_id),
                memory_authority=self._memory_authority,
                v1_write_path_enabled=v1_write_enabled,
                v1_prompt_retrieval_entered=self._memory_authority == "v1",
            )
            self._emit(
                "memory_updated",
                count=len(getattr(self.memory, "memories", [])),
            )

            if self._memory_authority == "v1":
                self.conversation.update_summary()
            if self._turn_is_current(turn_id, cancel_event):
                self._emit("status", state="ready", message="Ready")

            development_flight_recorder().mark(
                "turn_terminal_outcome", turn_id=int(turn_id), outcome="published",
                succeeded=True, generation_origin=generation_origin,
            )

            return TurnResult(
                user_message=user_message,
                reply=reply,
                spoken_text=spoken_text,
                presentation=parsed_response.presentation,
            )

        except ConversationPersistenceError as error:
            commit_boundary.close()
            # Conversation has already restored the last saved records after
            # pre-commit failure, or retained the committed replacement after
            # a directory-sync failure. Never retry generation or observation.
            if speech_queue is not None and error.record_kind == "conversation":
                speech_queue.cancel()
                self.stop_speaking(interrupted=True)
            message = self._report_conversation_persistence_failure(
                error, turn_id=turn_id, generation_origin=generation_origin,
                assistant_persisted=assistant_persisted,
            )
            return TurnResult(user_message=user_message, error=message)

        except _TurnCancelled:
            commit_boundary.close()
            if not assistant_persisted and not canonical_user_committed_to_state:
                self._discard_unanswered_user_message(canonical_user_message, canonical_user_index)
            if speech_queue is not None:
                speech_queue.cancel()
            if _scene_reaction is None or turn_announced:
                self._emit("turn_cancelled", turn_id=turn_id, generation_origin=generation_origin)
                development_flight_recorder().mark(
                    "turn_terminal_outcome", turn_id=int(turn_id), outcome="cancelled",
                    succeeded=False, generation_origin=generation_origin,
                )
            return TurnResult(user_message=user_message, error="interrupted")

        except Exception as error:
            commit_boundary.close()
            if not assistant_persisted and not canonical_user_committed_to_state:
                self._discard_unanswered_user_message(canonical_user_message, canonical_user_index)
            # A later observer failure cannot turn an already committed
            # response into cancellation merely because PTT now owns audio.
            if not assistant_persisted and (
                cancel_event.is_set() or not self._turn_is_current(turn_id, cancel_event)
            ):
                if speech_queue is not None:
                    speech_queue.cancel()
                self._emit("turn_cancelled", turn_id=turn_id, generation_origin=generation_origin)
                development_flight_recorder().mark(
                    "turn_terminal_outcome", turn_id=int(turn_id), outcome="cancelled",
                    succeeded=False, generation_origin=generation_origin,
                )
                return TurnResult(user_message=user_message, error="interrupted")
            if isinstance(error, ModelTransportError):
                self.report_model_runtime_unavailable()
            message = str(error)
            from memory_v2_authority import MemoryV2AuthorityUnavailable
            if isinstance(error, _ContinuityMutationUnavailable):
                self._emit(
                    "error", source="current_continuity", message=message,
                    code="continuity_update_unavailable", recoverable=True,
                    turn_id=turn_id, provider_called=False,
                )
            elif isinstance(error, MemoryV2AuthorityUnavailable):
                self._emit("error", message=message, code="memory_lookup_unavailable",
                           recoverable=True, turn_id=turn_id)
            else:
                if _scene_reaction is not None:
                    message = "The scene change is saved, but its reaction is unavailable."
                    self._emit("error", message=message, code="scene_reaction_unavailable",
                               recoverable=True, turn_id=turn_id)
                else:
                    self._emit("error", message=message)
            self._emit("status", state="error", message="Error")
            development_flight_recorder().mark(
                "turn_terminal_outcome", turn_id=int(turn_id), outcome="error",
                succeeded=False, generation_origin=generation_origin,
            )
            return TurnResult(user_message=user_message, error=message)

        finally:
            commit_boundary.close()
            if not self._impulse_published:
                self._release_impulse()
            self._impulse_lease = None
            self._impulse_turn_owner = None
            self._impulse_receipt_ready = False
            if getattr(response_policy, "memory_authority_turn", None) is None:
                # Input which never entered memory preparation (for example
                # scene control or an early failure) still breaks adjacency.
                self._invalidate_memory_recall_anchor()
            # Independently committed user records survive a failed/cancelled
            # reaction. This bounded deterministic work holds no interruption
            # lock and dispatches no provider, action, speech or turn events.
            self._recover_canonical_observers()
            self._current_turn_started_at = None
            self._finish_turn(turn_id, cancel_event)
            self._turn_lock.release()

    def run_development_presentation_qa(self, response: str, *, delta_characters: int = 24) -> bool:
        """Exercise the real Unity/TTS event path without canonical persistence.

        The loopback host exposes this only behind an explicit development
        environment gate. Synthetic text never enters conversation, memory, or
        character data, but synthesis, playback IDs, interruption, alignment,
        lip sync, subtitles, and frontend transport remain the production path.
        """
        response = str(response or "").strip()
        if not response:
            return False
        turn_id, cancel_event, replaced_turn = self._claim_replacement_turn()
        with self._tts_state_lock:
            playback_active = self._active_tts_playback_id != 0
        if replaced_turn or playback_active or self._streaming_speech_queue is not None:
            self.stop_speaking(interrupted=True)
        self._turn_lock.acquire()
        try:
            if not self._turn_is_current(turn_id, cancel_event):
                return False
            started_at = time.monotonic()
            self._current_turn_started_at = started_at
            self._emit("turn_started", user_message="Development presentation QA", turn_id=turn_id)
            self._emit("status", state="thinking", message="Development presentation QA")
            width = max(8, int(delta_characters))
            for offset in range(0, len(response), width):
                if not self._turn_is_current(turn_id, cancel_event):
                    self._emit("turn_cancelled", turn_id=turn_id)
                    return False
                self._emit("assistant_delta", content=response[offset:offset + width], turn_id=turn_id)
                time.sleep(.025)
            self._emit("assistant_response", content=response, turn_id=turn_id, has_presentation=False)
            spoken = self.clean_text_for_tts(response)
            if not spoken:
                self._emit("tts_state", state="not_started")
                return True
            with self._speech_generation_lock:
                speech_generation = self._speech_generation
            self._emit("status", state="speaking", message="Speaking...")
            self._emit("tts_state", state="starting", streamed=False, turn_id=turn_id)
            with self._speech_generation_lock:
                if speech_generation != self._speech_generation:
                    return False
                started = self.tts.speak(spoken)
            if started is False:
                self._emit("tts_state", state="failed", turn_id=turn_id)
                return False
            if not self._tts_reports_playback_start:
                self._emit("tts_state", state="playback_started", streamed=False, turn_id=turn_id)
            self._emit("tts_state", state="speaking", streamed=False, turn_id=turn_id)
            self._emit("status", state="ready", message="Ready")
            return True
        finally:
            self._current_turn_started_at = None
            self._finish_turn(turn_id, cancel_event)
            self._turn_lock.release()

    def _mark_proactive_checkins_responded(self, canonical_user_message: object) -> None:
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id or not isinstance(canonical_user_message, dict):
            return
        try:
            from memory_v2_store.store import parse_timestamp_us
            from proactive_companion import mark_checkins_responded
            recorded_at_us = parse_timestamp_us(canonical_user_message.get("timestamp"))
            if recorded_at_us is not None:
                mark_checkins_responded(store, str(self.character_id), responded_at_us=recorded_at_us)
        except Exception:
            pass

    def proactive_eligibility(self, *, now_us: int | None = None, enabled: bool | None = None,
                              interval_seconds: int | None = None):
        """Return zero-or-one deterministic proactive reason without generation."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return None
        try:
            from conversation.temporal_context import clock_local_datetime
            from memory_v2_store import MemoryV2Repository
            from model_settings import proactive_behavior_status
            from proactive_companion import evaluate_proactive_eligibility
            if now_us is None:
                current = clock_local_datetime(getattr(self.conversation, "_clock", None))
                now_us = int(current.timestamp() * 1_000_000)
            proactive_status = proactive_behavior_status()
            if enabled is None:
                enabled = bool(proactive_status["enabled"])
            if interval_seconds is None:
                interval_seconds = int(proactive_status.get("interval_seconds", 3600))
            with self._tts_state_lock:
                playback_active = self._active_tts_playback_id != 0
            return evaluate_proactive_eligibility(
                MemoryV2Repository(store), str(self.character_id),
                getattr(self.conversation, "messages", ()), now_us=int(now_us),
                enabled=bool(enabled),
                minimum_interval_us=max(30, int(interval_seconds or 3600)) * 1_000_000,
                actively_conversing=(
                    self._turn_lock.locked() or self.provider_request_active()
                    or playback_active or self._streaming_speech_queue is not None
                ),
            )
        except Exception:
            return None

    def process_proactive_checkin(self, *, now_us: int | None = None, speak: bool = True) -> TurnResult:
        """Generate privately, then publish one eligible proactive message atomically.

        Background provider work is intentionally not a frontend turn.  The
        first externally visible lifecycle event is emitted only after a
        bounded response has passed parsing and canonical persistence.
        """
        self._assert_character_state_ownership()
        eligibility = self.proactive_eligibility(now_us=now_us)
        if eligibility is None or not eligibility.eligible or eligibility.reason is None:
            return TurnResult(user_message="", error=(eligibility.outcome if eligibility is not None else "unavailable"))
        if not self._turn_lock.acquire(blocking=False):
            return TurnResult(user_message="", error="busy")
        # Own cancellation while still private: PTT must invalidate provider
        # work before there is any frontend turn to announce. Never replace
        # input that has already claimed ownership and is waiting on this lock.
        with self._turn_state_lock:
            if self._active_turn_cancel is not None:
                self._turn_lock.release()
                return TurnResult(user_message="", error="busy")
            self._turn_generation += 1
            turn_id = self._turn_generation
            cancel_event = threading.Event()
            self._active_turn_id = turn_id
            self._active_turn_cancel = cancel_event
        with self._speech_generation_lock:
            turn_speech_generation = self._speech_generation
        character_id = str(self.character_id)
        store = self._memory_v2_shadow_writer.store
        attempted_at_us = int(now_us if now_us is not None else time.time() * 1_000_000)
        attempt_recorded = False
        published = False

        def record_attempt(outcome: str) -> None:
            nonlocal attempt_recorded
            if attempt_recorded:
                return
            attempt_recorded = True
            try:
                from proactive_companion import record_proactive_attempt
                record_proactive_attempt(
                    store, character_id,
                    eligibility.reason, attempted_at_us=attempted_at_us, outcome=outcome,
                )
            except Exception:
                # Attempt throttling is safety metadata, never a reason to
                # publish a failed draft or mutate canonical conversation.
                pass
            development_flight_recorder().mark(
                "proactive_attempt_outcome", turn_id=int(turn_id),
                outcome=outcome, succeeded=outcome == "published",
                generation_origin="proactive",
            )

        development_flight_recorder().mark(
            "proactive_attempt", turn_id=int(turn_id), outcome="started",
            generation_origin="proactive",
        )
        try:
            if not self._turn_is_current(turn_id, cancel_event):
                raise _TurnCancelled()
            configuration_error = self._model_configuration_error()
            if configuration_error is not None:
                record_attempt("configuration_error")
                return TurnResult(user_message="", error=configuration_error)
            from conversation.temporal_context import clock_local_datetime
            from proactive_companion import record_displayed_checkin, render_proactive_reason

            reason_block = render_proactive_reason(eligibility.reason)
            if reason_block is None:
                record_attempt("unsafe_reason")
                return TurnResult(user_message="", error="unsafe_reason")

            class _NoProactiveMemory:
                @staticmethod
                def get_relevant_memories(_query, max_memories=0):
                    return []

            truth_scope = self.truth_scope_provenance()
            # Empty input reads current authority without treating the
            # generated reason as new user evidence or a mutation proposal.
            capability_policy = self._response_policy("")
            if capability_policy.policy_error is not None:
                record_attempt("unsafe_reason")
                return TurnResult(user_message="", error="capability_policy_unavailable")
            proactive_policy = replace(
                capability_policy,
                context_block="\n".join(part for part in (
                    capability_policy.context_block, reason_block,
                ) if part),
                enforce_before_presentation=True,
                current_user_projection=reason_block,
            )
            proactive_memory_decision = None
            if self._memory_authority == "v2":
                from memory_query_decision import decide_memory_query
                proactive_memory_decision = decide_memory_query("")
                proactive_policy = self._apply_v2_retrospective_only_policy(
                    "",
                    proactive_policy,
                    active_truth_scope=truth_scope,
                    memory_query_decision=proactive_memory_decision,
                )
            proactive_system_prompt = self._response_character_prompt()
            if proactive_policy.memory_answer_requirement is not None:
                from memory_v2_answer_governance import memory_answer_system_prompt
                proactive_system_prompt = memory_answer_system_prompt(
                    proactive_system_prompt,
                    proactive_policy.memory_answer_requirement,
                )
            if governor_enabled(self._memory_authority):
                def context_check():
                    if not self._turn_is_current(turn_id, cancel_event):
                        raise _TurnCancelled()
                context = self.conversation.build_context(
                    _NoProactiveMemory(), reason_block, active_truth_scope=truth_scope,
                    long_term_memory_authority="v2", context_provider=self.llm,
                    provider_system_prompt=proactive_system_prompt,
                    context_character_id=str(self.character_id),
                    admitted_active_state_context=capability_policy.context_block,
                    memory_query_decision=proactive_memory_decision,
                    context_check=context_check,
                )
                self._record_context_plan()
            else:
                from config import (
                    V2_AUTHORITY_RECENT_CHARACTERS,
                    V2_AUTHORITY_RECENT_MESSAGES,
                )
                context = self.conversation.build_context(
                    _NoProactiveMemory(), "", active_truth_scope=truth_scope,
                    long_term_memory_authority=self._memory_authority,
                    recent_message_limit=(
                        V2_AUTHORITY_RECENT_MESSAGES
                        if self._memory_authority == "v2" else None
                    ),
                    recent_character_limit=(
                        V2_AUTHORITY_RECENT_CHARACTERS
                        if self._memory_authority == "v2" else None
                    ),
                    recent_context_policy=self._v2_recent_context_policy,
                    memory_query_decision=proactive_memory_decision,
                )
                # Exactly one governed reason is last. The model cannot select a
                # different hidden memory or state as its reason for acting.
                if capability_policy.context_block:
                    context.append({"role": "system", "content": capability_policy.context_block})
                context.append({"role": "user", "content": reason_block})
            if not self._turn_is_current(turn_id, cancel_event):
                raise _TurnCancelled()
            self._provider_request_begin()
            try:
                generated = canonicalize_model_output(
                    self.llm.generate(context, proactive_system_prompt),
                )
            finally:
                self._provider_request_end()
            if not self._turn_is_current(turn_id, cancel_event):
                raise _TurnCancelled()
            parsed = parse_assistant_response(generated, normalize_generated_dialogue=True)
            if not parsed.dialogue.strip():
                record_attempt("empty_output")
                return TurnResult(user_message="", error="empty_proactive_output")
            if len(parsed.dialogue.strip()) > 320:
                record_attempt("overlength_output")
                return TurnResult(user_message="", error="overlength_proactive_output")
            if proactive_policy.effects is not None:
                from capability_policy import normalize_response_for_capabilities
                parsed = normalize_response_for_capabilities(parsed, proactive_policy.effects)
            accepted, _, _, _ = self._validate_governed_response(parsed, proactive_policy)
            if not accepted:
                repaired = self._repair_governed_response(
                    reason_block, parsed, proactive_policy,
                )
                if not self._turn_is_current(turn_id, cancel_event):
                    raise _TurnCancelled()
                if repaired is not None:
                    parsed = repaired
                elif proactive_policy.memory_answer_requirement is not None:
                    from memory_v2_answer_governance import remove_unsupported_retrospective_sentences
                    projected = remove_unsupported_retrospective_sentences(
                        proactive_policy.memory_answer_requirement, parsed.dialogue,
                    )
                    if projected:
                        parsed = parse_assistant_response(projected)
                # Repairs and source-contained projections must pass every
                # policy lane, not merely the lane that rejected the draft.
            accepted, _, spoken, presentation = self._validate_governed_response(parsed, proactive_policy)
            if not accepted:
                record_attempt("unsafe_response")
                return TurnResult(user_message="", error="unsafe_response")
            from capability_policy import capability_requires_validation
            parsed = replace(
                parsed, presentation=presentation,
                has_presentation_contract=(
                    parsed.has_presentation_contract or (
                        proactive_policy.effects is not None
                        and capability_requires_validation(proactive_policy.effects)
                    )
                ),
            )
            reply = parsed.dialogue.strip()
            if not reply:
                record_attempt("empty_output")
                return TurnResult(user_message="", error="empty_proactive_output")
            if len(reply) > 320:
                record_attempt("overlength_output")
                return TurnResult(user_message="", error="overlength_proactive_output")

            # No provider or synthesis work belongs inside this boundary.
            # Re-read current authority as well as turn ownership: an old
            # envelope or resolved thread cannot license a new publication.
            with self._turn_commit_boundary(turn_id, cancel_event):
                if (str(self.character_id) != character_id
                        or self.truth_scope_provenance() != truth_scope
                        or self._response_policy("") != capability_policy):
                    raise _TurnCancelled()
                from memory_v2_store import MemoryV2Repository
                from model_settings import proactive_behavior_status
                from proactive_companion import evaluate_proactive_eligibility
                status = proactive_behavior_status()
                current_eligibility = evaluate_proactive_eligibility(
                    MemoryV2Repository(store), character_id, self.conversation.messages,
                    now_us=(int(now_us) if now_us is not None else int(
                        clock_local_datetime(getattr(self.conversation, "_clock", None)).timestamp() * 1_000_000
                    )),
                    enabled=bool(status["enabled"]),
                    minimum_interval_us=max(30, int(status.get("interval_seconds", 3600) or 3600)) * 1_000_000,
                )
                if not current_eligibility.eligible or current_eligibility.reason != eligibility.reason:
                    raise _TurnCancelled()
                if truth_scope is None:
                    self.conversation.add_assistant_message(reply)
                else:
                    self.conversation.add_assistant_message(reply, truth_scope=truth_scope)
                self.conversation.save()
                index = len(self.conversation.messages) - 1
                timestamp = self.conversation.messages[index].get("timestamp")
                from memory_v2_store.store import parse_timestamp_us
                displayed_at_us = parse_timestamp_us(timestamp)
                if displayed_at_us is None:
                    current = clock_local_datetime(getattr(self.conversation, "_clock", None))
                    displayed_at_us = int(current.timestamp() * 1_000_000)
                try:
                    record_displayed_checkin(
                        store, character_id, eligibility.reason,
                        displayed_at_us=displayed_at_us, conversation_index=index,
                        assistant_content=reply,
                    )
                except Exception:
                    # The canonical message is already durable and will appear
                    # in the next snapshot even if scheduler metadata fails.
                    development_flight_recorder().mark(
                        "proactive_checkin_record_failure", turn_id=int(turn_id),
                        outcome="store_error", generation_origin="proactive",
                    )
                record_attempt("published")
                published = True
                self._current_turn_started_at = time.monotonic()
                # Only a saved, governed reply starts the frontend lifecycle.
                self._emit(
                    "turn_started", user_message="", proactive=True,
                    generation_origin="proactive", turn_id=turn_id,
                )
                self._remember_published_expression(parsed.presentation)
                self._emit(
                    "assistant_response", content=reply, proactive=True,
                    generation_origin="proactive",
                    turn_id=turn_id,
                    presentation=parsed.presentation.to_event_data() if parsed.presentation else None,
                    has_presentation=parsed.has_presentation_contract,
                )
                self._emit(
                    "conversation_message", role="assistant", content=reply,
                    message_id=canonical_message_identity(index, self.conversation.messages[index]),
                    timestamp=timestamp,
                    proactive=True, generation_origin="proactive", turn_id=turn_id,
                )
            with self._speech_generation_lock:
                speech_current = (
                    not cancel_event.is_set() and turn_speech_generation == self._speech_generation
                )
                should_speak = bool(speak and spoken and speech_current)
                if should_speak:
                    self._emit("status", state="speaking", message="Speaking...")
                    self._emit(
                        "tts_state", state="starting", turn_id=turn_id,
                        generation_origin="proactive",
                    )
            if should_speak:
                try:
                    started = self._dispatch_direct_speech_with_recovery(
                        spoken, cancel_event=cancel_event,
                        speech_generation=turn_speech_generation, require_prepared=True,
                    )
                    # PTT may win immediately after dispatch returns. Publish
                    # audio state under the same identity boundary as stop;
                    # this section contains no synthesis or playback wait.
                    with self._speech_generation_lock:
                        if cancel_event.is_set() or turn_speech_generation != self._speech_generation:
                            pass  # Stop already owns presentation; no late no-audio fallback.
                        elif started is None:
                            self._emit(
                                "tts_state", state="not_started", turn_id=turn_id,
                                generation_origin="proactive",
                            )
                        elif started is False:
                            self._emit(
                                "tts_state", state="failed", turn_id=turn_id,
                                generation_origin="proactive",
                            )
                        else:
                            if not self._tts_reports_playback_start:
                                self._emit(
                                    "tts_state", state="playback_started", turn_id=turn_id,
                                    generation_origin="proactive",
                                )
                            self._emit(
                                "tts_state", state="speaking", turn_id=turn_id,
                                generation_origin="proactive",
                            )
                except Exception:
                    with self._speech_generation_lock:
                        if not cancel_event.is_set() and turn_speech_generation == self._speech_generation:
                            self._emit("error", source="tts", message="Speech could not be prepared.")
                            self._emit(
                                "tts_state", state="failed", turn_id=turn_id,
                                generation_origin="proactive",
                            )
            else:
                with self._speech_generation_lock:
                    if not cancel_event.is_set() and turn_speech_generation == self._speech_generation:
                        self._emit(
                            "tts_state", state="not_started", turn_id=turn_id,
                            generation_origin="proactive",
                        )
            if self._memory_authority == "v1":
                self.conversation.update_summary()
            with self._turn_state_lock:
                if self._turn_is_current_locked(turn_id, cancel_event):
                    self._emit("status", state="ready", message="Ready")
            development_flight_recorder().mark(
                "turn_terminal_outcome", turn_id=int(turn_id), outcome="published",
                succeeded=True, generation_origin="proactive",
            )
            return TurnResult(user_message="", reply=reply, spoken_text=spoken, presentation=parsed.presentation)
        except _TurnCancelled:
            record_attempt("interrupted")
            # A discarded private attempt never announced a frontend turn.
            return TurnResult(user_message="", error="interrupted")
        except ConversationPersistenceError as error:
            record_attempt("persistence_error")
            message = self._report_conversation_persistence_failure(
                error, turn_id=turn_id, generation_origin="proactive",
                assistant_persisted=(published or (
                    error.record_kind == "conversation" and error.committed
                )),
            )
            return TurnResult(user_message="", error=message)
        except Exception as error:
            if not published and not self._turn_is_current(turn_id, cancel_event):
                record_attempt("interrupted")
                return TurnResult(user_message="", error="interrupted")
            outcome = "provider_timeout" if isinstance(error, TimeoutError) else "provider_error"
            record_attempt(outcome)
            # A pre-publication background failure is private. If publication
            # already happened, preserve the ordinary terminal error boundary.
            if published:
                self._emit("error", source="proactive_companion", message=str(error))
                self._emit("status", state="error", message="Proactive check-in failed safely.")
                development_flight_recorder().mark(
                    "turn_terminal_outcome", turn_id=int(turn_id), outcome="error",
                    succeeded=False, generation_origin="proactive",
                )
            return TurnResult(user_message="", error=str(error))
        finally:
            self._current_turn_started_at = None
            if turn_id:
                self._finish_turn(turn_id, cancel_event)
            self._turn_lock.release()

    def set_tts_volume(self, volume: float) -> None:
        self.tts.set_volume(volume)
        self._emit("tts_state", state="volume_changed", volume=volume)

    def replace_llm(self, llm: Any) -> None:
        """Apply a provider reconfiguration before the next serialized turn."""
        if self._turn_lock.locked():
            raise RuntimeError("Assistant is still processing. Try again when it is ready.")
        self.llm = llm
        self._model_runtime_availability = "unconfigured" if getattr(llm, "is_available", True) is False else "unknown"
        # Conversation summarization and legacy-memory maintenance use the
        # same configured provider boundary.  A settings change therefore
        # refreshes their references too, without altering any Memory V2 data
        # or requiring a backend restart.
        self.memory.llm = llm
        self.conversation.llm = llm

    def can_reconfigure_model(self) -> bool:
        """Whether Settings may atomically begin a provider refresh now."""
        return not self._turn_lock.locked()

    def character_switch_busy(self) -> bool:
        """Whether switching identity would risk splitting a live turn."""
        with self._tts_state_lock:
            speaking = self._active_tts_playback_id != 0
        return self._turn_lock.locked() or speaking

    def _run_memory_v2_shadow(self, user_message: str) -> None:
        """Observe a completed V1 context build without changing the reply."""
        if self._memory_v2_shadow is not None:
            try:
                freshness_reader = getattr(self._memory_v2_shadow, "freshness", None)
                freshness = (
                    freshness_reader() if callable(freshness_reader)
                    else {"character_id": str(self.character_id)}
                )
                # The disposable legacy comparator can exist alongside
                # multi-character production state, but must never compare
                # one character's V1 context against another character's
                # cached shadow mapping.
                if freshness.get("character_id") == str(self.character_id):
                    v1_selected = getattr(
                        self.conversation, "_last_v1_prompt_diagnostics",
                        getattr(self.conversation, "_last_v1_retrieval_diagnostics", ()),
                    )
                    comparison = self._memory_v2_shadow.compare(
                        user_message, getattr(self.conversation, "messages", ()), v1_selected,
                    )
                    self._emit("memory_shadow", **comparison)
            except Exception as error:
                # Diagnostics are strictly fail-open for the user turn.
                self._emit("memory_shadow", shadow={"state": "invalid"}, error={"source": "memory_v2_shadow", "kind": type(error).__name__})
        if self._memory_v2_shadow_writer is not None:
            try:
                selected = getattr(
                    self.conversation, "_last_v1_prompt_diagnostics",
                    getattr(self.conversation, "_last_v1_retrieval_diagnostics", ()),
                )
                self._emit(
                    "memory_v2_parity",
                    **self._memory_v2_shadow_writer.compare(
                        user_message,
                        selected,
                        getattr(self.conversation, "messages", ()),
                        v1_latency_ms=getattr(
                            self.conversation,
                            "_last_v1_retrieval_latency_ms",
                            None,
                        ),
                    ),
                )
            except Exception as error:
                self._emit("memory_v2_parity", error={"source": "memory_v2_shadow_writer", "kind": type(error).__name__})

    def _schedule_memory_recall_shadow(
        self,
        user_message: str,
        *,
        turn_id: int,
        generation: int,
        canonical_user_index: int,
        memory_query_decision: Any = None,
    ) -> None:
        observer = self._memory_recall_shadow
        if observer is not None:
            try:
                selected = getattr(
                    self.conversation, "_last_v1_prompt_diagnostics",
                    getattr(self.conversation, "_last_v1_retrieval_diagnostics", ()),
                )
                observer.submit(
                    turn_id=turn_id,
                    generation=generation,
                    canonical_user_index=(
                        canonical_user_index if canonical_user_index is not None else -1
                    ),
                    query_text=user_message,
                    messages=getattr(self.conversation, "messages", ()),
                    v1_selected=selected,
                    v1_latency_ms=getattr(
                        self.conversation, "_last_v1_retrieval_latency_ms", None,
                    ),
                    accept=self._accept_memory_recall_shadow,
                    memory_query_decision=memory_query_decision,
                )
            except Exception:
                # The observational worker is never part of turn success.
                pass

    def _accept_memory_recall_shadow(self, record: dict[str, Any]) -> bool:
        """Reject a late result after any turn/character generation change."""
        if not isinstance(record, dict):
            return False
        with self._turn_state_lock:
            return (
                int(record.get("generation", -1)) == self._turn_generation
                and str(record.get("character_key", ""))
                == hashlib.sha256(str(self.character_id).encode("utf-8")).hexdigest()
            )

    def _observe_durable_identity_name(self, canonical_user_message: object, canonical_user_index: int) -> None:
        """Populate verified closed-schema V2 durable facts after V1 save.

        This is fail-open and has no prompt-facing effect.  The shadow writer
        receives the exact already-persisted user record and its canonical
        conversation index; unsupported text is ignored.
        """
        observer = getattr(self._memory_v2_shadow_writer, "observe_canonical_user_message", None)
        if callable(observer):
            try:
                observer(
                    canonical_user_message, conversation_index=canonical_user_index,
                    conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"),
                )
            except Exception:
                # Durable V2 shadow population cannot invalidate the canonical
                # turn after it has been saved.
                pass
        broader_observer = getattr(
            self._memory_v2_shadow_writer, "observe_canonical_user_durable_facts", None,
        )
        if callable(broader_observer):
            try:
                broader_observer(
                    canonical_user_message, conversation_index=canonical_user_index,
                    conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"),
                )
            except Exception:
                pass

    def _observe_active_headwear(self, canonical_user_message: object, canonical_user_index: int) -> None:
        """Populate only verified, explicit active headwear state after save."""
        observer = getattr(self._memory_v2_shadow_writer, "observe_canonical_user_active_state", None)
        if not callable(observer):
            return
        try:
            observer(
                canonical_user_message, conversation_index=canonical_user_index,
                conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"),
            )
        except Exception:
            # A derived-state observer cannot invalidate an already-saved turn.
            pass

    def _observe_current_continuity(self, canonical_user_message: object, canonical_user_index: int) -> dict[str, Any]:
        """Apply deterministic scoped continuity only after canonical save."""
        observer = getattr(self._memory_v2_shadow_writer, "observe_canonical_user_continuity", None)
        if not callable(observer):
            return {"state": "unavailable", "memory_v1_allowed": True}
        try:
            result = observer(
                canonical_user_message, conversation_index=canonical_user_index,
                conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"),
            )
            if not isinstance(result, dict):
                return {"state": "failed", "memory_v1_allowed": True}
            scope = result.get("truth_scope")
            if result.get("scope_changed") and isinstance(scope, dict):
                self._emit(
                    "truth_scope_changed",
                    scope_kind=str(scope.get("kind") or "real_world"),
                    scope_label=(str(scope.get("label")) if scope.get("label") else ""),
                )
            if result.get("state") in {"applied", "unchanged"}:
                try:
                    self._emit("continuity_changed", continuity=self.continuity_snapshot())
                except Exception:
                    pass
            intents = result.get("extraction_intents")
            if not isinstance(intents, (tuple, list)):
                intents = ()
            development_flight_recorder().mark(
                "continuity_extraction",
                method=str(result.get("extraction_method") or "none"),
                outcome=str(result.get("extraction_outcome") or "none"),
                confidence=str(result.get("extraction_confidence") or "none"),
                intent=(str(intents[0]) if len(intents) == 1 else ("multiple" if intents else "none")),
                intent_count=len(intents),
            )
            return result
        except Exception:
            return {"state": "failed", "memory_v1_allowed": True}

    def truth_scope_status(self) -> dict[str, str]:
        """Return only safe display data from the authoritative active scope."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return {"kind": "real_world", "label": ""}
        try:
            from memory_v2_store import MemoryV2Repository
            scope = MemoryV2Repository(store).active_truth_scope(str(self.character_id))
            return {"kind": scope.kind, "label": scope.label if scope.kind == "scenario" else ""}
        except Exception:
            return {"kind": "real_world", "label": ""}

    def memory_view_page(
        self,
        *,
        character_id: str,
        lane: str = "v1",
        query: str = "",
        status_filter: str = "current",
        scope_filter: str = "applicable",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one bounded character-owned memory inspection page.

        The existing turn lock is the character-rebind boundary as well as the
        canonical mutation boundary.  Viewer work is intentionally short and
        cannot race a turn or return rows from a retired character owner.
        """
        if not self._turn_lock.acquire(blocking=False):
            return {
                "character_id": str(character_id or ""), "lane": str(lane or "v1"),
                "query": str(query or ""), "status_filter": str(status_filter or "current"),
                "scope_filter": str(scope_filter or "applicable"), "offset": int(offset or 0),
                "limit": int(limit or 20), "has_more": False, "items": [],
                "availability": "busy",
                "authority_label": "Memory Viewer waiting for the current turn boundary",
                "warning": "Memory inspection is briefly unavailable while the current turn is being saved.",
            }
        try:
            self._assert_character_state_ownership()
            if str(character_id or "") != str(self.character_id or ""):
                raise RuntimeError("Memory viewer request belongs to a stale character selection.")
            return self._memory_viewer().page(
                lane=lane, query=query, status_filter=status_filter,
                scope_filter=scope_filter, limit=limit, offset=offset,
            )
        finally:
            self._turn_lock.release()

    def memory_view_detail(
        self,
        *,
        character_id: str,
        lane: str,
        record_id: str,
        limit: int = 8,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read bounded detail tied to the current character generation."""
        if not self._turn_lock.acquire(blocking=False):
            return {
                "character_id": str(character_id or ""), "lane": str(lane or ""),
                "record_id": str(record_id or ""), "limit": int(limit or 8),
                "offset": int(offset or 0), "availability": "busy",
                "has_more": False, "detail": {},
                "warning": "Memory detail is briefly unavailable while the current turn is being saved.",
            }
        try:
            self._assert_character_state_ownership()
            if str(character_id or "") != str(self.character_id or ""):
                raise RuntimeError("Memory detail request belongs to a stale character selection.")
            return self._memory_viewer().detail(lane=lane, record_id=record_id, limit=limit, offset=offset)
        finally:
            self._turn_lock.release()

    def _memory_viewer(self):
        """Construct under the existing turn/character boundary, without recovery work."""
        from memory_viewer import MemoryViewer
        development_v2 = self._memory_authority == "v2"
        recovery = self._canonical_observation_recovery
        messages = (self.conversation.messages[:self.conversation._persisted_message_count]
                    if development_v2 else self.conversation._semantic_context_messages())
        return MemoryViewer(
            self.memory, getattr(self._memory_v2_shadow_writer, "store", None), str(self.character_id),
            episode_cache=getattr(self.conversation, "episode_compaction_cache", None),
            canonical_messages=messages, active_truth_scope=self.truth_scope_provenance(),
            development_v2_authority=development_v2,
            recovery_status=recovery.inspection_status() if recovery is not None else "current",
        )

    def apply_memory_view_mutation(
        self,
        *,
        character_id: str,
        action: str,
        record_id: str,
        content: str = "",
        category: str = "",
        importance: int = 5,
        command_id: str = "",
        character_session: str | None = None,
    ) -> dict[str, Any]:
        """Apply one narrow semantic edit without bypassing V1/V2 ownership."""
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("Memory editing is unavailable until the current turn is saved.")
        try:
            self._assert_character_state_ownership()
            if character_session is not None:
                self.require_character_binding(character_id, character_session)
            if str(character_id or "") != str(self.character_id or ""):
                raise RuntimeError("Memory editor request belongs to a stale character selection.")
            from memory_viewer import MemoryViewer
            writer = self._memory_v2_shadow_writer
            store = getattr(writer, "store", None)
            return MemoryViewer(
                self.memory, store, str(self.character_id),
            ).mutate(
                action=action, record_id=record_id, content=content,
                category=category, importance=importance,
                command_id=command_id,
            )
        finally:
            self._turn_lock.release()

    def truth_scope_provenance(self) -> dict[str, str] | None:
        """Return stable canonical provenance, never a prompt display label."""
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return None
        try:
            from conversation.truth_scope import canonical_truth_scope
            from memory_v2_store import MemoryV2Repository
            scope = MemoryV2Repository(store).active_truth_scope(str(self.character_id))
            return canonical_truth_scope(scope.kind, scope.truth_scope_id)
        except Exception:
            # Scope provenance is fail-closed: an unavailable authority must
            # not be guessed into a canonical real-world record.
            return None

    def continuity_snapshot(self) -> dict[str, Any]:
        """Return bounded display/control state without provenance internals."""
        self._assert_character_state_ownership()
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return {
                "scope": {"kind": "real_world", "label": ""},
                "activity": None,
                "companion_activity": None,
                "scene_subjects": [],
                "scene_relations": [],
                "capability_effects": [],
                "profile_baseline": [],
                "open_threads": [],
                "revision": "unavailable",
            }
        from current_continuity import USER_ACTIVITY_KEY
        from memory_v2_store import MemoryV2Repository

        repository = MemoryV2Repository(store)
        scope = repository.active_truth_scope(str(self.character_id))
        activity = repository.lookup_active_state(
            str(self.character_id), USER_ACTIVITY_KEY,
        ).state
        companion_activity = repository.lookup_actor_state(
            str(self.character_id), "companion", "activity",
        ).state
        scene_subjects = []
        scene_labels: dict[str, str] = {}
        subject_rows = repository.list_scene_subjects(str(self.character_id), limit=12)
        for subject_index, subject in enumerate(subject_rows):
            attribute_records = repository.lookup_scene_attributes(
                str(self.character_id), subject.scene_subject_id,
            )
            attributes = {
                record.subject_key.rsplit(".", 1)[-1]: record.value
                for record in attribute_records
            }
            kind = str(attributes.pop("kind", "object"))
            label = " ".join(
                value for value in (str(attributes.get("color") or ""), kind) if value
            )
            scene_labels[subject.scene_subject_id] = label or "scene subject"
            details = [f"{key.replace('_', ' ')}: {value}" for key, value in sorted(attributes.items())]
            latest_confirmation_us = max(
                (record.last_confirmed_at_us or 0 for record in attribute_records), default=0,
            )
            confirmed = (
                datetime.fromtimestamp(latest_confirmation_us / 1_000_000, timezone.utc).isoformat(
                    timespec="minutes",
                ).replace("+00:00", "Z")
                if latest_confirmation_us > 0 else ""
            )
            scene_subjects.append({
                "kind": kind,
                "label": label or kind,
                "lifecycle": subject.lifecycle_state,
                "can_remove": subject.lifecycle_state == "dormant",
                "remove_token": f"subject:{subject_index}" if subject.lifecycle_state == "dormant" else "",
                "scope": "Real world" if scope.kind == "real_world" else f"RP: {scope.label}",
                "summary": ", ".join(details)[:180],
                "conditions": [
                    f"{key.replace('_', ' ')}: {value}"
                    for key, value in sorted(attributes.items())
                    if key in {"condition", "stain", "wet"}
                    and not (key == "wet" and str(value).casefold() == "false")
                ][:4],
                "confirmed": confirmed,
            })
        scene_relations = []
        relation_rows = repository.list_scene_relations(str(self.character_id), limit=16)
        for relation_index, relation in enumerate(relation_rows):
            effect = str(relation.semantic_family or "").replace("_", " ")
            scene_relations.append({
                "target": (
                    relation.target if relation.target_kind == "actor"
                    else scene_labels.get(relation.target, "scene subject")
                ),
                "facet": relation.facet or "",
                "side": relation.side or "",
                "predicate": relation.predicate,
                "cause": relation.cause + (f" on {relation.locus}" if relation.locus else ""),
                "locus": relation.locus or "",
                "effect": effect,
                "quantity": relation.quantity or 0,
                "scope": "Real world" if scope.kind == "real_world" else f"RP: {scope.label}",
                "can_clear": True,
                "clear_token": f"relation:{relation_index}",
            })
        effects = repository.capability_effects(str(self.character_id))
        user_effects = repository.capability_effects(str(self.character_id), target="user")
        capability_effects = []
        for target_effects in (effects, user_effects):
            cause_rows = {
                ("perception", "vision"): target_effects.vision_causes,
                ("perception", "hearing"): target_effects.hearing_causes,
                ("perception", "smell"): target_effects.smell_causes,
                ("perception", "taste"): target_effects.taste_causes,
                ("perception", "touch"): target_effects.touch_causes,
                ("communication", "speech"): target_effects.speech_causes,
                ("manipulation", "hands"): target_effects.hand_causes,
                ("manipulation", "left_arm"): target_effects.hand_causes,
                ("manipulation", "right_arm"): target_effects.hand_causes,
                ("locomotion", "mode"): target_effects.locomotion_causes,
                ("locomotion", "constraint"): target_effects.locomotion_causes,
            }
            capability_effects.extend({
                "target": effect.target,
                "domain": effect.domain,
                "capability": effect.capability,
                "state": effect.state,
                "severity": effect.severity,
                "source_count": len(effect.source_relation_ids),
                "cause": ", ".join(cause_rows.get((effect.domain, effect.capability), ()))[:96],
            } for effect in target_effects.effects)
        profile_baseline = []
        try:
            from character_scene_profile import effective_profile_worn_items
            profile_baseline = [
                {"relation": "normally_worn", "item": item.label, "region": item.region,
                 "provenance": "character_profile"}
                for item in effective_profile_worn_items(repository, str(self.character_id))
            ][:12]
        except Exception:
            profile_baseline = []
        threads = repository.list_open_threads(str(self.character_id)).threads[:6]
        revision_parts = [
            hashlib.sha256(
                f"{self.character_id}:{scope.truth_scope_id}".encode("utf-8"),
            ).hexdigest()[:32],
            str(scope.last_active_at_us),
            str(activity.last_confirmed_at_us if activity is not None else 0),
            str(companion_activity.last_confirmed_at_us if companion_activity is not None else 0),
            str(len(scene_subjects)),
            str(len(scene_relations)),
            *(str(item.valid_from_us) for item in relation_rows),
            str(len(profile_baseline)),
            effects.vision_mode, effects.speech_mode, effects.hands_mode,
            effects.hearing_mode, effects.smell_mode, effects.taste_mode, effects.touch_mode,
            effects.locomotion_mode, effects.awareness_mode,
            user_effects.vision_mode, user_effects.speech_mode, user_effects.hands_mode,
            user_effects.hearing_mode, user_effects.smell_mode,
            user_effects.taste_mode, user_effects.touch_mode,
            user_effects.locomotion_mode, user_effects.awareness_mode,
            str(len(threads)),
            *(str(item.get("confirmed", "")) for item in scene_subjects),
            *(str(item.last_mentioned_at_us) for item in threads),
        ]
        return {
            "scope": {
                "kind": scope.kind,
                "label": scope.label if scope.kind == "scenario" else "",
            },
            "activity": (
                {"value": activity.value, "can_clear": True}
                if activity is not None else None
            ),
            "companion_activity": (
                {"value": companion_activity.value, "can_clear": False}
                if companion_activity is not None else None
            ),
            "scene_subjects": scene_subjects,
            "scene_relations": scene_relations,
            "capability_effects": capability_effects,
            "profile_baseline": profile_baseline,
            "open_threads": [
                {
                    "kind": item.kind,
                    "description": item.description,
                    # A bounded snapshot-relative selector is sufficient
                    # because mutations also require the exact revision. Do
                    # not expose canonical thread/provenance IDs to Unity.
                    "action_token": str(index),
                }
                for index, item in enumerate(threads)
            ],
            "revision": ":".join(revision_parts),
        }

    def apply_continuity_control(
        self,
        *,
        command_id: str,
        action: str,
        expected_revision: str,
        action_token: str = "",
        character_id: str | None = None,
        character_session: str | None = None,
    ) -> dict[str, Any]:
        """Apply silent controls or one serialized immersive scene gesture."""
        self._assert_character_state_ownership()
        if character_session is not None:
            self.require_character_binding(character_id, character_session)
        if str(action or "") != "interact_scene_relation":
            return self._apply_continuity_control_impl(
                command_id=command_id, action=action,
                expected_revision=expected_revision, action_token=action_token,
                expected_character_id=character_id, expected_character_session=character_session,
            )
        # Reserve ownership before waiting. Never reclaim a new turn after
        # the mandatory save: PTT/replacement during that gap must still win.
        character_id = str(self.character_id or "")
        turn_id, cancel_event, _ = self._claim_replacement_turn()
        self.stop_speaking(interrupted=True)
        try:
            with self._scene_ui_interaction_lock:
                result = self._apply_continuity_control_impl(
                    command_id=command_id, action=action,
                    expected_revision=expected_revision, action_token=action_token,
                    wait_for_turn=True, expected_character_id=character_id,
                    expected_character_session=character_session,
                )
                committed = result.pop("_scene_event", None)
                if committed is not None:
                    event, index, message = committed
                    reaction = self.process_text_turn(
                        event.model_text, speak=True, input_source="scene_ui",
                        _scene_reaction=_SceneReaction(
                            event, index, message, character_id, result["continuity"]["revision"],
                            turn_id, cancel_event,
                        ),
                    )
                    result["reaction"] = {
                        "attempted": True, "published": reaction.succeeded,
                        "error": None if reaction.succeeded else str(reaction.error or "unavailable"),
                    }
                    # Keep the accepted operation's snapshot. The reaction
                    # has released _turn_lock; reading here could race a
                    # replacement owner's SQLite work or character rebind.
                elif result.get("canonical_event", {}).get("state") == "committed" and result["outcome"] == "applied":
                    with self._turn_state_lock:
                        if self._turn_is_current_locked(turn_id, cancel_event):
                            self._emit("status", state="ready", message="Ready")
                return result
        finally:
            self._finish_turn(turn_id, cancel_event)

    def _complete_scene_control_record(self, result, payload, *, event_id, character_id):
        """Complete just this SQLite/JSON gap, while the caller owns _turn_lock."""
        from scene_ui_event import load_scene_ui_control_record

        command_id = result["command_id"]
        try:
            event, message = load_scene_ui_control_record(
                payload.get("scene_event"), command_id=command_id,
                control_event_id=event_id, character_id=character_id,
            )
        except ValueError:
            # Older control records did not capture wording/scope. Guessing
            # from the now-cleared relation would manufacture provenance.
            result.update(outcome="applied_record_incomplete", canonical_event={"state": "unrecoverable"})
            self._emit(
                "error", code="scene_event_record_unavailable", recoverable=True,
                command_id=command_id,
                message="The scene change was applied, but its original event record is unavailable.",
            )
            return result

        messages = self.conversation.messages
        matches = [index for index, row in enumerate(messages)
                   if isinstance(row.get("origin"), dict)
                   and row["origin"].get("control_event_id") == event_id]
        if matches:
            if len(matches) != 1 or messages[matches[0]] != message:
                raise RuntimeError("Stored scene event identity is inconsistent; its data was preserved.")
            index = matches[0]
            if not self.conversation.is_message_persisted(index, message):
                raise RuntimeError("The scene event has not been committed safely.")
            result["canonical_event"] = {
                "state": "committed", "message_id": canonical_message_identity(index, message),
            }
            return result

        # This message records an accepted operation even if cancellation has
        # already won the optional reaction. It is never conversational input.
        try:
            self.conversation.add_user_message(
                message["content"], truth_scope=message["truth_scope"], origin=message["origin"],
            )
            index = len(messages) - 1
            messages[index]["timestamp"] = message["timestamp"]
            self.conversation.save()
        except ConversationPersistenceError as error:
            result.update(
                outcome="applied_durability_unconfirmed" if error.committed else "applied_record_incomplete",
                canonical_event={
                    "state": "committed" if error.committed else "pending",
                    "replacement_committed": error.committed, "persistence_stage": error.stage,
                },
            )
            if error.committed:
                result["canonical_event"]["message_id"] = canonical_message_identity(index, message)
            # No reaction turn has been announced; this is a command failure,
            # not a fabricated turn completion. Replacement remains committed.
            self._emit(
                "error", source="conversation_persistence", code="conversation_persistence_failed",
                message=str(error), command_id=command_id, generation_origin="scene_ui",
                record_kind=error.record_kind, persistence_stage=error.stage,
                replacement_committed=error.committed, assistant_persisted=False,
                recoverable=True,
            )
            self._emit("status", state="error", message=str(error))
            return result

        result["canonical_event"] = {
            "state": "committed", "message_id": canonical_message_identity(index, message),
            "recovered": bool(result["duplicate"]),
        }
        self._emit(
            "conversation_message", role="user", content=message["content"],
            message_id=canonical_message_identity(index, message), timestamp=message["timestamp"],
            generation_origin="scene_ui", command_id=command_id,
        )
        # Canonical presence is the durable at-most-once marker. Recovery or
        # redelivery never reoffers the optional reaction, even after a crash.
        if not result["duplicate"] and event.reaction_opportunity:
            result["_scene_event"] = (event, index, messages[index])
        return result

    def _apply_continuity_control_impl(
        self,
        *,
        command_id: str,
        action: str,
        expected_revision: str,
        action_token: str = "",
        wait_for_turn: bool = False,
        expected_character_id: str | None = None,
        expected_character_session: str | None = None,
    ) -> dict[str, Any]:
        """Apply one explicit, retry-safe structured continuity mutation."""
        try:
            command_id = str(uuid.UUID(str(command_id)))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("continuity control requires a valid command ID") from error
        action = str(action or "")
        if action not in {
            "clear_activity", "resolve_thread", "cancel_thread", "leave_scenario",
            "clear_scene_relation", "interact_scene_relation", "retire_scene_subject",
        }:
            raise ValueError("continuity control action is not governed")
        if not self._turn_lock.acquire(blocking=wait_for_turn):
            raise RuntimeError("Assistant is still processing. Try again when it is ready.")
        try:
            if expected_character_session is not None:
                self.require_character_binding(expected_character_id, expected_character_session)
            self._retire_automatic_expression()
            self._assert_character_state_ownership()
            if expected_character_id is not None and expected_character_id != str(self.character_id):
                raise RuntimeError("The scene command belongs to a previous character.")
            writer = self._memory_v2_shadow_writer
            store = getattr(writer, "store", None)
            if store is None or not self.character_id:
                raise RuntimeError("Current continuity is unavailable.")
            from current_continuity import USER_ACTIVITY_KEY
            from memory_v2_store import (
                MemoryV2Repository,
                OpenThreadProposal,
                OpenThreadProposalOperation,
            )

            character_id = str(self.character_id)
            repository = MemoryV2Repository(store)
            event_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"aifren:continuity-control:{character_id}:{command_id}",
            ))
            existing = store.connection.execute(
                "SELECT payload_json FROM events WHERE character_id=? AND event_id=?",
                (character_id, event_id),
            ).fetchone()
            if existing is not None:
                try:
                    payload = json.loads(existing["payload_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError("Stored continuity acknowledgement is invalid.") from error
                if not isinstance(payload, dict):
                    raise RuntimeError("Stored continuity acknowledgement is invalid.")
                if (payload.get("action") != action
                        or str(payload.get("action_token") or "") != str(action_token or "")
                        or ("expected_revision" in payload
                            and payload["expected_revision"] != str(expected_revision or ""))):
                    raise ValueError("command ID was already used for a different continuity action")
                result = {
                    "accepted": True,
                    "duplicate": True,
                    "outcome": str(payload.get("outcome") or "applied"),
                    "command_id": command_id,
                    "continuity": self.continuity_snapshot(),
                }
                if action == "interact_scene_relation" and result["outcome"] == "applied":
                    return self._complete_scene_control_record(
                        result, payload, event_id=event_id, character_id=character_id,
                    )
                return result

            before = self.continuity_snapshot()
            if str(expected_revision or "") != str(before["revision"]):
                raise RuntimeError("Current continuity changed; refresh before applying this action.")
            scope = repository.active_truth_scope(character_id)
            outcome = "applied"
            target = str(action_token or "")
            thread = None
            relation = None
            scene_subject = None
            if action in {"resolve_thread", "cancel_thread"}:
                try:
                    target_index = int(target)
                except (TypeError, ValueError):
                    target_index = -1
                current_threads = repository.list_open_threads(character_id).threads[:6]
                thread = (
                    current_threads[target_index]
                    if 0 <= target_index < len(current_threads) else None
                )
                if thread is None or thread.truth_scope_id != scope.truth_scope_id:
                    raise RuntimeError("The selected Open Thread is no longer current.")
                content = (
                    ("Resolve" if action == "resolve_thread" else "Cancel")
                    + f" open thread: {thread.description}"
                )
                excerpt_start = content.index(thread.description)
                excerpt_end = excerpt_start + len(thread.description)
            elif action == "clear_activity":
                content = "Clear current user activity."
                excerpt_start, excerpt_end = 0, len(content)
            elif action in {"clear_scene_relation", "interact_scene_relation"}:
                match = re.fullmatch(r"relation:(\d{1,2})", target)
                relation_index = int(match.group(1)) if match is not None else -1
                relation_rows = repository.list_scene_relations(character_id, limit=16)
                relation = relation_rows[relation_index] if 0 <= relation_index < len(relation_rows) else None
                if relation is None or relation.truth_scope_id != scope.truth_scope_id:
                    raise RuntimeError("The selected scene relation is no longer current.")
                content = (
                    "Perform the selected current scene interaction."
                    if action == "interact_scene_relation"
                    else "Administratively clear the selected current scene relation."
                )
                excerpt_start, excerpt_end = 0, len(content)
            elif action == "retire_scene_subject":
                match = re.fullmatch(r"subject:(\d{1,2})", target)
                subject_index = int(match.group(1)) if match is not None else -1
                subject_rows = repository.list_scene_subjects(character_id, limit=12)
                scene_subject = subject_rows[subject_index] if 0 <= subject_index < len(subject_rows) else None
                if (scene_subject is None or scene_subject.truth_scope_id != scope.truth_scope_id
                        or scene_subject.lifecycle_state != "dormant"):
                    raise RuntimeError("The selected scene subject is active or no longer current.")
                content = "Administratively remove the selected dormant subject from the current scene."
                excerpt_start, excerpt_end = 0, len(content)
            else:
                content = "Return to real world."
                excerpt_start, excerpt_end = 0, len(content)

            with store.transaction():
                sequence = store.connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE character_id=?",
                    (character_id,),
                ).fetchone()[0]
                # Store the exact idempotent command identity. Outcome is
                # finalized below in the same transaction.
                store.add_event(
                    character_id, event_id, sequence,
                    event_type="continuity_control", actor_kind="user",
                    content_text=content,
                    payload={"action": action, "action_token": target, "outcome": outcome},
                    source_origin="unity_continuity_control",
                    source_reference=f"continuity-control:{command_id}",
                )
                if action == "clear_activity":
                    changed = store.clear_active_state(
                        character_id, subject_key=USER_ACTIVITY_KEY,
                        evidence_event_id=event_id,
                        truth_scope_id=scope.truth_scope_id,
                    )
                    outcome = "applied" if changed else "unchanged"
                elif action in {"resolve_thread", "cancel_thread"}:
                    operation = "resolve" if action == "resolve_thread" else "cancel"
                    updates = store.apply_open_thread_proposal(
                        character_id,
                        OpenThreadProposal((OpenThreadProposalOperation(
                            operation, thread.thread_id, excerpt_start, excerpt_end,
                        ),)),
                        evidence_event_id=event_id,
                        truth_scope_id=scope.truth_scope_id,
                    )
                    outcome = "applied" if updates else "unchanged"
                elif action in {"clear_scene_relation", "interact_scene_relation"}:
                    from memory_v2_store.scene_relation_contract import SceneRelationProposal
                    assert relation is not None
                    changed = store.apply_scene_relation_proposals(
                        character_id,
                        (SceneRelationProposal(
                            "clear", relation.target, relation.facet, relation.predicate,
                            relation.cause_kind, relation.cause,
                            excerpt_start_cp=excerpt_start, excerpt_end_cp=excerpt_end,
                            target_kind=relation.target_kind, side=relation.side,
                            cause_subject_ref=relation.cause_subject_id, locus=relation.locus,
                        ),),
                        evidence_event_id=event_id, truth_scope_id=scope.truth_scope_id,
                    )
                    store.synchronize_scene_ownership_mirrors(
                        character_id, evidence_event_id=event_id,
                        truth_scope_id=scope.truth_scope_id,
                    )
                    outcome = "applied" if changed else "unchanged"
                elif action == "retire_scene_subject":
                    from memory_v2_store import ActiveSceneSubjectRetirement, ActiveStateProposal
                    assert scene_subject is not None
                    updates = store.apply_active_state_proposal(
                        character_id,
                        ActiveStateProposal((), retirements=(ActiveSceneSubjectRetirement(
                            scene_subject.scene_subject_id, excerpt_start, excerpt_end,
                        ),)),
                        evidence_event_id=event_id, truth_scope_id=scope.truth_scope_id,
                    )
                    outcome = "applied" if updates and bool(updates[0].get("retired")) else "unchanged"
                elif scope.kind == "scenario":
                    store.deactivate_to_real_world(
                        character_id, evidence_event_id=event_id,
                        evidence_excerpt_start_cp=excerpt_start,
                        evidence_excerpt_end_cp=excerpt_end,
                    )
                else:
                    outcome = "unchanged"
                payload = {
                    "action": action, "action_token": target, "outcome": outcome,
                }
                if action == "interact_scene_relation" and outcome == "applied":
                    from conversation.temporal_context import clock_local_datetime
                    from conversation.truth_scope import canonical_truth_scope
                    from scene_ui_event import scene_ui_clear_event
                    # Capture inside the same transaction as the mutation.
                    # This payload, not future state, owns retry/restart prose.
                    payload["expected_revision"] = str(expected_revision or "")
                    target_label = None
                    if relation.target_kind == "scene" and relation.locus:
                        attributes = {row.subject_key.rsplit(".", 1)[-1]: row.value
                            for row in repository.lookup_scene_attributes(character_id, relation.target)}
                        target_label = " ".join(attributes[key] for key in ("color", "kind") if key in attributes)
                    event_options = {"target_label": target_label} if target_label is not None else {}
                    payload["scene_event"] = scene_ui_clear_event(relation, scope, **event_options).control_record(
                        command_id=command_id, control_event_id=event_id, character_id=character_id,
                        truth_scope=canonical_truth_scope(scope.kind, scope.truth_scope_id),
                        timestamp=clock_local_datetime(getattr(self.conversation, "_clock", None)).isoformat(),
                    )
                store.connection.execute(
                    "UPDATE events SET payload_json=? WHERE character_id=? AND event_id=?",
                    (json.dumps(payload, sort_keys=True), character_id, event_id),
                )
            result = {
                "accepted": True,
                "duplicate": False,
                "outcome": outcome,
                "command_id": command_id,
                "continuity": self.continuity_snapshot(),
            }
            if action == "interact_scene_relation" and outcome == "applied":
                self._emit(
                    "continuity_changed", continuity=result["continuity"],
                    generation_origin="scene_ui", command_id=command_id,
                )
                return self._complete_scene_control_record(
                    result, payload, event_id=event_id, character_id=character_id,
                )
            return result
        finally:
            self._turn_lock.release()

    def _general_memory_allowed(self, user_message: str, continuity_result: dict[str, Any]) -> bool:
        """Keep temporary/scenario/game events out of authoritative Memory V1."""
        if continuity_result.get("memory_v1_allowed") is False:
            return False
        writer = self._memory_v2_shadow_writer
        store = getattr(writer, "store", None)
        if store is None or not self.character_id:
            return True
        try:
            from current_continuity import game_event_is_contextual
            from memory_v2_store import MemoryV2Repository
            repository = MemoryV2Repository(store)
            if repository.active_truth_scope(str(self.character_id)).kind == "scenario":
                return False
            activity = repository.lookup_actor_state(str(self.character_id), "user", "activity").state
            return not game_event_is_contextual(user_message, activity)
        except Exception:
            return True

    def _observe_contextual_active_state_shadow(self, canonical_user_message: object, canonical_user_index: int) -> None:
        """Run optional non-authoritative extraction only after canonical save."""
        observer = self._active_state_contextual_shadow
        callback = getattr(observer, "observe_canonical_user_turn", None)
        if not callable(callback):
            return
        try:
            result = callback(
                canonical_user_message, conversation_index=canonical_user_index,
                conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"),
            )
            # Disabled observation remains entirely invisible to normal turns.
            if isinstance(result, dict) and result.get("state") != "disabled":
                public = {key: value for key, value in result.items() if key != "proposal"}
                self._emit("active_state_contextual_shadow", **public)
        except Exception as error:
            # Extraction, validation, and diagnostics are all strictly
            # fail-open for the canonical turn and generated response.
            self._emit("active_state_contextual_shadow", state="failed", reason=type(error).__name__)

    def _observe_contextual_open_thread_shadow(self, canonical_user_message: object, canonical_user_index: int) -> None:
        """Run optional non-authoritative Open Thread extraction after save."""
        callback = getattr(self._open_thread_contextual_shadow, "observe_canonical_user_turn", None)
        if not callable(callback):
            return
        try:
            result = callback(canonical_user_message, conversation_index=canonical_user_index,
                              conversation_file=getattr(self.conversation, "conversation_file", "conversation.json"))
            if isinstance(result, dict) and result.get("state") != "disabled":
                self._emit("open_thread_contextual_shadow", **{key: value for key, value in result.items() if key != "proposal"})
        except Exception as error:
            self._emit("open_thread_contextual_shadow", state="failed", reason=type(error).__name__)

    def stop_speaking(self, *, interrupted: bool = False) -> None:
        """Immediately invalidate local speech; presentation observes only."""
        self._retire_automatic_expression()
        started_at = time.monotonic()
        provider = self.tts
        conditional_stop = getattr(type(provider), "stop_if_generation", None)
        with self._speech_generation_lock:
            self._speech_generation += 1
            stopped_speech_generation = self._speech_generation
            provider_generation = getattr(provider, "playback_generation", None)
            # Detach the owner while invalidating it. A replacement may start
            # after this boundary; an older stop must never look up its queue.
            queue = self._streaming_speech_queue
            self._streaming_speech_queue = None
            pending_stream_playback = self._pending_stream_speech
            self._pending_stream_speech = None
            with self._tts_state_lock:
                active_playback_id = self._active_tts_playback_id
                active_stream_playback = self._active_stream_playback
                self._active_tts_playback_id = 0
                self._active_stream_playback = None
        tts_state = getattr(provider, "playback_debug_state", None)
        before = tts_state() if callable(tts_state) else {"active_id": active_playback_id}
        print(f"[AIFren TTS] stop requested; state={before}")
        if queue is not None:
            queue.cancel()
        if queue is not None and getattr(queue, "owns_continuous_playback", False) is True:
            # cancel() uses exact-session abort, including a pending first
            # chunk. A second global stop here could stop a newer reply.
            playback_id = int(queue.playback_id or active_playback_id or 0)
        elif callable(conditional_stop) and type(provider_generation) is int:
            invalidated_id = conditional_stop(provider, provider_generation)
            if invalidated_id is None:
                # Idle/naturally retired queues have no exact queue handle.
                # The captured provider generation still cannot stop a newer
                # utterance or emit an identity-free stale subtitle stop.
                return
            playback_id = int(invalidated_id or active_playback_id or 0)
        else:
            invalidated_id = provider.stop()
            playback_id = int(invalidated_id or active_playback_id or 0)
        print(
            "[AIFren TTS] stop returned; "
            f"id={playback_id}; elapsed={(time.monotonic() - started_at) * 1000:.1f}ms"
        )
        event_data = {
            "state": "stopped",
            "playback_id": playback_id,
            "streamed": active_stream_playback is not None,
            "interrupted": bool(interrupted),
        }
        stream_identity = active_stream_playback or pending_stream_playback
        if stream_identity is not None:
            event_data.update(
                chunk_index=stream_identity["chunk_index"],
                turn_id=stream_identity["turn_id"],
                committed_stream=stream_identity.get("committed_stream", False),
            )
        with self._speech_generation_lock:
            if stopped_speech_generation == self._speech_generation:
                self._emit("tts_state", **event_data)

    def start_push_to_talk(self, listen_globally: bool = True, binding: str | None = None) -> Any:
        """Enable existing F8 PTT and route its voice events through service events."""
        binding = str(binding or self._ptt_binding)
        if self._ptt is not None:
            if listen_globally:
                enable_global = getattr(self._ptt, "enable_global_listener", None)
                if callable(enable_global):
                    enable_global()
            set_binding = getattr(self._ptt, "set_binding", None)
            if callable(set_binding):
                set_binding(binding)
            return self._ptt

        ptt_factory = self._ptt_factory

        if ptt_factory is None:
            from voice.ptt import PushToTalk

            ptt_factory = PushToTalk

        owner = self.character_binding()
        def owned(callback):
            def guarded(*args, **kwargs):
                with self._character_binding_lock:
                    if owner != self.character_binding():
                        return None
                    return callback(*args, **kwargs)
            return guarded

        ptt_arguments = (
            self.voice,
            self.tts,
            lambda text: self._handle_ptt_transcription(text, expected_binding=owner),
        )
        ptt_keywords = {
            "on_state": owned(self._handle_ptt_state),
            "on_tts_interrupt": owned(self._handle_ptt_tts_interrupt),
            "on_error": owned(self._handle_ptt_error),
        }
        try:
            self._ptt = ptt_factory(
                *ptt_arguments,
                listen_globally=listen_globally,
                binding=binding,
                **ptt_keywords,
            )
        except TypeError:
            # Existing test/extension factories predate the optional local
            # frontend bridge. They retain normal global-listener behavior.
            if not listen_globally:
                raise
            self._ptt = ptt_factory(*ptt_arguments, **ptt_keywords)

        set_binding = getattr(self._ptt, "set_binding", None)
        if callable(set_binding):
            set_binding(binding)
        if listen_globally:
            enable_global = getattr(self._ptt, "enable_global_listener", None)
            if callable(enable_global):
                enable_global()

        return self._ptt

    def push_to_talk_press(self) -> None:
        """Accept a focused frontend press; PushToTalk de-duplicates OS input."""
        ptt = self.start_push_to_talk(listen_globally=True)
        try:
            ptt.press(source="frontend")
        except TypeError:
            # Existing extensions may expose the historic zero-argument API.
            ptt.press()

    def push_to_talk_release(self) -> None:
        if self._ptt is not None:
            try:
                self._ptt.release(source="frontend")
            except TypeError:
                self._ptt.release()

    def set_ptt_auto_submit_transcriptions(self, enabled: bool) -> None:
        """Set the frontend-local PTT submission policy without persisting text."""
        self._ptt_auto_submit_transcriptions = bool(enabled)

    def set_push_to_talk_binding(self, binding: str) -> None:
        """Set the one global PTT binding used by all active frontends."""
        value = str(binding or "F8").strip()
        self._ptt_binding = value
        ptt = self.start_push_to_talk(listen_globally=True, binding=value)
        listener_active = getattr(ptt, "global_listener_active", None)
        global_listener = bool(listener_active()) if callable(listener_active) else True
        self._emit("voice_state", state="ready", binding=value, global_listener=global_listener)

    def _handle_ptt_state(self, state: str) -> None:
        # Every voice-state event carries availability.  Frontends must not
        # interpret an omitted field as a failed global hook.
        listener_active = getattr(self._ptt, "global_listener_active", None)
        global_listener = bool(listener_active()) if callable(listener_active) else False
        if state == "released":
            self._last_ptt_release_at = time.monotonic()
            _timing_log("PTT released / capture complete t=0.000s")
        self._emit("voice_state", state=state, global_listener=global_listener)

        status_by_state = {
            "listening": ("Listening...", "listening"),
            "released": ("Transcribing...", "thinking"),
            "ready": ("Ready", "ready"),
        }

        status = status_by_state.get(state)

        if status:
            self._emit("status", message=status[0], state=status[1])

    def _handle_ptt_tts_interrupt(self) -> None:
        print("[AIFren PTT] interrupt requested; cancelling active turn and TTS before microphone capture")
        started_at = time.monotonic()
        self._cancel_active_turn()
        self.stop_speaking(interrupted=True)
        print(f"[AIFren PTT] TTS cancellation dispatched in {(time.monotonic() - started_at) * 1000:.1f}ms")
        self._emit("voice_event", action="tts_interrupted")

    def _handle_ptt_error(self, message: str) -> None:
        self._emit("error", source="voice", message=str(message))
        self._handle_ptt_state("ready")

    def _handle_ptt_transcription(self, text: str, *, expected_binding=None) -> None:
        owner = expected_binding or self.character_binding()
        with self._character_binding_lock:
            if owner != self.character_binding():
                return None
            prepared = self._prepare_ptt_transcription(text)
        if prepared is None:
            return None
        text, ptt_release_at, stt_final_at = prepared
        try:
            return self.process_text_turn(text, input_source="ptt",
                ptt_release_at=ptt_release_at, stt_final_at=stt_final_at, **owner)
        except RuntimeError:
            if owner != self.character_binding():
                return None
            raise

    def _prepare_ptt_transcription(self, text: str):
        """Publish capture data under the short binding lock; never infer here."""
        stt_final_at = time.monotonic()
        ptt_release_at = self._last_ptt_release_at
        if ptt_release_at is not None:
            _timing_log(f"STT final available; release -> final={stt_final_at - ptt_release_at:.3f}s")
        text = str(text or "").strip()
        self._emit("voice_transcription", content=text)
        development_flight_recorder().mark(
            "voice_transcription_emitted",
            characters=len(text),
            words=len(text.split()),
        )

        if not text:
            self._handle_ptt_state("ready")
            return None

        if not self._ptt_auto_submit_transcriptions:
            # The text is deliberately only an event at this point.  It does
            # not enter canonical history until the frontend sends submit_text.
            self._handle_ptt_state("ready")
            return None

        # Transcription is complete now.  The following serialized text turn
        # may take time to generate/speak, but it must not leave a frontend in
        # the transient "Transcribing" presentation state.
        self._handle_ptt_state("ready")
        return text, ptt_release_at, stt_final_at

    def prepare_character_maintenance(self) -> None:
        """Review only an idle, persisted character; never rewrite its archive."""
        if self._closed:
            raise RuntimeError("This character runtime is retired.")
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("Wait for the current turn before character maintenance.")
        try:
            if self._closed:
                raise RuntimeError("This character runtime is retired.")
            prepare = getattr(self.conversation, "prepare_maintenance", None)
            if callable(prepare):
                initialized_empty = prepare()
                if initialized_empty:
                    # Only first-run empty-file initialization changed identity;
                    # settle its zero-record observer page before inventory.
                    self._recover_canonical_observers()
            else:
                self.conversation.save()  # Alternate injected conversation owner.
            if self._memory_authority == "v1":
                save_summary = getattr(self.conversation, "save_summary", None)
                if callable(save_summary):
                    save_summary()
                self.memory.save()
        finally:
            self._turn_lock.release()

    def save(self) -> None:
        self.conversation.save()
        save_summary = getattr(self.conversation, "save_summary", None)
        if callable(save_summary):
            save_summary()
        if self._memory_authority == "v1":
            self.memory.save()

    def close(self) -> None:
        if self._closed: return
        self._closed = True
        # Register the existing shutdown order in reverse. Every owner still
        # retires if an earlier callback raises; _closed cannot strand a lease
        # behind an episode/PTT/TTS/store exception and a later no-op close.
        try:
            with ExitStack() as cleanup:
                if self._storage_lease is not None:
                    cleanup.callback(self._storage_lease.close)
                if self._memory_v2_shadow_writer is not None:
                    cleanup.callback(self._memory_v2_shadow_writer.close)
                if self._memory_v2_authority is not None:
                    authority = self._memory_v2_authority

                    def close_authority():
                        try:
                            authority.close()
                        except Exception:
                            pass
                        finally:
                            self._memory_v2_authority = None

                    cleanup.callback(close_authority)
                if self._memory_recall_shadow is not None:
                    cleanup.callback(self._memory_recall_shadow.close)
                if self._memory_v2_shadow is not None:
                    cleanup.callback(self._memory_v2_shadow.close)
                if self._memory_v2_unsubscribe is not None:
                    unsubscribe = self._memory_v2_unsubscribe
                    self._memory_v2_unsubscribe = None
                    cleanup.callback(unsubscribe)
                cleanup.callback(self.save)
                close_tts = getattr(self.tts, "close", None)
                if callable(close_tts):
                    cleanup.callback(close_tts)
                if self._automatic_expression_worker is not None:
                    cleanup.callback(self._automatic_expression_worker.close)
                cleanup.callback(self.stop_speaking)
                if self._ptt is not None:
                    ptt = self._ptt

                    def close_ptt():
                        try:
                            ptt.stop()
                        finally:
                            self._ptt = None

                    cleanup.callback(close_ptt)
                close_episode_rollover = getattr(self.conversation, "close_episode_compaction_rollover", None)
                if callable(close_episode_rollover):
                    cleanup.callback(close_episode_rollover)
        finally:
            self._canonical_observation_recovery = None
