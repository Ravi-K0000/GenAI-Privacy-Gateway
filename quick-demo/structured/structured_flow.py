import datetime
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from common.mock_mapping_store import save_mapping
from common.paths import (
    EXTERNAL_RESULTS_DIR,
    PRIVACY_POLICY_FILE,
    STRUCTURED_ANON_DIR,
    STRUCTURED_MAPPING_DIR,
    STRUCTURED_SAMPLE,
    ensure_output_structure,
)

logger = logging.getLogger(__name__)


def _load_policy() -> dict:
    with open(PRIVACY_POLICY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["structured"]


STRUCTURED_POLICY = _load_policy()


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def run_static_anonymization(df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, str]]:
    mapping: Dict[str, str] = {}
    anonymized = df.copy().astype(str)

    for rule in STRUCTURED_POLICY["static_fields"]:
        column = rule["field"]
        label = rule["label"]
        if column not in anonymized.columns:
            continue
        for idx, value in anonymized[column].items():
            placeholder = f"<{label}_{idx}>"
            mapping[placeholder] = str(value)
            anonymized.at[idx, column] = placeholder

    return anonymized, mapping


def run_dynamic_anonymization(df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, str]]:
    mapping: Dict[str, str] = {}
    anonymized = df.copy().astype(str)
    for rule in STRUCTURED_POLICY["dynamic_fields"]:
        column = rule["field"]
        label = rule["label"]
        if column not in anonymized.columns:
            continue
        for value in sorted(set(anonymized[column].dropna().astype(str))):
            if not value or value.startswith("<"):
                continue
            digest = hashlib.md5(f"{label}|{value}".encode("utf-8")).hexdigest()[:6]
            placeholder = f"<LLM_{label}_{digest}>"
            anonymized.loc[anonymized[column].astype(str) == value, column] = placeholder
            mapping[placeholder] = value

    return anonymized, mapping


def _export_dataframe(df: pd.DataFrame, request_id: str, prefix: str) -> Path:
    out_path = STRUCTURED_ANON_DIR / f"{prefix}_{request_id}_{_timestamp()}.csv"
    df.to_csv(out_path, index=False)
    logger.info("Wrote structured %s CSV: %s", prefix, out_path)
    return out_path


def _export_mapping(mapping: Dict[str, str], request_id: str, stage: str) -> Path:
    out_path = STRUCTURED_MAPPING_DIR / f"mapping_{stage}_{request_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    if stage == "final":
        save_mapping(mapping, request_id, stage, table_name="mappings_table")
    logger.info("Wrote structured %s mapping: %s", stage, out_path)
    return out_path


def run_anonymization(request_id: str = None) -> str:
    ensure_output_structure()
    request_id = request_id or str(uuid.uuid4())
    if not STRUCTURED_SAMPLE.exists():
        raise FileNotFoundError(f"Missing structured sample: {STRUCTURED_SAMPLE}")

    df = pd.read_csv(STRUCTURED_SAMPLE).astype(str)
    logger.info("Loaded %s structured rows", len(df))

    static_df, static_mapping = run_static_anonymization(df)
    _export_dataframe(static_df, request_id, "static")
    _export_mapping(static_mapping, request_id, "static")

    dynamic_df, dynamic_mapping = run_dynamic_anonymization(static_df)
    _export_dataframe(dynamic_df, request_id, "dynamic")
    _export_mapping(dynamic_mapping, request_id, "dynamic")

    final_mapping = {**static_mapping, **dynamic_mapping}
    _export_dataframe(dynamic_df, request_id, "final")
    _export_mapping(final_mapping, request_id, "final")
    return request_id


def _value(record: dict, column: str, default: Any = None) -> Any:
    value = record.get(column, default)
    return default if pd.isna(value) else value


def _number(record: dict, column: str, default: float = 0.0) -> float:
    try:
        return float(_value(record, column, default))
    except (TypeError, ValueError):
        return default


def build_mock_analysis(df: pd.DataFrame) -> dict:
    customer_profiles = []
    for record in df.to_dict(orient="records"):
        credit_limit = _number(record, "Credit Limit")
        card_balance = _number(record, "Credit Card Balance")
        utilization = card_balance / credit_limit if credit_limit > 0 else 0.0
        first_name = str(_value(record, "First Name", "")).strip()
        last_name = str(_value(record, "Last Name", "")).strip()

        customer_profiles.append(
            {
                "CustomerID": str(_value(record, "Customer ID", "")),
                "Name": f"{first_name} {last_name}".strip(),
                "Age": int(_number(record, "Age")),
                "Gender": _value(record, "Gender", ""),
                "City": _value(record, "City", ""),
                "Email": _value(record, "Email", ""),
                "Account": {
                    "Type": _value(record, "Account Type", ""),
                    "Balance": _number(record, "Account Balance"),
                    "Opened": _value(record, "Date Of Account Opening", ""),
                },
                "Loan": {
                    "LoanID": str(_value(record, "Loan ID", "")),
                    "Amount": _number(record, "Loan Amount"),
                    "Type": _value(record, "Loan Type", ""),
                    "Status": _value(record, "Loan Status", ""),
                },
                "Card": {
                    "CardID": str(_value(record, "CardID", "")),
                    "Type": _value(record, "Card Type", ""),
                    "CreditLimit": credit_limit,
                    "Balance": card_balance,
                },
                "Feedback": {
                    "Type": _value(record, "Feedback Type", ""),
                    "Resolution": _value(record, "Resolution Status", ""),
                },
                "CreditUtilization": utilization,
                "HighUtilizationFlag": utilization >= 0.8,
            }
        )

    utilizations = [profile["CreditUtilization"] for profile in customer_profiles]
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "aggregate_stats": {
            "total_customers": len(customer_profiles),
            "flagged_high_utilization": sum(profile["HighUtilizationFlag"] for profile in customer_profiles),
            "average_utilization": sum(utilizations) / len(utilizations) if utilizations else 0.0,
        },
        "customer_profiles": customer_profiles,
    }


def run_external_processor() -> Path:
    csv_files = sorted(STRUCTURED_ANON_DIR.glob("final_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csv_files:
        raise FileNotFoundError(f"No structured final CSV found in {STRUCTURED_ANON_DIR}")

    df = pd.read_csv(csv_files[0])
    out_path = EXTERNAL_RESULTS_DIR / f"external_output_str_{_timestamp()}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build_mock_analysis(df), f, indent=2, ensure_ascii=False)
    logger.info("Wrote structured mock external output: %s", out_path)
    return out_path
