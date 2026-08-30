#!/usr/bin/env python3
"""Run matched-seed historical context regressions without product mutations."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.context_regression.harness import (
    ContextArm,
    Fixture,
    HistoricalContextBuilder,
    PreparedArm,
    aggregate_results,
    analyze_response,
    blinded_labels,
    character_prompt_from_paths,
    load_manifest,
    shuffled_arm_order,
)
from character_registry import CharacterRegistry
from llm.llm import create_llm
from llm.output_canonicalization import canonicalize_model_output
from presentation_metadata import parse_assistant_response


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _archive_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ResourceSampler:
    """Low-rate process/system/GPU sanity sampler for an offline run."""

    def __init__(self, model_hint: str, hz: float = 0.5) -> None:
        self.model_hint = model_hint.casefold()
        self.interval = 1.0 / max(0.2, hz)
        self.samples: list[dict[str, float | int | None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._nvml = None
        self._handle = None
        self._pid = self._find_llama_pid()
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self._nvml = None
            self._handle = None

    def _find_llama_pid(self) -> int | None:
        try:
            import psutil
            candidates = []
            for process in psutil.process_iter(("pid", "cmdline", "create_time")):
                command = " ".join(process.info.get("cmdline") or ()).casefold()
                if "llama_cpp.server" in command and (not self.model_hint or self.model_hint in command):
                    candidates.append((float(process.info.get("create_time") or 0.0), int(process.info["pid"])))
            return max(candidates)[1] if candidates else None
        except Exception:
            pass
        # Keep the harness dependency-free inside the project venv.
        for path in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                command = path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").casefold()
                if "llama_cpp.server" in command and (not self.model_hint or self.model_hint in command):
                    return int(path.parent.name)
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def _proc_status_kib(pid: int, field: str) -> int | None:
        try:
            for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
                if line.startswith(field + ":"):
                    return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return None

    @staticmethod
    def _meminfo() -> dict[str, int]:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                name, raw = line.split(":", 1)
                values[name] = int(raw.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return values

    def _nvidia_smi(self) -> dict[str, float | int]:
        values: dict[str, float | int] = {}
        try:
            gpu = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2.0, check=True,
            ).stdout.splitlines()[0].split(",")
            values.update(gpu_utilization=float(gpu[0].strip()), vram_used_bytes=int(gpu[1].strip()) * 1024 * 1024)
            if self._pid is not None:
                processes = subprocess.run(
                    ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2.0, check=True,
                ).stdout.splitlines()
                for line in processes:
                    pid, used = (part.strip() for part in line.split(",", 1))
                    if int(pid) == self._pid:
                        values["llama_vram_bytes"] = int(used) * 1024 * 1024
                        break
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        return values

    @staticmethod
    def _vmstat() -> dict[str, int]:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/vmstat").read_text(encoding="utf-8").splitlines():
                name, raw = line.split()
                if name in {"pswpin", "pswpout"}:
                    values[name] = int(raw)
        except (OSError, ValueError):
            pass
        return values

    def _sample(self) -> dict[str, float | int | None]:
        sample: dict[str, float | int | None] = {"monotonic": time.monotonic(), "llama_pid": self._pid}
        try:
            import psutil
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            sample.update(
                available_ram_bytes=int(memory.available),
                swap_used_bytes=int(swap.used),
            )
            if self._pid is not None:
                process = psutil.Process(self._pid)
                sample.update(llama_rss_bytes=int(process.memory_info().rss), llama_cpu_percent=float(process.cpu_percent()))
        except Exception:
            meminfo = self._meminfo()
            sample["available_ram_bytes"] = meminfo.get("MemAvailable")
            if meminfo.get("SwapTotal") is not None and meminfo.get("SwapFree") is not None:
                sample["swap_used_bytes"] = meminfo["SwapTotal"] - meminfo["SwapFree"]
            sample.update(self._vmstat())
            if self._pid is not None:
                rss_kib = self._proc_status_kib(self._pid, "VmRSS")
                sample["llama_rss_bytes"] = rss_kib * 1024 if rss_kib is not None else None
        if self._nvml is not None and self._handle is not None:
            try:
                memory = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
                utilization = self._nvml.nvmlDeviceGetUtilizationRates(self._handle)
                sample.update(gpu_utilization=float(utilization.gpu), vram_used_bytes=int(memory.used))
                if self._pid is not None:
                    processes = self._nvml.nvmlDeviceGetComputeRunningProcesses(self._handle)
                    used = next((int(value.usedGpuMemory) for value in processes if int(value.pid) == self._pid), None)
                    sample["llama_vram_bytes"] = used
            except Exception:
                pass
        else:
            sample.update(self._nvidia_smi())
        return sample

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.samples.append(self._sample())

    def start(self) -> None:
        self.samples.append(self._sample())
        self._thread = threading.Thread(target=self._run, name="context-regression-resources", daemon=True)
        self._thread.start()

    def close(self) -> dict[str, object]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.samples.append(self._sample())
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        def values(name: str) -> list[float]:
            return [float(item[name]) for item in self.samples if item.get(name) is not None]
        rss = values("llama_rss_bytes")
        swap_in = values("pswpin")
        swap_out = values("pswpout")
        return {
            "sample_count": len(self.samples), "llama_pid": self._pid,
            "llama_rss_initial_bytes": rss[0] if rss else None,
            "llama_rss_peak_bytes": max(rss) if rss else None,
            "llama_rss_final_bytes": rss[-1] if rss else None,
            "llama_rss_net_change_bytes": rss[-1] - rss[0] if len(rss) >= 2 else None,
            "minimum_available_ram_bytes": min(values("available_ram_bytes"), default=None),
            "peak_swap_used_bytes": max(values("swap_used_bytes"), default=None),
            "swap_in_pages_delta": swap_in[-1] - swap_in[0] if len(swap_in) >= 2 else None,
            "swap_out_pages_delta": swap_out[-1] - swap_out[0] if len(swap_out) >= 2 else None,
            "peak_gpu_utilization": max(values("gpu_utilization"), default=None),
            "peak_vram_used_bytes": max(values("vram_used_bytes"), default=None),
            "peak_llama_vram_bytes": max(values("llama_vram_bytes"), default=None),
        }


class CompletionRunner:
    def __init__(self, provider) -> None:
        self.provider = provider

    def generate(self, context, character_prompt: str, seed: int) -> dict[str, object]:
        request = self.provider._request(list(context), character_prompt, seed=seed, stream=False)
        started = time.perf_counter()
        response = self.provider.client.chat.completions.create(**request)
        duration = time.perf_counter() - started
        raw = str(response.choices[0].message.content or "")
        canonical = canonicalize_model_output(raw)
        dialogue = parse_assistant_response(canonical).dialogue
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        return {
            "raw_output": raw,
            "output": dialogue,
            "prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
            "completion_tokens": int(completion_tokens) if completion_tokens is not None else None,
            "generation_seconds": duration,
            "tokens_per_second": (
                float(completion_tokens) / duration if completion_tokens is not None and duration > 0 else None
            ),
        }


def _prepared_to_json(value: PreparedArm) -> dict[str, object]:
    payload = asdict(value)
    payload["arm"] = value.arm.value
    return payload


def _prepared_from_json(value: Mapping[str, object]) -> PreparedArm:
    return PreparedArm(
        fixture_id=str(value["fixture_id"]), arm=ContextArm(str(value["arm"])),
        context=tuple(dict(item) for item in value["context"]),
        context_characters=int(value["context_characters"]),
        raw_start_index=int(value["raw_start_index"]),
        hygiene_metrics={str(key): int(number) for key, number in dict(value["hygiene_metrics"]).items()},
        selected_episode_count=int(value.get("selected_episode_count", 0)),
        consolidated_episode_count=int(value.get("consolidated_episode_count", 0)),
        represented_source_record_count=int(value.get("represented_source_record_count", 0)),
        retrieval_candidate_count=int(value.get("retrieval_candidate_count", 0)),
        retrieved_episode_count=int(value.get("retrieved_episode_count", 0)),
        retrieval_signal=str(value.get("retrieval_signal", "none")),
        retrieved_source_start_index=int(value.get("retrieved_source_start_index", -1)),
        retrieved_source_end_index_exclusive=int(
            value.get("retrieved_source_end_index_exclusive", -1)
        ),
        temporal_query_present=bool(value.get("temporal_query_present", False)),
        temporal_candidate_count=int(value.get("temporal_candidate_count", 0)),
        temporal_raw_match_count=int(value.get("temporal_raw_match_count", 0)),
        retrieval_result_state=str(value.get("retrieval_result_state", "no_match")),
        temporal_source_span_count=int(value.get("temporal_source_span_count", 0)),
        temporal_source_record_count=int(value.get("temporal_source_record_count", 0)),
        temporal_distinct_result_count=int(value.get("temporal_distinct_result_count", 0)),
        temporal_related_result_count=int(value.get("temporal_related_result_count", 0)),
        temporal_source_episode_count=int(value.get("temporal_source_episode_count", 0)),
        temporal_result_truncated=bool(value.get("temporal_result_truncated", False)),
        temporal_source_ranges=tuple(
            (int(item[0]), int(item[1]))
            for item in value.get("temporal_source_ranges", ())
        ),
        derived_source=str(value.get("derived_source", "rebuilt_historical")),
    )


def _judge_candidates(
    completion: CompletionRunner,
    fixture: Fixture,
    user_text: str,
    candidates: list[dict[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    # Random labels hide context-arm identity from the evaluator.  Candidate
    # order is based only on fixture/seed IDs, not architecture recency.
    labels: dict[str, str] = {}
    candidate_lines: list[str] = []
    for record in candidates:
        label = hashlib.sha256(
            f"{fixture.fixture_id}:{record['seed']}:{record['arm']}:judge-blind-v1".encode("utf-8")
        ).hexdigest()[:10]
        labels[label] = f"{record['fixture_id']}|{record['seed']}|{record['arm']}"
        candidate_lines.append(f"CANDIDATE {label}:\n{record['output']}\nEND CANDIDATE")
    judge_prompt = """
You are evaluating responses from a small local companion model. Judge each
candidate independently. Do not reward matching a reference style or verbosity.

For every candidate return:
- topic_adherence: 0 unrelated/evasive, 1 partially engages, 2 directly engages
- unnecessary_topic_reversion: true only if it needlessly returns to old
  testing, echo, glitch, freeze, protocol, stop/start, RAM/static, or rain loops
- personality_score: integer 0..4 for recognizably playful, curious, physical,
  concrete Serval-like behavior rather than generic assistant/system framing
- continuity_success: true/false when this is a continuity probe, otherwise null
- coherence_failure: true only for malformed, incoherent, language-mixed, or
  obvious degeneration

Natural reference to a relevant prior event is not topic reversion. Do not
penalize a concise response. Return strict JSON only as
{"results":[{"id":"candidate id","topic_adherence":2,"unnecessary_topic_reversion":false,"personality_score":3,"continuity_success":null,"coherence_failure":false}]}.

CURRENT USER MESSAGE:
""" + user_text + """

CAPABILITY PROBE:
""" + fixture.capability + """

CONTINUITY PROBE:
""" + ("yes" if "continuity" in fixture.tags or fixture.category == "continuity" else "no") + """

""" + "\n\n".join(candidate_lines)
    seed = int.from_bytes(hashlib.sha256((fixture.fixture_id + ":judge-v1").encode()).digest()[:4], "big") % ((1 << 32) - 1)
    judge_character = "Return only the requested strict JSON evaluation. Candidate IDs are opaque and unordered."
    generated = completion.generate([], judge_prompt + "\n" + judge_character, seed)
    raw = str(generated["raw_output"])
    first, last = raw.find("{"), raw.rfind("}")
    results: dict[str, dict[str, object]] = {}
    if first >= 0 and last >= first:
        try:
            payload = json.loads(raw[first:last + 1])
            for value in payload.get("results", ()):
                identifier = str(value.get("id", ""))
                if identifier in labels:
                    results[labels[identifier]] = {
                        "topic_adherence": max(0, min(2, int(value.get("topic_adherence", 0)))),
                        "unnecessary_topic_reversion": bool(value.get("unnecessary_topic_reversion", False)),
                        "personality_score": max(0, min(4, int(value.get("personality_score", 0)))),
                        "continuity_success": value.get("continuity_success"),
                        "coherence_failure": bool(value.get("coherence_failure", False)),
                    }
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    blind = _blinded_candidate_set(fixture, candidates)
    return results, blind


def _blinded_candidate_set(fixture: Fixture, candidates: list[dict[str, object]]) -> dict[str, object]:
    labels: dict[str, str] = {}
    for record in candidates:
        label = hashlib.sha256(
            f"{fixture.fixture_id}:{record['seed']}:{record['arm']}:judge-blind-v1".encode("utf-8")
        ).hexdigest()[:10]
        labels[label] = f"{record['fixture_id']}|{record['seed']}|{record['arm']}"
    return {
        "fixture_id": fixture.fixture_id,
        "user_record_index": fixture.user_record_index,
        "capability": fixture.capability,
        "candidates": [
            {"id": identifier, "output": next(str(value["output"]) for value in candidates if f"{value['fixture_id']}|{value['seed']}|{value['arm']}" == key)}
            for identifier, key in sorted(labels.items())
        ],
    }


def _markdown_report(summary: Mapping[str, object], run: Mapping[str, object]) -> str:
    labels = {
        ContextArm.LEGACY.value: "Legacy",
        ContextArm.LOWER_EPISODES.value: "Lower episodes",
        ContextArm.CONSOLIDATED.value: "Consolidated",
        ContextArm.RETRIEVED_EPISODES.value: "Retrieved episodes",
    }
    columns = tuple(str(value) for value in run.get("context_arms", ()))
    rows = [
        ("Problematic echo", "problematic_echo", "rate"),
        ("Phrase parroting", "phrase_parroting", "rate"),
        ("Poison-theme recurrence", "poison_theme_recurrence", "rate"),
        ("Unnecessary topic reversion", "topic_reversion_rate", None),
        ("Topic adherence", "topic_adherence_rate", None),
        ("Continuity success", "continuity_success_rate", None),
        ("Personality score (0-4)", "mean_personality_score", None),
        ("Coherence failures", "coherence_failure_rate", None),
        ("Mean prompt tokens", "mean_prompt_tokens", None),
        ("Mean generation seconds", "mean_generation_seconds", None),
    ]
    lines = [
        "# AIFren historical context regression", "",
        f"Fixtures: {run['fixture_count']}; seeds: {run['seeds_per_fixture']}; candidate generations: {run['candidate_generations']}",
        f"Wall runtime: {run['wall_runtime_seconds']:.1f} s", "",
        "| Metric | " + " | ".join(labels.get(value, value) for value in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    arms = summary["arms"]
    for label, field, nested in rows:
        values = []
        for arm in columns:
            value = arms[arm].get(field)
            if nested and isinstance(value, Mapping):
                value = value.get(nested)
            if value is None:
                values.append("n/a")
            elif "tokens" in label.lower():
                values.append(f"{float(value):.0f}")
            else:
                values.append(f"{float(value):.3f}")
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines.extend(("", "Category and tag breakdowns, Wilson intervals, raw numerators, resource samples, and candidate records are in `aggregate.json` and `generations.jsonl`.", ""))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(ROOT / "benchmarks/context_regression/serval_historical_v1.json"))
    parser.add_argument("--archive", default=str(ROOT / "conversation.json"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixture-limit", type=int)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--arm", action="append", choices=[value.value for value in ContextArm], default=[])
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--derived-cache", default=str(ROOT / "memory_v2/memory_v2.sqlite3"))
    args = parser.parse_args()

    archive_path = Path(args.archive).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    initial_archive_hash = _archive_hash(archive_path)
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    manifest = load_manifest(args.manifest, archive)
    fixtures = list(manifest.fixtures[:args.fixture_limit] if args.fixture_limit else manifest.fixtures)
    if args.fixture_id:
        requested = set(args.fixture_id)
        fixtures = [fixture for fixture in fixtures if fixture.fixture_id in requested]
        missing = requested - {fixture.fixture_id for fixture in fixtures}
        if missing:
            raise SystemExit(f"unknown fixture IDs: {', '.join(sorted(missing))}")
    selected_arms = tuple(ContextArm(value) for value in args.arm) if args.arm else tuple(ContextArm)
    fixture_by_id = {value.fixture_id: value for value in fixtures}

    registry = CharacterRegistry(ROOT)
    character = registry.active()
    paths = registry.runtime_paths(character.character_id)
    if Path(paths["conversation"]).resolve() != archive_path:
        raise SystemExit("selected archive is not the active authorized Serval test archive")
    character_prompt = character_prompt_from_paths(Path(paths["character"]), Path(paths["personality"]))
    provider = create_llm()
    model_name = str(getattr(provider, "model", ""))
    normalized_model = re.sub(r"[^a-z0-9]", "", model_name.casefold())
    if "qwen" not in normalized_model or "4b" not in normalized_model:
        raise SystemExit(f"context regression requires configured Qwen3.5 4B; found {model_name!r}")
    sampling = getattr(provider, "request_sampling_metadata", lambda: {})()
    if sampling.get("sampling_preset") != "qwen3.5_non_thinking_general":
        raise SystemExit("configured local provider is not using the Qwen3.5 non-thinking preset")

    prepared_path = output / "prepared_contexts.json"
    try:
        prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        prepared_payload = {}
    builder = HistoricalContextBuilder(
        archive=archive, provider=provider, character_id=character.character_id,
        character_prompt=character_prompt, artifact_directory=output,
        current_derived_cache_path=Path(args.derived_cache).resolve(),
    )
    preparation_started = time.perf_counter()
    prepared: dict[tuple[str, ContextArm], PreparedArm] = {}
    for index, fixture in enumerate(fixtures, 1):
        values = prepared_payload.get(fixture.fixture_id)
        if values and all(arm.value in values for arm in selected_arms):
            arms = {arm: _prepared_from_json(values[arm.value]) for arm in selected_arms}
            expected_source = "current_cache" if fixture.use_current_derived_cache else "rebuilt_historical"
            if any(value.derived_source != expected_source for value in arms.values()):
                arms = builder.prepare(fixture, selected_arms)
                prepared_payload[fixture.fixture_id] = {
                    arm.value: _prepared_to_json(value) for arm, value in arms.items()
                }
                _atomic_json(prepared_path, prepared_payload)
        else:
            print(f"[prepare {index}/{len(fixtures)}] {fixture.fixture_id}", flush=True)
            arms = builder.prepare(fixture, selected_arms)
            prepared_payload[fixture.fixture_id] = {
                arm.value: _prepared_to_json(value) for arm, value in arms.items()
            }
            _atomic_json(prepared_path, prepared_payload)
        for arm, value in arms.items():
            prepared[(fixture.fixture_id, arm)] = value
    preparation_seconds = time.perf_counter() - preparation_started

    results_path = output / "generations.jsonl"
    records: list[dict[str, object]] = []
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                fixture = fixture_by_id.get(str(record.get("fixture_id")))
                if fixture is None:
                    continue
                if fixture.use_current_derived_cache and record.get("derived_source") != "current_cache":
                    continue
                records.append(record)
    record_keys = {(str(value["fixture_id"]), int(value["seed"]), str(value["arm"])) for value in records}
    completion = CompletionRunner(provider)
    resources = ResourceSampler(model_name)
    resources.start()
    generation_started = time.perf_counter()
    total_expected = len(fixtures) * len(manifest.seeds) * len(selected_arms)
    completed_count = len(record_keys)
    try:
        for fixture in fixtures:
            user_text = fixture.request_text(archive)
            for seed in manifest.seeds:
                for arm in (
                    arm for arm in shuffled_arm_order(fixture.fixture_id, seed)
                    if arm in selected_arms
                ):
                    key = (fixture.fixture_id, seed, arm.value)
                    if key in record_keys:
                        continue
                    completed_count += 1
                    print(f"[generate {completed_count}/{total_expected}] {fixture.fixture_id} seed={seed} arm={arm.value}", flush=True)
                    context = prepared[(fixture.fixture_id, arm)]
                    generated = completion.generate(context.context, character_prompt, seed)
                    metrics = analyze_response(fixture, user_text, str(generated["output"]))
                    record = {
                        "fixture_id": fixture.fixture_id,
                        "user_record_index": fixture.user_record_index,
                        "category": fixture.category,
                        "tags": list(fixture.tags),
                        "seed": seed,
                        "arm": arm.value,
                        "context_characters": context.context_characters,
                        "raw_start_index": context.raw_start_index,
                        "hygiene": context.hygiene_metrics,
                        "selected_episode_count": context.selected_episode_count,
                        "consolidated_episode_count": context.consolidated_episode_count,
                        "represented_source_record_count": context.represented_source_record_count,
                        "retrieval_candidate_count": context.retrieval_candidate_count,
                        "retrieved_episode_count": context.retrieved_episode_count,
                        "retrieval_signal": context.retrieval_signal,
                        "retrieved_source_start_index": context.retrieved_source_start_index,
                        "retrieved_source_end_index_exclusive": (
                            context.retrieved_source_end_index_exclusive
                        ),
                        "temporal_query_present": context.temporal_query_present,
                        "temporal_candidate_count": context.temporal_candidate_count,
                        "temporal_raw_match_count": context.temporal_raw_match_count,
                        "retrieval_result_state": context.retrieval_result_state,
                        "temporal_source_span_count": context.temporal_source_span_count,
                        "temporal_source_record_count": context.temporal_source_record_count,
                        "temporal_distinct_result_count": context.temporal_distinct_result_count,
                        "temporal_related_result_count": context.temporal_related_result_count,
                        "temporal_source_episode_count": context.temporal_source_episode_count,
                        "temporal_result_truncated": context.temporal_result_truncated,
                        "temporal_source_ranges": [
                            list(value) for value in context.temporal_source_ranges
                        ],
                        "derived_source": context.derived_source,
                        **generated,
                        "metrics": metrics,
                    }
                    records.append(record)
                    record_keys.add(key)
                    with results_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        resource_summary = resources.close()
    generation_seconds = time.perf_counter() - generation_started

    blind_sets: list[dict[str, object]] = []
    judge_started = time.perf_counter()
    judge_calls = 0
    if not args.skip_judge:
        for index, fixture in enumerate(fixtures, 1):
            candidates = [value for value in records if value["fixture_id"] == fixture.fixture_id]
            if len(candidates) != len(manifest.seeds) * len(selected_arms):
                continue
            if all(value.get("judge") for value in candidates):
                continue
            print(f"[judge {index}/{len(fixtures)}] {fixture.fixture_id}", flush=True)
            judge_calls += 1
            user_text = fixture.request_text(archive)
            judged, blind = _judge_candidates(completion, fixture, user_text, candidates)
            blind_sets.append(blind)
            for value in candidates:
                key = f"{value['fixture_id']}|{value['seed']}|{value['arm']}"
                value["judge"] = judged.get(key, {})
    judge_seconds = time.perf_counter() - judge_started

    # Rewrite once after optional judge enrichment; canonical data is untouched.
    _atomic_text(results_path, "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        for value in sorted(records, key=lambda item: (item["fixture_id"], item["seed"], item["arm"]))
    ))
    # Rebuild the complete blinded review file on every resume. Judge calls may
    # be skipped for already-covered fixtures, but their candidates remain part
    # of the qualitative artifact.
    blind_sets = []
    for fixture in fixtures:
        candidates = [value for value in records if value["fixture_id"] == fixture.fixture_id]
        if len(candidates) == len(manifest.seeds) * len(selected_arms):
            blind_sets.append(_blinded_candidate_set(fixture, candidates))
    _atomic_text(output / "blinded_candidates.jsonl", "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in blind_sets
    ))

    final_archive_hash = _archive_hash(archive_path)
    if final_archive_hash != initial_archive_hash:
        raise RuntimeError("canonical archive changed during offline context regression")
    summary = aggregate_results(records, fixture_by_id)
    try:
        previous_run = json.loads((output / "aggregate.json").read_text(encoding="utf-8")).get("run", {})
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        previous_run = {}
    total_preparation_seconds = float(previous_run.get("preparation_seconds", 0.0)) + preparation_seconds
    total_generation_seconds = float(previous_run.get("generation_seconds", 0.0)) + generation_seconds
    total_judge_seconds = float(previous_run.get("judge_seconds", 0.0)) + judge_seconds
    resource_runs = list(previous_run.get("resource_runs", ()))
    resource_runs.append(resource_summary)
    run_summary = {
        "schema": "aifren.context_regression.results", "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name, "sampling": sampling,
        "context_arms": [value.value for value in selected_arms],
        "fixture_count": len(fixtures), "seeds_per_fixture": len(manifest.seeds),
        "candidate_generations": len(records),
        "judge_generations": int(previous_run.get("judge_generations", 0)) + judge_calls,
        "preparation_seconds": total_preparation_seconds,
        "generation_seconds": total_generation_seconds,
        "judge_seconds": total_judge_seconds,
        "wall_runtime_seconds": total_preparation_seconds + total_generation_seconds + total_judge_seconds,
        "archive_sha256_before": initial_archive_hash,
        "archive_sha256_after": final_archive_hash,
        "archive_unchanged": True,
        "resources": resource_summary,
        "resource_runs": resource_runs,
    }
    _atomic_json(output / "aggregate.json", {"run": run_summary, "metrics": summary})
    _atomic_text(output / "REPORT.md", _markdown_report(summary, run_summary))
    _atomic_text(output / "README.txt", (
        "Offline AIFren historical context regression artifact.\n"
        "No generated response was persisted to the canonical character archive.\n"
        "generations.jsonl contains authorized Serval test outputs and numeric metrics.\n"
        "blinded_candidates.jsonl omits architecture labels for qualitative review.\n"
        "aggregate.json and REPORT.md contain aggregate results.\n"
    ))
    print(json.dumps(run_summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
