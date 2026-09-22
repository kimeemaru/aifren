"""Strict NVIDIA deployment checks; all records, transports and audio synthetic."""
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from aifren.runtime.config import configured_inference_device, require_torch_device, InferenceDeviceUnavailable
from scripts.launch_friend import FriendPackageLayout, package_environment, prepare_packaged_phonemizer


class GpuTesterProfileTests(unittest.TestCase):
    def test_profile_is_explicit_and_preserves_data_route_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'package.json').write_text(json.dumps({'runtime_profile':'nvidia-stock-v1'}))
            layout = FriendPackageLayout.from_package_root(root)
            with patch.dict(os.environ, {'AIFREN_INFERENCE_DEVICE':'cpu','AIFREN_DATA_ROOT':'unrelated',
                                         'AIFREN_ENGLISH_G2P':'espeak','SD_ENABLE_ASIO':'1'}):
                result = package_environment(layout,root/'UserData')
            self.assertEqual('cuda',result['AIFREN_INFERENCE_DEVICE'])
            self.assertEqual('cuda',result['AIFREN_KOKORO_DEVICE'])
            self.assertEqual('flite',result['AIFREN_ENGLISH_G2P'])
            self.assertEqual('1',result['AIFREN_STT_PCM_ONLY'])
            self.assertEqual(str(root/'UserData'),result['AIFREN_DATA_ROOT'])
            self.assertNotIn('SD_ENABLE_ASIO',result)

    def test_unavailable_gpu_is_visible_without_cpu_fallback(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with self.assertRaisesRegex(InferenceDeviceUnavailable,'NVIDIA CUDA'):
            require_torch_device(torch,'cuda')
        require_torch_device(torch,'cpu')
        with patch.dict(os.environ, {'AIFREN_INFERENCE_DEVICE':'unknown'}):
            with self.assertRaises(ValueError): configured_inference_device()

    def test_stock_startup_never_imports_legacy_phonemizer(self):
        import builtins
        original = builtins.__import__
        def guarded(name,*args,**kwargs):
            if name in {'espeakng_loader','phonemizer','misaki.espeak'}:
                raise AssertionError('Legacy pronunciation imported')
            return original(name,*args,**kwargs)
        with patch.dict(os.environ, {'AIFREN_ENGLISH_G2P':'flite'}),patch('builtins.__import__',guarded):
            self.assertIsNone(prepare_packaged_phonemizer())

    def test_kokoro_strict_device_never_moves_failed_model_to_cpu(self):
        from aifren.tts.tts import KokoroTextToSpeech
        provider = KokoroTextToSpeech.__new__(KokoroTextToSpeech)
        provider._allow_cpu_fallback = False
        provider.pipeline = Mock()
        self.assertFalse(provider.fallback_to_cpu_after_resource_failure())
        provider.pipeline.model.to.assert_not_called()

    def test_pcm_input_preserves_exact_samples_and_no_decoder(self):
        import numpy as np
        from aifren.stt.stt import SpeechToText
        samples = np.array([-32768,-16384,0,8192,32767],dtype='<i2')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'capture.wav'
            with wave.open(str(path),'wb') as output:
                output.setparams((1,2,16000,0,'NONE','not compressed'))
                output.writeframes(samples.tobytes())
            model = Mock()
            model.transcribe.return_value = ([SimpleNamespace(text=' Amber. ')],None)
            with patch.dict(os.environ,{'AIFREN_STT_PCM_ONLY':'1'}):
                self.assertEqual('Amber.',SpeechToText._transcribe_once(model,path))
            np.testing.assert_array_equal(samples.astype(np.float32)/32768,
                                          model.transcribe.call_args.args[0])
            with wave.open(str(path),'wb') as output:
                output.setparams((2,2,16000,0,'NONE','not compressed'))
                output.writeframes(b'\0'*20)
            with patch.dict(os.environ,{'AIFREN_STT_PCM_ONLY':'1'}),self.assertRaises(ValueError):
                SpeechToText._transcribe_once(model,path)
            self.assertEqual(1,model.transcribe.call_count)

    def test_gpu_stt_failure_does_not_retry_or_reload_cpu(self):
        from aifren.stt.stt import SpeechToText
        import threading
        stt = SpeechToText.__new__(SpeechToText)
        stt._model_lock=threading.Lock();stt._device='cuda';stt._allow_cpu_fallback=False
        stt.model=Mock();stt.model.transcribe.side_effect=RuntimeError('CUDA out of memory')
        stt._switch_to_cpu=Mock()
        with self.assertRaisesRegex(RuntimeError,'CUDA out of memory'):stt.transcribe('synthetic.wav')
        stt._switch_to_cpu.assert_not_called()
        self.assertEqual(1,stt.model.transcribe.call_count)

    def test_gpu_failure_keeps_character_manager_and_records(self):
        from aifren.character.character_registry import CharacterRegistry
        from aifren.backend_host import AIFrenWebSocketHost
        with tempfile.TemporaryDirectory() as directory:
            registry=CharacterRegistry(directory)
            character=registry.create('Synthetic GPU check')
            registry.select(character.character_id)
            paths=registry.runtime_paths(character.character_id)
            before=Path(paths['conversation']).read_bytes()
            factory=Mock(side_effect=InferenceDeviceUnavailable('NVIDIA CUDA is unavailable.'))
            host=AIFrenWebSocketHost(application_dir=directory,service_factory=factory)
            service=host._create_service_or_management()
            self.assertEqual(character.character_id,service.character_id)
            self.assertEqual('runtime_unavailable',host._status['state'])
            self.assertEqual(before,Path(paths['conversation']).read_bytes())
            self.assertEqual(character.character_id,host._registry().active().character_id)

    def test_strict_chat_requires_real_complete_offload(self):
        from aifren.runtime.local_model_runtime import LocalModelRuntime
        runtime=LocalModelRuntime.__new__(LocalModelRuntime)
        import threading
        runtime._lock=threading.RLock();runtime._gpu_offload_event=threading.Event()
        runtime._gpu_offload_confirmed=False
        runtime._status=SimpleNamespace(ownership='managed',compute='GPU offload requested')
        runtime._log=lambda *_:None
        for output,accepted in [('offloaded 0/33 layers to GPU',False),
                                ('offloaded 20/33 layers to GPU',False),
                                ('offloaded 33/33 layers to GPU',True)]:
            runtime._gpu_offload_confirmed=False;runtime._gpu_offload_event.clear()
            process=SimpleNamespace(stdout=io.StringIO(output+'\n'))
            runtime._process=process
            with patch.dict(os.environ,{'AIFREN_INFERENCE_DEVICE':'cuda'}):runtime._drain_output(process)
            self.assertEqual(accepted,runtime._gpu_offload_confirmed)

    def test_gpu_expression_never_loads_cpu_quantized_graph_when_missing(self):
        from aifren.dialogue.automatic_expression import CpuExpressionClassifier
        with tempfile.TemporaryDirectory() as directory:
            model=CpuExpressionClassifier(directory,device='cuda')
            self.assertFalse(model.load())
            self.assertIsNone(model._session)
            self.assertEqual('cuda',model.status()['device'])
            self.assertEqual('model_missing_or_invalid',model.status()['state'])


if __name__=='__main__':unittest.main()
