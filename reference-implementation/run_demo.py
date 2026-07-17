import argparse
import logging
import uuid

from common.config import load_runtime_config
from common.metrics import build_metrics, compare_structured_fidelity, print_metrics_table, write_metrics
from common.paths import ensure_output_tree
from common.policy import load_policy_context
from common.run_logging import setup_run_logging
from provenance.log_to_blockchain import record_run_provenance
from rehydration.structured_rehydrator import rehydrate_structured_result
from rehydration.unstructured_rehydrator import rehydrate_unstructured_result
from structured.anonymization import run_structured_anonymization
from structured.third_party_client import invoke_structured_processor
from unstructured.anonymization import run_unstructured_anonymization
from unstructured.third_party_client import invoke_unstructured_processor


log = logging.getLogger("privacy-gateway-demo")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the privacy gateway reference prototype.")
    parser.add_argument("domain", choices=["structured", "unstructured", "both"], nargs="?", default="both")
    args = parser.parse_args()

    try:
        ensure_output_tree()
        policy_context = load_policy_context()
        runtime_config = load_runtime_config(policy_context)

        if args.domain in {"structured", "both"}:
            _run_structured(policy_context, runtime_config)
        if args.domain in {"unstructured", "both"}:
            _run_unstructured(policy_context, runtime_config)
    except FileNotFoundError as exc:
        print(f"\n{exc}\n")
        raise SystemExit(1)

def _run_structured(policy_context, runtime_config) -> None:
    run_id = str(uuid.uuid4())
    log_path = setup_run_logging("structured", run_id, runtime_config.log)
    log.info("Starting structured run_id=%s policy_version=%s", run_id, policy_context.policy_version)
    log.debug(
        "Runtime config: mapping_store=%s encryption_provider=%s rehydration_max_passes=%s blockchain_enabled=%s anchor_digest=%s llm_provider=%s llm_batch_size=%s llm_delay_seconds=%s external_processing=%s large_row_threshold=%s on_large_input=%s log_path=%s",
        runtime_config.mapping_store,
        runtime_config.mapping_encryption_provider,
        runtime_config.rehydration_max_passes,
        runtime_config.blockchain.enabled,
        runtime_config.blockchain.anchor_digest,
        runtime_config.llm.provider,
        runtime_config.llm.batch_size,
        runtime_config.llm.delay_seconds,
        runtime_config.external_processing.mode,
        runtime_config.external_processing.lambda_max_rows_without_override,
        runtime_config.external_processing.on_large_input,
        log_path,
    )
    log.debug("Starting structured anonymization")
    anonymization = run_structured_anonymization(run_id, policy_context, runtime_config)
    log.debug("Structured anonymization complete: records=%s mappings=%s", anonymization["records"], anonymization["mappings_created"])
    log.debug("Invoking structured third-party processor")
    third_party_result = invoke_structured_processor(
        run_id,
        anonymization["final_path"],
        policy_context,
        runtime_config,
    )
    log.debug("Structured third-party result written to %s", third_party_result)
    log.debug("Starting structured rehydration")
    rehydration = rehydrate_structured_result(
        run_id,
        third_party_result,
        policy_context,
        runtime_config,
        anonymization.get("expected_placeholder_counts", {}),
    )
    log.debug("Structured rehydration complete: unresolved_placeholders=%s", rehydration["unresolved_placeholders"])
    _finish_run("structured", run_id, anonymization, third_party_result, rehydration, policy_context, runtime_config, log_path)


def _run_unstructured(policy_context, runtime_config) -> None:
    run_id = str(uuid.uuid4())
    log_path = setup_run_logging("unstructured", run_id, runtime_config.log)
    log.info("Starting unstructured run_id=%s policy_version=%s", run_id, policy_context.policy_version)
    log.debug(
        "Runtime config: mapping_store=%s encryption_provider=%s rehydration_max_passes=%s "
        "blockchain_enabled=%s anchor_digest=%s llm_provider=%s llm_batch_size=%s "
        "llm_delay_seconds=%s external_processing=%s large_row_threshold=%s "
        "on_large_input=%s log_path=%s",
        runtime_config.mapping_store,
        runtime_config.mapping_encryption_provider,
        runtime_config.rehydration_max_passes,
        runtime_config.blockchain.enabled,
        runtime_config.blockchain.anchor_digest,
        runtime_config.llm.provider,
        runtime_config.llm.batch_size,
        runtime_config.llm.delay_seconds,
        runtime_config.external_processing.mode,
        runtime_config.external_processing.lambda_max_rows_without_override,
        runtime_config.external_processing.on_large_input,
        log_path,
    )
    log.debug("Starting unstructured anonymization")
    anonymization = run_unstructured_anonymization(run_id, policy_context, runtime_config)
    log.debug("Unstructured anonymization complete: records=%s mappings=%s", anonymization["records"], anonymization["mappings_created"])
    log.debug("Invoking unstructured third-party processor")
    third_party_result = invoke_unstructured_processor(
        run_id,
        anonymization["final_path"],
        policy_context,
        runtime_config,
    )
    log.debug("Unstructured third-party result written to %s", third_party_result)
    log.debug("Starting unstructured rehydration")
    rehydration = rehydrate_unstructured_result(
        run_id,
        third_party_result,
        policy_context,
        runtime_config,
        anonymization.get("expected_placeholder_counts", {}),
    )
    log.debug("Unstructured rehydration complete: unresolved_placeholders=%s", rehydration["unresolved_placeholders"])
    _finish_run("unstructured", run_id, anonymization, third_party_result, rehydration, policy_context, runtime_config, log_path)


def _finish_run(domain, run_id, anonymization, third_party_result, rehydration, policy_context, runtime_config, log_path) -> None:
    artifacts = [
        anonymization["static_path"],
        anonymization["dynamic_path"],
        anonymization["final_path"],
        third_party_result,
        rehydration["path"],
    ]
    if anonymization.get("validation_path"):
        artifacts.append(anonymization["validation_path"])
    if runtime_config.enable_performance_metrics:
        fidelity_details = (
            compare_structured_fidelity(anonymization["input_path"], rehydration["path"])
            if domain == "structured"
            else None
        )
        metrics = build_metrics(
            records=anonymization["records"],
            sensitive_values_detected=anonymization["sensitive_values_detected"],
            mappings_created=anonymization["mappings_created"],
            anonymization_seconds=anonymization["anonymization_seconds"],
            rehydration_seconds=rehydration["rehydration_seconds"],
            unresolved_placeholders=rehydration["unresolved_placeholders"],
            expected_placeholders=rehydration.get("expected_placeholders", 0),
            returned_placeholders=rehydration.get("returned_placeholders", 0),
            missing_placeholders=rehydration.get("missing_placeholders", 0),
            unexpected_placeholders=rehydration.get("unexpected_placeholders", 0),
            third_party_status=rehydration.get("third_party_status", "processed"),
            rehydration_status=rehydration.get("rehydration_status", "completed"),
            static_anonymization_seconds=anonymization.get("static_anonymization_seconds"),
            llm_inference_seconds=anonymization.get("llm_inference_seconds"),
            dynamic_replacement_seconds=anonymization.get("dynamic_replacement_seconds"),
            excluded_wait_seconds=anonymization.get("excluded_wait_seconds"),
            anonymization_validation_status=anonymization.get("anonymization_validation_status", "not_run"),
            residual_pii_count=anonymization.get("residual_pii_count", 0),
            fidelity_details=fidelity_details,
            rehydration_integrity_seconds=rehydration.get("integrity_seconds"),
            mapping_retrieval_seconds=rehydration.get("mapping_retrieval_seconds"),
            placeholder_replacement_seconds=rehydration.get("placeholder_replacement_seconds"),
            rehydration_output_seconds=rehydration.get("output_construction_seconds"),
            end_to_end_rehydration_seconds=rehydration.get("end_to_end_rehydration_seconds"),
            rehydration_passes=rehydration.get("rehydration_passes"),
            recovery_rows=anonymization.get("recovery_rows", 0),
            recovery_values_detected=anonymization.get("recovery_values_detected", 0),
        )
        print_metrics_table(metrics)
        metrics_json, metrics_txt = write_metrics(domain, run_id, metrics, policy_context)
        artifacts.extend([metrics_json, metrics_txt])

    if runtime_config.enable_provenance:
        log.debug("Recording provenance from run logs")
        bundle_path = record_run_provenance(run_id, domain, artifacts, policy_context, runtime_config, log_path)
        log.info("Provenance log bundle written to %s", bundle_path)
    log.info("Completed %s run_id=%s", domain, run_id)


if __name__ == "__main__":
    main()
