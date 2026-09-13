from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from companion_context import CompanionContextAssembler, CompanionContextRequest
from recent_pulse import RecentPulseBuilder
from memory_v2_episode_compaction import EpisodeCompactionCache, EpisodeCompactor
from memory_v2_store import OpenThreadProposal, OpenThreadProposalOperation
from test_continuity_companion_tranche import _Harness
from test_memory_v2_episode_compaction import _Provider


class RecentPulseTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(); self.addCleanup(self.h.close)
        self.now = datetime(2026,9,10,12,tzinfo=timezone.utc)
        self.h.base = self.now - timedelta(days=1)
        self.scope = self.h.repository.active_truth_scope(self.h.character_id)
        self.request = CompanionContextRequest(self.h.character_id, self.scope.truth_scope_id, "turn", self.now)
        self.conversation = SimpleNamespace(messages=self.h.rows, conversation_file=self.h.conversation_file)

    def add(self, text, when=None):
        index = len(self.h.rows)
        when = when or (self.h.base + timedelta(seconds=index))
        scope = {"kind":self.scope.kind,"scope_id":self.scope.truth_scope_id}
        self.h.rows.extend((dict(role="user",content=text,timestamp=when.isoformat(),truth_scope=scope),
                            dict(role="assistant",content="I understand.",timestamp=(when+timedelta(seconds=1)).isoformat(),truth_scope=scope)))
        self.h.conversation_file.write_text(json.dumps(self.h.rows))
        return index

    def thread(self, when=None):
        text = "I plan to prepare for the hiking trip."
        index = self.add(text, when)
        event = "thread-event-"+str(index)
        self.h.writer.store.add_event(self.h.character_id,event,index+1,event_type="canonical_user_message",
            actor_kind="user",recorded_at_us=int(datetime.fromisoformat(self.h.rows[index]['timestamp']).timestamp()*1e6),
            content_text=text,source_origin="canonical_conversation",source_reference="conversation.json#"+str(index))
        applied = self.h.writer.store.apply_open_thread_proposal(self.h.character_id,
            OpenThreadProposal((OpenThreadProposalOperation("open","trip",0,len(text),
                "plan_or_intention","user","prepare for the hiking trip"),)), evidence_event_id=event)
        return applied[0]['thread_id'], index

    def build(self, window="balanced", request=None):
        return RecentPulseBuilder(self.h.writer,self.conversation,window=window).build(request or self.request)

    def episode(self, text="We discussed hiking trip preparation.", terms=("hiking trip",), index=None):
        if index is None: index = self.add(text)
        while len(self.h.rows)<64:
            self.add("A separate short discussion.")
        cache = EpisodeCompactionCache(self.h.writer.store,self.h.character_id)
        report = cache.rebuild(self.h.rows,EpisodeCompactor(_Provider()))
        self.assertTrue(report.published)
        row = self.h.writer.store.connection.execute(
            "SELECT summary_id,legacy_metadata_json FROM summaries WHERE character_id=? AND summary_level='episode_compaction' ORDER BY created_at_us LIMIT 1",
            (self.h.character_id,)).fetchone()
        data=json.loads(row['legacy_metadata_json'])
        data.update(continuity_anchor_count=1,continuity_anchors=[dict(anchor_id="A1",detail=text,
                    source_record_indices=[index],key_terms=list(terms))])
        self.h.writer.store.connection.execute("UPDATE summaries SET legacy_metadata_json=? WHERE character_id=? AND summary_id=?",
            (json.dumps(data),self.h.character_id,row['summary_id']))
        return row['summary_id']

    def test_current_recent_open_thread_and_read_only_build(self):
        self.thread()
        before=self.h.writer.store.connection.total_changes
        result=self.build()
        self.assertEqual(1,len(result.contributions))
        self.assertIn("prepare for the hiking trip",result.contributions[0].payload)
        self.assertEqual(before,self.h.writer.store.connection.total_changes)

    def test_local_calendar_windows_not_rolling_hours(self):
        self.thread(self.now.replace(hour=0,minute=1)-timedelta(days=2))
        self.assertEqual(1,len(self.build().contributions))
        self.assertEqual(0,len(self.build('compact').contributions))
        self.assertEqual(0,len(self.build(request=replace(self.request,now=self.now+timedelta(days=1))).contributions))
        self.assertEqual(1,len(self.build('deep',replace(self.request,now=self.now+timedelta(days=4))).contributions))
        self.assertEqual(0,len(self.build('deep',replace(self.request,now=self.now+timedelta(days=5))).contributions))

    def test_same_day_compact_and_future_timestamp(self):
        self.thread(self.now-timedelta(minutes=1))
        self.assertEqual(1,len(self.build('compact').contributions))
        self.assertEqual(0,len(self.build(request=replace(self.request,now=self.now-timedelta(minutes=2))).contributions))

    def test_dst_ambiguous_legacy_time_excluded(self):
        self.thread(datetime(2026,11,1,1,30))
        request=replace(self.request,now=datetime(2026,11,2,12,tzinfo=ZoneInfo('America/Toronto')))
        self.assertEqual((),self.build(request=request).contributions)

    def test_closed_thread_is_excluded(self):
        thread,index=self.thread()
        self.h.writer.store.apply_open_thread_proposal(self.h.character_id,
            OpenThreadProposal((OpenThreadProposalOperation('resolve',thread,0,len(self.h.rows[index]['content'])),)),
            evidence_event_id='thread-event-'+str(index))
        self.assertEqual((),self.build().contributions)

    def test_character_scope_source_hash_and_incomplete_exchange_are_excluded(self):
        self.thread()
        self.assertEqual((),self.build(request=replace(self.request,character_id='other')).contributions)
        self.assertEqual((),self.build(request=replace(self.request,truth_scope_id='other')).contributions)
        original=self.h.rows[0]['content'];self.h.rows[0]['content']='Different content.'
        self.assertEqual((),self.build().contributions)
        self.h.rows[0]['content']=original;self.h.rows.pop()
        self.assertEqual((),self.build().contributions)

    def test_recovery_unresolved_source_never_enters_pulse(self):
        self.thread()
        key=hashlib.sha256(b'conversation.json').hexdigest()
        self.h.writer.store.connection.execute("INSERT INTO canonical_observation_progress VALUES (?,?,?,?,?,?,?,?,?)",
            (self.h.character_id,key,'continuity','synthetic-policy',0,'digest','unresolved','unproved_operation',1))
        self.assertEqual((),self.build().contributions)

    def test_episode_topics_are_source_linked_past_data_not_summary_truth(self):
        self.episode()
        result=self.build()
        self.assertEqual(1,len(result.contributions))
        cue=result.contributions[0]
        self.assertIn('Past discussion topics',cue.payload)
        self.assertIn('current status unknown',cue.payload)
        self.assertIn('hiking trip',cue.payload)
        self.assertNotIn('completed episode',cue.payload)
        self.assertTrue(cue.source_refs[0].startswith('episode:'))

    def test_episode_and_thread_same_source_deduplicate(self):
        _,index=self.thread()
        self.episode(text=self.h.rows[index]['content'],index=index)
        result=self.build()
        self.assertEqual(2,len(result.contributions))
        assembled=CompanionContextAssembler().assemble(result.contributions,self.request)
        self.assertEqual(1,len(assembled.contributions))
        self.assertTrue(assembled.contributions[0].payload.startswith('Open'))

    def test_closed_thread_source_is_not_revived_by_an_episode(self):
        thread,index=self.thread()
        self.episode(text=self.h.rows[index]['content'],index=index)
        self.h.writer.store.apply_open_thread_proposal(self.h.character_id,
            OpenThreadProposal((OpenThreadProposalOperation('resolve',thread,0,len(self.h.rows[index]['content'])),)),
            evidence_event_id='thread-event-'+str(index))
        self.assertEqual((),self.build().contributions)

    def test_old_episode_topics_and_wrong_scope_are_excluded(self):
        self.episode()
        self.assertEqual((),self.build(request=replace(self.request,now=self.now+timedelta(days=3))).contributions)
        self.h.turn("Let's roleplay that we're in a synthetic test scene.")
        other=self.h.repository.active_truth_scope(self.h.character_id)
        self.assertNotEqual(self.scope.truth_scope_id,other.truth_scope_id)
        self.assertEqual((),self.build(request=replace(self.request,truth_scope_id=other.truth_scope_id)).contributions)

    def test_episode_admission_work_limit_abstains_without_running_validator(self):
        self.episode()
        with (patch('recent_pulse.MAX_VALIDATION_RECORDS',10),
              patch.object(EpisodeCompactionCache,'validate_for_context',side_effect=AssertionError('bounded work'))):
            result=self.build()
        self.assertEqual((),result.contributions)
        self.assertEqual(1,result.diagnostics['episode_budget_excluded'])

    def test_episode_source_change_fails_shared_validator(self):
        self.episode();self.h.rows[0]['content']='We discussed something different.'
        self.assertEqual((),self.build().contributions)

    def test_corrected_fact_not_projected_as_current(self):
        self.h.turn('My favorite color is green.')
        self.episode(text=self.h.rows[0]['content'],terms=('green',),index=0)
        self.h.turn('My favorite color is blue now.')
        self.assertEqual((),self.build().contributions)
        self.assertGreater(self.build().diagnostics['state_excluded'],0)

    def test_assistant_echo_of_a_corrected_fact_is_also_excluded(self):
        self.h.turn('My favorite color is green.')
        self.h.rows[1]['content']='You said green.'
        self.episode(text=self.h.rows[1]['content'],terms=('green',),index=1)
        self.h.turn('My favorite color is blue now.')
        self.assertEqual((),self.build().contributions)

    def test_retired_scene_source_is_not_resurrected(self):
        self.h.turn('You are wearing a blue hat.')
        self.episode(text=self.h.rows[0]['content'],terms=('blue hat',),index=0)
        self.h.turn('I remove your blue hat.')
        self.assertEqual((),self.build().contributions)

    def test_explicit_memory_exits_before_store_access(self):
        class Forbidden:
            @property
            def character_id(self): raise AssertionError('no collection for memory answers')
        result=RecentPulseBuilder(Forbidden(),None).build(replace(self.request,explicit_memory=True))
        self.assertEqual((),result.contributions)

    def test_restart_rebuild_and_original_rows_unchanged(self):
        self.thread();self.episode()
        before=self.h.conversation_file.read_bytes()
        first=self.build()
        reopened=SimpleNamespace(messages=json.loads(before),conversation_file=self.h.conversation_file)
        second=RecentPulseBuilder(self.h.writer,reopened).build(self.request)
        self.assertEqual(first,second)
        self.assertEqual(before,self.h.conversation_file.read_bytes())


if __name__ == '__main__': unittest.main()
