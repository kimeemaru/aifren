import json
from pathlib import Path
import tempfile
import unittest

from conversation.context_hygiene import ContextHygiene
from conversation.conversation import Conversation


def message(role, content, index=0):
    return {"role": role, "content": content, "timestamp": f"t{index}"}


class _Memory:
    def get_relevant_memories(self, _query, max_memories):
        self.max_memories = max_memories
        return []


class _LLM:
    pass


class ContextHygieneTests(unittest.TestCase):
    repeated = (
        "I understand that testing made the {subject} feel repetitive, and I want to respond "
        "playfully while keeping the conversation moving forward with a fresh idea."
    )

    distinct_loop_prompts = (
        "Describe a synthetic evening activity beside the observatory.",
        "Which tool should we pack for a harmless garden repair?",
        "Invent a playful name for the test weather balloon.",
    )

    def test_normal_related_continuity_remains_verbatim(self):
        messages = [
            message("user", "How is the balcony garden doing?", 1),
            message("assistant", "The balcony herbs are thriving, though the basil needs a little more sunlight.", 2),
            message("user", "What should we plant beside it?", 3),
            message("assistant", "Mint would spread too quickly there, so compact thyme would be the gentler neighbor.", 4),
            message("user", "Can we water everything tomorrow morning?", 5),
            message("assistant", "Tomorrow morning is a good time because the leaves can dry before evening.", 6),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.suppressed_count)

    def test_model_loop_keeps_archive_input_and_only_newest_repetition(self):
        messages = []
        for index, (subject, prompt) in enumerate(zip(
            ("evening", "night", "conversation"), self.distinct_loop_prompts,
        )):
            messages.extend([
                message("user", prompt, index * 2),
                message("assistant", self.repeated.format(subject=subject), index * 2 + 1),
            ])
        raw_snapshot = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        result = ContextHygiene().filter(messages)

        self.assertEqual(raw_snapshot, json.dumps(messages, ensure_ascii=False, sort_keys=True))
        admitted_assistant = [item for item in result.messages if item["role"] == "assistant"]
        self.assertEqual([messages[-1]], admitted_assistant)
        self.assertEqual(2, result.stats.suppressed_count)
        self.assertEqual(2, result.stats.assistant_only_suppressed_count)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)
        self.assertEqual(2, result.stats.self_redundancy_count)
        self.assertEqual(1, result.stats.repetitive_run_count)
        self.assertEqual(3, len([item for item in result.messages if item["role"] == "user"]))

    def test_repeated_user_echoes_keep_users_and_only_newest_assistant_echo(self):
        first_user = (
            "Please say the copper lantern is beside the blue window and the folded map is on the table tonight."
        )
        second_user = (
            "Please confirm the picnic basket contains two red cups and a clean blanket for tomorrow morning."
        )
        messages = [
            message("user", first_user, 1),
            message("assistant", first_user, 2),
            message("user", second_user, 3),
            message("assistant", second_user, 4),
            message("user", "Now change direction and suggest a garden activity.", 5),
        ]
        result = ContextHygiene().filter(messages)

        self.assertEqual(2, result.stats.user_echo_count)
        self.assertEqual(1, result.stats.suppressed_count)
        self.assertEqual(1, result.stats.assistant_only_suppressed_count)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)
        self.assertEqual(3, len([item for item in result.messages if item["role"] == "user"]))
        self.assertIn(messages[3], result.messages)
        self.assertEqual(messages[-1], result.messages[-1])

    def test_single_legitimate_reference_is_not_filtered(self):
        quoted = (
            "The calibration note says the northern sensor should remain exactly twelve centimeters above the marked rail."
        )
        messages = [
            message("user", f"Please quote this once so I can verify it: {quoted}", 1),
            message("assistant", quoted, 2),
            message("user", "Thanks; now explain why that measurement matters.", 3),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.suppressed_count)

    def test_new_direction_survives_after_a_repetitive_run(self):
        messages = []
        for index, (subject, prompt) in enumerate(zip(
            ("evening", "night", "conversation"), self.distinct_loop_prompts,
        )):
            messages.extend([
                message("user", prompt, index * 2),
                message("assistant", self.repeated.format(subject=subject), index * 2 + 1),
            ])
        recovery_user = message("user", "Leave that topic behind and help plan a small astronomy picnic.", 7)
        recovery_assistant = message(
            "assistant",
            "We can pack a red flashlight, choose a moonless hour, and bring a simple star chart for the picnic.",
            8,
        )
        latest_user = message("user", "Which constellation should we look for first?", 9)
        messages.extend([recovery_user, recovery_assistant, latest_user])
        result = ContextHygiene().filter(messages)

        self.assertEqual(2, result.stats.suppressed_count)
        self.assertIn(messages[5], result.messages)
        self.assertIn(recovery_user, result.messages)
        self.assertIn(recovery_assistant, result.messages)
        self.assertEqual(latest_user, result.messages[-1])

    def test_exact_repeated_exchange_suppresses_older_pair(self):
        messages = [
            message("user", "Boop", 1),
            message("assistant", self.repeated.format(subject="boop response"), 2),
            message("user", "Boop!", 3),
            message("assistant", self.repeated.format(subject="newer boop response"), 4),
        ]
        result = ContextHygiene().filter(messages)

        self.assertEqual((messages[2], messages[3]), result.messages)
        self.assertEqual(1, result.stats.exchange_pairs_suppressed_count)
        self.assertEqual(0, result.stats.assistant_only_suppressed_count)
        self.assertEqual(2, result.stats.suppressed_count)

    def test_near_duplicate_repeated_exchange_suppresses_older_pair(self):
        messages = [
            message("user", "Please repeat this synthetic playback check with the same safe settings now", 1),
            message("assistant", self.repeated.format(subject="playback check"), 2),
            message("user", "Please repeat this synthetic playback check with the same safe settings today", 3),
            message("assistant", self.repeated.format(subject="new playback check"), 4),
        ]
        result = ContextHygiene().filter(messages)

        self.assertEqual((messages[2], messages[3]), result.messages)
        self.assertEqual(1, result.stats.exchange_pairs_suppressed_count)

    def test_same_user_wording_with_different_answer_preserves_both_exchanges(self):
        messages = [
            message("user", "Boop", 1),
            message(
                "assistant",
                "I jump back with surprised ears, then laugh and tap your hand with one playful paw before asking for another.",
                2,
            ),
            message("user", "Boop", 3),
            message(
                "assistant",
                "This time I stay perfectly calm and explain that the blue sensor registered one harmless contact event.",
                4,
            ),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)

    def test_different_question_on_same_topic_preserves_both_exchanges(self):
        messages = [
            message("user", "Is the balcony garden healthy after yesterday's rain?", 1),
            message("assistant", "The balcony garden looks healthy, and the basil leaves are holding only a little extra water.", 2),
            message("user", "Why did the balcony garden need shade during yesterday's rain?", 3),
            message("assistant", "The shade kept the youngest basil leaves from being battered while their stems were still delicate.", 4),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)

    def test_user_correction_that_changes_meaning_is_preserved(self):
        messages = [
            message("user", "Please keep the blue indicator enabled during the next synthetic playback test", 1),
            message(
                "assistant",
                "I will leave the blue indicator glowing so the observer can verify every playback transition visually.",
                2,
            ),
            message("user", "Please keep the blue indicator disabled during the next synthetic playback test", 3),
            message(
                "assistant",
                "I will switch that light off and record the test result through the separate numeric diagnostics instead.",
                4,
            ),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)

    def test_one_natural_mirroring_exchange_is_preserved(self):
        messages = [
            message("user", "The northern sensor should remain exactly twelve centimeters above the marked rail.", 1),
            message("assistant", "The northern sensor should remain exactly twelve centimeters above the marked rail.", 2),
            message("user", "Why does that spacing matter?", 3),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(tuple(messages), result.messages)
        self.assertEqual(0, result.stats.exchange_pairs_suppressed_count)

    def test_three_redundant_exchanges_keep_only_newest_pair(self):
        messages = []
        for index, subject in enumerate(("first check", "second check", "newest check")):
            messages.extend([
                message("user", "Run the same harmless synthetic check", index * 2),
                message("assistant", self.repeated.format(subject=subject), index * 2 + 1),
            ])
        result = ContextHygiene().filter(messages)

        self.assertEqual((messages[-2], messages[-1]), result.messages)
        self.assertEqual(2, result.stats.exchange_pairs_suppressed_count)
        self.assertEqual(4, result.stats.suppressed_count)

    def test_latest_standalone_user_message_is_always_preserved(self):
        messages = [
            message("user", "Boop", 1),
            message("assistant", self.repeated.format(subject="first boop"), 2),
            message("user", "Boop", 3),
            message("assistant", self.repeated.format(subject="second boop"), 4),
            message("user", "Boop", 5),
        ]
        result = ContextHygiene().filter(messages)
        self.assertEqual(messages[-1], result.messages[-1])
        self.assertIn(messages[-1], result.messages)

    def test_conversation_build_is_provider_neutral_and_archive_bytes_do_not_change(self):
        with tempfile.TemporaryDirectory() as root:
            conversation_path = Path(root) / "conversation.json"
            summary_path = Path(root) / "summary.json"
            conversation = Conversation(_LLM(), conversation_file=conversation_path, summary_file=summary_path)
            for index, (subject, prompt) in enumerate(zip(
                ("evening", "night", "conversation"), self.distinct_loop_prompts,
            )):
                conversation.messages.extend([
                    message("user", prompt, index * 2),
                    message("assistant", self.repeated.format(subject=subject), index * 2 + 1),
                ])
            latest = message("user", "Please change to a new synthetic subject.", 7)
            conversation.messages.append(latest)
            conversation.save()
            archive_before = conversation_path.read_bytes()
            records_before = json.dumps(conversation.messages, ensure_ascii=False, sort_keys=True)

            first = conversation.build_context(_Memory(), latest["content"])
            first_metrics = dict(conversation._last_context_hygiene_metrics)
            conversation.llm = object()
            second = conversation.build_context(_Memory(), latest["content"])

            self.assertEqual(first, second)
            self.assertEqual(archive_before, conversation_path.read_bytes())
            self.assertEqual(records_before, json.dumps(conversation.messages, ensure_ascii=False, sort_keys=True))
            self.assertEqual(7, first_metrics["raw_recent_message_count"])
            self.assertEqual(5, first_metrics["admitted_recent_message_count"])
            self.assertEqual(2, first_metrics["context_hygiene_suppressed"])
            self.assertGreater(first_metrics["context_hygiene_removed_characters"], 0)
            self.assertEqual(latest, first[-1])


if __name__ == "__main__":
    unittest.main()
