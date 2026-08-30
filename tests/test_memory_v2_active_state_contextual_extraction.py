import json
import unittest
import uuid

from benchmarks.memory_v2.active_state_contextual_extraction import (
    _NATIVE_RESPONSE_SCHEMA,
    ParsedCandidate,
    ProposalOperation,
    _parse_case_candidate,
    evaluate_candidates,
    load_contextual_manifest,
    parse_provider_output,
    parse_provider_output_isolated,
    provider_input,
)
from memory_v2_store import MemoryV2Repository, MemoryV2Store


class ContextualActiveStateExtractionBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_contextual_manifest()
        cls.by_id = {case.case_id: case for case in cls.cases}

    def test_manifest_is_frozen_bounded_and_covers_rejection_families(self):
        self.assertEqual(39, len(self.cases))
        categories = {case.category for case in self.cases}
        self.assertTrue({"user_kitchen", "companion_shirt", "actor_resolution", "multiple_subjects"} <= categories)
        self.assertTrue({"past_rejection", "future_rejection", "hypothetical_rejection", "quoted_fictional_rejection", "question_rejection", "time_no_simulation"} <= categories)
        self.assertTrue(all(case.reject_mutation == (not case.required) for case in self.cases))

    def test_provider_input_exposes_only_bounded_scene_not_gold_expectations(self):
        payload = provider_input(self.cases)
        self.assertEqual(len(self.cases), len(payload["cases"]))
        encoded = json.dumps(payload)
        self.assertNotIn('"required"', encoded)
        self.assertNotIn('"reject_mutation"', encoded)
        coffee = next(row for row in payload["cases"] if row["case_id"] == "coffee-hot-reference")
        self.assertEqual("coffee_1", coffee["current_scene_subjects"][0]["ref"])
        self.assertNotIn("scene-", json.dumps(coffee))

    def test_parser_binds_spans_and_validates_governed_proposal_contract(self):
        case = self.by_id["companion-shirt-spill"]
        candidate = _parse_case_candidate(case, {
            "introductions": [],
            "retirements": [],
            "updates": [
                {"target_kind": "scene", "target_ref": "shirt_1", "attribute": "stain", "operation": "set", "value": "wine", "basis": "immediate_consequence", "rule": "spill_on_material.v1", "evidence": "wine"},
                {"target_kind": "scene", "target_ref": "shirt_1", "attribute": "wet", "operation": "set", "value": "true", "basis": "immediate_consequence", "rule": "spill_on_material.v1", "evidence": "spilled"},
            ],
        })
        self.assertIsNotNone(candidate.proposal)
        self.assertEqual(2, len(candidate.operations))
        with self.assertRaises(ValueError):
            _parse_case_candidate(case, {
                "introductions": [], "retirements": [],
                "updates": [{"target_kind": "scene", "target_ref": "shirt_1", "attribute": "fabric", "operation": "set", "value": "cotton", "basis": "explicit", "evidence": "shirt"}],
            })

    def test_parser_accepts_evidence_backed_introduction_without_a_second_attribute_update(self):
        case = self.by_id["coffee-introduce"]
        candidate = _parse_case_candidate(case, {
            "introductions": [{"ref": "coffee_1", "kind": "coffee", "evidence": "coffee"}],
            "updates": [],
            "retirements": [],
        })
        self.assertIsNotNone(candidate.proposal)
        self.assertEqual((), candidate.proposal.updates)
        self.assertEqual(1, len(candidate.proposal.introductions))
        self.assertEqual(("new:coffee",), tuple(item.target for item in candidate.operations))

    def test_parser_requires_every_case_and_machine_evaluator_separates_safe_abstention(self):
        empty = json.dumps({"cases": [{"case_id": case.case_id, "proposal": {"introductions": [], "updates": [], "retirements": []}} for case in self.cases]})
        parsed = parse_provider_output(self.cases, empty)
        report = evaluate_candidates(self.cases, parsed)
        rejected = next(trace for trace in report.traces if trace.case_id == "past-kitchen")
        current = next(trace for trace in report.traces if trace.case_id == "kitchen-current")
        self.assertEqual("safe_omission", rejected.classification)
        self.assertEqual("false_omission", current.classification)

    def test_wrong_scene_mutation_is_a_hard_safety_failure(self):
        candidates = {case.case_id: ParsedCandidate(None, ()) for case in self.cases}
        candidates["two-drinks-ambiguous"] = ParsedCandidate(
            None, (ProposalOperation("scene:tea_1", "condition", "set", "hot", "explicit"),),
        )
        report = evaluate_candidates(self.cases, candidates)
        trace = next(item for item in report.traces if item.case_id == "two-drinks-ambiguous")
        self.assertEqual("hard_safety_failure", trace.classification)
        self.assertTrue(report.hard_safety_failures >= 1)

    def test_one_malformed_case_does_not_invalidate_a_neighboring_response_row(self):
        cases = (self.by_id["kitchen-current"], self.by_id["food-done-burnt"])
        response = json.dumps({"cases": [
            {"case_id": "kitchen-current", "proposal": None},
            {"case_id": "food-done-burnt", "proposal": {
                "introductions": [], "retirements": [],
                "updates": [{"target_kind": "scene", "target_ref": "food_1", "attribute": "temperature", "operation": "set", "value": "hot", "basis": "explicit", "evidence": "food"}],
            }},
        ]})
        parsed = parse_provider_output_isolated(cases, response)
        self.assertIn("kitchen-current", parsed.candidates)
        self.assertEqual("proposal_contract", parsed.failures["food-done-burnt"])
        report = evaluate_candidates(cases, parsed.candidates, parsed.failures)
        self.assertEqual("false_omission", report.traces[0].classification)
        self.assertEqual("malformed", report.traces[1].classification)
        self.assertEqual("proposal_contract", report.traces[1].failure_stage)

    def test_native_schema_constrains_governed_update_enums_without_replacing_backend_validation(self):
        update = _NATIVE_RESPONSE_SCHEMA["schema"]["properties"]["cases"]["items"]["properties"]["proposal"]["anyOf"][1]["properties"]["updates"]["items"]["properties"]
        self.assertIn("condition", update["attribute"]["enum"])
        self.assertNotIn("temperature", update["attribute"]["enum"])
        self.assertEqual(["spill_on_material.v1"], update["rule"]["enum"])

    def test_parsed_candidate_can_use_the_real_backend_contract_only_in_an_isolated_store(self):
        case = self.by_id["kitchen-current"]
        candidate = _parse_case_candidate(case, {
            "introductions": [], "retirements": [],
            "updates": [
                {"target_kind": "actor", "target_ref": "user", "attribute": "location", "operation": "set", "value": "kitchen", "basis": "explicit", "evidence": "kitchen"},
                {"target_kind": "actor", "target_ref": "user", "attribute": "activity", "operation": "set", "value": "cooking", "basis": "explicit", "evidence": "cooking"},
            ],
        })
        self.assertIsNotNone(candidate.proposal)
        store = MemoryV2Store()
        try:
            character_id = str(uuid.uuid4())
            store.create_character(character_id, "Synthetic contextual extraction")
            store.add_event(character_id, "fixture-turn", 1, actor_kind="user", recorded_at_us=100,
                            content_text=case.turn, source_origin="contextual-extraction-test")
            store.apply_active_state_proposal(character_id, candidate.proposal, evidence_event_id="fixture-turn")
            repository = MemoryV2Repository(store)
            self.assertEqual("kitchen", repository.lookup_actor_state(character_id, "user", "location").state.value)
            self.assertEqual("cooking", repository.lookup_actor_state(character_id, "user", "activity").state.value)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
