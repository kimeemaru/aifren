"""Native headless reconstruction check; does not certify GPU/audio/desktop use."""
from pathlib import Path
import importlib
import json
import os
import sys
import unittest


def check(root, source):
    root, source = Path(root).resolve(), Path(source).resolve()
    os.environ.update(AIFREN_DATA_ROOT=str(root / 'synthetic-data'),
                      AIFREN_RESOURCE_ROOT=str(root / 'runtime/app'),
                      AIFREN_ENGLISH_G2P='flite', AIFREN_STT_PCM_ONLY='1',
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      PYNPUT_BACKEND='dummy')
    from scripts.launch_friend import prepare_packaged_runtime
    prepare_packaged_runtime()
    for name in ('numpy', 'hnswlib', 'torch', 'llama_cpp', 'kokoro',
                 'faster_whisper', 'onnxruntime', 'cupy', 'sounddevice',
                 'pynput', 'aifren.assistant_service'):
        module = importlib.import_module(name)
        assert Path(module.__file__).is_relative_to(root), name
        print('Bundled import:', name)
    import torch
    import llama_cpp
    import onnxruntime
    import sounddevice
    assert torch.version.cuda == '12.8', torch.version.cuda
    assert llama_cpp.llama_supports_gpu_offload()
    assert 'CUDAExecutionProvider' in onnxruntime.get_available_providers()
    assert isinstance(sounddevice.query_hostapis(), tuple)
    for name in ('av', 'phonemizer', 'espeakng_loader'):
        assert importlib.util.find_spec(name) is None, name
    assert 'misaki.espeak' not in sys.modules
    from aifren.tts.flite_lts import FliteEnglishFallback
    from misaki.en import G2P
    fallback = FliteEnglishFallback(root/'runtime/app/models/english-lts/cmu_lts.json')
    g2p = G2P(trf=False, british=False, fallback=fallback)
    fallback.lexicon = g2p.lexicon
    for text in ('Hello, Xanthe. We study nanophotonics.',
                 'That costs $250.75 for 12 items in 2026.',
                 'Meet at 12:30; compare 3.14 with 31.4!'):
        phones, tokens = g2p(text)
        assert phones and all(t.phonemes for t in tokens if any(c.isalnum() for c in t.text))
    print('English fallback/number coverage passed; no acoustic claim')
    print(json.dumps({'torch_cuda_build': torch.version.cuda,
                      'cuda_device_available': torch.cuda.is_available(),
                      'llama_cuda_build': True, 'ort_cuda_build': True,
                      'gpu_inference': 'NOT RUN', 'audio_playback': 'NOT RUN'}))
    sys.path[:0] = [str(source/'tests'), str(source)]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
        'test_literal_preference_recall', 'test_portable_file_lock',
        'test_gpu_tester_profile', 'test_flite_lts'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert all(Path(m.__file__).is_relative_to(root) for n, m in sys.modules.items()
               if n.startswith('aifren.') and getattr(m, '__file__', None))
    return result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(0 if check(*sys.argv[1:]) else 1)
