#!/usr/bin/env python3
"""Benchmark persistent Audio8 CUDA inference without packaging local voice data."""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
import types
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoProcessor


DEFAULT_CHUNKS = (
    "I can hear you clearly, and I am ready to help.",
    "The next sentence is prepared while the first one is playing.",
    "Short conversational chunks should remain clean and continuous.",
    "This final sample verifies that the resident model keeps its pace.",
)


def timed_cuda(call):
    torch.cuda.synchronize()
    started = time.perf_counter()
    result = call()
    torch.cuda.synchronize()
    return result, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--reference-audio", type=Path, required=True)
    parser.add_argument("--reference-text-env", default="AIFREN_AUDIO8_REFERENCE_TEXT")
    parser.add_argument("--warmup-text", default="The voice system is warmed up and ready for conversation.")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reduced-semantic-projection", action="store_true")
    parser.add_argument("text", nargs="*", default=list(DEFAULT_CHUNKS))
    args = parser.parse_args()

    reference_text = os.environ.get(args.reference_text_env, "").strip()
    if not reference_text:
        raise RuntimeError(f"{args.reference_text_env} must contain the approved reference transcript")
    if not args.reference_audio.is_file():
        raise FileNotFoundError(args.reference_audio)

    device = torch.device("cuda")
    dtype = torch.bfloat16
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(str(args.model), trust_remote_code=True)
    processor_seconds = time.perf_counter() - started
    started = time.perf_counter()
    model = AutoModel.from_pretrained(
        str(args.model), trust_remote_code=True, dtype=dtype
    ).eval().to(device)
    torch.cuda.synchronize()
    model_seconds = time.perf_counter() - started
    model_load_count = 1

    if args.reduced_semantic_projection:
        # The stock preview projects every audio frame to the full 155,776
        # token text vocabulary and masks almost all of it afterward. Mirror
        # Audio8's optimized server: project only EOS + 4,096 semantic codes,
        # then map the sampled local index back to its canonical token id.
        def reduced_slow_step(self, input_ids, cache_position, position_ids, attention_mask):
            hidden = self._embed(input_ids)
            rope = self.freqs_cis[position_ids]
            mask = self._causal_mask(attention_mask, cache_position, self.config.max_seq_len)
            for layer in self.layers:
                hidden = layer(hidden, rope, mask, cache_position)
            hidden = hidden[:, -1:]
            normalized = self.norm(hidden)
            weights = torch.cat((
                self.embeddings.weight[self.config.eos_token_id:self.config.eos_token_id + 1],
                self.embeddings.weight[self.config.semantic_begin_id:self.config.semantic_end_id + 1],
            ), dim=0)
            logits = F.linear(normalized, weights)[:, -1]
            fast_hidden = normalized if self.config.norm_fastlayer_input else hidden
            return logits, fast_hidden

        def reduced_sample_semantic(
            self, history, logits, custom_processors, top_k, top_p, temperature,
            previous, do_sample, generator=None,
        ):
            if len(custom_processors):
                raise RuntimeError("reduced benchmark does not support custom logits processors")
            regular_scores = self._processed_scores(
                history, logits, custom_processors, top_k, top_p, temperature
            )
            normal_local = regular_scores.argmax(dim=-1) if not do_sample else self._sample(
                regular_scores, generator=generator
            )
            normal = torch.where(
                normal_local == 0,
                torch.full_like(normal_local, self.config.eos_token_id),
                normal_local - 1 + self.config.semantic_begin_id,
            )
            if not do_sample:
                return normal
            high_scores = self._processed_scores(
                history, logits, custom_processors, top_k,
                self.config.ras_top_p, self.config.ras_temperature,
            )
            high_local = self._sample(high_scores, generator=generator)
            high = torch.where(
                high_local == 0,
                torch.full_like(high_local, self.config.eos_token_id),
                high_local - 1 + self.config.semantic_begin_id,
            )
            if previous is None:
                return normal
            repeated = (previous == normal[:, None]).any(dim=1)
            semantic = (normal >= self.config.semantic_begin_id) & (normal <= self.config.semantic_end_id)
            return torch.where(repeated & semantic, high, normal)

        model._slow_step = types.MethodType(reduced_slow_step, model)
        model._sample_semantic = types.MethodType(reduced_sample_semantic, model)

    reference_input = processor(
        text=args.warmup_text,
        reference_audio=args.reference_audio,
        reference_text=reference_text,
        return_tensors="pt",
    )
    (reference_codes, reference_lengths), condition_seconds = timed_cuda(
        lambda: model.encode_audio(
            reference_input["reference_audio_values"].to(device),
            reference_input["reference_audio_lengths"].to(device),
        )
    )
    voice_condition_count = 1
    reference_length = int(reference_lengths[0])
    cached_reference_codes = reference_codes[0, :, :reference_length].cpu()
    sample_rate = int(model.config.codec_sample_rate)
    generator = torch.Generator(device=device).manual_seed(args.seed)

    def synthesize(text: str) -> dict[str, float | int | bool]:
        inputs = processor(
            text=text,
            reference_codes=cached_reference_codes,
            reference_text=reference_text,
            return_tensors="pt",
        )
        inputs = {name: value.to(device) for name, value in inputs.items()}
        output, generation_seconds = timed_cuda(lambda: model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            temperature=.8,
            top_p=.95,
            top_k=50,
            do_sample=True,
            generator=generator,
            return_dict_in_generate=True,
        ))
        (waveforms, lengths), decode_seconds = timed_cuda(
            lambda: model.decode_audio(output.codes)
        )
        length = int(lengths[0])
        waveform = waveforms[0, :length].float().cpu().numpy()
        synthesis_seconds = generation_seconds + decode_seconds
        duration_seconds = length / sample_rate
        peak = float(np.max(np.abs(waveform))) if length else 0.0
        rms = float(np.sqrt(np.mean(np.square(waveform)))) if length else 0.0
        dc = float(np.mean(waveform)) if length else 0.0
        max_adjacent_step = float(np.max(np.abs(np.diff(waveform)))) if length > 1 else 0.0
        clipped_fraction = float(np.mean(np.abs(waveform) >= .999)) if length else 0.0
        return {
            "text_chars": len(text),
            "finished": bool(output.finished[0]),
            "code_frames": int(output.code_lengths[0]),
            "generation_seconds": generation_seconds,
            "decode_seconds": decode_seconds,
            "synthesis_seconds": synthesis_seconds,
            "audio_seconds": duration_seconds,
            "rtf": synthesis_seconds / duration_seconds if duration_seconds else float("inf"),
            "peak": peak,
            "rms": rms,
            "dc": dc,
            "max_adjacent_step": max_adjacent_step,
            "clipped_fraction": clipped_fraction,
        }

    warmup = synthesize(args.warmup_text)
    synthesis_count = 0
    results = []
    for text in args.text:
        results.append(synthesize(text))
        synthesis_count += 1

    report = {
        "device": torch.cuda.get_device_name(device),
        "dtype": str(dtype),
        "reduced_semantic_projection": args.reduced_semantic_projection,
        "processor_load_seconds": processor_seconds,
        "model_load_seconds": model_seconds,
        "voice_condition_seconds": condition_seconds,
        "warmup": warmup,
        "syntheses": results,
        "counts": {
            "model_load_count": model_load_count,
            "voice_condition_count": voice_condition_count,
            "warmup_count": 1,
            "synthesis_count": synthesis_count,
        },
        "cuda_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1048576,
        "cuda_peak_reserved_mib": torch.cuda.max_memory_reserved() / 1048576,
        "process_max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
