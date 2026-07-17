import json
import re
from pathlib import Path

import pandas as pd

from common.metadata import write_metadata
from common.paths import domain_output
from common.policy import PolicyContext


FREE_TEXT_COLUMNS = [
    "Transaction Description",
    "Loan Officer Remarks",
    "Case Resolution Notes",
    "Customer Notes",
]
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d(). -]{8,17}\d(?!\d)")
VALID_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*_[0-9a-f]{10}>")
PLACEHOLDER_BODY_RE = re.compile(r"[A-Z][A-Z0-9_]*_[0-9a-f]{10}")
ANGLE_TOKEN_RE = re.compile(r"<+[^<>\r\n]+>+")


def validate_structured_anonymization(
    original_df: pd.DataFrame,
    anonymized_df: pd.DataFrame,
    run_id: str,
    policy_context: PolicyContext,
    mapped_placeholders: set[str] | None = None,
) -> dict[str, object]:
    columns = [column for column in FREE_TEXT_COLUMNS if column in anonymized_df.columns]
    counts = {"email": 0, "phone": 0, "known_name": 0, "known_address": 0, "malformed_placeholder": 0}
    contextual_patterns = _contextual_patterns(policy_context)
    affected_rows: set[int] = set()

    for position, (row_index, row) in enumerate(anonymized_df.iterrows()):
        text = "\n".join(str(row[column]) for column in columns)
        without_placeholders = _mask_recognized_placeholders(text, mapped_placeholders)
        email_count = len(EMAIL_RE.findall(without_placeholders))
        phone_count = len(PHONE_RE.findall(without_placeholders))
        malformed_count = _count_unrecognized_placeholders(text, mapped_placeholders)
        counts["email"] += email_count
        counts["phone"] += phone_count
        counts["malformed_placeholder"] += malformed_count
        contextual_count = 0
        for label, pattern, group in contextual_patterns:
            matches = list(pattern.finditer(without_placeholders))
            match_count = sum(1 for match in matches if (match.groupdict().get(group) or "").strip())
            counts[label] = counts.get(label, 0) + match_count
            contextual_count += match_count

        original = original_df.iloc[position]
        full_name = " ".join(
            part.strip() for part in [str(original.get("First Name", "")), str(original.get("Last Name", ""))] if part.strip()
        )
        address = str(original.get("Address", "")).strip()
        if full_name:
            counts["known_name"] += sum(
                text_value.lower().count(full_name.lower()) for text_value in [str(row[column]) for column in columns]
            )
        if address:
            counts["known_address"] += sum(
                text_value.lower().count(address.lower()) for text_value in [str(row[column]) for column in columns]
            )
        if email_count or phone_count or malformed_count or contextual_count or (
            full_name and full_name.lower() in text.lower()
        ) or (address and address.lower() in text.lower()):
            affected_rows.add(int(position) + 1)

    residual_count = sum(counts.values())
    status = "passed" if residual_count == 0 else "completed_with_warnings"
    report = {
        **policy_context.metadata(run_id),
        "status": status,
        "residual_pii_count": residual_count,
        "counts": counts,
        "affected_row_count": len(affected_rows),
        "affected_rows": sorted(affected_rows),
        "note": "The report contains counts and row numbers only; raw detected values are not persisted.",
    }
    path = domain_output("structured", "validation", f"anonymization_validation_{run_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_metadata(path, policy_context, run_id)
    report["path"] = path
    return report


def contextual_residual_rows(
    anonymized_df: pd.DataFrame,
    policy_context: PolicyContext,
    mapped_placeholders: set[str] | None = None,
) -> list[int]:
    columns = [column for column in FREE_TEXT_COLUMNS if column in anonymized_df.columns]
    patterns = _contextual_patterns(policy_context)
    affected: list[int] = []
    for row_index, row in anonymized_df.iterrows():
        text = "\n".join(str(row[column]) for column in columns)
        without_placeholders = _mask_recognized_placeholders(text, mapped_placeholders)
        if any(
            (match.groupdict().get(group) or "").strip()
            for _label, pattern, group in patterns
            for match in pattern.finditer(without_placeholders)
        ):
            affected.append(row_index)
    return affected


def _contextual_patterns(policy_context: PolicyContext):
    compiled = []
    for item in policy_context.policy["structured"].get("contextual_validation_patterns", []):
        flags = re.IGNORECASE if "IGNORECASE" in item.get("flags", []) else 0
        compiled.append((item.get("label", "contextual").lower(), re.compile(item["pattern"], flags), item.get("group", "value")))
    return compiled


def _canonical_placeholder(token: str) -> str | None:
    body = token.strip("<>")
    if not PLACEHOLDER_BODY_RE.fullmatch(body):
        return None
    return f"<{body}>"


def _is_recognized_placeholder(token: str, mapped_placeholders: set[str] | None) -> bool:
    canonical = _canonical_placeholder(token)
    if canonical is None:
        return False
    return mapped_placeholders is None or canonical in mapped_placeholders


def _mask_recognized_placeholders(text: str, mapped_placeholders: set[str] | None) -> str:
    return ANGLE_TOKEN_RE.sub(
        lambda match: "" if _is_recognized_placeholder(match.group(0), mapped_placeholders) else match.group(0),
        text,
    )


def _count_unrecognized_placeholders(text: str, mapped_placeholders: set[str] | None) -> int:
    return sum(
        1
        for token in ANGLE_TOKEN_RE.findall(text)
        if not _is_recognized_placeholder(token, mapped_placeholders)
    )
