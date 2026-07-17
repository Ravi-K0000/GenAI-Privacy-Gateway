import logging
import re
import time

from common.config import RuntimeConfig
from common.mapping_store import write_mapping
from common.metadata import write_metadata
from common.metrics import Timer
from common.paths import domain_output, sample_input
from common.placeholders import PlaceholderRegistry, count_placeholders
from common.policy import PolicyContext
from unstructured.llm_detector import apply_llm, split_numbered_text
from unstructured.validation import residual_record_numbers, validate_unstructured_anonymization


log = logging.getLogger(__name__)

SUPPLEMENTAL_STATIC_PATTERNS = (
    (
        "EMPLOYEE_ID",
        re.compile(r"\bEmployee\s+ID\s+(?P<value>[A-Z0-9-]{4,})\b", re.IGNORECASE),
    ),
    (
        "TRAVEL_BOOKING_ID",
        re.compile(r"\bTravel\s+booking\s+ID\s*:?\s*(?P<value>[A-Z0-9-]{4,})\b", re.IGNORECASE),
    ),
    (
        "TRANSACTION_REFERENCE",
        re.compile(r"\bTransaction\s+(?P<value>TXN-[A-Z0-9-]{5,})\b", re.IGNORECASE),
    ),
    (
        "MEDICAL_REFERENCE",
        re.compile(r"\bmedical\s+ref(?:erence)?\s*:?\s*(?P<value>MED-[A-Z0-9-]{5,})\b", re.IGNORECASE),
    ),
    (
        "PASSPORT",
        re.compile(r"\bPassport\s+number\s*:?\s*(?P<value>[A-Z][0-9]{7,9})\b", re.IGNORECASE),
    ),
    (
        "CURRENCY_AMOUNT",
        re.compile(
            r"(?P<value>(?:[$€£₹]\s?\d[\d,]*(?:\.\d{1,2})?|"
            r"(?:INR|USD|EUR|GBP)\s+\d[\d,]*(?:\.\d{1,2})?|"
            r"\d[\d,]*(?:\.\d{1,2})?\s+pounds?))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "HEALTH_CONTEXT",
        re.compile(
            r"(?P<value>\b(?:diabetes|cancer|asthma|hypertension|migraine|thyroid)"
            r"(?:\s+(?:diagnosis|medication|treatment|consultation))?\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "INVESTMENT_CONTEXT",
        re.compile(
            r"(?P<value>\b(?:ETF|investment|portfolio|securities)"
            r"(?:\s+(?:investment|advice|discussion|discussions|review)){0,2}\b)",
            re.IGNORECASE,
        ),
    ),
)


def run_unstructured_anonymization(
    run_id: str,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
) -> dict[str, object]:
    input_path = sample_input("unstructured")
    log.debug("Unstructured anonymization input=%s", input_path)
    raw_text = input_path.read_text(encoding="utf-8")
    registry = PlaceholderRegistry(run_id)
    anonymization_timer = Timer()

    static_text, static_mapping = anonymization_timer.measure(
        _run_static_anonymization, raw_text, policy_context, registry
    )
    static_seconds = anonymization_timer.elapsed_seconds
    static_path = _write_text(static_text, "static", run_id, policy_context)
    log.debug("Unstructured static anonymization wrote %s mapping_entries=%s", static_path, len(static_mapping))
    write_mapping("unstructured", run_id, "static", static_mapping, runtime_config, policy_context)

    dynamic_started = time.perf_counter()
    (
        dynamic_text,
        dynamic_mapping,
        dynamic_detected,
        excluded_wait_seconds,
        llm_inference_seconds,
        replacement_seconds,
    ) = apply_llm(static_text, runtime_config, registry)

    recovery_records = residual_record_numbers(dynamic_text, policy_context)
    recovery_detected = 0
    if recovery_records:
        log.warning(
            "Unstructured contextual residual scan selected %s records for targeted LLM recovery",
            len(recovery_records),
        )
        (
            dynamic_text,
            recovery_mapping,
            recovery_detected,
            recovery_wait,
            recovery_inference,
            recovery_replacement,
        ) = apply_llm(
            dynamic_text,
            runtime_config,
            registry,
            recovery=True,
            record_numbers=set(recovery_records),
        )
        dynamic_mapping.update(recovery_mapping)
        dynamic_detected += recovery_detected
        excluded_wait_seconds += recovery_wait
        llm_inference_seconds += recovery_inference
        replacement_seconds += recovery_replacement

    dynamic_wall_seconds = time.perf_counter() - dynamic_started
    dynamic_benchmark_seconds = max(0.0, dynamic_wall_seconds - excluded_wait_seconds)
    anonymization_timer.elapsed_seconds += dynamic_benchmark_seconds
    dynamic_path = _write_text(dynamic_text, "dynamic", run_id, policy_context)
    log.debug(
        "Unstructured dynamic anonymization wrote %s detected=%s mapping_entries=%s "
        "wall_seconds=%.3f excluded_wait_seconds=%.3f benchmark_seconds=%.3f",
        dynamic_path,
        dynamic_detected,
        len(dynamic_mapping),
        dynamic_wall_seconds,
        excluded_wait_seconds,
        dynamic_benchmark_seconds,
    )
    write_mapping("unstructured", run_id, "dynamic", dynamic_mapping, runtime_config, policy_context)

    final_mapping = registry.mappings()
    final_path = _write_text(dynamic_text, "final", run_id, policy_context)
    log.debug("Unstructured final anonymized output wrote %s final_mapping_entries=%s", final_path, len(final_mapping))
    write_mapping("unstructured", run_id, "final", final_mapping, runtime_config, policy_context)
    final_placeholder_counts = count_placeholders(dynamic_text)
    validation = validate_unstructured_anonymization(dynamic_text, run_id, policy_context)
    log.info(
        "Unstructured anonymization validation status=%s residual_pii_count=%s affected_records=%s report=%s",
        validation["status"],
        validation["residual_pii_count"],
        validation["affected_record_count"],
        validation["path"],
    )

    return {
        "run_id": run_id,
        "input_path": input_path,
        "static_path": static_path,
        "dynamic_path": dynamic_path,
        "final_path": final_path,
        "records": _count_records(raw_text),
        "sensitive_values_detected": len(static_mapping) + dynamic_detected,
        "mappings_created": len(final_mapping),
        "anonymization_seconds": anonymization_timer.elapsed_seconds,
        "static_anonymization_seconds": static_seconds,
        "llm_inference_seconds": llm_inference_seconds,
        "dynamic_replacement_seconds": replacement_seconds,
        "excluded_wait_seconds": excluded_wait_seconds,
        "recovery_rows": len(recovery_records),
        "recovery_values_detected": recovery_detected,
        "anonymization_validation_status": validation["status"],
        "residual_pii_count": validation["residual_pii_count"],
        "validation_path": validation["path"],
        "expected_placeholders": sorted(final_placeholder_counts),
        "expected_placeholder_counts": final_placeholder_counts,
    }


def _run_static_anonymization(
    text: str,
    policy_context: PolicyContext,
    registry: PlaceholderRegistry,
) -> tuple[str, dict[str, str]]:
    anonymized = text
    before = set(registry.mappings())
    patterns = policy_context.policy["unstructured"].get("regex_patterns", {})
    for label, pattern in patterns.items():
        anonymized = _replace_pattern(anonymized, label, re.compile(pattern), registry)

    for label, pattern in SUPPLEMENTAL_STATIC_PATTERNS:
        anonymized = _replace_pattern(anonymized, label, pattern, registry, "value")

    name_candidates: set[str] = set()
    for item in policy_context.policy["unstructured"].get("name_context_patterns", []):
        flags = re.IGNORECASE if "IGNORECASE" in item.get("flags", []) else 0
        pattern = re.compile(item["pattern"], flags)
        for match in list(pattern.finditer(anonymized)):
            value = match.groupdict().get("name")
            if value:
                name_candidates.add(value.strip())

    anonymized = _replace_candidate_values(anonymized, "NAME", name_candidates, registry)

    contextual_candidates: set[tuple[str, str]] = set()
    for item in policy_context.policy["unstructured"].get("contextual_entity_patterns", []):
        flags = re.IGNORECASE if "IGNORECASE" in item.get("flags", []) else 0
        pattern = re.compile(item["pattern"], flags)
        label = item.get("label", "CONTEXTUAL_IDENTIFIER")
        group = item.get("group", "value")
        for match in list(pattern.finditer(anonymized)):
            value = match.groupdict().get(group)
            if value:
                contextual_candidates.add((label, value.strip()))

    candidates_by_label: dict[str, set[str]] = {}
    for label, value in contextual_candidates:
        candidates_by_label.setdefault(label, set()).add(value)
    for label, values in candidates_by_label.items():
        anonymized = _replace_candidate_values(anonymized, label, values, registry)
    after = registry.mappings()
    return anonymized, {placeholder: after[placeholder] for placeholder in set(after) - before}


def _replace_pattern(
    text: str,
    label: str,
    pattern: re.Pattern[str],
    registry: PlaceholderRegistry,
    group: str | None = None,
) -> str:
    def replacement(match: re.Match[str]) -> str:
        value = match.group(group) if group else match.group(0)
        if not value or (value.startswith("<") and value.endswith(">")):
            return match.group(0)
        placeholder = registry.placeholder_for(label, value)
        if not group:
            return placeholder
        relative_start = match.start(group) - match.start()
        relative_end = match.end(group) - match.start()
        return f"{match.group(0)[:relative_start]}{placeholder}{match.group(0)[relative_end:]}"

    return pattern.sub(replacement, text)


def _replace_candidate_values(
    text: str,
    label: str,
    values: set[str],
    registry: PlaceholderRegistry,
) -> str:
    usable = sorted(
        {value for value in values if value and not (value.startswith("<") and value.endswith(">"))},
        key=len,
        reverse=True,
    )
    if not usable:
        return text
    alternatives = "|".join(re.escape(value) for value in usable)
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda match: registry.placeholder_for(label, match.group(0)), text)


def _write_text(text: str, stage: str, run_id: str, policy_context: PolicyContext):
    path = domain_output("unstructured", "anonymized", stage, f"{stage}_{run_id}.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    write_metadata(path, policy_context, run_id, {"stage": stage})
    return path


def _count_records(text: str) -> int:
    _prefix, records = split_numbered_text(text)
    return len(records)
