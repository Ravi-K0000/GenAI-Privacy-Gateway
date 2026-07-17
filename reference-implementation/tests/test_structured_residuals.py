import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from structured.llm_detector import call_llm_detection
from structured.validation import (
    _count_unrecognized_placeholders,
    _mask_recognized_placeholders,
    contextual_residual_rows,
)


ROOT = Path(__file__).resolve().parents[1]


def policy_context():
    policy = json.loads((ROOT / "common" / "privacy_policy.json").read_text(encoding="utf-8"))
    return SimpleNamespace(policy=policy)


class StructuredResidualTests(unittest.TestCase):
    def test_standalone_contextual_name_is_selected_for_recovery(self):
        frame = pd.DataFrame(
            [
                {
                    "Transaction Description": "Mortgage payment received from <NAME_1111111111>.",
                    "Loan Officer Remarks": "Risk review completed.",
                    "Case Resolution Notes": (
                        "Replacement documents dispatched to <POSTAL_ADDRESSES_2222222222> "
                        "after speaking with William."
                    ),
                    "Customer Notes": "No follow-up required.",
                }
            ]
        )
        mapped = {"<NAME_1111111111>", "<POSTAL_ADDRESSES_2222222222>"}
        self.assertEqual(contextual_residual_rows(frame, policy_context(), mapped), [0])

    def test_mapped_double_wrapper_is_masked_not_malformed(self):
        mapped = {"<NAME_1111111111>"}
        text = "Employee <<NAME_1111111111>> was contacted."
        self.assertEqual(_mask_recognized_placeholders(text, mapped), "Employee  was contacted.")
        self.assertEqual(_count_unrecognized_placeholders(text, mapped), 0)

    def test_unknown_double_wrapper_remains_malformed(self):
        text = "Employee <<NAME_1111111111>> was contacted."
        self.assertEqual(_count_unrecognized_placeholders(text, set()), 1)

    def test_primary_prompt_mentions_standalone_aliases(self):
        captured = {}

        def fake_detect(system_prompt, prompt, _runtime_config):
            captured["system_prompt"] = system_prompt
            return {}

        import structured.llm_detector as detector

        original = detector.detect_pii_json
        detector.detect_pii_json = fake_detect
        try:
            call_llm_detection("{}", object())
        finally:
            detector.detect_pii_json = original
        self.assertIn("after speaking with William", captured["system_prompt"])
        self.assertIn("shortened aliases", captured["system_prompt"])


if __name__ == "__main__":
    unittest.main()
