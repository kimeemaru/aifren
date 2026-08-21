import json
from pathlib import Path
import tempfile
import unittest

from assistant_service import AssistantService
from conversation.conversation import Conversation
from memory_v2_shadow import MemoryV2ShadowComparator, build_shadow_query, rebuild_shadow


class ToyProvider:
    provider = "test"
    model = "shadow-toy"
    model_version = "1"
    dimensions = 2
    normalized = True
    dtype = "float32"
    preprocessing_fingerprint = "shadow-test-v1"
    device = "cpu"

    def embed(self, texts):
        return [[1.0, 0.0] if "tea" in str(text).lower() else [0.0, 1.0] for text in texts]


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "source"
        (self.root / "characters/default").mkdir(parents=True)
        (self.root / "conversation.json").write_text(json.dumps([
            {"role": "user", "content": "Synthetic tea", "timestamp": "2025-01-01T00:00:00Z"},
        ]), encoding="utf-8")
        (self.root / "conversation_summary.json").write_text(json.dumps({"summary": "", "summarized_messages": 0}), encoding="utf-8")
        (self.root / "memories.json").write_text(json.dumps([
            {"id": 7, "category": "preference", "content": "Synthetic user prefers tea", "importance": 5},
        ]), encoding="utf-8")
        (self.root / "characters/default/character.json").write_text(json.dumps({"name": "Synthetic"}), encoding="utf-8")
        self.destination = self.root / "memory_v2_shadow/live_shadow.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return rebuild_shadow(self.root, self.destination, provider_factory=ToyProvider)

    def test_missing_stale_current_and_comparison_are_privacy_safe(self):
        comparator = MemoryV2ShadowComparator(self.root, shadow_path=self.destination, console=False, provider_factory=ToyProvider)
        self.assertEqual(comparator.freshness()["state"], "missing")
        built = self.build()
        self.assertEqual(comparator.freshness()["state"], "current")
        result = comparator.compare("What tea do I prefer?", [{"role": "user", "content": "What tea do I prefer?"}], [{"id": "7", "category": "preference", "rank": 1}])
        self.assertEqual(result["shadow"]["state"], "current")
        self.assertEqual(result["shadow"]["character_id"], built["character_id"])
        self.assertEqual(result["v1"]["selected"][0]["id"], "7")
        self.assertTrue(result["v2"]["selected"])
        self.assertNotIn("content", json.dumps(result))
        (self.root / "memories.json").write_text("[]", encoding="utf-8")
        self.assertEqual(comparator.freshness()["state"], "stale")
        comparator.close()

    def test_repeatability_and_failure_isolation(self):
        self.build()
        comparator = MemoryV2ShadowComparator(self.root, shadow_path=self.destination, console=False, provider_factory=ToyProvider)
        first = comparator.compare("tea", [], ())
        second = comparator.compare("tea", [], ())
        self.assertEqual(first, second)
        comparator.close()
        self.destination.unlink()
        missing = comparator.compare("tea", [], ())
        self.assertEqual(missing["shadow"]["state"], "missing")

    def test_v1_capture_records_ids_categories_and_ranks_not_text(self):
        conversation = Conversation.__new__(Conversation)
        conversation._capture_v1_retrieval_diagnostics = True
        class Memory:
            def get_relevant_memories(self, *_args, **_kwargs):
                return [{"id": 3, "category": "fact", "content": "private synthetic content"}]
        result = conversation.get_relevant_memories(Memory(), "query")
        self.assertEqual(len(result), 1)
        self.assertEqual(conversation._last_v1_retrieval_diagnostics, ({"id": "3", "category": "fact", "rank": 1},))


class _Conversation:
    def __init__(self): self.messages = []
    def add_user_message(self, value): self.messages.append({"role": "user", "content": value})
    def add_assistant_message(self, value): self.messages.append({"role": "assistant", "content": value})
    def save(self): pass
    def update_summary(self): pass


class _Memory:
    memories = []
    def process(self, *_args): pass
    def save(self): pass


class _Tts:
    def speak(self, *_args): pass
    def stop(self): pass
    def set_volume(self, *_args): pass


class _Shadow:
    def __init__(self, fail=False): self.fail = fail
    def compare(self, *_args):
        if self.fail: raise RuntimeError("synthetic shadow failure")
        return {"shadow": {"state": "current"}, "v1": {"selected": [], "count": 0}, "v2": {"selected": [], "count": 0, "abstention_reason": "none"}, "comparison": {"overlap": [], "v1_only": [], "v2_only": [], "count_difference": 0}}
    def close(self): pass


class _OrderedTts(_Tts):
    def __init__(self, order): self.order = order
    def speak(self, *_args): self.order.append("tts_started")


class _OrderedShadow(_Shadow):
    def __init__(self, order): self.order = order
    def compare(self, *_args):
        self.order.append("shadow_started")
        return super().compare(*_args)


class ShadowServiceIsolationTests(unittest.TestCase):
    def service(self, shadow=None):
        return AssistantService(object(), _Memory(), _Conversation(), object(), {}, "", _Tts(), response_generator=lambda *_args: "unchanged reply", memory_v2_shadow=shadow)

    def test_disabled_has_no_shadow_event_and_enabled_never_changes_reply(self):
        disabled = self.service()
        events = []; disabled.subscribe(events.append)
        self.assertEqual(disabled.process_text_turn("hello", speak=False).reply, "unchanged reply")
        self.assertNotIn("memory_shadow", [event.type for event in events])
        enabled = self.service(_Shadow())
        events = []; enabled.subscribe(events.append)
        self.assertEqual(enabled.process_text_turn("hello", speak=False).reply, "unchanged reply")
        shadow_event = next(event for event in events if event.type == "memory_shadow")
        self.assertEqual(shadow_event.data["shadow"]["state"], "current")
        self.assertNotIn("unchanged reply", str(shadow_event.data))

    def test_shadow_failure_isolated_from_turn(self):
        service = self.service(_Shadow(fail=True))
        result = service.process_text_turn("hello", speak=False)
        self.assertTrue(result.succeeded)

    def test_shadow_observation_never_delays_tts_initiation(self):
        order = []
        service = AssistantService(
            object(), _Memory(), _Conversation(), object(), {}, "", _OrderedTts(order),
            response_generator=lambda *_args: "unchanged reply",
            memory_v2_shadow=_OrderedShadow(order),
        )
        service.process_text_turn("hello", speak=True)
        self.assertEqual(order, ["tts_started", "shadow_started"])

    def test_shadow_query_uses_current_and_prior_user_turns_only(self):
        query = build_shadow_query("synthetic", "current user", [
            {"role": "user", "content": "earlier user"},
            {"role": "assistant", "content": "private assistant words"},
            {"role": "user", "content": "current user"},
        ], at="2026-01-01T00:00:00Z")
        self.assertEqual(query.current_user_text, "current user")
        self.assertEqual(query.recent_user_turns, ("earlier user",))


if __name__ == "__main__":
    unittest.main()
