"""The real worker pipe/lifecycle against an independently synthetic upstream API.

The reviewed engine saves its inference configuration during model initialization
and recovery. This stand-in deliberately writes at every stage to check ownership;
it is not real-model synthesis or acoustic evidence.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from aifren.tts.clone_worker import prepare_conditioning


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "aifren/tts/clone_worker.py"


class CloneWorkerIsolationTests(unittest.TestCase):
    def test_prepare_populates_transcript_cache_without_synthesizing(self):
        tts = Mock()
        tts.configs = SimpleNamespace(languages=["en", "all_ja"], version="v2ProPlus")
        tts.prompt_cache = {}
        tts.text_preprocessor.segment_and_extract_feature_for_text.return_value = ([1, 2], "features", "normalized")
        request = dict(reference="synthetic.wav", transcript="\nSynthetic reference\n", language="en")
        prepare_conditioning(tts, request, {".", "!", "?", "。"})
        tts.set_ref_audio.assert_called_once_with("synthetic.wav")
        tts.text_preprocessor.segment_and_extract_feature_for_text.assert_called_once_with(
            "Synthetic reference.", "en", "v2ProPlus")
        self.assertEqual(dict(prompt_text="Synthetic reference.", prompt_lang="en",
                              phones=[1, 2], bert_features="features", norm_text="normalized"), tts.prompt_cache)
        tts.run.assert_not_called()
        # Same text with another language must not reuse the previous language's features.
        request["language"] = "all_ja"
        prepare_conditioning(tts, request, {".", "!", "?", "。"})
        self.assertEqual("all_ja", tts.prompt_cache["prompt_lang"])
        tts.text_preprocessor.segment_and_extract_feature_for_text.assert_called_with(
            "Synthetic reference。", "all_ja", "v2ProPlus")

    def test_failed_transcript_preparation_does_not_claim_a_populated_cache(self):
        tts = Mock()
        tts.configs = SimpleNamespace(languages=["en"], version="v2ProPlus")
        tts.prompt_cache = {"prompt_text": "old reference"}
        tts.text_preprocessor.segment_and_extract_feature_for_text.side_effect = ValueError("synthetic error")
        with self.assertRaises(ValueError):
            prepare_conditioning(tts, dict(reference="synthetic.wav", transcript="Test!", language="en"), {"!"})
        self.assertIsNone(tts.prompt_cache["prompt_text"])
        tts.run.assert_not_called()

    def test_full_lifecycle_preserves_modified_upstream_config_and_retires_temporary_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream"
            api = upstream / "GPT_SoVITS/TTS_infer_pack"
            api.mkdir(parents=True)
            (api / "__init__.py").write_text("")
            (api / "text_segmentation_method.py").write_text("splits = {'.', '!', '?', '。'}\n")
            config = upstream / "GPT_SoVITS/configs/tts_infer.yaml"
            config.parent.mkdir()
            original = b"# pre-existing independently modified configuration\ncustom: synthetic\n"
            config.write_bytes(original)
            (upstream / "torch.py").write_text(
                "def set_num_threads(value): pass\ndef set_num_interop_threads(value): pass\n")
            (api / "TTS.py").write_text('''
from pathlib import Path
from types import SimpleNamespace
import numpy as np
class TTS_Config:
    def __init__(self, values):
        self.configs_path = 'GPT_SoVITS/configs/tts_infer.yaml'
class TTS:
    def __init__(self, configuration):
        self.config = configuration
        self.configs = SimpleNamespace(languages=['en'], version='v2ProPlus')
        self.text_preprocessor = self
        self.prompt_cache = {}
        Path('observed-path.txt').write_text(configuration.configs_path)
        self.save('initialize')
    def save(self, stage):
        Path(self.config.configs_path).write_text(stage)
    def set_ref_audio(self, path):
        self.save('prepare')
    def segment_and_extract_feature_for_text(self, text, language, version):
        self.save('transcript')
        return [1], 'features', text
    def run(self, inputs):
        assert self.prompt_cache['prompt_text'] == inputs['prompt_text']
        self.save('synthesize')
        if inputs['text'] == 'Controlled failure.':
            self.save('recovery')
            raise RuntimeError('synthetic failure')
        yield 24000, np.array([0, 1000, -1000], dtype=np.int16)
''')
            install = root / "installation.json"
            install.write_text(json.dumps({"root": str(upstream)}))
            process = subprocess.Popen([sys.executable, "-B", str(WORKER),
                                        "--installation", str(install), "--instance", "synthetic-instance"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Bounded communicate prevents a broken frame/lifecycle from hanging the suite.
            requests = [
                {"id": "prepare", "op": "prepare", "key": "one", "reference": "synthetic.wav",
                 "language": "en", "transcript": "Synthetic reference."},
                {"id": "speech", "op": "synthesize", "key": "one", "reference": "synthetic.wav",
                 "language": "en", "transcript": "Synthetic reference.", "text": "Hello."},
                {"id": "failure", "op": "synthesize", "key": "two", "reference": "other.wav",
                 "language": "en", "transcript": "Other reference.", "text": "Controlled failure."},
                {"id": "reprepare", "op": "prepare", "key": "two", "reference": "other.wav",
                 "language": "en", "transcript": "Other reference."},
                {"op": "close"},
            ]
            try:
                output, errors = process.communicate(
                    b"".join(json.dumps(value).encode() + b"\n" for value in requests), timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("Synthetic worker did not complete its bounded lifecycle")
            self.assertEqual(0, process.returncode, errors.decode(errors="replace"))
            frames = []
            while output:
                header, output = output.split(b"\n", 1)
                frame = json.loads(header)
                self.assertEqual("synthetic-instance", frame["instance"])
                length = frame["bytes"]
                self.assertGreaterEqual(len(output), length)
                frames.append((frame, output[:length]))
                output = output[length:]
            self.assertEqual(["ready", "ready", "ready", "failed", "ready"],
                             [frame["state"] for frame, _ in frames])
            self.assertEqual(12, len(frames[2][1]))
            self.assertEqual([None, "prepare", "speech", "failure", "reprepare"],
                             [frame.get("id") for frame, _ in frames])
            self.assertEqual(hashlib.sha256(original).digest(), hashlib.sha256(config.read_bytes()).digest())
            generated = Path((upstream / "observed-path.txt").read_text())
            self.assertFalse(generated.is_relative_to(upstream))
            self.assertFalse(generated.parent.exists(), "Worker left its generated configuration behind")
