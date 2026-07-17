import json
import logging
import time
from pathlib import Path
from typing import Any

from common.config import RuntimeConfig
from common.mapping_store import read_mapping
from common.metadata import write_metadata
from common.metrics import count_unresolved_placeholders
from common.paths import domain_output
from common.placeholders import PLACEHOLDER_RE, count_placeholders
from common.policy import PolicyContext
from common.third_party_contract import unwrap_third_party_payload


log = logging.getLogger(__name__)


class RehydrationIntegrityError(RuntimeError):
    pass


def rehydrate_json_result(
    domain: str,
    run_id: str,
    third_party_result: Path,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
    expected_placeholders: list[str] | set[str] | dict[str, int] | None = None,
) -> dict[str, Any]:
    log.debug("Starting rehydration domain=%s run_id=%s third_party_result=%s", domain, run_id, third_party_result)
    end_to_end_started = time.perf_counter()
    rehydrated, integrity, phases = _rehydrate_json_inner(
        domain, run_id, third_party_result, runtime_config, expected_placeholders or [], policy_context
    )
    output_started = time.perf_counter()
    output_path = domain_output(domain, "rehydrated", f"rehydrated_{run_id}.json")
    output_path.write_text(json.dumps(rehydrated, indent=2), encoding="utf-8")
    write_metadata(output_path, policy_context, run_id)
    unresolved = count_unresolved_placeholders(rehydrated)
    phases["output_construction_seconds"] = time.perf_counter() - output_started
    rehydration_seconds = (
        phases["integrity_seconds"]
        + phases["placeholder_replacement_seconds"]
        + phases["output_construction_seconds"]
    )
    end_to_end_seconds = time.perf_counter() - end_to_end_started
    log.info(
        "Rehydration completed domain=%s run_id=%s output=%s unresolved_placeholders=%s "
        "passes=%s core_elapsed=%.3fs end_to_end_elapsed=%.3fs mapping_retrieval=%.3fs",
        domain,
        run_id,
        output_path,
        unresolved,
        phases.get("rehydration_passes", 0),
        rehydration_seconds,
        end_to_end_seconds,
        phases["mapping_retrieval_seconds"],
    )
    return {
        "path": output_path,
        "rehydration_seconds": rehydration_seconds,
        "end_to_end_rehydration_seconds": end_to_end_seconds,
        "unresolved_placeholders": unresolved,
        "third_party_status": integrity.get("third_party_status", "processed"),
        "rehydration_status": integrity.get("rehydration_status", "completed"),
        "expected_placeholders": integrity["expected_count"],
        "returned_placeholders": integrity["returned_count"],
        "missing_placeholders": integrity["missing_count"],
        "unexpected_placeholders": integrity["unexpected_count"],
        **phases,
    }


def _rehydrate_json_inner(
    domain: str,
    run_id: str,
    third_party_result: Path,
    runtime_config: RuntimeConfig,
    expected_placeholders: list[str] | set[str] | dict[str, int],
    policy_context: PolicyContext,
):
    # read_mapping dispatches to local JSON or Postgres based on mapping_store.
    # If Vault encryption is enabled, mapping_store decrypts values before returning them.
    log.debug(
        "Loading mappings for rehydration domain=%s run_id=%s mapping_store=%s encryption_provider=%s",
        domain,
        run_id,
        runtime_config.mapping_store,
        runtime_config.mapping_encryption_provider,
    )
    integrity_started = time.perf_counter()
    data = json.loads(third_party_result.read_text(encoding="utf-8"))
    payload, gateway_metadata = unwrap_third_party_payload(data)
    third_party_status = _third_party_status(payload)
    integrity = _validate_placeholder_integrity(domain, run_id, payload, expected_placeholders, policy_context)
    integrity["third_party_status"] = third_party_status
    phases = {
        "integrity_seconds": time.perf_counter() - integrity_started,
        "mapping_retrieval_seconds": 0.0,
        "placeholder_replacement_seconds": 0.0,
        "rehydration_passes": 0,
    }
    if integrity["unexpected_count"]:
        report_path = _write_rehydration_failure(domain, run_id, integrity, policy_context)
        log.error("Rehydration integrity failed domain=%s run_id=%s report=%s", domain, run_id, report_path)
        raise RehydrationIntegrityError(f"Rehydration integrity failed; see {report_path}")

    if third_party_status in {"failed", "file_drop", "skipped"} or integrity["returned_count"] == 0:
        integrity["rehydration_status"] = _rehydration_status_for(third_party_status, integrity)
        log.info(
            "Skipping mapping retrieval for rehydration domain=%s run_id=%s third_party_status=%s returned_placeholders=%s",
            domain,
            run_id,
            third_party_status,
            integrity["returned_count"],
        )
        status_payload = {
            "third_party_status": third_party_status,
            "rehydration_status": integrity["rehydration_status"],
            "third_party_payload": payload,
            "note": "No mapping retrieval was performed because there were no returned placeholders to restore.",
        }
        if gateway_metadata:
            return {"gateway_metadata": gateway_metadata, "rehydrated_payload": status_payload}, integrity, phases
        return status_payload, integrity, phases

    retrieval_started = time.perf_counter()
    mappings = read_mapping(domain, run_id, runtime_config)
    phases["mapping_retrieval_seconds"] = time.perf_counter() - retrieval_started
    log.debug("Loaded %s mapping entries for rehydration domain=%s run_id=%s", len(mappings), domain, run_id)
    replacement_started = time.perf_counter()
    returned_placeholders = set(count_placeholders(payload))
    cycles = _find_mapping_cycles(mappings, returned_placeholders)
    if cycles:
        failure = {
            **integrity,
            "rehydration_status": "failed_mapping_dependency_cycle",
            "mapping_dependency_cycles": cycles,
        }
        report_path = _write_rehydration_failure(
            domain,
            run_id,
            failure,
            policy_context,
            reason="mapping_dependency_cycle",
        )
        log.error(
            "Rehydration mapping dependency cycle domain=%s run_id=%s cycles=%s report=%s",
            domain,
            run_id,
            len(cycles),
            report_path,
        )
        raise RehydrationIntegrityError(f"Rehydration mapping dependency cycle; see {report_path}")

    rehydrated_payload, passes = _apply_mappings(
        payload, mappings, runtime_config.rehydration_max_passes
    )
    phases["placeholder_replacement_seconds"] = time.perf_counter() - replacement_started
    phases["rehydration_passes"] = passes
    unresolved_counts = count_placeholders(rehydrated_payload)
    if unresolved_counts:
        failure = {
            **integrity,
            "rehydration_status": "failed_unresolved_placeholders",
            "rehydration_passes": passes,
            "max_rehydration_passes": runtime_config.rehydration_max_passes,
            "unresolved_count": sum(unresolved_counts.values()),
            "unresolved_unique_count": len(unresolved_counts),
            "unresolved_placeholders": sorted(unresolved_counts),
        }
        report_path = _write_rehydration_failure(
            domain,
            run_id,
            failure,
            policy_context,
            reason="unresolved_placeholders_after_rehydration",
        )
        log.error(
            "Rehydration incomplete domain=%s run_id=%s passes=%s unresolved=%s report=%s",
            domain,
            run_id,
            passes,
            failure["unresolved_count"],
            report_path,
        )
        raise RehydrationIntegrityError(f"Rehydration incomplete; see {report_path}")
    integrity["rehydration_status"] = "completed"
    if gateway_metadata:
        return {"gateway_metadata": gateway_metadata, "rehydrated_payload": rehydrated_payload}, integrity, phases
    return rehydrated_payload, integrity, phases


def _apply_mappings(value: Any, mappings: dict[str, str], max_passes: int) -> tuple[Any, int]:
    if isinstance(value, dict):
        restored = {}
        passes = 0
        for key, item in value.items():
            restored[key], item_passes = _apply_mappings(item, mappings, max_passes)
            passes = max(passes, item_passes)
        return restored, passes
    if isinstance(value, list):
        restored = []
        passes = 0
        for item in value:
            restored_item, item_passes = _apply_mappings(item, mappings, max_passes)
            restored.append(restored_item)
            passes = max(passes, item_passes)
        return restored, passes
    if isinstance(value, str):
        restored = value
        passes = 0
        for pass_number in range(1, max_passes + 1):
            updated = PLACEHOLDER_RE.sub(
                lambda match: mappings.get(match.group(0), match.group(0)), restored
            )
            if updated == restored:
                break
            restored = updated
            passes = pass_number
            if not PLACEHOLDER_RE.search(restored):
                break
        return restored, passes
    return value, 0


def _find_mapping_cycles(
    mappings: dict[str, str], roots: set[str]
) -> list[list[str]]:
    dependencies = {
        placeholder: sorted(
            dependency
            for dependency in set(PLACEHOLDER_RE.findall(original))
            if dependency in mappings
        )
        for placeholder, original in mappings.items()
    }
    cycles: list[list[str]] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            start = path.index(node)
            cycle = path[start:]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if node in visited or node not in mappings:
            return
        visiting.add(node)
        for dependency in dependencies.get(node, []):
            visit(dependency, path + [dependency])
        visiting.remove(node)
        visited.add(node)

    for root in roots:
        visit(root, [root])
    return cycles


def _validate_placeholder_integrity(
    domain: str,
    run_id: str,
    third_party_data: Any,
    expected_placeholders: list[str] | set[str] | dict[str, int],
    policy_context: PolicyContext,
) -> dict[str, Any]:
    expected_counts = _as_counts(expected_placeholders)
    returned_counts = count_placeholders(third_party_data)
    expected = set(expected_counts)
    returned = set(returned_counts)
    missing = sorted(placeholder for placeholder in expected if returned_counts.get(placeholder, 0) < expected_counts[placeholder])
    unexpected = sorted(placeholder for placeholder in returned if returned_counts[placeholder] > expected_counts.get(placeholder, 0))
    integrity = {
        "status": "passed" if not unexpected else "failed",
        "expected_count": len(expected),
        "returned_count": len(returned),
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "missing_placeholders": missing,
        "unexpected_placeholders": unexpected,
        "processor_omitted_placeholders": missing,
        "note": (
            "Missing placeholders are allowed for derived third-party outputs. "
            "Unexpected placeholders indicate a contract/integrity issue and block rehydration."
        ),
        "expected_placeholder_counts": expected_counts,
        "returned_placeholder_counts": returned_counts,
    }
    report_path = domain_output(domain, "validation", f"placeholder_integrity_{run_id}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({**policy_context.metadata(run_id), "placeholder_integrity": integrity}, indent=2),
        encoding="utf-8",
    )
    write_metadata(report_path, policy_context, run_id)
    log.info(
        "Placeholder integrity %s domain=%s run_id=%s expected=%s returned=%s omitted_by_processor=%s unexpected=%s",
        integrity["status"],
        domain,
        run_id,
        integrity["expected_count"],
        integrity["returned_count"],
        integrity["missing_count"],
        integrity["unexpected_count"],
    )
    return integrity


def _write_rehydration_failure(
    domain: str,
    run_id: str,
    integrity: dict[str, Any],
    policy_context: PolicyContext,
    reason: str = "unexpected_placeholder_integrity_failed",
) -> Path:
    path = domain_output(domain, "rehydrated", f"rehydration_failed_{run_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **policy_context.metadata(run_id),
        "status": "failed",
        "reason": reason,
        "placeholder_integrity": integrity,
        "note": "No partial rehydrated output was produced because rehydration integrity failed.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_metadata(path, policy_context, run_id)
    return path


def _third_party_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "processed"
    if payload.get("errorType") or payload.get("errorMessage"):
        return "failed"
    status = str(payload.get("status", "")).strip().lower()
    if status in {"file_drop", "skipped", "failed"}:
        return status
    return "processed"


def _rehydration_status_for(third_party_status: str, integrity: dict[str, Any]) -> str:
    if third_party_status == "failed":
        return "not_run_third_party_failed"
    if third_party_status == "file_drop":
        return "not_run_external_file_handoff"
    if third_party_status == "skipped":
        return "not_run_external_processing_skipped"
    if integrity["returned_count"] == 0:
        return "not_required_no_placeholders_returned"
    return "completed"


def _as_counts(placeholders: list[str] | set[str] | dict[str, int]) -> dict[str, int]:
    if isinstance(placeholders, dict):
        return {str(placeholder): int(count) for placeholder, count in placeholders.items()}
    counts: dict[str, int] = {}
    for placeholder in placeholders:
        counts[str(placeholder)] = counts.get(str(placeholder), 0) + 1
    return counts
