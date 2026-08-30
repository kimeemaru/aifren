import unittest

from benchmarks.memory_v2.identity_name_provider_eval import load_provider_manifest


class IdentityNameProviderEvalManifestTests(unittest.TestCase):
    def test_manifest_is_frozen_small_and_represents_required_behavior(self):
        cases = load_provider_manifest()
        self.assertEqual(8, len(cases))
        self.assertEqual(
            {"relevant": 3, "withhold": 3, "hypothetical": 1, "correction": 1},
            {kind: sum(case.kind == kind for case in cases) for kind in ("relevant", "withhold", "hypothetical", "correction")},
        )
        self.assertEqual("What is my name?", cases[0].query)
        self.assertEqual("What is my name?", cases[-1].followup_query)


if __name__ == "__main__":
    unittest.main()
