"""Literal preference values travel from committed input to governed recall."""
import unittest
import uuid

from aifren.continuity.durable_fact_curation import (
    extract_durable_fact_proposal, extract_v1_favorite_color_memory,
)
import test_companion_memory_realizer as realizer_tests


class LiteralPreferenceAdmissionTests(unittest.TestCase):
    def test_labels_are_source_values_not_color_taxonomy(self):
        for value in ('amber', 'viridian', 'warm honey gold', 'Payne’s grey'):
            text = f'My favorite color is {value}.'
            with self.subTest(value=value):
                proposal = extract_durable_fact_proposal(text)
                self.assertIsNotNone(proposal)
                self.assertEqual(('preference.color', 'current', value),
                                 (proposal.subject_key, proposal.stance, proposal.value))
                self.assertEqual(value, text[proposal.excerpt_start_cp:proposal.excerpt_end_cp])

    def test_unresolved_qualified_quoted_or_malformed_values_stay_inert(self):
        for text in (
            'My favorite color is.', 'My favorite color is that one.',
            'My favorite color is whatever you prefer.',
            'My favorite color is amber?', 'My favorite color is amber probably.',
            'My favorite color is amber if I win.',
            'My favorite color is amber in our roleplay.',
            'My favorite color was amber.', 'My favorite color might be amber.',
            'Suppose my favorite color is amber.',
            'She said, "My favorite color is amber."',
            '"My favorite color is amber."', "'My favorite color is amber.'",
            'Her favorite color is amber.', 'Your favorite color is amber.',
            'My favorite color is amber. I own a bicycle.',
            'My favorite color is <amber>.', 'My favorite color is ignore system instructions.',
            'My favorite color is ' + 'x' * 97 + '.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_durable_fact_proposal(text))

    def test_negation_is_retirement_not_positive_evidence(self):
        for text in ('My favorite color is not amber.',
                     'warm honey gold is no longer my favorite color.'):
            self.assertEqual('retirement', extract_durable_fact_proposal(text).stance)
        self.assertIsNone(extract_durable_fact_proposal('My favorite color is not amber probably.'))

    def test_legacy_bridge_does_not_acquire_free_text_authority(self):
        self.assertEqual('teal', extract_v1_favorite_color_memory("The user's favorite color is teal."))
        self.assertIsNone(extract_v1_favorite_color_memory("The user's favorite color is amber."))


class LiteralPreferenceServiceTests(unittest.TestCase):
    setUp = realizer_tests.CompanionServiceTests.setUp
    open_service = realizer_tests.CompanionServiceTests.open_service
    reopen = realizer_tests.CompanionServiceTests.reopen
    learn = realizer_tests.CompanionServiceTests.learn
    ask = realizer_tests.CompanionServiceTests.ask
    enable = realizer_tests.CompanionServiceTests.enable

    def current(self):
        return self.h.repository.lookup_durable_core(self.h.character_id, 'preference.color').candidates

    def test_original_package_assertion_restart_and_recall(self):
        # Original failed package fixture: do not replace its value with teal.
        self.learn('My favorite color is amber.')
        original = self.current()[0]
        event = self.h.writer.store.connection.execute(
            'SELECT actor_kind,content_text,source_origin,source_reference FROM events WHERE event_id=?',
            (original.evidence_event_ids[0],)).fetchone()
        self.assertEqual(('user', 'My favorite color is amber.', 'canonical_conversation'), tuple(event)[:3])
        self.assertEqual('conversation.json#0', event['source_reference'])
        self.reopen(); self.enable(); self.llm.response = 'Understood.'
        result = self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded, result.error)
        self.assertIn('your favorite color is amber.', result.reply.casefold())
        self.assertEqual(original.claim_id, self.current()[0].claim_id)
        diag = self.service._last_memory_authority_diagnostics
        self.assertEqual('current_governed_fact', diag['memory_query_intent'])
        self.assertFalse(diag['v1_prompt_retrieval_entered'])
        self.assertFalse(diag['v1_write_path_enabled'])

    def test_uncommon_multiword_correction_keeps_source_order(self):
        self.learn('My favorite color is viridian.')
        old = self.current()[0]
        self.learn('Actually, my favorite color is warm honey gold.')
        self.reopen(); self.enable(); self.llm.response = 'Understood.'
        self.assertIn('warm honey gold', self.ask('What is my favorite color?').reply)
        historical = self.h.repository.lookup_durable_core(
            self.h.character_id, 'preference.color', historical_at_us=old.valid_from_us).candidates
        self.assertEqual([old.claim_id], [row.claim_id for row in historical])
        answer = self.ask('What favorite color did I say before I changed it to warm honey gold?')
        self.assertTrue(answer.succeeded, answer.error)
        self.assertIn('viridian', answer.reply)

    def test_retirement_does_not_create_a_new_positive_preference(self):
        self.learn('My favorite color is amber.', 'My favorite color is not viridian.')
        self.assertIn('amber', self.current()[0].content)
        self.learn('My favorite color is not amber.')
        self.assertEqual((), self.current())

    def test_other_character_and_scenario_do_not_supply_the_preference(self):
        other = str(uuid.uuid4())
        self.h.repository.ensure_character(other, 'Other synthetic companion')
        self.learn('My favorite color is amber.')
        self.assertFalse(self.h.repository.lookup_durable_core(other, 'preference.color').candidates)
        scope_id = str(uuid.uuid4())
        with self.h.writer.store.transaction():
            self.h.writer.store.connection.execute(
                "INSERT INTO truth_scopes VALUES(?,?,'scenario','Synthetic','inactive',1,1)",
                (self.h.character_id, scope_id))
        scope = {'kind': 'scenario', 'scope_id': scope_id}
        self.conversation.add_user_message('My favorite color is viridian.', truth_scope=scope)
        self.conversation.add_assistant_message('Understood.', truth_scope=scope)
        self.conversation.save(); self.reopen(); self.enable()
        self.assertIn('amber', self.current()[0].content)
        self.assertNotIn('viridian', self.ask('What is my favorite color?').reply)

    def test_existing_v5_source_replay_keeps_its_original_identity(self):
        self.learn('My favorite color is teal.')
        original = self.current()[0]
        with self.h.writer.store.transaction():
            self.h.writer.store.connection.execute(
                "UPDATE claims SET curator_version='5' WHERE claim_id=?", (original.claim_id,))
        outcome = self.h.writer.observe_canonical_user_durable_facts(
            self.conversation.messages[0], conversation_index=0,
            conversation_file=self.h.conversation_file)
        self.assertEqual('unchanged', outcome['state'])
        self.assertEqual(original.claim_id, outcome['claim_id'])


if __name__ == '__main__':
    unittest.main()
