import json
from pathlib import Path
import tempfile
import unittest
import uuid

from benchmarks.memory_v2.open_thread_contextual_extraction import (
    load_manifest, parse_provider_output, provider_input, run_provider_evaluation,
)
from assistant_service import AssistantService
from memory_v2_open_thread_shadow import (JsonlOpenThreadShadowTraceStore, OpenThreadContextualShadowObserver)
from memory_v2_store import MemoryV2Store, OpenThreadProposal, OpenThreadProposalOperation


class _Extractor:
    provider_id = "fake-local"
    model_id = "test"
    def __init__(self, result): self.result, self.inputs = result, []
    def propose(self, value):
        self.inputs.append(value)
        if isinstance(self.result, Exception): raise self.result
        return self.result

class _BenchmarkProvider:
    """Synthetic transport adapter; it deliberately abstains for every case."""
    def generate(self, messages, _instructions):
        payload = json.loads(messages[-1]["content"])
        return json.dumps({"cases":[{"case_id": row["case_id"], "proposal":None} for row in payload["cases"]]})

class _Conversation:
    def __init__(self, path): self.conversation_file, self.messages = str(path), []
    def add_user_message(self, content): self.messages.append({"role":"user","content":content,"timestamp":"2026-01-01T00:00:00Z"})
    def add_assistant_message(self, content): self.messages.append({"role":"assistant","content":content,"timestamp":"2026-01-01T00:00:01Z"})
    def save(self): Path(self.conversation_file).write_text(json.dumps(self.messages), encoding="utf-8")
    def update_summary(self): return None
class _Memory:
    memories = []
    def process(self, *_): return None
class _Tts:
    def set_volume(self, *_): return None
    def speak(self, *_): return True
    def stop(self): return 0


class OpenThreadContextualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.path = self.root / "conversation.json"; self.store = MemoryV2Store()
        self.character = str(uuid.uuid4()); self.store.create_character(self.character, "Synthetic")

    def tearDown(self): self.store.close(); self.temp.cleanup()

    def message(self, text, role="user"):
        message = {"role":role,"content":text,"timestamp":"2026-01-01T00:00:00Z"}
        self.path.write_text(json.dumps([message]), encoding="utf-8")
        return message

    def observer(self, extractor, enabled=True):
        return OpenThreadContextualShadowObserver(self.store, character_id=self.character, extractor=extractor,
            trace_store=JsonlOpenThreadShadowTraceStore(self.root / "trace.jsonl"), enabled=enabled)

    def test_manifest_input_and_parser_are_frozen_and_provider_neutral(self):
        cases = load_manifest(); self.assertEqual(33, len(cases))
        payload = provider_input(cases[:1]); self.assertIn("governed_operations", payload["cases"][0])
        response = json.dumps({"cases":[{"case_id":"open-problem","proposal":{"operations":[
            {"operation":"open","ref":"new1","kind":"unresolved_problem","scope":"user","description":"fix animation regression","evidence":"I still need to fix the animation regression."}
        ]}}]})
        candidates, failures = parse_provider_output(cases[:1], response)
        self.assertEqual({}, failures); self.assertIsNotNone(candidates["open-problem"].proposal)

    def test_benchmark_persists_normalized_candidates_before_reporting(self):
        output = self.root / "run.json"
        report, _candidates, failures = run_provider_evaluation(provider=_BenchmarkProvider(), result_path=output)
        self.assertEqual({}, failures)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("open-thread-contextual-extraction-run-v1", payload["format"])
        self.assertEqual(5, payload["provider_calls"])
        self.assertIn("parsed_candidates", payload["batches"][0])
        self.assertEqual(33, len(report.traces))

    def test_disabled_valid_invalid_and_failure_shadow_paths_never_mutate(self):
        text = "I still need to fix the animation regression."; message = self.message(text)
        proposal = OpenThreadProposal((OpenThreadProposalOperation("open", "new1", 0, len(text), "unresolved_problem", "user", "fix animation regression"),))
        disabled = self.observer(_Extractor(proposal), enabled=False)
        self.assertEqual("disabled", disabled.observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.path)["state"])
        self.assertEqual([], disabled.extractor.inputs); self.assertFalse((self.root / "trace.jsonl").exists())
        self.store.apply_open_thread_proposal = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not apply"))
        result = self.observer(_Extractor(proposal)).observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.path)
        self.assertEqual("validated", result["state"]); self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM open_threads").fetchone()[0])
        self.assertNotIn(text, (self.root / "trace.jsonl").read_text(encoding="utf-8"))
        self.assertEqual("rejected", self.observer(_Extractor("bad")).observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.path)["state"])
        self.assertEqual("failed", self.observer(_Extractor(RuntimeError("timeout"))).observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.path)["state"])

    def test_existing_refs_recent_bounds_and_non_user_evidence_are_safe(self):
        seed = "I am waiting for the package."; self.store.add_event(self.character, "seed", 1, actor_kind="user", recorded_at_us=1, content_text=seed)
        thread = self.store.apply_open_thread_proposal(self.character, OpenThreadProposal((
            OpenThreadProposalOperation("open", "p", 0, len(seed), "waiting", "user", "waiting for package"),
        )), evidence_event_id="seed")[0]["thread_id"]
        text = "The package arrived."; message = self.message(text)
        proposal = OpenThreadProposal((OpenThreadProposalOperation("resolve", "t1", 0, len(text)),))
        observer = self.observer(_Extractor(proposal)); result = observer.observe_canonical_user_turn(message, conversation_index=0, conversation_file=self.path)
        self.assertEqual("validated", result["state"]); self.assertEqual(thread, result["proposal"].operations[0].reference)
        current = observer.extractor.inputs[0]; self.assertEqual(("The package arrived.",), (current.latest_user_turn,))
        self.assertLessEqual(len(current.recent_user_turns), 4); self.assertEqual("t1", current.current_open_threads[0].reference)
        assistant = self.message(text, "assistant")
        self.assertEqual("rejected", self.observer(_Extractor(None)).observe_canonical_user_turn(assistant, conversation_index=0, conversation_file=self.path)["state"])

    def test_service_failure_is_fail_open_and_shadow_never_writes(self):
        conversation = _Conversation(self.root / "service.json")
        observer = OpenThreadContextualShadowObserver(self.store, character_id=self.character,
            extractor=_Extractor(RuntimeError("synthetic provider failure")),
            trace_store=JsonlOpenThreadShadowTraceStore(self.root / "service-trace.jsonl"), enabled=True)
        service = AssistantService(object(), _Memory(), conversation, object(), {"_character_id":self.character}, "", _Tts(),
            response_generator=lambda *_: "reply", open_thread_contextual_shadow=observer, memory_authority="v1")
        self.assertTrue(service.process_text_turn("I need to fix the bike.", speak=False).succeeded)
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM open_threads").fetchone()[0])


if __name__ == "__main__": unittest.main()
