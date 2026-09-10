from pathlib import Path
import json
from types import SimpleNamespace
import tempfile
import unittest

from assistant_service import AssistantService, _ResponsePolicy
from conversation.conversation import Conversation
from memory_v2_answer_governance import (
    MemoryAnswerEvidence,
    compose_memory_answer_requirement,
)
from durable_fact_curation import topic_subject_key
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository
from presentation_metadata import parse_assistant_response


class _Memory:
    def __init__(self):
        self.memories = []
        self.processed = []
        self.retrieval_calls = 0
        self.save_calls = 0

    def get_relevant_memories(self, *_args, **_kwargs):
        self.retrieval_calls += 1
        raise AssertionError("Memory V1 retrieval entered V2 authority context")

    def process(self, user, reply):
        self.processed.append((user, reply))

    def save(self):
        self.save_calls += 1


class _LLM:
    def __init__(self, response="Hello."):
        self.response = response
        self.calls = []

    def generate(self, context, prompt):
        self.calls.append((tuple(context), prompt))
        if isinstance(self.response, (list, tuple)):
            index = min(len(self.calls) - 1, len(self.response) - 1)
            return self.response[index]
        return self.response

    def generate_bounded(self, context, prompt, **_kwargs):
        return self.generate(context, prompt)


class _TTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


class _Authority:
    character_id = "11111111-1111-4111-8111-111111111111"
    scope_id = "scope-22222222-2222-4222-8222-222222222222"

    def __init__(self, *, no_evidence, governed_value=""):
        self.no_evidence = no_evidence
        self.governed_value = governed_value
        self.decisions = []

    def prepare(self, query, **kwargs):
        decision = kwargs.get("memory_query_decision")
        self.decisions.append(decision)
        evidence = ()
        if self.governed_value:
            evidence = (MemoryAnswerEvidence(
                "favorite-color", "governed_current_fact", "", "real_world",
                "assertion", "", "identity.favorite_color", self.governed_value,
            ),)
        requirement = compose_memory_answer_requirement(
            query, evidence, memory_query_decision=decision,
        )
        if not self.no_evidence and not self.governed_value:
            requirement = compose_memory_answer_requirement(
                query, (), memory_query_decision=decision,
            )
        return SimpleNamespace(
            requirement=requirement,
            authoritative_no_evidence=self.no_evidence,
            context_block="[Long-term memory authority — backend policy]\nV2 only",
            retrieval_latency_ms=1.25,
            candidate_count=0,
            abstention_reason="no_candidates",
            absence_kind="nothing_relevant",
            memory_query_decision=decision,
        )

    def close(self):
        pass


class AssistantServiceV2AuthorityTests(unittest.TestCase):
    def test_v2_mode_fails_closed_without_authority_owner(self):
        with tempfile.TemporaryDirectory() as root:
            llm = _LLM()
            conversation = Conversation(
                llm,
                conversation_file=Path(root) / "conversation.json",
                summary_file=Path(root) / "summary.json",
            )
            with self.assertRaisesRegex(RuntimeError, "could not initialize"):
                AssistantService(
                    llm=llm, memory=_Memory(), conversation=conversation,
                    voice=object(), character={"name": "Test"},
                    character_prompt="character prompt", tts=_TTS(),
                    memory_authority="v2", memory_v2_authority=None,
                )

    def _service(
        self, root, *, no_evidence, response="Hello.", governed_value="",
        writer=None,
    ):
        llm = _LLM(response)
        memory = _Memory()
        conversation = Conversation(
            llm,
            conversation_file=Path(root) / "conversation.json",
            summary_file=Path(root) / "summary.json",
        )
        authority = _Authority(
            no_evidence=no_evidence, governed_value=governed_value,
        )
        service = AssistantService(
            llm=llm, memory=memory, conversation=conversation, voice=object(),
            character={
                "name": "Test",
                "_character_id": _Authority.character_id,
            },
            character_prompt="character prompt", tts=_TTS(),
            character_id=_Authority.character_id,
            memory_v2_shadow_writer=writer,
            memory_authority="v2",
            memory_v2_authority=authority,
        )
        service.truth_scope_provenance = lambda: {
            "kind": "real_world", "scope_id": _Authority.scope_id,
        }
        return service, llm, memory, conversation

    def test_cat_callback_uses_authoritative_absence_and_bypasses_provider(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, _memory, _conversation = self._service(
                root, no_evidence=True,
            )
            result = service.process_text_turn(
                "Do you know if I told you about my cat?", speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertIn(result.reply, {
                "I don't remember you telling me about your cat.",
                "I can't place you mentioning your cat before.",
            })
            self.assertEqual([], llm.calls)
            diagnostics = service._last_memory_authority_diagnostics
            self.assertEqual("user_historical_source", diagnostics["memory_query_intent"])
            self.assertEqual("historical_recall", diagnostics["requested_relation"])
            self.assertEqual("user", diagnostics["requested_speaker"])
            self.assertTrue(diagnostics["provider_bypassed"])
            self.assertFalse(diagnostics["v1_prompt_retrieval_entered"])

    def test_no_evidence_bypasses_provider_and_persists_normal_turn(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, memory, conversation = self._service(
                root, no_evidence=True,
            )
            result = service.process_text_turn(
                "Which motorcycle did I say I owned?", speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual("I don't remember you saying you owned one.", result.reply)
            self.assertEqual([], llm.calls)
            self.assertEqual(0, memory.retrieval_calls)
            self.assertEqual([], memory.processed)
            self.assertEqual(2, len(conversation.messages))
            self.assertTrue(
                service._last_memory_authority_diagnostics[
                    "authoritative_no_evidence"
                ]
            )
            self.assertFalse(
                service._last_memory_authority_diagnostics["fallback_used"]
            )

    def test_generic_authoritative_absence_is_accepted_without_repair(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, _memory, _conversation = self._service(
                root, no_evidence=True,
            )
            result = service.process_text_turn(
                "What's something oddly specific you remember from long ago?",
                speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual([], llm.calls)
            diagnostics = service._last_memory_authority_diagnostics
            self.assertTrue(diagnostics["authoritative_no_evidence"])
            self.assertFalse(diagnostics["repair_attempted"])
            self.assertFalse(diagnostics["fallback_used"])

    def test_plan_and_place_absence_are_authoritative_without_provider(self):
        for query in (
            "Do you remember something I was planning to do later?",
            "Do you remember a place I talked about going to before?",
        ):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as root:
                service, llm, _memory, _conversation = self._service(
                    root, no_evidence=True,
                )
                result = service.process_text_turn(query, speak=False)
                self.assertTrue(result.succeeded)
                self.assertEqual([], llm.calls)
                diagnostics = service._last_memory_authority_diagnostics
                self.assertTrue(diagnostics["authoritative_no_evidence"])
                self.assertFalse(diagnostics["repair_attempted"])
                self.assertFalse(diagnostics["fallback_used"])

    def test_v2_context_never_calls_or_renders_v1_memory(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, memory, _conversation = self._service(
                root, no_evidence=False,
            )
            result = service.process_text_turn("Hello there", speak=False)
            self.assertTrue(result.succeeded)
            self.assertEqual(0, memory.retrieval_calls)
            self.assertEqual(1, len(llm.calls))
            context, _prompt = llm.calls[0]
            rendered = "\n".join(str(item.get("content", "")) for item in context)
            self.assertIn("V2 only", rendered)
            self.assertNotIn("AUTHORITATIVE LIFELONG MEMORIES", rendered)
            self.assertNotIn("LONG-TERM CONVERSATION BACKGROUND", rendered)

    def test_default_v1_mode_retains_existing_write_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            llm = _LLM("Ordinary V1 response.")
            memory = _Memory()
            conversation = Conversation(
                llm,
                conversation_file=Path(root) / "conversation.json",
                summary_file=Path(root) / "summary.json",
            )
            summary_calls = []
            conversation.update_summary = lambda: summary_calls.append(True)
            service = AssistantService(
                llm=llm, memory=memory, conversation=conversation,
                voice=object(), character={"name": "Test"},
                character_prompt="character prompt", tts=_TTS(),
                response_generator=lambda *_args: "Ordinary V1 response.",
                memory_authority="v1",
            )

            result = service.process_text_turn("Hello there", speak=False)

            self.assertTrue(result.succeeded)
            self.assertEqual(
                [("Hello there", "Ordinary V1 response.")], memory.processed,
            )
            self.assertEqual([True], summary_calls)
            self.assertEqual("v1", service.memory_authority_status()["mode"])

    def test_special_provider_path_uses_retrospective_guard(self):
        with tempfile.TemporaryDirectory() as root:
            service, _llm, _memory, conversation = self._service(
                root, no_evidence=False,
            )
            conversation.add_user_message(
                "Do you know if I told you about my cat?",
                truth_scope={
                    "kind": "real_world", "scope_id": _Authority.scope_id,
                },
            )
            policy = service._apply_v2_retrospective_only_policy(
                "",
                _ResponsePolicy(),
                active_truth_scope={
                    "kind": "real_world", "scope_id": _Authority.scope_id,
                },
            )

            accepted, category, _spoken, _presentation = (
                service._validate_governed_response(
                    parse_assistant_response(
                        "You previously described your super pampered cat."
                    ),
                    policy,
                )
            )

            self.assertFalse(accepted)
            self.assertIn("unsupported_retrospective_claim", category)

    def test_recent_context_keeps_latest_twelve_and_current_query(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, memory, conversation = self._service(
                root, no_evidence=False,
            )
            for index in range(20):
                conversation.add_user_message(
                    f"old user {index}", truth_scope={
                        "kind": "real_world", "scope_id": _Authority.scope_id,
                    },
                )
                conversation.add_assistant_message(
                    f"old assistant {index}", truth_scope={
                        "kind": "real_world", "scope_id": _Authority.scope_id,
                    },
                )
            result = service.process_text_turn("current query", speak=False)
            self.assertTrue(result.succeeded)
            context, _prompt = llm.calls[0]
            raw = [
                item for item in context
                if str(item.get("content", "")).startswith(("old user", "old assistant"))
                or item.get("content") == "current query"
            ]
            self.assertLessEqual(len(raw), 12)
            self.assertEqual("current query", raw[-1]["content"])
            self.assertEqual(0, memory.retrieval_calls)

    def test_grounded_current_fact_uses_provider_and_typed_validation(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, memory, _conversation = self._service(
                root, no_evidence=False,
                response="Your favorite color is purple.",
                governed_value="purple",
            )
            result = service.process_text_turn(
                "What is my favorite color?", speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual("Your favorite color is purple.", result.reply)
            self.assertEqual(1, len(llm.calls))
            self.assertEqual(0, memory.retrieval_calls)

    def test_memory_query_containment_excludes_unrelated_recent_dialogue(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, _memory, conversation = self._service(
                root, no_evidence=False,
                response="Your favorite color is purple.",
                governed_value="purple",
            )
            conversation.add_user_message(
                "Did I own a motorcycle?", truth_scope={
                    "kind": "real_world", "scope_id": _Authority.scope_id,
                },
            )
            conversation.add_assistant_message(
                "You owned a Harley-Davidson.", truth_scope={
                    "kind": "real_world", "scope_id": _Authority.scope_id,
                },
            )
            result = service.process_text_turn(
                "What is my favorite color?", speak=False,
            )
            self.assertTrue(result.succeeded)
            context, _prompt = llm.calls[0]
            rendered = "\n".join(str(item.get("content", "")) for item in context)
            self.assertIn("continuity only", rendered)
            self.assertNotIn("Harley-Davidson", rendered)
            self.assertIn("What is my favorite color?", rendered)

    def test_composite_current_state_requirement_does_not_bypass_provider(self):
        with tempfile.TemporaryDirectory() as root:
            service, _llm, _memory, _conversation = self._service(
                root, no_evidence=True,
            )
            policy = service._apply_memory_authority_policy(
                "What am I wearing, and which motorcycle did I say I owned?",
                _ResponsePolicy(requirement=object()),
                active_truth_scope={
                    "kind": "real_world", "scope_id": _Authority.scope_id,
                },
            )
            self.assertIsNone(policy.authoritative_memory_response)
            self.assertIsNotNone(policy.memory_answer_requirement)

    def test_spontaneous_unsupported_memory_decoration_is_repaired_and_revalidated(self):
        with tempfile.TemporaryDirectory() as root:
            service, llm, _memory, _conversation = self._service(
                root,
                no_evidence=False,
                response=(
                    "I think you enjoy quiet projects. You previously described your super pampered cat.",
                    "I think you enjoy quiet projects.",
                ),
            )
            result = service.process_text_turn(
                "What do you think I like to do?", speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual("I think you enjoy quiet projects.", result.reply)
            self.assertEqual(2, len(llm.calls))
            diagnostics = service._last_memory_authority_diagnostics
            self.assertTrue(diagnostics["repair_attempted"])
            self.assertTrue(diagnostics["repair_succeeded"])
            self.assertFalse(diagnostics["fallback_used"])
            self.assertEqual(1, diagnostics["spontaneous_retrospective_claim_count"])
            self.assertEqual(1, diagnostics["unsupported_retrospective_claim_count"])

    def test_failed_repair_projects_only_unsupported_retrospective_sentence(self):
        contaminated = (
            "Cats can be lovely companions. "
            "From what you've told me, yours is super pampered."
        )
        with tempfile.TemporaryDirectory() as root:
            service, llm, _memory, _conversation = self._service(
                root, no_evidence=False, response=(contaminated, contaminated),
            )
            result = service.process_text_turn(
                "What do you think about cats?", speak=False,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual("Cats can be lovely companions.", result.reply)
            self.assertEqual(2, len(llm.calls))
            diagnostics = service._last_memory_authority_diagnostics
            self.assertTrue(diagnostics["repair_attempted"])
            self.assertFalse(diagnostics["repair_succeeded"])
            self.assertTrue(diagnostics["fallback_used"])

    def test_v2_turns_leave_v1_memory_and_summary_bytes_unchanged(self):
        class Writer:
            character_id = _Authority.character_id
            store = None

            def __init__(self):
                self.comparisons = 0
                self.observations = 0

            def compare(self, *_args, **_kwargs):
                self.comparisons += 1
                return {"state": "ok"}

            def observe_canonical_user_message(self, *_args, **_kwargs):
                self.observations += 1

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            memory_path = root_path / "memories.json"
            summary_path = root_path / "summary.json"
            memory_path.write_text("[]", encoding="utf-8")
            summary_path.write_text(json.dumps({
                "summary": "original V1 summary",
                "summarized_messages": 8,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            before_memory = memory_path.read_bytes()
            before_summary = summary_path.read_bytes()
            writer = Writer()
            service, llm, memory, conversation = self._service(
                root, no_evidence=True, writer=writer,
            )
            for query in (
                "Do you know if I told you about my cat?",
                "Do you remember anything about my dog?",
            ):
                result = service.process_text_turn(query, speak=False)
                self.assertTrue(result.succeeded)
            service.save()

            self.assertEqual(before_memory, memory_path.read_bytes())
            self.assertEqual(before_summary, summary_path.read_bytes())
            self.assertEqual([], memory.processed)
            self.assertEqual(0, memory.save_calls)
            self.assertEqual([], llm.calls)
            self.assertEqual(4, len(conversation.messages))
            self.assertGreaterEqual(writer.comparisons, 2)
            self.assertGreaterEqual(writer.observations, 2)
            self.assertEqual(
                "original V1 summary",
                Conversation(
                    llm,
                    conversation_file=root_path / "conversation.json",
                    summary_file=summary_path,
                ).summary_data["summary"],
            )

    def test_embedded_current_assertion_is_observed_and_kept_for_immediate_continuity(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            memory_path = root_path / "memories.json"
            memory_path.write_text("[]", encoding="utf-8")
            writer = MemoryV2ShadowWriter(
                root_path, character_id=_Authority.character_id,
                display_name="Test", memory_file=memory_path,
            )
            service, llm, memory, _conversation = self._service(
                root, no_evidence=False,
                response=("That sounds refreshing.", "Watermelon sounds refreshing."),
                writer=writer,
            )
            scope_id = writer.store.active_truth_scope_id(_Authority.character_id)
            service.truth_scope_provenance = lambda: {
                "kind": "real_world", "scope_id": scope_id,
            }
            try:
                first = service.process_text_turn(
                    "Did you know that I like watermelon?", speak=False,
                )
                self.assertTrue(first.succeeded)
                current = MemoryV2Repository(writer.store).lookup_durable_core(
                    _Authority.character_id,
                    topic_subject_key("preference", "watermelon"),
                ).candidates
                self.assertEqual(1, len(current))
                self.assertEqual("The user likes watermelon.", current[0].content)

                followup = service.process_text_turn(
                    "What do you think about that?", speak=False,
                )
                self.assertTrue(followup.succeeded)
                rendered = "\n".join(
                    str(item.get("content", "")) for item in llm.calls[-1][0]
                )
                self.assertIn("Did you know that I like watermelon?", rendered)
                self.assertEqual([], memory.processed)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
