"""Private-pipe worker for the reviewed, separately installed GPT-SoVITS runtime.

This process loads only a locally registered pinned installation. Upstream code
and model objects stay in its own environment; no conditioning object is pickled
by AIFren. Standard output is reserved for bounded protocol frames.
"""
from __future__ import annotations
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

MAX_REQUEST = 40000
MAX_PCM = 32 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installation", required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.installation).read_text(encoding="utf-8"))
    root = Path(config["root"]).resolve()
    # Deliberate external dependency boundary, confined to this worker. It does
    # not add a data root or old application checkout to the backend imports.
    os.chdir(root)
    sys.path[:0] = [str(root), str(root / "GPT_SoVITS")]
    os.environ["NLTK_DATA"] = str(root / ".nltk_data")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    output = sys.stdout.buffer

    def send(value, pcm=b""):
        value.update(instance=args.instance, bytes=len(pcm))
        output.write(json.dumps(value).encode() + b"\n")
        output.write(pcm)
        output.flush()

    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import torch
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        from TTS_infer_pack.TTS import TTS, TTS_Config
        start = time.monotonic()
        models = root / "GPT_SoVITS/pretrained_models"
        configuration = TTS_Config({"custom": {
            "device": "cpu", "is_half": False, "version": "v2ProPlus",
            "t2s_weights_path": str(models / "s1v3.ckpt"),
            "vits_weights_path": str(models / "v2Pro/s2Gv2ProPlus.pth"),
            "cnhuhbert_base_path": str(models / "chinese-hubert-base"),
            "bert_base_path": str(models / "chinese-roberta-wwm-ext-large"),
        }})
        # Upstream model initialization saves its config. Keep that generated
        # file in this worker's temporary directory, never the installation.
        config_temp = tempfile.TemporaryDirectory(prefix="aifren-clone-worker-")
        configuration.configs_path = str(Path(config_temp.name) / "inference.yaml")
        tts = TTS(configuration)
    send({"state": "ready", "seconds": time.monotonic() - start})
    key = None
    while line := sys.stdin.buffer.readline(MAX_REQUEST + 1):
        if len(line) > MAX_REQUEST:
            return 2
        request = json.loads(line)
        if request.get("op") == "close":
            return 0
        started = time.monotonic()
        try:
            with contextlib.redirect_stdout(sys.stderr):
                if key != request["key"]:
                    tts.set_ref_audio(request["reference"])
                    # Upstream caches prompt text independently of language.
                    # Invalidate it when any conditioning input changes.
                    tts.prompt_cache["prompt_text"] = None
                    key = request["key"]
                if request["op"] == "prepare":
                    send({"id": request["id"], "state": "ready", "seconds": time.monotonic()-started})
                    continue
                text = request["text"]
                if not isinstance(text, str) or not 1 <= len(text) <= 8000:
                    raise ValueError("invalid text")
                chunks = []
                for rate, audio in tts.run({
                    "text": text, "text_lang": request["language"],
                    "ref_audio_path": None, "prompt_text": request["transcript"],
                    "prompt_lang": request["language"], "text_split_method": "cut5",
                    "batch_size": 8, "parallel_infer": True, "streaming_mode": False,
                    "split_bucket": True, "fragment_interval": 0.0,
                }):
                    values = np.asarray(audio)
                    if values.dtype == np.int16:
                        values = values.astype(np.float32) / 32768.0
                    chunks.append(values.astype("<f4").reshape(-1))
                    if sum(chunk.nbytes for chunk in chunks) > MAX_PCM:
                        raise ValueError("audio work bound")
                samples = np.concatenate(chunks)
                if not len(samples) or not np.isfinite(samples).all():
                    raise ValueError("invalid audio")
                send({"id": request["id"], "state": "ready", "rate": rate,
                      "seconds": time.monotonic()-started}, samples.tobytes())
        except Exception:
            # No private text, reference paths or upstream error payloads.
            key = None
            send({"id": request.get("id"), "state": "failed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
