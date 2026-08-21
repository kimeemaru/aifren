"""Execution harness with lightweight latency and peak-memory measurements."""

import time
import tracemalloc

from .metrics import evaluate_retrieval


def run_retrieval_benchmark(fixture, adapter, limit=5, cases=None, outcomes_by_case=None):
    tracemalloc.start()
    start = time.perf_counter()
    try:
        selected_cases = tuple(cases) if cases is not None else fixture.retrieval_cases
        outcomes_by_case = outcomes_by_case or {}
        retrieved_by_case = {}
        retrieve_outcome = getattr(adapter, "retrieve_outcome", None)
        for case in selected_cases:
            if retrieve_outcome:
                outcome = retrieve_outcome(fixture, case, limit=limit)
                outcomes_by_case[case.case_id] = outcome
                retrieved_by_case[case.case_id] = list(outcome.claim_ids)
            else:
                retrieved_by_case[case.case_id] = adapter.retrieve(fixture, case, limit=limit)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        closer = getattr(adapter, "close", None)
        if closer:
            closer()
    selected_fixture = fixture if cases is None else type(fixture)(fixture.version, fixture.events, fixture.claims, selected_cases)
    return evaluate_retrieval(selected_fixture, retrieved_by_case, elapsed, peak, outcomes_by_case), retrieved_by_case
