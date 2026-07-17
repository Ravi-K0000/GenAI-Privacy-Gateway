import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.paths import COMMON_DIR


POLICY_PATH = COMMON_DIR / "privacy_policy.json"
LIFECYCLE_PATH = COMMON_DIR / "policy_lifecycle.json"


@dataclass(frozen=True)
class PolicyContext:
    policy: dict[str, Any]
    lifecycle: dict[str, Any]
    policy_hash: str
    policy_version: str
    policy_id: str

    def metadata(self, run_id: str) -> dict[str, str]:
        return {
            "run_id": run_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Missing required policy file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in policy file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Policy file must contain a JSON object: {path}")
    return data


def _hash_policy(policy: dict[str, Any], lifecycle: dict[str, Any]) -> str:
    payload = json.dumps(
        {"privacy_policy": policy, "policy_lifecycle": lifecycle},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_policy(policy: dict[str, Any], lifecycle: dict[str, Any]) -> None:
    for section in ("structured", "unstructured"):
        if section not in policy or not isinstance(policy[section], dict):
            raise ValueError(f"privacy_policy.json must define object section '{section}'")

    structured = policy["structured"]
    for field_name in ("static_fields", "dynamic_fields"):
        fields = structured.get(field_name)
        if not isinstance(fields, list):
            raise ValueError(f"structured.{field_name} must be a list")
        for item in fields:
            if not isinstance(item, dict) or not item.get("field") or not item.get("label"):
                raise ValueError(f"structured.{field_name} entries need 'field' and 'label'")

    unstructured = policy["unstructured"]
    if not isinstance(unstructured.get("regex_patterns"), dict):
        raise ValueError("unstructured.regex_patterns must be an object")
    if "name_context_patterns" in unstructured and not isinstance(unstructured["name_context_patterns"], list):
        raise ValueError("unstructured.name_context_patterns must be a list when present")

    required_lifecycle = ("policy_id", "version", "status", "runtime_flags", "mapping_storage")
    missing = [key for key in required_lifecycle if key not in lifecycle]
    if missing:
        raise ValueError(f"policy_lifecycle.json missing required keys: {', '.join(missing)}")
    if lifecycle["status"] != "active":
        raise ValueError("Only active policies can be used for demo runs")
    mapping_mode = lifecycle["mapping_storage"].get("mode")
    if mapping_mode not in {"local", "db", "both"}:
        raise ValueError("mapping_storage.mode must be one of: local, db, both")
    encryption_provider = lifecycle.get("mapping_security", {}).get("encryption_provider", "vault")
    if encryption_provider not in {"vault", "none"}:
        raise ValueError("mapping_security.encryption_provider must be vault or none")


def load_policy_context() -> PolicyContext:
    policy = _read_json(POLICY_PATH)
    lifecycle = _read_json(LIFECYCLE_PATH)
    validate_policy(policy, lifecycle)
    return PolicyContext(
        policy=policy,
        lifecycle=lifecycle,
        policy_hash=_hash_policy(policy, lifecycle),
        policy_version=str(lifecycle["version"]),
        policy_id=str(lifecycle["policy_id"]),
    )
