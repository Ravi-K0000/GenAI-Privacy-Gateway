import datetime
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.mock_mapping_store import save_mapping
from common.paths import (
    EXTERNAL_RESULTS_DIR,
    PRIVACY_POLICY_FILE,
    UNSTRUCTURED_ANON_DIR,
    UNSTRUCTURED_MAPPING_DIR,
    UNSTRUCTURED_SAMPLE_DIR,
    ensure_output_structure,
)

logger = logging.getLogger(__name__)


def _load_policy() -> dict:
    with open(PRIVACY_POLICY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["unstructured"]


UNSTRUCTURED_POLICY = _load_policy()
REGEX_PATTERNS = UNSTRUCTURED_POLICY["regex_patterns"]
NAME_CONTEXT_PATTERNS = tuple(
    re.compile(
        rule["pattern"],
        re.IGNORECASE if "IGNORECASE" in rule.get("flags", []) else 0,
    )
    for rule in UNSTRUCTURED_POLICY["name_context_patterns"]
)


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _placeholder(label: str) -> str:
    return f"<{label}_{uuid.uuid4().hex[:8]}>"


def _latest_sample_text() -> Path:
    files = sorted(UNSTRUCTURED_SAMPLE_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No .txt files found in {UNSTRUCTURED_SAMPLE_DIR}")
    return files[0]


def anonymize_names(content: str) -> tuple[str, Dict[str, str]]:
    mappings: Dict[str, str] = {}
    anonymized = content

    def replace_name(match: re.Match) -> str:
        parts = match.group("name").split()
        first = _placeholder("FIRSTNAME")
        mappings[first] = parts[0]
        replacement = first
        if len(parts) > 1:
            last = _placeholder("LASTNAME")
            mappings[last] = " ".join(parts[1:])
            replacement = f"{first} {last}"
        return f"{match.group('prefix')}{replacement}{match.group('suffix')}"

    for pattern in NAME_CONTEXT_PATTERNS:
        anonymized = pattern.sub(replace_name, anonymized)
    return anonymized, mappings


def anonymize_text(content: str) -> tuple[str, Dict[str, str]]:
    anonymized, mappings = anonymize_names(content)
    for label, pattern in REGEX_PATTERNS.items():
        for match in sorted(set(re.findall(pattern, anonymized)), key=len, reverse=True):
            placeholder = _placeholder(label)
            anonymized = re.sub(re.escape(match), placeholder, anonymized)
            mappings[placeholder] = match
    return anonymized, mappings


def _export_text(text: str, request_id: str, prefix: str) -> Path:
    out_path = UNSTRUCTURED_ANON_DIR / f"{prefix}_{request_id}_{_timestamp()}.txt"
    out_path.write_text(text, encoding="utf-8")
    logger.info("Wrote unstructured %s text: %s", prefix, out_path)
    return out_path


def _export_mapping(mapping: Dict[str, str], request_id: str, stage: str) -> Path:
    out_path = UNSTRUCTURED_MAPPING_DIR / f"mapping_{stage}_{request_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    if stage == "final":
        save_mapping(mapping, request_id, stage, table_name="mappings_unstructured_table")
    logger.info("Wrote unstructured %s mapping: %s", stage, out_path)
    return out_path


def run_anonymization(request_id: str = None) -> str:
    ensure_output_structure()
    request_id = request_id or str(uuid.uuid4())
    raw_text = _latest_sample_text().read_text(encoding="utf-8")

    anonymized, mapping = anonymize_text(raw_text)
    _export_text(anonymized, request_id, "anon_static")
    _export_mapping(mapping, request_id, "static")
    _export_text(anonymized, request_id, "anon_dynamic")
    _export_mapping({}, request_id, "llm")
    _export_text(anonymized, request_id, "anon_final")
    _export_mapping(mapping, request_id, "final")
    return request_id


def split_records(text: str) -> List[Tuple[int, str]]:
    matches = re.findall(r"(?ms)^\s*(\d+)\.\s*(.*?)(?=^\s*\d+\.\s|\Z)", text)
    if matches:
        return [(int(number), body.strip()) for number, body in matches]
    return [(1, text.strip())] if text.strip() else []


def _contains(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


PRIVACY_WEIGHTS = {
    "Credit Card": 10,
    "Bank Account": 8,
    "Passport": 8,
    "Medical Info": 9,
    "Address": 4,
    "Email": 2,
    "Phone": 2,
    "DOB": 4,
}


def detect_privacy_types(text: str) -> List[str]:
    rules = {
        "Credit Card": (r"\b(?:credit|debit)\s+card\b", r"<(?:LLM_)?CREDIT_CARD_[^>]+>", r"<(?:LLM_)?CARD_ID_[^>]+>"),
        "Bank Account": (r"\baccount\s+number\b", r"<(?:LLM_)?ACCOUNT_[^>]+>", r"\bIFSC\b", r"<(?:LLM_)?IFSC_[^>]+>"),
        "Passport": (r"\bpassport\b", r"<(?:LLM_)?PASSPORT_[^>]+>"),
        "Medical Info": (r"\bhospital\b", r"\bdiagnosis\b", r"\bdiabetes\b", r"\bmedication\b", r"\bmedical\b"),
        "Address": (r"\b(?:residential\s+)?address\b", r"<(?:LLM_)?ADDRESS_[^>]+>"),
        "Email": (r"\bemail\b", r"\bcontact\b", r"<(?:LLM_)?EMAIL_[^>]+>"),
        "Phone": (r"\bphone\b", r"<(?:LLM_)?PHONE(?:_NUMBER)?_[^>]+>"),
        "DOB": (r"\bDOB\b", r"<(?:LLM_)?DOB_[^>]+>"),
    }
    return [name for name, patterns in rules.items() if _contains(text, *patterns)]


def _risk(score: int, detected_types: List[str]) -> str:
    if detected_types == ["Medical Info"]:
        return "Medium"
    if score >= 8:
        return "High"
    if score >= 4:
        return "Medium"
    return "Low"


def _first_match(text: str, patterns: Tuple[str, ...]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" \t\r\n,.;")
    return None


def extract_rehydratable_fields(text: str) -> Dict[str, str]:
    fields = {}
    candidates = (
        ("Name", _first_match(text, (r"\bCustomer\s+(.+?)\s+\(DOB\b", r"\b(?:Mr|Mrs|Ms)\.?\s+(.+?)\s+visited\b", r"\b(?:spouse|mother|father|parent)\s+of\s+(.+?)\s+works\b"))),
        ("Address", _first_match(text, (r"\b(?:residential\s+)?address(?:\s+on\s+file|\s+in\s+the\s+file)?\s*:\s*(.+?)(?:\n|$)",))),
        ("Email", _first_match(text, (r"\bContact\s*:\s*([^,\s]+)", r"(<(?:LLM_)?EMAIL_[^>]+>)", r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"))),
        ("Phone", _first_match(text, (r"\bPhone\s*:\s*([^\s,.;]+)", r"(<(?:LLM_)?PHONE(?:_NUMBER)?_[^>]+>)"))),
    )
    for label, value in candidates:
        if value:
            fields[label] = value
    return fields


def analyze_record(record_number: int, text: str) -> dict:
    detected = detect_privacy_types(text)
    score = sum(PRIVACY_WEIGHTS[item] for item in detected)
    return {
        "Record": record_number,
        **extract_rehydratable_fields(text),
        "DataTypes": detected,
        "RiskCalculation": " + ".join(str(PRIVACY_WEIGHTS[item]) for item in detected) or "0",
        "RiskScore": score,
        "Risk": _risk(score, detected),
    }


def build_mock_analysis(file_path: Path, file_content: str) -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "file_analyzed": file_path.name,
        "purpose": "Determine privacy risk of unstructured content.",
        "privacy_risk_assessments": [
            analyze_record(record_number, text)
            for record_number, text in split_records(file_content)
        ],
    }


def run_external_processor() -> Path:
    files = sorted(UNSTRUCTURED_ANON_DIR.glob("anon_final_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No unstructured final text found in {UNSTRUCTURED_ANON_DIR}")
    latest = files[0]
    out_path = EXTERNAL_RESULTS_DIR / f"external_output_unstr_{_timestamp()}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build_mock_analysis(latest, latest.read_text(encoding="utf-8")), f, indent=2, ensure_ascii=False)
    logger.info("Wrote unstructured mock external output: %s", out_path)
    return out_path
