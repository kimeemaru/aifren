import unittest
import threading
import time
from unittest.mock import MagicMock, patch

from assistant_service import AssistantService
from llm.unavailable import ModelTransportError, UnavailableLLM


class FakeConversation:
    def __init__(self):
        self.messages = []
        self.saved = 0
        self.summaries_updated = 0

    def add_user_message(self, content):
        self.messages.append(("user", content))

    def add_assistant_message(self, content):
        self.messages.append(("assistant", content))

    def save(self):
        self.saved += 1

    def update_summary(self):
        self.summaries_updated += 1


class FakeMemory:
    def __init__(self):
        self.memories = [{"id": 1}]
        self.processed = []
        self.saved = 0

    def process(self, user_message, reply):
        self.processed.append((user_message, reply))

    def save(self):
        self.saved += 1


class FakeTTS:
    def __init__(self):
        self.spoken = []
        self.stopped = 0
        self.volume = None
        self.playback_started_callback = None
        self.playback_finished_callback = None

    def set_playback_started_callback(self, callback):
        self.playback_started_callback = callback

    def set_playback_finished_callback(self, callback):
        self.playback_finished_callback = callback

    def speak(self, text):
        self.spoken.append(text)
        if self.playback_started_callback is not None:
            self.playback_started_callback(2.5)

    def stop(self):
        self.stopped += 1

    def set_volume(self, volume):
        self.volume = volume


class FailingTTS(FakeTTS):
    def speak(self, text):
        raise RuntimeError("speaker unavailable")


class RecordingDurableNameWriter:
    def __init__(self, conversation):
        self.conversation = conversation
        self.calls = []

    def observe_canonical_user_message(self, message, *, conversation_index, conversation_file):
        self.calls.append((message, conversation_index, conversation_file, self.conversation.saved))


class FakePushToTalk:
    def __init__(
        self,
        voice,
        tts,
        on_transcription,
        on_state=None,
        on_tts_interrupt=None,
        on_error=None,
    ):
        self.voice = voice
        self.tts = tts
        self.on_transcription = on_transcription
        self.on_state = on_state
        self.on_tts_interrupt = on_tts_interrupt
        self.on_error = on_error
        self.stopped = 0
        self.binding = None
        self.global_enabled = False

    def stop(self):
        self.stopped += 1

    def set_binding(self, binding):
        self.binding = binding

    def enable_global_listener(self):
        self.global_enabled = True

    def global_listener_active(self):
        return self.global_enabled


def fake_response_generator(llm, conversation, memory, message, character_prompt):
    return "*Serval waves.*\n\nHello!"


class AssistantServiceTests(unittest.TestCase):
    def setUp(self):
        self.conversation = FakeConversation()
        self.memory = FakeMemory()
        self.tts = FakeTTS()
        self.service = AssistantService(
            llm=object(),
            memory=self.memory,
            conversation=self.conversation,
            voice=object(),
            character={"name": "Serval"},
            character_prompt="character prompt",
            tts=self.tts,
            response_generator=fake_response_generator,
            ptt_factory=FakePushToTalk,
        )

    def test_text_turn_preserves_turn_lifecycle_and_emote_filtering(self):
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(result.reply, "*Serval waves.*\n\nHello!")
        self.assertEqual(result.spoken_text, "Hello!")
        self.assertEqual(self.tts.spoken, ["Hello!"])
        self.assertEqual(
            self.conversation.messages,
            [("user", "Hello"), ("assistant", result.reply)],
        )
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        # Governed turns persist canonical user evidence before applying V2,
        # then persist the assistant only after mutation consistency passes.
        self.assertEqual(self.conversation.saved, 2)
        self.assertEqual(self.conversation.summaries_updated, 1)
        self.assertEqual(
            [event.type for event in events],
            [
                "turn_started",
                "status",
                "assistant_response",
                "status",
                "tts_state",
                "tts_state",
                "tts_state",
                "conversation_message",
                "conversation_message",
                "memory_updated",
                "status",
            ],
        )
        playback = next(event for event in events if event.data.get("state") == "playback_started")
        self.assertEqual(playback.data["duration_seconds"], 2.5)

    def test_unconfigured_model_rejects_before_persisting_any_turn(self):
        self.service.llm = UnavailableLLM()
        self.service._response_generator = None
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertFalse(result.succeeded)
        self.assertEqual("Configure a model in Settings > Model.", result.error)
        self.assertEqual([], self.conversation.messages)
        self.assertEqual([], self.memory.processed)
        self.assertEqual(0, self.conversation.saved)
        self.assertIn(
            ("error", "model_unconfigured"),
            [(event.type, event.data.get("code")) for event in events],
        )
        self.assertEqual("ready", events[-1].data.get("state"))

    def test_provider_transport_failure_rolls_back_the_unanswered_user_message(self):
        class FailingConversation(FakeConversation):
            def build_context(self, _memory, _user_message, **_kwargs):
                return []

        class UnreachableLocalAdapter:
            def stream_generate(self, _context, _prompt):
                raise ModelTransportError("The configured model is unavailable. Check Settings > Model.")

        conversation = FailingConversation()
        service = AssistantService(
            llm=UnreachableLocalAdapter(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        result = service.process_text_turn("Hello", speak=False)

        self.assertFalse(result.succeeded)
        self.assertEqual("The configured model is unavailable. Check Settings > Model.", result.error)
        self.assertEqual([], conversation.messages)
        self.assertEqual([], self.memory.processed)
        self.assertEqual("unavailable", service.model_runtime_availability())

    def test_provider_activity_diagnostic_tracks_only_live_request_lifetime(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_generator(*_args):
            entered.set()
            release.wait(timeout=2)
            return "Provider response."

        service = AssistantService(
            llm=object(), memory=self.memory, conversation=FakeConversation(), voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
            response_generator=blocking_generator,
        )
        worker = threading.Thread(target=service.process_text_turn, args=("Hello",), kwargs={"speak": False})
        self.assertFalse(service.provider_request_active())
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(service.provider_request_active())
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(service.provider_request_active())

    def test_replacing_model_refreshes_summary_and_memory_provider_references(self):
        old_llm, replacement = object(), object()
        self.service.llm = old_llm
        self.conversation.llm = old_llm
        self.memory.llm = old_llm

        self.service.replace_llm(replacement)

        self.assertIs(replacement, self.service.llm)
        self.assertIs(replacement, self.conversation.llm)
        self.assertIs(replacement, self.memory.llm)

    def test_streaming_generation_emits_deltas_but_persists_one_assistant_message(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return [{"role": "user", "content": user_message}]

        class StreamingLlm:
            def stream_generate(self, context, prompt):
                yield "First sentence. "
                yield "Second sentence."

        conversation = StreamingConversation()
        service = AssistantService(
            llm=StreamingLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        events = []
        service.subscribe(events.append)
        result = service.process_text_turn("Hello", speak=False)
        self.assertEqual("First sentence. Second sentence.", result.reply)
        self.assertEqual([("user", "Hello"), ("assistant", result.reply)], conversation.messages)
        deltas = [event.data["content"] for event in events if event.type == "assistant_delta"]
        self.assertEqual(result.reply, "".join(deltas))
        self.assertEqual(2, len(deltas))

    def test_local_stream_seed_is_created_once_and_recorded_with_turn_id(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                self._last_context_hygiene_metrics = {
                    "context_hygiene_candidates": 3,
                    "context_hygiene_assistant_only_suppressed": 1,
                    "context_hygiene_suppressed": 2,
                    "context_hygiene_user_echo_count": 1,
                    "context_hygiene_self_redundancy_count": 2,
                    "context_hygiene_repetitive_run_count": 1,
                    "exchange_candidates": 3,
                    "exchange_pairs_suppressed": 1,
                    "raw_recent_message_count": 7,
                    "admitted_recent_message_count": 5,
                    "context_hygiene_removed_characters": 120,
                    "context_hygiene_approximate_tokens_removed": 30,
                    "final_context_characters": len(user_message),
                    "approximate_final_context_tokens": 2,
                    "compaction_version": 1,
                    "episode_count": 14,
                    "episode_context_count": 8,
                    "episode_source_record_count": 1114,
                    "compacted_context_characters": 4200,
                    "compacted_context_approximate_tokens": 1050,
                }
                return [{"role": "user", "content": user_message}]

        class SeededStreamingLlm:
            def __init__(self):
                self.created = 0
                self.received_seed = None

            def new_request_seed(self):
                self.created += 1
                return 424242

            def request_sampling_metadata(self):
                return {
                    "sampling_preset": "qwen3.5_non_thinking_general",
                    "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
                    "presence_penalty": 1.5, "repeat_penalty": 1.0,
                }

            def stream_generate(self, context, prompt, *, seed):
                self.received_seed = seed
                yield "Seeded response."

        llm = SeededStreamingLlm()
        recorder = MagicMock()
        conversation = StreamingConversation()
        service = AssistantService(
            llm=llm, memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )

        with patch("assistant_service.development_flight_recorder", return_value=recorder):
            result = service.process_text_turn("Hello", speak=False)

        self.assertTrue(result.succeeded)
        self.assertEqual(1, llm.created)
        self.assertEqual(424242, llm.received_seed)
        recorder.mark.assert_any_call(
            "local_request_seed", turn_id=1, seed=424242, explicit_seed=True,
        )
        recorder.mark.assert_any_call(
            "local_request_sampling", turn_id=1,
            sampling_preset="qwen3.5_non_thinking_general",
            temperature=0.7, top_p=0.8, top_k=20, min_p=0.0,
            presence_penalty=1.5, repeat_penalty=1.0,
        )
        recorder.mark.assert_any_call(
            "context_hygiene",
            turn_id=1,
            context_hygiene_candidates=3,
            context_hygiene_assistant_only_suppressed=1,
            context_hygiene_suppressed=2,
            context_hygiene_user_echo_count=1,
            context_hygiene_self_redundancy_count=2,
            context_hygiene_repetitive_run_count=1,
            exchange_candidates=3,
            exchange_pairs_suppressed=1,
            raw_recent_message_count=7,
            admitted_recent_message_count=5,
            context_hygiene_removed_characters=120,
            context_hygiene_approximate_tokens_removed=30,
            final_context_characters=5,
            approximate_final_context_tokens=2,
            compaction_version=1,
            episode_count=14,
            episode_context_count=8,
            episode_source_record_count=1114,
            compacted_context_characters=4200,
            compacted_context_approximate_tokens=1050,
            final_prompt_characters=11,
            approximate_final_prompt_tokens=3,
        )

    def test_streamed_reasoning_is_hidden_from_deltas_tts_persistence_and_memory(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return [{"role": "user", "content": user_message}]

        class ReasoningLlm:
            def stream_generate(self, context, prompt):
                yield "<thi"
                yield "nk>Private reasoning sentence. "
                yield "Still private.</th"
                yield "ink>\n"
                yield "This final answer is deliberately long enough to become a complete streamed speech sentence. "
                yield "It stays canonical."

        conversation = StreamingConversation()
        service = AssistantService(
            llm=ReasoningLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        events = []
        service.subscribe(events.append)

        result = service.process_text_turn("Hello")
        service._streaming_speech_queue.join(timeout=1)

        expected = "This final answer is deliberately long enough to become a complete streamed speech sentence. It stays canonical."
        deltas = [event.data["content"] for event in events if event.type == "assistant_delta"]
        self.assertEqual(expected, result.reply)
        self.assertEqual(expected, "".join(deltas))
        self.assertEqual([("user", "Hello"), ("assistant", expected)], conversation.messages)
        self.assertEqual([("Hello", expected)], self.memory.processed)
        self.assertEqual(expected, " ".join(self.tts.spoken))
        self.assertNotIn("Private", "".join(deltas) + " ".join(self.tts.spoken))
        streamed_playback = [
            event for event in events
            if event.type == "tts_state" and event.data.get("state") == "playback_started"
        ]
        queued = [
            event for event in events
            if event.type == "tts_state" and event.data.get("state") == "chunk_queued"
        ]
        self.assertEqual(self.tts.spoken, [event.data["content"] for event in queued])
        self.assertEqual(list(range(len(queued))), [event.data["chunk_index"] for event in queued])
        self.assertTrue(all(event.data["streamed"] for event in streamed_playback))
        self.assertEqual(self.tts.spoken, [event.data["content"] for event in streamed_playback])
        self.assertEqual(expected, " ".join(event.data["subtitle_content"] for event in streamed_playback))

    def test_split_streamed_emote_is_never_submitted_to_manual_chunk_tts(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        class SplitEmoteLlm:
            def stream_generate(self, context, prompt):
                yield "*"
                yield "leans closer. Still "
                yield "waiting* This complete spoken sentence is deliberately long enough for streaming speech."

        service = AssistantService(
            llm=SplitEmoteLlm(), memory=self.memory, conversation=StreamingConversation(), voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )

        result = service.process_text_turn("Hello")
        service._streaming_speech_queue.join(timeout=1)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            "This complete spoken sentence is deliberately long enough for streaming speech.",
            " ".join(self.tts.spoken),
        )
        self.assertNotIn("leans", " ".join(self.tts.spoken))
        self.assertIn("*leans closer. Still waiting*", result.reply)


    def test_streamed_sentence_speech_never_changes_canonical_fragment_spacing(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        fragments = (
            "Did", " the", " testing", " make", " me", " forget", " how", " to", " react? ",
            "*leans", " closer and smiles", " warmly* ", "No", " —", " it", " did", " not."
        )

        class FragmentedLlm:
            def stream_generate(self, context, prompt):
                yield from fragments

        service = AssistantService(
            llm=FragmentedLlm(), memory=self.memory, conversation=StreamingConversation(), voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        events = []
        service.subscribe(events.append)

        result = service.process_text_turn("Hello")
        service._streaming_speech_queue.join(timeout=1)

        canonical = "".join(fragments)
        self.assertEqual(canonical, result.reply)
        self.assertEqual(canonical, "".join(
            event.data["content"] for event in events if event.type == "assistant_delta"
        ))
        self.assertEqual(("assistant", canonical), service.conversation.messages[-1])
        self.assertEqual(
            ["Did the testing make me forget how to react?", "No — it did not."],
            self.tts.spoken,
        )
        self.assertNotIn("leans", " ".join(self.tts.spoken))

    def test_streamed_speech_keeps_first_sentence_immediate_then_groups_following_pairs(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        fragments = (
            "First sentence. ", "Second sentence. ", "*waves slowly across the room* ",
            "Third **clear** sentence. ", "Fourth sentence. ", "Final tail",
        )

        class GroupedLlm:
            def stream_generate(self, context, prompt):
                yield from fragments

        conversation = StreamingConversation()
        service = AssistantService(
            llm=GroupedLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )

        result = service.process_text_turn("Hello")
        service._streaming_speech_queue.join(timeout=1)

        canonical = "".join(fragments)
        self.assertEqual(canonical, result.reply)
        self.assertEqual(("assistant", canonical), conversation.messages[-1])
        self.assertEqual(
            ["First sentence.", "Second sentence. Third clear sentence.",
             "Fourth sentence. Final tail"],
            self.tts.spoken,
        )
        self.assertNotIn("waves", " ".join(self.tts.spoken))

    def test_whole_response_provider_ignores_manual_stream_chunk_boundaries(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        class WholeResponseTts(FakeTTS):
            synthesis_strategy = "whole_response"

        class StreamingLlm:
            def stream_generate(self, context, prompt):
                yield "First complete sentence. "
                yield "Second complete sentence with *spoken emphasis*."

        provider = WholeResponseTts()
        service = AssistantService(
            llm=StreamingLlm(), memory=self.memory, conversation=StreamingConversation(), voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=provider,
        )
        events = []
        service.subscribe(events.append)

        result = service.process_text_turn("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            ["First complete sentence. Second complete sentence with spoken emphasis."],
            provider.spoken,
        )
        self.assertFalse(any(
            event.type == "tts_state" and event.data.get("state") == "chunk_queued"
            for event in events
        ))

    def test_ptt_before_first_streamed_speech_chunk_invalidates_later_turn_audio(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return [{"role": "user", "content": user_message}]

        service = None

        class InterruptedLlm:
            calls = 0

            def stream_generate(self, context, prompt):
                self.calls += 1
                yield "An opening fragment without a sentence boundary "
                if self.calls == 1:
                    service.start_push_to_talk().on_tts_interrupt()
                yield "followed by a complete sentence that must remain silent."

        conversation = StreamingConversation()
        service = AssistantService(
            llm=InterruptedLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
            ptt_factory=FakePushToTalk,
        )
        events = []
        service.subscribe(events.append)

        result = service.process_text_turn("Hello")

        self.assertFalse(result.succeeded)
        self.assertEqual("interrupted", result.error)
        self.assertEqual([], self.tts.spoken)
        self.assertIsNone(service._streaming_speech_queue)
        self.assertIn(("voice_event", "tts_interrupted"), [
            (event.type, event.data.get("action")) for event in events
        ])
        self.assertEqual([], conversation.messages)

        next_result = service.process_text_turn("Next turn")
        service._streaming_speech_queue.join(timeout=1)
        self.assertTrue(next_result.succeeded)
        self.assertNotEqual([], self.tts.spoken)

    def test_interruption_discards_a_pending_group_and_future_sentences(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        service = None

        class InterruptedGroupedLlm:
            def stream_generate(self, context, prompt):
                yield "First sentence may already have started. "
                yield "Second sentence is pending for grouping. "
                service.start_push_to_talk().on_tts_interrupt()
                yield "Third sentence must never be admitted."

        conversation = StreamingConversation()
        service = AssistantService(
            llm=InterruptedGroupedLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
            ptt_factory=FakePushToTalk,
        )

        result = service.process_text_turn("Hello")

        self.assertFalse(result.succeeded)
        self.assertEqual("interrupted", result.error)
        self.assertFalse(any("Second" in chunk or "Third" in chunk for chunk in self.tts.spoken))
        self.assertEqual([], conversation.messages)

    def test_nonstreamed_reasoning_is_canonicalized_before_response_consumers(self):
        self.service._response_generator = lambda *_: "<think>private chain</think>\n\nFinal prose."
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertEqual("Final prose.", result.reply)
        self.assertEqual(["Final prose."], self.tts.spoken)
        self.assertEqual(("assistant", "Final prose."), self.conversation.messages[-1])
        self.assertEqual(("Hello", "Final prose."), self.memory.processed[-1])
        response = next(event for event in events if event.type == "assistant_response")
        self.assertEqual("Final prose.", response.data["content"])

    def test_streamed_presentation_envelope_exposes_only_exact_dialogue_deltas(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        class EnvelopeLlm:
            def stream_generate(self, context, prompt):
                yield "<think>private</think>\n"
                yield '{"dialogue":"Did'
                yield ' the'
                yield ' testing preserve spaces? '
                yield '*waves slowly across the room* '
                yield '**Absolutely** it did!",'
                yield '"presentation":null}'

        conversation = StreamingConversation()
        service = AssistantService(
            llm=EnvelopeLlm(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        events = []
        service.subscribe(events.append)

        result = service.process_text_turn("Hello")

        expected = "Did the testing preserve spaces? *waves slowly across the room* **Absolutely** it did!"
        self.assertEqual(expected, result.reply)
        self.assertEqual(
            expected,
            "".join(event.data["content"] for event in events if event.type == "assistant_delta"),
        )
        deadline = time.monotonic() + 1
        while len(self.tts.spoken) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(["Did the testing preserve spaces?", "Absolutely it did!"], self.tts.spoken)
        self.assertEqual(("assistant", expected), conversation.messages[-1])

    def test_durable_name_observer_runs_only_after_canonical_save(self):
        writer = RecordingDurableNameWriter(self.conversation)
        self.service._memory_v2_shadow_writer = writer

        self.service.process_text_turn("My name is Elena.", speak=False)

        self.assertEqual(1, len(writer.calls))
        message, index, conversation_file, saves = writer.calls[0]
        self.assertEqual(("user", "My name is Elena."), message)
        self.assertEqual(0, index)
        self.assertEqual("conversation.json", conversation_file)
        self.assertEqual(2, saves)

    def test_episode_rollover_starts_only_after_canonical_pair_is_saved(self):
        class RolloverConversation(FakeConversation):
            def __init__(self):
                super().__init__()
                self.rollover_calls = []

            def start_episode_compaction_rollover(self):
                self.rollover_calls.append((self.saved, tuple(self.messages)))
                return True

        conversation = RolloverConversation()
        service = AssistantService(
            llm=object(), memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
            response_generator=fake_response_generator,
        )

        result = service.process_text_turn("Hello", speak=False)

        self.assertTrue(result.succeeded)
        self.assertEqual(1, len(conversation.rollover_calls))
        saved_count, snapshot = conversation.rollover_calls[0]
        self.assertEqual(2, saved_count)
        self.assertEqual(
            [("user", "Hello"), ("assistant", "*Serval waves.*\n\nHello!")],
            list(snapshot),
        )

    def test_response_envelope_persists_only_dialogue_and_emits_semantic_metadata(self):
        self.service._response_generator = lambda *_: (
            '{"dialogue":"Hello!","presentation":{"emotion":"happy","intensity":0.75,"gesture":"greeting"}}'
        )
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hi")

        self.assertEqual("Hello!", result.reply)
        self.assertEqual("Hello!", self.conversation.messages[-1][1])
        self.assertEqual("Hello!", self.memory.processed[-1][1])
        self.assertEqual("happy", result.presentation.emotion)
        response = next(event for event in events if event.type == "assistant_response")
        self.assertTrue(response.data["has_presentation"])
        self.assertEqual(
            {"emotion": "happy", "intensity": 0.75, "has_intensity": True, "gesture": "greeting"},
            response.data["presentation"],
        )

    def test_playback_alignment_and_completion_are_forwarded_without_changing_turn_data(self):
        events = []
        self.service.subscribe(events.append)

        self.tts.playback_started_callback(1.5, [0.1], [0.0, 0.7], 42)
        self.tts.playback_finished_callback(42)

        started = events[0]
        self.assertEqual("playback_started", started.data["state"])
        self.assertEqual([0.0, 0.7], started.data["word_start_seconds"])
        self.assertEqual(42, started.data["playback_id"])
        self.assertEqual("stopped", events[1].data["state"])
        self.assertEqual(42, events[1].data["playback_id"])

    def test_stale_natural_completion_cannot_clear_newer_playback_state(self):
        events = []
        self.service.subscribe(events.append)

        self.tts.playback_started_callback(1.0, [], [], 10)
        self.tts.playback_started_callback(1.0, [], [], 11)
        self.tts.playback_finished_callback(10)

        self.assertEqual(11, self.service._active_tts_playback_id)
        self.assertEqual([], [event for event in events if event.data.get("state") == "stopped"])

        self.tts.playback_finished_callback(11)
        self.assertEqual(0, self.service._active_tts_playback_id)
        self.assertEqual(11, events[-1].data["playback_id"])

    def test_explicit_stop_clears_service_playback_state_before_tts_cleanup(self):
        self.tts.playback_started_callback(1.0, [], [], 23)

        self.service.stop_speaking()

        self.assertEqual(0, self.service._active_tts_playback_id)
        self.assertEqual(1, self.tts.stopped)

    def test_turn_can_skip_speech_for_a_frontend_that_owns_playback(self):
        result = self.service.process_text_turn("Hello", speak=False)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.tts.spoken, [])
        self.assertEqual(result.spoken_text, "Hello!")

    def test_controls_and_save_use_existing_dependencies(self):
        self.service.set_tts_volume(0.4)
        self.service.stop_speaking()
        self.service.save()

        self.assertEqual(self.tts.volume, 0.4)
        self.assertEqual(self.tts.stopped, 1)
        self.assertEqual(self.conversation.saved, 1)
        self.assertEqual(self.memory.saved, 1)

    def test_tts_failure_keeps_the_turn_persistence_lifecycle(self):
        self.service.tts = FailingTTS()
        events = []
        self.service.subscribe(events.append)

        result = self.service.process_text_turn("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            self.conversation.messages[-1],
            ("assistant", result.reply),
        )
        self.assertEqual(self.memory.processed, [("Hello", result.reply)])
        self.assertEqual(self.conversation.saved, 2)
        self.assertEqual(self.conversation.summaries_updated, 1)
        self.assertIn(
            ("error", "tts"),
            [
                (event.type, event.data.get("source"))
                for event in events
            ],
        )

    def test_ptt_state_and_tts_interruption_route_through_service_events(self):
        events = []
        self.service.subscribe(events.append)

        ptt = self.service.start_push_to_talk()
        ptt.on_state("listening")
        ptt.on_tts_interrupt()

        self.assertIs(self.service.start_push_to_talk(), ptt)
        self.assertEqual(self.tts.stopped, 1)
        self.assertIn(
            ("voice_state", "listening"),
            [
                (event.type, event.data.get("state"))
                for event in events
            ],
        )
        self.assertIn(
            ("tts_state", "stopped"),
            [
                (event.type, event.data.get("state"))
                for event in events
            ],
        )

    @patch("assistant_service.development_flight_recorder")
    def test_ptt_transcription_uses_the_common_text_turn_path(self, recorder_factory):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        result = ptt.on_transcription("Hello")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            self.conversation.messages,
            [("user", "Hello"), ("assistant", result.reply)],
        )
        voice_states = [event.data.get("state") for event in events if event.type == "voice_state"]
        self.assertEqual(voice_states[-1], "ready")
        self.assertTrue(any(
            call.args == ("voice_transcription_emitted",)
            and call.kwargs == {"characters": 5, "words": 1}
            for call in recorder_factory.return_value.mark.call_args_list
        ))

    def test_ptt_review_mode_emits_text_without_persisting_or_starting_a_turn(self):
        events = []
        self.service.subscribe(events.append)
        self.service.set_ptt_auto_submit_transcriptions(False)
        ptt = self.service.start_push_to_talk()

        result = ptt.on_transcription("Please let me review this")

        self.assertIsNone(result)
        self.assertEqual(self.conversation.messages, [])
        self.assertEqual(self.memory.processed, [])
        self.assertIn(
            ("voice_transcription", "Please let me review this"),
            [(event.type, event.data.get("content")) for event in events],
        )

    def test_empty_ptt_transcription_does_not_start_another_turn(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        self.assertIsNone(ptt.on_transcription("   "))
        self.assertEqual(self.conversation.messages, [])

        self.assertIn(
            "voice_transcription",
            [event.type for event in events],
        )

    def test_typed_turn_replaces_active_stream_and_old_status_cannot_win(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return [{"role": "user", "content": user_message}]

        first_delta = threading.Event()
        release_old_provider = threading.Event()
        old_stream_closed = threading.Event()

        class ReplaceableLlm:
            def __init__(self):
                self.calls = 0
                self.cancelled = 0

            def cancel_active_generation(self):
                self.cancelled += 1

            def stream_generate(self, context, prompt):
                self.calls += 1
                if self.calls == 1:
                    try:
                        yield "Old partial response "
                        first_delta.set()
                        release_old_provider.wait(2)
                        yield "that must be abandoned."
                    finally:
                        old_stream_closed.set()
                    return
                yield "Replacement response."

        conversation = StreamingConversation()
        llm = ReplaceableLlm()
        service = AssistantService(
            llm=llm, memory=self.memory, conversation=conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=self.tts,
        )
        events = []
        service.subscribe(events.append)
        results = {}
        old_thread = threading.Thread(
            target=lambda: results.setdefault("old", service.process_text_turn("old", speak=False))
        )
        old_thread.start()
        self.assertTrue(first_delta.wait(1))
        new_thread = threading.Thread(
            target=lambda: results.setdefault("new", service.process_text_turn("new", speak=False))
        )
        new_thread.start()
        time.sleep(.05)
        release_old_provider.set()
        old_thread.join(2)
        new_thread.join(2)

        self.assertEqual("interrupted", results["old"].error)
        self.assertTrue(old_stream_closed.is_set())
        self.assertEqual(1, llm.cancelled)
        self.assertTrue(results["new"].succeeded)
        self.assertEqual(
            [("user", "new"), ("assistant", "Replacement response.")],
            conversation.messages,
        )
        self.assertEqual(
            [("user", "new"), ("assistant", "Replacement response.")],
            [(event.data["role"], event.data["content"]) for event in events if event.type == "conversation_message"],
        )
        started = [event.data["user_message"] for event in events if event.type == "turn_started"]
        self.assertEqual(["old", "new"], started)
        cancellation_index = next(i for i, event in enumerate(events) if event.type == "turn_cancelled")
        replacement_started = next(
            i for i, event in enumerate(events)
            if event.type == "turn_started" and event.data["user_message"] == "new"
        )
        self.assertLess(cancellation_index, replacement_started)
        statuses_after_replacement = [
            event.data.get("state") for event in events[replacement_started + 1:] if event.type == "status"
        ]
        self.assertEqual(["thinking", "ready"], statuses_after_replacement)

    def test_new_turn_interrupts_active_playback_before_generation(self):
        events = []
        self.service.subscribe(events.append)
        first = self.service.process_text_turn("First")
        self.assertTrue(first.succeeded)
        self.tts.playback_started_callback(2.5, [], [], 41)
        self.assertNotEqual(0, self.service._active_tts_playback_id)

        second = self.service.process_text_turn("Second", speak=False)

        self.assertTrue(second.succeeded)
        self.assertGreaterEqual(self.tts.stopped, 1)
        stopped = next(event for event in events if event.type == "tts_state" and event.data.get("state") == "stopped")
        second_started = next(
            event for event in events if event.type == "turn_started" and event.data.get("user_message") == "Second"
        )
        self.assertLess(events.index(stopped), events.index(second_started))

    def test_ptt_during_active_stream_chunk_stops_and_stale_chunk_cannot_restart(self):
        class StreamingConversation(FakeConversation):
            def build_context(self, memory, user_message, **_kwargs):
                return []

        class BlockingChunkTts(FakeTTS):
            def __init__(self):
                super().__init__()
                self.playback_finished = threading.Event()
                self.started = threading.Event()
                self.next_id = 70

            def prepare_stream_chunk(self, text):
                return text

            def start_prepared_chunk(self, text):
                self.spoken.append(text)
                self.playback_finished.clear()
                self.next_id += 1
                self.playback_started_callback(5.0, [], [0.0], self.next_id)
                self.started.set()
                return True

            def stop(self):
                self.stopped += 1
                self.playback_finished.set()
                return self.next_id

        class TwoChunkLlm:
            def stream_generate(self, context, prompt):
                yield "This is the first complete sentence for active playback. "
                yield "This is the second complete sentence that must never restart."

        tts = BlockingChunkTts()
        service = AssistantService(
            llm=TwoChunkLlm(), memory=self.memory, conversation=StreamingConversation(), voice=object(),
            character={"name": "Serval"}, character_prompt="prompt", tts=tts,
            ptt_factory=FakePushToTalk,
        )
        events = []
        service.subscribe(events.append)
        result = service.process_text_turn("Hello")
        self.assertTrue(result.succeeded)
        self.assertTrue(tts.started.wait(1))
        queue = service._streaming_speech_queue

        service.start_push_to_talk().on_tts_interrupt()
        if queue is not None:
            queue.join(1)

        self.assertEqual(1, len(tts.spoken))
        self.assertGreaterEqual(tts.stopped, 1)
        stopped = [event for event in events if event.type == "tts_state" and event.data.get("state") == "stopped"][-1]
        self.assertTrue(stopped.data["interrupted"])
        self.assertTrue(stopped.data["streamed"])

    def test_ptt_errors_are_reported_as_backend_errors(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()

        ptt.on_error("microphone unavailable")

        self.assertIn(
            ("error", "voice"),
            [
                (event.type, event.data.get("source"))
                for event in events
            ],
        )

    def test_ptt_binding_uses_the_single_global_listener(self):
        events = []
        self.service.subscribe(events.append)

        self.service.set_push_to_talk_binding("Mouse4")
        ptt = self.service.start_push_to_talk()

        self.assertEqual(ptt.binding, "Mouse4")
        self.assertTrue(ptt.global_enabled)
        self.assertIn(
            ("voice_state", "Mouse4"),
            [(event.type, event.data.get("binding")) for event in events],
        )

    def test_ptt_binding_does_not_regress_to_f8_after_reuse(self):
        self.service.set_push_to_talk_binding("Mouse4")
        ptt = self.service.start_push_to_talk()
        self.service.start_push_to_talk()
        self.assertEqual(ptt.binding, "Mouse4")

    def test_ptt_binding_reports_when_the_global_listener_is_unavailable(self):
        class UnavailablePushToTalk(FakePushToTalk):
            def global_listener_active(self):
                return False

        service = AssistantService(
            llm=object(), memory=self.memory, conversation=self.conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="character prompt", tts=self.tts,
            response_generator=fake_response_generator, ptt_factory=UnavailablePushToTalk,
        )
        events = []
        service.subscribe(events.append)
        service.set_push_to_talk_binding("Mouse4")
        event = [item for item in events if item.type == "voice_state"][-1]
        self.assertFalse(event.data["global_listener"])

    def test_ptt_state_events_preserve_global_listener_availability(self):
        events = []
        self.service.subscribe(events.append)
        ptt = self.service.start_push_to_talk()
        ptt.global_enabled = True

        self.service._handle_ptt_state("listening")

        event = [item for item in events if item.type == "voice_state"][-1]
        self.assertEqual("listening", event.data["state"])
        self.assertTrue(event.data["global_listener"])

    def test_v2_dual_read_is_fail_open_and_never_changes_v1_turn_data(self):
        class FailingShadowWriter:
            def compare(self, *_args, **_kwargs):
                raise RuntimeError("diagnostic database unavailable")

        service = AssistantService(
            llm=object(), memory=self.memory, conversation=self.conversation, voice=object(),
            character={"name": "Serval"}, character_prompt="character prompt", tts=self.tts,
            response_generator=fake_response_generator, ptt_factory=FakePushToTalk,
            memory_v2_shadow_writer=FailingShadowWriter(),
        )
        events = []
        service.subscribe(events.append)
        service._run_memory_v2_shadow("safe query")
        event = [event for event in events if event.type == "memory_v2_parity"][-1]
        self.assertEqual("memory_v2_shadow_writer", event.data["error"]["source"])
        self.assertEqual([], self.conversation.messages)


if __name__ == "__main__":
    unittest.main()
