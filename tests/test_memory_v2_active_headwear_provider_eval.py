import unittest

from benchmarks.memory_v2.active_headwear_provider_eval import load_provider_manifest


class ActiveHeadwearProviderEvalManifestTests(unittest.TestCase):
    def test_manifest_is_frozen_small_and_covers_current_replace_clear_and_withholding(self):
        cases = load_provider_manifest()
        self.assertEqual(6, len(cases))
        self.assertEqual(
            {"relevant": 2, "clear": 1, "withhold": 2, "correction": 1},
            {kind: sum(case.kind == kind for case in cases) for kind in ("relevant", "clear", "withhold", "correction")},
        )
        self.assertEqual("What hat is she wearing?", cases[0].query)
        self.assertEqual("What hat is she wearing?", cases[-1].followup_query)


if __name__ == "__main__":
    unittest.main()
