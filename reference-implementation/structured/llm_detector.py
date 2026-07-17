import json
import logging
import random
import re
import time
from typing import Any

import pandas as pd

from common.config import RuntimeConfig
from common.llm_client import detect_pii_json
from common.placeholders import PlaceholderRegistry, replace_sensitive_matches, replace_sensitive_value
from common.policy import PolicyContext


log = logging.getLogger(__name__)


def call_llm_detection(
    prompt: str,
    runtime_config: RuntimeConfig,
    recovery: bool = False,
) -> dict[str, list[str]]:
    system_prompt = (
        "You are a PII and contextual-sensitive-data detection model for banking free-text. Identify all "
        "sensitive identifiers and person-linked context still present in every supplied field. Detect explicit "
        "PII such as names, emails, phone numbers, postal addresses, government IDs, account/card/loan IDs, "
        "employee IDs, customer IDs, booking or PNR references, transaction references, medical references, "
        "and other codes tied to an individual or their activity. Also detect contextual sensitive information "
        "when linked to a person, including health conditions or treatment, employment and workplace details, "
        "family relationships, travel details, hospitals, organizations, and specific locations. Interpret "
        "common human shorthand and minor variations such as cust, emp, emp id, txn, ref, bkng, pnr, pt, appt, "
        "hosp, meds, mob, ph, and addr. Treat a standalone first or last name as a person name when the wording "
        "clearly refers to a person, for example 'after speaking with William', even when a full name appears in "
        "another field. Check shortened aliases and repeated references consistently across all fields in a record. "
        "Return only valid JSON where keys are meaningful PII categories and "
        "values are arrays of exact text snippets copied from the input. Return only values that actually occur "
        "in the input; never infer or normalize a value. Ignore placeholder tokens already in angle brackets. "
        "Do not classify generic business categories or statuses unless they are part of person-linked sensitive "
        "context. Do not include explanations or markdown."
    )
    if recovery:
        system_prompt += (
            " This is a targeted residual-recovery pass. Inspect every supplied field again and do not omit "
            "person-linked health conditions, treatment phrases, hospitals, employers, organizations, family "
            "relationships, travel providers, destinations, names (including standalone or shortened aliases in "
            "phrases such as 'speaking with William'), or reference identifiers that remain."
        )
    return detect_pii_json(system_prompt, prompt, runtime_config)


def apply_llm(
    df: pd.DataFrame,
    runtime_config: RuntimeConfig,
    policy_context: PolicyContext,
    registry: PlaceholderRegistry,
    recovery: bool = False,
    batch_size_override: int | None = None,
) -> tuple[pd.DataFrame, dict[str, str], int, float, float, float]:
    max_rows_per_batch = max(1, batch_size_override or runtime_config.llm.batch_size)
    delay_seconds = max(0.0, runtime_config.llm.delay_seconds)
    dynamic_fields = _dynamic_fields(policy_context, df)
    if not dynamic_fields:
        return df.copy().astype(str), {}, 0, 0.0, 0.0, 0.0

    anonymized_df = df.copy().astype(str)
    candidate_df = anonymized_df[[field for field, _label in dynamic_fields]].copy()
    batches = [candidate_df.iloc[i : i + max_rows_per_batch] for i in range(0, len(candidate_df), max_rows_per_batch)]
    before = set(registry.mappings())
    detected_count = 0
    excluded_wait_seconds = 0.0
    llm_inference_seconds = 0.0
    replacement_seconds = 0.0

    for idx, batch_df in enumerate(batches):
        batch_number = idx + 1
        log.info(
            "Sending structured LLM %s batch %s/%s provider=%s rows=%s fields=%s",
            "recovery" if recovery else "primary",
            batch_number,
            len(batches),
            runtime_config.llm.provider,
            len(batch_df),
            len(batch_df.columns),
        )
        prompt = _build_free_text_prompt(batch_df)
        retry_delay = 5
        for attempt in range(3):
            inference_start = time.perf_counter()
            try:
                batch_result = call_llm_detection(prompt, runtime_config, recovery=recovery)
                llm_inference_seconds += time.perf_counter() - inference_start
                break
            except Exception as exc:
                llm_inference_seconds += time.perf_counter() - inference_start
                if isinstance(exc, (json.JSONDecodeError, ValueError)):
                    if attempt < 2:
                        log.warning(
                            "Structured LLM %s batch %s returned malformed JSON; retrying attempt %s/3",
                            "recovery" if recovery else "primary",
                            batch_number,
                            attempt + 2,
                        )
                        continue
                    raise
                import requests

                if not isinstance(exc, requests.exceptions.HTTPError):
                    raise
                status = getattr(exc.response, "status_code", None)
                if status == 429 and attempt < 2:
                    log.warning("LLM batch %s hit rate limit; retrying after backoff", batch_number)
                    wait_seconds = retry_delay + random.uniform(0.5, 2.0)
                    excluded_wait_seconds += wait_seconds
                    time.sleep(wait_seconds)
                    retry_delay = min(retry_delay * 2, 30)
                    continue
                raise
        else:
            raise RuntimeError(f"LLM batch {batch_number} failed after retries")

        batch_detections: dict[str, list[Any]] = {}
        for key, values in batch_result.items():
            normalized_key = re.sub(r"\W+", "_", key.strip().upper()).strip("_")
            normalized_key = _canonical_category(normalized_key, policy_context)
            if not isinstance(values, list):
                values = [values]
            batch_detections.setdefault(normalized_key, []).extend(values)

        replacement_start = time.perf_counter()
        replaced_batch, _batch_mapping, batch_detected = _replace_detected_values(
            anonymized_df.loc[batch_df.index],
            batch_detections,
            dynamic_fields,
            registry,
        )
        for target_col, _policy_label in dynamic_fields:
            anonymized_df.loc[batch_df.index, target_col] = replaced_batch[target_col]
        replacement_seconds += time.perf_counter() - replacement_start
        detected_count += batch_detected
        log.info(
            "Completed structured LLM %s batch %s/%s categories_detected=%s values_replaced=%s",
            "recovery" if recovery else "primary",
            batch_number,
            len(batches),
            len(batch_result),
            batch_detected,
        )
        if delay_seconds and batch_number < len(batches):
            wait_seconds = delay_seconds + random.uniform(0.0, 0.5)
            excluded_wait_seconds += wait_seconds
            time.sleep(wait_seconds)

    after_mapping = registry.mappings()
    mapping = {placeholder: after_mapping[placeholder] for placeholder in set(after_mapping) - before}
    return (
        anonymized_df,
        mapping,
        detected_count,
        excluded_wait_seconds,
        llm_inference_seconds,
        replacement_seconds,
    )


def _build_free_text_prompt(batch_df: pd.DataFrame) -> str:
    records = []
    for row_number, row in enumerate(batch_df.to_dict(orient="records"), start=1):
        records.append(
            {
                "record_number_in_batch": row_number,
                "fields": {
                    key: value
                    for key, value in row.items()
                    if not _is_blank(value) and not _is_placeholder(str(value).strip())
                },
            }
        )
    return json.dumps(
        {
            "task": (
                "Extract PII snippets from these banking free-text fields. Return JSON only. "
                "Use PII category names as keys and exact snippets as array values."
            ),
            "records": records,
        },
        ensure_ascii=False,
    )


def _replace_detected_values(
    df: pd.DataFrame,
    detections: dict[str, list[Any]],
    dynamic_fields: list[tuple[str, str]],
    registry: PlaceholderRegistry,
) -> tuple[pd.DataFrame, dict[str, str], int]:
    anonymized_df = df.copy().astype(str)
    before = set(registry.mappings())
    detected_count = 0

    for category, values in detections.items():
        clean_values = sorted({str(value).strip() for value in values if value}, key=len, reverse=True)
        for value in clean_values:
            if _is_placeholder(value):
                continue
            if len(value) < 2 and not value.isdigit():
                continue
            if value.isdigit() and len(value) < 4 and category not in {"CUSTOMER_ID", "TRANSACTION_ID", "LOAN_ID", "CARD_ID"}:
                continue
            replaced_anywhere = False
            for target_col, _policy_label in dynamic_fields:
                for row_idx, cell_value in anonymized_df[target_col].items():
                    _preview, preview_count = replace_sensitive_value(str(cell_value), value, "<PII_PREVIEW>")
                    if not preview_count:
                        continue
                    updated, replacements = replace_sensitive_matches(
                        str(cell_value),
                        value,
                        lambda actual, category=category: registry.placeholder_for(category, actual),
                    )
                    if replacements:
                        anonymized_df.at[row_idx, target_col] = updated
                        replaced_anywhere = True
            if replaced_anywhere:
                detected_count += 1

    after_mapping = registry.mappings()
    mapping = {placeholder: after_mapping[placeholder] for placeholder in set(after_mapping) - before}
    return anonymized_df, mapping, detected_count


def _canonical_category(category: str, policy_context: PolicyContext) -> str:
    aliases = policy_context.policy["structured"].get("llm_category_aliases", {})
    return str(aliases.get(category, category))


def _dynamic_fields(policy_context: PolicyContext, df: pd.DataFrame) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for item in policy_context.policy["structured"].get("dynamic_fields", []):
        field = item.get("field")
        label = item.get("label", field)
        if field in df.columns:
            fields.append((field, label))
    return fields


def _is_placeholder(value: str) -> bool:
    return value.startswith("<") and value.endswith(">")


def _is_blank(value: Any) -> bool:
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "null"}
