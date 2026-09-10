"""Run deterministic Active State QA and emit structural JSON only."""

from __future__ import annotations

import argparse
from pathlib import Path

from .harness import (
    report_json,
    run_curated_matrix,
    run_long_context_matrix,
    run_randomized_state_machine,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("curated", "randomized", "long_context", "all"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seeds", type=int, default=300)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--operations", type=int, default=150)
    args = parser.parse_args()
    reports = []
    if args.suite in {"curated", "all"}:
        reports.append(run_curated_matrix())
    if args.suite in {"randomized", "all"}:
        reports.append(run_randomized_state_machine(
            seed_count=args.seeds, operations_per_seed=args.operations,
            seed_start=args.seed_start,
        ))
    if args.suite in {"long_context", "all"}:
        reports.append(run_long_context_matrix())
    payload = "[\n" + ",\n".join(report_json(item) for item in reports) + "\n]\n"
    if args.output is not None:
        args.output.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
