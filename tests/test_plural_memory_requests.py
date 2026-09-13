"""Natural plural preference requests use the existing immutable slot decision."""
import unittest
import test_v2_runtime_recovery as recovery_tests
from aifren.continuity.memory_query_decision import decide_memory_query


class PluralMemoryRequestTests(unittest.TestCase):
    setUp=recovery_tests.V2RuntimeRecoveryTests.setUp
    open_service=recovery_tests.V2RuntimeRecoveryTests.open_service

    def test_plural_query_preserves_supported_color_and_backend_missing_food(self):
        self.assertTrue(self.service.process_text_turn('My favorite color is blue.',speak=False).succeeded)
        self.service.maintain_canonical_observers()
        self.llm.response='Your favorite color is blue and your favorite food is sushi.'
        result=self.service.process_text_turn('What are my favorite color and favorite food?',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply.lower());self.assertNotIn('sushi',result.reply.lower())
        self.assertIn("don't remember",result.reply.lower());self.assertIn('favorite food',result.reply.lower())
        diagnostics=self.service._last_memory_authority_diagnostics
        self.assertEqual('current_governed_fact',diagnostics['memory_query_intent'])
        self.assertEqual(1,diagnostics['memory_supported_slots'])
        self.assertEqual(1,diagnostics['memory_missing_slots'])

    def test_plural_all_missing_remains_healthy_provider_free_absence(self):
        before=len(self.llm.calls)
        result=self.service.process_text_turn('What are my favorite color and favorite food?',speak=False)
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(before,len(self.llm.calls))
        self.assertTrue(self.service._last_memory_authority_diagnostics['authoritative_no_evidence'])

    def test_plural_and_singular_share_slots_without_reclassifying_downstream(self):
        singular=decide_memory_query('What is my favorite color and favorite food?')
        plural=decide_memory_query('What are my favorite color and favorite food?')
        self.assertEqual(singular,plural)

    def test_quotes_statements_and_assistant_opinions_do_not_become_user_fact_queries(self):
        for text in ('The example says "What are my favorite color and food?".',
                     'These are my favorite colors.', 'What are your favorite colors?'):
            with self.subTest(text=text):self.assertFalse(decide_memory_query(text).applicable)
