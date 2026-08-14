import threading

from pynput import keyboard, mouse


# ============================================================
# Push To Talk
# ============================================================

PTT_KEY = keyboard.Key.f8


def _normalise_binding(binding):
    """Translate Unity's persisted KeyCode spelling to pynput input values."""
    value = str(binding or "F8").strip()
    mouse_bindings = {
        # Unity: Mouse0/1/2 are left/right/middle, then Mouse3/4 map to
        # Windows' first/second thumb buttons. pynput calls those x1/x2.
        "Mouse3": mouse.Button.x1,
        "Mouse4": mouse.Button.x2,
    }
    if value in mouse_bindings:
        return mouse_bindings[value]
    if value == "Mouse5":
        return None
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

        self.start()

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

            print(
                f"TTS interruption error: {e}"
            )

            if self.on_error:

                try:

                    self.on_error(
                        str(e)
                    )

                except Exception:

                    pass

        # ----------------------------------------------------
        # Start recording.
        # ----------------------------------------------------

        self.record_thread = threading.Thread(
            target=self._record,
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

    # ========================================================
    # Record
    # ========================================================

    def _record(
        self
    ):

        try:

            print(
                "Starting PTT microphone..."
            )

            text = self.voice_input.record_ptt(
                self.is_pressed
            )

            print(
                f"PTT transcription: {text!r}"
            )

            if text:

                self.on_transcription(
                    text
                )

            else:

                self._state(
                    "ready"
                )

        except Exception as e:

            print(
                f"\nPTT error: {e}"
            )

            if self.on_error:

                try:

                    self.on_error(
                        str(e)
                    )

                except Exception:

                    pass

            self._state(
                "ready"
            )

        finally:

            self.record_thread = None

    # ========================================================
    # Stop
    # ========================================================

    def stop(
        self
    ):

        with self._state_lock:

            self.running = False
            self._pressed = False

        self._stop_listeners()

        try:

            self.tts.stop()

        except Exception:
            pass

        self._state(
            "stopped"
        )

        print(f"PTT release ({source}) accepted.")
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
