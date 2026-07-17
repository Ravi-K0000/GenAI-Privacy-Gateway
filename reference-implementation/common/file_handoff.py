import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from common.config import RuntimeConfig
from common.metadata import write_metadata
from common.paths import ROOT_DIR, domain_output
from common.policy import PolicyContext
from common.third_party_contract import wrap_third_party_payload


log = logging.getLogger(__name__)


class HandoffTimeoutError(TimeoutError):
    pass


def process_via_sftp_share(
    domain: str,
    run_id: str,
    anonymized_path: Path,
    record_count: int,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
) -> Path:
    config = runtime_config.external_processing
    root = _resolve_handoff_root(config.handoff_root)
    outbound = root / "outbound" / domain / run_id
    inbound = root / "inbound" / domain / run_id
    outbound.mkdir(parents=True, exist_ok=True)
    inbound.mkdir(parents=True, exist_ok=True)

    published_input = outbound / f"anonymized{anonymized_path.suffix.lower()}"
    _atomic_copy(anonymized_path, published_input)
    request_manifest = {
        **policy_context.metadata(run_id),
        "domain": domain,
        "format": "csv" if domain == "structured" else "text",
        "input_file": published_input.name,
        "input_sha256": _sha256(published_input),
        "records": record_count,
        "processing_profile": "risk_enrichment",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(outbound / "request_manifest.json", request_manifest)
    log.info(
        "Published external handoff request provider=sftp_share domain=%s run_id=%s records=%s directory=%s",
        domain,
        run_id,
        record_count,
        outbound,
    )

    if not config.wait_for_result:
        return _write_pending_result(domain, run_id, outbound, policy_context)

    response_manifest_path = inbound / "response_manifest.json"
    deadline = time.monotonic() + max(0.0, config.result_timeout_seconds)
    next_progress_log = time.monotonic()
    while not response_manifest_path.exists():
        now = time.monotonic()
        if now >= deadline:
            raise HandoffTimeoutError(
                f"Timed out waiting for external handoff result domain={domain}, run_id={run_id}, path={inbound}"
            )
        if now >= next_progress_log:
            log.info("Waiting for external handoff response domain=%s run_id=%s inbound=%s", domain, run_id, inbound)
            next_progress_log = now + 30.0
        time.sleep(max(0.1, config.poll_interval_seconds))

    response_manifest = json.loads(response_manifest_path.read_text(encoding="utf-8"))
    _validate_response_manifest(domain, run_id, inbound, response_manifest)
    result_path = inbound / response_manifest["result_file"]
    payload = _read_result_payload(domain, result_path, response_manifest)
    wrapped = wrap_third_party_payload(run_id, payload, policy_context)
    output_path = domain_output(domain, "third-party-results", f"third_party_result_{run_id}.json")
    _atomic_write_json(output_path, wrapped)
    write_metadata(output_path, policy_context, run_id, {"external_provider": "sftp_share"})
    log.info(
        "Accepted external handoff response provider=sftp_share domain=%s run_id=%s records=%s output=%s",
        domain,
        run_id,
        response_manifest.get("output_records"),
        output_path,
    )
    return output_path


def _read_result_payload(domain: str, result_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    common = {
        "run_id": manifest["run_id"],
        "status": "processed",
        "processor": manifest.get("processor", "external-processor"),
        "processing_profile": manifest.get("processing_profile", "risk_enrichment"),
        "input_records": manifest.get("input_records"),
        "output_records": manifest.get("output_records"),
    }
    if domain == "structured":
        records = pd.read_csv(result_path, dtype=str, keep_default_na=False).to_dict(orient="records")
        return {**common, "records": records}
    return {**common, "content": result_path.read_text(encoding="utf-8")}


def _validate_response_manifest(domain: str, run_id: str, inbound: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("run_id") != run_id:
        raise RuntimeError(
            f"External handoff run_id mismatch: expected={run_id}, returned={manifest.get('run_id')}"
        )
    if manifest.get("domain") != domain:
        raise RuntimeError(
            f"External handoff domain mismatch: expected={domain}, returned={manifest.get('domain')}"
        )
    if manifest.get("status") != "completed":
        raise RuntimeError(f"External handoff did not complete successfully: status={manifest.get('status')}")
    result_name = str(manifest.get("result_file", ""))
    if not result_name or Path(result_name).name != result_name:
        raise RuntimeError("External handoff result_file must be a filename in the inbound run directory")
    result_path = inbound / result_name
    if not result_path.is_file():
        raise RuntimeError(f"External handoff result file is missing: {result_path}")
    expected_hash = str(manifest.get("result_sha256", ""))
    if not expected_hash or _sha256(result_path) != expected_hash:
        raise RuntimeError("External handoff result SHA-256 validation failed")


def _write_pending_result(
    domain: str,
    run_id: str,
    outbound: Path,
    policy_context: PolicyContext,
) -> Path:
    payload = {
        "run_id": run_id,
        "status": "file_drop",
        "handoff_directory": str(outbound),
        "note": "External handoff request was published; wait_for_result is disabled.",
    }
    wrapped = wrap_third_party_payload(run_id, payload, policy_context)
    output_path = domain_output(domain, "third-party-results", f"third_party_result_{run_id}.json")
    _atomic_write_json(output_path, wrapped)
    write_metadata(output_path, policy_context, run_id, {"external_provider": "sftp_share"})
    return output_path


def _resolve_handoff_root(configured: str) -> Path:
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT_DIR / path).resolve()


def _atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
