"""Deterministic, privacy-safe multi-turn Current Continuity V2 harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import uuid

from aifren.state.current_continuity import admit_current_continuity_context
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository


MANIFEST = Path(__file__).with_name("manifest.json")


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    checks: int
    failure: str | None = None


@dataclass(frozen=True)
class HarnessReport:
    version: str
    case_count: int
    passed: int
    failed: int
    checks: int
    cases: tuple[CaseResult, ...]


def load_manifest(path: Path = MANIFEST) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "current-continuity-v2-regression-v1":
        raise ValueError("Current Continuity manifest version is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 30:
        raise ValueError("Current Continuity manifest must contain exactly 30 cases")
    if len({item.get("id") for item in cases if isinstance(item, dict)}) != len(cases):
        raise ValueError("Current Continuity manifest IDs must be unique")
    return payload


def _run_case(case: dict) -> CaseResult:
    checks = 0
    try:
        with tempfile.TemporaryDirectory(prefix="aifren-continuity-") as directory:
            root = Path(directory)
            memory = root / "memories.json"
            memory.write_text("[]", encoding="utf-8")
            conversation = root / "conversation.json"
            character_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "aifren:continuity-fixture:" + case["id"]))
            writer = MemoryV2ShadowWriter(root, character_id=character_id, display_name="Synthetic", memory_file=memory)
            repository = MemoryV2Repository(writer.store)
            repository.ensure_character(character_id, "Synthetic", legacy_config_key="characters/default")
            rows = []
            base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
            results = []
            for index, text in enumerate(case["turns"]):
                timestamp = case.get("timestamps", [])[index] if index < len(case.get("timestamps", [])) else (base + timedelta(minutes=index)).isoformat()
                message = {"role": "user", "content": text, "timestamp": timestamp}
                canonical_index = len(rows)
                rows.extend((message, {"role": "assistant", "content": "Synthetic reply.", "timestamp": (base + timedelta(minutes=index, seconds=1)).isoformat()}))
                conversation.write_text(json.dumps(rows), encoding="utf-8")
                results.append(writer.observe_canonical_user_continuity(
                    message, conversation_index=canonical_index, conversation_file=conversation,
                ))

            if case.get("restart"):
                writer.close()
                writer = MemoryV2ShadowWriter(root, character_id=character_id, display_name="Synthetic", memory_file=memory)
                repository = MemoryV2Repository(writer.store)
            scope = repository.active_truth_scope(character_id)
            checks += 1
            assert scope.kind == case["scope"], (scope.kind, case["scope"])
            if "label" in case:
                checks += 1
                assert scope.label == case["label"], (scope.label, case["label"])
            activity = repository.lookup_actor_state(character_id, "user", "activity").state
            checks += 1
            assert (activity.value if activity else None) == case.get("activity"), (
                activity.value if activity else None, case.get("activity"),
            )
            threads = repository.list_open_threads(character_id).threads
            actual_threads = sorted(item.description for item in threads)
            checks += 1
            assert actual_threads == sorted(case.get("threads", [])), (actual_threads, case.get("threads", []))

            now = base + timedelta(hours=case.get("gap_hours", 1), minutes=100)
            admission = admit_current_continuity_context(
                repository, character_id, case["query"], now_us=int(now.timestamp() * 1_000_000),
            )
            if "active_admitted" in case:
                checks += 1
                assert bool(admission.active_state_context) is bool(case["active_admitted"])
            if "thread_admitted" in case:
                checks += 1
                assert admission.admitted_thread_count == case["thread_admitted"], (
                    admission.admitted_thread_count, case["thread_admitted"],
                )
            prompt = "\n".join(filter(None, (
                admission.truth_scope_context, admission.active_state_context, admission.open_thread_context,
            )))
            checks += 3
            assert "thread-" not in prompt and "scope-" not in prompt
            assert "event_id" not in prompt and "excerpt" not in prompt and "sha256" not in prompt
            assert len(prompt) <= int(case.get("max_context_chars", 1800))
            for absent in case.get("absent", []):
                checks += 1
                assert absent not in prompt, absent
            if case.get("single_thread_block"):
                checks += 1
                assert prompt.count("[Open continuity") == 1
            if case.get("provider_parity"):
                checks += 1
                second = admit_current_continuity_context(
                    repository, character_id, case["query"], now_us=int(now.timestamp() * 1_000_000),
                )
                assert admission == second
            if case["id"] == "malformed_timestamp":
                checks += 1
                assert results[-1].get("reason") == "invalid_canonical_timestamp"
            writer.close()
        return CaseResult(case["id"], True, checks)
    except Exception as error:
        try:
            writer.close()
        except Exception:
            pass
        return CaseResult(case.get("id", "unknown"), False, checks, f"{type(error).__name__}: {error}")


def run(path: Path = MANIFEST) -> HarnessReport:
    manifest = load_manifest(path)
    results = tuple(_run_case(case) for case in manifest["cases"])
    passed = sum(item.passed for item in results)
    return HarnessReport(
        manifest["version"], len(results), passed, len(results) - passed,
        sum(item.checks for item in results), results,
    )


def report_json(report: HarnessReport) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=True)
