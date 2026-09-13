import threading
import time

from pynput import keyboard, mouse

from aifren.runtime.development_flight_recorder import development_flight_recorder


# ============================================================
# Push To Talk
# ============================================================

PTT_KEY = keyboard.Key.f8


def _mouse_button(*names):
    """Return the first side-button spelling provided by this pynput backend."""
    for name in names:
        button = getattr(mouse.Button, name, None)
        if button is not None:
            return button
    return None


def _normalise_binding(binding):
    """Translate Unity's persisted KeyCode spelling to pynput input values."""
    value = str(binding or "F8").strip()
    # pynput exposes x1/x2 on Windows. Its X11 backend instead exposes the
    # conventional side buttons as button8/button9/button10. Look these up
    # only for the requested binding so unsupported hardware never prevents
    # keyboard PTT from starting.
    if value == "Mouse3":
        return _mouse_button("x1", "button8")
    if value == "Mouse4":
        return _mouse_button("x2", "button9")
    if value == "Mouse5":
        return _mouse_button("button10")
    if len(value) == 1 and value.isalnum():
        return value.lower()
    key_name = value.lower()
    return getattr(keyboard.Key, key_name, PTT_KEY)


class PushToTalk:

    def __init__(
        self,
        voice_input,
        tts,
        on_transcription,
        on_state=None,
        on_tts_interrupt=None,
        on_error=None,
        listen_globally=True,
        binding="F8",
    ):

        self.voice_input = voice_input
        self.tts = tts
        self.on_transcription = on_transcription
        self.on_state = on_state
        self.on_tts_interrupt = on_tts_interrupt
        self.on_error = on_error

        self.running = False
        self.listen_globally = listen_globally
        self.binding = str(binding or "F8")
        self._bound_key = _normalise_binding(self.binding)
        self._pressed = False

        self._state_lock = threading.Lock()

        self.listener = None
        self.mouse_listener = None
        self.record_thread = None
        self._ptt_worker_started_at = None
        self._ptt_stage = "idle"
        self._ptt_stage_started_at = time.monotonic()
        self._ptt_released_at = None
        self._next_capture_id = 0
        self._active_capture_id = None

        set_stage_observer = getattr(self.voice_input, "set_ptt_stage_observer", None)
        if callable(set_stage_observer):
            set_stage_observer(self._observe_ptt_stage)

        self.start()

    def _observe_ptt_stage(self, stage, **metadata):
        """Record structural Development telemetry without affecting PTT."""
        now = time.monotonic()
        capture_id = metadata.get("capture_id")
        with self._state_lock:
            authoritative = capture_id is None or capture_id == getattr(self, "_active_capture_id", None)
            if authoritative:
                self._ptt_stage = str(stage or "unknown")
                self._ptt_stage_started_at = now
                if stage == "ptt_record_thread_started" and getattr(self, "_ptt_worker_started_at", None) is None:
                    self._ptt_worker_started_at = now
                elif stage == "ptt_release_seen":
                    self._ptt_released_at = now
        try:
            development_flight_recorder().mark(str(stage), **metadata)
        except Exception:
            # Development diagnostics must never affect PTT authority.
            pass

    def flight_recorder_state(self):
        """Return privacy-safe, low-rate PTT worker diagnostics."""
        now = time.monotonic()
        with self._state_lock:
            worker = self.record_thread
            alive = bool(worker is not None and worker.is_alive())
            stage = str(getattr(self, "_ptt_stage", "unknown") or "unknown")
            worker_started = getattr(self, "_ptt_worker_started_at", None)
            stage_started = getattr(self, "_ptt_stage_started_at", None)
            released_at = getattr(self, "_ptt_released_at", None)
            listening = bool(self._pressed)
        recording_stages = {
            "ptt_record_thread_pending", "ptt_record_thread_started", "mic_open_begin",
            "mic_open_end", "mic_capture_started", "ptt_release_seen", "mic_close_begin",
            "mic_stop_begin", "mic_stop_end", "mic_stream_close_begin",
            "mic_stream_close_end", "mic_close_timeout", "mic_abort_begin",
            "mic_abort_end", "mic_abort_error", "mic_close_end",
        }
        transcribing_stages = {
            "captured_audio_ready", "wav_prepare_begin", "wav_prepare_end", "whisper_begin",
            "whisper_end", "transcription_ready",
        }
        post_release = bool(alive and released_at is not None)
        return {
            "ptt_worker_alive": alive,
            "ptt_worker_age_seconds": max(0.0, now - worker_started) if alive and worker_started else 0.0,
            "ptt_stage": stage,
            "ptt_stage_age_seconds": max(0.0, now - stage_started) if stage_started else 0.0,
            "ptt_post_release": post_release,
            "ptt_post_release_age_seconds": max(0.0, now - released_at) if post_release else 0.0,
            "ptt_recording": bool(alive and stage in recording_stages),
            "ptt_listening": listening,
            "ptt_transcribing": bool(alive and stage in transcribing_stages),
        }

    # ========================================================
    # State callback
    # ========================================================

    def _state(
        self,
        state
    ):

        if not self.on_state:
            return

        try:

            self.on_state(
                state
            )

        except Exception:
            pass

    # ========================================================
    # Pressed state
    # ========================================================

    def is_pressed(
        self
    ):

        with self._state_lock:

            return self._pressed

    def _capture_is_pressed(self, capture_id):
        with self._state_lock:
            return bool(
                self._pressed
                and capture_id == getattr(self, "_active_capture_id", None)
            )

    # ========================================================
    # Start
    # ========================================================

    def start(
        self
    ):

        with self._state_lock:

            if self.running:
                return

            self.running = True

        print()
        print(
            "Push-to-talk ready."
        )
        print(
            f"Hold {self.binding} to speak."
        )
        print(
            "Press F8 while speaking to interrupt."
        )

        if self.listen_globally:
            self._start_global_listener()

    def _start_global_listener(self):
        if self.listener is not None or self.mouse_listener is not None:
            return
        try:
            if self._bound_key is None:
                raise RuntimeError(f"{self.binding} is not exposed by the Windows input API")
            if isinstance(self._bound_key, mouse.Button):
                self.mouse_listener = mouse.Listener(
                    on_click=self._on_mouse_click,
                )
                self.mouse_listener.daemon = True
                self.mouse_listener.start()
            else:
                self.listener = keyboard.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release
                )
                self.listener.daemon = True
                self.listener.start()
        except Exception as error:
            self.listen_globally = False
            if self.on_error:
                self.on_error(f"Global push-to-talk is unavailable: {error}")

    def enable_global_listener(self):
        self.listen_globally = True
        self._start_global_listener()

    def global_listener_active(self):
        """Return whether the requested OS-level listener actually started."""
        listener = self.listener or self.mouse_listener
        if not self.listen_globally or listener is None:
            return False
        # pynput marks a listener as running immediately after successful
        # startup. Checking only Thread.is_alive() races the initial binding
        # event and incorrectly labels a usable global listener unavailable.
        return bool(getattr(listener, "running", listener.is_alive()))

    def set_binding(self, binding):
        changed = str(binding or "F8") != self.binding
        self.binding = str(binding or "F8")
        self._bound_key = _normalise_binding(self.binding)
        if changed and self.listen_globally:
            self._stop_listeners()
            self._start_global_listener()

    # ========================================================
    # Key Press
    # ========================================================

    def _on_press(
        self,
        key
    ):

        if self._bound_key is None or key != self._bound_key:
            return
        self.press(source="global_keyboard")

    def press(self, source="frontend"):

        with self._state_lock:

            if not self.running:
                return

            if self._pressed:
                print(f"PTT press deduplicated ({source}).")
                return

            if (
                self.record_thread
                and self.record_thread.is_alive()
            ):

                return

            self._pressed = True
            self._next_capture_id += 1
            capture_id = self._next_capture_id
            self._active_capture_id = capture_id
            self._ptt_worker_started_at = time.monotonic()
            self._ptt_released_at = None
            self._ptt_stage = "ptt_record_thread_pending"
            self._ptt_stage_started_at = self._ptt_worker_started_at

        print()
        print(f"PTT press ({source}) accepted.")
        print(
            "F8 pressed — listening..."
        )

        self._state(
            "listening"
        )

        # ----------------------------------------------------
        # Interrupt TTS immediately.
        # ----------------------------------------------------

        try:

            if self.on_tts_interrupt:

                self.on_tts_interrupt()

            else:

                self.tts.stop()

        except Exception as e:

            print('[AIFren PTT] audio operation failed.')

            if self.on_error:

                try:

                    self.on_error("Audio operation failed; try PTT again.")

                except Exception:

                    pass

        # ----------------------------------------------------
        # Start recording.
        # ----------------------------------------------------

        self.record_thread = threading.Thread(
            target=self._record,
            args=(capture_id,),
            name=f"AIFren PTT capture {capture_id}",
            daemon=True
        )

        self.record_thread.start()

    # ========================================================
    # Key Release
    # ========================================================

    def _on_release(
        self,
        key
    ):

        if self._bound_key is None or key != self._bound_key:
            return
        self.release(source="global_keyboard")

    def _on_mouse_click(self, _x, _y, button, pressed):
        if button != self._bound_key:
            return
        if pressed:
            self.press(source="global_mouse")
        else:
            self.release(source="global_mouse")

    def release(self, source="frontend"):

        with self._state_lock:
            was_pressed = self._pressed
            self._pressed = False
            capture_id = getattr(self, "_active_capture_id", None)

        # Ignore an unmatched release.  In particular, a reconnect or focus
        # transition must not manufacture a permanent "Transcribing" state.
        if not was_pressed:
            print(f"PTT release deduplicated ({source}).")
            return

        print(
            "F8 released."
        )

        self._state(
            "released"
        )
        self._observe_ptt_stage("ptt_release_seen", capture_id=capture_id)

    # ========================================================
    # Record
    # ========================================================

    def _record(
        self,
        capture_id=None,
    ):

        recording_thread = threading.current_thread()
        if capture_id is None:
            with self._state_lock:
                capture_id = getattr(self, "_active_capture_id", None)
        self._observe_ptt_stage("ptt_record_thread_started", capture_id=capture_id)

        try:

            print(
                "Starting PTT microphone..."
            )

            text = self.voice_input.record_ptt(
                lambda: self._capture_is_pressed(capture_id),
                capture_id=capture_id,
            )

            print('[AIFren PTT] transcription completed.')

            if text:

                # Recording ownership ends when microphone capture and STT
                # finish, not when the (synchronous) transcription consumer
                # finishes generating an assistant turn.  Keeping this thread
                # registered through on_transcription made every later PTT
                # press look like a duplicate for the entire LLM/TTS turn.
                with self._state_lock:
                    if (
                        self.record_thread is recording_thread
                        and getattr(self, "_active_capture_id", capture_id) == capture_id
                    ):
                        self.record_thread = None
                        self._active_capture_id = None

                # A timed-out/retired capture must never deliver a late
                # transcription after a newer PTT session has taken authority.
                with self._state_lock:
                    authoritative = bool(
                        self._active_capture_id is None
                        and self.record_thread is None
                        and getattr(self, "_next_capture_id", capture_id) == capture_id
                    )
                if not authoritative:
                    self._observe_ptt_stage("ptt_capture_discarded", capture_id=capture_id)
                    return

                self.on_transcription(
                    text
                )

            else:

                self._state(
                    "ready"
                )

        except Exception as e:

            discarded_capture = bool(getattr(e, "discard_capture", False))

            self._observe_ptt_stage(
                "ptt_record_thread_error",
                capture_id=capture_id,
                error_type=type(e).__name__,
            )

            if discarded_capture:
                self._observe_ptt_stage("ptt_capture_discarded", capture_id=capture_id)

            print('[AIFren PTT] audio operation failed.')

            if self.on_error:

                try:

                    self.on_error("Audio operation failed; try PTT again.")

                except Exception:

                    pass

            self._state(
                "ready"
            )

            if discarded_capture:
                self._observe_ptt_stage("ptt_worker_recovered", capture_id=capture_id)

        finally:

            self._observe_ptt_stage("ptt_record_thread_exit", capture_id=capture_id)

            # A new press may already have installed its own recorder while
            # the old transcription callback was running.  Never clear that
            # newer recording from the old thread's cleanup.
            with self._state_lock:
                if (
                    self.record_thread is recording_thread
                    and getattr(self, "_active_capture_id", capture_id) == capture_id
                ):
                    self.record_thread = None
                    self._active_capture_id = None

    # ========================================================
    # Stop
    # ========================================================

    def stop(
        self
    ):

        with self._state_lock:

            self.running = False
            self._pressed = False
            self._active_capture_id = None

        self._stop_listeners()

        try:

            self.tts.stop()

        except Exception:
            pass

        self._state(
            "stopped"
        )

        print(
            "Push-to-talk stopped."
        )

    def _stop_listeners(self):
        if self.listener:

            try:

                self.listener.stop()

            except Exception:
                pass

            self.listener = None
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
            self.mouse_listener = None
