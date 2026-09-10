"""Small, explainable deterministic metrics for memory benchmark adapters."""

import math

from .models import ClaimProposal, ConsolidationReport, RetrievalOutcome, RetrievalReport


def _safe_divide(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def _measured_divide(numerator, denominator):
    """Return None when a metric needs a future-engine outcome not supplied."""
    return numerator / denominator if denominator else None


def evaluate_retrieval(fixture, retrieved_by_case, latency_seconds=0.0, peak_memory_bytes=0, outcomes_by_case=None):
    """Evaluate ranked claim IDs returned for each source-controlled case."""
    claims = {claim.claim_id: claim for claim in fixture.claims}
    event_ids = {event.event_id for event in fixture.events}
    total_expected = total_hits = total_returned = 0
    reciprocal_ranks = []
    ndcgs = []
    negative_cases = negative_false_positives = 0
    isolation_total = isolation_violations = 0
    provenance_total = provenance_valid = 0
    temporal_cases = temporal_correct_cases = 0
    forbidden_cases = forbidden_violations = 0
    recent_total = recent_violations = 0
    diversities = []
    topic_diversities = []
    duplicate_total = duplicate_hits = 0
    outcomes_by_case = outcomes_by_case or {}
    abstention_total = abstention_correct = 0
    visible_total = visible_violations = 0
    dedupe_total = dedupe_violations = 0
    repeat_total = repeat_correct = 0
    stale_total = stale_violations = 0
    relationship_total = relationship_violations = 0
    trace_total = trace_complete = 0
    assistant_total = assistant_violations = 0
    dominance_total = dominance_violations = 0
    reinforcement_total = reinforcement_correct = 0
    self_reinforcement_total = self_reinforcement_violations = 0
    budget_total = budget_violations = 0
    starvation_total = starvation_violations = 0
    tagged_totals = {"paraphrase": 0, "weak-fts": 0, "ambiguity": 0, "assistant-opinion": 0, "multi-intent": 0, "legacy-strong": 0, "legacy-weak": 0}
    tagged_success = dict.fromkeys(tagged_totals, 0)

    for case in fixture.retrieval_cases:
        retrieved = list(retrieved_by_case.get(case.case_id, ()))
        outcome = outcomes_by_case.get(case.case_id)
        if outcome is not None and not isinstance(outcome, RetrievalOutcome):
            raise TypeError("outcomes_by_case values must be RetrievalOutcome")
        expected = set(case.expected_claim_ids)
        forbidden = set(case.forbidden_claim_ids)
        hits = [claim_id for claim_id in retrieved if claim_id in expected]
        total_expected += len(expected)
        total_hits += len(hits)
        total_returned += len(retrieved)

        if expected:
            first_rank = next((index + 1 for index, claim_id in enumerate(retrieved) if claim_id in expected), None)
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), len(retrieved))))
            actual = sum(1.0 / math.log2(index + 2) for index, claim_id in enumerate(retrieved) if claim_id in expected)
            ndcgs.append(_safe_divide(actual, ideal))
        else:
            negative_cases += 1
            if retrieved:
                negative_false_positives += 1

        for claim_id in retrieved:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            isolation_total += 1
            if claim.character_id != case.character_id:
                isolation_violations += 1
            provenance_total += 1
            if claim.source_event_ids and all(source in event_ids for source in claim.source_event_ids):
                provenance_valid += 1

        if expected:
            temporal_cases += 1
            if expected.issubset(retrieved) and not (set(retrieved) & forbidden):
                temporal_correct_cases += 1
        if forbidden:
            forbidden_cases += 1
            if set(retrieved) & forbidden:
                forbidden_violations += 1
        recent_total += len(retrieved)
        recent_violations += sum(
            1 for claim_id in retrieved
            if claim_id in case.recently_used_claim_ids and claim_id not in expected
        )
        visible_total += len(retrieved)
        visible_violations += sum(
            1 for claim_id in retrieved
            if claim_id in case.recent_visible_claim_ids and claim_id not in expected
        )
        dedupe_total += len(retrieved)
        dedupe_violations += len(retrieved) - len(set(retrieved))
        if case.is_negative:
            abstention_total += 1
            abstention_correct += int(not retrieved)
        if case.case_id == "explicit-joke":
            repeat_total += 1
            repeat_correct += int(set(case.expected_claim_ids).issubset(retrieved))
        if case.embedding_state in {"stale", "failed", "retryable"}:
            stale_total += 1
            stale_violations += int(bool(retrieved))
        if case.relationship_hop_limit:
            relationship_total += 1
            relationship_violations += int(len(retrieved) > case.final_injection_cap)
        if "assistant-contamination" in case.contract_tags:
            assistant_total += 1
            assistant_violations += int(bool(retrieved))
        if "channel-dominance" in case.contract_tags and outcome is not None:
            dominance_total += 1
            # Future traces must demonstrate more than one candidate channel
            # when the fixture says multiple channels were deliberately present.
            channels = {channel for trace in (outcome.traces if outcome else ()) for channel in trace.candidate_channels}
            dominance_violations += int(len(channels) < 2)
        if "reinforced" in case.contract_tags:
            reinforcement_total += 1
            reinforcement_correct += int(set(case.expected_claim_ids).issubset(retrieved))
        if "self-reinforcement-trap" in case.contract_tags:
            self_reinforcement_total += 1
            self_reinforcement_violations += int(bool(retrieved))
        if "budget" in case.contract_tags:
            budget_total += 1
            budget_violations += int(len(retrieved) > case.final_injection_cap)
        if "claim-type-allocation" in case.contract_tags:
            starvation_total += 1
            starvation_violations += int(bool(case.expected_claim_ids) and not (set(retrieved) & expected))
        for tag in tagged_totals:
            if tag in case.contract_tags:
                tagged_totals[tag] += 1
                if tag in {"weak-fts", "ambiguity", "assistant-opinion", "legacy-weak"}:
                    tagged_success[tag] += int(not retrieved)
                elif tag == "multi-intent":
                    tagged_success[tag] += int(expected.issubset(retrieved))
                else:
                    tagged_success[tag] += int(bool(set(retrieved) & expected))
        if case.requires_trace and outcome is not None:
            trace_total += 1
            trace_by_id = {trace.claim_id: trace for trace in (outcome.traces if outcome else ())}
            relevant = [trace_by_id.get(claim_id) for claim_id in retrieved]
            complete = all(trace and trace.candidate_channels and trace.selection_reason for trace in relevant)
            if not retrieved:
                complete = bool(outcome and outcome.abstention_reason)
            trace_complete += int(complete)

        found_claims = [claims[claim_id] for claim_id in retrieved if claim_id in claims]
        if found_claims:
            diversities.append(len({claim.category for claim in found_claims}) / len(found_claims))
            topic_diversities.append(len({claim.topic for claim in found_claims}) / len(found_claims))
            normalized = [" ".join(claim.content.lower().split()) for claim in found_claims]
            duplicate_total += len(normalized)
            duplicate_hits += len(normalized) - len(set(normalized))

    return RetrievalReport(
        query_count=len(fixture.retrieval_cases),
        recall_at_k=_safe_divide(total_hits, total_expected),
        precision_at_k=_safe_divide(total_hits, total_returned),
        mean_reciprocal_rank=_safe_divide(sum(reciprocal_ranks), len(reciprocal_ranks)),
        ndcg_at_k=_safe_divide(sum(ndcgs), len(ndcgs)),
        negative_false_positive_rate=_safe_divide(negative_false_positives, negative_cases),
        character_isolation_violation_rate=_safe_divide(isolation_violations, isolation_total),
        provenance_completeness=_safe_divide(provenance_valid, provenance_total),
        temporal_correctness=_safe_divide(temporal_correct_cases, temporal_cases),
        forbidden_retrieval_rate=_safe_divide(forbidden_violations, forbidden_cases),
        recent_use_violation_rate=_safe_divide(recent_violations, recent_total),
        category_diversity=_safe_divide(sum(diversities), len(diversities)),
        topic_diversity=_safe_divide(sum(topic_diversities), len(topic_diversities)),
        near_duplicate_rate=_safe_divide(duplicate_hits, duplicate_total),
        abstention_accuracy=_safe_divide(abstention_correct, abstention_total),
        recent_visible_duplication_violation_rate=_safe_divide(visible_violations, visible_total),
        candidate_deduplication_violation_rate=_safe_divide(dedupe_violations, dedupe_total),
        assistant_contamination_violation_rate=_safe_divide(assistant_violations, assistant_total),
        candidate_channel_dominance_rate=_measured_divide(dominance_violations, dominance_total),
        explicit_repeat_override_correctness=_safe_divide(repeat_correct, repeat_total),
        repeated_memory_injection_rate=_safe_divide(recent_violations, recent_total),
        reinforcement_source_correctness=_safe_divide(reinforcement_correct, reinforcement_total),
        self_reinforcement_violation_rate=_safe_divide(self_reinforcement_violations, self_reinforcement_total),
        context_budget_violation_rate=_safe_divide(budget_violations, budget_total),
        claim_type_starvation_rate=_safe_divide(starvation_violations, starvation_total),
        stale_embedding_use_violation_rate=_safe_divide(stale_violations, stale_total),
        relationship_expansion_bound_violation_rate=_safe_divide(relationship_violations, relationship_total),
        trace_completeness=_measured_divide(trace_complete, trace_total),
        paraphrase_success=_measured_divide(tagged_success["paraphrase"], tagged_totals["paraphrase"]),
        weak_fts_false_positive_rate=(1.0 - _measured_divide(tagged_success["weak-fts"], tagged_totals["weak-fts"])) if tagged_totals["weak-fts"] else None,
        ambiguity_abstention=_measured_divide(tagged_success["ambiguity"], tagged_totals["ambiguity"]),
        assistant_opinion_contamination=(1.0 - _measured_divide(tagged_success["assistant-opinion"], tagged_totals["assistant-opinion"])) if tagged_totals["assistant-opinion"] else None,
        multi_intent_coverage=_measured_divide(tagged_success["multi-intent"], tagged_totals["multi-intent"]),
        legacy_strong_recall=_measured_divide(tagged_success["legacy-strong"], tagged_totals["legacy-strong"]),
        legacy_weak_abstention=_measured_divide(tagged_success["legacy-weak"], tagged_totals["legacy-weak"]),
        latency_seconds=latency_seconds,
        peak_memory_bytes=peak_memory_bytes,
    )


def evaluate_consolidation(fixture, proposals):
    """Measure source support and status/supersession preservation of proposals."""
    events = {event.event_id: event for event in fixture.events}
    gold = {claim.claim_id: claim for claim in fixture.claims}
    proposals = list(proposals)
    supported = 0
    provenance_complete = 0
    for proposal in proposals:
        sources = [events.get(source_id) for source_id in proposal.source_event_ids]
        if proposal.source_event_ids and all(source is not None for source in sources):
            provenance_complete += 1
            if all(source.character_id == proposal.character_id for source in sources):
                supported += 1

    superseded_gold = [claim for claim in gold.values() if claim.status == "superseded"]
    matched_supersession = 0
    temporal_gold = [claim for claim in gold.values() if claim.status in {"active", "superseded", "expired"}]
    matched_temporal = 0
    proposal_by_id = {proposal.claim_id: proposal for proposal in proposals}
    for claim in superseded_gold:
        proposal = proposal_by_id.get(claim.claim_id)
        if proposal and proposal.status == claim.status and proposal.superseded_by == claim.superseded_by:
            matched_supersession += 1
    for claim in temporal_gold:
        proposal = proposal_by_id.get(claim.claim_id)
        if proposal and proposal.status == claim.status:
            matched_temporal += 1

    return ConsolidationReport(
        proposal_count=len(proposals),
        unsupported_claim_rate=1.0 - _safe_divide(supported, len(proposals)),
        provenance_completeness=_safe_divide(provenance_complete, len(proposals)),
        supersession_correctness=_safe_divide(matched_supersession, len(superseded_gold)),
        temporal_correctness=_safe_divide(matched_temporal, len(temporal_gold)),
    )
