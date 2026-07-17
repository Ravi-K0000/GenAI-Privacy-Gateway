import json
import csv
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from common.metadata import write_metadata
from common.paths import domain_output
from common.placeholders import PLACEHOLDER_RE
from common.policy import PolicyContext


@dataclass
class Timer:
    elapsed_seconds: float = 0.0

    def measure(self, func, *args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        self.elapsed_seconds += time.perf_counter() - start
        return result


def count_unresolved_placeholders(value: Any) -> int:
    if isinstance(value, dict):
        return sum(count_unresolved_placeholders(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_unresolved_placeholders(item) for item in value)
    if isinstance(value, str):
        return len(PLACEHOLDER_RE.findall(value))
    return 0


def build_metrics(
    records: int,
    sensitive_values_detected: int,
    mappings_created: int,
    anonymization_seconds: float,
    rehydration_seconds: float,
    unresolved_placeholders: int,
    expected_placeholders: int = 0,
    returned_placeholders: int = 0,
    missing_placeholders: int = 0,
    unexpected_placeholders: int = 0,
    third_party_status: str = "processed",
    rehydration_status: str = "completed",
    static_anonymization_seconds: float | None = None,
    llm_inference_seconds: float | None = None,
    dynamic_replacement_seconds: float | None = None,
    excluded_wait_seconds: float | None = None,
    anonymization_validation_status: str = "not_run",
    residual_pii_count: int = 0,
    fidelity_details: dict[str, Any] | None = None,
    rehydration_integrity_seconds: float | None = None,
    mapping_retrieval_seconds: float | None = None,
    placeholder_replacement_seconds: float | None = None,
    rehydration_output_seconds: float | None = None,
    end_to_end_rehydration_seconds: float | None = None,
    rehydration_passes: int | None = None,
    recovery_rows: int = 0,
    recovery_values_detected: int = 0,
) -> dict[str, Any]:
    fidelity_ok = unresolved_placeholders == 0 and unexpected_placeholders == 0 and third_party_status != "failed"
    fidelity = "Not applicable" if third_party_status in {"failed", "file_drop", "skipped"} else ("100%" if fidelity_ok else "Incomplete")
    metrics = {
        "Records": records,
        "Sensitive values detected": sensitive_values_detected,
        "Mappings created": mappings_created,
        "Third-party processing": third_party_status,
        "Rehydration status": rehydration_status,
        "Anonymization": f"{anonymization_seconds:.3f}s",
        "Anonymization validation": anonymization_validation_status,
        "Residual PII findings": residual_pii_count,
        "Contextual recovery rows": recovery_rows,
        "Recovery values detected": recovery_values_detected,
        "Core rehydration (excluding mapping retrieval)": f"{rehydration_seconds:.3f}s",
        "Placeholder restoration fidelity": fidelity,
        "Expected placeholders": expected_placeholders,
        "Returned placeholders": returned_placeholders,
        "Processor omitted placeholders": missing_placeholders,
        "Unexpected placeholders": unexpected_placeholders,
        "Unresolved placeholders": unresolved_placeholders,
    }
    if static_anonymization_seconds is not None:
        metrics["Static anonymization"] = f"{static_anonymization_seconds:.3f}s"
    if llm_inference_seconds is not None:
        metrics["LLM inference"] = f"{llm_inference_seconds:.3f}s"
    if dynamic_replacement_seconds is not None:
        metrics["Dynamic replacement"] = f"{dynamic_replacement_seconds:.3f}s"
    if excluded_wait_seconds is not None:
        metrics["Excluded provider waits"] = f"{excluded_wait_seconds:.3f}s"
    if fidelity_details:
        metrics["Exact field fidelity"] = fidelity_details.get("exact_field_fidelity", "Not applicable")
        metrics["Semantic field fidelity"] = fidelity_details.get("semantic_field_fidelity", "Not applicable")
        metrics["Fields changed"] = fidelity_details.get("fields_changed", "Not applicable")
        metrics["Rows changed"] = fidelity_details.get("rows_changed", "Not applicable")
    if rehydration_integrity_seconds is not None:
        metrics["Rehydration integrity"] = f"{rehydration_integrity_seconds:.3f}s"
    if mapping_retrieval_seconds is not None:
        metrics["Mapping retrieval/decryption"] = f"{mapping_retrieval_seconds:.3f}s"
    if end_to_end_rehydration_seconds is not None:
        metrics["End-to-end rehydration"] = f"{end_to_end_rehydration_seconds:.3f}s"
    if rehydration_passes is not None:
        metrics["Rehydration passes"] = rehydration_passes
    if placeholder_replacement_seconds is not None:
        metrics["Placeholder replacement"] = f"{placeholder_replacement_seconds:.3f}s"
    if rehydration_output_seconds is not None:
        metrics["Rehydration output"] = f"{rehydration_output_seconds:.3f}s"
    return metrics


def compare_structured_fidelity(source_csv: Path, rehydrated_json: Path) -> dict[str, Any]:
    source_rows = list(csv.DictReader(source_csv.open("r", newline="", encoding="utf-8-sig")))
    payload = json.loads(rehydrated_json.read_text(encoding="utf-8"))
    rehydrated_payload = payload.get("rehydrated_payload", payload) if isinstance(payload, dict) else payload
    returned_rows = rehydrated_payload.get("records") if isinstance(rehydrated_payload, dict) else None
    if not isinstance(returned_rows, list) or len(returned_rows) != len(source_rows):
        return {
            "exact_field_fidelity": "Not applicable",
            "semantic_field_fidelity": "Not applicable",
            "fields_changed": "Not applicable",
            "rows_changed": "Not applicable",
        }

    source_fields = list(source_rows[0]) if source_rows else []
    total_fields = len(source_rows) * len(source_fields)
    exact_matches = 0
    semantic_matches = 0
    fields_changed = 0
    changed_rows = 0
    for source, returned in zip(source_rows, returned_rows):
        row_changed = False
        for field in source_fields:
            expected = str(source.get(field, ""))
            actual = str(returned.get(field, ""))
            if expected == actual:
                exact_matches += 1
                semantic_matches += 1
                continue
            fields_changed += 1
            row_changed = True
            if _semantically_equal(expected, actual):
                semantic_matches += 1
        changed_rows += int(row_changed)

    return {
        "exact_field_fidelity": _percentage(exact_matches, total_fields),
        "semantic_field_fidelity": _percentage(semantic_matches, total_fields),
        "fields_changed": fields_changed,
        "rows_changed": changed_rows,
    }


def _semantically_equal(expected: str, actual: str) -> bool:
    try:
        return Decimal(expected) == Decimal(actual)
    except (InvalidOperation, ValueError):
        return False


def _percentage(numerator: int, denominator: int) -> str:
    return "Not applicable" if denominator == 0 else f"{(numerator * 100 / denominator):.4f}%"


def print_metrics_table(metrics: dict[str, Any]) -> None:
    print("\nMetric                         Value")
    for key, value in metrics.items():
        print(f"{key:<30} {value}")


def write_metrics(domain: str, run_id: str, metrics: dict[str, Any], policy_context: PolicyContext) -> tuple[Path, Path]:
    metrics_dir = domain_output(domain, "metrics")
    metrics_dir.mkdir(parents=True, exist_ok=True)
    json_path = metrics_dir / f"metrics_{run_id}.json"
    text_path = metrics_dir / f"metrics_{run_id}.txt"
    payload = {**policy_context.metadata(run_id), "metrics": metrics}
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["Metric                         Value"]
    lines.extend(f"{key:<30} {value}" for key, value in metrics.items())
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_metadata(text_path, policy_context, run_id)
    return json_path, text_path
