"""PTT binding normalization must tolerate platform-specific pynput buttons."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PTT_PATH = Path(__file__).resolve().parents[1] / "voice" / "ptt.py"


def load_ptt_module(button_type):
    key_type = SimpleNamespace(f8="F8")
    fake_pynput = SimpleNamespace(
        keyboard=SimpleNamespace(Key=key_type, Listener=object),
        mouse=SimpleNamespace(Button=button_type, Listener=object),
    )
    spec = importlib.util.spec_from_file_location("testable_ptt", PTT_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"pynput": fake_pynput}):
        spec.loader.exec_module(module)
    return module


class PttBindingTests(unittest.TestCase):
    def test_missing_linux_side_buttons_do_not_break_keyboard_or_mouse_binding(self):
        module = load_ptt_module(SimpleNamespace(left="left"))

        self.assertEqual(module._normalise_binding("F8"), "F8")
        self.assertIsNone(module._normalise_binding("Mouse3"))
        self.assertIsNone(module._normalise_binding("Mouse4"))
        self.assertIsNone(module._normalise_binding("Mouse5"))

    def test_linux_button_numbering_enables_global_side_buttons(self):
        button8 = object()
        button9 = object()
        button10 = object()
        module = load_ptt_module(SimpleNamespace(button8=button8, button9=button9, button10=button10))

        self.assertIs(module._normalise_binding("Mouse3"), button8)
        self.assertIs(module._normalise_binding("Mouse4"), button9)
        self.assertIs(module._normalise_binding("Mouse5"), button10)

    def test_supported_side_buttons_keep_windows_mapping(self):
        x1 = object()
        x2 = object()
        module = load_ptt_module(SimpleNamespace(x1=x1, x2=x2))

        self.assertIs(module._normalise_binding("Mouse3"), x1)
        self.assertIs(module._normalise_binding("Mouse4"), x2)

    def test_stop_does_not_depend_on_a_press_source(self):
        class Listener:
            def stop(self):
                pass

        module = load_ptt_module(SimpleNamespace(left="left"))
        instance = object.__new__(module.PushToTalk)
        instance._state_lock = __import__("threading").Lock()
        instance.running = True
        instance._pressed = False
        instance.listener = Listener()
        instance.mouse_listener = None
        instance.tts = SimpleNamespace(stop=lambda: None)
        instance._state = lambda _state: None

        instance.stop()
        self.assertFalse(instance.running)
