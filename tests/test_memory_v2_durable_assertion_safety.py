from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from durable_fact_curation import extract_durable_fact_proposal
from memory_v2_shadow_writer import MemoryV2ShadowWriter
from memory_v2_store import MemoryV2Repository


REVIEWER_UNSAFE_VALUE_ASSERTIONS = (
    "My GPU is supposedly an RTX 4090.",
    "My GPU is allegedly an RTX 4090.",
    "My GPU is reportedly an RTX 4090.",
    "My GPU is apparently an RTX 4090.",
    "My GPU is rumored to be an RTX 4090.",
    "My GPU is said to be an RTX 4090.",
    "My GPU is expected to be an RTX 4090.",
    "My computer is scheduled to be a Framework laptop.",
    "My project is roleplay.",
    "My project is in D&D.",
    "My computer is starting in 2027 a Framework laptop.",
    "My computer is beginning in 2027 a Framework laptop.",
    "My computer is effective from 2027 a Framework laptop.",
    "My computer is from 2027 a Framework laptop.",
)

REVIEWER_FIGURATIVE_OR_MISBOUND_ASSERTIONS = (
    "I own an RTX poster.",
    "I have a Radeon sticker.",
    "I use an RTX voice effect.",
    "I moved to using Linux full-time.",
    "I moved to tears.",
    "I moved to the Toronto office.",
    "I moved to Toronto for work.",
    "I moved to Paris on the chessboard.",
    "I moved to Mars in a game.",
    "I moved to Windows 11.",
    "I live in denial.",
    "I work as needed.",
    "I work as if nothing happened.",
    "I work as soon as I can.",
    "I work as part of the team.",
    "I work as much as possible.",
    "I work as a wizard in a game.",
    "I work as a joke.",
    "My job is to clean the garage.",
    "My job is helping my friend.",
    "I study at night.",
    "I study at Toronto time.",
    "I study at NASA archives.",
    "I study at Oxford for a day.",
    "My school is University Challenge.",
    "My computer is a PC game.",
    "My laptop is a ThinkPad sticker.",
    "My computer is a MacBook Sticker.",
    "My computer is a ThinkPad Poster.",
    "My computer is a laptop-shaped cake.",
    "My pet is named in the document.",
    "I have a dog named after my grandfather.",
    "I hate to say this.",
    "I love you.",
    "I love that for you.",
    "I'm interested in what you think.",
    "My favorite movie is the one you suggested.",
    "My favorite game is the game you named.",
    "My favorite food is whatever you're having.",
    "I like the thing you suggested.",
    "I like whatever you like.",
    "I'm interested in where you live.",
    "I enjoy one of those.",
    "My favorite game is Chess if Alice asks.",
    "My favorite food is pizza in Minecraft.",
    "My primary project is Aurora if Alice agrees.",
    "I am interested in astronomy while roleplaying.",
    "My favorite game is Chess starting Monday.",
    "My favorite game is Chess from Monday.",
    "My favorite game is Chess next Monday.",
    "My favorite game is Chess effective Monday.",
    "My favorite game is Chess unless Alice asks.",
    "My favorite food is pizza during roleplay.",
    "I like it.",
    "I love him.",
    "I'm interested in it.",
)


class _SyntheticCanonicalHarness:
    """Exercise the same persisted-user observer boundary as production."""

    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.character_id = str(uuid.uuid4())
        self.rows: list[dict[str, object]] = []
        self.base = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        self.writer = MemoryV2ShadowWriter(
            self.root,
            character_id=self.character_id,
            display_name="Synthetic",
            memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(self.character_id, "Synthetic")

    def close(self) -> None:
        self.writer.close()
        self._temp.cleanup()

    def _persist(
        self,
        role: str,
        content: str,
        *,
        fields: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], int]:
        index = len(self.rows)
        scope = self.repository.active_truth_scope(self.character_id)
        message: dict[str, object] = {
            "role": role,
            "content": content,
            "timestamp": (self.base + timedelta(minutes=index)).isoformat(),
            "truth_scope": {
                "kind": scope.kind,
                "scope_id": scope.truth_scope_id,
            },
        }
        message.update(fields or {})
        self.rows.append(message)
        self.conversation_file.write_text(json.dumps(self.rows), encoding="utf-8")
        return message, index

    def user_turn(
        self,
        content: str,
        *,
        fields: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        message, index = self._persist("user", content, fields=fields)
        continuity = self.writer.observe_canonical_user_continuity(
            message,
            conversation_index=index,
            conversation_file=self.conversation_file,
        )
        # Durable observation occurs at the production completed-turn
        # boundary.  Scenario transitions may change the assistant's scope;
        # ordinary durable assertions keep the same exact pair scope.
        self._persist("assistant", "Synthetic completed response.")
        durable = self.writer.observe_canonical_user_durable_facts(
            message,
            conversation_index=index,
            conversation_file=self.conversation_file,
        )
        return continuity, durable

    def assistant_observation(self, content: str) -> dict[str, object]:
        message, index = self._persist("assistant", content)
        return self.writer.observe_canonical_user_durable_facts(
            message,
            conversation_index=index,
            conversation_file=self.conversation_file,
        )


class DurableAssertionCurationSafetyTests(unittest.TestCase):
    def assert_not_curated(self, *texts: str) -> None:
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(extract_durable_fact_proposal(text))

    def test_recommendation_and_possession_questions_are_not_assertions(self):
        self.assert_not_curated(
            "Do you think I should buy a motorcycle?",
            "Which telescope would you recommend?",
            "Do I own a motorcycle?",
            "Which telescope do I own?",
        )

    def test_specific_computer_models_remain_admissible(self):
        for text in (
            "My computer is a Framework laptop.",
            "My computer is a MacBook.",
            "My computer is a MacBook Pro M3 Max.",
            "My computer is a ThinkPad X1 Carbon.",
            "My computer is a ThinkPad T14.",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(extract_durable_fact_proposal(text))

    def test_assertion_shaped_trailing_questions_are_not_assertions(self):
        # Punctuation is evidence of speech act and must be checked before any
        # terminal punctuation is removed for value normalization.
        self.assert_not_curated(
            "I own a motorcycle?",
            "I live in Toronto?",
            "My computer is a Framework laptop?",
            "I like coffee?",
            "I own a motorcycle?!",
        )

    def test_embedded_self_assertion_is_narrow_and_preserves_modality(self):
        for text in (
            "Did you know that I like watermelon?",
            "Did you know I like watermelon?",
        ):
            with self.subTest(text=text):
                proposal = extract_durable_fact_proposal(text)
                self.assertIsNotNone(proposal)
                self.assertEqual(("likes", "watermelon"), (
                    proposal.fact_kind, proposal.value,
                ))
                self.assertEqual(
                    "watermelon", text[proposal.excerpt_start_cp:proposal.excerpt_end_cp],
                )

        self.assert_not_curated(
            "Did I ever tell you that I like watermelon?",
            "Do you think I like watermelon?",
            "Did you know that I might like watermelon?",
            "Did you know that maybe I like watermelon?",
        )

        spaced = "Did you know that I   like   watermelon?"
        proposal = extract_durable_fact_proposal(spaced)
        self.assertIsNotNone(proposal)
        self.assertEqual(
            "watermelon", spaced[proposal.excerpt_start_cp:proposal.excerpt_end_cp],
        )

    def test_prefix_and_suffix_hypotheticals_are_not_assertions(self):
        self.assert_not_curated(
            "What if I own a motorcycle?",
            "Suppose I own a motorcycle.",
            "Imagine I own a motorcycle.",
            "I own a motorcycle if I win the lottery.",
            "I own a motorcycle hypothetically.",
            "I own a motorcycle for example.",
            "I own a motorcycle in a hypothetical story.",
        )

    def test_uncertainty_is_not_promoted_to_current_fact(self):
        self.assert_not_curated(
            "Maybe I own a motorcycle.",
            "I might own a motorcycle.",
            "I may own a motorcycle.",
            "I own a motorcycle maybe.",
            "I own a motorcycle probably.",
            "I own a motorcycle I think.",
        )

    def test_negated_possession_is_not_encoded_as_owned_possession(self):
        self.assert_not_curated(
            "I don't own a motorcycle.",
            "I do not own a motorcycle.",
            "I no longer own a motorcycle.",
            "I own no motorcycle.",
            "I own nothing.",
            "Actually, I don't own a motorcycle.",
            "Actually, I own no motorcycle.",
        )

    def test_negated_non_possession_values_are_not_positive_facts(self):
        self.assert_not_curated(
            "My GPU is not an RTX 4090.",
            "My job is not a doctor.",
        )

    def test_exact_preference_endings_are_retirements_not_negative_facts(self):
        pairs = (
            ("I used to like coffee.", "The user likes coffee."),
            ("I don't like coffee anymore.", "The user likes coffee."),
            ("My favorite game is not Chess.", "The user's favorite game is Chess."),
            ("Chess is no longer my favorite game.", "The user's favorite game is Chess."),
        )
        for text, expected in pairs:
            with self.subTest(text=text):
                proposal = extract_durable_fact_proposal(text)
                self.assertIsNotNone(proposal)
                self.assertEqual(("retirement", expected), (
                    proposal.stance, proposal.content,
                ))

    def test_future_intention_is_not_completed_or_current_fact(self):
        self.assert_not_curated(
            "I plan to buy a motorcycle.",
            "I intend to buy a motorcycle.",
            "I will buy a motorcycle.",
            "I'm going to buy a motorcycle.",
            "I want to buy a motorcycle.",
            "I own a motorcycle next year.",
            "My computer is going to be a Framework laptop.",
        )

    def test_bare_hypothetical_value_is_not_a_project_fact(self):
        self.assert_not_curated("My project is hypothetical.")

    def test_epistemic_roleplay_and_future_values_are_not_current_facts(self):
        self.assert_not_curated(*REVIEWER_UNSAFE_VALUE_ASSERTIONS)

    def test_figurative_and_semantically_misbound_values_are_not_facts(self):
        self.assert_not_curated(*REVIEWER_FIGURATIVE_OR_MISBOUND_ASSERTIONS)

    def test_pet_multiplicity_is_not_collapsed_into_one_primary_slot(self):
        self.assert_not_curated(
            "I have a dog named Fido.",
            "I have a cat named Luna.",
            "My dog is named Miso.",
        )

    def test_quoted_and_roleplay_qualified_possession_is_not_real_world_fact(self):
        self.assert_not_curated(
            'She said, "I own a motorcycle."',
            'In our roleplay, I own a plasma sword.',
            'I own a plasma sword in our roleplay.',
            'I own a motorcycle in GTA.',
        )

    def test_one_explicit_current_assertion_remains_supported(self):
        proposal = extract_durable_fact_proposal("I own a blue motorcycle.")
        self.assertIsNotNone(proposal)
        self.assertEqual("possession", proposal.fact_kind)
        self.assertEqual("The user owns a blue motorcycle.", proposal.content)

    def test_explicit_dislike_remains_supported(self):
        proposal = extract_durable_fact_proposal("I don't like olives.")
        self.assertIsNotNone(proposal)
        self.assertEqual("dislikes", proposal.fact_kind)
        self.assertEqual("The user dislikes olives.", proposal.content)

    def test_purchase_and_sale_are_excluded_until_retirement_identity_is_governed(self):
        # Historical replay must not infer ownership from an acquisition event
        # while it cannot resolve the corresponding sale to exactly one owned
        # subject and append a governed retirement. Direct current `I own ...`
        # remains a separately supported live assertion above.
        self.assert_not_curated(
            "I bought a blue motorcycle yesterday.",
            "I purchased a blue motorcycle yesterday.",
            "I sold the blue motorcycle.",
            "I traded away the blue motorcycle.",
        )

    def test_contextual_gpu_replacement_requires_prior_explicit_gpu_assertion(self):
        unsafe_contexts = (
            "Which GPU would you recommend?",
            "Do you think I should buy an RTX GPU?",
            "I saw an RTX 3060 in a shop.",
        )
        for previous in unsafe_contexts:
            with self.subTest(previous=previous):
                self.assertIsNone(extract_durable_fact_proposal(
                    "I replaced it with an RTX 4070.",
                    previous_user_content=previous,
                ))
        proposal = extract_durable_fact_proposal(
            "I replaced it with an RTX 4070.",
            previous_user_content="My GPU is an RTX 3060.",
        )
        self.assertIsNotNone(proposal)
        self.assertEqual(("device.gpu", "an RTX 4070"), (
            proposal.subject_key, proposal.value,
        ))


class DurableAssertionObserverSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _SyntheticCanonicalHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_assistant_suggestion_cannot_create_user_durable_fact(self):
        result = self.harness.assistant_observation("You should buy a telescope.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("not_user_message", result["reason"])
        self.assertEqual(0, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=?",
            (self.harness.character_id,),
        ).fetchone()[0])

    def test_scenario_scoped_assertion_cannot_create_real_world_durable_fact(self):
        continuity, _ = self.harness.user_turn(
            "Let's roleplay that we're in a synthetic moon-base scene."
        )
        self.assertEqual("applied", continuity["state"])
        _, result = self.harness.user_turn("I own a plasma sword.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("truth_scope_not_real_world", result["reason"])
        self.assertEqual(0, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
            (self.harness.character_id,),
        ).fetchone()[0])

    def test_rejected_question_creates_neither_claim_nor_evidence_event(self):
        _, result = self.harness.user_turn("I own a motorcycle?!")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("no_clear_durable_fact", result["reason"])
        self.assertEqual(0, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
            (self.harness.character_id,),
        ).fetchone()[0])
        self.assertEqual(0, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE character_id=? AND source_origin='canonical_conversation'",
            (self.harness.character_id,),
        ).fetchone()[0])

    def test_non_assertive_and_negated_forms_create_no_observer_records(self):
        unsafe = (
            "Actually, I own no motorcycle.",
            "My GPU is not an RTX 4090.",
            "My job is not a doctor.",
            "My computer is going to be a Framework laptop.",
            "My project is hypothetical.",
            *REVIEWER_UNSAFE_VALUE_ASSERTIONS,
            *REVIEWER_FIGURATIVE_OR_MISBOUND_ASSERTIONS,
        )
        for text in unsafe:
            with self.subTest(text=text):
                harness = _SyntheticCanonicalHarness()
                try:
                    _, result = harness.user_turn(text)
                    self.assertEqual("ignored", result["state"])
                    self.assertEqual("no_clear_durable_fact", result["reason"])
                    self.assertEqual(0, harness.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
                        (harness.character_id,),
                    ).fetchone()[0])
                    self.assertEqual(0, harness.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM events WHERE character_id=? AND source_origin='canonical_conversation'",
                        (harness.character_id,),
                    ).fetchone()[0])
                finally:
                    harness.close()

    def test_ambiguous_color_shorthand_requires_adjacent_color_context(self):
        _, first = self.harness.user_turn("My favorite color is green.")
        self.assertEqual("created", first["state"])
        self.harness.user_turn("The synthetic sky is clear.")
        _, result = self.harness.user_turn("My favorite is purple actually.")
        self.assertEqual(("ignored", "no_clear_durable_fact"), (
            result["state"], result["reason"],
        ))
        current = self.harness.repository.lookup_durable_core(
            self.harness.character_id, "preference.color",
        ).candidates
        self.assertEqual("The user's favorite color is green.", current[0].content)

    def test_malformed_semantic_admission_never_becomes_observer_evidence(self):
        malformed = (
            {},
            {"understood": 0},
            {"understood": "false"},
            {
                "channel": "hearing",
                "state": "unavailable",
                "understood": False,
                "extra": "not-in-schema",
            },
            {
                "channel": "hearing",
                "state": "available",
                "understood": True,
            },
        )
        for semantic_admission in malformed:
            with self.subTest(semantic_admission=semantic_admission):
                harness = _SyntheticCanonicalHarness()
                try:
                    _, result = harness.user_turn(
                        "I live in Malformed City.",
                        fields={"semantic_admission": semantic_admission},
                    )
                    self.assertEqual("ignored", result["state"])
                    self.assertEqual("generated_or_unavailable_source", result["reason"])
                    self.assertEqual(0, harness.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM claims WHERE character_id=? AND claim_type='durable_core_fact'",
                        (harness.character_id,),
                    ).fetchone()[0])
                    self.assertEqual(0, harness.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM events WHERE character_id=? AND source_origin='canonical_conversation'",
                        (harness.character_id,),
                    ).fetchone()[0])
                finally:
                    harness.close()

    def test_explicit_dislike_remains_supported_by_observer(self):
        _, result = self.harness.user_turn("I don't like olives.")
        self.assertEqual("created", result["state"])
        row = self.harness.writer.store.connection.execute(
            """SELECT content FROM claims WHERE character_id=?
                 AND claim_type='durable_core_fact'""",
            (self.harness.character_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("The user dislikes olives.", row[0])

    def test_embedded_self_assertion_reaches_governed_v2_observation(self):
        _, result = self.harness.user_turn(
            "Did you know that I like watermelon?",
        )
        self.assertEqual("created", result["state"])
        row = self.harness.writer.store.connection.execute(
            "SELECT content FROM claims WHERE character_id=? "
            "AND claim_type='durable_core_fact'",
            (self.harness.character_id,),
        ).fetchone()
        self.assertEqual("The user likes watermelon.", row[0])

        separate = _SyntheticCanonicalHarness()
        try:
            _, uncertain = separate.user_turn(
                "Did you know that I might like watermelon?",
            )
            self.assertEqual(("ignored", "no_clear_durable_fact"), (
                uncertain["state"], uncertain["reason"],
            ))
        finally:
            separate.close()

    def test_preference_retirement_closes_exact_value_without_negative_claim(self):
        _, created = self.harness.user_turn("I like coffee.")
        _, retired = self.harness.user_turn("I used to like coffee.")

        self.assertEqual(("created", "retired"), (
            created["state"], retired["state"],
        ))
        rows = self.harness.writer.store.connection.execute(
            "SELECT content, valid_to_us FROM claims WHERE character_id=? "
            "AND claim_type='durable_core_fact'",
            (self.harness.character_id,),
        ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("The user likes coffee.", rows[0]["content"])
        self.assertIsNotNone(rows[0]["valid_to_us"])
        status = self.harness.writer.store.connection.execute(
            "SELECT status, reason, actor_kind, source_event_id "
            "FROM claim_status_events WHERE character_id=?",
            (self.harness.character_id,),
        ).fetchone()
        self.assertEqual(("expired", "durable_user_retirement", "user"), (
            status["status"], status["reason"], status["actor_kind"],
        ))
        self.assertIsNotNone(status["source_event_id"])
        replayed = self.harness.writer.observe_canonical_user_durable_facts(
            self.harness.rows[-2],
            conversation_index=len(self.harness.rows) - 2,
            conversation_file=self.harness.conversation_file,
        )
        self.assertEqual(("unchanged", retired["claim_id"]), (
            replayed["state"], replayed["claim_id"],
        ))
        self.assertEqual(1, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claim_status_events WHERE character_id=?",
            (self.harness.character_id,),
        ).fetchone()[0])

    def test_retirement_value_mismatch_cannot_close_another_preference(self):
        self.harness.user_turn("My favorite game is Chess.")
        _, result = self.harness.user_turn("My favorite game is not Noita.")
        self.assertEqual(("ignored", "retirement_value_mismatch"), (
            result["state"], result["reason"],
        ))
        self.assertEqual(0, self.harness.writer.store.connection.execute(
            "SELECT COUNT(*) FROM claim_status_events WHERE character_id=?",
            (self.harness.character_id,),
        ).fetchone()[0])

    def test_observer_never_turns_inquiry_or_incidental_gpu_context_into_correction(self):
        for previous in (
            "Which GPU would you recommend?",
            "Do you think I should buy an RTX GPU?",
            "I saw an RTX 3060 in a shop.",
        ):
            with self.subTest(previous=previous):
                harness = _SyntheticCanonicalHarness()
                try:
                    harness.user_turn(previous)
                    _, result = harness.user_turn("I replaced it with an RTX 4070.")
                    self.assertEqual(("ignored", "no_clear_durable_fact"), (
                        result["state"], result["reason"],
                    ))
                    self.assertEqual(0, harness.writer.store.connection.execute(
                        "SELECT COUNT(*) FROM claims WHERE character_id=? "
                        "AND claim_type='durable_core_fact'",
                        (harness.character_id,),
                    ).fetchone()[0])
                finally:
                    harness.close()

    def test_valid_assertion_and_correction_use_append_only_supersession(self):
        _, first = self.harness.user_turn("I live in Toronto.")
        _, second = self.harness.user_turn("I live in Montreal.")
        self.assertEqual("created", first["state"])
        self.assertEqual("superseded", second["state"])

        current = self.harness.repository.lookup_durable_core(
            self.harness.character_id, "home.primary"
        ).candidates
        self.assertEqual(1, len(current))
        self.assertEqual("The user lives in Montreal.", current[0].content)
        self.assertEqual(
            "superseded",
            self.harness.writer.store.effective_status(
                self.harness.character_id, str(first["claim_id"])
            ),
        )
        relation = self.harness.writer.store.connection.execute(
            """SELECT relation_type FROM claim_relations
                 WHERE character_id=? AND from_claim_id=? AND to_claim_id=?""",
            (
                self.harness.character_id,
                str(second["claim_id"]),
                str(first["claim_id"]),
            ),
        ).fetchone()
        self.assertIsNotNone(relation)
        self.assertEqual("supersedes", relation[0])


if __name__ == "__main__":
    unittest.main()
