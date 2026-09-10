import hashlib
import tempfile
import unittest
import uuid

from benchmarks.memory_v2.models import RetrievalHealth, RetrievalLaneHealth, RetrievalQuery
from memory_v2_episode_compaction import (
    EPISODE_PURPOSE_HISTORICAL,
    EPISODE_SOURCE_HISTORICAL,
    EpisodeCacheValidationResult,
    EpisodeRecordValidation,
    EpisodeRetrievalCandidate,
    EpisodeRetrievalResult,
    EpisodeSourceRefinement,
    canonical_record_id,
)
from memory_v2_hybrid_recall import HistoricalRecallAnchor, HybridMemoryV2Recall
from memory_v2_store import MemoryV2Store, SemanticRetrievalV2


class _ValidatedCallbackEpisodeCache:
    """Small cache-owner contract double; generic retrieval remains real."""

    def __init__(self, messages):
        self.messages = messages
        self.record = EpisodeRecordValidation(
            "episode-callback", "episode_compaction", "current_valid", "accepted",
            generation_id="generation-callback", truth_scope_kind="legacy_untagged",
            source_start_index=0, source_end_index_exclusive=len(messages),
            source_start_sequence=1, source_end_sequence=len(messages),
            source_count=len(messages), source_authority=EPISODE_SOURCE_HISTORICAL,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
            scope_state="unknown_scope",
        )

    def validate_for_context(self, *_args, **_kwargs):
        return EpisodeCacheValidationResult(
            True, "current_valid", "accepted", "generation-callback",
            raw_start_index=len(self.messages), records=(self.record,),
            lower_records=(self.record,), source_authority=EPISODE_SOURCE_HISTORICAL,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
        )

    def retrieve_candidates(self, _messages, query, **_kwargs):
        health = RetrievalHealth((RetrievalLaneHealth("episodes", "complete"),))
        if "game boy" not in str(query).casefold():
            return EpisodeRetrievalResult((), "current_valid", "no_relevant_episode", health=health)
        source = self.messages[3]
        return EpisodeRetrievalResult((EpisodeRetrievalCandidate(
            self.record.record_id,
            "The user owns an old Game Boy collection.",
            10, "generation-callback", "legacy_untagged", "",
            0, len(self.messages), 1, len(self.messages),
            source_authority=EPISODE_SOURCE_HISTORICAL,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
            scope_state="unknown_scope",
            source_refinements=(EpisodeSourceRefinement(
                canonical_record_id(3, source), 3, 4, "assistant",
                "canonical_conversation_assistant",
                str(source["content"]), "verified_anchor",
                "user_attribution_unsupported",
            ),),
        ),), "current_valid", generation_id="generation-callback",
           generated_candidate_count=1, health=health)


class ConversationalCallbackEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.character_id = str(uuid.uuid4())
        self.store = MemoryV2Store(f"{self.temporary.name}/memory-v2.sqlite3")
        self.store.create_character(self.character_id, "Callback test")
        self.scope_id = self.store.default_truth_scope_id(self.character_id)
        self.messages = [
            self._record(
                "user", "I spent the afternoon refactoring one of my Python projects.", 0,
            ),
            self._record(
                "assistant", "I told you the Python parser module was the tricky part.", 1,
            ),
            self._record("user", "game.", 2),
            self._record(
                "assistant",
                "I previously told you about our old Game Boy cartridge conversations.",
                3,
            ),
            self._record("user", "Do you think I should buy a telescope?", 4),
            self._record(
                "assistant", "I speculated that a silver telescope could be fun.", 5,
            ),
        ]
        for index, record in enumerate(self.messages):
            role = str(record["role"])
            speech_act = "question" if role == "user" and "?" in record["content"] else (
                "assertion" if role == "user" else "other"
            )
            kind = "question" if speech_act == "question" else (
                "statement" if speech_act == "assertion" else "interaction"
            )
            searchable = f"Historical {role} {kind}: {record['content']}"
            self.store.add_historical_evidence(
                self.character_id, f"claim-{index}", event_id=f"event-{index}",
                canonical_index=index,
                canonical_record_id=canonical_record_id(index, record),
                speaker_role=role, speech_act=speech_act,
                source_class="ordinary_conversation", scope_state="unknown_scope",
                truth_scope_id=None,
                source_content_sha256=hashlib.sha256(
                    str(record["content"]).encode("utf-8"),
                ).hexdigest(),
                searchable_text=searchable, recorded_at_us=index + 1,
                source_reference=f"conversation.json#{index}",
                retrieval_eligible=role == "user",
            )
        self.store.rebuild_fts()
        self.hybrid = HybridMemoryV2Recall(
            self.store, self.character_id,
            semantic_retriever=SemanticRetrievalV2(
                self.store, include_historical_evidence=True,
            ),
            episode_cache=_ValidatedCallbackEpisodeCache(self.messages),
            canonical_messages=self.messages, include_historical_episodes=True,
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    @staticmethod
    def _record(role, content, offset):
        return {
            "role": role, "content": content,
            "timestamp": f"2026-01-01T00:00:{offset:02d}+00:00",
        }

    def _query(self, text, *, anchor=None):
        return self.hybrid.retrieve(RetrievalQuery(
            self.character_id, text, "2026-02-01T00:00:00+00:00",
        ), recall_anchor=anchor)

    def test_assistant_user_and_mixed_callbacks_preserve_exact_ownership(self):
        assistant = self._query("What did you tell me about Game Boy?")
        self.assertTrue(assistant.candidates)
        self.assertEqual("assistant", assistant.candidates[0].speaker_role)
        self.assertEqual("historical_evidence", assistant.candidates[0].lane)

        user = self._query("Do you remember my Python project from before?")
        self.assertTrue(user.candidates)
        self.assertEqual("user", user.candidates[0].speaker_role)
        self.assertEqual("assertion", user.candidates[0].speech_act)

        mixed = self._query("Do you remember what we talked about with Game Boy?")
        self.assertTrue(mixed.candidates)
        self.assertNotIn("historical_episode", {value.lane for value in mixed.candidates})
        episode_source = next(
            value for value in mixed.candidates
            if value.lane == "historical_episode_source"
        )
        self.assertEqual("assistant", episode_source.speaker_role)
        self.assertEqual("user_attribution_unsupported", episode_source.attribution_state)

    def test_callback_intent_precedes_broad_assistant_opinion_grammar(self):
        user_history = self._query(
            "What do you remember me saying about one of my Python projects?",
        )
        self.assertTrue(user_history.candidates)
        self.assertEqual("user", user_history.candidates[0].speaker_role)

        assistant_history = self._query("What did you tell me before about Game Boy?")
        self.assertTrue(assistant_history.candidates)
        self.assertEqual("assistant", assistant_history.candidates[0].speaker_role)

        shared_history = self._query("What did we talk about before regarding Game Boy?")
        self.assertTrue(shared_history.candidates)
        self.assertIn(
            shared_history.candidates[0].speaker_role, {"user", "assistant"},
        )

        opinion = self._query("What do you think about my Python project?")
        self.assertFalse(opinion.candidates)
        self.assertEqual("intent_assistant_opinion", opinion.abstention_reason)

    def test_false_user_attribution_does_not_use_assistant_speculation(self):
        result = self._query("Did I say I owned the silver telescope?")
        self.assertFalse(result.candidates)

    def test_grounded_followup_uses_one_exact_interaction_hop(self):
        first = self._query("Do you remember my Python project from before?")
        source = next(value for value in first.candidates if value.canonical_index == 0)
        anchor = HistoricalRecallAnchor(
            self.character_id, self.scope_id, 1,
            (source.canonical_record_id,), (source.canonical_index,), (),
        )
        followup = self._query("What else was connected to that?", anchor=anchor)
        self.assertEqual(1, len(followup.candidates))
        self.assertEqual("historical_recall_anchor_source", followup.candidates[0].lane)
        self.assertEqual("assistant", followup.candidates[0].speaker_role)
        self.assertEqual(1, followup.candidates[0].canonical_index)

    def test_concrete_followup_recovers_only_the_anchored_source_value(self):
        first = self._query("Do you remember my Python project from before?")
        source = next(value for value in first.candidates if value.canonical_index == 0)
        anchor = HistoricalRecallAnchor(
            self.character_id, self.scope_id, 1,
            (source.canonical_record_id,), (source.canonical_index,), (), ("user",),
        )

        language = self._query(
            "Which programming language was connected to it?", anchor=anchor,
        )
        self.assertEqual(1, len(language.candidates))
        self.assertEqual(
            "historical_recall_anchor_source", language.candidates[0].lane,
        )
        self.assertEqual("user", language.candidates[0].speaker_role)
        self.assertEqual(0, language.candidates[0].canonical_index)
        self.assertIn("Python", language.candidates[0].content)

        self.assertFalse(self._query(
            "Which programming language was connected to it?",
        ).candidates)

    def test_no_anchor_foreign_character_and_scope_mismatch_abstain(self):
        query = "What else was connected to that?"
        self.assertFalse(self._query(query).candidates)
        source_id = canonical_record_id(0, self.messages[0])
        for anchor in (
            HistoricalRecallAnchor(str(uuid.uuid4()), self.scope_id, 1, (source_id,), (0,), ()),
            HistoricalRecallAnchor(self.character_id, "different-scope", 1, (source_id,), (0,), ()),
        ):
            self.assertFalse(self._query(query, anchor=anchor).candidates)


if __name__ == "__main__":
    unittest.main()
