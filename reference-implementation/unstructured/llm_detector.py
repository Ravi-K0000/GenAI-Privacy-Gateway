import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from common.config import RuntimeConfig
from common.llm_client import detect_pii_json
from common.placeholders import PLACEHOLDER_RE, PlaceholderRegistry, replace_sensitive_matches


log = logging.getLogger(__name__)

MAX_BATCH_CHARACTERS = 12000
RECORD_START_RE = re.compile(r"(?m)^(?P<number>\d+)\.\s")

CATEGORY_ALIASES = {
    "PASSPORT_NUMBER": "PASSPORT",
    "PASSPORT_NUMBERS": "PASSPORT",
    "PHONE_NUMBER": "PHONE",
    "PHONE_NUMBERS": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "EMAIL_ADDRESSES": "EMAIL",
    "ORGANIZATION_NAME": "ORGANIZATION",
    "ORGANIZATION_NAMES": "ORGANIZATION",
    "ORGANIZATIONS": "ORGANIZATION",
    "COMPANY": "ORGANIZATION",
    "COMPANY_NAME": "ORGANIZATION",
    "HOSPITAL": "HEALTHCARE_ORGANIZATION",
    "HOSPITALS": "HEALTHCARE_ORGANIZATION",
    "MEDICAL_CENTER": "HEALTHCARE_ORGANIZATION",
    "BOOKING_ID": "TRAVEL_BOOKING_ID",
    "BOOKING_REFERENCE": "TRAVEL_BOOKING_ID",
    "BOOKING_REFERENCES": "TRAVEL_BOOKING_ID",
    "PNR": "TRAVEL_BOOKING_ID",
    "PNR_REFERENCE": "TRAVEL_BOOKING_ID",
    "TRANSACTION_ID": "TRANSACTION_REFERENCE",
    "TRANSACTION_IDS": "TRANSACTION_REFERENCE",
    "MEDICAL_ID": "MEDICAL_REFERENCE",
    "MEDICAL_REFERENCES": "MEDICAL_REFERENCE",
    "MONEY": "CURRENCY_AMOUNT",
    "FINANCIAL_AMOUNT": "CURRENCY_AMOUNT",
    "CURRENCY_AMOUNTS": "CURRENCY_AMOUNT",
    "HEALTH_CONDITION": "HEALTH_CONTEXT",
    "HEALTH_CONDITIONS": "HEALTH_CONTEXT",
    "HEALTH_INFORMATION": "HEALTH_CONTEXT",
    "MEDICAL_CONDITION": "HEALTH_CONTEXT",
    "MEDICAL_CONDITIONS": "HEALTH_CONTEXT",
    "INVESTMENT": "INVESTMENT_CONTEXT",
    "INVESTMENT_ACTIVITY": "INVESTMENT_CONTEXT",
    "FAMILY_RELATIONSHIP": "FAMILY_CONTEXT",
    "EMPLOYMENT_DETAILS": "EMPLOYMENT_CONTEXT",
    "EMPLOYMENT": "EMPLOYMENT_CONTEXT",
    "EMPLOYMENT_AND_WORKPLACE_DETAILS": "EMPLOYMENT_CONTEXT",
    "EMPLOYEE_IDS": "EMPLOYEE_ID",
    "NAMES": "NAME",
    "FAMILY_RELATIONSHIPS": "FAMILY_CONTEXT",
    "TRAVEL_DETAILS": "TRAVEL_CONTEXT",
    "ADDRESSES": "ADDRESS",
    "MONETARY_VALUE": "CURRENCY_AMOUNT",
    "MONETARY_VALUES": "CURRENCY_AMOUNT",
    "AMOUNT": "CURRENCY_AMOUNT",
    "AMOUNTS": "CURRENCY_AMOUNT",
    "DATES_OF_BIRTH": "DOB",
    "PAYMENT_CARD": "CREDIT_CARD",
    "PAYMENT_CARDS": "CREDIT_CARD",
    "BANK_ACCOUNT": "ACCOUNT",
    "BANK_ACCOUNTS": "ACCOUNT",
    "IFSC_CODE": "IFSC",
    "IFSC_CODES": "IFSC",
    "PASSPORTS": "PASSPORT",
    "GOVERNMENT_IDS": "GOVERNMENT_ID",
}

CANONICAL_CATEGORIES = (
    "NAME", "DOB", "EMAIL", "PHONE", "ADDRESS", "CREDIT_CARD", "ACCOUNT", "IFSC", "PASSPORT",
    "GOVERNMENT_ID", "EMPLOYEE_ID", "TRAVEL_BOOKING_ID", "TRANSACTION_REFERENCE",
    "MEDICAL_REFERENCE", "CURRENCY_AMOUNT", "HEALTH_CONTEXT", "HEALTHCARE_ORGANIZATION",
    "EMPLOYMENT_CONTEXT", "FAMILY_CONTEXT", "TRAVEL_CONTEXT", "TRAVEL_PROVIDER",
    "INVESTMENT_CONTEXT", "ORGANIZATION", "LOCATION",
)


@dataclass(frozen=True)
class TextRecord:
    number: int
    text: str


class BatchCallError(RuntimeError):
    def __init__(self, message: str, excluded_wait_seconds: float, inference_seconds: float):
        super().__init__(message)
        self.excluded_wait_seconds = excluded_wait_seconds
        self.inference_seconds = inference_seconds


def split_numbered_text(text: str) -> tuple[str, list[TextRecord]]:
    matches = list(RECORD_START_RE.finditer(text))
    if not matches:
        return "", [TextRecord(1, text)] if text.strip() else []
    prefix = text[: matches[0].start()]
    records = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        records.append(TextRecord(int(match.group("number")), text[match.start() : end]))
    return prefix, records


def join_numbered_text(prefix: str, records: list[TextRecord]) -> str:
    return prefix + "".join(record.text for record in records)


def apply_llm(
    text: str,
    runtime_config: RuntimeConfig,
    registry: PlaceholderRegistry,
    recovery: bool = False,
    record_numbers: set[int] | None = None,
    batch_size_override: int | None = None,
) -> tuple[str, dict[str, str], int, float, float, float]:
    prefix, records = split_numbered_text(text)
    selected_indexes = [
        index
        for index, record in enumerate(records)
        if record_numbers is None or record.number in record_numbers
    ]
    if not selected_indexes:
        return text, {}, 0, 0.0, 0.0, 0.0

    max_records = max(1, batch_size_override or runtime_config.llm.batch_size)
    batches = _build_batches(records, selected_indexes, max_records, MAX_BATCH_CHARACTERS)
    delay_seconds = max(0.0, runtime_config.llm.delay_seconds)
    before = set(registry.mappings())
    detected_count = 0
    excluded_wait_seconds = 0.0
    inference_seconds = 0.0
    replacement_seconds = 0.0

    mutable_records = list(records)
    for batch_number, indexes in enumerate(batches, start=1):
        phase = "recovery" if recovery else "primary"
        replaced, retry_wait, call_seconds, batch_replacement_seconds = _process_batch(
            mutable_records,
            indexes,
            runtime_config,
            registry,
            recovery,
            phase,
            f"{batch_number}/{len(batches)}",
        )
        excluded_wait_seconds += retry_wait
        inference_seconds += call_seconds
        replacement_seconds += batch_replacement_seconds
        detected_count += replaced

        if delay_seconds and batch_number < len(batches):
            wait_seconds = delay_seconds + random.uniform(0.0, 0.5)
            excluded_wait_seconds += wait_seconds
            time.sleep(wait_seconds)

    after = registry.mappings()
    mappings = {placeholder: after[placeholder] for placeholder in set(after) - before}
    return (
        join_numbered_text(prefix, mutable_records),
        mappings,
        detected_count,
        excluded_wait_seconds,
        inference_seconds,
        replacement_seconds,
    )


def _process_batch(
    mutable_records: list[TextRecord],
    indexes: list[int],
    runtime_config: RuntimeConfig,
    registry: PlaceholderRegistry,
    recovery: bool,
    phase: str,
    batch_label: str,
) -> tuple[int, float, float, float]:
    batch_records = [mutable_records[index] for index in indexes]
    first_number = batch_records[0].number
    last_number = batch_records[-1].number
    character_count = sum(len(record.text) for record in batch_records)
    log.info(
        "Sending unstructured LLM %s batch %s provider=%s records=%s range=%s-%s chars=%s",
        phase,
        batch_label,
        runtime_config.llm.provider,
        len(batch_records),
        first_number,
        last_number,
        character_count,
    )
    try:
        result, retry_wait, call_seconds = _call_with_retries(
            _build_prompt(batch_records),
            runtime_config,
            recovery,
            batch_label,
            first_number,
            last_number,
        )
    except BatchCallError as exc:
        if len(indexes) <= 1 or not _malformed_response(exc):
            raise
        midpoint = len(indexes) // 2
        left = indexes[:midpoint]
        right = indexes[midpoint:]
        log.warning(
            "Unstructured LLM %s batch %s returned invalid JSON; "
            "splitting records=%s into %s and %s",
            phase,
            batch_label,
            len(indexes),
            len(left),
            len(right),
        )
        totals = [0, exc.excluded_wait_seconds, exc.inference_seconds, 0.0]
        for suffix, subset in (("a", left), ("b", right)):
            child = _process_batch(
                mutable_records,
                subset,
                runtime_config,
                registry,
                recovery,
                phase,
                f"{batch_label}{suffix}",
            )
            totals = [current + value for current, value in zip(totals, child)]
        return totals[0], totals[1], totals[2], totals[3]

    detections = _normalize_detections(result)
    replacement_started = time.perf_counter()
    replaced = 0
    for index in indexes:
        updated, record_replacements = _replace_record(
            mutable_records[index].text,
            detections,
            registry,
        )
        mutable_records[index] = TextRecord(mutable_records[index].number, updated)
        replaced += record_replacements
    replacement_seconds = time.perf_counter() - replacement_started
    log.info(
        "Completed unstructured LLM %s batch %s categories_detected=%s values_replaced=%s",
        phase,
        batch_label,
        len(result),
        replaced,
    )
    return replaced, retry_wait, call_seconds, replacement_seconds


def _build_batches(
    records: list[TextRecord],
    selected_indexes: list[int],
    max_records: int,
    max_characters: int,
) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    current_characters = 0
    for index in selected_indexes:
        record_length = len(records[index].text)
        would_overflow = current and (
            len(current) >= max_records or current_characters + record_length > max_characters
        )
        if would_overflow:
            batches.append(current)
            current = []
            current_characters = 0
        current.append(index)
        current_characters += record_length
    if current:
        batches.append(current)
    return batches


def _build_prompt(records: list[TextRecord]) -> str:
    return json.dumps(
        {
            "task": (
                "Extract all PII and person-linked sensitive text. Return JSON only, using canonical "
                "category names as keys and exact source snippets as array values."
            ),
            "records": [
                {"record_number": record.number, "text": record.text.rstrip()}
                for record in records
            ],
        },
        ensure_ascii=False,
    )


def _call_with_retries(
    prompt: str,
    runtime_config: RuntimeConfig,
    recovery: bool,
    batch_number: str,
    first_record: int,
    last_record: int,
) -> tuple[dict[str, list[str]], float, float]:
    excluded_wait_seconds = 0.0
    inference_seconds = 0.0
    retry_delay = 5.0
    for attempt in range(1, 4):
        started = time.perf_counter()
        try:
            result = _call_llm(prompt, runtime_config, recovery)
            inference_seconds += time.perf_counter() - started
            return result, excluded_wait_seconds, inference_seconds
        except Exception as exc:
            inference_seconds += time.perf_counter() - started
            malformed = isinstance(exc, (ValueError, json.JSONDecodeError))
            if malformed or attempt >= 3 or not _retryable(exc):
                raise BatchCallError(
                    f"Unstructured LLM batch {batch_number} records {first_record}-{last_record} "
                    f"failed after {attempt} attempt(s): {type(exc).__name__}",
                    excluded_wait_seconds,
                    inference_seconds,
                ) from exc
            wait_seconds = retry_delay + random.uniform(0.5, 2.0)
            log.warning(
                "Unstructured LLM batch %s records=%s-%s provider=%s attempt=%s failed=%s; "
                "retrying after backoff",
                batch_number,
                first_record,
                last_record,
                runtime_config.llm.provider,
                attempt,
                type(exc).__name__,
            )
            excluded_wait_seconds += wait_seconds
            time.sleep(wait_seconds)
            retry_delay = min(retry_delay * 2, 30.0)
    raise RuntimeError("Unreachable retry state")


def _call_llm(
    prompt: str,
    runtime_config: RuntimeConfig,
    recovery: bool,
) -> dict[str, list[str]]:
    system_prompt = (
        "You are a PII and contextual-sensitive-data detection model for extracted unstructured banking text. "
        "Identify every explicit identifier still present, including names, dates of birth, emails, phone "
        "numbers, addresses, payment cards, bank accounts, IFSC codes, passports, government IDs, employee "
        "IDs, booking/PNR references, transaction references, medical references, and monetary values tied to "
        "a person or activity. Also identify exact person-linked sensitive context, including health conditions, "
        "diagnosis, treatment or medication, employment and workplace details, family relationships, travel "
        "details, investment activity, hospitals, organizations, and specific locations. Understand shorthand "
        "such as cust, emp, txn, ref, bkng, pnr, appt, hosp, meds, mob, ph, and addr. Return only valid JSON "
        f"using only these category keys: {', '.join(CANONICAL_CATEGORIES)}. Omit categories with no values. "
        "Values must be arrays of exact snippets copied from the supplied records. "
        "Do not infer, normalize, explain, or use markdown. Ignore existing placeholder tokens in angle brackets. "
        "Do not return a placeholder by itself. Generic business language is not sensitive unless linked to an "
        "individual or their activity."
    )
    if recovery:
        system_prompt += (
            " This is a targeted residual-recovery pass. Recheck every supplied record carefully, especially "
            "health, employment, family, travel, investment, monetary and reference information missed earlier."
        )
    return detect_pii_json(system_prompt, prompt, runtime_config)


def _malformed_response(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, (ValueError, json.JSONDecodeError)):
            return True
        current = current.__cause__
    return False


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (ValueError, json.JSONDecodeError, TimeoutError, ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", "")).lower()
        return any(
            marker in code
            for marker in ("throttl", "toomanyrequests", "timeout", "serviceunavailable", "internalserver")
        )
    name = type(exc).__name__.lower()
    return any(marker in name for marker in ("throttl", "timeout", "endpointconnection", "connectionclosed"))


def _normalize_detections(result: dict[str, Any]) -> dict[str, list[str]]:
    detections: dict[str, list[str]] = {}
    for key, values in result.items():
        category = re.sub(r"\W+", "_", str(key).strip().upper()).strip("_")
        category = CATEGORY_ALIASES.get(category, category)
        if not isinstance(values, list):
            values = [values]
        detections.setdefault(category, []).extend(str(value).strip() for value in values if value)
    return detections


def _replace_record(
    text: str,
    detections: dict[str, list[str]],
    registry: PlaceholderRegistry,
) -> tuple[str, int]:
    updated = text
    accepted_values = 0
    for category, values in detections.items():
        for value in sorted(set(values), key=len, reverse=True):
            if not value or _placeholder_only(value):
                continue
            if len(value) < 2 and not value.isdigit():
                continue
            actual_category = _category_override(category, value)
            replaced, count = _replace_outside_placeholders(
                updated,
                value,
                actual_category,
                registry,
            )
            if count:
                updated = replaced
                accepted_values += 1
    return updated, accepted_values


def _replace_outside_placeholders(
    text: str,
    sensitive_value: str,
    category: str,
    registry: PlaceholderRegistry,
) -> tuple[str, int]:
    parts: list[str] = []
    replaced_count = 0
    position = 0
    for placeholder_match in PLACEHOLDER_RE.finditer(text):
        segment = text[position : placeholder_match.start()]
        replaced_segment, count = replace_sensitive_matches(
            segment,
            sensitive_value,
            lambda actual: registry.placeholder_for(category, actual),
        )
        parts.extend((replaced_segment, placeholder_match.group(0)))
        replaced_count += count
        position = placeholder_match.end()
    tail, count = replace_sensitive_matches(
        text[position:],
        sensitive_value,
        lambda actual: registry.placeholder_for(category, actual),
    )
    parts.append(tail)
    return "".join(parts), replaced_count + count


def _placeholder_only(value: str) -> bool:
    if PLACEHOLDER_RE.fullmatch(value):
        return True
    remainder = PLACEHOLDER_RE.sub("", value)
    return not remainder.strip(" <>\t\r\n")


def _category_override(category: str, value: str) -> str:
    checks = (
        ("EMPLOYEE_ID", r"\bEMP(?:[-\s][A-Z0-9-]+|[A-Z]+\d{4,})\b"),
        ("TRAVEL_BOOKING_ID", r"\b(?:PNR|TRV)[A-Z0-9-]+\b"),
        ("TRANSACTION_REFERENCE", r"\bTXN[-\s]?[A-Z0-9-]+\b"),
        ("MEDICAL_REFERENCE", r"\bMED(?:[-\s][A-Z0-9-]+|\d{4,})\b"),
        ("EMAIL", r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    )
    for canonical, pattern in checks:
        if re.search(pattern, value, re.IGNORECASE):
            return canonical
    if category == "MEDICAL_REFERENCE":
        return "HEALTH_CONTEXT"
    return category
