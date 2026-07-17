import json
import logging
from pathlib import Path

from common.config import RuntimeConfig
from common.file_handoff import process_via_sftp_share
from common.metadata import write_metadata
from common.paths import domain_output
from common.policy import PolicyContext
from common.third_party_contract import wrap_third_party_payload
from unstructured.llm_detector import split_numbered_text


log = logging.getLogger(__name__)


def invoke_unstructured_processor(
    run_id: str,
    anonymized_text: Path,
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
) -> Path:
    mode = runtime_config.external_processing.mode
    text = anonymized_text.read_text(encoding="utf-8")
    _prefix, records = split_numbered_text(text)
    record_count = len(records)
    threshold = runtime_config.external_processing.lambda_max_rows_without_override
    large_input = record_count > threshold
    large_input_mode = runtime_config.external_processing.on_large_input

    use_sftp_share = mode in {"sftp_share", "file_handoff"} or (
        mode == "lambda"
        and large_input
        and large_input_mode in {"sftp_share", "file_handoff"}
    )
    if use_sftp_share:
        log.info(
            "Unstructured external processing using sftp_share run_id=%s records=%s anonymized_input=%s",
            run_id,
            record_count,
            anonymized_text,
        )
        return process_via_sftp_share(
            "unstructured", run_id, anonymized_text, record_count, policy_context, runtime_config
        )

    use_file_drop = mode == "file_drop" or (
        mode == "lambda" and large_input and large_input_mode == "file_drop"
    )
    if use_file_drop:
        log.info(
            "Unstructured external processing using file_drop run_id=%s records=%s anonymized_input=%s",
            run_id,
            record_count,
            anonymized_text,
        )
        return _write_file_drop_result(run_id, anonymized_text, record_count, policy_context)

    if mode == "skip" or (mode == "lambda" and large_input and large_input_mode == "skip"):
        log.info(
            "Unstructured external processing skipped by config run_id=%s records=%s",
            run_id,
            record_count,
        )
        return _write_skipped_result(run_id, anonymized_text, record_count, policy_context)

    if mode != "lambda":
        raise ValueError("external_processing.mode must be 'lambda', 'sftp_share', 'file_drop', or 'skip'")

    import boto3

    log.info(
        "Invoking unstructured Lambda function=%s run_id=%s records=%s file=%s",
        runtime_config.unstructured_lambda_name,
        run_id,
        record_count,
        anonymized_text.name,
    )
    payload = {
        **policy_context.metadata(run_id),
        "records": record_count,
        "file_name": anonymized_text.name,
        "file_content": text,
    }
    response = boto3.client("lambda").invoke(
        FunctionName=runtime_config.unstructured_lambda_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    response_payload = response["Payload"].read().decode("utf-8")
    body = _unwrap_lambda_response(response_payload)
    wrapped_body = wrap_third_party_payload(run_id, body, policy_context)
    output_path = domain_output("unstructured", "third-party-results", f"third_party_result_{run_id}.json")
    output_path.write_text(json.dumps(wrapped_body, indent=2), encoding="utf-8")
    write_metadata(output_path, policy_context, run_id)
    log.info("Unstructured Lambda response persisted run_id=%s output=%s", run_id, output_path)
    return output_path


def _write_file_drop_result(
    run_id: str,
    anonymized_text: Path,
    record_count: int,
    policy_context: PolicyContext,
) -> Path:
    handoff_manifest = domain_output("unstructured", "external-handoff", f"external_handoff_{run_id}.json")
    payload = {
        "status": "file_drop",
        "reason": "record_count_exceeds_lambda_threshold_or_file_drop_configured",
        "records": record_count,
        "anonymized_input_path": str(anonymized_text),
        "handoff_manifest_path": str(handoff_manifest),
        "note": "Anonymized data is prepared for an external processor. No Lambda invocation was performed.",
    }
    handoff_manifest.write_text(
        json.dumps({**policy_context.metadata(run_id), **payload}, indent=2),
        encoding="utf-8",
    )
    write_metadata(handoff_manifest, policy_context, run_id)
    return _write_controlled_result(run_id, payload, policy_context)


def _write_skipped_result(
    run_id: str,
    anonymized_text: Path,
    record_count: int,
    policy_context: PolicyContext,
) -> Path:
    payload = {
        "status": "skipped",
        "records": record_count,
        "anonymized_input_path": str(anonymized_text),
        "note": "External processing was skipped by runtime configuration.",
    }
    return _write_controlled_result(run_id, payload, policy_context)


def _write_controlled_result(run_id: str, payload: dict, policy_context: PolicyContext) -> Path:
    wrapped_body = wrap_third_party_payload(run_id, payload, policy_context)
    output_path = domain_output("unstructured", "third-party-results", f"third_party_result_{run_id}.json")
    output_path.write_text(json.dumps(wrapped_body, indent=2), encoding="utf-8")
    write_metadata(output_path, policy_context, run_id)
    log.info(
        "Unstructured third-party control result persisted run_id=%s status=%s output=%s",
        run_id,
        payload.get("status"),
        output_path,
    )
    return output_path


def _unwrap_lambda_response(response_payload: str):
    outer = json.loads(response_payload)
    if isinstance(outer, dict) and "body" in outer:
        body = outer["body"]
        return json.loads(body) if isinstance(body, str) else body
    return outer
