import unittest

from common.metrics import build_metrics


class MetricsTests(unittest.TestCase):
    def test_core_rehydration_label_is_explicit(self):
        metrics = build_metrics(
            records=1,
            sensitive_values_detected=1,
            mappings_created=1,
            anonymization_seconds=2.0,
            rehydration_seconds=3.0,
            unresolved_placeholders=0,
        )
        self.assertEqual(metrics["Core rehydration (excluding mapping retrieval)"], "3.000s")
        self.assertNotIn("Rehydration", metrics)


if __name__ == "__main__":
    unittest.main()
