from pathlib import Path

from common.config import RuntimeConfig
from common.policy import PolicyContext
from rehydration.common import rehydrate_json_result


def rehydrate_unstructured_result(
    run_id: str,
    third_party_result: Path,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
    expected_placeholders: list[str] | set[str] | None = None,
) -> dict[str, object]:
    return rehydrate_json_result("unstructured", run_id, third_party_result, policy_context, runtime_config, expected_placeholders)
