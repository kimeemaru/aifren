"""Core authority and optional reaction have separate, complete ownership."""
from dataclasses import replace
import unittest
from unittest.mock import patch
import threading

from aifren.continuity.companion_memory_realizer import (
    CompanionMemoryRealizer, CompanionMemoryResponse, present_reaction_allowed,
)
from aifren.continuity.memory_v2_answer_governance import (
    MemoryAnswerEvidence, compose_memory_answer_requirement, validate_memory_answer_response,
)
from aifren.dialogue.presentation_metadata import parse_assistant_response
import test_memory_v2_ordering as ordering
import test_v2_followup_source_binding as binding


class CompanionSurfaceTests(unittest.TestCase):
    def requirement(self, source, speaker='user', relation='kite'):
        query = f'What did {"you" if speaker == "assistant" else "I"} say about {relation}?'
        return compose_memory_answer_requirement(query, (MemoryAnswerEvidence(
            'exact-record', 'historical_conversation_only', speaker, 'unknown_scope', 'assertion', source),))

    def test_surface_matrix_is_reproducible_short_and_fully_grounded(self):
        matrix = []
        for speaker in ('user','assistant'):
            for source in ('I built a green kite.', 'I might build a green kite.',
                           'I did not finish the project.', 'We watched a meteor beside a bridge.',
                           'I used Python for the project.', 'I own a green hat.'):
                matrix.append(self.requirement(source,speaker))
        for value in ('blue','green','a fox','vanilla'):
            matrix.append(compose_memory_answer_requirement('What is my favorite color?', (
                MemoryAnswerEvidence('color','governed_current_fact','','real_world','assertion','',
                                     'preference.color',value),)))
        matrix.append(compose_memory_answer_requirement('What is my favorite dessert?', ()))
        for req in matrix:
            for seed in range(32):
                with self.subTest(speaker=req.requested_speaker, relation=req.requested_relation, seed=seed):
                    a=CompanionMemoryRealizer().realize(req,seed=str(seed))
                    b=CompanionMemoryRealizer().realize(req,seed=str(seed))
                    self.assertEqual(a,b)
                    self.assertTrue(validate_memory_answer_response(req,a.dialogue).accepted)
                    self.assertNotEqual('emergency_safe_response',a.state)
                    self.assertLess(len(a.dialogue.split()),60)
                    self.assertNotRegex(a.dialogue,r'(?i)\b(?:fallback|retrieval|governed|evidence|confidence|database)\b')
                    self.assertNotIn('..',a.dialogue)
                    self.assertNotRegex(a.dialogue,r"(?:Yeah|Right), i\b|that We\b")

    def test_typed_place_avoids_archive_quotation(self):
        from aifren.continuity.memory_query_decision import decide_memory_query
        req=compose_memory_answer_requirement('Which place was that?', (
            MemoryAnswerEvidence('event','historical_conversation_only','user','real_world','assertion',
                                 'We watched a meteor beside the maple arch.'),),
            memory_query_decision=decide_memory_query('Which place was that?'))
        for seed in range(8):
            core=CompanionMemoryRealizer().realize(req,seed=str(seed))
            self.assertIn('beside the maple arch',core.dialogue)
            self.assertNotIn('“',core.dialogue)
            self.assertNotIn('mentioned',core.dialogue)
            self.assertTrue(validate_memory_answer_response(req,core.dialogue).accepted)

    def test_other_typed_attributes_identity_and_unavailable(self):
        from aifren.continuity.memory_query_decision import decide_memory_query
        from aifren.memory_v2_store.models import RetrievalHealth, RetrievalLaneHealth
        for source,query,value in (
            ('I wrote the parser in Python.','What language was it?','Python'),
            ('I met Mira.','Who was that?','Mira'),
            ('My name is Mira.','What name was that?','Mira')):
            req=compose_memory_answer_requirement(query,(MemoryAnswerEvidence(
                'record','historical_conversation_only','user','real_world','assertion',source),),
                memory_query_decision=decide_memory_query(query))
            for seed in range(8):
                core=CompanionMemoryRealizer().realize(req,seed=str(seed))
                self.assertIn(value,core.dialogue)
                self.assertTrue(validate_memory_answer_response(req,core.dialogue).accepted)
        req=compose_memory_answer_requirement('What is my name?',(MemoryAnswerEvidence(
            'name','governed_current_fact','','real_world','assertion','','identity.name','Mira'),))
        core=CompanionMemoryRealizer().realize(req,seed='name')
        self.assertEqual('Your name is Mira.',core.dialogue)
        self.assertNotEqual('emergency_safe_response',core.state)
        req=compose_memory_answer_requirement('What is my favorite color?',(),
            lookup_health=RetrievalHealth((RetrievalLaneHealth('claims','incomplete','lookup','lookup_failed'),)))
        core=CompanionMemoryRealizer().realize(req,seed='unavailable')
        self.assertEqual('unavailable',core.state)
        self.assertNotIn("don't remember",core.dialogue)
        self.assertTrue(validate_memory_answer_response(req,core.dialogue).accepted)

    def test_reactions_are_present_subjective_complete_clauses(self):
        for reaction in ("That's adorable.", 'I like that color.', 'That sounds peaceful.',
                         "That's a nice color!", 'I love that. That sounds fun.'):
            with self.subTest(reaction=reaction): self.assertTrue(present_reaction_allowed(reaction))
        for reaction in ('You seemed really happy about it.', 'I remember loving that.',
                         'It must have been raining.', 'That was adorable.', 'That sounds fun again.',
                         'I like that color because you wore it.', 'That is green.',
                         'That sounds peaceful beside the lake.', 'I like that color. You wore it again.',
                         '*smiles* You were happy.', 'I like that? Were you happy?',
                         'That sounds peaceful, just like last time.', "That's adorable; you looked happy."):
            with self.subTest(reaction=reaction): self.assertFalse(present_reaction_allowed(reaction))

    def test_unsafe_tail_drops_without_altering_core_or_authority(self):
        req=self.requirement('I built a green kite.')
        realizer=CompanionMemoryRealizer();core=realizer.realize(req,seed='stable')
        for raw in ('You were delighted.', '{broken', '', 'It must have been raining.'):
            result=realizer.compose(core,parse_assistant_response(raw))
            self.assertEqual(core.dialogue,result.dialogue)
            self.assertFalse(result.reaction)
        result=realizer.compose(core,parse_assistant_response("That's adorable."))
        self.assertEqual(core.dialogue+" That's adorable.",result.dialogue)
        self.assertTrue(validate_memory_answer_response(req,core.dialogue).accepted)

    def test_historical_action_stays_quoted_instead_of_becoming_a_present_emote(self):
        req=self.requirement('I built a green kite. *smiles*',speaker='assistant')
        core=CompanionMemoryRealizer().realize(req,seed='proof')
        self.assertEqual('exact_report',core.surface)
        self.assertIn('"I built a green kite. *smiles*"',core.dialogue)
        emphasis=self.requirement('I built a kite that fell *once*.',speaker='assistant')
        core=CompanionMemoryRealizer().realize(emphasis,seed='proof')
        self.assertNotEqual('exact_report',core.surface)
        self.assertIn('*once*',core.dialogue)
        self.assertTrue(validate_memory_answer_response(emphasis,core.dialogue).accepted)

    def test_core_validator_is_unchanged_for_wrong_or_decorated_history(self):
        req=self.requirement('I built a green kite.')
        for text in ('You told me you built a shiny green kite.',
                     'I told you I built a green kite.',
                     'You built a green kite again.',
                     'You told me you built a green kite in the rain.'):
            self.assertFalse(validate_memory_answer_response(req,text).accepted)


class CompanionServiceTests(unittest.TestCase):
    setUp=ordering.HistoricalOrderingServiceTests.setUp
    open_service=ordering.HistoricalOrderingServiceTests.open_service
    reopen=ordering.HistoricalOrderingServiceTests.reopen
    learn=ordering.HistoricalOrderingServiceTests.learn
    ask=ordering.HistoricalOrderingServiceTests.ask

    def enable(self):
        self.llm.companion_memory_realization=True
        self.service._memory_surface_session='fixed-session'

    def test_current_core_plus_reaction_one_call_one_publication_and_exact_speech(self):
        self.learn('My favorite color is blue.');self.enable()
        self.llm.response="That's adorable."
        events=[];self.service.subscribe(events.append);before=len(self.llm.calls)
        result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('favorite color is blue.',result.reply)
        self.assertTrue(result.reply.endswith("That's adorable."))
        self.assertEqual(1,len(self.llm.calls)-before)
        self.assertEqual(1,len([e for e in events if e.type=='assistant_response']))
        self.assertFalse([e for e in events if e.type=='assistant_delta'])
        diag=self.service._last_memory_authority_diagnostics
        self.assertEqual('grounded_core_plus_reaction',diag['memory_realization'])
        self.assertFalse(diag['repair_attempted']);self.assertFalse(diag['fallback_used'])
        self.assertIn('ONLY one optional present reaction choice',self.llm.calls[-1][1])
        self.assertNotIn('[Authoritative memory answer brief]',self.llm.calls[-1][1])

    def test_only_accepted_reaction_metadata_publishes_and_provider_cap_is_local(self):
        self.learn('My favorite color is blue.');self.enable()
        events=[];self.service.subscribe(events.append)
        self.llm.response='{"dialogue":"That sounds nice.","presentation":{"emotion":"happy"}}'
        with patch.object(self.llm,'generate_bounded',wraps=self.llm.generate_bounded) as bounded:
            result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual(64,bounded.call_args.kwargs['max_output_tokens'])
        published=[e for e in events if e.type=='assistant_response'][-1]
        self.assertEqual('happy',published.data['presentation']['emotion'])
        self.llm.response='{"dialogue":"You looked happy again.","presentation":{"emotion":"angry"}}'
        result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        published=[e for e in events if e.type=='assistant_response'][-1]
        self.assertNotIn('emotion',published.data['presentation'])
        self.assertNotIn('gesture',published.data['presentation'])
        self.assertEqual('normal',published.data['presentation']['awareness_mode'])

    def test_unsafe_empty_malformed_and_failed_provider_keep_core_without_repair(self):
        from aifren.llm.unavailable import ModelTransportError
        self.learn('My favorite color is blue.');self.enable()
        for raw in ('You wore it in the rain.', '{"dialogue":', '', 'Your favorite color is red.'):
            self.llm.response=raw;before=len(self.llm.calls)
            result=self.ask('What is my favorite color?')
            self.assertTrue(result.succeeded,result.error)
            self.assertIn('favorite color is blue.',result.reply)
            self.assertNotIn('rain',result.reply);self.assertNotIn('red',result.reply)
            self.assertEqual(1,len(self.llm.calls)-before)
            self.assertFalse(self.service._last_memory_authority_diagnostics['repair_attempted'])
        with patch.object(self.llm,'generate',side_effect=ModelTransportError('test')), patch.object(self.llm,'generate_bounded',side_effect=ModelTransportError('test')):
            result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply)

    def test_unrepresentable_composition_uses_existing_safety_without_second_call(self):
        self.learn('My favorite color is blue.');self.enable()
        self.llm.response='That sounds nice.';before=len(self.llm.calls)
        validate=self.service._validate_governed_response
        def reject_composition(parsed,policy):
            if policy.memory_realized_response is not None:
                return False,'capability_test_boundary','',None
            return validate(parsed,policy)
        with patch.object(self.service,'_validate_governed_response',side_effect=reject_composition):
            result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn('blue',result.reply)
        self.assertEqual(1,len(self.llm.calls)-before)
        self.assertEqual('emergency_safe_response',self.service._last_memory_authority_diagnostics['memory_realization'])
        self.assertFalse(self.service._last_memory_authority_diagnostics['repair_attempted'])

    def test_mixed_slots_and_before_after_restart_keep_typed_authority(self):
        self.learn('My favorite color is green.','Actually, my favorite color is blue.')
        self.reopen();self.enable();self.llm.response='I like that color.'
        for q,value in [('What did I say my favorite color was before I corrected it to blue?','green'),
                        ('What is my current favorite color?','blue'),
                        ('What did I say about my favorite color after I said it was green?','blue'),
                        ('What are my favorite color and favorite dessert?','blue')]:
            result=self.ask(q);self.assertTrue(result.succeeded,result.error)
            self.assertIn(value,result.reply);self.assertTrue(result.reply.endswith('I like that color.'))
            if 'dessert' in q: self.assertIn("don't remember",result.reply)

    def test_first_memory_after_long_gap_keeps_return_policy_without_free_fact_generation(self):
        from datetime import datetime,timedelta,timezone
        then=datetime(2026,7,1,12,tzinfo=timezone.utc)
        self.conversation._clock=lambda:then
        self.learn('My favorite color is blue.')
        self.conversation._clock=lambda:then+timedelta(days=2)
        self.enable();self.llm.response='That sounds nice.'
        policies=[];validate=self.service._validate_governed_response
        def capture(parsed,policy):
            policies.append(policy)
            return validate(parsed,policy)
        with patch.object(self.service,'_validate_governed_response',side_effect=capture):
            result=self.ask('What is my favorite color?')
        self.assertTrue(result.succeeded,result.error)
        self.assertEqual('grounded_core_plus_reaction',self.service._last_memory_authority_diagnostics['memory_realization'])
        self.assertTrue(any(p.requirement and p.requirement.intent=='return_continuity' for p in policies))
        self.assertTrue(any(p.temporal_facts and p.temporal_facts.return_opportunity for p in policies))

    def test_no_evidence_keeps_abstention_and_no_provider_call(self):
        self.enable();before=len(self.llm.calls)
        result=self.ask('What is my favorite dessert?')
        self.assertTrue(result.succeeded,result.error)
        self.assertIn("don't remember",result.reply)
        self.assertEqual(before,len(self.llm.calls))


class CompanionBindingTests(binding.FollowupSourceBindingTests):
    # Reuse all production-shaped cancellation/persistence/scope/missing/multi-source
    # tests on the new path, not just helper flags or the old provider-direct route.
    def open_service(self):
        service = super().open_service()
        self.llm.companion_memory_realization=True
        return service
