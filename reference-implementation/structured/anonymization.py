import logging
import re
import time

import pandas as pd

from common.config import RuntimeConfig
from common.mapping_store import write_mapping
from common.metadata import write_metadata
from common.metrics import Timer
from common.paths import domain_output, sample_input
from common.placeholders import (
    PLACEHOLDER_RE,
    PlaceholderRegistry,
    count_placeholders,
    replace_sensitive_matches,
    replace_sensitive_value,
)
from common.policy import PolicyContext
from structured.llm_detector import apply_llm
from structured.validation import contextual_residual_rows, validate_structured_anonymization


log = logging.getLogger(__name__)


def run_structured_anonymization(
    run_id: str,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
) -> dict[str, object]:
    input_path = sample_input("structured")
    log.debug("Structured anonymization input=%s", input_path)
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    registry = PlaceholderRegistry(run_id)

    anonymization_timer = Timer()
    static_df, static_mapping = anonymization_timer.measure(_run_static_anonymization, df, policy_context, registry)
    static_anonymization_seconds = anonymization_timer.elapsed_seconds
    static_path = _write_df(static_df, "static", run_id, policy_context)
    log.debug("Structured static anonymization wrote %s mapping_entries=%s", static_path, len(static_mapping))
    write_mapping("structured", run_id, "static", static_mapping, runtime_config, policy_context)

    dynamic_start = time.perf_counter()
    (
        dynamic_df,
        dynamic_mapping,
        dynamic_detected,
        excluded_wait_seconds,
        llm_inference_seconds,
        dynamic_replacement_seconds,
    ) = apply_llm(
        static_df,
        runtime_config,
        policy_context,
        registry,
    )
    recovery_rows = contextual_residual_rows(
        dynamic_df,
        policy_context,
        mapped_placeholders=set(registry.mappings()),
    )
    recovery_detected = 0
    if recovery_rows:
        log.warning(
            "Structured contextual residual scan selected %s rows for targeted LLM recovery",
            len(recovery_rows),
        )
        (
            recovery_df,
            recovery_mapping,
            recovery_detected,
            recovery_wait_seconds,
            recovery_inference_seconds,
            recovery_replacement_seconds,
        ) = apply_llm(
            dynamic_df.loc[recovery_rows],
            runtime_config,
            policy_context,
            registry,
            recovery=True,
            batch_size_override=min(10, runtime_config.llm.batch_size),
        )
        dynamic_df.loc[recovery_rows, recovery_df.columns] = recovery_df
        dynamic_mapping.update(recovery_mapping)
        dynamic_detected += recovery_detected
        excluded_wait_seconds += recovery_wait_seconds
        llm_inference_seconds += recovery_inference_seconds
        dynamic_replacement_seconds += recovery_replacement_seconds
    dynamic_wall_seconds = time.perf_counter() - dynamic_start
    dynamic_benchmark_seconds = max(0.0, dynamic_wall_seconds - excluded_wait_seconds)
    anonymization_timer.elapsed_seconds += dynamic_benchmark_seconds
    dynamic_path = _write_df(dynamic_df, "dynamic", run_id, policy_context)
    log.debug(
        "Structured dynamic anonymization wrote %s detected=%s mapping_entries=%s wall_seconds=%.3f excluded_wait_seconds=%.3f benchmark_seconds=%.3f",
        dynamic_path,
        dynamic_detected,
        len(dynamic_mapping),
        dynamic_wall_seconds,
        excluded_wait_seconds,
        dynamic_benchmark_seconds,
    )
    write_mapping("structured", run_id, "dynamic", dynamic_mapping, runtime_config, policy_context)

    final_mapping = registry.mappings()
    final_path = _write_df(dynamic_df, "final", run_id, policy_context)
    log.debug("Structured final anonymized output wrote %s final_mapping_entries=%s", final_path, len(final_mapping))
    write_mapping("structured", run_id, "final", final_mapping, runtime_config, policy_context)
    final_placeholder_counts = _count_df_placeholders(dynamic_df)
    validation = validate_structured_anonymization(
        df,
        dynamic_df,
        run_id,
        policy_context,
        mapped_placeholders=set(final_mapping),
    )
    log.info(
        "Structured anonymization validation status=%s residual_pii_count=%s affected_rows=%s report=%s",
        validation["status"],
        validation["residual_pii_count"],
        validation["affected_row_count"],
        validation["path"],
    )

    return {
        "run_id": run_id,
        "input_path": input_path,
        "static_path": static_path,
        "dynamic_path": dynamic_path,
        "final_path": final_path,
        "records": len(df),
        "sensitive_values_detected": len(static_mapping) + dynamic_detected,
        "mappings_created": len(final_mapping),
        "anonymization_seconds": anonymization_timer.elapsed_seconds,
        "static_anonymization_seconds": static_anonymization_seconds,
        "llm_inference_seconds": llm_inference_seconds,
        "dynamic_replacement_seconds": dynamic_replacement_seconds,
        "excluded_wait_seconds": excluded_wait_seconds,
        "recovery_rows": len(recovery_rows),
        "recovery_values_detected": recovery_detected,
        "anonymization_validation_status": validation["status"],
        "residual_pii_count": validation["residual_pii_count"],
        "validation_path": validation["path"],
        "expected_placeholders": sorted(final_placeholder_counts),
        "expected_placeholder_counts": final_placeholder_counts,
    }


def _run_static_anonymization(
    df: pd.DataFrame,
    policy_context: PolicyContext,
    registry: PlaceholderRegistry,
) -> tuple[pd.DataFrame, dict[str, str]]:
    anonymized = df.copy().astype(str)
    original = df.copy().astype(str)
    before = set(registry.mappings())
    for item in policy_context.policy["structured"].get("static_fields", []):
        field = item["field"]
        label = item["label"]
        if field not in anonymized.columns:
            continue
        for idx, value in anonymized[field].items():
            placeholder = registry.placeholder_for(label, value)
            anonymized.at[idx, field] = placeholder

    dynamic_fields = [
        item["field"]
        for item in policy_context.policy["structured"].get("dynamic_fields", [])
        if item.get("field") in anonymized.columns
    ]
    patterns = policy_context.policy["structured"].get("deterministic_free_text_patterns", [])
    for idx in anonymized.index:
        full_name = " ".join(
            part.strip()
            for part in [original.at[idx, "First Name"] if "First Name" in original else "", original.at[idx, "Last Name"] if "Last Name" in original else ""]
            if part.strip()
        )
        known_values = [
            ("ADDRESS", original.at[idx, "Address"] if "Address" in original else ""),
            ("EMAIL", original.at[idx, "Email"] if "Email" in original else ""),
            ("PHONE_NUMBER", original.at[idx, "Contact Number"] if "Contact Number" in original else ""),
            ("NAME", full_name),
        ]
        known_values.sort(key=lambda item: len(str(item[1])), reverse=True)
        for field in dynamic_fields:
            text = str(anonymized.at[idx, field])
            for label, value in known_values:
                value = str(value).strip()
                if not value:
                    continue
                _preview, preview_count = replace_sensitive_value(text, value, "<PII_PREVIEW>")
                if not preview_count:
                    continue
                text, _count = replace_sensitive_matches(
                    text,
                    value,
                    lambda actual, label=label: registry.placeholder_for(label, actual),
                )
            for pattern_config in patterns:
                label = pattern_config["label"]
                pattern = re.compile(pattern_config["pattern"], re.IGNORECASE)
                placeholder_spans = [match.span() for match in PLACEHOLDER_RE.finditer(text)]
                matches = sorted(
                    {
                        match.group(0)
                        for match in pattern.finditer(text)
                        if not any(match.start() < end and match.end() > start for start, end in placeholder_spans)
                    },
                    key=len,
                    reverse=True,
                )
                for value in matches:
                    if value.startswith("<") and value.endswith(">"):
                        continue
                    text, _count = replace_sensitive_matches(
                        text,
                        value,
                        lambda actual, label=label: registry.placeholder_for(label, actual),
                    )
            anonymized.at[idx, field] = text
    after_mapping = registry.mappings()
    return anonymized, {placeholder: after_mapping[placeholder] for placeholder in set(after_mapping) - before}


def _write_df(df: pd.DataFrame, stage: str, run_id: str, policy_context: PolicyContext):
    path = domain_output("structured", "anonymized", stage, f"{stage}_{run_id}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    write_metadata(path, policy_context, run_id, {"stage": stage})
    return path


def _count_df_placeholders(df: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in df.astype(str).to_numpy().flatten():
        for placeholder, count in count_placeholders(value).items():
            counts[placeholder] = counts.get(placeholder, 0) + count
    return counts
