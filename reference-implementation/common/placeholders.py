import hashlib
import re
from dataclasses import dataclass, field
from collections import Counter
from typing import Any, Callable


PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_]+>")


@dataclass
class PlaceholderRegistry:
    run_id: str
    _value_to_placeholder: dict[tuple[str, str], str] = field(default_factory=dict)
    _placeholder_to_value: dict[str, str] = field(default_factory=dict)

    def placeholder_for(self, label: str, value: Any) -> str:
        normalized_label = _normalize_label(label)
        normalized_value = _normalize_value(value)
        key = (normalized_label, normalized_value)
        if key in self._value_to_placeholder:
            return self._value_to_placeholder[key]

        suffix = hashlib.sha256(f"{self.run_id}|{normalized_label}|{normalized_value}".encode("utf-8")).hexdigest()[:10]
        placeholder = f"<{normalized_label}_{suffix}>"
        counter = 1
        while placeholder in self._placeholder_to_value and self._placeholder_to_value[placeholder] != str(value):
            suffix = hashlib.sha256(
                f"{self.run_id}|{normalized_label}|{normalized_value}|{counter}".encode("utf-8")
            ).hexdigest()[:10]
            placeholder = f"<{normalized_label}_{suffix}>"
            counter += 1

        self._value_to_placeholder[key] = placeholder
        self._placeholder_to_value[placeholder] = str(value)
        return placeholder

    def mappings(self) -> dict[str, str]:
        return dict(self._placeholder_to_value)


def extract_placeholders(value: Any) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(extract_placeholders(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(extract_placeholders(item))
        return result
    if isinstance(value, str):
        return set(PLACEHOLDER_RE.findall(value))
    return set()


def count_placeholders(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        counts: Counter[str] = Counter()
        for item in value.values():
            counts.update(count_placeholders(item))
        return dict(counts)
    if isinstance(value, list):
        counts: Counter[str] = Counter()
        for item in value:
            counts.update(count_placeholders(item))
        return dict(counts)
    if isinstance(value, str):
        return dict(Counter(PLACEHOLDER_RE.findall(value)))
    return {}


def replace_exact_placeholder(value: str, placeholder: str, replacement: str) -> str:
    return re.sub(re.escape(placeholder), replacement, value)


def replace_sensitive_value(text: str, sensitive_value: str, placeholder: str) -> tuple[str, int]:
    if not sensitive_value:
        return text, 0
    pattern = re.compile(_value_pattern(sensitive_value), re.IGNORECASE)
    return pattern.subn(placeholder, text)


def replace_sensitive_matches(
    text: str,
    sensitive_value: str,
    replacement_for_match: Callable[[str], str],
) -> tuple[str, int]:
    """Replace case-insensitively while letting callers preserve the exact source match."""
    if not sensitive_value:
        return text, 0
    pattern = re.compile(_value_pattern(sensitive_value), re.IGNORECASE)
    return pattern.subn(lambda match: replacement_for_match(match.group(0)), text)


def _value_pattern(value: str) -> str:
    escaped = re.escape(value)
    starts_word = value[0].isalnum() if value else False
    ends_word = value[-1].isalnum() if value else False
    prefix = r"(?<!\w)" if starts_word else ""
    suffix = r"(?!\w)" if ends_word else ""
    return f"{prefix}{escaped}{suffix}"


def _normalize_label(label: str) -> str:
    return re.sub(r"\W+", "_", str(label).strip().upper()).strip("_") or "PII"


def _normalize_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip())
