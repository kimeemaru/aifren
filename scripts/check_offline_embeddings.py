"""Run the real MiniLM synthetic quality assertion without network or GPU use."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models/all-MiniLM-L6-v2')
    args = parser.parse_args()
    model = args.model_dir.resolve()
    if not (model / 'config.json').is_file():
        parser.error('Install MiniLM separately or supply --model-dir; this check never downloads it.')
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT)]
    for name in tuple(os.environ):
        if name.startswith('AIFREN_') or name in {'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'}:
            del os.environ[name]
    with tempfile.TemporaryDirectory(prefix='companion-minilm-') as temp:
        os.chdir(temp)
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          HF_HUB_DISABLE_TELEMETRY='1', CUDA_VISIBLE_DEVICES='',
                          HF_HOME=str(Path(temp) / 'hf'), XDG_CACHE_HOME=str(Path(temp) / 'cache'))
        with ExitStack() as stack:
            stack.enter_context(patch('memory.embeddings.MODEL_DIR', str(model)))
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network excluded')))
            stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Network excluded')))
            suite = unittest.defaultTestLoader.loadTestsFromName(
                'test_memory_v2_semantic_telemetry.MemoryV2SemanticTelemetryTests.'
                'test_semantic_fixture_enforces_lifecycle_scope_and_abstention')
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
