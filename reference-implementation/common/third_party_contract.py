import logging
from datetime import datetime, timezone
from typing import Any

from common.policy import PolicyContext


log = logging.getLogger(__name__)


def wrap_third_party_payload(run_id: str, payload: Any, policy_context: PolicyContext) -> dict[str, Any]:
    reported_run_id = payload.get("run_id") if isinstance(payload, dict) else None
    if reported_run_id == run_id:
        status = "matched"
    elif reported_run_id:
        status = "mismatch"
        log.warning(
            "Third-party response run_id mismatch gateway_run_id=%s third_party_run_id=%s; gateway run_id remains authoritative",
            run_id,
            reported_run_id,
        )
    else:
        status = "absent"
        log.warning("Third-party response did not echo gateway run_id=%s; gateway metadata wrapper added", run_id)

    return {
        "gateway_metadata": {
            **policy_context.metadata(run_id),
            "third_party_run_id_status": status,
            "third_party_reported_run_id": reported_run_id,
            "wrapped_at_utc": datetime.now(timezone.utc).isoformat(),
            "note": "Gateway metadata is authoritative for provenance, mapping lookup, rehydration, and metrics.",
        },
        "third_party_payload": payload,
    }


def unwrap_third_party_payload(value: Any) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(value, dict) and "gateway_metadata" in value and "third_party_payload" in value:
        return value["third_party_payload"], value["gateway_metadata"]
    return value, None
