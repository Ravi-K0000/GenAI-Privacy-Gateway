import json
import re

from common.metadata import write_metadata
from common.paths import domain_output
from common.placeholders import PLACEHOLDER_RE
from common.policy import PolicyContext
from unstructured.llm_detector import split_numbered_text


ANGLE_TOKEN_RE = re.compile(r"<[^>\r\n]+>")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d(). -]{8,17}\d(?!\d)")
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
REFERENCE_RE = re.compile(
    r"\b(?:EMP(?:[-\s][A-Z0-9-]+|[A-Z]+\d{4,})|(?:PNR|TRV|TXN|MED)[-\s]?[A-Z0-9-]*\d[A-Z0-9-]*)\b",
    re.IGNORECASE,
)
PASSPORT_RE = re.compile(r"\b[A-Z][0-9]{7,9}\b")
CONTEXTUAL_ID_RE = re.compile(
    r"\b(?:Employee\s+ID|Travel\s+booking\s+ID|Passport\s+number)\s*:?\s*"
    r"(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}\b",
    re.IGNORECASE,
)
HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)?\s+(?:visited|attended)\b"
)
SHORTHAND_NAME_RE = re.compile(
    r"\b(?:cust|customer|pt|emp)\s+[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)?\s*[;,]",
    re.IGNORECASE,
)
HEALTHCARE_ORGANIZATION_RE = re.compile(
    r"\b[A-Z][A-Za-z&.'-]*(?:\s+[A-Z]?[A-Za-z&.'-]*){0,4}\s+"
    r"(?:hospital|clinic|medical centre|medical center|health centre|health center)\b",
    re.IGNORECASE,
)
COMPANY_CONTEXT_RE = re.compile(
    r"\bof\s+[A-Z][A-Za-z&.'-]*(?:\s+[A-Z][A-Za-z&.'-]*){0,3}\s+"
    r"(?:Corp|Ltd|Limited|LLC|Inc)\s+transferred\b"
)
CURRENCY_RE = re.compile(
    r"(?<!\w)(?:[$€£₹]\s?\d[\d,]*(?:\.\d{1,2})?|(?:INR|USD|EUR|GBP)\s+\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s+pounds?)\b",
    re.IGNORECASE,
)
CONTEXT_PATTERNS = {
    "health_context": re.compile(
        r"\b(?:diabetes|cancer|asthma|hypertension|migraine|diagnosis|medication|treatment|thyroid)\b",
        re.IGNORECASE,
    ),
    "investment_context": re.compile(r"\b(?:ETF|investment|portfolio|securities)\b", re.IGNORECASE),
    "employment_context": re.compile(
        r"\b(?:terminated|dismissed|job title|employment status|performance warning)\b",
        re.IGNORECASE,
    ),
    "family_context": re.compile(
        r"\b(?:spouse|mother|father|parent|husband|wife)\s+of\s+[A-Z][A-Za-z'-]+",
        re.IGNORECASE,
    ),
    "travel_context": re.compile(
        r"\b(?:trip|travel|flying)\s+to\s+[A-Z][A-Za-z'-]+",
        re.IGNORECASE,
    ),
}

def residual_record_numbers(text: str, policy_context: PolicyContext) -> list[int]:
    del policy_context  # Reserved for future policy-supplied validation patterns.
    _prefix, records = split_numbered_text(text)
    affected = []
    for record in records:
        counts = _record_findings(record.text)
        actionable = sum(value for key, value in counts.items() if key != "malformed_placeholder")
        if actionable:
            affected.append(record.number)
    return affected


def validate_unstructured_anonymization(
    text: str,
    run_id: str,
    policy_context: PolicyContext,
) -> dict[str, object]:
    _prefix, records = split_numbered_text(text)
    counts = _empty_counts()
    affected = []
    for record in records:
        findings = _record_findings(record.text)
        for key, value in findings.items():
            counts[key] += value
        if any(findings.values()):
            affected.append(record.number)

    residual_count = sum(counts.values())
    report = {
        **policy_context.metadata(run_id),
        "status": "passed" if residual_count == 0 else "completed_with_warnings",
        "residual_pii_count": residual_count,
        "counts": counts,
        "affected_record_count": len(affected),
        "affected_records": affected,
        "note": "The report contains counts and logical record numbers only; raw detected values are not persisted.",
    }
    path = domain_output("unstructured", "validation", f"anonymization_validation_{run_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_metadata(path, policy_context, run_id)
    report["path"] = path
    return report


def _record_findings(text: str) -> dict[str, int]:
    counts = _empty_counts()
    counts["malformed_placeholder"] = sum(
        1 for token in ANGLE_TOKEN_RE.findall(text) if not PLACEHOLDER_RE.fullmatch(token)
    )
    clean = PLACEHOLDER_RE.sub("", text)
    counts["email"] = len(EMAIL_RE.findall(clean))
    counts["phone"] = len(PHONE_RE.findall(clean))
    counts["card_or_account"] = len(CARD_RE.findall(clean))
    counts["reference_identifier"] = len(REFERENCE_RE.findall(clean))
    counts["passport"] = len(PASSPORT_RE.findall(clean))
    counts["contextual_identifier"] = len(CONTEXTUAL_ID_RE.findall(clean))
    counts["honorific_name"] = len(HONORIFIC_NAME_RE.findall(clean))
    counts["shorthand_name"] = len(SHORTHAND_NAME_RE.findall(clean))
    counts["healthcare_organization"] = len(HEALTHCARE_ORGANIZATION_RE.findall(clean))
    counts["company_context"] = len(COMPANY_CONTEXT_RE.findall(clean))
    counts["currency_amount"] = len(CURRENCY_RE.findall(clean))
    for label, pattern in CONTEXT_PATTERNS.items():
        counts[label] = len(pattern.findall(clean))
    return counts


def _empty_counts() -> dict[str, int]:
    return {
        "email": 0,
        "phone": 0,
        "card_or_account": 0,
        "reference_identifier": 0,
        "passport": 0,
        "contextual_identifier": 0,
        "honorific_name": 0,
        "shorthand_name": 0,
        "healthcare_organization": 0,
        "company_context": 0,
        "currency_amount": 0,
        "health_context": 0,
        "investment_context": 0,
        "employment_context": 0,
        "family_context": 0,
        "travel_context": 0,
        "malformed_placeholder": 0,
    }
