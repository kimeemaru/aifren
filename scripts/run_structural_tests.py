"""Public synthetic unittest discovery; no installed character/model is test truth."""
from contextlib import ExitStack, redirect_stdout
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
QUALITY_CASE = ('test_memory_v2_semantic_telemetry.MemoryV2SemanticTelemetryTests.'
                'test_semantic_fixture_enforces_lifecycle_scope_and_abstention')


def cases(group):
    for item in group:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory for isolated state and reports')
    parser.add_argument('modules', nargs='*', help='Optional test module names')
    args = parser.parse_args()
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='companion-structural-'))
    if args.output:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT)]
    os.chdir(output)
    for name in tuple(os.environ):
        if name.startswith('AIFREN_') or name in {'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'}:
            del os.environ[name]
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', CUDA_VISIBLE_DEVICES='',
                      PYNPUT_BACKEND='dummy', HF_HOME=str(output / 'hf'),
                      XDG_CACHE_HOME=str(output / 'cache'))
    from test_character_memory_v2_shadow import _Embedding
    from test_memory_v2_embeddings import ToyEmbeddingProvider
    from aifren.runtime.development_flight_recorder import DevelopmentFlightRecorder
    from aifren.runtime.local_model_runtime import LocalModelRuntime

    class SyntheticEmbeddingModel:
        def __init__(self):
            self.model = self
            self.device = 'cpu'
        def get_embedding_dimension(self):
            return ToyEmbeddingProvider.dimensions
        def encode(self, texts, **_kwargs):
            return ToyEmbeddingProvider().embed(texts)

    for name in ('aging_manifest.json', 'router_intent_manifest.json',
                 'durable_core_multiplicity_manifest.json'):
        relative = Path('benchmarks/memory_v2') / name
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    with ExitStack() as stack:
        stack.enter_context(patch('aifren.memory.memory.EmbeddingModel', _Embedding))
        stack.enter_context(patch('aifren.memory.embeddings.EmbeddingModel', SyntheticEmbeddingModel))
        stack.enter_context(patch.object(DevelopmentFlightRecorder, '_gpu_sample', return_value={}))
        stack.enter_context(patch.object(LocalModelRuntime, '_probe_gpu_offload', return_value=False))
        with (output / 'test-output.txt').open('w') as stdout, \
                (output / 'unittest.txt').open('w') as log, redirect_stdout(stdout):
            selected = list(cases(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
            if args.modules:
                missing = set(args.modules) - {t.id().split('.')[0] for t in selected}
                if missing:
                    raise ValueError('Test modules not discovered: ' + ', '.join(sorted(missing)))
                selected = [t for t in selected if t.id().split('.')[0] in args.modules]
            structural = [t for t in selected if t.id() != QUALITY_CASE]
            (output / 'test-ids.txt').write_text('\n'.join(t.id() for t in structural) + '\n')
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite(structural))
    print('Public structural reports:', output)
    print('Ran:', result.testsRun, 'failures:', len(result.failures),
          'errors:', len(result.errors), 'skipped:', len(result.skipped))
    print('Real MiniLM quality is separate; run scripts/check_offline_embeddings.py.')
    for test, trace in result.failures + result.errors:
        print(test.id(), '\n', '\n'.join(trace.splitlines()[-14:]))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
