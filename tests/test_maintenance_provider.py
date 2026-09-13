import tempfile
import unittest

from aifren.llm.maintenance import configured_maintenance_provider


class _Runtime:
    def __init__(self, *_args, **_kwargs):
        self.start_args = None
        self.stopped = False

    def start(self, **kwargs):
        self.start_args = kwargs
        return {"state": "ready", "ownership": "managed", "compute": "Synthetic GPU"}

    def stop(self):
        self.stopped = True


class _UnavailableRuntime(_Runtime):
    def start(self, **kwargs):
        self.start_args = kwargs
        return {"state": "error", "error": "synthetic unavailable"}


class _Provider:
    model = "synthetic-model"


class MaintenanceProviderTests(unittest.TestCase):
    def test_local_maintenance_starts_and_always_stops_existing_runtime(self):
        created = []

        def runtime_factory(*args, **kwargs):
            value = _Runtime(*args, **kwargs)
            created.append(value)
            return value

        with tempfile.TemporaryDirectory() as root:
            with configured_maintenance_provider(
                root,
                settings_getter=lambda: {
                    "mode": "local", "local_endpoint": "http://127.0.0.1:8000/v1",
                    "local_model": "synthetic.gguf", "local_api_key": "",
                },
                provider_factory=_Provider,
                runtime_factory=runtime_factory,
            ) as session:
                self.assertEqual("managed", session.runtime_ownership)
                self.assertEqual("Synthetic GPU", session.runtime_compute)
                self.assertEqual("synthetic-model", session.model)
                self.assertFalse(created[0].stopped)
            self.assertTrue(created[0].stopped)
            self.assertEqual("synthetic.gguf", created[0].start_args["selected_model"])

    def test_online_maintenance_does_not_create_local_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            with configured_maintenance_provider(
                root,
                settings_getter=lambda: {"mode": "online"},
                provider_factory=_Provider,
                runtime_factory=lambda *_args, **_kwargs: self.fail("runtime created"),
            ) as session:
                self.assertEqual("online", session.mode)
                self.assertEqual("none", session.runtime_ownership)

    def test_local_start_failure_is_bounded_and_stops_runtime(self):
        created = []

        def runtime_factory(*args, **kwargs):
            value = _UnavailableRuntime(*args, **kwargs)
            created.append(value)
            return value

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, "synthetic unavailable"):
                with configured_maintenance_provider(
                    root,
                    settings_getter=lambda: {
                        "mode": "local", "local_endpoint": "loopback",
                        "local_model": "missing.gguf", "local_api_key": "secret",
                    },
                    provider_factory=lambda: self.fail("provider created"),
                    runtime_factory=runtime_factory,
                ):
                    pass
        self.assertTrue(created[0].stopped)


if __name__ == "__main__":
    unittest.main()
