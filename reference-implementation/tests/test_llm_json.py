import json
import unittest
from types import SimpleNamespace

import pandas as pd

from common.llm_client import parse_llm_json
from common.placeholders import PlaceholderRegistry
from structured.llm_detector import apply_llm


class LlmJsonTests(unittest.TestCase):
    def test_multiple_json_objects_are_merged(self):
        parsed = parse_llm_json(
            '{"NAME": ["William"]}\n'
            '{"NAME": ["William", "Alice"], "EMAIL": ["alice@example.test"]}'
        )
        self.assertEqual(parsed["NAME"], ["William", "Alice"])
        self.assertEqual(parsed["EMAIL"], ["alice@example.test"])

    def test_json_inside_code_fence_is_parsed(self):
        self.assertEqual(parse_llm_json('```json\n{"NAME": ["William"]}\n```'), {"NAME": ["William"]})

    def test_missing_json_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_llm_json("No sensitive values found")

    def test_structured_flow_retries_malformed_json(self):
        frame = pd.DataFrame(
            [{
                "Transaction Description": "Spoke with William.",
                "Loan Officer Remarks": "Review complete.",
                "Case Resolution Notes": "No action.",
                "Customer Notes": "No follow-up.",
            }]
        )
        runtime = SimpleNamespace(
            llm=SimpleNamespace(batch_size=50, delay_seconds=0.0, provider="test")
        )
        policy = SimpleNamespace(
            policy={
                "structured": {
                    "dynamic_fields": [
                        {"field": "Transaction Description", "label": "TRANSACTION_DESCRIPTION"},
                        {"field": "Loan Officer Remarks", "label": "LOAN_OFFICER_REMARKS"},
                        {"field": "Case Resolution Notes", "label": "CASE_RESOLUTION_NOTES"},
                        {"field": "Customer Notes", "label": "CUSTOMER_NOTES"},
                    ],
                    "llm_category_aliases": {},
                }
            }
        )

        import structured.llm_detector as detector

        calls = {"count": 0}
        original = detector.detect_pii_json

        def fake_detect(_system_prompt, _prompt, _runtime):
            calls["count"] += 1
            if calls["count"] == 1:
                raise json.JSONDecodeError("Extra data", "{}{}", 2)
            return {"NAME": ["William"]}

        detector.detect_pii_json = fake_detect
        try:
            updated, _mapping, detected, *_timings = apply_llm(
                frame,
                runtime,
                policy,
                PlaceholderRegistry("retry-test"),
            )
        finally:
            detector.detect_pii_json = original

        self.assertEqual(calls["count"], 2)
        self.assertEqual(detected, 1)
        self.assertRegex(updated.iloc[0]["Transaction Description"], r"<NAME_[0-9a-f]{10}>")


if __name__ == "__main__":
    unittest.main()
