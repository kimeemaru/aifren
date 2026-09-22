"""Stock runtime preparation is bounded to reviewed, disposable inputs."""
from pathlib import Path
import tempfile
import unittest
from scripts.prepare_stock_runtime import prune, prepare, CUDA_RUNTIME_HEADERS


class StockRuntimePreparationTests(unittest.TestCase):
    def test_unreviewed_source_fails_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'site/kokoro/pipeline.py'
            target.parent.mkdir(parents=True)
            target.write_text('original = True\n')
            with self.assertRaisesRegex(ValueError, 'reviewed upstream'):
                prepare(root/'site', root/'materials')
            self.assertEqual('original = True\n', target.read_text())
            self.assertFalse((root/'materials').exists())

    def test_prune_keeps_runtime_licenses_and_only_reviewed_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            removed = ('torch/include/unused.h','torch/share/cmake/config.cmake',
                       'cupy/_core/include/cupy/_cuda/cuda-11/cuda_fp16.h',
                       'nvidia/cuda_runtime/include/cuda_runtime.h',
                       '_sounddevice_data/portaudio-binaries/libportaudio64bit-asio.dll')
            kept = ('torch/lib/runtime.dll','torch-2.7.0.dist-info/licenses/LICENSE',
                    'nvidia/cuda_runtime/include/cuda_fp16.h',
                    'cupy/.data/_wheel.json',
                    '_sounddevice_data/portaudio-binaries/libportaudio64bit.dll')
            for name in (*removed,*kept):
                path=site/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic')
            prune(site)
            self.assertTrue(all(not (site/name).exists() for name in removed))
            self.assertTrue(all((site/name).is_file() for name in kept))
            self.assertNotIn('cuda_runtime.h',CUDA_RUNTIME_HEADERS)
            self.assertNotIn('driver_types.h',CUDA_RUNTIME_HEADERS)


if __name__=='__main__': unittest.main()
