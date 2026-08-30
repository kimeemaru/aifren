import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import threading

import model_settings
from llm.llm import QWEN35_NON_THINKING_GENERAL, create_llm
from llm.openai_compatible import OpenAICompatibleLLM
from llm.unavailable import UnavailableLLM


class ModelConfigurationTests(unittest.TestCase):
    def test_compatible_adapter_exposes_shared_stream_cancellation(self):
        class Stream:
            def __init__(self): self.closed = 0
            def close(self): self.closed += 1

        adapter = object.__new__(OpenAICompatibleLLM)
        adapter._stream_lock = threading.Lock()
        adapter._active_stream = Stream()

        adapter.cancel_active_generation()

        self.assertEqual(1, adapter._active_stream.closed)

    def test_online_and_local_settings_persist_without_exposing_api_key(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            model_settings.set_model_settings(
                mode="online", online_provider="gemini", online_model="test-online",
                local_endpoint="http://127.0.0.1:8000/v1", local_model="test-local", api_key="not-for-logs",
            )
            status = model_settings.model_status()
            self.assertEqual("online", status["mode"])
            self.assertTrue(status["configured"])
            self.assertNotIn("not-for-logs", json.dumps(status))
            model_settings.set_model_settings(
                mode="local", online_provider="auto", online_model="test-online", local_endpoint="http://127.0.0.1:9000/v1",
                local_model="local-model", local_api_key="local-secret",
            )
            self.assertEqual("local", model_settings.model_status()["mode"])
            self.assertEqual("local-model", model_settings.model_status()["model"])

    def test_local_mode_routes_through_the_same_compatible_adapter(self):
        settings = {"mode": "local", "local_api_key": "", "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "demo", "online_provider": "auto", "api_key": "", "online_base_url": "", "online_model": ""}
        with patch("llm.llm.get_model_settings", return_value=settings):
            adapter = create_llm()
        self.assertIsInstance(adapter, OpenAICompatibleLLM)
        self.assertEqual("demo", adapter.model)
        self.assertEqual("http://127.0.0.1:8000/v1/", adapter.base_url)
        self.assertTrue(adapter.fresh_request_seeds)
        self.assertEqual({}, adapter.sampling_options)

    def test_qwen35_local_model_receives_only_the_non_thinking_general_preset(self):
        settings = {
            "mode": "local", "local_api_key": "", "local_endpoint": "http://127.0.0.1:8000/v1",
            "local_model": "Qwen_Qwen3.5-4B-Q4_K_M.gguf", "online_provider": "auto",
            "api_key": "", "online_base_url": "", "online_model": "",
        }
        with patch("llm.llm.get_model_settings", return_value=settings):
            adapter = create_llm()

        self.assertEqual("qwen3.5_non_thinking_general", adapter.sampling_preset)
        self.assertEqual(QWEN35_NON_THINKING_GENERAL, adapter.sampling_options)

    def test_non_qwen_local_model_keeps_generic_server_sampling_defaults(self):
        settings = {
            "mode": "local", "local_api_key": "", "local_endpoint": "http://127.0.0.1:8000/v1",
            "local_model": "gemma-3-4b-q4.gguf", "online_provider": "auto",
            "api_key": "", "online_base_url": "", "online_model": "",
        }
        with patch("llm.llm.get_model_settings", return_value=settings):
            adapter = create_llm()

        self.assertEqual("", adapter.sampling_preset)
        self.assertEqual({}, adapter.sampling_options)

    def test_local_adapter_supplies_one_fresh_explicit_seed_per_request(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="one"))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="two"))]),
        ]
        seeds = iter((101, 202))
        with patch("llm.openai_compatible.OpenAI", return_value=client):
            adapter = OpenAICompatibleLLM(
                api_key=None, base_url="http://127.0.0.1:8000/v1", model="local",
                fresh_request_seeds=True, seed_source=lambda: next(seeds),
            )

        self.assertEqual("one", adapter.generate([], "prompt"))
        self.assertEqual("two", adapter.generate([], "prompt"))
        self.assertEqual(
            [101, 202],
            [call.kwargs["seed"] for call in client.chat.completions.create.call_args_list],
        )

    def test_explicit_fixed_seed_overrides_fresh_seed_source_for_reproducible_tests(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fixed"))]
        )
        seed_source = MagicMock(return_value=999)
        with patch("llm.openai_compatible.OpenAI", return_value=client):
            adapter = OpenAICompatibleLLM(
                api_key=None, base_url="http://127.0.0.1:8000/v1", model="local",
                fresh_request_seeds=True, seed_source=seed_source,
            )

        self.assertEqual("fixed", adapter.generate([], "prompt", seed=77))
        self.assertEqual(77, client.chat.completions.create.call_args.kwargs["seed"])
        seed_source.assert_not_called()

    def test_bounded_generation_sets_transport_output_cap(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"nonverbal"}'))]
        )
        with patch("llm.openai_compatible.OpenAI", return_value=client):
            adapter = OpenAICompatibleLLM(
                api_key=None, base_url="http://127.0.0.1:8000/v1", model="local",
            )

        self.assertEqual(
            '{"kind":"nonverbal"}',
            adapter.generate_bounded([], "policy", max_output_tokens=48, seed=33),
        )
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(48, request["max_tokens"])
        self.assertEqual(33, request["seed"])
        self.assertNotIn("stream", request)

        with self.assertRaises(ValueError):
            adapter.generate_bounded([], "policy", max_output_tokens=257)

    def test_qwen_sampling_fields_use_the_installed_server_spelling(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="preset"))]
        )
        with patch("llm.openai_compatible.OpenAI", return_value=client):
            adapter = OpenAICompatibleLLM(
                api_key=None, base_url="http://127.0.0.1:8000/v1", model="qwen",
                sampling_preset="qwen3.5_non_thinking_general",
                sampling_options=QWEN35_NON_THINKING_GENERAL,
            )

        self.assertEqual("preset", adapter.generate([], "prompt", seed=88))
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(0.7, request["temperature"])
        self.assertEqual(0.8, request["top_p"])
        self.assertEqual(1.5, request["presence_penalty"])
        self.assertEqual(
            {"top_k": 20, "min_p": 0.0, "repeat_penalty": 1.0},
            request["extra_body"],
        )
        self.assertNotIn("top_k", request)
        self.assertNotIn("min_p", request)
        self.assertNotIn("repeat_penalty", request)
        self.assertNotIn("repetition_penalty", request)

    def test_online_compatible_adapter_preserves_provider_seed_default(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="online"))]
        )
        with patch("llm.openai_compatible.OpenAI", return_value=client):
            adapter = OpenAICompatibleLLM(
                api_key="test", base_url="https://example.invalid/v1", model="online",
            )

        self.assertEqual("online", adapter.generate([], "prompt"))
        self.assertNotIn("seed", client.chat.completions.create.call_args.kwargs)

    def test_missing_online_key_is_a_recoverable_unconfigured_adapter(self):
        settings = {"mode": "online", "online_provider": "gemini", "online_model": "demo", "api_key": "",
                    "online_base_url": "", "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "",
                    "local_api_key": ""}
        with patch("llm.llm.get_model_settings", return_value=settings), patch("llm.gemini.Gemini") as gemini:
            adapter = create_llm()
        self.assertIsInstance(adapter, UnavailableLLM)
        gemini.assert_not_called()

    def test_local_without_a_discovered_model_is_recoverable_and_never_constructs_gemini(self):
        settings = {"mode": "local", "local_api_key": "", "local_endpoint": "http://127.0.0.1:8000/v1",
                    "local_model": "", "online_provider": "gemini", "api_key": "online-key",
                    "online_base_url": "", "online_model": "demo"}
        with patch("llm.llm.get_model_settings", return_value=settings), patch("llm.gemini.Gemini") as gemini:
            adapter = create_llm()
        self.assertIsInstance(adapter, UnavailableLLM)
        gemini.assert_not_called()

    def test_configured_online_provider_is_selected_normally(self):
        settings = {"mode": "online", "online_provider": "gemini", "online_model": "demo", "api_key": "safe-test-key",
                    "online_base_url": "", "local_endpoint": "http://127.0.0.1:8000/v1", "local_model": "",
                    "local_api_key": ""}
        sentinel = object()
        with patch("llm.llm.get_model_settings", return_value=settings), patch("llm.gemini.Gemini", return_value=sentinel) as gemini:
            self.assertIs(sentinel, create_llm())
        gemini.assert_called_once_with()

    def test_local_adapter_is_selected_without_contacting_an_unavailable_endpoint(self):
        settings = {"mode": "local", "local_api_key": "", "local_endpoint": "http://127.0.0.1:1/v1",
                    "local_model": "demo", "online_provider": "gemini", "api_key": "online-key",
                    "online_base_url": "", "online_model": "demo"}
        with patch("llm.llm.get_model_settings", return_value=settings), patch("llm.gemini.Gemini") as gemini:
            adapter = create_llm()
        self.assertIsInstance(adapter, OpenAICompatibleLLM)
        self.assertEqual("http://127.0.0.1:1/v1/", adapter.base_url)
        gemini.assert_not_called()

    def test_local_status_requires_both_endpoint_and_model(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            model_settings.set_model_settings(mode="local", local_endpoint="http://127.0.0.1:8000/v1", local_model="")
            self.assertFalse(model_settings.model_status()["configured"])

    def test_mode_transitions_persist_across_reloads_without_a_credential(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            model_settings.set_model_settings(mode="online", local_endpoint="http://127.0.0.1:8000/v1", local_model="local-a")
            self.assertEqual("online", model_settings.get_model_settings()["mode"])
            self.assertEqual("", model_settings.model_status()["model"])
            self.assertEqual("unconfigured", model_settings.model_status()["availability"])

            model_settings.set_model_settings(mode="local", local_endpoint="http://127.0.0.1:8000/v1", local_model="local-a")
            local = model_settings.model_status()
            self.assertEqual("local", local["mode"])
            self.assertEqual("local-a", local["model"])
            self.assertEqual("unverified", local["availability"])

            # get_model_settings reads the persisted file each time; this is
            # the same state a fresh backend process observes after restart.
            self.assertEqual("local", model_settings.get_model_settings()["mode"])

            model_settings.set_model_settings(mode="online", local_endpoint="http://127.0.0.1:8000/v1", local_model="local-a")
            self.assertEqual("online", model_settings.get_model_settings()["mode"])

    def test_empty_or_unknown_mode_is_rejected(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            with self.assertRaises(ValueError):
                model_settings.set_model_settings(mode="other")

    def test_local_model_and_auto_start_persist_independently_of_online_key(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            model_settings.set_model_settings(
                mode="local", local_endpoint="http://127.0.0.1:8000/v1", local_model="four-b.gguf",
                local_auto_start=True, api_key="online-key",
            )
            saved = model_settings.get_model_settings()
            self.assertEqual("local", saved["mode"])
            self.assertEqual("four-b.gguf", saved["local_model"])
            self.assertTrue(saved["local_auto_start"])
            self.assertEqual("online-key", saved["api_key"])

    def test_auto_start_only_update_preserves_every_provider_setting(self):
        with TemporaryDirectory() as directory, patch.object(model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"):
            model_settings.set_model_settings(
                mode="local", online_provider="openai_compatible", online_model="online-model",
                online_base_url="https://provider.invalid/v1", local_endpoint="http://127.0.0.1:8000/v1",
                local_model="four-b.gguf", api_key="online-key", local_api_key="local-key",
            )
            before = model_settings.get_model_settings()

            model_settings.set_local_auto_start(True)
            after = model_settings.get_model_settings()

            self.assertTrue(after.pop("local_auto_start"))
            before.pop("local_auto_start")
            self.assertEqual(before, after)

    def test_kokoro_early_speech_defaults_on_and_persists_without_changing_model_settings(self):
        with TemporaryDirectory() as directory, patch.object(
            model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"
        ), patch.object(model_settings, "KOKORO_EARLY_SPEECH_OVERRIDE", None):
            self.assertEqual(
                {"configured": True, "effective": True, "overridden": False},
                model_settings.kokoro_early_speech_status(),
            )
            model_settings.set_model_settings(
                mode="local", local_endpoint="http://127.0.0.1:8000/v1", local_model="small.gguf"
            )
            before = model_settings.get_model_settings()
            model_settings.set_kokoro_early_speech(False)
            self.assertEqual(before, model_settings.get_model_settings())
            self.assertEqual(
                {"configured": False, "effective": False, "overridden": False},
                model_settings.kokoro_early_speech_status(),
            )

    def test_kokoro_environment_override_wins_without_replacing_saved_preference(self):
        with TemporaryDirectory() as directory, patch.object(
            model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"
        ), patch.object(model_settings, "KOKORO_EARLY_SPEECH_OVERRIDE", False):
            model_settings.set_kokoro_early_speech(True)
            self.assertEqual(
                {"configured": True, "effective": False, "overridden": True},
                model_settings.kokoro_early_speech_status(),
            )

    def test_proactive_behavior_defaults_conservative_on_and_persists_independently(self):
        with TemporaryDirectory() as directory, patch.object(
            model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"
        ):
            self.assertEqual({"enabled": True, "interval_seconds": 3600}, model_settings.proactive_behavior_status())
            model_settings.set_model_settings(
                mode="local", local_endpoint="http://127.0.0.1:8000/v1", local_model="small.gguf"
            )
            before = model_settings.get_model_settings()
            model_settings.set_proactive_behavior(False)
            self.assertEqual({"enabled": False, "interval_seconds": 0}, model_settings.proactive_behavior_status())
            self.assertEqual(before, model_settings.get_model_settings())
            with self.assertRaises(ValueError):
                model_settings.set_proactive_behavior("false")

    def test_every_proactive_interval_persists_and_legacy_boolean_migrates(self):
        with TemporaryDirectory() as directory, patch.object(
            model_settings, "LOCAL_SETTINGS_FILE", Path(directory) / "settings.json"
        ):
            for interval in model_settings.PROACTIVE_INTERVAL_SECONDS:
                model_settings.set_proactive_interval(interval)
                self.assertEqual(interval, model_settings.proactive_behavior_status()["interval_seconds"])
            model_settings.LOCAL_SETTINGS_FILE.write_text('{"proactive_behavior":false}\n', encoding="utf-8")
            self.assertEqual({"enabled": False, "interval_seconds": 0}, model_settings.proactive_behavior_status())
            model_settings.LOCAL_SETTINGS_FILE.write_text('{"proactive_behavior":true}\n', encoding="utf-8")
            self.assertEqual({"enabled": True, "interval_seconds": 3600}, model_settings.proactive_behavior_status())
            with self.assertRaises(ValueError):
                model_settings.set_proactive_interval(31)
