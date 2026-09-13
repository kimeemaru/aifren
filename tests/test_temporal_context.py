from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from aifren.conversation.conversation import ContextManager, Conversation
from aifren.conversation.temporal_context import (
    build_temporal_context_block,
    derive_temporal_context_facts,
)


class _Memory:
    def get_relevant_memories(self, _user_message, max_memories):
        return []


def _message(role, content, timestamp=None):
    result = {"role": role, "content": content}
    if timestamp is not None:
        result["timestamp"] = timestamp
    return result


class TemporalContextTests(unittest.TestCase):
    def setUp(self):
        self.toronto = ZoneInfo("America/Toronto")

    def facts(self, previous_timestamp, current, *, current_content="I'm back"):
        messages = [
            _message("user", "Earlier user turn", previous_timestamp),
            _message("assistant", "Earlier assistant reply", previous_timestamp),
            _message("user", current_content, current.isoformat()),
        ]
        return derive_temporal_context_facts(
            messages, current_content, clock=lambda: current,
        )

    def adjacent_return_facts(
        self,
        *,
        earlier_timestamp="2026-08-26T18:40:00-04:00",
        return_timestamp="2026-08-27T08:00:00-04:00",
        current=None,
        return_content="I'm back",
        current_content="How long have I been gone?",
    ):
        current = current or datetime(2026, 8, 27, 8, 0, 17, tzinfo=self.toronto)
        messages = [
            _message("user", "Earlier user turn", earlier_timestamp),
            _message("assistant", "See you later.", earlier_timestamp),
            _message("user", return_content, return_timestamp),
            _message("assistant", "Welcome back.", return_timestamp),
            _message("user", current_content, current.isoformat()),
        ]
        return derive_temporal_context_facts(
            messages, current_content, clock=lambda: current,
        )

    def test_short_gap_is_derived_from_previous_user_interaction(self):
        current = datetime(2026, 8, 27, 12, 0, tzinfo=self.toronto)
        facts = self.facts("2026-08-27T11:53:00-04:00", current)

        self.assertEqual(timedelta(minutes=7), facts.elapsed_since_previous_user_interaction)
        self.assertIn(
            "Elapsed since previous canonical user interaction: 7 minutes.",
            build_temporal_context_block(facts),
        )

    def test_multi_hour_gap_stays_factual(self):
        current = datetime(2026, 8, 27, 8, 0, tzinfo=self.toronto)
        facts = self.facts("2026-08-26T22:00:00-04:00", current)
        block = build_temporal_context_block(facts)

        self.assertEqual(timedelta(hours=10), facts.elapsed_since_previous_user_interaction)
        self.assertIn("10 hours", block)
        self.assertIn("does not establish sleep, work, travel", block)
        self.assertNotIn("slept for", block.casefold())

    def test_explicit_adjacent_absence_followup_exposes_the_return_gap(self):
        facts = self.adjacent_return_facts()
        block = build_temporal_context_block(facts)

        self.assertEqual(timedelta(seconds=17), facts.elapsed_since_previous_user_interaction)
        self.assertEqual(
            timedelta(hours=13, minutes=20),
            facts.elapsed_gap_ending_at_previous_user_interaction,
        )
        self.assertIn(
            "Elapsed since previous canonical user interaction: less than 1 minute.",
            block,
        )
        self.assertIn(
            "Return/absence interaction gap ending at the previous canonical user "
            "interaction: 13 hours 20 minutes.",
            block,
        )
        self.assertIn("These elapsed interaction gaps do not establish sleep", block)

    def test_supported_followups_are_explicit_first_person_absence_questions(self):
        for question in (
            "How long was I away?",
            "How long has it been since I was here?",
            "Could you tell me about how long I have been away?",
        ):
            with self.subTest(question=question):
                facts = self.adjacent_return_facts(current_content=question)
                self.assertEqual(
                    timedelta(hours=13, minutes=20),
                    facts.elapsed_gap_ending_at_previous_user_interaction,
                )

    def test_unrelated_or_ambiguous_adjacent_turn_does_not_expose_return_gap(self):
        for message in (
            "Tell me about your favorite food.",
            "What time is it?",
            "How long has it been since we talked about otters?",
            "How long did the trip take?",
        ):
            with self.subTest(message=message):
                facts = self.adjacent_return_facts(current_content=message)
                self.assertIsNone(facts.elapsed_gap_ending_at_previous_user_interaction)
                self.assertNotIn(
                    "Return/absence interaction gap",
                    build_temporal_context_block(facts),
                )

        explicit_question_without_return = self.adjacent_return_facts(
            return_content="We were discussing dinner.",
        )
        return_word_in_an_unrelated_statement = self.adjacent_return_facts(
            return_content="I'm back in the office archives from 2012.",
        )
        self.assertIsNone(
            explicit_question_without_return.elapsed_gap_ending_at_previous_user_interaction,
        )
        self.assertIsNone(
            return_word_in_an_unrelated_statement.elapsed_gap_ending_at_previous_user_interaction,
        )

    def test_return_gap_disappears_after_one_adjacent_user_turn(self):
        current = datetime(2026, 8, 27, 8, 2, tzinfo=self.toronto)
        messages = [
            _message("user", "Earlier", "2026-08-26T18:40:00-04:00"),
            _message("assistant", "See you.", "2026-08-26T18:41:00-04:00"),
            _message("user", "I'm back", "2026-08-27T08:00:00-04:00"),
            _message("assistant", "Welcome back.", "2026-08-27T08:00:05-04:00"),
            _message("user", "Tell me a joke.", "2026-08-27T08:01:00-04:00"),
            _message("assistant", "Why did the fox cross the road?", "2026-08-27T08:01:05-04:00"),
            _message("user", "How long have I been gone?", current.isoformat()),
        ]

        facts = derive_temporal_context_facts(
            messages, "How long have I been gone?", clock=lambda: current,
        )

        self.assertEqual(timedelta(minutes=1), facts.elapsed_since_previous_user_interaction)
        self.assertIsNone(facts.elapsed_gap_ending_at_previous_user_interaction)

    def test_overnight_date_boundary_uses_elapsed_instant(self):
        current = datetime(2026, 8, 28, 7, 15, tzinfo=self.toronto)
        facts = self.facts("2026-08-27T23:45:00-04:00", current)

        self.assertEqual(timedelta(hours=7, minutes=30), facts.elapsed_since_previous_user_interaction)
        block = build_temporal_context_block(facts)
        self.assertIn("2026-08-28T07:15:00-04:00 (Friday; America/Toronto)", block)
        self.assertIn("7 hours 30 minutes", block)

    def test_restart_uses_persisted_canonical_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            conversation_path = Path(root) / "conversation.json"
            summary_path = Path(root) / "summary.json"
            original_time = datetime(2026, 8, 26, 22, 0, tzinfo=self.toronto)
            original = Conversation(
                object(), conversation_file=conversation_path, summary_file=summary_path,
                clock=lambda: original_time,
            )
            original.add_user_message("I'm going to bed")
            original.add_assistant_message("Good night.")
            original.save()

            return_time = datetime(2026, 8, 27, 8, 0, tzinfo=self.toronto)
            restored = Conversation(
                object(), conversation_file=conversation_path, summary_file=summary_path,
                clock=lambda: return_time,
            )
            restored.add_user_message("I'm back")
            context = restored.build_context(_Memory(), "I'm back")
            temporal = next(
                item["content"] for item in context
                if item["content"].startswith("[Current turn temporal facts]")
            )

            self.assertIn("Elapsed since previous canonical user interaction: 10 hours.", temporal)
            self.assertEqual("2026-08-27T08:00:00-04:00", restored.messages[-1]["timestamp"])

    def test_adjacent_return_gap_reconstructs_from_canonical_history_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            conversation_path = Path(root) / "conversation.json"
            summary_path = Path(root) / "summary.json"
            original = Conversation(
                object(), conversation_file=conversation_path, summary_file=summary_path,
            )
            original.messages = [
                _message("user", "Earlier", "2026-08-26T18:40:00-04:00"),
                _message("assistant", "See you.", "2026-08-26T18:41:00-04:00"),
                _message("user", "I'm back", "2026-08-27T08:00:00-04:00"),
                _message("assistant", "Welcome back.", "2026-08-27T08:00:05-04:00"),
            ]
            original.save()

            current = datetime(2026, 8, 27, 8, 0, 17, tzinfo=self.toronto)
            restored = Conversation(
                object(), conversation_file=conversation_path, summary_file=summary_path,
                clock=lambda: current,
            )
            restored.add_user_message("How long have I been gone?")
            context = restored.build_context(_Memory(), "How long have I been gone?")
            temporal = next(
                item["content"] for item in context
                if item["content"].startswith("[Current turn temporal facts]")
            )

            self.assertIn("previous canonical user interaction: less than 1 minute", temporal)
            self.assertIn("interaction: 13 hours 20 minutes", temporal)

    def test_legacy_naive_timestamp_is_supported_but_missing_timestamp_fails_open(self):
        current = datetime(2026, 8, 27, 12, 0, tzinfo=self.toronto)
        legacy = self.facts("2026-08-27T11:30:00", current)
        missing = self.facts(None, current)

        self.assertEqual(timedelta(minutes=30), legacy.elapsed_since_previous_user_interaction)
        self.assertIsNone(missing.previous_user_interaction_at)
        missing_block = build_temporal_context_block(missing)
        self.assertIn("Current local datetime:", missing_block)
        self.assertNotIn("Elapsed since", missing_block)

    def test_adjacent_return_gap_fails_open_for_missing_invalid_or_negative_timestamps(self):
        missing_earlier = self.adjacent_return_facts(earlier_timestamp=None)
        invalid_earlier = self.adjacent_return_facts(earlier_timestamp="not-a-timestamp")
        negative_earlier = self.adjacent_return_facts(
            earlier_timestamp="2026-08-27T09:00:00-04:00",
        )
        missing_return = self.adjacent_return_facts(return_timestamp=None)
        clock_before_return = self.adjacent_return_facts(
            current=datetime(2026, 8, 27, 7, 59, 59, tzinfo=self.toronto),
        )

        for facts in (
            missing_earlier, invalid_earlier, negative_earlier, missing_return,
            clock_before_return,
        ):
            self.assertIsNone(facts.elapsed_gap_ending_at_previous_user_interaction)

    def test_dst_offset_transition_uses_wall_clock_instants(self):
        new_york = ZoneInfo("America/New_York")
        current = datetime(2026, 3, 8, 3, 30, tzinfo=new_york)
        facts = self.facts("2026-03-08T01:30:00-05:00", current)

        self.assertEqual(timedelta(hours=1), facts.elapsed_since_previous_user_interaction)

    def test_adjacent_return_gap_uses_utc_instants_across_dst(self):
        new_york = ZoneInfo("America/New_York")
        current = datetime(2026, 3, 8, 3, 30, 17, tzinfo=new_york)
        facts = self.adjacent_return_facts(
            earlier_timestamp="2026-03-08T01:30:00-05:00",
            return_timestamp="2026-03-08T03:30:00-04:00",
            current=current,
        )
        ambiguous_legacy = self.adjacent_return_facts(
            earlier_timestamp="2026-11-01T01:30:00",
            return_timestamp="2026-11-01T02:30:00-05:00",
            current=datetime(2026, 11, 1, 2, 30, 17, tzinfo=new_york),
        )

        self.assertEqual(
            timedelta(hours=1), facts.elapsed_gap_ending_at_previous_user_interaction,
        )
        self.assertIsNone(ambiguous_legacy.elapsed_gap_ending_at_previous_user_interaction)

    def test_ambiguous_legacy_dst_timestamp_and_clock_anomaly_abstain(self):
        new_york = ZoneInfo("America/New_York")
        current = datetime(2026, 11, 1, 2, 30, tzinfo=new_york)
        ambiguous = self.facts("2026-11-01T01:30:00", current)
        future = self.facts("2026-11-01T03:30:00-05:00", current)

        self.assertIsNone(ambiguous.elapsed_since_previous_user_interaction)
        self.assertIsNone(future.elapsed_since_previous_user_interaction)
        self.assertNotIn("Elapsed since", build_temporal_context_block(ambiguous))
        self.assertNotIn("Elapsed since", build_temporal_context_block(future))

    def test_local_and_online_provider_context_is_identical(self):
        with tempfile.TemporaryDirectory() as root:
            current = datetime(2026, 8, 27, 8, 0, tzinfo=self.toronto)
            conversation = Conversation(
                object(),
                conversation_file=Path(root) / "conversation.json",
                summary_file=Path(root) / "summary.json",
                clock=lambda: current,
            )
            conversation.messages = [
                _message("user", "Earlier", "2026-08-26T18:40:00-04:00"),
                _message("assistant", "See you later", "2026-08-26T18:41:00-04:00"),
                _message("user", "I'm back", "2026-08-27T07:59:43-04:00"),
                _message("assistant", "Welcome back", "2026-08-27T07:59:50-04:00"),
                _message("user", "How long have I been gone?", current.isoformat()),
            ]
            local = conversation.build_context(_Memory(), "How long have I been gone?")
            conversation.llm = object()
            online = conversation.build_context(_Memory(), "How long have I been gone?")

            self.assertEqual(local, online)
            self.assertEqual(
                1,
                "\n".join(item["content"] for item in local).count("[Current turn temporal facts]"),
            )
            self.assertIn(
                "Return/absence interaction gap ending at the previous canonical user interaction",
                "\n".join(item["content"] for item in local),
            )

    def test_episode_context_and_current_time_have_distinct_nonduplicated_roles(self):
        current = datetime(2026, 8, 27, 8, 0, 17, tzinfo=self.toronto)
        facts = self.adjacent_return_facts(current=current)
        temporal = build_temporal_context_block(facts)
        episode = (
            "[Derived episodic conversation background]\n"
            "Historical recall scope: yesterday.\n"
            "[End derived episodic conversation background]"
        )
        current_message = _message("user", "How long have I been gone?", current.isoformat())

        context = ContextManager().build_context(
            "", [], [current_message], admitted_episode_context=episode,
            temporal_context=temporal,
        )
        contents = [item["content"] for item in context]

        self.assertEqual(1, "\n".join(contents).count("[Current turn temporal facts]"))
        self.assertEqual(1, "\n".join(contents).count("[Derived episodic conversation background]"))
        self.assertEqual(1, "\n".join(contents).count("Return/absence interaction gap"))
        self.assertLess(contents.index(temporal), contents.index(episode))
        self.assertEqual(current_message, context[-1])


if __name__ == "__main__":
    unittest.main()
