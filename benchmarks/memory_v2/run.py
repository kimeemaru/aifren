"""Command-line runner for lightweight deterministic benchmark baselines."""

import argparse
from dataclasses import asdict
import json

from .adapters import GoldReferenceAdapter, HybridSemanticRetrievalV2Adapter, MemoryV1StructuralAdapter, MemoryV2StructuralAdapter, SemanticRetrievalV2Adapter
from .fixtures import build_core_fixture, generate_scale_fixture
from .harness import run_retrieval_benchmark


def main():
    parser = argparse.ArgumentParser(description="Run deterministic AIFren memory benchmarks.")
    parser.add_argument("--adapter", choices=("reference", "v1", "v2", "semantic-v2", "hybrid-v2"), default="reference")
    parser.add_argument("--scale", type=int, default=0, help="Generate this many synthetic events on demand.")
    parser.add_argument("--limit", type=int, default=5)
    arguments = parser.parse_args()

    fixture = generate_scale_fixture(arguments.scale) if arguments.scale else build_core_fixture()
    adapter = {
        "reference": GoldReferenceAdapter,
        "v1": MemoryV1StructuralAdapter,
        "v2": MemoryV2StructuralAdapter,
        "semantic-v2": SemanticRetrievalV2Adapter,
        "hybrid-v2": HybridSemanticRetrievalV2Adapter,
    }[arguments.adapter](fixture) if arguments.adapter != "reference" else GoldReferenceAdapter()
    report, retrieved = run_retrieval_benchmark(fixture, adapter, limit=arguments.limit)
    print(json.dumps({
        "fixture_version": fixture.version,
        "adapter": adapter.name,
        "event_count": len(fixture.events),
        "claim_count": len(fixture.claims),
        "report": asdict(report),
        "retrieved_by_case": retrieved,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
