"""Bounded synthesis through real Kokoro/service ownership, no model or audio I/O."""
from concurrent.futures import Future
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from benchmarks.active_state.production_session import ProductionSession, response_envelope
from test_cancelled_response_repair import FocusedPtt
from tts.tts import KokoroTextToSpeech
from tts.streaming import StreamingSpeechQueue, TtsSynthesisResourceManager
from dialogue_semantics import spoken_text

LONG = ' '.join(['We can enjoy the quiet garden, with its bright flowers and peaceful paths.'] * 12)


class ControlledKokoro(KokoroTextToSpeech):
    """Only model kernels and hardware dispatch replaced; production preparation remains."""
    def __init__(self):
        self._initialize_playback_state()
        self._initialize_continuous_state()
        self.voice = 'af_synthetic'
        self.voice_path = 'synthetic-test-voice'  # Never opened.
        self.device, self.speed = 'cpu', 1.0
        self.entered = [threading.Event() for _ in range(40)]
        self.release = [threading.Event() for _ in range(40)]
        self.block = set()
        self.fail = set()
        self.units, self.kernels, self.dispatched = [], [], []
        self.pipeline = self.generate_fixture

    def generate_fixture(self, text, **kwargs):
        self.units.append(text)
        # Two yield boundaries within a provider request reproduce the old
        # prepare path consuming the remaining native work after interruption.
        mid = len(text) // 2
        for fragment in (text[:mid], text[mid:]):
            index = len(self.kernels)
            self.kernels.append(fragment)
            if index < len(self.entered): self.entered[index].set()
            if index in self.block and not self.release[index].wait(5):
                raise TimeoutError('Synthetic kernel gate timeout')
            if index in self.fail: raise MemoryError('Synthetic resource failure')
            yield SimpleNamespace(audio=np.array([ord(c) for c in fragment], dtype=np.float32), tokens=())

    def _start_playback(self, audio, sample_rate, generation, word_start_seconds=None):
        self.dispatched.append(''.join(chr(int(value)) for value in audio.reshape(-1)))
        self._mark_playback_active(generation)
        self._notify_playback_started(len(audio)/sample_rate, [], word_start_seconds, generation)
        return True

    def complete(self):
        generation = self.active_playback_generation
        if generation is not None:
            self._retire_playback(generation)
            self.playback_finished.set()
            self._notify_playback_finished(generation)

    def unblock(self):
        for event in self.release: event.set()


class CancellableSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aifren-synthesis-test-')
        self.addCleanup(self.temp.cleanup)
        cwd = Path.cwd(); os.chdir(self.temp.name); self.addCleanup(os.chdir, cwd)
        self.env = patch.dict(os.environ); self.env.start(); self.addCleanup(self.env.stop)
        for key in tuple(os.environ):
            if key.startswith('AIFREN_'): del os.environ[key]
        self.tts = ControlledKokoro()
        self.session = ProductionSession(self.id(), tts=self.tts)
        self.service = self.session.service
        # This fixture owns the full-preparation compatibility path; its
        # hardware stub intentionally overrides _start_playback only. The
        # responsive path has separate real-queue/fake-device coverage.
        self.service.responsive_speech = False
        self.service._ptt_factory = FocusedPtt
        self.events = []; self.service.subscribe(self.events.append)
        self.workers = []
        self.addCleanup(self.close)

    def close(self):
        self.tts.unblock()
        for thread, future in self.workers:
            thread.join(6); self.assertFalse(thread.is_alive(), 'Owned worker leaked')
        self.session.close()

    def start(self, callback):
        future = Future()
        def run():
            try: future.set_result(callback())
            except BaseException as error: future.set_exception(error)
        thread = threading.Thread(target=run, daemon=True)
        self.workers.append((thread, future)); thread.start()
        return future

    def turn(self, text='Hello.', response=LONG):
        self.session.script.queue([response_envelope(response)])
        return self.start(lambda: self.service.process_text_turn(text, speak=True))

    def cancelled_case(self, middle=False, late_error=False):
        gate = 2 if middle else 0
        self.tts.block = {gate, gate+1}
        if late_error: self.tts.fail = {gate}
        old = self.turn()
        self.assertTrue(self.tts.entered[gate].wait(3))
        # PTT completes before native work is released; this alone is not success.
        self.start(self.service.push_to_talk_press).result(1)
        self.service.push_to_talk_release()
        replacement = self.turn('Hello again.', 'Fresh replacement.')
        # A following obsolete native unit remains blocked. Replacement must
        # still reach provider/commit/playback without consuming that unit.
        self.tts.block.remove(gate+1) if late_error else None
        if not late_error:
            # Replacement kernels must be free; only old content would wait.
            original = self.tts.pipeline
            def scoped_gate(text, **kwargs):
                if text == 'Fresh replacement.': self.tts.block.discard(gate+1)
                yield from original(text, **kwargs)
            self.tts.pipeline = scoped_gate
        self.tts.release[gate].set()
        result = replacement.result(2)
        old.result(2)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(['Fresh replacement.'], self.tts.dispatched)
        self.assertEqual(1, sum(e.type == 'tts_state' and e.data.get('state') == 'playback_started' for e in self.events))
        self.assertEqual(1, sum(e.type == 'assistant_response' and e.data.get('turn_id') == 1 for e in self.events))
        saved = json.loads(self.session.conversation_file.read_text())
        self.assertEqual([LONG, 'Fresh replacement.'], [m['content'] for m in saved if m['role']=='assistant'])
        self.tts.complete()

    def test_ptt_first_unit_does_not_wait_for_the_obsolete_tail(self): self.cancelled_case()
    def test_ptt_middle_unit_does_not_wait_for_the_obsolete_tail(self): self.cancelled_case(middle=True)
    def test_late_resource_error_cannot_retry_or_publish_old_audio(self): self.cancelled_case(late_error=True)

    def test_complete_spoken_projection_is_exact_and_ordered(self):
        text = 'A *very bright* day. *smiles with **quiet warmth*** ' + LONG + '  And now: one last thought!'
        result = self.turn(response=text).result(3)
        self.assertTrue(result.succeeded, result.error)
        expected = spoken_text(text)
        self.assertEqual([expected], self.tts.dispatched)
        self.assertEqual(expected, ''.join(self.tts.units))
        self.assertGreater(len(self.tts.units), 1)
        self.assertTrue(all(len(unit) <= 260 for unit in self.tts.units))
        self.assertEqual(1, sum(e.type=='tts_state' and e.data.get('state')=='playback_started' for e in self.events))
        self.tts.complete()

    def test_long_single_sentence_whitespace_punctuation_are_not_lost(self):
        text = '  '+('unusually detailed, peaceful\twords  '*40)+'end!  '
        result = TtsSynthesisResourceManager(self.tts, cancelled=threading.Event()).prepare(text)
        self.assertTrue(result.succeeded)
        audio, rate, words = result.prepared
        self.assertEqual(text, ''.join(chr(int(value)) for value in audio.reshape(-1)))
        self.assertEqual(text, ''.join(self.tts.units))
        self.assertTrue(all(len(unit) <= 260 for unit in self.tts.units))

    def test_cancelled_lock_waiter_never_enters_model(self):
        self.tts._kokoro_synthesis_lock.acquire()
        cancelled = threading.Event()
        try:
            waiter = self.start(lambda:TtsSynthesisResourceManager(self.tts,cancelled=cancelled).prepare(LONG))
            cancelled.set()
            self.assertTrue(waiter.result(1).cancelled)
            self.assertEqual([],self.tts.units)
        finally: self.tts._kokoro_synthesis_lock.release()

    def test_queue_cancellation_discards_queued_and_overflow_units(self):
        self.tts.block = {0}
        completed = threading.Event()
        queue = StreamingSpeechQueue(self.tts,max_chunks=1,on_complete=lambda _:completed.set())
        try:
            queue.submit(LONG)
            self.assertTrue(self.tts.entered[0].wait(2))
            for i in range(8): queue.submit('Obsolete queued sentence.')
            queue.cancel()
            self.assertEqual(0, queue.pending_jobs)
            self.tts.release[0].set()
            queue.join(2)
            self.assertFalse(queue._synthesis_thread.is_alive())
            self.assertFalse(queue._playback_thread.is_alive())
            self.assertEqual([], self.tts.dispatched)
            self.assertTrue(completed.is_set())
        finally:
            self.tts.unblock();queue.cancel();queue.join(2)

    def test_scene_reaction_uses_cancellable_preparation_after_durable_event(self):
        import uuid
        self.session.turn('I blindfold you.', response_envelope('*Holds still.*'))
        snapshot=self.service.continuity_snapshot()
        row=next(row for row in snapshot['scene_relations'] if row['cause']=='blindfold')
        self.service._response_generator=None
        self.session.provider.generate=lambda *a:response_envelope(LONG)
        self.tts.block={0,1}
        future=self.start(lambda:self.service.apply_continuity_control(
            command_id=str(uuid.uuid4()),action='interact_scene_relation',
            expected_revision=snapshot['revision'],action_token=row['clear_token']))
        self.assertTrue(self.tts.entered[0].wait(3))
        self.start(self.service.push_to_talk_press).result(1)
        self.tts.release[0].set()
        result=future.result(2)
        self.assertTrue(result['reaction']['published'])
        records=json.loads(self.session.conversation_file.read_text())
        self.assertEqual(1,len([r for r in records if r.get('origin',{}).get('kind')=='scene_ui']))
        self.assertEqual(LONG,records[-1]['content'])
        self.assertEqual([],self.tts.dispatched)
        self.assertEqual(1,len(self.tts.kernels))

    def test_governed_repair_stays_private_then_uses_cancellable_preparation(self):
        self.session.turn('I blindfold you.',response_envelope('*Holds still.*'))
        repair_entered,release_repair=threading.Event(),threading.Event()
        original=self.session.provider.generate_bounded
        def repair(*a,**kw):
            repair_entered.set()
            if not release_repair.wait(3): raise TimeoutError('Synthetic repair gate')
            return "I can still enjoy a quiet conversation."
        self.session.provider.generate_bounded=repair
        self.session.script.queue([response_envelope('I can see your red shirt clearly.')])
        with patch('model_settings.kokoro_early_speech_status',return_value={'effective':True}):
            future=self.start(lambda:self.service.process_text_turn('How are you?',speak=True))
            try:
                self.assertTrue(repair_entered.wait(2))
                self.assertEqual([],self.tts.units)
                self.assertFalse(any(e.type=='assistant_delta' for e in self.events))
                self.tts.block={0,1};release_repair.set()
                self.assertTrue(self.tts.entered[0].wait(2), str(future.result(1)) if future.done() else 'still generating')
                self.start(self.service.push_to_talk_press).result(1)
                self.tts.release[0].set()
                self.assertTrue(future.result(2).succeeded)
                self.assertEqual([],self.tts.dispatched)
            finally: release_repair.set()
        self.assertNotIn('I can see your red shirt clearly.',self.session.conversation_file.read_text())

    def test_disabled_early_speech_waits_for_provider_completion(self):
        waiting,release=threading.Event(),threading.Event()
        def stream(*a,**kw):
            yield 'A calm first sentence. '
            waiting.set()
            if not release.wait(3): raise TimeoutError('Synthetic provider gate')
            yield 'A clear second sentence.'
        self.service._response_generator=None
        self.session.provider.stream_generate=stream
        with patch('model_settings.kokoro_early_speech_status',return_value={'effective':False}):
            turn=self.start(lambda:self.service.process_text_turn('Hello.',speak=True))
            try:
                self.assertTrue(waiting.wait(2))
                self.assertEqual([],self.tts.units)
                release.set();self.assertTrue(turn.result(3).succeeded)
                self.assertEqual(['A calm first sentence. A clear second sentence.'],self.tts.dispatched)
            finally: release.set()

    def test_empty_and_failed_synthesis_retire_then_subsequent_turn_works(self):
        first=self.turn(response='*nods*').result(3)
        self.assertTrue(first.succeeded);self.assertEqual([],self.tts.units)
        self.tts.pipeline=lambda *a,**k:iter(())
        self.assertTrue(self.turn(response='A safe complete response.').result(3).succeeded)
        self.assertEqual([],self.tts.dispatched)
        self.assertTrue(any(e.type=='tts_state' and e.data.get('state')=='failed' for e in self.events))
        self.tts.pipeline=self.tts.generate_fixture
        self.assertTrue(self.turn(response='Now audio recovers.').result(3).succeeded)
        self.assertEqual(['Now audio recovers.'],self.tts.dispatched)

    def test_rapid_retirement_during_kernels_does_not_leak_workers(self):
        for iteration in range(3):
            gate=len(self.tts.kernels)
            self.tts.block={gate}
            old=self.turn()
            self.assertTrue(self.tts.entered[gate].wait(2))
            self.service.push_to_talk_press()
            self.service.push_to_talk_release()
            self.tts.release[gate].set()
            old.result(2)
        self.assertEqual([],self.tts.dispatched)
        self.assertFalse(self.service._turn_lock.locked())

    def test_speech_retirement_without_turn_cancellation_drops_obsolete_units(self):
        self.tts.block={0,1}
        old=self.turn()
        self.assertTrue(self.tts.entered[0].wait(2))
        # This is the shared stop boundary used by shutdown and settings; it
        # must not depend on PTT separately cancelling the canonical turn.
        self.service.stop_speaking()
        self.tts.release[0].set()
        self.assertTrue(old.result(2).succeeded)
        self.assertEqual([],self.tts.dispatched)
        self.assertEqual(1,len(self.tts.kernels))
        self.assertFalse(any(e.type=='tts_state' and e.data.get('state') in {'failed','not_started'} for e in self.events))

    def test_alignment_offsets_are_global_across_native_units(self):
        def aligned(text, **kwargs):
            self.tts.units.append(text)
            tokens=[SimpleNamespace(text=word,start_ts=index*.01)
                    for index,word in enumerate(text.split())]
            yield SimpleNamespace(audio=np.ones(24000,dtype=np.float32),tokens=tokens)
        self.tts.pipeline=aligned
        prepared=TtsSynthesisResourceManager(self.tts,cancelled=threading.Event()).prepare(LONG).prepared
        audio,rate,starts=prepared
        self.assertEqual(len(LONG.split()),len(starts))
        self.assertEqual(starts,sorted(starts))
        index=0
        for number,unit in enumerate(self.tts.units):
            if unit.split(): self.assertAlmostEqual(number,starts[index])
            index+=len(unit.split())
        self.assertEqual(len(self.tts.units)*24000,len(audio))

    def test_continuous_queue_plays_all_prepared_units_once_in_order(self):
        from tts import tts as owner
        captured,started,finished=[],[],[]
        self.tts.set_playback_started_callback(lambda duration,envelope,words,pid:started.append(pid))
        self.tts.set_playback_finished_callback(finished.append)
        streams=[]
        class Output:
            def __init__(self,**kwargs):
                self.callback=kwargs['callback'];self.active=False;self.worker=None
                streams.append(self)
            def start(self):
                self.active=True
                def pump():
                    try:
                        while self.active:
                            data=np.zeros((64,1),dtype=np.float32)
                            try: self.callback(data,64,None,None)
                            finally: captured.extend(data.reshape(-1))
                            threading.Event().wait(.001)
                    except owner.sd.CallbackStop: self.active=False
                self.worker=threading.Thread(target=pump,daemon=True);self.worker.start()
            def abort(self): self.active=False
            def stop(self): self.active=False
            def close(self):
                self.active=False
                if self.worker: self.worker.join(1)
        done=threading.Event()
        with patch.object(owner.sd,'OutputStream',Output):
            queue=StreamingSpeechQueue(self.tts,on_complete=lambda _:done.set())
            try:
                queue.submit(LONG);queue.submit('Final clear sentence.');queue.close()
                self.assertTrue(done.wait(3));queue.join(1)
                self.assertFalse(queue._synthesis_thread.is_alive())
                self.assertFalse(queue._playback_thread.is_alive())
                self.assertEqual(LONG+'Final clear sentence.', ''.join(chr(int(v)) for v in captured if v))
                self.assertEqual(1,len(streams));self.assertEqual(1,len(set(started)))
                self.assertEqual(2,len(started));self.assertEqual(started[-1:],finished)
            finally: queue.cancel();queue.join(1)

    def test_old_queue_cleanup_cannot_stop_replacement_playback(self):
        old=StreamingSpeechQueue(self.tts)
        old.cancel();old.join(1)
        replacement=self.turn(response='Fresh playback remains owned.').result(3)
        self.assertTrue(replacement.succeeded)
        active=self.tts.active_playback_generation
        self.assertIsNotNone(active)
        old.cancel()  # Late completion/exception cleanup of the already retired queue.
        self.assertEqual(active,self.tts.active_playback_generation)
        self.assertFalse(self.tts.stop_event.is_set())


# Reuse eligible production Open Thread setup, substituting only native kernels/audio.
import test_proactive_governance as proactive

class ProactiveCancellableSynthesisTests(proactive._ProactiveFixture):
    def setUp(self):
        audio=ControlledKokoro()
        audio.release_synthesis=audio.release[0]
        with patch.object(proactive,'GatedAudio',return_value=audio): super().setUp()
        self.service.responsive_speech = False
        self.addCleanup(audio.unblock)

    def test_proactive_commit_survives_cancel_without_consuming_tail(self):
        self.tts.block={0,1}
        old=self.start(self.proactive)
        self.assertTrue(self.tts.entered[0].wait(2))
        self.finish(self.start(self.service.push_to_talk_press))
        self.tts.release[0].set()
        self.assert_published(self.finish(old))
        self.assertEqual([],self.tts.dispatched)
        self.assertEqual(1,len(self.tts.kernels))
        self.service.push_to_talk_release()
        self.tts.block=set()
        self.provider.draft='A fresh ordinary reply.'
        following=self.service.process_text_turn('Hello again.')
        self.assertTrue(following.succeeded,following.error)
        self.assertEqual(['A fresh ordinary reply.'],self.tts.dispatched)
