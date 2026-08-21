"""Frontend-independent application service for AIFren.

This module deliberately owns no UI toolkit code.  It provides the same
application turn lifecycle used by the desktop client and exposes lifecycle
events that a future frontend can consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
import threading
from typing import Any, Callable, Optional


EventListener = Callable[["AssistantEvent"], None]
ResponseGenerator = Callable[[Any, Any, Any, str, str], str]


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
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


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
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.conversation = conversation
        self.voice = voice
        self.character = character
        self.character_prompt = character_prompt
        self.tts = tts

        self._response_generator = response_generator
        self._ptt_factory = ptt_factory
        self._listeners: list[EventListener] = []
        self._listeners_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._ptt = None
        self._ptt_binding = "F8"
        self._memory_v2_shadow = memory_v2_shadow
        # Frontends may choose whether an STT result is immediately submitted
        # or presented for review.  The historic desktop/global-PTT behavior
        # remains auto-submit by default.
        self._ptt_auto_submit_transcriptions = True
        self._tts_reports_playback_start = False
        self._tts_state_lock = threading.Lock()
        self._active_tts_playback_id = 0
        self._configure_tts_playback_events()

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
    ) -> None:
        """Forward actual local playback start without coupling to a frontend."""
        with self._tts_state_lock:
            self._active_tts_playback_id = int(playback_id or 0)
        self._emit(
            "tts_state",
            state="playback_started",
            duration_seconds=float(duration_seconds),
            lip_sync_envelope=list(lip_sync_envelope or ()),
            word_start_seconds=list(word_start_seconds or ()),
            playback_id=int(playback_id or 0),
        )

    def _on_tts_playback_finished(self, playback_id: int | None = None) -> None:
        """Forward a natural local playback completion to presentation clients."""
        completed_id = int(playback_id or 0)
        with self._tts_state_lock:
            if completed_id and self._active_tts_playback_id not in (0, completed_id):
                print(
                    "[AIFren TTS] ignored stale natural completion; "
                    f"id={completed_id}; active={self._active_tts_playback_id}"
                )
                return
            self._active_tts_playback_id = 0
        print(f"[AIFren TTS] service natural completion; id={completed_id}; PTT ready")
        self._emit("tts_state", state="stopped", playback_id=completed_id)

    @classmethod
    def create_default(cls) -> "AssistantService":
        """Build the service with AIFren's existing default components."""
        # Import lazily so alternative frontends and unit tests do not need to
        # import the local STT/TTS implementations until they use this factory.
        from assistant import initialize

        (
            llm,
            memory,
            conversation,
            voice,
            character,
            character_prompt,
            tts,
            _,
        ) = initialize()

        shadow = None
        from config import MEMORY_V2_SHADOW_ENABLED
        if MEMORY_V2_SHADOW_ENABLED:
            from memory_v2_shadow import MemoryV2ShadowComparator
            shadow = MemoryV2ShadowComparator(".")

        return cls(
            llm=llm,
            memory=memory,
            conversation=conversation,
            voice=voice,
            character=character,
            character_prompt=character_prompt,
            tts=tts,
            memory_v2_shadow=shadow,
        )

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
        """Omit action emotes while preserving inline asterisk emphasis for speech."""
        action_verbs = {
            "smile", "smiles", "smiled", "smiling", "nod", "nods", "nodded", "nodding",
            "shake", "shakes", "shook", "shaking", "wave", "waves", "waved", "waving",
            "shrug", "shrugs", "shrugged", "shrugging", "tilt", "tilts", "tilted", "tilting",
            "cross", "crosses", "crossed", "crossing", "look", "looks", "looked", "looking",
            "sigh", "sighs", "sighed", "sighing", "think", "thinks", "thought", "thinking",
            "ponder", "ponders", "pondered", "pondering", "laugh", "laughs", "laughed", "laughing",
            "grin", "grins", "grinned", "grinning", "frown", "frowns", "frowned", "frowning",
            "turn", "turns", "turned", "turning", "blink", "blinks", "blinked", "blinking",
            "pause", "pauses", "paused", "pausing", "blush", "blushes", "blushed", "blushing",
            "chuckle", "chuckles", "chuckled", "chuckling", "giggle", "giggles", "giggled", "giggling",
            "gasp", "gasps", "gasped", "gasping", "stare", "stares", "stared", "staring",
            "glance", "glances", "glanced", "glancing", "raise", "raises", "raised", "raising",
            "lower", "lowers", "lowered", "lowering", "rub", "rubs", "rubbed", "rubbing",
            "bite", "bites", "bit", "biting", "lean", "leans", "leaned", "leaning",
            "shift", "shifts", "shifted", "shifting", "tap", "taps", "tapped", "tapping",
            "take", "takes", "took", "taking", "breathe", "breathes", "breathed", "breathing",
            "walk", "walks", "walked", "walking",
        }

        def replace(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            words = content.split()
            first = words[0].strip('"\'.,!?;:').lower() if words else ""
            second = words[1].strip('"\'.,!?;:').lower() if len(words) > 1 else ""
            # Keep this fallback aligned with DialoguePresentationParser:
            # a long single-marker roleplay beat is an action unless it used
            # double-marker emphasis (normalized before this replacement).
            if first in action_verbs or second in action_verbs or len(words) >= 4:
                return ""
            return content

        # Double markers are always emphasis, never stage directions. Normalize
        # them first so well-formed Markdown emphasis cannot reach the provider
        # as literal asterisks. Single markers retain the shared action rule.
        text = re.sub(
            r"(?<![\\*])\*\*([^\s*][^*\r\n]*?)\*\*(?!\*)",
            lambda match: match.group(1).strip(),
            text,
        )
        return re.sub(r"(?<![\\*])\*([^\s*][^*\r\n]*?)\*(?!\*)", replace, text).strip()

    def _generate_reply(self, user_message: str) -> str:
        if self._response_generator is not None:
            return self._response_generator(
                self.llm,
                self.conversation,
                self.memory,
                user_message,
                self.character_prompt,
            )

        # This preserves the existing response-generation implementation.
        # Stage 2 will make Conversation its single context-builder source.
        from assistant import generate_response

        return generate_response(
            self.llm,
            self.conversation,
            self.memory,
            user_message,
            self.character_prompt,
        )

    def process_text_turn(self, user_message: str, speak: bool = True) -> TurnResult:
        """Process one text turn using the existing persistence lifecycle."""
        user_message = str(user_message).strip()

        if not user_message:
            error = "A message is required."
            self._emit("error", message=error)
            return TurnResult(user_message=user_message, error=error)

        if not self._turn_lock.acquire(blocking=False):
            error = "Assistant is still processing."
            self._emit("status", state="thinking", message=error)
            return TurnResult(user_message=user_message, error=error)

        try:
            turn_started_at = time.monotonic()
            print("[AIFren Timing] user accepted; backend turn started t=0.000s")
            self._emit("turn_started", user_message=user_message)
            self._emit("status", state="thinking", message="Thinking...")

            self.conversation.add_user_message(user_message)
            self._emit("conversation_message", role="user", content=user_message)

            if self._memory_v2_shadow is not None:
                setattr(self.conversation, "_capture_v1_retrieval_diagnostics", True)
                setattr(self.conversation, "_last_v1_retrieval_diagnostics", ())
            try:
                reply = str(self._generate_reply(user_message) or "")
            finally:
                if self._memory_v2_shadow is not None:
                    setattr(self.conversation, "_capture_v1_retrieval_diagnostics", False)
            print(f"[AIFren Timing] full assistant response ready t={time.monotonic() - turn_started_at:.3f}s")
            self._emit("assistant_response", content=reply)

            spoken_text = self.clean_text_for_tts(reply)
            if speak and spoken_text:
                self._emit("status", state="speaking", message="Speaking...")
                self._emit("tts_state", state="starting")
                print(f"[AIFren Timing] TTS synthesis requested t={time.monotonic() - turn_started_at:.3f}s")

                try:
                    started = self.tts.speak(spoken_text)
                    print(f"[AIFren Timing] TTS synthesis/playback dispatch returned t={time.monotonic() - turn_started_at:.3f}s")
                    if started is False:
                        self._emit("tts_state", state="failed")
                    else:
                        # Existing third-party-compatible providers without the
                        # callback still get a prompt presentation fallback.
                        if not self._tts_reports_playback_start:
                            self._emit("tts_state", state="playback_started")
                        self._emit("tts_state", state="speaking")

                except Exception as error:
                    # Speaking failure must not discard an otherwise valid
                    # assistant response or prevent memory/summary processing.
                    self._emit(
                        "error",
                        source="tts",
                        message=str(error)
                    )
                    self._emit("tts_state", state="failed")
            else:
                # Do not leave a frontend waiting for an event that cannot
                # occur when speech is disabled or contains only emotes.
                self._emit("tts_state", state="not_started")

            # The optional V2 shadow observer can run local retrieval and
            # embedding work.  It must not sit between the complete-reply
            # event (which starts frontend reveal) and TTS initiation.
            self._run_memory_v2_shadow(user_message)

            self.conversation.add_assistant_message(reply)
            self.conversation.save()
            self._emit("conversation_message", role="assistant", content=reply)

            self.memory.process(user_message, reply)
            self._emit(
                "memory_updated",
                count=len(getattr(self.memory, "memories", [])),
            )

            self.conversation.update_summary()
            self._emit("status", state="ready", message="Ready")

            return TurnResult(
                user_message=user_message,
                reply=reply,
                spoken_text=spoken_text,
            )

        except Exception as error:
            message = str(error)
            self._emit("error", message=message)
            self._emit("status", state="error", message="Error")
            return TurnResult(user_message=user_message, error=message)

        finally:
            self._turn_lock.release()

    def set_tts_volume(self, volume: float) -> None:
        self.tts.set_volume(volume)
        self._emit("tts_state", state="volume_changed", volume=volume)

    def replace_llm(self, llm: Any) -> None:
        """Apply a provider reconfiguration before the next serialized turn."""
        if self._turn_lock.locked():
            raise RuntimeError("Assistant is still processing. Try again when it is ready.")
        self.llm = llm
        self.memory.llm = llm
        self.conversation.llm = llm

    def _run_memory_v2_shadow(self, user_message: str) -> None:
        """Observe a completed V1 context build without changing the reply."""
        if self._memory_v2_shadow is None:
            return
        try:
            v1_selected = getattr(self.conversation, "_last_v1_retrieval_diagnostics", ())
            comparison = self._memory_v2_shadow.compare(
                user_message, getattr(self.conversation, "messages", ()), v1_selected,
            )
            self._emit("memory_shadow", **comparison)
        except Exception as error:
            # Diagnostics are strictly fail-open for the user turn.
            self._emit("memory_shadow", shadow={"state": "invalid"}, error={"source": "memory_v2_shadow", "kind": type(error).__name__})

    def stop_speaking(self) -> None:
        """Immediately invalidate local speech; presentation observes only."""
        started_at = time.monotonic()
        with self._tts_state_lock:
            active_playback_id = self._active_tts_playback_id
            self._active_tts_playback_id = 0
        tts_state = getattr(self.tts, "playback_debug_state", None)
        before = tts_state() if callable(tts_state) else {"active_id": active_playback_id}
        print(f"[AIFren TTS] stop requested; state={before}")
        invalidated_id = self.tts.stop()
        playback_id = int(invalidated_id or active_playback_id or 0)
        print(
            "[AIFren TTS] stop returned; "
            f"id={playback_id}; elapsed={(time.monotonic() - started_at) * 1000:.1f}ms"
        )
        self._emit("tts_state", state="stopped", playback_id=playback_id)

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

        ptt_arguments = (
            self.voice,
            self.tts,
            self._handle_ptt_transcription,
        )
        ptt_keywords = {
            "on_state": self._handle_ptt_state,
            "on_tts_interrupt": self._handle_ptt_tts_interrupt,
            "on_error": self._handle_ptt_error,
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
        print("[AIFren PTT] interrupt requested; cancelling TTS before microphone capture")
        started_at = time.monotonic()
        self.stop_speaking()
        print(f"[AIFren PTT] TTS cancellation dispatched in {(time.monotonic() - started_at) * 1000:.1f}ms")
        self._emit("voice_event", action="tts_interrupted")

    def _handle_ptt_error(self, message: str) -> None:
        self._emit("error", source="voice", message=str(message))
        self._handle_ptt_state("ready")

    def _handle_ptt_transcription(self, text: str) -> None:
        text = str(text or "").strip()
        self._emit("voice_transcription", content=text)

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
        return self.process_text_turn(text)

    def save(self) -> None:
        self.conversation.save()
        self.memory.save()

    def close(self) -> None:
        if self._ptt is not None:
            self._ptt.stop()
            self._ptt = None

        self.stop_speaking()
        self.save()
        if self._memory_v2_shadow is not None:
            self._memory_v2_shadow.close()
